"""v0.11D live Mark readout and local-analysis acceptance tests."""

from __future__ import annotations

import os
from time import perf_counter

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.axis_preset_store import AxisPresetStore
from app.core.local_analysis import (
    AROUND_MARK, SELECTED_RANGE, AnalysisError, HALF_PEAK, LOCAL_MAXIMUM,
    LOCAL_MINIMUM, PEAK, TROUGH, LocalAnalyzer,
)
from app.core.mark_model import CROSSHAIR, HORIZONTAL_LINE, RANGE, VERTICAL_LINE, MarkManager
from tests.real_data import BIG_FILE, FLUX_FILE, S31_FILE, SMALL_FILE


pytestmark = pytest.mark.skipif(
    not all(path.exists() for path in (BIG_FILE, FLUX_FILE, S31_FILE, SMALL_FILE)),
    reason="Required workspace real-data fixtures are unavailable.",
)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_range_subset_uses_actual_coordinates_descending_nonuniform_duplicate_x():
    x = [5, 3, 3, 1.5, 0]
    analyzer = LocalAnalyzer(x, [9, 4, 8, 2, 7], [10, 11, 12, 13, 14])
    positions = analyzer.range_positions(1, 3)
    assert positions.tolist() == [1, 2, 3]
    maximum = analyzer.analyze(LOCAL_MAXIMUM, positions)
    minimum = analyzer.analyze(LOCAL_MINIMUM, positions)
    assert (maximum.position, maximum.sample_index, maximum.x, maximum.y) == (2, 12, 3, 8)
    assert (minimum.position, minimum.sample_index, minimum.x, minimum.y) == (3, 13, 1.5, 2)


def test_point_window_is_local_and_moves_with_center():
    analyzer = LocalAnalyzer(np.arange(12), [9, 8, 7, 6, 5, 4, 3, 4, 5, 0, 8, 9])
    first = analyzer.point_window_positions(3, 2)
    second = analyzer.point_window_positions(9, 1)
    assert first.tolist() == [1, 2, 3, 4, 5]
    assert analyzer.analyze(LOCAL_MINIMUM, first).position == 5
    assert second.tolist() == [8, 9, 10]
    assert analyzer.analyze(LOCAL_MINIMUM, second).position == 9


def test_local_maximum_and_peak_are_distinct():
    analyzer = LocalAnalyzer(np.arange(7), [10, 2, 5, 1, 4, 2, 11])
    positions = np.arange(7)
    assert analyzer.analyze(LOCAL_MAXIMUM, positions).position == 6
    peak = analyzer.analyze(PEAK, positions)
    assert peak.position == 2 and peak.prominence > 0


def test_local_minimum_and_trough_are_distinct():
    analyzer = LocalAnalyzer(np.arange(7), [-10, 2, -5, 1, -4, 2, -11])
    positions = np.arange(7)
    assert analyzer.analyze(LOCAL_MINIMUM, positions).position == 6
    trough = analyzer.analyze(TROUGH, positions)
    assert trough.position == 2 and trough.prominence > 0


def test_multiple_peaks_and_troughs_choose_most_prominent_deterministically():
    x = np.arange(9)
    peaks = LocalAnalyzer(x, [0, 3, 0, 1, 0, 7, 0, 2, 0])
    troughs = LocalAnalyzer(x, [0, -3, 0, -1, 0, -7, 0, -2, 0])
    assert peaks.analyze(PEAK, x).position == 5
    assert peaks.analyze(PEAK, x).position == 5
    assert troughs.analyze(TROUGH, x).position == 5


def test_half_peak_baseline_level_two_crossings_and_width():
    analyzer = LocalAnalyzer([-3, -2, -1, 0, 1, 2, 3], [0, 1, 5, 10, 5, 1, 0])
    result = analyzer.analyze(HALF_PEAK, np.arange(7))
    assert result.position == 3
    assert result.baseline == pytest.approx(0)
    assert result.half_level == pytest.approx(5)
    assert (result.left_half_x, result.right_half_x) == pytest.approx((-1, 1))
    assert result.half_level_width == pytest.approx(2)
    dip = LocalAnalyzer([-3, -2, -1, 0, 1, 2, 3], [0, -1, -5, -10, -5, -1, 0])
    dip_result = dip.analyze(HALF_PEAK, np.arange(7))
    assert dip_result.position == 3
    assert (dip_result.left_half_x, dip_result.right_half_x) == pytest.approx((-1, 1))


@pytest.mark.parametrize(
    "values, message",
    [
        ([0, 5, 10, 8, 7], "No valid right"),
        ([0, 1, 2, 3, 4], "No valid half-level feature"),
        ([0, 5, 0, 5, 0], "Ambiguous"),
    ],
)
def test_half_peak_failure_modes(values, message):
    with pytest.raises(AnalysisError, match=message):
        LocalAnalyzer(np.arange(len(values)), values).analyze(HALF_PEAK, np.arange(len(values)))


def test_nan_inf_empty_and_invalid_region_handling():
    analyzer = LocalAnalyzer([0, 1, 2, 3, 4], [np.nan, 4, np.inf, -2, -np.inf])
    positions = analyzer.range_positions(0, 4)
    assert positions.tolist() == [1, 3]
    assert analyzer.analyze(LOCAL_MAXIMUM, positions).position == 1
    assert analyzer.analyze(LOCAL_MINIMUM, positions).position == 3
    with pytest.raises(AnalysisError, match="No local peak"):
        analyzer.analyze(PEAK, positions)
    with pytest.raises(AnalysisError, match="No local trough"):
        analyzer.analyze(TROUGH, positions)
    with pytest.raises(AnalysisError, match="No valid data"):
        analyzer.analyze(LOCAL_MAXIMUM, [])


def _window(path, tmp_path):
    from app.gui.main_window import MainWindow
    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / f"{path.name}.json"))
    window.open_file(str(path))
    return window


def test_live_point_updates_cached_state_label_without_recreating_items(qapp, tmp_path):
    window = _window(SMALL_FILE, tmp_path)
    x, y = window.plot_widget._curve.getData()
    manager = window._mark_managers[0]
    mark = manager.add_nearest(x[10], y[10])
    window._refresh_mark_ui()
    target = window._mark_overlays[0]._targets[mark.number]
    items = tuple(window._mark_overlays[0]._items)
    target.setPos(float(x[200]), float(y[200]))
    qapp.processEvents()
    live = manager.object(mark.object_id)
    assert live.sample_index == 200
    assert str(f"{live.x:.8g}") in window.selected_mark_details.text()
    assert str(f"{live.x:.5g}") in target.label().toPlainText()
    assert tuple(window._mark_overlays[0]._items) == items
    target.sigPositionChangeFinished.emit(target)
    assert manager.object(mark.object_id).sample_index == 200
    window.close()


def test_live_2d_point_and_crosshair_resolve_real_xyz(qapp, tmp_path):
    window = _window(BIG_FILE, tmp_path)
    window.mode_combo.setCurrentIndex(1)
    grid = window.plot_2d_widget._grid
    manager = window._mark_managers[1]
    mark = manager.add_nearest(grid.x_values[2], grid.y_values[3])
    cross = manager.add_crosshair(grid.x_values[4], grid.y_values[5])
    window._refresh_mark_ui()
    target = window._mark_overlays[1]._targets[mark.number]
    target.setPos(float(grid.x_values[20]), float(grid.y_values[30]))
    qapp.processEvents()
    moved = manager.object(mark.object_id)
    assert (moved.x, moved.y, moved.value) == pytest.approx(
        (grid.x_values[20], grid.y_values[30], grid.z_values[30, 20])
    )
    _, _, handle = window._mark_overlays[1]._annotation_items[cross.object_id]
    handle.setPos(float(grid.x_values[40]), float(grid.y_values[50]))
    qapp.processEvents()
    moved_cross = manager.object(cross.object_id)
    assert (moved_cross.x, moved_cross.y, moved_cross.value) == pytest.approx(
        (grid.x_values[40], grid.y_values[50], grid.z_values[50, 40])
    )
    window.close()


def test_live_range_lines_crosshair_and_show_values_off(qapp, tmp_path):
    window = _window(SMALL_FILE, tmp_path)
    x, y = window.plot_widget._curve.getData()
    manager = window._mark_managers[0]
    region = manager.add_range(x[50], x[150])
    hline = manager.add_horizontal_line(y[100])
    vline = manager.add_vertical_line(x[100])
    cross = manager.add_crosshair(x[100], y[100])
    window._refresh_mark_ui()
    overlay = window._mark_overlays[0]

    region_item = overlay._annotation_items[region.object_id]
    width = region.width
    region_item.setRegion((x[80], x[180]))
    qapp.processEvents()
    assert manager.object(region.object_id).width == pytest.approx(x[180] - x[80])
    region_item.setRegion((x[100], x[200]))
    qapp.processEvents()
    assert manager.object(region.object_id).width == pytest.approx(width)

    h_item = overlay._annotation_items[hline.object_id]
    h_item.setValue(float(y[180])); qapp.processEvents()
    assert manager.object(hline.object_id).y == pytest.approx(y[180])
    v_item = overlay._annotation_items[vline.object_id]
    v_item.setValue(float(x[180])); qapp.processEvents()
    assert manager.object(vline.object_id).x == pytest.approx(x[180])

    window.show_mark_values_checkbox.setChecked(False)
    overlay = window._mark_overlays[0]
    _, _, handle = overlay._annotation_items[cross.object_id]
    handle.setPos(float(x[220]), float(y[220])); qapp.processEvents()
    assert manager.object(cross.object_id).x == pytest.approx(x[220])
    assert "X / Start" in window.selected_mark_details.text()
    assert handle.label().toPlainText() == cross.display_name
    window.close()


def test_ui_range_analysis_creates_exact_lowest_free_mark(qapp, tmp_path):
    window = _window(SMALL_FILE, tmp_path)
    x, y = window.plot_widget._curve.getData()
    manager = window._mark_managers[0]
    manager.add_nearest(x[5], y[5])
    region = manager.add_range(x[100], x[300])
    manager.select_object(region.object_id)
    window._analysis_target_id = region.object_id
    window.analysis_region_combo.setCurrentText(SELECTED_RANGE)
    window._refresh_mark_ui()
    window.analysis_operation_combo.setCurrentText(LOCAL_MINIMUM)
    expected_positions = LocalAnalyzer(*manager.analysis_trace()).range_positions(region.x, region.x2)
    expected = LocalAnalyzer(*manager.analysis_trace()).analyze(LOCAL_MINIMUM, expected_positions)
    window._run_local_analysis()
    result_mark = manager.object("point:2")
    assert result_mark is not None
    assert result_mark.sample_index == expected.sample_index
    assert manager.selected_id == result_mark.object_id
    assert "Local Minimum" in window.analysis_result_label.text()
    window.close()


def test_ui_point_window_moves_same_mark_and_all_marks_full_is_safe(qapp, tmp_path):
    window = _window(SMALL_FILE, tmp_path)
    x, y = window.plot_widget._curve.getData()
    manager = window._mark_managers[0]
    mark = manager.add_nearest(x[200], y[200])
    manager.select_object(mark.object_id)
    window._analysis_target_id = mark.object_id
    window.analysis_region_combo.setCurrentText(AROUND_MARK)
    window.analysis_window_spin.setValue(5)
    window.analysis_operation_combo.setCurrentText(LOCAL_MAXIMUM)
    window._run_local_analysis()
    assert len(manager.marks()) == 1
    assert manager.selected_id == mark.object_id

    manager.clear()
    for index in range(10):
        manager.add_nearest(x[index], y[index])
    region = manager.add_range(x[100], x[200])
    window._analysis_target_id = region.object_id
    window.analysis_region_combo.setCurrentText(SELECTED_RANGE)
    window.analysis_operation_combo.setCurrentText(LOCAL_MINIMUM)
    window._run_local_analysis()
    assert len(manager.marks()) == 10
    assert "No free Point Mark" in window.analysis_result_label.text()
    window.close()


def test_unsupported_2d_and_iq_analysis_are_clear(qapp, tmp_path):
    window = _window(SMALL_FILE, tmp_path)
    iq = window.axis_preset_combo.findText("IQ Transform")
    window.axis_preset_combo.setCurrentIndex(iq)
    x, y = window.plot_widget._curve.getData()
    mark = window._mark_managers[0].add_nearest(x[10], y[10])
    window._analysis_target_id = mark.object_id
    window._run_local_analysis()
    assert "unavailable for IQ" in window.analysis_result_label.text()
    window.mode_combo.setCurrentIndex(1)
    window._run_local_analysis()
    assert "Select a 1D" in window.analysis_result_label.text()
    window.close()


def test_real_10001_live_path_is_cached_responsive_and_preserves_hidden(qapp, tmp_path):
    window = _window(FLUX_FILE, tmp_path)
    x, y = window.plot_widget._curve.getData()
    assert len(x) == 10001
    manager = window._mark_managers[0]
    mark = manager.add_nearest(x[10], y[10])
    hidden = manager.add_nearest(x[20], y[20])
    manager.set_visible(hidden.object_id, False)
    window._refresh_mark_ui()
    started = perf_counter()
    for index in range(0, 10001, 100):
        window._live_move_mark(0, mark.number, float(x[index]), float(y[index]))
    elapsed = perf_counter() - started
    assert elapsed < 1.0
    assert manager.object(mark.object_id).sample_index == 10000
    assert not manager.object(hidden.object_id).visible

    window.mode_combo.setCurrentIndex(1)
    grid = window.plot_2d_widget._grid
    manager_2d = window._mark_managers[1]
    point_2d = manager_2d.add_nearest(grid.x_values[10], grid.y_values[10])
    cross_2d = manager_2d.add_crosshair(grid.x_values[20], grid.y_values[20])
    window._refresh_mark_ui()
    window._live_move_mark(
        1, point_2d.number, float(grid.x_values[100]), float(grid.y_values[30])
    )
    window._live_move_annotation(
        1, cross_2d.object_id, float(grid.x_values[200]), float(grid.y_values[40]), np.nan
    )
    assert manager_2d.object(point_2d.object_id).value == pytest.approx(grid.z_values[30, 100])
    assert manager_2d.object(cross_2d.object_id).value == pytest.approx(grid.z_values[40, 200])
    window.close()


@pytest.mark.parametrize("path", [SMALL_FILE, BIG_FILE, FLUX_FILE, S31_FILE])
def test_real_data_viewer_regression(qapp, tmp_path, path):
    window = _window(path, tmp_path)
    assert window.experiment is not None
    assert window.plot_widget._curve is not None
    assert window.metadata_dialog is not None and window.data_table_dialog is not None
    window.close()
