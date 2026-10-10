"""Self-check of how many samples per axis this GPU draws with correct colours.

A surface whose colour texture is longer than the GPU handles is cut short or has its
colours shifted off the heights. The probe draws a flat surface whose colour texture has
the lowest colour at both ends' first/last band and the highest colour at the opposite
ends, plus an interior pair at 30 % / 70 %, and checks from the rendered pixels that the
lowest and highest colours sit where they should. ``find_axis_limit`` then keeps halving
the axis length until the check passes."""
from __future__ import annotations

import numpy as np

PROBE_FROM = 2048
CANDIDATES = (16384, 8192, 4096, 2048, 1024, 512, 256)
TOLERANCE = 0.03
_state = {"ok": 0, "bad": None, "reference": None}


def reference_extent() -> float | None:
    return _state["reference"]


def set_reference_extent(value: float) -> None:
    _state["reference"] = value


def marker_values(columns: int) -> np.ndarray:
    """Colour values 0 (lowest) .. 1 (highest) along the axis: low at the first band and at
    70 %, high at 30 % and at the last band, neutral elsewhere."""
    width = max(2, columns // 50)
    values = np.full(columns, 0.5)
    values[:width] = 0.0
    values[-width:] = 1.0
    for centre, value in ((0.3, 1.0), (0.7, 0.0)):
        start = int(centre * columns) - width // 2
        values[start:start + width] = value
    return values


def _runs(mask_columns: np.ndarray) -> list[tuple[int, int]]:
    indices = np.flatnonzero(mask_columns)
    if indices.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(indices) > 4)
    starts = np.concatenate(([indices[0]], indices[breaks + 1]))
    stops = np.concatenate((indices[breaks], [indices[-1]]))
    return list(zip(starts.tolist(), stops.tolist()))


def _marker_runs(rgb: np.ndarray, colour) -> list[tuple[int, int]]:
    """Column runs dominated by ``colour``'s hue (lighting dims the exact value, so the test is
    on which channel leads, not on the exact RGB)."""
    pixels = rgb.astype(np.int32)
    lead = int(np.argmax(np.asarray(colour)))
    others = np.delete(pixels, lead, axis=2).max(axis=2)
    per_column = ((pixels[..., lead] - others) > 40).sum(axis=0)
    if per_column.max() < 8:
        return []
    return _runs(per_column >= max(8, 0.4 * per_column.max()))


def markers_aligned(rgb: np.ndarray, low_rgb, high_rgb, reference_extent: float | None = None) -> bool:
    """True when the lowest/highest colours appear at 0 %, 30 %, 70 % and 100 % of the width,
    and (given ``reference_extent``, the width a known-good small probe covered) the surface is
    not cut short."""
    lows, highs = _marker_runs(rgb, low_rgb), _marker_runs(rgb, high_rgb)
    if len(lows) != 2 or len(highs) != 2:
        return False
    left = lows[0][0]
    right = highs[1][1]
    extent = right - left
    if extent < 40 or lows[0][0] > highs[0][0] or highs[0][1] > lows[1][0] or lows[1][1] > highs[1][0]:
        return False
    if reference_extent is not None and abs(extent - reference_extent) > TOLERANCE * reference_extent:
        return False
    centre = lambda run: ((run[0] + run[1]) / 2 - left) / extent
    return (abs(centre(highs[0]) - 0.3) <= TOLERANCE and abs(centre(lows[1]) - 0.7) <= TOLERANCE)


def marker_extent(rgb: np.ndarray, low_rgb, high_rgb) -> float | None:
    """Pixel width between the first lowest-colour band and the last highest-colour band."""
    lows, highs = _marker_runs(rgb, low_rgb), _marker_runs(rgb, high_rgb)
    if not lows or not highs:
        return None
    return float(highs[-1][1] - lows[0][0])


def find_axis_limit(needed: int, render_ok) -> int | None:
    """Largest axis length up to ``needed`` that ``render_ok(n)`` accepts; ``None`` when ``needed``
    itself is fine (no limit). Results are remembered for later, larger requests."""
    needed = int(needed)
    if needed <= PROBE_FROM or needed <= _state["ok"]:
        return None
    if _state["bad"] is not None and needed >= _state["bad"]:
        return _state["ok"] if _state["ok"] else None
    ladder = [needed] + [value for value in CANDIDATES if value < needed]
    for count in ladder:
        if count <= _state["ok"]:
            break
        if render_ok(count):
            _state["ok"] = count
            break
        _state["bad"] = count if _state["bad"] is None else min(_state["bad"], count)
    else:
        return None
    return None if _state["ok"] >= needed else _state["ok"]


def reset_probe_state() -> None:
    _state.update(ok=0, bad=None, reference=None)
