"""v0.11B advanced Mark tools and visual-refinement acceptance tests."""

from __future__ import annotations

import os
from time import perf_counter

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.mark_model import (
    CROSSHAIR,
    HORIZONTAL_LINE,
    MARK_TOOLS,
    POINT_MARK,
    RANGE,
    TOOL_LABELS,
    VERTICAL_LINE,
    MarkManager,
)
from app.gui.mark_overlay import BRIGHT_GREEN, downward_pointer_path
from tests.real_data import BIG_FILE, FLUX_FILE, SMALL_FILE


pytestmark = pytest.mark.skipif(
    not all(path.exists() for path in (BIG_FILE, FLUX_FILE, SMALL_FILE)),
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
            "trace", [0, 1, 2, 3], [10, 11, 12, 13],
            x_name="Frequency", y_name="S21", x_unit="GHz", y_unit="dB",
            x_semantic="frequency", y_semantic="magnitude_db",
        )
    else:
        manager.set_2d_context(
            "grid", [1, 2, 4], [10, 20],
            [[11, 12, 14], [21, 22, 24]],
            x_name="Frequency", y_name="Current", value_name="S21",
            x_unit="GHz", y_unit="mA", value_unit="dB",
        )
    return manager


def _select_tool(window, tool):
    index = window.mark_tool_combo.findData(tool)
    assert index >= 0
    window.mark_tool_combo.setCurrentIndex(index)


def _combo_index(combo, name: str, base_channel: str) -> int:
    for index in range(combo.count()):
        candidate = combo.itemData(index)
        if candidate is not None and candidate.name == name and candidate.base_channel == base_channel:
            return index
    return -1


def test_tool_model_range_normalization_boundaries_width_and_whole_drag():
    manager = _manager()
    item = manager.add_range(3.0, 1.0)
    assert (item.x, item.x2, item.width) == pytest.approx((1.0, 3.0, 2.0))
    item = manager.move_annotation(item.object_id, x=1.5, x2=3.0)
    assert (item.x, item.x2, item.width) == pytest.approx((1.5, 3.0, 1.5))
    item = manager.move_annotation(item.object_id, x=5.0, x2=2.0)
    assert (item.x, item.x2, item.width) == pytest.approx((2.0, 5.0, 3.0))
    width = item.width
    item = manager.move_annotation(item.object_id, x=item.x + 4, x2=item.x2 + 4)
    assert item.width == pytest.approx(width)


def test_lines_crosshair_creation_drag_deletion_and_clear():
    manager = _manager("2d")
    hline = manager.add_horizontal_line(15)
    vline = manager.add_vertical_line(2.5)
    crosshair = manager.add_crosshair(2.2, 18.0)
    assert crosshair.value == pytest.approx(22.0)
    assert manager.move_annotation(hline.object_id, y=16.5).y == pytest.approx(16.5)
    assert manager.move_annotation(vline.object_id, x=3.5).x == pytest.approx(3.5)
    moved = manager.move_annotation(crosshair.object_id, x=3.9, y=9.0)
    assert (moved.x, moved.y, moved.value) == pytest.approx((4.0, 10.0, 14.0))
    assert manager.delete_object(hline.object_id)
    assert {item.annotation_type for item in manager.annotations()} == {VERTICAL_LINE, CROSSHAIR}
    manager.clear()
    assert manager.objects() == []


def test_reference_tool_transform_and_axis_semantic_safety():
    manager = _manager()
    point = manager.add_nearest(2, 12)
    range_item = manager.add_range(1, 3)
    hline = manager.add_horizontal_line(11)
    vline = manager.add_vertical_line(2)
    crosshair = manager.add_crosshair(2, 11)
    manager.set_1d_context(
        "trace", [0, 1, 2, 3], [100, 110, 120, 130],
        x_name="Frequency", y_name="S21", x_unit="GHz", y_unit="deg",
        x_semantic="frequency", y_semantic="phase",
    )
    ids = {item.object_id for item in manager.objects()}
    assert point.object_id in ids and manager.marks()[0].value == pytest.approx(120)
    assert range_item.object_id in ids and vline.object_id in ids
    assert hline.object_id not in ids and crosshair.object_id not in ids

    hline2 = manager.add_horizontal_line(115)
    manager.add_crosshair(2, 115)
    manager.set_1d_context(
        "trace", [3, 2, 1, 0], [100, 110, 120, 130],
        x_name="Imaginary", y_name="S21", y_unit="deg",
        x_semantic="imaginary", y_semantic="phase",
    )
    ids = {item.object_id for item in manager.objects()}
    assert hline2.object_id in ids
    assert not any(key.startswith((f"{RANGE}:", f"{VERTICAL_LINE}:", f"{CROSSHAIR}:")) for key in ids)


def test_tool_selector_has_five_painter_icons(qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    assert window.mark_tool_combo.itemData(0) is None
    assert window.mark_tool_combo.itemText(0) == "Choose Mark"
    assert [window.mark_tool_combo.itemData(i) for i in range(1, 6)] == list(MARK_TOOLS)
    assert [window.mark_tool_combo.itemText(i) for i in range(1, 6)] == [TOOL_LABELS[t] for t in MARK_TOOLS]
    pixmaps = [window.mark_tool_combo.itemIcon(i).pixmap(20, 20) for i in range(1, 6)]
    assert all(not pixmap.isNull() for pixmap in pixmaps)
    assert all(pixmap.toImage().sizeInBytes() > 0 for pixmap in pixmaps)
    window.close()


def test_1d_pointer_has_fixed_visual_gap_above_sample():
    bounds = downward_pointer_path().boundingRect()
    assert bounds.bottom() == pytest.approx(-0.55)
    assert 6.0 <= abs(bounds.bottom() * 14) <= 10.0
    assert bounds.top() < bounds.bottom() < 0


def test_two_click_range_creation_and_overlay_drag_callbacks(qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.open_file(str(SMALL_FILE))
    x, _ = window.plot_widget._curve.getData()
    _select_tool(window, RANGE)
    window.add_mark_button.setChecked(True)
    window._place_mark(0, float(x[300]), 0.0)
    assert window._range_starts[0] == pytest.approx(x[300])
    assert window._mark_overlays[0]._range_preview is not None
    assert window.add_mark_button.isChecked()
    window._place_mark(0, float(x[100]), 0.0)
    item = window._mark_managers[0].annotations()[0]
    assert (item.x, item.x2) == pytest.approx((x[100], x[300]))
    assert not window.add_mark_button.isChecked()

    region = window._mark_overlays[0]._annotation_items[item.object_id]
    width = item.width
    region.setRegion((item.x + 1000, item.x2 + 1000))
    region.sigRegionChangeFinished.emit(region)
    moved = window._mark_managers[0].annotations()[0]
    assert moved.width == pytest.approx(width)
    assert moved.x == pytest.approx(item.x + 1000)
    window.close()


def test_mixed_tool_selection_delete_and_clear_in_viewer(qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.open_file(str(SMALL_FILE))
    x, y = window.plot_widget._curve.getData()
    manager = window._mark_managers[0]
    manager.add_nearest(x[50], y[50])
    manager.add_nearest(x[100], y[100])
    range_item = manager.add_range(x[120], x[220])
    manager.add_horizontal_line(y[200])
    manager.add_vertical_line(x[250])
    manager.add_crosshair(x[300], y[300])
    window._refresh_mark_ui()
    assert window.marks_table.rowCount() == 6
    # v0.12E adds one exact-sample target ring for each 1D Point Mark.
    assert len(window._mark_overlays[0]._items) == 11  # includes Range label + 2 target rings
    manager.select_object(range_item.object_id)
    window._delete_selected_mark()
    assert len(manager.objects()) == 5
    assert all(item.object_id != range_item.object_id for item in manager.objects())
    window._clear_marks()
    assert manager.objects() == []
    window.close()


def test_selector_creation_and_line_crosshair_drag_signals(qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.open_file(str(SMALL_FILE))
    x, y = window.plot_widget._curve.getData()
    for tool, px, py in (
        (HORIZONTAL_LINE, x[100], y[100]),
        (VERTICAL_LINE, x[200], y[200]),
        (CROSSHAIR, x[300], y[300]),
    ):
        _select_tool(window, tool)
        window.add_mark_button.setChecked(True)
        window._place_mark(0, float(px), float(py))
        assert not window.add_mark_button.isChecked()

    manager = window._mark_managers[0]
    by_type = {item.annotation_type: item for item in manager.annotations()}
    hline = window._mark_overlays[0]._annotation_items[by_type[HORIZONTAL_LINE].object_id]
    hline.setValue(float(y[150]))
    hline.sigPositionChangeFinished.emit(hline)
    assert next(item for item in manager.annotations()
                if item.annotation_type == HORIZONTAL_LINE).y == pytest.approx(y[150])

    vline = window._mark_overlays[0]._annotation_items[by_type[VERTICAL_LINE].object_id]
    vline.setValue(float(x[250]))
    vline.sigPositionChangeFinished.emit(vline)
    assert next(item for item in manager.annotations()
                if item.annotation_type == VERTICAL_LINE).x == pytest.approx(x[250])

    _, _, handle = window._mark_overlays[0]._annotation_items[by_type[CROSSHAIR].object_id]
    handle.setPos(float(x[350]), float(y[350]))
    handle.sigPositionChangeFinished.emit(handle)
    crosshair = next(item for item in manager.annotations() if item.annotation_type == CROSSHAIR)
    assert (crosshair.x, crosshair.y) == pytest.approx((x[350], y[350]))
    window.close()


def test_real_1d_visual_state_drag_zoom_pan_maximize_and_transforms(qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.resize(1200, 780)
    window.show()
    window.open_file(str(SMALL_FILE))
    x, y = window.plot_widget._curve.getData()
    manager = window._mark_managers[0]
    for index in (100, 250, 400):
        manager.add_nearest(x[index], y[index])
    manager.add_range(x[80], x[320])
    manager.add_horizontal_line(y[180])
    manager.add_vertical_line(x[280])
    manager.add_crosshair(x[330], y[330])
    window._refresh_mark_ui()
    first = manager.marks()[0]
    window._move_mark(0, first.number, float(x[140]), float(y[140]))
    assert manager.marks()[0].sample_index == 140
    target = window._mark_overlays[0]._targets[1]
    assert target._path.boundingRect().bottom() < 0
    stored = [(item.object_id, item.x, item.y, item.x2) for item in manager.annotations()]
    window.plot_widget.plot_widget.getViewBox().setRange(xRange=(x[100], x[300]), padding=0)
    window.maximize_plot_button.setChecked(True)
    qapp.processEvents()
    assert window._mark_overlays[0]._targets[1].isVisible()
    window.maximize_plot_button.setChecked(False)
    assert stored == [(item.object_id, item.x, item.y, item.x2) for item in manager.annotations()]

    for transform in ("Real", "Imaginary", "Magnitude", "Phase"):
        window.transform_combo.setCurrentIndex(window.transform_combo.findText(transform))
        assert manager.marks()
    window.transform_combo.setCurrentIndex(window.transform_combo.findText("Magnitude"))
    window.db_checkbox.setChecked(True)
    assert manager.marks()[0].y_unit == "dB"
    window.transform_combo.setCurrentIndex(window.transform_combo.findText("Phase"))
    window.unwrap_checkbox.setChecked(True)
    assert manager.marks()
    window.close()


def test_real_2d_fixed_green_points_and_all_tools(qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.resize(1400, 900)
    window.show()
    window.open_file(str(BIG_FILE))
    window.mode_combo.setCurrentIndex(1)
    qapp.processEvents()
    grid = window.plot_2d_widget._grid
    manager = window._mark_managers[1]
    flat = grid.z_values.reshape(-1)
    levels = np.nanpercentile(flat, [3, 35, 65, 97])
    for level in levels:
        iy, ix = np.unravel_index(np.nanargmin(np.abs(grid.z_values - level)), grid.z_values.shape)
        manager.add_nearest(grid.x_values[ix], grid.y_values[iy])
    range_item = manager.add_range(grid.x_values[100], grid.x_values[350])
    hline = manager.add_horizontal_line(grid.y_values[300])
    vline = manager.add_vertical_line(grid.x_values[250])
    crosshair = manager.add_crosshair(grid.x_values[300], grid.y_values[400])
    manager.select_object(manager.marks()[3].object_id)
    window._refresh_mark_ui()

    assert all(target.brush.color().name() == BRIGHT_GREEN for target in window._mark_overlays[1]._targets.values())
    selected = window._mark_overlays[1]._targets[4]
    assert selected.scale > window._mark_overlays[1]._targets[1].scale
    assert crosshair.value == pytest.approx(grid.z_values[400, 300])
    region = window._mark_overlays[1]._annotation_items[range_item.object_id]
    assert 20 <= region.currentBrush.color().alpha() <= 60

    window._move_annotation(1, hline.object_id, np.nan, grid.y_values[320], np.nan)
    window._move_annotation(1, vline.object_id, grid.x_values[270], np.nan, np.nan)
    window._move_annotation(1, crosshair.object_id, grid.x_values[310], grid.y_values[410], np.nan)
    moved_crosshair = next(item for item in manager.annotations() if item.object_id == crosshair.object_id)
    assert moved_crosshair.value == pytest.approx(grid.z_values[410, 310])
    window.plot_2d_widget.view_box.setRange(
        xRange=(grid.x_values[100], grid.x_values[500]), padding=0
    )
    window.maximize_plot_button.setChecked(True)
    qapp.processEvents()
    assert all(item.isVisible() for item in window._mark_overlays[1]._items)
    window.maximize_plot_button.setChecked(False)
    window.close()


def test_real_flux_large_data_tools_performance_and_transform_value(qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.open_file(str(FLUX_FILE))
    x, y = window.plot_widget._curve.getData()
    assert len(x) == 10001
    manager = window._mark_managers[0]
    started = perf_counter()
    point = manager.add_nearest(x[7000], y[7000])
    manager.move_nearest(point.number, x[8000], y[8000])
    range_item = manager.add_range(x[2000], x[6000])
    manager.move_annotation(range_item.object_id, x=x[2500], x2=x[6500])
    vline = manager.add_vertical_line(x[5000])
    manager.move_annotation(vline.object_id, x=x[5100])
    crosshair = manager.add_crosshair(x[4000], y[4000])
    manager.move_annotation(crosshair.object_id, x=x[4200], y=y[4200])
    assert perf_counter() - started < 1.0

    window.mode_combo.setCurrentIndex(1)
    qapp.processEvents()
    grid = window.plot_2d_widget._grid
    manager = window._mark_managers[1]
    point = manager.add_nearest(grid.x_values[1800], grid.y_values[150])
    manager.add_range(grid.x_values[2000], grid.x_values[6000])
    manager.add_horizontal_line(grid.y_values[300])
    manager.add_vertical_line(grid.x_values[5000])
    crosshair = manager.add_crosshair(grid.x_values[4000], grid.y_values[400])
    window._refresh_mark_ui()
    assert point.value == pytest.approx(grid.z_values[150, 1800])
    assert crosshair.value == pytest.approx(grid.z_values[400, 4000])
    window.transform_combo_2d.setCurrentIndex(window.transform_combo_2d.findData("phase_deg"))
    phase_grid = window.plot_2d_widget._grid
    assert manager.marks()[0].value == pytest.approx(phase_grid.z_values[150, 1800])
    updated_cross = next(item for item in manager.annotations() if item.object_id == crosshair.object_id)
    assert updated_cross.value == pytest.approx(phase_grid.z_values[400, 4000])
    window.close()


def test_real_iq_tools_are_accurate_then_clear_on_axis_change(qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.open_file(str(SMALL_FILE))
    window.x_combo.setCurrentIndex(_combo_index(window.x_combo, "Imaginary", "VNA - S21"))
    window.y_combo.setCurrentIndex(_combo_index(window.y_combo, "Real", "VNA - S21"))
    x, y = window.plot_widget._curve.getData()
    manager = window._mark_managers[0]
    point = manager.add_nearest(x[120], y[120])
    manager.add_range(x[50], x[200])
    manager.add_horizontal_line(y[100])
    manager.add_vertical_line(x[150])
    crosshair = manager.add_crosshair(x[220], y[220])
    assert (point.x, point.value) == pytest.approx((x[120], y[120]))
    assert (crosshair.x, crosshair.y) == pytest.approx((x[220], y[220]))
    window.x_combo.setCurrentIndex(_combo_index(window.x_combo, "Frequency", "VNA - S21"))
    assert manager.objects() == []
    window.close()
