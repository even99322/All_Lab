"""Lightweight, reusable preparation of Browser quick-preview data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from app.core.data_model import AcquisitionStatus, Grid2DData, Slice1DData
from app.core.labber_parser import load_experiment
from app.core.formula import FormulaError, apply_formulas


@dataclass
class QuickPreviewData:
    kind: str
    log_name: str
    channel_name: str
    data: Grid2DData | Slice1DData
    acquisition: AcquisitionStatus | None = None
    colormap: str = "LabLog BWR"
    z_min: float | None = None
    z_max: float | None = None
    restored: bool = False


def _channel_order(experiment) -> list[str]:
    """Prefer vector measurements while remaining channel-name agnostic."""
    names = list(experiment.log_channel_names)
    return sorted(
        names,
        key=lambda name: (
            name not in experiment.vector_traces,
            not bool(experiment.vector_traces.get(name) and experiment.vector_traces[name].complex),
            names.index(name),
        ),
    )


_PREVIEW_COLORMAPS = {"LabLog BWR", "bwr", "viridis", "plasma", "inferno", "magma", "gray"}
_PREVIEW_TRANSFORMS = {"raw", "real", "imag", "magnitude", "magnitude_db", "phase_deg", "phase_rad"}


def _valid_2d_options(experiment, state: dict[str, Any]) -> tuple[str, str, str, str, str, float | None, float | None] | None:
    two_d = state.get("two_d")
    if not isinstance(two_d, dict):
        return None
    z_name = two_d.get("z")
    x_name = two_d.get("x")
    y_name = two_d.get("y")
    transform = two_d.get("transform")
    colormap = two_d.get("colormap", "LabLog BWR")
    if not all(isinstance(value, str) and value for value in (z_name, x_name, y_name, transform, colormap)):
        return None
    if z_name not in experiment.log_channel_names or transform not in _PREVIEW_TRANSFORMS:
        return None
    dimensions = experiment.list_dimensions(z_name)
    if len(dimensions) != 2 or {x_name, y_name} != {dimension.name for dimension in dimensions}:
        return None
    if colormap not in _PREVIEW_COLORMAPS:
        return None
    auto_color = two_d.get("auto_color", True)
    if not isinstance(auto_color, bool):
        return None
    if auto_color:
        return z_name, x_name, y_name, transform, colormap, None, None
    minimum, maximum = two_d.get("minimum"), two_d.get("maximum")
    if not isinstance(minimum, (int, float)) or not isinstance(maximum, (int, float)):
        return None
    if not np.isfinite(minimum) or not np.isfinite(maximum) or minimum >= maximum:
        return None
    return z_name, x_name, y_name, transform, colormap, float(minimum), float(maximum)


def _build_1d_preview(experiment, channel_name: str, transform: str, trace_index: int,
                      x_axis: str | None = None) -> QuickPreviewData | None:
    dimensions = experiment.list_dimensions(channel_name)
    channel = experiment.get_channel(channel_name)
    if channel_name in experiment.vector_traces:
        trace_info = experiment.vector_traces[channel_name]
        # A vector trace plus one sweep dimension is a valid 2D heatmap,
        # but each measured sweep entry remains a scientifically real 1D
        # trace. This is the Viewer-to-Preview case that must not fall back
        # to the default heatmap.
        if x_axis and x_axis not in (trace_info.x_name, "Index"):
            return None
        entry_count = trace_info.n_entries
        if not 0 <= trace_index < entry_count:
            return None
        values = experiment.get_data(channel_name, transform=transform, entry_slice=trace_index)
        values = np.asarray(values).reshape(-1)
        x_values = np.asarray(trace_info.x_values)
        x_name, x_unit = trace_info.x_name or "Index", trace_info.x_unit
    else:
        if len(dimensions) != 1:
            return None
        dimension = dimensions[0]
        if x_axis and x_axis != dimension.name:
            return None
        values = np.asarray(experiment.get_data(channel_name, transform=transform)).reshape(-1)
        x_values = np.asarray(dimension.values)
        x_name, x_unit = dimension.name, dimension.unit
    if len(x_values) != values.size:
        return None
    trace = Slice1DData(
        x_values=x_values, y_values=values,
        x_name=x_name, x_unit=x_unit,
        z_name=channel_name, z_unit=channel.unit, transform=transform,
    )
    return QuickPreviewData(
        "1d", experiment.log_name, channel_name, trace,
        acquisition=experiment.acquisition_status(channel_name),
    )


def build_quick_preview(path: str, display_state: dict[str, Any] | None = None) -> QuickPreviewData:
    """Load one useful 1D trace or 2D slice, then close the HDF5 file."""
    with load_experiment(path) as experiment:
        channels = _channel_order(experiment)
        if not channels:
            raise ValueError("This log has no plottable measurement channels.")

        # A saved state is advisory. Validate it completely against the current
        # data before using it, then fall through to the normal default preview.
        if isinstance(display_state, dict):
            options = _valid_2d_options(experiment, display_state)
            if options is not None:
                z_name, x_name, y_name, transform, colormap, z_min, z_max = options
                try:
                    grid = experiment.get_2d_data(x_name, y_name, z_name, transform=transform)
                except Exception:
                    pass
                else:
                    return QuickPreviewData(
                        "2d", experiment.log_name, z_name, grid,
                        acquisition=experiment.acquisition_status(z_name),
                        colormap=colormap, z_min=z_min, z_max=z_max, restored=True,
                    )
            one_d = display_state.get("one_d")
            if isinstance(one_d, dict):
                channel_name = one_d.get("channel")
                x_axis = one_d.get("x_axis")
                transform = one_d.get("transform")
                trace_index = one_d.get("trace_index", 0)
                if (isinstance(channel_name, str) and channel_name in channels
                        and isinstance(transform, str) and transform in _PREVIEW_TRANSFORMS
                        and isinstance(trace_index, int)):
                    restored = _build_1d_preview(
                        experiment, channel_name, transform, trace_index,
                        x_axis if isinstance(x_axis, str) else None,
                    )
                    if restored is not None and bool(one_d.get("formula_enabled", False)):
                        x_formula, y_formula = one_d.get("x_formula", ""), one_d.get("y_formula", "")
                        try:
                            if not isinstance(x_formula, str) or not isinstance(y_formula, str):
                                raise FormulaError("Saved Formula state is invalid.")
                            x_values, y_values = apply_formulas(
                                restored.data.x_values, restored.data.y_values,
                                x_formula, y_formula,
                            )
                        except FormulaError:
                            restored = None
                        else:
                            restored.data.x_values = x_values
                            restored.data.y_values = y_values
                            if x_formula:
                                restored.data.x_name = f"{restored.data.x_name}′"
                                restored.data.x_unit = None
                            if y_formula:
                                restored.data.z_name = f"{restored.data.z_name}′"
                                restored.data.z_unit = None
                    if restored is not None:
                        restored.restored = True
                        return restored

        # A true two-dimensional channel is the best browser preview. Higher
        # dimensional data is intentionally left to the full Viewer.
        for channel_name in channels:
            dimensions = experiment.list_dimensions(channel_name)
            if len(dimensions) != 2:
                continue
            channel = experiment.get_channel(channel_name)
            transform = "magnitude_db" if channel.is_complex else "raw"
            grid = experiment.get_2d_data(
                dimensions[0].name,
                dimensions[1].name,
                channel_name,
                transform=transform,
            )
            return QuickPreviewData("2d", experiment.log_name, channel_name, grid,
                                    acquisition=experiment.acquisition_status(channel_name))

        for channel_name in channels:
            dimensions = experiment.list_dimensions(channel_name)
            if len(dimensions) != 1:
                continue
            channel = experiment.get_channel(channel_name)
            transform = "magnitude_db" if channel.is_complex else "raw"
            preview = _build_1d_preview(experiment, channel_name, transform, 0)
            if preview is not None:
                return preview

        raise ValueError("Quick Preview supports one- and two-dimensional data; open the full Viewer for this log.")
