"""安全機制：上下限與斜坡（ramp）。

所有 Source 的寫入都經過這裡，不論是量測引擎、web 面板還是 Jupyter 腳本，
同一台儀器都套用同一組限制。限制寫在 LAB/instruments.yaml，不寫死在程式裡。
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .errors import LimitError


@dataclass
class Limits:
    lo: float = -math.inf
    hi: float = math.inf

    def contains(self, value: float) -> bool:
        return self.lo <= value <= self.hi

    def check(self, value: float, who: str = "") -> None:
        if not (self.lo <= value <= self.hi):
            raise LimitError(f"{who} 設定值 {value:g} 超出安全範圍 [{self.lo:g}, {self.hi:g}]")


@dataclass
class RampPolicy:
    """rate: 單位/秒（電流源就是 A/s）。None 表示不限速。
    max_jump: 單次允許直接跳的最大差值；超過就自動改走斜坡。None 表示永遠直接跳。
    dt: 斜坡每一步的時間間隔（秒）。
    """

    rate: Optional[float] = None
    max_jump: Optional[float] = None
    dt: float = 0.1

    def needs_ramp(self, delta: float) -> bool:
        return self.rate is not None and self.max_jump is not None and abs(delta) > self.max_jump


def ramp(
    setter: Callable[[float], None],
    start: float,
    target: float,
    rate: float,
    dt: float = 0.1,
    stop_event: Optional[threading.Event] = None,
    on_step: Optional[Callable[[float], None]] = None,
    decimals: int = 12,
    time_scale: float = 1.0,
) -> float:
    """以固定速率從 start 線性走到 target。回傳最後實際設定的值。

    stop_event 被 set 時會停在目前位置（不會跳到 target），回傳當下的值。
    time_scale < 1 只縮短實際等待時間（模擬模式加速用），不改變步進序列。
    """
    delta = target - start
    if rate is None or rate <= 0 or abs(delta) == 0:
        setter(target)
        return target
    total_t = abs(delta) / rate
    steps = max(1, int(math.ceil(total_t / dt)))
    step_dt = total_t / steps * time_scale
    value = start
    t0 = time.monotonic()
    for k in range(1, steps + 1):
        if stop_event is not None and stop_event.is_set():
            return value
        value = target if k == steps else round(start + delta * k / steps, decimals)
        setter(value)
        if on_step:
            on_step(value)
        # 以絕對時間排程，避免 VISA 延遲累積讓斜坡越走越慢
        remain = t0 + k * step_dt - time.monotonic()
        if remain > 0 and k < steps:
            time.sleep(remain)
    return value
