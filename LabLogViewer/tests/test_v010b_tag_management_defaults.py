"""v0.10B Tag management, smart-default, and GUI acceptance tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.database_scanner import DatabaseScanResult, LogEntry
from app.core.star_store import StarStore
from app.core.tag_store import TagStore, is_flux_named, session_group_id


def _entry(relative_path: str, log_name: str | None = None):
    return SimpleNamespace(relative_path=relative_path, log_name=log_name or Path(relative_path).stem)


def test_session_identity_uses_nearest_structural_data_folder():
    assert session_group_id("project/2026/08/Data_0828/run.hdf5") == "project/2026/08/Data_0828"
    assert session_group_id("CCEP/Data_0911/sub/run.h5") == "CCEP/Data_0911"
    assert session_group_id("project/session/run.hdf5") == "project/session"
    assert session_group_id("run.hdf5") == "."


def test_flux_detection_matches_real_convention_and_rejects_false_positives():
    assert is_flux_named("Data_0828/0828 X1 Flux-dep.hdf5")
    assert is_flux_named("Data_0828/0828 X1 Flux-dep_debg.hdf5")
    assert is_flux_named("Data_0828/run.hdf5", "X2 flux-dep")
    assert not is_flux_named("Data_0828/notFlux-dep.hdf5")
    assert not is_flux_named("Data_0828/flux density.hdf5")
    assert not is_flux_named("Data_Flux/ordinary.hdf5")


def test_session_defaults_propagate_without_row_order_and_suppression_survives(tmp_path):
    path = tmp_path / "tags.json"
    db = str((tmp_path / "db").resolve())
    entries = [_entry(f"2026/Data_0911/{name}.hdf5") for name in ("C", "A", "B")]
    store = TagStore(path, legacy_paths=[])
    store.initialize_entries(db, entries)
    group = session_group_id(entries[1].relative_path)
    store.set_tags(db, entries[1].relative_path, {"LR", "doubleYIG"}, group_id=group)
    assert store.tags_for(db, entries[0].relative_path) == {"LR", "doubleYIG"}
    assert store.tags_for(db, entries[2].relative_path) == {"LR", "doubleYIG"}

    store.set_tags(db, entries[0].relative_path, {"doubleYIG"}, group_id=group)
    restarted = TagStore(path, legacy_paths=[])
    restarted.initialize_entries(db, list(reversed(entries)))
    assert restarted.tags_for(db, entries[0].relative_path) == {"doubleYIG"}
    assert restarted.state_for(db, entries[0].relative_path)["suppressed_auto_tags"] == ["LR"]


def test_flux_suppression_reload_restart_and_manual_reenable(tmp_path):
    path = tmp_path / "tags.json"
    db = str((tmp_path / "db").resolve())
    entry = _entry("Data_0828/0828 X1 Flux-dep_debg.hdf5")
    store = TagStore(path, legacy_paths=[])
    store.initialize_entries(db, [entry])
    assert store.tags_for(db, entry.relative_path) == {"Flux", "De-background"}
    store.set_tags(db, entry.relative_path, set(), flux_default=True)
    store.reload()
    store.initialize_entries(db, [entry])
    assert store.tags_for(db, entry.relative_path) == set()

    restarted = TagStore(path, legacy_paths=[])
    restarted.initialize_entries(db, [entry])
    assert restarted.tags_for(db, entry.relative_path) == set()
    restarted.set_tags(db, entry.relative_path, {"Flux"}, flux_default=True)
    assert TagStore(path, legacy_paths=[]).tags_for(db, entry.relative_path) == {"Flux"}


def test_mixed_defaults_partial_override_manual_addition_and_zero_override(tmp_path):
    path = tmp_path / "tags.json"
    db = str((tmp_path / "db").resolve())
    regular = _entry("Data_0828/A.hdf5")
    flux = _entry("Data_0828/B Flux-dep.hdf5")
    zero = _entry("Data_0828/C.hdf5")
    store = TagStore(path, legacy_paths=[])
    store.create_tag("coherent_EP")
    store.initialize_entries(db, [regular, flux, zero])
    group = session_group_id(regular.relative_path)
    store.set_tags(db, regular.relative_path, {"LR", "doubleYIG"}, group_id=group)
    assert store.tags_for(db, flux.relative_path) == {"LR", "doubleYIG", "Flux"}

    store.set_tags(db, flux.relative_path, {"doubleYIG", "Flux"}, group_id=group, flux_default=True)
    store.set_tags(db, flux.relative_path, {"doubleYIG", "Flux", "coherent_EP"},
                   group_id=group, flux_default=True)
    store.set_tags(db, zero.relative_path, set(), group_id=group)
    restarted = TagStore(path, legacy_paths=[])
    restarted.initialize_entries(db, [zero, regular, flux])
    assert restarted.tags_for(db, flux.relative_path) == {"doubleYIG", "Flux", "coherent_EP"}
    assert restarted.tags_for(db, zero.relative_path) == set()
    assert set(restarted.state_for(db, zero.relative_path)["suppressed_auto_tags"]) == {"LR", "doubleYIG"}


def test_v010a_schema_migrates_assignments_as_authoritative_explicit_state(tmp_path):
    path = tmp_path / "tags.json"
    db = str((tmp_path / "db").resolve())
    path.write_text(json.dumps({
        "schema_version": 1,
        "available_tags": ["LA", "LR", "Mirror", "debug", "Flux", "BG", "singleYIG", "doubleYIG"],
        "assignments": {db: {
            "Data_0901/A.hdf5": ["LR"],
            "Data_0901/B.hdf5": ["doubleYIG"],
        }},
        "migration_report": {},
    }))
    store = TagStore(path, legacy_paths=[])
    store.initialize_entries(db, [_entry("Data_0901/B.hdf5"), _entry("Data_0901/A.hdf5")])
    assert store.tags_for(db, "Data_0901/A.hdf5") == {"LR"}
    assert store.tags_for(db, "Data_0901/B.hdf5") == {"doubleYIG"}
    saved = json.loads(path.read_text())
    assert saved["schema_version"] == 4
    assert saved["migration_report"]["schema_migrated_from"] == 1
    assert path.with_name(path.name + ".pre-taxonomy-v4.bak").is_file()


def test_create_rename_delete_assigned_unicode_and_persistence(tmp_path):
    path = tmp_path / "tags.json"
    db = str((tmp_path / "db").resolve())
    store = TagStore(path, legacy_paths=[])
    assert store.create_tag("測試")
    assert not store.create_tag(" 測試 ")
    store.set_tags(db, "A/same.hdf5", {"測試"})
    store.set_tags(db, "B/same.hdf5", {"LR"})
    assert store.assignment_count("測試") == 1
    assert store.rename_tag("測試", "實驗")
    assert not store.rename_tag("實驗", "LR")
    assert store.tags_for(db, "A/same.hdf5") == {"實驗"}
    assert store.tags_for(db, "B/same.hdf5") == {"LR"}
    assert store.delete_tag("實驗")
    restarted = TagStore(path, legacy_paths=[])
    assert "實驗" not in restarted.list_tags()
    assert restarted.tags_for(db, "A/same.hdf5") == set()


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _log(relative: str, name: str) -> LogEntry:
    return LogEntry("/tmp/" + Path(relative).name, relative, Path(relative).name,
                    name, "ok", None, 10, 1.0)


def test_selected_panel_quick_preview_and_table_stay_synchronized(qapp, tmp_path, monkeypatch):
    import app.gui.browser_window as browser_module
    from app.gui.browser_window import BrowserWindow

    first = _log("Data_0911/A.hdf5", "Entry A")
    second = _log("Data_0911/B.hdf5", "Entry B")
    db = str(tmp_path.resolve())
    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    window = BrowserWindow(StarStore(tmp_path / "stars.json"), store)
    result = DatabaseScanResult(db, db, [second, first])
    window.scan_result = result
    window._populate_folder_tree(result)
    window.folder_tree.setCurrentItem(window._folder_items[("Data_0911",)])

    class AcceptedDialog:
        Accepted = 1
        pending_tags = []
        def __init__(self, *args, **kwargs): pass
        def exec(self): return 1
        def selected_tags(self): return {"LR", "doubleYIG"}

    monkeypatch.setattr(browser_module, "TagAssignmentDialog", AcceptedDialog)
    item = window.data_list.topLevelItem(0)
    window.data_list.setCurrentItem(item)
    window.edit_tags_button.click()
    assert window.selected_log_label.text() == "Entry A"
    assert [window.tags_tree.topLevelItem(i).text(0) for i in range(2)] == ["Level", "Other"]
    assert window.selected_tags_label.text() == "Tags: Level: LR; Other: doubleYIG"
    assert item.text(4) == "Level: LR; Other: doubleYIG"
    inherited = window.data_list.topLevelItem(1)
    assert inherited.text(4) == "Level: LR; Other: doubleYIG"
    window.data_list.setCurrentItem(inherited)
    assert window.selected_log_label.text() == "Entry B"
    assert window.selected_tags_label.text() == "Tags: Level: LR; Other: doubleYIG"
    window.close()


def test_management_dialog_cancel_delete_and_assigned_rename(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from app.gui.tag_management_dialog import TagManagementDialog

    db = str((tmp_path / "db").resolve())
    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    store.create_tag("custom")
    store.set_tags(db, "Data_0901/run.hdf5", {"custom"})
    dialog = TagManagementDialog(store)
    assert dialog.rename_tag("custom", "renamed") == "renamed"
    assert store.tags_for(db, "Data_0901/run.hdf5") == {"renamed"}
    assert dialog.tag_list.currentItem().data(256) == "renamed"
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Cancel)
    dialog._confirm_delete()
    assert "renamed" in store.list_tags()
    assert dialog.delete_tag("renamed")
    assert store.tags_for(db, "Data_0901/run.hdf5") == set()
    dialog.close()
