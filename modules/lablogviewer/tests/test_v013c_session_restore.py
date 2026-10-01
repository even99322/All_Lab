"""Focused v0.13C workspace session and Metadata Comment coverage."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.database_scanner import DatabaseScanResult, LogEntry
from app.core.session_store import SessionStore
from tests.real_data import BIG_FILE, SMALL_FILE

try:
    from PySide6.QtWidgets import QApplication
    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _entry(path: Path) -> LogEntry:
    stat = path.stat()
    return LogEntry(
        absolute_path=str(path.resolve()), relative_path=path.name, file_name=path.name,
        log_name=path.stem, status="ok", error_message=None,
        size_bytes=stat.st_size, mtime=stat.st_mtime, sweep_dimension="1D",
    )


def test_session_store_is_schema_aware_atomic_and_corruption_safe(tmp_path):
    path = tmp_path / "session.json"
    store = SessionStore(path)
    store.set({"database_path": "/data", "browser": {"selected_relative_path": "a.hdf5"}})
    raw = path.read_text(encoding="utf-8")
    assert '"schema_version": 1' in raw
    assert SessionStore(path).get()["database_path"] == "/data"
    path.write_text("{truncated", encoding="utf-8")
    assert SessionStore(path).get() == {}
    assert path.with_suffix(".json.bak").exists()


@pytest.mark.skipif(not PYSIDE_AVAILABLE or not SMALL_FILE.exists(), reason="Qt or real fixture unavailable")
def test_startup_auto_reopens_database_and_selected_data(qapp, tmp_path):
    from PySide6.QtCore import QEventLoop, QTimer
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    database = tmp_path / "database"
    database.mkdir()
    shutil.copy2(SMALL_FILE, database / "sample.hdf5")
    store = SessionStore(tmp_path / "session.json")
    first = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"), session_store=store)
    loop = QEventLoop()
    first.open_database(str(database))
    first._scan_worker.finished_scan.connect(loop.quit)
    QTimer.singleShot(15000, loop.quit)
    loop.exec()
    first.data_list.setCurrentItem(first.data_list.topLevelItem(0))
    first._flush_session()
    first.close()

    second = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"), session_store=SessionStore(store.storage_path))
    loop = QEventLoop()
    assert second.restore_previous_session()
    second._scan_worker.finished_scan.connect(loop.quit)
    QTimer.singleShot(15000, loop.quit)
    loop.exec()
    assert Path(second.scanner_root) == database.resolve()
    assert second._selected_data()[1] is not None
    assert second._selected_data()[1].relative_path == "sample.hdf5"
    second.close()


def test_missing_database_session_starts_normally(tmp_path):
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    store = SessionStore(tmp_path / "session.json")
    store.set({"database_path": str(tmp_path / "missing")})
    browser = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"), session_store=store)
    assert not browser.restore_previous_session()
    assert browser.scan_result is None
    browser.close()


@pytest.mark.skipif(not PYSIDE_AVAILABLE or not SMALL_FILE.exists(), reason="Qt or real fixture unavailable")
def test_browser_session_restores_selection_viewer_and_missing_data_isolated(qapp, tmp_path):
    from app.core.comment_store import CommentStore
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    entry = _entry(SMALL_FILE)
    result = DatabaseScanResult(str(SMALL_FILE.parent), str(SMALL_FILE.parent), [entry])
    store = SessionStore(tmp_path / "session.json")
    browser = BrowserWindow(
        star_store=StarStore(tmp_path / "stars.json"),
        comment_store=CommentStore(tmp_path / "comments.json"), session_store=store,
    )
    browser.scan_result = result
    browser.scanner_root = result.root_path
    browser._populate_folder_tree(result)
    browser.data_list.setCurrentItem(browser.data_list.topLevelItem(0))
    viewer = browser._open_viewer_for(entry)
    assert viewer is not None
    viewer._show_metadata_dialog()
    browser._flush_session()
    saved = store.get()
    assert saved["browser"]["selected_relative_path"] == entry.relative_path
    assert len(saved["viewers"]) == 1

    restored = BrowserWindow(
        star_store=StarStore(tmp_path / "stars-2.json"),
        comment_store=CommentStore(tmp_path / "comments-2.json"), session_store=store,
    )
    restored.scan_result = result
    restored.scanner_root = result.root_path
    restored._populate_folder_tree(result)
    restored._restore_after_scan(saved)
    assert restored._selected_data()[1].relative_path == entry.relative_path
    assert len([item for item in restored._viewers if item.isVisible()]) == 1
    assert restored._viewers[-1].metadata_dialog.isVisible()

    broken = dict(saved)
    broken["viewers"] = [dict(saved["viewers"][0], source_path=str(tmp_path / "missing.hdf5"))]
    restored._restore_after_scan(broken)
    assert restored._selected_data()[1].relative_path == entry.relative_path
    for item in browser._viewers + restored._viewers:
        item.close()
    browser.close()
    restored.close()


@pytest.mark.skipif(not PYSIDE_AVAILABLE or not BIG_FILE.exists(), reason="Qt or real fixture unavailable")
def test_viewer_session_restores_panes_splitters_trace_and_auxiliary_windows(qapp, tmp_path):
    from app.core.comment_store import CommentStore
    from app.core.viewer_display_state_store import ViewerDisplayStateStore
    from app.gui.main_window import MainWindow

    first = MainWindow(
        viewer_display_state_store=ViewerDisplayStateStore(tmp_path / "viewer.json"),
        comment_store=CommentStore(tmp_path / "comments.json"),
    )
    first.open_file(str(BIG_FILE))
    first.pane_layout_combo.setCurrentText("2 Panes · Side by Side")
    first._activate_pane(2)
    first.log_entries.restore_selection([0, 1], active=1, visible=[1], reference=0)
    first._show_metadata_dialog()
    first._show_data_table_dialog()
    state = first.session_state()
    assert state is not None

    second = MainWindow(
        viewer_display_state_store=ViewerDisplayStateStore(tmp_path / "viewer.json"),
        comment_store=CommentStore(tmp_path / "comments.json"),
    )
    second.open_file(str(BIG_FILE))
    assert second.restore_session_state(state)
    assert second.pane_layout_combo.currentText() == "2 Panes · Side by Side"
    assert second._active_pane_id == 2
    assert second.log_entries.selection_state.active_trace == 1
    assert second.log_entries.selection_state.reference_trace == 0
    assert second.metadata_dialog.isVisible()
    assert second.data_table_dialog.isVisible()
    first.close()
    second.close()
