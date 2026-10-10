from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.mark_store import MarkStore
from app.core.named_view_store import NamedViewStore
from app.core.plot_config import PlotConfig
from app.core.viewer_display_state_store import ViewerDisplayStateStore
from tests.real_data import FLUX_FILE, SMALL_FILE


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def _window(tmp_path, path):
    from app.gui.main_window import MainWindow

    window = MainWindow(
        named_view_store=NamedViewStore(tmp_path / "views.json"),
        mark_store=MarkStore(tmp_path / "marks.json"),
        viewer_display_state_store=ViewerDisplayStateStore(tmp_path / "display.json"),
    )
    window.open_file(str(path))
    return window


@pytest.mark.skipif(not SMALL_FILE.exists(), reason="Real sample file is unavailable.")
def test_1d_operations_and_equation_toggle(qapp, tmp_path):
    window = _window(tmp_path, SMALL_FILE)
    panel = window.plot_config_1d
    _, base = window.plot_widget._curve.getData()
    panel.operation_combo.setCurrentText("y-<y>")
    panel.changed.emit()
    window.plot_widget._curve.getData()
    _, shifted = window.plot_widget._curve.getData()
    assert np.isclose(np.nanmean(shifted), 0.0, atol=1e-9 * max(1.0, np.nanmax(np.abs(base))))
    panel.operation_combo.setCurrentText("None")
    panel.changed.emit()
    _, again = window.plot_widget._curve.getData()
    assert np.allclose(again, base, equal_nan=True)

    window.y_formula_edit.setText("y^2")
    window.formula_enable_button.setChecked(True)
    _, squared = window.plot_widget._curve.getData()
    assert np.allclose(squared, base ** 2, equal_nan=True)
    window.formula_enable_button.setChecked(False)
    assert window.y_formula_edit.text() == "y^2"
    _, plain = window.plot_widget._curve.getData()
    assert np.allclose(plain, base, equal_nan=True)
    window.close()


@pytest.mark.skipif(not FLUX_FILE.exists(), reason="Real sample file is unavailable.")
def test_2d_operations_and_equations(qapp, tmp_path):
    window = _window(tmp_path, FLUX_FILE)
    window.mode_combo.setCurrentIndex(1)
    assert window.z_combo_2d.count() > 0 and window.y_combo_2d.currentText()
    state = window._pane_states[window._active_pane_id]
    raw = window._build_2d_grid(
        window.z_combo_2d.currentText(), window.x_combo_2d.currentText(),
        window.y_combo_2d.currentText(), window.transform_combo_2d.currentData() or "raw", state,
    )
    state.config = PlotConfig(operation="y-<y>")
    centred = window._build_2d_grid(
        window.z_combo_2d.currentText(), window.x_combo_2d.currentText(),
        window.y_combo_2d.currentText(), window.transform_combo_2d.currentData() or "raw", state,
    )
    assert np.allclose(np.nanmean(centred.z_values, axis=1), 0.0, atol=1e-9)
    assert centred.z_values.shape == raw.z_values.shape
    state.config = PlotConfig(third_operation="d/dx")
    ddy = window._build_2d_grid(
        window.z_combo_2d.currentText(), window.x_combo_2d.currentText(),
        window.y_combo_2d.currentText(), window.transform_combo_2d.currentData() or "raw", state,
    )
    assert ddy.operation_label and ddy.z_values.shape == raw.z_values.shape
    window.plot_config_2d.operation_combo.setCurrentText("Histogram")
    window.plot_config_2d.changed.emit()
    window.y_formula_edit.setText("y*2")
    window.formula_enable_button.setChecked(True)
    assert window._pane_states[window._active_pane_id].formula_enabled
    window.close()


def test_config_round_trips_through_pane_payload():
    from app.gui.main_window import MainWindow
    from app.gui.multi_pane import PaneState

    state = PaneState(1)
    state.config = PlotConfig(operation="FFT", smooth=5, third_operation="d/dx")
    state.fixed_indices = {"Time": 3}
    payload = MainWindow._pane_state_payload(state)
    other = PaneState(1)
    MainWindow._apply_pane_state_payload(other, payload)
    assert other.config == state.config and other.fixed_indices == {"Time": 3}


@pytest.mark.skipif(not SMALL_FILE.exists(), reason="Real sample file is unavailable.")
def test_1d_histogram_2d_opens_iq_dialog_and_rotation_calculate(qapp, tmp_path):
    window = _window(tmp_path, SMALL_FILE)
    panel = window.plot_config_1d
    panel.operation_combo.setCurrentText("Histogram-2D")
    panel.changed.emit()
    dialog = window._iq_histogram_dialog
    assert dialog is not None and dialog.grid is not None
    assert dialog.grid.z_values.sum() > 0
    panel.operation_combo.setCurrentText("None")
    panel.changed.emit()
    window._calculate_rotation("fixed_rotation")
    window._calculate_rotation("rotation_per_x")
    assert np.isfinite(panel.fixed_rotation_spin.value())
    assert window._active_config().operation == "None"
    window.close()


def test_plot_in_db_checkbox_follows_transform(qtbot=None):
    from app.gui.plot_config_panel import PlotConfigPanel
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    panel = PlotConfigPanel("1d")
    got = []
    panel.db_toggled.connect(got.append)
    assert not panel.plot_in_db_check.isEnabled()
    panel.set_db_state(True, True)
    assert panel.plot_in_db_check.isChecked() and got == []
    panel.plot_in_db_check.setChecked(False)
    assert got == [False]
