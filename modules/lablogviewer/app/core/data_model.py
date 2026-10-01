"""
app/core/data_model.py — Phase 3

The unified internal representation of a parsed Labber log, per
docs/parser_architecture.md. The GUI and plotting/analysis layers
must only ever touch objects defined here — never an h5py.File or a
raw h5py.Dataset. The only exception is `Experiment._reader`, which is
kept open for lazy loading and accessed exclusively through
`Experiment.get_data()` / `get_step_values()`.

Two real Labber layouts are unified here (see
docs/hdf5_structure_report.md):
  - scalar channels living in /Data/Data
  - vector/complex "trace" channels living in /Traces/<name>, whose
    own x-axis is reconstructed from a stored (t0, dt) pair rather
    than stored as an explicit array.

Nothing in this module loads a full vector trace dataset into memory
implicitly - `get_data()` always slices lazily through the
HDF5Reader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.core.data_identity import stable_data_identity
from app.core.hdf5_reader import HDF5Reader


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

class UnsupportedLabberFormat(Exception):
    """Raised by a parser's `parse()` when it can technically open the
    file but the internal structure doesn't match what it expects
    closely enough to safely proceed. The GUI layer catches this and
    falls back to Raw HDF5 Explorer Mode (spec §22)."""


class ChannelNotFound(KeyError):
    pass


class Data2DError(Exception):
    """Raised by Experiment.get_2d_data() when the requested X/Y/Z
    channel combination cannot be represented as a regular 2D sweep
    surface (spec §12, Phase 6). The GUI catches this and shows a
    clear message without crashing or discarding the current plot —
    it is a validation result, not a bug."""


class SliceError(Exception):
    """Raised by the generic N-D slicing API (Phase 7):
    Experiment.get_nd_slice() / get_full_nd_array() / list_dimensions().
    Same "validation result, not a bug" contract as Data2DError — the
    GUI shows a warning and keeps the existing plot untouched."""


# --------------------------------------------------------------------------
# Channel metadata
# --------------------------------------------------------------------------

@dataclass
class ChannelInfo:
    """Metadata for one channel (step or log, scalar or vector)."""
    name: str
    instrument: str | None = None
    unit: str | None = None
    is_step: bool = False
    is_log: bool = False
    is_vector: bool = False          # True for trace/complex channels
    is_complex: bool = False
    shape: tuple[int, ...] = field(default_factory=tuple)
    n_points: int | None = None      # points along the channel's own axis
                                       # (e.g. 501 for a VNA frequency trace);
                                       # None for scalar channels.
    is_relation_based: bool = False  # v0.9B: True when this STEP channel's
                                       # swept values are computed from an
                                       # "equation" referencing other channels
                                       # (Labber's "use_relations" mechanism —
                                       # the concrete form its "Function"
                                       # dimensions take) rather than being a
                                       # directly-configured range. The
                                       # RECORDED values in Data/Data are
                                       # still the evaluated results, so no
                                       # special handling is needed to read
                                       # them correctly — this flag exists so
                                       # the UI can label such a dimension
                                       # clearly (e.g. "Flux (derived)")
                                       # rather than implying it's a directly
                                       # hardware-configured sweep.
    equation: str | None = None      # the relation formula itself, if any —
                                       # informational only (not evaluated by
                                       # this application; Labber already
                                       # evaluated and stored the results).


@dataclass
class StepAxis:
    """One ACTIVE step (sweep) axis, in sweep order. Only axes with
    Step dimensions > 1 are included — fixed/constant step channels
    are still recorded in Experiment.channels but do not get a
    StepAxis, since they contribute no dimensionality to plotting."""
    channel: ChannelInfo
    values: np.ndarray       # 1D, length == this axis's n_points
    dim_index: int           # index into the raw Step dimensions array,
                               # kept for traceability back to the source file


@dataclass
class VectorTraceInfo:
    """Describes a vector/trace-valued log channel's own axis
    (e.g. VNA S21's frequency axis), reconstructed from the file's
    stored t0/dt convention rather than read as an explicit array."""
    channel: ChannelInfo
    x_name: str | None
    x_unit: str | None
    x_values: np.ndarray          # reconstructed once at parse time (small: N points)
    n_points: int
    n_entries: int                 # length of the sweep-index axis
    trace_path: str                # HDF5 path to the raw dataset, for lazy reads
    complex: bool


@dataclass
class Grid2DData:
    """Result of Experiment.get_2d_data() / get_nd_slice() — a regular
    2D surface ready for a heatmap widget to render, with no knowledge
    of HDF5 internal layout (real/imag axis, dataset shape order, etc.)
    leaking through.

    z_values has shape (len(y_values), len(x_values)) — row = Y,
    column = X — matching the numpy/image convention so
    z_values[iy, ix] is the value at (x_values[ix], y_values[iy]).
    """
    x_values: np.ndarray
    y_values: np.ndarray
    z_values: np.ndarray
    x_name: str
    x_unit: str | None
    y_name: str
    y_unit: str | None
    z_name: str
    z_unit: str | None
    transform: str
    fixed_dims: dict = field(default_factory=dict)
    acquisition: "AcquisitionStatus | None" = None
    # ^ Phase 7 addition: {dim_name: {"index": i, "value": v, "unit": u}}
    # for every dimension NOT shown as X/Y — empty for plain Phase 6
    # get_2d_data() results (which have no "other" dimensions to fix),
    # populated for get_nd_slice() results with 3+ total dimensions.
    # Added with a default so existing Grid2DData construction sites
    # (Phase 6, get_2d_data) remain valid unchanged.

    @property
    def nbytes(self) -> int:
        return int(self.x_values.nbytes + self.y_values.nbytes + self.z_values.nbytes)


@dataclass
class Slice1DData:
    """Phase 7: result of Experiment.get_nd_slice() when no Y
    dimension is requested — a 1D trace with one varying dimension and
    zero or more OTHER dimensions fixed at a specific index/value,
    recorded in `fixed_dims` so the GUI can show what's being held
    constant (e.g. 'Current = 12.7 mA')."""
    x_values: np.ndarray
    y_values: np.ndarray
    x_name: str
    x_unit: str | None
    z_name: str
    z_unit: str | None
    transform: str
    fixed_dims: dict = field(default_factory=dict)

    @property
    def nbytes(self) -> int:
        return int(self.x_values.nbytes + self.y_values.nbytes)


@dataclass
class Dimension:
    """Phase 7: a generic, named axis of a channel's data — either a
    vector/trace channel's own reconstructed axis (e.g. Frequency) or
    an active step (sweep) axis (e.g. Current, Field, Power,
    Temperature, ...). This is the abstraction the N-D Slice Explorer
    is built on: nothing about it assumes any particular physical
    quantity, count, or ordering.
    """
    name: str
    unit: str | None
    size: int
    values: np.ndarray       # 1D, length == size — ALWAYS the real
                               # coordinate values, never assumed from
                               # index alone (spec §1/§8)
    is_uniform: bool
    step: float | None       # only meaningful when is_uniform is True

    @property
    def min(self) -> float:
        return float(np.min(self.values)) if self.size else 0.0

    @property
    def max(self) -> float:
        return float(np.max(self.values)) if self.size else 0.0

    def nearest_index(self, value: float) -> int:
        """Index of the coordinate value closest to `value` — the
        correct way to map a physical value back to an index for BOTH
        uniform and non-uniform coordinate arrays (spec §8)."""
        return int(np.argmin(np.abs(self.values - value)))

    def value_at(self, index: int) -> float:
        return float(self.values[index])


@dataclass(frozen=True)
class AcquisitionStatus:
    """Whether a channel's recorded sweeps match its nominal step plan."""
    channel_name: str
    nominal_entries: int
    acquired_entries: int
    state: str  # "complete" | "partial" | "ambiguous"
    reason: str

    @property
    def is_partial(self) -> bool:
        return self.state == "partial"

    @property
    def is_recoverable(self) -> bool:
        return self.state in {"complete", "partial"}


def extract_scalar_channel_row(full: np.ndarray, row_idx: int, n_channels: int) -> np.ndarray:
    """v0.9C: extracts one channel's full per-sweep-entry row from the
    `/Data/Data` scalar matrix, regardless of which of its 3 axes is
    the "channel" axis vs the "entries" axis vs the leftover singleton
    axis.

    This generalizes a bug found via real-file diagnosis: two real
    Labber files store this matrix in DIFFERENT axis orders —
    `0828_RSMEP_1.hdf5` uses (1, n_channels, n_entries), while
    `0828 X1 Flux-dep_debg.hdf5` uses (n_entries, n_channels, 1). The
    previous code assumed the first ordering unconditionally
    (`full[0, row_idx, :]`), which silently produced a length-1
    "sweep axis" for the Flux file's actively-swept "DC supply - 1 -
    Current" channel instead of its real 681 values — breaking 2D
    Heatmap, the Log Entries table, and axis-domain validation for
    that file specifically, with no error raised (a silent
    correctness bug, not a crash).

    Fixed generically, not by special-casing either filename: locates
    the "channel" axis by matching its size against `n_channels`
    (the one dimension whose length we independently know must be
    correct), treats the axis with more than one point as "entries",
    and the remaining axis as the leftover singleton.
    """
    shape = full.shape
    if full.ndim != 3:
        raise ValueError(f"Expected a 3D scalar data matrix, got shape {shape}")

    channel_axes = [i for i, s in enumerate(shape) if s == n_channels]
    if not channel_axes:
        raise ValueError(
            f"No axis of scalar data matrix shape {shape} matches "
            f"n_channels={n_channels} — cannot determine its layout."
        )
    channel_axis = channel_axes[0]

    other_axes = [i for i in range(3) if i != channel_axis]
    if shape[other_axes[0]] >= shape[other_axes[1]]:
        entries_axis, singleton_axis = other_axes[0], other_axes[1]
    else:
        entries_axis, singleton_axis = other_axes[1], other_axes[0]

    index: list[Any] = [slice(None)] * 3
    index[channel_axis] = row_idx
    index[singleton_axis] = 0
    row = full[tuple(index)]
    return np.asarray(row).reshape(-1)


def _make_dimension(name: str, unit: str | None, values: np.ndarray) -> Dimension:
    """Builds a Dimension from a name/unit/coordinate-array, detecting
    whether the coordinates are uniformly spaced (spec §8) rather than
    assuming it. Tolerant of a single-point or empty axis."""
    values = np.asarray(values, dtype=float)
    size = len(values)
    if size >= 2:
        diffs = np.diff(values)
        is_uniform = bool(np.allclose(diffs, diffs[0], rtol=1e-6, atol=1e-9))
        step = float(diffs[0]) if is_uniform else None
    else:
        is_uniform = True
        step = None
    return Dimension(name=name, unit=unit, size=size, values=values, is_uniform=is_uniform, step=step)


@dataclass
class LineCutData:
    """Phase 8: a 1D trace extracted from an ALREADY-COMPUTED
    Grid2DData (a 2D Heatmap or the 2D sub-mode of the N-D Slice
    Explorer) by fixing one of its two displayed axes at the nearest
    sample to a requested physical coordinate.

    Deliberately built from a Grid2DData rather than going back to
    Experiment/HDF5Reader: the grid already IS "the current N-D
    slice" — any OTHER dimensions the user fixed via the Slice
    Explorer are already baked into grid.z_values and grid.fixed_dims
    (spec's "Line Cut 必須建立在 CURRENT N-D SLICE 之上" requirement).
    Extracting a line cut is then just an in-memory numpy row/column
    read — no HDF5 access, no re-slicing, and the exact same transform
    already applied to the heatmap (spec's "所有 plotting modes 必須一致"
    requirement is satisfied by construction, not by re-checking).
    """
    x_values: np.ndarray
    y_values: np.ndarray
    x_name: str
    x_unit: str | None
    z_name: str
    z_unit: str | None
    transform: str
    cut_axis: str             # "x" (vary along the heatmap's X axis) or
                                # "y" (vary along the heatmap's Y axis)
    fixed_dims: dict           # the grid's original fixed_dims PLUS the
                                # newly-fixed heatmap axis
    requested_value: float     # what the user actually pointed at
    nearest_value: float       # the real sample coordinate that was used
    nearest_index: int

    @property
    def nbytes(self) -> int:
        return int(self.x_values.nbytes + self.y_values.nbytes)


def extract_line_cut(grid: "Grid2DData", cut_axis: str, requested_value: float) -> LineCutData:
    """Phase 8 data-layer API: X Cut / Y Cut extraction from a
    Grid2DData, per spec's 'get_line_cut(axis=..., fixed_coordinates=...)'
    requirement.

    `cut_axis="x"` = an "X Cut" (per spec): fixes the grid's Y
    coordinate nearest `requested_value` and varies X — e.g. with
    X=Frequency, Y=Field, this yields "S21 vs Frequency @ Field=...".

    `cut_axis="y"` = a "Y Cut": fixes X nearest `requested_value` and
    varies Y.

    Never interpolates (spec explicitly forbids silent linear
    interpolation for this phase) — always snaps to the nearest real
    sample coordinate, and always reports BOTH `requested_value` and
    `nearest_value` so the GUI can show the difference if any.
    """
    if cut_axis not in ("x", "y"):
        raise ValueError("cut_axis must be 'x' or 'y'")

    if cut_axis == "x":
        fix_values, fix_name, fix_unit = grid.y_values, grid.y_name, grid.y_unit
    else:
        fix_values, fix_name, fix_unit = grid.x_values, grid.x_name, grid.x_unit

    if fix_values.size == 0:
        raise ValueError(f"Grid has no '{fix_name}' samples to cut at.")

    fix_index = int(np.argmin(np.abs(fix_values - requested_value)))
    nearest_value = float(fix_values[fix_index])

    if cut_axis == "x":
        z_line = grid.z_values[fix_index, :]
        out_values, out_name, out_unit = grid.x_values, grid.x_name, grid.x_unit
    else:
        z_line = grid.z_values[:, fix_index]
        out_values, out_name, out_unit = grid.y_values, grid.y_name, grid.y_unit

    fixed_dims = dict(grid.fixed_dims)
    fixed_dims[fix_name] = {"index": fix_index, "value": nearest_value, "unit": fix_unit}

    return LineCutData(
        x_values=out_values,
        y_values=np.asarray(z_line).reshape(-1),
        x_name=out_name,
        x_unit=out_unit,
        z_name=grid.z_name,
        z_unit=grid.z_unit,
        transform=grid.transform,
        cut_axis=cut_axis,
        fixed_dims=fixed_dims,
        requested_value=float(requested_value),
        nearest_value=nearest_value,
        nearest_index=fix_index,
    )


def _slice_full_array(experiment: "Experiment", z_channel: str, dims: list[Dimension],
                       arr: np.ndarray, x_dim: str, y_dim: str | None,
                       fixed: dict[str, int] | None, transform: str
                       ) -> "Slice1DData | Grid2DData":
    """Shared slicing logic behind Experiment.get_nd_slice() and
    CachedExperiment.get_nd_slice() (which supplies a cached `arr`
    instead of re-reading it). Pure numpy indexing - no HDF5 access
    happens in this function, which is exactly what makes repeated
    slice-slider movement cheap once the full array is cached.
    """
    fixed = fixed or {}
    if not dims:
        raise SliceError(f"Channel '{z_channel}' has no dimensions to slice.")

    dim_by_name = {d.name: d for d in dims}
    dim_index_by_name = {d.name: i for i, d in enumerate(dims)}

    if x_dim not in dim_by_name:
        raise SliceError(
            f"X dimension '{x_dim}' not available for '{z_channel}'. "
            f"Available dimensions: {list(dim_by_name)}"
        )
    if y_dim is not None:
        if y_dim not in dim_by_name:
            raise SliceError(
                f"Y dimension '{y_dim}' not available for '{z_channel}'. "
                f"Available dimensions: {list(dim_by_name)}"
            )
        if x_dim == y_dim:
            raise SliceError("X and Y dimensions must be different.")

    varying_names = {x_dim} | ({y_dim} if y_dim else set())
    remaining = [d.name for d in dims if d.name not in varying_names]

    fixed_summary: dict[str, dict] = {}
    index_tuple: list[Any] = [slice(None)] * len(dims)
    for name in remaining:
        dim = dim_by_name[name]
        idx = int(fixed.get(name, 0))
        if not (0 <= idx < dim.size):
            raise SliceError(
                f"Fixed index {idx} out of range for dimension '{name}' (size {dim.size})."
            )
        index_tuple[dim_index_by_name[name]] = idx
        fixed_summary[name] = {"index": idx, "value": dim.value_at(idx), "unit": dim.unit}

    sliced = arr[tuple(index_tuple)]  # numpy drops the integer-indexed axes;
                                       # remaining axes keep their ORIGINAL relative order

    z_channel_info = experiment.get_channel(z_channel)

    if y_dim is None:
        x_dimension = dim_by_name[x_dim]
        y_values = np.asarray(sliced).reshape(-1)
        return Slice1DData(
            x_values=x_dimension.values,
            y_values=y_values,
            x_name=x_dimension.name,
            x_unit=x_dimension.unit,
            z_name=z_channel,
            z_unit=z_channel_info.unit,
            transform=transform,
            fixed_dims=fixed_summary,
        )

    varying_dims_in_order = [d for d in dims if d.name in varying_names]
    names_in_order = [d.name for d in varying_dims_in_order]
    x_pos = names_in_order.index(x_dim)
    y_pos = names_in_order.index(y_dim)

    # sliced.shape is (size of whichever varying dim comes first in the
    # ORIGINAL dims order, size of the other). We want z_values shaped
    # (ny, nx) regardless of which one that was.
    if x_pos < y_pos:
        z_grid = np.asarray(sliced).T   # sliced was (nx, ny) -> (ny, nx)
    else:
        z_grid = np.asarray(sliced)      # sliced was already (ny, nx)

    x_dimension = dim_by_name[x_dim]
    y_dimension = dim_by_name[y_dim]
    return Grid2DData(
        x_values=x_dimension.values,
        y_values=y_dimension.values,
        z_values=z_grid,
        x_name=x_dimension.name,
        x_unit=x_dimension.unit,
        y_name=y_dimension.name,
        y_unit=y_dimension.unit,
        z_name=z_channel,
        z_unit=z_channel_info.unit,
        transform=transform,
        fixed_dims=fixed_summary,
        acquisition=experiment.acquisition_status(z_channel),
    )


@dataclass
class Experiment:
    """The single unified representation the rest of the application
    operates on. Constructed exclusively by a BaseLabberParser
    subclass in labber_parser.py."""

    log_name: str
    source_path: str
    creation_time: float | None
    comment: str
    project: str | None
    tags: list[str]
    user: str | None
    version: str | None

    channels: dict[str, ChannelInfo]
    step_axes: list[StepAxis]                # active sweep axes only, in order
    log_channel_names: list[str]
    vector_traces: dict[str, VectorTraceInfo]  # keyed by channel name
    instrument_config: dict[str, dict]
    metadata_tree: dict[str, Any]              # everything else, for Metadata Viewer

    format_variant: str                        # from labber_parser classification

    _reader: HDF5Reader                        # kept open for lazy data access

    # ---- derived properties -------------------------------------------

    @property
    def display_name(self) -> str:
        """Human-facing name, intentionally separate from persistent state."""
        return self.log_name

    @property
    def data_identity(self) -> str:
        """Stable source key for viewer state, never derived from display text."""
        return stable_data_identity(self.source_path)

    @property
    def step_channel_names(self) -> list[str]:
        return [c.name for c in self.channels.values() if c.is_step]

    @property
    def n_active_step_dims(self) -> int:
        return len(self.step_axes)

    @property
    def has_vector_log_channel(self) -> bool:
        return len(self.vector_traces) > 0

    @property
    def total_dimensions(self) -> int:
        """Dimensionality relevant for plotting: active step axes plus
        one extra dimension if any log channel is itself a
        vector/trace (its own reconstructed axis, e.g. frequency)."""
        return self.n_active_step_dims + (1 if self.has_vector_log_channel else 0)

    def sweep_dimension_summary(self) -> dict[str, int]:
        summary = {axis.channel.name: len(axis.values) for axis in self.step_axes}
        for name, vt in self.vector_traces.items():
            summary[f"{name} (trace axis: {vt.x_name or 'index'})"] = vt.n_points
        return summary

    def acquisition_status(self, channel_name: str) -> AcquisitionStatus | None:
        """Describe a channel-specific legacy partial acquisition safely.

        Labber can retain a full nominal Step list when an acquisition stops
        early. A one-step vector trace maps unambiguously to the leading
        measured coordinates. Multi-step mismatches do not: their flat entry
        order alone cannot prove which incomplete row or slice was acquired.
        """
        trace = self.vector_traces.get(channel_name)
        if trace is None or not self.step_axes:
            return None
        nominal = int(np.prod([len(axis.values) for axis in self.step_axes]))
        acquired = int(trace.n_entries)
        if acquired == nominal:
            return AcquisitionStatus(channel_name, nominal, acquired, "complete", "complete acquisition")
        if len(self.step_axes) == 1 and 0 < acquired < nominal:
            return AcquisitionStatus(
                channel_name, nominal, acquired, "partial",
                "one-step acquisition ended before the nominal sweep completed",
            )
        return AcquisitionStatus(
            channel_name, nominal, acquired, "ambiguous",
            "entry count does not unambiguously map to the nominal multi-step sweep",
        )

    def _effective_step_values(self, channel_name: str, axis: StepAxis) -> np.ndarray:
        """Return only recorded coordinates for a safely recoverable trace."""
        status = self.acquisition_status(channel_name)
        if status is not None and status.is_partial and len(self.step_axes) == 1:
            return np.asarray(axis.values)[:status.acquired_entries]
        return np.asarray(axis.values)

    # ---- channel access --------------------------------------------------

    def get_channel(self, name: str) -> ChannelInfo:
        if name not in self.channels:
            raise ChannelNotFound(name)
        return self.channels[name]

    def get_step_values(self, channel_name: str) -> np.ndarray:
        """Returns the swept values for a step channel. Works whether
        the channel is an active StepAxis or (fallback) whatever
        constant/fixed value it holds in the scalar Data/Data matrix."""
        for axis in self.step_axes:
            if axis.channel.name == channel_name:
                return axis.values
        # fixed channel — read its (constant) recorded values from the
        # scalar matrix, if present, so callers get a consistent API
        # even for non-swept channels.
        return self.get_data(channel_name, transform="raw")

    def get_data(self, channel_name: str, transform: str = "raw",
                 entry_slice: Any = None) -> np.ndarray:
        """The single lazy data-loading entry point used by the plot
        layer. Never called implicitly at parse time.

        For a SCALAR channel: returns the 1D array of values across
        sweep entries (or a slice of it via `entry_slice`).

        For a VECTOR/complex channel: returns data with shape
        (n_points, n_entries) [or a slice], with `transform` applied:
        "raw" -> complex128 array
        "real" | "imag" | "magnitude" | "magnitude_db" | "phase_deg" | "phase_rad"
        (actual transform math lives in app/plotting/complex_transform.py;
        this method just knows how to fetch the raw real/imag pair and
        delegates.)

        `entry_slice` always refers to the SWEEP ENTRIES axis only
        (a plain slice or int) — callers never need to know whether
        the underlying raw dataset has an extra real/imag axis in the
        middle. This method builds the correct full-shape slice
        internally so the public API stays consistent between scalar
        and vector channels.
        """
        from app.plotting.complex_transform import apply_transform  # local import
        # avoids a hard dependency from core -> plotting at import time

        if channel_name in self.vector_traces:
            vt = self.vector_traces[channel_name]
            if entry_slice is None:
                full_slice = None
            elif vt.complex:
                # raw dataset shape: (n_points, 2[real/imag], n_entries)
                full_slice = (slice(None), slice(None), entry_slice)
            else:
                # raw dataset shape: (n_points, n_entries)
                full_slice = (slice(None), entry_slice)
            raw = self._reader.read(vt.trace_path, slice_=full_slice)
            # raw shape: (n_points, 2, n_entries_or_sliced) if complex
            if vt.complex:
                real = raw[:, 0, ...]
                imag = raw[:, 1, ...]
                complex_arr = real + 1j * imag
            else:
                complex_arr = raw
            return apply_transform(complex_arr, transform)

        # scalar channel: locate its row in Data/Data via metadata_tree
        scalar_info = self.metadata_tree.get("scalar_data_matrix")
        if not scalar_info:
            raise ChannelNotFound(
                f"Channel '{channel_name}' has no vector trace and no "
                f"scalar data matrix was found in this file."
            )
        names = scalar_info["channel_names"]
        if channel_name not in names:
            raise ChannelNotFound(channel_name)
        row_idx = names.index(channel_name)
        path = scalar_info["path"]
        full = self._reader.read(path)  # 3D, small - safe to load whole; axis
        # order varies between Labber files (see extract_scalar_channel_row's
        # docstring) so it is never assumed positionally.
        row = extract_scalar_channel_row(full, row_idx, n_channels=len(names))
        if entry_slice is not None:
            row = row[entry_slice]
        return row

    # ---- 2D surface API (Phase 6) ------------------------------------------

    def get_2d_data(self, x_channel: str, y_channel: str, z_channel: str,
                     transform: str = "raw") -> "Grid2DData":
        """Build a regular 2D surface from the selected Z channel's dimensions.

        Axis names and order are resolved through the generic N-D model, so a
        two-dimensional experiment can place either physical dimension on X or
        Y without special-casing Frequency, Current, Flux, or any channel name.
        Experiments with another dimensionality use the dedicated 1D/N-D views.
        """
        dimensions = self.list_dimensions(z_channel)
        if len(dimensions) != 2:
            raise Data2DError(
                f"Channel '{z_channel}' has {len(dimensions)} physical dimensions; "
                "the 2D Heatmap view requires exactly two. Use the 1D Plot or "
                "N-D Slice Explorer for this channel."
            )
        try:
            result = self.get_nd_slice(
                z_channel, x_dim=x_channel, y_dim=y_channel,
                fixed={}, transform=transform,
            )
        except SliceError as exc:
            raise Data2DError(str(exc)) from exc
        if not isinstance(result, Grid2DData):
            raise Data2DError("The selected dimensions did not produce a 2D surface.")
        return result

    def _get_2d_data_vector_z(self, x_channel: str, y_channel: str, z_channel: str,
                               transform: str) -> "Grid2DData":
        vt = self.vector_traces[z_channel]
        expected_x = vt.x_name or z_channel
        if x_channel not in (expected_x, z_channel):
            raise Data2DError(
                f"X axis '{x_channel}' does not match the trace's own axis "
                f"'{expected_x}' of Z channel '{z_channel}'. A vector log "
                f"channel's own axis is the only valid X choice."
            )

        matching = [a for a in self.step_axes if a.channel.name == y_channel]
        if not matching:
            raise Data2DError(
                f"Y axis '{y_channel}' is not an actively swept channel in "
                f"this file, so it cannot be used as the 2D sweep axis."
            )
        if len(self.step_axes) != 1:
            other_names = [a.channel.name for a in self.step_axes if a.channel.name != y_channel]
            raise Data2DError(
                f"This file has {len(self.step_axes)} active sweep dimensions "
                f"({[a.channel.name for a in self.step_axes]}), not just "
                f"'{y_channel}'. Forming a clean 2D surface would require "
                f"fixing the other axes ({other_names}) at a specific value, "
                f"which needs the N-dimensional Slice Explorer (not yet "
                f"implemented in this phase). This channel combination cannot "
                f"currently be represented as a 2D sweep."
            )
        axis = matching[0]
        status = self.acquisition_status(z_channel)
        if status is not None and not status.is_recoverable:
            raise Data2DError(
                f"'{z_channel}' has {status.acquired_entries} acquired sweep entries for "
                f"{status.nominal_entries} planned entries. The partial multi-step layout is ambiguous."
            )
        y_values = self._effective_step_values(z_channel, axis)

        raw = self.get_data(z_channel, transform=transform)  # shape (n_points, n_entries)
        if raw.ndim != 2 or raw.shape[1] != len(y_values):
            raise Data2DError(
                f"'{z_channel}' data shape {raw.shape} does not match the "
                f"{len(axis.values)} points recorded for '{y_channel}'. This "
                f"channel combination cannot be represented as a 2D sweep."
            )

        z_grid = raw.T  # (n_entries, n_points) == (ny, nx)
        z_channel_info = self.get_channel(z_channel)

        return Grid2DData(
            x_values=vt.x_values,
            y_values=y_values,
            z_values=z_grid,
            x_name=vt.x_name or "Index",
            x_unit=vt.x_unit,
            y_name=axis.channel.name,
            y_unit=axis.channel.unit,
            z_name=z_channel,
            z_unit=z_channel_info.unit,
            transform=transform,
            acquisition=status,
        )

    def _get_2d_data_scalar_z(self, x_channel: str, y_channel: str, z_channel: str,
                               transform: str) -> "Grid2DData":
        x_axis = next((a for a in self.step_axes if a.channel.name == x_channel), None)
        y_axis = next((a for a in self.step_axes if a.channel.name == y_channel), None)
        if x_axis is None or y_axis is None:
            raise Data2DError(
                f"For a scalar Z channel ('{z_channel}'), both X ('{x_channel}') "
                f"and Y ('{y_channel}') must be actively swept step channels. "
                f"This channel combination cannot be represented as a 2D sweep."
            )
        if x_axis is y_axis:
            raise Data2DError("X and Y axes must be two different channels.")

        nx, ny = len(x_axis.values), len(y_axis.values)
        flat = np.asarray(self.get_data(z_channel, transform=transform)).reshape(-1)
        if flat.size != nx * ny:
            raise Data2DError(
                f"Channel '{z_channel}' has {flat.size} data points, which does "
                f"not match {y_channel}\u00d7{x_channel} = {ny}\u00d7{nx}. This "
                f"channel combination cannot be represented as a regular 2D sweep."
            )

        # Infer loop nesting from each axis's recorded position in the
        # original Step list (dim_index) rather than assuming an order —
        # the axis that appears earlier in Step list is conventionally
        # the outer (slower-varying) loop in a Labber measurement.
        if y_axis.dim_index < x_axis.dim_index:
            z_grid = flat.reshape(ny, nx)
        else:
            z_grid = flat.reshape(nx, ny).T

        z_channel_info = self.get_channel(z_channel)
        return Grid2DData(
            x_values=x_axis.values,
            y_values=y_axis.values,
            z_values=z_grid,
            x_name=x_axis.channel.name,
            x_unit=x_axis.channel.unit,
            y_name=y_axis.channel.name,
            y_unit=y_axis.channel.unit,
            z_name=z_channel,
            z_unit=z_channel_info.unit,
            transform=transform,
        )

    # ---- N-D Slice Explorer API (Phase 7) --------------------------------------

    def list_dimensions(self, z_channel: str) -> list["Dimension"]:
        """Returns the ordered list of Dimension objects for a channel
        - the physical axis order of its underlying data array. Never
        hardcoded: derived purely from whether the channel is a
        vector/trace (its own reconstructed axis comes first) and the
        experiment's actual active step axes (in Step list order).

        For a vector/trace channel (e.g. 'VNA - S21'):
            [trace's own axis, *active step axes in Step list order]
        For a scalar log channel:
            [*active step axes in Step list order]

        This generalizes cleanly to any N: a channel with 4 active
        step axes plus a vector trace axis simply returns a 5-element
        list, with no special-casing beyond "vector or not"."""
        if z_channel in self.vector_traces:
            vt = self.vector_traces[z_channel]
            dims = [_make_dimension(vt.x_name or "Index", vt.x_unit, vt.x_values)]
            dims += [
                _make_dimension(a.channel.name, a.channel.unit, self._effective_step_values(z_channel, a))
                for a in self.step_axes
            ]
            return dims
        return [_make_dimension(a.channel.name, a.channel.unit, a.values) for a in self.step_axes]

    def get_full_nd_array(self, z_channel: str, transform: str = "raw"
                           ) -> tuple[list["Dimension"], np.ndarray]:
        """Reads (and, for a vector channel, transforms) the FULL data
        array for a channel, reshaped to match list_dimensions(z_channel)
        order exactly - array.shape[i] == dims[i].size for every i.

        This is the one place a vector channel's entire trace dataset
        gets read into memory at once (e.g. ~3.4MB for the 855x501
        real sample) - a deliberate, documented trade-off: it lets the
        N-D Slice Explorer support genuinely arbitrary X/Y dimension
        pairing (including swapping the trace axis for a step axis)
        with simple, obviously-correct numpy indexing, rather than a
        much more complex family of special-cased lazy HDF5 reads for
        every possible pairing. CachedExperiment caches this per
        (z_channel, transform) so it is read from HDF5 at most ONCE
        per transform choice, however many times the user moves a
        slice slider afterward (spec §6/§12 performance requirements)
        - see cache.py.

        Raises SliceError if the entries count doesn't factor cleanly
        into the active step dimensions (should not happen for a
        correctly-parsed file, but validated defensively rather than
        producing a garbled reshape).
        """
        dims = self.list_dimensions(z_channel)

        if z_channel in self.vector_traces:
            flat = self.get_data(z_channel, transform=transform)  # (n_points, n_entries)
            active_sizes = tuple(d.size for d in dims[1:])
            if not active_sizes:
                # no active sweep - a single entry; squeeze it away so
                # the array shape matches list_dimensions() (length 1)
                # exactly, with no synthetic placeholder dimension.
                arr = flat.reshape(dims[0].size)
            else:
                expected_entries = int(np.prod(active_sizes))
                if flat.shape[-1] != expected_entries:
                    raise SliceError(
                        f"'{z_channel}' has {flat.shape[-1]} sweep entries, which does "
                        f"not match the product of active step dimensions {active_sizes} "
                        f"= {expected_entries}. Cannot build the N-D array."
                    )
                arr = flat.reshape((dims[0].size,) + active_sizes)
            return dims, arr

        flat = np.asarray(self.get_data(z_channel, transform=transform)).reshape(-1)
        active_sizes = tuple(d.size for d in dims)
        if not active_sizes:
            raise SliceError(
                f"'{z_channel}' has no active sweep dimensions - there is nothing "
                f"to slice."
            )
        expected = int(np.prod(active_sizes))
        if flat.size != expected:
            raise SliceError(
                f"Channel '{z_channel}' has {flat.size} data points, which does not "
                f"match the product of active step dimensions {active_sizes} = "
                f"{expected}. Cannot build the N-D array."
            )
        arr = flat.reshape(active_sizes)
        return dims, arr

    def get_nd_slice(self, z_channel: str, x_dim: str, y_dim: str | None = None,
                      fixed: dict[str, int] | None = None, transform: str = "raw"
                      ) -> "Slice1DData | Grid2DData":
        """The generic N-D Slice Explorer entry point (Phase 7).

        `x_dim` (required) and `y_dim` (optional - omit for a 1D
        trace) name any two DIFFERENT dimensions from
        list_dimensions(z_channel), in any order/combination - the
        trace's own axis and any active step axis are all equally
        valid choices for either one (spec §4's "dynamic X/Y
        selection", including swapping which one is X vs Y).

        `fixed` supplies the INDEX (not physical value) to hold every
        OTHER dimension at; any dimension not mentioned defaults to
        index 0. Raises SliceError for an out-of-range index, an
        unknown dimension name, or X/Y being the same dimension -
        the GUI catches this, shows a message, and keeps the previous
        plot (spec §12).
        """
        dims, arr = self.get_full_nd_array(z_channel, transform=transform)
        return _slice_full_array(self, z_channel, dims, arr, x_dim, y_dim, fixed, transform)

    def close(self) -> None:
        self._reader.close()

    def __enter__(self) -> "Experiment":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
