"""v0.12D Mark selector and Half-Peak regression acceptance tests."""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.axis_preset_store import AxisPresetStore
from app.core.local_analysis import (
    AROUND_MARK, AUTO_NEARBY, BETWEEN_MARKS, HALF_PEAK, LOCAL_MAXIMUM,
    LOCAL_MINIMUM, PEAK, SELECTED_RANGE, TROUGH, AnalysisError, LocalAnalyzer,
)
from app.core.mark_model import (
    CROSSHAIR, HORIZONTAL_LINE, MARK_TOOLS, POINT_MARK, RANGE, VERTICAL_LINE,
)
from app.gui.mark_overlay import MarkOverlay
from tests.real_data import BIG_FILE


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _synthetic_window(tmp_path, qapp):
    from app.gui.main_window import MainWindow
    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / "axis.json"))
    x = np.linspace(-5, 5, 501)
    y = 0.2 * x + 4 * np.exp(-((x + 2) / 0.45) ** 2) \
        - 7 * np.exp(-((x - 1.5) / 0.6) ** 2)
    window.plot_widget.plot(x, y, x_label="X", y_label="Magnitude", title="Synthetic")
    manager = window._mark_managers[0]
    manager.set_1d_context(
        "v012d", x, y, x_name="X", y_name="Magnitude",
        x_unit="GHz", y_unit="a.u.", x_semantic="x", y_semantic="magnitude",
    )
    window._refresh_mark_ui()
    qapp.processEvents()
    return window, manager, x, y


def test_selector_starts_neutral_and_each_tool_returns_after_completion(qapp, tmp_path):
    window, manager, x, y = _synthetic_window(tmp_path, qapp)
    assert window.mark_tool_combo.currentText() == "Choose Mark"
    assert window._current_mark_tool() is None
    for tool in (POINT_MARK, HORIZONTAL_LINE, VERTICAL_LINE, CROSSHAIR):
        window.mark_tool_combo.setCurrentIndex(window.mark_tool_combo.findData(tool))
        assert window._current_mark_tool() == tool
        assert window.add_mark_button.isChecked()
        window._place_mark(0, float(x[100]), float(y[100]))
        assert window.mark_tool_combo.currentText() == "Choose Mark"
        assert window._current_mark_tool() is None
    window.mark_tool_combo.setCurrentIndex(window.mark_tool_combo.findData(RANGE))
    window._place_mark(0, float(x[150]), float(y[150]))
    assert window._current_mark_tool() == RANGE
    window._place_mark(0, float(x[250]), float(y[250]))
    assert window.mark_tool_combo.currentText() == "Choose Mark"
    assert len(manager.annotations()) == 4
    window.close()


def test_single_point_dependencies_and_between_two_marks_guard(qapp, tmp_path):
    window, manager, x, y = _synthetic_window(tmp_path, qapp)
    mark = manager.add_nearest(float(x[200]), float(y[200]))
    window._analysis_target_id = None
    for region in (AUTO_NEARBY, AROUND_MARK):
        window.analysis_region_combo.setCurrentText(region)
        window.analysis_operation_combo.setCurrentText(LOCAL_MINIMUM)
        window._refresh_mark_ui()
        assert window.analysis_find_button.isEnabled()
        assert window._analysis_target_id == mark.object_id
    window.analysis_region_combo.setCurrentText(BETWEEN_MARKS)
    window._refresh_mark_ui()
    assert not window.analysis_find_button.isEnabled()
    window.close()


@pytest.mark.parametrize("operation", [LOCAL_MAXIMUM, LOCAL_MINIMUM, PEAK, TROUGH])
def test_only_one_range_enables_and_runs_range_analysis(qapp, tmp_path, operation):
    window, manager, x, _y = _synthetic_window(tmp_path, qapp)
    region = manager.add_range(float(x[40]), float(x[-40]))
    manager.select_object(region.object_id)
    window._analysis_target_id = None
    window.analysis_region_combo.setCurrentText(SELECTED_RANGE)
    window.analysis_operation_combo.setCurrentText(operation)
    window._refresh_mark_ui()
    assert window._analysis_target_id == region.object_id
    assert window.analysis_find_button.isEnabled()
    window._run_local_analysis()
    assert operation in window.analysis_result_label.text()
    assert len(manager.marks()) == 1
    window.close()


def test_half_peak_clean_peak_and_dip_choose_nearest_enclosing_crossings():
    x = np.arange(-3, 4, dtype=float)
    for y in ([0, 1, 5, 10, 5, 1, 0], [0, -1, -5, -10, -5, -1, 0]):
        result = LocalAnalyzer(x, y).analyze(HALF_PEAK, np.arange(x.size))
        assert result.position == 3
        assert (result.left_half_x, result.right_half_x) == pytest.approx((-1, 1))
        assert result.half_level_width == pytest.approx(
            result.right_half_x - result.left_half_x
        )


def test_remote_extra_crossings_do_not_force_ambiguity():
    x = np.arange(-10, 11, dtype=float)
    y = np.zeros(x.size)
    y[[3, 4, 5]] = [-6, -2, 0]
    y[[9, 10, 11]] = [-5, -10, -5]
    y[[15, 16, 17]] = [0, -2, -6]
    result = LocalAnalyzer(x, y).analyze(HALF_PEAK, np.arange(x.size))
    assert result.x == 0
    assert (result.left_half_x, result.right_half_x) == pytest.approx((-1, 1))


def test_half_peak_reports_directional_missing_crossings_and_invalid_data():
    with pytest.raises(AnalysisError, match="right"):
        LocalAnalyzer(np.arange(5), [0, 5, 10, 8, 7]).analyze(HALF_PEAK, np.arange(5))
    with pytest.raises(AnalysisError, match="left"):
        LocalAnalyzer(np.arange(5), [7, 8, 10, 5, 0]).analyze(HALF_PEAK, np.arange(5))
    with pytest.raises(AnalysisError, match="too small"):
        LocalAnalyzer(np.arange(5), [np.nan, 1, np.inf, 0, -np.inf]).analyze(
            HALF_PEAK, np.arange(5)
        )


def test_half_peak_visualization_matches_result_coordinates(qapp):
    import pyqtgraph as pg
    widget = pg.PlotWidget()
    overlay = MarkOverlay(widget.getPlotItem(), widget)
    overlay.show_half_peak_result(-1.25, 2.5, -19.85)
    segment, crossings, _label = overlay._analysis_result_items
    sx, sy = segment.getData()
    cx, cy = crossings.getData()
    assert sx == pytest.approx([-1.25, 2.5])
    assert sy == pytest.approx([-19.85, -19.85])
    assert cx == pytest.approx([-1.25, 2.5])
    assert cy == pytest.approx([-19.85, -19.85])
    overlay.clear_analysis_result()
    assert not overlay._analysis_result_items
    widget.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real clean resonance fixture unavailable")
def test_real_clean_resonance_half_peak_magnitude_and_db(qapp, tmp_path):
    from app.gui.main_window import MainWindow
    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / "real-axis.json"))
    window.open_file(str(BIG_FILE))
    x, magnitude = window.plot_widget._curve.getData()
    positions = np.arange(219, 260)
    linear = LocalAnalyzer(x, magnitude).analyze(HALF_PEAK, positions)
    db_values = 20 * np.log10(np.maximum(magnitude, np.finfo(float).tiny))
    db = LocalAnalyzer(x, db_values).analyze(HALF_PEAK, positions)
    for result in (linear, db):
        assert result.position == 239
        assert result.left_half_x < result.x < result.right_half_x
        assert result.half_level_width > 0
    window.close()
