"""
tests/test_star_store.py — v0.8

Tests for app/core/star_store.py, including the critical HDF5
immutability check: Star/Unstar must NEVER modify the original file.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.star_store import StarStore, default_storage_path

from tests.real_data import SMALL_FILE

real_file_pytestmark = pytest.mark.skipif(
    not SMALL_FILE.exists(), reason="Real Labber sample file not present in this environment."
)


# ---- basic star / unstar -------------------------------------------------------

def test_star_and_unstar(tmp_path):
    store = StarStore(tmp_path / "stars.json")
    assert not store.is_starred("db1", "a.hdf5")
    store.set_starred("db1", "a.hdf5", True)
    assert store.is_starred("db1", "a.hdf5")
    store.set_starred("db1", "a.hdf5", False)
    assert not store.is_starred("db1", "a.hdf5")


def test_toggle_returns_new_state(tmp_path):
    store = StarStore(tmp_path / "stars.json")
    assert store.toggle("db1", "a.hdf5") is True
    assert store.toggle("db1", "a.hdf5") is False


def test_starred_paths_for(tmp_path):
    store = StarStore(tmp_path / "stars.json")
    store.set_starred("db1", "a.hdf5", True)
    store.set_starred("db1", "b.hdf5", True)
    store.set_starred("db1", "c.hdf5", False)
    assert store.starred_paths_for("db1") == {"a.hdf5", "b.hdf5"}
    assert store.starred_paths_for("db2") == set()


# ---- persistence / reload ----------------------------------------------------

def test_persistence_across_new_instance(tmp_path):
    path = tmp_path / "stars.json"
    store1 = StarStore(path)
    store1.set_starred("db1", "a.hdf5", True)

    store2 = StarStore(path)  # simulates closing and reopening the app
    assert store2.is_starred("db1", "a.hdf5")


def test_reload_picks_up_external_changes(tmp_path):
    path = tmp_path / "stars.json"
    store1 = StarStore(path)
    store2 = StarStore(path)

    store1.set_starred("db1", "a.hdf5", True)
    assert not store2.is_starred("db1", "a.hdf5")  # store2 hasn't reloaded yet
    store2.reload()
    assert store2.is_starred("db1", "a.hdf5")


def test_persistence_survives_multiple_open_close_cycles(tmp_path):
    path = tmp_path / "stars.json"
    for i in range(5):
        store = StarStore(path)
        if i == 2:
            store.set_starred("dbX", "log.hdf5", True)
        if i > 2:
            assert store.is_starred("dbX", "log.hdf5")


# ---- database identity + relative path (no filename-only collisions) ----------

def test_database_identity_isolates_stars(tmp_path):
    store = StarStore(tmp_path / "stars.json")
    store.set_starred("dbA", "Experiment_A/test.hdf5", True)
    assert store.is_starred("dbA", "Experiment_A/test.hdf5")
    assert not store.is_starred("dbB", "Experiment_A/test.hdf5")


def test_same_filename_different_folders_same_database(tmp_path):
    """The key regression this feature must never have: two files
    named identically in different subfolders of the SAME database
    must be starrable independently."""
    store = StarStore(tmp_path / "stars.json")
    store.set_starred("db1", "Experiment_A/test.hdf5", True)
    store.set_starred("db1", "Experiment_B/test.hdf5", False)
    assert store.is_starred("db1", "Experiment_A/test.hdf5")
    assert not store.is_starred("db1", "Experiment_B/test.hdf5")

    store.set_starred("db1", "Experiment_B/test.hdf5", True)
    assert store.is_starred("db1", "Experiment_A/test.hdf5")
    assert store.is_starred("db1", "Experiment_B/test.hdf5")


# ---- corrupted state file recovery --------------------------------------------

def test_corrupted_json_recovers_without_crash(tmp_path):
    path = tmp_path / "stars.json"
    path.write_text("{ not valid json !!!")
    store = StarStore(path)  # must not raise
    assert not store.is_starred("db1", "a.hdf5")
    # can still be used normally afterward
    store.set_starred("db1", "a.hdf5", True)
    assert store.is_starred("db1", "a.hdf5")


def test_corrupted_json_creates_backup(tmp_path):
    path = tmp_path / "stars.json"
    path.write_text("not json at all")
    StarStore(path)
    backup = path.with_suffix(".json.bak")
    assert backup.exists()
    assert backup.read_text() == "not json at all"


def test_malformed_but_valid_json_structure_is_tolerated(tmp_path):
    """Valid JSON, but not the expected shape (e.g. a list instead of
    a dict, or non-string entries) - must degrade gracefully."""
    path = tmp_path / "stars.json"
    path.write_text('["this", "is", "a", "list", "not", "a", "dict"]')
    store = StarStore(path)
    assert not store.is_starred("db1", "a.hdf5")  # started fresh
    store.set_starred("db1", "a.hdf5", True)
    assert store.is_starred("db1", "a.hdf5")


def test_missing_storage_file_starts_empty(tmp_path):
    path = tmp_path / "does_not_exist_yet.json"
    store = StarStore(path)
    assert not store.is_starred("db1", "a.hdf5")


def test_default_storage_path_is_under_home():
    path = default_storage_path()
    assert str(Path.home()) in str(path)
    assert path.name == "stars.json"


# ---- unstarring the last entry for a database cleans up the key ----------------

def test_unstarring_last_entry_removes_database_key(tmp_path):
    path = tmp_path / "stars.json"
    store = StarStore(path)
    store.set_starred("db1", "a.hdf5", True)
    store.set_starred("db1", "a.hdf5", False)
    assert store.starred_paths_for("db1") == set()
    # internal dict shouldn't accumulate empty entries forever
    assert "db1" not in store._data


# ---- HDF5 IMMUTABILITY (critical) ----------------------------------------------

@real_file_pytestmark
def test_star_unstar_never_modifies_original_hdf5(tmp_path):
    """The single most important correctness property of Star: it
    must be implemented via completely external state, never by
    touching the HDF5 file's bytes, size, or mtime in any way."""
    hdf5_copy = tmp_path / "test.hdf5"
    shutil.copy(SMALL_FILE, hdf5_copy)

    stat_before = hdf5_copy.stat()
    size_before, mtime_before = stat_before.st_size, stat_before.st_mtime
    hash_before = hashlib.sha256(hdf5_copy.read_bytes()).hexdigest()

    store = StarStore(tmp_path / "stars.json")
    db_id = "test_db"
    rel_path = "test.hdf5"

    store.set_starred(db_id, rel_path, True)
    store.set_starred(db_id, rel_path, False)
    store.toggle(db_id, rel_path)
    store.toggle(db_id, rel_path)
    store.reload()

    stat_after = hdf5_copy.stat()
    size_after, mtime_after = stat_after.st_size, stat_after.st_mtime
    hash_after = hashlib.sha256(hdf5_copy.read_bytes()).hexdigest()

    assert size_before == size_after
    assert mtime_before == mtime_after
    assert hash_before == hash_after


@real_file_pytestmark
def test_star_does_not_open_hdf5_file_at_all(tmp_path, monkeypatch):
    """Even stronger guarantee: StarStore must never even attempt to
    open the HDF5 file - it operates purely on the (database_id,
    relative_path) STRING key, with no filesystem access to the HDF5
    itself."""
    import app.core.hdf5_reader as hdf5_reader_module

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("StarStore must never open the HDF5 file")

    monkeypatch.setattr(hdf5_reader_module.h5py, "File", _fail_if_called)

    store = StarStore(tmp_path / "stars.json")
    store.set_starred("db1", "test.hdf5", True)
    store.toggle("db1", "test.hdf5")
    assert True  # if we got here without the monkeypatched File() firing, we're good
