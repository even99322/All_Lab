"""v0.19F: the opened database refreshes itself (default every 2 s).

Works on copies of real measurements in tmp_path; the real data is only read."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from tests.real_data import BIG_FILE, SMALL_FILE


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_compare_only_reports_what_changed(tmp_path):
    from app.core import change_watch as cw

    (tmp_path / "A").mkdir()
    (tmp_path / "B").mkdir()
    (tmp_path / "A" / "one.hdf5").write_bytes(b"1" * 10)
    (tmp_path / "B" / "two.h5").write_bytes(b"2" * 10)
    (tmp_path / "A" / "notes.txt").write_text("not a measurement")
    known = cw.full_snapshot(tmp_path)
    assert set(known) == {"A/one.hdf5", "B/two.h5"}
    assert not cw.compare(known, cw.full_snapshot(tmp_path))
    (tmp_path / "A" / "one.hdf5").write_bytes(b"1" * 20)
    (tmp_path / "A" / "three.hdf5").write_bytes(b"3")
    (tmp_path / "B" / "two.h5").unlink()
    only_a = cw.folder_snapshot(tmp_path, {("A",)})
    changes = cw.compare(known, only_a, {("A",)})
    assert changes.added == ["A/three.hdf5"] and changes.changed == ["A/one.hdf5"]
    assert changes.removed == []                       # folder B was not looked at
    assert cw.compare(known, cw.full_snapshot(tmp_path)).removed == ["B/two.h5"]
    assert cw.hot_folders(cw.full_snapshot(tmp_path), ("B",), count=1) == {("A",), ("B",)}


def test_settings_interval(tmp_path):
    from app.settings.store import SettingsStore

    store = SettingsStore(tmp_path / "settings.json")
    assert store.auto_refresh_seconds() == 2
    store.set_auto_refresh_seconds(0)
    assert SettingsStore(tmp_path / "settings.json").auto_refresh_seconds() == 0
    for bad in (3, -1, True, "2"):
        with pytest.raises(ValueError):
            store.set_auto_refresh_seconds(bad)


def _browser(qapp, root: Path):
    from app.gui.browser_window import BrowserWindow

    browser = BrowserWindow()
    browser.auto_refresher.timer.stop()               # the test ticks by hand
    browser.show()
    browser.open_database(str(root))
    end = time.time() + 60
    while (browser.scan_result is None or browser._scan_worker.isRunning()
           or (browser._enrichment_worker is not None and browser._enrichment_worker.isRunning())) \
            and time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)
    for _ in range(20):                                # deliver the scan / enrichment results still queued
        qapp.processEvents()
        time.sleep(0.02)
    return browser


def _paths(browser) -> set[str]:
    return {entry.relative_path for entry in browser.scan_result.entries}


@pytest.mark.skipif(not SMALL_FILE.exists(), reason="Real measurement fixture unavailable")
def test_browser_picks_up_new_changed_removed_and_new_folders(qapp, tmp_path):
    root = tmp_path / "db"
    (root / "Data_0928").mkdir(parents=True)
    shutil.copy2(SMALL_FILE, root / "Data_0928" / "a.hdf5")
    shutil.copy2(SMALL_FILE, root / "Data_0928" / "b.hdf5")
    browser = _browser(qapp, root)
    assert _paths(browser) == {"Data_0928/a.hdf5", "Data_0928/b.hdf5"}
    folder = browser._folder_items[("Data_0928",)]
    browser.folder_tree.setCurrentItem(folder)
    browser._populate_data_list(("Data_0928",))
    browser.data_list.setCurrentItem(browser.data_list.topLevelItem(1))
    selected = browser._selected_data()[1].relative_path
    # a new measurement appears in the folder on screen
    shutil.copy2(SMALL_FILE, root / "Data_0928" / "c.hdf5")
    assert browser.auto_refresher.tick(wait=True)
    qapp.processEvents()
    assert "Data_0928/c.hdf5" in _paths(browser)
    assert browser.data_list.topLevelItemCount() == 3
    assert browser._selected_data()[1].relative_path == selected          # selection kept
    entry = next(e for e in browser.scan_result.entries if e.relative_path == "Data_0928/c.hdf5")
    assert entry.status == "ok" and entry.metadata_complete
    # a new folder elsewhere is found by the whole-database walk; the tree gets it
    (root / "Data_0929").mkdir()
    shutil.copy2(SMALL_FILE, root / "Data_0929" / "d.hdf5")
    browser.auto_refresher.tick(wait=True, force_full=True)
    qapp.processEvents()
    assert ("Data_0929",) in browser._folder_items and "Data_0929/d.hdf5" in _paths(browser)
    assert browser.folder_tree.currentItem() is browser._folder_items[("Data_0928",)]
    # removed
    (root / "Data_0928" / "a.hdf5").unlink()
    browser.auto_refresher.tick(wait=True, force_full=True)
    qapp.processEvents()
    assert "Data_0928/a.hdf5" not in _paths(browser)
    # nothing changed: nothing happens
    before = list(browser.scan_result.entries)
    browser.auto_refresher.tick(wait=True, force_full=True)
    qapp.processEvents()
    assert browser.scan_result.entries == before
    browser.close()


@pytest.mark.skipif(not SMALL_FILE.exists(), reason="Real measurement fixture unavailable")
def test_a_file_still_being_written_is_retried_not_marked_unreadable(qapp, tmp_path):
    root = tmp_path / "db"
    root.mkdir()
    shutil.copy2(SMALL_FILE, root / "done.hdf5")
    browser = _browser(qapp, root)
    writing = root / "writing.hdf5"
    writing.write_bytes(SMALL_FILE.read_bytes()[:4096])                    # Labber has just started
    browser.auto_refresher.tick(wait=True, force_full=True)
    qapp.processEvents()
    entry = next(e for e in browser.scan_result.entries if e.relative_path == "writing.hdf5")
    assert entry.status == "ok" and not entry.metadata_complete and entry.mtime == -1.0
    shutil.copy2(SMALL_FILE, writing)                                      # the measurement finished
    browser.auto_refresher.tick(wait=True, force_full=True)
    qapp.processEvents()
    entry = next(e for e in browser.scan_result.entries if e.relative_path == "writing.hdf5")
    assert entry.metadata_complete and entry.mtime > 0
    # an old unreadable file is reported as unreadable, as before
    broken = root / "broken.hdf5"
    broken.write_bytes(b"not hdf5")
    old = time.time() - 3600
    os.utime(broken, (old, old))
    browser.auto_refresher.tick(wait=True, force_full=True)
    qapp.processEvents()
    assert next(e for e in browser.scan_result.entries if e.relative_path == "broken.hdf5").status == "error"
    browser.close()


@pytest.mark.skipif(not (SMALL_FILE.exists() and BIG_FILE.exists()), reason="Real measurement fixtures unavailable")
def test_an_open_viewer_follows_a_growing_measurement_and_keeps_its_view(qapp, tmp_path):
    from app.gui.main_window import MainWindow

    root = tmp_path / "db"
    root.mkdir()
    live = root / "live.hdf5"
    shutil.copy2(SMALL_FILE, live)
    browser = _browser(qapp, root)
    viewer = MainWindow()
    viewer.show()
    viewer.open_file(str(live))
    qapp.processEvents()
    viewer.mode_combo.setCurrentIndex(1 if viewer.mode_combo.count() > 1 else 0)
    mode = viewer.mode_combo.currentIndex()
    entries_before = viewer.experiment.n_entries if hasattr(viewer.experiment, "n_entries") else None
    shutil.copy2(BIG_FILE, live)                                           # "more traces were written"
    browser.auto_refresher._viewer_reloaded.clear()
    browser.auto_refresher.tick(wait=True, force_full=True)
    qapp.processEvents()
    assert str(viewer.experiment.source_path) == str(live)
    if entries_before is not None:
        assert viewer.experiment.n_entries != entries_before
    assert viewer.mode_combo.currentIndex() == mode                        # the view was kept
    viewer.close()
    browser.close()


def test_refresh_is_skipped_while_busy(qapp, tmp_path):
    from app.gui.browser_window import BrowserWindow

    browser = BrowserWindow()
    browser.auto_refresher.timer.stop()
    browser.show()
    assert browser.auto_refresher.tick(wait=True) is False                 # no database opened yet
    browser.close()


@pytest.mark.skipif(not SMALL_FILE.exists(), reason="Real measurement fixture unavailable")
def test_new_folder_from_the_browser(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    root = tmp_path / "db"
    (root / "Data_0928").mkdir(parents=True)
    (root / "empty_from_before").mkdir()
    shutil.copy2(SMALL_FILE, root / "Data_0928" / "a.hdf5")
    browser = _browser(qapp, root)
    assert ("empty_from_before",) in browser._folder_items                    # empty folders show too
    assert browser.new_folder_action.isEnabled()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))
    made = browser.new_folder(("Data_0928",), "calibration")
    assert made == root / "Data_0928" / "calibration" and made.is_dir()
    assert browser.folder_tree.currentItem() is browser._folder_items[("Data_0928", "calibration")]
    for bad in ("", "a/b", ".hidden", "ends with a dot.", "calibration"):
        assert browser.new_folder(("Data_0928",), bad) is None
    assert len(warnings) == 5 and "already exists" in warnings[-1]
    assert browser.new_folder((), "Run 2026") == root / "Run 2026"
    # created by someone else: found by the whole-database walk
    (root / "made_elsewhere").mkdir()
    browser.auto_refresher.tick(wait=True, force_full=True)
    qapp.processEvents()
    assert ("made_elsewhere",) in browser._folder_items
    assert sorted(p.name for p in (root / "Data_0928").iterdir()) == ["a.hdf5", "calibration"]   # data untouched
    browser.close()
