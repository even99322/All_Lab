"""v0.18D: Windows long paths, locked-file retries and non-UTF-8 model files."""

from __future__ import annotations

import os

import pytest


def test_long_windows_paths_get_the_extended_prefix(monkeypatch):
    from app.core import win_paths

    long = "/data/" + "x" * 300 + "/scan.hdf5"
    assert win_paths.long_path(long) == long                     # not Windows: unchanged
    monkeypatch.setattr(win_paths, "_is_windows", lambda: True)
    assert win_paths.long_path("/short/scan.hdf5") == "/short/scan.hdf5"
    assert win_paths.long_path(long).startswith("\\\\?\\")


def test_replace_is_retried_while_windows_reports_the_file_in_use(tmp_path, monkeypatch):
    from app.core import external_state

    calls = []
    real_replace = os.replace

    def flaky(source, destination):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("in use by another process")
        real_replace(source, destination)

    monkeypatch.setattr(external_state, "_is_windows", lambda: True)
    monkeypatch.setattr(external_state.os, "replace", flaky)
    external_state.atomic_write_json(tmp_path / "stars.json", {"ok": True})
    assert len(calls) == 3
    assert external_state.load_json_state(tmp_path / "stars.json", {}).value == {"ok": True}


def test_replace_still_fails_fast_off_windows(tmp_path, monkeypatch):
    from app.core import external_state

    def locked(source, destination):
        raise PermissionError("denied")

    monkeypatch.setattr(external_state.os, "replace", locked)
    with pytest.raises(PermissionError):
        external_state.atomic_write_json(tmp_path / "x.json", {})
    assert not list(tmp_path.glob(".x.json.*"))                   # temporary file cleaned up


def test_big5_model_file_with_coding_declaration_is_read(tmp_path):
    from app.analysis.yig_fitting.core.trust import read_source, scan_source

    model = tmp_path / "model_big5.py"
    model.write_bytes("# -*- coding: cp950 -*-\n# 共振模型\ndef S(w, a):\n    return a * w\n".encode("cp950"))
    text = read_source(model)
    assert "共振模型" in text and not scan_source(text).blocked
