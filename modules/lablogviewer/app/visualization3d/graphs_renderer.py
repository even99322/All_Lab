"""Qt Graphs 3D renderer for the surface family (GPU via Qt RHI).

Surface, Transparent Surface, Dual Surface and Waterfall render here. Grids are
uploaded in one call (``resetArrayNp``) from an evenly spaced, peak-preserving
display grid (see ``display_grid``); Qt Graphs blends transparency with
order-independent techniques, so opacity no longer alters scientific colors.
Trajectory and Scatter stay on the Qt Data Visualization renderer.

Scene conventions match the previous renderer: scientific X on Qt X,
scientific Y on Qt Z, the height on Qt Y, ``z[y, x]`` preserved. Every display
cell maps back to one real source sample for readouts.
"""

from __future__ import annotations

import os
import time

import numpy as np
from PySide6.QtCore import QEvent, QEventLoop, QObject, QPoint, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QVector3D
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from app.gui.plot_2d_widget import get_colormap, robust_color_limits
from app.localization import LocalizationManager, get_localization_manager
from app.palette import PLOT_WHITE, SURFACE_3D
from app.visualization3d.data import SurfaceGrid
from app.visualization3d.diagnostics import clear_renderer_diagnostics, publish_renderer_diagnostics
from app.visualization3d.display_grid import DisplayGrid, build_display_grid, select_waterfall_traces
from app.visualization3d.policy import RenderingDecision, RenderingPolicy
from app.visualization3d.renderer import InteractiveColorbar, _ColorRamp, _axis_title, _transform_label
from app.visualization3d.state import CameraState3D


PROFILES = {
    # idle vertex budget, interaction vertex budget
    "balanced": (1_000_000, 150_000),
    "quality": (4_200_000, 300_000),
    "economy": (250_000, 50_000),
}
FULL_RESOLUTION_LIMIT = 4_200_000
ADAPTIVE_LIMIT = 250_000
PERFORMANCE_LIMIT = 50_000
ASYNC_SOURCE_POINTS = 1_000_000
WATERFALL_TRACE_LIMIT = 64
WATERFALL_RIBBON_DEPTH = 0.75      # fraction of the trace spacing (gap keeps traces distinct)
PROJECTION_LIFT = 0.004            # fraction of the height span, avoids floor z-fighting
IDLE_RESTORE_MS = 300
DEFAULT_ZOOM = 150.0               # Qt Graphs frames the box smaller than Data Visualization did
AXIS_LABEL_SIZE = 2.0
SURFACE_GEOMETRIES = {"Surface", "Transparent Surface", "Dual Surface", "Waterfall"}


def graphs_available() -> bool:
    """Qt Graphs needs a real RHI surface; offscreen/minimal platforms fall back."""
    app = QApplication.instance()
    platform = (app.platformName() if app is not None else os.environ.get("QT_QPA_PLATFORM", "")).lower()
    if platform in {"offscreen", "minimal"} or os.environ.get("LABLOGVIEWER_DISABLE_QTGRAPHS") == "1":
        return False
    try:
        import PySide6.QtGraphsWidgets  # noqa: F401
        import PySide6.QtQuickWidgets  # noqa: F401
    except ImportError:
        return False
    return True


def resolve_graphs_policy(rows: int, columns: int, requested: RenderingPolicy | str,
                          profile: str = "balanced") -> RenderingDecision:
    """Idle display budget for Qt Graphs; source data is never changed."""
    policy = RenderingPolicy(requested)
    points = int(rows) * int(columns)
    idle, _interaction = PROFILES.get(profile, PROFILES["balanced"])
    if policy is RenderingPolicy.AUTO:
        budget = None if points <= idle else idle
        effective = policy if budget is None else RenderingPolicy.ADAPTIVE_LOD
        warning = None if budget is None else f"Large grid: peak-preserving {idle:,}-vertex display."
        return RenderingDecision(policy, effective, points, budget, warning)
    if policy is RenderingPolicy.FULL_RESOLUTION:
        budget = None if points <= FULL_RESOLUTION_LIMIT else FULL_RESOLUTION_LIMIT
        warning = (f"Full Resolution is capped at {FULL_RESOLUTION_LIMIT:,} display vertices."
                   if budget is not None else None)
        return RenderingDecision(policy, policy, points, budget, warning)
    limit = ADAPTIVE_LIMIT if policy is RenderingPolicy.ADAPTIVE_LOD else PERFORMANCE_LIMIT
    return RenderingDecision(policy, policy, points, None if points <= limit else limit)


def colormap_rgba(values: np.ndarray, colormap: str, limits: tuple[float, float],
                  opacity: float = 1.0) -> np.ndarray:
    """Scientific colors with straight (non-premultiplied) alpha; NaN is clear."""
    low, high = (float(value) for value in limits)
    values = np.asarray(values, dtype=np.float64)
    finite = np.isfinite(values)
    normalized = np.zeros(values.shape, dtype=np.float64)
    normalized[finite] = np.clip((values[finite] - low) / (high - low), 0.0, 1.0)
    rgba = np.ascontiguousarray(get_colormap(colormap).map(normalized, mode="byte"), dtype=np.uint8)
    rgba[..., 3] = np.uint8(round(255 * float(opacity)))
    rgba[~finite, 3] = 0
    return rgba


def reset_surface(series, x0: float, dx: float, z0: float, dz: float, data: np.ndarray) -> None:
    """``resetArrayNp`` that keeps the uploaded buffer alive.

    PySide6 6.11's ``QSurfaceDataProxy.resetArrayNp`` does not own the numpy
    buffer; releasing it after the call crashed the process during a later
    render or at shutdown (verified natively). The last two buffers per series
    stay referenced until they are replaced.
    """
    data = np.ascontiguousarray(data, dtype=np.float32)
    held = getattr(series, "_lablog_buffers", None)
    if held is None:
        held = []
        series._lablog_buffers = held
    held.append(data)
    del held[:-2]
    series.dataProxy().resetArrayNp(float(x0), float(dx), float(z0), float(dz), data)


def _rgba_image(rgba: np.ndarray) -> QImage:
    rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
    height, width = rgba.shape[:2]
    return QImage(rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()


class _GridSignals(QObject):
    ready = Signal(int, object)


class _GridJob(QRunnable):
    def __init__(self, signals: _GridSignals, generation: int, args: tuple, budget: int | None):
        super().__init__()
        self.signals, self.generation, self.args, self.budget = signals, generation, args, budget

    def run(self) -> None:
        try:
            started = time.perf_counter()
            result = (build_display_grid(*self.args, max_vertices=self.budget),
                      (time.perf_counter() - started) * 1000)
        except Exception as error:  # reported on the GUI thread
            result = error
        self.signals.ready.emit(self.generation, result)


class _HeightMap:
    """Reversible map between physical height and Qt's vertical axis."""

    def __init__(self, low: float, high: float, z_scale: float):
        if not np.isfinite([low, high]).all():
            raise ValueError("Height limits must be finite.")
        if low == high:
            low, high = low - 0.5, high + 0.5
        self.low, self.high, self.scale = float(low), float(high), float(z_scale)

    def to_scene(self, values):
        normalized = (np.asarray(values, dtype=np.float64) - self.low) / (self.high - self.low)
        return 0.5 + (normalized - 0.5) * self.scale

    @property
    def scene_range(self) -> tuple[float, float]:
        return 0.5 - 0.5 * self.scale, 0.5 + 0.5 * self.scale

    def physical(self, scene_value: float) -> float:
        normalized = (float(scene_value) - 0.5) / self.scale + 0.5
        return self.low + normalized * (self.high - self.low)


class _Interaction(QObject):
    """Left-drag rotate, middle-drag pan, click pick, right-click export menu."""

    DRAG_THRESHOLD = 6.0

    def __init__(self, renderer: "GraphsSurfaceRenderer"):
        super().__init__(renderer)
        self.renderer = renderer
        self._button = None
        self._press = None
        self._last = None
        self._dragging = False

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API spelling
        kind = event.type()
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.RightButton:
            self.renderer.export_context_requested.emit(event.globalPosition().toPoint())
            return True
        if kind == QEvent.Type.MouseButtonPress and event.button() in (
                Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton) and self._button is None:
            self._button = event.button()
            self._press = self._last = event.position()
            self._dragging = False
            return True
        if kind == QEvent.Type.MouseMove and self._button is not None:
            current = event.position()
            if not self._dragging:
                if (current - self._press).manhattanLength() < self.DRAG_THRESHOLD:
                    return True
                if self._button == Qt.MouseButton.LeftButton and self.renderer._share_mode:
                    self._reset()
                    self.renderer.share_drag_requested.emit()
                    return True
                self._dragging = True
                watched.setCursor(Qt.CursorShape.SizeAllCursor if self._button == Qt.MouseButton.LeftButton
                                  else Qt.CursorShape.ClosedHandCursor)
                self.renderer._interaction_start()
            delta = current - self._last
            self._last = current
            if self._button == Qt.MouseButton.LeftButton:
                self.renderer._rotate_camera(delta.x(), delta.y())
            else:
                self.renderer._pan_camera(delta.x(), delta.y())
            return True
        if kind == QEvent.Type.MouseButtonRelease and event.button() == self._button:
            if self._dragging:
                watched.unsetCursor()
                self.renderer._interaction_end()
            elif self._button == Qt.MouseButton.LeftButton:
                self.renderer._pick(event.position().toPoint())
            self._reset()
            return True
        if kind == QEvent.Type.Wheel:
            # Trackpad two-finger scroll (it comes in phases): rotate, or pan with Shift.
            if event.phase() != Qt.ScrollPhase.NoScrollPhase and not event.pixelDelta().isNull():
                delta = event.pixelDelta()
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.renderer._pan_camera(delta.x(), delta.y())
                else:
                    self.renderer._rotate_camera(-delta.x(), -delta.y())
                self.renderer._camera_update_count += 1
                return True
            # Mouse-wheel zoom is handled by Qt Graphs; it needs no LOD swap.
            self.renderer._camera_update_count += 1
        if kind == QEvent.Type.NativeGesture and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            graph = self.renderer.graph                  # trackpad pinch
            level = graph.cameraZoomLevel() * (1.0 + float(event.value()))
            graph.setCameraZoomLevel(max(graph.minCameraZoomLevel() if hasattr(graph, "minCameraZoomLevel") else 10.0,
                                         min(level, graph.maxCameraZoomLevel() if hasattr(graph, "maxCameraZoomLevel") else 500.0)))
            self.renderer._camera_update_count += 1
            return True
        return False

    def _reset(self) -> None:
        self._button = self._press = self._last = None
        self._dragging = False


class GraphsSurfaceRenderer(QWidget):
    """Qt Graphs replacement for ``SurfaceRenderer``'s surface-family API."""

    surface_ready = Signal(object)
    surface_failed = Signal(str)
    color_range_changed = Signal(float, float)
    point_render_stage = Signal(str)
    export_context_requested = Signal(QPoint)
    share_drag_requested = Signal()

    backend = "Qt Graphs 3D (RHI)"

    def __init__(self, parent=None, localizer: LocalizationManager | None = None):
        super().__init__(parent)
        from PySide6.QtGraphs import (
            QGraphsTheme, QSurface3DSeries, QSurfaceDataProxy, QtGraphs3D, QValue3DAxis, QValue3DAxisFormatter,
        )
        from PySide6.QtGraphsWidgets import Q3DSurfaceWidgetItem
        from PySide6.QtQuickWidgets import QQuickWidget

        self.localizer = localizer or get_localization_manager()
        self._QSurface3DSeries = QSurface3DSeries
        self._QSurfaceDataProxy = QSurfaceDataProxy
        self._QtGraphs3D = QtGraphs3D
        self._QGraphsTheme = QGraphsTheme

        class PhysicalFormatter(QValue3DAxisFormatter):
            # Qt keeps formatter copies made by createNewInstance(); a Python
            # object nobody references is collected and Qt then dereferences
            # it in QQuickGraphsItem::updateLabels() (native crash, verified).
            _alive: list = []

            def __init__(self, to_physical=None):
                super().__init__()
                self.to_physical = to_physical or (lambda value: value)

            def createNewInstance(self):  # noqa: N802 - Qt API spelling
                instance = PhysicalFormatter(self.to_physical)
                PhysicalFormatter._alive.append(instance)
                return instance

            def populateCopy(self, copy):  # noqa: N802 - Qt API spelling
                super().populateCopy(copy)
                copy.to_physical = self.to_physical

            def stringForValue(self, value, _format):  # noqa: N802 - Qt API spelling
                return f"{self.to_physical(float(value)):.6g}"

        self._Formatter = PhysicalFormatter
        # Qt owns the graph item (parent = this renderer) and deletes it before
        # the QQuickWidget created after it. A Python-owned item was destroyed
        # during interpreter shutdown after its widget: native crash on quit.
        self.graph = Q3DSurfaceWidgetItem(self)
        self.view = QQuickWidget(self)
        self.view.setObjectName("surface3DViewport")
        self.view.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.view.setMinimumSize(240, 180)
        self.view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.graph.setWidget(self.view)
        self.active_graph = self.graph
        self.container = self.view
        self.graph.setMeasureFps(True)
        self.graph.setAspectRatio(0.72)
        self.graph.setHorizontalAspectRatio(1.0)
        self.graph.setShadowQuality(QtGraphs3D.ShadowQuality.None_)
        self.graph.setSelectionMode(QtGraphs3D.SelectionFlag.Item)
        self.graph.unsetDefaultDragHandler()
        self.graph.unsetDefaultTapHandler()
        self._axes = []
        self._formatters = []
        for setter in (self.graph.setAxisX, self.graph.setAxisY, self.graph.setAxisZ):
            axis = QValue3DAxis()
            formatter = PhysicalFormatter()
            axis.setFormatter(formatter)
            axis.setTitleVisible(True)
            setter(axis)
            self._axes.append(axis)
            self._formatters.append(formatter)

        def surface_series():
            series = QSurface3DSeries(QSurfaceDataProxy())
            series.setDrawMode(QSurface3DSeries.DrawFlag.DrawSurface)
            series.setShading(QSurface3DSeries.Shading.Smooth)
            series.setItemLabelVisible(False)
            series.setVisible(False)
            self.graph.addSeries(series)
            return series

        self.series = surface_series()
        self.series.selectedPointChanged.connect(self._on_selected_point)
        self.secondary_series = surface_series()
        self.projection_series = surface_series()
        self.projection_series.setShading(QSurface3DSeries.Shading.Flat)
        self.reference_series = surface_series()
        self.reference_series.setShading(QSurface3DSeries.Shading.Flat)
        self._ribbons: list = []
        self._make_series = surface_series

        self.color_title = QLabel(); self.color_title.setWordWrap(True)
        self.color_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.color_high = QLabel("—"); self.color_high.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.color_low = QLabel("—"); self.color_low.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.color_ramp = _ColorRamp()
        self.horizontal_colorbar = InteractiveColorbar()
        self.horizontal_colorbar.hide()
        self.horizontal_colorbar.levels_changed.connect(self.color_range_changed)
        colorbar = QWidget(); colorbar.setObjectName("surfaceColorbar")
        colorbar_layout = QVBoxLayout(colorbar)
        colorbar_layout.setContentsMargins(2, 2, 2, 2)
        for widget, stretch in ((self.color_title, 0), (self.color_high, 0), (self.color_ramp, 1), (self.color_low, 0)):
            colorbar_layout.addWidget(widget, stretch)
        colorbar.setMaximumWidth(120)
        self.colorbar_widget = colorbar
        self.status_label = QLabel(); self.status_label.setWordWrap(True)
        self.reference_label = QLabel(); self.reference_label.setWordWrap(True)
        self.pick_label = QLabel(); self.pick_label.setWordWrap(True); self.pick_label.setMaximumHeight(48)
        self.pick_label.setText(self.localizer.text("viewer.surface_pick_hint"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.horizontal_colorbar)
        row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.view, 1); row.addWidget(colorbar)
        layout.addLayout(row, 1)
        for label in (self.reference_label, self.pick_label, self.status_label):
            layout.addWidget(label)

        # scientific state
        self._source_height = None
        self._source_color = None
        self._display: DisplayGrid | None = None
        self._grid: SurfaceGrid | None = None
        self._height_map: _HeightMap | None = None
        self._color_follows_height = True
        self._geometry_type = "Surface"
        self._z_scale = 1.0
        self._opacity = 1.0
        self._colormap_name = "LabLog BWR"
        self._color_range: tuple[float, float] | None = None
        self._shared_height_limits: tuple[float, float] | None = None
        self._secondary_source = None
        self._secondary_opacity = 0.55
        self._projection_enabled = False
        self._projection_opacity = 1.0
        self._reference_mode = "off"
        self._reference_value = 0.0
        self._waterfall = None
        self._rendering_decision: RenderingDecision | None = None
        self._requested_policy = RenderingPolicy.AUTO
        self._mesh_rebuild_count = 0
        self._camera_update_count = 0
        self._last_prepare_ms: float | None = None
        self._last_upload_ms: float | None = None
        self._share_mode = False
        self._point_cloud = None
        self._source_height_grid = None
        self._interaction_active = False
        self._interaction_grid: DisplayGrid | None = None
        self._idle_grid: DisplayGrid | None = None
        self._generation = 0
        self._pending = None
        self._signals = _GridSignals()
        self._signals.ready.connect(self._on_grid_ready)
        self._pool = QThreadPool.globalInstance()
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(IDLE_RESTORE_MS)
        self._idle_timer.timeout.connect(self._restore_idle_grid)
        self._interaction = _Interaction(self)
        self.view.installEventFilter(self._interaction)
        self._fps = 0.0
        self.graph.currentFpsChanged.connect(self._on_fps)
        renderer_id = id(self)
        self.destroyed.connect(lambda _obj=None, rid=renderer_id: clear_renderer_diagnostics(rid))
        from app.theme import WHITE_PLOT
        self.apply_scientific_plot_appearance(WHITE_PLOT)
        self.reset_view()

    # ------------------------------------------------------------ properties
    @property
    def surface_grid(self) -> SurfaceGrid | None:
        return self._grid

    @property
    def color_range(self):
        return self._color_range

    @property
    def rendering_decision(self):
        return self._rendering_decision

    @property
    def is_preparing(self) -> bool:
        return self._pending is not None

    @property
    def mesh_rebuild_count(self) -> int:
        return self._mesh_rebuild_count

    @property
    def camera_update_count(self) -> int:
        return self._camera_update_count

    def _profile(self) -> str:
        store = getattr(self.localizer, "store", None)
        getter = getattr(store, "three_d_profile", None)
        return getter() if callable(getter) else "balanced"

    # ------------------------------------------------------------ appearance
    def apply_scientific_plot_appearance(self, colors) -> None:
        theme = self.graph.activeTheme()
        dark = QColor(colors.background).lightness() < 128
        theme.setColorScheme(self._QGraphsTheme.ColorScheme.Dark if dark else self._QGraphsTheme.ColorScheme.Light)
        # Journal-figure look: plain background, light grey walls, fine grey
        # grid, serif labels, strong ambient light so surface colors stay close
        # to the scientific colormap with only gentle shape shading.
        theme.setBackgroundColor(QColor(colors.background))
        theme.setPlotAreaBackgroundColor(QColor(SURFACE_3D["wall_light"]) if not dark else QColor(colors.panel))
        theme.setLabelTextColor(QColor(colors.text))
        theme.setLabelBackgroundVisible(False)
        theme.setLabelBorderVisible(False)
        font = QFont(theme.labelFont())
        font.setFamilies(["Times New Roman", "Times", "STIX Two Text", "Georgia", "serif"])
        theme.setLabelFont(font)
        for axis in self._axes:
            axis.setLabelSize(AXIS_LABEL_SIZE)
            axis.setTitleOffset(0.0)
        grid = theme.grid()
        if hasattr(grid, "setMainColor"):
            grid.setMainColor(QColor(SURFACE_3D["grid_main_light"]) if not dark else QColor(colors.border))
            grid.setSubColor(QColor(SURFACE_3D["grid_sub_light"]) if not dark else QColor(colors.border).darker(120))
            theme.setGrid(grid)
        self.graph.setAmbientLightStrength(0.8)
        self.graph.setLightStrength(2.0)
        self._plot_colors = colors
        self.colorbar_widget.setStyleSheet(
            f"QWidget#surfaceColorbar {{ background-color: {colors.background}; }}"
            f"QWidget#surfaceColorbar QLabel {{ color: {colors.text}; background: transparent; }}"
        )
        self.pick_label.setStyleSheet(
            f"QLabel {{ color: {colors.text}; background-color: {colors.panel}; "
            f"border: 1px solid {colors.border}; padding: 3px 6px; }}"
        )
        if self._height_map is not None:
            self._update_reference_plane()

    # ------------------------------------------------------------ data entry
    def clear(self) -> None:
        self._generation += 1
        self._pending = None
        self._idle_timer.stop()
        self._interaction_active = False
        for series in (self.series, self.secondary_series, self.projection_series, self.reference_series):
            series.setVisible(False)
        self._clear_ribbons()
        self._source_height = self._source_color = self._source_height_grid = None
        self._display = self._grid = self._height_map = None
        self._idle_grid = self._interaction_grid = None
        self._secondary_source = None
        self._waterfall = None
        self._rendering_decision = None
        self._color_range = None
        self.pick_label.setText(self.localizer.text("viewer.surface_pick_hint"))
        for label in (self.reference_label, self.color_title, self.status_label):
            label.clear()
        self.color_high.setText("—"); self.color_low.setText("—")
        self._publish_diagnostics()

    def retranslate(self) -> None:
        if self._grid is not None:
            self._set_status_text()
        if self.series.selectedPoint() == self._QSurface3DSeries.invalidSelectionPosition():
            self.pick_label.setText(self.localizer.text("viewer.surface_pick_hint"))

    def set_geometry_type(self, geometry: str) -> None:
        geometry = str(geometry)
        if geometry not in SURFACE_GEOMETRIES:
            raise ValueError(f"{geometry!r} is not rendered by the Qt Graphs surface renderer.")
        self._geometry_type = geometry
        if geometry != "Dual Surface":
            self._secondary_source = None
            self._shared_height_limits = None
            self.secondary_series.setVisible(False)
        if geometry != "Waterfall":
            self._waterfall = None
            self._clear_ribbons()

    def _policy_budget(self, rows: int, columns: int, policy) -> RenderingDecision:
        return resolve_graphs_policy(rows, columns, policy, self._profile())

    def set_grid(self, grid, *, color_grid=None, colormap: str = "LabLog BWR",
                 color_range=None, rendering_policy=RenderingPolicy.AUTO,
                 shared_height_limits=None, _prepared: DisplayGrid | None = None) -> SurfaceGrid:
        heights = np.asarray(grid.z_values)
        color_grid = grid if color_grid is None else color_grid
        if np.iscomplexobj(heights) or np.iscomplexobj(np.asarray(color_grid.z_values)):
            raise ValueError("Select a real-valued Transform before displaying a Surface.")
        if heights.ndim != 2 or heights.shape != (len(grid.y_values), len(grid.x_values)):
            raise ValueError("Surface data must have shape (len(Y), len(X)).")
        if np.shape(color_grid.z_values) != heights.shape:
            raise ValueError("Color Source must use the same X/Y grid as Height.")
        if not np.isfinite(heights).any():
            raise ValueError("The selected Surface contains no finite Z values.")
        new_axes = (self._grid is None or self._source_height is None
                    or (grid.x_name, grid.y_name, heights.shape) != (
                        self._source_height.x_name, self._source_height.y_name,
                        np.shape(self._source_height.z_values)))
        self._source_height = self._source_height_grid = grid
        self._source_color = color_grid
        self._color_follows_height = color_grid is grid
        self._colormap_name = colormap
        self._color_range = color_range
        self._shared_height_limits = shared_height_limits
        self._requested_policy = RenderingPolicy(rendering_policy)
        rows, columns = heights.shape
        self._rendering_decision = self._policy_budget(rows, columns, rendering_policy)
        self._idle_grid = self._interaction_grid = None
        display = _prepared or build_display_grid(
            grid.x_values, grid.y_values, heights, color_grid.z_values,
            max_vertices=self._rendering_decision.max_vertices,
        )
        self._idle_grid = display
        self._apply_display(display)
        if new_axes:
            self.view_all()
        self._publish_diagnostics()
        return self._grid

    def set_grid_async(self, grid, *, color_grid=None, colormap: str = "LabLog BWR",
                       color_range=None, rendering_policy=RenderingPolicy.AUTO) -> None:
        """Build a large display grid in a worker; upload on the GUI thread."""
        color_grid = grid if color_grid is None else color_grid
        rows, columns = np.shape(grid.z_values)
        decision = self._policy_budget(rows, columns, rendering_policy)
        self._generation += 1
        self._pending = (self._generation, grid, color_grid, colormap, color_range, rendering_policy)
        self.series.setVisible(False)
        # Nothing is displayable until the worker returns; later setters
        # (opacity, overlays) only record state until then.
        self._grid = self._display = self._height_map = None
        self._pool.start(_GridJob(
            self._signals, self._generation,
            (grid.x_values, grid.y_values, np.asarray(grid.z_values), np.asarray(color_grid.z_values)),
            decision.max_vertices,
        ))

    def _on_grid_ready(self, generation: int, result) -> None:
        if isinstance(self._pending, tuple) and self._pending[0] == generation:
            _, grid, color_grid, colormap, color_range, policy = self._pending
            self._pending = None
            if isinstance(result, Exception):
                self.surface_failed.emit(str(result))
                return
            display, elapsed = result
            try:
                self.set_grid(grid, color_grid=color_grid, colormap=colormap, color_range=color_range,
                              rendering_policy=policy, _prepared=display)
                self._last_prepare_ms = elapsed
            except Exception as error:
                self.surface_failed.emit(str(error))
                return
            self.surface_ready.emit(self._grid)
            return
        pending = getattr(self, "_pending_interaction", None)
        if pending == generation and not isinstance(result, Exception):
            self._interaction_grid = result[0]
            if self._interaction_active:
                self._apply_display(self._interaction_grid, keep_labels=True)

    def set_color_grid(self, color_grid) -> None:
        if self._source_height is None:
            raise ValueError("Set a Height grid before the Color Source.")
        self.set_grid(self._source_height, color_grid=color_grid, colormap=self._colormap_name,
                      color_range=None, rendering_policy=self._requested_policy,
                      shared_height_limits=self._shared_height_limits)

    def set_color_settings(self, *, colormap: str | None = None, color_range=None) -> None:
        if colormap is not None:
            self._colormap_name = colormap
        self._color_range = color_range
        if self._grid is not None:
            self._apply_colors()
            self._apply_overlays()
        self._publish_diagnostics()

    def set_rendering_policy(self, policy):
        if self._source_height is None:
            return None
        return self.set_grid(self._source_height, color_grid=self._source_color, colormap=self._colormap_name,
                             color_range=self._color_range, rendering_policy=policy,
                             shared_height_limits=self._shared_height_limits)

    def set_opacity(self, opacity: float) -> None:
        opacity = float(opacity)
        if not np.isfinite(opacity) or not 0.0 <= opacity <= 1.0:
            raise ValueError("Surface opacity must be between 0 and 1.")
        if opacity == self._opacity:
            return
        self._opacity = opacity
        self._update_transparency_technique()
        if self._grid is not None:
            self._apply_colors()
            if self._waterfall is not None:
                self._apply_waterfall()

    def set_z_scale(self, scale: float) -> None:
        scale = float(scale)
        if not 0.1 <= scale <= 10.0:
            raise ValueError("Z Scale must be between 0.1x and 10x.")
        if scale == self._z_scale:
            return
        self._z_scale = scale
        if self._grid is not None:
            self._apply_display(self._display, keep_labels=True)

    def set_bottom_projection(self, enabled: bool, *, opacity: float | None = None) -> None:
        self._projection_enabled = bool(enabled)
        if opacity is not None:
            if not 0.0 <= float(opacity) <= 1.0:
                raise ValueError("Projection opacity must be between 0 and 1.")
            self._projection_opacity = float(opacity)
        self._apply_overlays()

    def set_reference_plane(self, mode: str = "off", value: float | None = None) -> None:
        mode = str(mode).lower()
        if mode not in {"off", "minimum", "custom", "zero"}:
            raise ValueError("Reference Plane mode must be off, minimum, custom, or zero.")
        self._reference_mode = mode
        if value is not None:
            self._reference_value = float(value)
        self._update_reference_plane()

    def set_secondary_grid(self, grid, *, opacity: float = 0.55) -> None:
        if self._source_height is None:
            raise ValueError("A primary Surface is required before a second Surface.")
        if np.shape(grid.z_values) != np.shape(self._source_height.z_values):
            raise ValueError("Dual Surface mappings must share the same X/Y grid.")
        self._secondary_source = grid
        self._secondary_opacity = float(opacity)
        primary = np.asarray(self._source_height.z_values, dtype=np.float64)
        secondary = np.asarray(grid.z_values, dtype=np.float64)
        finite = np.concatenate((primary[np.isfinite(primary)], secondary[np.isfinite(secondary)]))
        self._shared_height_limits = (float(finite.min()), float(finite.max()))
        self._update_transparency_technique()
        if self._grid is not None:
            self._apply_display(self._display, keep_labels=True)

    def clear_secondary_grid(self) -> None:
        self._secondary_source = None
        self._shared_height_limits = None
        self.secondary_series.setVisible(False)
        if self._display is not None:
            self._apply_display(self._display, keep_labels=True)

    def set_secondary_opacity(self, opacity: float) -> None:
        self._secondary_opacity = float(opacity)
        self._update_transparency_technique()
        if self._secondary_source is not None and self._grid is not None:
            self._apply_secondary()

    def set_horizontal_colorbar_visible(self, visible: bool) -> None:
        self.horizontal_colorbar.setVisible(bool(visible))

    def set_waterfall_grid(self, prepared, *, colormap: str = "LabLog BWR", color_range=None,
                           rendering_policy=RenderingPolicy.AUTO) -> SurfaceGrid:
        """Waterfall as up to 64 colored ribbons, one per selected trace."""
        self.set_geometry_type("Waterfall")
        height, color = prepared.source_height, prepared.source_color
        self._waterfall = {"height": height, "color": color}
        grid = self.set_grid(height, color_grid=color, colormap=colormap, color_range=color_range,
                             rendering_policy=rendering_policy)
        return grid

    # ------------------------------------------------------------ upload
    def _limits_for_height(self) -> tuple[float, float]:
        if self._shared_height_limits is not None:
            return self._shared_height_limits
        values = np.asarray(self._source_height.z_values, dtype=np.float64)
        finite = values[np.isfinite(values)]
        return float(finite.min()), float(finite.max())

    def _upload(self, series, display: DisplayGrid, heights: np.ndarray) -> None:
        rows, columns = heights.shape
        scene = self._height_map.to_scene(heights).astype(np.float32)
        scene[~np.isfinite(scene)] = np.float32(self._height_map.scene_range[0])
        started = time.perf_counter()
        reset_surface(series, 0.0, 1.0 / max(1, columns - 1), 0.0, 1.0 / max(1, rows - 1), scene)
        self._last_upload_ms = (time.perf_counter() - started) * 1000

    def _apply_display(self, display: DisplayGrid | None, *, keep_labels: bool = False) -> None:
        if display is None:
            return
        self._display = display
        self._height_map = _HeightMap(*self._limits_for_height(), self._z_scale)
        self._grid = self._surface_grid_from(display)
        if self._waterfall is None:
            self._upload(self.series, display, display.heights)
            self.series.setVisible(True)
        else:
            self.series.setVisible(False)
        self._mesh_rebuild_count += 1
        self._set_axes()
        self._apply_colors()
        if self._waterfall is not None:
            self._apply_waterfall()
        self._apply_secondary()
        self._apply_overlays()
        if not keep_labels:
            self._set_status_text()

    def _surface_grid_from(self, display: DisplayGrid) -> SurfaceGrid:
        grid, color = self._source_height, self._source_color
        invalid = int(np.count_nonzero(~np.isfinite(np.asarray(grid.z_values, dtype=np.float64))))
        rows, columns = display.source_shape
        return SurfaceGrid(
            x_values=display.x_centers, y_values=display.y_centers, z_values=display.heights,
            x_name=str(grid.x_name), x_unit=grid.x_unit, y_name=str(grid.y_name), y_unit=grid.y_unit,
            z_name=str(grid.z_name), z_unit=grid.z_unit, z_transform=str(getattr(grid, "transform", "raw")),
            color_values=display.colors, color_name=str(color.z_name), color_unit=color.z_unit,
            color_transform=str(getattr(color, "transform", "raw")),
            source_shape=(rows, columns), invalid_value_count=invalid,
            sample_step=(max(1, -(-rows // display.shape[0])), max(1, -(-columns // display.shape[1]))),
            source_row_indices=display.source_rows[:, 0].copy(),
            source_column_indices=display.source_columns[0].copy(),
        )

    def _set_axes(self) -> None:
        grid, display, height_map = self._source_height, self._display, self._height_map
        x_axis, y_axis, z_axis = self._axes

        from app.visualization3d.publication_style import si_axis

        def linear(centers, scale):
            low, high = float(centers[0]), float(centers[-1])
            return lambda value: (low + value * (high - low)) / scale

        # Readable SI prefixes on screen too (5.022 GHz instead of 5.022e+09 Hz).
        x_text = si_axis(grid.x_name, grid.x_unit, display.x_centers)
        y_text = si_axis(grid.y_name, grid.y_unit, display.y_centers)
        x_formatter, y_formatter, z_formatter = self._formatters
        for axis, formatter, title, mapping, span in (
            (x_axis, x_formatter, x_text.label, linear(display.x_centers, x_text.scale), (0.0, 1.0)),
            (z_axis, z_formatter, y_text.label, linear(display.y_centers, y_text.scale), (0.0, 1.0)),
            (y_axis, y_formatter,
             _axis_title(f"{grid.z_name} — {_transform_label(str(getattr(grid, 'transform', 'raw')))}",
                         grid.z_unit, "Z"), height_map.physical, height_map.scene_range),
        ):
            axis.setTitle(title)
            # Update the persistent formatter in place; markDirty makes Qt
            # re-populate its render copy through populateCopy().
            formatter.to_physical = mapping
            axis.setRange(*span)
            formatter.markDirty(True)

    def _color_limits(self) -> tuple[float, float]:
        if self._color_range is not None:
            low, high = (float(value) for value in self._color_range)
        else:
            low, high = robust_color_limits(np.asarray(self._source_color.z_values, dtype=np.float64))
        if not np.isfinite([low, high]).all() or low >= high:
            raise ValueError("Color Range must contain finite Minimum < Maximum values.")
        return low, high

    def _apply_colors(self) -> None:
        low, high = self._color_limits()
        self._color_range = (low, high)
        if self._waterfall is None:
            rgba = colormap_rgba(self._display.colors, self._colormap_name, (low, high), self._opacity)
            self.series.setTexture(_rgba_image(rgba))
        ramp = np.asarray(get_colormap(self._colormap_name).map(np.linspace(0, 1, 256), mode="byte"))
        self.color_ramp.set_colors(ramp)
        grid = self._grid
        title = (f"{grid.color_name} — {_transform_label(grid.color_transform)}"
                 + (f" [{grid.color_unit}]" if grid.color_unit else ""))
        self.color_title.setText(title)
        self.color_high.setText(f"{high:.5g}")
        self.color_low.setText(f"{low:.5g}")
        source = np.asarray(self._source_color.z_values, dtype=np.float64)
        finite = source[np.isfinite(source)]
        span = (float(finite.min()), float(finite.max())) if finite.size else (low, high)
        if span[0] >= span[1]:
            span = (low, high)
        self.horizontal_colorbar.set_mapping(ramp, span, (low, high), title)

    def _apply_secondary(self) -> None:
        if self._secondary_source is None or self._display is None:
            self.secondary_series.setVisible(False)
            return
        display = self._display
        values = np.asarray(self._secondary_source.z_values, dtype=np.float64)
        heights = values[display.source_rows, display.source_columns]
        self._upload(self.secondary_series, display, heights)
        tint = np.zeros(heights.shape + (4,), np.uint8)
        tint[..., :3] = SURFACE_3D["dual_surface_b"]
        tint[..., 3] = np.uint8(round(255 * self._secondary_opacity))
        tint[~np.isfinite(heights), 3] = 0
        self.secondary_series.setTexture(_rgba_image(tint))
        self.secondary_series.setVisible(True)

    def _clear_ribbons(self) -> None:
        for series in self._ribbons:
            series.setVisible(False)

    def _apply_waterfall(self) -> None:
        display = self._display
        height = np.asarray(self._waterfall["height"].z_values, dtype=np.float64)
        color = np.asarray(self._waterfall["color"].z_values, dtype=np.float64)
        traces = select_waterfall_traces(height.shape[0], WATERFALL_TRACE_LIMIT)
        y_values = np.asarray(self._source_height.y_values, dtype=np.float64)
        y_low, y_high = float(np.min(y_values)), float(np.max(y_values))
        span = (y_high - y_low) or 1.0
        spacing = 1.0 / max(1, len(traces))
        depth = spacing * WATERFALL_RIBBON_DEPTH
        columns = display.source_columns[0]
        low, high = self._color_range
        while len(self._ribbons) < len(traces):
            self._ribbons.append(self._make_series())
        self._ribbon_traces = traces
        for ribbon, trace in zip(self._ribbons, traces):
            values = height[trace, columns]
            scene = self._height_map.to_scene(np.vstack([values, values])).astype(np.float32)
            scene[~np.isfinite(scene)] = np.float32(self._height_map.scene_range[0])
            center = (y_values[trace] - y_low) / span
            z0 = min(max(center - depth / 2, 0.0), 1.0 - depth)
            reset_surface(ribbon, 0.0, 1.0 / max(1, len(columns) - 1), float(z0), float(depth), scene)
            rgba = colormap_rgba(np.vstack([color[trace, columns]] * 2), self._colormap_name, (low, high),
                                 self._opacity)
            ribbon.setTexture(_rgba_image(rgba))
            ribbon.setVisible(True)
        for ribbon in self._ribbons[len(traces):]:
            ribbon.setVisible(False)

    def _apply_overlays(self) -> None:
        if self._grid is None or self._display is None or self._height_map is None:
            self.projection_series.setVisible(False)
            self.reference_series.setVisible(False)
            return
        if self._projection_enabled:
            display = self._display
            floor = self._height_map.scene_range[0]
            lift = PROJECTION_LIFT * (self._height_map.scene_range[1] - floor)
            flat = np.full(display.shape, floor + lift, np.float32)
            rows, columns = display.shape
            reset_surface(self.projection_series, 0.0, 1.0 / max(1, columns - 1), 0.0, 1.0 / max(1, rows - 1), flat)
            rgba = colormap_rgba(display.colors, self._colormap_name, self._color_range, self._projection_opacity)
            self.projection_series.setTexture(_rgba_image(rgba))
            self.projection_series.setVisible(True)
        else:
            self.projection_series.setVisible(False)
        self._update_reference_plane()

    def _update_reference_plane(self) -> None:
        mode = self._reference_mode
        if mode == "off" or self._height_map is None or self._source_height is None:
            self.reference_series.setVisible(False)
            self.reference_label.clear()
            return
        low, high = self._limits_for_height()
        value = low if mode == "minimum" else 0.0 if mode == "zero" else self._reference_value
        unit = self._source_height.z_unit
        if not low <= value <= high:
            self.reference_series.setVisible(False)
            self.reference_label.setText(self.localizer.text(
                "viewer.reference_outside", value=f"{value:.5g}", low=f"{low:.5g}", high=f"{high:.5g}"))
            return
        scene = float(self._height_map.to_scene(value))
        if mode == "minimum" and self._projection_enabled:
            scene += 2 * PROJECTION_LIFT * (self._height_map.scene_range[1] - self._height_map.scene_range[0])
        reset_surface(self.reference_series, 0.0, 1.0, 0.0, 1.0, np.full((2, 2), scene, np.float32))
        dark = QColor(getattr(getattr(self, "_plot_colors", None), "background", PLOT_WHITE.background)).lightness() < 128
        plane = np.zeros((2, 2, 4), np.uint8)
        plane[...] = SURFACE_3D["reference_plane_dark" if dark else "reference_plane_light"]
        self.reference_series.setTexture(_rgba_image(plane))
        self.reference_series.setVisible(True)
        self._update_transparency_technique()
        key = {"minimum": "viewer.reference_minimum", "zero": "viewer.reference_zero"}.get(mode, "viewer.reference_custom")
        self.reference_label.setText(self.localizer.text(key, value=f"{value:.6g}" + (f" {unit}" if unit else "")))

    def _update_transparency_technique(self) -> None:
        # Approximate OIT renders translucent textured surfaces with correct
        # scientific colors; Default blending produced false colors (verified
        # natively). Qt may log an "invalid blend modes" notice for helper series.
        technique = self._QtGraphs3D.TransparencyTechnique
        needs_blend = (self._opacity < 1.0 or self._secondary_source is not None
                       or (self._reference_mode != "off" and self.reference_series.isVisible()))
        self.graph.setTransparencyTechnique(technique.Approximate if needs_blend else technique.Default)

    # ------------------------------------------------------------ interaction LOD
    def _interaction_start(self) -> None:
        self._camera_update_count += 1
        if self._display is None or self._source_height is None or self._waterfall is not None:
            return
        self._idle_timer.stop()
        self._interaction_active = True
        _idle, budget = PROFILES.get(self._profile(), PROFILES["balanced"])
        if self._display.vertex_count <= budget:
            return
        if self._interaction_grid is None:
            grid, color = self._source_height, self._source_color
            args = (grid.x_values, grid.y_values, np.asarray(grid.z_values), np.asarray(color.z_values))
            if np.size(grid.z_values) > ASYNC_SOURCE_POINTS:
                self._generation += 1
                self._pending_interaction = self._generation
                self._pool.start(_GridJob(self._signals, self._generation, args, budget))
                return
            self._interaction_grid = build_display_grid(*args, max_vertices=budget)
        self._apply_display(self._interaction_grid, keep_labels=True)

    def _interaction_end(self) -> None:
        if self._interaction_active:
            self._idle_timer.start()

    def _restore_idle_grid(self) -> None:
        self._interaction_active = False
        if self._idle_grid is not None and self._display is not self._idle_grid:
            self._apply_display(self._idle_grid)
        self._publish_diagnostics()

    # ------------------------------------------------------------ camera
    def _rotate_camera(self, dx: float, dy: float) -> None:
        graph = self.graph
        graph.setCameraXRotation((graph.cameraXRotation() + dx * 0.5) % 360.0)
        vertical = graph.cameraYRotation() + dy * 0.5
        graph.setCameraYRotation(max(graph.minCameraYRotation(), min(graph.maxCameraYRotation(), vertical)))
        self._camera_update_count += 1

    def _pan_camera(self, dx: float, dy: float) -> None:
        target = self.graph.cameraTargetPosition()
        scale = 2.0 / max(1, min(self.view.width(), self.view.height())) * (100.0 / max(10.0, self.graph.cameraZoomLevel()))
        yaw = np.deg2rad(self.graph.cameraXRotation())
        right = QVector3D(float(np.cos(yaw)), 0.0, float(np.sin(yaw)))
        moved = target - right * (dx * scale) + QVector3D(0.0, dy * scale, 0.0)
        self.graph.setCameraTargetPosition(QVector3D(
            max(-1.0, min(1.0, moved.x())), max(-1.0, min(1.0, moved.y())), max(-1.0, min(1.0, moved.z()))))
        self._camera_update_count += 1

    def reset_view(self) -> None:
        self.graph.setCameraPosition(35.0, 25.0, DEFAULT_ZOOM)
        self.graph.setCameraTargetPosition(QVector3D(0.0, 0.0, 0.0))
        self._camera_update_count += 1

    view_all = reset_view

    def set_camera_preset(self, name: str) -> None:
        presets = self._QtGraphs3D.CameraPreset
        mapping = {"Top": presets.DirectlyAbove, "Front": presets.Front,
                   "Side": presets.Right, "Isometric": presets.IsometricRight}
        if name not in mapping:
            raise ValueError(f"Unknown camera preset: {name}")
        self.graph.setCameraPreset(mapping[name])
        self._camera_update_count += 1

    def set_projection(self, projection: str) -> None:
        normalized = str(projection).lower()
        if normalized not in {"perspective", "orthographic"}:
            raise ValueError(f"Unknown projection: {projection}")
        self.graph.setOrthoProjection(normalized == "orthographic")
        self._camera_update_count += 1

    def camera_state(self) -> CameraState3D:
        target = self.graph.cameraTargetPosition()
        return CameraState3D.from_dict({
            "x_rotation": self.graph.cameraXRotation(), "y_rotation": self.graph.cameraYRotation(),
            "zoom_level": self.graph.cameraZoomLevel(), "target": [target.x(), target.y(), target.z()],
        })

    def apply_camera_state(self, state: CameraState3D) -> None:
        self.graph.setCameraPosition(float(state.x_rotation), float(state.y_rotation), float(state.zoom_level))
        target = [max(-1.0, min(1.0, float(value))) for value in state.target]
        self.graph.setCameraTargetPosition(QVector3D(*target))
        self._camera_update_count += 1

    # ------------------------------------------------------------ picking
    def _pick(self, position: QPoint) -> None:
        self.graph.doPicking(position)

    def _on_selected_point(self, point) -> None:
        # Qt Graphs reports QPoint(column, row).
        if self._display is None or point.x() < 0 or point.y() < 0:
            self.pick_label.setText(self.localizer.text("viewer.surface_pick_hint"))
            return
        column, row = int(point.x()), int(point.y())
        if not (0 <= row < self._display.shape[0] and 0 <= column < self._display.shape[1]):
            return
        source_row = int(self._display.source_rows[row, column])
        source_column = int(self._display.source_columns[row, column])
        self.pick_label.setText(self.readout(source_row, source_column))

    def readout(self, source_row: int, source_column: int) -> str:
        grid, color = self._source_height, self._source_color

        def text(value, unit):
            value = float(value)
            return "not finite" if not np.isfinite(value) else f"{value:.6g}" + (f" {unit}" if unit else "")

        parts = [f"{grid.x_name}: {text(grid.x_values[source_column], grid.x_unit)}",
                 f"{grid.y_name}: {text(grid.y_values[source_row], grid.y_unit)}",
                 f"Height {grid.z_name} ({_transform_label(str(getattr(grid, 'transform', 'raw')))}): "
                 f"{text(np.asarray(grid.z_values)[source_row, source_column], grid.z_unit)}"]
        if color is not grid:
            parts.append(f"Color {color.z_name} ({_transform_label(str(getattr(color, 'transform', 'raw')))}): "
                         f"{text(np.asarray(color.z_values)[source_row, source_column], color.z_unit)}")
        return "  |  ".join(parts)

    # ------------------------------------------------------------ export
    def render_to_image(self, size: QSize, *, msaa_samples: int = 0) -> QImage:
        if size.width() <= 0 or size.height() <= 0:
            raise ValueError("Render size must be positive.")
        result = self.graph.renderToImage(size)
        result = result[0] if isinstance(result, tuple) else result
        if result is None:
            raise RuntimeError("Qt Graphs could not start an image capture.")
        if result.image().isNull():
            loop = QEventLoop()
            result.ready.connect(loop.quit)
            QTimer.singleShot(5000, loop.quit)
            loop.exec()
        image = result.image()
        if image.isNull():
            raise RuntimeError("The 3D renderer did not return the requested frame.")
        return image

    def render_plot_image(self, *, scale: int = 3) -> QImage:
        if scale < 1 or scale > 4:
            raise ValueError("3D export scale must be between 1 and 4.")
        if self._grid is None:
            raise ValueError("Wait for the 3D surface to finish preparing before export.")
        size = self.view.size()
        if size.width() <= 0 or size.height() <= 0:
            raise ValueError("The 3D viewport is not ready for export.")
        scene = self.render_to_image(size * scale)
        bar_width = self.colorbar_widget.width() if self.colorbar_widget.isVisible() else 0
        image = QImage((size.width() + bar_width) * scale, size.height() * scale,
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(getattr(getattr(self, "_plot_colors", None), "background", PLOT_WHITE.background)))
        painter = QPainter(image)
        painter.drawImage(0, 0, scene.scaled(size * scale))
        if bar_width:
            painter.save()
            painter.scale(scale, scale)
            self.colorbar_widget.render(painter, QPoint(size.width(), 0))
            painter.restore()
        painter.end()
        return image

    # ------------------------------------------------------------ status
    def _on_fps(self, fps) -> None:
        self._fps = float(fps)
        if self._grid is not None:
            self._set_status_text()

    def _set_status_text(self) -> None:
        if self._grid is None:
            self.status_label.clear()
            return
        decision = self._rendering_decision
        parts = [self.localizer.text(
            "viewer.surface_grid_status", source_rows=self._grid.source_shape[0],
            source_columns=self._grid.source_shape[1], vertices=self._display.vertex_count,
            policy=decision.effective.value if decision else "Auto",
        )]
        if self._waterfall is not None:
            parts.append(self.localizer.text("viewer.waterfall_traces", count=len(getattr(self, "_ribbon_traces", []))))
        if decision is not None and decision.warning:
            parts.append(decision.warning)
        if self._fps >= 5:   # on-demand rendering reports ~1 FPS when idle
            parts.append(self.localizer.text("viewer.surface_performance", fps=self._fps, frame_ms=1000.0 / self._fps))
        self.status_label.setText("  |  ".join(parts))

    def diagnostics(self) -> dict[str, object]:
        return {
            "renderer_id": id(self), "backend": self.backend, "geometry_type": self._geometry_type,
            "mesh_dimensions": list(self._display.shape) if self._display else None,
            "source_dimensions": list(self._display.source_shape) if self._display else None,
            "vertex_count": self._display.vertex_count if self._display else 0,
            "display_exact": self._display.exact if self._display else None,
            "fps": self._fps or None, "frame_time_ms": 1000.0 / self._fps if self._fps else None,
            "mesh_rebuild_count": self._mesh_rebuild_count,
            "grid_prepare_ms": self._last_prepare_ms, "upload_ms": self._last_upload_ms,
            "interaction_lod_active": self._interaction_active, "profile": self._profile(),
            "rendering_policy": self._rendering_decision.effective.value if self._rendering_decision else None,
            "z_scale": self._z_scale, "surface_opacity": self._opacity,
            "bottom_projection": self._projection_enabled, "reference_plane": self._reference_mode,
            "dual_surface": self._secondary_source is not None,
            "waterfall_traces": len(getattr(self, "_ribbon_traces", [])) if self._waterfall else 0,
            "projection": "orthographic" if self.graph.isOrthoProjection() else "perspective",
            "camera_update_count": self._camera_update_count,
        }

    def _publish_diagnostics(self, *_args) -> None:
        publish_renderer_diagnostics(self.diagnostics())

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API spelling
        self._generation += 1
        self._idle_timer.stop()
        clear_renderer_diagnostics(id(self))
        super().closeEvent(event)
