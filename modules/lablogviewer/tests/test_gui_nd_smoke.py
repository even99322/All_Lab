"""
tests/test_gui_nd_smoke.py — Phase 7

Smoke tests for the N-D Slice Explorer plot mode, run via Qt's
"offscreen" platform plugin. Uses the two real sample files (which
only exercise 0-1 active dimensions) for the GUI wiring checks, since
that's what's actually available end-to-end through main.py; the
deeper 3D/4D correctness checks live in test_nd_slice_synthetic.py
(core layer, no Qt needed).

Covers the Phase 7 GUI checklist:
  J. GUI smoke test (this file)
  K. confirm the GUI still never imports h5py
  L. regression: v0.4 tests (1D/2D/complex/crosshair) still pass -
     verified by running the full suite, not duplicated here.
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


# K. GUI must never import h5py - extends the Phase 5A static check to
# the new slice_widget.py module explicitly.

def test_slice_widget_does_not_import_h5py():
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


# J. GUI smoke tests -----------------------------------------------------------

@samples_pytestmark
def test_3d_surface_opens_as_an_analysis_window(qapp):
    """v0.18B: 3D lives in its own window (Analysis > 3D Surface...)."""
    from app.gui.main_window import MainWindow

    win = MainWindow()
    assert not win.surface_3d_action.isEnabled()
    win.open_file(str(BIG_FILE))

    assert win.mode_combo.count() == 2
    assert win.mode_stack.count() == 2 and win.plot_controls_stack.count() == 2
    assert win.surface_3d_action in win.analysis_menu.actions()
    assert win.surface_3d_action.isEnabled()
    assert not hasattr(win, "nd_visualization_combo")

    window = win.open_3d_window()
    assert window is not None and window.isVisible() and win._three_d_active()
    assert win.nd_plot_stack.currentIndex() == 2
    assert window.splitter.widget(1) is win._nd_plot_page
    assert window.controls_scroll.widget() is win._nd_controls_page
    assert window.windowTitle().startswith("3D Surface")
    window.close()
    assert not win._three_d_active()

    win.close()


@samples_pytestmark
def test_surface_big_file_uses_dynamic_xy_and_full_standard_grid(qapp):
    """With exactly 2 total dimensions (Frequency + Average Current),
    X/Y auto-select both of them, leaving 0 remaining slice
    dimensions - matches Phase 6's get_2d_data behavior exactly."""
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.open_3d_window()

    assert win.x_combo_nd.currentText() == "Frequency"
    assert win.y_combo_nd.currentData() == "Average Current"
    assert len(win.slice_explorer._rows) == 0
    assert win.nd_plot_stack.currentIndex() == 2
    assert win.nd_surface_renderer is None  # offscreen has no usable GL context
    assert "OpenGL context" in win.statusBar().currentMessage()

    grid = win.plot_2d_widget_nd._grid
    assert grid.z_values.shape == (855, 501)

    win.close()


@samples_pytestmark
def test_nd_result_matches_phase6_2d_heatmap(qapp):
    """Cross-check: the SAME data through the OLD Phase 6 page and the
    NEW Phase 7 ND page must agree exactly - proves Phase 7 didn't
    change what gets computed, only how it's reached."""
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))

    win.mode_combo.setCurrentIndex(1)  # old 2D Heatmap page
    old_grid = win.plot_2d_widget._grid

    win.open_3d_window()  # new ND page
    new_grid = win.plot_2d_widget_nd._grid

    assert np.allclose(old_grid.z_values, new_grid.z_values, equal_nan=True)
    assert np.array_equal(old_grid.x_values, new_grid.x_values)
    assert np.array_equal(old_grid.y_values, new_grid.y_values)

    win.close()


@samples_pytestmark
def test_slice_dimensions_are_hidden_when_no_extra_dimensions_remain(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.open_3d_window()

    assert len(win.slice_explorer._rows) == 0
    assert win.slice_explorer.isHidden()
    assert win.surface_slice_label.isHidden()
    assert win.nd_surface_placeholder.isVisible() or win.nd_surface_renderer is None

    win.close()


@samples_pytestmark
def test_surface_controls_keep_slice_semantics_in_advanced_area(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.open_3d_window()
    win.surface_advanced_button.click()
    assert not win.surface_advanced_content.isHidden()
    assert win.surface_slice_label.parentWidget() is win.surface_advanced_content
    win.surface_advanced_button.click()
    assert win.surface_advanced_content.isHidden()

    win.close()


def test_surface_controls_support_qt_interaction_localization_and_layout(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from app.localization import LocalizationManager
    from app.settings.store import SettingsStore

    localizer = LocalizationManager(SettingsStore(tmp_path / "settings.json"))
    monkeypatch.setattr(
        "app.gui.main_window.get_localization_manager", lambda: localizer
    )
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.resize(1050, 720)
    win.show()
    qapp.processEvents()

    def select_popup_item(combo, index):
        combo.setFocus()
        combo.showPopup()
        qapp.processEvents()
        view = combo.view()
        item_rect = view.visualRect(combo.model().index(index, 0))
        assert item_rect.isValid()
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=item_rect.center())
        qapp.processEvents()
        assert combo.currentIndex() == index

    win.open_3d_window()
    qapp.processEvents()

    select_popup_item(win.surface_rendering_combo, 3)
    assert win.surface_rendering_combo.currentData() == "Performance"
    select_popup_item(win.surface_projection_combo, 1)
    assert win.surface_projection_combo.currentData() == "orthographic"
    select_popup_item(win.colormap_combo_nd, win.colormap_combo_nd.findText("CoolWarm"))

    win.surface_advanced_button.click()
    assert not win.surface_advanced_content.isHidden()
    win.surface_advanced_button.click()
    assert win.surface_advanced_content.isHidden()

    win.surface_z_auto_checkbox.click()
    assert win.surface_z_scale_slider.isEnabled()
    win.surface_z_scale_slider.setValue(40)
    assert win.surface_z_scale_label.text() == "4.0×"
    win.surface_z_reset_button.click()
    assert win.surface_z_auto_checkbox.isChecked()
    assert win.surface_z_scale_slider.value() == 10

    win.maximize_plot_button.click()
    assert win.controls_panel.isHidden()
    assert win.toggle_channels_button.isHidden()
    assert win.maximize_plot_button.isVisible()
    win.maximize_plot_button.click()
    assert not win.controls_panel.isHidden()
    assert not win.toggle_channels_button.isHidden()

    win.toggle_channels_button.click()
    assert win.controls_panel.isHidden()
    win.toggle_channels_button.click()
    assert not win.controls_panel.isHidden()

    localizer.set_language("zh_TW")
    assert win.mode_combo.count() == 2
    assert win.surface_advanced_button.text() == "進階"
    # v0.19D: drop-down items keep their English text (code reads it) and are translated where drawn
    assert win.surface_projection_combo.itemText(1) == "Orthographic"
    assert localizer._translated(win.surface_projection_combo.itemText(1)) == "正交"
    localizer.set_language("en")
    assert win.surface_advanced_button.text() == "Advanced"

    win.resize(860, 640)
    qapp.processEvents()
    assert win.controls_scroll_area.isVisible()
    assert win.mode_stack.isVisible()
    win.close()


@samples_pytestmark
def test_nd_swap_x_and_y_via_combos(qapp):
    """Dynamic X/Y selection (spec §4): changing X to Average Current
    must repopulate Y to offer Frequency, and produce the transposed
    surface."""
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.open_3d_window()

    original_grid = win.plot_2d_widget_nd._grid
    assert original_grid.x_name == "Frequency"

    idx = win.x_combo_nd.findText("Average Current")
    win.x_combo_nd.setCurrentIndex(idx)

    assert "Frequency" in [win.y_combo_nd.itemText(i) for i in range(win.y_combo_nd.count())]
    # auto-selects Frequency as the new Y (only other dim available)
    assert win.y_combo_nd.currentData() == "Frequency"

    swapped_grid = win.plot_2d_widget_nd._grid
    assert swapped_grid.x_name == "Average Current"
    assert swapped_grid.y_name == "Frequency"
    assert np.allclose(swapped_grid.z_values.T, original_grid.z_values, equal_nan=True)

    win.close()


@samples_pytestmark
def test_nd_small_file_single_dimension(qapp):
    """The single-point file has only 1 total dimension (Frequency) -
    Y combo must offer only '(none - 1D)', forcing 1D mode, and this
    must not crash."""
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(SMALL_FILE))
    win.open_3d_window()

    assert win.x_combo_nd.count() == 1
    assert win.x_combo_nd.itemText(0) == "Frequency"
    assert win.y_combo_nd.count() == 1
    assert win.y_combo_nd.currentData() is None
    assert win.nd_plot_stack.currentIndex() == 2
    assert "X and Y" in win.nd_surface_placeholder.text()

    win.close()


@samples_pytestmark
def test_nd_colormap_and_range_do_not_crash(qapp):
    from app.gui.main_window import MainWindow
    from app.gui.plot_2d_widget import COLORMAPS

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.open_3d_window()

    for cmap in COLORMAPS:
        win.colormap_combo_nd.setCurrentText(cmap)
        assert win.plot_2d_widget_nd._colormap_name == cmap

    win.auto_range_checkbox_nd.setChecked(False)
    win.zmin_spin_nd.setValue(-50)
    win.zmax_spin_nd.setValue(0)
    assert win.plot_2d_widget_nd._z_min == -50

    win.auto_range_button_nd.click()
    assert win.auto_range_checkbox_nd.isChecked()

    win.close()


@samples_pytestmark
def test_nd_complex_transform_switch(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.open_3d_window()

    idx = win.transform_combo_nd.findData("phase_deg")
    win.transform_combo_nd.setCurrentIndex(idx)

    grid = win.plot_2d_widget_nd._grid
    assert grid.transform == "phase_deg"
    assert grid.z_values.min() >= -180.01
    assert grid.z_values.max() <= 180.01

    win.close()


@samples_pytestmark
def test_nd_invalid_state_does_not_crash(qapp, monkeypatch):
    """Forcing an inconsistent combo state (e.g. via rapid programmatic
    changes) must never crash the app, even if it produces a warning."""
    from app.gui.main_window import MainWindow
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.open_3d_window()

    # force X and Y to the same dimension directly, bypassing the
    # normal repopulation logic, then trigger a rebuild
    win.y_combo_nd.blockSignals(True)
    win.y_combo_nd.clear()
    win.y_combo_nd.addItem("Frequency", "Frequency")
    win.y_combo_nd.blockSignals(False)
    win._rebuild_nd_plot()  # must not raise

    win.close()


# L. Regression guard: opening a file and switching through all THREE
# modes in sequence must work without any of them interfering with
# the others' state.

@samples_pytestmark
def test_all_three_modes_coexist(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))

    # 1D
    win.mode_combo.setCurrentIndex(0)
    assert win.plot_widget._curve is not None

    # 2D (Phase 6, untouched)
    win.mode_combo.setCurrentIndex(1)
    assert win.plot_2d_widget._grid.z_values.shape == (855, 501)

    # N-D (Phase 7)
    win.open_3d_window()
    assert win.plot_2d_widget_nd._grid.z_values.shape == (855, 501)

    # back to 1D - still intact
    win.mode_combo.setCurrentIndex(0)
    assert win.plot_widget._curve is not None

    win.close()
