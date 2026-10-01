"""Bridge the LabLogViewer Experiment model into the reference fitter format."""

from __future__ import annotations

import numpy as np


def _entry_coordinates(experiment, n_entries: int) -> dict[str, np.ndarray]:
    axes = list(experiment.step_axes)
    if not axes:
        return {}
    sizes = tuple(int(np.asarray(axis.values).size) for axis in axes)
    nominal = int(np.prod(sizes))
    coordinates: dict[str, np.ndarray] = {}
    if nominal == n_entries:
        for index, axis in enumerate(axes):
            shape = [1] * len(axes)
            shape[index] = sizes[index]
            values = np.asarray(axis.values, dtype=float).reshape(shape)
            coordinates[axis.channel.name] = np.broadcast_to(values, sizes).reshape(-1).copy()
    elif len(axes) == 1 and 0 < n_entries < nominal:
        coordinates[axes[0].channel.name] = np.asarray(axes[0].values, dtype=float)[:n_entries].copy()
    return coordinates


def dataset_from_experiment(experiment) -> dict:
    """Build the fitter's in-memory dataset without opening HDF5 here.

    Ambiguous incomplete N-D sweeps intentionally expose only the trace index,
    rather than inventing coordinates for the acquired entries.
    """
    traces = []
    for name, trace in experiment.vector_traces.items():
        if not trace.complex or not trace.x_name or "freq" not in trace.x_name.casefold():
            continue
        values = np.asarray(experiment.get_data(name, transform="raw"))
        if values.ndim == 1:
            values = values[:, None]
        if values.ndim != 2 or not np.iscomplexobj(values):
            continue
        if values.shape[0] != int(trace.n_points):
            continue
        traces.append((name, trace, values))

    if not traces:
        raise ValueError("The Viewer Data has no complex vector channel with a frequency axis.")

    first_name, first_trace, first_values = traces[0]
    n_entries = int(first_values.shape[1])
    frequency = np.asarray(first_trace.x_values, dtype=float).reshape(-1)
    s_params: dict[str, np.ndarray] = {}
    omitted: list[str] = []
    for name, trace, values in traces:
        same_grid = (trace.x_values.shape == frequency.shape and
                     np.array_equal(np.asarray(trace.x_values), frequency))
        if values.shape[1] != n_entries or not same_grid:
            omitted.append(name)
            continue
        s_params[name] = np.asarray(values, dtype=np.complex128)

    if not s_params:
        raise ValueError("No compatible complex S-parameter traces share the same frequency grid and sweep count.")

    step_channels = _entry_coordinates(experiment, n_entries)
    for name, channel in experiment.channels.items():
        if not channel.is_step or name in step_channels:
            continue
        try:
            values = np.asarray(experiment.get_step_values(name), dtype=float).reshape(-1)
        except Exception:
            continue
        if values.size == n_entries:
            step_channels[name] = values.copy()

    db_values = {}
    for name, values in s_params.items():
        with np.errstate(divide="ignore", invalid="ignore"):
            db_values[name] = (20.0 * np.log10(np.abs(values))).astype(np.float32)

    return {
        "path": str(experiment.source_path),
        "identity": str(experiment.data_identity),
        "log_name": str(experiment.display_name),
        "frequency": frequency.copy(),
        "s_params": s_params,
        "s_db": db_values,
        "step_channels": step_channels,
        "n_steps": n_entries,
        "omitted_channels": omitted,
    }
