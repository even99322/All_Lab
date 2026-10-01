"""Scientific data export primitives shared by the Viewer and tests.

The GUI prepares arrays using its existing ChannelManager/CachedExperiment
path; this module deliberately knows nothing about Qt or HDF5.  Keeping the
writer here makes the scientific export contract explicit and prevents a
second transform or data-loading path from growing inside the GUI.
"""

from __future__ import annotations

import csv
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np


RAW = "raw"
CURRENT_TRANSFORM = "current_transform"
DISPLAYED = "displayed"

ACTIVE_TRACE = "active"
SELECTED_TRACES = "selected"
VISIBLE_TRACES = "visible"
FULL_DATA = "full"

FULL_RANGE = "full"
VISIBLE_X_RANGE = "visible_x"


@dataclass(frozen=True)
class ExportColumn:
    """One tabular output column, with a stable data and display name."""

    key: str
    label: str
    values: np.ndarray


@dataclass
class ExportDataset:
    """Numerical payload prepared from one Viewer pane.

    ``columns`` is the unambiguous long/tidy CSV representation.  ``arrays``
    retains the structured representation used by NPZ.  Metadata is always
    JSON-compatible and is intentionally separate from both values and GUI
    object state.
    """

    columns: list[ExportColumn]
    arrays: dict[str, np.ndarray]
    metadata: dict[str, object] = field(default_factory=dict)

    def filtered_x_range(self, low: float, high: float) -> "ExportDataset":
        """Keep rows whose named X-coordinate is inside an inclusive range."""
        if not self.columns:
            return self
        x_key = str(self.metadata.get("x_column_key", ""))
        x_column = next((column for column in self.columns if column.key == x_key), None)
        if x_column is None:
            return self
        values = np.asarray(x_column.values)
        try:
            mask = np.isfinite(values.astype(float)) & (values >= low) & (values <= high)
        except (TypeError, ValueError):
            return self
        columns = [
            ExportColumn(column.key, column.label, np.asarray(column.values)[mask])
            for column in self.columns
        ]
        arrays = dict(self.arrays)
        metadata = dict(self.metadata)
        metadata["range"] = {
            "kind": VISIBLE_X_RANGE,
            "minimum": float(low),
            "maximum": float(high),
            "selection": "existing_samples_inclusive",
        }
        return ExportDataset(columns, arrays, metadata)


def safe_key(value: str, *, fallback: str = "value") -> str:
    """Return a portable NPZ/CSV key without losing the original label."""
    text = re.sub(r"[^0-9A-Za-z_]+", "_", str(value).strip())
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        text = fallback
    if text[0].isdigit():
        text = f"value_{text}"
    return text


def safe_filename(value: str, *, fallback: str = "LabLogViewer_export") -> str:
    """Preserve meaningful display text while removing only filesystem hazards."""
    text = re.sub(r'[\\/:*?"<>|]+', "_", str(value).strip())
    text = re.sub(r"\s+", "_", text)
    text = text.strip("._ ")
    return text[:120] or fallback


def _serialise_cell(value):
    if isinstance(value, np.generic):
        return value.item()
    return value


def write_csv(path: str | Path, dataset: ExportDataset) -> None:
    """Write UTF-8 CSV with concise, comment-prefixed JSON provenance.

    Comment lines retain simple interoperability: standard CSV readers can
    skip them with their existing comment option, while the actual header and
    data remain a conventional table.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    metadata = json.dumps(dataset.metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    row_count = max((len(np.asarray(column.values)) for column in dataset.columns), default=0)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=".tmp", dir=destination.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write("# LabLogViewer scientific data export\n")
            handle.write(f"# metadata: {metadata}\n")
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow([column.label for column in dataset.columns])
            for row in range(row_count):
                writer.writerow([
                    _serialise_cell(np.asarray(column.values)[row]) if row < len(column.values) else ""
                    for column in dataset.columns
                ])
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def write_npz(path: str | Path, dataset: ExportDataset) -> None:
    """Write structured arrays and JSON metadata without pickled objects."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {safe_key(key): np.asarray(value) for key, value in dataset.arrays.items()}
    payload["metadata_json"] = np.asarray(
        json.dumps(dataset.metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=".npz", dir=destination.parent,
    )
    os.close(descriptor)
    try:
        np.savez_compressed(temporary_name, **payload)
        os.replace(temporary_name, destination)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def complex_columns(name: str, values: np.ndarray) -> list[ExportColumn]:
    """Represent complex CSV values explicitly as real and imaginary columns."""
    key = safe_key(name)
    data = np.asarray(values)
    if np.iscomplexobj(data):
        return [
            ExportColumn(f"{key}_real", f"{name}_Real", np.real(data)),
            ExportColumn(f"{key}_imag", f"{name}_Imag", np.imag(data)),
        ]
    return [ExportColumn(key, name, data)]


def long_grid_dataset(*, x_values: np.ndarray, y_values: np.ndarray, z_values: np.ndarray,
                      x_name: str, x_unit: str | None, y_name: str, y_unit: str | None,
                      z_name: str, z_unit: str | None, metadata: dict[str, object]) -> ExportDataset:
    """Build a tidy 2D representation with ``z.shape == (len(y), len(x))``.

    No transpose is performed here.  The caller supplies the canonical
    Grid2DData orientation, so a Flux surface keeps the same scientific axis
    semantics in the Viewer, CSV and NPZ.
    """
    x = np.asarray(x_values).reshape(-1)
    y = np.asarray(y_values).reshape(-1)
    z = np.asarray(z_values)
    if z.shape != (y.size, x.size):
        raise ValueError("2D export requires z shape (len(y), len(x)).")
    xx, yy = np.meshgrid(x, y, indexing="xy")
    x_label = f"{x_name}_{x_unit}" if x_unit else x_name
    y_label = f"{y_name}_{y_unit}" if y_unit else y_name
    z_label = f"{z_name}_{z_unit}" if z_unit else z_name
    columns = [
        ExportColumn(safe_key(x_name), x_label, xx.reshape(-1)),
        ExportColumn(safe_key(y_name), y_label, yy.reshape(-1)),
        *complex_columns(z_label, z.reshape(-1)),
    ]
    metadata = dict(metadata)
    metadata.update({
        "mode": "2d",
        "x_axis": {"name": x_name, "unit": x_unit},
        "y_axis": {"name": y_name, "unit": y_unit},
        "value": {"name": z_name, "unit": z_unit},
        "x_column_key": safe_key(x_name),
        "array_orientation": "z[y_index, x_index]",
    })
    arrays = {
        "x_values": x,
        "y_values": y,
        "z_values": z,
    }
    return ExportDataset(columns, arrays, metadata)


def nd_dataset(*, dimensions: Iterable[object], values: np.ndarray, value_name: str,
               value_unit: str | None, metadata: dict[str, object]) -> ExportDataset:
    """Represent full N-D data as named axes plus one tidy row per sample."""
    dims = list(dimensions)
    data = np.asarray(values)
    shapes = tuple(len(np.asarray(getattr(dimension, "values"))) for dimension in dims)
    if data.shape != shapes:
        raise ValueError("N-D export values do not match the supplied dimensions.")
    meshes = np.meshgrid(
        *(np.asarray(getattr(dimension, "values")) for dimension in dims), indexing="ij"
    )
    columns: list[ExportColumn] = []
    axes_metadata = []
    arrays: dict[str, np.ndarray] = {"values": data}
    for dimension, mesh in zip(dims, meshes):
        name = str(getattr(dimension, "name"))
        unit = getattr(dimension, "unit", None)
        key = safe_key(name)
        columns.append(ExportColumn(key, f"{name}_{unit}" if unit else name, mesh.reshape(-1)))
        arrays[f"axis_{key}"] = np.asarray(getattr(dimension, "values"))
        axes_metadata.append({"key": f"axis_{key}", "name": name, "unit": unit})
    columns.extend(complex_columns(f"{value_name}_{value_unit}" if value_unit else value_name, data.reshape(-1)))
    metadata = dict(metadata)
    metadata.update({
        "mode": "nd",
        "dimensions": axes_metadata,
        "value": {"name": value_name, "unit": value_unit},
        "array_orientation": "values[dimension_0, ...]",
        "x_column_key": safe_key(str(getattr(dims[0], "name"))) if dims else "",
    })
    return ExportDataset(columns, arrays, metadata)
