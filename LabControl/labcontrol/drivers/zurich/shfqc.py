"""Zurich Instruments SHFQC —— 參數表已定義（量測方案編輯器可用），實機通訊待實作（v1.x）。

--sim 時自動換成 sim.shfqc（參數表相同）。實作時沿用 shfqc_app 既有的 zhinst-toolkit 程式碼：
覆寫 connect / configure / x_axis / acquire 即可，參數表不用改。
"""
from __future__ import annotations

from typing import Any, List, Optional

import numpy as np

from ...core.capabilities import TraceAcquirer, XAxis
from ...core.driverkit import ParamSpec
from ...core.errors import ConfigError
from ...core.instrument import Instrument
from ...core.registry import register_driver


@register_driver("zi.shfqc")
class SHFQC(Instrument, TraceAcquirer):
    sim_driver = "sim.shfqc"
    MEASURE_KIND = "shfqc"
    TRACES = ["QA0", "QA1"]
    ERROR_QUERY = None
    PARAMS = [
        ParamSpec("center_freq", label="中心頻率", unit="Hz", display_unit="GHz", default="5.0248 GHz",
                  measure_setting=True, sweepable=True),
        ParamSpec("span", label="頻寬(span)", unit="Hz", display_unit="MHz", default="10 MHz",
                  measure_setting=True, sweepable=True),
        ParamSpec("points", label="點數", kind="int", limits=(1, 100000), default=501, measure_setting=True),
        ParamSpec("power", label="功率", unit="dBm", display_unit="dBm", default="-30 dBm",
                  measure_setting=True, sweepable=True),
        ParamSpec("averages", label="平均次數", kind="int", limits=(1, 1 << 20), default=1, measure_setting=True),
        ParamSpec("integration_time", label="積分時間", unit="s", display_unit="us", default="10 us",
                  measure_setting=True, sweepable=True),
    ]

    def open_transport(self):
        return None   # 走 zhinst-toolkit，不用 VISA

    def connect(self) -> None:
        raise ConfigError(f"[{self.name}] SHFQC 實機 driver 尚未實作（v1.x，接 shfqc_app 的 zhinst 程式碼）；請先用模擬模式")

    def configure(self, **settings: Any) -> None:  # pragma: no cover - 待實作
        raise NotImplementedError

    def trace_names(self) -> List[str]:
        return self.trace_list()

    def x_axis(self, name: Optional[str] = None) -> XAxis:  # pragma: no cover
        raise NotImplementedError

    def acquire(self, name: Optional[str] = None) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError
