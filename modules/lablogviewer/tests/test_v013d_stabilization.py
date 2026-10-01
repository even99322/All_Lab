"""Focused v0.13D Browser, native Comment, Preview, cache and named View tests."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import h5py
import pytest

from app.core.database_index_store import DatabaseIndexStore
from app.core.database_scanner import DatabaseScanner
from app.core.labber_comment import read_native_comment, write_native_comment
from app.core.labber_parser import load_experiment
from app.core.named_view_store import NamedViewStore
from app.core.quick_preview import build_quick_preview
from tests.real_data import BIG_FILE

try:
    from PySide6.QtWidgets import QApplication
    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _scientific_manifest(path: Path) -> dict[str, tuple[tuple, str, str]]:
    """Stable value hashes which also handle Labber compound-string datasets."""
    result = {}
    with h5py.File(path, "r") as file:
        def visit(name, obj):
            if not isinstance(obj, h5py.Dataset):
                return
            value = obj[()]
            # h5py object/string arrays do not have stable pointer bytes.
            payload = repr(value.tolist() if hasattr(value, "tolist") else value).encode("utf-8")
            result[name] = (obj.shape, str(obj.dtype), hashlib.sha256(payload).hexdigest())
        file.visititems(visit)
    return result


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real Labber fixture unavailable")
def test_native_root_comment_write_round_trips_without_scientific_changes(tmp_path):
    copied = tmp_path / "comment-copy.hdf5"
    shutil.copy2(BIG_FILE, copied)
    before = _scientific_manifest(copied)
    initial = read_native_comment(copied)
    assert initial.supported and initial.variable_length and initial.encoding == "utf-8"
    changed = write_native_comment(copied, "第一行\nNative comment ΔS21")
    assert changed.text == "第一行\nNative comment ΔS21"
    assert read_native_comment(copied).text == changed.text
    with load_experiment(copied) as experiment:
        assert experiment.comment == changed.text
    assert _scientific_manifest(copied) == before


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real Labber fixture unavailable")
def test_vector_trace_last_viewer_state_restores_a_real_1d_preview():
    state = {"mode": "1d", "one_d": {
        "channel": "VNA - S21", "x_axis": "Frequency", "transform": "magnitude",
        "trace_index": 4, "db": False, "unwrap": False,
    }}
    preview = build_quick_preview(str(BIG_FILE), state)
    assert preview.kind == "1d" and preview.restored
    assert preview.data.x_name == "Frequency"
    assert preview.data.transform == "magnitude"


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real Labber fixture unavailable")
def test_database_index_reuses_unchanged_metadata_and_invalidates_changed_file(tmp_path, monkeypatch):
    database = tmp_path / "database"
    database.mkdir()
    copied = database / "sample.hdf5"
    shutil.copy2(BIG_FILE, copied)
    index = DatabaseIndexStore(tmp_path / "database-index.json")
    first = DatabaseScanner.scan(database, index_store=index)
    assert first.n_ok == 1
    original = DatabaseScanner._scan_one
    calls = []

    def counted(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(DatabaseScanner, "_scan_one", staticmethod(counted))
    second = DatabaseScanner.scan(database, index_store=index)
    assert second.entries[0].log_name == first.entries[0].log_name
    assert calls == []
    copied.touch()
    DatabaseScanner.scan(database, index_store=index)
    assert calls == [copied]


def test_named_views_are_data_owned_atomic_and_validate_names(tmp_path):
    store = NamedViewStore(tmp_path / "views.json")
    state = {"display": {"mode": "1d"}, "pane": {"layout": "1 Pane"}}
    store.save("/data/a.hdf5", "Flux overview", state)
    assert store.list_names("/data/a.hdf5") == ["Flux overview"]
    assert store.get("/data/b.hdf5", "Flux overview") is None
    assert store.get("/data/a.hdf5", "Flux overview") == state
    with pytest.raises(ValueError):
        store.save("/data/a.hdf5", "Flux overview", state)
    assert store.rename("/data/a.hdf5", "Flux overview", "Phase")
    assert store.delete("/data/a.hdf5", "Phase")
    assert NamedViewStore(store.storage_path).list_names("/data/a.hdf5") == []


@pytest.mark.skipif(not PYSIDE_AVAILABLE, reason="PySide6 unavailable")
def test_cut_always_on_top_toggles_keep_the_same_visible_window(qapp):
    from app.gui.linecut_widget import CutWindow

    window = CutWindow("x")
    window.show()
    qapp.processEvents()
    geometry = window.geometry()
    window.always_on_top_checkbox.setChecked(True)
    qapp.processEvents()
    assert window.isVisible() and window.geometry() == geometry
    window.always_on_top_checkbox.setChecked(False)
    qapp.processEvents()
    assert window.isVisible() and window.geometry() == geometry
    window.close()
