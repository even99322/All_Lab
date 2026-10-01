"""v0.11C Viewer restructure, positioning, visibility, and preset tests."""

from __future__ import annotations

import json
import os
from time import perf_counter

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.axis_preset_store import AxisPreset, AxisPresetStore, AxisRef
from app.core.mark_model import (
    CROSSHAIR, HORIZONTAL_LINE, RANGE, VERTICAL_LINE, MarkManager,
)
from tests.real_data import BIG_FILE, FLUX_FILE, S31_FILE, SMALL_FILE


pytestmark = pytest.mark.skipif(
    not all(path.exists() for path in (BIG_FILE, FLUX_FILE, S31_FILE, SMALL_FILE)),
    reason="Required workspace real-data fixtures are unavailable.",
)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _manager(mode="1d"):
    manager = MarkManager()
    if mode == "1d":
        manager.set_1d_context(
            "trace", [0, 1, 2, 3], [10, 20, 15, 30],
            x_name="Frequency", y_name="S21", x_unit="GHz", y_unit="dB",
            x_semantic="frequency", y_semantic="magnitude",
        )
    else:
        manager.set_2d_context(
            "grid", [1, 2, 4], [10, 20], [[11, 12, 14], [21, 22, 24]],
            x_name="Frequency", y_name="Current", value_name="S21",
            x_unit="GHz", y_unit="mA", value_unit="dB",
        )
    return manager


def test_numeric_point_snaps_by_x_and_preserves_visibility():
    manager = _manager()
    mark = manager.add_nearest(0, 10)
    manager.set_visible(mark.object_id, False)
    moved = manager.position_mark(mark.number, x=1.8)
    assert (moved.x, moved.y, moved.sample_index) == (2.0, 15.0, 2)
    assert not moved.visible


def test_2d_point_partial_positioning_and_real_grid_snap():
    manager = _manager("2d")
    mark = manager.add_nearest(1, 10)
    moved = manager.position_mark(mark.number, y=19)
    assert (moved.x, moved.y, moved.value) == (1.0, 20.0, 21.0)
    moved = manager.position_mark(mark.number, x=3.7)
    assert (moved.x, moved.y, moved.value) == (4.0, 20.0, 24.0)


def test_numeric_range_lines_and_crosshair_partial_updates():
    manager = _manager()
    region = manager.add_range(0, 3)
    assert manager.position_annotation(region.object_id, x=1).x2 == 3
    assert manager.position_annotation(region.object_id, x2=2).x == 1
    with pytest.raises(ValueError):
        manager.position_annotation(region.object_id, x=2, x2=2)
    hline = manager.add_horizontal_line(15)
    vline = manager.add_vertical_line(1)
    crosshair = manager.add_crosshair(1, 20)
    assert manager.position_annotation(hline.object_id, y=25).y == 25
    assert manager.position_annotation(vline.object_id, x=2.5).x == 2.5
    assert manager.position_annotation(crosshair.object_id, x=2).y == 20
    assert manager.position_annotation(crosshair.object_id, y=15).x == 2


def test_invalid_and_out_of_range_numeric_positions():
    manager = _manager()
    mark = manager.add_nearest(1, 20)
    with pytest.raises(ValueError):
        manager.position_mark(mark.number, x=np.nan)
    with pytest.raises(ValueError):
        manager.position_mark(mark.number, x=99)
    line = manager.add_vertical_line(1)
    with pytest.raises(ValueError):
        manager.position_annotation(line.object_id, x=-1)


def test_2d_crosshair_numeric_position_snaps_and_updates_z():
    manager = _manager("2d")
    item = manager.add_crosshair(1, 10)
    moved = manager.position_annotation(item.object_id, x=3.8)
    assert (moved.x, moved.y, moved.value) == (4.0, 10.0, 14.0)
    moved = manager.position_annotation(item.object_id, y=19)
    assert (moved.x, moved.y, moved.value) == (4.0, 20.0, 24.0)


def test_visibility_is_independent_from_delete_for_every_tool():
    manager = _manager()
    objects = [
        manager.add_nearest(1, 20), manager.add_range(0, 2),
        manager.add_horizontal_line(20), manager.add_vertical_line(1),
        manager.add_crosshair(1, 20),
    ]
    positions = [(obj.object_id, obj.x, obj.y, getattr(obj, "x2", None)) for obj in objects]
    for obj in objects:
        assert manager.set_visible(obj.object_id, False)
    assert len(manager.objects()) == 5 and all(not obj.visible for obj in manager.objects())
    for obj in objects:
        manager.set_visible(obj.object_id, True)
    assert positions == [(obj.object_id, obj.x, obj.y, getattr(obj, "x2", None)) for obj in manager.objects()]


def test_per_data_preset_schema_restart_isolation_and_legacy(tmp_path):
    path = tmp_path / "axis_presets.json"
    preset_a = AxisPreset("A1", AxisRef("X", "step", "X"), AxisRef("Y", "log_scalar", "Y"))
    preset_b = AxisPreset("B1", AxisRef("X", "step", "X"), AxisRef("Z", "log_scalar", "Z"))
    store = AxisPresetStore(path)
    store.save(preset_a, "/root/session/a.hdf5")
    store.save(preset_b, "/root/session/b.hdf5")
    restarted = AxisPresetStore(path)
    assert restarted.list_all("/root/session/a.hdf5") == [preset_a]
    assert restarted.list_all("/root/session/b.hdf5") == [preset_b]
    assert restarted.list_all("/root/session/c.hdf5") == []
    assert json.loads(path.read_text())["schema_version"] == 2

    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(json.dumps([preset_a.to_dict()]))
    legacy = AxisPresetStore(legacy_path)
    assert legacy.get("A1") == preset_a
    assert legacy.list_all("/root/session/a.hdf5") == []


def test_viewer_layout_metadata_iq_and_no_marks_tab(qapp, tmp_path):
    from app.gui.main_window import MainWindow
    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / "axis.json"))
    window.resize(1400, 900)
    window.show()
    qapp.processEvents()
    window.open_file(str(SMALL_FILE))
    assert window.main_splitter.widget(0) is window.controls_panel
    sizes = window.main_splitter.sizes()
    assert sizes[1] > sizes[0] * 3
    assert window.metadata_dialog.windowTitle() == "Metadata"
    assert window.data_table_dialog.windowTitle() == "Data Table"
    metadata = window.summary_text.toPlainText()
    assert "Step Channels" in metadata and "Log Channels" in metadata
    assert "VNA - S21" in metadata and "Frequency" in metadata
    iq = window.axis_preset_combo.findText("IQ Transform")
    assert iq >= 0
    window.axis_preset_combo.setCurrentIndex(iq)
    assert window.x_combo.currentText() == "Imaginary"
    assert window.y_combo.currentText() == "Real"
    window.close()


def test_viewer_visibility_show_values_editor_and_maximize(qapp, tmp_path):
    from PySide6.QtCore import Qt
    from app.gui.main_window import MainWindow
    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / "axis.json"))
    window.open_file(str(SMALL_FILE))
    x, y = window.plot_widget._curve.getData()
    manager = window._mark_managers[0]
    mark = manager.add_nearest(x[100], y[100])
    region = manager.add_range(x[120], x[220])
    manager.add_horizontal_line(y[200])
    manager.add_vertical_line(x[250])
    manager.add_crosshair(x[300], y[300])
    window._refresh_mark_ui()
    items_with_values = len(window._mark_overlays[0]._items)
    window.show_mark_values_checkbox.setChecked(False)
    assert len(manager.objects()) == 5
    assert len(window._mark_overlays[0]._items) == items_with_values - 1
    window.show_mark_values_checkbox.setChecked(True)

    row = next(i for i in range(window.marks_table.rowCount())
               if window.marks_table.item(i, 0).data(Qt.UserRole) == mark.object_id)
    window.marks_table.item(row, 0).setCheckState(Qt.Unchecked)
    assert not manager.object(mark.object_id).visible
    assert mark.number not in window._mark_overlays[0]._targets
    window.marks_table.item(row, 0).setCheckState(Qt.Checked)
    assert manager.object(mark.object_id).visible

    manager.select_object(region.object_id)
    window._refresh_mark_ui()
    window.numeric_x_edit.setText(str(float(x[130])))
    window._apply_numeric_position()
    assert manager.object(region.object_id).x == pytest.approx(x[130])
    old_end = manager.object(region.object_id).x2
    window.numeric_y_edit.setText("abc")
    window._apply_numeric_position()
    assert manager.object(region.object_id).x2 == old_end

    manager.set_visible(mark.object_id, False)
    selected = region.object_id
    manager.select_object(selected)
    window.maximize_plot_button.setChecked(True)
    window.maximize_plot_button.setChecked(False)
    assert not manager.object(mark.object_id).visible
    assert manager.selected_id == selected
    assert window.show_mark_values_checkbox.isChecked()
    window.close()


def test_real_2d_partial_numeric_and_transform_label_refresh(qapp, tmp_path):
    from app.gui.main_window import MainWindow
    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / "axis.json"))
    window.open_file(str(BIG_FILE))
    window.mode_combo.setCurrentIndex(1)
    grid = window.plot_2d_widget._grid
    manager = window._mark_managers[1]
    mark = manager.add_nearest(grid.x_values[10], grid.y_values[20])
    moved = manager.position_mark(mark.number, y=float(grid.y_values[30]))
    assert moved.x == pytest.approx(grid.x_values[10])
    crosshair = manager.add_crosshair(grid.x_values[20], grid.y_values[30])
    moved = manager.position_annotation(crosshair.object_id, x=float(grid.x_values[40]))
    assert moved.y == pytest.approx(grid.y_values[30])
    assert moved.value == pytest.approx(grid.z_values[30, 40])
    window.close()


@pytest.mark.parametrize("path", [SMALL_FILE, BIG_FILE, FLUX_FILE, S31_FILE])
def test_real_s21_s31_flux_viewer_regression(qapp, tmp_path, path):
    from app.gui.main_window import MainWindow
    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / f"{path.name}.json"))
    window.open_file(str(path))
    assert window.experiment is not None
    assert window.plot_widget._curve is not None
    if path == FLUX_FILE:
        x, _ = window.plot_widget._curve.getData()
        assert len(x) == 10001
    window.close()


def test_10001_point_numeric_interaction_performance(qapp, tmp_path):
    from app.gui.main_window import MainWindow
    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / "axis.json"))
    window.open_file(str(FLUX_FILE))
    x, y = window.plot_widget._curve.getData()
    manager = window._mark_managers[0]
    point = manager.add_nearest(x[100], y[100])
    region = manager.add_range(x[1000], x[5000])
    line = manager.add_vertical_line(x[3000])
    crosshair = manager.add_crosshair(x[4000], y[4000])
    started = perf_counter()
    manager.position_mark(point.number, x=x[8000])
    manager.position_annotation(region.object_id, x=x[2000])
    manager.position_annotation(line.object_id, x=x[6000])
    manager.position_annotation(crosshair.object_id, x=x[7000])
    window._refresh_mark_ui()
    assert perf_counter() - started < 1.0
    window.close()
