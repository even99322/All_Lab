"""Validated, complex-domain De-background processing for Labber logs.

All measurement reads go through HDF5Reader -> LabberParser -> Experiment.
Only the isolated HDF5 processing writer can modify the copied output, and
it is restricted to the selected, existing complex trace dataset.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import threading
from typing import Callable

import numpy as np

from app.core.data_model import Experiment, VectorTraceInfo
from app.core.hdf5_reader import HDF5Reader
from app.core.hdf5_processing_writer import (
    HDF5ComplexTraceWriter,
    HDF5ProcessingWriteError,
    copy_to_temporary,
    publish_temporary,
)
from app.core.labber_parser import AutoDetector


ProgressCallback = Callable[[int, str], None]
_S_PARAMETER = re.compile(r"(?<![A-Za-z0-9])S\d{2}(?![A-Za-z0-9])", re.IGNORECASE)
_DENOMINATOR_RELATIVE_FLOOR = math.sqrt(np.finfo(np.float64).eps)
_LEGACY_COMPONENT_DTYPE = np.dtype(np.float16)


class DeBackgroundError(RuntimeError):
    """Base error presented by the processing dialog."""


class DeBackgroundCompatibilityError(DeBackgroundError):
    """The selected measurements cannot be safely combined."""


class DeBackgroundOutputExists(DeBackgroundError):
    """The destination exists and requires explicit replacement approval."""


class DeBackgroundCancelled(DeBackgroundError):
    """Processing was cancelled and its temporary output was removed."""


@dataclass(frozen=True)
class CompatibilityCheck:
    label: str
    status: str  # "ok" | "fail" | "warning" | "pending"
    detail: str


@dataclass(frozen=True)
class DeBackgroundInspection:
    target_path: str
    background_path: str
    channel_options: tuple[str, ...]
    selected_channel: str | None
    checks: tuple[CompatibilityCheck, ...]
    can_generate: bool
    frequency_tolerance: float | None = None


def divide_complex_trace(target: np.ndarray, background: np.ndarray) -> np.ndarray:
    """Return raw complex division, broadcasting one frequency trace over sweeps."""
    target = np.asarray(target, dtype=np.complex128)
    background = np.asarray(background, dtype=np.complex128)
    if target.ndim != 2:
        raise DeBackgroundCompatibilityError(
            f"Target complex data must be frequency × sweep-entry, got {target.shape}."
        )
    if background.ndim == 2 and background.shape[1] == 1:
        background = background[:, 0]
    if background.ndim != 1 or background.shape[0] != target.shape[0]:
        raise DeBackgroundCompatibilityError(
            "Background must be one complex frequency trace with the same point count."
        )
    if not np.all(np.isfinite(target)):
        raise DeBackgroundCompatibilityError("Target contains NaN or Inf measurement values.")
    if not np.all(np.isfinite(background)):
        raise DeBackgroundCompatibilityError("Background contains NaN or Inf measurement values.")
    if np.any(background == 0):
        raise DeBackgroundCompatibilityError("Background contains a zero complex denominator.")
    with np.errstate(divide="raise", invalid="raise", over="raise"):
        result = target / background[:, None]
    if not np.all(np.isfinite(result)):
        raise DeBackgroundCompatibilityError("Complex division produced NaN or Inf values.")
    return result


def _legacy_components(values: np.ndarray, output_dtype: np.dtype) -> tuple[np.ndarray, np.ndarray]:
    """Match the stored precision observed in Labber's supplied legacy result.

    The legacy output keeps the Target dataset's float64 HDF5 dtype but its
    real and imaginary values are exactly component-wise float16 quantized.
    This cast reproduces that established file behavior without changing the
    dataset layout or dtype.
    """
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        real = values.real.astype(_LEGACY_COMPONENT_DTYPE).astype(output_dtype)
        imag = values.imag.astype(_LEGACY_COMPONENT_DTYPE).astype(output_dtype)
    if not np.all(np.isfinite(real)) or not np.all(np.isfinite(imag)):
        raise DeBackgroundCompatibilityError(
            "The ratio exceeds the legacy-compatible component storage range."
        )
    return real, imag


def _load_experiment(path: Path, role: str) -> Experiment:
    if not path.exists() or not path.is_file():
        raise DeBackgroundError(f"{role} file is missing: {path}")
    reader = HDF5Reader(path)
    try:
        return AutoDetector.detect_and_parse(reader)
    except Exception:
        reader.close()
        raise


def _candidate_channels(experiment: Experiment) -> list[str]:
    ordered_names = list(experiment.log_channel_names)
    known_names = set(ordered_names)
    ordered_names.extend(name for name in experiment.vector_traces if name not in known_names)
    return [
        name for name in ordered_names
        if (trace := experiment.vector_traces.get(name)) is not None
        and trace.complex and trace.channel.is_complex and _S_PARAMETER.search(name)
        and (
            "vna" in (trace.channel.instrument or "").casefold()
            or "network analyzer" in (trace.channel.instrument or "").casefold()
            or re.search(r"\bVNA\b", name, re.IGNORECASE)
        )
    ]


def _grid_tolerance(target: np.ndarray, background: np.ndarray) -> float:
    scale = max(1.0, float(np.max(np.abs(target))), float(np.max(np.abs(background))))
    tolerance = 8.0 * np.finfo(np.float64).eps * scale
    spacings = []
    for values in (target, background):
        diffs = np.abs(np.diff(values))
        diffs = diffs[diffs > 0]
        if diffs.size:
            spacings.append(float(np.min(diffs)))
    if spacings:
        tolerance = min(tolerance, min(spacings) * 1e-8)
    return tolerance


def _trace_grid(experiment: Experiment, trace: VectorTraceInfo) -> tuple[np.ndarray, str | None]:
    if not trace.x_name or "freq" not in trace.x_name.casefold():
        return np.asarray(trace.x_values, dtype=np.float64), "trace axis is not identified as Frequency"
    values = np.asarray(trace.x_values, dtype=np.float64)
    if values.ndim != 1 or not values.size or not np.all(np.isfinite(values)):
        return values, "Frequency coordinates are missing or non-finite"
    path = f"{trace.trace_path}_t0dt"
    if experiment._reader.kind_of(path) != "dataset":
        return values, "Labber frequency calibration (_t0dt) is unavailable"
    calibration = np.asarray(experiment._reader.read(path), dtype=np.float64)
    if calibration.ndim == 1 and calibration.size == 2:
        calibration = calibration.reshape(1, 2)
    if calibration.ndim != 2 or calibration.shape[1] != 2 or not np.all(np.isfinite(calibration)):
        return values, "Labber frequency calibration has an unsupported structure"
    if calibration.shape[0] not in (1, trace.n_entries):
        return values, "Labber frequency calibration count does not match trace entries"
    indices = np.arange(trace.n_points, dtype=np.float64)
    grids = calibration[:, :1] + calibration[:, 1:2] * indices[None, :]
    tolerance = _grid_tolerance(values, values)
    if not np.all(np.abs(grids - values[None, :]) <= tolerance):
        return values, "Per-entry frequency calibration differs from the parser's common axis"
    if grids.shape[0] > 1 and not np.all(np.abs(grids - grids[:1]) <= tolerance):
        return values, "Frequency grid changes between sweep entries"
    return values, None


def _monotonic_direction(values: np.ndarray) -> int | None:
    if values.size < 2:
        return 1
    differences = np.diff(values)
    if np.all(differences > 0):
        return 1
    if np.all(differences < 0):
        return -1
    return None


def _format_range(values: np.ndarray, unit: str | None) -> str:
    if values.size == 0:
        return "unavailable"
    scale, label = (1e9, "GHz") if (unit or "").casefold() == "hz" else (1.0, unit or "")
    return f"{values[0] / scale:.9g}–{values[-1] / scale:.9g} {label}".strip()


def _inspect_loaded(
    target: Experiment,
    background: Experiment,
    target_path: Path,
    background_path: Path,
    selected_channel: str | None,
) -> DeBackgroundInspection:
    target_candidates = _candidate_channels(target)
    background_candidates = _candidate_channels(background)
    options = tuple(dict.fromkeys([*target_candidates, *background_candidates]))
    shared = [name for name in target_candidates if name in background_candidates]
    if selected_channel is None:
        selected_channel = (shared or list(options) or [None])[0]

    checks: list[CompatibilityCheck] = []
    if selected_channel is None:
        checks.append(CompatibilityCheck(
            "S Parameter", "fail", "No complex S-parameter trace was found in either file."
        ))
        return DeBackgroundInspection(
            str(target_path), str(background_path), options, None,
            tuple(checks), False,
        )

    has_target = selected_channel in target.vector_traces and selected_channel in target_candidates
    has_background = selected_channel in background.vector_traces and selected_channel in background_candidates
    if has_target and has_background:
        checks.append(CompatibilityCheck("S Parameter", "ok", selected_channel))
    else:
        missing = []
        if not has_target:
            missing.append("Target")
        if not has_background:
            missing.append("Background")
        checks.append(CompatibilityCheck(
            "S Parameter", "fail", f"{selected_channel} is not a complex S-parameter in " + " and ".join(missing)
        ))

    target_trace = target.vector_traces.get(selected_channel)
    background_trace = background.vector_traces.get(selected_channel)
    target_grid = background_grid = np.empty(0, dtype=np.float64)
    target_grid_error = background_grid_error = None
    if target_trace is not None:
        target_grid, target_grid_error = _trace_grid(target, target_trace)
    if background_trace is not None:
        background_grid, background_grid_error = _trace_grid(background, background_trace)

    if target_trace is None:
        checks.append(CompatibilityCheck("Target", "fail", "Selected S parameter is missing."))
    else:
        acquisition = target.acquisition_status(selected_channel)
        if acquisition is not None and not acquisition.is_recoverable:
            checks.append(CompatibilityCheck("Target", "fail", acquisition.reason))
        elif not target.step_axes and target_trace.n_entries != 1:
            checks.append(CompatibilityCheck(
                "Target", "fail",
                "Target has multiple acquired sweep entries but no active step dimension to identify their coordinates.",
            ))
        elif target_trace.n_entries < 1 or target_trace.n_points != target_grid.size:
            checks.append(CompatibilityCheck("Target", "fail", "Target trace shape or Frequency axis is invalid."))
        elif target_grid_error:
            checks.append(CompatibilityCheck("Target", "fail", target_grid_error))
        else:
            extent = "partial acquisition; acquired samples only" if acquisition and acquisition.is_partial else f"{target_trace.n_entries} sweep entries"
            checks.append(CompatibilityCheck("Target", "ok", extent))

    if background_trace is None:
        checks.append(CompatibilityCheck("Background shape", "fail", "Selected S parameter is missing."))
    elif background.step_axes or background_trace.n_entries != 1:
        checks.append(CompatibilityCheck(
            "Background shape", "fail",
            f"Background must be one Frequency trace without active sweep dimensions; found {background_trace.n_entries} entries and {len(background.step_axes)} active sweep dimensions.",
        ))
    elif background_grid_error or background_trace.n_points != background_grid.size:
        checks.append(CompatibilityCheck(
            "Background shape", "fail", background_grid_error or "Background trace shape is invalid."
        ))
    else:
        checks.append(CompatibilityCheck("Background shape", "ok", "one complex Frequency trace"))

    same_count = (
        target_trace is not None and background_trace is not None
        and target_trace.n_points == background_trace.n_points
        and target_grid.size == background_grid.size
    )
    count_detail = (
        f"{target_grid.size} points" if same_count else
        f"Target {target_grid.size if target_trace else '—'}; Background {background_grid.size if background_trace else '—'} points"
    )
    checks.append(CompatibilityCheck("Frequency points", "ok" if same_count else "fail", count_detail))

    tolerance = None
    same_units = (
        target_trace is not None and background_trace is not None
        and (target_trace.x_unit or "").strip().casefold() == (background_trace.x_unit or "").strip().casefold()
    )
    grid_status = False
    grid_detail = "Frequency grid is unavailable."
    if same_count and target_grid.size and same_units:
        tolerance = _grid_tolerance(target_grid, background_grid)
        target_direction = _monotonic_direction(target_grid)
        background_direction = _monotonic_direction(background_grid)
        if target_direction is None or background_direction is None:
            grid_detail = "Frequency values must be strictly monotonic and ordered."
        elif target_direction != background_direction:
            grid_detail = "Frequency ordering differs (ascending versus descending)."
        else:
            delta = float(np.max(np.abs(target_grid - background_grid)))
            grid_status = delta <= tolerance
            grid_detail = (
                f"Matched in order (max Δ {delta:.6g}; tolerance {tolerance:.6g} {target_trace.x_unit or ''})."
                if grid_status else
                f"Mismatch: max Δ {delta:.6g} {target_trace.x_unit or ''}; tolerance {tolerance:.6g}."
            )
    elif same_count and not same_units:
        grid_detail = f"Frequency units differ: {target_trace.x_unit!r} vs {background_trace.x_unit!r}."
    checks.append(CompatibilityCheck("Frequency grid", "ok" if grid_status else "fail", grid_detail))

    if target_trace is not None and background_trace is not None and target_grid.size and background_grid.size:
        ranges_match = bool(
            tolerance is not None
            and abs(float(target_grid[0] - background_grid[0])) <= tolerance
            and abs(float(target_grid[-1] - background_grid[-1])) <= tolerance
            and same_units
        )
        range_detail = (
            f"Target {_format_range(target_grid, target_trace.x_unit)}; Background {_format_range(background_grid, background_trace.x_unit)}"
        )
        checks.append(CompatibilityCheck("Frequency range", "ok" if ranges_match else "fail", range_detail))
    else:
        checks.append(CompatibilityCheck("Frequency range", "fail", "Frequency range is unavailable."))

    denominator_safe = False
    denominator_detail = "Not checked because the selected Background trace is unavailable."
    if has_background and background_trace is not None and not background_grid_error:
        try:
            bg_values = np.asarray(background.get_data(selected_channel, transform="raw"), dtype=np.complex128)
            if bg_values.shape != (background_trace.n_points, 1):
                denominator_detail = f"Unsupported Background data shape {bg_values.shape}."
            elif not np.all(np.isfinite(bg_values)):
                denominator_detail = "NaN or Inf values detected in Background."
            else:
                magnitudes = np.abs(bg_values[:, 0])
                peak = float(np.max(magnitudes))
                minimum = float(np.min(magnitudes))
                floor = max(1.0, peak) * _DENOMINATOR_RELATIVE_FLOOR
                denominator_safe = minimum > floor
                denominator_detail = (
                    f"Valid; min |BG| {minimum:.6g}." if denominator_safe else
                    f"Near-zero denominator: min |BG| {minimum:.6g} ≤ numerical floor {floor:.6g}."
                )
        except Exception as error:
            denominator_detail = f"Could not read Background trace: {error}"
    checks.append(CompatibilityCheck(
        "Denominator", "ok" if denominator_safe else "warning", denominator_detail
    ))

    can_generate = all(check.status == "ok" for check in checks)
    return DeBackgroundInspection(
        str(target_path), str(background_path), options, selected_channel,
        tuple(checks), can_generate, tolerance,
    )


def inspect_debackground(
    target_path: str | Path,
    background_path: str | Path,
    selected_channel: str | None = None,
) -> DeBackgroundInspection:
    """Inspect two Labber logs and report why processing is or is not safe."""
    target_path = Path(target_path).expanduser().resolve()
    background_path = Path(background_path).expanduser().resolve()
    if target_path == background_path:
        raise DeBackgroundError("Target and Background must be different files.")
    target = background = None
    try:
        target = _load_experiment(target_path, "Target")
        background = _load_experiment(background_path, "Background")
        return _inspect_loaded(target, background, target_path, background_path, selected_channel)
    except DeBackgroundError:
        raise
    except Exception as error:
        raise DeBackgroundError(f"Could not inspect the selected Labber data: {error}") from error
    finally:
        if target is not None:
            target.close()
        if background is not None:
            background.close()


def _check_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise DeBackgroundCancelled("De-background was cancelled; temporary output was removed.")


def _progress(callback: ProgressCallback | None, value: int, message: str) -> None:
    if callback is not None:
        callback(value, message)


def process_debackground(
    target_path: str | Path,
    background_path: str | Path,
    selected_channel: str,
    output_path: str | Path | None = None,
    *,
    overwrite: bool = False,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
    entry_chunk_size: int = 32,
) -> Path:
    """Create an atomically published, legacy-compatible ``*_debg.hdf5``."""
    target_path = Path(target_path).expanduser().resolve()
    background_path = Path(background_path).expanduser().resolve()
    if target_path == background_path:
        raise DeBackgroundError("Target and Background must be different files.")
    if output_path is None:
        output_path = target_path.with_name(f"{target_path.stem}_debg.hdf5")
    output = Path(output_path).expanduser()
    if not output.suffix:
        output = output.with_suffix(".hdf5")
    if output.suffix.casefold() not in {".h5", ".hdf5"}:
        raise DeBackgroundError("Output must use the .h5 or .hdf5 extension.")
    output = output.resolve()
    if output in {target_path, background_path}:
        raise DeBackgroundError("Output path cannot replace the Target or Background source file.")
    if not output.parent.is_dir():
        raise DeBackgroundError(f"Output folder does not exist: {output.parent}")
    if output.exists() and not overwrite:
        raise DeBackgroundOutputExists(f"Output already exists: {output}")

    target = background = None
    temporary: Path | None = None
    try:
        _check_cancelled(cancel_event)
        _progress(progress, 0, "Validating Target and Background…")
        target = _load_experiment(target_path, "Target")
        background = _load_experiment(background_path, "Background")
        inspection = _inspect_loaded(
            target, background, target_path, background_path, selected_channel,
        )
        if not inspection.can_generate:
            failures = [
                f"{check.label}: {check.detail}"
                for check in inspection.checks if check.status != "ok"
            ]
            raise DeBackgroundCompatibilityError("\n".join(failures))
        _check_cancelled(cancel_event)

        target_trace = target.vector_traces[selected_channel]
        background_trace = background.vector_traces[selected_channel]
        background_data = np.asarray(background.get_data(selected_channel, "raw"), dtype=np.complex128)
        output_dtype = target._reader.get_dataset(target_trace.trace_path).dtype
        if output_dtype.kind != "f":
            raise DeBackgroundCompatibilityError(
                f"Unsupported Labber complex trace storage dtype: {output_dtype}."
            )
        expected_hdf5_shape = (target_trace.n_points, 2, target_trace.n_entries)

        _progress(progress, 0, "Copying Target HDF5…")
        temporary = copy_to_temporary(
            target_path,
            output.parent,
            output.name,
            progress=lambda p: _progress(progress, p, "Copying Target HDF5…"),
            cancelled=lambda: bool(cancel_event and cancel_event.is_set()),
        )

        start = 0
        with HDF5ComplexTraceWriter(
            temporary, target_trace.trace_path, expected_hdf5_shape,
        ) as writer:
            while start < target_trace.n_entries:
                _check_cancelled(cancel_event)
                stop = min(start + max(1, entry_chunk_size), target_trace.n_entries)
                target_chunk = np.asarray(
                    target.get_data(selected_channel, "raw", entry_slice=slice(start, stop)),
                    dtype=np.complex128,
                )
                divided = divide_complex_trace(target_chunk, background_data)
                real, imag = _legacy_components(divided, output_dtype)
                writer.write_entries(start, stop, real, imag)
                start = stop
                progress_value = 25 + int(start * 65 / max(target_trace.n_entries, 1))
                _progress(progress, min(progress_value, 90), "Dividing complex S-parameter traces…")
            writer.flush()

        target.close()
        target = None
        background.close()
        background = None
        _check_cancelled(cancel_event)

        _progress(progress, 92, "Verifying Labber output…")
        verified = _load_experiment(temporary, "Generated output")
        try:
            result_trace = verified.vector_traces.get(selected_channel)
            if result_trace is None or not result_trace.complex:
                raise DeBackgroundError("Generated HDF5 did not reopen as the expected complex Labber trace.")
            if result_trace.n_points != target_trace.n_points or result_trace.n_entries != target_trace.n_entries:
                raise DeBackgroundError("Generated HDF5 trace dimensions changed during processing.")
            result_values = verified.get_data(selected_channel, "raw")
            if result_values.shape != (target_trace.n_points, target_trace.n_entries):
                raise DeBackgroundError("Generated HDF5 trace shape failed parser verification.")
            if not np.all(np.isfinite(result_values)):
                raise DeBackgroundError("Generated HDF5 contains non-finite output values.")
        finally:
            verified.close()

        _check_cancelled(cancel_event)
        _progress(progress, 98, "Publishing completed HDF5…")
        try:
            publish_temporary(temporary, output, overwrite=overwrite)
        except FileExistsError as error:
            raise DeBackgroundOutputExists(f"Output already exists: {output}") from error
        except HDF5ProcessingWriteError as error:
            raise DeBackgroundError(str(error)) from error
        temporary = None
        _progress(progress, 100, "De-background completed.")
        return output
    except DeBackgroundError:
        raise
    except InterruptedError as error:
        raise DeBackgroundCancelled(str(error)) from error
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        if isinstance(error, HDF5ProcessingWriteError):
            raise DeBackgroundError(str(error)) from error
        raise DeBackgroundError(f"De-background failed: {error}") from error
    finally:
        if target is not None:
            target.close()
        if background is not None:
            background.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
