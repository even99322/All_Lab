"""電源群組（即時監控的 DC 群組）：多台電源一起斜坡、兩台微調、列出電源與預設群組。

本機（Station）與遠端節點（NodeService 指令）共用這些函式。

    list_sources(station)                    → 所有電源輸出（ref、目前值、解析度、上下限、輸出開關…）
    default_groups(station)                  → instruments.yaml 的虛擬雙電源（magnet_A: DC3+DC4）當預設群組
    GroupRamp(station, refs, target, rate)   → 背景執行緒：成員交錯前進（彼此差距不超過一步），可中止
    fine_step(station, refs, +1 / -1)        → 兩台時：只動一台一個最小步進（落後的那台先動）
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from .capabilities import Source
from .errors import ConfigError
from .station import Station


def source_refs(station: Station, name: str) -> List[str]:
    inst = station.instruments[name]
    if inst.options.get("sources"):
        return []                                    # 虛擬儀器（群組本身）
    out = []
    if isinstance(inst, Source):
        out.append(f"{name}.level")
    for ck, ch in getattr(inst, "channels", {}).items():
        if isinstance(ch, Source):
            out.append(f"{name}.{ck}.level")
    return out


def _src(station: Station, ref: str) -> Source:
    p = station.parameter(ref)
    if not isinstance(p.owner, Source):
        raise ConfigError(f"{ref} 不是電源輸出")
    return p.owner


def _fin(v: Any) -> Optional[float]:
    import math

    return float(v) if v is not None and math.isfinite(v) else None


def list_sources(station: Station, only_connected: bool = True) -> List[Dict[str, Any]]:
    out = []
    for n, inst in station.instruments.items():
        if only_connected and not inst.connected:
            continue
        for ref in source_refs(station, n):
            src = _src(station, ref)
            p = src.parameters["level"]  # type: ignore[attr-defined]
            outp = src.parameters.get("output")  # type: ignore[attr-defined]
            rp = src.ramp_policy
            label = inst.options.get("label", n)
            parts = ref.split(".")
            if len(parts) == 3 and len(source_refs(station, n)) > 1:
                label = f"{label} {parts[1]}"                 # 多通道電源（GS820）：DC5 ch1
            out.append({"ref": ref, "name": n, "label": label, "unit": src.unit or "A",
                        "level": p.cache, "output": outp.cache if outp is not None else None,
                        "resolution": getattr(src, "resolution", None) or 1e-6,
                        "limits": [_fin(src.limits.lo), _fin(src.limits.hi)],
                        "rate": rp.rate, "max_jump": rp.max_jump, "connected": bool(inst.connected),
                        "lease": station.lease_holder(n)})
    return out


def default_groups(station: Station) -> List[Dict[str, Any]]:
    """instruments.yaml 的虛擬雙電源（sources: [DC3, DC4]）→ 預設群組。"""
    groups = []
    for n, inst in station.instruments.items():
        srcs = inst.options.get("sources")
        if not srcs:
            continue
        refs = []
        for s in srcs:
            if s in station.instruments:
                r = source_refs(station, s)
                if r:
                    refs.append(r[0])
        if refs:
            groups.append({"name": inst.options.get("label", n), "members": refs, "virtual": n})
    return groups


def read_levels(station: Station, refs: List[str]) -> Dict[str, float]:
    return {r: float(station.parameter(r).get()) for r in refs}


def fine_step(station: Station, refs: List[str], direction: int) -> Dict[str, float]:
    """兩台電源：+1 → 目前較低的那台升一個最小步進；-1 → 較高的那台降一步（相同時動第一台）。"""
    if len(refs) != 2:
        raise ConfigError("微調只適用於剛好兩台的群組")
    lv = read_levels(station, refs)
    a, b = refs
    if direction > 0:
        who = a if lv[a] <= lv[b] else b
    else:
        who = a if lv[a] >= lv[b] else b
    src = _src(station, who)
    step = float(getattr(src, "resolution", None) or 1e-6)
    station.parameter(who).set(lv[who] + (step if direction > 0 else -step))
    return read_levels(station, refs)


class GroupRamp:
    """群組斜坡：成員輪流前進（每輪每台最多一步），彼此差距不超過一步。"""

    jobs: Dict[str, "GroupRamp"] = {}

    def __init__(self, station: Station, refs: List[str], target: float, rate: Optional[float] = None,
                 dt: float = 0.1) -> None:
        if not refs:
            raise ConfigError("群組沒有成員")
        self.id = uuid.uuid4().hex[:8]
        self.station, self.refs, self.target = station, list(refs), float(target)
        srcs = [_src(station, r) for r in refs]
        for s in srcs:
            if s.limits:
                s.limits.check(self.target)
        rates = [r for r in ([rate] if rate else [s.ramp_policy.rate for s in srcs]) if r]
        self.rate = float(min(rates)) if rates else 5e-4
        jumps = [s.ramp_policy.max_jump for s in srcs if s.ramp_policy.max_jump]
        self.dt = dt
        self.step = min([self.rate * dt] + jumps)
        self.stop_event = threading.Event()
        self.done = False
        self.error = ""
        self.levels: Dict[str, float] = {}
        self.started = time.time()
        GroupRamp.jobs[self.id] = self

    def start(self) -> "GroupRamp":
        threading.Thread(target=self._run, daemon=True, name=f"group-ramp-{self.id}").start()
        return self

    def stop(self) -> None:
        self.stop_event.set()

    def _run(self) -> None:
        try:
            st = self.station
            scale = st.sim_time_scale if st.simulate else 1.0
            lv = read_levels(st, self.refs)
            self.levels = dict(lv)
            while not self.stop_event.is_set():
                moved = False
                for r in self.refs:
                    d = self.target - lv[r]
                    if abs(d) < 1e-12:
                        continue
                    v = lv[r] + max(-self.step, min(self.step, d))
                    st.parameter(r).set(v)
                    lv[r] = v
                    self.levels[r] = v
                    moved = True
                if not moved:
                    break
                time.sleep(self.dt * scale)
        except Exception as e:  # noqa: BLE001
            self.error = str(e) or type(e).__name__
        finally:
            self.done = True

    def view(self) -> Dict[str, Any]:
        return {"id": self.id, "refs": self.refs, "target": self.target, "rate": self.rate, "done": self.done,
                "stopped": self.stop_event.is_set(), "error": self.error, "levels": dict(self.levels)}

    @classmethod
    def active(cls) -> List[Dict[str, Any]]:
        now = time.time()
        for k in [k for k, j in cls.jobs.items() if j.done and now - j.started > 600]:
            del cls.jobs[k]
        return [j.view() for j in cls.jobs.values() if not j.done]


def set_output(station: Station, refs: List[str], on: bool) -> Dict[str, Any]:
    out = {}
    for r in refs:
        src = _src(station, r)
        p = src.parameters.get("output")  # type: ignore[attr-defined]
        if p is None:
            raise ConfigError(f"{r} 沒有輸出開關")
        p.set(bool(on))
        out[r] = p.get() if p.gettable else bool(on)
    return out
