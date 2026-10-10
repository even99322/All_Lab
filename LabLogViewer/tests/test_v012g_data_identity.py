"""v0.12G name preservation and stable source-identity regressions."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.core.axis_preset_store import AxisPreset, AxisPresetStore, AxisRef
from app.core.data_identity import display_name_for_source, stable_data_identity
from app.core.database_scanner import DatabaseScanner
from app.core.mark_store import MarkStore
from app.core.overlay_store import OverlayStore, SavedOverlay
from app.core.star_store import StarStore
from app.core.tag_store import TagStore
from tests.real_data import BIG_FILE


@pytest.mark.parametrize("filename", [
    "X1 Flux-dep.hdf5", "X2 Flux-dep.hdf5", "X2 Flux_p_debg.hdf5",
    "X2 Flux_debg.hdf5", "BG.hdf5", "_BG.hdf5", "_debg.hdf5",
    "_p_debg.hdf5", "ordinary_name-with-hyphen.h5",
])
def test_display_name_preserves_complete_filename_stem(filename):
    assert display_name_for_source(Path("/logs") / filename, "stale metadata") == Path(filename).stem


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real Labber fixture unavailable")
def test_scanner_prefers_complete_filename_over_stale_embedded_log_name(tmp_path):
    target = tmp_path / "0831 X2 Flux_p_debg.hdf5"
    shutil.copy2(BIG_FILE, target)

    entry = DatabaseScanner.scan(tmp_path).entries[0]

    assert entry.status == "ok"
    assert entry.display_name == "0831 X2 Flux_p_debg"
    assert entry.log_name == entry.display_name


def test_distinct_paths_with_identical_display_names_keep_all_external_state_separate(tmp_path):
    root = tmp_path / "database"
    a = root / "session_a" / "X2 Flux-dep.hdf5"
    b = root / "session_b" / "X2 Flux-dep.hdf5"
    a.parent.mkdir(parents=True)
    b.parent.mkdir(parents=True)
    a.touch()
    b.touch()
    key_a, key_b = stable_data_identity(a), stable_data_identity(b)
    assert key_a != key_b
    database_id = DatabaseScanner.compute_database_id(root)

    stars = StarStore(tmp_path / "stars.json")
    stars.set_starred(database_id, "session_a/X2 Flux-dep.hdf5", True)
    assert stars.is_starred(database_id, "session_a/X2 Flux-dep.hdf5")
    assert not stars.is_starred(database_id, "session_b/X2 Flux-dep.hdf5")

    tags = TagStore(tmp_path / "tags.json")
    tags.set_tags(database_id, "session_a/X2 Flux-dep.hdf5", {"LR"})
    assert tags.tags_for(database_id, "session_a/X2 Flux-dep.hdf5") == {"LR"}
    assert tags.tags_for(database_id, "session_b/X2 Flux-dep.hdf5") == set()

    axis = AxisPresetStore(tmp_path / "axis.json")
    preset = AxisPreset("A", AxisRef("X", "trace_axis", "S21"), AxisRef("Y", "derived", "S21"))
    axis.save(preset, key_a)
    assert axis.get("A", key_a) is not None
    assert axis.get("A", key_b) is None

    overlays = OverlayStore(tmp_path / "overlays.json")
    overlays.save(key_a, SavedOverlay("A", [0], [0], 0))
    assert overlays.get(key_a, "A") is not None
    assert overlays.get(key_b, "A") is None

    marks = MarkStore(tmp_path / "marks.json")
    context = (str(a), "frequency", "S21", 0)
    marks.save(key_a, 1, context, {"mode": "1d", "marks": []})
    assert marks.get(key_a, 1, context) is not None
    assert marks.get(key_b, 1, context) is None


def test_v012f_mark_store_path_key_remains_readable(tmp_path):
    path = tmp_path / "marks.json"
    legacy_key = stable_data_identity(tmp_path / "legacy" / "X2 Flux-dep.hdf5")
    context = (1, (legacy_key, "frequency", "S21", 0))
    store = MarkStore(path)
    store.save(legacy_key, 1, context[1], {"mode": "1d", "marks": []})

    reloaded = MarkStore(path)
    assert reloaded.get(legacy_key, 1, context[1]) == {"mode": "1d", "marks": []}


def test_browser_source_has_no_file_name_column_workaround():
    browser_source = (Path(__file__).resolve().parent.parent / "app" / "gui" / "browser_window.py").read_text()
    assert "File Name" not in browser_source
