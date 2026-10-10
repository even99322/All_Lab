"""v1.0.5: busy network files, native-crash reporting."""
import errno
from pathlib import Path

import h5py
import pytest

from app.core import hdf5_reader
from app.core.hdf5_reader import HDF5ReadError, HDF5Reader
from app.core.session_lifecycle import SessionLifecycleStore


def _make(tmp_path) -> Path:
    path = tmp_path / "a.hdf5"
    with h5py.File(path, "w") as f:
        f.create_dataset("x", data=[1, 2, 3])
    return path


def test_transient_lock_error_is_retried_and_succeeds(tmp_path, monkeypatch):
    path = _make(tmp_path)
    real = h5py.File
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError(errno.EAGAIN, "Unable to synchronously open file (unable to lock file, errno = 35)")
        return real(*args, **kwargs)

    monkeypatch.setattr(hdf5_reader.h5py, "File", flaky)
    monkeypatch.setattr(hdf5_reader.time, "sleep", lambda s: None)
    with HDF5Reader(path) as reader:
        assert reader.is_open
    assert calls["n"] == 3


def test_persistent_busy_file_is_reported_as_transient(tmp_path, monkeypatch):
    path = _make(tmp_path)

    def always(*args, **kwargs):
        raise OSError(errno.EAGAIN, "unable to lock file, errno = 35")

    monkeypatch.setattr(hdf5_reader.h5py, "File", always)
    monkeypatch.setattr(hdf5_reader.time, "sleep", lambda s: None)
    with pytest.raises(HDF5ReadError) as info:
        HDF5Reader(path)
    assert info.value.transient and "temporarily busy" in str(info.value)


def test_corrupt_file_is_not_transient(tmp_path):
    bad = tmp_path / "bad.hdf5"
    bad.write_bytes(b"not hdf5 at all")
    with pytest.raises(HDF5ReadError) as info:
        HDF5Reader(bad)
    assert not info.value.transient


def test_reader_opens_without_locking(tmp_path, monkeypatch):
    path = _make(tmp_path)
    seen = {}
    real = h5py.File

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(hdf5_reader.h5py, "File", spy)
    HDF5Reader(path).close()
    assert seen.get("locking") is False


def test_previous_native_crash_stack_is_reported(tmp_path):
    state = tmp_path / "session_lifecycle.json"
    first = SessionLifecycleStore(state)
    first.begin()                                       # status stays "running": unclean exit
    first.native_crash_path.write_text("Fatal Python error: Segmentation fault\nstack...", encoding="utf-8")
    second = SessionLifecycleStore(state)
    recovery = second.begin()
    assert recovery.safe_recovery
    assert "Segmentation fault" in (recovery.native_crash or "")
    assert not second.native_crash_path.exists()
    assert (tmp_path / "native_crash_previous.txt").exists()


def test_clean_exit_leaves_no_native_crash_report(tmp_path):
    state = tmp_path / "session_lifecycle.json"
    store = SessionLifecycleStore(state)
    store.begin()
    store.enable_native_crash_log()
    store.mark_clean()
    assert not store.native_crash_path.exists()
    assert SessionLifecycleStore(state).begin().native_crash is None
