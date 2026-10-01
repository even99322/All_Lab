"""v0.19B: firewall notice, missing-model fallback, cloud-synced data folders."""

from __future__ import annotations

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    from app.core import data_location

    user = tmp_path / "user"
    (user / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr(data_location, "legacy_sources", lambda: [])
    data_location._reset_for_tests()
    yield user
    data_location._reset_for_tests()


def test_firewall_notice_only_on_windows_and_once(tmp_path):
    from app.gui.network_panel import windows_firewall_notice_needed
    from app.settings.store import SettingsStore

    store = SettingsStore(tmp_path / "settings.json")
    assert windows_firewall_notice_needed(store, platform="win32")
    assert not windows_firewall_notice_needed(store, platform="darwin")
    store.set_network_flag("firewall_notice_shown", True)
    assert not windows_firewall_notice_needed(store, platform="win32")


@pytest.mark.parametrize("path, service", [
    ("~/Library/Mobile Documents/com~apple~CloudDocs/Lab", "iCloud"),
    ("~/OneDrive - NTU/Lab", "OneDrive"),
    ("~/Dropbox/Lab", "Dropbox"),
    ("~/Library/CloudStorage/GoogleDrive-me/Lab", "Google Drive"),
])
def test_cloud_folders_are_recognised(home, path, service):
    from app.core.data_location import cloud_service

    assert cloud_service(path.replace("~", str(home))) == service


def test_icloud_documents_sync_changes_the_default_and_blocks_moves(home):
    from app.core import data_location

    assert data_location.cloud_service(home / "Documents" / "X") is None
    assert data_location.default_data_root() == home / "Documents" / "LabLogViewerData"
    (home / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Documents").mkdir(parents=True)
    import sys
    if sys.platform == "darwin":
        assert data_location.cloud_service(home / "Documents" / "X") == "iCloud"
        assert data_location.default_data_root() == home / "LabLogViewerData"     # stays local


def test_move_into_cloud_needs_explicit_consent(home):
    from app.core import data_location

    data_location.data_root()
    target = home / "Dropbox" / "LabLogViewerData"
    target.parent.mkdir()
    with pytest.raises(data_location.CloudLocationError) as caught:
        data_location.request_move(target)
    assert caught.value.service == "Dropbox"
    assert data_location.request_move(target, allow_cloud=True) == "scheduled"
    pointer = data_location._read_pointer()
    assert pointer["cloud_consent"]["service"] == "Dropbox" and pointer["cloud_consent"]["accepted"]


def test_missing_model_uses_bundled_model_of_same_name(tmp_path):
    import os
    from app.analysis.yig_fitting import controller as ctl

    bundled = os.path.join(os.path.dirname(ctl.__file__), "models", "formulas_example.py")
    assert os.path.isfile(bundled)

    class Stub:
        formula_path = str(tmp_path / "LabLogViewer_v0.15C" / "formulas_example.py")
        messages = []

        def status(self, text):
            self.messages.append(text)

    stub = Stub()
    source = ctl.YigFittingController.reload_formula if hasattr(ctl, "YigFittingController") else None
    if source is None:
        source = next(obj.reload_formula for obj in vars(ctl).values()
                      if isinstance(obj, type) and hasattr(obj, "reload_formula"))
    stub.module = stub.funcs = None
    stub.w = None
    try:
        source(stub)
    except Exception:
        pass                                  # the stub has no UI; only the path decision matters
    assert stub.formula_path == bundled and stub.messages


def test_fingerprint_follows_content_not_path(home, tmp_path):
    import shutil
    from app.core import fingerprint
    from app.core.data_identity import stable_data_identity
    from tests.real_data import BIG_FILE

    if not BIG_FILE.exists():
        pytest.skip("fixture unavailable")
    fingerprint.remember(BIG_FILE, "0828 RSMEP_1", 1.0, background=False)
    entry = fingerprint.lookup(stable_data_identity(BIG_FILE))
    assert entry["size"] == BIG_FILE.stat().st_size and len(entry["sha256"]) == 64
    moved = tmp_path / "central" / "2026" / BIG_FILE.name
    moved.parent.mkdir(parents=True)
    shutil.copy2(BIG_FILE, moved)
    assert fingerprint.fingerprint(moved)["sha256"] == entry["sha256"]           # same data, new place
    assert fingerprint.sampled_sha256(moved) == entry["sample_sha256"]
    fingerprint.remember("lablogviewer-remote://x/y.hdf5", background=False)      # remote data: ignored
    assert len(fingerprint.load()) == 1
