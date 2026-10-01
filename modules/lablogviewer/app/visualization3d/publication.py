"""High-resolution, snapshot-based 3D figures independent of the Qt viewport.

The reference plot.ipynb uses a Surface plus a color-mapped floor Surface.
Matplotlib's Agg backend supplies static PNG/SVG output and real RGBA blending
without depending on a browser or Qt Data Visualization's opaque surface shader.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from io import BytesIO

import numpy as np

from app.palette import PUBLICATION
from app.visualization3d.data import SurfaceGrid, prepare_surface_grid


EXPORT_VERTEX_BUDGET = 20_000


def _frozen(values: np.ndarray) -> np.ndarray:
    result = np.array(values, copy=True)
    result.setflags(write=False)
    return result


def frozen_surface_grid(grid: SurfaceGrid) -> SurfaceGrid:
    return replace(
        grid,
        x_values=_frozen(grid.x_values), y_values=_frozen(grid.y_values),
        z_values=_frozen(grid.z_values), color_values=_frozen(grid.color_values),
        source_row_indices=_frozen(grid.source_row_indices),
        source_column_indices=_frozen(grid.source_column_indices),
    )


@dataclass(frozen=True)
class Publication3DSnapshot:
    geometry: str
    primary: SurfaceGrid | None
    secondary: SurfaceGrid | None
    points: np.ndarray | None
    point_colors: np.ndarray | None
    axis_labels: tuple[str, str, str]
    title: str
    color_label: str
    colormap: str
    color_range: tuple[float, float]
    opacity: float
    secondary_opacity: float
    bottom_projection: bool
    projection_opacity: float
    reference_mode: str
    reference_value: float
    z_scale: float
    camera_azimuth: float
    camera_elevation: float
    camera_zoom: float
    camera_target: tuple[float, float, float]
    projection: str


def snapshot_surface(
    height_grid, color_grid, *, geometry: str, secondary: SurfaceGrid | None = None,
    **settings,
) -> Publication3DSnapshot:
    primary = prepare_surface_grid(
        height_grid, color_grid=color_grid, max_vertices=EXPORT_VERTEX_BUDGET,
    )
    return Publication3DSnapshot(
        geometry=geometry, primary=frozen_surface_grid(primary),
        secondary=(frozen_surface_grid(prepare_surface_grid(
            secondary, max_vertices=EXPORT_VERTEX_BUDGET,
        )) if secondary is not None else None),
        points=None, point_colors=None, **settings,
    )


def snapshot_points(points: np.ndarray, colors: np.ndarray, *, geometry: str, **settings) -> Publication3DSnapshot:
    points = np.asarray(points, dtype=np.float64)
    colors = np.asarray(colors, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or colors.shape != (len(points),):
        raise ValueError("Publication point data must contain aligned XYZ and color values.")
    return Publication3DSnapshot(
        geometry=geometry, primary=None, secondary=None,
        points=_frozen(points), point_colors=_frozen(colors), **settings,
    )


def render_publication(snapshot: Publication3DSnapshot, image_format: str = "png") -> bytes:
    """Render one frozen scientific scene; PNG and SVG share this composition."""
    if image_format not in {"png", "svg"}:
        raise ValueError("3D publication output supports PNG and SVG.")
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.colors import ListedColormap, Normalize
    from matplotlib.figure import Figure
    from matplotlib.cm import ScalarMappable
    from app.gui.plot_2d_widget import get_colormap

    ramp = np.asarray(get_colormap(snapshot.colormap).map(np.linspace(0, 1, 256), mode="byte")) / 255.0
    cmap = ListedColormap(ramp[:, :3])
    norm = Normalize(*snapshot.color_range, clip=True)
    figure = Figure(figsize=(10.8, 7.8), dpi=220, facecolor="white")
    FigureCanvasAgg(figure)
    axes = figure.add_axes((0.075, 0.09, 0.85, 0.75), projection="3d", proj_type=(
        "ortho" if snapshot.projection == "orthographic" else "persp"
    ))
    axes.set_facecolor("white")
    axes.set_xlabel(snapshot.axis_labels[0], labelpad=12)
    axes.set_ylabel(snapshot.axis_labels[1], labelpad=12)
    axes.set_zlabel(snapshot.axis_labels[2], labelpad=10)
    axes.view_init(elev=snapshot.camera_elevation, azim=snapshot.camera_azimuth)
    axes.set_title(snapshot.title, fontsize=12, pad=14)
    axes.grid(True, color=PUBLICATION["legacy_grid"], linewidth=0.5)

    if snapshot.primary is not None:
        grid = snapshot.primary
        x, y = np.meshgrid(grid.x_values, grid.y_values)
        raw_z = np.asarray(grid.z_values, dtype=np.float64)
        finite = raw_z[np.isfinite(raw_z)]
        low, high = float(np.min(finite)), float(np.max(finite))
        if snapshot.secondary is not None:
            secondary_finite = snapshot.secondary.z_values[np.isfinite(snapshot.secondary.z_values)]
            if secondary_finite.size:
                low = min(low, float(np.min(secondary_finite)))
                high = max(high, float(np.max(secondary_finite)))
        span = max(high - low, 1e-12)
        center_height = 0.5 * (low + high)
        z = center_height + (raw_z - center_height) * snapshot.z_scale
        z_floor = (center_height + (low - center_height) * snapshot.z_scale
                   - 0.16 * span * max(1.0, snapshot.z_scale))

        if snapshot.bottom_projection:
            floor_rgba = cmap(norm(grid.color_values))
            floor_rgba[..., 3] = snapshot.projection_opacity
            axes.plot_surface(
                x, y, np.full_like(z, z_floor), facecolors=floor_rgba,
                linewidth=0, antialiased=False, shade=False, rstride=1, cstride=1,
            )
        if snapshot.reference_mode != "off":
            reference = {
                "minimum": low, "zero": 0.0, "custom": snapshot.reference_value,
            }.get(snapshot.reference_mode, low)
            reference = center_height + (reference - center_height) * snapshot.z_scale
            axes.plot_surface(
                x[::max(1, x.shape[0] // 25), ::max(1, x.shape[1] // 25)],
                y[::max(1, y.shape[0] // 25), ::max(1, y.shape[1] // 25)],
                np.full_like(x[::max(1, x.shape[0] // 25), ::max(1, x.shape[1] // 25)], reference),
                color=PUBLICATION["legacy_plane"], alpha=0.18, linewidth=0, shade=False,
            )
        if snapshot.geometry == "Waterfall":
            for row in range(len(y)):
                axes.plot(x[row], y[row], z[row], color=cmap(norm(grid.color_values[row]).mean(axis=0)),
                          linewidth=0.65, alpha=max(0.15, snapshot.opacity))
        else:
            colors = cmap(norm(grid.color_values))
            colors[..., 3] = snapshot.opacity
            axes.plot_surface(x, y, z, facecolors=colors, linewidth=0,
                              antialiased=False, shade=False, rstride=1, cstride=1)
        if snapshot.secondary is not None:
            second = snapshot.secondary
            sx, sy = np.meshgrid(second.x_values, second.y_values)
            second_z = center_height + (np.asarray(second.z_values) - center_height) * snapshot.z_scale
            second_colors = cmap(norm(second.color_values))
            second_colors[..., 3] = snapshot.secondary_opacity
            axes.plot_surface(sx, sy, second_z, facecolors=second_colors,
                              linewidth=0, antialiased=False, shade=False, rstride=1, cstride=1)
        if snapshot.bottom_projection:
            axes.set_zlim(z_floor, max(float(np.nanmax(z)), high) + 0.05 * span)
    elif snapshot.points is not None:
        points = np.array(snapshot.points, copy=True)
        center = 0.5 * (float(np.min(points[:, 2])) + float(np.max(points[:, 2])))
        points[:, 2] = center + (points[:, 2] - center) * snapshot.z_scale
        if snapshot.geometry == "Trajectory":
            from mpl_toolkits.mplot3d.art3d import Line3DCollection
            segments = np.stack((points[:-1], points[1:]), axis=1)
            line = Line3DCollection(segments, cmap=cmap, norm=norm, linewidth=1.3, alpha=snapshot.opacity)
            line.set_array(snapshot.point_colors[:-1])
            axes.add_collection3d(line)
            axes.auto_scale_xyz(points[:, 0], points[:, 1], points[:, 2])
        else:
            axes.scatter(points[:, 0], points[:, 1], points[:, 2], c=snapshot.point_colors,
                         cmap=cmap, norm=norm, s=3, alpha=snapshot.opacity, depthshade=False)
    else:
        raise ValueError("The 3D snapshot has no geometry.")

    zoom = float(np.clip(snapshot.camera_zoom / 100.0, 0.2, 5.0))
    for getter, setter, target in (
        (axes.get_xlim, axes.set_xlim, snapshot.camera_target[0]),
        (axes.get_ylim, axes.set_ylim, snapshot.camera_target[2]),
        (axes.get_zlim, axes.set_zlim, snapshot.camera_target[1]),
    ):
        low, high = getter()
        span = max(high - low, 1e-12)
        center = 0.5 * (low + high) + (float(target) - 0.5) * span
        setter(center - span / (2.0 * zoom), center + span / (2.0 * zoom))

    colorbar_axis = figure.add_axes((0.18, 0.925, 0.57, 0.016))
    colorbar = figure.colorbar(ScalarMappable(norm=norm, cmap=cmap), cax=colorbar_axis,
                               orientation="horizontal", label=snapshot.color_label)
    if colorbar.solids is not None:
        colorbar.solids.set_rasterized(False)
    colorbar.ax.xaxis.set_ticks_position("top")
    colorbar.ax.xaxis.set_label_position("top")
    output = BytesIO()
    figure.savefig(output, format=image_format, dpi=220, facecolor="white")
    return output.getvalue()
