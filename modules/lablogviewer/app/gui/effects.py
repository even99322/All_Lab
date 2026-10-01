"""Interface motion: theme ripple and button press spring (application wide).

Theme ripple
    When the appearance changes, every open window keeps a snapshot of its old
    look on a see-through overlay and a growing circle reveals the new theme,
    starting at the button that was pressed (other windows start at their
    centre). The circle grows to the farthest window corner. Where the look
    does not actually change (e.g. System is already dark and Dark is chosen),
    two colours fight at the circle's edge -- the edge is pushed back twice --
    and the theme colour wins.
    The overlay ignores the mouse and keyboard: the new theme is already live
    underneath, so nothing waits for the animation.

Button press
    Push and tool buttons shrink to 0.96 and darken (more along the top edge,
    only on the button's own pixels, so the shading follows its real shape)
    while held, and spring back with a small overshoot when released. Drawn on
    a see-through overlay: the real backdrop of the ancestors fills the ring the
    smaller button uncovers, so no rectangle appears on glass or gradients.
    Clicks, shortcuts and the button's own drawing are untouched.

LABLOGVIEWER_DISABLE_EFFECTS=1 turns both off.
"""

from __future__ import annotations

import math
import os
import time

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QRegion
from PySide6.QtWidgets import QAbstractButton, QApplication, QCheckBox, QPushButton, QRadioButton, QToolButton, QWidget

PRESS_SCALE = 0.96
RIPPLE_MS = 560
FIGHT_MS = 1150
ORIGIN_MEMORY_S = 2.0


def effects_enabled() -> bool:
    return os.environ.get("LABLOGVIEWER_DISABLE_EFFECTS", "") != "1"


def _valid(obj) -> bool:
    try:
        from shiboken6 import isValid

        return obj is not None and isValid(obj)
    except Exception:
        return obj is not None


# -- where the user last pressed ---------------------------------------------------------------
class _PressTracker(QObject):
    def __init__(self):
        super().__init__()
        self.widget = None
        self.global_pos = None
        self.when = 0.0

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API spelling
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
            self.widget = watched
            self.global_pos = event.globalPosition()
            self.when = time.monotonic()
        return False

    def origin_in(self, window: QWidget) -> QPointF | None:
        """Centre of the recently pressed button, in ``window`` coordinates, if it is inside it."""
        if time.monotonic() - self.when > ORIGIN_MEMORY_S or not _valid(self.widget):
            return None
        widget = self.widget
        target = widget
        while target is not None and not isinstance(target, QAbstractButton):
            target = target.parentWidget()
        target = target or widget
        if target.window() is not window:
            return None
        return QPointF(target.mapTo(window, target.rect().center()))


_tracker: _PressTracker | None = None


# -- theme ripple ------------------------------------------------------------------------------
class _RippleOverlay(QWidget):
    def __init__(self, window: QWidget, snapshot: QPixmap | None, origin: QPointF, fight: tuple[QColor, QColor] | None):
        super().__init__(window)
        self.setObjectName("themeRipple")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setGeometry(window.rect())
        self.snapshot = snapshot
        self.origin = origin
        self.fight = fight
        corners = [QPointF(0, 0), QPointF(self.width(), 0), QPointF(0, self.height()), QPointF(self.width(), self.height())]
        self.max_radius = max(math.dist((origin.x(), origin.y()), (c.x(), c.y())) for c in corners) + 2
        self.progress = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(FIGHT_MS if fight else RIPPLE_MS)
        self.animation.setEasingCurve(QEasingCurve.Type.Linear if fight else QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._step)
        self.animation.finished.connect(self.deleteLater)
        self.raise_()
        self.show()
        self.animation.start()

    @staticmethod
    def fight_radius(t: float) -> float:
        """Fraction of the full radius: grows, is pushed back twice, then wins."""
        keys = [(0.0, 0.0), (0.22, 0.34), (0.36, 0.2), (0.52, 0.5), (0.63, 0.4), (1.0, 1.04)]
        for (t0, r0), (t1, r1) in zip(keys, keys[1:]):
            if t <= t1:
                u = (t - t0) / (t1 - t0)
                u = u * u * (3 - 2 * u)                      # smooth in and out between keys
                return r0 + (r1 - r0) * u
        return keys[-1][1]

    def _step(self, value) -> None:
        self.progress = float(value)
        self.update()

    def radius(self) -> float:
        if self.fight:
            return self.fight_radius(self.progress) * self.max_radius
        return self.progress * self.max_radius

    def paintEvent(self, _event):  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.radius()
        circle = QPainterPath()
        circle.addEllipse(self.origin, r, r)
        if self.snapshot is not None:
            outside = QPainterPath()
            outside.addRect(QRectF(self.rect()))
            outside = outside.subtracted(circle)
            painter.setClipPath(outside)
            painter.drawPixmap(0, 0, self.snapshot)
            painter.setClipping(False)
        if self.fight:
            self._paint_fight(painter, r)

    def _paint_fight(self, painter: QPainter, r: float) -> None:
        theme_color, rival = self.fight
        fade = 1.0 if self.progress < 0.8 else max(0.0, 1 - (self.progress - 0.8) / 0.2)
        band = 30.0
        # the contested edge wobbles while the two colours push against each other
        wobble = 7.0 * (1 - self.progress) + 1.0
        edge = QPainterPath()
        steps = 96
        for i in range(steps + 1):
            a = 2 * math.pi * i / steps
            rr = r + wobble * math.sin(9 * a + self.progress * 40)
            point = QPointF(self.origin.x() + rr * math.cos(a), self.origin.y() + rr * math.sin(a))
            edge.moveTo(point) if i == 0 else edge.lineTo(point)
        edge.closeSubpath()
        outer = QPainterPath()
        outer.addEllipse(self.origin, r + band, r + band)
        ring = outer.subtracted(edge)
        gradient = QRadialGradient(self.origin, r + band)
        rival_color = QColor(rival)
        rival_color.setAlphaF(0.85 * fade)
        clear = QColor(rival)
        clear.setAlphaF(0.0)
        stop = max(0.0, min(1.0, r / (r + band)))
        gradient.setColorAt(0.0, clear)
        gradient.setColorAt(stop, rival_color)
        gradient.setColorAt(1.0, clear)
        painter.fillPath(ring, gradient)
        # the theme colour pushes from inside
        inner = QPainterPath()
        inner.addEllipse(self.origin, max(0.0, r - band), max(0.0, r - band))
        inside_band = edge.subtracted(inner)
        push = QRadialGradient(self.origin, r)
        glow = QColor(theme_color)
        glow.setAlphaF(0.55 * fade)
        none = QColor(theme_color)
        none.setAlphaF(0.0)
        push.setColorAt(max(0.0, (r - band) / max(r, 1.0)), none)
        push.setColorAt(1.0, glow)
        painter.fillPath(inside_band, push)
        winner = QColor(theme_color)
        winner.setAlphaF(0.9 * fade)
        painter.setPen(QPen(winner, 3.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(edge)


def _windows() -> list[QWidget]:
    return [w for w in QApplication.topLevelWidgets()
            if w.isVisible() and w.width() > 0 and w.height() > 0 and not w.inherits("QMenu")
            and w.windowType() not in (Qt.WindowType.ToolTip, Qt.WindowType.Popup)]


def theme_transition(apply, same_look: bool, theme_color: str | None = None, rival_color: str | None = None) -> None:
    """Run ``apply()`` (switches the theme) with the ripple on every open window."""
    if not effects_enabled() or QApplication.instance() is None:
        apply()
        return
    windows = _windows()
    snapshots = {}
    if not same_look:
        for window in windows:
            try:
                snapshots[window] = window.grab()
            except Exception:
                snapshots[window] = None
    apply()
    for window in windows:
        if not _valid(window):
            continue
        origin = (_tracker.origin_in(window) if _tracker is not None else None) \
            or QPointF(window.width() / 2, window.height() / 2)
        fight = (QColor(theme_color or "#297FA8"), QColor(rival_color or "#101418")) if same_look else None
        if same_look or snapshots.get(window) is not None:
            _RippleOverlay(window, snapshots.get(window), origin, fight)


# -- button press spring -----------------------------------------------------------------------
def _backdrop(button: QAbstractButton) -> QPixmap:
    """What is behind the button: each ancestor's own painting (without its children)
    under the button's rectangle, so the ring a smaller button uncovers matches the real
    toolbar glass or gradient instead of a flat colour."""
    ratio = button.devicePixelRatioF()
    pixmap = QPixmap(QSize(max(1, round(button.width() * ratio)), max(1, round(button.height() * ratio))))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    chain = []
    widget = button.parentWidget()
    while widget is not None:
        chain.append(widget)
        if widget.isWindow():
            break
        widget = widget.parentWidget()
    painter = QPainter(pixmap)
    for ancestor in reversed(chain):
        offset = button.mapTo(ancestor, QPoint(0, 0))
        # Only widgets that really fill their background get one; DrawWindowBackground
        # on a see-through container would cover the glass painted below it.
        fills = ancestor.isWindow() or ancestor.autoFillBackground() \
            or ancestor.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        flags = QWidget.RenderFlag.DrawWindowBackground if fills else QWidget.RenderFlag(0)
        ancestor.render(painter, QPoint(0, 0), QRegion(QRect(offset, button.size())), flags)
    painter.end()
    return pixmap


def _button_only(button: QAbstractButton) -> QPixmap:
    """The button's own drawing on a transparent pixmap (corners stay see-through)."""
    ratio = button.devicePixelRatioF()
    pixmap = QPixmap(QSize(max(1, round(button.width() * ratio)), max(1, round(button.height() * ratio))))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    button.render(pixmap, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
    return pixmap


def _coverage_and_light(button_pixmap: QPixmap, backdrop: QPixmap) -> tuple[float, bool]:
    """How much of the rectangle the button paints itself (icon-only tool buttons paint
    little), and whether what is behind it is light."""
    image = button_pixmap.toImage()
    back = backdrop.toImage()
    width, height = image.width(), image.height()
    if width == 0 or height == 0:
        return 1.0, True
    step = max(1, min(width, height) // 12)
    painted = total = 0
    light = 0.0
    for y in range(0, height, step):
        for x in range(0, width, step):
            total += 1
            if image.pixelColor(x, y).alpha() > 40:
                painted += 1
            light += back.pixelColor(x, y).lightnessF() if not back.isNull() else 1.0
    return painted / total, light / total >= 0.5


def _pressed_look(pixmap: QPixmap, depth: float, light: bool) -> QPixmap:
    """Shade only the button's own pixels, more along the top edge (pressed in), so the
    shading follows its real shape; darker on light surfaces, lighter on dark ones."""
    if depth <= 0.01:
        return pixmap
    shaded = QPixmap(pixmap)
    painter = QPainter(shaded)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
    size = pixmap.deviceIndependentSize()
    tone = (0, 0, 0) if light else (255, 255, 255)
    strength = 1.0 if light else 0.55
    gradient = QLinearGradient(0, 0, 0, size.height())
    gradient.setColorAt(0.0, QColor(*tone, int(70 * depth * strength)))
    gradient.setColorAt(0.45, QColor(*tone, int(26 * depth * strength)))
    gradient.setColorAt(1.0, QColor(*tone, int(14 * depth * strength)))
    painter.fillRect(QRectF(0, 0, size.width(), size.height()), gradient)
    painter.end()
    return shaded


class _PressLayer(QWidget):
    """The button drawn scaled over itself while pressed and during the spring back."""

    def __init__(self, button: QAbstractButton):
        window = button.window()
        super().__init__(window)
        self.setObjectName("pressSpring")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.button = button
        self.backdrop = _backdrop(button)
        self.pixmap = _button_only(button)
        coverage, self.light = _coverage_and_light(self.pixmap, self.backdrop)
        self.icon_only = coverage < 0.35          # glass-capsule tool buttons: mostly an icon
        self.scale = 1.0
        self.velocity = 0.0
        self.target = PRESS_SCALE
        self.depth = 0.0                      # pressed shading 0..1
        self.released = False
        self._refreshed = False
        self._last = time.monotonic()
        self._place()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.raise_()
        self.show()
        self.timer.start()

    def _place(self) -> None:
        if not _valid(self.button):
            return
        top_left = self.button.mapTo(self.parentWidget(), self.button.rect().topLeft())
        self.setGeometry(top_left.x(), top_left.y(), self.button.width(), self.button.height())

    def release(self) -> None:
        self.released = True
        self.target = 1.0

    def _tick(self) -> None:
        if not _valid(self.button) or not self.button.isVisible():
            self._finish()
            return
        if self.released and not self._refreshed:
            # the click may have changed the button (checked, new text): spring back with its new look
            self._refreshed = True
            self.hide()
            self.pixmap = _button_only(self.button)
            self.show()
        now = time.monotonic()
        dt = min(0.1, now - self._last)
        self._last = now
        # damped spring: pressing is quick and firm, the release overshoots a little.
        # Small fixed sub-steps keep it stable even when frames arrive late (busy computer).
        stiffness, damping = (900.0, 42.0) if not self.released else (520.0, 13.0)
        steps = max(1, int(dt / 0.004 + 0.5))
        h = dt / steps
        for _ in range(steps):
            force = stiffness * (self.target - self.scale) - damping * self.velocity
            self.velocity += force * h
            self.scale += self.velocity * h
        self.scale = min(1.06, max(0.9, self.scale))
        depth_target = 0.0 if self.released else 1.0
        self.depth += (depth_target - self.depth) * min(1.0, dt * (22 if not self.released else 14))
        self._place()
        self.update()
        if self.released and abs(self.scale - 1.0) < 0.0015 and abs(self.velocity) < 0.02 and self.depth < 0.02:
            self._finish()

    def _finish(self) -> None:
        self.timer.stop()
        self.hide()
        self.deleteLater()

    def paintEvent(self, _event):  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect())
        painter.drawPixmap(0, 0, self.backdrop)              # what the smaller button uncovers
        painter.translate(rect.center())
        painter.scale(self.scale, self.scale)
        painter.translate(-rect.center())
        if self.icon_only and self.depth > 0.01:
            # an icon on glass: a soft pressed capsule inside the button's own bounds
            pill = rect.adjusted(2, 3, -2, -3)
            radius = min(pill.height(), pill.width()) / 2
            tone = QColor(0, 0, 0, int(34 * self.depth)) if self.light else QColor(255, 255, 255, int(30 * self.depth))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(tone)
            painter.drawRoundedRect(pill, radius, radius)
        painter.drawPixmap(0, 0, _pressed_look(self.pixmap, self.depth, self.light))


class _ButtonSpring(QObject):
    def __init__(self):
        super().__init__()
        self.layers: dict[int, _PressLayer] = {}

    @staticmethod
    def applies_to(widget) -> bool:
        return (isinstance(widget, (QPushButton, QToolButton)) and not isinstance(widget, (QCheckBox, QRadioButton))
                and widget.isEnabled() and widget.isVisible() and widget.width() > 2 and widget.height() > 2
                and widget.window() is not widget)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API spelling
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton \
                and self.applies_to(watched):
            old = self.layers.pop(id(watched), None)
            if old is not None and _valid(old):
                old._finish()
            try:
                self.layers[id(watched)] = _PressLayer(watched)
            except Exception:
                pass
        elif kind in (QEvent.Type.MouseButtonRelease, QEvent.Type.Leave, QEvent.Type.Hide) \
                and id(watched) in self.layers:
            layer = self.layers.pop(id(watched))
            if _valid(layer):
                layer.release()
        return False


_spring: _ButtonSpring | None = None


def install_effects(app) -> None:
    """Button spring + press tracking for the theme ripple (idempotent)."""
    global _tracker, _spring
    if _tracker is None:
        _tracker = _PressTracker()
        app.installEventFilter(_tracker)
    if _spring is None and effects_enabled():
        _spring = _ButtonSpring()
        app.installEventFilter(_spring)
