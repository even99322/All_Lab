"""
tests/test_gui_2d_smoke.py — Phase 6

Smoke tests for the 2D Heatmap plot mode, run via Qt's "offscreen"
platform plugin. Covers the required Phase 6 test checklist:
opening both samples, building the full Frequency x Average Current x
S21 surface with correct shape, complex transforms (magnitude dB,
phase), colormap switching, and color-range changes - all without
crashing, plus confirming the original 40 tests remain green
(verified separately by running the full suite, not duplicated here).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.real_data import BIG_FILE, SMALL_FILE

try:
    from PySide6.QtWidgets import QApplication
    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not PYSIDE_AVAILABLE, reason="PySide6 not installed in this environment."
)

samples_pytestmark = pytest.mark.skipif(
    not (SMALL_FILE.exists() and BIG_FILE.exists()),
    reason="Real Labber sample files not present in this environment.",
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _suppress_dialogs(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))


# 1. small sample can be opened (in 2D mode context) -------------------------

@samples_pytestmark
def test_small_sample_opens_in_2d_context(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(SMALL_FILE))
    win.mode_combo.setCurrentIndex(1)  # switch to 2D Heatmap mode

    assert win.z_combo_2d.count() == 1
    assert win.z_combo_2d.itemText(0) == "VNA - S21"
    # no active sweep -> no valid Y candidate -> no 2D plot possible,
    # but this must not crash the app
    assert win.y_combo_2d.count() == 0

    win.close()


# 2. big sample can be opened -------------------------------------------------

@samples_pytestmark
def test_big_sample_opens_and_2d_mode_available(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.mode_combo.setCurrentIndex(1)

    assert win.mode_stack.currentIndex() == 1
    assert win.z_combo_2d.itemText(0) == "VNA - S21"
    assert win.x_combo_2d.itemText(0) == "Frequency"
    assert win.y_combo_2d.itemText(0) == "Average Current"

    win.close()


# 3 & 4. big sample builds Frequency x Average Current x S21, correct shape --

@samples_pytestmark
def test_big_sample_full_surface_shape(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.mode_combo.setCurrentIndex(1)

    grid = win.plot_2d_widget._grid
    assert grid is not None
    assert grid.z_values.shape == (855, 501)
    assert grid.x_name == "Frequency"
    assert grid.y_name == "Average Current"

    img_shape = win.plot_2d_widget.img_item.image.shape
    assert img_shape == (501, 855)  # (nx, ny) after GUI-boundary transpose

    win.close()


# 5. complex transform: Magnitude dB (default) ---------------------------------

@samples_pytestmark
def test_default_transform_is_magnitude_db(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.mode_combo.setCurrentIndex(1)

    assert win.transform_combo_2d.currentData() == "magnitude_db"
    grid = win.plot_2d_widget._grid
    assert np.isfinite(grid.z_values).all()

    win.close()


# 6. Phase transform ------------------------------------------------------------

@samples_pytestmark
def test_switch_to_phase_transform(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.mode_combo.setCurrentIndex(1)

    idx = win.transform_combo_2d.findData("phase_deg")
    win.transform_combo_2d.setCurrentIndex(idx)

    grid = win.plot_2d_widget._grid
    assert grid.transform == "phase_deg"
    assert grid.z_values.min() >= -180.01
    assert grid.z_values.max() <= 180.01

    win.close()


# 7. changing colormap does not crash -------------------------------------------

@samples_pytestmark
def test_changing_colormap_does_not_crash(qapp):
    from app.gui.main_window import MainWindow
    from app.gui.plot_2d_widget import COLORMAPS

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.mode_combo.setCurrentIndex(1)

    for cmap in COLORMAPS:
        win.colormap_combo.setCurrentText(cmap)
        assert win.plot_2d_widget._colormap_name == cmap

    win.close()


# 8. changing color range does not crash ----------------------------------------

@samples_pytestmark
def test_changing_color_range_does_not_crash(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.mode_combo.setCurrentIndex(1)

    win.auto_range_checkbox.setChecked(False)
    win.zmin_spin.setValue(-60)
    win.zmax_spin.setValue(0)
    assert win.plot_2d_widget._z_min == -60
    assert win.plot_2d_widget._z_max == 0

    win.auto_range_button.click()
    assert win.auto_range_checkbox.isChecked()
    assert win.plot_2d_widget._z_min is not None

    win.close()


# ---- additional coverage: crosshair, 1D mode untouched, error handling ------

@samples_pytestmark
def test_crosshair_readout_matches_grid_value(qapp):
    """Cross-checks the crosshair coordinate readout against the raw
    grid array to catch any axis-orientation regression."""
    from app.gui.main_window import MainWindow
    from PySide6.QtCore import QPointF

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.mode_combo.setCurrentIndex(1)

    widget = win.plot_2d_widget
    grid = widget._grid
    ix, iy = 250, 400
    expected_z = grid.z_values[iy, ix]

    scene_pos = widget.view_box.mapViewToScene(QPointF(grid.x_values[ix], grid.y_values[iy]))
    widget._on_mouse_moved(scene_pos)

    assert f"{expected_z:.4g}" in widget.coord_label.text()
    assert "Frequency" in widget.coord_label.text()
    assert "Average Current" in widget.coord_label.text()

    win.close()


@samples_pytestmark
def test_1d_mode_still_works_after_2d_additions(qapp):
    """Regression guard: Phase 6 additions must not break Phase 5A's
    1D plot flow."""
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))

    assert win.mode_stack.currentIndex() == 0  # 1D is still the default
    # v0.9B: y_combo (Y Axis) now lists ALL dynamically-discovered axis
    # candidates, not just log channels - "VNA - S21" is still among
    # them, just not necessarily first (item order changed by design).
    y_items = [win.y_combo.itemText(i) for i in range(win.y_combo.count())]
    assert "VNA - S21" in y_items
    curve = win.plot_widget._curve
    assert curve is not None
    xdata, ydata = curve.getData()
    assert len(xdata) == 501
    assert np.isfinite(ydata).all()

    win.close()


@samples_pytestmark
def test_invalid_2d_combination_shows_warning_not_crash(qapp, monkeypatch):
    """spec §12: an invalid X/Y/Z combination must show a clear
    message and NOT crash or clear the existing plot."""
    from app.gui.main_window import MainWindow

    _suppress_dialogs(monkeypatch)

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.mode_combo.setCurrentIndex(1)

    grid_before = win.plot_2d_widget._grid
    assert grid_before is not None

    # force an invalid combination directly (X mismatched with Z's trace axis)
    win.x_combo_2d.blockSignals(True)
    win.x_combo_2d.clear()
    win.x_combo_2d.addItem("Average Current")  # invalid for a vector Z channel
    win.x_combo_2d.blockSignals(False)
    win._rebuild_2d_plot()  # must not raise

    # existing plot must be preserved, not cleared
    assert win.plot_2d_widget._grid is grid_before

    win.close()
