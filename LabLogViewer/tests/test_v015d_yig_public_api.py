from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app.analysis.yig_mirror.node_antinode.coarse import coarse_detection_points
from app.analysis.yig_mirror.node_antinode.physical import (
    SIN_SQUARED_COUPLING,
    analyze_physical_positions,
)
from app.analysis.yig_mirror.results.model import AnalysisPoint


def test_physical_node_and_antinode_follow_verified_sin_squared_convention():
    T_ns = 1.25
    phi_ref = 0.2
    f_ref_hz = 5e9
    points = analyze_physical_positions(
        model_relation=SIN_SQUARED_COUPLING,
        T_ns=T_ns,
        phi_ref=phi_ref,
        f_ref_hz=f_ref_hz,
        f_min_hz=4.0e9,
        f_max_hz=6.0e9,
        kappa_b_hz=8e6,
        trace_identity="canonical:data-id",
    )
    nodes = [point for point in points if point.type == "Physical Node"]
    antinodes = [point for point in points if point.type == "Physical Antinode"]
    assert nodes and antinodes
    for point in nodes:
        np.testing.assert_allclose(np.sin(point.phase_rad) ** 2, 0.0, atol=1e-12)
        np.testing.assert_allclose(point.kappa_hz, 0.0, atol=1e-6)
        assert point.method == "Fitting-Based Physical Analysis"
    for point in antinodes:
        np.testing.assert_allclose(np.sin(point.phase_rad) ** 2, 1.0, atol=1e-12)
        np.testing.assert_allclose(point.kappa_hz, 8e6, rtol=1e-12)


def test_physical_extrema_refuse_unverified_phase_conventions():
    with pytest.raises(ValueError, match="verified sin² coupling"):
        analyze_physical_positions(
            model_relation="unknown-model",
            T_ns=1.0,
            phi_ref=0.0,
            f_ref_hz=5e9,
            f_min_hz=4e9,
            f_max_hz=6e9,
        )


def test_analysis_point_round_trip_keeps_physical_and_coarse_methods_distinct():
    physical = AnalysisPoint(
        type="Physical Node",
        method="Fitting-Based Physical Analysis",
        frequency_hz=5.0e9,
        phase_rad=0.0,
        kappa_hz=0.0,
    )
    assert AnalysisPoint.from_dict(physical.to_dict()) == physical

    coarse_result = SimpleNamespace(
        node_indices=np.asarray([1]),
        antinode_indices=np.asarray([2]),
        exact_resonance_frequencies=np.asarray([4.9e9, 5.0e9, 5.1e9]),
        sweep_values=np.asarray([0.0, 1.0, 2.0]),
        valid_fit_mask=np.asarray([True, True, False]),
    )
    coarse = coarse_detection_points(coarse_result, trace_identity="canonical:data-id")
    assert [point.type for point in coarse] == [
        "Coarse Node Candidate", "Coarse Antinode Candidate",
    ]
    assert [point.method for point in coarse] == ["Coarse Detector", "Coarse Detector"]
    assert coarse[1].status == "Warning"
    assert coarse[1].phase_rad is None
