"""Stable public entry points for YIG Mirror analysis.

The implementation is intentionally delegated to the already-tested
``yig_fitting`` modules. GUI callers should prefer these functions over
depending on private implementation layout.
"""

from __future__ import annotations

from app.analysis.yig_fitting.core.batch import run_batch
from app.analysis.yig_fitting.core.data_io import dataset_from_experiment
from app.analysis.yig_fitting.core.fitting import run_fit
from app.analysis.yig_fitting.core.phase import (
    fit_kappa_line,
    fit_phase_line,
    run_global_fit,
)
from app.analysis.yig_mirror.node_antinode.coarse import (
    coarse_detection_points,
    run_coarse_detector,
)
from app.analysis.yig_mirror.node_antinode.physical import analyze_physical_positions
from app.analysis.yig_mirror.node_antinode.physical import SIN_SQUARED_COUPLING
from app.analysis.yig_mirror.results.model import AnalysisPoint


def adapt_experiment(experiment) -> dict:
    """Adapt an existing in-memory Experiment; performs no HDF5 I/O."""
    return dataset_from_experiment(experiment)


def fit_single_trace(*args, **kwargs):
    """Run one complex/magnitude fit with the existing scientific engine."""
    return run_fit(*args, **kwargs)


def fit_batch(*args, **kwargs):
    """Run the existing per-trace fit engine with isolated trace failures."""
    return run_batch(*args, **kwargs)


def fit_global(*args, **kwargs):
    """Run shared/slice/fixed/phase-linked global optimization."""
    return run_global_fit(*args, **kwargs)


def estimate_kappa_phase_line(frequency_hz, kappa, *, phase=None, **kwargs):
    """Estimate the phase line from κeff or fitted phase observations."""
    if phase is None:
        return fit_kappa_line(frequency_hz, kappa, **kwargs)
    return fit_phase_line(frequency_hz, phase, **kwargs)


__all__ = [
    "AnalysisPoint", "adapt_experiment", "analyze_physical_positions",
    "coarse_detection_points", "estimate_kappa_phase_line", "fit_batch",
    "fit_global", "fit_single_trace", "run_coarse_detector",
]
