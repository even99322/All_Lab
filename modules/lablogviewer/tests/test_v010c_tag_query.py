"""v0.10C Boolean Tag retrieval and lightweight Recent Query tests."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.database_scanner import DatabaseScanResult, LogEntry
from app.core.star_store import StarStore
from app.core.tag_query import matches_tag_query, query_entries
from app.core.tag_store import RECENT_QUERY_MAX_AGE_SECONDS, TagStore


def _entry(relative: str, name: str | None = None) -> LogEntry:
    return LogEntry(
        f"/tmp/{relative}", relative, Path(relative).name,
        name or Path(relative).stem, "ok", None, 10, 1.0,
    )


def _controlled_store(tmp_path):
    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    db = str((tmp_path / "database").resolve())
    entries = [
        _entry("2026/Data_0828/same.hdf5", "A"),
        _entry("2026/Data_0831/same.hdf5", "B"),
        _entry("CCEP/Data_0901/C.hdf5", "C"),
        _entry("CCEP/Data_0911/D.hdf5", "D"),
    ]
    store.initialize_entries(db, entries)
    for entry, tags in zip(entries, ({"Flux"}, {"LR"}, {"Flux", "LR"}, {"Flux", "LR", "doubleYIG"})):
        store.set_tags(db, entry.relative_path, tags)
    return store, db, entries


def test_boolean_single_or_and_three_tag_queries_cross_sessions_and_duplicates(tmp_path):
    store, db, entries = _controlled_store(tmp_path)
    lookup = lambda entry: store.tags_for(db, entry.relative_path)
    assert query_entries(entries, {"Flux"}, "OR", lookup) == [entries[0], entries[2], entries[3]]
    assert query_entries(entries, {"Flux", "LR"}, "OR", lookup) == entries
    assert query_entries(entries, {"Flux", "LR"}, "AND", lookup) == [entries[2], entries[3]]
    assert query_entries(entries, {"Flux", "LR", "doubleYIG"}, "AND", lookup) == [entries[3]]
    assert not matches_tag_query({"Flux", "LR"}, {"Flux", "LR", "doubleYIG"}, "AND")
    assert query_entries(entries, set(), "AND", lookup) == []
    assert query_entries(entries, {"BG"}, "OR", lookup) == []
    assert entries[0].file_name == entries[1].file_name
    assert entries[0].relative_path != entries[1].relative_path


def test_query_uses_effective_state_after_flux_suppression(tmp_path):
    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    db = str((tmp_path / "database").resolve())
    flux = _entry("Data_0828/X1 Flux-dep.hdf5")
    store.initialize_entries(db, [flux])
    lookup = lambda entry: store.tags_for(db, entry.relative_path)
    assert query_entries([flux], {"Flux"}, "AND", lookup) == [flux]
    store.set_tags(db, flux.relative_path, set(), flux_default=True)
    assert query_entries([flux], {"Flux"}, "AND", lookup) == []


def test_recent_persistence_deduplication_order_limit_and_expiration(tmp_path):
    path = tmp_path / "tags.json"
    store = TagStore(path, legacy_paths=[])
    now = time.time()
    store.record_query({"LR", "Flux"}, "AND", now=now - 30)
    store.record_query({"BG"}, "OR", now=now - 20)
    store.record_query({"Flux", "LR"}, "AND", now=now - 10)
    recent = TagStore(path, legacy_paths=[]).recent_queries(now=now)
    assert len(recent) == 2
    assert recent[0]["tags"] == ["LR", "Flux"]
    assert recent[0]["last_used"] == now - 10

    for index, tag in enumerate(("LA", "Mirror", "Debug", "singleYIG", "doubleYIG")):
        store.record_query({tag}, "OR", now=now + index)
    recent = store.recent_queries(now=now + 5)
    assert len(recent) == 5
    assert not any(query["tags"] == ["BG"] for query in recent)

    raw = json.loads(path.read_text())
    raw["recent_queries"].append({
        "tags": ["BG"], "mode": "AND",
        "last_used": now - RECENT_QUERY_MAX_AGE_SECONDS - 1,
    })
    path.write_text(json.dumps(raw))
    restarted = TagStore(path, legacy_paths=[])
    assert not any(query["tags"] == ["BG"] and query["mode"] == "AND"
                   for query in restarted.recent_queries(now=now))


def test_clear_recent_preserves_tags_assignments_defaults_and_suppression(tmp_path):
    path = tmp_path / "tags.json"
    db = str((tmp_path / "database").resolve())
    flux = _entry("Data_0828/X1 Flux-dep.hdf5")
    store = TagStore(path, legacy_paths=[])
    store.initialize_entries(db, [flux])
    store.set_tags(db, flux.relative_path, set(), flux_default=True)
    store.record_query({"Flux"}, "AND")
    before = json.loads(path.read_text())
    store.clear_recent_queries()
    after = json.loads(path.read_text())
    assert store.recent_queries() == []
    for key in ("available_tags", "assignments", "entry_states", "group_defaults"):
        assert after[key] == before[key]


def test_recent_query_tag_rename_migrates_and_delete_removes_whole_query(tmp_path):
    path = tmp_path / "tags.json"
    store = TagStore(path, legacy_paths=[])
    store.create_tag("custom")
    store.record_query({"LR", "custom"}, "AND")
    assert store.rename_tag("custom", "renamed")
    assert store.recent_queries()[0]["tags"] == ["LR", "renamed"]
    assert store.delete_tag("renamed")
    assert store.recent_queries() == []
    assert TagStore(path, legacy_paths=[]).recent_queries() == []


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _window(tmp_path, monkeypatch):
    from app.gui.browser_window import BrowserWindow
    store, db, entries = _controlled_store(tmp_path)
    window = BrowserWindow(StarStore(tmp_path / "stars.json"), store)
    result = DatabaseScanResult(db, db, entries)
    window.scan_result = result
    window._populate_folder_tree(result)
    monkeypatch.setattr(window, "_start_preview", lambda entry: setattr(window, "previewed_entry", entry))
    return window, store, db, entries


def test_query_dialog_scroll_modes_empty_validation_recent_and_clear(qapp, tmp_path):
    from app.gui.tag_query_dialog import TagQueryDialog
    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    for index in range(20):
        store.create_tag(f"tag-{index}")
    store.record_query({"LR", "Flux"}, "OR")
    dialog = TagQueryDialog(store)
    assert dialog.scroll_area.widgetResizable()
    assert not dialog.retrieve_button.isEnabled()
    item = dialog.recent_list.item(0)
    dialog._restore_recent_item(item)
    assert dialog.selected_tags() == {"LR", "Flux"}
    assert dialog.query_mode() == "OR"
    assert dialog.retrieve_button.isEnabled()
    dialog.clear_recent_button.click()
    assert store.recent_queries() == []
    assert dialog.recent_list.item(0).text() == "No recent queries"
    dialog.close()


def test_query_result_context_count_selection_preview_star_and_open(qapp, tmp_path, monkeypatch):
    window, store, db, entries = _window(tmp_path, monkeypatch)
    window._run_tag_query({"Flux", "LR"}, "AND")
    assert window.data_group.title() == "Query Results"
    assert window.data_list.topLevelItemCount() == 2
    assert window.query_status_label.text().startswith("2 results")
    assert window.data_list.columnCount() == 6
    assert window.data_list.topLevelItem(0).text(2) == "CCEP/Data_0901"
    item = window.data_list.topLevelItem(0)
    entry = item.data(0, 256)
    window.data_list.setCurrentItem(item)
    assert window.previewed_entry is entry
    assert window.selected_log_label.text() == entry.log_name
    assert window.selected_tags_label.text() == "Tags: Level: LR; Data Analysis: Flux"
    window._toggle_star(item, entry)
    assert window.star_store.is_starred(db, entry.relative_path)
    opened = []
    monkeypatch.setattr(window, "_open_viewer_for", opened.append)
    window._on_item_double_clicked(item, 0)
    assert opened == [entry]
    window.close()


def test_tag_edit_removes_stale_active_result_and_back_restores_folder(qapp, tmp_path, monkeypatch):
    import app.gui.browser_window as browser_module
    window, store, db, entries = _window(tmp_path, monkeypatch)
    window._run_tag_query({"Flux", "LR"}, "AND")
    item = window.data_list.topLevelItem(0)
    entry = item.data(0, 256)
    window.data_list.setCurrentItem(item)

    class RemoveLR:
        Accepted = 1
        pending_tags = []
        def __init__(self, *args, **kwargs): pass
        def exec(self): return 1
        def selected_tags(self): return {"Flux"}

    monkeypatch.setattr(browser_module, "TagAssignmentDialog", RemoveLR)
    window._edit_tags(item, entry)
    assert window.data_list.topLevelItemCount() == 1
    assert all(window.data_list.topLevelItem(i).data(0, 256) is not entry
               for i in range(window.data_list.topLevelItemCount()))
    store.set_tags(db, entry.relative_path, {"Flux", "LR"})
    window._run_tag_query({"Flux", "LR"}, "AND", record=False)
    assert window.data_list.topLevelItemCount() == 2
    window._clear_query()
    assert window.data_group.title() == "Data in Folder"
    assert not window.clear_query_button.isEnabled()
    window.close()


def test_zero_match_query_has_clear_empty_state(qapp, tmp_path, monkeypatch):
    window, _, _, _ = _window(tmp_path, monkeypatch)
    window._run_tag_query({"BG"}, "OR")
    assert window.data_list.topLevelItemCount() == 0
    assert not window.no_results_label.isHidden()
    assert window.no_results_label.text() == "No matching data."
    assert window.query_status_label.text().startswith("0 results")
    window.close()
