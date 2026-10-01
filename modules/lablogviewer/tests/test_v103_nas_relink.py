"""v1.0.3: writing to shares without hard links, and Re-link that merges instead of refusing.

The "NAS" here is a folder in the temporary home whose os.link fails the way an SMB
mount does (errno 45); nothing touches a real network share.
"""

from __future__ import annotations

import errno
import json
import os
import sys
from pathlib import Path

import pytest

DEVTOOLS = Path(__file__).resolve().parents[2] / "LabLogViewer_Plugins" / "DevTools"
sys.path.insert(0, str(DEVTOOLS))


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture
def no_hard_links(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr(os, "link", refuse)


# -- publishing a finished file ---------------------------------------------------------------------
def test_publish_falls_back_to_rename_on_a_share_without_hard_links(tmp_path, no_hard_links):
    from app.core.hdf5_processing_writer import publish_temporary

    temporary = tmp_path / ".out.hdf5.x.tmp"
    temporary.write_bytes(b"result")
    publish_temporary(temporary, tmp_path / "out.hdf5", overwrite=False)
    assert (tmp_path / "out.hdf5").read_bytes() == b"result"
    assert not temporary.exists()


def test_publish_never_replaces_an_existing_file_without_hard_links(tmp_path, no_hard_links):
    from app.core.hdf5_processing_writer import publish_temporary

    (tmp_path / "out.hdf5").write_bytes(b"someone else's")
    temporary = tmp_path / ".out.hdf5.x.tmp"
    temporary.write_bytes(b"result")
    with pytest.raises(FileExistsError):
        publish_temporary(temporary, tmp_path / "out.hdf5", overwrite=False)
    assert (tmp_path / "out.hdf5").read_bytes() == b"someone else's"


# -- merging records -----------------------------------------------------------------------------------
def _tag_state(explicit=(), auto=(), decided=False, group="."):
    return {"explicit_tags": list(explicit), "auto_tags": list(auto), "suppressed_auto_tags": [],
            "initialized": True, "group_id": group, "flux_default": False, "user_decided": decided}


def test_automatic_tags_at_the_new_path_give_way_to_the_users_old_tags():
    from app.core.record_merge import merge_record

    existing = _tag_state(auto=["BG"], group="Data_0908")
    incoming = _tag_state(explicit=["Sample A"], decided=True, group="old")
    merged, conflict = merge_record("tags.json", "entry_states", existing, incoming)
    assert not conflict
    assert merged["explicit_tags"] == ["Sample A"] and merged["user_decided"]
    assert merged["group_id"] == "Data_0908" and merged["auto_tags"] == ["BG"]


def test_two_user_tag_choices_are_combined_unless_they_contradict():
    from app.core.record_merge import merge_record

    merged, conflict = merge_record("tags.json", "entry_states", _tag_state(["A"], decided=True),
                                    _tag_state(["B"], decided=True))
    assert not conflict and merged["explicit_tags"] == ["A", "B"]
    _merged, conflict = merge_record("tags.json", "entry_states", _tag_state(["Flux"], decided=True),
                                     _tag_state(["BG"], decided=True))
    assert conflict


def test_other_records_merge_without_losing_anything():
    from app.core.record_merge import merge_record

    assert merge_record("comments.json", "by_data", "new note", "old note") == ("new note\n\nold note", False)
    views, conflict = merge_record("named_views.json", "by_data", {"zoom": {"a": 1}}, {"zoom": {"a": 2}})
    assert not conflict and views == {"zoom": {"a": 1}, "zoom (2)": {"a": 2}}
    overlays, _ = merge_record("overlays.json", "by_data", [{"name": "x", "v": 1}], [{"name": "x", "v": 2}])
    assert [o["name"] for o in overlays] == ["x", "x (2)"]
    assert merge_record("viewer_display_states.json", "by_data", {"mode": "2d"}, {"mode": "1d"}) == \
        ({"mode": "1d"}, False)
    assert merge_record("data_fingerprints.json", "", {"size": 1}, {"size": 2}) == ({"size": 1}, False)
    empty = {"contexts": {"1d": {"pane_id": 0, "state": {"marks": [], "annotations": []}}}}
    marked = {"contexts": {"1d": {"pane_id": 0, "state": {"marks": [{"number": 1}]}}}}
    assert merge_record("marks.json", "datasets", empty, marked) == (marked, False)


# -- Re-link on a (simulated) share -------------------------------------------------------------------
@pytest.fixture
def moved_share(tmp_path):
    """Records made under an old mount name; the same files now under a new one, already opened once."""
    from app.core import fingerprint
    from app.core.data_location import data_root
    from app.core.external_state import atomic_write_json

    old_mount = (tmp_path / "Volumes" / "ccuqel").resolve()
    new_mount = (tmp_path / "Volumes" / "ccuqel-1").resolve()
    database_old = old_mount / "Lab data"
    database_new = new_mount / "Lab data"
    names = ["Data_0908/0908 BG.hdf5", "Data_0909/0909 Flux-dep.hdf5"]
    for root in (database_old, database_new):
        for name in names:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(b"x" * 4096)
    for name in names:
        fingerprint.remember(database_old / name, background=False)
    for name in names:
        (database_old / name).unlink()                  # the old mount is gone
    state = data_root() / "state"
    old_states = {name: _tag_state(["Sample A"], decided=True) for name in names}
    new_states = {name: _tag_state(auto=["BG"]) for name in names}   # made when the new path was opened
    atomic_write_json(state / "tags.json", {
        "schema_version": 3, "available_tags": ["Sample A", "BG"], "tag_categories": {},
        "assignments": {}, "entry_states": {str(database_old): old_states, str(database_new): new_states},
        "group_defaults": {}, "recent_queries": []})
    atomic_write_json(state / "stars.json", {"schema_version": 1, "by_database": {
        str(database_old): [names[0]], str(database_new): [names[0]]}})
    atomic_write_json(state / "comments.json", {"schema_version": 2, "by_database": {},
                                               "by_data": {str(database_old / names[1]): "check the dip"}})
    return {"old_mount": old_mount, "new_mount": new_mount, "database_new": database_new,
            "database_old": database_old, "names": names, "state": state}


def test_match_by_path_needs_no_file_reading_and_checks_sizes(moved_share):
    from app.core import relink

    records = relink.find_missing(moved_share["state"])
    assert str(moved_share["old_mount"]) in relink.suggest_old_prefixes(records)
    matches = relink.match_by_prefix(records, moved_share["old_mount"], moved_share["new_mount"])
    assert len(matches) == 2 and {m.confidence for m in matches} == {"path"}
    (moved_share["database_new"] / moved_share["names"][0]).write_bytes(b"different size")
    assert len(relink.match_by_prefix(records, moved_share["old_mount"], moved_share["new_mount"])) == 1


def test_relink_moves_tags_over_automatic_ones_and_reports_no_conflict(moved_share):
    from app.analysis.yig_fitting.core.paths import sub_dir
    from app.core import relink
    from app.core.data_location import data_root

    records = relink.find_missing(moved_share["state"])
    matches = relink.match_by_prefix(records, moved_share["old_mount"], moved_share["new_mount"])
    # The Browser has the parent open; the user picked the new mount as the folder.
    roots = [str(moved_share["database_new"]), str(moved_share["new_mount"])]
    assert relink.preview_conflicts(matches, moved_share["state"], sub_dir("sessions"), roots) == []
    result = relink.apply(matches, moved_share["state"], sub_dir("sessions"), roots, data_root())
    assert result.moved == 2 and result.conflicts == []
    tags = _read(moved_share["state"] / "tags.json")["entry_states"]
    assert str(moved_share["database_old"]) not in tags
    for name in moved_share["names"]:
        assert tags[str(moved_share["database_new"])][name]["explicit_tags"] == ["Sample A"]
    stars = _read(moved_share["state"] / "stars.json")["by_database"]
    assert stars == {str(moved_share["database_new"]): [moved_share["names"][0]]}
    comments = _read(moved_share["state"] / "comments.json")["by_data"]
    assert comments == {str(moved_share["database_new"] / moved_share["names"][1]): "check the dip"}


def test_relink_asks_and_can_overwrite_with_the_old_record(moved_share):
    from app.analysis.yig_fitting.core.paths import sub_dir
    from app.core import relink
    from app.core.data_location import data_root
    from app.core.external_state import atomic_write_json

    tags = _read(moved_share["state"] / "tags.json")
    name = moved_share["names"][0]
    tags["entry_states"][str(moved_share["database_old"])][name] = _tag_state(["BG"], decided=True)
    tags["entry_states"][str(moved_share["database_new"])][name] = _tag_state(["Flux"], decided=True)
    atomic_write_json(moved_share["state"] / "tags.json", tags)
    records = relink.find_missing(moved_share["state"])
    matches = relink.match_by_prefix(records, moved_share["old_mount"], moved_share["new_mount"])
    roots = [str(moved_share["database_new"])]
    assert len(relink.preview_conflicts(matches, moved_share["state"], sub_dir("sessions"), roots)) == 1
    relink.apply(matches, moved_share["state"], sub_dir("sessions"), roots, data_root(), prefer_old=True)
    after = _read(moved_share["state"] / "tags.json")["entry_states"][str(moved_share["database_new"])][name]
    assert after["explicit_tags"] == ["BG"]


def test_relink_dialog_matches_by_path(moved_share, tmp_path, monkeypatch):
    pytest.importorskip("llv_devtools")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from PySide6.QtWidgets import QApplication

    import llv_devtools as dev
    from app._guard import license as licenses, trusted_key
    from app._guard.machine import machine_code
    import app.gui.relink_dialog as relink_dialog
    from app.localization import get_localization_manager

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QApplication.instance() or QApplication([])
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(trusted_key, "PUBLIC_KEY_HEX", dev.public_hex(key))
    path = tmp_path / "t.llvkey"
    path.write_bytes(dev.issue(key, machine_code(), "Tester", ["relink"]))
    licenses.install(path)
    monkeypatch.setattr(relink_dialog, "_browser", lambda: None)
    dialog = relink_dialog.open_relink(get_localization_manager())
    try:
        assert dialog.old_folder.count() >= 1
        dialog.old_folder.setEditText(str(moved_share["old_mount"]))
        dialog.folder.setText(str(moved_share["new_mount"]))
        dialog.match_by_path()
        assert len(dialog.matches) == 2
        assert dialog.conflicts() == []
        result = dialog.apply()
        assert result.moved == 2 and result.conflicts == []
    finally:
        dialog.close()


# -- same-name question before saving ---------------------------------------------------------------
def _answer(label, seen=None):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    def click():
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox)
        if seen is not None:
            seen.extend(b.text().replace("&", "") for b in box.buttons())
        button = next(b for b in box.buttons() if b.text().replace("&", "") == label)
        button.click()

    QTimer.singleShot(0, click)


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_numbered_names(tmp_path):
    from app.gui.save_target import numbered

    (tmp_path / "plot.png").write_bytes(b"1")
    assert numbered(tmp_path / "plot.png").name == "plot (2).png"
    (tmp_path / "plot (2).png").write_bytes(b"2")
    assert numbered(tmp_path / "plot.png").name == "plot (3).png"
    assert numbered(tmp_path / "plot (2).png").name == "plot (3).png"


def test_save_asks_overwrite_keep_both_or_cancel(qapp, tmp_path):
    from app.gui.save_target import confirm_save

    (tmp_path / "0908 4.9~5.1GHz X2.png").write_bytes(b"old")
    typed = str(tmp_path / "0908 4.9~5.1GHz X2")            # no real extension: the filter's is added
    _answer("Keep Both")
    assert confirm_save(None, typed, "PNG image (*.png)") == str(tmp_path / "0908 4.9~5.1GHz X2 (2).png")
    _answer("Overwrite")
    assert confirm_save(None, typed, "PNG image (*.png)") == str(tmp_path / "0908 4.9~5.1GHz X2.png")
    _answer("Cancel")
    assert confirm_save(None, typed, "PNG image (*.png)") == ""
    assert confirm_save(None, str(tmp_path / "new.csv"), "CSV (*.csv)") == str(tmp_path / "new.csv")


def test_every_save_dialog_asks_through_the_translation_wrapper(qapp, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    from app.localization import manager

    existing = tmp_path / "out.csv"
    existing.write_text("old", encoding="utf-8")
    seen = {}

    def fake(*args, **kwargs):
        seen["options"] = kwargs.get("options")
        return str(existing), "CSV (*.csv)"

    _answer("Keep Both")
    path, _selected = manager.ask_before_replacing(fake, [None, "Export", str(existing), "CSV (*.csv)"], {})
    assert path == str(tmp_path / "out (2).csv")
    assert seen["options"] & QFileDialog.Option.DontConfirmOverwrite
    manager.install_dynamic_translation()
    assert "ask_before_replacing" in QFileDialog.getSaveFileName.__code__.co_names


def test_rename_and_new_folder_offer_keep_both(qapp, tmp_path):
    from app.gui.save_target import ask_keep_both

    (tmp_path / "Data_0929").mkdir()
    _answer("Keep Both")
    assert ask_keep_both(None, tmp_path / "Data_0929") == tmp_path / "Data_0929 (2)"
    (tmp_path / "a.hdf5").write_bytes(b"x")
    offered: list[str] = []
    _answer("Cancel", offered)
    assert ask_keep_both(None, tmp_path / "a.hdf5") is None
    assert "Keep Both" in offered and "Overwrite" not in offered      # a data file is never replaced here


def test_whats_new_lists_every_version_since_the_previous_one():
    from app.core.app_update import whats_new_text

    skipped = whats_new_text("1.0.3", "en", since="0.19E")
    assert "## v1.0.3" in skipped and "## v1.01" in skipped
    assert skipped.index("## v1.0.3") < skipped.index("## v1.01")
    only = whats_new_text("1.0.3", "zh", since="1.01")
    assert "## v1.01" not in only and "兩者都保留" in only


def test_three_part_versions_follow_the_older_two_part_ones():
    from app.core.app_update import version_key

    order = ["0.19D", "0.19E", "1.01", "1.0.2", "1.0.3", "1.0.4", "1.0.10", "1.1.0", "2.0.0"]
    assert sorted(order, key=version_key) == order
    assert version_key("1.01") == version_key("1.0.1") and version_key("v1.0.3") == version_key("1.0.3")


def test_update_from_1_01_shows_whats_new_and_keeps_data(tmp_path):
    from app.core.app_update import check

    check(tmp_path, "1.01", had_data=True)                  # the folder as 1.01 left it
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "stars.json").write_text("{}", encoding="utf-8")
    result = check(tmp_path, "1.0.3")
    assert (tmp_path / "state" / "stars.json").read_text(encoding="utf-8") == "{}"
    assert result.show_whats_new and not result.newer_data and result.previous_version == "1.01"
