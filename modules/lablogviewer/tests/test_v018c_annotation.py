"""v0.18C screen annotation: display-only pen / laser overlay."""

from __future__ import annotations

import time

import numpy as np
import pytest


@pytest.fixture(scope="module")
def qapp():
    import os
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _drag(qapp, widget, points, button):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    def send(kind, point, pressed, buttons):
        global_point = widget.mapToGlobal(point)
        qapp.sendEvent(widget, QMouseEvent(kind, QPointF(point), QPointF(global_point), pressed, buttons,
                                           Qt.KeyboardModifier.NoModifier))

    send(QEvent.Type.MouseButtonPress, points[0], button, button)
    for point in points[1:]:
        send(QEvent.Type.MouseMove, point, Qt.MouseButton.NoButton, button)
    send(QEvent.Type.MouseButtonRelease, points[-1], button, Qt.MouseButton.NoButton)


def test_smoothing_keeps_endpoints():
    from PySide6.QtCore import QPointF
    from app.gui.annotation import smooth_points

    points = [QPointF(0, 0), QPointF(10, 0), QPointF(10, 10), QPointF(20, 10)]
    smooth = smooth_points(points)
    assert smooth[0] == points[0] and smooth[-1] == points[-1]
    assert len(smooth) > len(points)


def test_viewer_annotation_is_display_only_and_locks_layout(qapp):
    from PySide6.QtCore import QPoint, Qt
    from app.gui.main_window import MainWindow
    from tests.real_data import BIG_FILE

    if not BIG_FILE.exists():
        pytest.skip("Real Viewer fixture unavailable")
    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    window.open_file(str(BIG_FILE))
    qapp.processEvents()
    plot = window.plot_widget.plot_widget
    view_box = plot.getViewBox()
    x_before, y_before = window.plot_widget._curve.getData()
    range_before = [list(axis) for axis in view_box.viewRange()]
    policy_before = plot.contextMenuPolicy()
    try:
        window.annotation_button.click()
        session = window.annotation
        assert session.active and session.toolbar.isVisible()
        assert not window.mode_combo.isEnabled() and not window.pane_layout_combo.isEnabled()
        assert plot.contextMenuPolicy() == Qt.ContextMenuPolicy.PreventContextMenu

        viewport = plot.viewport()
        _drag(qapp, viewport, [QPoint(300 + 10 * i, 200 + 3 * i) for i in range(12)], Qt.MouseButton.RightButton)
        assert sum(len(canvas.strokes) for canvas in session.canvases) == 1
        # Right-drag draws; it no longer zooms the plot, and data are untouched.
        assert [list(axis) for axis in view_box.viewRange()] == range_before
        x_after, y_after = window.plot_widget._curve.getData()
        assert np.array_equal(x_before, x_after) and np.array_equal(y_before, y_after)

        session.toolbar.laser_button.click()
        assert session.tool == "laser"
        assert not session.toolbar._swatch_actions["red"].isVisible()      # pen colors hidden
        assert session.toolbar._laser_swatch_action.isVisible()            # laser: red only
        session.set_trail(0.1)
        _drag(qapp, viewport, [QPoint(200 + 10 * i, 300) for i in range(8)], Qt.MouseButton.RightButton)
        assert any(canvas.laser for canvas in session.canvases)
        time.sleep(0.2)
        session._tick_laser()
        assert not any(canvas.laser for canvas in session.canvases)
        assert sum(len(canvas.strokes) for canvas in session.canvases) == 1   # laser never persists

        session.clear_with_animation()
        assert sum(len(canvas.strokes) for canvas in session.canvases) == 0

        window.annotation_button.click()
        assert not session.active and not session.toolbar.isVisible()
        assert window.mode_combo.isEnabled() and window.pane_layout_combo.isEnabled()
        assert plot.contextMenuPolicy() == policy_before
    finally:
        window.close()
        qapp.processEvents()


def test_pen_colors_are_the_four_requested_and_laser_is_red():
    from app.gui.annotation import LASER_COLOR, PEN_COLORS

    assert list(PEN_COLORS) == ["red", "blue", "yellow", "green"]
    assert LASER_COLOR.upper().startswith("#FF")


def test_every_host_has_an_annotation_button(qapp, tmp_path):
    import inspect
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow
    from app.gui import three_d_window, yig_fitting_window

    browser = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    try:
        assert browser.annotation.toggle is browser.annotation_button
        assert not browser.annotation.active
    finally:
        browser.close()
    assert "AnnotationSession" in inspect.getsource(three_d_window.ThreeDAnalysisWindow.__init__)
    assert "AnnotationSession" in inspect.getsource(yig_fitting_window.YigMirrorFittingWindow._install_annotation)
