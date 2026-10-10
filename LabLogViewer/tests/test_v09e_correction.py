"""Acceptance tests for the in-place v0.9E correction pass."""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.channel_manager import ChannelManager
from app.core.labber_parser import load_experiment
from app.gui.plot_2d_widget import (
    DEFAULT_COLORMAP,
    Plot2DWidget,
    get_colormap,
    robust_color_limits,
)
from app.plotting.complex_transform import apply_transform
from tests.real_data import BIG_FILE, FLUX_FILE, S31_FILE, SMALL_FILE


pytestmark = pytest.mark.skipif(
    not all(path.exists() for path in (BIG_FILE, FLUX_FILE, S31_FILE, SMALL_FILE)),
    reason="Required workspace real-data fixtures are unavailable.",
)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _combo_index(combo, name: str, base_channel: str | None = None) -> int:
    for index in range(combo.count()):
        candidate = combo.itemData(index)
        if candidate is None or candidate.name != name:
            continue
        if base_channel is None or candidate.base_channel == base_channel:
            return index
    return -1


def test_default_lut_direction_is_low_red_mid_white_high_blue(qapp):
    colors = get_colormap(DEFAULT_COLORMAP).map([0.0, 0.5, 1.0], mode="byte")
    assert tuple(colors[0][:3]) == (125, 0, 0)
    assert tuple(colors[1][:3]) == (255, 255, 255)
    assert tuple(colors[2][:3]) == (0, 35, 130)


def test_robust_auto_range_rejects_isolated_extremes():
    values = np.concatenate((np.linspace(-10.0, 10.0, 10000), [-1000.0, 1000.0]))
    low, high = robust_color_limits(values)
    assert -11.0 < low < -9.0
    assert 9.0 < high < 11.0


def test_real_flux_auto_range_is_useful_and_display_only(qapp):
    with load_experiment(str(FLUX_FILE)) as experiment:
        raw = experiment.get_data("VNA - S21", transform="magnitude_db")
        original = raw.copy()
        low, high = robust_color_limits(raw)
        assert raw.min() < low < -0.1
        assert -0.01 < high < raw.max()
        assert np.array_equal(raw, original)

        grid = experiment.get_2d_data(
            "Frequency", "DC supply - 1 - Current", "VNA - S21",
            transform="magnitude_db",
        )
        widget = Plot2DWidget()
        widget.plot(grid)
        assert widget._colormap_name == DEFAULT_COLORMAP
        assert widget._z_min == pytest.approx(low)
        assert widget._z_max == pytest.approx(high)
        widget.close()


def test_all_derived_axes_match_real_complex_s21_math():
    with load_experiment(str(SMALL_FILE)) as experiment:
        manager = ChannelManager(experiment)
        raw = experiment.get_data("VNA - S21", transform="raw", entry_slice=slice(0, 1)).reshape(-1)
        expected = {
            "Real": "real",
            "Imaginary": "imag",
            "Magnitude": "magnitude",
            "Phase": "phase_deg",
        }
        derived = [candidate for candidate in manager.list_axis_candidates()
                   if candidate.source == "derived"]
        assert {candidate.name for candidate in derived} == set(expected)
        for candidate in derived:
            actual = manager.get_axis_data(candidate, entry_index=0)
            assert np.allclose(actual, apply_transform(raw, expected[candidate.name]))


def test_real_vs_imaginary_gui_trajectory_matches_source(qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.open_file(str(SMALL_FILE))
    real_index = _combo_index(window.x_combo, "Real", "VNA - S21")
    imag_index = _combo_index(window.y_combo, "Imaginary", "VNA - S21")
    assert real_index >= 0 and imag_index >= 0
    window.x_combo.setCurrentIndex(real_index)
    window.y_combo.setCurrentIndex(imag_index)

    x_values, y_values = window.plot_widget._curve.getData()
    raw = window.experiment.get_data(
        "VNA - S21", transform="raw", entry_slice=slice(0, 1)
    ).reshape(-1)
    assert np.allclose(x_values, np.real(raw))
    assert np.allclose(y_values, np.imag(raw))
    assert not window.transform_combo.isEnabled()
    window.close()


def test_invalid_physical_to_derived_domain_pair_is_rejected():
    with load_experiment(str(BIG_FILE)) as experiment:
        manager = ChannelManager(experiment)
        candidates = manager.list_axis_candidates()
        current = next(candidate for candidate in candidates
                       if candidate.name == "Average Current")
        magnitude = next(candidate for candidate in candidates
                         if candidate.name == "Magnitude")
        assert not manager.axis_domains_compatible(current, magnitude)


def test_real_s31_gets_generic_derived_axes():
    with load_experiment(str(S31_FILE)) as experiment:
        manager = ChannelManager(experiment)
        derived = [candidate for candidate in manager.list_axis_candidates()
                   if candidate.source == "derived"]
        assert {candidate.name for candidate in derived} == {
            "Real", "Imaginary", "Magnitude", "Phase"
        }
        assert {candidate.base_channel for candidate in derived} == {"VNA - S31"}


def test_2d_plot_uses_reclaimed_line_cut_space(qapp):
    from PySide6.QtCore import Qt
    from app.gui.main_window import MainWindow
    from app.gui.linecut_widget import LineCutWidget

    window = MainWindow()
    window.resize(1300, 900)
    window.show()
    qapp.processEvents()
    assert window.plot_2d_splitter.orientation() == Qt.Vertical
    assert window.plot_2d_splitter.count() == 1
    assert not window.findChildren(LineCutWidget)
    window.close()


def test_maximize_plot_and_restore_layout(qapp):
    from app.gui.main_window import MainWindow
    from app.gui.linecut_widget import LineCutWidget

    window = MainWindow()
    window.resize(1300, 900)
    window.open_file(str(FLUX_FILE))
    window.mode_combo.setCurrentIndex(1)
    window.show()
    qapp.processEvents()
    before_main = window.main_splitter.sizes()
    before_right = window.right_splitter.sizes()

    window.maximize_plot_button.click()
    qapp.processEvents()
    assert window.controls_panel.isHidden()
    assert not window.findChildren(LineCutWidget)
    assert window.maximize_plot_button.text() == "Restore Layout"

    window.maximize_plot_button.click()
    qapp.processEvents()
    assert not window.controls_panel.isHidden()
    assert not window.findChildren(LineCutWidget)
    assert window.maximize_plot_button.text() == "Maximize Plot"
    assert window.main_splitter.sizes() == before_main
    assert window.right_splitter.sizes() == before_right
    window.close()
