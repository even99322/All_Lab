"""掃描定義：一個或多個軸（外層 → 內層），以「平坦索引」逐點走訪，方便回溯。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..core.errors import ConfigError
from ..core.units import parse_quantity, split_unit
from ..data.dataset import AxisSpec


@dataclass
class Axis:
    target: str                 # 參數 ref，例如 "magnet_A.level"、"VNA1.power"
    values: np.ndarray          # SI
    name: str = ""
    unit: str = ""              # SI 單位
    display_unit: str = ""
    display_scale: float = 1.0
    settle: float = 0.0         # 設定後等待秒數
    alternate: bool = False     # 來回掃（Labber: Alternate step direction）：外圈每走一步，這一軸反向

    def __post_init__(self) -> None:
        self.values = np.asarray(self.values, dtype=float)
        self.name = self.name or self.target

    @classmethod
    def from_config(cls, d: Dict[str, Any]) -> "Axis":
        """支援三種寫法（數值單位由 unit 決定，例如 unit: mA 表示 start/stop/step 以 mA 填寫）：
            start/stop/step    （含端點，步進會自動修正浮點誤差）
            start/stop/num     （interp: log → 對數間隔，起訖需同號且不為 0）
            values: [...]
        其他：settle（每步等待）、alternate（來回掃）
        """
        if "target" not in d:
            raise ConfigError(f"sweep 軸缺少 target：{d}")
        unit = str(d.get("unit", ""))
        si_unit, scale = split_unit(unit)
        q = lambda k: parse_quantity(d[k], unit)  # noqa: E731
        if "values" in d:
            vals = np.array([parse_quantity(v, unit) for v in d["values"]], dtype=float)
        elif "step" in d:
            start, stop, step = q("start"), q("stop"), abs(q("step"))
            if step == 0:
                raise ConfigError("step 不可為 0")
            n = int(round(abs(stop - start) / step))
            sign = 1.0 if stop >= start else -1.0
            vals = np.round(start + sign * step * np.arange(n + 1), 12)
        elif "num" in d:
            n = int(d["num"])
            if n < 1:
                raise ConfigError("num 至少為 1")
            if str(d.get("interp", "linear")).lower() == "log":
                a, b = q("start"), q("stop")
                if a == 0 or b == 0 or (a > 0) != (b > 0):
                    raise ConfigError(f"對數間隔的起訖需同號且不為 0（{d['target']}）")
                vals = np.geomspace(a, b, n)
            else:
                vals = np.linspace(q("start"), q("stop"), n)
        else:
            raise ConfigError(f"sweep 軸 {d['target']} 需要 step、num 或 values")
        return cls(
            target=d["target"], values=vals, name=d.get("name", d["target"]),
            unit=si_unit, display_unit=unit or si_unit, display_scale=1.0 / scale,
            settle=parse_quantity(d.get("settle", 0), "s"), alternate=bool(d.get("alternate", False)),
        )

    def spec(self) -> AxisSpec:
        return AxisSpec(self.name, self.target, self.unit, self.values, self.display_unit, self.display_scale)


class SweepPlan:
    def __init__(self, axes: List[Axis], snake: bool = False) -> None:
        if not axes:
            raise ConfigError("至少需要一個掃描軸")
        names = [a.name for a in axes]
        if len(set(names)) != len(names):
            raise ConfigError(f"掃描軸名稱重複：{names}")
        self.axes = axes
        self.snake = snake

    @property
    def shape(self) -> Tuple[int, ...]:
        return tuple(len(a.values) for a in self.axes)

    def __len__(self) -> int:
        return int(np.prod(self.shape))

    def multi_index(self, i: int) -> Tuple[int, ...]:
        orig = [int(x) for x in np.unravel_index(i, self.shape)]
        idx = list(orig)
        # 來回掃：外層每前進一格，該軸方向反轉（減少大跳）；奇偶以外層在走訪順序中的位置判斷
        for k in range(1, len(idx)):
            if (self.snake or self.axes[k].alternate) and \
                    int(np.ravel_multi_index(orig[:k], self.shape[:k])) % 2 == 1:
                idx[k] = self.shape[k] - 1 - orig[k]
        return tuple(int(x) for x in idx)

    def setpoints(self, i: int) -> Dict[str, float]:
        mi = self.multi_index(i)
        return {a.name: float(a.values[j]) for a, j in zip(self.axes, mi)}

    def axis(self, name: str) -> Optional[Axis]:
        return next((a for a in self.axes if a.name == name), None)

    @classmethod
    def from_config(cls, items: List[Dict[str, Any]], snake: bool = False) -> "SweepPlan":
        return cls([Axis.from_config(d) for d in items], snake=snake)
