"""v1.0.4: restoring a session and switching folders on the NAS must not ask the server per file."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture()
def net(tmp_path, monkeypatch):
    from app.core import data_identity

    monkeypatch.setenv("LABLOGVIEWER_NET_CACHE", "force")
    monkeypatch.setenv("LABLOGVIEWER_NET_CACHE_DIR", str(tmp_path / "cache"))
    data_identity._NETWORK_IDENTITIES.clear()
    data_identity._NETWORK_FOLDERS.clear()
    yield data_identity
    data_identity._NETWORK_IDENTITIES.clear()
    data_identity._NETWORK_FOLDERS.clear()


def _count_resolves(monkeypatch) -> list:
    calls: list = []
    real = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **k: calls.append(self) or real(self, *a, **k))
    return calls


def test_network_identity_resolves_each_folder_once(tmp_path, monkeypatch, net):
    folder = tmp_path / "share" / "Data_1014"
    folder.mkdir(parents=True)
    files = [folder / f"meas_{k}.hdf5" for k in range(20)]
    for path in files:
        path.write_bytes(b"x")
    expected = [str(path.resolve()) for path in files]
    calls = _count_resolves(monkeypatch)
    assert [net.stable_data_identity(path) for path in files] == expected
    assert len(calls) == 1                                       # the folder, not every file


def test_local_identity_still_resolves_the_file(tmp_path, monkeypatch):
    from app.core import data_identity

    monkeypatch.setenv("LABLOGVIEWER_NET_CACHE", "0")
    target = tmp_path / "real.hdf5"
    target.write_bytes(b"x")
    link = tmp_path / "link.hdf5"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    assert data_identity.stable_data_identity(link) == str(target.resolve())


def _entry(path: Path, status: str = "ok"):
    return SimpleNamespace(absolute_path=str(path), file_name=path.name, status=status)


def test_session_restore_matches_viewers_without_resolving_every_entry(tmp_path, monkeypatch, net):
    from app.gui.browser_window import BrowserWindow

    folder = tmp_path / "share"
    folder.mkdir()
    entries = []
    for k in range(300):
        path = folder / f"meas_{k:03d}.hdf5"
        path.write_bytes(b"x")
        entries.append(_entry(path))
    host = SimpleNamespace(scan_result=SimpleNamespace(entries=entries))
    wanted = entries[123]
    calls = _count_resolves(monkeypatch)
    found = BrowserWindow._entry_for_source(host, str(Path(wanted.absolute_path)))
    assert found is wanted and not calls
    # A saved identity spelled differently (e.g. a mapped drive) is matched by resolving only same-name files.
    other_root = tmp_path / "alias"
    other_root.symlink_to(folder, target_is_directory=True)
    alias_entries = [_entry(other_root / Path(e.absolute_path).name) for e in entries]
    host.scan_result.entries = alias_entries
    saved = str(Path(wanted.absolute_path).resolve())
    calls.clear()
    found = BrowserWindow._entry_for_source(host, saved)
    assert found is alias_entries[123]
    assert len(calls) <= 1
    assert BrowserWindow._entry_for_source(host, str(folder / "missing.hdf5")) is None
    assert BrowserWindow._entry_for_source(host, None) is None


def test_files_below_an_opened_root_need_no_server_call(tmp_path, monkeypatch, net):
    root = (tmp_path / "share").resolve()
    for day in ("Data_1013", "Data_1014"):
        (root / "2026" / "10" / day).mkdir(parents=True)
        (root / "2026" / "10" / day / "a.hdf5").write_bytes(b"x")
    net.remember_network_root(str(root), str(root))
    calls = _count_resolves(monkeypatch)
    for day in ("Data_1013", "Data_1014"):
        path = root / "2026" / "10" / day / "a.hdf5"
        assert net.stable_data_identity(path) == str(path)
    assert not calls
