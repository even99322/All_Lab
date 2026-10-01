"""v0.12G independent, cache-only X/Y Cut-window regression coverage."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.data_model import Grid2DData, extract_line_cut
from app.gui.multi_pane import TWO_SIDE
from tests.real_data import BIG_FILE, FLUX_FILE

try:
    from PySide6.QtWidgets import QApplication
    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False


pytestmark = pytest.mark.skipif(not PYSIDE_AVAILABLE, reason="PySide6 unavailable")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _grid() -> Grid2DData:
    return Grid2DData(
        x_values=np.array([1.0, 2.0, 4.0]),
        y_values=np.array([10.0, 30.0]),
        z_values=np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]),
        x_name="Frequency", x_unit="GHz", y_name="Field", y_unit="mT",
        z_name="S21", z_unit="dB", transform="magnitude_db",
    )


def test_cut_window_uses_shared_extract_line_cut_and_toggle_is_non_destructive(qapp):
    from app.gui.linecut_widget import CutWindow

    window = CutWindow("x")
    window.set_grid(_grid())
    window.set_position(2.0, 29.0)
    qapp.processEvents()
    x_data, y_data = window.panel.plot_widget._curve.getData()
    expected = extract_line_cut(_grid(), "x", 29.0)
    assert np.array_equal(x_data, expected.x_values)
    assert np.array_equal(y_data, expected.y_values)
    assert "Field = 30" in window.position_label.text()

    window.show()
    window.always_on_top_checkbox.setChecked(True)
    assert window.always_on_top_checkbox.isChecked()
    window.always_on_top_checkbox.setChecked(False)
    assert window.panel.plot_widget._curve is not None
    window.close()


def test_cut_window_follow_toggle_holds_position_without_recreating_curve(qapp):
    from app.gui.linecut_widget import CutWindow

    window = CutWindow("x")
    grid = _grid()
    window.update_source(grid, 1.0, 10.0, force_position=True)
    curve = window.panel.plot_widget._curve
    window.follow_main_plot_checkbox.setChecked(False)
    window.update_source(grid, 4.0, 30.0)
    assert window._position == pytest.approx((1.0, 10.0))
    assert window.panel.plot_widget._curve is curve
    window.follow_main_plot_checkbox.setChecked(True)
    window.update_source(grid, 4.0, 30.0)
    assert window._position == pytest.approx((4.0, 30.0))
    assert window.panel.plot_widget._curve is curve
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real 2D fixture unavailable")
def test_independent_windows_follow_cached_crosshair_without_cache_access(qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.open_file(str(BIG_FILE))
    window.mode_combo.setCurrentIndex(1)
    window._show_cut_window("x")
    window._show_cut_window("y")
    qapp.processEvents()
    x_window, y_window = window._cut_windows["x"], window._cut_windows["y"]
    assert x_window.isVisible() and y_window.isVisible()
    assert x_window.grid is window.plot_2d_widget._grid
    assert y_window.grid is window.plot_2d_widget._grid

    before = dict(window.cached.cache.stats())
    grid = window.plot_2d_widget._grid
    window.plot_2d_widget.set_crosshair_position(grid.x_values[100], grid.y_values[200])
    after = window.cached.cache.stats()
    assert after == before
    assert x_window._position == pytest.approx((grid.x_values[100], grid.y_values[200]))
    assert y_window._position == pytest.approx((grid.x_values[100], grid.y_values[200]))

    x_window.close()
    assert y_window.isVisible()
    original_panel = x_window.panel
    window._show_cut_window("x")
    assert x_window.panel is original_panel
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real 2D fixture unavailable")
def test_cut_windows_follow_current_transform_and_active_pane(qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.open_file(str(BIG_FILE))
    window.mode_combo.setCurrentIndex(1)
    phase_index = window.transform_combo_2d.findData("phase_deg")
    window.transform_combo_2d.setCurrentIndex(phase_index)
    window._show_cut_window("x")
    assert window._cut_windows["x"].grid.transform == "phase_deg"

    window.pane_layout_combo.setCurrentText(TWO_SIDE)
    qapp.processEvents()
    window._activate_pane(1)
    assert window._cut_source() is window._pane_frames[1].plot_2d
    window._pane_states[2].plot_mode = 0
    window._activate_pane(2)
    assert window._cut_source() is None
    assert "unavailable" in window._cut_windows["x"].position_label.text()
    window._activate_pane(1)
    assert window._cut_windows["x"].grid is window._pane_frames[1].plot_2d._grid
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real 2D fixture unavailable")
def test_mark_crosshair_and_mode_switch_keep_cut_window_state_correct(qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.open_file(str(BIG_FILE))
    window.mode_combo.setCurrentIndex(1)
    window._show_cut_window("x")
    grid = window.plot_2d_widget._grid
    manager = window._mark_managers[1]
    crosshair = manager.add_crosshair(grid.x_values[5], grid.y_values[6])
    manager.select_object(crosshair.object_id)
    window.numeric_x_edit.setText(str(grid.x_values[40]))
    window.numeric_y_edit.setText(str(grid.y_values[50]))
    window._apply_numeric_position()
    assert window.plot_2d_widget.last_hover[:2] == pytest.approx((grid.x_values[40], grid.y_values[50]))
    assert window._cut_windows["x"]._position == pytest.approx((grid.x_values[40], grid.y_values[50]))

    window.mode_combo.setCurrentIndex(0)
    assert "unavailable" in window._cut_windows["x"].position_label.text()
    window.close()


@pytest.mark.skipif(not (BIG_FILE.exists() and FLUX_FILE.exists()), reason="Real 2D fixtures unavailable")
def test_cut_windows_are_viewer_local_and_flux_axes_remain_untransposed(qapp):
    from app.gui.main_window import MainWindow

    left, right = MainWindow(), MainWindow()
    left.open_file(str(BIG_FILE))
    right.open_file(str(FLUX_FILE))
    left.mode_combo.setCurrentIndex(1)
    right.mode_combo.setCurrentIndex(1)
    left._show_cut_window("x")
    right._show_cut_window("x")
    left_grid, right_grid = left.plot_2d_widget._grid, right.plot_2d_widget._grid
    left.plot_2d_widget.set_crosshair_position(left_grid.x_values[20], left_grid.y_values[30])
    assert left._cut_windows["x"]._position == pytest.approx((left_grid.x_values[20], left_grid.y_values[30]))
    assert right._cut_windows["x"]._position != left._cut_windows["x"]._position

    right.plot_2d_widget.set_crosshair_position(right_grid.x_values[10], right_grid.y_values[15])
    x_data, y_data = right._cut_windows["x"].panel.plot_widget._curve.getData()
    expected = extract_line_cut(right_grid, "x", right_grid.y_values[15])
    assert np.array_equal(x_data, expected.x_values)
    assert np.allclose(y_data, expected.y_values, equal_nan=True)
    left.close()
    right.close()
