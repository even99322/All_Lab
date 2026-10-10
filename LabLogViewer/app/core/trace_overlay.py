"""Deterministic styling rules for 1D multi-trace overlays."""

from __future__ import annotations

from dataclasses import dataclass


SEQUENTIAL = "Sequential"
DISTINCT = "Distinct"
SINGLE_COLOR = "Single Color"
COLOR_MODES = (SEQUENTIAL, DISTINCT, SINGLE_COLOR)

SEQUENTIAL_START = (67, 143, 179)
SEQUENTIAL_END = (23, 59, 112)
SINGLE_COLOR_RGB = (31, 111, 165)
DISTINCT_PALETTE = (
    (31, 119, 180),
    (214, 39, 40),
    (44, 160, 44),
    (148, 103, 189),
    (255, 127, 14),
    (23, 190, 207),
    (140, 86, 75),
    (227, 119, 194),
)


@dataclass(frozen=True)
class TraceStyle:
    color: tuple[int, int, int]
    opacity: float
    width: float
    z: float
    active: bool


def adaptive_inactive_opacity(count: int) -> float:
    """Keep small overlays crisp and dense overlays recoverable."""
    count = max(int(count), 1)
    if count <= 10:
        return 0.88
    if count <= 30:
        return 0.88 - (count - 10) * 0.008
    if count <= 100:
        return 0.72 - (count - 30) * 0.004
    return max(0.18, 0.44 - (count - 100) * 0.00065)


def sequential_color(position: int, count: int) -> tuple[int, int, int]:
    """Map sweep order to a readable light-blue through navy ramp."""
    fraction = 0.5 if count <= 1 else max(0.0, min(position / (count - 1), 1.0))
    return tuple(
        round(start + (end - start) * fraction)
        for start, end in zip(SEQUENTIAL_START, SEQUENTIAL_END)
    )


def color_for_trace(position: int, count: int, mode: str) -> tuple[int, int, int]:
    if mode == DISTINCT:
        return DISTINCT_PALETTE[position % len(DISTINCT_PALETTE)]
    if mode == SINGLE_COLOR:
        return SINGLE_COLOR_RGB
    return sequential_color(position, count)


def build_trace_styles(
    ordered_traces: tuple[int, ...] | list[int], active_trace: int | None, mode: str
) -> dict[int, TraceStyle]:
    """Build stable styles from actual sweep order, never click order."""
    if mode not in COLOR_MODES:
        mode = SEQUENTIAL
    traces = tuple(ordered_traces)
    opacity = adaptive_inactive_opacity(len(traces))
    styles = {}
    for position, trace in enumerate(traces):
        active = trace == active_trace
        styles[trace] = TraceStyle(
            color=color_for_trace(position, len(traces), mode),
            opacity=1.0 if active else opacity,
            width=2.8 if active else (2.0 if len(traces) == 1 else 1.15),
            z=20.0 if active else 2.0 + position / max(len(traces), 1),
            active=active,
        )
    return styles


def relative_luminance(color: tuple[int, int, int]) -> float:
    channels = []
    for value in color:
        component = value / 255.0
        channels.append(
            component / 12.92
            if component <= 0.04045
            else ((component + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_against_white(color: tuple[int, int, int]) -> float:
    return 1.05 / (relative_luminance(color) + 0.05)
