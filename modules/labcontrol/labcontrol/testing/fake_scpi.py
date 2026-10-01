"""依手冊語法模擬儀器的 SCPI 端（測試用）：讓「真的驅動程式碼」在沒有硬體時也能跑完整流程。

和 sim.* 模擬驅動不同：這裡模擬的是儀器本身，驅動送出的每一條指令都會被解析；
不認得的指令會像實機一樣在錯誤佇列放入 -113 "Undefined header"，因此可以用來驗證驅動測試會抓到錯誤。
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from ..core.transport import MockTransport

NO_ERROR = '0,"No error"'


class FakeSCPI(MockTransport):
    """規則表：[(正規表示式, 處理函式)]；查詢回傳字串，寫入回傳 None。"""

    IDN = "FAKE,INSTRUMENT,0,1.0"

    def __init__(self) -> None:
        super().__init__()
        self.errors: List[str] = []
        self.rules: List[Tuple[re.Pattern, Callable[..., Optional[str]]]] = []
        self.add(r"\*IDN\?", lambda: self.IDN)
        self.add(r"\*CLS", lambda: self.errors.clear())
        self.add(r":?SYST(?:em)?:ERR(?:or)?(?::NEXT)?\?", lambda: self.errors.pop(0) if self.errors else NO_ERROR)

    def add(self, pattern: str, fn: Callable[..., Optional[str]]) -> None:
        self.rules.append((re.compile(pattern + r"$", re.I), fn))

    def _handle(self, cmd: str) -> Optional[str]:
        for pat, fn in self.rules:
            m = pat.match(cmd.strip())
            if m:
                return fn(*m.groups())
        self.errors.append(f'-113,"Undefined header;{cmd.strip()}"')
        return None

    def write(self, cmd: str) -> None:
        self.log.append("W " + cmd)
        self._tap(">>", cmd)
        self._stat(cmd, 0.0)
        self._handle(cmd)

    def query(self, cmd: str) -> str:
        self.log.append("Q " + cmd)
        self._tap("?>", cmd)
        ans = self._handle(cmd)
        self._stat(cmd, 0.0)
        ans = "" if ans is None else str(ans)
        self._tap("<<", ans)
        return ans


class FakeGS200(FakeSCPI):
    """Yokogawa GS200（IM GS210-01EN §13.2 的子集）。"""

    IDN = "YOKOGAWA,GS210,91W000001,2.02"
    RANGES = {"CURR": [1e-3, 10e-3, 100e-3, 200e-3], "VOLT": [10e-3, 100e-3, 1.0, 10.0, 30.0]}

    def __init__(self, level: float = 0.1586, output: bool = True, func: str = "CURR", rng: float = 0.2) -> None:
        super().__init__()
        self.s = {"func": func, "range": rng, "level": level, "out": output, "prot_v": 5.0, "prot_i": 0.1}
        a = self.add
        a(r":SOUR(?:ce)?:FUNC(?:tion)?\?", lambda: self.s["func"])
        a(r":SOUR(?:ce)?:FUNC(?:tion)? (CURR|VOLT)\w*", self._func)
        a(r":SOUR(?:ce)?:RANG(?:e)?\?", lambda: f"{self.s['range']:.6E}")
        a(r":SOUR(?:ce)?:RANG(?:e)? (\S+)", self._range)
        a(r":SOUR(?:ce)?:LEV(?:el)?(?::FIX)?\?", lambda: f"{self.s['level']:.6E}")
        a(r":SOUR(?:ce)?:LEV(?:el)?(?::FIX)? (\S+)", self._level)
        a(r":OUTP(?:ut)?(?::STAT)?\?", lambda: "1" if self.s["out"] else "0")
        a(r":OUTP(?:ut)?(?::STAT)? (\S+)", lambda v: self.s.__setitem__("out", v.upper() in ("1", "ON")))
        a(r":SOUR(?:ce)?:PROT(?:ection)?:VOLT\?", lambda: f"{self.s['prot_v']:.6E}")
        a(r":SOUR(?:ce)?:PROT(?:ection)?:VOLT (\S+)", lambda v: self.s.__setitem__("prot_v", float(v)))
        a(r":SOUR(?:ce)?:PROT(?:ection)?:CURR\?", lambda: f"{self.s['prot_i']:.6E}")
        a(r":SOUR(?:ce)?:PROT(?:ection)?:CURR (\S+)", lambda v: self.s.__setitem__("prot_i", float(v)))

    def _func(self, f: str) -> None:
        self.s["func"] = f.upper()[:4]
        self.s["out"] = self.s["out"]

    def _range(self, v: str) -> None:
        r = float(v)
        table = self.RANGES[self.s["func"]]
        self.s["range"] = min(table, key=lambda x: abs(x - r)) if r <= max(table) else max(table)

    def _level(self, v: str) -> None:
        x = float(v)
        if abs(x) > self.s["range"] * 1.2 + 1e-12:
            self.errors.append('-222,"Data out of range"')
            return
        self.s["level"] = round(x, 9)


class FakeZNA(FakeSCPI):
    """R&S ZNA（User Manual 1178.6462.02 第 7 章的子集），通道 1，trace Trc1=S21、Trc2=S11。"""

    IDN = "Rohde-Schwarz,ZNA26-4Port,1332450064100001,2.20"

    def __init__(self) -> None:
        super().__init__()
        self.s = {"start": 5.0198e9, "stop": 5.0298e9, "points": 501, "power": -10.0, "bw": 10e3,
                  "aver": False, "count": 1, "out": True, "cont": True, "esr": 0,
                  "form": "ASC"}
        a = self.add
        ch = r":?(?:SENS(?:e)?)?1?"
        for key, name in (("start", "STAR"), ("stop", "STOP")):
            a(rf":?SENS(?:e)?1:FREQ(?:uency)?:{name}\w*\?", lambda k=key: f"{self.s[k]:.10E}")
            a(rf":?SENS(?:e)?1:FREQ(?:uency)?:{name}\w* (\S+)", lambda v, k=key: self.s.__setitem__(k, float(v)))
        a(r":?SENS(?:e)?1:FREQ(?:uency)?:CENT\w*\?", lambda: f"{(self.s['start'] + self.s['stop']) / 2:.10E}")
        a(r":?SENS(?:e)?1:FREQ(?:uency)?:CENT\w* (\S+)", self._center)
        a(r":?SENS(?:e)?1:FREQ(?:uency)?:SPAN\?", lambda: f"{self.s['stop'] - self.s['start']:.10E}")
        a(r":?SENS(?:e)?1:FREQ(?:uency)?:SPAN (\S+)", self._span)
        a(r":?SENS(?:e)?1:SWE(?:ep)?:POIN(?:ts)?\?", lambda: str(self.s["points"]))
        a(r":?SENS(?:e)?1:SWE(?:ep)?:POIN(?:ts)? (\S+)", lambda v: self.s.__setitem__("points", int(float(v))))
        a(r":?SENS(?:e)?1:SWE(?:ep)?:COUN(?:t)? (\S+)", lambda v: self.s.__setitem__("count", int(float(v))))
        a(r":?SENS(?:e)?1:SWE(?:ep)?:TIME\?", lambda: f"{self._sweep_time():.6E}")
        a(r":?SENS(?:e)?1:SWE(?:ep)?:TIME:AUTO\?", lambda: "1" if self.s.get("st_auto", True) else "0")
        a(r":?SENS(?:e)?1:SWE(?:ep)?:TIME:AUTO (\S+)",
          lambda v: self.s.__setitem__("st_auto", v.upper() in ("1", "ON", "TRUE")))
        a(r":?SENS(?:e)?1:SWE(?:ep)?:TIME (\S+)", lambda v: self._set_sweep_time(float(v)))
        a(r":?SOUR(?:ce)?1:POW(?:er)?\?", lambda: f"{self.s['power']:.3f}")
        a(r":?SOUR(?:ce)?1:POW(?:er)? (\S+)", lambda v: self.s.__setitem__("power", float(v)))
        a(r":?SENS(?:e)?1:BAND(?:width)?\?", lambda: f"{self.s['bw']:.6E}")
        a(r":?SENS(?:e)?1:BAND(?:width)? (\S+)", self._bw)
        a(r":?SENS(?:e)?1:AVER(?:age)?\?", lambda: "1" if self.s["aver"] else "0")
        a(r":?SENS(?:e)?1:AVER(?:age)? (ON|OFF|1|0)", lambda v: self.s.__setitem__("aver", v.upper() in ("ON", "1")))
        a(r":?SENS(?:e)?1:AVER(?:age)?:COUN(?:t)?\?", lambda: str(self.s["count"]))
        a(r":?SENS(?:e)?1:AVER(?:age)?:COUN(?:t)? (\S+)", lambda v: self.s.__setitem__("count", int(float(v))))
        a(r":?SENS(?:e)?1:AVER(?:age)?:CLE(?:ar)?", lambda: None)
        a(r":?OUTP(?:ut)?1?\?", lambda: "1" if self.s["out"] else "0")
        a(r":?OUTP(?:ut)?1? (\S+)", lambda v: self.s.__setitem__("out", v.upper() in ("ON", "1")))
        a(r"INIT1:CONT(?:inuous)? (ON|OFF)", lambda v: self.s.__setitem__("cont", v.upper() == "ON"))
        a(r"INIT1(?::IMM(?:ediate)?)?", lambda: self.s.__setitem__("esr", 0))
        a(r"\*OPC", lambda: self.s.__setitem__("esr", 1))
        a(r"\*ESR\?", lambda: str(self.s["esr"]))
        a(r"FORM(?:at)?(?::DATA)? (ASC\w*|REAL,64)", lambda v: self.s.__setitem__("form", v[:3].upper()))
        a(r"FORM(?:at)?:BORD(?:er)? SWAP\w*", lambda: None)
        a(r"CALC1:PAR(?:ameter)?:CAT(?:alog)?\?", lambda: "'Trc1,S21,Trc2,S11'")
        a(r"CALC1:PAR(?:ameter)?:SEL(?:ect)? '(\w+)'", lambda t: self.s.__setitem__("sel", t))
        a(r"CALC1:DATA:STIM(?:ulus)?\?", lambda: self._ascii(",".join(f"{f:.10E}" for f in self.freqs())))
        a(r"CALC:DATA:TRAC(?:e)?\? '(\w+)', ?SDAT\w*", self._trace)
        a(r":?ABOR\w*", lambda: None)

    def _ascii(self, text: str) -> str:
        """FORM 不是 ASCII 時，實機回傳二進位區塊；pyvisa 以 ASCII 解碼會失敗（0.0.5 實機報告）。"""
        if self.s["form"] != "ASC":
            b"#18\x95".decode("ascii")        # 丟出與實機相同的 UnicodeDecodeError
        return text

    def freqs(self) -> np.ndarray:
        return np.linspace(self.s["start"], self.s["stop"], self.s["points"])

    def _sweep_time(self) -> float:
        fastest = self.s["points"] / self.s["bw"] * 1.1
        if self.s.get("st_auto", True):
            return fastest
        return max(fastest, float(self.s.get("st", fastest)))      # 實機：低於最短時間會被調成最短

    def _set_sweep_time(self, v: float) -> None:
        self.s["st"] = v
        self.s["st_auto"] = False                                   # 實機：設定掃描時間 → 自動關閉

    def _center(self, v: str) -> None:
        span = self.s["stop"] - self.s["start"]
        c = float(v)
        self.s["start"], self.s["stop"] = c - span / 2, c + span / 2

    def _span(self, v: str) -> None:
        c = (self.s["start"] + self.s["stop"]) / 2
        sp = float(v)
        self.s["start"], self.s["stop"] = c - sp / 2, c + sp / 2

    def _bw(self, v: str) -> None:
        x = float(v)
        steps = [1, 1.5, 2, 3, 5, 7]
        dec = 10 ** np.floor(np.log10(x))
        self.s["bw"] = float(min((s * dec for s in steps + [10]), key=lambda s: abs(s - x) if s >= x else 1e30))

    def _trace(self, t: str) -> str:
        if t not in ("Trc1", "Trc2"):
            self.errors.append('-222,"Data out of range;trace"')
            return ""
        f = self.freqs()
        f0 = (self.s["start"] + self.s["stop"]) / 2 + 1.1e6
        z = 10 ** (-50 / 20) * (1 - 0.995 / (1 + 2j * (f - f0) / 4e5))
        return self._ascii(",".join(f"{x:.6E}" for pair in zip(z.real, z.imag) for x in pair))


def fake_station(simulate: bool = False):
    """一個用 Fake 儀器（真驅動 + 模擬 SCPI 端）組成的 Station：DC1、DC2（GS200）、VNA1（ZNA）、magnet_A。"""
    from ..core.station import Station
    from ..drivers.rohde_schwarz.vna import RohdeSchwarzZNA
    from ..drivers.yokogawa.gs import GS200

    st = Station({"instruments": {}}, simulate=simulate)
    src = {"limits": [-0.2, 0.2], "ramp_rate": 5e-3, "max_jump": 5e-5}
    for n in ("DC1", "DC2"):
        inst = GS200(n, transport=FakeGS200(), station=st, address=f"USB0::FAKE::{n}::INSTR", label=f"{n}（假儀器）",
                     source=src)
        inst.config_driver = "yokogawa.gs200"
        st.instruments[n] = inst
    vna = RohdeSchwarzZNA("VNA1", transport=FakeZNA(), station=st, address="TCPIP0::FAKE::VNA1::INSTR",
                          label="VNA-1（假儀器）", labber_name="VNA")
    vna.config_driver = "rs.zna"
    st.instruments["VNA1"] = vna
    st.add("magnet_A", "virtual.interleaved_pair", sources=["DC1", "DC2"], group="magnet", label="磁鐵 A（DC1+DC2）",
           display_unit="mA", source={**src, "resolution": 1e-6})
    return st
