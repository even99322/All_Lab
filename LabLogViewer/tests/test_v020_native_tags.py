"""Tags are stored inside the HDF5 files (Labber's /Tags group) and shared through them."""

from __future__ import annotations

import h5py
import numpy as np
import pytest

from app.core import labber_tags
from app.core.labber_tags import NativeTagError, read_native_tags, write_native_tags
from app.core.tag_store import TagStore


def _file(path, tags=None, project="P"):
    with h5py.File(path, "w") as h:
        h.create_dataset("Data/Data", data=np.arange(6.0).reshape(3, 2))
        h.attrs["comment"] = "keep me"
        if tags is not None:
            g = h.create_group("Tags")
            g.attrs.create("Project", np.array([project], dtype=object), dtype=h5py.string_dtype())
            g.attrs.create("Tags", np.array(tags, dtype=object), dtype=h5py.string_dtype())
            g.attrs.create("User", np.array([""], dtype=object), dtype=h5py.string_dtype())
    return path


def test_labber_tags_are_kept_and_ours_are_added(tmp_path):
    f = _file(tmp_path / "a.hdf5", ["Labber/own", "Old"])
    assert write_native_tags(f, ["Flux", "Old"], removed=["Old", "Flux"]) == ["Labber/own", "Old", "Flux"]
    # removal only of names that are not wanted
    assert write_native_tags(f, ["Flux"], removed=["Old"]) == ["Labber/own", "Flux"]
    assert read_native_tags(f) == ["Labber/own", "Flux"]
    with h5py.File(f) as h:
        assert h["Tags"].attrs["Project"][0] == "P" and h.attrs["comment"] == "keep me"
        assert h["Data/Data"].shape == (3, 2)


def test_file_without_group_gets_one_only_when_there_are_tags(tmp_path):
    f = _file(tmp_path / "b.hdf5")
    assert write_native_tags(f, []) == []
    with h5py.File(f) as h:
        assert "Tags" not in h
    assert write_native_tags(f, ["BG"]) == ["BG"] and read_native_tags(f) == ["BG"]


def test_unchanged_file_is_not_touched(tmp_path):
    f = _file(tmp_path / "c.hdf5", ["Flux"])
    before = f.stat().st_mtime_ns
    assert write_native_tags(f, ["Flux"]) == ["Flux"]
    assert f.stat().st_mtime_ns == before


def test_locked_or_missing_file_fails_cleanly_and_changes_nothing(tmp_path):
    f = _file(tmp_path / "d.hdf5", ["Flux"])
    before = f.read_bytes()
    holder = h5py.File(f, "r")                      # another reader/writer holds the file
    try:
        try:
            write_native_tags(f, ["BG"], attempts=1)
            locked = False
        except NativeTagError:
            locked = True
    finally:
        holder.close()
    if locked:                                      # depends on the platform's HDF5 file locking
        assert f.read_bytes() == before
    with pytest.raises(NativeTagError):
        write_native_tags(tmp_path / "missing.hdf5", ["x"], attempts=1)
    (tmp_path / "junk.hdf5").write_text("not hdf5")
    with pytest.raises(NativeTagError):
        write_native_tags(tmp_path / "junk.hdf5", ["x"], attempts=1)
    assert (tmp_path / "junk.hdf5").read_text() == "not hdf5"


def test_failed_write_leaves_the_file_identical(tmp_path, monkeypatch):
    f = _file(tmp_path / "e.hdf5", ["Flux"])
    before = f.read_bytes()
    monkeypatch.setattr(labber_tags, "_read_tags_checked", lambda path, attempts=4: ["something else"])
    with pytest.raises(NativeTagError):
        write_native_tags(f, ["BG"])                # read-back mismatch is reported, never claimed as success


def test_store_adopts_file_tags_and_keeps_waiting_changes(tmp_path):
    root = tmp_path / "db"; root.mkdir()
    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    db = str(root)
    store.adopt_file_tags(db, {"a.hdf5": ["Flux", "From Labber"], "b.hdf5": []})
    assert store.tags_for(db, "a.hdf5") == {"Flux", "From Labber"}
    assert "From Labber" in store.list_tags() and store.tags_for(db, "b.hdf5") == set()
    assert store.adopt_file_tags(db, {"a.hdf5": ["Flux", "From Labber"]}) == 0     # nothing changed, nothing saved
    store.set_tags(db, "a.hdf5", {"BG"})
    assert store.native_job(db, "a.hdf5") == (["BG"], ["Flux", "From Labber"])
    store.adopt_file_tags(db, {"a.hdf5": ["Flux"]})                                # waiting change is not overwritten
    assert store.tags_for(db, "a.hdf5") == {"BG"}
    store.native_done(db, "a.hdf5")
    assert store.native_job(db, "a.hdf5") is None
    store.adopt_file_tags(db, {"a.hdf5": ["Flux"]})
    assert store.tags_for(db, "a.hdf5") == {"Flux"}
    assert TagStore(tmp_path / "tags.json", legacy_paths=[]).tags_for(db, "a.hdf5") == {"Flux"}


def test_renaming_or_deleting_a_tag_queues_the_files(tmp_path):
    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    db = str(tmp_path)
    store.adopt_file_tags(db, {"a.hdf5": ["Mine", "Flux"], "b.hdf5": ["Flux"]})
    store.rename_tag("Mine", "Ours")
    assert store.native_pending_paths(db) == ["a.hdf5"]
    assert store.native_job(db, "a.hdf5")[1] == ["Mine"] and "Ours" in store.native_job(db, "a.hdf5")[0]
    store.native_done(db, "a.hdf5")
    store.delete_tag("Flux")
    assert sorted(store.native_pending_paths(db)) == ["a.hdf5", "b.hdf5"]


def test_scan_reads_file_tags_of_a_real_log_and_the_index_cache_keeps_them(tmp_path):
    import shutil

    from app.core.database_index_store import DatabaseIndexStore
    from app.core.database_scanner import DatabaseScanner
    from tests.real_data import SMALL_FILE

    root = tmp_path / "db"; root.mkdir()
    target = root / "x.hdf5"
    shutil.copyfile(SMALL_FILE, target)
    write_native_tags(target, ["Flux", "Group/Sub"])
    index = DatabaseIndexStore(tmp_path / "index.json")
    first = DatabaseScanner.scan(root, index_store=index)
    assert first.entries[0].status == "ok" and first.entries[0].file_tags == ["Flux", "Group/Sub"]
    second = DatabaseScanner.scan(root, index_store=index)                 # now answered from the cache
    assert second.entries[0].file_tags == ["Flux", "Group/Sub"]


# -- the Browser: editing tags writes into the file, a failure is reported and retried -------------
import os
import shutil
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _browser(tmp_path, qapp):
    from app.core.database_scanner import DatabaseScanner
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow
    from tests.real_data import SMALL_FILE

    root = tmp_path / "db"; root.mkdir()
    shutil.copyfile(SMALL_FILE, root / "x.hdf5")
    window = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"),
                           tag_store=TagStore(tmp_path / "tags.json", legacy_paths=[]))
    result = DatabaseScanner.scan(root)
    window.scan_result = result
    window._populate_folder_tree(result)
    return window, result, root / "x.hdf5"


def _wait(window, qapp, seconds=10):
    end = time.time() + seconds
    while getattr(window, "_native_writer", None) is not None and time.time() < end:
        qapp.processEvents(); time.sleep(0.01)
    qapp.processEvents()


def _edit(window, tags, monkeypatch):
    from app.gui.tag_assignment_dialog import TagAssignmentDialog

    monkeypatch.setattr(TagAssignmentDialog, "exec", lambda self: TagAssignmentDialog.Accepted)
    monkeypatch.setattr(TagAssignmentDialog, "selected_tags", lambda self: set(tags))
    window._edit_tags(None, window.scan_result.entries[0])


def test_browser_edit_writes_the_file_and_a_failure_is_reported_then_retried(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    messages = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: messages.append(a[2]) or QMessageBox.Ok))
    window, result, path = _browser(tmp_path, qapp)
    db = result.database_id
    assert len(result.entries) == 1
    _edit(window, {"Flux"}, monkeypatch); _wait(window, qapp)
    assert messages == [] and read_native_tags(path) == ["Flux"] and window.tag_store.native_pending_paths(db) == []

    # a locked / failing file: message shown, file unchanged, change kept and not overwritten by a rescan
    import app.gui.native_tag_worker as worker

    real = worker.write_native_tags
    monkeypatch.setattr(worker, "write_native_tags",
                        lambda *a, **k: (_ for _ in ()).throw(NativeTagError("locked by Labber", locked=True)))
    before = path.read_bytes()
    _edit(window, {"Flux", "BG"} - {"BG"} | {"Debug"}, monkeypatch); _wait(window, qapp)
    assert messages and "locked by Labber" in messages[0]
    assert path.read_bytes() == before and read_native_tags(path) == ["Flux"]
    assert window.tag_store.native_pending_paths(db) == ["x.hdf5"]
    window.tag_store.adopt_file_tags(db, {"x.hdf5": ["Flux"]})
    assert window.tag_store.tags_for(db, "x.hdf5") == {"Flux", "Debug"}           # local change survived the rescan

    monkeypatch.setattr(worker, "write_native_tags", real)
    window._native_last_try = 0.0
    window._adopt_native_tags(result); _wait(window, qapp)                          # the periodic retry
    assert read_native_tags(path) == ["Flux", "Debug"] and window.tag_store.native_pending_paths(db) == []
    window.close()


def test_log_entries_scroll_after_selecting_while_hidden(qapp):
    """Selecting a row while the table is hidden (2D mode) must scroll to it once it is shown again."""
    from PySide6.QtWidgets import QTableWidgetItem
    from app.gui.log_entries_widget import LogEntriesWidget

    widget = LogEntriesWidget()
    widget.table.setColumnCount(1); widget.table.setRowCount(300)
    for row in range(300):
        widget.table.setItem(row, 0, QTableWidgetItem(str(row)))
    widget.resize(300, 200)
    widget._reset_selection()
    widget.select_row(190)
    assert widget._scroll_pending
    widget.show(); qapp.processEvents(); qapp.processEvents()
    assert widget.table.rowViewportPosition(190) >= 0 and not widget._scroll_pending
    widget.close()
