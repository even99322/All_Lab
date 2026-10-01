"""Uniform, peak-preserving display grids for bulk GPU upload.

Qt Graphs uploads a surface in one call only for an evenly spaced grid
(``QSurfaceDataProxy.resetArrayNp``). Measurement axes may be unevenly spaced
and large grids need a display budget, so the display grid is built on evenly
spaced physical coordinates:

* Every display cell covers an equal physical interval on each axis.
* Within a cell the source sample that departs most from the cell mean is
  kept, so narrow resonance dips keep their full depth instead of being
  averaged away. Its position moves by at most half a cell (display only).
* Each display cell records the source (row, column) it represents, so
  picking and readouts always report real, untransformed source samples.

When an axis is already evenly spaced and fits the budget the mapping is the
identity and nothing moves.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


UNIFORM_TOLERANCE = 1e-3     # relative spacing deviation still treated as even
KEEP_SHORT_AXIS_MIN = 512    # keep the short axis whole if the long one keeps >= this


@dataclass(frozen=True)
class DisplayGrid:
    """Display heights/colors on evenly spaced physical centers, ``[y, x]``."""

    heights: np.ndarray
    colors: np.ndarray
    x_centers: np.ndarray
    y_centers: np.ndarray
    source_rows: np.ndarray
    source_columns: np.ndarray
    source_shape: tuple[int, int]
    exact: bool

    @property
    def shape(self) -> tuple[int, int]:
        return tuple(int(value) for value in self.heights.shape)

    @property
    def vertex_count(self) -> int:
        return int(self.heights.size)


def display_shape(rows: int, columns: int, budget: int | None) -> tuple[int, int]:
    """Choose display rows/columns within ``budget`` vertices.

    The short axis (usually the sweep) is kept whole while the long axis
    still gets at least ``KEEP_SHORT_AXIS_MIN`` samples; otherwise both axes
    shrink proportionally.
    """
    rows, columns = int(rows), int(columns)
    if budget is None or rows * columns <= budget:
        return rows, columns
    if budget < 4:
        raise ValueError("A display grid needs at least four vertices.")
    if rows <= columns and budget // rows >= min(columns, KEEP_SHORT_AXIS_MIN):
        return rows, max(2, min(columns, budget // rows))
    if columns < rows and budget // columns >= min(rows, KEEP_SHORT_AXIS_MIN):
        return max(2, min(rows, budget // columns)), columns
    scale = math.sqrt(budget / (rows * columns))
    out_rows = max(2, min(rows, int(rows * scale)))
    out_columns = max(2, min(columns, budget // out_rows))
    return out_rows, out_columns


def _is_uniform(sorted_values: np.ndarray) -> bool:
    if sorted_values.size < 3:
        return True
    steps = np.diff(sorted_values)
    mean = float(np.mean(steps))
    if mean <= 0:
        return False
    return float(np.max(np.abs(steps - mean))) <= UNIFORM_TOLERANCE * mean


def _axis_bins(values: np.ndarray, count: int):
    """Map an axis onto ``count`` evenly spaced centers.

    Returns (order, starts, stops, centers, exact): ``order`` sorts the source
    ascending; bin ``b`` covers ``order[starts[b]:stops[b]]`` (never empty).
    """
    values = np.asarray(values, dtype=np.float64)
    order = np.argsort(values, kind="stable")
    ordered = values[order]
    n = ordered.size
    low, high = float(ordered[0]), float(ordered[-1])
    if n <= count and _is_uniform(ordered):
        index = np.arange(n)
        return order, index, index + 1, ordered.copy(), True
    count = max(2, min(count, n))
    centers = np.linspace(low, high, count)
    if high == low:
        positions = np.zeros(n, dtype=np.intp)
    else:
        positions = np.rint((ordered - low) / (high - low) * (count - 1)).astype(np.intp)
    starts = np.searchsorted(positions, np.arange(count), side="left")
    stops = np.searchsorted(positions, np.arange(count), side="right")
    empty = starts == stops
    if np.any(empty):
        # Sparse regions: represent the cell by the nearest source sample.
        nearest = np.clip(np.searchsorted(ordered, centers[empty]), 1, n - 1)
        left_closer = (centers[empty] - ordered[nearest - 1]) <= (ordered[nearest] - centers[empty])
        chosen = np.where(left_closer, nearest - 1, nearest)
        starts = starts.copy(); stops = stops.copy()
        starts[empty] = chosen
        stops[empty] = chosen + 1
    return order, starts, stops, centers, False


def _peak_pick(block: np.ndarray, axis: int) -> np.ndarray:
    """Index along ``axis`` of the sample farthest from the block mean."""
    with np.errstate(invalid="ignore"):
        finite = np.isfinite(block)
        counts = np.maximum(finite.sum(axis=axis, keepdims=True), 1)
        mean = np.where(finite, block, 0.0).sum(axis=axis, keepdims=True) / counts
        deviation = np.where(finite, np.abs(block - mean), -1.0)
    return np.argmax(deviation, axis=axis)


def build_display_grid(x_values, y_values, heights, colors=None, *,
                       max_vertices: int | None = None) -> DisplayGrid:
    """Peak-preserving evenly spaced display grid for ``heights[y, x]``."""
    heights = np.asarray(heights, dtype=np.float64)
    colors = heights if colors is None else np.asarray(colors, dtype=np.float64)
    x_values = np.asarray(x_values, dtype=np.float64)
    y_values = np.asarray(y_values, dtype=np.float64)
    if heights.ndim != 2 or heights.shape != (y_values.size, x_values.size):
        raise ValueError("Display heights must have shape (len(Y), len(X)).")
    if colors.shape != heights.shape:
        raise ValueError("Display colors must match the height grid.")
    if x_values.size < 2 or y_values.size < 2:
        raise ValueError("A 3D surface needs at least two samples on X and Y.")
    rows, columns = heights.shape
    out_rows, out_columns = display_shape(rows, columns, max_vertices)

    x_order, x_starts, x_stops, x_centers, x_exact = _axis_bins(x_values, out_columns)
    y_order, y_starts, y_stops, y_centers, y_exact = _axis_bins(y_values, out_rows)

    # Pass 1: along X, per source row.
    ordered_h = heights[:, x_order]
    if x_exact:
        column_pick = np.broadcast_to(x_order, (rows, x_order.size)).copy()
    else:
        column_pick = np.empty((rows, x_starts.size), dtype=np.intp)
        for index, (start, stop) in enumerate(zip(x_starts, x_stops)):
            if stop - start == 1:
                column_pick[:, index] = x_order[start]
            else:
                local = _peak_pick(ordered_h[:, start:stop], axis=1)
                column_pick[:, index] = x_order[start + local]
    stage_h = np.take_along_axis(heights, column_pick, axis=1)

    # Pass 2: along Y, per display column.
    stage_h = stage_h[y_order]
    stage_columns = column_pick[y_order]
    if y_exact:
        row_pick_ordered = np.broadcast_to(np.arange(rows)[:, None], stage_h.shape).copy()
    else:
        row_pick_ordered = np.empty((y_starts.size, stage_h.shape[1]), dtype=np.intp)
        for index, (start, stop) in enumerate(zip(y_starts, y_stops)):
            if stop - start == 1:
                row_pick_ordered[index, :] = start
            else:
                local = _peak_pick(stage_h[start:stop], axis=0)
                row_pick_ordered[index, :] = start + local
    source_rows = y_order[row_pick_ordered]
    source_columns = np.take_along_axis(stage_columns, row_pick_ordered, axis=0)
    return DisplayGrid(
        heights=heights[source_rows, source_columns],
        colors=colors[source_rows, source_columns],
        x_centers=x_centers,
        y_centers=y_centers,
        source_rows=source_rows,
        source_columns=source_columns,
        source_shape=(rows, columns),
        exact=bool(x_exact and y_exact),
    )


def select_waterfall_traces(trace_count: int, limit: int = 64) -> np.ndarray:
    """Evenly spread trace indices (first and last always kept)."""
    trace_count = int(trace_count)
    if trace_count <= limit:
        return np.arange(trace_count, dtype=np.intp)
    return np.unique(np.rint(np.linspace(0, trace_count - 1, limit)).astype(np.intp))
