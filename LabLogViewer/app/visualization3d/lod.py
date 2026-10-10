"""Feature-aware index selection for rectilinear scientific surfaces.

The output is an index map into the original grid, never a replacement for
the scientific array. Keeping shared row/column indices preserves z[y, x].
"""

from __future__ import annotations

import math

import numpy as np


def lod_shape(rows: int, columns: int, budget: int) -> tuple[int, int]:
    if budget < 4:
        raise ValueError("A surface LOD needs at least four vertices.")
    if rows * columns <= budget:
        return rows, columns
    row_count = max(2, min(rows, int(math.sqrt(budget * rows / columns))))
    column_count = max(2, min(columns, budget // row_count))
    while row_count * column_count > budget:
        if column_count > 2:
            column_count -= 1
        else:
            row_count -= 1
    return row_count, column_count


def _curvature_scores(values: np.ndarray, axis: int) -> np.ndarray:
    """Maximum local departure from a straight line, in bounded chunks."""
    length = values.shape[axis]
    score = np.zeros(length, dtype=np.float64)
    if length < 3:
        return score
    if axis == 1:
        for start in range(0, values.shape[0], 128):
            block = values[start:start + 128]
            curvature = np.abs(2 * block[:, 1:-1] - block[:, :-2] - block[:, 2:])
            score[1:-1] = np.maximum(
                score[1:-1], np.max(np.nan_to_num(curvature, nan=0.0, posinf=0.0), axis=0)
            )
    else:
        for start in range(0, values.shape[1], 512):
            block = values[:, start:start + 512]
            curvature = np.abs(2 * block[1:-1] - block[:-2] - block[2:])
            score[1:-1] = np.maximum(
                score[1:-1], np.max(np.nan_to_num(curvature, nan=0.0, posinf=0.0), axis=1)
            )
    return score


def _choose_axis_indices(length: int, count: int, scores: np.ndarray,
                         required: set[int]) -> np.ndarray:
    if count >= length:
        return np.arange(length, dtype=np.intp)
    # Uniform anchors preserve broad shape. A local curvature winner in each
    # interval replaces its anchor only when it is a meaningful sharp feature.
    anchors = np.rint(np.linspace(0, length - 1, count)).astype(np.intp)
    interior_required = {int(i) for i in required if 0 < i < length - 1}
    interior_required = set(sorted(
        interior_required, key=lambda i: (scores[i], -i), reverse=True
    )[:max(0, count - 2)])
    chosen = {0, length - 1, *interior_required}
    for position in anchors[1:-1]:
        half = max(1, int(math.ceil((length - 1) / (count - 1) / 2)))
        lo, hi = max(1, int(position) - half), min(length - 1, int(position) + half + 1)
        best = lo + int(np.argmax(scores[lo:hi])) if hi > lo else int(position)
        chosen.add(best if scores[best] > 0 else int(position))
    if len(chosen) > count:
        protected = {0, length - 1, *interior_required}
        candidates = sorted(chosen - protected, key=lambda i: (scores[i], -i), reverse=True)
        chosen = protected | set(candidates[:max(0, count - len(protected))])
    if len(chosen) < count:
        for index in anchors:
            chosen.add(int(index))
            if len(chosen) == count:
                break
    if len(chosen) < count:
        for index in range(length):
            chosen.add(index)
            if len(chosen) == count:
                break
    return np.asarray(sorted(chosen), dtype=np.intp)


def feature_preserving_indices(values: np.ndarray, budget: int) -> tuple[np.ndarray, np.ndarray]:
    """Select shared axes including global extrema and prominent narrow features."""
    field = np.asarray(values)
    if field.ndim != 2 or min(field.shape) < 2:
        raise ValueError("LOD requires a two-dimensional surface grid.")
    rows, columns = field.shape
    target_rows, target_columns = lod_shape(rows, columns, budget)
    if (target_rows, target_columns) == field.shape:
        return np.arange(rows, dtype=np.intp), np.arange(columns, dtype=np.intp)
    finite = np.isfinite(field)
    if not np.any(finite):
        raise ValueError("The surface has no finite samples.")
    low = np.unravel_index(int(np.argmin(np.where(finite, field, np.inf))), field.shape)
    high = np.unravel_index(int(np.argmax(np.where(finite, field, -np.inf))), field.shape)
    row_scores = _curvature_scores(field, 0)
    column_scores = _curvature_scores(field, 1)
    return (
        _choose_axis_indices(rows, target_rows, row_scores, {low[0], high[0]}),
        _choose_axis_indices(columns, target_columns, column_scores, {low[1], high[1]}),
    )
