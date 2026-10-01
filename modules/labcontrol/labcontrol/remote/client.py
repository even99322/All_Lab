"""控制端：列出節點、送指令、接收即時資料、自動請節點更新（都經過 Lab Control Hub）。

    hub = find_hub()
    for st in list_nodes(hub): ...
    node = RemoteNode(hub, "QEL-PC")
    node.call("run_scheme", scheme=scheme.to_dict())
    feed = LiveFeed(hub, "QEL-PC", bus)      # 把節點的量測事件重播到本機 EventBus
    feed.poll(wait=5)                         # long-poll：有新資料就立即回傳
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import __version__
from ..core.events import EventBus
from ..settings import setting
from .codec import RemoteDataset, decode_event
from .hub import Hub, HubError
from .versioning import is_newer


def node_online(st: Optional[Dict[str, Any]]) -> bool:
    """線上與否由 Hub 判斷（依 Hub 收到心跳的時間，不受各電腦時鐘誤差影響）。"""
    return bool(st and st.get("online") and st.get("state") != "offline")


def list_nodes(hub: Hub) -> List[Dict[str, Any]]:
    out = []
    for st in hub.nodes():
        st["online"] = node_online(st)
        out.append(st)
    return out


class RemoteNode:
    def __init__(self, hub: Hub, name: str) -> None:
        self.hub, self.name = hub, name

    def status(self) -> Optional[Dict[str, Any]]:
        st = self.hub.node_status(self.name)
        if st is not None:
            st["online"] = node_online(st)
        return st

    def call(self, cmd: str, timeout: Optional[float] = None, **args: Any) -> Any:
        r = self.hub.call(self.name, cmd, args, float(timeout or setting("remote.reply_timeout_s", 20)))
        if not r.get("ok"):
            raise HubError(r.get("error") or f"{cmd} 失敗")
        return r.get("result")

    def post(self, cmd: str, **args: Any) -> str:
        """送出不等回覆（例如 stop）；回傳指令 id。"""
        return self.hub.send(self.name, cmd, args)

    def instruments_yaml(self) -> str:
        return self.hub.instruments(self.name)

    def mirror_station(self):
        """用節點的儀器清單建立「模擬鏡像」Station：參數表、上下限與節點相同，用來編輯 / 檢查方案。"""
        import yaml

        from ..core.station import Station

        cfg = yaml.safe_load(self.instruments_yaml() or "instruments: {}") or {}
        insts = {}
        from ..core.registry import DRIVERS, ensure_builtins_loaded
        ensure_builtins_loaded()
        for n, o in (cfg.get("instruments") or {}).items():
            o = dict(o or {})
            if o.get("enabled", True) is False or o.get("driver") not in DRIVERS:
                continue                      # 節點上的外掛驅動在這台電腦沒有 → 略過
            insts[n] = o
        cfg["instruments"] = insts
        cfg.pop("plugin_paths", None)
        cfg["_lab_plugins"] = False
        return Station(cfg, simulate=True)

    def needs_update(self) -> Optional[str]:
        """節點版本比這台舊 → 回傳要更新到的版本。"""
        st = self.status()
        if st and st.get("online") and is_newer(__version__, st.get("version", "0")):
            return __version__
        return None

    def request_update(self, version: Optional[str] = None) -> Dict[str, Any]:
        return self.call("update", version=version or __version__)


def auto_update_nodes(hub: Hub, names: Optional[List[str]] = None) -> Dict[str, Any]:
    """「任何使用者用新版本時，請量測電腦更新」：對所有（或指定）比這台舊的線上節點送更新要求。"""
    out: Dict[str, Any] = {}
    if not setting("remote.auto_update_nodes", True):
        return out
    for st in list_nodes(hub):
        n = st.get("name")
        if names is not None and n not in names:
            continue
        if not st["online"] or not is_newer(__version__, st.get("version", "0")):
            continue
        if st.get("update", {}).get("pending") == __version__:
            out[n] = {"accepted": True, "already": True}
            continue
        try:
            out[n] = RemoteNode(hub, n).request_update(__version__)
        except Exception as e:  # noqa: BLE001
            out[n] = {"accepted": False, "reason": str(e)}
    return out


class LiveFeed:
    """從 Hub 取節點的即時事件，重播到本機 EventBus（監控畫面照常訂閱）。"""

    CATCH_UP = 40        # 中途加入時只補最近幾批（避免一次讀太多）

    def __init__(self, hub: Hub, name: str, bus: EventBus) -> None:
        self.hub, self.name, self.bus = hub, name, bus
        self.run_id: Optional[str] = None
        self.last_seq = 0
        self.ds: Optional[RemoteDataset] = None

    def poll(self, wait: float = 0.0) -> int:
        """處理新資料（wait > 0：沒有新資料時在 Hub 等最多 wait 秒），回傳這次重播的事件數。"""
        r = self.hub.live(self.name, self.last_seq, self.run_id, wait)
        newest = int(r.get("newest", 0))
        if newest < self.last_seq:          # Hub 重開過：序號重新開始
            self.last_seq = 0
        head = r.get("head")
        n = 0
        start_after = self.last_seq
        if head and head.get("run_id") != self.run_id:
            self.run_id = head["run_id"]
            topic, p, self.ds = decode_event(head["started"], None)
            self.bus.publish(topic, **p)
            n += 1
            start_after = max(int(head.get("first_seq", 1)) - 1, newest - self.CATCH_UP)
            if start_after < self.last_seq or not r.get("batches") or \
                    min(s for s, _ in r["batches"]) > start_after + 1:
                r = self.hub.live(self.name, start_after, self.run_id, 0)   # 重新取這次量測的批次
        for seq, events in r.get("batches") or []:
            if seq <= start_after:
                continue
            self.last_seq = seq
            for ev in events:
                if ev.get("t") == "run.started":
                    continue
                topic, p, self.ds = decode_event(ev, self.ds)
                self.bus.publish(topic, **p)
                n += 1
        self.last_seq = max(self.last_seq, start_after)
        return n


class RemoteRunnerProxy:
    """讓監控畫面的按鈕（暫停、停止…）改送到節點。"""

    def __init__(self, node: RemoteNode, feed: LiveFeed) -> None:
        self.node, self.feed = node, feed
        self.state_value = "idle"

    @property
    def plan(self):
        return self.feed.ds.plan if self.feed.ds is not None else None

    @property
    def is_active(self) -> bool:
        return self.state_value in ("preparing", "running", "paused")

    def toggle_pause(self) -> None:
        self.node.post("resume" if self.state_value == "paused" else "pause")

    def pause(self) -> None:
        self.node.post("pause")

    def resume(self) -> None:
        self.node.post("resume")

    def stop(self) -> None:
        self.node.post("stop")

    def rollback(self) -> None:
        self.node.post("rollback")

    def set_manual(self, on: bool) -> None:
        self.node.post("manual", on=on)

    def measure_once(self) -> None:
        self.node.post("measure_once")

    def loop_start(self) -> None:
        self.node.post("loop", on=True)

    def loop_stop(self) -> None:
        self.node.post("loop", on=False)

    def accept(self, retain: bool = False) -> None:
        self.node.post("accept", retain=retain)

    def wait(self, timeout: Optional[float] = None) -> bool:
        return True
