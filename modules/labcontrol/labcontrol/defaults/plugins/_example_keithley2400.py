"""範例：Keithley 2400 電流源（Source 類儀器的寫法）。

⚠ 範例未在實機驗證。要使用時：改名為 keithley2400.py（去掉底線）並對照 Keithley 2400 手冊確認指令。

    instruments:
      K1:
        driver: keithley.k2400
        address: GPIB0::24::INSTR
        source: {limits: [-0.01, 0.01], ramp_rate: 1.0e-4, max_jump: 1.0e-5}
        parameters:
          compliance: {limits: [0, 21]}

之後 K1 就能用在任何量測：方塊庫出現「DC set · 單台電源」、網頁面板也會自動出現它。
"""
from typing import Any, Dict

from labcontrol.core import Channel, Instrument, ParamSpec, Source, register_driver, source_settings


class K2400Channel(Channel, Source):
    # level / output 由 Source 管理（上下限、斜坡、lease），這裡只宣告其他參數
    PARAMS = [
        ParamSpec("compliance", label="電壓限制", unit="V", limits=(0, 210),
                  get=":SENS:VOLT:PROT?", set=":SENS:VOLT:PROT {value}"),
        ParamSpec("measured", label="量測電壓", unit="V", get=":READ?", snapshot=False),
    ]

    def __init__(self, parent: "Keithley2400", key: str) -> None:
        Channel.__init__(self, parent, key)
        self.init_source(**source_settings(parent.options, key))

    def _get_measured(self) -> float:        # :READ? 回傳 V,I,R,t,status → 取第一個
        return float(self.transport.query(":READ?").split(",")[0])

    def _write_level(self, v: float) -> None:
        self.transport.write(f":SOUR:CURR:LEV {v:.9g}")

    def _read_level(self) -> float:
        return float(self.transport.query(":SOUR:CURR:LEV?"))

    def _write_output(self, on: bool) -> None:
        self.transport.write(f":OUTP {'ON' if on else 'OFF'}")

    def _read_output(self) -> bool:
        return self.transport.query(":OUTP?").strip() in ("1", "ON")


@register_driver("keithley.k2400")
class Keithley2400(Instrument):
    IDN_PATTERN = r"KEITHLEY.*2400"
    sim_driver = "sim.current_source"      # --sim 時自動換成模擬電流源

    def __init__(self, name: str, **kw: Any) -> None:
        super().__init__(name, **kw)
        self.add_channel(K2400Channel(self, "ch1"))

    def on_connect(self) -> None:
        self.check_errors()
        self.channels["ch1"].get_level()   # 只讀，不改變輸出
