"""Depth-sorted translucent surface on a Qt Matplotlib canvas.

Qt Data Visualization's surface path is retained for opaque high-density work.
This path blends individual triangles, never the entire widget, and samples
only display geometry. Transparency uses painter depth sorting (not OIT).
"""
from __future__ import annotations

from dataclasses import replace
import io
import numpy as np
from PySide6.QtCore import QEvent, Qt, QTimer, QThreadPool, Signal, QPoint
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.ticker import FuncFormatter, MaxNLocator
from matplotlib.colors import to_rgba
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from mpl_toolkits.mplot3d import proj3d

from app.palette import SURFACE_3D
from app.localization import get_localization_manager
from app.gui.plot_2d_widget import get_colormap, robust_color_limits
from app.visualization3d.data import prepare_surface_grid, normalize_axis_for_scene
from app.visualization3d.policy import RenderingPolicy, RenderingDecision
from app.visualization3d.state import CameraState3D
from app.visualization3d.diagnostics import publish_renderer_diagnostics, clear_renderer_diagnostics
from app.visualization3d.renderer import _LodSignals, _LodJob, _SurfaceInteractionFilter, format_pick_readout, _axis_title


def surface_triangles(grid):
    """Triangulate z[y,x], dropping faces incident to invalid height/color data."""
    axes = [normalize_axis_for_scene(a) for a in (grid.x_values, grid.y_values, grid.z_values)]
    x, y = np.meshgrid(axes[0].values, axes[1].values)
    vertices = np.stack((x, y, axes[2].values), axis=-1).reshape(-1, 3)
    rows, cols = grid.shape
    top_left = (np.arange(rows - 1)[:, None] * cols + np.arange(cols - 1)).ravel()
    faces = np.concatenate((np.stack((top_left, top_left + 1, top_left + cols), axis=1),
                            np.stack((top_left + 1, top_left + cols + 1, top_left + cols), axis=1)))
    values = grid.color_values.ravel()
    valid = np.isfinite(vertices[faces]).all(axis=(1, 2)) & np.isfinite(values[faces]).all(axis=1)
    faces = faces[valid]
    return vertices, faces, values[faces].mean(axis=1), axes


class TransparentSurfaceRenderer(QWidget):
    surface_ready = Signal(object)
    surface_failed = Signal(str)
    color_range_changed = Signal(float, float)
    point_render_stage = Signal(str)
    export_context_requested = Signal(QPoint)
    share_drag_requested = Signal()
    MAX_VERTICES = 6000
    FULL_LIMIT = 12000

    def __init__(self, parent=None, localizer=None):
        super().__init__(parent)
        self.localizer = localizer or get_localization_manager()
        self.figure = Figure(figsize=(8, 6), facecolor=SURFACE_3D["transparent_fallback"]["background"])
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.container = self.canvas
        self.canvas.setMinimumSize(240, 180)
        self.canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.ax = self.figure.add_axes([0.01, 0.05, 0.84, 0.91], projection="3d")
        self.ax.disable_mouse_rotation()
        self.color_ax = self.figure.add_axes([0.90, 0.25, 0.018, 0.50])
        self.pick_label = QLabel()
        self.pick_label.setWordWrap(True)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.pick_label)
        layout.addWidget(self.status_label)
        self._grid = None
        self._point_cloud = None
        self._source_height_grid = self._source_color_grid = None
        self._color_range = None
        self._colormap_name = "LabLog BWR"
        self._policy = "Auto"
        self._rendering_decision = None
        self._transparency = 35
        self._z_scale = 1.0
        self._theme_colors = None
        self._camera = CameraState3D(target=(0.5, 0.5, 0.5))
        self._collection = None
        self._generation = 0
        self._preparing = False
        self._mesh_rebuild_count = 0
        self._camera_update_count = 0
        self._interaction_active = False
        self._vertices = self._faces = self._values = self._axes = None
        self._drag_grid = None
        self._signals = _LodSignals()
        self._signals.ready.connect(self._on_ready)
        self._pool = QThreadPool.globalInstance()
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(180)
        self._idle.timeout.connect(self._restore_detail)
        self._frame = QTimer(self)
        self._frame.setSingleShot(True)
        self._frame.setInterval(30)
        self._frame.timeout.connect(self._draw_camera)
        self._controller = _SurfaceInteractionFilter(self)
        self.canvas.installEventFilter(self)
        self._left_press = None
        self._left_dragged = False
        self.retranslate()
        self._style_axes()
        rid = id(self)
        self.destroyed.connect(lambda *_: clear_renderer_diagnostics(rid))

    surface_grid = property(lambda self: self._grid)
    color_range = property(lambda self: self._color_range)
    rendering_decision = property(lambda self: self._rendering_decision)
    is_preparing = property(lambda self: self._preparing)
    mesh_rebuild_count = property(lambda self: self._mesh_rebuild_count)
    camera_update_count = property(lambda self: self._camera_update_count)

    def _budget(self, grid, policy):
        requested = RenderingPolicy(policy)
        points = int(np.size(grid.z_values))
        if requested == RenderingPolicy.FULL_RESOLUTION and points > self.FULL_LIMIT:
            raise ValueError(self.localizer.text("viewer.transparent_full_limit"))
        budget = self.FULL_LIMIT if requested == RenderingPolicy.FULL_RESOLUTION else (
            2000 if requested == RenderingPolicy.PERFORMANCE else self.MAX_VERTICES)
        self._rendering_decision = RenderingDecision(
            requested, requested if points <= budget else RenderingPolicy.ADAPTIVE_LOD,
            points, budget)
        return budget

    def set_grid(self, grid, *, color_grid=None, colormap="LabLog BWR", color_range=None,
                 rendering_policy="Auto", shared_height_limits=None):
        budget = self._budget(grid, rendering_policy)
        prepared = prepare_surface_grid(grid, color_grid=color_grid, max_vertices=budget)
        self._generation += 1
        self._preparing = False
        self._configure_source(grid, color_grid, colormap, color_range, rendering_policy)
        self._accept_grid(prepared)
        return prepared

    def _configure_source(self, grid, color, cmap, limits, policy):
        self._source_height_grid = grid
        self._source_color_grid = color if color is not None else grid
        self._colormap_name = cmap
        self._color_range = limits or robust_color_limits(self._source_color_grid.z_values)
        self._policy = policy

    def set_grid_async(self, grid, *, color_grid=None, colormap="LabLog BWR", color_range=None,
                       rendering_policy="Auto", shared_height_limits=None):
        budget = self._budget(grid, rendering_policy)
        self._generation += 1
        self._preparing = True
        self._configure_source(grid, color_grid, colormap, color_range, rendering_policy)
        self._pool.start(_LodJob(self._signals, self._generation, None, grid, color_grid, budget))

    def _on_ready(self, generation, _key, _budget, result):
        if generation != self._generation:
            return
        self._preparing = False
        if isinstance(result, Exception):
            self.surface_failed.emit(str(result))
            return
        self._accept_grid(result.grid)
        self.surface_ready.emit(self._grid)

    def _accept_grid(self, grid):
        self._idle.stop()
        self._interaction_active = False
        self._grid = grid
        # Reduce only the already bounded display grid for camera motion.
        from types import SimpleNamespace
        source = SimpleNamespace(**{name: getattr(grid, name) for name in (
            "x_values", "y_values", "z_values", "x_name", "y_name", "z_name", "x_unit", "y_unit", "z_unit")},
            transform=grid.z_transform)
        color = SimpleNamespace(**vars(source))
        color.z_values = grid.color_values
        color.z_name, color.z_unit, color.transform = grid.color_name, grid.color_unit, grid.color_transform
        self._drag_grid = prepare_surface_grid(source, color_grid=color, max_vertices=1400)
        self._build_surface(grid)
        self.retranslate()
        self._publish()

    def _style_axes(self):
        background = self._theme_colors.plot_background if self._theme_colors else SURFACE_3D["transparent_fallback"]["background"]
        text = self._theme_colors.text if self._theme_colors else SURFACE_3D["transparent_fallback"]["text"]
        secondary = self._theme_colors.secondary if self._theme_colors else SURFACE_3D["transparent_fallback"]["secondary"]
        border = self._theme_colors.border if self._theme_colors else SURFACE_3D["transparent_fallback"]["border"]
        pane = self._theme_colors.plot_panel if self._theme_colors else SURFACE_3D["transparent_fallback"]["pane"]
        self.figure.set_facecolor(background)
        self.ax.set_facecolor(background)
        self.color_ax.set_facecolor(background)
        for axis in (self.ax.xaxis, self.ax.yaxis, self.ax.zaxis):
            pane_rgba = (*to_rgba(pane)[:3], 1.0)
            axis.set_pane_color(pane_rgba)
            axis.line.set_color(border)
            axis.label.set_color(text)
            axis.set_major_locator(MaxNLocator(4))
            axis._axinfo["grid"].update(color=border, linewidth=0.6)
        self.ax.tick_params(colors=secondary, labelsize=8, pad=0)

    def apply_scientific_plot_appearance(self, colors):
        """Restyle axes and colorbar without rebuilding surface geometry."""
        self._theme_colors = colors
        self.figure.set_facecolor(colors.plot_background)
        self.ax.set_facecolor(colors.plot_background)
        self.color_ax.set_facecolor(colors.plot_background)
        pane_rgba = (*to_rgba(colors.plot_panel)[:3], 1.0)
        for axis in (self.ax.xaxis, self.ax.yaxis, self.ax.zaxis):
            axis.set_pane_color(pane_rgba)
            axis.line.set_color(colors.border)
            axis.label.set_color(colors.text)
            axis._axinfo["grid"].update(color=colors.border, linewidth=0.6)
        self.ax.tick_params(colors=colors.secondary, labelsize=8, pad=0)
        text = colors.text
        self.color_ax.tick_params(colors=colors.secondary, labelsize=8)
        self.color_ax.yaxis.label.set_color(text)
        for label in (*self.color_ax.get_xticklabels(), *self.color_ax.get_yticklabels()):
            label.set_color(colors.secondary)
        self.pick_label.setStyleSheet(
            f"QLabel {{ color: {text}; background-color: {colors.panel}; "
            f"border: 1px solid {colors.border}; padding: 3px 6px; }}"
        )
        self.canvas.draw_idle()

    def _build_surface(self, grid):
        if self._collection is not None:
            self._collection.remove()
        self._vertices, self._faces, self._values, self._axes = surface_triangles(grid)
        vertices = self._vertices.copy()
        vertices[:, 2] = 0.5 + (vertices[:, 2] - 0.5) * self._z_scale
        self._collection = Poly3DCollection(vertices[self._faces], linewidths=0,
                                            edgecolors="none", antialiased=False, zsort="average")
        self.ax.add_collection3d(self._collection)
        for axis, mapping in zip((self.ax.xaxis, self.ax.yaxis, self.ax.zaxis), self._axes):
            scale = self._z_scale if axis is self.ax.zaxis else 1.0
            axis.set_major_formatter(FuncFormatter(
                lambda value, _pos, m=mapping, s=scale: f"{m.physical_value(0.5 + (value - 0.5) / s):.5g}"))
        text_color = self._theme_colors.text if self._theme_colors else SURFACE_3D["transparent_fallback"]["text"]
        self.ax.set_xlabel(_axis_title(grid.x_name, grid.x_unit, "X"), fontsize=9, labelpad=6, color=text_color)
        self.ax.set_ylabel(_axis_title(grid.y_name, grid.y_unit, "Y"), fontsize=9, labelpad=6, color=text_color)
        self.ax.set_zlabel(_axis_title(grid.z_name, grid.z_unit, "Z"), fontsize=9, labelpad=7, color=text_color)
        self._mesh_rebuild_count += 1
        self._apply_colors()
        self._draw_camera()

    def _apply_colors(self):
        if self._collection is None:
            return
        low, high = self._color_range
        rgba = np.asarray(get_colormap(self._colormap_name).map(
            np.clip((self._values - low) / (high - low), 0, 1), mode="float"))
        rgba[:, 3] = 1.0 - self._transparency / 100.0
        self._collection.set_facecolor(rgba)
        from matplotlib.colors import ListedColormap, Normalize
        from matplotlib.colorbar import ColorbarBase
        cmap = ListedColormap(get_colormap(self._colormap_name).map(np.linspace(0, 1, 256), mode="float"))
        self.color_ax.clear()
        bar = ColorbarBase(self.color_ax, cmap=cmap, norm=Normalize(low, high))
        theme = self._theme_colors
        bar.ax.tick_params(colors=theme.secondary if theme else SURFACE_3D["transparent_fallback"]["secondary"], labelsize=8)
        bar.outline.set_edgecolor(theme.border if theme else SURFACE_3D["transparent_fallback"]["border"])
        if self._grid is not None:
            bar.set_label(f"{self._grid.color_name} [{self._grid.color_unit or self._grid.color_transform}]",
                          color=theme.text if theme else SURFACE_3D["transparent_fallback"]["text"], fontsize=8)

    def _draw_camera(self):
        camera = self._camera
        self.ax.view_init(elev=camera.y_rotation, azim=-90 - camera.x_rotation)
        self.ax.set_proj_type("ortho" if camera.projection == "orthographic" else "persp")
        self.ax.set_box_aspect((1, 1, 0.72))
        radius = 0.56 * 100 / camera.zoom_level
        # CameraState target follows Qt coordinates: X, height, scientific Y.
        for setter, center in zip((self.ax.set_xlim, self.ax.set_ylim, self.ax.set_zlim),
                                  (camera.target[0], camera.target[2], camera.target[1])):
            setter(center - radius, center + radius)
        self.canvas.draw_idle()

    def set_transparency(self, value):
        value = int(value)
        if not 0 <= value <= 90:
            raise ValueError("Transparency must be between 0 and 90 percent.")
        self._transparency = value
        self._apply_colors()
        self.canvas.draw_idle()
        self._publish()

    def set_opacity(self, value):
        """Keep the shared Viewer control expressed as opacity, not alpha."""
        opacity = float(value)
        if not np.isfinite(opacity) or not 0.0 <= opacity <= 1.0:
            raise ValueError("Surface opacity must be between 0 and 1.")
        self.set_transparency(min(90, int(round((1.0 - opacity) * 100))))

    def set_bottom_projection(self, _enabled):
        return None

    def set_reference_plane(self, _mode, _value=0.0):
        return None

    def set_horizontal_colorbar_visible(self, _visible):
        return None

    def clear_secondary_grid(self):
        return None

    def set_color_settings(self, *, colormap=None, color_range=None):
        if colormap is not None:
            self._colormap_name = colormap
        if self._source_color_grid is not None:
            limits = color_range or robust_color_limits(self._source_color_grid.z_values)
            if not np.isfinite(limits).all() or limits[0] >= limits[1]:
                raise ValueError("Color minimum must be less than maximum.")
            self._color_range = limits
        self._apply_colors()
        self.canvas.draw_idle()

    def set_color_grid(self, grid):
        return self.set_grid(self._source_height_grid, color_grid=grid, colormap=self._colormap_name,
                             color_range=self._color_range, rendering_policy=self._policy)

    def set_rendering_policy(self, policy):
        if self._source_height_grid is not None:
            return self.set_grid(self._source_height_grid, color_grid=self._source_color_grid,
                                 colormap=self._colormap_name, color_range=self._color_range, rendering_policy=policy)

    def set_z_scale(self, scale):
        if not np.isfinite(scale) or not 0.1 <= scale <= 10:
            raise ValueError("Z Scale must be between 0.1 and 10.")
        if scale != self._z_scale:
            self._z_scale = scale
            if self._grid is not None:
                self._build_surface(self._grid)

    def set_projection(self, projection):
        if projection not in ("perspective", "orthographic"):
            raise ValueError("Invalid projection")
        self._camera = replace(self._camera, projection=projection)
        self._draw_camera()

    def set_geometry_type(self, geometry):
        if str(geometry) != "Transparent Surface":
            raise ValueError("The depth-sorted renderer supports Transparent Surface only.")

    def set_secondary_opacity(self, _value):
        return None

    def camera_state(self):
        return self._camera

    def apply_camera_state(self, state):
        self._camera = state
        self._draw_camera()

    def set_camera_preset(self, name):
        rotations = {"Top": (0, 90), "Front": (0, 0), "Side": (90, 0), "Isometric": (35, 30)}
        x, y = rotations[name]
        self._camera = replace(self._camera, x_rotation=x, y_rotation=y)
        self._draw_camera()

    def reset_view(self):
        self._camera = CameraState3D(target=(0.5, 0.5, 0.5), projection=self._camera.projection)
        self._draw_camera()

    view_all = reset_view

    def set_rendering_policy(self, policy):
        if self._source_height_grid is not None:
            return self.set_grid(
                self._source_height_grid, color_grid=self._source_color_grid,
                colormap=self._colormap_name, color_range=self._color_range,
                rendering_policy=policy,
            )

    def _rotate_camera(self, dx, dy):
        self._camera = replace(self._camera, x_rotation=(self._camera.x_rotation + dx * 0.5) % 360,
                               y_rotation=min(90, max(-90, self._camera.y_rotation - dy * 0.5)))
        self._camera_update_count += 1
        if not self._frame.isActive():
            self._frame.start()

    def _pan_camera(self, dx, dy):
        azim, elev = np.deg2rad([-90 - self._camera.x_rotation, self._camera.y_rotation])
        right = np.array([-np.sin(azim), np.cos(azim), 0.0])
        up = np.array([-np.cos(azim) * np.sin(elev), -np.sin(azim) * np.sin(elev), np.cos(elev)])
        scale = 1.5 * 100 / self._camera.zoom_level / max(1, min(self.canvas.width(), self.canvas.height()))
        delta = (-dx * right + dy * up) * scale
        target = np.asarray(self._camera.target) + delta[[0, 2, 1]]
        self._camera = replace(self._camera, target=tuple(target))
        self._camera_update_count += 1
        if not self._frame.isActive():
            self._frame.start()

    def _interaction_start(self):
        self._idle.stop()
        if not self._interaction_active and self._grid is not None:
            self._interaction_active = True
            if self._grid.vertex_count > 1400:
                self._build_surface(self._drag_grid)

    def _interaction_end(self):
        self._idle.start()

    def _restore_detail(self):
        if self._interaction_active:
            self._interaction_active = False
            if self._grid is not None:
                self._build_surface(self._grid)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            if self._controller._active_button is None:
                self._left_press = event.position()
                self._left_dragged = False
        if kind == QEvent.Type.MouseMove and self._left_press is not None:
            if (event.position() - self._left_press).manhattanLength() > 8:
                self._left_dragged = True
        consumed = self._controller.eventFilter(watched, event)
        if kind in (QEvent.Type.FocusOut, QEvent.Type.WindowDeactivate):
            self._left_press = None
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if self._left_press is not None and not self._left_dragged and not consumed:
                self._pick(event.position())
            self._left_press = None
        if kind == QEvent.Type.Wheel:
            zoom = min(500, max(10, self._camera.zoom_level * 1.15 ** (event.angleDelta().y() / 120)))
            self._camera = replace(self._camera, zoom_level=zoom)
            self._draw_camera()
            event.accept()
            return True
        # Matplotlib navigation stays disabled; no competing mouse bindings.
        return consumed or kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease)

    def contextMenuEvent(self, event):  # noqa: N802 - Qt API spelling
        self.export_context_requested.emit(event.globalPos())
        event.accept()

    def _pick(self, point):
        if self._grid is None:
            return
        self._restore_detail()
        vertices = self._vertices.copy()
        vertices[:, 2] = 0.5 + (vertices[:, 2] - 0.5) * self._z_scale
        x, y, depth = proj3d.proj_transform(*vertices.T, self.ax.get_proj())
        pixels = self.ax.transData.transform(np.column_stack((x, y)))
        ratio = self.canvas.devicePixelRatioF()
        position = np.array([point.x() * ratio, (self.canvas.height() - point.y()) * ratio])
        distance = np.sum((pixels - position) ** 2, axis=1)
        valid = np.isfinite(distance) & np.isfinite(vertices).all(axis=1)
        nearby = np.flatnonzero(valid & (distance < (14 * ratio) ** 2))
        if not len(nearby):
            return
        nearest = nearby[np.argmin(distance[nearby])]
        row, col = np.unravel_index(nearest, self._grid.shape)
        self.pick_label.setText(format_pick_readout(self._grid, row, col))

    def retranslate(self):
        self.pick_label.setText(self.localizer.text("viewer.surface_pick_hint"))
        if self._grid is not None:
            self.status_label.setText(self.localizer.text("viewer.transparent_status",
                vertices=self._grid.vertex_count, source=int(np.prod(self._grid.source_shape))))

    def clear(self):
        self._generation += 1
        self._preparing = False
        self._idle.stop()
        self._frame.stop()
        if self._collection is not None:
            self._collection.remove()
        self._collection = None
        self._grid = self._source_height_grid = self._source_color_grid = None
        self._drag_grid = None
        self._interaction_active = False
        self.color_ax.clear()
        self.status_label.clear()
        self.retranslate()
        self.canvas.draw_idle()

    def render_to_image(self, size, *, wait_ms=0):
        if size.width() <= 0 or size.height() <= 0:
            raise ValueError("Render size must be positive.")
        original = self.figure.get_size_inches().copy()
        try:
            self.figure.set_size_inches(size.width() / 100, size.height() / 100)
            buffer = io.BytesIO()
            self.figure.savefig(buffer, format="png", dpi=100, facecolor=self.figure.get_facecolor())
            return QImage.fromData(buffer.getvalue())
        finally:
            self.figure.set_size_inches(original)
            self.canvas.draw_idle()

    def render_plot_image(self, *, scale=3):
        if not 1 <= int(scale) <= 4:
            raise ValueError("3D export scale must be between 1 and 4.")
        if self._grid is None or self._collection is None:
            raise ValueError("Wait for the transparent Surface to finish preparing before export.")
        return self.render_to_image(self.canvas.size() * int(scale))

    def diagnostics(self):
        return {"renderer_id": id(self), "backend": "Matplotlib depth-sorted transparency",
                "transparency_percent": self._transparency,
                "vertex_count": self._grid.vertex_count if self._grid else 0,
                "source_dimensions": list(self._grid.source_shape) if self._grid else None,
                "mesh_rebuild_count": self._mesh_rebuild_count, "camera_update_count": self._camera_update_count,
                "fps": None, "display_vertex_limit": self.MAX_VERTICES}

    def _publish(self):
        publish_renderer_diagnostics(self.diagnostics())

    def closeEvent(self, event):
        self.clear()
        clear_renderer_diagnostics(id(self))
        super().closeEvent(event)
