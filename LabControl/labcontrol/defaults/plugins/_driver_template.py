"""新儀器 driver 範本（Lab Control 驅動規範，完整說明見 docs/DRIVER_GUIDE.md）。

使用方式：
  1. 複製這個檔案，改名為不以底線開頭的名稱（例如 my_scope.py），放在 LAB/plugins/。
     （底線開頭的檔案不會被載入。）
  2. 依儀器手冊填寫 PARAMS 參數表與 SCPI 字串（每一條請在註解寫上手冊章節）。
  3. 在 LAB/instruments.yaml 加一段：
         SCOPE1:
           driver: mylab.my_instrument
           address: TCPIP0::192.168.1.20::INSTR
  4. 重新開啟 Lab Control：方塊庫會自動出現這台儀器。
     先用  python -m labcontrol check  連線確認，再接入量測。

規範重點：
  * 所有可調的值寫成 ParamSpec（上下限、單位、預設值、是否可掃描），不要在程式裡寫死。
  * instruments.yaml 可以不改程式就覆寫：parameters.<name>.{label, limits, default, get, set, …}
  * on_connect() 只讀狀態，不改變輸出（避免重開程式時電流 / 功率跳動）。
  * 會影響輸出的動作（切換量程、功能）要有保護，並寫清楚理由。
  * 寫完一定要有 sim_driver 或模擬測試，讓沒有硬體的電腦也能跑。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from labcontrol.core import Instrument, ParamSpec, register_driver
from labcontrol.core.capabilities import TraceAcquirer, XAxis
from labcontrol.core.errors import ConfigError


@register_driver("mylab.my_instrument")          # ← 驅動名稱：<廠牌>.<型號>，全小寫
class MyInstrument(Instrument, TraceAcquirer):   # 量測儀器繼承 TraceAcquirer；電源請看 Source（見指南）
    MEASURE_KIND = "scope"                       # 量測種類：方塊庫分類、互斥規則、估時用
    TRACES = ["CH1", "CH2"]                      # 可量的通道
    ERROR_QUERY = ":SYST:ERR?"                   # 錯誤佇列查詢；不支援就設 None
    default_timeout_ms = 10000
    sim_driver = None                            # 有模擬 driver 就填名稱，--sim 會自動換掉

    PARAMS = [
        # name          顯示名稱        SI 單位     UI 單位           SCPI 查詢 / 設定樣板（{value} = 寫入值）
        ParamSpec("timebase", label="時基", unit="s", display_unit="us",
                  get=":TIM:SCAL?", set=":TIM:SCAL {value}", limits=(1e-9, 10),
                  default="10 us", measure_setting=True, sweepable=True,
                  doc="手冊 §x.y :TIMebase:SCALe"),
        ParamSpec("points", label="點數", kind="int", limits=(100, 1_000_000),
                  get=":ACQ:POIN?", set=":ACQ:POIN {value}", default=1000, measure_setting=True),
        ParamSpec("averages", label="平均次數", kind="int", limits=(1, 10000), default=1, measure_setting=True),
    ]

    def __init__(self, name: str, **kw: Any) -> None:
        super().__init__(name, **kw)              # 這一步會依 PARAMS 建立 parameters（含 yaml 覆寫）
        self._averages = 1

    def scpi_context(self) -> Dict[str, Any]:
        return {}                                # SCPI 樣板用得到的變數，例如 {"ch": 1}

    def on_connect(self) -> None:
        self.check_errors()                      # 只讀狀態、清錯誤佇列，不改變輸出

    # ---- 樣板表達不了的參數：寫 _get_<name> / _set_<name> ----------------------------
    def _get_averages(self) -> int:
        return self._averages

    def _set_averages(self, n: int) -> None:
        self.transport.write(f":ACQ:AVER {int(n)}")
        self._averages = int(n)

    # ---- TraceAcquirer -----------------------------------------------------------
    def configure(self, **settings: Any) -> None:
        unknown = set(settings) - {k for k, p in self.parameters.items() if p.settable}
        if unknown:
            raise ConfigError(f"[{self.name}] 不認得的設定：{sorted(unknown)}")
        self.guard_write()
        for k, v in settings.items():
            self.parameters[k].set(v)

    def x_axis(self, name: Optional[str] = None) -> XAxis:
        n = int(self.parameters["points"].get())
        dt = float(self.parameters["timebase"].get()) * 10 / n
        return XAxis(np.arange(n) * dt, "Time", "s")

    def acquire(self, name: Optional[str] = None) -> np.ndarray:
        ch = name or self.TRACES[0]
        self.transport.write(":SING")
        self.transport.query("*OPC?")
        text = self.transport.query(f":WAV:SOUR {ch};:WAV:DATA?")
        return np.array([float(x) for x in text.split(",")])

    def labber_settings(self, name: Optional[str] = None) -> List[list]:
        return [[p.spec.label or k, p.cache, p.unit] for k, p in self.parameters.items() if p.spec]
