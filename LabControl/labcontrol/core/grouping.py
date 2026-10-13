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
            s = str(s)
            base = s.split(".")[0]
            if base not in station.instruments:
                continue
            if "." in s:                                     # 通道：GS820 的 DC5.ch1
                refs.append(f"{s}.level")
                continue
            r = source_refs(station, s)
            if r:
                refs.append(r[0])
        if refs:
            groups.append({"name": inst.options.get("label", n), "members": refs, "virtual": n})
    return groups


def read_levels(station: Station, refs: List[str]) -> Dict[str, float]:
    return {r: float(station.parameter(r).get()) for r in refs}


def fine_step(station: Station, refs: List[str], direction: int, step: Optional[float] = None) -> Dict[str, float]:
    """微調。兩台：+1 → 目前較低的那台升一步；-1 → 較高的那台降一步（相同時動第一台）。一台：直接升 / 降一步。
    step = 一步多大（SI 單位；0.0.28 起可選，像網頁版選位數）；None = 儀器的最小步進。
    超過儀器限制時拒絕（limits）；不會小於最小步進，也會對齊最小步進。"""
    if len(refs) not in (1, 2):
        raise ConfigError("微調只適用於一台或兩台的群組")
    lv = read_levels(station, refs)
    if len(refs) == 1:
        who = refs[0]
    else:
        a, b = refs
        if direction > 0:
            who = a if lv[a] <= lv[b] else b
        else:
            who = a if lv[a] >= lv[b] else b
    src = _src(station, who)
    res = float(getattr(src, "resolution", None) or 1e-6)
    size = res if not step else max(res, round(abs(float(step)) / res) * res)
    target = lv[who] + (size if direction > 0 else -size)
    if src.limits:
        src.limits.check(target)
    station.parameter(who).set(target)
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
        self.rate = float(min(rates)) if rates else 1e-2
        jumps = [s.ramp_policy.max_jump for s in srcs if s.ramp_policy.max_jump]
        self.dt = dt
        self.max_jump = min(jumps) if jumps else None       # 一次寫入最多動多少（每台）
        self.step = min([self.rate * dt] + jumps)            # 名義步進（舊的欄位，view 用）
        self.actual_rate: Optional[float] = None             # 實際速率（A/s，從開始到現在）
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
        """0.0.31：依「實際經過的時間」前進（以前每步固定 rate×dt，再加上每次寫入儀器的時間，
        10 mA/s 實際只有 5 mA/s 左右）。每一輪：可以走 rate×經過時間（不超過 max_jump），每台輪流前進。
        儀器寫入太慢、max_jump 又小時，最快只能到 max_jump / 每輪時間（actual_rate 會顯示實際速率）。"""
        try:
            st = self.station
            scale = st.sim_time_scale if st.simulate else 1.0
            lv = read_levels(st, self.refs)
            self.levels = dict(lv)
            start = dict(lv)
            # 每輪間隔：max_jump 小時要走得更勤（例如 0.05 mA、10 mA/s → 每 5 ms 一輪），最短 2 ms
            tick = self.dt if not self.max_jump else max(0.002, min(self.dt, self.max_jump / self.rate))
            t0 = last = time.monotonic()
            first = True
            while not self.stop_event.is_set():
                now = time.monotonic()
                if scale <= 0:
                    budget = float("inf")
                else:
                    budget = self.rate * (tick if first else (now - last)) / scale
                first = False
                last = now
                step = budget if self.max_jump is None else min(budget, self.max_jump)
                moved = False
                for r in self.refs:
                    d = self.target - lv[r]
                    if abs(d) < 1e-12:
                        continue
                    v = self.target if abs(d) <= step else lv[r] + (step if d > 0 else -step)
                    st.parameter(r).set(v)
                    lv[r] = v
                    self.levels[r] = v
                    moved = True
                el = time.monotonic() - t0
                if el > 0 and scale > 0:
                    self.actual_rate = max(abs(lv[r] - start[r]) for r in self.refs) / el * scale
                if not moved:
                    break
                wait = tick * max(scale, 0) - (time.monotonic() - now)
                if wait > 0:
                    time.sleep(wait)
        except Exception as e:  # noqa: BLE001
            self.error = str(e) or type(e).__name__
        finally:
            self.done = True

    def view(self) -> Dict[str, Any]:
        return {"id": self.id, "refs": self.refs, "target": self.target, "rate": self.rate,
                "actual_rate": self.actual_rate, "done": self.done,
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
