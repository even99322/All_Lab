"""v0.19D Retrieve by Tags across the largest data folder (a simulated folder in a temp dir)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core.database_scanner import DatabaseScanResult, LogEntry


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _entry(root: Path, relative: str) -> LogEntry:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return LogEntry(str(path), relative, path.name, path.stem, "ok", None, 1, 1.0)


@pytest.fixture
def master(tmp_path):
    """largest folder / two databases (one opened before, one now) / one never-opened folder."""
    from app.core.tag_store import TagStore

    root = (tmp_path / "ALL").resolve()
    db_a, db_b = root / "2025" / "run_A", root / "2026" / "run_B"
    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    store.create_tag("Sample1")
    a1 = _entry(db_a, "Data_0101/one.hdf5")
    a2 = _entry(db_a, "Data_0102/two.hdf5")
    b1 = _entry(db_b, "Data_0201/three.hdf5")
    _entry(root / "never_opened", "four.hdf5")
    store.set_tags(str(db_a), a1.relative_path, {"Sample1"})
    store.set_tags(str(db_b), b1.relative_path, {"Sample1", "BG"})
    store.set_tags(str(db_a), a2.relative_path, {"BG"})
    outside = (tmp_path / "elsewhere").resolve()
    other = _entry(outside, "x.hdf5")
    store.set_tags(str(outside), other.relative_path, {"Sample1"})
    return {"root": root, "db_a": db_a, "db_b": db_b, "store": store, "entries": (a1, a2, b1), "tmp": tmp_path}


def test_search_uses_tag_records_under_the_largest_folder(master):
    from app.core.master_search import search

    found = search(master["store"], master["root"], {"Sample1"}, "OR")
    assert {Path(e.absolute_path).name for e in found} == {"one.hdf5", "three.hdf5"}    # not the one outside
    assert {e.database_id for e in found} == {str(master["db_a"]), str(master["db_b"])}
    assert {e.source_folder for e in found} == {"2025/run_A/Data_0101", "2026/run_B/Data_0201"}
    both = search(master["store"], master["root"], {"Sample1", "BG"}, "AND")
    assert [Path(e.absolute_path).name for e in both] == ["three.hdf5"]
    Path(both[0].absolute_path).unlink()                                          # moved away / deleted
    assert search(master["store"], master["root"], {"Sample1", "BG"}, "AND") == []


def test_automatic_tags_stop_four_folders_below_the_opened_database(tmp_path):
    from app.core.master_search import folder_depth
    from app.core.tag_store import TagStore

    root = tmp_path.resolve()
    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    shallow = _entry(root, "a/b/c/d/0828 BG.hdf5")                 # 4 folders: automatic Tags
    deep = _entry(root, "a/b/c/d/e/0828 BG.hdf5")                  # 5 folders: left alone
    assert (folder_depth(shallow.relative_path), folder_depth(deep.relative_path)) == (4, 5)
    store.initialize_entries(str(root), [shallow, deep])
    assert "BG" in store.tags_for(str(root), shallow.relative_path)
    assert store.tags_for(str(root), deep.relative_path) == set()


def test_settings_and_dialog_offer_the_largest_folder(qapp, master):
    from app.gui.tag_query_dialog import TagQueryDialog
    from app.settings.store import SettingsStore

    store = SettingsStore(master["tmp"] / "settings.json")
    assert store.tag_search_root() == ""
    store.set_tag_search_root(str(master["root"]))
    assert SettingsStore(master["tmp"] / "settings.json").tag_search_root() == str(master["root"])
    plain = TagQueryDialog(master["store"])
    assert not plain.master_radio.isEnabled() and plain.scope() == "database"
    dialog = TagQueryDialog(master["store"], master_root=str(master["root"]))
    dialog.set_scope("master")
    assert dialog.scope() == "master" and str(master["root"]) in dialog.master_radio.text()


def test_browser_shows_results_from_other_databases_with_their_own_records(qapp, master, monkeypatch):
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    a1 = master["entries"][0]
    stars = StarStore(master["tmp"] / "stars.json")
    stars.toggle(str(master["db_b"]), "Data_0201/three.hdf5")
    window = BrowserWindow(star_store=stars, tag_store=master["store"])
    # the open database is run_A; run_B's results must keep run_B's star and Tags
    window.scan_result = DatabaseScanResult(str(master["db_a"]), str(master["db_a"]), [a1])
    window._populate_folder_tree(window.scan_result)
    monkeypatch.setattr(window, "_master_root", lambda: str(master["root"]))
    window._run_tag_query({"Sample1"}, "OR", scope="master")
    rows = {window.data_list.topLevelItem(i).text(1): window.data_list.topLevelItem(i)
            for i in range(window.data_list.topLevelItemCount())}
    assert set(rows) == {"one", "three"}
    three = rows["three"]
    assert three.text(2) == "2026/run_B/Data_0201"
    assert three.data(0, 0x0101) is True or window.star_store.is_starred(str(master["db_b"]), "Data_0201/three.hdf5")
    assert "BG" in three.text(4)
    assert window._active_query[2] == "master"
    assert window.session_state()["browser"]["query"]["scope"] == "master"
    window._run_tag_query({"Sample1"}, "OR", scope="database")
    assert window.data_list.topLevelItemCount() == 1
    window.close()
