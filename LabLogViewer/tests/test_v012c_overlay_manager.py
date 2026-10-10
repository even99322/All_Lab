"""v0.12C Overlay Manager and interaction regression acceptance tests."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.core.axis_preset_store import AxisPresetStore
from app.core.local_analysis import AROUND_MARK, AUTO_NEARBY, BETWEEN_MARKS, VISIBLE_RANGE
from app.core.mark_model import (
    CROSSHAIR, HORIZONTAL_LINE, POINT_MARK, RANGE, VERTICAL_LINE,
)
from app.core.overlay_store import MAX_SAVED_OVERLAYS, OverlayStore, SavedOverlay
from app.core.trace_overlay import DISTINCT, SEQUENTIAL
from app.core.trace_selection import TraceSelectionState
from app.gui.main_window import MainWindow
from tests.real_data import BIG_FILE, FLUX_FILE


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(path, tmp_path):
    window = MainWindow(
        axis_preset_store=AxisPresetStore(tmp_path / "axis.json"),
        overlay_store=OverlayStore(tmp_path / "overlays.json"),
    )
    window.open_file(str(path))
    return window


def _select(window, traces):
    first, *rest = traces
    window.log_entries.select_row(first)
    for trace in rest:
        window.log_entries.select_row(trace, Qt.ControlModifier)


def _overlay(name, trace=0):
    return SavedOverlay(
        name=name, selected_traces=[trace], visible_traces=[trace], active_trace=trace,
    )


def test_selection_state_separates_active_visibility_and_reference():
    state = TraceSelectionState(range(250))
    state.restore([122, 126, 150, 199], active=126)
    assert state.set_active(150)
    assert state.ordered_selection() == (122, 126, 150, 199)
    assert state.set_visible(199, False)
    assert state.ordered_selection() == (122, 126, 150, 199)
    assert state.visible_selection() == (122, 126, 150)
    assert state.set_reference(122) and state.reference_trace == 122
    assert state.set_reference(199) and state.reference_trace == 199
    assert state.set_reference(None) and state.reference_trace is None
    assert not state.set_active(17)
    assert not state.set_reference(17)


def test_toggle_non_active_preserves_active_and_cleans_removed_state():
    state = TraceSelectionState(range(10))
    state.restore([1, 3, 5], active=5, visible=[1, 5], reference=3)
    state.toggle_select(3)
    assert state.ordered_selection() == (1, 5)
    assert state.active_trace == 5
    assert state.reference_trace is None
    state.toggle_select(5)
    assert state.ordered_selection() == (1,)
    assert state.active_trace == 1 and state.is_visible(1)


def test_overlay_store_round_trip_rename_delete_and_limit(tmp_path):
    store = OverlayStore(tmp_path / "overlays.json")
    key = "/data/file.hdf5"
    for index in range(MAX_SAVED_OVERLAYS):
        store.save(key, _overlay(f"Overlay {index}", index))
    with pytest.raises(ValueError, match="Maximum"):
        store.save(key, _overlay("Overflow"))
    replacement = _overlay("Overlay 0", 9)
    store.save(key, replacement)
    reloaded = OverlayStore(store.storage_path)
    assert reloaded.get(key, "Overlay 0").active_trace == 9
    assert reloaded.rename(key, "Overlay 0", "Renamed")
    assert reloaded.get(key, "Renamed") is not None
    assert reloaded.delete(key, "Renamed")
    assert len(reloaded.list_all(key)) == MAX_SAVED_OVERLAYS - 1


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real multi-trace fixture unavailable")
def test_panel_active_change_preserves_selection_visibility_reference_and_cache(qapp, tmp_path):
    window = _window(BIG_FILE, tmp_path)
    _select(window, (19, 29, 39, 49))
    window.trace_selection.set_visible(49, False)
    window.trace_selection.set_reference(19)
    window._refresh_multi_trace_panel()
    misses = window.cached.cache.stats()["misses"]
    window.log_entries.set_active(39)
    assert window.trace_selection.ordered_selection() == (19, 29, 39, 49)
    assert window.trace_selection.visible_selection() == (19, 29, 39)
    assert window.trace_selection.reference_trace == 19
    assert window.cached.cache.stats()["misses"] == misses
    assert len(window.plot_widget._curves) == 3
    assert window.multi_trace_panel.isVisible() == window.isVisible()
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real multi-trace fixture unavailable")
def test_panel_visibility_and_reference_controls_do_not_change_selection(qapp, tmp_path):
    window = _window(BIG_FILE, tmp_path)
    window.show()
    _select(window, (2, 5, 8))
    window._refresh_multi_trace_panel()
    item = window.multi_trace_tree.topLevelItem(1)
    window._on_multi_trace_item_clicked(item, 0)
    QApplication.processEvents()
    assert window.trace_selection.ordered_selection() == (2, 5, 8)
    assert window.trace_selection.visible_selection() == (2, 8)
    assert set(window.plot_widget._curves) == {2, 8}
    window._on_multi_trace_item_clicked(item, 2)
    assert window.trace_selection.reference_trace == 5
    window._on_multi_trace_item_clicked(window.multi_trace_tree.topLevelItem(2), 2)
    assert window.trace_selection.reference_trace == 8
    window._clear_reference_trace()
    assert window.trace_selection.reference_trace is None
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real multi-trace fixture unavailable")
def test_ctrl_command_and_shift_sweep_share_selection_model(qapp, tmp_path):
    window = _window(BIG_FILE, tmp_path)
    _select(window, (9, 19, 29))
    window._select_sweep(125, Qt.MetaModifier)
    window._select_sweep(199, Qt.ControlModifier)
    assert window.trace_selection.ordered_selection() == (9, 19, 29, 125, 199)
    assert window.trace_selection.active_trace == 199
    window._select_sweep(204, Qt.ShiftModifier)
    assert window.trace_selection.ordered_selection() == tuple(range(199, 205))
    assert window.trace_selection.anchor_trace == 199
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real mark fixture unavailable")
def test_one_visible_mark_enables_local_modes_and_two_marks_mode_stays_guarded(qapp, tmp_path):
    window = _window(BIG_FILE, tmp_path)
    manager = window._mark_managers[0]
    mark = manager.add_at_trace_position(250)
    assert mark is not None
    window._analysis_target_id = None
    window.analysis_region_combo.setCurrentText(AUTO_NEARBY)
    window._refresh_mark_ui()
    assert window.analysis_find_button.isEnabled()
    assert window._analysis_target_id == mark.object_id
    window.analysis_region_combo.setCurrentText(AROUND_MARK)
    assert window.analysis_find_button.isEnabled()
    window.analysis_region_combo.setCurrentText(BETWEEN_MARKS)
    assert not window.analysis_find_button.isEnabled()
    window.analysis_region_combo.setCurrentText(VISIBLE_RANGE)
    assert window.analysis_find_button.isEnabled()
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real mark fixture unavailable")
@pytest.mark.parametrize(
    "sequence",
    [
        (RANGE, POINT_MARK),
        (HORIZONTAL_LINE, POINT_MARK),
        (VERTICAL_LINE, POINT_MARK),
        (CROSSHAIR, POINT_MARK),
        (RANGE, CROSSHAIR, POINT_MARK),
        (CROSSHAIR, RANGE, POINT_MARK),
    ],
)
def test_mark_tool_reliably_returns_to_point_mark(qapp, tmp_path, sequence):
    window = _window(BIG_FILE, tmp_path)
    for tool in sequence:
        window.mark_tool_combo.setCurrentIndex(window.mark_tool_combo.findData(tool))
        assert window._current_mark_tool() == tool
    assert window.mark_tool_combo.currentText() == "Point Mark"
    window.add_mark_button.setChecked(True)
    x, y = window.plot_widget._curve.getData()
    window._place_mark(0, float(x[100]), float(y[100]))
    assert len(window._mark_managers[0].marks()) == 1
    assert not window._mark_managers[0].annotations()
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real IQ fixture unavailable")
def test_iq_preset_returns_to_normal_transform_operation(qapp, tmp_path):
    window = _window(BIG_FILE, tmp_path)
    original = (window.x_combo.currentText(), window.y_combo.currentText())
    window.axis_preset_combo.setCurrentIndex(window.axis_preset_combo.findText("IQ Transform"))
    assert window._is_iq_plot() and not window.transform_combo.isEnabled()
    window.axis_preset_combo.setCurrentIndex(0)
    assert not window._is_iq_plot()
    assert window.transform_combo.isEnabled()
    assert (window.x_combo.currentText(), window.y_combo.currentText()) == original
    assert window.transform_combo.findText("Magnitude") >= 0
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real overlay fixture unavailable")
def test_saved_overlay_full_round_trip(qapp, tmp_path):
    window = _window(BIG_FILE, tmp_path)
    _select(window, (19, 29, 39, 49))
    window.trace_selection.set_visible(49, False)
    window.trace_selection.set_reference(19)
    window.log_entries.set_active(39)
    window.trace_color_combo.setCurrentText(DISTINCT)
    window.transform_combo.setCurrentText("Phase")
    window.unwrap_checkbox.setChecked(True)
    window.auto_range_checkbox.setChecked(False)
    window.zmin_spin.setValue(-47.5)
    window.zmax_spin.setValue(12.25)
    assert window._save_overlay("Working Group")

    window.log_entries.select_row(1)
    window.trace_color_combo.setCurrentText(SEQUENTIAL)
    window.transform_combo.setCurrentText("Magnitude")
    window.unwrap_checkbox.setChecked(False)
    window.auto_range_checkbox.setChecked(True)
    saved = window.overlay_store.get(window._current_data_key(), "Working Group")
    assert window._apply_overlay(saved)
    assert window.trace_selection.ordered_selection() == (19, 29, 39, 49)
    assert window.trace_selection.visible_selection() == (19, 29, 39)
    assert window.trace_selection.active_trace == 39
    assert window.trace_selection.reference_trace == 19
    assert window.trace_color_combo.currentText() == DISTINCT
    assert window.transform_combo.currentText() == "Phase"
    assert window.unwrap_checkbox.isChecked()
    assert not window.auto_range_checkbox.isChecked()
    assert window.zmin_spin.value() == pytest.approx(-47.5)
    assert window.zmax_spin.value() == pytest.approx(12.25)
    window.close()


@pytest.mark.skipif(not FLUX_FILE.exists(), reason="Real Flux fixture unavailable")
def test_saved_overlay_restores_manual_2d_color_state(qapp, tmp_path):
    window = _window(FLUX_FILE, tmp_path)
    _select(window, (0, 1))
    window.mode_combo.setCurrentIndex(1)
    window.auto_range_checkbox.setChecked(False)
    window.zmin_spin.setValue(-55.0)
    window.zmax_spin.setValue(-5.0)
    window.colormap_combo.setCurrentText("LabLog BWR")
    assert window._save_overlay("Flux View")
    window.auto_range_checkbox.setChecked(True)
    window.colormap_combo.setCurrentIndex(0)
    saved = window.overlay_store.get(window._current_data_key(), "Flux View")
    assert window._apply_overlay(saved)
    assert window.mode_combo.currentIndex() == 1
    assert not window.auto_range_checkbox.isChecked()
    assert window.zmin_spin.value() == pytest.approx(-55.0)
    assert window.zmax_spin.value() == pytest.approx(-5.0)
    assert window.plot_2d_widget._z_min == pytest.approx(-55.0)
    assert window.plot_2d_widget._z_max == pytest.approx(-5.0)
    window.close()
