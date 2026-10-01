"""Test isolation: every test gets its own empty user home / data folder.

Viewer records (marks, display states, sessions, stars ...) are saved in the
user's data folder and restored the next time a file opens. Without a fresh
folder per test, records written by one test leaked into the next (the long
standing "14 Mark failures": counts of 2, 3, 4 ... marks instead of 1).
"""

from __future__ import annotations

import os

import pytest

# Tests written before v1.01 expect a newly opened file in 1D; the first-open-in-2D
# behaviour is tested on its own (test_v019f_opening_view turns it back on). Set in the
# environment so that the Host / Client processes of the network tests inherit it.
os.environ.setdefault("LABLOGVIEWER_FIRST_OPEN_2D", "0")


@pytest.fixture(autouse=True)
def isolated_user_data(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    (home / "Documents").mkdir()
    monkeypatch.setenv("HOME", str(home))
    from app.core import data_location

    data_location._reset_for_tests()
    try:
        from app.analysis.yig_fitting.core import paths

        monkeypatch.setattr(paths, "_CONFIG", None)
    except Exception:
        pass
    try:
        from app.core import personal

        personal.reset_cache()
    except Exception:
        pass
    try:
        from app.localization import manager as localization

        # each test starts with its own (English) settings; no language left over from another test
        monkeypatch.setattr(localization, "_DEFAULT_MANAGER", None)
        monkeypatch.setattr(localization, "_DEFAULT_STORE", None)
        monkeypatch.setattr(localization, "_ACTIVE", None)
    except Exception:
        pass
    try:
        from app._guard import experience, gate

        experience.reset_cache()
        gate.invalidate()
    except Exception:
        pass
    yield home
    data_location._reset_for_tests()
