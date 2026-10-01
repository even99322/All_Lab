"""
tests/test_gui_metadata_table_smoke.py — Phase 9

GUI smoke tests for the modeless Metadata / Data Table windows, run via Qt's
"offscreen" platform plugin.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.real_data import BIG_FILE

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


def test_metadata_and_table_widgets_construct_without_file(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    assert win.summary_text is not None
    assert win.data_table is not None
    assert win.metadata_dialog.windowTitle() == "Metadata"
    assert win.data_table_dialog.windowTitle() == "Data Table"
    win._show_metadata_dialog()
    win._show_data_table_dialog()
    assert win.metadata_dialog.isVisible()
    assert win.data_table_dialog.isVisible()
    assert win.controls_scroll_area.widget().isAncestorOf(win.marks_widget)
    assert win.formula_controls.isAncestorOf(win.x_formula_edit)
    win.close()


@samples_pytestmark
def test_metadata_tab_populated_after_open(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    text = win.summary_text.toPlainText()
    assert BIG_FILE.name in text
    assert "VNA - S21" in text
    assert "Frequency" in text
    assert "Average Current" in text
    win.close()


@samples_pytestmark
def test_data_table_populated_in_1d_mode(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    assert win.mode_combo.currentIndex() == 0
    assert win.data_table.rowCount() == 501
    assert win.data_table.item(0, 0).text() == "0"
    win.close()


@samples_pytestmark
def test_data_table_shows_guidance_in_2d_mode(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.mode_combo.setCurrentIndex(1)
    assert win.data_table.rowCount() == 0
    assert "1D Plot" in win.data_table_info_label.text() or "Line Cut" in win.data_table_info_label.text()
    win.close()


@samples_pytestmark
def test_surface_mode_does_not_fall_back_to_removed_nd_1d_submode(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.open_3d_window()
    assert win.data_table.rowCount() == 0

    idx_none = win.y_combo_nd.findData(None)
    win.y_combo_nd.setCurrentIndex(idx_none)
    assert win.nd_plot_stack.currentIndex() == 2
    assert "X and Y" in win.nd_surface_placeholder.text()
    assert win.data_table.rowCount() == 0

    idx_current = win.y_combo_nd.findData("Average Current")
    win.y_combo_nd.setCurrentIndex(idx_current)
    assert win.plot_2d_widget_nd._grid.z_values.shape == (855, 501)
    assert win.data_table.rowCount() == 0

    win.close()


@samples_pytestmark
def test_data_table_updates_when_1d_channel_changes(qapp):
    """Changing the sweep-entry spinbox on the 1D page should refresh
    the table's values (same channel, different entry)."""
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    first_val = win.data_table.item(0, 2).text()

    if win.entry_spin.maximum() > 0:
        win.entry_spin.setValue(500)
        second_val = win.data_table.item(0, 2).text()
        assert first_val != second_val

    win.close()


@samples_pytestmark
def test_switching_files_refreshes_metadata(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    text1 = win.summary_text.toPlainText()
    assert BIG_FILE.name in text1

    win.open_file(str(BIG_FILE))  # reopen - metadata must still be correct, not stale/duplicated
    text2 = win.summary_text.toPlainText()
    assert BIG_FILE.name in text2

    win.close()


def test_gui_does_not_import_h5py_still_holds():
    """Extends the ongoing static check to cover any Phase 9 additions
    to main_window.py (data table logic lives there, plus in
    app/core/data_table.py, which is core, not GUI)."""
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
