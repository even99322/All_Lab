"""虛擬儀器：由多台實體 Source 組成、對外看起來像一台 Source。

virtual.interleaved_pair
    舊 sweep_main.py「DC 異步」掃描：兩台電流源輪流前進一格。
    level = (A + B) / 2；設定 level 時拆成 A = ceil、B = floor（相對「交錯網格」）。
      * 網格預設 = resolution、原點 0：值在 resolution 網格上時兩台相同（同步）；
      * 異步量測（量測方案的 Step「電流異步」）：網格 = 每台步進 d、原點 = 起點，
        平均值每點走 d/2，每一點只有一台前進 d（A 先走），與舊 sweep_main 相同。
        參數 interleave_step / interleave_origin（量測方案在開始前自動設定）。
    量測程序只要掃 "magnet_A.level"，不需要知道背後有兩台。

virtual.group
    舊 web_yoko.py 的「合併面板」：多台同步到同一個值，第一台為主控。

任何需要「組合儀器」的新需求（例如 N 台串聯、差動輸出）都用同樣方式新增一個虛擬 driver。
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

from ...core.capabilities import Source
from ...core.config import source_settings
from ...core.errors import ConfigError
from ...core.instrument import Instrument
from ...core.registry import register_driver


class _VirtualSource(Instrument, Source):
    def __init__(self, name: str, **kw: Any) -> None:
        super().__init__(name, **kw)
        self.refs: List[str] = list(self.options.get("sources") or [])
        if not self.refs:
            raise ConfigError(f"[{name}] 需要 sources: [...]")
        self.members: List[Source] = []
        self.init_source(**source_settings(self.options))

    def open_transport(self):
        return None

    def dependencies(self) -> List[str]:
        return list(self.refs)

    def idn(self) -> str:
        return f"VIRTUAL {self.driver_name}({', '.join(self.refs)})"

    def on_connect(self) -> None:
        assert self.station is not None
        self.members = [self.station.source(r) for r in self.refs]
        self.unit = self.members[0].unit
        self.parameters["level"].unit = self.unit

    def _write_output(self, on: bool) -> None:
        for m in self.members:
            m.set_output(on)

    def _read_output(self) -> bool:
        return all(m.get_output() for m in self.members)

    def snapshot(self):
        snap = super().snapshot()
        snap["members"] = self.refs
        return snap


@register_driver("virtual.interleaved_pair")
class InterleavedPair(_VirtualSource):
    def __init__(self, name: str, **kw: Any) -> None:
        super().__init__(name, **kw)
        if len(self.refs) != 2:
            raise ConfigError(f"[{name}] interleaved_pair 需要剛好兩台 sources")
        self.grid_step = 0.0          # 0 = 使用 resolution
        self.grid_origin = 0.0
        self.add_parameter("interleave_step", get=lambda: self.grid_step,
                           set=lambda v: setattr(self, "grid_step", abs(float(v or 0.0))), unit=self.unit)
        self.add_parameter("interleave_origin", get=lambda: self.grid_origin,
                           set=lambda v: setattr(self, "grid_origin", float(v or 0.0)), unit=self.unit)

    def on_connect(self) -> None:
        super().on_connect()
        if self.resolution is None:
            res = [m.resolution for m in self.members if m.resolution]
            if not res:
                raise ConfigError(f"[{self.name}] 請設定 source.resolution（實體最小步進，例如 1e-6 A）")
            self.resolution = max(res)

    def split(self, level: float) -> Tuple[float, float]:
        """平均值 → (A, B)。A 先前進（與舊 sweep_main 的 c1/c2 順序一致）。"""
        d = self.grid_step or self.resolution
        if not d:
            raise ConfigError(f"[{self.name}] resolution 未設定")
        o = self.grid_origin if self.grid_step else 0.0
        n = int(round(2 * (level - o) / d))
        hi = -((-n) // 2)  # ceil(n/2)
        lo = n // 2        # floor(n/2)
        return round(o + hi * d, 12), round(o + lo * d, 12)

    def _write_level(self, value: float) -> None:
        a, b = self.split(value)
        self.members[0].set_level(a, ramp_if_needed=False)
        self.members[1].set_level(b, ramp_if_needed=False)

    def _read_level(self) -> float:
        return (self.members[0].get_level() + self.members[1].get_level()) / 2


@register_driver("virtual.group")
class SourceGroup(_VirtualSource):
    def _write_level(self, value: float) -> None:
        for m in self.members:
            m.set_level(value, ramp_if_needed=False)

    def _read_level(self) -> float:
        return self.members[0].get_level()

    def spread(self) -> Optional[float]:
        """成員之間的最大差值（web 面板顯示「不同步」用）。"""
        vals = [m.get_level() for m in self.members]
        return max(vals) - min(vals) if vals else None

