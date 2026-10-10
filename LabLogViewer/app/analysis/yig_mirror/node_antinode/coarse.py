"""Adapter from the retained legacy empirical detector to labeled results."""

from __future__ import annotations

from app.core.node_antinode import (
    NodeAntinodeParameters,
    NodeAntinodeResult,
    analyze_nodes_and_antinodes,
)
from app.analysis.yig_mirror.results.model import AnalysisPoint


def coarse_detection_points(
    result: NodeAntinodeResult, *, trace_identity: str | None = None,
) -> list[AnalysisPoint]:
    """Convert empirical peak/minimum indices to explicitly coarse candidates."""
    points = []
    for indices, type_name in (
        (result.node_indices, "Coarse Node Candidate"),
        (result.antinode_indices, "Coarse Antinode Candidate"),
    ):
        for index in indices:
            points.append(AnalysisPoint(
                type=type_name,
                method="Coarse Detector",
                frequency_hz=float(result.exact_resonance_frequencies[index]),
                trace_identity=trace_identity,
                sweep_value=float(result.sweep_values[index]),
                status="Good" if bool(result.valid_fit_mask[index]) else "Warning",
            ))
    return points


def run_coarse_detector(
    sweep_values,
    frequency_values,
    complex_data,
    parameters: NodeAntinodeParameters,
    *,
    trace_identity: str | None = None,
    cancel_check=None,
):
    """Run the retained empirical algorithm and return its labeled records."""
    result = analyze_nodes_and_antinodes(
        sweep_values,
        frequency_values,
        complex_data,
        parameters,
        cancel_check=cancel_check,
    )
    return result, coarse_detection_points(result, trace_identity=trace_identity)
