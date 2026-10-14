"""v1.0.2: opening a big database on the NAS over VPN — nothing on the window's thread waits for the server.

* the folder tree comes from the scan's own listing (the Browser used to walk the whole share again);
* network folders are listed several at a time; scan progress is throttled;
* file identities (resolved paths) on a network drive are remembered;
* the Comment of a network file is read in the background; the preview key uses the listing's size / time;
* auto refresh takes sizes / times from the folder listing and walks a network database less often;
* Automatic Tags for thousands of files: group ids worked out once.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.test_nd_slice_synthetic import _build_synthetic_labber_file


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def net(tmp_path, monkeypatch):
    from app.core import data_identity, net_cache

    monkeypatch.setenv("LABLOGVIEWER_NET_CACHE", "force")
    monkeypatch.setenv("LABLOGVIEWER_NET_CACHE_DIR", str(tmp_path / "cache"))
    data_identity._NETWORK_IDENTITIES.clear()
    data_identity._NETWORK_FOLDERS.clear()
    yield net_cache
    data_identity._NETWORK_IDENTITIES.clear()
    data_identity._NETWORK_FOLDERS.clear()


def _tree(root: Path, days: int = 4, per: int = 3) -> Path:
    tpl = root.parent / "tpl.hdf5"
    _build_synthetic_labber_file(tpl, [("Current", "A", np.linspace(0, 1, 3))], np.linspace(1e9, 2e9, 5))
    data = tpl.read_bytes()
    for d in range(days):
        folder = root / "2026" / "10" / f"Data_10{d + 10:02d}"
        folder.mkdir(parents=True)
        for k in range(per):
            (folder / f"m{k}.hdf5").write_bytes(data)
    (root / "2026" / "11" / "empty").mkdir(parents=True)
    (root / ".hidden").mkdir()
    return root


def _pump(app, seconds: float, until=None) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return
        time.sleep(0.01)


def test_scan_reports_folders_and_network_listing_matches(tmp_path, monkeypatch):
    from app.core import net_cache
    from app.core.database_scanner import DatabaseScanner

    root = _tree(tmp_path / "db")
    local = DatabaseScanner.scan(root, fast_listing=True)
    assert ("2026", "11", "empty") in local.folders and ("2026", "10", "Data_1010") in local.folders
    assert not any(part.startswith(".") for parts in local.folders for part in parts)
    monkeypatch.setenv("LABLOGVIEWER_NET_CACHE", "force")
    monkeypatch.setenv("LABLOGVIEWER_NET_CACHE_DIR", str(tmp_path / "cache"))
    assert net_cache.is_network_path(root)
    folders: set = set()
    remote = DatabaseScanner.find_hdf5_files_with_stats(root, None, folders)
    assert [p for p, _ in remote] == [p for p, _ in DatabaseScanner.find_hdf5_files_with_stats(root)]
    assert all(stat is not None for _, stat in remote) and len(remote) == 12
    assert folders == local.folders


def test_browser_does_not_walk_the_share_again(qapp, tmp_path, monkeypatch, net):
    from app.core import change_watch
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    root = _tree(tmp_path / "db")

    def no_walk(_root):
        raise AssertionError("folders_on_disk must not run on the window's thread")

    monkeypatch.setattr(change_watch, "folders_on_disk", no_walk)
    browser = BrowserWindow(star_store=StarStore(tmp_path / "state" / "stars.json"))
    browser.open_database(str(root))
    _pump(qapp, 20, until=lambda: browser.scan_result is not None)
    assert browser.scan_result is not None and ("2026", "11", "empty") in browser._folder_items
    browser._stop_enrichment()
    browser.close()


def test_scan_progress_is_throttled(qapp, tmp_path):
    from app.gui.database_scan_worker import DatabaseScanWorker

    worker = DatabaseScanWorker(str(tmp_path))
    seen = []
    worker.progress.connect(lambda done, total: seen.append(done))
    worker._last_progress = 0.0
    for done in range(1, 2001):
        worker._progress(done, 2000)
    qapp.processEvents()
    assert seen[-1] == 2000 and len(seen) < 20


def test_network_identity_is_remembered(tmp_path, monkeypatch, net):
    from app.core import data_identity

    path = tmp_path / "x.hdf5"
    path.write_bytes(b"x")
    first = data_identity.stable_data_identity(path)
    calls = []
    real = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **k: calls.append(self) or real(self, *a, **k))
    assert data_identity.stable_data_identity(path) == first and not calls
    monkeypatch.setenv("LABLOGVIEWER_NET_CACHE", "0")                  # local drive: resolved every time
    data_identity._NETWORK_IDENTITIES.clear()
    assert data_identity.stable_data_identity(path) == first and calls


def test_network_comment_is_read_in_the_background(qapp, tmp_path, monkeypatch, net):
    import threading

    from app.core.star_store import StarStore
    from app.gui import browser_window as bw
    from app.gui.browser_window import BrowserWindow

    root = _tree(tmp_path / "db", days=1, per=2)
    gate = threading.Event()
    threads = []
    real = bw.read_native_comment

    def slow(path):
        threads.append(threading.current_thread() is threading.main_thread())
        gate.wait(5)
        return real(path)

    monkeypatch.setattr(bw, "read_native_comment", slow)
    browser = BrowserWindow(star_store=StarStore(tmp_path / "state" / "stars.json"))
    browser.open_database(str(root))
    _pump(qapp, 20, until=lambda: browser.scan_result is not None)
    browser._stop_enrichment()
    folder = browser._folder_items[("2026", "10", "Data_1010")]
    browser.folder_tree.setCurrentItem(folder)
    _pump(qapp, 0.2)
    entry = browser._selected_data()[1]
    assert entry is not None
    assert threads and not any(threads)                                # never on the window's thread
    assert not browser.comment_editor.isEnabled()                      # reading …
    gate.set()
    _pump(qapp, 5, until=browser.comment_editor.isEnabled)
    assert browser.comment_editor.isEnabled()
    # a late answer for another file is ignored
    browser._native_comment_arrived((browser._comment_token - 1, entry.absolute_path, None))
    # the preview key comes from the listing (no request to the server)
    key = browser._preview_cache_key(entry, None)
    assert key == (entry.absolute_path, entry.mtime, entry.size_bytes, "None")
    browser.close()


def test_change_watch_uses_the_listing(tmp_path):
    from app.core import change_watch

    root = _tree(tmp_path / "db", days=2, per=2)
    (root / "2026" / "10" / "Data_1010" / "notes.txt").write_text("x")
    (root / ".hidden" / "h.hdf5").write_bytes(b"h")
    folders: set = set()
    snap = change_watch.full_snapshot(root, folders)
    assert sorted(snap) == sorted(f"2026/10/Data_10{d}/m{k}.hdf5" for d in (10, 11) for k in (0, 1))
    stat = (root / "2026/10/Data_1010/m0.hdf5").stat()
    assert snap["2026/10/Data_1010/m0.hdf5"] == (stat.st_size, stat.st_mtime)
    assert ("2026", "11", "empty") in folders and (".hidden",) not in folders
    hot = change_watch.folder_snapshot(root, {("2026", "10", "Data_1011")})
    assert sorted(hot) == ["2026/10/Data_1011/m0.hdf5", "2026/10/Data_1011/m1.hdf5"]


def test_auto_refresh_walks_a_network_database_less_often():
    from app.gui import auto_refresh

    assert auto_refresh.FULL_EVERY_NETWORK >= 10 * auto_refresh.FULL_EVERY


def test_folder_parts_follow_a_rename():
    from app.core.database_scanner import LogEntry

    entry = LogEntry("/x/a/b/f.hdf5", "a/b/f.hdf5", "f.hdf5", "f", "ok", None, 1, 1.0)
    assert entry.folder_parts == ("a", "b")
    entry.relative_path = "c/f.hdf5"
    assert entry.folder_parts == ("c",)


def test_automatic_tags_for_many_files_are_quick(tmp_path):
    from app.core.database_scanner import LogEntry
    from app.core.tag_store import TagStore

    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    db = str(tmp_path / "db")
    entries = [LogEntry(f"{db}/2026/Data_{g:04d}/m{k}.hdf5", f"2026/Data_{g:04d}/m{k}.hdf5", f"m{k}.hdf5",
                        f"m{k}", "ok", None, 1, 1.0) for g in range(600) for k in range(15)]
    start = time.perf_counter()
    store.initialize_entries(db, entries)
    assert time.perf_counter() - start < 5
