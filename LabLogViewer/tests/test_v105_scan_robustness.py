import h5py

from app.core.database_scanner import DatabaseScanner


def _make(path):
    with h5py.File(path, "w") as f:
        f.create_dataset("x", data=[1, 2, 3])


def test_one_unreadable_file_does_not_abort_scan(tmp_path, monkeypatch):
    _make(tmp_path / "good.hdf5")
    _make(tmp_path / "bad.hdf5")
    real = h5py.is_hdf5

    def flaky(p):
        if "bad" in str(p):
            raise OSError(5, "Input/output error")
        return real(p)

    monkeypatch.setattr(h5py, "is_hdf5", flaky)
    monkeypatch.setattr("app.core.database_scanner.time.sleep", lambda s: None)
    result = DatabaseScanner.scan(tmp_path, fast_listing=True)
    status = {e.file_name: e.status for e in result.entries}
    assert status == {"good.hdf5": "ok", "bad.hdf5": "error"}
