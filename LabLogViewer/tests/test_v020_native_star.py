"""The star lives inside the HDF5 file (root attribute 'star', an enum bool like Labber's); file lock; shared queue."""
import json
import os
import shutil
import time

import h5py
import numpy as np
import pytest

from app.core.data_rename import DataRenameError, rename_hdf5_data
from app.core.file_lock import FileBusyError, file_lock, lock_path
from app.core.labber_star import read_native_star, write_native_star
from app.core.labber_tags import NativeTagError, write_native_tags
from app.core.star_store import StarStore


def make(path, star=None):
    with h5py.File(path, "w") as f:
        f.create_group("Data")
        if star is not None:
            f.attrs.create("star", np.bool_(star), dtype=np.bool_)
    return path


def test_star_true_stays_true_and_false_stays_false(tmp_path):
    t, f = make(tmp_path / "t.hdf5", True), make(tmp_path / "f.hdf5", False)
    m1, m2 = t.stat().st_mtime_ns, f.stat().st_mtime_ns
    assert write_native_star(t, True) is True and write_native_star(f, False) is False
    assert t.stat().st_mtime_ns == m1 and f.stat().st_mtime_ns == m2     # nothing needed writing: not even opened r+


def test_unstar_then_restar_and_enum_type(tmp_path):
    p = make(tmp_path / "a.hdf5", True)
    assert write_native_star(p, False) is False and read_native_star(p) is False
    assert write_native_star(p, True) is True and read_native_star(p) is True
    with h5py.File(p, "r") as h:
        dt = h["/"].attrs.get_id("star").dtype
        assert dt.kind == "b" and dt.itemsize == 1


def test_missing_attribute_rules(tmp_path):
    p = make(tmp_path / "n.hdf5")
    assert write_native_star(p, False) is None and read_native_star(p) is None
    assert write_native_star(p, True) is True


def test_lock_blocks_second_writer_and_is_removed(tmp_path):
    p = make(tmp_path / "a.hdf5", False)
    with file_lock(p):
        assert lock_path(p).exists()
        with pytest.raises(FileBusyError):
            with file_lock(p, wait=0.3):
                pass
        with pytest.raises(NativeTagError) as error:
            write_native_star(p, True)
        assert error.value.locked
    assert not lock_path(p).exists()
    assert read_native_star(p) is False                                   # untouched while busy


def test_stale_lock_is_taken_over(tmp_path):
    p = make(tmp_path / "a.hdf5", False)
    lock_path(p).write_text(json.dumps({"host": "other"}))
    old = time.time() - 600
    os.utime(lock_path(p), (old, old))
    assert write_native_star(p, True) is True
    assert not lock_path(p).exists()


def test_tags_respect_lock(tmp_path):
    p = make(tmp_path / "a.hdf5", True)
    with file_lock(p):
        with pytest.raises(NativeTagError):
            write_native_tags(p, ["x"], attempts=1)
    assert write_native_tags(p, ["x"]) == ["x"]


def test_rename_refuses_when_locked_and_cleans_up(tmp_path):
    p = make(tmp_path / "a.hdf5", True)
    with file_lock(p):
        with pytest.raises(DataRenameError, match="another device"):
            rename_hdf5_data(p, "b", database_id=str(tmp_path), old_relative_path="a.hdf5", state_stores=[])
    assert p.exists()
    new = rename_hdf5_data(p, "b", database_id=str(tmp_path), old_relative_path="a.hdf5", state_stores=[])
    assert new.exists() and not p.exists() and not lock_path(p).exists() and read_native_star(new) is True


def test_pending_queue_persists_and_adopts(tmp_path):
    path = tmp_path / "stars.json"
    s = StarStore(path)
    s.queue_native("db", "a.hdf5", True)
    s2 = StarStore(path)
    assert s2.native_pending_paths("db") == ["a.hdf5"] and s2.native_job("db", "a.hdf5") is True
    s2.adopt_file_stars("db", {"a.hdf5": False, "b.hdf5": True, "c.hdf5": None})
    assert s2.starred_paths_for("db") == {"b.hdf5"}                     # waiting change of ours is kept, file wins elsewhere
    s2.native_done("db", "a.hdf5", False)                               # user changed their mind meanwhile: still waiting
    assert s2.native_pending_paths("db") == ["a.hdf5"]
    s2.native_done("db", "a.hdf5", True)
    assert s2.native_pending_paths("db") == []
    s2.queue_native("db", "b.hdf5", False)
    s2.move_data_identity("", "", database_id="db", old_relative_path="b.hdf5", new_relative_path="z.hdf5")
    assert s2.native_pending_paths("db") == ["z.hdf5"]


def test_stale_scan_does_not_undo_a_later_toggle(tmp_path):
    store = StarStore(tmp_path / "stars.json")
    since = store.clock()                       # a scan starts and reads the file (star = True)
    store.toggle("db", "a.hdf5")                # the user stars it meanwhile; the write finished already
    store.adopt_file_stars("db", {"a.hdf5": False}, since=since)
    assert store.is_starred("db", "a.hdf5")     # the old reading must not win
    store.adopt_file_stars("db", {"a.hdf5": False}, since=store.clock())
    assert not store.is_starred("db", "a.hdf5")  # a scan started afterwards does
