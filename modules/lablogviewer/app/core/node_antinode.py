"""Data-model adapter and numerical core for Node / Antinode analysis.

The Node calculation follows ``references/node_antinode/labber_viewer_app.py``:
per-sweep dip detection, linear resonance-trajectory fit, a dynamic frequency
window, moving-average smoothing with the same edge treatment, then scipy's
peak-distance/prominence detection. Antinodes are the minima of that same
smoothed average-transmission curve; this extension is not present in the
reference implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from app.core.data_model import Data2DError, Experiment, Grid2DData


class NodeAntinodeError(ValueError):
    """The selected Data or parameters cannot support an unambiguous analysis."""


@dataclass(frozen=True)
class NodeAntinodeParameters:
    window_half_width: float
    smoothing_points: int = 3
    minimum_distance: int = 10
    prominence: float = 0.0002
    dip_depth_threshold: float | None = None
    detection_mode: str = "Both"


@dataclass(frozen=True)
class NodeAntinodeResult:
    sweep_values: np.ndarray
    frequency_values: np.ndarray
    dip_frequencies: np.ndarray
    dip_depths: np.ndarray
    valid_fit_mask: np.ndarray
    fitted_frequency: np.ndarray
    average_transmission: np.ndarray
    smoothed_transmission: np.ndarray
    exact_resonance_frequencies: np.ndarray
    node_indices: np.ndarray
    antinode_indices: np.ndarray
    slope: float
    intercept: float
    dip_depth_threshold: float


def _find_peaks_numpy(values: np.ndarray, *, distance: int,
                      prominence: float) -> np.ndarray:
    """SciPy-compatible local-maximum subset for distance/prominence only.

    SciPy remains the preferred implementation. This small fallback preserves
    the reference's two requested peak properties on one-dimensional finite
    curves when SciPy is not installed in an existing user environment.
    """
    curve = np.asarray(values, dtype=float).reshape(-1)
    peaks: list[int] = []
    index = 1
    while index < curve.size - 1:
        if not np.isfinite(curve[index]):
            index += 1
            continue
        left = index
        right = index
        while right + 1 < curve.size and np.isfinite(curve[right + 1]) and curve[right + 1] == curve[index]:
            right += 1
        if (left > 0 and right + 1 < curve.size
                and np.isfinite(curve[left - 1]) and np.isfinite(curve[right + 1])
                and curve[left - 1] < curve[index] and curve[right + 1] < curve[index]):
            peaks.append((left + right) // 2)
        index = right + 1

    if not peaks:
        return np.asarray([], dtype=int)
    candidates = np.asarray(peaks, dtype=int)
    kept = np.ones(candidates.size, dtype=bool)
    for candidate_index in np.argsort(curve[candidates])[::-1]:
        if not kept[candidate_index]:
            continue
        close = np.abs(candidates - candidates[candidate_index]) < int(distance)
        close[candidate_index] = False
        kept[close] = False

    candidates = candidates[kept]
    prominences = []
    for peak in candidates:
        height = curve[peak]
        left_min = height
        cursor = peak
        while cursor > 0 and np.isfinite(curve[cursor - 1]) and curve[cursor - 1] <= height:
            cursor -= 1
            left_min = min(left_min, curve[cursor])
        right_min = height
        cursor = peak
        while (cursor + 1 < curve.size and np.isfinite(curve[cursor + 1])
               and curve[cursor + 1] <= height):
            cursor += 1
            right_min = min(right_min, curve[cursor])
        prominences.append(height - max(left_min, right_min))
    return candidates[np.asarray(prominences) >= float(prominence)]


def _find_peaks(values: np.ndarray, *, distance: int, prominence: float) -> np.ndarray:
    try:
        from scipy.signal import find_peaks
    except ImportError:
        return _find_peaks_numpy(values, distance=distance, prominence=prominence)
    peaks, _ = find_peaks(values, distance=distance, prominence=prominence)
    return np.asarray(peaks, dtype=int)


def get_node_analysis_grid(
    experiment: Experiment, channel_name: str, sweep_axis_name: str | None = None
) -> Grid2DData:
    """Return the selected complex channel as [sweep, frequency] via the model."""
    trace = experiment.vector_traces.get(channel_name)
    if trace is None or not trace.complex:
        raise NodeAntinodeError("Choose a complex vector S-parameter channel.")
    if len(experiment.step_axes) != 1:
        raise NodeAntinodeError(
            "Node / Antinode analysis requires exactly one active sweep dimension."
        )
    available_sweep = experiment.step_axes[0].channel.name
    if sweep_axis_name is not None and sweep_axis_name != available_sweep:
        raise NodeAntinodeError(f"Sweep axis {sweep_axis_name!r} is not the active Data Model axis.")
    if not trace.x_name:
        raise NodeAntinodeError("The selected S-parameter has no named frequency axis.")
    try:
        grid = experiment.get_2d_data(
            x_channel=trace.x_name,
            y_channel=sweep_axis_name or available_sweep,
            z_channel=channel_name,
            transform="raw",
        )
    except (Data2DError, ValueError) as error:
        raise NodeAntinodeError(str(error)) from error
    if grid.z_values.ndim != 2 or np.iscomplexobj(grid.z_values) is False:
        raise NodeAntinodeError("The selected Data did not produce a complex 2D sweep.")
    if grid.z_values.shape != (grid.y_values.size, grid.x_values.size):
        raise NodeAntinodeError(
            "The Data Model returned an inconsistent sweep/frequency orientation."
        )
    return grid


def analyze_nodes_and_antinodes(
    sweep_values: np.ndarray,
    frequency_values: np.ndarray,
    complex_data: np.ndarray,
    parameters: NodeAntinodeParameters,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> NodeAntinodeResult:
    """Analyze a complex matrix whose rows are sweep entries and columns frequency."""
    def check_cancelled() -> None:
        if cancel_check is not None and cancel_check():
            raise NodeAntinodeError("Analysis cancelled.")

    check_cancelled()
    sweep = np.asarray(sweep_values, dtype=float).reshape(-1)
    frequency = np.asarray(frequency_values, dtype=float).reshape(-1)
    data = np.asarray(complex_data)
    if data.ndim != 2 or data.shape != (sweep.size, frequency.size):
        raise NodeAntinodeError(
            "Complex data must have shape (number of sweep values, number of frequency values)."
        )
    if sweep.size < 2 or frequency.size < 3:
        raise NodeAntinodeError("At least two sweep entries and three frequency points are required.")
    if not np.all(np.isfinite(sweep)) or not np.all(np.isfinite(frequency)):
        raise NodeAntinodeError("Sweep and frequency coordinates must be finite.")
    if not np.all(np.isfinite(data.real) & np.isfinite(data.imag)):
        raise NodeAntinodeError("The selected complex Data contains NaN or infinite samples.")
    if not np.isfinite(parameters.window_half_width) or parameters.window_half_width <= 0:
        raise NodeAntinodeError("Dynamic frequency half-width must be positive and finite.")
    if parameters.smoothing_points < 1 or parameters.smoothing_points > sweep.size:
        raise NodeAntinodeError(
            f"Smoothing length must be between 1 and {sweep.size} sweep entries."
        )
    if parameters.minimum_distance < 1:
        raise NodeAntinodeError("Minimum peak distance must be at least one sweep entry.")
    if not np.isfinite(parameters.prominence) or parameters.prominence < 0:
        raise NodeAntinodeError("Peak prominence must be finite and non-negative.")
    if parameters.detection_mode not in {"Node", "Antinode", "Both"}:
        raise NodeAntinodeError("Detection mode must be Node, Antinode, or Both.")

    magnitude = np.abs(data)
    dip_indices = np.argmin(magnitude, axis=1)
    dip_frequencies = frequency[dip_indices]
    dip_depths = np.max(magnitude, axis=1) - magnitude[np.arange(sweep.size), dip_indices]
    threshold = (
        float(parameters.dip_depth_threshold)
        if parameters.dip_depth_threshold is not None
        else float(np.median(dip_depths))
    )
    valid_fit_mask = dip_depths > threshold
    if int(np.count_nonzero(valid_fit_mask)) < 2:
        raise NodeAntinodeError(
            "Fewer than two sweep traces pass the dip-depth threshold; lower the threshold."
        )
    fit_sweep = sweep[valid_fit_mask]
    if np.unique(fit_sweep).size < 2:
        raise NodeAntinodeError("The selected sweep coordinates do not support a linear fit.")

    slope, intercept = (float(value) for value in np.polyfit(
        fit_sweep, dip_frequencies[valid_fit_mask], 1
    ))
    fitted_frequency = slope * sweep + intercept
    average_transmission = np.full(sweep.size, np.nan, dtype=float)
    exact_resonance_frequencies = np.full(sweep.size, np.nan, dtype=float)

    for index, sweep_value in enumerate(sweep):
        if index % 32 == 0:
            check_cancelled()
        center = slope * sweep_value + intercept
        in_window = np.abs(frequency - center) <= parameters.window_half_width
        if not np.any(in_window):
            continue
        window_magnitude = magnitude[index, in_window]
        window_frequency = frequency[in_window]
        average_transmission[index] = float(np.mean(window_magnitude))
        exact_resonance_frequencies[index] = float(
            window_frequency[int(np.argmin(window_magnitude))]
        )

    window_length = int(parameters.smoothing_points)
    if window_length > 1:
        kernel = np.ones(window_length, dtype=float) / window_length
        smoothed_transmission = np.convolve(average_transmission, kernel, mode="same")
        half = window_length // 2
        if half > 0:
            smoothed_transmission[:half] = average_transmission[:half]
            smoothed_transmission[-half:] = average_transmission[-half:]
    else:
        smoothed_transmission = average_transmission.copy()

    check_cancelled()
    node_indices = _find_peaks(
        smoothed_transmission,
        distance=int(parameters.minimum_distance),
        prominence=float(parameters.prominence),
    )
    antinode_indices = _find_peaks(
        -smoothed_transmission,
        distance=int(parameters.minimum_distance),
        prominence=float(parameters.prominence),
    )
    check_cancelled()
    if parameters.detection_mode == "Node":
        antinode_indices = np.asarray([], dtype=int)
    elif parameters.detection_mode == "Antinode":
        node_indices = np.asarray([], dtype=int)
    return NodeAntinodeResult(
        sweep_values=sweep.copy(),
        frequency_values=frequency.copy(),
        dip_frequencies=dip_frequencies,
        dip_depths=dip_depths,
        valid_fit_mask=valid_fit_mask,
        fitted_frequency=fitted_frequency,
        average_transmission=average_transmission,
        smoothed_transmission=smoothed_transmission,
        exact_resonance_frequencies=exact_resonance_frequencies,
        node_indices=np.asarray(node_indices, dtype=int),
        antinode_indices=np.asarray(antinode_indices, dtype=int),
        slope=slope,
        intercept=intercept,
        dip_depth_threshold=threshold,
    )
