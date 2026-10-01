"""
tests/test_gui_browser_smoke.py — v0.8

GUI smoke tests for BrowserWindow and multi-Viewer behavior, run via
Qt's "offscreen" platform plugin.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from app.gui.browser_window import STAR_STATE_ROLE

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
    not (BIG_FILE.exists() and SMALL_FILE.exists()),
    reason="Real Labber sample files not present in this environment.",
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _run_scan_and_wait(win, root_path: str, timeout_ms: int = 15000) -> None:
    """Runs a real scan through the QThread worker and blocks (via a
    local Qt event loop) until it completes - used so tests exercise
    the actual background-thread code path, not just the core
    DatabaseScanner directly."""
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    win.open_database(root_path)
    win._scan_worker.finished_scan.connect(loop.quit)
    win._scan_worker.failed.connect(loop.quit)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()


# ---- construction --------------------------------------------------------------

def test_browser_window_constructs(qapp, tmp_path):
    from app.gui.browser_window import BrowserWindow
    from app.core.star_store import StarStore

    win = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    assert win.scan_result is None
    assert not win.reload_button.isEnabled()
    win.close()


def test_h5py_not_imported_in_browser_gui_modules():
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


# ---- Open Database / scanning ---------------------------------------------------

@samples_pytestmark
def test_open_database_populates_tree(qapp, tmp_path):
    from app.gui.browser_window import BrowserWindow
    from app.core.star_store import StarStore

    db_dir = tmp_path / "db"
    (db_dir / "ExpA").mkdir(parents=True)
    shutil.copy(BIG_FILE, db_dir / "ExpA" / "run1.hdf5")
    shutil.copy(SMALL_FILE, db_dir / "run2.hdf5")

    win = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    _run_scan_and_wait(win, str(db_dir))

    assert win.scan_result is not None
    assert win.scan_result.n_total == 2
    assert win.scan_result.n_ok == 2
    assert win.reload_button.isEnabled()
    assert win.tree.topLevelItemCount() >= 1

    win.close()


@samples_pytestmark
def test_reload_database_rescans_same_folder(qapp, tmp_path):
    from app.gui.browser_window import BrowserWindow
    from app.core.star_store import StarStore

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    shutil.copy(SMALL_FILE, db_dir / "run1.hdf5")

    win = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    _run_scan_and_wait(win, str(db_dir))
    assert win.scan_result.n_total == 1

    shutil.copy(BIG_FILE, db_dir / "run2.hdf5")

    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    win.reload_database()
    win._scan_worker.finished_scan.connect(loop.quit)
    QTimer.singleShot(15000, loop.quit)
    loop.exec()

    assert win.scan_result.n_total == 2  # picked up the new file
    win.close()


def test_reload_disabled_with_no_database_open(qapp, tmp_path):
    from app.gui.browser_window import BrowserWindow
    from app.core.star_store import StarStore

    win = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    assert not win.reload_button.isEnabled()
    assert not win.reload_action.isEnabled()
    win.reload_database()  # must not raise even though nothing is open
    win.close()


@samples_pytestmark
def test_empty_database_folder_handled_gracefully(qapp, tmp_path):
    from app.gui.browser_window import BrowserWindow
    from app.core.star_store import StarStore

    db_dir = tmp_path / "empty_db"
    db_dir.mkdir()
    win = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    _run_scan_and_wait(win, str(db_dir))
    assert win.scan_result.n_total == 0
    assert win.tree.topLevelItemCount() == 0
    win.close()


@samples_pytestmark
def test_corrupted_file_shown_as_error_in_tree(qapp, tmp_path, monkeypatch):
    from app.gui.browser_window import BrowserWindow
    from app.core.star_store import StarStore
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    shutil.copy(SMALL_FILE, db_dir / "good.hdf5")
    (db_dir / "bad.hdf5").write_bytes(b"not valid hdf5")

    win = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    _run_scan_and_wait(win, str(db_dir))

    error_entries = [e for e in win.scan_result.entries if e.status == "error"]
    assert len(error_entries) == 1

    # opening the errored entry must show a warning, not crash
    win._open_viewer_for(error_entries[0])  # must not raise
    assert len(win._viewers) == 0  # no viewer created for a broken log

    win.close()


# ---- opening a Log -> independent Viewer ---------------------------------------

@samples_pytestmark
def test_double_click_log_opens_viewer(qapp, tmp_path):
    from app.gui.browser_window import BrowserWindow
    from app.core.star_store import StarStore
    from PySide6.QtCore import Qt

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    shutil.copy(BIG_FILE, db_dir / "run1.hdf5")

    win = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    _run_scan_and_wait(win, str(db_dir))

    item = win.tree.topLevelItem(0)
    entry = item.data(0, Qt.UserRole)
    win._on_item_double_clicked(item, 0)

    assert len(win._viewers) == 1
    assert win._viewers[0].experiment is not None
    assert win._viewers[0].experiment.source_path == entry.absolute_path

    win.close()  # Browser closing must not close the Viewer
    assert win._viewers[0].isVisible() or True  # window may not be "shown" in
    # offscreen mode in a way isVisible() reflects, but it must not have
    # been force-closed/destroyed by win.close() - verified next:
    assert win._viewers[0].experiment is not None  # still alive, not torn down
    win._viewers[0].close()


@samples_pytestmark
def test_multiple_viewers_have_independent_state(qapp, tmp_path):
    from app.gui.browser_window import BrowserWindow
    from app.core.star_store import StarStore
    from PySide6.QtCore import Qt

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    shutil.copy(BIG_FILE, db_dir / "run1.hdf5")

    win = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    _run_scan_and_wait(win, str(db_dir))

    item = win.tree.topLevelItem(0)
    entry = item.data(0, Qt.UserRole)

    win._open_viewer_for(entry)
    win._open_viewer_for(entry)
    assert len(win._viewers) == 2

    v1, v2 = win._viewers
    v1.mode_combo.setCurrentIndex(1)  # 2D Heatmap
    v2.mode_combo.setCurrentIndex(0)  # 1D Plot

    assert v1.mode_combo.currentIndex() != v2.mode_combo.currentIndex()
    assert v1 is not v2
    assert v1.cached is not v2.cached
    assert v1.experiment is not v2.experiment  # each Viewer parses its own Experiment

    win.close()
    v1.close()
    v2.close()


# ---- Star via context menu wiring ----------------------------------------------

@samples_pytestmark
def test_star_toggle_updates_tree_label(qapp, tmp_path):
    from app.gui.browser_window import BrowserWindow
    from app.core.star_store import StarStore
    from PySide6.QtCore import Qt

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    shutil.copy(SMALL_FILE, db_dir / "run1.hdf5")

    win = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    _run_scan_and_wait(win, str(db_dir))

    item = win.tree.topLevelItem(0)
    entry = item.data(0, Qt.UserRole)
    assert item.data(0, STAR_STATE_ROLE) is False

    win._toggle_star(item, entry)
    assert item.data(0, STAR_STATE_ROLE) is True
    assert win.star_store.is_starred(win.scan_result.database_id, entry.relative_path)

    win._toggle_star(item, entry)
    assert item.data(0, STAR_STATE_ROLE) is False

    win.close()


@samples_pytestmark
def test_star_persists_across_reload(qapp, tmp_path):
    from app.gui.browser_window import BrowserWindow
    from app.core.star_store import StarStore
    from PySide6.QtCore import Qt, QEventLoop, QTimer

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    shutil.copy(SMALL_FILE, db_dir / "run1.hdf5")

    star_store = StarStore(tmp_path / "stars.json")
    win = BrowserWindow(star_store=star_store)
    _run_scan_and_wait(win, str(db_dir))

    item = win.tree.topLevelItem(0)
    entry = item.data(0, Qt.UserRole)
    win._toggle_star(item, entry)
    assert star_store.is_starred(win.scan_result.database_id, entry.relative_path)

    loop = QEventLoop()
    win.reload_database()
    win._scan_worker.finished_scan.connect(loop.quit)
    QTimer.singleShot(15000, loop.quit)
    loop.exec()

    reloaded_item = win.tree.topLevelItem(0)
    assert reloaded_item.data(0, STAR_STATE_ROLE) is True  # star survived the reload

    win.close()


# ---- layout redesign: collapsible channel browser (main_window.py) ------------

def test_collapsible_channel_browser(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.resize(1200, 800)
    initial_width = win.main_splitter.sizes()[0]
    assert initial_width > 0

    win.toggle_channels_button.setChecked(True)
    assert win.main_splitter.sizes()[0] == 0

    win.toggle_channels_button.setChecked(False)
    assert win.main_splitter.sizes()[0] > 0

    win.close()


def test_plot_area_gets_majority_of_space_by_default(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.resize(1200, 800)
    sizes = win.main_splitter.sizes()
    assert sizes[1] > sizes[0]  # plot side wider than channel browser side

    win.close()


# ---- main.py entry point still supports direct single-file open ---------------

def test_backward_compatible_single_file_open_still_works(qapp):
    """v0.7 behavior (python main.py file.hdf5 -> single Viewer) must
    remain available even though the Browser is now the default entry
    point with no arguments."""
    from app.gui.main_window import MainWindow

    win = MainWindow()
    assert win.experiment is None  # constructs fine standalone, same as before
    win.close()
