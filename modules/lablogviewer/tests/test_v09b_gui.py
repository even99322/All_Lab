"""
tests/test_v09b_gui.py — v0.9B

GUI smoke tests for the redesigned Advanced Viewer: dynamic X/Y Axis
selection, the Transform system (defaults + Save Transform), the
Log Entries table (replacing the plain Trace label as the primary
Trace UI), keyboard navigation through it, Sweep as a synced
quick-jump, and regression checks against the Database Browser /
other Viewer pages. Run via Qt's "offscreen" platform plugin.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.real_data import BIG_FILE, SMALL_FILE

try:
    from PySide6.QtWidgets import QApplication
    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not PYSIDE_AVAILABLE, reason="PySide6 not installed in this environment."
)

samples_pytestmark = pytest.mark.skipif(
    not BIG_FILE.exists(), reason="Real Labber sample file not present in this environment."
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_active_viewer(qapp, file_path: str, transform_store=None, axis_preset_store=None):
    from app.gui.main_window import MainWindow

    win = MainWindow(axis_preset_store=axis_preset_store)
    if transform_store is not None:
        win.transform_store = transform_store
    win.show()
    win.activateWindow()
    qapp.processEvents()
    win.open_file(file_path)
    return win


def _key_event(key):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    return QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier)


def test_gui_still_does_not_import_h5py():
    import ast

    gui_dir = Path(__file__).resolve().parent.parent / "app" / "gui"
    for py_file in gui_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "h5py", f"{py_file} imports h5py directly!"
            if isinstance(node, ast.ImportFrom):
                assert node.module != "h5py", f"{py_file} imports from h5py directly!"


# ---- Dynamic X/Y Axis selection (GUI) --------------------------------------------

@samples_pytestmark
def test_x_and_y_axis_combos_populated_dynamically(qapp):
    win = _make_active_viewer(qapp, str(BIG_FILE))
    x_items = {win.x_combo.itemText(i) for i in range(win.x_combo.count())}
    y_items = {win.y_combo.itemText(i) for i in range(win.y_combo.count())}
    assert x_items < y_items
    assert "Average Current" in x_items
    assert "Output power" in x_items
    assert "VNA - S21" not in x_items
    assert "VNA - S21" in y_items
    assert "Frequency" in x_items
    win.close()


@samples_pytestmark
def test_default_axis_selection_is_sensible(qapp):
    win = _make_active_viewer(qapp, str(BIG_FILE))
    assert win.x_combo.currentText() == "Frequency"
    assert win.y_combo.currentText() == "VNA - S21"
    win.close()


@samples_pytestmark
def test_entries_domain_axis_pair_plots_correctly(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.y_combo.setCurrentText("Output power")  # entries-domain first (avoids
    win.x_combo.setCurrentText("Average Current")  # an intermediate mismatch warning)
    xd, yd = win.plot_widget._curve.getData()
    assert xd.shape == (855,)
    assert yd.shape == (855,)
    win.close()


@samples_pytestmark
def test_points_domain_axis_pair_plots_correctly(qapp):
    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.x_combo.setCurrentText("Frequency")
    win.y_combo.setCurrentText("VNA - S21")
    xd, yd = win.plot_widget._curve.getData()
    assert xd.shape == (501,)
    assert xd[0] == pytest.approx(5.0197e9)
    win.close()


@samples_pytestmark
def test_incompatible_axis_pair_shows_warning_keeps_plot(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a)))

    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.x_combo.setCurrentText("Frequency")
    win.y_combo.setCurrentText("VNA - S21")
    xd_before, yd_before = win.plot_widget._curve.getData()

    warnings.clear()
    win.x_combo.setCurrentText("Average Current")  # now mismatched vs points-domain Y
    assert len(warnings) == 1

    xd_after, yd_after = win.plot_widget._curve.getData()
    assert np.array_equal(xd_before, xd_after)
    assert np.array_equal(yd_before, yd_after)  # previous plot preserved

    win.close()


@samples_pytestmark
def test_complex_channel_not_offered_as_raw_x_axis(qapp):
    win = _make_active_viewer(qapp, str(BIG_FILE))
    x_items = {win.x_combo.itemText(i) for i in range(win.x_combo.count())}
    y_items = {win.y_combo.itemText(i) for i in range(win.y_combo.count())}
    assert "VNA - S21" not in x_items
    assert "VNA - S21" in y_items
    win.close()


# ---- Transform system (GUI) ----------------------------------------------------

@samples_pytestmark
def test_transform_combo_has_four_defaults(qapp):
    win = _make_active_viewer(qapp, str(BIG_FILE))
    items = {win.transform_combo.itemText(i) for i in range(win.transform_combo.count())}
    assert items == {"Real", "Imaginary", "Magnitude", "Phase"}
    win.close()


@samples_pytestmark
def test_transform_disabled_for_non_complex_y(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.y_combo.setCurrentText("Output power")
    win.x_combo.setCurrentText("Average Current")
    assert not win.transform_combo.isEnabled()
    assert win.save_transform_button.isEnabled()  # complete plot presets also support scalar axes
    win.close()


@samples_pytestmark
def test_db_checkbox_only_enabled_for_magnitude(qapp):
    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.transform_combo.setCurrentText("Magnitude")
    assert win.db_checkbox.isEnabled()
    win.transform_combo.setCurrentText("Real")
    assert not win.db_checkbox.isEnabled()
    win.close()


@samples_pytestmark
def test_unwrap_checkbox_only_enabled_for_phase(qapp):
    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.transform_combo.setCurrentText("Phase")
    assert win.unwrap_checkbox.isEnabled()
    win.transform_combo.setCurrentText("Magnitude")
    assert not win.unwrap_checkbox.isEnabled()
    win.close()


@samples_pytestmark
def test_db_toggle_changes_plotted_data(qapp):
    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.transform_combo.setCurrentText("Magnitude")
    win.db_checkbox.setChecked(False)
    _, y_linear = win.plot_widget._curve.getData()
    win.db_checkbox.setChecked(True)
    _, y_db = win.plot_widget._curve.getData()
    assert not np.allclose(y_linear, y_db)
    assert np.allclose(20 * np.log10(y_linear), y_db, equal_nan=True)
    win.close()


@samples_pytestmark
def test_unwrap_toggle_changes_plotted_data(qapp):
    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.transform_combo.setCurrentText("Phase")
    win.unwrap_checkbox.setChecked(False)
    _, y_wrapped = win.plot_widget._curve.getData()
    win.unwrap_checkbox.setChecked(True)
    _, y_unwrapped = win.plot_widget._curve.getData()
    assert not np.allclose(y_wrapped, y_unwrapped)
    win.close()


@samples_pytestmark
def test_no_double_db_application(qapp):
    """Spec §7's explicit warning: switching transforms and toggling
    dB repeatedly must never compound - Magnitude(dB) always shows the
    SAME value regardless of how many times it was toggled off/on."""
    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.transform_combo.setCurrentText("Magnitude")
    win.db_checkbox.setChecked(True)
    _, y1 = win.plot_widget._curve.getData()
    win.db_checkbox.setChecked(False)
    win.db_checkbox.setChecked(True)
    _, y2 = win.plot_widget._curve.getData()
    assert np.allclose(y1, y2)
    win.close()


@samples_pytestmark
def test_save_plot_preset_persists_and_appears_for_current_data(qapp, tmp_path):
    from app.core.axis_preset_store import AxisPresetStore
    from app.core.transform_store import TransformStore

    store = TransformStore(tmp_path / "transforms.json")
    preset_store = AxisPresetStore(tmp_path / "axis_presets.json")
    win = _make_active_viewer(
        qapp, str(BIG_FILE), transform_store=store, axis_preset_store=preset_store
    )
    win.x_combo.setCurrentText("Frequency")
    win.y_combo.setCurrentText("VNA - S21")
    win.transform_combo.setCurrentText("Magnitude")
    win.db_checkbox.setChecked(True)

    from PySide6.QtWidgets import QInputDialog
    original = QInputDialog.getText
    QInputDialog.getText = staticmethod(lambda *a, **k: ("My S21 dB", True))
    try:
        win._on_save_transform_clicked()
    finally:
        QInputDialog.getText = original

    items = [win.axis_preset_combo.itemText(i) for i in range(win.axis_preset_combo.count())]
    assert "My S21 dB" in items
    assert win.axis_preset_combo.currentText() == "My S21 dB"

    saved = preset_store.get("My S21 dB", win._current_data_key())
    assert saved is not None
    assert saved.db is True

    win.close()


# ---- Log Entries table (primary Trace UI) ----------------------------------------

@samples_pytestmark
def test_log_entries_table_populated(qapp):
    win = _make_active_viewer(qapp, str(BIG_FILE))
    assert win.log_entries.row_count() == 855
    headers = [win.log_entries.table.horizontalHeaderItem(i).text()
               for i in range(win.log_entries.table.columnCount())]
    assert "Average Current" in headers
    assert "#" in headers
    win.close()


@samples_pytestmark
def test_log_entries_columns_not_hardcoded(qapp):
    """The columns must come from THIS file's actual step/log
    channels, not a fixed hardcoded set - cross-check against the
    small file, which has NO scalar columns at all (single point, all
    fixed) but still reports 1 entry."""
    win_big = _make_active_viewer(qapp, str(BIG_FILE))
    headers_big = {win_big.log_entries.table.horizontalHeaderItem(i).text()
                   for i in range(win_big.log_entries.table.columnCount())}
    win_big.close()

    win_small = _make_active_viewer(qapp, str(SMALL_FILE))
    assert win_small.log_entries.row_count() == 1
    win_small.close()

    assert "Average Current" in headers_big


@samples_pytestmark
def test_clicking_log_entry_row_updates_plot(qapp):
    win = _make_active_viewer(qapp, str(BIG_FILE))
    _, y_before = win.plot_widget._curve.getData()
    win.log_entries.select_row(700)
    _, y_after = win.plot_widget._curve.getData()
    assert not np.allclose(y_before, y_after)
    win.close()


@samples_pytestmark
def test_selected_row_visually_distinguishable(qapp):
    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.log_entries.select_row(42)
    selected_items = win.log_entries.table.selectedItems()
    assert len(selected_items) > 0
    assert selected_items[0].row() == 42
    win.close()


# ---- Keyboard Up/Down through Log Entries ---------------------------------------

@samples_pytestmark
def test_keyboard_down_advances_log_entry_selection(qapp):
    from PySide6.QtCore import Qt
    win = _make_active_viewer(qapp, str(BIG_FILE))
    assert win.log_entries.current_row() == 0
    win.eventFilter(win, _key_event(Qt.Key_Down))
    assert win.log_entries.current_row() == 1
    win.close()


@samples_pytestmark
def test_keyboard_up_retreats_log_entry_selection(qapp):
    from PySide6.QtCore import Qt
    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.log_entries.select_row(10)
    win.eventFilter(win, _key_event(Qt.Key_Up))
    assert win.log_entries.current_row() == 9
    win.close()


@samples_pytestmark
def test_keyboard_navigation_updates_plot_and_table_together(qapp):
    from PySide6.QtCore import Qt
    win = _make_active_viewer(qapp, str(BIG_FILE))
    _, y_before = win.plot_widget._curve.getData()
    win.eventFilter(win, _key_event(Qt.Key_Down))
    _, y_after = win.plot_widget._curve.getData()
    assert not np.allclose(y_before, y_after)
    assert win.log_entries.table.currentRow() == win.log_entries.current_row()
    win.close()


@samples_pytestmark
def test_boundary_cannot_go_below_first_entry(qapp):
    from PySide6.QtCore import Qt
    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.eventFilter(win, _key_event(Qt.Key_Up))
    assert win.log_entries.current_row() == 0
    win.close()


@samples_pytestmark
def test_boundary_cannot_go_past_last_entry(qapp):
    from PySide6.QtCore import Qt
    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.log_entries.select_row(854)
    win.eventFilter(win, _key_event(Qt.Key_Down))
    assert win.log_entries.current_row() == 854
    win.close()


# ---- Sweep = synced quick-jump, single source of truth ------------------------

@samples_pytestmark
def test_sweep_spin_jumps_log_entries_selection(qapp):
    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.entry_spin.setValue(500)
    assert win.log_entries.current_row() == 500
    win.close()


@samples_pytestmark
def test_log_entries_selection_syncs_sweep_spin(qapp):
    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.log_entries.select_row(321)
    assert win.entry_spin.value() == 321
    win.close()


@samples_pytestmark
def test_trace_display_reflects_log_entries_state(qapp):
    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.log_entries.select_row(99)
    assert win.trace_display_label.text() == "Trace 100 / 855"
    win.close()


@samples_pytestmark
def test_keyboard_nav_continues_from_sweep_jump(qapp):
    from PySide6.QtCore import Qt
    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.entry_spin.setValue(500)
    win.eventFilter(win, _key_event(Qt.Key_Down))
    assert win.log_entries.current_row() == 501
    assert win.entry_spin.value() == 501
    win.close()


@samples_pytestmark
def test_no_independent_sweep_and_log_entries_state(qapp):
    """There is exactly ONE current-trace state - LogEntriesWidget's
    selected row - both Sweep and the table always reflect it."""
    win = _make_active_viewer(qapp, str(BIG_FILE))
    win.log_entries.select_row(77)
    assert win.entry_spin.value() == win.log_entries.current_row() == 77
    win.entry_spin.setValue(200)
    assert win.log_entries.current_row() == win.entry_spin.value() == 200
    win.close()


# ---- Multi-viewer isolation -------------------------------------------------------

@samples_pytestmark
def test_multiple_viewers_independent_axis_and_transform(qapp):
    win1 = _make_active_viewer(qapp, str(BIG_FILE))
    win2 = _make_active_viewer(qapp, str(BIG_FILE))

    win1.transform_combo.setCurrentText("Phase")
    win2.transform_combo.setCurrentText("Real")
    assert win1.transform_combo.currentText() != win2.transform_combo.currentText()

    win1.log_entries.select_row(10)
    win2.log_entries.select_row(500)
    assert win1.log_entries.current_row() != win2.log_entries.current_row()

    win1.close()
    win2.close()


# ---- Regression: Database Browser + other pages ----------------------------------

@samples_pytestmark
def test_browser_opens_viewer_with_new_advanced_viewer_intact(qapp, tmp_path):
    import shutil
    from app.gui.browser_window import BrowserWindow
    from app.core.star_store import StarStore
    from PySide6.QtCore import Qt, QEventLoop, QTimer

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    shutil.copy(BIG_FILE, db_dir / "run1.hdf5")

    win = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    loop = QEventLoop()
    win.open_database(str(db_dir))
    win._scan_worker.finished_scan.connect(loop.quit)
    QTimer.singleShot(15000, loop.quit)
    loop.exec()

    item = win.tree.topLevelItem(0)
    entry = item.data(0, Qt.UserRole)
    win._open_viewer_for(entry)
    viewer = win._viewers[0]

    assert viewer.log_entries.row_count() == 855
    assert viewer.x_combo.currentText() == "Frequency"
    assert viewer.y_combo.currentText() == "VNA - S21"

    win.close()


@samples_pytestmark
def test_2d_and_nd_pages_unaffected_by_1d_redesign(qapp):
    win = _make_active_viewer(qapp, str(BIG_FILE))

    win.mode_combo.setCurrentIndex(1)
    assert win.plot_2d_widget._grid.z_values.shape == (855, 501)

    win.open_3d_window()
    assert win.plot_2d_widget_nd._grid.z_values.shape == (855, 501)

    win.mode_combo.setCurrentIndex(0)
    assert win.plot_widget._curve is not None

    win.close()
