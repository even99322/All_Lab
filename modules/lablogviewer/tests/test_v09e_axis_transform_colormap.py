"""v0.9E acceptance coverage for semantic axes and real workspace data."""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app import __version__
from app.core.channel_manager import ChannelManager
from app.core.labber_parser import load_experiment
from app.core.transform_store import TransformSpec, TransformStore
from tests.real_data import BIG_FILE, FLUX_FILE, S31_FILE, SMALL_FILE


REAL_FILES = (BIG_FILE, FLUX_FILE, S31_FILE, SMALL_FILE)
real_data_required = pytest.mark.skipif(
    not all(path.exists() for path in REAL_FILES),
    reason="Required workspace real-data fixtures are unavailable.",
)


def test_version_reports_current_release():
    assert __version__ == "1.0.3"


def test_main_window_title_reports_current_release():
    from PySide6.QtWidgets import QApplication
    from app.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    assert f"v{__version__}" in window.windowTitle()
    window.close()


@real_data_required
def test_1d_axis_roles_keep_complex_measurements_out_of_raw_x():
    with load_experiment(str(BIG_FILE)) as experiment:
        manager = ChannelManager(experiment)
        x_names = {candidate.name for candidate in manager.list_x_axis_candidates()}
        y_names = {candidate.name for candidate in manager.list_y_axis_candidates()}
        assert "Frequency" in x_names
        assert "Average Current" in x_names
        assert "VNA - S21" not in x_names
        assert "VNA - S21" in y_names


@real_data_required
def test_2d_axes_are_actual_dimensions_and_can_be_swapped():
    with load_experiment(str(BIG_FILE)) as experiment:
        manager = ChannelManager(experiment)
        x_names, y_names = manager.get_2d_axis_candidates("VNA - S21")
        assert x_names == ["Frequency", "Average Current"]
        assert y_names == x_names

        normal = experiment.get_2d_data(
            "Frequency", "Average Current", "VNA - S21", transform="magnitude_db"
        )
        swapped = experiment.get_2d_data(
            "Average Current", "Frequency", "VNA - S21", transform="magnitude_db"
        )
        assert swapped.z_values.shape == normal.z_values.T.shape
        assert np.allclose(swapped.z_values, normal.z_values.T)


@real_data_required
def test_2d_gui_axis_swap_rebuilds_with_distinct_dimensions():
    from PySide6.QtWidgets import QApplication
    from app.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.open_file(str(BIG_FILE))
    window.mode_combo.setCurrentIndex(1)
    original_shape = window.plot_2d_widget._grid.z_values.shape

    window.x_combo_2d.setCurrentText("Average Current")
    assert window.y_combo_2d.currentText() == "Frequency"
    assert window.plot_2d_widget._grid.x_name == "Average Current"
    assert window.plot_2d_widget._grid.y_name == "Frequency"
    assert window.plot_2d_widget._grid.z_values.shape == original_shape[::-1]
    window.close()


@real_data_required
def test_flux_dimensions_and_surface_remain_correct():
    with load_experiment(str(FLUX_FILE)) as experiment:
        manager = ChannelManager(experiment)
        dimensions = manager.list_dimensions("VNA - S21")
        assert [(dimension.name, dimension.size) for dimension in dimensions] == [
            ("Frequency", 10001),
            ("DC supply - 1 - Current", 681),
        ]
        grid = experiment.get_2d_data(
            "Frequency", "DC supply - 1 - Current", "VNA - S21",
            transform="magnitude_db",
        )
        assert grid.z_values.shape == (681, 10001)
        assert np.isfinite(grid.z_values).all()


@real_data_required
def test_real_s31_uses_same_generic_pipeline():
    with load_experiment(str(S31_FILE)) as experiment:
        assert experiment.log_channel_names == ["VNA - S31"]
        manager = ChannelManager(experiment)
        names = manager.get_2d_axis_candidates("VNA - S31")[0]
        assert names == ["Frequency", "DC supply - 1 - Current"]
        grid = experiment.get_2d_data(
            "Frequency", "DC supply - 1 - Current", "VNA - S31",
            transform="phase_deg",
        )
        assert grid.z_values.shape == (581, 1001)
        assert np.isfinite(grid.z_values).all()


@real_data_required
def test_real_1d_file_opens_and_exposes_frequency_trace():
    with load_experiment(str(SMALL_FILE)) as experiment:
        manager = ChannelManager(experiment)
        assert manager.get_2d_axis_candidates("VNA - S21") == ([], [])
        candidates = manager.list_axis_candidates()
        frequency = next(candidate for candidate in candidates if candidate.source == "trace_axis")
        measurement = next(candidate for candidate in candidates if candidate.source == "vector_channel")
        x_values = manager.get_axis_data(frequency)
        y_values = manager.get_axis_data(measurement, transform="magnitude")
        assert x_values.shape == y_values.shape == (501,)
        assert np.isfinite(y_values).all()


def test_saved_transform_survives_store_restart(tmp_path):
    path = tmp_path / "transforms.json"
    TransformStore(path).save(
        TransformSpec(name="Restart Check", base="phase_deg", unwrap=True)
    )
    restored = TransformStore(path).get("Restart Check")
    assert restored is not None
    assert restored.base == "phase_deg"
    assert restored.unwrap is True
