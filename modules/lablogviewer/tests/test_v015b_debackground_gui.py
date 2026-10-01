"""Off-screen Qt interaction tests for the De-background Browser workflow."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from app.core.database_scanner import DatabaseScanner
from app.core.star_store import StarStore
from app.gui.browser_window import BrowserWindow
from app.gui.data_picker_dialog import DataPickerDialog
from app.gui.debackground_dialog import DeBackgroundDialog
from tests.test_v015b_debackground import _build_labber_file


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _make_database(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    frequency = 5.0e9 + 1.0e6 * np.arange(5)
    target_values = np.full((5, 4), 0.25 + 0.5j)
    background_values = np.full((5, 1), 0.5 + 0.25j)
    _build_labber_file(
        root / "target.hdf5", {"VNA - S21": target_values}, frequency,
        step_values=np.arange(4),
    )
    nested = root / "Reference"
    nested.mkdir()
    _build_labber_file(
        nested / "background.hdf5", {"VNA - S21": background_values}, frequency,
    )
    return DatabaseScanner.scan(root)


def _click_tree_item(tree, item):
    rect = tree.visualItemRect(item)
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, pos=rect.center())


def _wait_until(qapp, predicate, timeout_ms=5000):
    elapsed = 0
    while elapsed < timeout_ms and not predicate():
        qapp.processEvents()
        QTest.qWait(20)
        elapsed += 20
    qapp.processEvents()
    assert predicate(), "Timed out waiting for Qt workflow state."


def _select_with_picker(qapp, dialog, role, relative_folder, filename):
    def choose():
        picker = QApplication.activeModalWidget()
        assert isinstance(picker, DataPickerDialog)
        folder = picker._items_by_folder[relative_folder]
        _click_tree_item(picker.folder_tree, folder)
        qapp.processEvents()
        item = next(
            picker.file_tree.topLevelItem(row)
            for row in range(picker.file_tree.topLevelItemCount())
            if Path(picker.file_tree.topLevelItem(row).data(0, Qt.UserRole).absolute_path).name == filename
        )
        _click_tree_item(picker.file_tree, item)
        QTest.mouseClick(picker.ok_button, Qt.LeftButton)

    QTimer.singleShot(0, choose)
    QTest.mouseClick(getattr(dialog, f"{role}_select_button"), Qt.LeftButton)


def test_data_picker_uses_database_hierarchy_and_native_selection(qapp, tmp_path):
    result = _make_database(tmp_path / "database")
    dialog = DataPickerDialog(result, "Select Background Data")
    dialog.show()
    qapp.processEvents()
    folder = dialog._items_by_folder[("Reference",)]
    _click_tree_item(dialog.folder_tree, folder)
    qapp.processEvents()
    item = dialog.file_tree.topLevelItem(0)
    assert item.text(0) == "background"
    _click_tree_item(dialog.file_tree, item)
    assert dialog.ok_button.isEnabled()
    QTest.mouseClick(dialog.ok_button, Qt.LeftButton)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_entry is not None
    assert Path(dialog.selected_entry.absolute_path).name == "background.hdf5"
    dialog.close()


def test_browser_action_preselects_selected_data_and_opens_modeless_window(qapp, tmp_path):
    result = _make_database(tmp_path / "database")
    browser = BrowserWindow(star_store=StarStore(tmp_path / "state" / "stars.json"))
    browser.scan_result = result
    target = next(entry for entry in result.entries if entry.file_name == "target.hdf5")
    browser._selected_data = lambda: (None, target)
    browser.show()
    QTest.mouseClick(browser.debackground_button, Qt.LeftButton)
    qapp.processEvents()
    dialog = browser._debackground_dialog
    assert isinstance(dialog, DeBackgroundDialog)
    assert dialog.isVisible()
    assert dialog.windowModality() == Qt.NonModal
    assert dialog.target_path == str(Path(target.absolute_path).resolve())
    assert dialog.target_label.text() == "target.hdf5"
    dialog.close()
    browser.close()


def test_complete_debackground_dialog_refreshes_browser_and_opens_normal_viewer(qapp, tmp_path):
    root = tmp_path / "database"
    result = _make_database(root)
    browser = BrowserWindow(star_store=StarStore(tmp_path / "state" / "stars.json"))
    browser.scan_result = result
    browser.scanner_root = str(root.resolve())
    target = next(entry for entry in result.entries if entry.file_name == "target.hdf5")
    background = next(entry for entry in result.entries if entry.file_name == "background.hdf5")
    dialog = DeBackgroundDialog(browser, target)
    dialog.show()
    qapp.processEvents()

    assert not dialog.generate_button.isEnabled()
    _select_with_picker(qapp, dialog, "background", ("Reference",), "background.hdf5")
    assert dialog.background_path == str(Path(background.absolute_path).resolve())

    _wait_until(qapp, lambda: dialog._inspection is not None)
    assert dialog._inspection.can_generate
    assert dialog.channel_combo.currentText() == "VNA - S21"

    QTest.mouseClick(dialog.background_clear_button, Qt.LeftButton)
    assert dialog.background_path is None
    assert not dialog.generate_button.isEnabled()
    _select_with_picker(qapp, dialog, "background", ("Reference",), "background.hdf5")
    _wait_until(qapp, lambda: dialog._inspection is not None and dialog._inspection.can_generate)

    QTest.mouseClick(dialog.target_clear_button, Qt.LeftButton)
    assert dialog.target_path is None
    assert not dialog.generate_button.isEnabled()
    _select_with_picker(qapp, dialog, "target", (), "target.hdf5")
    _wait_until(qapp, lambda: dialog._inspection is not None and dialog._inspection.can_generate)

    dialog.channel_combo.showPopup()
    qapp.processEvents()
    QTest.mouseClick(dialog.channel_combo.view().viewport(), Qt.LeftButton,
                     pos=dialog.channel_combo.view().visualRect(dialog.channel_combo.model().index(0, 0)).center())
    qapp.processEvents()
    assert dialog.generate_button.isEnabled()

    output = root / "target_debg.hdf5"
    dialog._output_customized = True
    dialog.output_edit.setText(str(output))

    output.write_bytes(b"old output")

    def cancel_collision():
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox)
        button = next(button for button in box.buttons() if button.text() == "Cancel")
        QTest.mouseClick(button, Qt.LeftButton)

    QTimer.singleShot(0, cancel_collision)
    QTest.mouseClick(dialog.generate_button, Qt.LeftButton)
    assert output.read_bytes() == b"old output"

    def confirm_replace():
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox)
        button = next(button for button in box.buttons() if button.text() == "Overwrite")
        QTest.mouseClick(button, Qt.LeftButton)

    QTimer.singleShot(0, confirm_replace)
    QTest.mouseClick(dialog.generate_button, Qt.LeftButton)
    _wait_until(qapp, lambda: dialog._last_output_path is not None and dialog._processing_worker is None, 10000)
    assert output.is_file()
    assert dialog.status_label.text().startswith("De-background completed")
    _wait_until(qapp, lambda: any(
        Path(entry.absolute_path).resolve() == output.resolve()
        for entry in (browser.scan_result.entries if browser.scan_result else [])
    ), 10000)

    QTest.mouseClick(dialog.open_viewer_button, Qt.LeftButton)
    qapp.processEvents()
    assert browser._viewers
    assert Path(browser._viewers[-1].experiment.source_path).resolve() == output.resolve()

    for viewer in list(browser._viewers):
        viewer.close()
    dialog.close()
    browser.close()


def test_dialog_resizes_without_hiding_actions(qapp, tmp_path):
    result = _make_database(tmp_path / "database")
    target = next(entry for entry in result.entries if entry.file_name == "target.hdf5")
    browser = BrowserWindow(star_store=StarStore(tmp_path / "state" / "stars.json"))
    dialog = DeBackgroundDialog(browser, target)
    dialog.show()
    qapp.processEvents()
    for size in ((560, 500), (900, 760), (640, 560)):
        dialog.resize(*size)
        qapp.processEvents()
        assert dialog.cancel_button.isVisible()
        assert dialog.generate_button.isVisible()
        assert dialog.output_edit.isVisible()
    dialog.close()
    browser.close()
