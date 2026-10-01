"""v0.19D theme ripple, button press spring and liquid-glass scroll bars."""

from __future__ import annotations

import os
import time

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    from app.gui.effects import install_effects

    install_effects(app)
    return app


def _wait(qapp, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)


def test_fight_radius_is_pushed_back_then_wins():
    from app.gui.effects import _RippleOverlay

    r = _RippleOverlay.fight_radius
    assert r(0.0) == 0.0 and r(0.22) == pytest.approx(0.34)
    assert r(0.36) < r(0.22)                       # pushed back
    assert r(0.63) < r(0.52)                       # pushed back again
    assert r(1.0) > 1.0                            # the theme colour covers everything


def test_ripple_starts_at_the_pressed_button_and_cleans_up(qapp):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

    from app.gui import effects

    window = QWidget()
    layout = QVBoxLayout(window)
    button = QPushButton("Theme")
    layout.addWidget(button)
    window.resize(400, 300)
    window.show()
    qapp.processEvents()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    applied = []
    effects.theme_transition(lambda: applied.append(True), same_look=False)
    assert applied == [True]
    overlays = window.findChildren(effects._RippleOverlay)
    assert len(overlays) == 1
    center = button.mapTo(window, button.rect().center())
    assert overlays[0].origin == QPointF(center)
    assert not overlays[0].isEnabled() or overlays[0].testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    _wait(qapp, effects.RIPPLE_MS / 1000 + 0.3)
    assert window.findChildren(effects._RippleOverlay) == []
    effects.theme_transition(lambda: None, same_look=True)      # same look: fight, no snapshot
    fight = window.findChildren(effects._RippleOverlay)
    assert fight and fight[0].fight is not None and fight[0].snapshot is None
    window.close()


def test_press_spring_scales_and_overshoots_without_blocking_clicks(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

    from app.gui import effects

    window = QWidget()
    layout = QVBoxLayout(window)
    button = QPushButton("Export")
    button.setMinimumSize(160, 40)
    layout.addWidget(button)
    window.show()
    clicks = []
    button.clicked.connect(lambda: clicks.append(1))
    QTest.mousePress(button, Qt.MouseButton.LeftButton)
    _wait(qapp, 0.2)
    layer = window.findChildren(effects._PressLayer)[0]
    assert layer.scale == pytest.approx(effects.PRESS_SCALE, abs=0.004) and layer.depth > 0.8
    QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
    peak = 0.0
    for _ in range(60):
        _wait(qapp, 0.01)
        if window.findChildren(effects._PressLayer):
            peak = max(peak, layer.scale)
    assert peak > 1.003                                   # spring overshoot
    assert clicks == [1]
    _wait(qapp, 0.6)
    assert window.findChildren(effects._PressLayer) == []
    window.close()


def test_glass_scroll_bars_float_and_refract(qapp):
    from PySide6.QtWidgets import QApplication, QStyle, QTextEdit

    from app.gui.glass_scrollbars import EXTENT, GlassScrollStyle, handle_rect

    style = GlassScrollStyle()
    assert style.styleHint(QStyle.StyleHint.SH_ScrollBar_Transient) == 1
    assert style.pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent) == EXTENT
    previous = QApplication.style().name()
    QApplication.setStyle(style)
    try:
        editor = QTextEdit()
        editor.setPlainText("\n".join(f"line {i} with text under the glass" for i in range(400)))
        editor.resize(400, 300)
        editor.show()
        bar = editor.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        _wait(qapp, 0.25)
        # the bar lies over the content (transient): the viewport keeps the full width
        assert editor.viewport().width() >= editor.width() - 2 * editor.frameWidth() - 1
        rect = handle_rect(bar)
        assert rect.width() >= 6 and rect.height() >= 20
        bar.repaint()
        _wait(qapp, 0.2)
        helper = style._helpers.get(id(bar))
        assert helper is not None
        from app.gui import glass

        if glass.optical_glass_available():
            assert helper.image is not None and helper.image_rect == handle_rect(bar)
        editor.close()
    finally:
        from PySide6.QtWidgets import QStyleFactory

        QApplication.setStyle(QStyleFactory.create(previous))


def test_spring_stays_stable_when_frames_are_late(qapp):
    """A busy computer delivers animation frames late; the spring must not blow up."""
    from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

    from app.gui import effects

    window = QWidget()
    QVBoxLayout(window).addWidget(button := QPushButton("Busy"))
    window.show()
    layer = effects._PressLayer(button)
    layer.timer.stop()
    for _ in range(40):                       # 100 ms between frames
        layer._last -= 0.1
        layer._tick()
    assert layer.scale == pytest.approx(effects.PRESS_SCALE, abs=0.003)
    layer.release()
    values = []
    for _ in range(40):
        layer._last -= 0.1
        layer._tick()
        values.append(layer.scale)
    assert max(values) < 1.06 and abs(values[-1] - 1.0) < 0.01
    window.close()


def test_press_backdrop_shows_what_is_really_behind_the_button(qapp):
    """v0.19E: the ring a pressed (smaller) button uncovers is the real backdrop, not a
    flat rectangle: see-through containers must not paint the window colour over glass."""
    from PySide6.QtGui import QColor, QPainter
    from PySide6.QtWidgets import QHBoxLayout, QToolButton, QVBoxLayout, QWidget

    from app.gui import effects

    class Glass(QWidget):
        def paintEvent(self, _event):
            QPainter(self).fillRect(self.rect(), QColor("#FF0000"))

    window = QWidget()
    glass = Glass(window)
    QVBoxLayout(window).addWidget(glass)
    holder = QWidget(glass)                    # a see-through container, like the toolbar groups
    QVBoxLayout(glass).addWidget(holder)
    button = QToolButton()
    button.setText("A")
    QHBoxLayout(holder).addWidget(button)
    window.resize(200, 120)
    window.show()
    qapp.processEvents()
    backdrop = effects._backdrop(button).toImage()
    corner = backdrop.pixelColor(1, 1)
    assert (corner.red(), corner.green(), corner.blue()) == (255, 0, 0)
    alone = effects._button_only(button).toImage()
    assert alone.pixelColor(0, 0).alpha() < 255 or alone.hasAlphaChannel()
    layer = effects._PressLayer(button)
    layer.timer.stop()
    layer.depth = 1.0
    layer.repaint()
    layer._finish()
    window.close()
