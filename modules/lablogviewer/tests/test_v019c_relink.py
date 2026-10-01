"""v0.19C hidden Re-link tool, Personal lock UI and licence dialog (all in a temporary home)."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

DEVTOOLS = Path(__file__).resolve().parents[2] / "LabLogViewer_Plugins" / "DevTools"
sys.path.insert(0, str(DEVTOOLS))
dev = pytest.importorskip("llv_devtools")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def world(tmp_path):
    """Old database (files since moved away) with records, and the new database."""
    from app.analysis.yig_fitting.core.paths import sub_dir
    from app.core import fingerprint
    from app.core.data_location import data_root
    from app.core.external_state import atomic_write_json

    old_root = (tmp_path / "old_db").resolve()
    new_root = (tmp_path / "new_db").resolve()
    (old_root / "run").mkdir(parents=True)
    (new_root / "2026" / "a").mkdir(parents=True)
    contents = {"A.hdf5": os.urandom(3000), "B.hdf5": os.urandom(5000), "C.hdf5": os.urandom(700)}
    for name, data in contents.items():
        (old_root / "run" / name).write_bytes(data)
    # Fingerprints were remembered when A and B were opened; C was never opened.
    for name in ("A.hdf5", "B.hdf5"):
        fingerprint.remember(old_root / "run" / name, background=False)
    old = {name: str(old_root / "run" / name) for name in contents}
    state = data_root() / "state"
    atomic_write_json(state / "stars.json", {"schema_version": 1, "by_database": {str(old_root): ["run/A.hdf5", "run/C.hdf5"]}})
    atomic_write_json(state / "comments.json", {"schema_version": 2, "by_database": {str(old_root): {"run/B.hdf5": "legacy"}},
                                               "by_data": {old["A.hdf5"]: "note A"}})
    atomic_write_json(state / "marks.json", {"schema_version": 1, "app_version": "x",
                                            "datasets": {old["A.hdf5"]: {"contexts": {"1d": []}}}})
    atomic_write_json(state / "viewer_display_states.json", {"schema_version": 1, "by_data": {old["B.hdf5"]: {"mode": 1}}})
    atomic_write_json(state / "three_d_states.json", {old["A.hdf5"]: {"azimuth": 30}})
    atomic_write_json(state / "session.json", {"viewer": {"open": [old["A.hdf5"]]}, "database_path": str(old_root)})
    sessions = Path(sub_dir("sessions"))
    session_file = sessions / (hashlib.sha256(old["A.hdf5"].encode()).hexdigest() + ".json")
    session_file.write_text(json.dumps({"source": old["A.hdf5"], "p0": [1, 2]}), encoding="utf-8")
    # The files move: A and B into the new database (A renamed), C too (same name).
    (new_root / "2026" / "a" / "A_renamed.hdf5").write_bytes(contents["A.hdf5"])
    (new_root / "2026" / "a" / "B.hdf5").write_bytes(contents["B.hdf5"])
    (new_root / "C.hdf5").write_bytes(contents["C.hdf5"])
    for name in contents:
        (old_root / "run" / name).unlink()
    return {"old": old, "old_root": str(old_root), "new_root": str(new_root), "state": state, "sessions": sessions}


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_find_match_apply_and_undo(world):
    from app.core import relink
    from app.core.data_location import data_root

    records = relink.find_missing(world["state"])
    assert {r.identity for r in records} == set(world["old"].values())
    by_id = {r.identity: r for r in records}
    assert by_id[world["old"]["A.hdf5"]].fingerprint and not by_id[world["old"]["C.hdf5"]].fingerprint
    matches = relink.match(records, world["new_root"])
    found = {Path(m.old.identity).name: (Path(m.new_path).name, m.confidence, m.selected) for m in matches}
    assert found["A.hdf5"] == ("A_renamed.hdf5", "exact", True)       # found by content despite the rename
    assert found["B.hdf5"] == ("B.hdf5", "exact", True)
    assert found["C.hdf5"] == ("C.hdf5", "guess", False)              # no fingerprint: name only, not ticked
    before = {name: (world["state"] / name).read_bytes() for name in relink.RECORD_FILES
              if (world["state"] / name).exists()}

    result = relink.apply(matches, world["state"], world["sessions"], world["new_root"], data_root())
    assert result.moved == 2 and result.conflicts == []
    new_a = str(Path(world["new_root"]) / "2026" / "a" / "A_renamed.hdf5")
    new_b = str(Path(world["new_root"]) / "2026" / "a" / "B.hdf5")
    stars = _read(world["state"] / "stars.json")["by_database"]
    assert stars[world["new_root"]] == ["2026/a/A_renamed.hdf5"] and stars[world["old_root"]] == ["run/C.hdf5"]
    comments = _read(world["state"] / "comments.json")
    assert comments["by_data"] == {new_a: "note A"}
    assert comments["by_database"][world["new_root"]] == {"2026/a/B.hdf5": "legacy"}
    assert list(_read(world["state"] / "marks.json")["datasets"]) == [new_a]
    assert list(_read(world["state"] / "viewer_display_states.json")["by_data"]) == [new_b]
    assert list(_read(world["state"] / "three_d_states.json")) == [new_a]
    assert _read(world["state"] / "session.json")["viewer"]["open"] == [new_a]
    new_session = world["sessions"] / (hashlib.sha256(new_a.encode()).hexdigest() + ".json")
    assert _read(new_session)["source"] == new_a
    old_session = world["sessions"] / (hashlib.sha256(world["old"]["A.hdf5"].encode()).hexdigest() + ".json")
    assert not old_session.exists()                                   # moved, not copied
    assert {r.identity for r in relink.find_missing(world["state"])} == {world["old"]["C.hdf5"]}

    relink.undo(result.backup, world["state"], world["sessions"])
    for name, data in before.items():
        assert (world["state"] / name).read_bytes() == data
    assert not new_session.exists()
    assert (world["sessions"] / (hashlib.sha256(world["old"]["A.hdf5"].encode()).hexdigest() + ".json")).exists()


def test_existing_records_at_the_new_path_are_kept(world):
    from app.core import relink
    from app.core.data_location import data_root
    from app.core.external_state import atomic_write_json

    new_a = str(Path(world["new_root"]) / "2026" / "a" / "A_renamed.hdf5")
    marks = _read(world["state"] / "marks.json")
    old_a = next(iter(marks["datasets"]))
    marks["datasets"][old_a] = {"contexts": {"1d": {"pane_id": 0, "state": {"marks": [{"number": 1, "x": 1.0}]}}}}
    newer = {"contexts": {"1d": {"pane_id": 0, "state": {"marks": [{"number": 1, "x": 2.0}]}}}}
    marks["datasets"][new_a] = newer
    atomic_write_json(world["state"] / "marks.json", marks)
    matches = relink.match(relink.find_missing(world["state"]), world["new_root"])
    assert any(c.startswith("marks.json") for c in
               relink.preview_conflicts(matches, world["state"], world["sessions"], world["new_root"]))
    result = relink.apply(matches, world["state"], world["sessions"], world["new_root"], data_root())
    assert any(c.startswith("marks.json") for c in result.conflicts)
    assert _read(world["state"] / "marks.json")["datasets"][new_a] == newer


def _install(tmp_path, monkeypatch, features):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from app._guard import license as licenses, trusted_key
    from app._guard.machine import machine_code

    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(trusted_key, "PUBLIC_KEY_HEX", dev.public_hex(key))
    path = tmp_path / "t.llvkey"
    path.write_bytes(dev.issue(key, machine_code(), "Tester", features))
    return path


def test_relink_tool_needs_the_relink_feature(qapp, tmp_path, monkeypatch, world):
    from app.gui.license_dialog import LicenseDialog
    from app.gui.relink_dialog import open_relink
    from app.localization import get_localization_manager

    import app.gui.relink_dialog as relink_dialog

    # Windows left open by earlier tests belong to other temporary homes.
    monkeypatch.setattr(relink_dialog, "_browser", lambda: None)
    localizer = get_localization_manager()
    assert open_relink(localizer) is None
    dialog = LicenseDialog(localizer)
    assert dialog.relink_button.isHidden()
    assert dialog.install_from(str(tmp_path / "nothing.llvkey"))           # error text
    assert dialog.install_from(str(_install(tmp_path, monkeypatch, ["relink"]))) is None
    assert not dialog.relink_button.isHidden()
    relink_dialog = open_relink(localizer)
    assert relink_dialog is not None and relink_dialog.table.topLevelItemCount() == 3
    relink_dialog.folder.setText(world["new_root"])
    relink_dialog.search(wait=True)
    assert len(relink_dialog.matches) == 3
    result = relink_dialog.apply()
    assert result.moved == 2
    assert relink_dialog.table.topLevelItemCount() == 1                   # only C is left
    assert relink_dialog.undo_last()
    assert relink_dialog.table.topLevelItemCount() == 3
    relink_dialog.close()
    dialog.close()


def test_personal_page_shows_the_lock_and_unlocks_with_a_licence(qapp, tmp_path, monkeypatch):
    from app.gui.personal_page import PersonalPage
    from app.localization import get_localization_manager

    localizer = get_localization_manager()
    page = PersonalPage(localizer, None)
    assert "10" in page.lock_label.text()
    parent = page.tree.topLevelItem(0)
    page.tree.setCurrentItem(parent.child(0))
    assert not page.wheel.isEnabled() and not page.hex_edit.isEnabled() and not page.reset_all.isEnabled()
    assert page.replace_icon_from("zoom", str(tmp_path / "none.svg")) is not None   # icons tab works (file missing)
    from app._guard import license as licenses

    licenses.install(_install(tmp_path, monkeypatch, ["personal_colors"]))
    page._licenses_changed()
    page.tree.setCurrentItem(page.tree.topLevelItem(0).child(0))
    assert page.wheel.isEnabled() and page.hex_edit.isEnabled() and page.reset_all.isEnabled()


def test_viewer_operations_count_as_experience(qapp):
    from tests.real_data import SMALL_FILE

    if not SMALL_FILE.exists():
        pytest.skip("fixture unavailable")
    from app._guard import experience
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.open_file(str(SMALL_FILE))
    qapp.processEvents()
    try:
        assert experience.count() == 0                                   # opening alone does not count
        window.transform_combo.activated.emit(1)
        window.transform_combo.activated.emit(2)                          # same file: still one
        assert experience.count() == 1
        assert window.rect_zoom.user_zoomed == window._note_data_operation
    finally:
        window.close()
