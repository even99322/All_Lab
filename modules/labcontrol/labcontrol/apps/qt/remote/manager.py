"""遠端量測的 Qt 端：連 Lab Control Hub、定期回報這台電腦的狀態並取得節點清單、開關本機節點、自動請節點更新。"""
from __future__ import annotations

import logging
import os
import platform
from typing import Any, Dict, List, Optional

from PyQt6 import QtCore
from PyQt6.QtCore import QObject, pyqtSignal

from .... import __version__
from ....core.station import Station
from ....remote import Hub, NodeService, auto_update_nodes, find_hub, load_node_state, save_node_state
from ....remote.hub import default_node_name, hostname, user_id, user_name
from ....settings import setting
from ..worker import run_bg

log = logging.getLogger(__name__)


class RemoteManager(QObject):
    hub_changed = pyqtSignal(str, str)          # 狀態（searching / ok / error）, 訊息
    nodes_updated = pyqtSignal(list)
    node_changed = pyqtSignal(bool, str)        # 本機節點開 / 關, 訊息
    restart_requested = pyqtSignal()            # 節點更新：請主程式結束（新版已啟動）
    message = pyqtSignal(str)
    registry_changed = pyqtSignal()             # 共用儀器歸屬 / 登錄更新

    def __init__(self, parent=None, station: Optional[Station] = None) -> None:
        super().__init__(parent)
        self.station = station
        self.hub: Optional[Hub] = None
        self.node: Optional[NodeService] = None
        self.nodes: List[Dict[str, Any]] = []
        self.state: Dict[str, Any] = {}
        self._searching = False
        self._refreshing = False
        self._auto_update_done = False
        self._want_node: Optional[Station] = None
        self._last_error = ""
        self._fails = 0
        self.owners: Dict[str, str] = {}
        self.shared: set = set()
        if station is not None and station.connect_guard is None:
            from ....remote.node import owner_guard

            station.connect_guard = owner_guard(lambda: self.owners, station, hostname, lambda: self.shared)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(int(max(1.0, float(setting("remote.heartbeat_s", 2))) * 1000))
        self.retry = QtCore.QTimer(self)
        self.retry.timeout.connect(self.find)

    # ---- Hub ------------------------------------------------------------
    def find(self) -> None:
        if self._searching:
            return
        self._searching = True
        self.hub_changed.emit("searching", "尋找 Lab Control Hub…")

        def ok(hub):
            self._searching = False
            self.hub = hub
            self.retry.stop()
            if self._last_error:
                log.info("已連上 Hub %s", hub.url)
            self._last_error = ""
            self.hub_changed.emit("ok", hub.url)
            self.refresh()
            if self._want_node is not None and self.node is None:
                self.start_node(self._want_node)

        def fail(msg):
            self._searching = False
            self.hub = None
            self._set_error(msg)
            if not self.retry.isActive():
                self.retry.start(15000)
        run_bg(find_hub, ok, fail)

    def _set_error(self, msg: str) -> None:
        if msg != self._last_error:
            log.warning("Hub：%s", msg)          # 寫進 LAB/logs/labcontrol.log，方便查原因
            self._last_error = msg
        self.hub_changed.emit("error", msg)

    def device_info(self) -> Dict[str, Any]:
        """這台 Lab Control 的狀態（Hub 網頁的「所有電腦」）。"""
        from ....measure.runner import Runner

        info: Dict[str, Any] = {"id": user_id(), "host": hostname(), "user": user_name(), "version": __version__,
                                "pid": os.getpid(), "platform": f"{platform.system()} {platform.release()}",
                                "role": "node" if self.node is not None else "client",
                                "node": self.node.name if self.node is not None else None,
                                "simulate": bool(self.station.simulate) if self.station is not None else None}
        if self.station is not None:
            from ....remote.instruments import instrument_report, last_scan

            info["instruments"] = instrument_report(self.station)
            info["scan"] = dict(last_scan)
        rs = Runner.active_runners()
        if rs:
            r = rs[0]
            info["state"] = r.state.value
            info["run"] = {"name": r.name, "index": r.index, "total": len(r.plan), "by": "本機"}
        else:
            info["state"], info["run"] = "idle", None
        return info

    def refresh(self) -> None:
        if self.hub is None or self._refreshing:
            return
        self._refreshing = True
        hub = self.hub
        try:
            info = self.device_info()
        except Exception as e:  # noqa: BLE001
            self._refreshing = False
            log.warning("整理這台電腦的狀態失敗：%s", e)
            return

        def work():
            hub.heartbeat(info)
            return hub.state()

        def ok(state):
            self._refreshing = False
            self._fails = 0
            self.state = state
            owners, shared = dict(state.get("owners") or {}), set(state.get("shared") or [])
            if owners != self.owners or shared != self.shared:
                self.owners, self.shared = owners, shared
                self.registry_changed.emit()
            self._enforce_owners()
            self.nodes = [dict(n, online=bool(n.get("online"))) for n in state.get("nodes") or []]
            if self._last_error:
                self._last_error = ""
                self.hub_changed.emit("ok", hub.url)
            self.nodes_updated.emit(self.nodes)
            if not self._auto_update_done:
                self._auto_update_done = True
                self.auto_update()

        def fail(msg):
            self._refreshing = False
            self._fails += 1
            if self._fails < 3:              # 偶發逾時（VPN）：先不要斷線，下一次心跳再試
                if self._fails == 1:
                    log.info("Hub 回應逾時（第 %d 次），稍後重試：%s", self._fails, msg)
                self.hub_changed.emit("unstable", msg)
                return
            self._set_error(msg)
            self.hub = None                  # 連續失敗：可能從內網換到 VPN，重新尋找
            if not self.retry.isActive():
                self.retry.start(10000)
        run_bg(work, ok, fail)

    def _enforce_owners(self) -> None:
        """共用儀器被拉到別台電腦：這台如果連著（且沒在量測），中斷它。"""
        st = self.station
        if st is None or not self.owners:
            return
        me = hostname().lower()
        from ....remote.instruments import instrument_key

        for n, inst in list(st.instruments.items()):
            if not inst.connected or st.lease_holder(n):
                continue
            k = instrument_key(str(inst.options.get("address") or ""), inst.options)
            owner = self.owners.get(k) if k else None
            if owner and owner.lower() != me:
                try:
                    st.disconnect(n)
                    self.message.emit(f"🔌 {n} 已被拉取到 {owner}，這台已中斷連線")
                except Exception as e:  # noqa: BLE001
                    log.warning("中斷 %s 失敗：%s", n, e)

    def claim(self, key: str, on_done=None) -> None:
        """把共用儀器拉到這台電腦（其他電腦會中斷它）。"""
        if self.hub is None:
            self.message.emit("⚠ 還沒連上 Hub，無法拉取")
            return
        hub = self.hub

        def ok(r):
            self.owners[key] = hostname()
            self.message.emit(f"✔ 已把 {key} 拉到這台電腦" + (f"（{', '.join(r.get('released') or [])} 已中斷）"
                                                          if r.get("released") else ""))
            if on_done:
                on_done(True, "")
        run_bg(lambda: hub.claim_instrument(key, hostname()), ok,
               lambda m: (self.message.emit(f"⚠ 拉取失敗：{m}"), on_done and on_done(False, m)))

    def auto_update(self, names: Optional[List[str]] = None) -> None:
        """版本比這台舊的節點 → 請它更新（任何使用者開新版時都會觸發）。"""
        if self.hub is None:
            return
        hub = self.hub
        me = self.node.name if self.node is not None else None

        def ok(res):
            for n, r in res.items():
                if n == me:
                    continue
                if r.get("accepted"):
                    if not r.get("already"):
                        self.message.emit(f"🛰 已請節點 {n} 更新到新版"
                                          + ("（量測結束後更新）" if r.get("deferred") else ""))
                else:
                    self.message.emit(f"⚠ 節點 {n} 沒有更新：{r.get('reason')}")
        run_bg(lambda: auto_update_nodes(hub, names), ok, lambda m: self.message.emit(f"⚠ 自動更新檢查失敗：{m}"))

    # ---- 本機節點 ----------------------------------------------------------
    @property
    def node_name(self) -> str:
        return self.node.name if self.node is not None else (load_node_state().get("name") or default_node_name())

    def start_node(self, station: Station) -> None:
        self._want_node = station
        if self.hub is None:
            self.node_changed.emit(False, "還沒連上 Hub，連上後會自動上線（狀態列顯示 Hub 連線狀態）")
            self.find()
            return
        if self.node is not None:
            return
        try:
            self.node = NodeService(station, self.hub, name=self.node_name,
                                    on_restart=self.restart_requested.emit).start()
            save_node_state(auto_start=True, name=self.node.name)
            self.node_changed.emit(True, f"節點「{self.node.name}」已上線：其他電腦在「執行於」可以選它，"
                                         f"Hub 網頁 {self.hub.dashboard_url} 也看得到")
        except Exception as e:  # noqa: BLE001
            self.node = None
            log.warning("節點啟動失敗：%s", e)
            self.node_changed.emit(False, f"節點啟動失敗：{e}")

    def stop_node(self, remember: bool = True) -> None:
        self._want_node = None
        if self.node is not None:
            node, self.node = self.node, None
            run_bg(node.stop)
        if remember:
            save_node_state(auto_start=False)
        self.node_changed.emit(False, "節點已離線")

    def shutdown(self) -> None:
        self.timer.stop()
        self.retry.stop()
        if self.node is not None:
            self.node.stop()
            self.node = None
