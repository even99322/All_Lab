"""v1.0.4: a running scan can be cancelled; the IP-transfer row of the Browser window is hidden."""

from __future__ import annotations

import os

import h5py
import pytest

from app.core.database_scanner import DatabaseScanner, ScanCancelled


def test_scan_can_be_cancelled_and_nothing_is_modified(tmp_path):
    for i in range(30):
        with h5py.File(tmp_path / f"f{i:02d}.hdf5", "w") as f:
            f.create_dataset("x", data=[i])
    before = {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()}
    calls = [0]

    def cancel():
        calls[0] += 1
        return calls[0] > 5

    with pytest.raises(ScanCancelled):
        DatabaseScanner.scan(tmp_path, fast_listing=True, should_cancel=cancel)
    assert {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()} == before


def test_transfer_row_is_hidden_in_browser_window():
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    from app.gui.browser_window import BrowserWindow

    window = BrowserWindow()
    window.show()
    app.processEvents()
    assert not window.transfer_panel.isVisible()
    window.close()
