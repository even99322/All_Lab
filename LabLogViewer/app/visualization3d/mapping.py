"""Scientific mapping helpers shared by native 3D geometry adapters.

These functions only create display representations. The source experiment
arrays and Labber files are never modified.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

import numpy as np

from app.core.data_model import Grid2DData
from app.plotting.complex_transform import apply_transform


class GeometryType(StrEnum):
    SURFACE = "Surface"
    TRANSPARENT_SURFACE = "Transparent Surface"
    DUAL_SURFACE = "Dual Surface"
    WATERFALL = "Waterfall"
    TRAJECTORY = "Trajectory"
    SCATTER = "Scatter"


TRANSFORM_LABELS_3D = {
    "raw": "Raw",
    "real": "Real",
    "imag": "Imaginary",
    "magnitude": "Magnitude",
    "magnitude_db": "Magnitude (dB)",
    "phase_deg": "Phase (deg)",
    "phase_rad": "Phase (rad)",
    "phase_unwrapped_deg": "Unwrapped Phase (deg)",
    "phase_unwrapped_rad": "Unwrapped Phase (rad)",
}


def unwrap_phase_grid(values: np.ndarray, *, axis: int, unit: str = "deg") -> np.ndarray:
    """Unwrap finite 1D runs independently along one real grid dimension."""
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 2 or axis not in (0, 1):
        raise ValueError("Phase unwrapping requires a 2D grid and axis 0 or 1.")
    if unit not in ("deg", "rad"):
        raise ValueError("Phase unit must be 'deg' or 'rad'.")
    moved = np.moveaxis(array, axis, -1)
    result = moved.copy()
    factor = np.pi / 180.0 if unit == "deg" else 1.0
    scale = 180.0 / np.pi if unit == "deg" else 1.0
    for row_index, row in enumerate(moved.reshape(-1, moved.shape[-1])):
        valid = np.isfinite(row)
        starts = np.flatnonzero(valid & np.r_[True, ~valid[:-1]])
        ends = np.flatnonzero(valid & np.r_[~valid[1:], True]) + 1
        output = result.reshape(-1, result.shape[-1])[row_index]
        for start, end in zip(starts, ends):
            output[start:end] = np.unwrap(row[start:end] * factor) * scale
    return np.moveaxis(result, -1, axis)


def transform_grid_3d(grid: Grid2DData, transform: str, *, unwrap_axis: int = 1) -> Grid2DData:
    """Apply a regular complex transform, optionally unwrapping one axis."""
    base_transform = transform
    unit = None
    if transform == "phase_unwrapped_deg":
        base_transform, unit = "phase_deg", "deg"
    elif transform == "phase_unwrapped_rad":
        base_transform, unit = "phase_rad", "rad"
    if unit is not None and getattr(grid, "transform", "raw") == base_transform:
        values = np.asarray(grid.z_values)
    else:
        values = np.asarray(apply_transform(np.asarray(grid.z_values), base_transform))
    if unit is not None:
        values = unwrap_phase_grid(values, axis=unwrap_axis, unit=unit)
    return replace(grid, z_values=values, transform=transform)


def mapping_values(grid: Grid2DData, key: str) -> np.ndarray:
    """Resolve an XYZ mapping against the actual dimensions in a 2D grid.

    ``point_index`` is a zero-based C-order index into the original ``z[y, x]``
    grid, so a point can be mapped back without changing its scientific sample.
    """
    rows, columns = np.asarray(grid.z_values).shape
    if key in ("x", "frequency", grid.x_name):
        return np.broadcast_to(np.asarray(grid.x_values)[None, :], (rows, columns))
    if key in ("y", "sweep", grid.y_name):
        return np.broadcast_to(np.asarray(grid.y_values)[:, None], (rows, columns))
    if key in ("point_index", "index"):
        return np.arange(rows * columns, dtype=np.float64).reshape(rows, columns)
    transforms = {
        "raw": "raw", "real": "real", "imag": "imag", "imaginary": "imag",
        "magnitude": "magnitude", "magnitude_db": "magnitude_db",
        "phase": "phase_deg", "phase_deg": "phase_deg", "phase_rad": "phase_rad",
        "phase_unwrapped": "phase_unwrapped_deg",
        "phase_unwrapped_deg": "phase_unwrapped_deg",
        "phase_unwrapped_rad": "phase_unwrapped_rad",
    }
    transform = transforms.get(key)
    if transform is None:
        raise ValueError(f"Unsupported scientific 3D mapping: {key}")
    return np.asarray(transform_grid_3d(grid, transform).z_values)


@dataclass(frozen=True)
class WaterfallDisplayGrid:
    """Wireframe-ready trace rows and their source indices.

    Every second row is a NaN separator so Qt's wireframe does not bridge
    neighboring traces. The retained scientific rows/columns index the source.
    """

    grid: Grid2DData
    color_grid: Grid2DData
    source_rows: np.ndarray
    source_columns: np.ndarray
    original_shape: tuple[int, int]
    source_height: Grid2DData
    source_color: Grid2DData


def _column_feature_scores(values: np.ndarray) -> np.ndarray:
    scores = np.zeros(values.shape[1], dtype=np.float64)
    if values.shape[1] < 3:
        return scores
    curvature = np.abs(2.0 * values[:, 1:-1] - values[:, :-2] - values[:, 2:])
    finite = np.isfinite(curvature)
    scores[1:-1] = np.max(np.where(finite, curvature, 0.0), axis=0)
    return scores


def _ranked_indices(length: int, count: int, scores: np.ndarray) -> np.ndarray:
    if count >= length:
        return np.arange(length, dtype=np.intp)
    anchors = np.rint(np.linspace(0, length - 1, count)).astype(np.intp)
    chosen = set(int(index) for index in anchors)
    feature_count = min(max(1, count // 4), count - 2)
    candidates = np.argsort(scores[1:-1], kind="stable")[-feature_count:] + 1
    for feature in candidates:
        feature = int(feature)
        if feature in chosen:
            continue
        replaceable = [index for index in chosen if index not in (0, length - 1)]
        if not replaceable:
            break
        weakest = min(replaceable, key=lambda index: (scores[index], -index))
        chosen.remove(weakest)
        chosen.add(feature)
    return np.asarray(sorted(chosen), dtype=np.intp)


def prepare_waterfall_grid(
    height: Grid2DData,
    color: Grid2DData | None = None,
    *,
    max_vertices: int = 100_000,
) -> WaterfallDisplayGrid:
    """Build separated, feature-aware trace lines under a bounded vertex budget."""
    color = height if color is None else color
    z = np.asarray(height.z_values)
    c = np.asarray(color.z_values)
    if z.ndim != 2 or z.shape != c.shape or z.shape != (len(height.y_values), len(height.x_values)):
        raise ValueError("Waterfall height and color must share a regular (Y, X) grid.")
    if np.iscomplexobj(z) or np.iscomplexobj(c):
        raise ValueError("Waterfall mappings must be real-valued.")
    if max_vertices < 8:
        raise ValueError("Waterfall rendering needs a budget of at least eight vertices.")

    row_count, column_count = z.shape
    if (not np.array_equal(np.asarray(color.x_values), np.asarray(height.x_values))
            or not np.array_equal(np.asarray(color.y_values), np.asarray(height.y_values))):
        raise ValueError("Waterfall height and color must share the same scientific X/Y coordinates.")
    row_budget = max(2, min(row_count, max_vertices // 4))
    if row_budget < row_count:
        row_score = np.nanmax(np.abs(np.nan_to_num(z)), axis=1)
        source_rows = _ranked_indices(row_count, row_budget, row_score)
    else:
        source_rows = np.arange(row_count, dtype=np.intp)
    line_budget = max(2, min(column_count, max_vertices // (2 * len(source_rows))))
    selected_z = np.asarray(z[np.ix_(source_rows, np.arange(column_count))], dtype=np.float64)
    source_columns = _ranked_indices(column_count, line_budget, _column_feature_scores(selected_z))

    values = selected_z[:, source_columns]
    colors = np.asarray(c[np.ix_(source_rows, source_columns)], dtype=np.float64)
    rows, columns = values.shape
    waterfall_z = np.full((rows * 2, columns), np.nan, dtype=np.float64)
    waterfall_c = np.full((rows * 2, columns), np.nan, dtype=np.float64)
    waterfall_z[::2] = values
    waterfall_c[::2] = colors
    source_y = np.asarray(height.y_values, dtype=np.float64)[source_rows]
    waterfall_y = np.empty(rows * 2, dtype=np.float64)
    waterfall_y[::2] = source_y
    if rows > 1:
        waterfall_y[1:-1:2] = (source_y[:-1] + source_y[1:]) / 2.0
        waterfall_y[-1] = source_y[-1] + (source_y[-1] - source_y[-2]) / 2.0
    else:
        waterfall_y[1] = source_y[0]
    display = replace(
        height,
        x_values=np.asarray(height.x_values)[source_columns],
        y_values=waterfall_y,
        z_values=waterfall_z,
        transform=f"waterfall:{height.transform}",
    )
    waterfall_color_grid = replace(
        color, x_values=np.asarray(color.x_values)[source_columns],
        y_values=waterfall_y, z_values=waterfall_c,
    )
    return WaterfallDisplayGrid(
        display, waterfall_color_grid, source_rows, source_columns, tuple(z.shape), height, color
    )


@dataclass(frozen=True)
class PointCloudData:
    coordinates: np.ndarray
    color_values: np.ndarray
    source_indices: np.ndarray
    source_shape: tuple[int, ...]


def prepare_point_cloud(
    x: np.ndarray, y: np.ndarray, z: np.ndarray, color: np.ndarray | None = None,
    *, max_points: int = 100_000,
) -> PointCloudData:
    """Flatten mappings and build a bounded, feature-aware display point set."""
    inputs = (x, y, z) if color is None else (x, y, z, color)
    if any(np.asarray(value).dtype.kind not in "biuf" for value in inputs):
        raise ValueError("3D point mappings must be real-valued numeric arrays.")
    arrays = np.broadcast_arrays(
        np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64), np.asarray(z, dtype=np.float64)
    )
    source_shape = tuple(int(value) for value in arrays[0].shape)
    flat_coordinates = tuple(value.reshape(-1) for value in arrays)
    flat_colors = flat_coordinates[2] if color is None else np.broadcast_to(
        np.asarray(color, dtype=np.float64), source_shape
    ).reshape(-1)
    finite = np.isfinite(flat_colors)
    for values in flat_coordinates:
        finite &= np.isfinite(values)
    source_indices = np.flatnonzero(finite)
    if source_indices.size == 0:
        raise ValueError("The selected 3D mapping contains no finite points.")
    if max_points < 2:
        raise ValueError("A point-cloud display budget must be at least two.")
    if len(source_indices) > max_points:
        count = len(source_indices)
        feature_fields = (*flat_coordinates, flat_colors)
        scores = np.zeros(count, dtype=np.float32)
        extrema = {0, count - 1}
        scales = []
        chunk_size = 1_000_000
        for values in feature_fields:
            low, high = np.inf, -np.inf
            low_index = high_index = 0
            for start in range(0, count, chunk_size):
                stop = min(count, start + chunk_size)
                block = values[source_indices[start:stop]]
                local_low, local_high = float(np.min(block)), float(np.max(block))
                if local_low < low:
                    low, low_index = local_low, start + int(np.argmin(block))
                if local_high > high:
                    high, high_index = local_high, start + int(np.argmax(block))
            extrema.update((low_index, high_index))
            scales.append(max(high - low, np.finfo(np.float64).tiny))

        for values, scale in zip(feature_fields, scales):
            for start in range(1, count - 1, chunk_size):
                stop = min(count - 1, start + chunk_size)
                left = values[source_indices[start - 1:stop - 1]]
                center = values[source_indices[start:stop]]
                right = values[source_indices[start + 1:stop + 1]]
                curvature = (np.abs(2.0 * center - left - right) / scale).astype(np.float32)
                scores[start:stop] = np.maximum(scores[start:stop], curvature)

        if len(extrema) > max_points:
            extrema = {0, count - 1}
        anchor_count = max(2, max_points - len(extrema))
        anchors = set(np.rint(np.linspace(0, count - 1, anchor_count)).astype(np.intp).tolist())
        chosen = extrema | anchors
        remaining = max_points - len(chosen)
        if remaining > 0:
            candidate_count = min(count, len(chosen) + remaining + len(extrema))
            candidates = np.argpartition(scores, count - candidate_count)[count - candidate_count:]
            candidates = candidates[np.argsort(scores[candidates], kind="stable")[::-1]]
            for index in candidates:
                chosen.add(int(index))
                if len(chosen) >= max_points:
                    break
        kept_positions = np.asarray(sorted(chosen), dtype=np.intp)[:max_points]
        source_indices = source_indices[kept_positions]
    coords = np.column_stack([values[source_indices] for values in flat_coordinates])
    colors = flat_colors[source_indices]
    return PointCloudData(coords, colors, source_indices, source_shape)


def prepare_trajectory_trace(
    x: np.ndarray, y: np.ndarray, z: np.ndarray, color: np.ndarray,
    *, trace_index: int, max_points: int = 20_000,
) -> PointCloudData:
    """Prepare one selected row of z[y, x], retaining original sample indices."""
    arrays = tuple(np.asarray(value) for value in (x, y, z, color))
    shape = arrays[2].shape
    if (len(shape) != 2 or any(value.shape != shape for value in arrays)
            or not 0 <= trace_index < shape[0]):
        raise ValueError("The selected trajectory trace is unavailable or has incompatible mappings.")
    row = tuple(value[trace_index] for value in arrays)
    cloud = prepare_point_cloud(*row, max_points=max_points)
    if len(cloud.coordinates) < 2:
        raise ValueError("A trajectory requires at least two finite points in the selected trace.")
    return replace(
        cloud, source_indices=cloud.source_indices + trace_index * shape[1], source_shape=shape,
    )
