from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.tag_store import (
    DEFAULT_CATEGORY_TAGS, DEFAULT_TAGS, SCHEMA_VERSION, TAG_CATEGORIES,
    TagAssignmentConflict, TagStore,
)
from app.localization import LocalizationManager
from app.settings import SettingsStore


def test_tag_taxonomy_defaults_and_external_schema(tmp_path):
    path = tmp_path / "tags.json"
    store = TagStore(path, legacy_paths=[])
    assert store.categories() == TAG_CATEGORIES
    assert tuple(store.list_tags()) == DEFAULT_TAGS
    assert store.tags_by_category() == {key: list(value) for key, value in DEFAULT_CATEGORY_TAGS.items()}
    payload = json.loads(path.read_text())
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["tag_categories"]["RSMEP"] == "Project"


def test_tag_assignment_cardinality_and_query_cardinality_are_distinct(tmp_path):
    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    db = str(tmp_path.resolve())
    with pytest.raises(TagAssignmentConflict, match="one Project"):
        store.set_tags(db, "run.hdf5", {"RSMEP", "BIC"})
    with pytest.raises(TagAssignmentConflict, match="one Level"):
        store.set_tags(db, "run.hdf5", {"LA", "LR"})
    with pytest.raises(TagAssignmentConflict, match="Flux and BG"):
        store.set_tags(db, "run.hdf5", {"Flux", "BG"})
    store.create_tag("Edge A", "Board Design")
    store.create_tag("Edge B", "Board Design")
    store.create_tag("Reviewed", "Other")
    store.set_tags(db, "run.hdf5", {"RSMEP", "LR", "Mirror", "Edge A", "Edge B", "Flux", "Reviewed"})
    store.record_query({"RSMEP", "LRCPAEP"}, "OR")
    store.record_query({"Flux", "BG"}, "OR")
    assert len(store.recent_queries()) == 2


def test_category_change_requires_explicit_conflict_resolution(tmp_path):
    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    store.create_tag("Project candidate")
    db = str(tmp_path.resolve())
    store.set_tags(db, "run.hdf5", {"RSMEP", "Project candidate"})
    with pytest.raises(TagAssignmentConflict):
        store.set_tag_category("Project candidate", "Project")
    store.set_tag_category("Project candidate", "Project", replace_conflicts=True)
    assert store.tags_for(db, "run.hdf5") == {"Project candidate"}
    assert store.category_for("Project candidate") == "Project"


def test_schema3_taxonomy_migration_backs_up_and_preserves_conflicts(tmp_path):
    path = tmp_path / "tags.json"
    db = str((tmp_path / "db").resolve())
    legacy = {
        "schema_version": 3,
        "available_tags": ["LA", "LR", "Mirror", "debug", "Flux", "BG", "singleYIG",
                           "doubleYIG", "RSMEP", "LRCPAEP/CCEP", "best_data",
                           "debackground", "custom-note"],
        "assignments": {},
        "entry_states": {db: {"run.hdf5": {
            "explicit_tags": ["LRCPAEP/CCEP", "RSMEP", "LA", "LR", "Flux", "BG",
                              "debg", "best_data", "custom-note"],
            "auto_tags": [], "suppressed_auto_tags": [], "initialized": True,
            "group_id": ".", "flux_default": False, "user_decided": True,
        }}},
        "group_defaults": {}, "recent_queries": [], "migration_report": {},
    }
    original = json.dumps(legacy, ensure_ascii=False).encode()
    path.write_bytes(original)
    store = TagStore(path, legacy_paths=[])
    backup = path.with_name(path.name + ".pre-taxonomy-v4.bak")
    assert backup.read_bytes() == original
    assert json.loads(path.read_text())["schema_version"] == 4
    tags = store.tags_for(db, "run.hdf5")
    assert {"LRCPAEP", "LA", "BG", "De-background", "Best Data", "custom-note"} <= tags
    assert "Legacy Project: RSMEP" in tags
    assert "Legacy Level: LR" in tags
    assert "Legacy Data Analysis: Flux" in tags
    assert store.category_for("custom-note") == "Other"
    assert store.migration_report.preserved_conflicts


def test_schema_migration_remains_readable_when_backup_is_unavailable(tmp_path, monkeypatch):
    from app.core.tag_store import TagStoreError

    path = tmp_path / "tags.json"
    legacy = {"schema_version": 3, "available_tags": ["best_data"],
              "assignments": {}, "entry_states": {}, "group_defaults": {},
              "recent_queries": [], "migration_report": {}}
    original = json.dumps(legacy).encode()
    path.write_bytes(original)
    monkeypatch.setattr(TagStore, "_backup_before_taxonomy_migration",
                        lambda _self: (_ for _ in ()).throw(TagStoreError("read-only")))

    store = TagStore(path, legacy_paths=[])
    assert "Best Data" in store.list_tags()
    store.initialize_entries(str(tmp_path.resolve()), [])
    assert path.read_bytes() == original


def test_auto_flux_bg_and_debackground_preserve_unrelated_assignments(tmp_path):
    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    db = str(tmp_path.resolve())
    entries = [
        SimpleNamespace(relative_path="a/Flux-dep_debg.hdf5", log_name="Flux Sweep", file_name="Flux-dep_debg.hdf5", sweep_dimension="Current"),
        SimpleNamespace(relative_path="b/scan BG.h5", log_name="scan BG", file_name="scan BG.h5", sweep_dimension="Frequency"),
        SimpleNamespace(relative_path="c/scan DEBG.hdf5", log_name="scan DEBG", file_name="scan DEBG.hdf5", sweep_dimension="Frequency"),
    ]
    store.initialize_entries(db, entries)
    assert store.tags_for(db, entries[0].relative_path) == {"Flux", "De-background"}
    assert store.tags_for(db, entries[1].relative_path) == {"BG"}
    assert store.tags_for(db, entries[2].relative_path) == {"De-background"}
    store.create_tag("Reviewed", "Other")
    store.set_tags(db, entries[0].relative_path,
                   {"RSMEP", "LR", "Mirror", "Reviewed", "De-background"})
    store.initialize_entries(db, entries)
    assert {"RSMEP", "LR", "Mirror", "Reviewed", "De-background"} <= store.tags_for(db, entries[0].relative_path)


def test_settings_language_persists_without_dropping_unknown_future_settings(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "schema_version": 8,
        "general": {"language": "en", "future_option": True},
        "appearance": {"accent": "green"},
    }))
    store = SettingsStore(path)
    store.set_language("zh_TW")
    saved = json.loads(path.read_text())
    assert saved["schema_version"] == 1
    assert saved["general"] == {
        "language": "zh_TW", "future_option": True, "appearance": "system",
        "scientific_plot_appearance": "white", "export_plot_background": "white",
        "glass_thickness": 0.5, "glass_frost": 0.25, "three_d_profile": "balanced",
        "three_d_export_style": "publication", "app_icon": 4,
    }
    assert saved["appearance"] == {"accent": "green"}
    assert SettingsStore(path).language() == "zh_TW"


def test_runtime_localization_retranslates_open_widgets_and_keeps_science_terms(qapp, tmp_path):
    from PySide6.QtWidgets import QComboBox, QLabel, QMainWindow, QPushButton, QTabWidget, QWidget

    store = SettingsStore(tmp_path / "settings.json")
    localizer = LocalizationManager(store)
    window = QMainWindow()
    root = QWidget()
    window.setCentralWidget(root)
    button = QPushButton("Save", root)
    combo = QComboBox(root)
    combo.addItem("Magnitude")
    tabs = QTabWidget(root)
    tabs.addTab(QLabel("content"), "Fit Results")
    window.show()
    localizer.bind(window)
    localizer.set_language("zh_TW")
    assert button.text() == "儲存"
    assert combo.itemText(0) == "Magnitude"
    assert tabs.tabText(0) == "擬合結果"
    localizer.set_language("en")
    assert button.text() == "Save"
    assert tabs.tabText(0) == "Fit Results"
    window.close()


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])
