"""v0.12D Multi-Pane Viewer acceptance tests."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.core.axis_preset_store import AxisPresetStore
from app.core.mark_model import POINT_MARK, RANGE
from app.core.overlay_store import OverlayStore
from app.gui.main_window import MainWindow
from app.gui.multi_pane import (
    FOUR_PANES, ONE_PANE, THREE_PANES, TWO_SIDE, TWO_STACKED,
)
from tests.real_data import BIG_FILE


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(tmp_path, qapp):
    window = MainWindow(
        axis_preset_store=AxisPresetStore(tmp_path / "axis.json"),
        overlay_store=OverlayStore(tmp_path / "overlays.json"),
    )
    window.show()
    window.open_file(str(BIG_FILE))
    qapp.processEvents()
    return window


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real pane fixture unavailable")
def test_single_pane_default_and_all_fixed_layouts(qapp, tmp_path):
    window = _window(tmp_path, qapp)
    assert window.pane_layout_combo.currentText() == ONE_PANE
    assert window.multi_pane_splitter.isHidden()
    assert not window.mode_stack.isHidden()
    for name, count in ((TWO_SIDE, 2), (TWO_STACKED, 2),
                        (THREE_PANES, 3), (FOUR_PANES, 4)):
        window.pane_layout_combo.setCurrentText(name)
        qapp.processEvents()
        assert not window.multi_pane_splitter.isHidden()
        assert sum(not window._pane_frames[index].isHidden()
                   for index in range(1, count + 1)) == count
        assert tuple(window._pane_states)[:count] == tuple(range(1, count + 1))
    window.pane_layout_combo.setCurrentText(ONE_PANE)
    assert window.multi_pane_splitter.isHidden()
    assert not window.mode_stack.isHidden()
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real pane fixture unavailable")
def test_active_pane_controls_and_independent_magnitude_phase_iq(qapp, tmp_path):
    window = _window(tmp_path, qapp)
    window.pane_layout_combo.setCurrentText(THREE_PANES)
    window._activate_pane(1)
    window.transform_combo.setCurrentText("Magnitude")
    window.db_checkbox.setChecked(True)
    window._activate_pane(2)
    window.transform_combo.setCurrentText("Phase")
    window.unwrap_checkbox.setChecked(True)
    window._activate_pane(3)
    iq_index = window.axis_preset_combo.findText("IQ Transform")
    assert iq_index >= 0
    window.axis_preset_combo.setCurrentIndex(iq_index)
    window._capture_active_pane_state()

    assert window._pane_states[1].transform_name == "Magnitude"
    assert window._pane_states[1].db
    assert window._pane_states[2].transform_name == "Phase"
    assert window._pane_states[2].unwrap
    assert window._pane_states[3].x_axis["transform_key"] == "imag"
    assert window._pane_states[3].y_axis["transform_key"] == "real"

    window._activate_pane(2)
    assert window.active_pane_label.text() == "Active Pane: 2"
    assert window.transform_combo.currentText() == "Phase"
    assert window._pane_frames[2].styleSheet() != window._pane_frames[1].styleSheet()
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real pane fixture unavailable")
def test_trace_sync_on_and_off_preserve_independent_trace_state(qapp, tmp_path):
    window = _window(tmp_path, qapp)
    window.pane_layout_combo.setCurrentText(TWO_SIDE)
    assert window.sync_trace_checkbox.isChecked()
    window.log_entries.select_row(29)
    assert window._pane_states[1].trace_index == 29
    assert window._pane_states[2].trace_index == 29

    window.sync_trace_checkbox.setChecked(False)
    window._activate_pane(2)
    window.log_entries.select_row(39)
    assert window._pane_states[1].trace_index == 29
    assert window._pane_states[2].trace_index == 39
    window._activate_pane(1)
    assert window._pane_states[1].trace_index == 29
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real pane fixture unavailable")
def test_x_sync_only_for_compatible_1d_axes(qapp, tmp_path):
    window = _window(tmp_path, qapp)
    window.pane_layout_combo.setCurrentText(THREE_PANES)
    window.sync_x_checkbox.setChecked(True)
    window._pane_states[2].x_axis = dict(window._pane_states[1].x_axis)
    window._on_pane_x_range_changed(1, 5.020e9, 5.025e9)
    assert window._pane_states[2].x_range == pytest.approx((5.020e9, 5.025e9))
    old_iq = window._pane_states[3].x_range
    window._on_pane_x_range_changed(1, 5.021e9, 5.024e9)
    assert window._pane_states[3].x_range == old_iq
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real pane fixture unavailable")
def test_marks_are_pane_local_and_survive_active_switch(qapp, tmp_path):
    window = _window(tmp_path, qapp)
    window.pane_layout_combo.setCurrentText(TWO_SIDE)
    first = window._pane_states[1].mark_managers[0]
    second = window._pane_states[2].mark_managers[0]
    first.add_at_trace_position(100)
    first.add_range(5.021e9, 5.023e9)
    window._render_multi_panes()
    assert len(first.objects()) == 2
    assert len(second.objects()) == 0
    window._activate_pane(2)
    second.add_at_trace_position(200)
    window._refresh_mark_ui()
    assert len(first.marks()) == 1 and len(second.marks()) == 1
    assert first.marks()[0].sample_index != second.marks()[0].sample_index
    window._activate_pane(1)
    assert window._current_mark_manager() is first
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real 1D/2D fixture unavailable")
def test_1d_and_2d_coexist_with_independent_color_state(qapp, tmp_path):
    window = _window(tmp_path, qapp)
    window.pane_layout_combo.setCurrentText(TWO_SIDE)
    window._activate_pane(2)
    window.mode_combo.setCurrentIndex(1)
    window.auto_range_checkbox.setChecked(False)
    window.zmin_spin.setValue(-65.0)
    window.zmax_spin.setValue(-15.0)
    window._render_multi_panes()
    assert window._pane_frames[1].stack.currentIndex() == 0
    assert window._pane_frames[1].plot_1d._trace_data
    assert window._pane_frames[2].stack.currentIndex() == 1
    assert window._pane_frames[2].plot_2d._grid is not None
    assert window._pane_frames[2].plot_2d._z_min == pytest.approx(-65.0)
    assert window._pane_frames[2].plot_2d._z_max == pytest.approx(-15.0)
    window._activate_pane(1)
    assert window.mode_combo.currentIndex() == 0
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real pane fixture unavailable")
def test_multitrace_reference_visibility_and_saved_overlay_survive(qapp, tmp_path):
    window = _window(tmp_path, qapp)
    window.log_entries.select_row(9)
    window.log_entries.select_row(19, Qt.ControlModifier)
    window.log_entries.select_row(29, Qt.ControlModifier)
    window.trace_selection.set_visible(29, False)
    window.trace_selection.set_reference(9)
    assert window._save_overlay("Pane compatibility")
    saved = window.overlay_store.get(window._current_data_key(), "Pane compatibility")
    window.pane_layout_combo.setCurrentText(TWO_SIDE)
    assert window._apply_overlay(saved)
    window._render_multi_panes()
    assert window.trace_selection.ordered_selection() == (9, 19, 29)
    assert window.trace_selection.visible_selection() == (9, 19)
    assert window.trace_selection.reference_trace == 9
    assert set(window._pane_frames[1].plot_1d._trace_data) == {9, 19}
    assert set(window._pane_frames[2].plot_1d._trace_data) == {9, 19}
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real pane fixture unavailable")
def test_maximize_active_pane_restores_layout_and_state(qapp, tmp_path):
    window = _window(tmp_path, qapp)
    window.pane_layout_combo.setCurrentText(FOUR_PANES)
    window._activate_pane(3)
    before = {index: state.transform_name for index, state in window._pane_states.items()}
    window.maximize_plot_button.setChecked(True)
    qapp.processEvents()
    assert window._multi_pane_maximized
    assert not window._pane_frames[3].isHidden()
    assert all(window._pane_frames[index].isHidden() for index in (1, 2, 4))
    window.maximize_plot_button.setChecked(False)
    qapp.processEvents()
    assert not window._multi_pane_maximized
    assert all(not window._pane_frames[index].isHidden() for index in range(1, 5))
    assert before == {index: state.transform_name for index, state in window._pane_states.items()}
    assert window._active_pane_id == 3
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real pane fixture unavailable")
def test_two_pane_creation_reuses_cached_data(qapp, tmp_path):
    window = _window(tmp_path, qapp)
    before = window.cached.cache.stats()["misses"]
    window.pane_layout_combo.setCurrentText(TWO_SIDE)
    qapp.processEvents()
    after_first = window.cached.cache.stats()["misses"]
    window._render_multi_panes()
    after_second = window.cached.cache.stats()["misses"]
    assert after_second == after_first
    assert after_first - before <= 1
    window.close()
