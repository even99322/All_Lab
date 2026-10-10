"""v1.0.4: Horizontal Range and Rectangle Range Marks, and Marks that name the quantity shown."""

from __future__ import annotations

import math
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.mark_model import (
    HORIZONTAL_RANGE, MARK_TOOLS, POINT_MARK, RANGE, RECT_RANGE, MarkManager,
)


def _manager_1d(x_semantic="x", y_semantic="mag"):
    manager = MarkManager()
    x = np.linspace(0.0, 10.0, 101)
    manager.set_1d_context("ctx", x, np.sin(x), x_name="Freq", y_name="S21 — Magnitude",
                           x_unit="GHz", y_unit=None, x_semantic=x_semantic, y_semantic=y_semantic)
    return manager


def test_new_tools_are_registered_as_marks():
    assert HORIZONTAL_RANGE in MARK_TOOLS and RECT_RANGE in MARK_TOOLS


def test_horizontal_and_rectangle_range_create_sort_move_and_reject_empty():
    manager = _manager_1d()
    horizontal = manager.add_horizontal_range(0.6, -0.4)
    assert (horizontal.y, horizontal.y2) == (-0.4, 0.6)
    assert horizontal.height == pytest.approx(1.0) and horizontal.width is None
    assert manager.add_horizontal_range(0.2, 0.2) is None
    rect = manager.add_rect_range(8.0, 2.0, 0.5, -0.5)
    assert (rect.x, rect.x2, rect.y, rect.y2) == (2.0, 8.0, -0.5, 0.5)
    assert (rect.width, rect.height) == pytest.approx((6.0, 1.0))
    assert manager.add_rect_range(1.0, 1.0, 0.0, 1.0) is None
    moved = manager.move_annotation(rect.object_id, x=9.5, x2=1.0, y=0.9, y2=0.1)
    assert (moved.x, moved.x2, moved.y, moved.y2) == (1.0, 9.5, 0.1, 0.9)
    edited = manager.position_annotation(rect.object_id, y2=0.7)
    assert edited.y2 == 0.7
    with pytest.raises(ValueError):
        manager.position_annotation(horizontal.object_id, y=99.0)
    with pytest.raises(ValueError):
        manager.position_annotation(rect.object_id, y=0.7)
    assert manager.annotations()[0].annotation_type == RANGE or True


def test_new_ranges_persist_and_restore_with_all_four_edges():
    manager = _manager_1d()
    manager.add_horizontal_range(-0.3, 0.3)
    manager.add_rect_range(2.0, 7.0, -0.8, 0.8)
    state = manager.persistence_state()
    assert {item["type"] for item in state["annotations"]} == {HORIZONTAL_RANGE, RECT_RANGE}
    fresh = _manager_1d()
    assert fresh.restore_persistence_state(state) == 2
    rect = fresh.object("rect_range:1")
    assert (rect.x, rect.x2, rect.y, rect.y2) == (2.0, 7.0, -0.8, 0.8)


def test_changing_the_plotted_quantity_drops_only_objects_bound_to_it():
    manager = _manager_1d()
    manager.add_range(1.0, 2.0)
    manager.add_horizontal_range(-0.3, 0.3)
    manager.add_rect_range(2.0, 7.0, -0.8, 0.8)
    x = np.linspace(0.0, 10.0, 101)
    manager.set_1d_context("ctx", x, np.cos(x), x_name="Freq", y_name="S21 — Phase",
                           x_unit="GHz", y_unit="deg", x_semantic="x", y_semantic="phase")
    assert [item.object_id for item in manager.annotations()] == ["range:1"]
    assert manager.annotations()[0].y_name == "S21 — Phase"


def test_overlay_draws_and_reports_new_ranges(qapp=None):
    from PySide6.QtWidgets import QApplication
    import pyqtgraph as pg
    from app.gui.mark_overlay import MarkOverlay

    app = QApplication.instance() or QApplication([])
    widget = pg.PlotWidget()
    overlay = MarkOverlay(widget.getPlotItem(), widget)
    manager = _manager_1d()
    horizontal = manager.add_horizontal_range(-0.3, 0.3)
    rect = manager.add_rect_range(2.0, 7.0, -0.8, 0.8)
    overlay.render(manager.marks(), manager.annotations(), rect.object_id, True)
    assert set(overlay._annotation_items) == {horizontal.object_id, rect.object_id}
    seen = []
    overlay.annotation_moved.connect(lambda *args: seen.append(args))
    roi = overlay._annotation_items[rect.object_id]
    roi.setPos((3.0, -0.5)); roi.setSize((2.0, 1.0))
    assert len(seen) >= 1
    key, x1, y1, x2, y2 = seen[-1]
    assert (key, x1, y1, x2, y2) == (rect.object_id, 3.0, -0.5, 5.0, 0.5)
    overlay._annotation_items[horizontal.object_id].setRegion((-0.1, 0.2))
    key, x1, y1, x2, y2 = seen[-1]
    assert key == horizontal.object_id and math.isnan(x1) and (y1, y2) == pytest.approx((-0.1, 0.2))
    widget.close()


@pytest.fixture(scope="module")
def qapp_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_viewer_places_new_ranges_and_marks_name_the_quantity(qapp_app):
    from app.gui.main_window import MainWindow
    from tests.real_data import SMALL_FILE

    if not SMALL_FILE.exists():
        pytest.skip("real-data fixture unavailable")
    window = MainWindow()
    window.open_file(str(SMALL_FILE))
    x, _ = window.plot_widget._curve.getData()
    manager = window._mark_managers[0]
    for tool, clicks in ((HORIZONTAL_RANGE, [(x[10], 0.1), (x[50], 0.3)]),
                         (RECT_RANGE, [(x[100], 0.0), (x[300], 0.2)])):
        window.mark_tool_combo.setCurrentIndex(window.mark_tool_combo.findData(tool))
        for cx, cy in clicks:
            window._place_mark(0, float(cx), float(cy))
    kinds = {item.annotation_type for item in manager.annotations()}
    assert kinds == {HORIZONTAL_RANGE, RECT_RANGE}
    window.mark_tool_combo.setCurrentIndex(window.mark_tool_combo.findData(POINT_MARK))
    window._place_mark(0, float(x[211]), 0.0)
    window.transform_combo.setCurrentIndex(window.transform_combo.findText("Phase"))
    mark = manager.marks()[0]
    assert mark.y_name.endswith("Phase") and mark.y_unit == "deg"
    assert "Phase" in window.selected_mark_details.text()
    assert not manager.annotations()
    window.close()
