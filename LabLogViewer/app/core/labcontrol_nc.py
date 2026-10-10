"""
app/core/labcontrol_nc.py — Lab Control xarray/NetCDF (.nc) files, v2.1.0

Lab Control 0.0.20+ saves measurements as xarray NetCDF4 files written
with h5netcdf (schema attribute "labcontrol-nc/1"). Such a file is an
ordinary HDF5 file, so HDF5Reader opens it with h5py; this module then
builds an in-memory *Labber-shaped view* of it (/Channels, /Step list,
/Log list, /Data/Data, /Traces/<name>, /Tags, /Instrument config) so the
existing LabberParserV2 / Experiment / plotting / analysis code reads it
exactly like a Labber log — nothing downstream needs to know the source
was NetCDF.

Layout mapping
--------------
- grid layout (attrs layout == "grid"): every coordinate carrying a
  "target" attribute is a sweep axis. Sweep entries are the C-order
  flattening of those axes in their stored order (the first axis varies
  slowest) — the same convention Experiment.get_full_nd_array() uses to
  reshape entries back into N-D. Values use the file's display units
  (e.g. mA), as written by Lab Control.
- point layout (irregular sweeps): one active step channel "Point"
  (1..N); each recorded axis value becomes a scalar log channel.
- variables with one extra (non-sweep) dimension are vector traces
  (e.g. S21 vs Frequency). Their x axis is stored explicitly as
  /Traces/<name>_xvalues (plus the usual _N / _t0dt for compatibility).
- missing points of a partial measurement are NaN.

Everything is read into memory once at open time; Lab Control files are
small (a 101 x 501 complex sweep is ~0.8 MB).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

SCHEMA_PREFIX = "labcontrol-nc/"
XVALUES_SUFFIX = "_xvalues"


def _scalar(value: Any) -> Any:
    """h5netcdf stores numeric attributes as 1-element arrays."""
    if isinstance(value, np.ndarray):
        return value.reshape(-1)[0].item() if value.size else None
    return value


def _s(value: Any) -> str:
    value = _scalar(value)
    if isinstance(value, (bytes, np.bytes_)):
        return value.decode("utf-8", errors="replace")
    return "" if value is None else str(value)


def is_labcontrol_nc(h5file) -> bool:
    try:
        return _s(h5file.attrs.get("schema", "")).startswith(SCHEMA_PREFIX)
    except Exception:
        return False


@dataclass
class VNode:
    """One node of the virtual Labber tree."""
    kind: str                                  # "group" | "dataset"
    data: np.ndarray | None = None
    attrs: dict = field(default_factory=dict)
    children: list[str] = field(default_factory=list)


def _dims(ds) -> list[str]:
    """Dimension names of an h5netcdf variable (from its attached dimension scales)."""
    names = []
    for i in range(len(ds.shape)):
        try:
            scales = list(ds.dims[i].values())
            names.append(scales[0].name.lstrip("/") if scales else f"dim{i}")
        except Exception:
            names.append(f"dim{i}")
    return names


def _row_array(rows: list[dict], fields: list[str]) -> np.ndarray:
    arr = np.empty(len(rows), dtype=[(f, object) for f in fields])
    for i, row in enumerate(rows):
        arr[i] = tuple(row.get(f, "") for f in fields)
    return arr


def _instrument_of(target: str) -> str:
    return target.split(".", 1)[0] if target else ""


def _flatten_settings(value: Any, prefix: str = "", depth: int = 0) -> dict:
    out: dict = {}
    if isinstance(value, dict) and depth < 3:
        for k, v in value.items():
            out.update(_flatten_settings(v, f"{prefix}{k}" if not prefix else f"{prefix} / {k}", depth + 1))
    elif isinstance(value, (str, int, float, bool)) and prefix:
        out[prefix] = value
    return out


def _creation_time(attrs: dict, timestamps: np.ndarray | None) -> float | None:
    created = _s(attrs.get("created", ""))
    if created:
        try:
            return datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    if timestamps is not None and np.isfinite(timestamps).any():
        return float(np.nanmin(timestamps))
    return None


def build_labber_view(h5file) -> dict[str, VNode]:
    """Return {path: VNode} — a Labber-shaped tree for one Lab Control .nc file."""
    root_attrs = {str(k): h5file.attrs[k] for k in h5file.attrs}
    variables = {name: obj for name, obj in h5file.items() if hasattr(obj, "shape")}

    def attr(name: str, key: str, default: Any = "") -> Any:
        try:
            return variables[name].attrs.get(key, default)
        except Exception:
            return default

    sweep_coords = [n for n in variables if "target" in variables[n].attrs]
    data_vars = [n for n in variables
                 if n not in sweep_coords and "channel" in variables[n].attrs]
    layout = _s(root_attrs.get("layout", "grid")) or "grid"

    # order of sweep axes = order of dimensions on the data variables
    axis_order: list[str] = []
    for n in data_vars:
        for d in _dims(variables[n]):
            if d in sweep_coords and d not in axis_order:
                axis_order.append(d)
    for d in sweep_coords:
        if d not in axis_order:
            axis_order.append(d)

    if layout == "point":
        n_entries = int(variables["point"].shape[0]) if "point" in variables else 0
        sweep_dims = ["point"]
        sizes = [n_entries]
    else:
        sweep_dims = axis_order
        sizes = [int(variables[d].shape[0]) for d in axis_order]
        n_entries = int(np.prod(sizes)) if sizes else 1

    def entries(values: np.ndarray, extra: int = 0) -> np.ndarray:
        """(sweep dims…, extra…) → (n_entries, extra…) in C order."""
        tail = values.shape[values.ndim - extra:] if extra else ()
        return np.asarray(values).reshape((n_entries,) + tuple(tail))

    channels_rows: list[dict] = []
    step_rows: list[dict] = []
    log_rows: list[dict] = []
    scalar_names: list[str] = []
    scalar_rows: list[np.ndarray] = []
    traces: dict[str, VNode] = {}

    # ---- step (sweep) channels ------------------------------------------
    if layout == "point":
        step_name = "Point"
        channels_rows.append({"name": step_name, "instrument": "", "unitPhys": "", "unitInstr": ""})
        step_rows.append({"channel_name": step_name, "use_relations": False, "equation": ""})
        scalar_names.append(step_name)
        scalar_rows.append(np.arange(1, n_entries + 1, dtype=float))
        for d in axis_order:              # recorded axis values → scalar log channels
            name = _s(attr(d, "long_name", d)) or d
            channels_rows.append({"name": name, "instrument": _instrument_of(_s(attr(d, "target"))),
                                  "unitPhys": _s(attr(d, "units")), "unitInstr": _s(attr(d, "units"))})
            log_rows.append({"channel_name": name})
            scalar_names.append(name)
            scalar_rows.append(np.asarray(variables[d][()], dtype=float).reshape(-1))
    else:
        grids = np.meshgrid(*[np.asarray(variables[d][()], dtype=float) for d in axis_order], indexing="ij") \
            if axis_order else []
        for d, g in zip(axis_order, grids):
            name = _s(attr(d, "long_name", d)) or d
            channels_rows.append({"name": name, "instrument": _instrument_of(_s(attr(d, "target"))),
                                  "unitPhys": _s(attr(d, "units")), "unitInstr": _s(attr(d, "units"))})
            step_rows.append({"channel_name": name, "use_relations": False, "equation": ""})
            scalar_names.append(name)
            scalar_rows.append(g.reshape(-1))

    # ---- logged channels --------------------------------------------------
    for n in data_vars:
        v = variables[n]
        dims = _dims(v)
        extra = [d for d in dims if d not in sweep_dims]
        name = _s(v.attrs.get("long_name", n)) or n
        unit = _s(v.attrs.get("units", ""))
        channels_rows.append({"name": name, "instrument": _s(v.attrs.get("source", "")),
                              "unitPhys": unit, "unitInstr": unit})
        log_rows.append({"channel_name": name})
        values = np.asarray(v[()])
        if not extra:
            scalar_names.append(name)
            scalar_rows.append(entries(values).astype(float).reshape(-1))
            continue
        xd = extra[0]
        x_values = np.asarray(variables[xd][()], dtype=float) if xd in variables \
            else np.arange(values.shape[-1], dtype=float)
        flat = entries(values, extra=1)                      # (n_entries, n_points)
        is_complex = np.iscomplexobj(flat)
        if is_complex:
            trace = np.stack([flat.real.T, flat.imag.T], axis=1)   # (n_points, 2, n_entries)
        else:
            trace = flat.astype(float).T                          # (n_points, n_entries)
        x_name = _s(attr(xd, "long_name", xd)) or xd
        x_unit = _s(attr(xd, "units", ""))
        traces[name] = VNode("dataset", trace, {"complex": bool(is_complex), "x, name": x_name,
                                                "x, unit": x_unit})
        n_points = len(x_values)
        dx = float(x_values[1] - x_values[0]) if n_points > 1 else 1.0
        traces[f"{name}_N"] = VNode("dataset", np.array([[n_points]]))
        traces[f"{name}_t0dt"] = VNode("dataset", np.array([[float(x_values[0]) if n_points else 0.0, dx]]))
        traces[f"{name}{XVALUES_SUFFIX}"] = VNode("dataset", x_values)

    timestamps = None
    if "timestamp" in variables:
        timestamps = entries(np.asarray(variables["timestamp"][()], dtype=float)).reshape(-1)
        channels_rows.append({"name": "Time stamp", "instrument": "", "unitPhys": "s", "unitInstr": "s"})
        log_rows.append({"channel_name": "Time stamp"})
        scalar_names.append("Time stamp")
        scalar_rows.append(timestamps)

    data_matrix = (np.stack(scalar_rows)[np.newaxis, :, :] if scalar_rows
                   else np.zeros((1, 0, n_entries)))           # (1, n_channels, n_entries)

    # ---- tags / instrument settings -------------------------------------
    tags = [t.strip() for t in _s(root_attrs.get("tags", "")).split(",") if t.strip()]
    tags_attrs = {"Project": [_s(root_attrs.get("project", ""))], "Tags": tags,
                  "User": [_s(root_attrs.get("user", ""))]}
    instrument_nodes: dict[str, VNode] = {}
    try:
        meta = json.loads(_s(root_attrs.get("labcontrol", "")) or "{}")
    except ValueError:
        meta = {}
    for inst, settings in (meta.get("snapshot") or {}).items():
        instrument_nodes[f"/Instrument config/{inst}"] = VNode("group", None, _flatten_settings(settings))

    complete = bool(int(_scalar(root_attrs.get("complete", 1)) or 0))
    tree: dict[str, VNode] = {
        "/": VNode("group", None, {
            "log_name": _s(root_attrs.get("name", "")), "comment": _s(root_attrs.get("comment", "")),
            "creation_time": _creation_time(root_attrs, timestamps), "version": "Lab Control NetCDF",
            "labcontrol_schema": _s(root_attrs.get("schema", "")),
            **({"star": bool(_scalar(root_attrs["star"]))} if "star" in root_attrs else {}),
        }),
        "/Channels": VNode("dataset", _row_array(channels_rows, ["name", "instrument", "unitPhys", "unitInstr"])),
        "/Step list": VNode("dataset", _row_array(step_rows, ["channel_name", "use_relations", "equation"])),
        "/Log list": VNode("dataset", _row_array(log_rows, ["channel_name"])),
        "/Data": VNode("group", None, {"Step dimensions": np.array(sizes, dtype=int), "Completed": complete}),
        "/Data/Data": VNode("dataset", data_matrix),
        "/Data/Channel names": VNode("dataset", _row_array([{"name": n} for n in scalar_names], ["name"])),
        "/Tags": VNode("group", None, tags_attrs),
    }
    if traces:
        tree["/Traces"] = VNode("group")
        for key, node in traces.items():
            tree[f"/Traces/{key}"] = node
    if instrument_nodes:
        tree["/Instrument config"] = VNode("group")
        tree.update(instrument_nodes)
    # children lists
    for path in list(tree):
        if path == "/":
            continue
        parent = path.rsplit("/", 1)[0] or "/"
        tree.setdefault(parent, VNode("group")).children.append(path.rsplit("/", 1)[1])
    return tree


class NetCDFComplexTraceWriter:
    """De-background for Lab Control .nc files: write processed entries back into the complex NetCDF
    variable of a copied file (same interface as hdf5_processing_writer.HDF5ComplexTraceWriter).

    Entry k of the Labber-shaped view is the C-order position k of the sweep dimensions (see
    build_labber_view), so it is written to ``variable[unravel_index(k, sweep_shape) + (:,)]``.
    """

    def __init__(self, path, trace_name: str, n_points: int, n_entries: int):
        self.path = path
        self.trace_name = trace_name
        self.n_points = int(n_points)
        self.n_entries = int(n_entries)
        self._file = None
        self._dataset = None
        self._sweep_shape: tuple[int, ...] = ()

    def __enter__(self) -> "NetCDFComplexTraceWriter":
        import h5py

        from app.core.hdf5_processing_writer import HDF5ProcessingWriteError
        from app.core.win_paths import long_path
        from pathlib import Path

        try:
            self._file = h5py.File(long_path(Path(self.path)), "r+")
            found = None
            for name, obj in self._file.items():
                if not hasattr(obj, "shape") or "channel" not in obj.attrs:
                    continue
                label = _s(obj.attrs.get("long_name", name)) or name
                if label == self.trace_name:
                    found = obj
                    break
            if found is None:
                raise HDF5ProcessingWriteError(f"Trace variable '{self.trace_name}' is missing from the copied file.")
            if not np.iscomplexobj(np.empty(0, dtype=found.dtype)):
                raise HDF5ProcessingWriteError(f"Trace variable '{self.trace_name}' is not stored as complex numbers.")
            shape = tuple(int(n) for n in found.shape)
            if len(shape) < 2 or shape[-1] != self.n_points or int(np.prod(shape[:-1])) != self.n_entries:
                raise HDF5ProcessingWriteError(
                    f"Trace layout changed during processing: expected {self.n_entries} entries of "
                    f"{self.n_points} points, got {shape}.")
            self._dataset = found
            self._sweep_shape = shape[:-1]
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def write_entries(self, start: int, stop: int, real: np.ndarray, imag: np.ndarray) -> None:
        from app.core.hdf5_processing_writer import HDF5ProcessingWriteError

        if self._dataset is None:
            raise HDF5ProcessingWriteError("The trace writer is not open.")
        expected = (self.n_points, stop - start)
        if real.shape != expected or imag.shape != expected:
            raise HDF5ProcessingWriteError(f"Processed trace chunk has the wrong shape; expected {expected}.")
        values = (np.asarray(real, dtype=float) + 1j * np.asarray(imag, dtype=float)).T   # (entries, points)
        cells = np.unravel_index(np.arange(start, stop), self._sweep_shape)
        for j in range(stop - start):
            index = tuple(int(axis[j]) for axis in cells)
            self._dataset[index] = values[j].astype(self._dataset.dtype)

    def flush(self) -> None:
        if self._file is not None:
            self._file.flush()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._dataset = None
        if self._file is not None:
            self._file.close()
            self._file = None
