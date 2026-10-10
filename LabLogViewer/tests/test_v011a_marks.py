"""v0.11A Basic Mark System acceptance and regression tests."""

from __future__ import annotations

import os
from time import perf_counter

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.mark_model import MAX_MARKS, POINT_MARK, MarkManager
from tests.real_data import BIG_FILE, FLUX_FILE, S31_FILE, SMALL_FILE


pytestmark = pytest.mark.skipif(
    not all(path.exists() for path in (BIG_FILE, FLUX_FILE, S31_FILE, SMALL_FILE)),
    reason="Required workspace real-data fixtures are unavailable.",
)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _combo_index(combo, name: str, base_channel: str | None = None) -> int:
    for index in range(combo.count()):
        candidate = combo.itemData(index)
        if candidate is None or candidate.name != name:
            continue
        if base_channel is None or candidate.base_channel == base_channel:
            return index
    return -1


def _choose_point(window) -> None:
    window.mark_tool_combo.setCurrentIndex(window.mark_tool_combo.findData(POINT_MARK))


def test_mark_ids_limit_reuse_delete_and_clear():
    manager = MarkManager()
    manager.set_1d_context("trace", np.arange(12), np.arange(12) ** 2,
                           x_name="X", y_name="Y")
    marks = [manager.add_nearest(index, index ** 2) for index in range(MAX_MARKS)]
    assert [mark.mark_id for mark in marks] == [f"M{i}" for i in range(1, 11)]
    assert manager.add_nearest(11, 121) is None
    assert manager.delete(2)
    assert [mark.mark_id for mark in manager.marks()] == ["M1"] + [f"M{i}" for i in range(3, 11)]
    assert manager.add_nearest(11, 121).mark_id == "M2"
    manager.clear()
    assert manager.marks() == []
    assert manager.add_nearest(0, 0).mark_id == "M1"


def test_nearest_1d_sample_drag_and_transform_refresh():
    manager = MarkManager()
    x = np.array([5.000, 5.001, 5.002])
    magnitude = np.array([-25.0, -20.0, -30.0])
    manager.set_1d_context("same sample context", x, magnitude,
                           x_name="Frequency", y_name="S21", x_unit="GHz", y_unit="dB")
    mark = manager.add_nearest(5.00137, -20.2)
    assert mark.sample_index == 1
    assert mark.x == pytest.approx(5.001)
    assert mark.value == pytest.approx(-20.0)

    moved = manager.move_nearest(1, 5.0019, -29.8)
    assert moved.sample_index == 2
    phase = np.array([10.0, 20.0, 30.0])
    changed = manager.set_1d_context("same sample context", x, phase,
                                     x_name="Frequency", y_name="S21",
                                     x_unit="GHz", y_unit="deg")
    assert not changed
    assert manager.marks()[0].sample_index == 2
    assert manager.marks()[0].value == pytest.approx(30.0)
    assert manager.marks()[0].y_unit == "deg"


def test_transform_refresh_preserves_original_index_across_nonfinite_filter():
    manager = MarkManager()
    x = np.arange(4, dtype=float)
    manager.set_1d_context("trace", x, [10.0, np.nan, 30.0, 40.0],
                           x_name="X", y_name="Y")
    assert manager.add_nearest(2.0, 30.0).sample_index == 2
    manager.set_1d_context("trace", x, [100.0, 200.0, 300.0, 400.0],
                           x_name="X", y_name="Y")
    assert manager.marks()[0].sample_index == 2
    assert manager.marks()[0].value == pytest.approx(300.0)


def test_nearest_2d_grid_cell_coordinate_value_and_drag():
    manager = MarkManager()
    x = np.array([1.0, 2.0, 4.0])
    y = np.array([10.0, 20.0])
    z = np.array([[11.0, 12.0, 14.0], [21.0, 22.0, 24.0]])
    manager.set_2d_context("grid", x, y, z, x_name="Frequency", y_name="Current",
                           value_name="S21", x_unit="GHz", y_unit="mA", value_unit="dB")
    mark = manager.add_nearest(2.2, 18.0)
    assert (mark.x_index, mark.y_index) == (1, 1)
    assert (mark.x, mark.y, mark.value) == pytest.approx((2.0, 20.0, 22.0))
    moved = manager.move_nearest(1, 3.8, 9.0)
    assert (moved.x_index, moved.y_index) == (2, 0)
    assert (moved.x, moved.y, moved.value) == pytest.approx((4.0, 10.0, 14.0))


def test_context_change_clears_incompatible_marks():
    manager = MarkManager()
    manager.set_1d_context("trace 1", [0, 1], [1, 2], x_name="X", y_name="Y")
    manager.add_nearest(0, 1)
    assert manager.set_1d_context("trace 2", [0, 1], [5, 6], x_name="X", y_name="Y")
    assert manager.marks() == []


def test_10001_point_nearest_lookup_is_responsive():
    manager = MarkManager()
    x = np.linspace(4.0e9, 5.0e9, 10001)
    y = np.sin(np.linspace(0, 100, 10001))
    manager.set_1d_context("long trace", x, y, x_name="Frequency", y_name="S21")
    started = perf_counter()
    for index in range(10):
        if index == 0:
            manager.add_nearest(float(x[index * 997]), float(y[index * 997]))
        else:
            manager.move_nearest(1, float(x[index * 997]), float(y[index * 997]))
    assert perf_counter() - started < 1.0


def test_real_1d_marks_match_displayed_data_and_transform_updates(qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.open_file(str(SMALL_FILE))
    x, y = window.plot_widget._curve.getData()
    sample = 211
    _choose_point(window)
    window._place_mark(0, float(x[sample]), float(y[sample]))
    mark = window._mark_managers[0].marks()[0]
    assert mark.sample_index == sample
    assert (mark.x, mark.value) == pytest.approx((x[sample], y[sample]))
    assert set(window._mark_overlays[0]._targets) == {1}

    for transform in ("Real", "Imaginary", "Magnitude", "Phase"):
        window.transform_combo.setCurrentIndex(window.transform_combo.findText(transform))
        current_x, current_y = window.plot_widget._curve.getData()
        mark = window._mark_managers[0].marks()[0]
        assert mark.sample_index == sample
        assert (mark.x, mark.value) == pytest.approx((current_x[sample], current_y[sample]))

    window.transform_combo.setCurrentIndex(window.transform_combo.findText("Magnitude"))
    window.db_checkbox.setChecked(True)
    _, current_y = window.plot_widget._curve.getData()
    mark = window._mark_managers[0].marks()[0]
    assert mark.value == pytest.approx(current_y[sample])
    assert mark.y_unit == "dB"
    window.close()


def test_add_mark_button_places_exactly_one_mark_per_activation(qapp):
    from PySide6.QtCore import QPointF, Qt
    from app.gui.main_window import MainWindow

    class PlotClick:
        def __init__(self, position):
            self._position = position
            self.accepted = False

        def button(self):
            return Qt.LeftButton

        def scenePos(self):
            return self._position

        def accept(self):
            self.accepted = True

    window = MainWindow()
    window.resize(1200, 780)
    window.show()
    window.open_file(str(SMALL_FILE))
    qapp.processEvents()
    x, y = window.plot_widget._curve.getData()
    view_box = window.plot_widget.plot_widget.getPlotItem().getViewBox()
    scene_position = view_box.mapViewToScene(QPointF(float(x[250]), float(y[250])))
    event = PlotClick(scene_position)

    _choose_point(window)
    assert window._mark_overlays[0].placement_mode
    window._mark_overlays[0]._on_scene_clicked(event)
    assert event.accepted
    assert [mark.mark_id for mark in window._mark_managers[0].marks()] == ["M1"]
    assert not window._mark_overlays[0].placement_mode
    assert not window.add_mark_button.isChecked()

    window._mark_overlays[0]._on_scene_clicked(event)
    assert [mark.mark_id for mark in window._mark_managers[0].marks()] == ["M1"]
    window.close()


def test_real_iq_derived_axes_are_safe_and_exact(qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.open_file(str(SMALL_FILE))
    imaginary = _combo_index(window.x_combo, "Imaginary", "VNA - S21")
    real = _combo_index(window.y_combo, "Real", "VNA - S21")
    assert imaginary >= 0 and real >= 0
    window.x_combo.setCurrentIndex(imaginary)
    window.y_combo.setCurrentIndex(real)
    x, y = window.plot_widget._curve.getData()
    sample = 123
    _choose_point(window)
    window._place_mark(0, float(x[sample]), float(y[sample]))
    mark = window._mark_managers[0].marks()[0]
    assert mark.sample_index == sample
    assert (mark.x, mark.value) == pytest.approx((x[sample], y[sample]))
    normal_x = _combo_index(window.x_combo, "Frequency", "VNA - S21")
    window.x_combo.setCurrentIndex(normal_x)
    assert window._mark_managers[0].marks() == []
    window.close()


@pytest.mark.parametrize("path,channel", [(BIG_FILE, "VNA - S21"), (S31_FILE, "VNA - S31")])
def test_real_generic_sij_1d_marks(path, channel, qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.open_file(str(path))
    assert window.y_combo.currentData().base_channel == channel
    x, y = window.plot_widget._curve.getData()
    index = len(x) // 3
    _choose_point(window)
    window._place_mark(0, float(x[index]), float(y[index]))
    mark = window._mark_managers[0].marks()[0]
    assert (mark.x, mark.value) == pytest.approx((x[index], y[index]))
    window.close()


@pytest.mark.parametrize("path", [BIG_FILE, FLUX_FILE])
def test_real_regular_and_flux_2d_marks_match_grid_and_layout(path, qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.resize(1300, 850)
    window.show()
    window.open_file(str(path))
    window.mode_combo.setCurrentIndex(1)
    qapp.processEvents()
    grid = window.plot_2d_widget._grid
    ix, iy = len(grid.x_values) // 3, len(grid.y_values) // 2
    _choose_point(window)
    window._place_mark(1, float(grid.x_values[ix]), float(grid.y_values[iy]))
    mark = window._mark_managers[1].marks()[0]
    assert (mark.x_index, mark.y_index) == (ix, iy)
    assert (mark.x, mark.y, mark.value) == pytest.approx(
        (grid.x_values[ix], grid.y_values[iy], grid.z_values[iy, ix])
    )

    original = (mark.x, mark.y, mark.value)
    window.plot_2d_widget.set_color_range(float(np.nanmin(grid.z_values)),
                                          float(np.nanmax(grid.z_values)))
    assert (mark.x, mark.y, mark.value) == pytest.approx(original)
    window.plot_2d_widget.view_box.setRange(
        xRange=(grid.x_values[ix - 1], grid.x_values[ix + 1]), padding=0
    )
    assert window._mark_overlays[1]._targets[1].pos().x() == pytest.approx(mark.x)
    window.maximize_plot_button.setChecked(True)
    qapp.processEvents()
    assert window._mark_overlays[1]._targets[1].isVisible()
    window.maximize_plot_button.setChecked(False)
    assert window._mark_managers[1].marks()[0].value == pytest.approx(original[2])
    window.close()


def test_real_flux_10001_point_trace_mark_and_drag(qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.open_file(str(FLUX_FILE))
    x, y = window.plot_widget._curve.getData()
    assert len(x) == 10001
    started = perf_counter()
    _choose_point(window)
    window._place_mark(0, float(x[7000]), float(y[7000]))
    window._move_mark(0, 1, float(x[8000]), float(y[8000]))
    assert perf_counter() - started < 1.0
    mark = window._mark_managers[0].marks()[0]
    assert mark.sample_index == 8000
    assert (mark.x, mark.value) == pytest.approx((x[8000], y[8000]))
    window.close()


def test_trace_change_clears_marks_and_keyboard_navigation_still_works(qapp):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.open_file(str(BIG_FILE))
    x, y = window.plot_widget._curve.getData()
    _choose_point(window)
    window._place_mark(0, float(x[10]), float(y[10]))
    window.log_entries.select_row(1)
    assert window._mark_managers[0].marks() == []

    window.show()
    window.activateWindow()
    qapp.processEvents()
    before = window.log_entries.current_row()
    event = QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier)
    qapp.sendEvent(window, event)
    assert window.log_entries.current_row() == before + 1
    window.close()


def test_marks_are_viewer_only_and_not_added_to_browser(qapp):
    from app.gui.browser_window import BrowserWindow

    window = BrowserWindow()
    assert not hasattr(window, "add_mark_button")
    assert not hasattr(window, "marks_table")
    window.close()
