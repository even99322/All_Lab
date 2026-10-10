"""v0.13A external state, Browser comments, menu actions, and partial data."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.comment_store import CommentStore
from app.core.data_model import StepAxis
from app.core.database_scanner import DatabaseScanResult, LogEntry
from app.core.external_state import atomic_write_json, load_json_state
from app.core.labber_parser import load_experiment
from app.core.quick_preview import build_quick_preview
from app.core.star_store import StarStore
from app.core.transform_store import TransformSpec, TransformStore
from tests.real_data import BIG_FILE


LEGACY_PARTIAL_FILE = (
    Path(__file__).resolve().parent.parent.parent / "Data" / "2025 0827 LR CPAEP"
    / "2025" / "08" / "Data_0826" / "0825 LR @4.812GHz.hdf5"
)
SECOND_PARTIAL_FILE = (
    Path(__file__).resolve().parent.parent.parent / "Data" / "2025 0827 LR CPAEP"
    / "2025" / "08" / "Data_0826" / "0826 LRCPAEP_135.5400mA.hdf5"
)

try:
    from PySide6.QtWidgets import QApplication
    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _entry(name: str, relative_path: str) -> LogEntry:
    return LogEntry(
        absolute_path=name, relative_path=relative_path, file_name=Path(name).name,
        log_name=Path(name).stem, status="ok", error_message=None,
        size_bytes=1, mtime=1.0, creation_time=1.0,
    )


def test_atomic_state_write_and_corruption_backup(tmp_path):
    path = tmp_path / "state.json"
    atomic_write_json(path, {"schema_version": 1, "value": "繁體中文\nsecond line"})
    assert json.loads(path.read_text())["value"] == "繁體中文\nsecond line"
    path.write_text("{ interrupted")
    loaded = load_json_state(path, {})
    assert loaded.value == {}
    assert loaded.recovered_from_corruption
    assert loaded.backup_path is not None and loaded.backup_path.read_text() == "{ interrupted"


def test_legacy_star_mapping_remains_readable_and_migrates_on_save(tmp_path):
    path = tmp_path / "stars.json"
    path.write_text(json.dumps({"database": ["session/a.hdf5"]}))
    store = StarStore(path)
    assert store.is_starred("database", "session/a.hdf5")
    store.set_starred("database", "session/b.hdf5", True)
    raw = json.loads(path.read_text())
    assert raw["schema_version"] == 1
    assert raw["by_database"]["database"] == ["session/a.hdf5", "session/b.hdf5"]


def test_future_schema_state_is_read_only_and_not_rewritten(tmp_path):
    path = tmp_path / "transforms.json"
    original = {"schema_version": 99, "custom": [{"name": "Future", "base": "real"}]}
    path.write_text(json.dumps(original))
    store = TransformStore(path)
    assert store.get("Future") is None
    store.save(TransformSpec("Local", "real"))
    assert json.loads(path.read_text()) == original


def test_comment_store_unicode_multiline_restart_and_collision_isolation(tmp_path):
    path = tmp_path / "comments.json"
    database = str((tmp_path / "database").resolve())
    store = CommentStore(path)
    text = "第一行\n第二行: ΔS21 ★"
    store.set(database, "Data_0828/same.hdf5", text)
    store.set(database, "Data_0831/same.hdf5", "other")
    reloaded = CommentStore(path)
    assert reloaded.get(database, "Data_0828/same.hdf5") == text
    assert reloaded.get(database, "Data_0831/same.hdf5") == "other"


@pytest.mark.skipif(not PYSIDE_AVAILABLE, reason="PySide6 unavailable")
def test_comment_store_migrates_legacy_browser_key_to_canonical_data_identity(qapp, tmp_path):
    database = str(tmp_path.resolve())
    comments = CommentStore(tmp_path / "comments.json")
    comments.set(database, "Data_0828/same.hdf5", "跨行\ncomment")
    source = tmp_path / "Data_0828" / "same.hdf5"
    assert comments.get_for_source(source, database_id=database,
                                   relative_path="Data_0828/same.hdf5") == "跨行\ncomment"
    reloaded = CommentStore(tmp_path / "comments.json")
    assert reloaded.get_for_source(source) == "跨行\ncomment"


@pytest.mark.skipif(not PYSIDE_AVAILABLE, reason="PySide6 unavailable")
def test_viewer_menu_and_toolbar_share_actions_with_window_local_ownership(qapp):
    from app.gui.main_window import MainWindow

    left, right = MainWindow(), MainWindow()
    for window in (left, right):
        actions = window.application_toolbar.actions()
        assert window.open_action in actions
        assert window.reload_action in actions
        assert window.traces_action in actions
        assert window.show_tool_button.menu() is window.show_menu
        assert set(window.show_menu.actions()) >= {
            window.metadata_action, window.data_table_action,
            window.show_x_cut_action, window.show_y_cut_action,
        }

    left.open_file(str(BIG_FILE))
    right.open_file(str(BIG_FILE))
    left.metadata_action.trigger()
    qapp.processEvents()
    assert left.metadata_dialog.isVisible()
    assert not right.metadata_dialog.isVisible()
    right.data_table_action.trigger()
    qapp.processEvents()
    assert right.data_table_dialog.isVisible()
    left.close()
    right.close()


@pytest.mark.skipif(not LEGACY_PARTIAL_FILE.exists(), reason="Real legacy partial fixture unavailable")
def test_real_partial_s31_uses_only_measured_leading_coordinates_and_preview():
    with load_experiment(str(LEGACY_PARTIAL_FILE)) as experiment:
        status = experiment.acquisition_status("VNA - S31")
        assert status is not None and status.is_partial
        assert (status.nominal_entries, status.acquired_entries) == (301, 270)
        original_axis = experiment.step_axes[0].values.copy()
        dimensions = experiment.list_dimensions("VNA - S31")
        assert dimensions[1].size == 270
        grid = experiment.get_2d_data(
            dimensions[0].name, dimensions[1].name, "VNA - S31", transform="magnitude_db",
        )
        assert grid.z_values.shape == (270, 1001)
        assert np.array_equal(grid.y_values, original_axis[:270])
        assert grid.acquisition is not None and grid.acquisition.is_partial
        for transform in ("real", "imag", "magnitude", "phase_deg", "magnitude_db"):
            assert experiment.get_2d_data(
                dimensions[0].name, dimensions[1].name, "VNA - S31", transform=transform,
            ).z_values.shape == (270, 1001)

    preview = build_quick_preview(str(LEGACY_PARTIAL_FILE))
    assert preview.kind == "2d"
    assert preview.acquisition is not None and preview.acquisition.is_partial
    assert preview.data.z_values.shape == (270, 1001)


@pytest.mark.parametrize("path, nominal, acquired", [
    (LEGACY_PARTIAL_FILE, 301, 270),
    (SECOND_PARTIAL_FILE, 581, 133),
])
def test_real_historical_partial_files_report_their_actual_measurement_extent(path, nominal, acquired):
    if not path.exists():
        pytest.skip("Real legacy partial fixture unavailable")
    with load_experiment(str(path)) as experiment:
        status = experiment.acquisition_status("VNA - S31")
        assert status is not None and status.is_partial
        assert (status.nominal_entries, status.acquired_entries) == (nominal, acquired)
        assert experiment.list_dimensions("VNA - S31")[1].size == acquired


@pytest.mark.skipif(not (PYSIDE_AVAILABLE and LEGACY_PARTIAL_FILE.exists()), reason="Real legacy GUI fixture unavailable")
def test_viewer_opens_real_partial_s31_as_measured_heatmap(qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.open_file(str(LEGACY_PARTIAL_FILE))
    window.mode_combo.setCurrentIndex(1)
    qapp.processEvents()
    grid = window.plot_2d_widget._grid
    assert grid is not None and grid.z_values.shape == (270, 1001)
    assert grid.acquisition is not None and grid.acquisition.is_partial
    assert "Partial acquisition: 270 / 301" in window.statusBar().currentMessage()
    window.close()


def test_multidimensional_partial_status_stays_ambiguous_without_reshape():
    class FakeTrace:
        n_entries = 7

    class FakeAxis:
        def __init__(self, size):
            self.values = np.arange(size, dtype=float)

    experiment = object.__new__(__import__("app.core.data_model", fromlist=["Experiment"]).Experiment)
    experiment.vector_traces = {"S21": FakeTrace()}
    experiment.step_axes = [FakeAxis(3), FakeAxis(3)]
    status = experiment.acquisition_status("S21")
    assert status is not None
    assert status.state == "ambiguous"
    assert not status.is_recoverable
