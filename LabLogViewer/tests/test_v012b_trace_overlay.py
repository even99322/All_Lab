"""v0.12B multi-trace overlay acceptance and regression tests."""

from __future__ import annotations

import os
from time import perf_counter

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.core.axis_preset_store import AxisPresetStore
from app.core.trace_overlay import (
    DISTINCT, SEQUENTIAL, SINGLE_COLOR, SEQUENTIAL_START,
    adaptive_inactive_opacity, build_trace_styles, contrast_against_white,
)
from app.gui.main_window import MainWindow
from app.gui.plot_widget import Plot1DWidget
from tests.real_data import BIG_FILE, FLUX_FILE, LONG_TRACE_FILE, S31_FILE, SMALL_FILE


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _rgb(curve):
    color = curve.opts["pen"].color()
    return color.red(), color.green(), color.blue()


def _window(path, tmp_path):
    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / f"{path.name}.json"))
    window.open_file(str(path))
    return window


def _select(window, traces):
    first, *rest = traces
    window.log_entries.select_row(first)
    for trace in rest:
        window.log_entries.select_row(trace, Qt.ControlModifier)


def test_sequential_palette_order_contrast_and_active_emphasis():
    traces = (19, 29, 39, 49, 59)
    styles = build_trace_styles(traces, 39, SEQUENTIAL)
    colors = [styles[trace].color for trace in traces]
    luminance_order = [sum(color) for color in colors]
    assert luminance_order == sorted(luminance_order, reverse=True)
    assert colors[0] == SEQUENTIAL_START
    assert contrast_against_white(colors[0]) >= 3.0
    assert styles[39].width > styles[29].width
    assert styles[39].opacity == 1.0
    assert styles[39].z > styles[59].z


def test_distinct_and_single_color_are_deterministic_and_active_independent():
    traces = (2, 8, 20, 40, 60)
    first = build_trace_styles(traces, 20, DISTINCT)
    second = build_trace_styles(traces, 60, DISTINCT)
    assert len({style.color for style in first.values()}) == 5
    assert [first[t].color for t in traces] == [second[t].color for t in traces]
    single = build_trace_styles(traces, 20, SINGLE_COLOR)
    assert len({style.color for style in single.values()}) == 1
    assert single[20].width > single[8].width


def test_adaptive_opacity_reduces_for_dense_overlays():
    assert adaptive_inactive_opacity(5) > adaptive_inactive_opacity(25)
    assert adaptive_inactive_opacity(25) > adaptive_inactive_opacity(75)
    assert adaptive_inactive_opacity(75) > adaptive_inactive_opacity(500)


def test_plot_widget_reuses_curves_removes_stale_and_preserves_independent_x(qapp):
    widget = Plot1DWidget()
    traces = {
        19: (np.array([0.0, 1.0, 2.0]), np.array([1.0, 2.0, 3.0])),
        29: (np.array([0.0, 1.1, 2.4]), np.array([3.0, 2.0, 1.0])),
        39: (np.array([0.0, 2.0]), np.array([2.0, 4.0])),
    }
    assert widget.plot_traces(traces, active_trace=29, auto_range=True) == (19, 29, 39)
    identities = {trace: id(curve) for trace, curve in widget._curves.items()}
    x29, _ = widget._curves[29].getData()
    assert x29.tolist() == [0.0, 1.1, 2.4]
    widget.plot_traces({19: traces[19], 39: traces[39]}, active_trace=39)
    assert set(widget._curves) == {19, 39}
    assert id(widget._curves[19]) == identities[19]
    assert widget._curve is widget._curves[39]


def test_plot_widget_skips_invalid_trace_and_adapts_legend(qapp):
    widget = Plot1DWidget()
    good = (np.arange(4), np.arange(4))
    bad = (np.arange(3), np.arange(4))
    assert widget.plot_traces({0: good, 1: bad}, active_trace=0) == (0,)
    widget.plot_traces({i: good for i in range(5)}, active_trace=2)
    assert widget.legend.isVisible()
    assert len(widget.legend.items) == 5
    widget.plot_traces({i: good for i in range(50)}, active_trace=20)
    assert len(widget.legend.items) == 1
    assert "50 traces" in widget.legend.items[0][1].text


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real 855-trace fixture unavailable")
def test_real_basic_overlay_color_modes_active_change_and_curve_reuse(qapp, tmp_path):
    window = _window(BIG_FILE, tmp_path)
    _select(window, (19, 29, 39))
    assert tuple(window.plot_widget._curves) == (19, 29, 39)
    identities = {trace: id(curve) for trace, curve in window.plot_widget._curves.items()}
    sequential_colors = {trace: _rgb(curve) for trace, curve in window.plot_widget._curves.items()}
    window.trace_selection.active_trace = 29
    window.update_plot(preserve_view=True)
    assert {trace: id(curve) for trace, curve in window.plot_widget._curves.items()} == identities
    assert {trace: _rgb(curve) for trace, curve in window.plot_widget._curves.items()} == sequential_colors
    assert window.plot_widget._curves[29].opts["pen"].widthF() > window.plot_widget._curves[19].opts["pen"].widthF()
    window.trace_color_combo.setCurrentText(DISTINCT)
    assert len({_rgb(curve) for curve in window.plot_widget._curves.values()}) == 3
    window.trace_color_combo.setCurrentText(SINGLE_COLOR)
    assert len({_rgb(curve) for curve in window.plot_widget._curves.values()}) == 1
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real 855-trace fixture unavailable")
def test_real_add_remove_transform_zoom_marks_and_analysis_stay_active(qapp, tmp_path):
    window = _window(BIG_FILE, tmp_path)
    _select(window, (19, 29, 39))
    view_box = window.plot_widget.plot_widget.getPlotItem().getViewBox()
    view_box.setXRange(7.55e9, 7.56e9, padding=0)
    before = tuple(view_box.viewRange()[0])
    window.log_entries.select_row(49, Qt.ControlModifier)
    after = tuple(view_box.viewRange()[0])
    assert after == pytest.approx(before)
    window.log_entries.select_row(29, Qt.ControlModifier)
    assert 29 not in window.plot_widget._curves
    assert window.log_entries.current_row() == 49
    window.trace_selection.active_trace = 39
    window.update_plot(preserve_view=True)
    active = window.log_entries.current_row()
    assert window._mark_managers[0].context_key[-1] == active
    assert window._mark_managers[0]._x.size == window.plot_widget._trace_data[active][0].size
    window.transform_combo.setCurrentText("Real")
    active_x, active_y = window.plot_widget._curves[active].getData()
    assert np.allclose(active_y, window._mark_managers[0]._y)
    assert active_x.size == active_y.size
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real 855-trace fixture unavailable")
def test_real_855_large_overlay_is_recoverable_and_uses_one_cached_raw_array(qapp, tmp_path):
    window = _window(BIG_FILE, tmp_path)
    started = perf_counter()
    window.log_entries.select_row(99, Qt.ShiftModifier)
    elapsed_100 = perf_counter() - started
    assert len(window.plot_widget._curves) == 100
    assert len(window.plot_widget.legend.items) == 1
    assert elapsed_100 < 8.0
    before_misses = window.cached.cache.stats()["misses"]
    window.log_entries.select_row(49, Qt.ShiftModifier)
    assert len(window.plot_widget._curves) == 50
    assert window.cached.cache.stats()["misses"] == before_misses
    window.close()


@pytest.mark.skipif(not LONG_TRACE_FILE.exists(), reason="Real 10,001-point fixture unavailable")
def test_real_10001_point_twenty_trace_overlay_performance(qapp, tmp_path):
    window = _window(LONG_TRACE_FILE, tmp_path)
    started = perf_counter()
    window.log_entries.select_row(19, Qt.ShiftModifier)
    elapsed = perf_counter() - started
    assert len(window.plot_widget._curves) == 20
    assert all(curve.getData()[0].size == 10001 for curve in window.plot_widget._curves.values())
    assert elapsed < 8.0
    window.close()


@pytest.mark.parametrize("path", [SMALL_FILE, S31_FILE])
def test_s21_s31_single_and_multi_trace_pipeline(qapp, tmp_path, path):
    if not path.exists():
        pytest.skip("real fixture unavailable")
    window = _window(path, tmp_path)
    available = min(3, window.log_entries.row_count())
    _select(window, tuple(range(available)))
    assert len(window.plot_widget._curves) == available
    for curve in window.plot_widget._curves.values():
        x, y = curve.getData()
        assert x.size == y.size and x.size > 0
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real IQ fixture unavailable")
def test_multi_iq_uses_each_trace_and_viewers_are_independent(qapp, tmp_path):
    first = _window(BIG_FILE, tmp_path)
    second = _window(BIG_FILE, tmp_path)
    first.x_combo.setCurrentText("Imaginary")
    first.y_combo.setCurrentText("Real")
    _select(first, (0, 1, 2))
    assert len(first.plot_widget._curves) == 3
    assert all(curve.getData()[0].size for curve in first.plot_widget._curves.values())
    first.trace_color_combo.setCurrentText(DISTINCT)
    assert second.trace_color_combo.currentText() == SEQUENTIAL
    assert len(second.plot_widget._curves) == 1
    first.close()
    second.close()


@pytest.mark.skipif(not FLUX_FILE.exists(), reason="Real Flux fixture unavailable")
def test_flux_2d_regression_after_overlay(qapp, tmp_path):
    window = _window(FLUX_FILE, tmp_path)
    window.mode_combo.setCurrentIndex(1)
    grid = window.plot_2d_widget._grid
    assert grid is not None
    assert grid.z_values.shape == (681, 10001)
    window.close()
