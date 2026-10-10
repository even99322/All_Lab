"""GUI acceptance tests for v0.10A Tag assignment."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.database_scanner import DatabaseScanResult, LogEntry
from app.core.star_store import StarStore
from app.core.tag_store import DEFAULT_TAGS, TagStore


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _entry(path: str = "/tmp/run.hdf5", relative: str = "folder/run.hdf5") -> LogEntry:
    return LogEntry(path, relative, Path(path).name, Path(path).stem, "ok", None, 10, 1.0)


def _window(tmp_path, entry=None):
    from app.gui.browser_window import BrowserWindow

    star_store = StarStore(tmp_path / "stars.json")
    tag_store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    window = BrowserWindow(star_store=star_store, tag_store=tag_store)
    entry = entry or _entry()
    result = DatabaseScanResult(str(tmp_path.resolve()), str(tmp_path.resolve()), [entry])
    window.scan_result = result
    window._populate_folder_tree(result)
    if entry.folder_parts:
        window.folder_tree.setCurrentItem(window._folder_items[entry.folder_parts])
    return window


def test_left_tag_area_shows_tags_of_selected_data(qapp, tmp_path):
    window = _window(tmp_path)
    assert window.tags_tree.topLevelItem(0).text(0) == "No tags"
    item = window.data_list.topLevelItem(0)
    window.data_list.setCurrentItem(item)
    labels = [window.tags_tree.topLevelItem(i).text(0) for i in range(window.tags_tree.topLevelItemCount())]
    assert labels == ["No tags"]
    assert window.selected_log_label.text() == "run"
    assert window.edit_tags_button.text() == "Edit Tags..."
    window.close()


def test_assignment_dialog_checks_multiple_and_scrolls(qapp, tmp_path):
    from app.gui.tag_assignment_dialog import TagAssignmentDialog

    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    for index in range(30):
        store.create_tag(f"tag-{index:02d}")
    dialog = TagAssignmentDialog("run", store, {"LR", "Debug"})
    assert dialog.scroll_area.widgetResizable()
    assert dialog.tag_checkboxes["LR"].isChecked()
    assert dialog.tag_checkboxes["Debug"].isChecked()
    assert not dialog.tag_checkboxes["Flux"].isChecked()
    assert len(dialog.tag_checkboxes) == len(DEFAULT_TAGS) + 30
    dialog.reject()


def test_dialog_new_tag_validation_unicode_and_cancel(qapp, tmp_path):
    from app.gui.tag_assignment_dialog import TagAssignmentDialog

    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    dialog = TagAssignmentDialog("run", store, set())
    assert dialog.create_tag("   ") == "empty"
    assert dialog.create_tag("Flux") == "duplicate"
    assert dialog.create_tag("  測試  ") == "created"
    assert dialog.create_tag("測試") == "duplicate"
    assert "測試" in dialog.selected_tags()
    dialog.reject()
    assert "測試" not in store.list_tags()


def test_toolbar_assignment_save_updates_column_and_persists(qapp, tmp_path, monkeypatch):
    import app.gui.browser_window as browser_module

    class AcceptedDialog:
        Accepted = 1
        pending_tags = ["coherent_EP"]
        def __init__(self, *args, **kwargs): pass
        def exec(self): return self.Accepted
        def selected_tags(self): return {"LR", "Debug", "coherent_EP"}

    monkeypatch.setattr(browser_module, "TagAssignmentDialog", AcceptedDialog)
    window = _window(tmp_path)
    item = window.data_list.topLevelItem(0)
    window.data_list.setCurrentItem(item)
    assert window.tag_button.isEnabled()
    window.tag_button.click()
    assert item.text(4) == "Level: LR; Other: Debug, coherent_EP"
    assert window.selected_tags_label.text() == "Tags: Level: LR; Other: Debug, coherent_EP"
    restarted = TagStore(tmp_path / "tags.json", legacy_paths=[])
    assert restarted.tags_for(str(tmp_path.resolve()), "folder/run.hdf5") == {
        "LR", "Debug", "coherent_EP"
    }
    assert "coherent_EP" in restarted.list_tags()
    window.close()


def test_cancel_assignment_makes_no_changes(qapp, tmp_path, monkeypatch):
    import app.gui.browser_window as browser_module

    class CancelledDialog:
        Accepted = 1
        pending_tags = ["not_saved"]
        def __init__(self, *args, **kwargs): pass
        def exec(self): return 0
        def selected_tags(self): return {"LR"}

    monkeypatch.setattr(browser_module, "TagAssignmentDialog", CancelledDialog)
    window = _window(tmp_path)
    item = window.data_list.topLevelItem(0)
    window.data_list.setCurrentItem(item)
    window.tag_button.click()
    assert item.text(4) == ""
    assert "not_saved" not in window.tag_store.list_tags()
    assert window.tag_store.tags_for(str(tmp_path.resolve()), "folder/run.hdf5") == set()
    window.close()


def test_remove_one_and_then_all_through_dialog_path(qapp, tmp_path, monkeypatch):
    import app.gui.browser_window as browser_module

    window = _window(tmp_path)
    db = str(tmp_path.resolve())
    window.tag_store.set_tags(db, "folder/run.hdf5", {"LR", "Debug", "doubleYIG"})
    window._populate_data_list(("folder",))
    item = window.data_list.topLevelItem(0)

    class RemoveDebug:
        Accepted = 1
        pending_tags = []
        def __init__(self, *args, **kwargs): pass
        def exec(self): return 1
        def selected_tags(self): return {"LR", "doubleYIG"}

    monkeypatch.setattr(browser_module, "TagAssignmentDialog", RemoveDebug)
    window._edit_tags(item, item.data(0, 256))
    assert window.tag_store.tags_for(db, "folder/run.hdf5") == {"LR", "doubleYIG"}

    RemoveDebug.selected_tags = lambda self: set()
    window._edit_tags(item, item.data(0, 256))
    assert window.tag_store.tags_for(db, "folder/run.hdf5") == set()
    window.close()


def test_star_and_tags_are_independent(qapp, tmp_path):
    window = _window(tmp_path)
    item = window.data_list.topLevelItem(0)
    entry = item.data(0, 256)
    window.data_list.setCurrentItem(item)
    window.tag_store.set_tags(str(tmp_path.resolve()), entry.relative_path, {"LR"})
    window._toggle_star(item, entry)
    assert window.star_store.is_starred(str(tmp_path.resolve()), entry.relative_path)
    assert window.tag_store.tags_for(str(tmp_path.resolve()), entry.relative_path) == {"LR"}
    window._toggle_star(item, entry)
    assert not window.star_store.is_starred(str(tmp_path.resolve()), entry.relative_path)
    assert window.tag_store.tags_for(str(tmp_path.resolve()), entry.relative_path) == {"LR"}
    window.close()
