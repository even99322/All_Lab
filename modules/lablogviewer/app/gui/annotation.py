"""Screen annotation: a transparent pen / laser-pointer layer over plots.

Annotation is a display-only overlay. It never touches measurement data,
HDF5 files, transforms, formulas or analysis results, is never saved and is
not included in exports. Strokes are fixed to the screen (a sheet of glass
over the plot) and disappear when Annotation is closed or cleared.

Input while Annotation is on: right-button drag draws (pen) or points
(laser); left button and wheel keep panning, rotating and zooming the plot.
Plot right-click menus are paused and return when Annotation closes.

Each host window (Database Browser, Viewer, YIG Mirror Analysis, 3D Surface)
installs one ``AnnotationSession`` with its toggle button and plot regions.
Regions containing a native OpenGL window (the Data Visualization point
graph) get a floating transparent window instead of a child overlay, because
native windows are composited above ordinary Qt widgets.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QBrush, QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QLabel, QMainWindow, QSplitter, QToolBar, QToolButton, QWidget,
)

from app.gui.glass import GlassSlider, GlassToolBar
from app.icons import icon, make_icon_only
from app.palette import ANNOTATION, GLASS_SPECULAR_RGB, PLOT_WHITE
from app.theme import current_theme_colors, get_theme_manager


PEN_COLORS = dict(ANNOTATION["pens"])     # red, blue, yellow, green (app/palette.py)
LASER_COLOR = ANNOTATION["laser"]


def apply_personal_pens() -> None:
    """Use the user's pen colours (Settings > Personal) for new strokes."""
    global LASER_COLOR
    try:
        from app.core.personal import color

        for key in list(PEN_COLORS):
            PEN_COLORS[key] = color("pens", key)
        LASER_COLOR = color("pens", "laser")
    except Exception:
        pass
DEFAULT_WIDTH = 3
MAX_WIDTH = 12
DEFAULT_TRAIL_S = 0.8
MAX_TRAIL_S = 1.5
CLEAR_DURATION_MS = 700
RIPPLE_MS = 260


@dataclass
class Stroke:
    points: list[QPointF] = field(default_factory=list)
    color: QColor = field(default_factory=lambda: QColor(PEN_COLORS["red"]))
    width: float = DEFAULT_WIDTH


def smooth_points(points: list[QPointF], iterations: int = 2) -> list[QPointF]:
    """Chaikin corner cutting; endpoints are preserved."""
    result = list(points)
    for _ in range(iterations):
        if len(result) < 3:
            return result
        refined = [result[0]]
        for first, second in zip(result, result[1:]):
            refined.append(QPointF(0.75 * first.x() + 0.25 * second.x(), 0.75 * first.y() + 0.25 * second.y()))
            refined.append(QPointF(0.25 * first.x() + 0.75 * second.x(), 0.25 * first.y() + 0.75 * second.y()))
        refined.append(result[-1])
        result = refined
    return result


def stroke_path(points: list[QPointF]) -> QPainterPath:
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(points[0])
    if len(points) == 1:
        path.lineTo(points[0] + QPointF(0.01, 0.01))
        return path
    for current, following in zip(points[1:], points[2:]):
        path.quadTo(current, (current + following) / 2.0)
    path.lineTo(points[-1])
    return path


def halo_color() -> QColor:
    """Contrast outline so strokes stay visible on any colormap region."""
    manager = get_theme_manager()
    background = manager.plot_colors.background if manager is not None else PLOT_WHITE.background
    light = QColor(background).lightness() >= 128
    return QColor(*ANNOTATION["halo_on_light_plot" if light else "halo_on_dark_plot"])


def paint_strokes(painter: QPainter, strokes: list[Stroke]) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    halo = halo_color()
    for stroke in strokes:
        path = stroke_path(stroke.points)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(halo, stroke.width + 3.0, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)
        painter.setPen(QPen(stroke.color, stroke.width, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)


class AnnotationCanvas(QWidget):
    """Transparent drawing sheet placed over one plot region."""

    def __init__(self, tracked: list[QWidget], *, floating: bool, host: QWidget):
        self.tracked = [widget for widget in tracked if widget is not None]
        self.floating = floating
        parent = host if floating else self.tracked[0].parentWidget()
        # A QSplitter turns every child into a new pane (it squeezed the 3D plot);
        # overlay from the first ancestor that is not a splitter instead.
        while not floating and isinstance(parent, QSplitter) and parent.parentWidget() is not None:
            parent = parent.parentWidget()
        super().__init__(parent)
        if floating:
            self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                                | Qt.WindowType.WindowTransparentForInput
                                | Qt.WindowType.NoDropShadowWindowHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setObjectName("annotationCanvas")
        self.strokes: list[Stroke] = []
        self.active: Stroke | None = None
        self.laser: list[tuple[QPointF, float]] = []
        self.laser_down = False
        self.trail_s = DEFAULT_TRAIL_S
        self.sync_geometry()

    def region_global_rect(self) -> QRect:
        rect = QRect()
        for widget in self.tracked:
            if widget.isVisible():
                rect = rect.united(QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size()))
        return rect

    def sync_geometry(self) -> None:
        rect = self.region_global_rect()
        if rect.isEmpty():
            self.hide()
            return
        if self.floating:
            self.setGeometry(rect)
        else:
            parent = self.parentWidget()
            self.setGeometry(QRect(parent.mapFromGlobal(rect.topLeft()), rect.size()))
            self.raise_()
        if not self.isVisible():
            self.show()

    def contains_global(self, point: QPoint) -> bool:
        return self.isVisible() and self.region_global_rect().contains(point)

    # strokes ---------------------------------------------------------------
    def begin_stroke(self, point: QPointF, color: QColor, width: float) -> None:
        self.active = Stroke([point], QColor(color), float(width))
        self.update()

    def extend_stroke(self, point: QPointF) -> None:
        if self.active is None:
            return
        last = self.active.points[-1]
        if (point - last).manhattanLength() >= 1.5:
            self.active.points.append(point)
            self.update()

    def end_stroke(self) -> None:
        if self.active is not None:
            self.active.points = smooth_points(self.active.points)
            self.strokes.append(self.active)
            self.active = None
            self.update()

    def has_ink(self) -> bool:
        return bool(self.strokes) or self.active is not None

    def ink_image(self) -> QImage:
        """Strokes only, on a transparent background (for the clear animation)."""
        ratio = self.devicePixelRatioF()
        image = QImage(max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio)),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        paint_strokes(painter, self.strokes + ([self.active] if self.active else []))
        painter.end()
        return image

    def clear_ink(self) -> None:
        self.strokes.clear()
        self.active = None
        self.update()

    # laser -----------------------------------------------------------------
    def add_laser(self, point: QPointF) -> None:
        self.laser.append((point, time.monotonic()))
        self.update()

    def prune_laser(self) -> bool:
        now = time.monotonic()
        keep = max(0.05, self.trail_s)
        self.laser = [(point, stamp) for point, stamp in self.laser if now - stamp <= keep]
        if not self.laser_down and self.trail_s <= 0.0:
            self.laser.clear()
        self.update()
        return bool(self.laser) or self.laser_down

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        paint_strokes(painter, self.strokes + ([self.active] if self.active else []))
        if self.laser:
            self._paint_laser(painter)
        painter.end()

    def _paint_laser(self, painter: QPainter) -> None:
        now = time.monotonic()
        trail = max(0.05, self.trail_s)
        base = QColor(LASER_COLOR)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Draw the tail on its own layer with Source composition so overlapping
        # round caps of translucent segments do not darken into beads.
        ratio = self.devicePixelRatioF()
        size = QSize(max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio)))
        if getattr(self, "_laser_layer", None) is None or self._laser_layer.size() != size:
            self._laser_layer = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
            self._laser_layer.setDevicePixelRatio(ratio)
        layer = self._laser_layer
        layer.fill(Qt.GlobalColor.transparent)
        tail = QPainter(layer)
        tail.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        tail.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        for (first, _t0), (second, stamp) in zip(self.laser, self.laser[1:]):
            fresh = max(0.0, 1.0 - (now - stamp) / trail)
            if fresh <= 0.0:
                continue
            color = QColor(base)
            color.setAlphaF(0.85 * fresh)
            tail.setPen(QPen(color, 1.5 + 6.0 * fresh, Qt.PenStyle.SolidLine,
                             Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            tail.drawLine(first, second)
        tail.end()
        painter.drawImage(0, 0, layer)
        head, stamp = self.laser[-1]
        fresh = 1.0 if self.laser_down else max(0.0, 1.0 - (now - stamp) / trail)
        if fresh <= 0.0:
            return
        glow = QRadialGradient(head, 16.0)
        outer = QColor(base)
        outer.setAlphaF(0.0)
        middle = QColor(base)
        middle.setAlphaF(0.45 * fresh)
        glow.setColorAt(0.0, middle)
        glow.setColorAt(1.0, outer)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(head, 16.0, 16.0)
        core = QColor(base)
        core.setAlphaF(fresh)
        painter.setBrush(core)
        painter.drawEllipse(head, 4.5, 4.5)
        red, green, blue, alpha = ANNOTATION["laser_core"]
        painter.setBrush(QColor(red, green, blue, round(alpha * fresh)))
        painter.drawEllipse(head, 1.8, 1.8)


class _ClearAnimation(QWidget):
    """Liquid-glass 'suction': the ink sheet is drawn into the Annotation button.

    Only a snapshot of the strokes animates; the real strokes are removed
    before it starts, so the plot underneath is never covered or hidden.
    """

    STRIPS = 48

    def __init__(self, host: QWidget, sheets: list[tuple[QImage, QRect]], target: QPoint):
        super().__init__(host)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowTransparentForInput
                            | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        area = host.frameGeometry()
        for _image, rect in sheets:
            area = area.united(rect)
        self.setGeometry(area)
        self.origin = area.topLeft()
        self.sheets = []
        for image, rect in sheets:
            tinted = QImage(image)
            painter = QPainter(tinted)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
            painter.fillRect(tinted.rect(), QColor(*ANNOTATION["clear_tint"]))
            painter.end()
            self.sheets.append((image, tinted, rect.translated(-self.origin)))
        self.target = QPointF(target - self.origin)
        self.started = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.show()

    def _tick(self) -> None:
        if (time.monotonic() - self.started) * 1000 > CLEAR_DURATION_MS + RIPPLE_MS:
            self.timer.stop()
            self.close()
            self.deleteLater()
            return
        self.update()

    @staticmethod
    def _ease(value: float) -> float:
        value = min(max(value, 0.0), 1.0)
        return value * value * (3.0 - 2.0 * value) if value < 1.0 else 1.0

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API spelling
        elapsed = (time.monotonic() - self.started) * 1000.0
        progress = min(1.0, elapsed / CLEAR_DURATION_MS)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        arrived = 0.0
        for image, tinted, rect in self.sheets:
            strips = self.STRIPS
            spread = max(1.0, abs(self.target.y() - rect.center().y()) + rect.height())

            def boundary(position: float):
                # Boundaries nearer the button leave first (genie order), so the
                # sheet narrows into a funnel while staying one continuous piece.
                y0 = rect.top() + rect.height() * position
                closeness = 1.0 - min(1.0, abs(self.target.y() - y0) / spread)
                local = self._ease((progress - 0.35 * (1.0 - closeness)) / 0.65)
                width = rect.width() + (6.0 - rect.width()) * local ** 0.8
                center_x = rect.center().x() + (self.target.x() - rect.center().x()) * local ** 1.25
                return y0 + (self.target.y() - y0) * local, width, center_x, local

            edges = [boundary(index / strips) for index in range(strips + 1)]
            for index in range(strips):
                (y_a, w_a, x_a, l_a), (y_b, w_b, x_b, l_b) = edges[index], edges[index + 1]
                local = (l_a + l_b) / 2.0
                arrived += local / (strips * len(self.sheets))
                top, bottom = min(y_a, y_b), max(y_a, y_b)
                width, center_x = (w_a + w_b) / 2.0, (x_a + x_b) / 2.0
                target_rect = QRectF(center_x - width / 2.0, top, width, max(0.6, bottom - top + 0.5))
                source = QRectF(0, index * image.height() / strips, image.width(), image.height() / strips)
                painter.setOpacity(max(0.0, 1.0 - local ** 3 * 0.9))
                painter.drawImage(target_rect, image, source)
                painter.setOpacity(max(0.0, min(1.0, local * 1.4)) * max(0.0, 1.0 - local ** 4))
                painter.drawImage(target_rect, tinted, source)
        painter.setOpacity(1.0)
        # Glass droplet collecting at the button, then a ripple.
        glow_radius = 6.0 + 16.0 * arrived
        glow = QRadialGradient(self.target, glow_radius)
        glow_rgb, glow_alpha = ANNOTATION["clear_glow"][:3], ANNOTATION["clear_glow"][3]
        tint_rgb = ANNOTATION["clear_tint"][:3]
        glow.setColorAt(0.0, QColor(*glow_rgb, round(glow_alpha * min(1.0, arrived * 1.6))))
        glow.setColorAt(1.0, QColor(*tint_rgb, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(self.target, glow_radius, glow_radius)
        if elapsed > CLEAR_DURATION_MS * 0.85:
            ripple = min(1.0, (elapsed - CLEAR_DURATION_MS * 0.85) / (RIPPLE_MS + CLEAR_DURATION_MS * 0.15))
            radius = 12.0 + 26.0 * ripple
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(*ANNOTATION["clear_tint"][:3], round(200 * (1.0 - ripple))), 2.0))
            painter.drawEllipse(self.target, radius, radius)
        painter.end()


class ColorSwatch(QToolButton):
    """Round color chip; the checked chip gets an accent ring."""

    def __init__(self, key: str, color: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.color = QColor(color)
        self.setCheckable(True)
        self.setFixedSize(26, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("annotationSwatch", True)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API spelling
        colors = current_theme_colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        if self.isChecked():
            painter.setPen(QPen(QColor(colors.accent), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, 11.0, 11.0)
        painter.setPen(QPen(QColor(*ANNOTATION["swatch_outline"]), 1.0))
        painter.setBrush(self.color if self.isEnabled() else QColor(colors.secondary))
        painter.drawEllipse(center, 7.5, 7.5)
        painter.setPen(QPen(QColor(*GLASS_SPECULAR_RGB, 150), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(QRectF(center.x() - 5, center.y() - 5.5, 10, 8), 30 * 16, 120 * 16)
        painter.end()


class AnnotationToolbar(GlassToolBar):
    """Bottom glass toolbar: Pen / Laser / Clear, colors, width or trail."""

    def __init__(self, session: "AnnotationSession", parent=None):
        super().__init__("Annotation", parent)
        self.session = session
        self.setObjectName("annotationToolbar")
        self.setMovable(False)
        self.setFloatable(False)
        self.setIconSize(QSize(24, 24))
        self.pen_button = make_icon_only(QToolButton(), "pen", "Pen", 24, flat=True)
        self.laser_button = make_icon_only(QToolButton(), "laser", "Laser Pointer", 24, flat=True)
        for button in (self.pen_button, self.laser_button):
            button.setCheckable(True)
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_group.addButton(self.pen_button)
        self.tool_group.addButton(self.laser_button)
        self.pen_button.setChecked(True)
        self.clear_button = make_icon_only(QToolButton(), "clear", "Clear Screen", 24, flat=True)
        self.addWidget(self.pen_button)
        self.addWidget(self.laser_button)
        self.addWidget(self.clear_button)
        self.addSeparator()
        self.swatch_group = QButtonGroup(self)
        self.swatch_group.setExclusive(True)
        self.swatches: dict[str, ColorSwatch] = {}
        self._swatch_actions = {}
        for key, color in PEN_COLORS.items():
            swatch = ColorSwatch(key, color)
            swatch.setToolTip(key.title())
            self.swatch_group.addButton(swatch)
            self.swatches[key] = swatch
            self._swatch_actions[key] = self.addWidget(swatch)
        self.laser_swatch = ColorSwatch("laser", LASER_COLOR)
        self.laser_swatch.setChecked(True)
        self.laser_swatch.setToolTip("Red")
        self._laser_swatch_action = self.addWidget(self.laser_swatch)
        self.swatches["red"].setChecked(True)
        self.addSeparator()
        self.size_label = QLabel("Width")
        self.size_label.setObjectName("glassDialLabel")
        self.slider = GlassSlider()
        self.slider.setFixedWidth(140)
        self.value_label = QLabel()
        self.value_label.setObjectName("glassDialLabel")
        self.value_label.setMinimumWidth(34)
        self.addWidget(self.size_label)
        self.addWidget(self.slider)
        self.addWidget(self.value_label)
        self.pen_button.toggled.connect(lambda checked: checked and session.set_tool("pen"))
        self.laser_button.toggled.connect(lambda checked: checked and session.set_tool("laser"))
        self.clear_button.clicked.connect(session.clear_with_animation)
        self.swatch_group.buttonClicked.connect(lambda button: session.set_color(button.key))
        self.slider.valueChanged.connect(self._slider_changed)
        self.refresh()

    def refresh(self) -> None:
        session = self.session
        pen = session.tool == "pen"
        for action in self._swatch_actions.values():
            action.setVisible(pen)
        self._laser_swatch_action.setVisible(not pen)
        self.slider.blockSignals(True)
        if pen:
            self.size_label.setText("Width")
            self.slider.setRange(1, MAX_WIDTH)
            self.slider.setValue(round(session.width))
            self.value_label.setText(f"{round(session.width)} px")
        else:
            self.size_label.setText("Trail")
            self.slider.setRange(0, round(MAX_TRAIL_S * 100))
            self.slider.setValue(round(session.trail_s * 100))
            self.value_label.setText(f"{session.trail_s:.2f} s")
        self.slider.blockSignals(False)
        self.slider.update()
        self.clear_button.setEnabled(session.has_ink())
        localizer = getattr(session, "localizer", None)
        if localizer is not None:
            localizer.retranslate_tree(self)

    def _slider_changed(self, value: int) -> None:
        if self.session.tool == "pen":
            self.session.width = float(value)
            self.value_label.setText(f"{value} px")
        else:
            self.session.set_trail(value / 100.0)
            self.value_label.setText(f"{value / 100.0:.2f} s")


RegionSpec = tuple[list[QWidget], bool]


class AnnotationSession(QObject):
    """Annotation state for one host window."""

    def __init__(self, host: QMainWindow, toggle: QToolButton,
                 regions: Callable[[], list[RegionSpec]],
                 lock: Callable[[bool], None] | None = None, localizer=None):
        super().__init__(host)
        self.host = host
        self.toggle = toggle
        self.regions = regions
        self.lock = lock
        self.localizer = localizer
        self.tool = "pen"
        self.color_key = "red"
        self.width = float(DEFAULT_WIDTH)
        self.trail_s = DEFAULT_TRAIL_S
        self.canvases: list[AnnotationCanvas] = []
        self.toolbar: AnnotationToolbar | None = None
        self._drawing: AnnotationCanvas | None = None
        self._paused_menus: list[tuple[QWidget, Qt.ContextMenuPolicy]] = []
        self._locked_splitters: list[QSplitter] = []
        self.laser_timer = QTimer(self)
        self.laser_timer.setInterval(16)
        self.laser_timer.timeout.connect(self._tick_laser)
        self.sync_timer = QTimer(self)
        self.sync_timer.setSingleShot(True)
        self.sync_timer.setInterval(0)
        self.sync_timer.timeout.connect(self._sync_canvases)
        toggle.setCheckable(True)
        toggle.toggled.connect(self.set_active)

    @property
    def active(self) -> bool:
        return bool(self.canvases)

    def has_ink(self) -> bool:
        return any(canvas.has_ink() for canvas in self.canvases)

    # lifecycle ---------------------------------------------------------------
    def set_active(self, enabled: bool) -> None:
        if enabled and not self.active:
            self._activate()
        elif not enabled and self.active:
            self._deactivate()
        if self.toggle.isChecked() != self.active:
            self.toggle.blockSignals(True)
            self.toggle.setChecked(self.active)
            self.toggle.blockSignals(False)

    def _activate(self) -> None:
        specs = [(widgets, floating) for widgets, floating in self.regions() if widgets]
        if not specs:
            return
        for widgets, floating in specs:
            canvas = AnnotationCanvas(widgets, floating=floating, host=self.host)
            canvas.trail_s = self.trail_s
            self.canvases.append(canvas)
            for widget in widgets:
                widget.installEventFilter(self)
            self._pause_menus(widgets)
        self.host.installEventFilter(self)
        QApplication.instance().installEventFilter(self)
        if self.toolbar is None:
            self.toolbar = AnnotationToolbar(self, self.host)
            self.host.addToolBar(Qt.ToolBarArea.BottomToolBarArea, self.toolbar)
        self.toolbar.refresh()
        self.toolbar.show()
        if self.lock is not None:
            self.lock(True)
        self._sync_canvases()
        QTimer.singleShot(0, self._sync_canvases)

    def _deactivate(self) -> None:
        QApplication.instance().removeEventFilter(self)
        self.host.removeEventFilter(self)
        self.laser_timer.stop()
        self._drawing = None
        for canvas in self.canvases:
            for widget in canvas.tracked:
                try:
                    widget.removeEventFilter(self)
                except RuntimeError:
                    pass
            canvas.hide()
            canvas.deleteLater()
        self.canvases.clear()
        self._restore_menus()
        if self.toolbar is not None:
            self.toolbar.hide()
        if self.lock is not None:
            self.lock(False)

    def _pause_menus(self, widgets: list[QWidget]) -> None:
        for widget in widgets:
            for child in [widget, *widget.findChildren(QWidget)]:
                policy = child.contextMenuPolicy()
                if policy != Qt.ContextMenuPolicy.PreventContextMenu:
                    self._paused_menus.append((child, policy))
                    child.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)

    def _restore_menus(self) -> None:
        for widget, policy in self._paused_menus:
            try:
                widget.setContextMenuPolicy(policy)
            except RuntimeError:
                pass
        self._paused_menus.clear()

    # settings ----------------------------------------------------------------
    def set_tool(self, tool: str) -> None:
        self.tool = tool
        if self.toolbar is not None:
            self.toolbar.refresh()

    def set_color(self, key: str) -> None:
        if key in PEN_COLORS:
            self.color_key = key

    def set_trail(self, seconds: float) -> None:
        self.trail_s = min(max(float(seconds), 0.0), MAX_TRAIL_S)
        for canvas in self.canvases:
            canvas.trail_s = self.trail_s

    # clear -------------------------------------------------------------------
    def clear_with_animation(self) -> None:
        sheets = []
        for canvas in self.canvases:
            if canvas.has_ink():
                rect = QRect(canvas.mapToGlobal(QPoint(0, 0)), canvas.size())
                sheets.append((canvas.ink_image(), rect))
            canvas.clear_ink()        # the plot is visible again immediately
        if self.toolbar is not None:
            self.toolbar.refresh()
        if not sheets:
            return
        target = self.toggle.mapToGlobal(self.toggle.rect().center())
        try:
            _ClearAnimation(self.host, sheets, target)
        except Exception:
            pass                       # decoration only; ink is already cleared

    # geometry ----------------------------------------------------------------
    def _sync_canvases(self) -> None:
        for canvas in self.canvases:
            canvas.sync_geometry()

    def _canvas_at(self, global_point: QPoint) -> AnnotationCanvas | None:
        top = QApplication.topLevelAt(global_point)
        # Our floating sheets (point graphs) and the clear animation are
        # transparent for input but can still be the topmost window here.
        own = top is None or top is self.host or top.window() is self.host \
            or top in self.canvases or isinstance(top, _ClearAnimation)
        if not own:
            return None
        for canvas in self.canvases:
            if canvas.contains_global(global_point):
                return canvas
        return None

    def _tick_laser(self) -> None:
        alive = False
        for canvas in self.canvases:
            alive = canvas.prune_laser() or alive
        if not alive:
            self.laser_timer.stop()

    # input -------------------------------------------------------------------
    def eventFilter(self, watched, event):  # noqa: N802 - Qt API spelling
        kind = event.type()
        if kind in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide,
                    QEvent.Type.LayoutRequest, QEvent.Type.WindowStateChange):
            if watched is self.host or any(watched in canvas.tracked for canvas in self.canvases):
                self.sync_timer.start()
            return False
        if kind == QEvent.Type.ContextMenu:
            position = event.globalPos() if hasattr(event, "globalPos") else None
            return position is not None and self._canvas_at(position) is not None
        if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            if event.button() != Qt.MouseButton.RightButton:
                return False
            global_point = event.globalPosition().toPoint()
            canvas = self._canvas_at(global_point)
            if canvas is None:
                return False
            self._drawing = canvas
            local = QPointF(canvas.mapFromGlobal(global_point))
            if self.tool == "pen":
                canvas.begin_stroke(local, QColor(PEN_COLORS[self.color_key]), self.width)
            else:
                canvas.laser_down = True
                canvas.add_laser(local)
                self.laser_timer.start()
            return True
        if kind == QEvent.Type.MouseMove and self._drawing is not None:
            if not event.buttons() & Qt.MouseButton.RightButton:
                return False
            canvas = self._drawing
            local = QPointF(canvas.mapFromGlobal(event.globalPosition().toPoint()))
            if self.tool == "pen":
                canvas.extend_stroke(local)
            else:
                canvas.add_laser(local)
            return True
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.RightButton:
            canvas = self._drawing
            if canvas is None:
                return False
            self._drawing = None
            if self.tool == "pen":
                canvas.end_stroke()
                if self.toolbar is not None:
                    self.toolbar.clear_button.setEnabled(True)
            else:
                canvas.laser_down = False
            return True
        return False


def toolbar_stretch() -> QWidget:
    """Expanding toolbar spacer that pushes following controls to the right."""
    from PySide6.QtWidgets import QSizePolicy

    spacer = QWidget()
    spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    spacer.setProperty("glassSpacer", True)
    spacer.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    return spacer


def make_annotation_button(size: int) -> QToolButton:
    button = make_icon_only(QToolButton(), "annotate", "Annotation", size, flat=True)
    button.setCheckable(True)
    button.setObjectName("annotationToggle")
    return button


def lock_widgets(widgets: list[QWidget], splitter_roots: list[QWidget] = ()):
    """Return a lock(bool) callback that disables layout-changing controls."""
    saved: dict[int, bool] = {}

    def lock(enabled: bool) -> None:
        targets = [widget for widget in widgets if widget is not None]
        handles = []
        for root in splitter_roots:
            if root is None:
                continue
            splitters = [root] if isinstance(root, QSplitter) else []
            splitters += root.findChildren(QSplitter)
            for splitter in splitters:
                handles += [splitter.handle(index) for index in range(1, splitter.count())]
        for widget in targets + handles:
            if enabled:
                saved[id(widget)] = widget.isEnabled()
                widget.setEnabled(False)
            else:
                widget.setEnabled(saved.pop(id(widget), True))

    return lock
