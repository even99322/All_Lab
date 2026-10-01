"""Serializable camera state for future Session/View Preset integration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any


@dataclass(frozen=True)
class CameraState3D:
    x_rotation: float = 35.0
    y_rotation: float = 25.0
    zoom_level: float = 100.0
    target: tuple[float, float, float] = (0.0, 0.0, 0.0)
    projection: str = "perspective"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["target"] = list(self.target)
        return value

    @classmethod
    def from_dict(cls, raw: object) -> "CameraState3D":
        if not isinstance(raw, dict):
            return cls()
        try:
            x_rotation = float(raw.get("x_rotation", 35.0))
            y_rotation = float(raw.get("y_rotation", 25.0))
            zoom_level = float(raw.get("zoom_level", 100.0))
            target_raw = raw.get("target", (0.0, 0.0, 0.0))
            if not isinstance(target_raw, (list, tuple)) or len(target_raw) != 3:
                return cls()
            target = tuple(float(value) for value in target_raw)
            projection = str(raw.get("projection", "perspective")).lower()
        except (TypeError, ValueError, OverflowError):
            return cls()
        values = (x_rotation, y_rotation, zoom_level, *target)
        if not all(math.isfinite(value) for value in values):
            return cls()
        if projection not in {"perspective", "orthographic"}:
            projection = "perspective"
        return cls(
            x_rotation=x_rotation % 360.0,
            y_rotation=y_rotation % 360.0,
            zoom_level=min(500.0, max(10.0, zoom_level)),
            target=target,
            projection=projection,
        )
