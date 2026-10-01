"""儀器能力（capability）介面。

量測程序只依賴「能力」，不依賴品牌型號：
    Source          可設定輸出的源（Yokogawa、Keithley、虛擬雙源…）
    TraceAcquirer   一次取回一條向量資料（VNA、SHFQC 頻譜、示波器…）
    ScalarMeter     一次讀回一個數值（電表、溫度計…）

新儀器只要實作對應能力，就能直接放進既有的量測程序，量測程式碼不用改。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, List, Optional

import numpy as np

from .safety import Limits, RampPolicy, ramp


# ---------------------------------------------------------------------------
class Source:
    """混入（mixin）到 Channel 或 Instrument。

    driver 需實作四個原始方法（不含安全檢查）：
        _write_level(v)  _read_level()  _write_output(on)  _read_output()
    安全檢查、斜坡、快取都由這裡統一處理。
    """

    unit: str = "A"
    limits: Limits
    ramp_policy: RampPolicy
    resolution: Optional[float] = None
    time_scale: float = 1.0   # 模擬模式由 Station 設為 sim_time_scale，讓斜坡加速

    def init_source(self, *, unit: str = "A", limits: Optional[Limits] = None,
                    ramp_policy: Optional[RampPolicy] = None, resolution: Optional[float] = None) -> None:
        self.unit = unit
        self.limits = limits or Limits()
        self.ramp_policy = ramp_policy or RampPolicy()
        self.resolution = resolution
        self._last_level: Optional[float] = None
        self.add_parameter("level", get=self.get_level, set=self.set_level, unit=unit)  # type: ignore[attr-defined]
        self.add_parameter("output", get=self.get_output, set=self.set_output)  # type: ignore[attr-defined]

    # ---- driver 實作 -------------------------------------------------------
    def _write_level(self, value: float) -> None:
        raise NotImplementedError

    def _read_level(self) -> float:
        raise NotImplementedError

    def _write_output(self, on: bool) -> None:
        raise NotImplementedError

    def _read_output(self) -> bool:
        raise NotImplementedError

    # ---- 公開 API -----------------------------------------------------------
    def get_level(self) -> float:
        v = float(self._read_level())
        self._last_level = v
        return v

    def set_level(self, value: float, ramp_if_needed: bool = True) -> None:
        """設定輸出。差值超過 ramp_policy.max_jump 時自動改走斜坡。"""
        self.guard_write()  # type: ignore[attr-defined]
        value = float(value)
        self.limits.check(value, self.full_name)  # type: ignore[attr-defined]
        if ramp_if_needed and self.ramp_policy.rate is not None and self.ramp_policy.max_jump is not None:
            start = self._last_level if self._last_level is not None else self.get_level()
            if self.ramp_policy.needs_ramp(value - start):
                self.ramp_to(value)
                return
        self._raw_set(value)

    def _raw_set(self, value: float) -> None:
        self._write_level(value)
        self._last_level = value

    def ramp_to(self, target: float, rate: Optional[float] = None,
                stop_event: Optional[threading.Event] = None,
                on_step: Optional[Callable[[float], None]] = None) -> float:
        self.guard_write()  # type: ignore[attr-defined]
        target = float(target)
        self.limits.check(target, self.full_name)  # type: ignore[attr-defined]
        rate = rate if rate is not None else self.ramp_policy.rate
        start = self.get_level()
        return ramp(self._raw_set, start, target, rate, dt=self.ramp_policy.dt,
                    stop_event=stop_event, on_step=on_step, time_scale=self.time_scale)

    def get_output(self) -> bool:
        return bool(self._read_output())

    def set_output(self, on: bool) -> None:
        self.guard_write()  # type: ignore[attr-defined]
        self._write_output(bool(on))

    def safe_shutdown(self, rate: Optional[float] = None) -> None:
        """斜坡歸零後關輸出。"""
        self.ramp_to(0.0, rate=rate)
        self.set_output(False)


# ---------------------------------------------------------------------------
@dataclass
class XAxis:
    values: np.ndarray
    name: str = "x"
    unit: str = ""


class TraceAcquirer:
    """一次取一條向量。name 用來選擇量哪一條（例如 VNA 的 "S21"）。"""

    def trace_names(self) -> List[str]:
        return []

    def configure(self, **settings) -> None:
        """套用量測設定（頻率範圍、點數…）。未知 key 應丟出 ConfigError。"""

    def x_axis(self, name: Optional[str] = None) -> XAxis:
        raise NotImplementedError

    def acquire(self, name: Optional[str] = None) -> np.ndarray:
        raise NotImplementedError

    def labber_settings(self, name: Optional[str] = None) -> List[list]:
        """給 Labber 匯出用的固定設定 [[名稱, 值, 單位], ...]；不需要就回傳空 list。"""
        return []


class ScalarMeter:
    unit: str = ""

    def read(self) -> float:
        raise NotImplementedError
