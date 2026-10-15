"""v1.0.7: Move tool next to the pointer, F1–F4 select the plot tools, one-click X + Y cut button."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

NC = Path(__file__).parent / "data" / "labcontrol_sim_sweep.nc"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def win(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    from app.gui.main_window import MainWindow

    w = MainWindow()
    w.resize(1200, 850)
    w.show()
    yield w
    w.close()


def _pump(qapp, n=10):
    for _ in range(n):
        qapp.processEvents()


def test_move_tool_and_function_keys(win, qapp):
    from PySide6.QtGui import QKeySequence, QShortcut

    c = win.plot_interaction_controls
    layout = c.layout()
    order = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert order.index(c.btn_pan) == order.index(c.btn_pointer) + 1          # right of the pointer
    keys = {s.key().toString(): s for s in win.findChildren(QShortcut)}
    for key in ("F1", "F2", "F3", "F4"):
        assert key in keys
    assert "Shift+F1" in keys                                            # help moved off F1 in the Viewer
    assert "F1" in c.btn_pointer.toolTip() and "F2" in c.btn_pan.toolTip()
    keys["F2"].activated.emit()
    assert c.tool == "pan" and win.pan_tool.enabled and not win.rect_zoom.enabled
    keys["F4"].activated.emit()
    assert c.tool == "zoom" and win.rect_zoom.enabled and not win.pan_tool.enabled
    keys["F3"].activated.emit()
    assert c.tool == "share" and c.share_mode and not win.rect_zoom.enabled
    keys["F1"].activated.emit()
    assert c.tool == "pointer" and not c.share_mode and not win.pan_tool.enabled


def test_move_tool_drags_the_view(win, qapp):
    import numpy as np
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    win.open_file(str(NC))
    win.mode_combo.setCurrentIndex(1)
    _pump(qapp, 20)
    plot = win.plot_2d_widget
    view = plot.graphics_widget
    box = plot.plot_item.getViewBox() if hasattr(plot, "plot_item") else plot.img_item.getViewBox()
    win.plot_interaction_controls.select_tool("pan")
    before = np.array(box.viewRange())
    rect = box.sceneBoundingRect()
    start = view.mapFromScene(rect.center())
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(view.viewport(), start + QPoint(80, 40))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       start + QPoint(80, 40))
    after = np.array(box.viewRange())
    assert after[0][0] < before[0][0] and after[1][0] > before[1][0]       # dragged right / down
    win.plot_interaction_controls.select_tool("pointer")


def test_cut_button_shows_x_and_y(win, qapp):
    win.open_file(str(NC))
    win.mode_combo.setCurrentIndex(1)
    _pump(qapp, 20)
    plot = win.plot_2d_widget
    assert win.cuts_button.isEnabled()
    win.cuts_button.click()
    _pump(qapp)
    assert plot.cross_section_enabled and plot._cross.parts == {"x": True, "y": True}
    assert win.show_x_cut_action.isChecked() and win.show_y_cut_action.isChecked()
    ci = plot.graphics_widget.ci
    assert plot._cross.x_cut_plot in ci.items and plot._cross.y_cut_plot in ci.items
    win.show_y_cut_action.trigger()                                      # menu item turns one off
    _pump(qapp)
    assert plot._cross.parts == {"x": True, "y": False} and win.cuts_button.isChecked()
    win.cuts_button.click()
    _pump(qapp)
    assert not plot.cross_section_enabled and not win.cuts_button.isChecked()


def test_colorbar_title_shows_once(win, qapp):
    """v1.0.5 hid the colour-bar title together with the duplicate; v1.0.7 shows exactly one."""
    win.open_file(str(NC))
    win.mode_combo.setCurrentIndex(1)
    _pump(qapp, 20)
    for _ in range(2):                                               # first plot and a re-plot
        cb = win.plot_2d_widget.color_bar
        shown = [s for s in ("left", "right", "top", "bottom")
                 if cb.getAxis(s).isVisible() and cb.getAxis(s).label.isVisible()
                 and cb.getAxis(s).labelText]
        assert len(shown) == 1
        win.plot_2d_widget.update() if hasattr(win.plot_2d_widget, "update") else None
        win.mode_combo.setCurrentIndex(0)
        _pump(qapp, 5)
        win.mode_combo.setCurrentIndex(1)
        _pump(qapp, 20)


def test_pointer_tool_does_not_drag_the_data(win, qapp):
    """v1.0.8: with the Pointer (F1) a left-drag on the plot leaves the view where it is; Move (F2) still moves it."""
    import numpy as np
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    win.open_file(str(NC))
    win.mode_combo.setCurrentIndex(1)
    _pump(qapp, 20)
    plot = win.plot_2d_widget
    view = plot.graphics_widget
    box = plot.plot_item.getViewBox()

    def drag():
        rect = box.sceneBoundingRect()
        start = view.mapFromScene(rect.center() + rect.topLeft() * 0 + (rect.bottomRight() - rect.center()) * 0.3)
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        for k in range(1, 6):
            QTest.mouseMove(view.viewport(), start + QPoint(16 * k, 8 * k))
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                           start + QPoint(80, 40))
        _pump(qapp)

    win.plot_interaction_controls.select_tool("pointer")
    before = np.array(box.viewRange())
    drag()
    assert np.allclose(np.array(box.viewRange()), before)
    win.plot_interaction_controls.select_tool("pan")
    drag()
    assert not np.allclose(np.array(box.viewRange()), before)
    win.plot_interaction_controls.select_tool("pointer")
