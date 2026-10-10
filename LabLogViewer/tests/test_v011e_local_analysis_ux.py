"""v0.11E automatic local regions and Local Analysis UX tests."""

from __future__ import annotations

import os
from time import perf_counter

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.axis_preset_store import AxisPresetStore
from app.core.local_analysis import (
    AROUND_MARK, AUTO_NEARBY, BETWEEN_MARKS, HALF_PEAK, LOCAL_MAXIMUM,
    LOCAL_MINIMUM, PEAK, SELECTED_RANGE, TROUGH, VISIBLE_RANGE, AnalysisError,
    LocalAnalyzer,
)
from app.core.mark_model import MarkManager
from tests.real_data import BIG_FILE, FLUX_FILE, SMALL_FILE


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _feature_trace(size=1001):
    x = np.linspace(0, 100, size)
    y = 0.02 * x
    y += 2.0 * np.exp(-((x - 20) / 1.4) ** 2)
    y -= 1.5 * np.exp(-((x - 48) / 1.8) ** 2)
    y -= 8.0 * np.exp(-((x - 82) / 1.5) ** 2)
    return x, y


def test_auto_region_nearby_minimum_and_trough_choose_local_actual_sample():
    x, y = _feature_trace()
    analyzer = LocalAnalyzer(x, y, np.arange(x.size) + 100)
    center = int(np.argmin(np.abs(x - 45)))
    for operation in (LOCAL_MINIMUM, TROUGH):
        region = analyzer.auto_region(center, operation)
        result = analyzer.analyze(operation, region.positions)
        expected = int(np.argmin(np.abs(x - 48)))
        assert abs(result.position - expected) <= 2
        assert result.sample_index == result.position + 100
        assert region.start_position <= result.position <= region.end_position


def test_auto_region_nearby_maximum_and_peak_are_bounded():
    x, y = _feature_trace()
    analyzer = LocalAnalyzer(x, y)
    center = int(np.argmin(np.abs(x - 22)))
    for operation in (LOCAL_MAXIMUM, PEAK):
        region = analyzer.auto_region(center, operation)
        result = analyzer.analyze(operation, region.positions)
        assert abs(x[result.position] - 20) < 0.3
        assert max(abs(region.start_position - center), abs(region.end_position - center)) <= 50


def test_nearby_weak_feature_beats_distant_prominent_feature():
    x, y = _feature_trace()
    analyzer = LocalAnalyzer(x, y)
    center = int(np.argmin(np.abs(x - 46)))
    region = analyzer.auto_region(center, TROUGH, max_radius=450)
    result = analyzer.analyze(TROUGH, region.positions)
    assert abs(x[result.position] - 48) < 0.3
    assert abs(x[result.position] - 82) > 30


def test_auto_region_rejects_flat_data_and_features_outside_hard_limit():
    flat = LocalAnalyzer(np.arange(201), np.ones(201))
    with pytest.raises(AnalysisError, match="No nearby feature"):
        flat.auto_region(100, PEAK)
    y = np.zeros(1001)
    y[900] = -10
    analyzer = LocalAnalyzer(np.arange(1001), y)
    with pytest.raises(AnalysisError, match="No nearby feature"):
        analyzer.auto_region(100, TROUGH)


def test_auto_region_handles_boundaries_nan_descending_and_nonuniform_x():
    x = np.array([30, 28, 25, 21, 20, 19.5, 10, 3, 0], dtype=float)
    y = np.array([np.nan, -1, -4, -6, -8, -3, 0, np.inf, 1], dtype=float)
    analyzer = LocalAnalyzer(x, y)
    region = analyzer.auto_region(2, TROUGH, max_radius=5)
    result = analyzer.analyze(TROUGH, region.positions)
    assert result.position == 4
    assert result.x == 20


def test_auto_region_half_peak_retains_existing_crossing_semantics():
    x = np.linspace(-5, 5, 501)
    y = -8 * np.exp(-(x / 0.8) ** 2)
    analyzer = LocalAnalyzer(x, y)
    region = analyzer.auto_region(240, HALF_PEAK)
    result = analyzer.analyze(HALF_PEAK, region.positions)
    assert result.position == 250
    assert result.left_half_x < 0 < result.right_half_x
    assert result.half_level_width > 0


def _synthetic_window(tmp_path, qapp):
    from app.gui.main_window import MainWindow
    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / "presets.json"))
    x, y = _feature_trace(501)
    window.plot_widget.plot(x, y, x_label="Frequency", y_label="Magnitude", title="Synthetic")
    manager = window._mark_managers[0]
    manager.set_1d_context(
        "v011e-test", x, y, x_name="Frequency", y_name="Magnitude",
        x_unit="GHz", y_unit="a.u.", x_semantic="frequency", y_semantic="magnitude",
    )
    window._refresh_mark_ui()
    qapp.processEvents()
    return window, manager, x, y


def test_auto_preview_is_temporary_and_find_moves_selected_mark(qapp, tmp_path):
    window, manager, x, _y = _synthetic_window(tmp_path, qapp)
    mark = manager.add_nearest(x[220], 0)
    manager.select_object(mark.object_id)
    window._analysis_target_id = mark.object_id
    window.analysis_region_combo.setCurrentText(AUTO_NEARBY)
    window.analysis_operation_combo.setCurrentText(LOCAL_MINIMUM)
    window._refresh_mark_ui()
    original_index = manager.object(mark.object_id).sample_index
    window._preview_analysis_region()
    assert len(window._mark_overlays[0]._analysis_preview_items) == 2
    assert manager.object(mark.object_id).sample_index == original_index
    assert len(manager.annotations()) == 0
    window._run_local_analysis()
    moved = manager.object(mark.object_id)
    assert abs(moved.x - 48) < 0.4
    assert len(window._mark_overlays[0]._analysis_preview_items) == 0
    window.close()


def test_around_mark_and_selected_range_preserve_existing_semantics(qapp, tmp_path):
    window, manager, x, y = _synthetic_window(tmp_path, qapp)
    mark = manager.add_nearest(x[240], y[240])
    window._analysis_target_id = mark.object_id
    window.analysis_region_combo.setCurrentText(AROUND_MARK)
    window.analysis_window_spin.setValue(10)
    analyzer, positions, destination, _bounds, _signature = window._resolve_analysis_region()
    assert positions.tolist() == list(range(230, 251))
    assert destination.object_id == mark.object_id

    region = manager.add_range(x[220], x[270])
    window._analysis_target_id = region.object_id
    window.analysis_region_combo.setCurrentText(SELECTED_RANGE)
    analyzer, positions, destination, bounds, _signature = window._resolve_analysis_region()
    assert destination is None
    assert bounds == pytest.approx(tuple(sorted((x[220], x[270]))))
    assert analyzer.analyze(LOCAL_MINIMUM, positions).position == int(positions[np.argmin(y[positions])])
    window.close()


def test_between_marks_normalizes_order_and_creates_new_result_mark(qapp, tmp_path):
    window, manager, x, y = _synthetic_window(tmp_path, qapp)
    first = manager.add_nearest(x[270], y[270])
    second = manager.add_nearest(x[210], y[210])
    window._refresh_mark_ui()
    window.analysis_region_combo.setCurrentText(BETWEEN_MARKS)
    window.analysis_start_mark_combo.setCurrentIndex(
        window.analysis_start_mark_combo.findData(first.object_id)
    )
    window.analysis_end_mark_combo.setCurrentIndex(
        window.analysis_end_mark_combo.findData(second.object_id)
    )
    _analyzer, positions, destination, bounds, _signature = window._resolve_analysis_region()
    assert destination is None
    assert bounds == pytest.approx((x[210], x[270]))
    window.analysis_operation_combo.setCurrentText(LOCAL_MINIMUM)
    window._run_local_analysis()
    assert len(manager.marks()) == 3
    assert manager.selected_id == "point:3"
    assert manager.object("point:3").sample_index in positions
    window.close()


def test_current_visible_range_uses_live_data_coordinates(qapp, tmp_path):
    window, manager, x, _y = _synthetic_window(tmp_path, qapp)
    window.plot_widget.plot_widget.getPlotItem().getViewBox().setXRange(40, 55, padding=0)
    qapp.processEvents()
    window.analysis_region_combo.setCurrentText(VISIBLE_RANGE)
    _analyzer, positions, _destination, bounds, _signature = window._resolve_analysis_region()
    assert bounds == pytest.approx((40, 55), abs=0.05)
    assert np.all((x[positions] >= bounds[0]) & (x[positions] <= bounds[1]))
    window.close()


def test_target_selection_enable_states_hidden_handling_and_preview_clear(qapp, tmp_path):
    window, manager, x, y = _synthetic_window(tmp_path, qapp)
    mark = manager.add_nearest(x[230], y[230])
    region = manager.add_range(x[210], x[270])
    window._refresh_mark_ui()
    mark_row = next(row for row in range(window.marks_table.rowCount())
                    if window.marks_table.item(row, 0).data(256) == mark.object_id)
    window.marks_table.selectRow(mark_row)
    qapp.processEvents()
    assert window._analysis_target_id == mark.object_id
    assert "M1" in window.analysis_target_label.text()
    assert window.analysis_find_button.isEnabled()
    window._preview_analysis_region()
    manager.set_visible(mark.object_id, False)
    window._refresh_mark_ui()
    assert not window.analysis_find_button.isEnabled()
    window._clear_analysis_preview()
    assert not window._mark_overlays[0]._analysis_preview_items

    range_row = next(row for row in range(window.marks_table.rowCount())
                     if window.marks_table.item(row, 0).data(256) == region.object_id)
    window.marks_table.selectRow(range_row)
    qapp.processEvents()
    assert window.analysis_region_combo.currentText() == SELECTED_RANGE
    assert "Range" in window.analysis_target_label.text()
    window.close()


@pytest.mark.skipif(not all(path.exists() for path in (SMALL_FILE, BIG_FILE)),
                    reason="Real trace-change fixtures unavailable")
def test_trace_change_clears_temporary_preview(qapp, tmp_path):
    from app.gui.main_window import MainWindow
    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / "trace-presets.json"))
    window.open_file(str(SMALL_FILE))
    window.analysis_region_combo.setCurrentText(VISIBLE_RANGE)
    window._preview_analysis_region()
    assert window._mark_overlays[0]._analysis_preview_items
    window.open_file(str(BIG_FILE))
    assert not window._mark_overlays[0]._analysis_preview_items
    assert window._analysis_preview_signature is None
    window.close()


@pytest.mark.skipif(not FLUX_FILE.exists(), reason="Real 10,001-point fixture unavailable")
def test_real_10001_auto_region_is_bounded_and_fast(qapp, tmp_path):
    from app.gui.main_window import MainWindow
    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / "real-presets.json"))
    window.open_file(str(FLUX_FILE))
    x, values = window.plot_widget._curve.getData()
    assert len(x) == 10001
    analyzer = LocalAnalyzer(x, values)
    center = len(x) // 2
    started = perf_counter()
    try:
        region = analyzer.auto_region(center, LOCAL_MINIMUM)
        assert region.positions.size <= 1001
    except AnalysisError:
        pass
    assert perf_counter() - started < 0.25
    window.close()
