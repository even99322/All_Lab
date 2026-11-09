"""Yokogawa GS200 / GS610 / GS820 源（測量）單元。

指令依據（使用者手冊）：
    GS200  IM GS210-01EN  9th ed.  §13.2  （:SOURce:FUNCtion / :RANGe / :LEVel[:FIX]、:SOURce:PROTection、:OUTPut）
    GS610  IM 765501-01E  9th ed.  §16.2  （:SOURce:<func>:RANGe / :LEVel、:SOURce:VOLTage:PROTection:ULIMit/LLIMit、:SOURce:MODE）
    GS820  IM 765601-01E 10th ed.  §16.2  （[:CHANnel<n>]:SOURce:… 、[:CHANnel<n>]:OUTPut、[:CHANnel<n>]:MEASure?）

依手冊修正的重點（與舊 yoko_master.py 不同）：
  * GS820 的通道寫法是 :CHANnel<n>:SOURce:…（舊程式用 :SOURce<n>: 是錯的）。
  * GS610 / GS820 以電流源輸出時，限制器是「電壓」限制器（GS610 :SOUR:VOLT:PROT:ULIM/LLIM；
    GS820 :CHAN<n>:SOUR:VOLT:PROT:LEV）；舊程式對 GS610 寫到 CURR 限制器。
  * 設定值依目前量程的解析度量化（手冊規格表：例如 GS200 200 mA 檔 = 1 µA）。
  * 手冊警告：接線圈等感性負載時切換量程可能造成輸出跳脫、切換瞬間有 glitch；
    GS610 切換 function 會自動關閉輸出 → 輸出開啟時一律拒絕切換量程 / function（除非設定允許）。
  * 讀到 GS610/GS820 處於 auto range、sweep/list 模式或 pulse 波形時會警告。

所有 SCPI 字串都在類別的 SCPI 表裡，instruments.yaml 可用 scpi: {...} 逐條覆寫；
量程/解析度表也可用 ranges: {...} 覆寫。
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from ...core.capabilities import Source
from ...core.config import source_settings
from ...core.driverkit import ParamSpec, render
from ...core.errors import InstrumentError
from ...core.instrument import Channel, Instrument
from ...core.registry import register_driver

log = logging.getLogger(__name__)

# 量程 → (最大可輸出, 解析度)，單位 A 或 V（出自各手冊規格表）
GS200_RANGES = {
    "CURR": {1e-3: (1.2e-3, 10e-9), 10e-3: (12e-3, 100e-9), 100e-3: (120e-3, 1e-6), 200e-3: (200e-3, 1e-6)},
    "VOLT": {10e-3: (12e-3, 100e-9), 100e-3: (120e-3, 1e-6), 1.0: (1.2, 10e-6), 10.0: (12.0, 100e-6),
             30.0: (32.0, 1e-3)},
}
GS610_RANGES = {
    "CURR": {20e-6: (20.5e-6, 100e-12), 200e-6: (205e-6, 1e-9), 2e-3: (2.05e-3, 10e-9), 20e-3: (20.5e-3, 100e-9),
             200e-3: (205e-3, 1e-6), 0.5: (0.5, 10e-6), 1.0: (1.0, 10e-6), 2.0: (2.0, 10e-6), 3.0: (3.2, 10e-6)},
    "VOLT": {},
}
GS820_RANGES = {   # 查詢回傳值：0.5 A 檔回 600E-3、1 A 檔回 1.2、3 A 檔回 3.2
    "CURR": {200e-9: (200e-9, 1e-12), 2e-6: (2e-6, 10e-12), 20e-6: (20e-6, 100e-12), 200e-6: (200e-6, 1e-9),
             2e-3: (2e-3, 10e-9), 20e-3: (20e-3, 100e-9), 200e-3: (200e-3, 1e-6), 0.6: (0.6, 10e-6),
             1.2: (1.2, 10e-6), 3.2: (3.2, 10e-6)},
    "VOLT": {},
}


def _fmt_range(v: float, func: Optional[str]) -> str:
    if (func or "CURR") == "CURR":
        return f"{v * 1e3:g} mA" if v < 1 else f"{v:g} A"
    return f"{v * 1e3:g} mV" if v < 1 else f"{v:g} V"


def _norm_func(resp: str) -> str:
    return "CURR" if "CURR" in resp.upper() else "VOLT"


def _out_state(resp: str) -> str:
    r = resp.strip().upper()
    return "ON" if r in ("1", "ON") else "ZERO" if r.startswith("ZERO") else "OFF"


class GSChannel(Channel, Source):
    """一個源輸出通道。level / output 由 Source 處理（含上下限、斜坡），其餘為宣告式參數。"""

    PARAMS = [
        ParamSpec("function", label="源功能", kind="enum", choices=["CURR", "VOLT"],
                  doc="切換會影響輸出（GS610 會自動關閉輸出），輸出開啟時拒絕"),
        ParamSpec("range", label="源量程", unit="", doc="輸出開啟時拒絕切換（感性負載可能跳脫）"),
        ParamSpec("auto_range", label="自動量程", kind="bool"),
        ParamSpec("limiter", label="限制器", unit="", doc="電流源 → 電壓限制；電壓源 → 電流限制"),
    ]

    def __init__(self, parent: "YokogawaSMU", key: str, num: int) -> None:
        self.num = num
        self.func: Optional[str] = None
        self.range: Optional[float] = None
        self._configured_resolution: Optional[float] = None
        Channel.__init__(self, parent, key)
        cfg = source_settings(parent.options, key)
        self.init_source(**cfg)
        self._configured_resolution = cfg["resolution"]

    # ---- 基本 ---------------------------------------------------------------
    @property
    def dev(self) -> "YokogawaSMU":
        return self.parent  # type: ignore[return-value]

    def scpi_context(self) -> Dict[str, Any]:
        return {"ch": self.num, "func": self.func or "CURR"}

    def _cmd(self, key: str, **kw: Any) -> str:
        tpl = self.dev.scpi.get(key)
        if tpl is None:
            raise InstrumentError(f"{self.dev.driver_name} 不支援 {key}")
        return render(tpl, self.scpi_context(), **kw)

    def _need_func(self) -> str:
        if self.func is None:
            raise InstrumentError(f"{self.full_name} 尚未連線（function 未知）")
        return self.func

    def _output_on(self) -> bool:
        return self.get_output()

    def param_unit(self, name: str) -> str:
        """量程 / 限制器的單位隨源功能改變（電流源：量程 A、限制器 V）。"""
        cur = "A" if (self.func or "CURR") == "CURR" else "V"
        other = "V" if cur == "A" else "A"
        return {"range": cur, "limiter": other, "level": cur}.get(name, "")

    def param_choices(self, name: str) -> List[float]:
        """1.0.33：儀器控制視窗的量程下拉選單（目前源功能可用的量程）。"""
        if name != "range":
            return []
        return sorted(self.dev.ranges.get(self.func or "CURR", {}))

    def pick_range(self, value: float) -> Optional[float]:
        """最接近、且不小於 value 的量程（表裡沒有 → None）。"""
        table = sorted(self.dev.ranges.get(self.func or "CURR", {}))
        for r in table:
            if r >= abs(value) * (1 - 1e-9):
                return r
        return None

    # ---- 量程 / 解析度 ---------------------------------------------------------
    def range_info(self) -> Optional[Tuple[float, float]]:
        if self.range is None:
            return None
        table = self.dev.ranges.get(self._need_func(), {})
        if not table:
            return None
        key = min(table, key=lambda r: abs(math.log(r) - math.log(abs(self.range) or 1e-30)))
        return table[key]

    def _update_resolution(self) -> None:
        info = self.range_info()
        self.resolution = self._configured_resolution or (info[1] if info else None)

    def quantize(self, value: float) -> float:
        """依量程解析度取整（手冊：超出解析度的位數會被捨去）。"""
        if not self.resolution:
            return value
        return round(round(value / self.resolution) * self.resolution, 12)

    # ---- Source 原始操作 ---------------------------------------------------------
    def _write_level(self, value: float) -> None:
        info = self.range_info()
        if info is not None and abs(value) > info[0] * (1 + 1e-9):
            raise InstrumentError(f"{self.full_name} 設定 {value:g} 超出目前量程可輸出範圍 ±{info[0]:g}")
        self.dev.transport.write(self._cmd("level_w", value=f"{self.quantize(value):.9g}"))
        self.dev.after_write()

    def _read_level(self) -> float:
        self._need_func()
        return float(self.dev.transport.query(self._cmd("level_q")))

    def _write_output(self, on: bool) -> None:
        self.dev.transport.write(self._cmd("output_w", state="1" if on else "0"))
        self.dev.after_write()

    def _read_output(self) -> bool:
        return _out_state(self.dev.transport.query(self._cmd("output_q"))) == "ON"

    # ---- 宣告式參數的自訂讀寫 -------------------------------------------------------
    def _get_function(self) -> str:
        self.func = _norm_func(self.dev.transport.query(self._cmd("func_q")))
        self.unit = "A" if self.func == "CURR" else "V"
        return self.func

    def _set_function(self, func: str) -> None:
        func = _norm_func(func)
        if func == self.func:
            return
        if self._output_on() and not self.dev.options.get("allow_function_change_while_on", False):
            raise InstrumentError(f"{self.full_name} 輸出開啟中，拒絕切換源功能（手冊：切換會影響輸出）")
        self.dev.transport.write(self._cmd("func_w", func=func))
        self.func = func
        self.unit = "A" if func == "CURR" else "V"
        self._last_level = None
        self.get_range()

    def get_range(self) -> Optional[float]:
        try:
            self.range = float(self.dev.transport.query(self._cmd("range_q")))
        except (InstrumentError, ValueError):
            self.range = None
        self._update_resolution()
        return self.range

    def _get_range(self) -> Optional[float]:
        return self.get_range()

    def _set_range(self, value: float) -> None:
        if self._output_on() and not self.dev.options.get("allow_range_change_while_on", False):
            raise InstrumentError(f"{self.full_name} 輸出開啟中，拒絕切換量程（手冊：感性負載可能跳脫、輸出有 glitch）")
        self.dev.transport.write(self._cmd("range_w", value=f"{float(value):.6g}"))
        self.get_range()

    def _get_auto_range(self) -> Optional[bool]:
        if "auto_q" not in self.dev.scpi:
            return None
        return self.dev.transport.query(self._cmd("auto_q")).strip() in ("1", "ON")

    def _set_auto_range(self, on: bool) -> None:
        self.dev.transport.write(self._cmd("auto_w", state="1" if on else "0"))

    def _get_limiter(self) -> Optional[float]:
        key = "limit_curr_src_q" if self._need_func() == "CURR" else "limit_volt_src_q"
        if key not in self.dev.scpi:
            return None
        return float(self.dev.transport.query(self._cmd(key)))

    def _set_limiter(self, value: float) -> None:
        key = "limit_curr_src_w" if self._need_func() == "CURR" else "limit_volt_src_w"
        for tpl in self.dev.scpi_list(key):
            self.dev.transport.write(render(tpl, self.scpi_context(), value=f"{abs(float(value)):.6g}",
                                            neg=f"{-abs(float(value)):.6g}"))
        self.dev.after_write()

    # ---- 連線時讀狀態 ------------------------------------------------------------
    def read_state(self) -> List[str]:
        warnings = []
        self._get_function()
        self.parameters["level"].unit = self.unit
        self.get_range()
        self.get_level()
        for key, want, label in (("mode_q", "FIX", "源模式"), ("shape_q", "DC", "波形")):
            if key in self.dev.scpi:
                got = self.dev.transport.query(self._cmd(key)).strip().upper()
                if not got.startswith(want):
                    warnings.append(f"{self.full_name} {label} 為 {got}（預期 {want}），請在面板確認")
        if self._get_auto_range():
            warnings.append(f"{self.full_name} 開啟了自動量程；接電磁鐵時建議關閉（instruments.yaml: on_connect.auto_range: false）")
        return warnings


class YokogawaSMU(Instrument):
    N_CHANNELS = 1
    sim_driver = "sim.current_source"
    SCPI: Dict[str, Any] = {}
    RANGES: Dict[str, Dict[float, Tuple[float, float]]] = {}

    def __init__(self, name: str, **kw: Any) -> None:
        super().__init__(name, **kw)
        self.scpi: Dict[str, Any] = {**self.SCPI, **(self.options.get("scpi") or {})}
        self.ranges = {k: dict(v) for k, v in self.RANGES.items()}
        for func, table in (self.options.get("ranges") or {}).items():
            self.ranges[func.upper()] = {float(r): (float(v[0]), float(v[1])) for r, v in table.items()}
        n = int(self.options.get("n_channels", self.N_CHANNELS))
        for i in range(1, n + 1):
            self.add_channel(GSChannel(self, f"ch{i}", i))

    def scpi_list(self, key: str) -> List[str]:
        v = self.scpi.get(key)
        if v is None:
            raise InstrumentError(f"{self.driver_name} 不支援 {key}")
        return list(v) if isinstance(v, (list, tuple)) else [v]

    def after_write(self) -> None:
        if self.options.get("verify_writes", False):
            errs = self.check_errors()
            if errs:
                raise InstrumentError(f"[{self.name}] 儀器回報錯誤：{'; '.join(errs)}")

    def on_connect(self) -> None:
        cfg: Dict[str, Any] = self.options.get("on_connect") or {}
        errs = self.check_errors()   # 清掉開機前殘留的錯誤
        if errs:
            log.warning("[%s] 連線前殘留錯誤：%s", self.name, errs)
        for ch in self.channels.values():
            assert isinstance(ch, GSChannel)
            for w in ch.read_state():
                log.warning(w)
                if self.station is not None:
                    self.station.bus.log("⚠️ " + w, "warning")
            # 只有與目前狀態不同才寫入；會影響輸出的動作在 GSChannel 內有保護
            func = self.connect_function(cfg)
            if func and func != ch.func:
                try:
                    ch.parameters["function"].set(func)
                    msg = f"{ch.full_name} 原本是{'電壓' if func == 'CURR' else '電流'}模式，已切換成{'電流' if func == 'CURR' else '電壓'}模式"
                except InstrumentError as e:            # 輸出開著：不切換（切換會影響輸出），只提醒
                    msg = f"{ch.full_name} 沒有切換成{'電流' if func == 'CURR' else '電壓'}模式：{e}"
                log.warning(msg)
                if self.station is not None:
                    self.station.bus.log("⚠️ " + msg, "warning")
            if cfg.get("auto_range") is not None and ch._get_auto_range() not in (None, bool(cfg["auto_range"])):
                ch._set_auto_range(bool(cfg["auto_range"]))
            want = cfg.get("range") if cfg.get("range") is not None else self.connect_range(ch)
            if want is not None and (ch.range is None or abs(ch.range - float(want)) > 1e-12):
                try:
                    ch.parameters["range"].set(float(want))
                    msg = f"{ch.full_name} 量程設成 {_fmt_range(float(want), ch.func)}"
                    log.info(msg)
                    if self.station is not None:
                        self.station.bus.log("ℹ️ " + msg, "info")
                except InstrumentError as e:           # 輸出開著：不切換量程（感性負載可能跳脫），只提醒
                    msg = f"{ch.full_name} 量程沒有改成 {_fmt_range(float(want), ch.func)}：{e}"
                    log.warning(msg)
                    if self.station is not None:
                        self.station.bus.log("⚠️ " + msg, "warning")
            if cfg.get("limiter") is not None:
                ch.parameters["limiter"].set(float(cfg["limiter"]))
            if cfg.get("output") is not None and bool(cfg["output"]) != ch.get_output():
                ch.set_output(bool(cfg["output"]))
            ch.parameters["level"].unit = ch.unit

    def connect_range(self, ch: "GSChannel") -> Optional[float]:
        """1.0.33：連線時的預設量程。電流模式 → settings server.yokogawa_current_range（預設 0.2 A）；
        電壓模式 → server.yokogawa_voltage_range（預設不改）。"keep" / 空白 = 不改；instruments.yaml on_connect.range 優先。"""
        key = "server.yokogawa_current_range" if ch.func == "CURR" else "server.yokogawa_voltage_range"
        try:
            from ...settings import setting

            want = setting(key, 0.2 if ch.func == "CURR" else "keep")
        except Exception:  # noqa: BLE001
            want = 0.2 if ch.func == "CURR" else None
        if want is None or str(want).strip().lower() in ("", "keep", "none", "不改", "auto"):
            return None
        try:
            v = float(want)
        except (TypeError, ValueError):
            from ...core.units import parse_quantity

            v = parse_quantity(str(want), "A" if ch.func == "CURR" else "V")
        return ch.pick_range(v) if ch.param_choices("range") else v

    def connect_function(self, cfg: Dict[str, Any]) -> Optional[str]:
        """連線時要用的源功能：instruments.yaml on_connect.function → settings server.yokogawa_function（預設 CURR）。
        "keep" / 空白 = 不改。"""
        want = cfg.get("function")
        if want is None:
            try:
                from ...settings import setting

                want = setting("server.yokogawa_function", "CURR")
            except Exception:  # noqa: BLE001
                want = "CURR"
        want = str(want or "").strip()
        if not want or want.lower() in ("keep", "none", "不改"):
            return None
        return _norm_func(want)

    def snapshot(self) -> Dict[str, Any]:
        snap = super().snapshot()
        snap["resolution"] = {k: getattr(ch, "resolution", None) for k, ch in self.channels.items()}
        return snap


@register_driver("yokogawa.gs200")
class GS200(YokogawaSMU):
    """Yokogawa GS200 直流電壓 / 電流源（手冊 IM GS210-01EN §13.2；*IDN? 回報的型號碼是 GS210）"""
    IDN_PATTERN = r"YOKOGAWA.*GS2\d\d"
    #: GS200 實機確認的 USB VID / PID（*IDN? 失敗時掃描仍能認出）
    USB_IDS = [(0x0B21, 0x0039)]
    RANGES = GS200_RANGES
    SCPI = {
        "func_q": ":SOUR:FUNC?", "func_w": ":SOUR:FUNC {func}",
        "range_q": ":SOUR:RANG?", "range_w": ":SOUR:RANG {value}",
        "level_q": ":SOUR:LEV?", "level_w": ":SOUR:LEV {value}",
        "output_q": ":OUTP?", "output_w": ":OUTP {state}",
        # 電流源 → 電壓限制器；電壓源 → 電流限制器（§13.2.3 :SOURce:PROTection）
        "limit_curr_src_q": ":SOUR:PROT:VOLT?", "limit_curr_src_w": [":SOUR:PROT:VOLT {value}"],
        "limit_volt_src_q": ":SOUR:PROT:CURR?", "limit_volt_src_w": [":SOUR:PROT:CURR {value}"],
    }


@register_driver("yokogawa.gs610")
class GS610(YokogawaSMU):
    """IM 765501-01E §16.2"""
    IDN_PATTERN = r"YOKOGAWA.*(GS610|7655)"
    RANGES = GS610_RANGES
    SCPI = {
        "func_q": ":SOUR:FUNC?", "func_w": ":SOUR:FUNC {func}",
        "range_q": ":SOUR:{func}:RANG?", "range_w": ":SOUR:{func}:RANG {value}",
        "auto_q": ":SOUR:{func}:RANG:AUTO?", "auto_w": ":SOUR:{func}:RANG:AUTO {state}",
        "level_q": ":SOUR:{func}:LEV?", "level_w": ":SOUR:{func}:LEV {value}",
        "output_q": ":OUTP?", "output_w": ":OUTP {state}",
        "mode_q": ":SOUR:MODE?", "shape_q": ":SOUR:SHAP?",
        # 電流源時限制器是電壓限制器（手冊：the voltage limiter is activated when the source function is current）
        "limit_curr_src_q": ":SOUR:VOLT:PROT:ULIM?",
        "limit_curr_src_w": [":SOUR:VOLT:PROT:STAT 1", ":SOUR:VOLT:PROT:ULIM {value}", ":SOUR:VOLT:PROT:LLIM {neg}"],
        "limit_volt_src_q": ":SOUR:CURR:PROT:ULIM?",
        "limit_volt_src_w": [":SOUR:CURR:PROT:STAT 1", ":SOUR:CURR:PROT:ULIM {value}", ":SOUR:CURR:PROT:LLIM {neg}"],
    }


@register_driver("yokogawa.gs820")
class GS820(YokogawaSMU):
    """IM 765601-01E §16.2：所有通道指令以 :CHANnel<n> 開頭"""
    IDN_PATTERN = r"YOKOGAWA.*(GS8\d\d|7656)"
    N_CHANNELS = 2
    RANGES = GS820_RANGES
    SCPI = {
        "func_q": ":CHAN{ch}:SOUR:FUNC?", "func_w": ":CHAN{ch}:SOUR:FUNC {func}",
        "range_q": ":CHAN{ch}:SOUR:{func}:RANG?", "range_w": ":CHAN{ch}:SOUR:{func}:RANG {value}",
        "auto_q": ":CHAN{ch}:SOUR:{func}:RANG:AUTO?", "auto_w": ":CHAN{ch}:SOUR:{func}:RANG:AUTO {state}",
        "level_q": ":CHAN{ch}:SOUR:{func}:LEV?", "level_w": ":CHAN{ch}:SOUR:{func}:LEV {value}",
        "output_q": ":CHAN{ch}:OUTP?", "output_w": ":CHAN{ch}:OUTP {state}",
        "mode_q": ":CHAN{ch}:SOUR:MODE?", "shape_q": ":CHAN{ch}:SOUR:SHAP?",
        "limit_curr_src_q": ":CHAN{ch}:SOUR:VOLT:PROT:LEV?",
        "limit_curr_src_w": [":CHAN{ch}:SOUR:VOLT:PROT:STAT 1", ":CHAN{ch}:SOUR:VOLT:PROT:LEV {value}"],
        "limit_volt_src_q": ":CHAN{ch}:SOUR:CURR:PROT:LEV?",
        "limit_volt_src_w": [":CHAN{ch}:SOUR:CURR:PROT:STAT 1", ":CHAN{ch}:SOUR:CURR:PROT:LEV {value}"],
        "measure_q": ":CHAN{ch}:MEAS?",
    }

    def __init__(self, name: str, **kw: Any) -> None:
        super().__init__(name, **kw)
        for ch in self.channels.values():
            ch.add_parameter("measured", get=lambda ch=ch: float(self.transport.query(ch._cmd("measure_q"))),
                             snapshot=False)


