"""Rohde & Schwarz R&S ZNA 向量網路分析儀。

指令依據：R&S ZNA User Manual 1178.6462.02 – 41，第 7 章 Command reference。
    [SENSe<Ch>:]FREQuency:STARt / STOP / CENTer / SPAN
    [SENSe<Ch>:]SWEep:POINts、[SENSe<Ch>:]SWEep:COUNt、[SENSe<Ch>:]SWEep:TIME?
    [SENSe<Ch>:]BANDwidth[:RESolution]   （儀器會把 IF 頻寬捨入到 1-1.5-2-3-5-7 級距 → 設定後讀回）
    [SENSe<Ch>:]AVERage[:STATe] / :COUNt / :CLEar
    SOURce<Ch>:POWer<Pt>[:LEVel][:IMMediate][:AMPlitude]
    OUTPut<Ch>[:STATe]
    INITiate<Ch>:CONTinuous、INITiate<Ch>[:IMMediate][:DUMMy]、*OPC / *ESR?
    CALCulate<Ch>:PARameter:CATalog?        （回傳 'Trc1,S21,Trc2,S11'，依通道）
    CALCulate:DATA:TRACe? '<Trace>', SDATa  （讀指定 trace，不改變 active trace）
    CALCulate<Chn>:DATA:STIMulus?           （實際頻率點）
    FORMat[:DATA] ASCii | REAL,64、FORMat:BORDer SWAPped
    SYSTem:ERRor[:NEXT]?

與舊 RSVNA 的差異：
  * 通道號可設定（channel，預設 1），所有指令帶通道 suffix。
  * 讀資料改用 CALC:DATA:TRAC? '<trace>', SDAT（不需先 SEL 改 active trace）。
  * 平均開啟時，每次量測前送 [SENS]:AVER:CLE（手冊：starts a new average cycle），
    避免上一個電流點的掃描被平均進來。
  * x 軸使用儀器回傳的實際刺激值（CALC:DATA:STIM?），分段掃描也正確。
  * IF 頻寬、功率等設定後讀回實際值寫入檔案。
  * 可選 REAL,64 二進位傳輸（data_format: real64）；預設 ASCII。
  * *OPC 逾時丟 InstrumentTimeout（引擎會重試或暫停），不再讀回可能過期的資料。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import numpy as np

from ...core.capabilities import TraceAcquirer, XAxis
from ...core.driverkit import ParamSpec, render
from ...core.errors import ConfigError, InstrumentError, InstrumentTimeout
from ...core.instrument import Instrument
from ...core.registry import register_driver


@register_driver("rs.zna")
class RohdeSchwarzZNA(Instrument, TraceAcquirer):
    sim_driver = "sim.vna"
    MEASURE_KIND = "vna"
    TRACES = ["S21", "S11", "S12", "S22"]
    IDN_PATTERN = r"Rohde.*ZNA"
    default_timeout_ms = 10000
    ERROR_QUERY = "SYST:ERR?"

    PARAMS = [
        ParamSpec("start_freq", label="起始頻率", unit="Hz", display_unit="GHz",
                  get=":SENS{ch}:FREQ:STAR?", set=":SENS{ch}:FREQ:STAR {value}",
                  default="5.0198 GHz", measure_setting=True, sweepable=True),
        ParamSpec("stop_freq", label="終止頻率", unit="Hz", display_unit="GHz",
                  get=":SENS{ch}:FREQ:STOP?", set=":SENS{ch}:FREQ:STOP {value}",
                  default="5.0298 GHz", measure_setting=True, sweepable=True),
        ParamSpec("center_freq", label="中心頻率", unit="Hz", display_unit="GHz",
                  get=":SENS{ch}:FREQ:CENT?", set=":SENS{ch}:FREQ:CENT {value}", sweepable=True, snapshot=False),
        ParamSpec("span", label="頻寬(span)", unit="Hz", display_unit="MHz",
                  get=":SENS{ch}:FREQ:SPAN?", set=":SENS{ch}:FREQ:SPAN {value}", sweepable=True, snapshot=False),
        ParamSpec("points", label="點數", kind="int", limits=(1, 100001),
                  get=":SENS{ch}:SWE:POIN?", set=":SENS{ch}:SWE:POIN {value}", default=501, measure_setting=True),
        ParamSpec("power", label="功率", unit="dBm", display_unit="dBm",
                  get=":SOUR{ch}:POW?", set=":SOUR{ch}:POW {value}", default="-10 dBm", readback=True,
                  measure_setting=True, sweepable=True, doc="可用範圍與頻率有關（見 data sheet），請在 instruments.yaml 設 limits"),
        ParamSpec("if_bw", label="IF 頻寬", unit="Hz", display_unit="kHz", limits=(1, 30e6),
                  get=":SENS{ch}:BAND?", set=":SENS{ch}:BAND {value}", default="10 kHz", readback=True,
                  measure_setting=True, sweepable=True, doc="儀器會捨入到 1-1.5-2-3-5-7 級距"),
        ParamSpec("averages", label="平均次數", kind="int", limits=(0, 1000), default=1, measure_setting=True,
                  doc="0 = 關閉平均；n ≥ 1 = 開啟並設 AVER:COUN 與 SWE:COUN"),
        ParamSpec("output", label="RF 輸出", kind="bool", get=":OUTP?", set=":OUTP {value}", snapshot=True),
        ParamSpec("sweep_time", label="掃描時間", unit="s", display_unit="s", limits=(0, 1e5),
                  get=":SENS{ch}:SWE:TIME?", set=":SENS{ch}:SWE:TIME {value}",
                  doc="設定後儀器改用固定掃描時間（自動關閉）；低於儀器最短時間會被調成最短"),
        ParamSpec("sweep_time_auto", label="自動掃描時間", kind="bool",
                  get=":SENS{ch}:SWE:TIME:AUTO?", set=":SENS{ch}:SWE:TIME:AUTO {value}",
                  doc="ON = 儀器依點數 / IF 頻寬自動用最短掃描時間"),
    ]

    def __init__(self, name: str, **kw: Any) -> None:
        super().__init__(name, **kw)
        self.channel = int(self.options.get("channel", 1))
        self.opc_timeout_s = float(self.options.get("opc_timeout_s", 120))
        self.poll_s = float(self.options.get("opc_poll_s", 0.05))
        self.data_format = str(self.options.get("data_format", "ascii")).lower()
        self.clear_average = bool(self.options.get("clear_average_each_point", True))
        self.restore_continuous = bool(self.options.get("restore_continuous", True))
        self._averages: Optional[int] = None
        self._stimulus: Dict[str, np.ndarray] = {}

    def scpi_context(self) -> Dict[str, Any]:
        return {"ch": self.channel}

    def _w(self, tpl: str, **kw: Any) -> None:
        self.transport.write(render(tpl, self.scpi_context(), **kw))

    def _q(self, tpl: str, **kw: Any) -> str:
        return self.transport.query(render(tpl, self.scpi_context(), **kw))

    def on_connect(self) -> None:
        errs = self.check_errors()
        if errs and self.station is not None:
            self.station.bus.log(f"⚠️ [{self.name}] 連線前儀器錯誤佇列：{errs}", "warning")
        if self.data_format == "real64":
            self._w("FORM:BORD SWAP")
        try:
            self._averages = int(float(self._q(":SENS{ch}:AVER:COUN?"))) if \
                self._q(":SENS{ch}:AVER?").strip() in ("1", "ON") else 0
        except Exception:  # noqa: BLE001
            self._averages = None

    # ---- 平均（組合指令）--------------------------------------------------------
    def _get_averages(self) -> Optional[int]:
        return self._averages

    def _set_averages(self, n: int) -> None:
        n = int(n)
        if n >= 1:
            self._w(":SENS{ch}:AVER:COUN {n}", n=n)
            self._w(":SENS{ch}:AVER ON")
            self._w(":SENS{ch}:SWE:COUN {n}", n=n)
        else:
            self._w(":SENS{ch}:AVER OFF")
            self._w(":SENS{ch}:SWE:COUN 1")
        self._averages = n

    # ---- 設定 --------------------------------------------------------------------
    def configure(self, **settings: Any) -> None:
        """依參數表設定（未知 key → ConfigError）。output 預設開啟。"""
        settable = {k for k, p in self.parameters.items() if p.settable}
        unknown = set(settings) - settable
        if unknown:
            raise ConfigError(f"[{self.name}] 不認得的 VNA 設定：{sorted(unknown)}（可用：{sorted(settable)}）")
        self.guard_write()
        order = ["start_freq", "stop_freq", "center_freq", "span", "points", "if_bw", "power", "averages", "output"]
        for k in sorted(settings, key=lambda k: order.index(k) if k in order else 99):
            if settings[k] is not None:
                self.parameters[k].set(settings[k])
        if "output" not in settings and self.options.get("output_on_configure", True):
            self.parameters["output"].set(True)
        self._stimulus.clear()
        errs = self.check_errors()
        if errs:
            raise InstrumentError(f"[{self.name}] 設定後儀器回報錯誤：{'; '.join(errs)}")

    # ---- TraceAcquirer -------------------------------------------------------------
    def trace_catalog(self) -> Dict[str, List[str]]:
        """量測參數 → trace 名稱（CALC<Ch>:PAR:CAT? → 'Trc1,S21,Trc2,S11'）。"""
        cat = self._q("CALC{ch}:PAR:CAT?").strip().strip("'\"")
        items = [x.strip() for x in cat.split(",") if x.strip()]
        out: Dict[str, List[str]] = {}
        for i in range(0, len(items) - 1, 2):
            out.setdefault(items[i + 1].upper(), []).append(items[i])
        return out

    def trace_names(self) -> List[str]:
        return self.trace_list()

    def _trace_name(self, param: str) -> str:
        mapping = self.options.get("trace_map") or {}
        if param in mapping:
            return mapping[param]
        cat = self.trace_catalog()
        if param.upper() not in cat:
            if self.options.get("auto_create_trace", False):
                tname = f"LC_{param}"
                self._w("CALC{ch}:PAR:SDEF '{t}', '{p}'", t=tname, p=param)
                return tname
            raise InstrumentError(f"[{self.name}] 通道 {self.channel} 上沒有 {param} 的 trace（目前：{cat}）；"
                                  f"請在 ZNA 上建立，或在 instruments.yaml 設 auto_create_trace: true")
        return cat[param.upper()][-1]

    def x_axis(self, name: Optional[str] = None) -> XAxis:
        name = name or self.trace_list()[0]
        if name not in self._stimulus:
            with self.transport.lock:
                t = self._trace_name(name)
                self._w("CALC{ch}:PAR:SEL '{t}'", t=t)
                self._stimulus[name] = self._read_numbers("CALC{ch}:DATA:STIM?")
        return XAxis(self._stimulus[name], "Frequency", "Hz")

    def _read_numbers(self, tpl: str, **kw: Any) -> np.ndarray:
        """讀數值陣列。每次都先送 FORMat：儀器的資料格式是全域設定，可能被其他程式 / 前面板改成二進位
        （0.0.5 實機報告：FORM 停在 REAL 時 CALC:DATA:STIM? 回傳二進位 → 'ascii' codec can't decode）。"""
        cmd = render(tpl, self.scpi_context(), **kw)
        with self.transport.lock:
            if self.data_format == "real64":
                res = getattr(self.transport, "resource", None)
                if res is None:
                    raise ConfigError("real64 需要 VISA transport")
                self._w("FORM REAL,64")
                self._w("FORM:BORD SWAP")
                return np.asarray(res.query_binary_values(cmd, datatype="d", is_big_endian=False,
                                                          container=np.array))
            self._w("FORM ASC")
            return np.array(self.transport.query(cmd).split(","), dtype=float)

    def acquire(self, name: Optional[str] = None) -> np.ndarray:
        name = name or self.trace_list()[0]
        t = self.transport
        with t.lock:   # 整個量測序列不可被其他執行緒插入指令
            trace = self._trace_name(name)
            self._w("INIT{ch}:CONT OFF")
            if self.clear_average and (self._averages or 0) >= 1:
                self._w(":SENS{ch}:AVER:CLE")
            t.write("*CLS")
            self._w("INIT{ch}:IMM")
            t.write("*OPC")
            t0 = time.monotonic()
            while True:
                try:
                    esr = t.query("*ESR?").strip()
                    if esr.lstrip("+").isdigit() and (int(esr) & 1):
                        break
                except Exception:  # noqa: BLE001 - 掃描中查詢可能暫時逾時
                    pass
                if time.monotonic() - t0 > self.opc_timeout_s:
                    if self.restore_continuous:
                        self._w("INIT{ch}:CONT ON")
                    raise InstrumentTimeout(f"[{self.name}] 等待掃描完成超過 {self.opc_timeout_s:.0f} s")
                time.sleep(self.poll_s)
            arr = self._read_numbers("CALC:DATA:TRAC? '{t}', SDAT", t=trace)
            if self.restore_continuous:
                self._w("INIT{ch}:CONT ON")
        if arr.size % 2:
            raise InstrumentError(f"[{self.name}] SDATa 資料長度 {arr.size} 不是偶數")
        return arr[0::2] + 1j * arr[1::2]

    #: Labber 匯出的儀器設定名稱（與舊 save_labber.py 相同）；instruments.yaml labber_setting_names 可覆寫
    LABBER_NAMES = {"enabled": "{trace} - Enabled", "output": "Output enabled", "power": "Output power",
                    "if_bw": "IF bandwidth", "average": "Average", "averages": "# of averages",
                    "start_freq": "Start frequency", "stop_freq": "Stop frequency", "points": "# of points"}

    def labber_settings(self, name: Optional[str] = "S21") -> List[list]:
        values = {k: p.cache for k, p in self.parameters.items()}
        values["averages"] = self._averages
        return self.labber_settings_for(name, values, self._stimulus.get(name), self.options)

    @classmethod
    def labber_settings_for(cls, name: Optional[str], values: Dict[str, Any], x: Optional[np.ndarray],
                            options: Dict[str, Any]) -> List[list]:
        """由參數值產生 Labber 設定列（模擬 driver 也用這個，確保輸出檔結構與實機相同）。"""
        names = {k: v.replace("{trace}", str(name)) for k, v in
                 {**cls.LABBER_NAMES, **(options.get("labber_setting_names") or {})}.items()}
        c = values.get  # noqa: E731
        avg = int(values.get("averages") or 0)
        return [
            [names["enabled"], 1.0, ""],
            [names["output"], 1.0 if c("output") in (None, True) else 0.0, ""],
            [names["power"], _f(c("power")), "dBm"],
            [names["if_bw"], _f(c("if_bw")), "Hz"],
            [names["average"], 1.0 if avg >= 1 else 0.0, ""],
            [names["averages"], float(max(avg, 1)), ""],
            [names["start_freq"], float(x[0]) if x is not None else _f(c("start_freq")), "Hz"],
            [names["stop_freq"], float(x[-1]) if x is not None else _f(c("stop_freq")), "Hz"],
            [names["points"], float(len(x)) if x is not None else _f(c("points")), ""],
        ]

    # ---- 維護 ------------------------------------------------------------------
    def recover(self) -> None:
        """舊 unlock_vna.py：device clear、*CLS、ABOR、恢復連續掃描。"""
        t = self.transport
        t.set_timeout(2000)
        try:
            t.clear()
            t.write("*CLS")
            t.write(":ABOR")
            self._w("INIT{ch}:CONT ON")
        finally:
            t.set_timeout(int(self.options.get("timeout_ms", self.default_timeout_ms)))

    def close(self) -> None:
        if self.transport is not None and self.connected and self.restore_continuous:
            try:
                self.transport.write("*CLS")
                self._w("INIT{ch}:CONT ON")
            except Exception:  # noqa: BLE001
                pass
        super().close()


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


# 舊設定檔相容：driver: rs.vna
register_driver("rs.vna")(type("RohdeSchwarzVNA", (RohdeSchwarzZNA,), {"__doc__": "rs.zna 的別名（相容舊設定）"}))
