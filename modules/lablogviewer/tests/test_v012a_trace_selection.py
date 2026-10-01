"""v0.12A active/selected/anchor trace selection acceptance tests."""

from __future__ import annotations

import os
from time import perf_counter

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.core.axis_preset_store import AxisPresetStore
from app.core.trace_selection import TraceSelectionState
from app.gui.main_window import MainWindow
from tests.real_data import BIG_FILE


def test_initial_single_selection_and_invalid_identity():
    state = TraceSelectionState(range(5))
    assert state.active_trace == 0
    assert state.anchor_trace == 0
    assert state.ordered_selection() == (0,)
    assert not state.single_select(99)
    state.reset(range(3), initial=99)
    assert state.ordered_selection() == (0,)


def test_single_range_repeated_shift_and_reverse_selection():
    state = TraceSelectionState(range(100))
    state.single_select(19)
    state.range_select(39)
    assert state.ordered_selection() == tuple(range(19, 40))
    assert state.active_trace == 39 and state.anchor_trace == 19
    state.range_select(29)
    assert state.ordered_selection() == tuple(range(19, 30))
    assert state.anchor_trace == 19
    state.single_select(39)
    state.range_select(19)
    assert state.ordered_selection() == tuple(range(19, 40))


def test_toggle_add_remove_order_active_rule_and_empty_protection():
    state = TraceSelectionState(range(100))
    state.single_select(19)
    state.toggle_select(80)
    state.toggle_select(34)
    assert state.ordered_selection() == (19, 34, 80)
    state.toggle_select(34)
    assert state.ordered_selection() == (19, 80)
    assert state.active_trace == 19
    assert state.anchor_trace == 34
    state.single_select(50)
    state.toggle_select(50)
    assert state.ordered_selection() == (50,)
    assert state.active_trace == 50


def test_stable_order_duplicate_values_and_data_reset():
    state = TraceSelectionState((9, 3, 7))
    state.toggle_select(7)
    state.toggle_select(3)
    assert state.ordered_selection() == (9, 3, 7)
    state.reset((0, 1), initial=1)
    assert state.ordered_selection() == (1,)
    assert state.active_trace == state.anchor_trace == 1


def test_keyboard_move_and_shift_extension_keep_anchor():
    state = TraceSelectionState(range(20))
    state.single_select(5)
    state.move_active(1, extend=True)
    state.move_active(1, extend=True)
    assert state.ordered_selection() == (5, 6, 7)
    assert state.active_trace == 7 and state.anchor_trace == 5
    state.move_active(-1)
    assert state.ordered_selection() == (6,)
    assert state.active_trace == state.anchor_trace == 6


def test_large_state_selection_is_fast_and_ordered():
    state = TraceSelectionState(range(5000))
    started = perf_counter()
    state.single_select(0)
    state.range_select(4999)
    elapsed = perf_counter() - started
    assert state.selected_count == 5000
    assert state.ordered_selection() == tuple(range(5000))
    assert elapsed < 0.1


pytestmark = pytest.mark.skipif(not BIG_FILE.exists(), reason="Real 855-trace fixture unavailable")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(qapp, tmp_path):
    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / "presets.json"))
    window.open_file(str(BIG_FILE))
    window.show()
    window.activateWindow()
    qapp.processEvents()
    return window


def _click_row(window, row, modifier=Qt.NoModifier):
    table = window.log_entries.table
    item = table.item(row, 0)
    table.scrollToItem(item)
    QApplication.processEvents()
    point = table.visualItemRect(item).center()
    QTest.mouseClick(table.viewport(), Qt.LeftButton, modifier, point)
    QApplication.processEvents()


def test_gui_default_single_selection_summary_and_active_indicator(qapp, tmp_path):
    window = _window(qapp, tmp_path)
    assert window.trace_selection.ordered_selection() == (0,)
    assert window.trace_selection.active_trace == 0
    assert window.trace_display_label.text() == "Trace 1 / 855"
    assert window.log_entries.table.verticalHeaderItem(0).text() == ">"
    window.close()


def test_gui_shift_reverse_and_repeated_ranges(qapp, tmp_path):
    window = _window(qapp, tmp_path)
    _click_row(window, 19)
    _click_row(window, 39, Qt.ShiftModifier)
    assert window.trace_selection.ordered_selection() == tuple(range(19, 40))
    assert window.trace_selection.active_trace == 39
    assert window.trace_selection.anchor_trace == 19
    assert "21 selected" in window.trace_display_label.text()
    _click_row(window, 29, Qt.ShiftModifier)
    assert window.trace_selection.ordered_selection() == tuple(range(19, 30))
    _click_row(window, 39)
    _click_row(window, 19, Qt.ShiftModifier)
    assert window.trace_selection.ordered_selection() == tuple(range(19, 40))
    window.close()


@pytest.mark.parametrize("modifier", [Qt.ControlModifier, Qt.MetaModifier])
def test_gui_ctrl_and_command_discontinuous_toggle(qapp, tmp_path, modifier):
    window = _window(qapp, tmp_path)
    _click_row(window, 19)
    _click_row(window, 34, modifier)
    _click_row(window, 80, modifier)
    assert window.trace_selection.ordered_selection() == (19, 34, 80)
    assert window.trace_selection.active_trace == 80
    assert len(window.plot_widget.plot_widget.listDataItems()) == 3
    _click_row(window, 34, modifier)
    assert window.trace_selection.ordered_selection() == (19, 80)
    assert window.trace_selection.active_trace == 80
    window.close()


def test_plain_click_and_sweep_jump_reset_selection(qapp, tmp_path):
    window = _window(qapp, tmp_path)
    _click_row(window, 10)
    _click_row(window, 20, Qt.MetaModifier)
    assert window.trace_selection.selected_count == 2
    _click_row(window, 50)
    assert window.trace_selection.ordered_selection() == (50,)
    assert window.trace_selection.active_trace == window.trace_selection.anchor_trace == 50
    window.entry_spin.setValue(100)
    assert window.trace_selection.ordered_selection() == (100,)
    assert window.log_entries.current_row() == 100
    window.close()


def test_shift_keyboard_extension_and_plain_arrow_reset(qapp, tmp_path):
    window = _window(qapp, tmp_path)
    window.log_entries.select_row(20)
    window._navigate_trace(1, extend=True)
    window._navigate_trace(1, extend=True)
    assert window.trace_selection.ordered_selection() == (20, 21, 22)
    window._navigate_trace(-1)
    assert window.trace_selection.ordered_selection() == (21,)
    window.close()


def test_active_plot_updates_overlay_with_one_redraw(qapp, tmp_path):
    window = _window(qapp, tmp_path)
    calls = 0
    original = window.update_plot

    def counted_update():
        nonlocal calls
        calls += 1
        return original()

    window.update_plot = counted_update
    _click_row(window, 0)
    calls = 0
    _click_row(window, 854, Qt.ShiftModifier)
    assert window.trace_selection.selected_count == 855
    assert window.trace_selection.active_trace == 854
    assert calls == 1
    assert len(window.plot_widget.plot_widget.listDataItems()) == 855
    assert "855 selected" in window.trace_display_label.text()
    window.close()


def test_two_viewers_and_reload_have_independent_selection(qapp, tmp_path):
    first = _window(qapp, tmp_path)
    second = _window(qapp, tmp_path)
    first.log_entries.select_row(20, Qt.ControlModifier)
    second.log_entries.select_row(40)
    assert first.trace_selection.ordered_selection() != second.trace_selection.ordered_selection()
    first.open_file(str(BIG_FILE))
    assert first.trace_selection.ordered_selection() == (0,)
    assert second.trace_selection.ordered_selection() == (40,)
    first.close()
    second.close()
