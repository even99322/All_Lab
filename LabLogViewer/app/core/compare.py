"""Pure-numpy helpers for comparing two 1D traces (same file or two files).

Nothing here touches a file: callers pass arrays in and get arrays back, so the
original measurement data is never modified."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


class CompareError(Exception):
    """A validation result (not a bug): the two traces cannot be compared this way."""


@dataclass
class Trace:
    x: np.ndarray
    y: np.ndarray
    x_label: str = ""
    x_unit: str | None = None
    y_label: str = ""
    y_unit: str | None = None
    name: str = ""


@dataclass
class Aligned:
    x: np.ndarray
    a: np.ndarray
    b: np.ndarray
    x_is_index: bool = False
    notes: list[str] = field(default_factory=list)


X_MODES = ("overlap", "index", "fixed_step")
Y_MODES = ("none", "minmax", "zscore", "max")
OPERATIONS = ("a", "b", "difference", "ratio")


def _clean(trace: Trace) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(trace.x, dtype=float).reshape(-1)
    y = np.asarray(trace.y, dtype=float).reshape(-1)
    if x.size != y.size:
        raise CompareError("A trace has different X and Y lengths.")
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if x.size < 2:
        raise CompareError("A trace has fewer than two valid points.")
    order = np.argsort(x, kind="stable")
    x, y = x[order], y[order]
    keep = np.concatenate(([True], np.diff(x) > 0))
    return x[keep], y[keep]


def compatibility_notes(a: Trace, b: Trace) -> list[str]:
    """Plain-language warnings; an empty list means nothing suspicious."""
    notes: list[str] = []
    if (a.x_unit or "") != (b.x_unit or ""):
        notes.append(f"X units differ ({a.x_unit or '-'} vs {b.x_unit or '-'}).")
    if (a.y_unit or "") != (b.y_unit or ""):
        notes.append(f"Y units differ ({a.y_unit or '-'} vs {b.y_unit or '-'}).")
    return notes


def normalise(y: np.ndarray, mode: str) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    if mode == "none":
        return y
    if mode == "minmax":
        low, high = np.nanmin(y), np.nanmax(y)
        return (y - low) / (high - low) if high > low else np.zeros_like(y)
    if mode == "zscore":
        std = np.nanstd(y)
        return (y - np.nanmean(y)) / std if std > 0 else np.zeros_like(y)
    if mode == "max":
        peak = np.nanmax(np.abs(y))
        return y / peak if peak > 0 else np.zeros_like(y)
    raise CompareError(f"Unknown normalisation '{mode}'.")


def align(a: Trace, b: Trace, x_mode: str = "overlap", step: float | None = None,
          points: int | None = None) -> Aligned:
    """Put both traces on one X grid.

    overlap     - interpolate both onto the A grid restricted to the shared X range.
    index       - ignore X values entirely: both are stretched onto 0..1 by position,
                  so only the shape is compared.
    fixed_step  - a regular grid with spacing ``step`` over the shared X range.
    """
    ax, ay = _clean(a)
    bx, by = _clean(b)
    notes = compatibility_notes(a, b)
    if x_mode == "index":
        n = int(points or max(ax.size, bx.size))
        grid = np.linspace(0.0, 1.0, n)
        ia = np.interp(grid, np.linspace(0.0, 1.0, ax.size), ay)
        ib = np.interp(grid, np.linspace(0.0, 1.0, bx.size), by)
        notes.append("X values ignored: both traces were stretched to the same length.")
        return Aligned(grid, ia, ib, x_is_index=True, notes=notes)
    low, high = max(ax[0], bx[0]), min(ax[-1], bx[-1])
    if not high > low:
        raise CompareError("The two traces have no X range in common. "
                           "Use 'Ignore X values' to compare their shapes only.")
    if x_mode == "overlap":
        grid = ax[(ax >= low) & (ax <= high)]
        if grid.size < 2:
            grid = np.linspace(low, high, 2)
    elif x_mode == "fixed_step":
        if step is None or not step > 0:
            raise CompareError("The step must be greater than zero.")
        count = int(np.floor((high - low) / step + 1e-9)) + 1
        if count < 2:
            raise CompareError("The step is larger than the shared X range.")
        if count > 2_000_000:
            raise CompareError("The step is too small: it would create more than 2 million points.")
        grid = low + step * np.arange(count)
    else:
        raise CompareError(f"Unknown X mode '{x_mode}'.")
    if (ax.size and bx.size) and (ax[0] != bx[0] or ax[-1] != bx[-1]):
        notes.append("X ranges differ: only the shared range is shown.")
    return Aligned(grid, np.interp(grid, ax, ay), np.interp(grid, bx, by), notes=notes)


def combine(aligned: Aligned, operation: str, y_mode: str = "none") -> np.ndarray:
    """Y of the result for 'a', 'b', 'difference' (A-B) or 'ratio' (A/B)."""
    ya, yb = normalise(aligned.a, y_mode), normalise(aligned.b, y_mode)
    if operation == "a":
        return ya
    if operation == "b":
        return yb
    if operation == "difference":
        return ya - yb
    if operation == "ratio":
        with np.errstate(divide="ignore", invalid="ignore"):
            out = ya / yb
        out[~np.isfinite(out)] = np.nan
        return out
    raise CompareError(f"Unknown operation '{operation}'.")


def shared_grid_2d(ax, ay, bx, by) -> tuple[np.ndarray, np.ndarray]:
    """Shared X/Y axes of two 2D maps (for A-B maps); raises when they do not overlap."""
    def span(p, q):
        low, high = max(p.min(), q.min()), min(p.max(), q.max())
        if not high > low:
            raise CompareError("The two maps have no range in common.")
        return low, high
    xl, xh = span(np.asarray(ax, float), np.asarray(bx, float))
    yl, yh = span(np.asarray(ay, float), np.asarray(by, float))
    nx = max(2, int(np.sum((ax >= xl) & (ax <= xh))))
    ny = max(2, int(np.sum((ay >= yl) & (ay <= yh))))
    return np.linspace(xl, xh, nx), np.linspace(yl, yh, ny)


def resample_2d(x, y, z, new_x, new_y) -> np.ndarray:
    """Bilinear resample of z[y, x] onto (new_y, new_x)."""
    x, y, z = np.asarray(x, float), np.asarray(y, float), np.asarray(z, float)
    tmp = np.array([np.interp(new_x, x, row) for row in z])
    return np.array([np.interp(new_y, y, tmp[:, j]) for j in range(tmp.shape[1])]).T
