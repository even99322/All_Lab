"""Pure preparation of scientific grids for the interactive Surface renderer."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import TYPE_CHECKING

import numpy as np

from app.visualization3d.lod import feature_preserving_indices

if TYPE_CHECKING:
    from app.core.data_model import Grid2DData


MAX_SURFACE_VERTICES = 100_000


@dataclass(frozen=True)
class SurfaceGrid:
    """Renderer-ready grid with the invariant ``z_values[y, x]`` preserved."""

    x_values: np.ndarray
    y_values: np.ndarray
    z_values: np.ndarray
    x_name: str
    x_unit: str | None
    y_name: str
    y_unit: str | None
    z_name: str
    z_unit: str | None
    z_transform: str
    color_values: np.ndarray
    color_name: str
    color_unit: str | None
    color_transform: str
    source_shape: tuple[int, int]
    invalid_value_count: int
    sample_step: tuple[int, int]
    source_row_indices: np.ndarray
    source_column_indices: np.ndarray

    @property
    def shape(self) -> tuple[int, int]:
        return tuple(int(value) for value in self.z_values.shape)

    @property
    def vertex_count(self) -> int:
        return int(self.z_values.size)

    @property
    def was_decimated(self) -> bool:
        return self.shape != self.source_shape


@dataclass(frozen=True)
class NormalizedAxis:
    """Float32-safe scene coordinates with a reversible physical label map."""

    values: np.ndarray
    physical_min: float
    physical_max: float
    display_min: float
    display_max: float

    def physical_value(self, display_value: float) -> float:
        if self.physical_min == self.physical_max:
            return self.physical_min
        fraction = (float(display_value) - self.display_min) / (
            self.display_max - self.display_min
        )
        return self.physical_min + fraction * (self.physical_max - self.physical_min)


@dataclass(frozen=True)
class SurfaceMeshData:
    """Float64 render coordinates arranged as rows=Y, columns=X."""

    coordinates: np.ndarray
    x_axis: NormalizedAxis
    y_axis: NormalizedAxis
    z_axis: NormalizedAxis


def normalize_axis_for_scene(values: np.ndarray, *, limits: tuple[float, float] | None = None) -> NormalizedAxis:
    """Map float64 scientific coordinates onto a stable, unit scene span."""
    values = np.asarray(values, dtype=np.float64)
    finite = np.isfinite(values)
    if not np.any(finite):
        raise ValueError("A 3D axis requires at least one finite coordinate.")
    low, high = ((float(np.min(values[finite])), float(np.max(values[finite])))
                 if limits is None else (float(limits[0]), float(limits[1])))
    if not np.isfinite([low, high]).all() or low > high:
        raise ValueError("Scene-axis limits must be finite and ordered.")
    normalized = np.full(values.shape, np.nan, dtype=np.float64)
    if low == high:
        normalized[finite] = 0.0
        return NormalizedAxis(normalized, low, high, -0.5, 0.5)
    normalized[finite] = (values[finite] - low) / (high - low)
    return NormalizedAxis(normalized, low, high, 0.0, 1.0)


def prepare_surface_mesh(
    grid: SurfaceGrid, *, z_scale: float = 1.0,
    z_limits: tuple[float, float] | None = None,
) -> SurfaceMeshData:
    """Map a physical SurfaceGrid to Qt space with display-only Z exaggeration."""
    if not math.isfinite(float(z_scale)) or not 0.1 <= float(z_scale) <= 10.0:
        raise ValueError("Z Scale must be finite and between 0.1x and 10x.")
    x_axis = normalize_axis_for_scene(grid.x_values)
    y_axis = normalize_axis_for_scene(grid.y_values)
    z_axis = normalize_axis_for_scene(grid.z_values, limits=z_limits)
    center = (z_axis.display_min + z_axis.display_max) / 2.0
    scaled_values = center + (z_axis.values - center) * float(z_scale)
    scaled_min = center + (z_axis.display_min - center) * float(z_scale)
    scaled_max = center + (z_axis.display_max - center) * float(z_scale)
    z_axis = NormalizedAxis(
        values=scaled_values,
        physical_min=z_axis.physical_min,
        physical_max=z_axis.physical_max,
        display_min=scaled_min,
        display_max=scaled_max,
    )
    rows, columns = grid.shape
    mesh = np.empty((rows, columns, 3), dtype=np.float64)
    mesh[:, :, 0] = np.broadcast_to(x_axis.values[np.newaxis, :], (rows, columns))
    mesh[:, :, 1] = z_axis.values
    mesh[:, :, 2] = np.broadcast_to(y_axis.values[:, np.newaxis], (rows, columns))
    return SurfaceMeshData(mesh, x_axis, y_axis, z_axis)


def scene_height_for_value(value: float, axis: NormalizedAxis, *, z_scale: float = 1.0) -> float:
    """Map a scientific height into the same display-only Z exaggeration."""
    value = float(value)
    if not math.isfinite(value) or not math.isfinite(float(z_scale)):
        raise ValueError("Reference height and scale must be finite.")
    if axis.physical_min == axis.physical_max:
        normalized = 0.0
    else:
        normalized = (value - axis.physical_min) / (axis.physical_max - axis.physical_min)
    center = (axis.display_min + axis.display_max) / 2.0
    # display_min/max already include Z Scale. Recover the unscaled [0, 1]
    # coordinate before applying the requested exaggeration exactly once.
    if z_scale != 0:
        base_center = 0.5
        normalized = base_center + (normalized - base_center) * float(z_scale)
    return float(normalized)


def prepare_surface_grid(
    grid: "Grid2DData", *, color_grid: "Grid2DData | None" = None,
    max_vertices: int | None = None,
    indices: tuple[np.ndarray, np.ndarray] | None = None,
) -> SurfaceGrid:
    """Validate height/color grids and prepare an indexed display representation.

    The Data Model supplies coordinates and an already transformed numerical
    field. Non-finite field samples become visual gaps (NaN); non-finite axes,
    complex fields, malformed shapes, and empty surfaces are rejected.
    """
    if max_vertices is not None and max_vertices < 4:
        raise ValueError("The Surface vertex limit must be at least 4.")

    x_values = np.asarray(grid.x_values, dtype=np.float64)
    y_values = np.asarray(grid.y_values, dtype=np.float64)
    raw_z = np.asarray(grid.z_values)
    if x_values.ndim != 1 or y_values.ndim != 1:
        raise ValueError("A Surface requires one-dimensional X and Y coordinates.")
    if x_values.size < 2 or y_values.size < 2:
        raise ValueError("A Surface requires at least two values on both X and Y.")
    if raw_z.ndim != 2 or raw_z.shape != (y_values.size, x_values.size):
        raise ValueError(
            "Surface data must have shape (len(Y), len(X)); expected "
            f"({y_values.size}, {x_values.size}), received {raw_z.shape}."
        )
    if np.iscomplexobj(raw_z):
        raise ValueError("Select a real-valued Transform before displaying a Surface.")
    if not np.isfinite(x_values).all() or not np.isfinite(y_values).all():
        raise ValueError("Surface axis coordinates contain NaN or infinity.")

    color_grid = color_grid or grid
    color_x = np.asarray(color_grid.x_values, dtype=np.float64)
    color_y = np.asarray(color_grid.y_values, dtype=np.float64)
    raw_color = np.asarray(color_grid.z_values)
    if (color_x.shape != x_values.shape or color_y.shape != y_values.shape
            or raw_color.shape != raw_z.shape
            or not np.array_equal(color_x, x_values)
            or not np.array_equal(color_y, y_values)):
        raise ValueError("Color Source must use the same X/Y coordinates and grid shape as Height.")
    if np.iscomplexobj(raw_color):
        raise ValueError("Select a real-valued Transform for the Color Source.")

    z_values = np.asarray(raw_z, dtype=np.float64)
    finite_mask = np.isfinite(z_values)
    valid_count = int(np.count_nonzero(finite_mask))
    if valid_count == 0:
        raise ValueError("The selected Surface contains no finite Z values.")
    invalid_count = int(z_values.size - valid_count)
    if invalid_count:
        z_values = z_values.copy()
        z_values[~finite_mask] = np.nan
    color_values = z_values if color_grid is grid else np.asarray(raw_color, dtype=np.float64)
    if color_values is not z_values and not np.isfinite(color_values).all():
        color_values = color_values.copy()
        color_values[~np.isfinite(color_values)] = np.nan

    source_shape = (int(y_values.size), int(x_values.size))
    if indices is not None:
        rows, columns = indices
        rows = np.asarray(rows, dtype=np.intp)
        columns = np.asarray(columns, dtype=np.intp)
        if (rows.ndim != 1 or columns.ndim != 1 or rows.size < 2 or columns.size < 2
                or rows[0] < 0 or columns[0] < 0
                or rows[-1] >= source_shape[0] or columns[-1] >= source_shape[1]
                or np.any(np.diff(rows) <= 0) or np.any(np.diff(columns) <= 0)):
            raise ValueError("LOD indices must be ordered and inside the source grid.")
    elif max_vertices is not None and z_values.size > max_vertices:
        rows, columns = feature_preserving_indices(z_values, int(max_vertices))
    else:
        rows = np.arange(source_shape[0], dtype=np.intp)
        columns = np.arange(source_shape[1], dtype=np.intp)
    full = rows.size == source_shape[0] and columns.size == source_shape[1]
    selected_z = z_values if full else z_values[np.ix_(rows, columns)]
    selected_color = color_values if full else color_values[np.ix_(rows, columns)]

    return SurfaceGrid(
        x_values=x_values if full else x_values[columns],
        y_values=y_values if full else y_values[rows],
        z_values=selected_z,
        x_name=str(grid.x_name),
        x_unit=grid.x_unit,
        y_name=str(grid.y_name),
        y_unit=grid.y_unit,
        z_name=str(grid.z_name),
        z_unit=grid.z_unit,
        z_transform=str(getattr(grid, "transform", "raw")),
        color_values=selected_color,
        color_name=str(color_grid.z_name),
        color_unit=color_grid.z_unit,
        color_transform=str(getattr(color_grid, "transform", "raw")),
        source_shape=source_shape,
        invalid_value_count=invalid_count,
        sample_step=(max(1, math.ceil(source_shape[0] / rows.size)),
                     max(1, math.ceil(source_shape[1] / columns.size))),
        source_row_indices=rows,
        source_column_indices=columns,
    )
