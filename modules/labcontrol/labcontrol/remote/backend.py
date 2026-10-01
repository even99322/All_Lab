"""儀器操作後端：本機（Station）與遠端節點（經 Hub）用同一組介面。

即時監控、儀器參數列表、儀器伺服器都透過這裡操作儀器，所以「在 B 電腦操作 A 節點的儀器」和操作本機一樣。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..core.station import Station


class LocalBackend:
    """本機儀器（Station）。"""

    remote = False

    def __init__(self, station: Station) -> None:
        self.station = station
        self.label = "本機"

    # ---- 儀器 ----------------------------------------------------------------
    def instruments(self) -> List[Dict[str, Any]]:
        from .instruments import instrument_report

        return instrument_report(self.station)

    def connected(self, name: str) -> bool:
        inst = self.station.instruments.get(name)
        return bool(inst is not None and inst.connected)

    def connect(self, names: List[str]) -> Dict[str, str]:
        return self.station.connect_all(names)

    def connect_all(self) -> Dict[str, str]:
        return self.station.connect_all()

    def disconnect(self, names: List[str]) -> Dict[str, str]:
        return self.station.disconnect_all(names)

    def disconnect_all(self) -> Dict[str, str]:
        return self.station.disconnect_all()

    def describe(self, name: str) -> Dict[str, Any]:
        from .instruments import describe_instrument

        return describe_instrument(self.station, name)

    # ---- 參數 ----------------------------------------------------------------
    def get(self, refs: List[str], connect: bool = False) -> Dict[str, Tuple[Any, str]]:
        out = {}
        for r in refs:
            try:
                if connect:
                    self.station.ensure_connected([r])
                out[r] = (self.station.parameter(r).get(), "")
            except Exception as e:  # noqa: BLE001
                out[r] = (None, str(e))
        return out

    def set(self, ref: str, value: Any) -> Any:
        from ..core.units import parse_quantity

        self.station.ensure_connected([ref])
        p = self.station.parameter(ref)
        if isinstance(value, str) and p.unit:
            try:
                value = parse_quantity(value, p.unit)
            except Exception:  # noqa: BLE001
                pass
        p.set(value)
        return p.get() if p.gettable else value

    # ---- 電源群組 --------------------------------------------------------------
    def sources(self) -> Dict[str, Any]:
        from ..core.grouping import GroupRamp, default_groups, list_sources

        return {"sources": list_sources(self.station), "groups": default_groups(self.station),
                "ramps": GroupRamp.active()}

    def levels(self, refs: List[str]) -> Dict[str, Tuple[Any, str]]:
        return self.get(refs)

    def group_ramp(self, refs: List[str], target: float, rate: Optional[float] = None) -> str:
        from ..core.grouping import GroupRamp

        return GroupRamp(self.station, refs, target, rate).start().id

    def group_stop(self, job: Optional[str] = None) -> None:
        from ..core.grouping import GroupRamp

        for j in list(GroupRamp.jobs.values()):
            if job is None or j.id == job:
                j.stop()

    def ramp_status(self, job: str) -> Dict[str, Any]:
        from ..core.grouping import GroupRamp

        j = GroupRamp.jobs.get(job)
        return j.view() if j else {"id": job, "done": True}

    def fine_step(self, refs: List[str], direction: int) -> Dict[str, float]:
        from ..core.grouping import fine_step

        return fine_step(self.station, refs, direction)

    def output(self, refs: List[str], on: bool) -> Dict[str, Any]:
        from ..core.grouping import set_output

        return set_output(self.station, refs, on)

    # ---- VNA ------------------------------------------------------------------
    def vnas(self) -> List[Dict[str, Any]]:
        from ..core.capabilities import TraceAcquirer

        return [{"name": n, "label": i.options.get("label", n), "traces": list(i.trace_list())}
                for n, i in self.station.instruments.items() if i.connected and isinstance(i, TraceAcquirer)]

    def vna_catalog(self, name: str) -> Dict[str, List[str]]:
        inst = self.station.instruments[name]
        return inst.trace_catalog() if hasattr(inst, "trace_catalog") and not self.station.simulate else \
            {t: [t] for t in inst.trace_list()}

    def vna_trace(self, name: str, trace: str, create: bool = False) -> Tuple[np.ndarray, np.ndarray, str]:
        inst = self.station.instruments[name]
        old = inst.options.get("auto_create_trace")
        if create:
            inst.options["auto_create_trace"] = True
        if self.station.lease_holder(name):
            raise RuntimeError(f"{name} 正在量測中使用，即時監控暫停讀取")
        stim = getattr(inst, "_stimulus", None)
        if isinstance(stim, dict):
            stim.clear()                      # 頻率設定可能剛改過：重新讀頻率軸
        try:
            x = inst.x_axis(trace)
            z = np.asarray(inst.acquire(trace))
        finally:
            if create:
                inst.options["auto_create_trace"] = old
        return np.asarray(x.values, float), z, x.unit


class RemoteBackend:
    """遠端節點的儀器（指令經 Hub 送到那台電腦執行）。"""

    remote = True

    def __init__(self, node) -> None:
        self.node = node
        self.label = f"節點 {node.name}"
        self._status: Dict[str, Any] = {}
        self._t = 0.0

    def _st(self) -> Dict[str, Any]:
        if time.monotonic() - self._t > 1.0:
            self._status = self.node.status() or {}
            self._t = time.monotonic()
        return self._status

    def instruments(self) -> List[Dict[str, Any]]:
        self._t = 0.0
        return list(self._st().get("instruments") or [])

    def connected(self, name: str) -> bool:
        return any(i.get("name") == name and i.get("connected") for i in self._st().get("instruments") or [])

    def _res(self, r: Any) -> Dict[str, str]:
        self._t = 0.0
        return dict(r or {})

    def connect(self, names: List[str]) -> Dict[str, str]:
        return self._res(self.node.call("connect", names=list(names), timeout=90))

    def connect_all(self) -> Dict[str, str]:
        return self._res(self.node.call("connect_all", timeout=120))

    def disconnect(self, names: List[str]) -> Dict[str, str]:
        return self._res(self.node.call("disconnect", names=list(names), timeout=60))

    def disconnect_all(self) -> Dict[str, str]:
        return self._res(self.node.call("disconnect_all", timeout=60))

    def describe(self, name: str) -> Dict[str, Any]:
        return self.node.call("describe", name=name)

    def get(self, refs: List[str], connect: bool = False) -> Dict[str, Tuple[Any, str]]:
        from .codec import dec_array

        res = self.node.call("get", refs=list(refs), connect=connect)
        return {r: (dec_array(v.get("value")), v.get("error", "")) for r, v in res.items()}

    def set(self, ref: str, value: Any) -> Any:
        return self.node.call("set", ref=ref, value=value, timeout=300)

    def sources(self) -> Dict[str, Any]:
        return self.node.call("sources")

    def levels(self, refs: List[str]) -> Dict[str, Tuple[Any, str]]:
        return self.get(refs)

    def group_ramp(self, refs: List[str], target: float, rate: Optional[float] = None) -> str:
        return self.node.call("group_ramp", refs=list(refs), target=float(target), rate=rate)

    def group_stop(self, job: Optional[str] = None) -> None:
        self.node.post("group_stop", job=job)

    def ramp_status(self, job: str) -> Dict[str, Any]:
        return self.node.call("ramp_status", job=job)

    def fine_step(self, refs: List[str], direction: int) -> Dict[str, float]:
        return self.node.call("fine_step", refs=list(refs), direction=int(direction))

    def output(self, refs: List[str], on: bool) -> Dict[str, Any]:
        return self.node.call("output", refs=list(refs), on=bool(on))

    def vnas(self) -> List[Dict[str, Any]]:
        return self.node.call("vnas")

    def vna_catalog(self, name: str) -> Dict[str, List[str]]:
        return self.node.call("vna_catalog", name=name)

    def vna_trace(self, name: str, trace: str, create: bool = False) -> Tuple[np.ndarray, np.ndarray, str]:
        from .codec import dec_array

        r = self.node.call("vna_trace", name=name, trace=trace, create=create, timeout=180)
        return np.asarray(dec_array(r["x"]), float), np.asarray(dec_array(r["z"])), r.get("unit", "Hz")
