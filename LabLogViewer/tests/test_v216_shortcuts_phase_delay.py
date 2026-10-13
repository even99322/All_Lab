"""v2.1.6: Viewer single-key shortcuts (customisable in Settings) and the Phase delay processing option."""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.mark_store import MarkStore
from app.core.named_view_store import NamedViewStore
from app.core.viewer_display_state_store import ViewerDisplayStateStore
from tests.test_nd_slice_synthetic import _build_synthetic_labber_file


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def fresh_shortcuts():
    from app.core import shortcuts

    shortcuts.reset()
    yield shortcuts
    shortcuts.reset()


# ---- phase delay math ---------------------------------------------------------------------------
def test_phase_delay_removes_a_cable_delay_and_is_fitted_back():
    from app.core.plot_config import PlotConfig, calculate_phase_delay_ns, delay_phasor, rotate_complex

    f_ghz = np.linspace(4.0, 6.0, 401)
    tau = 37.5                                            # ns
    resonance = 1 - 0.8 / (1 + 2j * (f_ghz - 5.0) / 0.01)  # a dip in the middle
    measured = 0.3 * resonance * np.exp(-2j * np.pi * f_ghz * 1e9 * tau * 1e-9)
    assert calculate_phase_delay_ns(f_ghz, measured, "GHz") == pytest.approx(tau, rel=1e-3)
    # same data with X in Hz
    assert calculate_phase_delay_ns(f_ghz * 1e9, measured, "Hz") == pytest.approx(tau, rel=1e-3)
    fixed = rotate_complex(f_ghz, measured, PlotConfig(phase_delay_ns=tau), "GHz")
    assert np.allclose(fixed, 0.3 * resonance)
    assert np.allclose(np.abs(delay_phasor(f_ghz, tau, "GHz")), 1.0)
    assert PlotConfig(phase_delay_ns=1.0).rotates() and not PlotConfig().rotates()
    assert PlotConfig.from_dict({"phase_delay_ns": "2.5"}).phase_delay_ns == 2.5
    assert PlotConfig.from_dict(PlotConfig(phase_delay_ns=3.0).to_dict()).phase_delay_ns == 3.0
    assert calculate_phase_delay_ns(f_ghz, np.abs(measured), "GHz") == 0.0     # real data: nothing to fit


def test_hz_per_x_unit():
    from app.core.plot_config import hz_per_x_unit

    assert hz_per_x_unit("GHz") == 1e9 and hz_per_x_unit("mhz") == 1e6 and hz_per_x_unit("Hz") == 1.0
    assert hz_per_x_unit(None) == 1.0 and hz_per_x_unit("A") == 1.0


# ---- shortcuts store ----------------------------------------------------------------------------
def test_shortcut_defaults_change_and_conflicts(qapp, fresh_shortcuts):
    s = fresh_shortcuts
    table = s.bindings()
    assert table["reset_view"] == "Esc" and table["point_mark"] == "M" and table["plot_1d"] == "1"
    assert table["plot_2d"] == "2" and table["maximize"] == "F"
    assert s.conflicts() == {}
    fired = []
    s.notifier.changed.connect(lambda: fired.append(1))
    s.set_key("maximize", "M")
    assert s.key_for("maximize") == "M" and "m" in s.conflicts()
    s.set_key("maximize", "")
    assert s.key_for("maximize") == "" and s.conflicts() == {}
    s.reset()
    assert s.key_for("maximize") == "F" and len(fired) == 3
    with pytest.raises(KeyError):
        s.set_key("nope", "X")


# ---- in a Viewer ----------------------------------------------------------------------------------
@pytest.fixture()
def viewer(qapp, tmp_path, fresh_shortcuts):
    from app.gui.main_window import MainWindow

    path = tmp_path / "trace.hdf5"
    _build_synthetic_labber_file(path, [("Current", "A", np.linspace(0, 2, 4))], np.linspace(4e9, 6e9, 51))
    window = MainWindow(named_view_store=NamedViewStore(tmp_path / "v.json"),
                        mark_store=MarkStore(tmp_path / "m.json"),
                        viewer_display_state_store=ViewerDisplayStateStore(tmp_path / "d.json"))
    window.resize(1200, 800)
    window.show()
    window.open_file(str(path))
    qapp.processEvents()
    yield window
    window.close()


def _press(qapp, window, key: str) -> None:
    window.key_shortcuts.trigger(next(a for a, k in window.key_shortcuts._items.items() if k.key().toString() == key))
    qapp.processEvents()


def test_viewer_shortcuts(qapp, viewer):
    from app.core.mark_model import POINT_MARK

    w = viewer
    assert w.key_shortcuts.key("reset_view") == "Esc"
    _press(qapp, w, "2")
    assert w.mode_combo.currentIndex() == 1
    _press(qapp, w, "1")
    assert w.mode_combo.currentIndex() == 0
    # M: Point Mark placement on, M again: off
    _press(qapp, w, "M")
    assert w.mark_tool_combo.currentData() == POINT_MARK and w.add_mark_button.isChecked()
    _press(qapp, w, "M")
    assert not w.add_mark_button.isChecked()
    # F: Maximize Plot toggles
    before = w.maximize_plot_button.isChecked()
    _press(qapp, w, "F")
    assert w.maximize_plot_button.isChecked() != before
    _press(qapp, w, "F")
    assert w.maximize_plot_button.isChecked() == before
    # Esc: zoomed in → full range again
    box = w.plot_widget.plot_widget.getPlotItem().getViewBox()
    full = box.viewRange()[0]
    box.setXRange(full[0] + 0.4 * (full[1] - full[0]), full[0] + 0.5 * (full[1] - full[0]), padding=0)
    qapp.processEvents()
    assert box.viewRange()[0][1] - box.viewRange()[0][0] < 0.2 * (full[1] - full[0])
    _press(qapp, w, "Esc")
    span = box.viewRange()[0]
    assert span[1] - span[0] >= 0.9 * (full[1] - full[0])
    # P: data points
    before = w.show_data_points_checkbox.isChecked()
    _press(qapp, w, "P")
    assert w.show_data_points_checkbox.isChecked() != before


def test_shortcuts_rebind_in_open_viewers(qapp, viewer, fresh_shortcuts):
    fresh_shortcuts.set_key("plot_2d", "G")
    qapp.processEvents()
    assert viewer.key_shortcuts.key("plot_2d") == "G"
    fresh_shortcuts.set_key("plot_2d", "")
    assert viewer.key_shortcuts.key("plot_2d") == ""


def test_phase_delay_in_the_viewer(qapp, viewer):
    w = viewer
    panel = w.plot_config_1d
    assert panel.phase_delay_spin.value() == 0.0
    panel.phase_delay_spin.setValue(0.25)                   # ns
    panel.changed.emit()
    qapp.processEvents()
    assert w._active_config().phase_delay_ns == pytest.approx(0.25)
    x_cand, y_cand = w.x_combo.currentData(), w.y_combo.currentData()
    spec = w._pane_transform_spec(w._pane_states[w._active_pane_id])
    from app.core.transform_store import TransformSpec

    _x, phase, _t, _s = w._plot_arrays_for_entry(x_cand, y_cand, 0, TransformSpec("Phase", "phase_rad"))
    assert np.ptp(phase) > 0.1                              # the synthetic data has zero phase before the delay
    # D toggles it off and on again
    w.key_shortcuts.trigger("phase_delay")
    assert panel.phase_delay_spin.value() == 0.0
    w.key_shortcuts.trigger("phase_delay")
    assert panel.phase_delay_spin.value() == pytest.approx(0.25)
    assert spec is not None


def test_settings_shortcuts_page(qapp, tmp_path, fresh_shortcuts):
    from app.localization import LocalizationManager
    from app.settings.dialog import SettingsDialog
    from app.settings.store import SettingsStore

    dialog = SettingsDialog(LocalizationManager(SettingsStore(tmp_path / "settings.json")))
    page = dialog.shortcuts_page
    dialog.show_page("shortcuts")
    assert page.edits["point_mark"].keySequence().toString() == "M"
    from PySide6.QtGui import QKeySequence

    page.edits["point_mark"].setKeySequence(QKeySequence("K"))
    page._edited("point_mark")
    assert fresh_shortcuts.key_for("point_mark") == "K"
    page.edits["maximize"].setKeySequence(QKeySequence("K"))
    page._edited("maximize")
    assert "K" in page.warning.text()
    page._turn_off("maximize")
    assert fresh_shortcuts.key_for("maximize") == "" and page.warning.text() == ""
    page._reset()
    assert page.edits["point_mark"].keySequence().toString() == "M"
    dialog.close()
