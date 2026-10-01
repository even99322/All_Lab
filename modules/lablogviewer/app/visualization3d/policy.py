"""Small, explicit rendering-policy decisions for scientific surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


STANDARD_DATA_POINT_LIMIT = 1_000_000
AUTO_LARGE_VERTEX_LIMIT = 100_000
ADAPTIVE_VERTEX_LIMIT = 100_000
PERFORMANCE_VERTEX_LIMIT = 50_000


class RenderingPolicy(StrEnum):
    AUTO = "Auto"
    FULL_RESOLUTION = "Full Resolution"
    ADAPTIVE_LOD = "Adaptive LOD"
    PERFORMANCE = "Performance"


@dataclass(frozen=True)
class RenderingDecision:
    requested: RenderingPolicy
    effective: RenderingPolicy
    source_points: int
    max_vertices: int | None
    warning: str | None = None


def resolve_rendering_policy(
    rows: int, columns: int, requested: RenderingPolicy | str = RenderingPolicy.AUTO
) -> RenderingDecision:
    """Choose the idle mesh budget without changing source data.

    Auto keeps up to one million points at full resolution. Larger grids use
    a feature-preserving 100k representation. Explicit Full Resolution remains
    available for larger grids and carries a non-blocking risk notice.
    """
    rows, columns = int(rows), int(columns)
    if rows < 0 or columns < 0:
        raise ValueError("Surface dimensions cannot be negative.")
    policy = RenderingPolicy(requested)
    points = rows * columns
    if policy is RenderingPolicy.AUTO:
        if points <= STANDARD_DATA_POINT_LIMIT:
            return RenderingDecision(policy, policy, points, None)
        return RenderingDecision(
            policy, RenderingPolicy.ADAPTIVE_LOD, points, AUTO_LARGE_VERTEX_LIMIT,
            "Large grid: Auto uses a feature-preserving 100k display mesh.",
        )
    if policy is RenderingPolicy.FULL_RESOLUTION:
        warning = (
            "Full Resolution on a large grid may use substantial memory and render slowly."
            if points > STANDARD_DATA_POINT_LIMIT else None
        )
        return RenderingDecision(policy, policy, points, None, warning)
    if policy is RenderingPolicy.ADAPTIVE_LOD:
        return RenderingDecision(policy, policy, points, ADAPTIVE_VERTEX_LIMIT)
    return RenderingDecision(policy, policy, points, PERFORMANCE_VERTEX_LIMIT)


def interaction_vertex_budget(source_points: int, viewport: tuple[int, int],
                              zoom_level: float = 100.0,
                              measured_fps: float | None = None) -> int:
    """Choose a prepared interaction level from screen density and frame rate.

    This is a display budget only. It never changes the scientific array.
    Camera movement selects among cached levels; it does not compute LOD.
    """
    width, height = (max(1, int(value)) for value in viewport)
    visible_pixels = width * height
    zoom_factor = max(0.5, min(2.0, float(zoom_level) / 100.0))
    budget = int(min(PERFORMANCE_VERTEX_LIMIT, max(8_000, visible_pixels * 0.12 * zoom_factor)))
    if measured_fps is not None and 0 < measured_fps < 30:
        budget = max(8_000, budget // 2)
    # A small fixed ladder prevents wheel/resize events from spawning a new
    # nearly identical worker request and cache entry for every pixel change.
    level = max(value for value in (8_000, 16_000, 25_000, 50_000) if value <= budget)
    return min(int(source_points), level)
