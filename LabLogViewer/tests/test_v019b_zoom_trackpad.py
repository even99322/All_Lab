"""v0.19B Viewer rectangle Zoom tool and Mac trackpad gestures."""

from __future__ import annotations

import os

import pytest

from tests.real_data import BIG_FILE


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _send_mouse(qapp, widget, kind, point, button, buttons):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    event = QMouseEvent(kind, QPointF(point), QPointF(widget.mapToGlobal(point)), button, buttons,
                        Qt.KeyboardModifier.NoModifier)
    qapp.sendEvent(widget, event)


@pytest.mark.skipif(not BIG_FILE.exists(), reason="fixture unavailable")
def test_rectangle_zoom_and_step_back(qapp):
    from PySide6.QtCore import QEvent, QPoint, Qt
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    window.open_file(str(BIG_FILE))
    qapp.processEvents()
    try:
        controls = window.plot_interaction_controls
        assert controls.btn_zoom is not None
        controls.btn_zoom.click()
        assert controls.zoom_mode and window.rect_zoom.enabled and not controls.share_mode
        view = window.plot_widget.plot_widget
        box = view.getViewBox()
        before = [list(axis) for axis in box.viewRange()]
        viewport = view.viewport()
        center = viewport.rect().center()
        start, end = center - QPoint(120, 60), center + QPoint(80, 40)
        _send_mouse(qapp, viewport, QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        _send_mouse(qapp, viewport, QEvent.Type.MouseMove, end, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        _send_mouse(qapp, viewport, QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
        qapp.processEvents()
        zoomed = box.viewRange()
        assert zoomed[0][1] - zoomed[0][0] < 0.6 * (before[0][1] - before[0][0])
        assert window.rect_zoom.history_depth(box) == 1
        _send_mouse(qapp, viewport, QEvent.Type.MouseButtonPress, center, Qt.MouseButton.RightButton, Qt.MouseButton.RightButton)
        qapp.processEvents()
        back = box.viewRange()
        assert back[0] == pytest.approx(before[0], rel=1e-6)
        controls.btn_pointer.click()
        assert not window.rect_zoom.enabled
    finally:
        window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="fixture unavailable")
def test_trackpad_scroll_pans_and_pinch_zooms(qapp):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QNativeGestureEvent, QPointingDevice, QWheelEvent
    from app.gui.main_window import MainWindow
    from app.gui.plot_gestures import install_trackpad_gestures

    install_trackpad_gestures(qapp)
    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    window.open_file(str(BIG_FILE))
    qapp.processEvents()
    try:
        view = window.plot_widget.plot_widget
        box = view.getViewBox()
        viewport = view.viewport()
        center = QPointF(viewport.rect().center())
        (x0, x1), _ = box.viewRange()
        swipe = QWheelEvent(center, QPointF(viewport.mapToGlobal(center.toPoint())), QPoint(40, 0), QPoint(0, 0),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
        qapp.sendEvent(viewport, swipe)
        (p0, p1), _ = box.viewRange()
        assert p1 - p0 == pytest.approx(x1 - x0, rel=1e-6) and p0 < x0            # panned, same width
        mouse = QWheelEvent(center, QPointF(viewport.mapToGlobal(center.toPoint())), QPoint(0, 0), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        assert not install_trackpad_gestures(qapp).eventFilter(viewport, mouse)   # wheel keeps default zoom
        pinch = QNativeGestureEvent(Qt.NativeGestureType.ZoomNativeGesture, QPointingDevice.primaryPointingDevice(),
                                    2, center, center, QPointF(viewport.mapToGlobal(center.toPoint())), 0.25,
                                    QPointF(0, 0))
        (w0, w1), _ = box.viewRange()
        qapp.sendEvent(viewport, pinch)
        (z0, z1), _ = box.viewRange()
        assert (z1 - z0) < (w1 - w0)                                               # pinched in
    finally:
        window.close()
