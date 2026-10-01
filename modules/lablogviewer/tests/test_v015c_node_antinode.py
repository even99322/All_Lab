from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.hdf5_reader import HDF5Reader
from app.core.labber_parser import AutoDetector
from app.core.node_antinode import (
    NodeAntinodeError,
    NodeAntinodeParameters,
    analyze_nodes_and_antinodes,
    get_node_analysis_grid,
)
from tests.real_data import BIG_FILE, FLUX_FILE, S31_FILE


def _synthetic_sweep():
    sweep = np.arange(81, dtype=float)
    frequency = np.linspace(5.02e9, 5.12e9, 501)
    center = 5.035e9 + sweep * 1.15e6
    depth = 0.16 + 0.12 * (1.0 + np.sin(2.0 * np.pi * sweep / 16.0)) / 2.0
    magnitude = 1.0 - depth[:, None] * np.exp(
        -((frequency[None, :] - center[:, None]) / 2.2e6) ** 2
    )
    phase = (frequency[None, :] - center[:, None]) / 1.0e7
    complex_data = magnitude * np.exp(1j * phase)
    return sweep, frequency, complex_data


def _reference_node_algorithm(sweep, frequency, data, parameters):
    """Test-only transcription of reference run_node_detection's calculation."""
    magnitude = np.abs(data)
    dip_freqs = np.zeros(len(sweep))
    dip_depths = np.zeros(len(sweep))
    for index in range(len(sweep)):
        trace = magnitude[index, :]
        minimum = int(np.argmin(trace))
        dip_freqs[index] = frequency[minimum]
        dip_depths[index] = np.max(trace) - trace[minimum]
    threshold = (float(np.median(dip_depths)) if parameters.dip_depth_threshold is None
                 else parameters.dip_depth_threshold)
    valid = dip_depths > threshold
    slope, intercept = np.polyfit(sweep[valid], dip_freqs[valid], 1)
    averages = np.zeros(len(sweep))
    exact = np.zeros(len(sweep))
    for index, value in enumerate(sweep):
        center = slope * value + intercept
        mask = ((frequency >= center - parameters.window_half_width)
                & (frequency <= center + parameters.window_half_width))
        if np.any(mask):
            window_magnitude = magnitude[index, mask]
            window_frequency = frequency[mask]
            averages[index] = np.mean(window_magnitude)
            exact[index] = window_frequency[np.argmin(window_magnitude)]
        else:
            averages[index] = np.nan
            exact[index] = np.nan
    length = parameters.smoothing_points
    if length > 1:
        smooth = np.convolve(averages, np.ones(length) / length, mode="same")
        half = length // 2
        if half > 0:
            smooth[:half] = averages[:half]
            smooth[-half:] = averages[-half:]
    else:
        smooth = averages.copy()
    def reference_peaks(curve):
        peaks = np.flatnonzero(
            (curve[1:-1] > curve[:-2]) & (curve[1:-1] >= curve[2:])
        ) + 1
        chosen = []
        for peak in peaks[np.argsort(curve[peaks])[::-1]]:
            if all(abs(int(peak) - other) >= parameters.minimum_distance for other in chosen):
                chosen.append(int(peak))
        found = []
        for peak in sorted(chosen):
            left = peak
            while left > 0 and curve[left - 1] <= curve[peak]:
                left -= 1
            right = peak
            while right + 1 < curve.size and curve[right + 1] <= curve[peak]:
                right += 1
            left_min = np.min(curve[left:peak + 1])
            right_min = np.min(curve[peak:right + 1])
            if curve[peak] - max(left_min, right_min) >= parameters.prominence:
                found.append(peak)
        return np.asarray(found, dtype=int)

    nodes = reference_peaks(smooth)
    return slope, intercept, valid, averages, smooth, exact, nodes


def test_node_results_match_reference_calculation_and_antinodes_are_minima():
    sweep, frequency, data = _synthetic_sweep()
    parameters = NodeAntinodeParameters(
        window_half_width=8.0e6,
        smoothing_points=3,
        minimum_distance=5,
        prominence=0.001,
    )
    expected = _reference_node_algorithm(sweep, frequency, data, parameters)
    actual = analyze_nodes_and_antinodes(sweep, frequency, data, parameters)

    assert actual.slope == pytest.approx(expected[0], rel=1e-12)
    assert actual.intercept == pytest.approx(expected[1], rel=1e-12)
    assert np.array_equal(actual.valid_fit_mask, expected[2])
    assert np.allclose(actual.average_transmission, expected[3], rtol=1e-13, atol=1e-13)
    assert np.allclose(actual.smoothed_transmission, expected[4], rtol=1e-13, atol=1e-13)
    assert np.allclose(actual.exact_resonance_frequencies, expected[5])
    assert np.array_equal(actual.node_indices, expected[6])
    assert actual.node_indices.size > 0
    assert actual.antinode_indices.size > 0
    for index in actual.antinode_indices:
        assert actual.smoothed_transmission[index] < actual.smoothed_transmission[index - 1]
        assert actual.smoothed_transmission[index] < actual.smoothed_transmission[index + 1]


def test_manual_dip_threshold_and_smoothing_limits():
    sweep, frequency, data = _synthetic_sweep()
    parameters = NodeAntinodeParameters(
        window_half_width=8e6, smoothing_points=1, minimum_distance=1,
        prominence=0.0, dip_depth_threshold=0.2,
    )
    result = analyze_nodes_and_antinodes(sweep, frequency, data, parameters)
    assert result.dip_depth_threshold == 0.2
    assert np.all(result.dip_depths[result.valid_fit_mask] > 0.2)
    with pytest.raises(NodeAntinodeError, match="Smoothing length"):
        analyze_nodes_and_antinodes(
            sweep[:2], frequency, data[:2],
            NodeAntinodeParameters(1.0, smoothing_points=3),
        )


def test_detection_modes_return_only_requested_candidate_types():
    sweep, frequency, data = _synthetic_sweep()
    base = NodeAntinodeParameters(
        window_half_width=8e6, smoothing_points=3, minimum_distance=5,
        prominence=0.001,
    )
    nodes = analyze_nodes_and_antinodes(
        sweep, frequency, data, NodeAntinodeParameters(**{**base.__dict__, "detection_mode": "Node"})
    )
    antinodes = analyze_nodes_and_antinodes(
        sweep, frequency, data,
        NodeAntinodeParameters(**{**base.__dict__, "detection_mode": "Antinode"}),
    )
    both = analyze_nodes_and_antinodes(sweep, frequency, data, base)
    assert nodes.node_indices.size > 0 and nodes.antinode_indices.size == 0
    assert antinodes.node_indices.size == 0 and antinodes.antinode_indices.size > 0
    assert np.array_equal(both.node_indices, nodes.node_indices)
    assert np.array_equal(both.antinode_indices, antinodes.antinode_indices)
    with pytest.raises(NodeAntinodeError, match="Detection mode"):
        analyze_nodes_and_antinodes(
            sweep, frequency, data,
            NodeAntinodeParameters(**{**base.__dict__, "detection_mode": "Guess"}),
        )


def test_nonfinite_samples_are_rejected_without_repair():
    sweep, frequency, data = _synthetic_sweep()
    data[3, 4] = np.nan + 1j * 0
    with pytest.raises(NodeAntinodeError, match="NaN or infinite"):
        analyze_nodes_and_antinodes(
            sweep, frequency, data,
            NodeAntinodeParameters(8e6, smoothing_points=3),
        )


def test_cancellation_and_non_detectable_resonance_fail_without_fabrication():
    sweep, frequency, data = _synthetic_sweep()
    parameters = NodeAntinodeParameters(8e6, smoothing_points=3)
    with pytest.raises(NodeAntinodeError, match="cancelled"):
        analyze_nodes_and_antinodes(
            sweep, frequency, data, parameters, cancel_check=lambda: True
        )
    flat = np.ones_like(data, dtype=complex)
    with pytest.raises(NodeAntinodeError, match="Fewer than two"):
        analyze_nodes_and_antinodes(sweep, frequency, flat, parameters)


@pytest.mark.skipif(not BIG_FILE.is_file(), reason="Real RSMEP sample is unavailable.")
def test_real_rsmep_uses_data_model_frequency_and_sweep_semantics():
    reader = HDF5Reader(BIG_FILE)
    experiment = AutoDetector.detect_and_parse(reader)
    try:
        grid = get_node_analysis_grid(experiment, "VNA - S21")
        assert grid.x_name == "Frequency"
        assert grid.y_name == "Average Current"
        assert grid.x_values.size == 501
        assert grid.y_values.size == 855
        assert grid.z_values.shape == (855, 501)
        assert np.iscomplexobj(grid.z_values)
        result = analyze_nodes_and_antinodes(
            grid.y_values,
            grid.x_values,
            grid.z_values,
            NodeAntinodeParameters(
                window_half_width=0.25e9,
                smoothing_points=3,
                minimum_distance=10,
                prominence=0.0002,
            ),
        )
        assert result.fitted_frequency.shape == (855,)
        assert np.isfinite(result.dip_depth_threshold)
    finally:
        experiment.close()


@pytest.mark.parametrize(
    "path,channel,expected_x,expected_y",
    [
        (FLUX_FILE, "VNA - S21", 10001, 681),
        (S31_FILE, "VNA - S31", 1001, 581),
    ],
)
def test_real_debackgrounded_flux_and_arbitrary_sij_are_supported(
    path, channel, expected_x, expected_y,
):
    if not path.is_file():
        pytest.skip(f"Real Labber sample is unavailable: {path.name}")
    experiment = AutoDetector.detect_and_parse(HDF5Reader(path))
    try:
        sweep_axis = experiment.step_axes[0].channel.name
        grid = get_node_analysis_grid(experiment, channel, sweep_axis)
        assert grid.x_name == "Frequency"
        assert grid.y_name == sweep_axis
        assert grid.z_values.shape == (expected_y, expected_x)
        result = analyze_nodes_and_antinodes(
            grid.y_values, grid.x_values, grid.z_values,
            NodeAntinodeParameters(
                window_half_width=0.25e9, smoothing_points=3,
                minimum_distance=10, prominence=0.0002,
            ),
        )
        assert result.sweep_values.size == expected_y
        assert result.exact_resonance_frequencies.shape == (expected_y,)
    finally:
        experiment.close()


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.mark.skipif(not BIG_FILE.is_file(), reason="Real RSMEP sample is unavailable.")
def test_unified_analysis_contains_optional_coarse_detector(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer, Qt
    from PySide6.QtTest import QTest
    import time
    from app.gui.main_window import MainWindow
    from app.analysis.yig_fitting.core import paths

    monkeypatch.setattr(paths, "_CONFIG", str(tmp_path / "lablogviewer-analysis"))

    window = MainWindow()
    window.open_file(str(BIG_FILE))
    assert window.yig_fitting_action.isEnabled()
    window.yig_fitting_action.trigger()
    qapp.processEvents()
    analysis = window._yig_fitting_windows[window.experiment.data_identity]
    assert analysis is not None and analysis.isVisible()
    assert analysis.windowTitle().startswith("YIG Mirror Analysis")
    analysis.tabs.setCurrentIndex(3)
    assert not analysis.phase_panel.chk_coarse_enabled.isChecked()
    assert not analysis.phase_panel.coarse_controls.isVisible()
    analysis.resize(900, 680)
    qapp.processEvents()

    for combo in (analysis.cmb_s, analysis.cmb_axis, analysis.phase_panel.cmb_coarse_mode):
        combo.showPopup()
        qapp.processEvents()
        assert combo.view().isVisible()
        combo.hidePopup()
        qapp.processEvents()
    analysis.phase_panel.scroll.ensureWidgetVisible(
        analysis.phase_panel.chk_coarse_enabled, 4, 4
    )
    qapp.processEvents()
    analysis.phase_panel.chk_coarse_enabled.click()
    qapp.processEvents()
    assert analysis.phase_panel.coarse_controls.isVisible()

    deadline = time.monotonic() + 45
    while analysis.fit_controller.data is None and time.monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(10)
    assert analysis.fit_controller.data is not None
    if window.experiment.step_axes:
        analysis.cmb_axis.setCurrentText(window.experiment.step_axes[0].channel.name)
    assert analysis.phase_panel.btn_coarse_run.isEnabled()
    QTest.mouseClick(analysis.phase_panel.btn_coarse_run, Qt.LeftButton)
    worker = analysis.fit_controller.phase._coarse_worker
    if worker is not None:
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(30000, loop.quit)
        loop.exec()
    else:
        deadline = time.monotonic() + 30
        while not analysis.fit_controller.phase.coarse_points and time.monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
    status = analysis.phase_panel.lbl_coarse_status.text().lower()
    assert "candidate" in status or "failed" in status or "cancelled" in status
    assert all(point.type.startswith("Coarse ") for point in analysis.fit_controller.phase.coarse_points)
    assert analysis.phase_panel.results_table.rowCount() == len(
        analysis.fit_controller.phase.physical_points + analysis.fit_controller.phase.coarse_points
    )
    from app.analysis.yig_mirror.results.model import AnalysisPoint
    phase = analysis.fit_controller.phase
    identity = analysis.fit_controller.data["identity"]
    phase.coarse_points = [AnalysisPoint(
        "Coarse Node Candidate", "Coarse Detector", 5.024e9,
        trace_identity=identity, sweep_value=0.25,
    )]
    saved_phase = phase.state()
    phase.coarse_points = []
    phase.apply_state(saved_phase)
    phase.restore_data_bound_results()
    assert len(phase.coarse_points) == 1
    assert phase.coarse_points[0].type == "Coarse Node Candidate"
    phase.apply_state(saved_phase)
    phase._pending_saved_points["identity"] = "different-canonical-data"
    phase.restore_data_bound_results()
    assert phase.coarse_points == []
    window.close()
    qapp.processEvents()
