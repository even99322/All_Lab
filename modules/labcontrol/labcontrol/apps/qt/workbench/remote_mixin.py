"""主視窗的遠端量測功能（Lab Control Hub）。

  * 「🛰 量測節點」：把這台電腦開放給其他人控制（狀態、指令、即時資料、量測檔都經 Hub）。
  * 「執行於」：本機 / 某個節點。選節點後，儀器參數列表與方案檢查換成那台電腦的儀器，
    ▶ 開始量測在節點上執行，右上即時監控顯示節點的進度與資料（暫停 / 停止等按鈕送到節點）。
  * 開啟時如果發現節點版本比這台舊，自動請節點更新（量測中則等結束）。
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from PyQt6 import QtCore, QtWidgets

from .... import __version__
from ....core.events import EventBus
from ....remote import LiveFeed, RemoteNode, RemoteRunnerProxy, is_newer, load_node_state
from ....scheme import build_catalog
from ....settings import setting
from .. import theme
from ..remote import RemoteManager
from ..worker import run_bg, start_thread
from .channels import ChannelPanel
from ....remote.backend import RemoteBackend
from .monitor_panel import MonitorPanel

STATE_ZH = {"idle": "閒置", "running": "量測中", "paused": "暫停", "updating": "更新中", "offline": "離線",
            "preparing": "準備中"}


class RemoteMixin:
    # ---- 建立 ------------------------------------------------------------
    def _remote_toolbar(self, tb: QtWidgets.QToolBar) -> None:
        self.act_node = tb.addAction("🛰 量測節點")
        self.act_node.setCheckable(True)
        self.act_node.setToolTip("把這台電腦當作量測節點：其他電腦可以透過 Hub 在這台電腦上量測\n"
                                 "（自己這台的「執行於」不會列出自己，要從其他電腦選）")
        self.act_node.toggled.connect(self._on_node_toggled)

    def _remote_target_widget(self, tb: QtWidgets.QToolBar) -> None:
        tb.addWidget(QtWidgets.QLabel(" 執行於 "))
        self.target = QtWidgets.QComboBox()
        self.target.setMinimumWidth(230)
        self.target.addItem("💻 本機", None)
        self.target.currentIndexChanged.connect(self._on_target_changed)
        tb.addWidget(self.target)

    def _init_remote(self) -> None:
        self.remote = RemoteManager(self, station=self.station)
        self.remote_target: Optional[str] = None
        self.remote_node: Optional[RemoteNode] = None
        self.remote_mirror = None
        self._feed_stop: Optional[threading.Event] = None
        self._my_remote_runs: set = set()
        self.remote_status = QtWidgets.QLabel("Hub：尋找中…")
        self.statusBar().addPermanentWidget(self.remote_status)
        self.remote.hub_changed.connect(self._on_hub)
        self.remote.nodes_updated.connect(self._on_nodes)
        self.remote.node_changed.connect(self._on_node_changed)
        self.remote.restart_requested.connect(self._remote_restart)
        self.remote.message.connect(lambda m: self.statusBar().showMessage(m, 15000))
        self.remote.find()
        if load_node_state().get("auto_start"):
            self.act_node.setChecked(True)

    # ---- Hub / 節點清單 --------------------------------------------------------
    def _on_hub(self, state: str, msg: str) -> None:
        if state == "ok":
            self.remote_status.setText(f"Hub：✔ {msg.split('://')[-1]}")
            self.remote_status.setToolTip(f"{msg}\n網頁：{msg}/（設定 ▾ → 開啟 Hub 監控網頁）")
        elif state == "searching":
            self.remote_status.setText("Hub：尋找中…")
        elif state == "unstable":
            self.remote_status.setText(self.remote_status.text().split("（")[0] + "（回應慢，重試中）")
            self.remote_status.setToolTip(msg)
        else:
            self.remote_status.setText("Hub：✖ 未連線（設定 ▾ → Hub 連線設定）")
            self.remote_status.setToolTip(msg)

    def _on_nodes(self, nodes: List[Dict[str, Any]]) -> None:
        me = self.remote.node.name if self.remote.node is not None else None
        cur = self.target.currentData()
        self.target.blockSignals(True)
        while self.target.count() > 1:
            self.target.removeItem(1)
        for n in sorted(nodes, key=lambda x: (not x["online"], x.get("name", ""))):
            name = n.get("name")
            if name == me:
                continue
            st = STATE_ZH.get(n.get("state"), n.get("state")) if n["online"] else "離線"
            ver = n.get("version", "?")
            warn = "  ⬆" if n["online"] and is_newer(__version__, ver) else ""
            self.target.addItem(f"🛰 {name}（{st} · v{ver}）{warn}", name)
            idx = self.target.count() - 1
            if not n["online"]:
                self.target.model().item(idx).setEnabled(False)
        i = self.target.findData(cur)
        self.target.setCurrentIndex(i if i >= 0 else 0)
        self.target.blockSignals(False)
        if cur is not None and i < 0:
            self._select_target(None)
        self._update_banner(nodes)

    def _update_banner(self, nodes: List[Dict[str, Any]]) -> None:
        if not self.remote_target or not hasattr(self, "remote_banner"):
            return
        st = next((n for n in nodes if n.get("name") == self.remote_target), None)
        if st is None:
            return
        run = st.get("run") or {}
        txt = (f"🛰 <b>{self.remote_target}</b>　{st.get('user')}@{st.get('host')}　v{st.get('version')}　"
               f"{STATE_ZH.get(st.get('state'), st.get('state')) if st['online'] else '離線'}"
               + ("　（模擬模式）" if st.get("simulate") else ""))
        if run:
            txt += f"<br>量測：{run.get('name')}（{run.get('by')}）{run.get('index')}/{run.get('total')}"
        upd = st.get("update") or {}
        if upd.get("pending"):
            txt += f"<br>⬆ 等待更新到 {upd['pending']}"
        if upd.get("error"):
            txt += "<br>" + theme.span(f"更新失敗：{upd['error']}", "err")
        self.remote_banner.setText(txt)

    # ---- 本機節點 ----------------------------------------------------------
    def _on_node_toggled(self, on: bool) -> None:
        if on:
            self.remote.start_node(self.station)
        else:
            self.remote.stop_node()

    def _on_node_changed(self, on: bool, msg: str) -> None:
        self.statusBar().showMessage(f"🛰 {msg}", 10000)
        want = on or self.remote._want_node is not None
        if self.act_node.isChecked() != want:
            self.act_node.blockSignals(True)
            self.act_node.setChecked(want)
            self.act_node.blockSignals(False)
        self.act_node.setText(f"🛰 量測節點：{self.remote.node.name}" if on else "🛰 量測節點")

    def _remote_restart(self) -> None:
        """節點更新：Lab APP 會安裝並開啟新版，這裡存好方案後結束。"""
        from ....paths import lab_path

        try:
            if self.doc.dirty:
                self.doc.scheme.save(lab_path("schemes", "_更新前自動儲存.scheme.yaml"))
        except Exception:  # noqa: BLE001
            pass
        self.doc.dirty = False
        self._closing_for_update = True
        QtCore.QTimer.singleShot(500, QtWidgets.QApplication.quit)

    # ---- 執行位置 ----------------------------------------------------------
    def _on_target_changed(self, _i: int) -> None:
        self._select_target(self.target.currentData())

    def _select_target(self, name: Optional[str]) -> None:
        if self.monitor.busy and name != self.remote_target and self.remote_target is None:
            pass   # 本機量測中也可以切去看遠端；本機量測繼續
        self._stop_feed()
        if name is None:
            self.remote_target = None
            self.remote_node = None
            self.channels = self.local_channels
            self.monitor = self.local_monitor
            self.chan_stack.setCurrentWidget(self.local_channels)
            self.mon_stack.setCurrentWidget(self.local_monitor)
            from ....remote.backend import LocalBackend
            from ....remote.hub import hostname

            self.set_live_backend(LocalBackend(self.station), f"local:{hostname()}", "本機")
            self.doc.set_catalog(build_catalog(self.station))
            self._refresh()
            return
        hub = self.remote.hub
        if hub is None:
            return
        node = RemoteNode(hub, name)
        self.statusBar().showMessage(f"讀取節點 {name} 的儀器清單…")

        def ok(mirror):
            self.remote_target, self.remote_node, self.remote_mirror = name, node, mirror
            self._build_remote_panels(node, mirror)
            self.doc.set_catalog(build_catalog(mirror))
            self._update_banner(self.remote.nodes)
            self.statusBar().showMessage(f"執行於節點 {name}：流程圖與儀器參數已換成該電腦的儀器", 8000)
            st = node.status() or {}
            if st.get("online") and is_newer(__version__, st.get("version", "0")) and \
                    setting("remote.auto_update_nodes", True):
                self.remote.auto_update([name])
            elif st.get("online") and is_newer(st.get("version", "0"), __version__):
                QtWidgets.QMessageBox.information(
                    self, "版本", f"節點 {name} 是 v{st.get('version')}，比你的 v{__version__} 新。\n"
                                 "請從 Lab APP 更新 Lab Control。")
            self._refresh()

        def fail(msg):
            QtWidgets.QMessageBox.warning(self, "節點", f"無法讀取節點 {name}：{msg}")
            self.target.setCurrentIndex(0)
        run_bg(node.mirror_station, ok, fail)

    def _build_remote_panels(self, node: RemoteNode, mirror) -> None:
        for w in (getattr(self, "remote_channels", None), getattr(self, "remote_box", None)):
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        bus = EventBus()
        feed = LiveFeed(node.hub, node.name, bus)
        proxy = RemoteRunnerProxy(node, feed)
        mon = MonitorPanel(mirror, bus=bus)
        mon.runner = proxy
        mon.runner_resolver = lambda _rid: proxy
        mon.start_requested.connect(self.start_measurement)
        mon.finished.connect(lambda _s: self._refresh(preview=False))
        box = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        self.remote_banner = QtWidgets.QLabel()
        self.remote_banner.setWordWrap(True)
        self.remote_banner.setStyleSheet(theme.tint("info", padding="4px 8px"))
        v.addWidget(self.remote_banner)
        v.addWidget(mon, 1)
        ch = ChannelPanel(self.doc, mirror, backend=RemoteBackend(node))
        ch.edit_requested.connect(self.flow.view.edit_node)
        from ..bridge import QtEventBridge

        fb = QtEventBridge(bus, "remote.files", parent=box)
        fb.on("remote.files", lambda p, node=node: self._remote_files(node, p))
        self.remote_channels, self.remote_box, self.remote_monitor = ch, box, mon
        self.chan_stack.addWidget(ch)
        self.mon_stack.addWidget(box)
        self.chan_stack.setCurrentWidget(ch)
        self.mon_stack.setCurrentWidget(box)
        self.channels, self.monitor = ch, mon
        self.set_live_backend(ch.backend, f"node:{node.name}", f"節點 {node.name}")
        self._start_feed(feed)

    def _start_feed(self, feed: LiveFeed) -> None:
        stop = threading.Event()
        self._feed_stop = stop
        wait = 5.0

        def loop():
            while not stop.is_set():
                try:
                    feed.poll(wait=wait)          # long-poll：節點有新資料時立即回傳
                except Exception:  # noqa: BLE001 - Hub 暫時連不到：稍後再試
                    stop.wait(2.0)
        start_thread(loop, name="remote-feed")

    def _remote_files(self, node: RemoteNode, p: Dict[str, Any]) -> None:
        """節點把量測檔上傳到 Hub 後：自己開始的量測 → 自動下載到本機資料夾（保留節點上的資料夾結構）。"""
        files = p.get("files") or []
        if not files:
            return
        names = "、".join(f.get("rel", "") for f in files)
        if p.get("run_id") not in self._my_remote_runs or not setting("remote.download_results", True):
            self.statusBar().showMessage(f"📁 節點 {node.name} 已把量測檔存到 Hub：{names}", 15000)
            return
        from pathlib import Path

        root = Path(setting("remote.download_root", "") or setting("data.root", "") or "./data")
        hub = node.hub

        def work():
            return [str(hub.download(f["url"], root / f["rel"])) for f in files if f.get("url")]

        def ok(paths):
            self.statusBar().showMessage(f"📥 已從 Hub 下載 {len(paths)} 個量測檔到 {root}", 20000)
            if hasattr(self.monitor, "_log"):
                self.monitor._log(f"📥 已下載：{'、'.join(paths)}")

        run_bg(work, ok, lambda m: self.statusBar().showMessage(f"⚠ 下載量測檔失敗：{m}（可在 Hub 網頁下載）", 20000))

    def _stop_feed(self) -> None:
        if self._feed_stop is not None:
            self._feed_stop.set()
            self._feed_stop = None

    def _start_remote(self) -> None:
        node = self.remote_node
        st = node.status() or {}
        if not st.get("online"):
            QtWidgets.QMessageBox.warning(self, "節點", f"節點 {node.name} 目前離線")
            return
        if is_newer(__version__, st.get("version", "0")):
            self.remote.auto_update([node.name])
            QtWidgets.QMessageBox.information(
                self, "節點需要更新", f"節點 {node.name} 是 v{st.get('version')}，你是 v{__version__}。\n"
                                     "已請節點更新，更新完成（約一分鐘）後再開始量測。")
            return
        scheme = self.doc.scheme.to_dict()
        self.monitor.b_start.setEnabled(False)
        self.statusBar().showMessage(f"送出方案到節點 {node.name}…")

        def ok(res):
            self._my_remote_runs.add(res.get("run_id"))
            self.show_measure_tab()
            self.statusBar().showMessage(f"✔ 節點 {node.name} 已開始量測：{res.get('folder')}\\{res.get('file_name')}",
                                         15000)
            self._refresh(preview=False)

        def fail(msg):
            self.monitor.b_start.setEnabled(True)
            QtWidgets.QMessageBox.warning(self, "無法在節點開始量測", msg)
        run_bg(lambda: node.call("run_scheme", timeout=60, scheme=scheme), ok, fail)

    def _open_hub_settings(self) -> None:
        from ..remote.hub_dialog import HubSettingsDialog

        if HubSettingsDialog(self).exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.remote.hub = None
            self.remote.find()

    def _open_monitor(self) -> None:
        """在這個程式裡開 Lab Control Monitor（使用 settings.yaml 的 Hub 設定）。"""
        import sys
        from pathlib import Path

        root = str(Path(__file__).resolve().parents[4])
        if root not in sys.path:
            sys.path.insert(0, root)
        from labmonitor.window import MonitorWindow

        from ....remote.hub import hub_token

        if getattr(self, "_monitor_win", None) is None:
            cfg = {"hub_urls": list(setting("remote.hub_urls", []) or []), "token": hub_token(), "refresh_s": 2.0,
                   "hub_folder": ""}
            self._monitor_win = MonitorWindow(cfg, persist=False)
        self._monitor_win.show()
        self._monitor_win.raise_()

    def _open_hub_web(self) -> None:
        import urllib.parse

        from PyQt6 import QtGui

        from ....remote.hub import hub_token, hub_urls

        url = self.remote.hub.url if self.remote.hub is not None else (hub_urls() or [""])[0]
        if not url:
            self._open_hub_settings()
            return
        tok = hub_token()
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(url + "/" + (f"?token={urllib.parse.quote(tok)}" if tok else "")))

    def _shutdown_remote(self) -> None:
        self._stop_feed()
        self.remote.shutdown()
