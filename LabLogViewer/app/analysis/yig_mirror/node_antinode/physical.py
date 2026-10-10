"""Physical extrema derived from the verified ``kappa_b * sin(phi)**2`` model."""

from __future__ import annotations

from math import sin

from app.analysis.yig_fitting.core.phase import (
    antinode_frequencies,
    node_frequencies,
    phase_line,
)
from app.analysis.yig_mirror.results.model import AnalysisPoint


SIN_SQUARED_COUPLING = "kappa_b * sin(phi)**2"


def analyze_physical_positions(
    *, model_relation: str, T_ns: float, phi_ref: float, f_ref_hz: float,
    f_min_hz: float, f_max_hz: float, kappa_b_hz: float | None = None,
    trace_identity: str | None = None,
) -> list[AnalysisPoint]:
    """Return Node and Antinode positions only for the declared verified model.

    For ``kappa_m = kappa_b sin²(phi)``, minima occur at ``phi=n*pi`` and
    maxima at ``phi=(n+1/2)*pi``.  Other model conventions are rejected rather
    than silently interpreted as this physical relation. All frequencies,
    ``T`` and ``kappa_b`` use Hz, ns, and Hz respectively as named.
    """
    if model_relation != SIN_SQUARED_COUPLING:
        raise ValueError("Physical positions require the verified sin² coupling model.")
    if kappa_b_hz is not None and kappa_b_hz < 0:
        raise ValueError("kappa_b must be non-negative.")

    out: list[AnalysisPoint] = []
    for type_name, positions in (
        ("Physical Node", node_frequencies(T_ns, phi_ref, f_ref_hz, f_min_hz, f_max_hz)),
        ("Physical Antinode", antinode_frequencies(T_ns, phi_ref, f_ref_hz, f_min_hz, f_max_hz)),
    ):
        for _index, frequency_hz in positions:
            phase_rad = float(phase_line(frequency_hz, T_ns, phi_ref, f_ref_hz))
            out.append(AnalysisPoint(
                type=type_name,
                method="Fitting-Based Physical Analysis",
                frequency_hz=frequency_hz,
                trace_identity=trace_identity,
                phase_rad=phase_rad,
                kappa_hz=(None if kappa_b_hz is None else
                          float(kappa_b_hz * sin(phase_rad) ** 2)),
            ))
    return sorted(out, key=lambda point: point.frequency_hz)
