"""Hooks：插在量測流程中的小外掛（每點後檢查、自動暫停、即時串流…）。

實驗 YAML：
    hooks:
      - {type: dip_shape_pause, channel: S21, prominence_db: 3, enabled: false}
執行中可由前端開關：runner.hook("dip_shape_pause").enabled = True
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, NamedTuple, Optional

import numpy as np

from ..core.registry import register_hook

if TYPE_CHECKING:  # pragma: no cover
    from ..data.dataset import PointRecord
    from .procedure import RunContext


class HookResult(NamedTuple):
    action: str          # "pause" | "stop"
    reason: str = ""


class Hook:
    def __init__(self, **config: Any) -> None:
        self.config = config
        self.enabled = bool(config.get("enabled", True))

    @property
    def name(self) -> str:
        return getattr(self, "registered_name", type(self).__name__)

    def on_run_start(self, ctx: "RunContext") -> None: ...
    def after_point(self, ctx: "RunContext", record: "PointRecord") -> Optional[HookResult]: return None
    def on_rollback(self, ctx: "RunContext", index: int) -> None: ...
    def on_run_end(self, ctx: "RunContext") -> None: ...


def count_dips(mag_db: np.ndarray, prominence_db: float) -> int:
    try:
        from scipy.signal import find_peaks
    except ImportError:  # pragma: no cover - 降級演算法
        x = -mag_db
        base = np.median(x)
        above = (x - base) > prominence_db
        return int(np.sum(np.diff(above.astype(int)) == 1) + (1 if above[0] else 0))
    peaks, _ = find_peaks(-mag_db, prominence=prominence_db)
    return int(len(peaks))


@register_hook("dip_shape_pause")
class DipShapeChangePause(Hook):
    """舊 sweep_main.py「偵測吸收型態變化 (1⇌2) 並自動暫停」。

    config: channel (預設第一個向量通道)、prominence_db (3)、transitions ([[1, 2]])
    """

    def __init__(self, **config: Any) -> None:
        super().__init__(**config)
        self.prominence = float(config.get("prominence_db", 3.0))
        self.transitions = [set(t) for t in config.get("transitions", [[1, 2]])]
        self.channel: Optional[str] = config.get("channel")
        self.prev: Optional[int] = None

    def on_run_start(self, ctx):
        self.prev = None
        if self.channel is None and ctx.dataset is not None:
            vec = [c.name for c in ctx.dataset.channels if c.vector]
            self.channel = vec[0] if vec else None

    def on_rollback(self, ctx, index):
        self.prev = None

    def after_point(self, ctx, record):
        if not self.enabled or self.channel is None:
            self.prev = None
            return None
        trace = np.asarray(record.data[self.channel])
        n = count_dips(20 * np.log10(np.abs(trace) + 1e-30), self.prominence)
        prev, self.prev = self.prev, n
        record.notes["dip_count"] = n
        if prev is not None and {prev, n} in self.transitions:
            return HookResult("pause", f"偵測到吸收峰型態變化 ({prev} ⇌ {n})，已自動暫停")
        return None


@register_hook("limit_pause")
class ScalarLimitPause(Hook):
    """範例：某個純量通道超出範圍就暫停（例如溫度、漏電流）。
    config: channel, min, max, action (pause|stop)
    """

    def after_point(self, ctx, record):
        if not self.enabled:
            return None
        ch = self.config["channel"]
        v = float(record.data[ch])
        lo, hi = self.config.get("min", -np.inf), self.config.get("max", np.inf)
        if not (lo <= v <= hi):
            return HookResult(self.config.get("action", "pause"), f"{ch}={v:g} 超出 [{lo}, {hi}]")
        return None


def build_hook(d: Dict[str, Any]) -> Hook:
    from ..core.registry import HOOKS

    from ..settings import setting

    d = dict(d)
    kind = d.pop("type")
    cls = HOOKS.get(kind)
    defaults = setting(f"hooks.{kind}", {}) or {}   # settings.yaml hooks.<type> 是預設參數
    return cls(**{**defaults, **d})
