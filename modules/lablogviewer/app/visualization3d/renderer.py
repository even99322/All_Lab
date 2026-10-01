"""Persistent Qt Data Visualization Surface renderer for N-D grids."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import numpy as np

from PySide6.QtCore import QEvent, QObject, Qt, QSize, QTimer, QRunnable, QThreadPool, Signal, QPoint, QRect
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen, QPolygon, QQuaternion, QVector3D
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from app.palette import SURFACE_3D
from app.visualization3d.data import (
    NormalizedAxis, SurfaceGrid, normalize_axis_for_scene, prepare_surface_grid, prepare_surface_mesh,
    scene_height_for_value,
)
from app.visualization3d.diagnostics import clear_renderer_diagnostics, publish_renderer_diagnostics
from app.visualization3d.state import CameraState3D
from app.visualization3d.availability import probe_opengl_context
from app.visualization3d.cache import BoundedSurfaceCache
from app.localization import LocalizationManager, get_localization_manager
from app.gui.plot_2d_widget import get_colormap, robust_color_limits
from app.visualization3d.policy import (
    RenderingDecision, RenderingPolicy, interaction_vertex_budget, resolve_rendering_policy,
)


def _axis_title(name: str, unit: str | None, semantic_axis: str) -> str:
    suffix = f" [{unit}]" if unit else ""
    return f"{name}{suffix} ({semantic_axis})"


def _transform_label(transform: str) -> str:
    return {
        "raw": "Raw", "real": "Real", "imag": "Imaginary",
        "magnitude": "Magnitude", "magnitude_db": "Magnitude (dB)",
        "phase_deg": "Phase (deg)", "phase_rad": "Phase (rad)",
        "phase_unwrapped_deg": "Unwrapped Phase (deg)",
        "phase_unwrapped_rad": "Unwrapped Phase (rad)",
    }.get(transform, transform.replace("_", " "))


def map_surface_colors(values: np.ndarray, colormap: str,
                       limits: tuple[float, float], *, opacity: float = 1.0) -> np.ndarray:
    """Map the independent scientific color field to RGBA bytes."""
    low, high = (float(value) for value in limits)
    values = np.asarray(values, dtype=np.float64)
    if (values.ndim != 2 or not np.isfinite(low) or not np.isfinite(high) or low >= high
            or not np.isfinite(opacity) or not 0.0 <= opacity <= 1.0):
        raise ValueError("Color values must be a 2D grid with finite Minimum < Maximum.")
    finite = np.isfinite(values)
    normalized = np.zeros(values.shape, dtype=np.float64)
    normalized[finite] = np.clip((values[finite] - low) / (high - low), 0.0, 1.0)
    rgba = np.asarray(get_colormap(colormap).map(normalized, mode="byte"), dtype=np.uint8)
    rgba[..., 3] = np.rint(rgba[..., 3].astype(np.float64) * float(opacity)).astype(np.uint8)
    rgba[~finite, 3] = 0
    return rgba


def build_surface_gradient(
    colormap: str,
    color_limits: tuple[float, float],
    height_limits: tuple[float, float],
    *,
    samples: int = 257,
    opacity: float = 1.0,
) -> QLinearGradient:
    """Build the native Q3DSurface height-gradient using the shared 2D LUT.

    Qt's range-gradient shader colors surface vertices from their vertical
    coordinate. Height coordinates are normalized from ``height_limits``;
    this maps that coordinate back through the scientific color limits before
    sampling the same pyqtgraph ColorMap used by 2D and the color ramp.
    """
    color_low, color_high = (float(value) for value in color_limits)
    height_low, height_high = (float(value) for value in height_limits)
    if (not np.isfinite([color_low, color_high, height_low, height_high]).all()
            or color_low >= color_high or height_low > height_high or samples < 2
            or not 0.0 <= opacity <= 1.0):
        raise ValueError("Surface gradient requires finite, ordered color and height ranges.")

    positions = np.linspace(0.0, 1.0, int(samples))
    heights = height_low + positions * (height_high - height_low)
    normalized = np.clip((heights - color_low) / (color_high - color_low), 0.0, 1.0)
    rgba = np.asarray(get_colormap(colormap).map(normalized, mode="byte"), dtype=np.uint8)
    gradient = QLinearGradient(0.0, 0.0, 0.0, 1.0)
    for position, color in zip(positions, rgba):
        color = color.copy()
        color[3] = int(round(int(color[3]) * opacity))
        gradient.setColorAt(float(position), QColor(*(int(channel) for channel in color)))
    return gradient


def configure_surface_series_colors(
    series,
    *,
    color_follows_height: bool,
    gradient: QLinearGradient,
    texture: QImage,
    range_gradient_style,
    uniform_style,
) -> str:
    """Bind scientific colors to the Qt surface renderer, not just its legend."""
    series.setBaseColor(QColor(SURFACE_3D["white"]))
    if color_follows_height:
        # A QSurface3DSeries texture was being calculated correctly, but the
        # native surface remained configured as Uniform. Use the renderer's
        # actual scalar-range shader when color is the height field.
        series.setTexture(QImage())
        series.setBaseGradient(gradient)
        series.setColorStyle(range_gradient_style)
        return "range-gradient"
    series.setColorStyle(uniform_style)
    series.setTexture(texture)
    return "texture"


def surface_color_mapping_diagnostics(
    values: np.ndarray,
    colormap: str,
    limits: tuple[float, float],
) -> list[dict[str, object]]:
    """Expose the finite data → normalized scalar → shared LUT checkpoints."""
    field = np.asarray(values, dtype=np.float64)
    finite = field[np.isfinite(field)]
    if finite.size == 0:
        return []
    low, high = (float(value) for value in limits)
    samples = np.array([finite.min(), (finite.min() + finite.max()) / 2.0, finite.max()])
    rgba = np.asarray(
        get_colormap(colormap).map(
            np.clip((samples - low) / (high - low), 0.0, 1.0), mode="byte"
        ), dtype=np.uint8,
    )
    return [
        {
            "value": float(value),
            "normalized": float(np.clip((value - low) / (high - low), 0.0, 1.0)),
            "rgba": [int(channel) for channel in color],
        }
        for value, color in zip(samples, rgba)
    ]


def format_pick_readout(grid: SurfaceGrid, row: int, column: int) -> str:
    """Format sampled source values, never the vertically scaled render mesh."""
    if not (0 <= row < grid.shape[0] and 0 <= column < grid.shape[1]):
        return ""

    def value_text(value: object, unit: str | None) -> str:
        number = float(value)
        if not np.isfinite(number):
            return "not finite"
        return f"{number:.6g}" + (f" {unit}" if unit else "")

    x = value_text(grid.x_values[column], grid.x_unit)
    y = value_text(grid.y_values[row], grid.y_unit)
    height = value_text(grid.z_values[row, column], grid.z_unit)
    parts = [
        f"{grid.x_name}: {x}",
        f"{grid.y_name}: {y}",
        f"Height {grid.z_name} ({_transform_label(grid.z_transform)}): {height}",
    ]
    if (grid.color_name, grid.color_transform) != (grid.z_name, grid.z_transform):
        color = value_text(grid.color_values[row, column], grid.color_unit)
        parts.append(
            f"Color {grid.color_name} ({_transform_label(grid.color_transform)}): {color}"
        )
    return "  |  ".join(parts)


def configure_surface_input_handler(handler) -> None:
    """Keep native picking/zoom while camera rotation is handled exclusively."""
    handler.setRotationEnabled(False)
    handler.setZoomEnabled(True)
    handler.setSelectionEnabled(True)


def configure_filled_surface(series) -> None:
    """Qt defaults to SurfaceAndWireframe; dense black edges hide the faces."""
    from PySide6.QtDataVisualization import QSurface3DSeries

    series.setDrawMode(QSurface3DSeries.DrawFlag.DrawSurface)


def configure_scientific_3d_scene(graph, theme_type) -> None:
    """Use one restrained scientific axis-box style and XYZ proportion."""
    theme = graph.activeTheme()
    theme.setType(theme_type.Theme.ThemeUserDefined)
    theme.setBackgroundEnabled(True)
    theme.setBackgroundColor(QColor(SURFACE_3D["legacy_background"]))
    theme.setWindowColor(QColor(SURFACE_3D["legacy_background"]))
    theme.setLabelTextColor(QColor(SURFACE_3D["legacy_label"]))
    theme.setLabelBackgroundEnabled(False)
    theme.setGridLineColor(QColor(SURFACE_3D["legacy_grid"]))
    theme.setGridEnabled(True)
    theme.setAmbientLightStrength(0.85)
    theme.setLightStrength(0.15)
    graph.setShadowQuality(graph.ShadowQuality.ShadowQualityNone)
    # Match the reference's (X, Y, Z) = (1, 1, 0.72) box proportions.
    graph.setAspectRatio(0.72)
    graph.setHorizontalAspectRatio(1.0)


class _LodSignals(QObject):
    ready = Signal(int, object, object, object)


@dataclass(frozen=True)
class _PreparedResult:
    grid: SurfaceGrid
    elapsed_ms: float


class _LodJob(QRunnable):
    def __init__(self, signals: _LodSignals, generation: int, key: object,
                 height_grid: object, color_grid: object, budget: int):
        super().__init__()
        self.signals = signals
        self.generation = generation
        self.key = key
        self.height_grid = height_grid
        self.color_grid = color_grid
        self.budget = budget

    def run(self) -> None:
        try:
            started = time.perf_counter()
            grid = prepare_surface_grid(
                self.height_grid, color_grid=self.color_grid, max_vertices=self.budget,
            )
            prepared = _PreparedResult(grid, (time.perf_counter() - started) * 1000)
        except Exception as error:
            prepared = error
        self.signals.ready.emit(self.generation, self.key, self.budget, prepared)


def _apply_camera_pose(camera, x_rotation: float, y_rotation: float,
                       zoom_level: float, target: QVector3D) -> None:
    """Apply a camera pose using PySide6's actual 3-argument binding.

    Q3DCamera.setCameraPosition accepts horizontal rotation, vertical
    rotation, and zoom. Its look-at target is set independently.
    """
    camera.setTarget(target)
    camera.setCameraPosition(float(x_rotation), float(y_rotation), float(zoom_level))


def _camera_graph(renderer):
    return getattr(renderer, "active_graph", renderer.graph)


class _SurfaceInteractionFilter(QObject):
    """Exclusive mouse state machine for rotate, pan, and native click-pick."""

    IDLE = "idle"
    LEFT_PENDING = "left-pending"
    ROTATING = "rotating"
    PANNING = "panning"
    DRAG_THRESHOLD = 8.0

    def __init__(self, renderer: "SurfaceRenderer"):
        super().__init__(renderer)
        self.renderer = renderer
        self._active_button = None
        self._suppressed_buttons = set()
        self._state = self.IDLE
        self._press_position = None
        self._last_position = None
        self._active_watched = None

    def _finish(self) -> None:
        if self._active_watched is not None:
            self._active_watched.unsetCursor()
        self._active_button = None
        self._state = self.IDLE
        self._press_position = None
        self._last_position = None
        self._active_watched = None

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API spelling
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.RightButton:
            self.renderer.export_context_requested.emit(event.globalPosition().toPoint())
            event.accept()
            return True
        if event.type() == QEvent.Type.MouseButtonPress:
            button = event.button()
            if self._active_button is not None:
                if button != self._active_button:
                    self._suppressed_buttons.add(button)
                    event.accept()
                    return True
                return False
            if button not in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
                return False
            self._active_button = button
            self._active_watched = watched
            self._press_position = event.position()
            self._last_position = self._press_position
            if button == Qt.MouseButton.MiddleButton:
                self._state = self.PANNING
                watched.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.renderer._interaction_start()
                event.accept()
                return True
            self._state = self.LEFT_PENDING
            # Keep a short left press/release in Qt's native picking path.
            return False

        if event.type() == QEvent.Type.MouseMove and self._active_button is not None:
            current = event.position()
            if self._state == self.PANNING:
                delta = current - self._last_position
                self._last_position = current
                self.renderer._pan_camera(float(delta.x()), float(delta.y()))
                event.accept()
                return True
            if self._state == self.LEFT_PENDING:
                delta_from_press = current - self._press_position
                if delta_from_press.manhattanLength() < self.DRAG_THRESHOLD:
                    return False
                if getattr(self.renderer, "_share_mode", False):
                    self._finish()
                    self.renderer.share_drag_requested.emit()
                    event.accept()
                    return True
                self._state = self.ROTATING
                self._active_watched.setCursor(Qt.CursorShape.SizeAllCursor)
                self.renderer._interaction_start()
                delta = current - self._press_position
                self._last_position = current
                self.renderer._rotate_camera(float(delta.x()), float(delta.y()))
                event.accept()
                return True
            if self._state == self.ROTATING:
                delta = current - self._last_position
                self._last_position = current
                self.renderer._rotate_camera(float(delta.x()), float(delta.y()))
                event.accept()
                return True

        if event.type() == QEvent.Type.MouseButtonRelease:
            button = event.button()
            if button in self._suppressed_buttons:
                self._suppressed_buttons.discard(button)
                event.accept()
                return True
            if button != self._active_button:
                return False
            should_consume = self._state != self.LEFT_PENDING
            if should_consume:
                self.renderer._interaction_end()
            self._finish()
            if should_consume:
                event.accept()
                return True
        if event.type() == QEvent.Type.Wheel:
            self.renderer._interaction_start()
            self.renderer._interaction_end()
        return False


class _ColorRamp(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rgba = np.zeros((256, 4), dtype=np.uint8)
        self.setMinimumWidth(28)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def set_colors(self, rgba: np.ndarray) -> None:
        self._rgba = np.asarray(rgba, dtype=np.uint8).copy()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        rect = self.rect().adjusted(4, 1, -4, -1)
        gradient = QLinearGradient(rect.left(), rect.bottom(), rect.left(), rect.top())
        count = max(1, len(self._rgba) - 1)
        for index, rgba in enumerate(self._rgba):
            gradient.setColorAt(index / count, QColor(*[int(value) for value in rgba]))
        painter.fillRect(rect, gradient)
        painter.setPen(QPen(QColor(SURFACE_3D["colorbar_outline"]), 1))
        painter.drawRect(rect)


class InteractiveColorbar(QWidget):
    """Compact draggable two-handle range control for one shared color state."""

    levels_changed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(34)
        self.setMaximumHeight(46)
        self.setMouseTracking(True)
        self._rgba = np.zeros((256, 4), dtype=np.uint8)
        self._span = (0.0, 1.0)
        self._levels = (0.0, 1.0)
        self._title = ""
        self._drag_handle: str | None = None
        self.setToolTip("Drag the end handles to adjust the shared scientific color range.")

    @property
    def levels(self) -> tuple[float, float]:
        return self._levels

    def set_mapping(self, rgba: np.ndarray, span: tuple[float, float],
                    levels: tuple[float, float], title: str) -> None:
        low, high = map(float, span)
        level_low, level_high = map(float, levels)
        if not np.isfinite([low, high, level_low, level_high]).all() or low >= high:
            return
        self._rgba = np.asarray(rgba, dtype=np.uint8).copy()
        self._span = (low, high)
        self._levels = (max(low, level_low), min(high, level_high))
        if self._levels[0] >= self._levels[1]:
            self._levels = (low, high)
        self._title = str(title)
        self.update()

    def _x_for_value(self, value: float, rect) -> float:
        low, high = self._span
        return rect.left() + (value - low) / (high - low) * rect.width()

    def _value_for_x(self, x: float, rect) -> float:
        low, high = self._span
        return low + np.clip((x - rect.left()) / max(1, rect.width()), 0.0, 1.0) * (high - low)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        bar = self.rect().adjusted(36, 13, -36, -11)
        if bar.width() < 2 or bar.height() < 3:
            return
        gradient = QLinearGradient(bar.left(), 0, bar.right(), 0)
        count = max(1, len(self._rgba) - 1)
        for index, rgba in enumerate(self._rgba):
            gradient.setColorAt(index / count, QColor(*[int(value) for value in rgba]))
        painter.fillRect(bar, gradient)
        painter.setPen(QPen(QColor(SURFACE_3D["colorbar_outline"]), 1))
        painter.drawRect(bar)
        painter.setPen(QColor(SURFACE_3D["colorbar_text"]))
        painter.drawText(QRect(2, 0, self.width() - 4, 12), Qt.AlignmentFlag.AlignCenter, self._title)
        low, high = self._levels
        for value, label in ((low, f"{low:.5g}"), (high, f"{high:.5g}")):
            x = int(round(self._x_for_value(value, bar)))
            triangle = QPolygon([
                QPoint(x, bar.bottom() + 1),
                QPoint(x - 5, self.height() - 1),
                QPoint(x + 5, self.height() - 1),
            ])
            painter.setBrush(QColor(SURFACE_3D["colorbar_handle"]))
            painter.drawPolygon(triangle)
            painter.drawText(QRect(max(0, x - 42), bar.bottom() + 2, 84, 11),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, label)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API spelling
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        bar = self.rect().adjusted(36, 13, -36, -11)
        x = event.position().x()
        low_x = self._x_for_value(self._levels[0], bar)
        high_x = self._x_for_value(self._levels[1], bar)
        self._drag_handle = "minimum" if abs(x - low_x) <= abs(x - high_x) else "maximum"
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API spelling
        if self._drag_handle is None:
            return super().mouseMoveEvent(event)
        bar = self.rect().adjusted(36, 13, -36, -11)
        value = self._value_for_x(event.position().x(), bar)
        gap = max(np.finfo(float).eps, (self._span[1] - self._span[0]) * 1e-6)
        if self._drag_handle == "minimum":
            low = min(value, self._levels[1] - gap)
            levels = (max(self._span[0], low), self._levels[1])
        else:
            high = max(value, self._levels[0] + gap)
            levels = (self._levels[0], min(self._span[1], high))
        if levels != self._levels:
            self._levels = levels
            self.update()
            self.levels_changed.emit(*levels)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API spelling
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_handle = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SurfaceRenderer(QWidget):
    """A persistent Q3DSurface scene fed with already transformed Grid2DData.

    The Qt Data Visualization package is loaded only when this widget is
    created. Camera movements modify only Q3DCamera; the Surface proxy is reset
    when scientific grids or geometry policy change. Camera, projection,
    vertical exaggeration, and color mapping have independent update paths.
    """

    surface_ready = Signal(object)
    surface_failed = Signal(str)
    color_range_changed = Signal(float, float)
    point_render_stage = Signal(str)
    export_context_requested = Signal(QPoint)
    share_drag_requested = Signal()

    def __init__(self, parent=None, localizer: LocalizationManager | None = None):
        super().__init__(parent)
        self.localizer = localizer or get_localization_manager()
        if not probe_opengl_context():
            raise RuntimeError("No compatible OpenGL context is available on this platform.")
        from PySide6.QtDataVisualization import (
            Q3DCamera, Q3DSurface, Q3DTheme, QCustom3DItem, QSurface3DSeries, QSurfaceDataItem,
            QSurfaceDataProxy, QValue3DAxisFormatter,
        )

        class PhysicalAxisFormatter(QValue3DAxisFormatter):
            def __init__(self, mapping: NormalizedAxis, parent=None):
                super().__init__(parent)
                self.mapping = mapping

            def stringForValue(self, value, _format):  # noqa: N802 - Qt API spelling
                return f"{self.mapping.physical_value(float(value)):.6g}"

        self._QSurfaceDataItem = QSurfaceDataItem
        self._Q3DCamera = Q3DCamera
        self._Q3DTheme = Q3DTheme
        self._PhysicalAxisFormatter = PhysicalAxisFormatter
        self._QSurface3DSeries = QSurface3DSeries
        self.graph = Q3DSurface()
        self.active_graph = self.graph
        self.graph.setObjectName("labLogViewerSurfaceGraph")
        self.graph.setMeasureFps(True)
        configure_scientific_3d_scene(self.graph, Q3DTheme)

        self.proxy = QSurfaceDataProxy()
        self.series = QSurface3DSeries(self.proxy)
        configure_filled_surface(self.series)
        self.series.setItemLabelFormat("@xLabel, @zLabel: @yLabel")
        self.series.setItemLabelVisible(False)
        self.series.setColorStyle(Q3DTheme.ColorStyle.ColorStyleRangeGradient)
        self.series.setBaseColor(QColor(SURFACE_3D["white"]))
        self.graph.addSeries(self.series)
        self._secondary_proxy = QSurfaceDataProxy()
        self.secondary_series = QSurface3DSeries(self._secondary_proxy)
        configure_filled_surface(self.secondary_series)
        self.secondary_series.setColorStyle(Q3DTheme.ColorStyle.ColorStyleUniform)
        self.secondary_series.setBaseColor(QColor(QColor(*SURFACE_3D["dual_surface_b"]).name()))
        self.secondary_series.setItemLabelVisible(False)
        self.secondary_series.setVisible(False)
        self.graph.addSeries(self.secondary_series)
        self._projection_proxy = QSurfaceDataProxy()
        self.projection_series = QSurface3DSeries(self._projection_proxy)
        configure_filled_surface(self.projection_series)
        self.projection_series.setItemLabelVisible(False)
        self.projection_series.setVisible(False)
        self.graph.addSeries(self.projection_series)
        plane_texture = QImage(2, 2, QImage.Format.Format_RGBA8888)
        plane_texture.fill(QColor(*SURFACE_3D["legacy_reference_plane"]))
        plane_mesh = Path(__file__).with_name("reference_plane.obj")
        if not plane_mesh.is_file():
            raise RuntimeError("The 3D Reference Plane mesh asset is missing from this installation.")
        self.reference_plane_item = QCustom3DItem(
            str(plane_mesh),
            QVector3D(0.5, 0.5, 0.5), QVector3D(1.0, 0.001, 1.0),
            QQuaternion(), plane_texture,
        )
        self.reference_plane_item.setScalingAbsolute(False)
        self.reference_plane_item.setShadowCasting(False)
        self.reference_plane_item.setVisible(False)
        self.graph.addCustomItem(self.reference_plane_item)
        self.graph.setSelectionMode(self.graph.SelectionFlag.SelectionItem)
        self._input_handler = self.graph.activeInputHandler()
        configure_surface_input_handler(self._input_handler)
        self.series.selectedPointChanged.connect(self._on_selected_point_changed)

        self.container = QWidget.createWindowContainer(self.graph, self)
        self.container.setObjectName("surface3DViewport")
        self.container.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.container.setMinimumSize(240, 180)
        self.surface_container = self.container
        self.graph_stack = QStackedWidget()
        self.graph_stack.addWidget(self.container)
        self.scatter_graph = None
        self.scatter_container = None
        self._scatter_series: list[object] = []
        self._scatter_proxies: list[object] = []
        self._scatter_projection_series: list[object] = []
        self._scatter_projection_proxies: list[object] = []
        self._scatter_group_indices: list[np.ndarray] = []
        self._point_reference_item = None
        self._point_generation = 0
        self._point_first_frame_generation = -1
        self._point_submitted_generation = -1
        self._share_mode = False
        self._point_cloud = None
        self._point_axis_mappings = None
        self.color_title = QLabel()
        self.color_title.setWordWrap(True)
        self.color_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.color_high = QLabel("—")
        self.color_high.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.color_low = QLabel("—")
        self.color_low.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.color_ramp = _ColorRamp()
        self.horizontal_colorbar = InteractiveColorbar()
        self.horizontal_colorbar.hide()
        self.horizontal_colorbar.levels_changed.connect(self.color_range_changed)
        colorbar_layout = QVBoxLayout()
        colorbar_layout.setContentsMargins(2, 2, 2, 2)
        colorbar_layout.addWidget(self.color_title)
        colorbar_layout.addWidget(self.color_high)
        colorbar_layout.addWidget(self.color_ramp, 1)
        colorbar_layout.addWidget(self.color_low)
        colorbar = QWidget()
        colorbar.setLayout(colorbar_layout)
        self.colorbar_widget = colorbar
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.reference_label = QLabel()
        self.reference_label.setWordWrap(True)
        self.pick_label = QLabel()
        self.pick_label.setWordWrap(True)
        self.pick_label.setMaximumHeight(48)
        self.pick_label.setStyleSheet(
            "QLabel {{ color: {text}; background-color: {background}; "
            "border: 1px solid {border}; padding: 3px 6px; }}".format(**SURFACE_3D["pick_label"])
        )
        self.pick_label.setText(self.localizer.text("viewer.surface_pick_hint"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.horizontal_colorbar)
        viewport_row = QHBoxLayout()
        viewport_row.setContentsMargins(0, 0, 0, 0)
        viewport_row.addWidget(self.graph_stack, 1)
        viewport_row.addWidget(colorbar)
        layout.addLayout(viewport_row, 1)
        layout.addWidget(self.reference_label)
        layout.addWidget(self.pick_label)
        layout.addWidget(self.status_label)

        self._grid: SurfaceGrid | None = None
        self._color_follows_height = True
        self._source_height_grid = None
        self._source_color_grid = None
        self._axis_mappings: tuple[NormalizedAxis, NormalizedAxis, NormalizedAxis] | None = None
        self._z_scale = 1.0
        self._opacity = 1.0
        self._projection_enabled = False
        self._projection_opacity = 1.0
        self._reference_mode = "off"
        self._reference_value = 0.0
        self._secondary_grid: SurfaceGrid | None = None
        self._secondary_opacity = 0.55
        self._shared_height_limits: tuple[float, float] | None = None
        self._geometry_type = "Surface"
        self._waterfall_data = None
        self._colormap_name = "LabLog BWR"
        self._color_range: tuple[float, float] | None = None
        self._color_texture: QImage | None = None
        self._surface_gradient: QLinearGradient | None = None
        self._surface_color_mode = "range-gradient"
        self._rendering_decision: RenderingDecision | None = None
        self._mesh_rebuild_count = 0
        self._camera_update_count = 0
        self._last_grid_prepare_ms: float | None = None
        self._last_item_build_ms: float | None = None
        self._last_proxy_reset_ms: float | None = None
        self._lod_cache = BoundedSurfaceCache()
        self._lod_generation = 0
        self._source_key = None
        self._idle_budget: int | None = None
        self._active_budget: int | None = None
        self._interaction_active = False
        self._lod_pending: set[tuple[object, int]] = set()
        self._initial_request = None
        self._lod_signals = _LodSignals()
        self._lod_signals.ready.connect(self._on_lod_ready)
        self._initial_signals = _LodSignals()
        self._initial_signals.ready.connect(self._on_initial_ready)
        self._lod_pool = QThreadPool.globalInstance()
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(180)
        self._idle_timer.timeout.connect(self._restore_idle_lod)
        self._interaction_filter = _SurfaceInteractionFilter(self)
        self.graph.installEventFilter(self._interaction_filter)
        renderer_id = id(self)
        self.destroyed.connect(
            lambda _object=None, rid=renderer_id: clear_renderer_diagnostics(rid)
        )
        self.graph.currentFpsChanged.connect(self._publish_diagnostics)
        self.graph.activeThemeChanged.connect(self._publish_diagnostics)
        self._publish_diagnostics()

    def apply_scientific_plot_appearance(self, colors) -> None:
        """Update 3D scene chrome only; keep geometry and scientific colors intact."""
        graphs = [self.graph]
        if self.active_graph is not self.graph:
            graphs.append(self.active_graph)
        if self.scatter_graph is not None:
            if all(self.scatter_graph is not graph for graph in graphs):
                graphs.append(self.scatter_graph)
        for graph in graphs:
            theme = graph.activeTheme()
            theme.setBackgroundColor(QColor(colors.background))
            theme.setWindowColor(QColor(colors.panel))
            theme.setLabelTextColor(QColor(colors.text))
            theme.setGridLineColor(QColor(colors.border))
        self.pick_label.setStyleSheet(
            f"QLabel {{ color: {colors.text}; background-color: {colors.panel}; "
            f"border: 1px solid {colors.border}; padding: 3px 6px; }}"
        )
        self.colorbar_widget.setObjectName("surfaceColorbar")
        self.colorbar_widget.setStyleSheet(
            f"QWidget#surfaceColorbar {{ background-color: {colors.background}; }}"
            f"QWidget#surfaceColorbar QLabel {{ color: {colors.text}; background: transparent; }}"
        )
        # Q3DSurface is a QWindow: it has requestUpdate(), not QWidget.update().
        self.graph.requestUpdate()
        if self.scatter_graph is not None:
            self.scatter_graph.requestUpdate()

    @property
    def mesh_rebuild_count(self) -> int:
        return self._mesh_rebuild_count

    @property
    def camera_update_count(self) -> int:
        return self._camera_update_count

    @property
    def surface_grid(self) -> SurfaceGrid | None:
        return self._grid

    @property
    def color_range(self) -> tuple[float, float] | None:
        return self._color_range

    @property
    def rendering_decision(self) -> RenderingDecision | None:
        return self._rendering_decision

    @property
    def is_preparing(self) -> bool:
        return self._initial_request is not None

    def clear(self) -> None:
        had_point_scene = self._point_cloud is not None
        if had_point_scene:
            self.point_render_stage.emit(f"3D {self._geometry_type} scene cleanup started")
        self._point_generation += 1
        self._lod_generation += 1
        self._lod_pending.clear()
        self._initial_request = None
        self._lod_cache.clear()
        self._idle_timer.stop()
        self._interaction_active = False
        self._source_key = None
        if self.active_graph is not self.graph:
            self._switch_active_graph(self.graph, self.surface_container)
        self.series.setVisible(False)
        for point_series in self._scatter_series:
            point_series.setVisible(False)
        self._point_cloud = None
        self._grid = None
        self._surface_gradient = None
        self._source_height_grid = None
        self._source_color_grid = None
        self._axis_mappings = None
        self._rendering_decision = None
        self._color_range = None
        self._color_texture = None
        self._waterfall_data = None
        self._secondary_grid = None
        self.secondary_series.setVisible(False)
        self.projection_series.setVisible(False)
        self.reference_plane_item.setVisible(False)
        self.pick_label.setText(self.localizer.text("viewer.surface_pick_hint"))
        self.reference_label.clear()
        self.color_title.clear()
        self.color_high.setText("—")
        self.color_low.setText("—")
        self.status_label.clear()
        if had_point_scene:
            self.point_render_stage.emit(f"3D {self._geometry_type} scene cleanup completed")
        self._publish_diagnostics()

    def retranslate(self) -> None:
        if self._grid is not None:
            self._set_status_text()
        if self.series.selectedPoint() == self._QSurface3DSeries.invalidSelectionPosition():
            self.pick_label.setText(self.localizer.text("viewer.surface_pick_hint"))

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API spelling
        super().showEvent(event)
        self._schedule_color_refresh()
        QTimer.singleShot(80, self._refresh_surface_colors_after_expose)

    def set_grid(self, grid, *, color_grid=None, colormap: str = "LabLog BWR",
                 color_range: tuple[float, float] | None = None,
                 rendering_policy: RenderingPolicy | str = RenderingPolicy.AUTO,
                 shared_height_limits: tuple[float, float] | None = None,
                 _prepared: SurfaceGrid | None = None) -> SurfaceGrid:
        old_axes = None
        if self._grid is not None:
            old_axes = (self._grid.x_name, self._grid.y_name, self._grid.source_shape)
        self._source_height_grid = grid
        self._color_follows_height = color_grid is None or color_grid is grid
        self._source_color_grid = grid if color_grid is None else color_grid
        self._colormap_name = colormap
        self._color_range = color_range
        self._shared_height_limits = shared_height_limits
        configure_filled_surface(self.series)
        if getattr(self, "_geometry_type", "Surface") == "Waterfall":
            self.series.setDrawMode(self._QSurface3DSeries.DrawFlag.DrawWireframe)
        shape = np.shape(grid.z_values)
        self._rendering_decision = resolve_rendering_policy(shape[0], shape[1], rendering_policy)
        if (rendering_policy == RenderingPolicy.FULL_RESOLUTION.value
                or rendering_policy is RenderingPolicy.FULL_RESOLUTION) and shape[0] * shape[1] > 2_000_000:
            raise ValueError(
                "Full Resolution exceeds the safe Qt surface budget (2M vertices); "
                "choose Adaptive LOD. Source scientific data remains complete."
            )
        source_key = (
            id(grid.z_values), id(self._source_color_grid.z_values), shape,
            grid.x_name, grid.y_name, grid.z_name, getattr(grid, "transform", "raw"),
            self._source_color_grid.z_name,
            getattr(self._source_color_grid, "transform", "raw"),
            shared_height_limits,
        )
        if source_key != self._source_key:
            self._lod_generation += 1
            self._lod_pending.clear()
            self._lod_cache.clear()
            self._source_key = source_key
        self._idle_timer.stop()
        self._interaction_active = False
        self._idle_budget = self._rendering_decision.max_vertices
        initial_budget = self._idle_budget
        if shape[0] * shape[1] > 1_000_000 and initial_budget is not None:
            initial_budget = min(initial_budget, 50_000)
        cache_key = (source_key, initial_budget)
        surface_grid = self._lod_cache.get(cache_key)
        if surface_grid is None:
            prepare_started = time.perf_counter()
            surface_grid = _prepared or prepare_surface_grid(
                grid, color_grid=self._source_color_grid, max_vertices=initial_budget,
            )
            self._last_grid_prepare_ms = (time.perf_counter() - prepare_started) * 1000
            self._lod_cache.put(cache_key, surface_grid)
        else:
            self._last_grid_prepare_ms = 0.0
        self._active_budget = initial_budget
        self._apply_prepared_grid(surface_grid)
        axes_changed = old_axes is None or old_axes != (
            surface_grid.x_name, surface_grid.y_name, surface_grid.source_shape
        )
        if axes_changed:
            self.view_all()
        if self._idle_budget != initial_budget:
            self._request_lod(self._idle_budget)
        self._request_lod(interaction_vertex_budget(
            shape[0] * shape[1], (self.container.width(), self.container.height())
        ))
        self._publish_diagnostics()
        return surface_grid

    def set_geometry_type(self, geometry: str) -> None:
        geometry = str(geometry)
        if geometry not in {"Surface", "Dual Surface", "Waterfall", "Trajectory", "Scatter"}:
            raise ValueError(f"Unknown 3D geometry type: {geometry!r}.")
        if geometry in {"Trajectory", "Scatter"}:
            self._lod_generation += 1
            self._lod_pending.clear()
            self._initial_request = None
            self._idle_timer.stop()
            self._interaction_active = False
            self._ensure_scatter_graph()
            self._switch_active_graph(self.scatter_graph, self.scatter_container)
            self._geometry_type = geometry
            return
        self._switch_active_graph(self.graph, self.surface_container)
        self._geometry_type = geometry
        if geometry == "Waterfall":
            self.series.setDrawMode(self._QSurface3DSeries.DrawFlag.DrawWireframe)
        else:
            configure_filled_surface(self.series)
        if geometry != "Dual Surface":
            self.clear_secondary_grid()
        if geometry == "Waterfall":
            self.reference_plane_item.setVisible(False)
        if geometry != "Surface":
            self.projection_series.setVisible(False)
        elif self._grid is not None:
            self._refresh_overlay_geometries()

    def _switch_active_graph(self, graph, container) -> None:
        if graph is None or container is None or self.active_graph is graph:
            return
        old_camera = self.active_graph.scene().activeCamera()
        x_rotation, y_rotation, zoom = (
            float(old_camera.xRotation()), float(old_camera.yRotation()), float(old_camera.zoomLevel())
        )
        target = old_camera.target()
        old_projection = bool(self.active_graph.isOrthoProjection())
        self.active_graph = graph
        self.container = container
        self.graph_stack.setCurrentWidget(container)
        camera = graph.scene().activeCamera()
        _apply_camera_pose(camera, x_rotation, y_rotation, zoom, target)
        graph.setOrthoProjection(old_projection)
        graph.installEventFilter(self._interaction_filter)
        if graph is self.graph:
            self._schedule_color_refresh()

    def _ensure_scatter_graph(self) -> None:
        if self.scatter_graph is not None:
            return
        from PySide6.QtDataVisualization import (
            Q3DScatter, Q3DTheme, QAbstract3DSeries, QScatter3DSeries,
            QScatterDataItem, QScatterDataProxy,
        )

        self.scatter_graph = Q3DScatter()
        self.scatter_graph.setObjectName("labLogViewerPointGraph")
        self.scatter_graph.setMeasureFps(True)
        self.scatter_graph.currentFpsChanged.connect(self._on_point_fps_changed)
        configure_scientific_3d_scene(self.scatter_graph, Q3DTheme)
        self.scatter_graph.setSelectionMode(self.scatter_graph.SelectionFlag.SelectionItem)
        self.scatter_graph.setOptimizationHints(self.scatter_graph.OptimizationHint.OptimizationStatic)
        handler = self.scatter_graph.activeInputHandler()
        configure_surface_input_handler(handler)
        self.scatter_container = QWidget.createWindowContainer(self.scatter_graph, self)
        self.scatter_container.setObjectName("scientific3DPointViewport")
        self.scatter_container.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scatter_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.scatter_container.setMinimumSize(240, 180)
        self.graph_stack.addWidget(self.scatter_container)
        self._QScatter3DSeries = QScatter3DSeries
        self._QScatterDataItem = QScatterDataItem
        self._QScatterDataProxy = QScatterDataProxy
        self._QAbstract3DSeries = QAbstract3DSeries
        self._Q3DTheme = Q3DTheme
        from PySide6.QtDataVisualization import QCustom3DItem
        plane_texture = QImage(2, 2, QImage.Format.Format_RGBA8888)
        plane_texture.fill(QColor(*SURFACE_3D["legacy_point_plane"]))
        self._point_reference_item = QCustom3DItem(
            str(Path(__file__).with_name("reference_plane.obj")),
            QVector3D(0.5, 0.5, 0.5), QVector3D(1.0, 0.001, 1.0), QQuaternion(), plane_texture,
        )
        self._point_reference_item.setScalingAbsolute(False)
        self._point_reference_item.setShadowCasting(False)
        self._point_reference_item.setVisible(False)
        self.scatter_graph.addCustomItem(self._point_reference_item)

    def set_waterfall_grid(self, prepared, **kwargs) -> SurfaceGrid:
        """Render separated trace lines using the persistent Surface graph."""
        self._waterfall_data = prepared
        self.set_geometry_type("Waterfall")
        return self.set_grid(
            prepared.grid, color_grid=prepared.color_grid,
            shared_height_limits=None, **kwargs,
        )

    def set_point_cloud(
        self, cloud, *, geometry: str, axis_labels: tuple[tuple[str, str | None], ...],
        color_label: str = "Color", color_range: tuple[float, float] | None = None,
        colormap: str = "LabLog BWR", point_size: float = 0.018,
    ) -> None:
        """Display prepared scientific XYZ samples through Qt's native 3D point graph."""
        if geometry not in {"Trajectory", "Scatter"}:
            raise ValueError("Point-cloud data can only be rendered as Trajectory or Scatter.")
        if len(axis_labels) != 3 or cloud.coordinates.ndim != 2 or cloud.coordinates.shape[1] != 3:
            raise ValueError("3D point data must contain three mapped coordinates.")
        if (len(cloud.coordinates) < (2 if geometry == "Trajectory" else 1)
                or len(cloud.coordinates) > 20_000
                or cloud.coordinates.dtype.kind not in "iuf"
                or len(cloud.color_values) != len(cloud.coordinates)
                or not np.isfinite(cloud.coordinates).all()
                or not np.isfinite(cloud.color_values).all()):
            raise ValueError("3D point data must be finite, real-valued and within the renderer budget.")
        if color_range is not None and (not np.isfinite(color_range).all()
                                        or color_range[0] >= color_range[1]):
            raise ValueError("3D Color Range must have finite Minimum < Maximum values.")
        self._point_generation += 1
        generation = self._point_generation
        self.point_render_stage.emit(f"3D {geometry} geometry update requested")
        self.set_geometry_type(geometry)
        graph = self.scatter_graph
        self.scatter_graph.setMeasureFps(True)
        axis_mappings = []
        for axis_index, (axis, (name, unit)) in enumerate(zip(
            (graph.axisX(), graph.axisY(), graph.axisZ()), axis_labels
        )):
            mapping = normalize_axis_for_scene(cloud.coordinates[:, axis_index])
            if axis_index == 2:
                scaled = 0.5 + (mapping.values - 0.5) * self._z_scale
                mapping = NormalizedAxis(
                    scaled, mapping.physical_min, mapping.physical_max,
                    0.5 - 0.5 * self._z_scale, 0.5 + 0.5 * self._z_scale,
                )
            axis.setTitle(_axis_title(name, unit, "XYZ"[axis_index]))
            axis.setTitleVisible(True)
            formatter = axis.formatter()
            if isinstance(formatter, self._PhysicalAxisFormatter):
                formatter.mapping = mapping
            else:
                axis.setFormatter(self._PhysicalAxisFormatter(mapping, axis))
            axis.setRange(mapping.display_min, mapping.display_max)
            axis_mappings.append(mapping)
        self._point_axis_mappings = tuple(axis_mappings)
        positions = np.column_stack([mapping.values for mapping in axis_mappings])
        if not np.isfinite(point_size) or not 0.001 <= point_size <= 0.2:
            raise ValueError("Point size must be between 0.001 and 0.2.")
        low, high = color_range or robust_color_limits(cloud.color_values)
        if low >= high:
            delta = max(abs(low) * 1e-9, 1e-12)
            low, high = low - delta, high + delta
        rgba = np.asarray(get_colormap(colormap).map(
            np.clip((cloud.color_values - low) / (high - low), 0.0, 1.0), mode="byte"
        ), dtype=np.uint8)
        self._point_cloud = cloud
        for group in range(len(self._scatter_group_indices)):
            self._scatter_group_indices[group] = np.empty(0, dtype=np.intp)
        bucket_count = min(16, max(1, len(positions)))
        buckets = np.minimum((np.clip((cloud.color_values - low) / (high - low), 0.0, 1.0)
                              * bucket_count).astype(np.intp), bucket_count - 1)
        # Keep native series/proxies attached for the lifetime of the graph. Qt may
        # still be drawing the previous frame when another update is submitted.
        while len(self._scatter_series) < bucket_count:
            bucket = len(self._scatter_series)
            proxy = self._QScatterDataProxy()
            series = self._QScatter3DSeries(proxy)
            # Keep our own proxy reference: PySide can return a mistyped wrapper
            # from series.dataProxy() ("QScatter3DSeries has no resetArray").
            self._scatter_proxies.append(proxy)
            series.setColorStyle(self._Q3DTheme.ColorStyle.ColorStyleUniform)
            # MeshPoint draws nothing on macOS OpenGL (verified); low-poly spheres do.
            series.setMesh(self._QAbstract3DSeries.Mesh.MeshSphere)
            series.setMeshSmooth(False)
            series.selectedItemChanged.connect(
                lambda item, group=bucket: self._on_scatter_selected(group, int(item))
            )
            self.scatter_graph.addSeries(series)
            self._scatter_series.append(series)
            self._scatter_group_indices.append(np.empty(0, dtype=np.intp))
        self.point_render_stage.emit(f"3D {geometry} geometry attached to scene")
        for bucket in range(bucket_count):
            indices = np.flatnonzero(buckets == bucket)
            series = self._scatter_series[bucket]
            self._scatter_group_indices[bucket] = indices
            if indices.size == 0:
                series.setVisible(False)
                continue
            color = QColor(*[int(channel) for channel in rgba[indices[0]]])
            series.setName(f"{color_label} bin {bucket + 1}")
            series.setBaseColor(color)
            series.setItemSize(float(point_size))
            items = [self._QScatterDataItem(QVector3D(*positions[index].tolist())) for index in indices]
            self._scatter_proxies[bucket].resetArray(items)
            series.setVisible(True)
        for series in self._scatter_series[bucket_count:]:
            series.setVisible(False)
        self._point_bucket_colors = [QColor(*[int(c) for c in rgba[idx[0]]]) if idx.size else None
                                     for idx in self._scatter_group_indices[:bucket_count]]
        self._point_coordinate_labels = axis_labels
        self._point_color_label = color_label
        self._point_color_range = (float(low), float(high))
        self._point_positions = positions
        self._point_size = float(point_size)
        self._update_point_overlays()
        self._color_range = (float(low), float(high))
        self._colormap_name = colormap
        color_map = get_colormap(colormap)
        ramp = np.asarray(color_map.map(np.linspace(0.0, 1.0, 256), mode="byte"))
        self.color_ramp.set_colors(ramp)
        self.color_title.setText(color_label)
        self.color_high.setText(f"{high:.5g}")
        self.color_low.setText(f"{low:.5g}")
        span = (float(np.min(cloud.color_values)), float(np.max(cloud.color_values)))
        if span[0] >= span[1]:
            span = (float(low), float(high))
        self.horizontal_colorbar.set_mapping(ramp, span, (float(low), float(high)), color_label)
        self._point_grid_rebuild_count = getattr(self, "_point_grid_rebuild_count", 0) + 1
        self.view_all()
        self.pick_label.setText(
            f"{geometry}: {len(cloud.coordinates):,} rendered samples from {int(np.prod(cloud.source_shape)):,} source points."
        )
        self.graph_stack.setCurrentWidget(self.scatter_container)
        self._point_submitted_generation = generation
        self.point_render_stage.emit(f"3D {geometry} render update submitted")
        QTimer.singleShot(0, self, lambda: self._point_scene_checkpoint(generation, "Qt event processing reached"))
        QTimer.singleShot(1500, self, lambda: self._point_scene_checkpoint(generation, "scene remained alive"))
        self._publish_diagnostics()

    def _point_scene_checkpoint(self, generation: int, stage: str) -> None:
        if generation == self._point_generation and self.active_graph is self.scatter_graph:
            self.point_render_stage.emit(f"3D {self._geometry_type} {stage}")

    def _on_point_fps_changed(self, fps: float) -> None:
        if (fps > 0 and self._point_submitted_generation == self._point_generation
                and self.active_graph is self.scatter_graph
                and self._point_first_frame_generation != self._point_generation):
            self._point_first_frame_generation = self._point_generation
            self.point_render_stage.emit(f"3D {self._geometry_type} first measured frame reached")
        self._publish_diagnostics()

    def set_point_color_settings(self, *, color_range=None, colormap: str | None = None) -> None:
        if self._point_cloud is None:
            return
        state = self.camera_state()
        self.set_point_cloud(
            self._point_cloud, geometry=self._geometry_type,
            axis_labels=self._point_coordinate_labels,
            color_label=self._point_color_label,
            color_range=color_range or self._point_color_range,
            colormap=colormap or self._colormap_name,
            point_size=self._point_size,
        )
        self.apply_camera_state(state)

    def _on_scatter_selected(self, group: int, item_index: int) -> None:
        if self._point_cloud is None or not 0 <= group < len(self._scatter_group_indices):
            return
        indices = self._scatter_group_indices[group]
        if not 0 <= item_index < len(indices):
            return
        index = int(indices[item_index])
        xyz = self._point_cloud.coordinates[index]
        color = self._point_cloud.color_values[index]
        labels = self._point_coordinate_labels
        values = [f"{name}: {value:.6g}" + (f" {unit}" if unit else "")
                  for (name, unit), value in zip(labels, xyz)]
        values.append(f"{self._point_color_label}: {color:.6g}")
        values.append(f"Source point: {int(self._point_cloud.source_indices[index])}")
        self.pick_label.setText("  |  ".join(values))

    def set_grid_async(self, grid, *, color_grid=None, colormap: str = "LabLog BWR",
                       color_range: tuple[float, float] | None = None,
                       rendering_policy: RenderingPolicy | str = RenderingPolicy.AUTO) -> None:
        """Prepare a large initial LOD in a worker; apply Qt state on the GUI thread."""
        self._lod_generation += 1
        generation = self._lod_generation
        color_grid = grid if color_grid is None else color_grid
        self.series.setVisible(False)
        self._grid = None
        token = ("initial", id(grid.z_values), id(color_grid.z_values))
        self._initial_request = (
            generation, token, grid, color_grid, colormap, color_range, rendering_policy,
        )
        self._lod_pool.start(_LodJob(
            self._initial_signals, generation, token, grid, color_grid, 50_000,
        ))

    def _on_initial_ready(self, generation: int, token: object,
                          _budget: int, prepared: object) -> None:
        request = self._initial_request
        if request is None or request[0] != generation or request[1] != token:
            return
        self._initial_request = None
        if isinstance(prepared, Exception):
            self.surface_failed.emit(str(prepared))
            return
        _, _, grid, color_grid, colormap, color_range, policy = request
        try:
            result = self.set_grid(
                grid, color_grid=color_grid, colormap=colormap,
                color_range=color_range, rendering_policy=policy, _prepared=prepared.grid,
            )
            self._last_grid_prepare_ms = prepared.elapsed_ms
        except Exception as error:
            self.surface_failed.emit(str(error))
            return
        self.surface_ready.emit(result)

    def _reset_mesh(self, surface_grid: SurfaceGrid) -> None:
        mesh = prepare_surface_mesh(
            surface_grid, z_scale=self._z_scale, z_limits=self._shared_height_limits
        )
        item_started = time.perf_counter()
        coordinates = mesh.coordinates
        item = self._QSurfaceDataItem
        rows = []
        for iy in range(surface_grid.shape[0]):
            coords = coordinates[iy]
            rows.append([
                item(QVector3D(*point.tolist()))
                for point in coords
            ])
        self._last_item_build_ms = (time.perf_counter() - item_started) * 1000
        reset_started = time.perf_counter()
        self.proxy.resetArray(rows)
        self._last_proxy_reset_ms = (time.perf_counter() - reset_started) * 1000
        self._axis_mappings = (mesh.x_axis, mesh.y_axis, mesh.z_axis)
        self._mesh_rebuild_count += 1
        self._set_axes(surface_grid, *self._axis_mappings)
        self._refresh_overlay_geometries(mesh)

    def _apply_prepared_grid(self, surface_grid: SurfaceGrid) -> None:
        self.series.setVisible(False)
        self._grid = surface_grid
        self._reset_mesh(surface_grid)
        self._set_color_mapping()
        self.series.setVisible(True)
        self._set_status_text()

    def _request_lod(self, budget: int | None) -> None:
        if budget is None or self._source_key is None or self._source_height_grid is None:
            return
        key = (self._source_key, budget)
        if self._lod_cache.get(key) is not None or key in self._lod_pending:
            return
        self._lod_pending.add(key)
        self._lod_pool.start(_LodJob(
            self._lod_signals, self._lod_generation, self._source_key,
            self._source_height_grid, self._source_color_grid, budget,
        ))

    def _on_lod_ready(self, generation: int, source_key: object,
                      budget: int, prepared: object) -> None:
        key = (source_key, budget)
        self._lod_pending.discard(key)
        if generation != self._lod_generation or source_key != self._source_key:
            return
        if isinstance(prepared, Exception):
            self.status_label.setText(f"3D LOD preparation failed: {prepared}")
            return
        self._last_grid_prepare_ms = prepared.elapsed_ms
        grid = prepared.grid
        self._lod_cache.put(key, grid)
        if self._interaction_active:
            wanted = interaction_vertex_budget(
                int(np.prod(grid.source_shape)),
                (self.container.width(), self.container.height()),
                _camera_graph(self).scene().activeCamera().zoomLevel(), _camera_graph(self).currentFps(),
            )
            if budget == wanted and budget != self._active_budget:
                self._active_budget = budget
                self._apply_prepared_grid(grid)
        elif budget == self._idle_budget and budget != self._active_budget:
            self._active_budget = budget
            self._apply_prepared_grid(grid)
            self.surface_ready.emit(grid)
        self._publish_diagnostics()

    def _interaction_start(self) -> None:
        if getattr(self, "_geometry_type", "Surface") in {"Trajectory", "Scatter"}:
            self._idle_timer.stop()
            self._interaction_active = True
            return
        if self._grid is None or self._source_key is None:
            return
        self._idle_timer.stop()
        self._interaction_active = True
        budget = interaction_vertex_budget(
            int(np.prod(self._grid.source_shape)),
            (self.container.width(), self.container.height()),
            _camera_graph(self).scene().activeCamera().zoomLevel(), _camera_graph(self).currentFps(),
        )
        if budget >= int(np.prod(self._grid.source_shape)):
            return
        prepared = self._lod_cache.get((self._source_key, budget))
        if prepared is None:
            self._request_lod(budget)
        elif budget != self._active_budget:
            self._active_budget = budget
            self._apply_prepared_grid(prepared)

    def _interaction_end(self) -> None:
        if self._interaction_active:
            self._idle_timer.start()

    def _restore_idle_lod(self) -> None:
        self._interaction_active = False
        if self._source_key is None or self._active_budget == self._idle_budget:
            return
        prepared = self._lod_cache.get((self._source_key, self._idle_budget))
        if prepared is None:
            self._request_lod(self._idle_budget)
            return
        self._active_budget = self._idle_budget
        self._apply_prepared_grid(prepared)
        self._publish_diagnostics()

    def set_color_grid(self, color_grid) -> None:
        """Replace only the color field; leave surface vertices and camera intact."""
        if self._source_height_grid is None:
            raise ValueError("Set a Height grid before the Color Source.")
        previous = self._grid
        if previous is None:
            return
        updated = prepare_surface_grid(
            self._source_height_grid, color_grid=color_grid,
            indices=(previous.source_row_indices, previous.source_column_indices),
        )
        self._lod_generation += 1
        self._lod_pending.clear()
        self._lod_cache.clear()
        self._source_key = (
            id(self._source_height_grid.z_values), id(color_grid.z_values),
            np.shape(self._source_height_grid.z_values),
            self._source_height_grid.x_name, self._source_height_grid.y_name,
            self._source_height_grid.z_name,
            getattr(self._source_height_grid, "transform", "raw"),
            color_grid.z_name, getattr(color_grid, "transform", "raw"),
        )
        self._lod_cache.put((self._source_key, self._active_budget), updated)
        self._color_follows_height = color_grid is self._source_height_grid
        self._source_color_grid = color_grid
        self._grid = updated
        self._set_color_mapping()
        self._set_status_text()
        if self._idle_budget != self._active_budget:
            self._request_lod(self._idle_budget)
        self._publish_diagnostics()

    def _set_color_mapping(self) -> None:
        if self._grid is None:
            return
        values = self._grid.color_values
        low, high = self._color_range or robust_color_limits(values)
        if not np.isfinite(low) or not np.isfinite(high) or low >= high:
            raise ValueError("Color Range must contain finite Minimum < Maximum values.")
        colormap = get_colormap(self._colormap_name)
        self._color_texture = None
        self._surface_gradient = None
        if self._color_follows_height:
            finite_height = self._grid.z_values[np.isfinite(self._grid.z_values)]
            height_limits = self._shared_height_limits or (
                float(finite_height.min()), float(finite_height.max())
            )
            gradient = build_surface_gradient(
                self._colormap_name, (low, high), height_limits, opacity=self._opacity
            )
            self._surface_gradient = gradient
            self._surface_color_mode = configure_surface_series_colors(
                self.series,
                color_follows_height=True,
                gradient=gradient,
                texture=QImage(),
                range_gradient_style=self._Q3DTheme.ColorStyle.ColorStyleRangeGradient,
                uniform_style=self._Q3DTheme.ColorStyle.ColorStyleUniform,
            )
        else:
            rgba = map_surface_colors(values, self._colormap_name, (low, high), opacity=self._opacity)
            height, width, _ = rgba.shape
            image = QImage(
                rgba.data, width, height, int(rgba.strides[0]),
                QImage.Format.Format_RGBA8888,
            ).copy()
            self._color_texture = image
            self._surface_color_mode = configure_surface_series_colors(
                self.series,
                color_follows_height=False,
                gradient=QLinearGradient(),
                texture=image,
                range_gradient_style=self._Q3DTheme.ColorStyle.ColorStyleRangeGradient,
                uniform_style=self._Q3DTheme.ColorStyle.ColorStyleUniform,
            )
        self.color_ramp.set_colors(np.asarray(colormap.map(np.linspace(0.0, 1.0, 256), mode="byte")))
        self.color_title.setText(
            f"{self._grid.color_name} — {_transform_label(self._grid.color_transform)}"
            + (f" [{self._grid.color_unit}]" if self._grid.color_unit else "")
        )
        self.color_high.setText(f"{high:.5g}")
        self.color_low.setText(f"{low:.5g}")
        self._color_range = (float(low), float(high))
        finite_source = np.asarray(
            self._source_color_grid.z_values if self._source_color_grid is not None else values,
            dtype=np.float64,
        )
        finite_source = finite_source[np.isfinite(finite_source)]
        span = ((float(np.min(finite_source)), float(np.max(finite_source)))
                if finite_source.size else (float(low), float(high)))
        if span[0] >= span[1]:
            span = (float(low), float(high))
        self.horizontal_colorbar.set_mapping(
            np.asarray(colormap.map(np.linspace(0.0, 1.0, 256), mode="byte")),
            span, (float(low), float(high)), self.color_title.text(),
        )
        self._refresh_projection_colors()
        self._schedule_color_refresh()

    def set_opacity(self, opacity: float) -> None:
        opacity = float(opacity)
        if not np.isfinite(opacity) or not 0.0 <= opacity <= 1.0:
            raise ValueError("Surface opacity must be between 0 and 1.")
        if opacity == self._opacity:
            return
        self._opacity = opacity
        self._set_color_mapping()
        self._publish_diagnostics()

    def set_bottom_projection(self, enabled: bool, *, opacity: float | None = None) -> None:
        self._projection_enabled = bool(enabled)
        if opacity is not None:
            value = float(opacity)
            if not np.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError("Projection opacity must be between 0 and 1.")
            self._projection_opacity = value
        if self._geometry_type in {"Trajectory", "Scatter"}:
            self._update_point_overlays()
            return
        self._refresh_overlay_geometries()

    def set_reference_plane(self, mode: str = "off", value: float | None = None) -> None:
        mode = str(mode).lower()
        if mode not in {"off", "minimum", "custom", "zero"}:
            raise ValueError("Reference Plane mode must be off, minimum, custom, or zero.")
        self._reference_mode = mode
        if value is not None:
            self._reference_value = float(value)
        if self._geometry_type in {"Trajectory", "Scatter"}:
            self._update_point_overlays()
            return
        self._refresh_overlay_geometries()

    def _update_point_overlays(self) -> None:
        """Reference plane and floor projection for Trajectory/Scatter.

        Qt's vertical axis carries the mapped Point Y coordinate, so the plane
        is a horizontal plane at a Point Y value and the projection drops every
        point to the floor with its own scientific color.
        """
        if self.scatter_graph is None:
            return
        mappings = getattr(self, "_point_axis_mappings", None)
        positions = getattr(self, "_point_positions", None)
        if self._point_cloud is None or mappings is None or positions is None:
            if self._point_reference_item is not None:
                self._point_reference_item.setVisible(False)
            for series in self._scatter_projection_series:
                series.setVisible(False)
            return
        x_axis, vertical, z_axis = mappings
        # floor projection, one series per color bucket
        buckets = self._scatter_group_indices
        colors = getattr(self, "_point_bucket_colors", [])
        while len(self._scatter_projection_series) < len(colors):
            proxy = self._QScatterDataProxy()
            series = self._QScatter3DSeries(proxy)
            series.setColorStyle(self._Q3DTheme.ColorStyle.ColorStyleUniform)
            # MeshPoint draws nothing on macOS OpenGL (verified); low-poly spheres do.
            series.setMesh(self._QAbstract3DSeries.Mesh.MeshSphere)
            series.setMeshSmooth(False)
            series.setItemLabelVisible(False)
            self.scatter_graph.addSeries(series)
            self._scatter_projection_series.append(series)
            self._scatter_projection_proxies.append(proxy)
        floor = float(vertical.display_min)
        for index, series in enumerate(self._scatter_projection_series):
            if not self._projection_enabled or index >= len(colors) or colors[index] is None:
                series.setVisible(False)
                continue
            flat = positions[buckets[index]].copy()
            flat[:, 1] = floor
            color = QColor(colors[index])
            color.setAlpha(round(255 * 0.45 * self._projection_opacity))
            series.setBaseColor(color)
            series.setItemSize(float(getattr(self, "_point_size", 0.018)) * 0.8)
            self._scatter_projection_proxies[index].resetArray(
                [self._QScatterDataItem(QVector3D(*point)) for point in flat.tolist()])
            series.setVisible(True)
        # reference plane at a Point Y (vertical) value
        item = self._point_reference_item
        mode = self._reference_mode
        if item is None or mode == "off":
            if item is not None:
                item.setVisible(False)
            self.reference_label.clear()
            return
        low, high = vertical.physical_min, vertical.physical_max
        value = low if mode == "minimum" else 0.0 if mode == "zero" else self._reference_value
        if not low <= value <= high:
            item.setVisible(False)
            self.reference_label.setText(self.localizer.text(
                "viewer.reference_outside", value=f"{value:.5g}", low=f"{low:.5g}", high=f"{high:.5g}"))
            return
        if high == low:
            height = float(vertical.display_min)
        else:
            height = vertical.display_min + (value - low) / (high - low) * (vertical.display_max - vertical.display_min)
        item.setPosition(QVector3D((x_axis.display_min + x_axis.display_max) / 2.0, float(height),
                                   (z_axis.display_min + z_axis.display_max) / 2.0))
        item.setScaling(QVector3D(x_axis.display_max - x_axis.display_min, 0.001,
                                  z_axis.display_max - z_axis.display_min))
        item.setVisible(True)
        name, unit = self._point_coordinate_labels[1]
        key = {"minimum": "viewer.reference_minimum", "zero": "viewer.reference_zero"}.get(mode, "viewer.reference_custom")
        self.reference_label.setText(self.localizer.text(
            key, value=f"{name} = {value:.6g}" + (f" {unit}" if unit else "")))

    def set_secondary_grid(self, grid, *, opacity: float = 0.55) -> None:
        if self._grid is None or self._source_height_grid is None:
            raise ValueError("A primary Surface is required before a second Surface.")
        source = self._source_height_grid
        if (not np.array_equal(grid.x_values, source.x_values)
                or not np.array_equal(grid.y_values, source.y_values)
                or np.shape(grid.z_values) != np.shape(source.z_values)):
            raise ValueError("Dual Surface mappings must share the same X/Y grid.")
        opacity = float(opacity)
        if not 0.0 <= opacity <= 1.0:
            raise ValueError("Surface opacity must be between 0 and 1.")
        values = np.asarray(grid.z_values, dtype=np.float64)
        primary = np.asarray(self._grid.z_values, dtype=np.float64)
        finite = np.concatenate((primary[np.isfinite(primary)], values[np.isfinite(values)]))
        if finite.size == 0:
            raise ValueError("Dual surfaces contain no finite height values.")
        limits = (float(finite.min()), float(finite.max()))
        if limits[0] == limits[1]:
            limits = (limits[0] - 0.5, limits[1] + 0.5)
        self._secondary_grid = prepare_surface_grid(
            grid, indices=(self._grid.source_row_indices, self._grid.source_column_indices)
        )
        self._secondary_opacity = opacity
        self._shared_height_limits = limits
        self._reset_mesh(self._grid)

    def clear_secondary_grid(self) -> None:
        self._secondary_grid = None
        self.secondary_series.setVisible(False)
        self._shared_height_limits = None
        if self._grid is not None:
            self._reset_mesh(self._grid)

    def set_secondary_opacity(self, opacity: float) -> None:
        opacity = float(opacity)
        if not 0.0 <= opacity <= 1.0:
            raise ValueError("Surface opacity must be between 0 and 1.")
        self._secondary_opacity = opacity
        self.secondary_series.setBaseColor(QColor(*SURFACE_3D["dual_surface_b"], round(255 * opacity)))

    def set_horizontal_colorbar_visible(self, visible: bool) -> None:
        self.horizontal_colorbar.setVisible(bool(visible))

    def _series_array(self, grid: SurfaceGrid, *, z_limits=None, flat_height=None):
        mesh = prepare_surface_mesh(grid, z_scale=self._z_scale, z_limits=z_limits)
        coordinates = mesh.coordinates.copy()
        if flat_height is not None:
            coordinates[:, :, 1] = float(flat_height)
        return self._surface_items(coordinates), mesh

    def _surface_items(self, coordinates: np.ndarray):
        rows = [
            [self._QSurfaceDataItem(QVector3D(*point.tolist())) for point in row]
            for row in coordinates
        ]
        return rows

    def _apply_secondary_mesh(self) -> None:
        if self._secondary_grid is None or self._shared_height_limits is None:
            self.secondary_series.setVisible(False)
            return
        rows, _mesh = self._series_array(self._secondary_grid, z_limits=self._shared_height_limits)
        self._secondary_proxy.resetArray(rows)
        self.secondary_series.setBaseColor(QColor(*SURFACE_3D["dual_surface_b"], round(255 * self._secondary_opacity)))
        self.secondary_series.setVisible(True)

    def _refresh_projection_colors(self) -> None:
        if self._grid is None or self._color_range is None:
            return
        rgba = map_surface_colors(
            self._grid.color_values, self._colormap_name, self._color_range,
            opacity=self._projection_opacity,
        )
        height, width, _ = rgba.shape
        self.projection_series.setTexture(QImage(
            rgba.data, width, height, int(rgba.strides[0]), QImage.Format.Format_RGBA8888
        ).copy())
        self.projection_series.setColorStyle(self._Q3DTheme.ColorStyle.ColorStyleUniform)
        self.projection_series.setBaseColor(QColor(SURFACE_3D["white"]))

    def _refresh_overlay_geometries(self, mesh_data=None) -> None:
        if self._grid is None or self._axis_mappings is None:
            self.projection_series.setVisible(False)
            self.reference_plane_item.setVisible(False)
            return
        _x_axis, _y_axis, z_axis = self._axis_mappings
        if self._projection_enabled and self._geometry_type == "Surface":
            mesh = mesh_data or prepare_surface_mesh(
                self._grid, z_scale=self._z_scale, z_limits=self._shared_height_limits
            )
            floor = mesh.z_axis.display_min
            coordinates = mesh.coordinates.copy()
            coordinates[:, :, 1] = floor
            rows = self._surface_items(coordinates)
            self._projection_proxy.resetArray(rows)
            self._refresh_projection_colors()
            self.projection_series.setVisible(True)
        else:
            self.projection_series.setVisible(False)
        self._apply_secondary_mesh()
        if self._geometry_type in {"Surface", "Dual Surface", "Waterfall"}:
            self._update_reference_plane(z_axis)
        else:
            self.reference_plane_item.setVisible(False)
            self.reference_label.clear()

    def _update_reference_plane(self, z_axis: NormalizedAxis) -> None:
        mode = self._reference_mode
        if mode == "off" or self._grid is None or self._axis_mappings is None:
            self.reference_plane_item.setVisible(False)
            self.reference_label.clear()
            return
        finite = np.asarray(self._grid.z_values, dtype=np.float64)
        finite = finite[np.isfinite(finite)]
        if finite.size == 0:
            self.reference_plane_item.setVisible(False)
            return
        value = float(z_axis.physical_min) if mode == "minimum" else (0.0 if mode == "zero" else self._reference_value)
        if not z_axis.physical_min <= value <= z_axis.physical_max:
            self.reference_plane_item.setVisible(False)
            self.reference_label.setText(self.localizer.text(
                "viewer.reference_outside", value=f"{value:.5g}",
                low=f"{z_axis.physical_min:.5g}", high=f"{z_axis.physical_max:.5g}"))
            return
        height = scene_height_for_value(value, z_axis, z_scale=self._z_scale)
        x_axis, y_axis, _ = self._axis_mappings
        x0, x1 = x_axis.display_min, x_axis.display_max
        y0, y1 = y_axis.display_min, y_axis.display_max
        if self._projection_enabled and abs(height - z_axis.display_min) < 1e-6:
            height += (z_axis.display_max - z_axis.display_min) * 0.002
        self.reference_plane_item.setPosition(QVector3D(
            (x0 + x1) / 2.0, height, (y0 + y1) / 2.0,
        ))
        self.reference_plane_item.setScaling(QVector3D(
            x1 - x0, 0.001, y1 - y0,
        ))
        self.reference_plane_item.setVisible(True)
        unit = self._grid.z_unit
        key = {"minimum": "viewer.reference_minimum", "zero": "viewer.reference_zero"}.get(mode, "viewer.reference_custom")
        self.reference_label.setText(self.localizer.text(key, value=f"{value:.6g}" + (f" {unit}" if unit else "")))

    def _schedule_color_refresh(self) -> None:
        QTimer.singleShot(0, self._refresh_surface_colors_after_expose)

    def _refresh_surface_colors_after_expose(self) -> None:
        if self._grid is None or not self.graph.isExposed():
            return
        # Rebind after native QWindow exposure; this does not rebuild the mesh
        # or change scientific state, and ensures the first visible frame has
        # the same color source as the colorbar.
        if self._color_follows_height and self._surface_gradient is not None:
            self.series.setBaseGradient(self._surface_gradient)
        elif self._color_texture is not None:
            self.series.setTexture(self._color_texture)
        self.graph.requestUpdate()

    def _on_selected_point_changed(self, position) -> None:
        if self._grid is None or position.x() < 0 or position.y() < 0:
            self.pick_label.setText(self.localizer.text("viewer.surface_pick_hint"))
            return
        if self._geometry_type == "Waterfall" and self._waterfall_data is not None:
            row, column = int(position.x()), int(position.y())
            source_row = int(self._grid.source_row_indices[row])
            source_column = int(self._grid.source_column_indices[column])
            if source_row % 2:
                self.pick_label.setText("Trace separator")
                return
            waterfall = self._waterfall_data
            trace = int(waterfall.source_rows[source_row // 2])
            sample = int(waterfall.source_columns[source_column])
            height = waterfall.source_height
            color = waterfall.source_color
            text = (
                f"{height.x_name}: {height.x_values[sample]:.6g}"
                + (f" {height.x_unit}" if height.x_unit else "")
                + f"  |  {height.y_name}: {height.y_values[trace]:.6g}"
                + (f" {height.y_unit}" if height.y_unit else "")
                + f"  |  Height {height.z_name}: {height.z_values[trace, sample]:.6g}"
                + (f" {height.z_unit}" if height.z_unit else "")
            )
            if color is not height:
                text += f"  |  Color {color.z_name}: {color.z_values[trace, sample]:.6g}"
            self.pick_label.setText(text)
            return
        self.pick_label.setText(format_pick_readout(self._grid, position.x(), position.y()))

    def set_color_settings(self, *, colormap: str | None = None,
                           color_range: tuple[float, float] | None = None) -> None:
        """Remap color/texture only; surface geometry is left untouched."""
        if colormap is not None:
            self._colormap_name = colormap
        self._color_range = color_range
        self._set_color_mapping()
        self._publish_diagnostics()

    def set_z_scale(self, scale: float) -> None:
        """Rebuild display geometry only, without re-reading or transforming data."""
        scale = float(scale)
        if not 0.1 <= scale <= 10.0:
            raise ValueError("Z Scale must be between 0.1x and 10x.")
        if self._geometry_type in {"Trajectory", "Scatter"} and self._point_cloud is not None:
            if scale == self._z_scale:
                return
            state = self.camera_state()
            self._z_scale = scale
            self.set_point_cloud(
                self._point_cloud, geometry=self._geometry_type,
                axis_labels=self._point_coordinate_labels,
                color_label=self._point_color_label,
                color_range=self._point_color_range,
                colormap=self._colormap_name, point_size=self._point_size,
            )
            self.apply_camera_state(state)
            self._publish_diagnostics()
            return
        if self._grid is None or self._axis_mappings is None or scale == self._z_scale:
            self._z_scale = scale
            return
        self._z_scale = scale
        self._reset_mesh(self._grid)
        self._publish_diagnostics()

    def set_rendering_policy(self, policy: RenderingPolicy | str) -> SurfaceGrid | None:
        if self._source_height_grid is None:
            return None
        return self.set_grid(
            self._source_height_grid, color_grid=self._source_color_grid,
            colormap=self._colormap_name, color_range=self._color_range,
            rendering_policy=policy, shared_height_limits=self._shared_height_limits,
        )

    def _set_status_text(self) -> None:
        if self._grid is None:
            self.status_label.clear()
            return
        decision = self._rendering_decision
        parts = [self.localizer.text(
            "viewer.surface_grid_status", source_rows=self._grid.source_shape[0],
            source_columns=self._grid.source_shape[1], vertices=self._grid.vertex_count,
            policy=decision.effective.value if decision else "Auto",
        )]
        if decision is not None and decision.warning:
            parts.append(self.localizer.text("viewer.surface_large_warning"))
        if self._grid.invalid_value_count:
            parts.append(self.localizer.text("viewer.surface_gaps", count=self._grid.invalid_value_count))
        fps = float(self.active_graph.currentFps())
        if fps > 0:
            parts.append(self.localizer.text("viewer.surface_performance", fps=fps, frame_ms=1000.0 / fps))
        self.status_label.setText("  |  ".join(parts))

    def _axis(self, graph_axis, name: str, unit: str | None, semantic: str,
              mapping: NormalizedAxis) -> None:
        graph_axis.setTitle(_axis_title(name, unit, semantic))
        graph_axis.setTitleVisible(True)
        graph_axis.setFormatter(self._PhysicalAxisFormatter(mapping, graph_axis))
        graph_axis.setRange(mapping.display_min, mapping.display_max)

    def _set_axes(self, grid: SurfaceGrid, x_axis: NormalizedAxis,
                  y_axis: NormalizedAxis, z_axis: NormalizedAxis) -> None:
        # Q3DSurface uses Qt's Y axis as the vertical height axis.  The
        # scientific grid's Z value is mapped there, while the scientific Y
        # coordinate occupies Qt's horizontal Z axis. Each title states the
        # scientific semantic axis explicitly; points remain z[y, x].
        self._axis(self.graph.axisX(), grid.x_name, grid.x_unit, "X", x_axis)
        self._axis(
            self.graph.axisY(), f"{grid.z_name} — {_transform_label(grid.z_transform)}",
            grid.z_unit, "Z", z_axis,
        )
        self._axis(self.graph.axisZ(), grid.y_name, grid.y_unit, "Y", y_axis)

    def reset_view(self) -> None:
        camera = _camera_graph(self).scene().activeCamera()
        _apply_camera_pose(camera, 35.0, 25.0, 100.0, self._view_target())
        self._camera_update_count += 1
        self._publish_diagnostics()

    def set_camera_preset(self, name: str) -> None:
        presets = {
            "Top": self._Q3DCamera.CameraPreset.CameraPresetDirectlyAbove,
            "Front": self._Q3DCamera.CameraPreset.CameraPresetFront,
            "Side": self._Q3DCamera.CameraPreset.CameraPresetRight,
            "Isometric": self._Q3DCamera.CameraPreset.CameraPresetIsometricRight,
        }
        if name not in presets:
            raise ValueError(f"Unknown camera preset: {name}")
        _camera_graph(self).scene().activeCamera().setCameraPreset(presets[name])
        self._camera_update_count += 1
        self._publish_diagnostics()

    def set_projection(self, projection: str) -> None:
        normalized = str(projection).lower()
        if normalized not in {"perspective", "orthographic"}:
            raise ValueError(f"Unknown projection: {projection}")
        _camera_graph(self).setOrthoProjection(normalized == "orthographic")
        self._camera_update_count += 1
        self._publish_diagnostics()

    def _view_target(self) -> QVector3D:
        if _camera_graph(self) is not self.graph and getattr(self, "_point_axis_mappings", None) is not None:
            x_axis, y_axis, z_axis = self._point_axis_mappings
            return QVector3D(
                (x_axis.display_min + x_axis.display_max) / 2.0,
                (y_axis.display_min + y_axis.display_max) / 2.0,
                (z_axis.display_min + z_axis.display_max) / 2.0,
            )
        if self._axis_mappings is None:
            return QVector3D(0.0, 0.0, 0.0)
        x_axis, y_axis, z_axis = self._axis_mappings
        return QVector3D(
            (x_axis.display_min + x_axis.display_max) / 2.0,
            (z_axis.display_min + z_axis.display_max) / 2.0,
            (y_axis.display_min + y_axis.display_max) / 2.0,
        )

    def view_all(self) -> None:
        camera = _camera_graph(self).scene().activeCamera()
        _apply_camera_pose(camera, 35.0, 25.0, 100.0, self._view_target())
        self._camera_update_count += 1
        self._publish_diagnostics()

    def _pan_camera(self, dx: float, dy: float) -> None:
        camera = _camera_graph(self).scene().activeCamera()
        target = camera.target()
        horizontal = np.deg2rad(float(camera.xRotation()))
        vertical = np.deg2rad(float(camera.yRotation()))
        direction = QVector3D(
            float(np.sin(horizontal) * np.cos(vertical)),
            float(-np.sin(vertical)),
            float(-np.cos(horizontal) * np.cos(vertical)),
        ).normalized()
        world_up = QVector3D(0.0, 1.0, 0.0)
        right = QVector3D.crossProduct(direction, world_up).normalized()
        if right.lengthSquared() < 1e-12:
            right = QVector3D(1.0, 0.0, 0.0)
        up = QVector3D.crossProduct(right, direction).normalized()
        zoom = max(10.0, float(camera.zoomLevel()))
        span = self._world_span()
        pixels = max(1, min(self.container.width(), self.container.height()))
        scale = span / pixels * (100.0 / zoom)
        moved_target = target + right * (-dx * scale) + up * (dy * scale)
        camera.setTarget(moved_target)
        self._camera_update_count += 1
        self._publish_diagnostics()

    def _rotate_camera(self, dx: float, dy: float) -> None:
        camera = _camera_graph(self).scene().activeCamera()
        horizontal = (float(camera.xRotation()) + float(dx) * 0.5) % 360.0
        vertical = float(camera.yRotation()) - float(dy) * 0.5
        min_vertical = getattr(camera, "minVerticalRotation", None)
        max_vertical = getattr(camera, "maxVerticalRotation", None)
        if callable(min_vertical):
            vertical = max(float(min_vertical()), vertical)
        if callable(max_vertical):
            vertical = min(float(max_vertical()), vertical)
        camera.setCameraPosition(horizontal, vertical, float(camera.zoomLevel()))
        self._camera_update_count += 1
        self._publish_diagnostics()

    def _world_span(self) -> float:
        return 1.0

    def camera_state(self) -> CameraState3D:
        camera = _camera_graph(self).scene().activeCamera()
        target = camera.target()
        return CameraState3D.from_dict({
            "x_rotation": camera.xRotation(),
            "y_rotation": camera.yRotation(),
            "zoom_level": camera.zoomLevel(),
            "target": [target.x(), target.y(), target.z()],
        })

    def apply_camera_state(self, state: CameraState3D) -> None:
        camera = _camera_graph(self).scene().activeCamera()
        target = QVector3D(*state.target)
        _apply_camera_pose(
            camera, state.x_rotation, state.y_rotation, state.zoom_level, target
        )
        self._camera_update_count += 1
        self._publish_diagnostics()

    def render_to_image(self, size: QSize, *, msaa_samples: int = 0):
        """Render the active native scene, including camera and 3D overlays."""
        if size.width() <= 0 or size.height() <= 0:
            raise ValueError("Render size must be positive.")
        return _camera_graph(self).renderToImage(int(msaa_samples), size)

    def render_plot_image(self, *, scale: int = 3) -> QImage:
        """Capture the current native plot and its existing scientific colorbar."""
        if scale < 1 or scale > 4:
            raise ValueError("3D export scale must be between 1 and 4.")
        graph = _camera_graph(self)
        if graph is self.graph and (self._grid is None or not self.series.isVisible()):
            raise ValueError("Wait for the 3D surface to finish preparing before export.")
        if graph is self.scatter_graph and self._point_cloud is None:
            raise ValueError("Wait for the 3D point scene to finish preparing before export.")
        graph_size = graph.size()
        bar_width = self.colorbar_widget.width() if self.colorbar_widget.isVisible() else 0
        if graph_size.width() <= 0 or graph_size.height() <= 0:
            raise ValueError("The 3D viewport is not ready for export.")
        scene = graph.renderToImage(0, graph_size * scale)
        if scene.isNull() or scene.size() != graph_size * scale:
            raise RuntimeError("The native 3D renderer did not return the requested frame.")
        image = QImage(
            (graph_size.width() + bar_width) * scale,
            graph_size.height() * scale,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        painter.drawImage(0, 0, scene)
        if bar_width:
            painter.save()
            painter.scale(scale, scale)
            self.colorbar_widget.render(painter, QPoint(graph_size.width(), 0))
            painter.restore()
        painter.end()
        return image

    def diagnostics(self) -> dict[str, object]:
        fps = float(_camera_graph(self).currentFps())
        color_samples = None
        color_limits = self._color_range
        if self._grid is not None and color_limits is not None:
            color_samples = surface_color_mapping_diagnostics(
                self._grid.color_values, self._colormap_name, color_limits
            )
        return {
            "renderer_id": id(self),
            "backend": "Qt Data Visualization / OpenGL",
            "geometry_type": self._geometry_type,
            "mesh_dimensions": list(self._grid.shape) if self._grid else None,
            "source_dimensions": list(self._grid.source_shape) if self._grid else None,
            "vertex_count": self._grid.vertex_count if self._grid else 0,
            "fps": fps if fps > 0 else None,
            "frame_time_ms": 1000.0 / fps if fps > 0 else None,
            "mesh_rebuild_count": self._mesh_rebuild_count,
            "draw_mode": str(self.series.drawMode()),
            "lod_budget": self._active_budget,
            "interaction_lod_active": self._interaction_active,
            "lod_cache_entries": len(self._lod_cache),
            "lod_cache_bytes": self._lod_cache.current_bytes,
            "grid_prepare_ms": self._last_grid_prepare_ms,
            "qt_item_build_ms": self._last_item_build_ms,
            "proxy_reset_ms": self._last_proxy_reset_ms,
            "camera_update_count": self._camera_update_count,
            "rendering_policy": self._rendering_decision.effective.value if self._rendering_decision else None,
            "color_source": self._grid.color_name if self._grid else None,
            "color_application": self._surface_color_mode,
            "color_mapping_samples": color_samples,
            "surface_gradient_stops": len(self._surface_gradient.stops()) if self._surface_gradient else 0,
            "texture_size": (
                [self._color_texture.width(), self._color_texture.height()]
                if self._color_texture is not None else None
            ),
            "z_scale": self._z_scale,
            "surface_opacity": self._opacity,
            "bottom_projection": self._projection_enabled,
            "dual_surface": self._secondary_grid is not None,
            "point_count": len(self._point_cloud.coordinates) if self._point_cloud is not None else 0,
            "projection": "orthographic" if _camera_graph(self).isOrthoProjection() else "perspective",
        }

    def _publish_diagnostics(self, *_args) -> None:
        publish_renderer_diagnostics(self.diagnostics())

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API spelling
        self._lod_generation += 1
        self._idle_timer.stop()
        clear_renderer_diagnostics(id(self))
        super().closeEvent(event)
