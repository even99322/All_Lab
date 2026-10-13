"""v2.1.6: data on the NAS over VPN (local copies), big folders (rows added while scrolling, metadata of the
folder on screen first, filled in place), and the scan without one network request per file."""

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
    """Every folder counts as a network drive; the cache lives in tmp_path."""
    from app.core import net_cache

    monkeypatch.setenv("LABLOGVIEWER_NET_CACHE", "force")
    monkeypatch.setenv("LABLOGVIEWER_NET_CACHE_DIR", str(tmp_path / "cache"))
    return net_cache


def _labber(path: Path, n_entries: int = 3) -> Path:
    _build_synthetic_labber_file(path, [("Current", "A", np.linspace(0, 1, n_entries))], np.linspace(1e9, 2e9, 7))
    return path


# ---- local copies --------------------------------------------------------------------------------
def test_copy_is_used_while_identical(tmp_path, net):
    src = tmp_path / "share" / "a.hdf5"
    src.parent.mkdir()
    src.write_bytes(b"x" * 100_000)
    assert net.needs_copy(src)
    seen = []
    local = net.ensure_local(src, progress=lambda d, t: seen.append((d, t)))
    assert local != src and local.read_bytes() == src.read_bytes() and seen[-1] == (100_000, 100_000)
    assert net.read_path(src) == local and not net.needs_copy(src)
    assert net.ensure_local(src) == local                              # second time: no copy
    src.write_bytes(b"y" * 100_001)                                    # the original changed (Star, Tags …)
    assert net.read_path(src) == src and net.needs_copy(src)
    assert net.usage()[0] == 1


def test_too_big_cancelled_or_off(tmp_path, net, monkeypatch):
    src = tmp_path / "b.hdf5"
    src.write_bytes(b"z" * (3 << 20))
    assert net.ensure_local(src, max_bytes=1 << 20) == src             # too big: read directly
    with pytest.raises(net.Cancelled):
        net.ensure_local(src, cancel=lambda: True)
    assert net.fresh_copy(src) is None and not list((tmp_path / "cache").glob(".*.part"))
    monkeypatch.setenv("LABLOGVIEWER_NET_CACHE", "0")
    assert net.ensure_local(src) == src and net.read_path(src) == src


def test_growing_file_is_not_cached(tmp_path, net, monkeypatch):
    src = tmp_path / "growing.hdf5"
    src.write_bytes(b"g" * (net.CHUNK + 10))
    calls = {"n": 0}
    real = net._stamp

    def stamp(path):                                                   # the measurement writes meanwhile
        calls["n"] += 1
        size, mtime = real(path)
        return (size, mtime + calls["n"]) if calls["n"] > 1 else (size, mtime)

    monkeypatch.setattr(net, "_stamp", stamp)
    assert net.ensure_local(src) == src
    monkeypatch.setattr(net, "_stamp", real)
    assert net.fresh_copy(src) is None


def test_eviction_keeps_the_limit(tmp_path, net, monkeypatch):
    monkeypatch.setenv("LABLOGVIEWER_NET_CACHE_GB", str(250_000 / (1 << 30)))
    files = []
    for k in range(4):
        f = tmp_path / f"f{k}.hdf5"
        f.write_bytes(bytes([k]) * 100_000)
        files.append(f)
        net.ensure_local(f)
        time.sleep(0.02)
    count, size = net.usage()
    assert size <= 250_000 and count == 2
    assert net.fresh_copy(files[-1]) is not None and net.fresh_copy(files[0]) is None


def test_reader_and_viewer_read_the_copy(qapp, tmp_path, net):
    from app.core.hdf5_reader import HDF5Reader

    src = _labber(tmp_path / "trace.hdf5")
    reader = HDF5Reader(src)
    assert reader.read_path == src                                     # not copied yet
    reader.close()
    from app.gui.net_fetch import fetch

    assert fetch(None, str(src)) is True
    reader = HDF5Reader(src)
    assert reader.read_path != src and reader.path == src              # identity stays the original
    reader.close()


def test_preview_worker_copies_then_previews(qapp, tmp_path, net):
    from app.gui.quick_preview_worker import QuickPreviewWorker

    src = _labber(tmp_path / "p.hdf5")
    got = []
    worker = QuickPreviewWorker(str(src))
    worker.preview_ready.connect(lambda w, p: got.append(p))
    worker.preview_failed.connect(lambda w, m: got.append(m))
    worker.start()
    deadline = time.monotonic() + 20
    while worker.isRunning() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert got and not isinstance(got[0], str), got
    assert net.fresh_copy(src) is not None


def test_busy_pauses_background_work(net):
    import threading

    waited = {}

    def background():
        start = time.monotonic()
        net.wait_idle()
        waited["s"] = time.monotonic() - start

    with net.busy():
        t = threading.Thread(target=background)
        t.start()
        time.sleep(0.4)
    t.join(5)
    assert waited["s"] >= 0.3


# ---- scanning ----------------------------------------------------------------------------------------
def test_scan_lists_without_opening_and_parses_the_folder_on_screen_first(tmp_path, net):
    from app.core.database_index_store import DatabaseIndexStore
    from app.core.database_scanner import DatabaseScanner

    root = tmp_path / "db"
    for folder in ("a", "b", "c"):
        (root / folder).mkdir(parents=True)
        for k in range(3):
            _labber(root / folder / f"{folder}{k}.hdf5")
    listed = DatabaseScanner.find_hdf5_files_with_stats(root)
    assert len(listed) == 9 and all(stat is not None for _p, stat in listed)

    fast = DatabaseScanner.scan(root, fast_listing=True)               # network: nothing opened
    assert all(not e.metadata_complete and e.status == "ok" for e in fast.entries)

    order = []
    store = DatabaseIndexStore(tmp_path / "index.json")
    full = DatabaseScanner.scan(root, index_store=store, priority=lambda: ("c",),
                                batch_callback=lambda batch: order.extend(e.relative_path for e in batch))
    assert [p.split("/")[0] for p in order[:3]] == ["c", "c", "c"]
    assert sorted(order) == sorted(e.relative_path for e in full.entries)
    assert all(e.metadata_complete and e.sweep_dimension for e in full.entries)
    store.merge_root("x", {"r.h5": {"size_bytes": 1, "mtime": 2.0}})
    assert store.get("x", "r.h5", 1, 2.0) is not None
    assert store.get(full.database_id, "a/a0.hdf5", full.entries[0].size_bytes, full.entries[0].mtime)


# ---- big folders in the Browser ---------------------------------------------------------------------
def _fake_result(root: Path, n: int):
    from app.core.database_scanner import DatabaseScanResult, LogEntry

    entries = [LogEntry(absolute_path=str(root / f"m{k:04d}.hdf5"), relative_path=f"m{k:04d}.hdf5",
                        file_name=f"m{k:04d}.hdf5", log_name=f"m{k:04d}", status="ok", error_message=None,
                        size_bytes=10, mtime=1.0, metadata_complete=False) for k in range(n)]
    return DatabaseScanResult(root_path=str(root), database_id=str(root), entries=entries)


def test_big_folder_rows_fill_while_scrolling_and_metadata_in_place(qapp, tmp_path):
    from app.core.database_scanner import LogEntry
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    browser = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    browser.resize(1000, 700)
    browser.show()
    result = _fake_result(tmp_path, 250)
    browser.scan_result = result
    browser._populate_folder_tree(result)
    qapp.processEvents()
    assert browser.data_list.topLevelItemCount() == 100
    bar = browser.data_list.verticalScrollBar()
    bar.setValue(bar.maximum())
    qapp.processEvents()
    assert browser.data_list.topLevelItemCount() == 200
    item = browser._find_row("m0240.hdf5")                             # selecting a row not shown yet
    assert item is not None and item.text(1) == "m0240"
    assert browser.data_list.topLevelItemCount() == 250
    # metadata arrives for a few files: rows change in place (no rebuild, the row objects stay)
    done = [LogEntry(absolute_path=e.absolute_path, relative_path=e.relative_path, file_name=e.file_name,
                     log_name="named " + e.log_name, status="ok", error_message=None, size_bytes=10, mtime=1.0,
                     creation_time=None, sweep_dimension="2D · 7 × 3", file_tags=[], file_star=False)
            for e in result.entries[:3]]
    first = browser.data_list.topLevelItem(0)
    browser._apply_metadata_batch(done)
    assert browser.data_list.topLevelItem(0) is first
    assert first.text(1) == "named m0000" and first.text(3) == "2D · 7 × 3"
    assert result.entries[0].metadata_complete
    browser.close()


def test_small_folder_shows_everything(qapp, tmp_path):
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    browser = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    result = _fake_result(tmp_path, 40)
    browser.scan_result = result
    browser._populate_folder_tree(result)
    assert browser.data_list.topLevelItemCount() == 40
    assert browser._select_relative_path("m0039.hdf5")
    browser.close()
