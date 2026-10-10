"""內建量測程序。"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from ..core.capabilities import TraceAcquirer
from ..core.registry import register_procedure
from ..data.dataset import ChannelSpec
from .procedure import Procedure, RunContext, parse_readouts, readout_is_trace


@register_procedure("trace_sweep")
class TraceSweep(Procedure):
    """最通用的「掃參數 → 讀資料」程序。

    readouts 可以混合：
        - {ref: VNA1, trace: S21}          向量（TraceAcquirer）
        - {ref: DC5.ch1.measured}          純量（任何可讀參數）

    舊 sweep_main.py 的「調 DC → 量 VNA」就是：
        sweep 掃 magnet_A.level，readouts = [{ref: VNA1, trace: S21}]
    換成 SHFQC、換成別台 VNA、多加一台電表，都只要改 YAML。
    """

    def __init__(self, station, config: Dict[str, Any], setup=None) -> None:
        super().__init__(station, config, setup)
        self.readouts = parse_readouts(self.config.get("readouts", []))

    def refs(self) -> List[str]:
        return super().refs() + [r.ref for r in self.readouts]

    def setup(self, ctx: RunContext) -> List[ChannelSpec]:
        super().setup(ctx)
        specs = []
        for r in self.readouts:
            if readout_is_trace(self.station, r):
                acq: TraceAcquirer = self.station.acquirer(r.ref)
                ax = acq.x_axis(r.trace)
                inst = self.station.instruments[r.ref.split(".")[0]]
                group = inst.options.get("labber_name", inst.name)
                specs.append(ChannelSpec(
                    name=r.name, vector=True, complex=True, x_name=ax.name, x_unit=ax.unit,
                    x_values=np.asarray(ax.values), export_name=r.export_name or f"{group} - {r.name}",
                    source=r.ref, labber_group=group, labber_settings=acq.labber_settings(r.trace),
                ))
            else:
                p = self.station.parameter(r.ref)
                specs.append(ChannelSpec(name=r.name, unit=p.unit, export_name=r.export_name, source=r.ref))
        return specs

    def measure(self, ctx: RunContext) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for r in self.readouts:
            if readout_is_trace(self.station, r):
                out[r.name] = self.station.acquirer(r.ref).acquire(r.trace)
            else:
                out[r.name] = float(self.station.parameter(r.ref).get())
        return out

    def on_error(self, ctx: RunContext, error: Exception) -> None:
        for r in self.readouts:
            inst = self.station.instruments[r.ref.split(".")[0]]
            if hasattr(inst, "recover"):
                try:
                    inst.recover()
                except Exception:  # noqa: BLE001
                    pass
