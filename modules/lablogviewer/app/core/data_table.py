"""
app/core/data_table.py — Phase 9

Converts already-computed 1D-shaped results (Slice1DData / LineCutData
/ plain x,y arrays) into simple tabular rows for the GUI's Data Table
view and CSV export (Phase 10). Pure formatting - no HDF5 access, no
new data fetching; everything here operates on objects the caller
already obtained via CachedExperiment/ChannelManager.

2D data intentionally does NOT get a row-per-point table here (an
855x501 real sample would be 428,755 rows - impractical for a GUI
table and not what "看 Data Table" is meant for). 2D data gets a
matrix-shaped CSV export instead (Phase 10, app/core/export.py) - see
build_2d_matrix() below, which the Data Table view doesn't use but
Export does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

DEFAULT_MAX_ROWS = 5000


@dataclass
class DataTableRow:
    index: int
    x: float
    y: Any  # float, or complex when the source transform is "raw"


@dataclass
class DataTable:
    rows: list[DataTableRow]
    x_name: str
    x_unit: str | None
    y_name: str
    y_unit: str | None
    transform: str
    truncated: bool
    total_rows: int


def build_1d_table(x_values: np.ndarray, y_values: np.ndarray, *,
                    x_name: str, x_unit: str | None,
                    y_name: str, y_unit: str | None, transform: str,
                    max_rows: int | None = DEFAULT_MAX_ROWS) -> DataTable:
    """Builds a simple Index | X | Y table from any 1D-shaped result
    (a 1D Plot trace, an N-D Slice Explorer 1D slice, or a Line Cut).
    Truncates to `max_rows` (None = no limit) to keep the GUI table
    responsive on very long traces - `truncated`/`total_rows` let the
    caller show '(showing first 5000 of 12000 rows)'."""
    x_values = np.asarray(x_values)
    y_values = np.asarray(y_values)
    n_total = len(x_values)
    n = n_total if max_rows is None else min(n_total, max_rows)
    truncated = n < n_total

    rows = [
        DataTableRow(index=i, x=float(x_values[i]), y=_to_plain(y_values[i]))
        for i in range(n)
    ]
    return DataTable(
        rows=rows, x_name=x_name, x_unit=x_unit, y_name=y_name, y_unit=y_unit,
        transform=transform, truncated=truncated, total_rows=n_total,
    )


def _to_plain(value: Any) -> Any:
    if isinstance(value, (np.complexfloating, complex)):
        return complex(value)
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    return value
