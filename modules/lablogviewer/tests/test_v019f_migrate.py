"""v0.19F Data Transfer (licence feature "migrate", inside LabLogViewer; was the separate
Migrator plugin): records written by LabLogViewer's own stores, files moved to a simulated
shared folder (a local folder, never a real NAS), records read back from the new data folder."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from app.core import migrate as core


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def _measurement(path: Path, seed: int, size: int = 300_000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    block = bytes((seed * 31 + i * 7) % 251 for i in range(4096))
    path.write_bytes((block * (size // len(block) + 1))[:size])
    return path


def _stores(state: Path):
    from app.core.comment_store import CommentStore
    from app.core.mark_store import MarkStore
    from app.core.named_view_store import NamedViewStore
    from app.core.star_store import StarStore
    from app.core.tag_store import TagStore

    return (StarStore(state / "stars.json"), TagStore(state / "tags.json", legacy_paths=[]),
            CommentStore(state / "comments.json"), MarkStore(state / "marks.json"),
            NamedViewStore(state / "named_views.json"))


def _old_world(tmp_path: Path):
    """An old data folder with records on three files, then the files 'moved'."""
    old_db = tmp_path / "old_data" / "Data"
    a = _measurement(old_db / "2026" / "08" / "Data_0828" / "0828 A.hdf5", 1)
    b = _measurement(old_db / "2026" / "08" / "Data_0828" / "0828 B.hdf5", 2)
    c = _measurement(old_db / "2026" / "08" / "Data_0828" / "0828 C.hdf5", 3)
    old = tmp_path / "old_folder" / "LabLogViewerData"
    state = old / "state"
    stars, tags, comments, marks, views = _stores(state)
    db = core.identity_of(old_db)
    stars.set_starred(db, "2026/08/Data_0828/0828 A.hdf5", True)
    stars.set_starred(db, "2026/08/Data_0828/0828 C.hdf5", True)
    tags.set_tags(db, "2026/08/Data_0828/0828 A.hdf5", ["RSMEP", "Best Data"])
    comments.set_for_source(a, "resonance at 5.02 GHz", database_id=db, relative_path="2026/08/Data_0828/0828 A.hdf5")
    marks.save(core.identity_of(a), 1, ("1D", "S21"), {"marks": [{"x": 5.02}]})
    views.save(core.identity_of(b), "paper figure", {"transform": "dB"})
    # LabLogViewer fingerprints files it opens; A was opened, B was not
    prints = {core.identity_of(a): {"size": a.stat().st_size, "sample_sha256": core.sampled_sha256(a),
                                    "sha256": core.full_sha256(a), "file_name": a.name}}
    (state / "data_fingerprints.json").write_text(json.dumps(prints), encoding="utf-8")
    sessions = old / "fitting" / "sessions"
    sessions.mkdir(parents=True)
    session = {"version": 3, "data": {"path": core.identity_of(a), "slice": 50}, "last_dir": str(a.parent)}
    (sessions / core.session_name(core.identity_of(a))).write_text(json.dumps(session), encoding="utf-8")
    (sessions / "formulas").mkdir()
    (sessions / "formulas" / "my_model.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    (sessions / "formula_library.json").write_text(json.dumps({"version": 1, "options": {}, "formulas": [
        {"id": "m1", "title": "Mine", "file": "formulas/my_model.py", "func": "f"}]}), encoding="utf-8")
    (state / "settings.json").write_text(json.dumps({"schema_version": 1, "network": {"user_name": "Lab PC A"},
                                                     "general": {"language": "zh_TW"}}), encoding="utf-8")
    # the unified database (a folder standing in for shared storage): A renamed, B copied, C gone
    shared = tmp_path / "shared" / "Lab database"
    new_a = shared / "RSMEP" / "0828" / "A renamed.hdf5"
    new_b = shared / "RSMEP" / "0828" / "0828 B.hdf5"
    new_a.parent.mkdir(parents=True)
    shutil.copy2(a, new_a)
    shutil.copy2(b, new_b)
    _measurement(shared / "other" / "unrelated.hdf5", 9)
    a.unlink()                                  # A only exists in the shared folder now
    c.unlink()                                  # C is lost
    return old, shared, db, new_a, new_b


def test_compare_matches_by_content(tmp_path):
    old, shared, _db, new_a, new_b = _old_world(tmp_path)
    target = tmp_path / "new" / "LabLogViewerData"
    plan = core.compare(core.open_folder(old), core.open_folder(target, create=True), [shared])
    by_name = {Path(p.old.identity).name: p for p in plan.pairs}
    assert by_name["0828 A.hdf5"].status == "exact" and by_name["0828 A.hdf5"].new_path == str(new_a)
    assert by_name["0828 B.hdf5"].status == "exact"            # fingerprinted now: the old file is still there
    assert by_name["0828 C.hdf5"].status == "missing" and not by_name["0828 C.hdf5"].selected
    assert {"star", "tags", "comment", "marks", "YIG session"} <= by_name["0828 A.hdf5"].old.records
    assert not target.exists()                                  # comparing writes nothing


def test_migrate_then_lablogviewer_reads_records_at_the_new_paths(tmp_path):
    old, shared, db, new_a, new_b = _old_world(tmp_path)
    before = {p: p.read_bytes() for p in old.rglob("*") if p.is_file()}
    target_root = tmp_path / "new" / "LabLogViewerData"
    stars, _tags, comments, _marks, _views = _stores(target_root / "state")
    shared_db = core.identity_of(shared)
    stars.set_starred(shared_db, "other/unrelated.hdf5", True)                    # already in the new folder
    comments.set_for_source(new_b, "comment made in the new version", database_id=shared_db,
                            relative_path="RSMEP/0828/0828 B.hdf5")
    (target_root / "state" / "settings.json").write_text(json.dumps({"schema_version": 1,
                                                                     "general": {"language": "en"}}))
    plan = core.compare(core.open_folder(old), core.open_folder(target_root), [shared])
    report = core.migrate(plan)

    stars, tags, comments, marks, views = _stores(target_root / "state")
    rel_a = "RSMEP/0828/A renamed.hdf5"
    assert stars.is_starred(shared_db, rel_a) and stars.is_starred(shared_db, "other/unrelated.hdf5")
    assert stars.is_starred(db, "2026/08/Data_0828/0828 C.hdf5")                # not found: kept under the old path
    assert tags.tags_for(shared_db, rel_a) == {"RSMEP", "Best Data"}
    assert comments.get_for_source(new_a, database_id=shared_db, relative_path=rel_a) == "resonance at 5.02 GHz"
    assert marks.get(core.identity_of(new_a), 1, ("1D", "S21")) == {"marks": [{"x": 5.02}]}
    assert views.list_names(core.identity_of(new_b)) == ["paper figure"]
    # conflict: the new folder's own comment on B wins by default, and is reported
    assert comments.get_for_source(new_b, database_id=shared_db,
                                   relative_path="RSMEP/0828/0828 B.hdf5") == "comment made in the new version"
    # YIG session follows the file (renamed to the new identity, path inside rewritten)
    session = target_root / "fitting" / "sessions" / core.session_name(core.identity_of(new_a))
    assert json.loads(session.read_text())["data"]["path"] == core.identity_of(new_a)
    library = json.loads((target_root / "fitting" / "sessions" / "formula_library.json").read_text())
    assert [f["id"] for f in library["formulas"]] == ["m1"]
    assert (target_root / "fitting" / "sessions" / "formulas" / "my_model.py").is_file()
    settings = json.loads((target_root / "state" / "settings.json").read_text())
    assert settings["general"]["language"] == "en" and settings["network"]["user_name"] == "Lab PC A"
    assert report.moved_files == 2 and (report.backup / "report.md").is_file()
    assert "0828 C.hdf5" in (report.backup / "report.md").read_text()
    # the old folder is untouched
    assert before == {p: p.read_bytes() for p in old.rglob("*") if p.is_file()}

    # undo puts the new folder back exactly
    core.undo(core.open_folder(target_root), report.backup)
    stars, tags, comments, _marks, _views = _stores(target_root / "state")
    assert not stars.is_starred(shared_db, rel_a) and stars.is_starred(shared_db, "other/unrelated.hdf5")
    assert tags.tags_for(shared_db, rel_a) == set()
    assert not (target_root / "fitting").exists()
    assert core.list_backups(core.open_folder(target_root)) == []


def test_prefer_old_and_guess_and_newer_formats(tmp_path):
    old, shared, _db, new_a, new_b = _old_world(tmp_path)
    target_root = tmp_path / "new" / "LabLogViewerData"
    _stars, _tags, comments, _marks, _views = _stores(target_root / "state")
    shared_db = core.identity_of(shared)
    comments.set_for_source(new_a, "newer", database_id=shared_db, relative_path="RSMEP/0828/A renamed.hdf5")
    # a file edited after it was copied: no content match, but same name and size -> a guess, not ticked
    data = bytearray(new_b.read_bytes())
    data[1000] ^= 0xFF
    new_b.write_bytes(bytes(data))
    plan = core.compare(core.open_folder(old), core.open_folder(target_root), [shared])
    guess = next(p for p in plan.pairs if Path(p.old.identity).name == "0828 B.hdf5")
    assert guess.status == "guess" and not guess.selected
    guess.selected = True
    (old / "state" / "overlays.json").write_text(json.dumps({"schema_version": 99, "by_data": {}}))
    report = core.migrate(plan, core.Options(prefer_old=True))
    comments.reload()
    assert comments.get_for_source(new_a, database_id=shared_db, relative_path="RSMEP/0828/A renamed.hdf5") \
        == "resonance at 5.02 GHz"                              # the old comment on A was preferred
    _stars, _tags, _comments, _marks, views = _stores(target_root / "state")
    assert views.list_names(core.identity_of(new_b)) == ["paper figure"]   # the ticked guess moved B's view
    assert any("overlays.json" in s for s in report.skipped)    # a format newer than the Migrator knows


def test_refuses_same_folder_and_non_data_folders(tmp_path):
    old, shared, *_ = _old_world(tmp_path)
    with pytest.raises(core.MigrationError):
        core.open_folder(tmp_path / "shared")
    plan = core.compare(core.open_folder(old), core.open_folder(old), [shared])
    with pytest.raises(core.MigrationError):
        core.migrate(plan)


def test_window_compares_and_migrates(tmp_path):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from app.gui.migrate_dialog import MigratorWindow

    old, shared, _db, new_a, _new_b = _old_world(tmp_path)
    target_root = tmp_path / "new" / "LabLogViewerData"
    window = MigratorWindow("zh", target=str(tmp_path / "new" / "LabLogViewerData"), restart=None)
    window.source_edit.setText(str(old))
    window.describe_source()
    assert "星號" in window.source_info.text()
    window.target_edit.setText(str(target_root))
    window.roots.addItem(str(shared))
    window.compare()
    end = time.time() + 30
    while window.plan is None and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert window.table.topLevelItemCount() == len(window.plan.pairs) == 3
    assert "內容相同" in window.counts.text()
    window.toggle_language()
    assert "same content" in window.counts.text()
    report = window.migrate(confirmed=True)
    assert report is not None and report.moved_files == 2
    assert window.undo(confirmed=True)
    window.close()


def test_default_data_folder_follows_lablogviewer(fake_home):
    assert core.default_data_folder() == fake_home / "Documents" / "LabLogViewerData" or os.name == "nt"
    moved = fake_home / "elsewhere" / "LabLogViewerData"
    moved.mkdir(parents=True)
    pointer = (fake_home / "AppData" / "Roaming" if os.name == "nt" else
               fake_home / "Library" / "Application Support" if sys.platform == "darwin" else fake_home / ".config")
    (pointer / "LabLogViewer").mkdir(parents=True)
    (pointer / "LabLogViewer" / "data_location.json").write_text(json.dumps({"path": str(moved)}))
    assert core.default_data_folder() == moved


def _labber_like(path: Path, channels: list[str], shape: tuple, seed: float) -> Path:
    import h5py
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as handle:
        names = np.array([(c.encode(), b"") for c in channels], dtype=[("name", "S32"), ("unit", "S8")])
        handle.create_dataset("Data/Channel names", data=names)
        handle.create_dataset("Data/Data", data=np.full(shape, seed))
    return path


def test_manual_pair_compares_layout(tmp_path):
    old = core.OldFile(core.identity_of(_labber_like(tmp_path / "old.hdf5", ["Freq", "S21"], (201, 2, 5), 1.0)),
                       {"star", "marks", "comment"})
    same = _labber_like(tmp_path / "copy.hdf5", ["Freq", "S21"], (201, 2, 5), 1.0)
    layout = _labber_like(tmp_path / "rerun.hdf5", ["Freq", "S21"], (201, 2, 5), 2.0)
    other = _labber_like(tmp_path / "other.hdf5", ["Freq", "S31"], (401, 2, 5), 1.0)
    assert core.compare_files(old, same)[0] == "same content"
    assert core.compare_files(old, layout)[0] == "same layout"
    similarity, reason = core.compare_files(old, other)
    assert similarity == "different" and "channels" in reason and "sweep" in reason
    pair = core.Pair(old, "missing", None, False)
    core.set_manual(pair, layout)
    assert pair.kinds == {"star", "marks", "comment"} and pair.similarity == "same layout"
    with pytest.raises(core.MigrationError):
        core.set_manual(pair, other, {"star", "marks"})            # Marks cannot follow a different file
    core.set_manual(pair, other)
    assert pair.kinds == {"star", "comment"} and pair.status == "manual"
    core.clear_manual(pair)
    assert pair.status == "missing" and pair.new_path is None and pair.kinds is None


def test_manual_pair_to_a_different_file_moves_only_star_tags_comment(tmp_path):
    old, shared, db, new_a, _new_b = _old_world(tmp_path)
    target_root = tmp_path / "new" / "LabLogViewerData"
    plan = core.compare(core.open_folder(old), core.open_folder(target_root, create=True), [shared])
    pair_a = next(p for p in plan.pairs if Path(p.old.identity).name == "0828 A.hdf5")
    unrelated = shared / "other" / "unrelated.hdf5"
    core.set_manual(pair_a, unrelated)                               # the user insists: A is "unrelated"
    assert pair_a.similarity == "different" and pair_a.kinds == {"star", "tags", "comment"}
    report = core.migrate(plan)
    stars, tags, comments, marks, _views = _stores(target_root / "state")
    shared_db = core.identity_of(shared)
    assert stars.is_starred(shared_db, "other/unrelated.hdf5")
    assert tags.tags_for(shared_db, "other/unrelated.hdf5") == {"RSMEP", "Best Data"}
    assert comments.get_for_source(unrelated, database_id=shared_db,
                                   relative_path="other/unrelated.hdf5") == "resonance at 5.02 GHz"
    old_a = str(Path(db) / "2026/08/Data_0828/0828 A.hdf5")
    assert marks.get(core.identity_of(unrelated), 1, ("1D", "S21")) is None     # Marks stayed with the old path
    assert marks.get(old_a, 1, ("1D", "S21")) == {"marks": [{"x": 5.02}]}
    sessions = target_root / "fitting" / "sessions"
    assert not (sessions / core.session_name(core.identity_of(unrelated))).exists()
    prints = json.loads((target_root / "state" / "data_fingerprints.json").read_text())
    assert core.identity_of(unrelated) not in prints                            # A's fingerprint is not A's content
    text = (report.backup / "report.md").read_text()
    assert "Manual matches" in text and "responsibility" in text


def test_window_manual_pair_needs_the_disclaimer(tmp_path):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from app.gui.migrate_dialog import MigratorWindow

    old, shared, _db, _new_a, _new_b = _old_world(tmp_path)
    window = MigratorWindow("zh", target=str(tmp_path / "new" / "LabLogViewerData"), restart=None)
    window.source_edit.setText(str(old))
    window.target_edit.setText(str(tmp_path / "new" / "LabLogViewerData"))
    window.roots.addItem(str(shared))
    window.compare()
    end = time.time() + 30
    while window.plan is None and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    row = next(window.table.topLevelItem(i) for i in range(window.table.topLevelItemCount())
               if window.table.topLevelItem(i).text(0).endswith("0828 A.hdf5"))
    seen = {}

    def answer(dialog):
        seen["ok_before"] = dialog.ok_enabled()
        seen["kinds"] = dialog.kinds()
        seen["disclaimer"] = dialog.findChild(QLabel_class(), "migratorDisclaimer").text()
        dialog.accept_box.setChecked(True)
        seen["ok_after"] = dialog.ok_enabled()
        return True

    assert window.choose_manual(row, str(shared / "other" / "unrelated.hdf5"), answer)
    assert seen["ok_before"] is False and seen["ok_after"] is True
    assert seen["kinds"] == {"star", "tags", "comment"} and "免責聲明" in seen["disclaimer"]
    row = next(window.table.topLevelItem(i) for i in range(window.table.topLevelItemCount())
               if window.table.topLevelItem(i).text(0).endswith("0828 A.hdf5"))
    assert "手動指定" in row.text(2) and "差異過大" in row.text(2)
    window.close()


def QLabel_class():
    from PySide6.QtWidgets import QLabel

    return QLabel


def test_transfer_window_is_licensed_and_restarts_after_migrating(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from app._guard import gate, license as licenses
    from app.gui.license_dialog import LicenseDialog
    from app.gui.migrate_dialog import MigratorWindow
    from app.localization import initialize_localization

    localizer = initialize_localization(app)
    monkeypatch.setattr(licenses, "feature_granted", lambda feature: None)
    gate.invalidate()
    dialog = LicenseDialog(localizer)
    assert dialog.migrate_button.isHidden()                               # no licence: no button
    monkeypatch.setattr(licenses, "feature_granted",
                        lambda feature: object() if feature == "migrate" else None)
    gate.invalidate()
    dialog.refresh()
    assert not dialog.migrate_button.isHidden()
    dialog.close()
    old, shared, _db, _a, _b = _old_world(tmp_path)
    restarted = []
    window = MigratorWindow("en", target=str(tmp_path / "new" / "LabLogViewerData"),
                            restart=lambda: restarted.append(True))
    assert window.target_edit.isReadOnly() and window.target_browse.isHidden()
    window.source_edit.setText(str(old))
    window.roots.addItem(str(shared))
    window.compare()
    end = time.time() + 30
    while window.plan is None and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert window.migrate(confirmed=True) is not None and restarted == [True]
    window.close()
