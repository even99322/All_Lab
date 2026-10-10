"""Core acceptance tests for v0.10A external Tag persistence and migration."""

from __future__ import annotations

import json
import hashlib

import pytest

from app.core.tag_store import DEFAULT_TAGS, TagStore, default_tag_storage_path
from tests.real_data import BIG_FILE, FLUX_FILE, S31_FILE, SMALL_FILE


def test_default_available_tags_and_zero_assignments(tmp_path):
    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    assert tuple(store.list_tags()) == DEFAULT_TAGS
    assert store.tags_for(str(tmp_path), "Flux BG.hdf5") == set()


def test_assign_multiple_remove_one_and_remove_all(tmp_path):
    path = tmp_path / "tags.json"
    store = TagStore(path, legacy_paths=[])
    db = str(tmp_path.resolve())
    store.set_tags(db, "folder/run.hdf5", {"LR", "Debug", "doubleYIG"})
    assert store.tags_for(db, "folder/run.hdf5") == {"LR", "Debug", "doubleYIG"}
    store.set_tags(db, "folder/run.hdf5", {"LR", "doubleYIG"})
    assert store.tags_for(db, "folder/run.hdf5") == {"LR", "doubleYIG"}
    store.set_tags(db, "folder/run.hdf5", set())
    assert store.tags_for(db, "folder/run.hdf5") == set()


def test_custom_unicode_validation_and_restart(tmp_path):
    path = tmp_path / "tags.json"
    store = TagStore(path, legacy_paths=[])
    assert not store.create_tag("   ")
    assert not store.create_tag(" Flux ")
    assert store.create_tag("  coherent_EP  ")
    assert store.create_tag("測試")
    assert not store.create_tag("測試")
    restarted = TagStore(path, legacy_paths=[])
    assert "coherent_EP" in restarted.list_tags()
    assert "測試" in restarted.list_tags()


def test_identity_separates_duplicate_filenames(tmp_path):
    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    db = str(tmp_path.resolve())
    store.set_tags(db, "A/same.hdf5", {"LA"})
    store.set_tags(db, "B/same.hdf5", {"LR"})
    assert store.tags_for(db, "A/same.hdf5") == {"LA"}
    assert store.tags_for(db, "B/same.hdf5") == {"LR"}


def test_invalid_or_ambiguous_identity_is_rejected(tmp_path):
    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    with pytest.raises(ValueError):
        store.set_tags("display-name-only", "run.hdf5", {"LR"})
    with pytest.raises(ValueError):
        store.set_tags(str(tmp_path), "../run.hdf5", {"LR"})


def test_legacy_names_assignments_unicode_duplicates_and_unmatched(tmp_path):
    db = str((tmp_path / "database").resolve())
    legacy = tmp_path / "metadata.json"
    legacy.write_text(json.dumps({
        "available_tags": ["LR", "coherent_EP", "測試", "coherent_EP"],
        "assignments": {
            db: {"folder/run.hdf5": ["LR", "coherent_EP", "測試"]},
            "filename-only": ["debug"],
        },
    }, ensure_ascii=False))
    store = TagStore(tmp_path / "tags.json", legacy_paths=[legacy])
    report = store.migration_report
    assert set(DEFAULT_TAGS).issubset(store.list_tags())
    assert store.list_tags().count("coherent_EP") == 1
    assert "測試" in store.list_tags()
    assert store.tags_for(db, "folder/run.hdf5") == {"LR", "coherent_EP", "測試"}
    assert report.migrated_assignments == 1
    assert report.unmatched_assignments == [f"{legacy}: filename-only"]
    assert "LR" in report.duplicate_tags_skipped

    restarted = TagStore(tmp_path / "tags.json", legacy_paths=[legacy])
    assert restarted.tags_for(db, "folder/run.hdf5") == {"LR", "coherent_EP", "測試"}
    assert restarted.migration_report.unmatched_assignments == report.unmatched_assignments


def test_list_record_migration_requires_full_identity(tmp_path):
    db = str((tmp_path / "db").resolve())
    legacy = tmp_path / "tag_assignments.json"
    legacy.write_text(json.dumps({"assignments": [
        {"database_id": db, "relative_path": "A/run.hdf5", "tags": ["MMA"]},
        {"file_name": "run.hdf5", "tags": ["debug"]},
    ]}))
    store = TagStore(tmp_path / "tags.json", legacy_paths=[legacy])
    assert store.tags_for(db, "A/run.hdf5") == {"MMA"}
    assert len(store.migration_report.unmatched_assignments) == 1


def test_malformed_legacy_is_preserved_and_skipped(tmp_path):
    legacy = tmp_path / "legacy_tags.json"
    legacy.write_text("{ malformed")
    before = legacy.read_bytes()
    store = TagStore(tmp_path / "tags.json", legacy_paths=[legacy])
    assert tuple(store.list_tags()) == DEFAULT_TAGS
    assert store.migration_report.malformed_sources
    assert legacy.read_bytes() == before


def test_malformed_legacy_does_not_destroy_existing_current_store(tmp_path):
    current = tmp_path / "tags.json"
    store = TagStore(current, legacy_paths=[])
    store.create_tag("MMA")
    legacy = tmp_path / "metadata.json"
    legacy.write_text("not json")
    restarted = TagStore(current, legacy_paths=[legacy])
    assert "MMA" in restarted.list_tags()
    assert legacy.read_text() == "not json"


def test_default_storage_is_with_other_lablogviewer_state():
    assert default_tag_storage_path().parent.name == "state"
    assert default_tag_storage_path().parent.parent.name == "LabLogViewerData"
    assert default_tag_storage_path().name == "tags.json"


@pytest.mark.parametrize("path", [SMALL_FILE, BIG_FILE, FLUX_FILE, S31_FILE])
def test_real_files_receive_no_automatic_tags_and_are_never_modified(tmp_path, path):
    if not path.exists():
        pytest.skip(f"Real data unavailable: {path}")
    before_stat = path.stat()
    before_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    database_root = str(path.parents[3].resolve())
    relative_path = path.relative_to(path.parents[3]).as_posix()

    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    assert store.tags_for(database_root, relative_path) == set()
    store.set_tags(database_root, relative_path, {"LR", "Debug"})
    assert store.tags_for(database_root, relative_path) == {"LR", "Debug"}

    after_stat = path.stat()
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before_hash
