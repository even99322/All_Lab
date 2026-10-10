from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from app.analysis.yig_fitting.ui.analysis_pane import FitPlotWidget
from app.analysis.yig_fitting.ui.plots import DataPlotWidget, PhasePlotWidget
from app.analysis.yig_mirror.results.model import AnalysisPoint


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _events(qapp):
    qapp.processEvents()


def _sample():
    frequency = np.linspace(5.0e9, 5.1e9, 501)
    measured = (1 - 0.4 / (1 + 1j * (frequency - 5.045e9) / 2e6))
    fitted = (1 - 0.38 / (1 + 1j * (frequency - 5.044e9) / 2.1e6))
    return frequency, measured, fitted


def test_fit_results_are_six_independent_plots_and_render_current_layout(qapp, tmp_path):
    plot = FitPlotWidget()
    plot.resize(1500, 950)
    plot.show()
    frequency, measured, fitted = _sample()
    plot.plot(frequency, measured, fitted, np.abs(fitted), title="Trace 4")
    _events(qapp)

    assert len(plot.panes) == 6
    assert len({id(pane.figure) for pane in plot.panes}) == 6
    assert all(len(pane.figure.axes) == 1 for pane in plot.panes)
    assert all(pane.axis.lines for pane in plot.panes)
    assert "Trace 4" in plot.panes[0].axis.get_title()

    grid = plot.render_all_image()
    assert grid is not None and not grid.isNull()
    assert grid.width() >= 1920 and grid.height() >= 1080
    plot.copy_all()
    copied = QApplication.clipboard().image()
    assert copied.width() == grid.width() and copied.height() == grid.height()
    grid_path = tmp_path / "grid.png"
    assert plot.save_all_to(str(grid_path))
    assert (grid_path.stat().st_size > 10_000)

    plot.set_mode("focus")
    plot.focus_splitter.setSizes([650, 250])
    _events(qapp)
    ratio_before = plot._focus_ratio()
    assert sum(pane._compact for pane in plot.panes) == 5
    plot.set_active_pane(3)
    _events(qapp)
    assert plot.active_pane == 3
    assert sum(pane._compact for pane in plot.panes) == 5
    assert plot._focus_ratio() == pytest.approx(ratio_before, abs=0.04)

    focused = plot.render_all_image()
    assert focused is not None and not focused.isNull()
    plot.copy_all()
    copied_focus = QApplication.clipboard().image()
    assert copied_focus.width() == focused.width() and copied_focus.height() == focused.height()
    drag_image, _ = plot.render_drag_payload(None, True)
    assert drag_image.size() == focused.size()
    focus_path = tmp_path / "focus.png"
    assert plot.save_all_to(str(focus_path))
    assert focus_path.stat().st_size > 10_000
    active_path = tmp_path / "active.png"
    assert plot.save_active_to(str(active_path))
    assert active_path.stat().st_size > 1_000

    plot.toggle_focus(3)
    _events(qapp)
    assert plot.mode == "grid"
    assert not any(pane._compact for pane in plot.panes)
    plot.close()


def test_single_fit_result_export_uses_shared_high_resolution_renderer(qapp, tmp_path):
    from PySide6.QtGui import QImage

    from app.analysis.yig_fitting.controller import FitController

    plot = FitPlotWidget()
    plot.resize(1400, 900)
    plot.show()
    frequency, measured, fitted = _sample()
    plot.plot(frequency, measured, fitted, np.abs(fitted), title="Export check")
    _events(qapp)

    meta = {
        "file": "sample.hdf5", "file_path": "/data/sample.hdf5",
        "formula_path": "model.py", "func": "S11_single", "s_name": "VNA - S11",
        "idx": 0, "axis": "Current", "axis_val": 0.1,
        "f1": float(frequency[0]), "f2": float(frequency[-1]), "npts": frequency.size,
    }
    result = {
        "params": np.array([1.0]), "errors": np.array([0.01]),
        "r2": 0.99, "r2_complex": 0.98, "chi2_red": 0.02,
    }
    statuses = []
    controller = SimpleNamespace(
        fit_ctx={"meta": meta, "names": ["amplitude"], "units": ["a.u."]},
        fit_result=result,
        w=SimpleNamespace(fit_plot=plot),
        status=statuses.append,
    )
    csv_path = tmp_path / "single-fit.csv"
    FitController.write_single_result(controller, str(csv_path))

    png_path = tmp_path / "single-fit.png"
    image = QImage(str(png_path))
    assert csv_path.is_file()
    assert not image.isNull()
    assert image.width() >= 1920 and image.height() >= 1080
    assert statuses and "single-fit.png" in statuses[-1]
    plot.close()


def test_preview_map_and_slice_are_independent_shareable_plots(qapp):
    preview = DataPlotWidget()
    preview.resize(1100, 700)
    preview.show()
    frequency = np.linspace(4.0, 5.0, 201)
    sweep = np.linspace(0, 1, 31)
    image = -np.abs(np.sin(np.linspace(0, np.pi, 31)))[:, None] * np.exp(
        -((frequency[None, :] - 4.5) / 0.08) ** 2
    )
    preview.set_map(frequency, sweep, image.T, "Current", "VNA - S21")
    preview.set_slice(9)
    _events(qapp)

    assert preview.map_pane.fig.axes
    assert preview.slice_pane.fig.axes
    assert preview.map_pane.fig is not preview.slice_pane.fig
    assert preview.idx == 9
    exported = preview.slice_pane._render_composite()
    assert exported.width() >= 1920
    assert exported.height() >= 1080
    assert preview.interaction_controls.scope == "active"
    preview.cmb_drag_scope.setCurrentIndex(1)
    assert preview.is_drag_all()
    routed = []
    preview.render_drag_payload = lambda pane_id, all_panes: routed.append(
        (pane_id, all_panes)
    ) or (None, "test")
    preview.map_pane._render_drag_payload(0, False)
    assert routed == [(0, True)]
    preview.set_share_mode(True)
    assert preview.map_pane._share_mode and preview.slice_pane._share_mode
    preview.set_share_mode(False)
    preview.close()


def test_phase_workspace_shows_map_and_keeps_physical_coarse_overlays_distinct(qapp, tmp_path):
    plot = PhasePlotWidget()
    plot.resize(1400, 900)
    plot.show()
    frequency = np.linspace(5.0e9, 5.08e9, 101)
    sweep = np.linspace(0, 1, 31)
    values = np.exp(1j * (frequency[:, None] / 1e8)) * (
        1 - 0.4 / (1 + 1j * (frequency[:, None] - (5.02e9 + sweep[None, :] * 4e7)) / 2e6)
    )
    points = [
        AnalysisPoint("Physical Node", "Fitting-Based Physical Analysis", 5.025e9),
        AnalysisPoint("Physical Antinode", "Fitting-Based Physical Analysis", 5.055e9),
        AnalysisPoint("Coarse Node Candidate", "Coarse Detector", 5.03e9,
                      sweep_value=0.25),
        AnalysisPoint("Coarse Antinode Candidate", "Coarse Detector", 5.06e9,
                      sweep_value=0.75),
    ]
    plot.plot(
        kap=(np.linspace(5.01e9, 5.07e9, 31), np.linspace(0, 8, 31), None, "MHz"),
        phi=(np.linspace(5.01e9, 5.07e9, 31), np.linspace(0, np.pi, 31)),
        line={"T_ns": 1.0, "phi_ref": 0.2, "f_ref": 5.04e9, "kappa_b": 8.0},
        nodes=[(0, 5.025e9)], period=np.pi,
        source_map={"frequency_hz": frequency, "sweep_values": sweep,
                    "complex_values": values, "sweep_name": "Current (A)"},
        points=points,
        trajectory=(sweep, 5.02e9 + sweep * 4e7, np.ones(sweep.size, dtype=bool)),
    )
    _events(qapp)

    assert len(plot.panes) == 4
    assert all(len(pane.fig.axes) == 1 for pane in plot.panes[:2])
    assert len(plot.panes[2].fig.axes) == 2  # heatmap plus its colorbar
    assert len(plot.panes[3].fig.axes) == 1
    assert plot.interaction_controls.scope == "active"
    plot.cmb_drag_scope.setCurrentIndex(1)
    routed = []
    plot.render_drag_payload = lambda pane_id, all_panes: routed.append(
        (pane_id, all_panes)
    ) or (None, "test")
    plot.panes[2]._render_drag_payload(2, False)
    assert routed == [(2, True)]
    map_axis = plot.panes[2].fig.axes[0]
    labels = [item.get_label() for item in map_axis.lines]
    assert "Fitted resonance" in labels
    legend_labels = [text.get_text() for text in map_axis.get_legend().get_texts()]
    assert "Physical Node" in legend_labels
    assert "Physical Antinode" in legend_labels
    assert "Coarse Node Candidate" in legend_labels
    assert "Coarse Antinode Candidate" in legend_labels

    state = plot.layout_state()
    plot.pane_splitter.setSizes([800, 500])
    plot.apply_layout_state(state)
    _events(qapp)
    all_path = tmp_path / "phase-all.png"
    assert plot.save_all_to(str(all_path))
    assert all_path.stat().st_size > 10_000
    active_path = tmp_path / "phase-active.svg"
    assert plot.save_active_to(str(active_path))
    assert active_path.stat().st_size > 100
    active_svg = active_path.read_text(encoding="utf-8")
    assert "<path" in active_svg
    all_svg_path = tmp_path / "phase-all.svg"
    assert plot.save_all_to(str(all_svg_path))
    all_svg = all_svg_path.read_text(encoding="utf-8")
    assert "<image" in all_svg
    plot.close()


def test_phase_splitter_state_is_normalized_and_clamped(qapp):
    plot = PhasePlotWidget()
    plot.resize(1200, 800)
    plot.show()
    _events(qapp)
    plot.pane_splitter.setSizes([800, 400])
    plot.left_splitter.setSizes([250, 400])
    state = plot.layout_state()
    assert state["outer_ratio"] == pytest.approx(2 / 3, abs=0.03)
    assert 0.15 <= state["left_ratio"] <= 0.85
    plot.apply_layout_state({"outer_ratio": 100, "left_ratio": -2, "right_ratio": 0.7})
    _events(qapp)
    restored = plot.layout_state()
    assert restored["outer_ratio"] == pytest.approx(0.85, abs=0.03)
    assert restored["left_ratio"] == pytest.approx(0.15, abs=0.03)
    assert restored["right_ratio"] == pytest.approx(0.7, abs=0.03)
    plot.close()


def test_native_qt_controls_open_select_resize_and_switch_modes(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    plot = FitPlotWidget()
    plot.resize(1500, 950)
    plot.show()
    _events(qapp)
    plot.cmb_drag_scope.showPopup()
    _events(qapp)
    assert plot.cmb_drag_scope.view().isVisible()
    QTest.keyClick(plot.cmb_drag_scope, Qt.Key.Key_Down)
    QTest.keyClick(plot.cmb_drag_scope, Qt.Key.Key_Enter)
    _events(qapp)
    assert plot.cmb_drag_scope.currentData() == "all"

    QTest.mouseClick(plot.btn_focus, Qt.MouseButton.LeftButton)
    _events(qapp)
    assert plot.mode == "focus"
    assert plot.panes[1]._compact
    plot.set_share_mode(True)
    assert plot.share_mode and plot.btn_share.isChecked()
    plot.set_share_mode(False)
    plot.btn_grid.click()
    _events(qapp)
    assert plot.mode == "grid"
    plot.btn_view_all.click()
    plot.close()


def test_grid_focus_grid_cycles_preserve_ranges_and_keep_compact_panes_in_bounds(qapp):
    from PySide6.QtCore import QRect

    plot = FitPlotWidget()
    plot.resize(920, 680)
    plot.show()
    frequency, measured, fitted = _sample()
    plot.plot(frequency, measured, fitted, np.abs(fitted), title="layout regression")
    _events(qapp)
    plot.panes[0].axis.set_xlim(5.02, 5.06)
    plot.panes[0].axis.set_ylim(-4, 1)
    for focused in (0, 3, 5, 2):
        plot.set_active_pane(focused)
        plot.set_mode("focus")
        plot.resize(740, 560)
        _events(qapp)
        for index, pane in enumerate(plot.panes):
            if index == focused:
                continue
            pane_pos = pane.mapTo(plot.focus_strip, pane.rect().topLeft())
            pane_rect = QRect(pane_pos, pane.size())
            strip = plot.focus_strip.rect()
            assert pane_rect.left() >= strip.left()
            assert pane_rect.right() <= strip.right()
        plot.set_mode("grid")
        _events(qapp)
        assert all(not pane._compact for pane in plot.panes)
        assert plot.panes[0].axis.get_xlim() == pytest.approx((5.02, 5.06))
        plot.set_mode("focus")
        _events(qapp)
        plot.set_mode("grid")
        _events(qapp)
    plot.reset_layout()
    _events(qapp)
    assert plot.mode == "grid"
    assert plot.active_pane == 0
    assert plot.panes[0].axis.get_xlim() == pytest.approx((5.02, 5.06))
    assert not hasattr(plot, "btn_copy_active")
    assert not hasattr(plot, "btn_copy_all")
    plot.close()


def test_viewer_and_analysis_pointer_controls_default_to_pointer_and_allow_scope(qapp):
    from app.gui.plot_interaction import PlotInteractionControls

    controls = PlotInteractionControls()
    assert not controls.share_mode
    assert controls.scope == "active"
    controls.set_multi_pane_available(True)
    controls.scope_combo.setCurrentIndex(1)
    assert controls.scope == "all"
    controls.btn_share.click()
    assert controls.share_mode
    controls.btn_pointer.click()
    assert not controls.share_mode
    controls.close()
