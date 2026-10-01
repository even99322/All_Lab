"""模擬儀器：讓整個框架可以在沒有硬體的電腦上開發、測試 UI 與量測邏輯。

settings.yaml 的 app.simulate: true（或 --sim），Station 會把有 sim_driver 的 driver
自動換成這裡的類別；每台儀器的 sim: {...} 選項會一併帶入。
模擬儀器沿用實機 driver 的參數表（標籤、單位、上下限、方塊庫資訊都一致），只是值存在記憶體。

SimVNA 會讀取 coupled_to 指定的電流源，產生一個隨電流移動的共振吸收峰，
並可在 split_region 內出現第二個吸收峰（用來測試「吸收峰型態變化自動暫停」）。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import numpy as np

from ...core.capabilities import Source, TraceAcquirer, XAxis
from ...core.config import source_settings
from ...core.driverkit import build_memory_parameters
from ...core.errors import ConfigError
from ...core.instrument import Channel, Instrument
from ...core.registry import DRIVERS, register_driver


def _real(options: Dict[str, Any], fallback: str):
    name = options.get("real_driver") or fallback
    return DRIVERS.get(name) if name in DRIVERS else DRIVERS.get(fallback)


class SimSourceChannel(Channel, Source):
    def __init__(self, parent: Instrument, key: str) -> None:
        Channel.__init__(self, parent, key)
        self._level = float(parent.options.get("initial_level", 0.0))
        self._on = bool(parent.options.get("initial_output", True))
        self.write_delay = float(parent.options.get("write_delay", 0.0))
        self.func = "CURR"
        self.range = float(parent.options.get("range", 0.2))
        cfg = source_settings(parent.options, key)
        self.init_source(**cfg)
        if self.resolution is None:
            self.resolution = float(parent.options.get("sim_resolution", 1e-6))
        self._store: Dict[str, Any] = {"function": "CURR", "range": self.range, "auto_range": False, "limiter": 30.0}
        from ..yokogawa.gs import GSChannel

        build_memory_parameters(self, GSChannel.PARAMS, self._store,
                                ((parent.options.get("channels") or {}).get(key) or {}).get("parameters"),
                                real_cls=GSChannel)

    def _write_level(self, value: float) -> None:
        if self.write_delay:
            time.sleep(self.write_delay)
        self._level = float(value)

    def _read_level(self) -> float:
        return self._level

    def _write_output(self, on: bool) -> None:
        self._on = on

    def _read_output(self) -> bool:
        return self._on


@register_driver("sim.current_source")
class SimCurrentSource(Instrument):
    def __init__(self, name: str, **kw: Any) -> None:
        super().__init__(name, **kw)
        real = _real(self.options, "yokogawa.gs200")
        n = int(self.options.get("n_channels", getattr(real, "N_CHANNELS", 1)))
        for i in range(1, n + 1):
            self.add_channel(SimSourceChannel(self, f"ch{i}"))

    def open_transport(self):
        return None

    def idn(self) -> str:
        return f"SIM,CurrentSource({self.options.get('real_driver', '-')}),{self.name}"


@register_driver("sim.vna")
class SimVNA(Instrument, TraceAcquirer):
    """簡單的 notch-type 共振模型：
        S21(f) = A · [1 − η / (1 + 2j(f − f0)/κ)] · e^{jφ(f)}
        f0 = f0_ref + Σ df_dI_k · (I_k − I0_k)      （coupling：多組電磁鐵各自移動共振頻率）
    功率越低雜訊越大、功率高時線寬變寬（示意）。所有係數都可在 instruments.yaml 的 sim: 設定。
    """

    FALLBACK_REAL = "rs.zna"

    def __init__(self, name: str, **kw: Any) -> None:
        super().__init__(name, **kw)
        o = self.options
        real = _real(o, self.FALLBACK_REAL)
        self.MEASURE_KIND = real.MEASURE_KIND
        self.TRACES = list(real.TRACES)
        self.coupled_to: List[str] = list(o.get("coupled_to", []))
        self.coupling: Dict[str, Dict[str, float]] = {k: dict(v) for k, v in (o.get("coupling") or {}).items()}
        self.I0 = float(o.get("I0", 158.83e-3))
        self.f0 = float(o.get("f0", 5.0248e9))
        self.df_dI = float(o.get("df_dI", -1.5e10))
        self.kappa = float(o.get("kappa", 4e5))
        self.eta = float(o.get("eta", 0.995))
        self.baseline_db = float(o.get("baseline_db", -50.0))
        self.noise = float(o.get("noise", 2e-3))
        self.ref_power = float(o.get("ref_power", -10.0))
        self.split_region = o.get("split_region")
        self.split_offset = float(o.get("split_offset", 1.2e6))
        self.sweep_time = float(o.get("sweep_time", 0.02))
        self.electrical_delay = float(o.get("electrical_delay", 40e-9))
        self._rng = np.random.default_rng(int(o.get("seed", 1234)))
        self._s: Dict[str, Any] = {}
        # 實機 driver 已實作 SCPI 時，模擬的可寫性與實機相同（例如 sweep_time 只能讀）
        implemented = any(sp.set is not None for sp in real.PARAMS)
        build_memory_parameters(self, real.PARAMS, self._s, o.get("parameters"),
                                real_cls=real if implemented else None)
        for k, v in {"start_freq": 5.0198e9, "stop_freq": 5.0298e9, "points": 501, "power": -10.0,
                     "if_bw": 10e3, "averages": 1, "output": True, "sweep_time": self.sweep_time,
                     "sweep_time_auto": True}.items():
            if self._s.get(k) is None and k in self.parameters:
                self._s[k] = v

    def open_transport(self):
        return None

    def idn(self) -> str:
        return f"SIM,{self.MEASURE_KIND.upper()},{self.name}"

    def dependencies(self) -> List[str]:
        return list(dict.fromkeys(self.coupled_to + list(self.coupling)))

    def configure(self, **settings: Any) -> None:
        unknown = set(settings) - {k for k, p in self.parameters.items() if p.settable}
        if unknown:
            raise ConfigError(f"[{self.name}] 不認得的設定：{sorted(unknown)}")
        self.guard_write()
        for k, v in settings.items():
            if v is not None:
                self.parameters[k].set(v)

    def trace_names(self) -> List[str]:
        return self.trace_list()

    def _band(self) -> tuple:
        s = self._s
        if s.get("center_freq") is not None and s.get("span") is not None and "start_freq" not in self.parameters:
            return s["center_freq"] - s["span"] / 2, s["center_freq"] + s["span"] / 2
        return s["start_freq"], s["stop_freq"]

    def x_axis(self, name: Optional[str] = None) -> XAxis:
        f0, f1 = self._band()
        return XAxis(np.linspace(f0, f1, int(self._s["points"])), "Frequency", "Hz")

    def _current(self) -> float:
        if not self.coupled_to or self.station is None:
            return self.I0
        vals = [self.station.source(r).get_level() for r in self.coupled_to]
        return float(np.mean(vals))

    def acquire(self, name: Optional[str] = None) -> np.ndarray:
        t = self.sweep_time
        if self._s.get("sweep_time_auto") is False and self._s.get("sweep_time") is not None:
            t = float(self._s["sweep_time"])          # 模擬：固定掃描時間
        if t:
            time.sleep(t)
        f = self.x_axis().values
        I = self._current()
        amp = 10 ** (self.baseline_db / 20)
        f0 = self.f0 + self.df_dI * (I - self.I0) if self.coupled_to else self.f0
        for ref, c in self.coupling.items():
            f0 += float(c.get("df_dI", 0.0)) * (self.station.source(ref).get_level() - float(c.get("I0", 0.0)))
        power = float(self._s.get("power") if self._s.get("power") is not None else self.ref_power)
        kappa = self.kappa * (1 + 0.5 * 10 ** (power / 10))
        resp = 1 - self.eta / (1 + 2j * (f - f0) / kappa)
        if self.split_region and self.split_region[0] <= I <= self.split_region[1]:
            resp *= 1 - self.eta / (1 + 2j * (f - f0 - self.split_offset) / kappa)
        phase = np.exp(-2j * np.pi * f * self.electrical_delay)
        n_avg = max(1, int(self._s.get("averages") or 1))
        snr = 10 ** ((power - self.ref_power) / 20)
        noise = (self._rng.normal(size=f.size) + 1j * self._rng.normal(size=f.size)) * self.noise / np.sqrt(n_avg) / snr
        return amp * (resp + noise) * phase

    def labber_settings(self, name: Optional[str] = None) -> List[list]:
        real = _real(self.options, self.FALLBACK_REAL)
        if hasattr(real, "labber_settings_for"):     # 與實機 driver 輸出相同的 Labber 結構
            values = {k: p.get() for k, p in self.parameters.items() if p.gettable}
            return real.labber_settings_for(name, values, self.x_axis(name).values, self.options)
        out = [[f"{name} - Enabled", 1.0, ""]]
        for k, p in self.parameters.items():
            spec = p.spec
            if spec is not None and spec.snapshot and spec.kind in ("float", "int") and p.get() is not None:
                out.append([spec.label or k, float(p.get()), spec.unit])
        return out

    def recover(self) -> None:
        pass


@register_driver("sim.shfqc")
class SimSHFQC(SimVNA):
    """模擬 SHFQC 頻譜量測（參數表取自 zi.shfqc）。"""

    FALLBACK_REAL = "zi.shfqc"
