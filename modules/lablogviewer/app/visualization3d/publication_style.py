"""Publication-style 3D export (Matplotlib, journal figure look).

The interactive 3D view is GPU-rendered; this module re-renders the current
scene state as a static journal-style figure: serif typography, light grey
panes with fine grid lines, an exact (unshaded) scientific colormap on the
surface, a color-mapped floor projection and a compact horizontal colorbar in
the upper left. Physical units are scaled to readable SI prefixes (Hz -> GHz,
A -> mA). The camera angle follows the interactive view. Source data are read
only; large grids are reduced with the same peak-preserving display grid used
on screen, so narrow resonances keep their depth.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import numpy as np

from app.palette import PUBLICATION
from app.visualization3d.display_grid import build_display_grid, select_waterfall_traces


SURFACE_FACE_BUDGET = 60_000
FLOOR_BUDGET = 250_000
EXPORT_DPI = 300
SI_UNITS = {"Hz", "A", "V", "W", "s", "T", "F", "H", "Ohm", "Ω"}
SI_PREFIXES = ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"))


@dataclass(frozen=True)
class AxisText:
    label: str
    scale: float


def si_axis(name: str, unit: str | None, values) -> AxisText:
    """Choose a readable SI prefix for an axis; non-SI units are unchanged."""
    name = str(name)
    if not unit:
        return AxisText(name, 1.0)
    if unit not in SI_UNITS:
        return AxisText(f"{name} ({unit})", 1.0)
    finite = np.abs(np.asarray(values, dtype=np.float64))
    finite = finite[np.isfinite(finite) & (finite > 0)]
    magnitude = float(np.max(finite)) if finite.size else 1.0
    for factor, prefix in SI_PREFIXES:
        if magnitude >= factor:
            return AxisText(f"{name} ({prefix}{unit})", factor)
    return AxisText(f"{name} (n{unit})", 1e-9)


def _matplotlib_colormap(name: str):
    from matplotlib.colors import ListedColormap
    from app.gui.plot_2d_widget import get_colormap

    rgba = np.asarray(get_colormap(name).map(np.linspace(0.0, 1.0, 256), mode="float"), dtype=float)
    if rgba.max() > 1.0:
        rgba = rgba / 255.0
    return ListedColormap(rgba[:, :4], name=f"lablog-{name}")


def _style_axes(ax, colors) -> None:
    pane = PUBLICATION["pane"]
    grid = PUBLICATION["grid"]
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.set_pane_color(pane)
        axis.line.set_color(PUBLICATION["axis_line"])
        info = getattr(axis, "_axinfo", None)
        if isinstance(info, dict) and "grid" in info:
            info["grid"].update(color=grid, linewidth=0.6, linestyle="-")
        axis.set_tick_params(colors=colors.text, labelsize=10, pad=2)
        axis.label.set_color(colors.text)


def _view(ax, camera: dict) -> None:
    # Interactive horizontal rotation 35 deg is the default view; Matplotlib's
    # comparable journal view is azimuth -60 deg.
    ax.view_init(elev=float(camera.get("elevation", 25.0)),
                 azim=float(camera.get("azimuth", 35.0)) - 95.0)
    if camera.get("orthographic"):
        ax.set_proj_type("ortho")


def _colorbar(fig, cmap, norm, label: str, colors) -> None:
    from matplotlib.cm import ScalarMappable

    cax = fig.add_axes([0.07, 0.885, 0.30, 0.018])
    bar = fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), cax=cax, orientation="horizontal")
    bar.outline.set_linewidth(0.6)
    cax.tick_params(labelsize=9, length=2.5, colors=colors.text)
    cax.set_title(label, fontsize=11, pad=4, color=colors.text)


def _state(renderer) -> dict:
    """Collect scene state from either renderer (Qt Graphs or Data Visualization)."""
    graph = getattr(renderer, "graph", None)
    camera = {"azimuth": 35.0, "elevation": 25.0, "orthographic": False}
    try:
        state = renderer.camera_state()
        camera["azimuth"], camera["elevation"] = float(state.x_rotation), float(state.y_rotation)
    except Exception:
        pass
    try:
        active = getattr(renderer, "active_graph", graph)
        camera["orthographic"] = bool(active.isOrthoProjection())
    except Exception:
        pass
    return {
        "geometry": str(getattr(renderer, "_geometry_type", "Surface")),
        "height": getattr(renderer, "_source_height", None) or getattr(renderer, "_source_height_grid", None),
        "color": getattr(renderer, "_source_color", None) or getattr(renderer, "_source_color_grid", None),
        "secondary": getattr(renderer, "_secondary_source", None),
        "secondary_opacity": float(getattr(renderer, "_secondary_opacity", 0.55)),
        "opacity": float(getattr(renderer, "_opacity", 1.0)),
        "colormap": str(getattr(renderer, "_colormap_name", "LabLog BWR")),
        "color_range": getattr(renderer, "_color_range", None),
        "projection": bool(getattr(renderer, "_projection_enabled", False)),
        "projection_opacity": float(getattr(renderer, "_projection_opacity", 1.0)),
        "reference_mode": str(getattr(renderer, "_reference_mode", "off")),
        "reference_value": float(getattr(renderer, "_reference_value", 0.0)),
        "z_scale": float(getattr(renderer, "_z_scale", 1.0)),
        "waterfall": getattr(renderer, "_waterfall", None),
        "cloud": getattr(renderer, "_point_cloud", None),
        "point_labels": getattr(renderer, "_point_coordinate_labels", None),
        "point_color_label": getattr(renderer, "_point_color_label", "Color"),
        "camera": camera,
    }


def _transform_text(grid) -> str:
    from app.visualization3d.renderer import _transform_label
    return _transform_label(str(getattr(grid, "transform", "raw")))


def _value_label(grid) -> str:
    unit = f" ({grid.z_unit})" if getattr(grid, "z_unit", None) else ""
    transform = _transform_text(grid)
    return f"{grid.z_name} — {transform}{unit}" if transform not in {"Raw", "raw"} else f"{grid.z_name}{unit}"


def _reference_height(mode: str, value: float, low: float, high: float):
    height = low if mode == "minimum" else 0.0 if mode == "zero" else value
    return height if mode != "off" and low <= height <= high else None


def _plane(ax, xlim, ylim, z, colors) -> None:
    xx, yy = np.meshgrid(np.asarray(xlim, float), np.asarray(ylim, float))
    dark = sum(int(colors.background.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) < 384
    face = PUBLICATION["plane_on_dark"] if dark else PUBLICATION["plane_on_light"]
    ax.plot_surface(xx, yy, np.full_like(xx, z), color=face, shade=False, linewidth=0.8,
                    edgecolor=(face[0], face[1], face[2], 0.6))


def _render_surface_family(fig, ax, state, colors):
    from matplotlib.colors import Normalize

    height, color = state["height"], state["color"] or state["height"]
    xs = np.asarray(height.x_values, dtype=float)
    ys = np.asarray(height.y_values, dtype=float)
    hz = np.asarray(height.z_values, dtype=float)
    cz = np.asarray(color.z_values, dtype=float)
    x_text = si_axis(height.x_name, height.x_unit, xs)
    y_text = si_axis(height.y_name, height.y_unit, ys)
    cmap = _matplotlib_colormap(state["colormap"])
    finite_c = cz[np.isfinite(cz)]
    low, high = state["color_range"] or (float(finite_c.min()), float(finite_c.max()))
    norm = Normalize(vmin=float(low), vmax=float(high))
    finite_h = hz[np.isfinite(hz)]
    z_low, z_high = float(finite_h.min()), float(finite_h.max())
    if state["secondary"] is not None:
        second = np.asarray(state["secondary"].z_values, dtype=float)
        finite_s = second[np.isfinite(second)]
        if finite_s.size:
            z_low, z_high = min(z_low, float(finite_s.min())), max(z_high, float(finite_s.max()))
    span = (z_high - z_low) or 1.0
    floor = z_low - 0.02 * span

    if state["geometry"] == "Waterfall" and state["waterfall"] is not None:
        from mpl_toolkits.mplot3d.art3d import Line3DCollection

        grid = build_display_grid(xs, ys, hz, cz, max_vertices=FLOOR_BUDGET)
        columns = grid.source_columns[0]
        x_plot = xs[columns] / x_text.scale
        for trace in select_waterfall_traces(hz.shape[0], 64):
            z = hz[trace, columns]
            c = cz[trace, columns]
            points = np.column_stack([x_plot, np.full_like(x_plot, ys[trace] / y_text.scale), z])
            segments = np.stack([points[:-1], points[1:]], axis=1)
            valid = np.isfinite(segments).all(axis=(1, 2))
            lines = Line3DCollection(segments[valid], colors=cmap(norm(c[:-1][valid])), linewidths=0.9,
                                     alpha=state["opacity"])
            ax.add_collection3d(lines)
    else:
        grid = build_display_grid(xs, ys, hz, cz, max_vertices=SURFACE_FACE_BUDGET)
        X, Y = np.meshgrid(grid.x_centers / x_text.scale, grid.y_centers / y_text.scale)
        Z = np.ma.masked_invalid(grid.heights)
        faces = cmap(norm(np.nan_to_num(grid.colors, nan=low)))
        faces[..., 3] = np.where(np.isfinite(grid.colors), state["opacity"], 0.0)
        ax.plot_surface(X, Y, Z, facecolors=faces, rstride=1, cstride=1, linewidth=0,
                        antialiased=False, shade=False, rasterized=True)
        if state["secondary"] is not None:
            second = np.asarray(state["secondary"].z_values, dtype=float)[grid.source_rows, grid.source_columns]
            tint = np.zeros(second.shape + (4,))
            tint[...] = (*PUBLICATION["dual_surface_b"], state["secondary_opacity"])
            ax.plot_surface(X, Y, np.ma.masked_invalid(second), facecolors=tint, rstride=1, cstride=1,
                            linewidth=0, antialiased=False, shade=False, rasterized=True)

    if state["projection"]:
        floor_grid = build_display_grid(xs, ys, cz, max_vertices=FLOOR_BUDGET)
        FX, FY = np.meshgrid(floor_grid.x_centers / x_text.scale, floor_grid.y_centers / y_text.scale)
        floor_fill = ax.contourf(FX, FY, np.clip(floor_grid.heights, low, high),
                                 levels=np.linspace(low, high, 200), cmap=cmap, norm=norm, zdir="z",
                                 offset=floor, alpha=state["projection_opacity"], antialiased=True)
        # Dense fills are embedded as 300-dpi raster in PDF/SVG; text, ticks and
        # axes stay vector (small files, journal practice).
        floor_fill.set_rasterized(True)
    xlim = (float(xs.min()) / x_text.scale, float(xs.max()) / x_text.scale)
    ylim = (float(ys.min()) / y_text.scale, float(ys.max()) / y_text.scale)
    reference = _reference_height(state["reference_mode"], state["reference_value"], z_low, z_high)
    if reference is not None:
        _plane(ax, xlim, ylim, reference, colors)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_zlim(floor, z_high)
    ax.set_xlabel(x_text.label, labelpad=10)
    ax.set_ylabel(y_text.label, labelpad=12)
    ax.set_zlabel(_value_label(height), labelpad=10)
    color_label = _value_label(color)
    _colorbar(fig, cmap, norm, color_label, colors)


def _render_points(fig, ax, state, colors):
    from matplotlib.colors import Normalize

    cloud = state["cloud"]
    labels = state["point_labels"] or (("X", None), ("Y", None), ("Z", None))
    coordinates = np.asarray(cloud.coordinates, dtype=float)
    values = np.asarray(cloud.color_values, dtype=float)
    # Qt's vertical axis carries Point Y; keep that vertical here too.
    texts = [si_axis(name, unit, coordinates[:, index]) for index, (name, unit) in enumerate(labels)]
    x = coordinates[:, 0] / texts[0].scale
    vertical = coordinates[:, 1] / texts[1].scale
    depth = coordinates[:, 2] / texts[2].scale
    cmap = _matplotlib_colormap(state["colormap"])
    low, high = state["color_range"] or (float(values.min()), float(values.max()))
    if low >= high:
        low, high = low - 0.5, high + 0.5
    norm = Normalize(vmin=low, vmax=high)
    v_low, v_high = float(vertical.min()), float(vertical.max())
    span = (v_high - v_low) or 1.0
    floor = v_low - 0.02 * span
    if state["projection"]:
        ax.scatter(x, depth, np.full_like(x, floor), c=cmap(norm(values)), s=3, alpha=0.35,
                   depthshade=False, linewidths=0, rasterized=True)
    if state["geometry"] == "Trajectory":
        ax.plot(x, depth, vertical, color=PUBLICATION["trajectory_line"], linewidth=0.6)
    ax.scatter(x, depth, vertical, c=cmap(norm(values)), s=7, depthshade=False, linewidths=0, rasterized=True)
    raw_low, raw_high = v_low * texts[1].scale, v_high * texts[1].scale
    reference = _reference_height(state["reference_mode"], state["reference_value"], raw_low, raw_high)
    if reference is not None:
        _plane(ax, (float(x.min()), float(x.max())), (float(depth.min()), float(depth.max())),
               reference / texts[1].scale, colors)
    ax.set_zlim(floor, v_high)
    ax.set_xlabel(texts[0].label, labelpad=10)
    ax.set_ylabel(texts[2].label, labelpad=12)
    ax.set_zlabel(texts[1].label, labelpad=10)
    _colorbar(fig, cmap, norm, str(state["point_color_label"]), colors)


def render_renderer_publication(renderer, *, image_format: str = "png", plot_colors=None,
                                dpi: int = EXPORT_DPI, size_inches=(8.0, 7.0)) -> bytes:
    """Render the renderer's current 3D scene as a journal-style figure."""
    from matplotlib import rc_context
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    if plot_colors is None:
        from app.theme import WHITE_PLOT
        plot_colors = WHITE_PLOT
    state = _state(renderer)
    if state["cloud"] is None and state["height"] is None:
        raise ValueError("No 3D scene is available for export.")
    image_format = image_format.lower()
    if image_format not in {"png", "pdf", "svg"}:
        raise ValueError(f"Unsupported publication format: {image_format}")
    style = {
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "STIXGeneral", "DejaVu Serif"],
        "mathtext.fontset": "stix",
        "axes.labelsize": 13,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
    }
    with rc_context(style):
        fig = Figure(figsize=size_inches, dpi=dpi, facecolor=plot_colors.background)
        FigureCanvasAgg(fig)
        ax = fig.add_axes([0.0, 0.0, 0.94, 0.95], projection="3d")
        ax.set_facecolor(plot_colors.background)
        if state["cloud"] is not None and state["geometry"] in {"Trajectory", "Scatter"}:
            _render_points(fig, ax, state, plot_colors)
        else:
            _render_surface_family(fig, ax, state, plot_colors)
        ax.set_box_aspect((1.0, 1.0, float(np.clip(0.72 * state["z_scale"], 0.2, 2.0))))
        _style_axes(ax, plot_colors)
        _view(ax, state["camera"])
        buffer = BytesIO()
        fig.savefig(buffer, format=image_format, dpi=dpi, bbox_inches="tight", pad_inches=0.15,
                    facecolor=plot_colors.background)
    return buffer.getvalue()
