from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import importlib.util

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.analysis.yig_fitting.core.data_io import dataset_from_experiment
from tests.real_data import BIG_FILE


def _trace(name, x, values):
    return SimpleNamespace(
        complex=True,
        x_name="Frequency",
        n_points=len(x),
        x_values=np.asarray(x, dtype=float),
    ), np.asarray(values, dtype=np.complex128)


class _Experiment:
    source_path = Path("/measurements/sample.hdf5")
    data_identity = "/measurements/sample.hdf5"
    display_name = "sample"

    def __init__(self, x, traces, step_axes=()):
        self.step_axes = list(step_axes)
        self.vector_traces = {name: item[0] for name, item in traces.items()}
        self._values = {name: item[1] for name, item in traces.items()}
        self.channels = {}

    def get_data(self, name, transform="raw"):
        assert transform == "raw"
        return self._values[name]

    def get_step_values(self, name):
        raise KeyError(name)


def test_adapter_keeps_complex_frequency_by_entry_orientation_and_complete_axes():
    x = np.array([5.0, 5.1, 5.2])
    current = SimpleNamespace(name="Current")
    values = np.arange(12).reshape(3, 4) + 1j * np.arange(12, 24).reshape(3, 4)
    trace, raw = _trace("VNA - S11", x, values)
    trace.x_values = x
    experiment = _Experiment(x, {"VNA - S11": (trace, raw)}, [
        SimpleNamespace(channel=current, values=np.array([1.0, 2.0, 3.0, 4.0]))
    ])

    result = dataset_from_experiment(experiment)

    assert result["s_params"]["VNA - S11"].shape == (3, 4)
    np.testing.assert_array_equal(result["s_params"]["VNA - S11"], values)
    np.testing.assert_array_equal(result["step_channels"]["Current"], [1, 2, 3, 4])
    np.testing.assert_allclose(result["s_db"]["VNA - S11"], 20 * np.log10(np.abs(values)))


def test_adapter_maps_only_unambiguous_partial_one_dimensional_sweep():
    x = np.array([5.0, 5.1])
    axis = SimpleNamespace(channel=SimpleNamespace(name="Current"), values=np.arange(5.0))
    trace, raw = _trace("VNA - S21", x, np.ones((2, 3), dtype=complex))
    trace.x_values = x
    result = dataset_from_experiment(_Experiment(x, {"VNA - S21": (trace, raw)}, [axis]))
    np.testing.assert_array_equal(result["step_channels"]["Current"], [0, 1, 2])


def test_adapter_does_not_infer_coordinates_for_ambiguous_partial_nd_sweep():
    x = np.array([5.0, 5.1])
    axes = [
        SimpleNamespace(channel=SimpleNamespace(name="Current"), values=np.arange(3.0)),
        SimpleNamespace(channel=SimpleNamespace(name="Flux"), values=np.arange(4.0)),
    ]
    trace, raw = _trace("VNA - S21", x, np.ones((2, 7), dtype=complex))
    trace.x_values = x
    result = dataset_from_experiment(_Experiment(x, {"VNA - S21": (trace, raw)}, axes))
    assert result["n_steps"] == 7
    assert "Current" not in result["step_channels"]
    assert "Flux" not in result["step_channels"]


def test_adapter_omits_channels_with_incompatible_grid_or_entry_count():
    x = np.array([5.0, 5.1])
    good, good_values = _trace("VNA - S11", x, np.ones((2, 3), dtype=complex))
    wrong_grid, wrong_grid_values = _trace("VNA - S21", x + 0.01, np.ones((2, 3), dtype=complex))
    wrong_count, wrong_count_values = _trace("VNA - S22", x, np.ones((2, 2), dtype=complex))
    good.x_values = wrong_count.x_values = x
    wrong_grid.x_values = x + 0.01
    result = dataset_from_experiment(_Experiment(x, {
        "VNA - S11": (good, good_values),
        "VNA - S21": (wrong_grid, wrong_grid_values),
        "VNA - S22": (wrong_count, wrong_count_values),
    }))
    assert list(result["s_params"]) == ["VNA - S11"]
    assert result["omitted_channels"] == ["VNA - S21", "VNA - S22"]


@pytest.mark.skipif(not BIG_FILE.exists(), reason="real Labber sample is unavailable")
def test_adapter_reads_real_rsmep_through_experiment_api():
    from app.core.labber_parser import load_experiment

    experiment = load_experiment(str(BIG_FILE))
    try:
        result = dataset_from_experiment(experiment)
        assert result["identity"] == experiment.data_identity
        assert result["frequency"].size == 501
        assert result["s_params"]["VNA - S21"].shape == (501, 855)
        assert np.iscomplexobj(result["s_params"]["VNA - S21"])
    finally:
        experiment.close()


def test_phase_line_and_node_positions_are_deterministic_without_fit_engine():
    from app.analysis.yig_fitting.core.phase import (
        antinode_frequencies, node_frequencies, phase_line,
    )

    frequency = np.array([4.0e9, 4.5e9, 5.0e9])
    phase = phase_line(frequency, 2.0, 0.25, 4.0e9)
    np.testing.assert_allclose(phase, 0.25 + 2 * np.pi * 2e-9 * (frequency - frequency[0]))
    nodes = node_frequencies(2.0, 0.25, frequency[0], frequency[0], frequency[-1])
    node_hz = np.asarray([item[1] for item in nodes])
    assert np.all(np.diff(node_hz) > 0)
    np.testing.assert_allclose(np.diff(node_hz), 1 / (2 * 2e-9))
    antinodes = antinode_frequencies(2.0, 0.25, frequency[0], frequency[0], frequency[-1])
    assert antinodes
    for _, antinode_hz in antinodes:
        phase = phase_line(antinode_hz, 2.0, 0.25, frequency[0])
        np.testing.assert_allclose(np.sin(phase) ** 2, 1.0, atol=1e-12)


def test_kappa_and_phase_estimators_recover_synthetic_phase_line():
    pytest.importorskip("scipy")
    from app.analysis.yig_fitting.core.phase import (
        fit_kappa_line, fit_phase_line, phase_line, wrap,
    )

    frequency = np.linspace(4.4e9, 5.6e9, 121)
    f_ref = 5.0e9
    expected_t = 1.1
    expected_phi = 0.43
    expected_kappa_b = 12.0
    phase = phase_line(frequency, expected_t, expected_phi, f_ref)
    kappa = expected_kappa_b * np.sin(phase) ** 2

    kappa_result = fit_kappa_line(
        frequency, kappa, T_max_ns=2.0, f_ref_hz=f_ref,
    )
    phase_result = fit_phase_line(
        frequency, wrap(phase), T_max_ns=2.0, f_ref_hz=f_ref,
    )

    assert kappa_result["n_used"] >= frequency.size - 2
    assert abs(kappa_result["T_ns"] - expected_t) < 0.02
    assert abs(kappa_result["kappa_b"] - expected_kappa_b) < 0.05
    assert phase_result["n_used"] == frequency.size
    assert abs(abs(phase_result["T_ns"]) - expected_t) < 0.02
    assert phase_result["rms"] < 0.02


@pytest.mark.skipif(importlib.util.find_spec("scipy") is not None, reason="only tests dependency fallback")
def test_fit_core_reports_missing_scipy_without_failing_import():
    from app.analysis.yig_fitting.core.fitting import run_fit

    with pytest.raises(RuntimeError, match="requires SciPy"):
        run_fit(lambda x, a: a * x, np.arange(3.0), np.arange(3.0, dtype=complex),
                np.array([1.0]), np.array([-np.inf]), np.array([np.inf]),
                np.array([False]), {})


def test_reference_yig_node_model_single_fit_recovers_known_resonance():
    pytest.importorskip("scipy")
    from app.analysis.yig_fitting.core.formula import load_formula_module
    from app.analysis.yig_fitting.core.fitting import run_fit

    model_path = Path(__file__).resolve().parents[1] / "app/analysis/yig_fitting/models/formula_yig_node.py"
    _, functions = load_formula_module(str(model_path))
    model = functions["S11_node"]
    frequency = np.linspace(5.026e9, 5.036e9, 401)
    truth = np.array([5.03125, 5.0, 10.0, 1.0, 1.0, 0.98, 0.3, 0.02, 0.12])
    split = np.asarray(model(frequency, *truth), dtype=float)
    measured = split[:frequency.size] + 1j * split[frequency.size:]
    initial = truth.copy()
    initial[0] = 5.032
    lower = truth.copy()
    upper = truth.copy()
    lower[0], upper[0] = 5.028, 5.034
    fixed = np.ones(truth.size, dtype=bool)
    fixed[0] = False

    result = run_fit(model, frequency, measured, initial, lower, upper, fixed,
                     {"weight": False, "tol": 1e-10, "maxfev": 10000})

    assert result["params"][0] == pytest.approx(truth[0], abs=1e-8)
    assert result["r2_complex"] > 0.999999


def test_global_fit_restores_shared_and_per_slice_parameters():
    pytest.importorskip("scipy")
    from app.analysis.yig_fitting.core.phase import run_global_fit

    def affine(frequency, offset, slope):
        return offset + slope * ((frequency - 5e9) / 1e9)

    frequency = np.linspace(4.95e9, 5.05e9, 81)
    expected_slopes = np.array([0.2, -0.1])
    slices = [
        {"freq": frequency, "s": affine(frequency, 1.2, slope),
         "params": np.array([1.0, 0.0]), "idx": index}
        for index, slope in enumerate(expected_slopes)
    ]
    result = run_global_fit(
        affine, slices, ["offset", "slope"], ["shared", "slice"],
        np.array([1.0, 0.0]), np.array([-5.0, -2.0]), np.array([5.0, 2.0]),
        opts={"weight": False, "tol": 1e-10, "max_nfev": 1000},
    )

    assert result["success"]
    assert result["shared"]["offset"] == pytest.approx(1.2, abs=1e-7)
    np.testing.assert_allclose(result["params"][:, 1], expected_slopes, atol=1e-7)
    assert np.all(result["r2"] > 0.999999)


def test_batch_fit_tracks_several_reference_traces_without_reordering():
    pytest.importorskip("scipy")
    from app.analysis.yig_fitting.core.batch import run_batch

    def resonance(frequency, center_ghz, width_mhz, depth):
        offset = (frequency - center_ghz * 1e9) / (width_mhz * 1e6)
        return 1.0 - depth / (1.0 + 1j * offset)

    frequency = np.linspace(4.97e9, 5.03e9, 121)
    centers = np.array([4.99, 5.0, 5.01])
    traces = np.column_stack([resonance(frequency, center, 2.0, 0.2) for center in centers])
    initial = np.array([5.0, 2.5, 0.18])
    lower = np.array([4.98, 0.5, 0.01])
    upper = np.array([5.02, 8.0, 0.5])
    records = []

    run_batch(
        resonance, None, "resonance", frequency, traces, np.arange(centers.size),
        np.arange(centers.size, dtype=float), ["center_ghz", "width_mhz", "depth"],
        ["GHz", "MHz", ""], initial, lower, upper, np.zeros(3, dtype=bool),
        {"weight": False, "tol": 1e-10, "maxfev": 10000},
        {"track": "center_ghz", "width_hz": 30e6, "offset_hz": 0.0,
         "moving": [True, False, False], "init_mode": "prev", "bound_freq": True,
         "r2_min": 0.99, "stop_on_fail": True, "roll": {}, "roll_clip": False},
        records.append,
    )

    assert [record["idx"] for record in records] == [0, 1, 2]
    assert all(record["ok"] for record in records)
    np.testing.assert_allclose([record["params"][0] for record in records], centers, atol=1e-7)


def test_yig_fitting_ui_opens_without_scipy_and_does_not_start_worker(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    import app.analysis.yig_fitting.controller as controller_module
    from app.analysis.yig_fitting.core.settings import SessionStore
    from app.analysis.yig_fitting.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(controller_module.importlib.util, "find_spec", lambda name: None)
    started = Mock(side_effect=AssertionError("worker must not start without SciPy"))
    monkeypatch.setattr(controller_module.FitEngine, "start", started)
    window = MainWindow()
    controller = None
    try:
        controller = controller_module.FitController(
            window, store=SessionStore(str(tmp_path / "session.json")),
            source_path="/measurements/sample.hdf5",
        )
        assert not controller.scipy_available
        assert not window.btn_fit.isEnabled()
        assert window.lbl_engine.text().startswith("SciPy unavailable")
        assert window.tabs.count() == 4
        window.cmb_s.addItems(["VNA - S11", "VNA - S21"])
        window.show()
        app.processEvents()
        window.cmb_s.showPopup()
        app.processEvents()
        window.cmb_s.setCurrentIndex(1)
        window.cmb_s.hidePopup()
        app.processEvents()
        assert window.cmb_s.currentText() == "VNA - S21"
        assert window.size().width() >= 1000
    finally:
        window.close()
        app.processEvents()
        if controller is not None:
            controller.shutdown()
        started.assert_not_called()


def test_fitting_production_adapter_has_no_hdf5_access():
    root = Path(__file__).resolve().parents[1]
    sources = [root / "app/gui/yig_fitting_window.py", *root.glob("app/analysis/yig_fitting/**/*.py")]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert "import h5py" not in text and "from h5py" not in text, path


def test_viewer_analysis_group_is_separated_and_uses_native_menu_actions():
    from PySide6.QtWidgets import QApplication
    from app.gui.main_window import MainWindow as ViewerWindow

    app = QApplication.instance() or QApplication([])
    viewer = ViewerWindow()
    try:
        labels = [action.text().replace("&", "") for action in viewer.analysis_menu.actions()]
        assert labels == ["YIG Mirror Analysis...", "3D Surface..."]   # v0.18B 3D window
        assert not viewer.surface_3d_action.isEnabled()
        assert viewer.analysis_tool_button.menu() is viewer.analysis_menu
        assert not viewer.yig_fitting_action.isEnabled()
        assert not viewer.node_antinode_action.isVisible()
        assert viewer.application_toolbar.actions()[-1] is not viewer.yig_fitting_action
        assert viewer.analysis_tool_button.text() == "Analysis"
    finally:
        viewer.close()
        app.processEvents()
