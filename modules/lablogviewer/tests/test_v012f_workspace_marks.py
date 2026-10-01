"""v0.12F workspace and external-Mark persistence coverage."""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app import __version__
from app.core.axis_preset_store import AxisPresetStore
from app.core.mark_model import MarkManager
from app.core.mark_store import MARK_SCHEMA_VERSION, MarkStore, default_mark_storage_path
from app.core.overlay_store import OverlayStore
from app.gui.main_window import MainWindow
from app.gui.multi_pane import FOUR_PANES, THREE_PANES, TWO_SIDE
from tests.real_data import BIG_FILE, FLUX_FILE


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(tmp_path, qapp):
    window = MainWindow(
        axis_preset_store=AxisPresetStore(tmp_path / "axis.json"),
        overlay_store=OverlayStore(tmp_path / "overlays.json"),
        mark_store=MarkStore(tmp_path / "marks.json"),
    )
    window.resize(1400, 900)
    window.show()
    window.open_file(str(BIG_FILE))
    qapp.processEvents()
    return window


def _configured_manager() -> MarkManager:
    manager = MarkManager()
    x = np.array([5.0, 5.1, 5.3, 5.8, 6.0])
    y = np.array([0.0, -3.0, -1.0, -5.0, 2.0])
    manager.set_1d_context(("file", "x", "y", 0), x, y, x_name="Frequency", y_name="S21")
    return manager


def test_mark_store_schema_and_sample_identity_round_trip(tmp_path):
    store = MarkStore(tmp_path / "marks.json")
    manager = _configured_manager()
    point = manager.add_at_trace_position(3)
    region = manager.add_range(5.1, 5.8)
    line = manager.add_horizontal_line(-2.0)
    manager.set_visible(region.object_id, False)
    store.save("/data/a.hdf5", 1, manager.context_key, manager.persistence_state())

    reloaded = MarkStore(store.storage_path)
    restored = _configured_manager()
    assert restored.restore_persistence_state(reloaded.get("/data/a.hdf5", 1, restored.context_key)) == 3
    restored_point = restored.object(point.object_id)
    assert restored_point.sample_index == point.sample_index == 3
    assert restored.object(region.object_id).visible is False
    assert restored.object(line.object_id).y == pytest.approx(-2.0)
    raw = json.loads(store.storage_path.read_text())
    assert raw["schema_version"] == MARK_SCHEMA_VERSION
    assert raw["app_version"] == __version__
    assert default_mark_storage_path().parent.name == "state"
    assert default_mark_storage_path().parent.parent.name == "LabLogViewerData"


def test_mark_store_ignores_partial_and_unknown_entries(tmp_path):
    path = tmp_path / "marks.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "datasets": {"/data/a": {"contexts": {
            "[1,[\"file\",\"x\",\"y\",0]]": {"pane_id": 1, "state": {
                "mode": "1d", "marks": [{"number": "not-a-number"}],
                "annotations": [{"type": "unknown", "number": 1}],
            }},
        }}},
    }))
    store = MarkStore(path)
    manager = _configured_manager()
    assert manager.restore_persistence_state(store.get("/data/a", 1, manager.context_key)) == 0

    future_path = tmp_path / "future.json"
    future_path.write_text(json.dumps({"schema_version": 99, "datasets": {"future": {}}}))
    future = MarkStore(future_path)
    future.save("/data/a", 1, manager.context_key, manager.persistence_state())
    assert json.loads(future_path.read_text())["schema_version"] == 99


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real 1D fixture unavailable")
def test_marks_restore_after_viewer_reopen_and_range_numeric_guard(qapp, tmp_path):
    window = _window(tmp_path, qapp)
    manager = window._mark_managers[0]
    point = manager.add_at_trace_position(100)
    region = manager.add_range(point.x, manager.analysis_trace()[0][300])
    manager.add_horizontal_line(point.y)
    manager.add_vertical_line(point.x)
    manager.add_crosshair(point.x, point.y)
    manager.set_visible(region.object_id, False)
    window._refresh_mark_ui()
    window._persist_mark_manager(manager)
    window.close()

    restored = _window(tmp_path, qapp)
    objects = {item.object_id: item for item in restored._mark_managers[0].objects()}
    assert point.object_id in objects and objects[point.object_id].sample_index == point.sample_index
    assert region.object_id in objects and not objects[region.object_id].visible
    restored._mark_managers[0].select_object(None)
    restored._populate_numeric_editor()
    assert restored.numeric_editor.isHidden()
    assert not restored.numeric_apply_button.isEnabled()
    restored._mark_managers[0].select_object(region.object_id)
    restored._populate_numeric_editor()
    assert restored.numeric_apply_button.isEnabled()
    assert restored.numeric_x_edit.isVisible() and restored.numeric_x2_edit.isVisible()
    restored.close()


@pytest.mark.skipif(not FLUX_FILE.exists(), reason="Real 2D fixture unavailable")
def test_2d_mark_coordinates_restore_as_grid_cells(qapp, tmp_path):
    window = _window(tmp_path, qapp)
    window.open_file(str(FLUX_FILE))
    window.mode_combo.setCurrentIndex(1)
    qapp.processEvents()
    grid = window.plot_2d_widget._grid
    manager = window._mark_managers[1]
    point = manager.add_nearest(grid.x_values[20], grid.y_values[30])
    crosshair = manager.add_crosshair(grid.x_values[40], grid.y_values[50])
    window._persist_mark_manager(manager)
    window.close()

    restored = _window(tmp_path, qapp)
    restored.open_file(str(FLUX_FILE))
    restored.mode_combo.setCurrentIndex(1)
    qapp.processEvents()
    recovered = restored._mark_managers[1]
    assert recovered.object(point.object_id).x_index == point.x_index
    assert recovered.object(point.object_id).y_index == point.y_index
    restored_crosshair = recovered.object(crosshair.object_id)
    ix = int(np.argmin(np.abs(restored.plot_2d_widget._grid.x_values - restored_crosshair.x)))
    iy = int(np.argmin(np.abs(restored.plot_2d_widget._grid.y_values - restored_crosshair.y)))
    assert restored_crosshair.value == pytest.approx(restored.plot_2d_widget._grid.z_values[iy, ix])
    restored.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real pane fixture unavailable")
def test_toolbar_trace_manager_popouts_and_resizable_panes(qapp, tmp_path):
    window = _window(tmp_path, qapp)
    assert not window.multi_trace_panel.isVisible()
    window._show_trace_manager()
    qapp.processEvents()
    assert window.multi_trace_panel.isVisible()
    assert window.multi_trace_tree.topLevelItemCount() == window.log_entries.row_count()
    extra = window.multi_trace_tree.topLevelItem(2)
    window._on_multi_trace_item_clicked(extra, 3)
    assert window.trace_selection.selected_count == 2
    window.multi_trace_tree.setCurrentItem(extra)
    window._refresh_multi_trace_panel()
    window._remove_trace_from_manager()
    assert window.trace_selection.selected_count == 1

    window._show_metadata_dialog()
    window._show_data_table_dialog()
    assert window.metadata_dialog.isVisible() and window.data_table_dialog.isVisible()
    window.metadata_dialog.close()
    window._show_metadata_dialog()
    assert window.metadata_dialog.isVisible()

    window.pane_layout_combo.setCurrentText(TWO_SIDE)
    qapp.processEvents()
    assert window._pane_layout_root is not None
    assert window._pane_frames[1].minimumWidth() <= 1
    before = window._pane_splitter_sizes()
    window._pane_splitters[0].setSizes([700, 120])
    custom = window._pane_splitter_sizes()
    window.maximize_plot_button.setChecked(True)
    window.maximize_plot_button.setChecked(False)
    qapp.processEvents()
    assert len(window._pane_splitters) == len(before)
    assert window._pane_splitter_sizes()[0] == custom[0]

    window.pane_layout_combo.setCurrentText(THREE_PANES)
    assert len(window._pane_splitters) == 2
    window.pane_layout_combo.setCurrentText(FOUR_PANES)
    assert len(window._pane_splitters) == 3
    window._reset_pane_geometry()
    assert all(sum(splitter.sizes()) > 0 for splitter in window._pane_splitters)
    window.close()
