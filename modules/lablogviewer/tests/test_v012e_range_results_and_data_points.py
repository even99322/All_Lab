"""v0.12E range-owned Half-Peak and real-sample display regression tests."""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.mark_model import HalfPeakResult, MarkManager
from app.core.axis_preset_store import AxisPresetStore
from app.core.local_analysis import HALF_PEAK, SELECTED_RANGE
from app.gui.mark_overlay import MarkOverlay
from app.gui.plot_widget import Plot1DWidget
from app.gui.multi_pane import TWO_SIDE
from tests.real_data import BIG_FILE


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _half_peak(seed: float = 0.0) -> HalfPeakResult:
    return HalfPeakResult(
        extremum_x=seed + 5.0, extremum_value=-20.0, baseline=-5.0,
        half_level=-12.5, left_crossing=seed + 4.0, right_crossing=seed + 6.0,
        width=2.0, sample_index=50,
    )


def _manager() -> MarkManager:
    manager = MarkManager()
    x = np.linspace(0.0, 10.0, 101)
    manager.set_1d_context("v012e", x, np.sin(x), x_name="X", y_name="Magnitude")
    return manager


def test_half_peak_is_owned_by_its_range_and_invalidates_only_that_range():
    manager = _manager()
    first = manager.add_range(1.0, 4.0)
    second = manager.add_range(6.0, 9.0)
    assert first and second
    assert manager.set_range_half_peak(first.object_id, _half_peak())
    assert manager.set_range_half_peak(second.object_id, _half_peak(10.0))

    manager.move_annotation(first.object_id, x=1.2)
    assert manager.range_half_peak(first.object_id) is None
    assert manager.range_half_peak(second.object_id) == _half_peak(10.0)

    manager.position_annotation(second.object_id, x2=8.8)
    assert manager.range_half_peak(second.object_id) is None


def test_range_half_peak_visuals_are_independent_and_delete_cleanly(qapp):
    import pyqtgraph as pg

    widget = pg.PlotWidget()
    overlay = MarkOverlay(widget.getPlotItem(), widget)
    overlay.show_half_peak_result(1.0, 2.0, -10.0, range_id="Range:1")
    overlay.show_half_peak_result(4.0, 5.0, -12.0, range_id="Range:2")
    assert set(overlay._range_analysis_result_items) == {"Range:1", "Range:2"}
    first_items = tuple(overlay._range_analysis_result_items["Range:1"])
    overlay.clear_analysis_result("Range:1")
    assert "Range:1" not in overlay._range_analysis_result_items
    assert "Range:2" in overlay._range_analysis_result_items
    assert all(item not in widget.getPlotItem().items for item in first_items)
    overlay.clear_analysis_result("Range:2")
    widget.close()


def test_range_half_peak_run_persists_then_move_or_delete_clears_only_its_visual(qapp, tmp_path):
    from app.gui.main_window import MainWindow

    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / "axis.json"))
    x = np.linspace(-4.0, 4.0, 801)
    y = 8.0 * np.exp(-np.square(x / 0.8))
    window.plot_widget.plot(x, y)
    manager = window._mark_managers[0]
    manager.set_1d_context("half-run", x, y, x_name="X", y_name="Magnitude")
    first = manager.add_range(-3.0, 3.0)
    second = manager.add_range(-3.0, 3.0)
    window.analysis_region_combo.setCurrentText(SELECTED_RANGE)
    window.analysis_operation_combo.setCurrentText(HALF_PEAK)
    window._half_peak_supported = lambda: True

    for region in (first, second):
        manager.select_object(region.object_id)
        window._analysis_target_id = region.object_id
        window._run_local_analysis()
        assert manager.range_half_peak(region.object_id) is not None
        assert region.object_id in window._mark_overlays[0]._range_analysis_result_items

    window._live_move_annotation(0, first.object_id, -2.8, np.nan, 3.0)
    assert manager.range_half_peak(first.object_id) is None
    assert first.object_id not in window._mark_overlays[0]._range_analysis_result_items
    assert manager.range_half_peak(second.object_id) is not None

    manager.select_object(second.object_id)
    window._delete_selected_mark()
    assert second.object_id not in window._mark_overlays[0]._range_analysis_result_items
    window.close()


def test_real_sample_points_are_cached_coordinates_with_auto_manual_zoom_density(qapp):
    widget = Plot1DWidget()
    widget.resize(700, 400)
    widget.show()
    x = np.linspace(0.0, 100.0, 10_001)
    y = np.sin(x)
    widget.plot(x, y)
    qapp.processEvents()
    widget.set_data_points(True, "Auto")
    item = widget._data_point_items[0]
    shown_x, shown_y = item.getData()
    assert shown_x.size < x.size
    assert np.all(np.isin(shown_x, x))
    assert np.allclose(shown_y, np.sin(shown_x))
    full_size = item.opts["size"]

    widget.plot_widget.setXRange(20.0, 21.0, padding=0)
    qapp.processEvents()
    zoom_x, _zoom_y = item.getData()
    assert 0 < zoom_x.size < shown_x.size
    assert item.opts["size"] > full_size

    widget.set_data_points(True, "Manual", 7.5)
    assert item.opts["size"] == pytest.approx(7.5)
    widget.set_data_points(False)
    assert not item.isVisible()
    widget.close()


def test_multitrace_points_keep_real_coordinates_and_per_trace_visibility(qapp):
    widget = Plot1DWidget()
    widget.resize(600, 350)
    widget.show()
    x = np.arange(100.0)
    widget.plot_traces({3: (x, x), 7: (x, -x)}, active_trace=3, auto_range=True)
    widget.set_data_points(True)
    qapp.processEvents()
    assert set(widget._data_point_items) == {3, 7}
    for trace, item in widget._data_point_items.items():
        px, py = item.getData()
        expected = x if trace == 3 else -x
        assert np.all(np.isin(px, x))
        assert np.all(np.isin(py, expected))
    widget.plot_traces({3: (x, x)}, active_trace=3)
    assert set(widget._data_point_items) == {3}
    widget.close()


def test_point_mark_has_an_exact_sample_target_even_when_ordinary_points_are_off(qapp):
    import pyqtgraph as pg

    x = np.array([0.0, 0.2, 0.9, 1.7])
    y = np.array([3.0, 2.0, 4.0, 1.0])
    manager = MarkManager()
    manager.set_1d_context("mark", x, y, x_name="X", y_name="Y")
    mark = manager.add_at_trace_position(2)
    widget = pg.PlotWidget()
    overlay = MarkOverlay(widget.getPlotItem(), widget)
    overlay.render(manager.marks(), [], mark.object_id, show_values=True)
    ring = overlay._sample_targets[mark.number]
    ring_x, ring_y = ring.getData()
    assert (ring_x[0], ring_y[0]) == pytest.approx((x[2], y[2]))
    assert ring.opts["size"] == 11

    moved = manager.move_to_trace_position(mark.number, 3)
    overlay.update_object(moved, show_values=False)
    ring_x, ring_y = ring.getData()
    assert (ring_x[0], ring_y[0]) == pytest.approx((x[3], y[3]))
    widget.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real point-trace fixture unavailable")
def test_point_settings_are_per_pane_and_never_request_new_hdf_data(qapp, tmp_path):
    from app.gui.main_window import MainWindow

    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / "real-axis.json"))
    window.show()
    window.open_file(str(BIG_FILE))
    window.pane_layout_combo.setCurrentText(TWO_SIDE)
    qapp.processEvents()
    before = window.cached.cache.stats()["misses"]
    window._activate_pane(1)
    window.show_data_points_checkbox.setChecked(True)
    window.point_size_mode_combo.setCurrentText("Manual")
    window.manual_point_size_spin.setValue(6.0)
    first = window._pane_states[1]
    assert (first.show_data_points, first.point_size_mode, first.manual_point_size) == (True, "Manual", 6.0)
    assert window._pane_frames[1].plot_1d._data_point_items

    window._activate_pane(2)
    second = window._pane_states[2]
    assert not second.show_data_points
    window._pane_frames[1].plot_1d.plot_widget.setXRange(5.022e9, 5.023e9, padding=0)
    qapp.processEvents()
    assert window.cached.cache.stats()["misses"] == before
    window.close()
