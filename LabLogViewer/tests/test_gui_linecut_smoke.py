"""
tests/test_gui_linecut_smoke.py — Phase 8

GUI smoke tests for the Line Cut panel, crosshair Z-value transform
consistency, and N-D Slice Explorer integration, run via Qt's
"offscreen" platform plugin.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.real_data import BIG_FILE

try:
    from PySide6.QtWidgets import QApplication
    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not PYSIDE_AVAILABLE, reason="PySide6 not installed in this environment."
)

samples_pytestmark = pytest.mark.skipif(
    not BIG_FILE.exists(), reason="Real Labber sample file not present in this environment."
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_linecut_widget_does_not_import_h5py():
    import ast

    gui_dir = Path(__file__).resolve().parent.parent / "app" / "gui"
    for py_file in gui_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "h5py", f"{py_file} imports h5py directly!"
            if isinstance(node, ast.ImportFrom):
                assert node.module != "h5py", f"{py_file} imports from h5py directly!"


def test_main_window_does_not_construct_obsolete_embedded_linecut_panels(qapp):
    from app.gui.main_window import MainWindow
    from app.gui.linecut_widget import LineCutWidget

    win = MainWindow()
    assert not win.findChildren(LineCutWidget)
    assert set(win._cut_windows) == {"x", "y"}

    win.close()


def test_linecut_widget_standalone_construction(qapp):
    from app.gui.linecut_widget import LineCutWidget

    w = LineCutWidget()
    assert not w.is_active("x")
    assert not w.is_active("y")
    w.update_hover(1.0, 2.0)
    w.clear()


@samples_pytestmark
def test_x_cut_can_be_triggered_and_shows_data(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.mode_combo.setCurrentIndex(1)

    win._show_cut_window("x")

    cut_window = win._cut_windows["x"]
    assert cut_window.grid is win.plot_2d_widget._grid
    curve = cut_window.panel.plot_widget._curve
    assert curve is not None
    xdata, ydata = curve.getData()
    assert len(xdata) == 501
    assert np.isfinite(ydata).all()

    win.close()


@samples_pytestmark
def test_y_cut_can_be_triggered_and_shows_data(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.mode_combo.setCurrentIndex(1)

    win._show_cut_window("y")

    cut_window = win._cut_windows["y"]
    assert cut_window.grid is win.plot_2d_widget._grid
    curve = cut_window.panel.plot_widget._curve
    xdata, ydata = curve.getData()
    assert len(xdata) == 855
    assert np.isfinite(ydata).all()

    win.close()


@samples_pytestmark
def test_line_cut_updates_live_with_crosshair(qapp):
    from app.gui.main_window import MainWindow
    from PySide6.QtCore import QPointF

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.mode_combo.setCurrentIndex(1)
    win._show_cut_window("x")
    cut_window = win._cut_windows["x"]

    _, y_before = cut_window.panel.plot_widget._curve.getData()
    title_before = cut_window.position_label.text()

    scene_pos = win.plot_2d_widget.view_box.mapViewToScene(QPointF(5.025e9, 162.9))
    win.plot_2d_widget._on_mouse_moved(scene_pos)

    _, y_after = cut_window.panel.plot_widget._curve.getData()
    title_after = cut_window.position_label.text()

    assert not np.allclose(y_before, y_after)
    assert title_before != title_after

    win.close()


@samples_pytestmark
def test_crosshair_z_value_matches_current_transform(qapp):
    from app.gui.main_window import MainWindow
    from PySide6.QtCore import QPointF

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.mode_combo.setCurrentIndex(1)

    scene_pos = win.plot_2d_widget.view_box.mapViewToScene(QPointF(5.025e9, 162.9))
    win.plot_2d_widget._on_mouse_moved(scene_pos)
    label_db = win.plot_2d_widget.coord_label.text()
    z_db = win.plot_2d_widget.last_hover[2]

    idx = win.transform_combo_2d.findData("phase_deg")
    win.transform_combo_2d.setCurrentIndex(idx)
    win.plot_2d_widget._on_mouse_moved(scene_pos)
    label_phase = win.plot_2d_widget.coord_label.text()
    z_phase = win.plot_2d_widget.last_hover[2]

    assert z_db != z_phase
    assert -180.01 <= z_phase <= 180.01
    assert label_db != label_phase

    win.close()


@samples_pytestmark
def test_both_x_and_y_cut_active_simultaneously(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.mode_combo.setCurrentIndex(1)

    win._show_cut_window("x")
    win._show_cut_window("y")

    x_curve = win._cut_windows["x"].panel.plot_widget._curve
    y_curve = win._cut_windows["y"].panel.plot_widget._curve
    assert x_curve is not None and y_curve is not None

    win.close()


@samples_pytestmark
def test_nd_page_line_cut_uses_current_slice(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.open_3d_window()
    # v0.18B: X/Y Cut is a Viewer tool; with the 3D window open it follows the
    # Viewer's 2D heatmap (the 3D window has its own slice controls).
    win.mode_combo.setCurrentIndex(1)
    win._show_cut_window("x")
    cut_window = win._cut_windows["x"]
    assert cut_window.grid is win.plot_2d_widget._grid
    curve = cut_window.panel.plot_widget._curve
    assert curve is not None
    xdata, ydata = curve.getData()
    assert len(xdata) == 501
    assert np.isfinite(ydata).all()

    win.close()


@samples_pytestmark
def test_3d_surface_mode_keeps_current_xy_cut_source(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.open_3d_window()

    assert win.nd_plot_stack.currentIndex() == 2
    # Opening the 3D window does not hijack the Viewer's cut source.
    assert win._cut_source() is None            # Viewer is in 1D
    win.mode_combo.setCurrentIndex(1)
    assert win._cut_source() is win.plot_2d_widget
    win._show_cut_window("x")
    assert win._cut_windows["x"].grid is win.plot_2d_widget._grid

    win.close()


@samples_pytestmark
def test_opening_new_file_clears_stale_line_cuts(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.mode_combo.setCurrentIndex(1)
    win._show_cut_window("x")
    previous_grid = win._cut_windows["x"].grid
    assert previous_grid is win.plot_2d_widget._grid

    win.open_file(str(BIG_FILE))
    current_grid = win.plot_2d_widget._grid
    assert current_grid is not None and current_grid is not previous_grid
    assert win._cut_windows["x"].grid is current_grid
    assert win._cut_windows["y"].grid is current_grid

    win.close()


@samples_pytestmark
def test_all_modes_and_linecut_coexist(qapp):
    from app.gui.main_window import MainWindow
    from app.gui.linecut_widget import LineCutWidget

    win = MainWindow()
    win.open_file(str(BIG_FILE))

    win.mode_combo.setCurrentIndex(0)
    assert win.plot_widget._curve is not None

    win.mode_combo.setCurrentIndex(1)
    assert win.plot_2d_widget._grid.z_values.shape == (855, 501)
    assert not win.findChildren(LineCutWidget)
    win._show_cut_window("x")
    assert win._cut_windows["x"].grid is win.plot_2d_widget._grid

    win.open_3d_window()
    assert win.plot_2d_widget_nd._grid.z_values.shape == (855, 501)
    assert not win.findChildren(LineCutWidget)
    # v0.18B: cut windows are Viewer tools and keep following the Viewer's
    # 2D heatmap while the separate 3D window is open.
    win._show_cut_window("y")
    assert win._cut_windows["y"].grid is win.plot_2d_widget._grid

    win.mode_combo.setCurrentIndex(0)
    assert win.plot_widget._curve is not None
    assert win._cut_source() is None

    win.close()
