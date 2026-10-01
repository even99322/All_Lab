"""
tests/test_database_scanner.py — v0.8

Tests for app/core/database_scanner.py (DatabaseScanner, LogEntry,
DatabaseScanResult). No Qt dependency at all - pure filesystem +
existing HDF5 parser pipeline. Builds its own small synthetic/real
fixture folders under pytest's tmp_path rather than relying on any
fixed real sample layout.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import h5py
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database_scanner import DatabaseScanner, DatabaseScanResult, LogEntry

from tests.real_data import BIG_FILE, SMALL_FILE

real_file_pytestmark = pytest.mark.skipif(
    not (BIG_FILE.exists() and SMALL_FILE.exists()),
    reason="Real Labber sample files not present in this environment.",
)


# ---- helpers -----------------------------------------------------------------

def _write_fake_hdf5(path: Path, valid_labber: bool = False) -> None:
    """A minimal HDF5 file - NOT a valid Labber log unless valid_labber,
    used to exercise the 'valid HDF5 but not a Labber log' error path
    without needing a real Labber-format fixture."""
    with h5py.File(path, "w") as f:
        f.create_dataset("random_data", data=np.arange(5))
        if valid_labber:
            f.attrs["log_name"] = "not_actually_parseable"


# ---- recursive scan / nested folders -------------------------------------------

@real_file_pytestmark
def test_recursive_scan_finds_nested_files(tmp_path):
    (tmp_path / "A").mkdir()
    (tmp_path / "B" / "C").mkdir(parents=True)
    shutil.copy(BIG_FILE, tmp_path / "A" / "run1.hdf5")
    shutil.copy(SMALL_FILE, tmp_path / "B" / "C" / "run2.hdf5")

    result = DatabaseScanner.scan(tmp_path)
    assert result.n_total == 2
    assert result.n_ok == 2
    rel_paths = {e.relative_path for e in result.entries}
    assert rel_paths == {"A/run1.hdf5", "B/C/run2.hdf5"}


@real_file_pytestmark
def test_scan_recognizes_both_hdf5_and_h5_extensions(tmp_path):
    shutil.copy(BIG_FILE, tmp_path / "a.hdf5")
    shutil.copy(SMALL_FILE, tmp_path / "b.h5")
    result = DatabaseScanner.scan(tmp_path)
    assert result.n_total == 2


def test_scan_ignores_non_hdf5_files(tmp_path):
    (tmp_path / "notes.txt").write_text("hello")
    (tmp_path / "data.csv").write_text("a,b,c")
    result = DatabaseScanner.scan(tmp_path)
    assert result.n_total == 0


# ---- empty folder / no hdf5 --------------------------------------------------

def test_empty_folder(tmp_path):
    result = DatabaseScanner.scan(tmp_path)
    assert result.n_total == 0
    assert result.n_ok == 0
    assert result.n_error == 0


def test_folder_with_only_subfolders_no_files(tmp_path):
    (tmp_path / "empty_sub").mkdir()
    (tmp_path / "another_empty").mkdir()
    result = DatabaseScanner.scan(tmp_path)
    assert result.n_total == 0


def test_nonexistent_folder_does_not_raise():
    result = DatabaseScanner.scan("/definitely/does/not/exist/anywhere")
    assert result.n_total == 0


def test_scan_of_a_file_not_a_folder_does_not_raise(tmp_path):
    f = tmp_path / "not_a_folder.txt"
    f.write_text("x")
    result = DatabaseScanner.scan(f)
    assert result.n_total == 0


# ---- corrupted / invalid hdf5 -------------------------------------------------

def test_corrupted_hdf5_is_isolated_not_fatal(tmp_path):
    (tmp_path / "corrupted.hdf5").write_bytes(b"this is not an hdf5 file at all")
    result = DatabaseScanner.scan(tmp_path)
    assert result.n_total == 1
    assert result.n_error == 1
    entry = result.entries[0]
    assert entry.status == "error"
    assert entry.error_message
    assert entry.log_name == "corrupted"  # fallback to filename stem


def test_valid_hdf5_but_not_labber_format_is_isolated(tmp_path):
    _write_fake_hdf5(tmp_path / "not_labber.hdf5")
    result = DatabaseScanner.scan(tmp_path)
    assert result.n_total == 1
    assert result.n_error == 1
    assert result.entries[0].status == "error"


@real_file_pytestmark
def test_one_corrupted_file_does_not_block_others(tmp_path):
    shutil.copy(BIG_FILE, tmp_path / "good1.hdf5")
    (tmp_path / "bad.hdf5").write_bytes(b"garbage")
    shutil.copy(SMALL_FILE, tmp_path / "good2.hdf5")

    result = DatabaseScanner.scan(tmp_path)
    assert result.n_total == 3
    assert result.n_ok == 2
    assert result.n_error == 1
    good_names = {e.file_name for e in result.entries if e.status == "ok"}
    assert good_names == {"good1.hdf5", "good2.hdf5"}


# ---- missing file (deleted between find and scan) -----------------------------

def test_scan_one_handles_file_deleted_after_discovery(tmp_path):
    f = tmp_path / "will_vanish.hdf5"
    f.write_bytes(b"x")
    entry = DatabaseScanner._scan_one(tmp_path, f)  # file exists here
    assert entry.status == "error"  # garbage bytes -> not a valid hdf5 anyway

    f.unlink()
    entry2 = DatabaseScanner._scan_one(tmp_path, f)  # file gone now
    assert entry2.status == "error"
    assert "Cannot access" in entry2.error_message


# ---- duplicate filenames in different folders ---------------------------------

@real_file_pytestmark
def test_duplicate_filenames_different_folders_are_disambiguated(tmp_path):
    (tmp_path / "Experiment_A").mkdir()
    (tmp_path / "Experiment_B").mkdir()
    shutil.copy(SMALL_FILE, tmp_path / "Experiment_A" / "test.hdf5")
    shutil.copy(SMALL_FILE, tmp_path / "Experiment_B" / "test.hdf5")

    result = DatabaseScanner.scan(tmp_path)
    assert result.n_total == 2
    rel_paths = {e.relative_path for e in result.entries}
    assert rel_paths == {"Experiment_A/test.hdf5", "Experiment_B/test.hdf5"}
    assert len(rel_paths) == 2  # never collide despite identical file_name


# ---- database identity + relative path -----------------------------------------

def test_database_id_is_stable_absolute_path(tmp_path):
    id1 = DatabaseScanner.compute_database_id(tmp_path)
    id2 = DatabaseScanner.compute_database_id(str(tmp_path))
    assert id1 == id2
    assert id1 == str(tmp_path.resolve())


def test_database_id_differs_between_folders(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert DatabaseScanner.compute_database_id(a) != DatabaseScanner.compute_database_id(b)


def test_log_entry_folder_parts(tmp_path):
    entry = LogEntry(
        absolute_path="/x/A/B/f.hdf5", relative_path="A/B/f.hdf5", file_name="f.hdf5",
        log_name="f", status="ok", error_message=None, size_bytes=0, mtime=0.0,
    )
    assert entry.folder_parts == ("A", "B")

    entry_root = LogEntry(
        absolute_path="/x/f.hdf5", relative_path="f.hdf5", file_name="f.hdf5",
        log_name="f", status="ok", error_message=None, size_bytes=0, mtime=0.0,
    )
    assert entry_root.folder_parts == ()


# ---- progress callback behavior ------------------------------------------------

@real_file_pytestmark
def test_progress_callback_total_and_completion(tmp_path):
    shutil.copy(BIG_FILE, tmp_path / "a.hdf5")
    shutil.copy(SMALL_FILE, tmp_path / "b.hdf5")

    calls = []
    DatabaseScanner.scan(tmp_path, progress_callback=lambda done, total: calls.append((done, total)))

    assert len(calls) == 2
    assert calls[0] == (1, 2)
    assert calls[1] == (2, 2)  # completion: done == total on the last call


def test_progress_callback_not_called_for_empty_folder(tmp_path):
    calls = []
    DatabaseScanner.scan(tmp_path, progress_callback=lambda done, total: calls.append((done, total)))
    assert calls == []


def test_progress_callback_reports_errors_too(tmp_path):
    """An error entry still counts toward progress - the callback
    doesn't distinguish ok vs error, just files processed."""
    (tmp_path / "bad1.hdf5").write_bytes(b"x")
    (tmp_path / "bad2.hdf5").write_bytes(b"y")
    calls = []
    result = DatabaseScanner.scan(tmp_path, progress_callback=lambda d, t: calls.append((d, t)))
    assert len(calls) == 2
    assert result.n_error == 2


# ---- find_hdf5_files sorting / determinism -------------------------------------

@real_file_pytestmark
def test_find_hdf5_files_is_sorted_deterministically(tmp_path):
    shutil.copy(BIG_FILE, tmp_path / "z.hdf5")
    shutil.copy(SMALL_FILE, tmp_path / "a.hdf5")
    files = DatabaseScanner.find_hdf5_files(tmp_path)
    assert [f.name for f in files] == ["a.hdf5", "z.hdf5"]
