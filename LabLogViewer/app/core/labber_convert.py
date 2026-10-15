"""
app/core/labber_convert.py — v1.0.7: Lab Control .nc ⇄ Labber .hdf5

Both directions go through one neutral in-memory :class:`Sweep` (axes outer-first, every channel as
an N-D array), built from LabLogViewer's own :class:`Experiment` — so anything LabLogViewer can open
(Labber logs, Lab Control .nc, raw .lm.h5) can be converted.

.nc → Labber .hdf5
    * "api"     — Labber's own Python API (``Labber.createLogFile_ForData``), run in the lab's
                  Labber Python 3.8 environment (labber_api_script.py). Same route Lab Control uses.
    * "builtin" — written here with h5py, reproducing the layout of a real file made by that API
                  (sample_reports/0828_RSMEP_1.inspect.json): /Channels, /Instruments, /Step list,
                  /Step config, /Log list, /Data (+ "Step index API" first), /Traces, /Tags,
                  /Instrument config. Used when no Labber Python is available.
    * "auto"    — api when a working Labber Python is found, otherwise builtin.

Labber's Step list runs the FIRST channel fastest (inner loop), so the written Step list is
["Step index API", inner axis, …, outer axis, fixed settings…] and entries are ordered inner-fastest.

Labber .hdf5 → .nc
    Lab Control's NetCDF schema "labcontrol-nc/1" (grid layout): one coordinate per swept axis
    (units / long_name / target), trace axes as their own coordinate, complex values stored as complex
    (h5netcdf ``invalid_netcdf=True``, exactly like Lab Control), the instrument settings in the
    "labcontrol" attribute's "snapshot".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

LABBER_VERSION = "1.8.6"
STEP_INDEX = "Step index API"
STEP_INSTR = "Generic - GPIB: , Step channels at localhost"
LOG_INSTR = "Generic - GPIB: , Log channels at localhost"
DEFAULT_LABBER_PYTHON = r"C:\LocalEnvs\Labber環境_venv\Scripts\python.exe"   # Lab Control's default
SCRIPT = Path(__file__).with_name("labber_api_script.py")
ENGINES = ("auto", "api", "builtin")


class ConvertError(Exception):
    """The file cannot be converted (message is shown to the user)."""


# ---- neutral model --------------------------------------------------------------------------------
@dataclass
class Axis:
    name: str
    unit: str
    values: np.ndarray
    instrument: str = ""


@dataclass
class Chan:
    name: str
    unit: str
    data: np.ndarray                 # (*axis sizes) scalar, (*axis sizes, n_points) trace
    instrument: str = ""
    x_name: str = ""
    x_unit: str = ""
    x: np.ndarray | None = None      # trace axis (None for scalar channels)

    @property
    def vector(self) -> bool:
        return self.x is not None

    @property
    def is_complex(self) -> bool:
        return np.iscomplexobj(self.data)


@dataclass
class Sweep:
    name: str
    axes: list[Axis]                                   # outer (slowest) first
    channels: list[Chan]
    fixed: list[tuple[str, str, float]] = field(default_factory=list)   # constant step channels
    timestamps: np.ndarray | None = None               # (*axis sizes)
    instrument_config: dict[str, dict] = field(default_factory=dict)
    comment: str = ""
    project: str = ""
    user: str = ""
    tags: list[str] = field(default_factory=list)
    created: float | None = None
    star: bool = False
    complete: bool = True
    notes: list[str] = field(default_factory=list)      # things the user should know

    @property
    def sizes(self) -> tuple[int, ...]:
        return tuple(len(a.values) for a in self.axes)

    @property
    def n_entries(self) -> int:
        return int(np.prod(self.sizes)) if self.axes else 1


# ---- helpers ---------------------------------------------------------------------------------------
def _plain(value: Any) -> Any:
    """JSON / HDF5-attribute friendly scalar, or None to drop it."""
    if isinstance(value, (bytes, np.bytes_)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, np.ndarray):
        if value.size == 1:
            return _plain(value.reshape(-1)[0])
        return None
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (str, bool, int, float)):
        return value
    return None


def _uniform(x: np.ndarray) -> bool:
    if len(x) < 3:
        return True
    d = np.diff(x)
    return bool(np.allclose(d, d[0], rtol=1e-6, atol=abs(d[0]) * 1e-9 + 1e-300))


def unique_path(path: Path) -> Path:
    """``path`` itself, or ``name_2.ext``, ``name_3.ext`` … — conversion never overwrites a file."""
    if not path.exists():
        return path
    k = 2
    while True:
        other = path.with_name(f"{path.stem}_{k}{path.suffix}")
        if not other.exists():
            return other
        k += 1


def target_path(src: str | Path, to: str, folder: str | Path | None = None) -> Path:
    """Where ``src`` converted to ``to`` ("hdf5" | "nc") goes: same name, new extension, never overwriting."""
    src = Path(src)
    stem = src.name[:-len(".lm.h5")] if src.name.lower().endswith(".lm.h5") else src.stem
    return unique_path(Path(folder or src.parent) / f"{stem}.{to}")


def kind_of(path: str | Path) -> str:
    """"nc" for Lab Control files, "hdf5" for Labber logs, "" otherwise (by extension)."""
    name = str(path).lower()
    if name.endswith((".nc", ".lm.h5")):
        return "nc"
    if name.endswith((".hdf5", ".h5")):
        return "hdf5"
    return ""


# ---- Experiment → Sweep ------------------------------------------------------------------------------
def experiment_to_sweep(exp) -> Sweep:
    """Any opened LabLogViewer Experiment as a neutral Sweep (axes outer-first)."""
    listed = list(exp.step_axes)
    order = getattr(exp, "entry_order", "C")
    outer_first = list(reversed(listed)) if order == "F" else listed
    axes = [Axis(a.channel.name, a.channel.unit or "", np.asarray(a.values, dtype=float),
                 a.channel.instrument or "") for a in outer_first]
    nominal = int(np.prod([len(a.values) for a in axes])) if axes else 1
    notes: list[str] = []
    complete = True

    def shape_entries(flat: np.ndarray, n: int) -> np.ndarray:
        """(n_entries, *tail) in file order → (*outer-first sizes, *tail)."""
        tail = flat.shape[1:]
        return flat.reshape(tuple(len(a.values) for a in axes) + tail) if axes else flat.reshape(tail)

    def entries_count(n: int, what: str) -> None:
        nonlocal complete, axes
        if n == nominal:
            return
        if len(axes) == 1 and 0 < n < nominal:
            axes = [Axis(axes[0].name, axes[0].unit, axes[0].values[:n], axes[0].instrument)]
            complete = False
            return
        raise ConvertError(f"'{what}' has {n} entries, which does not fit the sweep "
                           f"({' × '.join(str(len(a.values)) for a in axes) or '1'}).")

    def file_order_to_entries(flat: np.ndarray) -> np.ndarray:
        """Entries in the file's own order → outer-first C order (they already are, both conventions)."""
        return flat

    channels: list[Chan] = []
    timestamps = None
    scalar_names = list((exp.metadata_tree.get("scalar_data_matrix") or {}).get("channel_names") or [])
    active = {a.name for a in axes}
    for name in exp.log_channel_names:
        if name in active:
            continue
        info = exp.channels.get(name)
        if name in exp.vector_traces:
            vt = exp.vector_traces[name]
            data = np.asarray(exp.get_data(name, transform="raw"))            # (n_points, n_entries)
            if data.ndim == 1:
                data = data[:, None]
            flat = data.T
            if not vt.complex:
                flat = flat.real.astype(float)
            entries_count(flat.shape[0], name)
            channels.append(Chan(name, (info.unit if info else "") or "", file_order_to_entries(flat),
                                 (info.instrument if info else "") or "", vt.x_name or "", vt.x_unit or "",
                                 np.asarray(vt.x_values, dtype=float)))
        elif name in scalar_names:
            flat = np.asarray(exp.get_data(name, transform="raw"), dtype=float).reshape(-1)
            if name == "Time stamp":
                timestamps = flat
                continue
            entries_count(flat.shape[0], name)
            channels.append(Chan(name, (info.unit if info else "") or "", flat, (info.instrument if info else "") or ""))
    if not channels:
        raise ConvertError("No measured (log) channels to convert.")
    n = int(np.prod([len(a.values) for a in axes])) if axes else 1
    for c in channels:
        c.data = shape_entries(np.asarray(c.data)[:n], n)

    if timestamps is None:
        reader = getattr(exp, "_reader", None)
        for path in ("/Traces/Time stamp", "/Data/Time stamp"):
            try:
                if reader is not None and reader.kind_of(path) == "dataset":
                    ts = np.asarray(reader.read(path), dtype=float).reshape(-1)
                    if ts.size >= n:
                        timestamps = ts
                        break
            except Exception:  # noqa: BLE001 - a missing time stamp is not an error
                pass
    if timestamps is not None and timestamps.size >= n:
        timestamps = shape_entries(timestamps[:n], n)
    else:
        timestamps = None

    fixed: list[tuple[str, str, float]] = []
    for ch in exp.channels.values():
        if not ch.is_step or ch.name in active or ch.name in (STEP_INDEX, "Point") or ch.name not in scalar_names:
            continue
        try:
            vals = np.asarray(exp.get_data(ch.name, transform="raw"), dtype=float).reshape(-1)
        except Exception:  # noqa: BLE001
            continue
        finite = vals[np.isfinite(vals)]
        if finite.size:
            fixed.append((ch.name, ch.unit or "", float(finite[0])))

    config: dict[str, dict] = {}
    for inst, settings in (exp.instrument_config or {}).items():
        if inst in (STEP_INSTR, LOG_INSTR):
            continue
        plain = {str(k): _plain(v) for k, v in (settings or {}).items()}
        plain = {k: v for k, v in plain.items() if v is not None and not k.startswith("___")}
        if plain:
            config[str(inst)] = plain

    root = (exp.metadata_tree.get("root_attrs") or {})
    project = exp.project if isinstance(exp.project, str) else ""
    user = exp.user if isinstance(exp.user, str) else ""
    stem = Path(exp.source_path).name
    for suffix in (".lm.h5", ".hdf5", ".h5", ".nc"):
        if stem.lower().endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    for c in channels:
        if c.vector and not _uniform(c.x):
            notes.append(f"'{c.name}': {c.x_name or 'x'} is not evenly spaced — Labber only keeps start + step.")
    return Sweep(name=str(root.get("log_name") or exp.log_name or stem) or stem, axes=axes, channels=channels,
                 fixed=fixed, timestamps=timestamps, instrument_config=config, comment=exp.comment or "",
                 project=project or "", user=user or "", tags=[str(t) for t in (exp.tags or [])],
                 created=float(exp.creation_time) if exp.creation_time else None,
                 star=bool(_plain(root.get("star", False)) or False), complete=complete, notes=notes)


def load_sweep(path: str | Path) -> Sweep:
    from app.core.labber_parser import load_experiment

    try:
        exp = load_experiment(str(path))
    except Exception as error:  # noqa: BLE001
        raise ConvertError(f"Cannot open: {error}") from error
    try:
        return experiment_to_sweep(exp)
    finally:
        try:
            exp.close()
        except Exception:  # noqa: BLE001
            pass


# ---- Sweep → Labber (built-in writer) -----------------------------------------------------------------
def _labber_entries(sweep: Sweep):
    """Per-entry arrays in Labber order (inner axis fastest = C order over outer-first axes)."""
    n = sweep.n_entries
    grids = np.meshgrid(*[a.values for a in sweep.axes], indexing="ij") if sweep.axes else []
    steps = {a.name: g.reshape(-1) for a, g in zip(sweep.axes, grids)}
    logs = {}
    for c in sweep.channels:
        logs[c.name] = c.data.reshape((n, len(c.x))) if c.vector else c.data.reshape(n)
    ts = sweep.timestamps.reshape(n) if sweep.timestamps is not None else None
    return n, steps, logs, ts


def _step_channels(sweep: Sweep) -> list[dict]:
    """Labber Step list: the API's own "Step index API", the swept axes inner first, then fixed settings."""
    rows = [dict(name=STEP_INDEX, unit="", values=np.array([0.0]))]
    rows += [dict(name=a.name, unit=a.unit, values=np.asarray(a.values, dtype=float)) for a in reversed(sweep.axes)]
    taken = {r["name"] for r in rows} | {c.name for c in sweep.channels}
    for name, unit, value in sweep.fixed:
        if name not in taken:
            rows.append(dict(name=name, unit=unit, values=np.array([float(value)])))
            taken.add(name)
    return rows


def write_labber_builtin(sweep: Sweep, out_path: str | Path) -> Path:
    import h5py

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    S = h5py.string_dtype()
    n, step_entries, log_entries, ts = _labber_entries(sweep)
    steps = _step_channels(sweep)
    for r in steps:
        if r["name"] not in step_entries:
            step_entries[r["name"]] = np.full(n, float(r["values"][0]))
    scalar_logs = [c for c in sweep.channels if not c.vector]
    vector_logs = [c for c in sweep.channels if c.vector]
    dims = [len(r["values"]) for r in steps]
    now = time.time()
    tmp = out_path.with_name(out_path.name + ".part")

    def empty():
        return np.zeros(0, dtype=float)

    def strings(values):
        return np.array([str(v) for v in values], dtype=S)

    with h5py.File(tmp, "w") as f:
        f.attrs.update({
            "Step dimensions": np.array(dims, dtype=np.int64), "arm_trig_mode": False,
            "comment": sweep.comment or "", "creation_time": float(sweep.created or now), "hardware_loop": False,
            "log_name": sweep.name, "log_parallel": True, "logger_mode": False, "star": bool(sweep.star),
            "time_per_point": 0.0, "trig_channel": "", "version": LABBER_VERSION, "wait_between": 0.0,
        })
        # /Channels — master registry
        ch_dt = np.dtype([("name", S), ("instrument", S), ("quantity", S), ("unitPhys", S), ("unitInstr", S),
                          ("gain", "<f8"), ("offset", "<f8"), ("amp", "<f8"), ("highLim", "<f8"), ("lowLim", "<f8"),
                          ("outputChannel", S), ("limit_action", S), ("limit_run_script", "?"),
                          ("limit_script", S), ("use_log_interval", "?"), ("log_interval", "<f8"),
                          ("limit_run_always", "?")])
        rows = [(r["name"], STEP_INSTR, r["name"], r["unit"], r["unit"], 1.0, 0.0, 1.0, np.inf, -np.inf, "",
                 "Continue", False, "", False, 10.0, False) for r in steps]
        rows += [(c.name, LOG_INSTR, c.name, c.unit, c.unit, 1.0, 0.0, 1.0, np.inf, -np.inf, "",
                  "Continue", False, "", False, 10.0, False) for c in sweep.channels]
        f.create_dataset("Channels", data=np.array(rows, dtype=ch_dt))
        # /Instruments — the two generic instruments the API creates
        in_dt = np.dtype([("hardware", S), ("version", S), ("id", S), ("model", S), ("name", S),
                          ("interface", "<i2"), ("address", S), ("server", S), ("startup", "<i2"), ("lock", "?"),
                          ("show_advanced", "?"), ("Timeout", "<f8"), ("Term. character", S),
                          ("Send end on write", "?"), ("Lock VISA resource", "?"),
                          ("Suppress end bit termination on read", "?"), ("Use specific TCP port", "?"),
                          ("TCP port", "<f8"), ("Use VICP protocol", "?"), ("Baud rate", "<f8"),
                          ("Data bits", "<f8"), ("Stop bits", "<f8"), ("Parity", S), ("GPIB board number", "<f8"),
                          ("Send GPIB go to local at close", "?"), ("PXI chassis", "<f8"),
                          ("Run in 32-bit mode", "?")])
        inst_rows = [("Generic", "1.0", "", "", nm, 0, "", "localhost", 0, False, False, 10.0, "Auto", True,
                      False, False, False, 0.0, False, 9600.0, 8.0, 1.0, "No parity", 0.0, False, 1.0, False)
                     for nm in ("Step channels", "Log channels")]
        f.create_dataset("Instruments", data=np.array(inst_rows, dtype=in_dt))
        # /Step list + /Step config
        sl_dt = np.dtype([("channel_name", S), ("step_unit", "<i2"), ("wait_after", "<f8"), ("after_last", "<i2"),
                          ("final_value", "<f8"), ("use_relations", "?"), ("equation", S), ("show_advanced", "?"),
                          ("sweep_mode", "<i2"), ("use_outside_sweep_rate", "?"), ("sweep_rate_outside", "<f8"),
                          ("alternate_direction", "?")])
        f.create_dataset("Step list", data=np.array(
            [(r["name"], 0, 0.0, 0, 0.0, False, "x", False, 0, False, 0.0, False) for r in steps], dtype=sl_dt))
        it_dt = np.dtype([("range_type", "<i2"), ("step_type", "<i2"), ("single", "<f8"), ("start", "<f8"),
                          ("stop", "<f8"), ("center", "<f8"), ("span", "<f8"), ("step", "<f8"), ("n_pts", "<i4"),
                          ("interp", "<i2"), ("sweep_rate", "<f8")])
        rp_dt = np.dtype([("variable", S), ("channel_name", S), ("use_lookup", "?")])
        cfg = f.create_group("Step config")
        for r in steps:
            v = np.asarray(r["values"], dtype=float)
            lo, hi = float(v[0]), float(v[-1])
            g = cfg.create_group(r["name"])
            span = hi - lo
            step = span / (len(v) - 1) if len(v) > 1 else 0.0
            g.create_dataset("Step items", data=np.array(
                [(1 if len(v) > 1 else 0, 1, lo, lo, hi, (lo + hi) / 2, span, step, len(v), 0, 0.0)], dtype=it_dt))
            g.create_dataset("Relation parameters", data=np.array([("x", r["name"], False)], dtype=rp_dt))
            opt = g.create_group("Optimizer")
            opt.attrs.update({"Enabled": False, "Initial step size": abs(span) / 5 if span else 0.2,
                              "Max value": max(lo, hi) if span else 2.0, "Min value": min(lo, hi) if span else 1.0,
                              "Precision": abs(span) * 1e-4 if span else 1e-4, "Start value": lo})
        # /Log list
        f.create_dataset("Log list", data=np.array([(c.name,) for c in sweep.channels],
                                                   dtype=np.dtype([("channel_name", S)])))
        # /Data — one row per step channel + scalar log channel, every entry its own "trace"
        names = [r["name"] for r in steps] + [c.name for c in scalar_logs]
        matrix = np.zeros((1, len(names), n))
        for k, r in enumerate(steps):
            matrix[0, k] = step_entries[r["name"]]
        for k, c in enumerate(scalar_logs, start=len(steps)):
            matrix[0, k] = np.asarray(log_entries[c.name], dtype=float)
        data = f.create_group("Data")
        data.create_dataset("Data", data=matrix)
        data.create_dataset("Channel names", data=np.array([(nm, "") for nm in names],
                                                           dtype=np.dtype([("name", S), ("info", S)])))
        stamps = ts if ts is not None else np.full(n, float(sweep.created or now))
        data.create_dataset("Time stamp", data=np.asarray(stamps, dtype=float))
        data.attrs.update({"Completed": bool(sweep.complete), "Entries, last trace": 1,
                           "Fixed step index": empty(), "Fixed step values": empty(),
                           "Step dimensions": np.array(dims, dtype=np.int64),
                           "Step index": np.arange(len(steps), dtype=np.int64)})
        # /Traces
        traces = f.create_group("Traces")
        traces.create_dataset("Time stamp", data=np.asarray(stamps, dtype=float))
        for c in vector_logs:
            vals = log_entries[c.name]                                     # (n, n_points)
            if np.iscomplexobj(vals):
                arr = np.stack([vals.real.T, vals.imag.T], axis=1)         # (n_points, 2, n)
            else:
                arr = np.asarray(vals, dtype=float).T[:, None, :]          # (n_points, 1, n)
            d = traces.create_dataset(c.name, data=arr, compression="gzip", compression_opts=4, chunks=True)
            d.attrs.update({"complex": bool(np.iscomplexobj(vals)), "x, name": c.x_name or "x",
                            "x, unit": c.x_unit or ""})
            x = np.asarray(c.x, dtype=float)
            traces.create_dataset(c.name + "_N", data=np.array([len(x)], dtype=np.int32))
            dx = float(x[1] - x[0]) if len(x) > 1 else 0.0
            traces.create_dataset(c.name + "_t0dt", data=np.array([[float(x[0]) if len(x) else 0.0, dx]]))
            if not _uniform(x):
                traces.create_dataset(c.name + "_xvalues", data=x)    # LabLogViewer reads the exact axis
        # /Tags, /Settings
        tags = f.create_group("Tags")
        tags.attrs["Project"] = strings([sweep.project or ""])
        tags.attrs["Tags"] = strings(sweep.tags) if sweep.tags else np.array([], dtype=S)
        tags.attrs["User"] = strings([sweep.user or ""])
        f.create_group("Settings")
        # /Instrument config
        ic = f.create_group("Instrument config")
        g = ic.create_group(STEP_INSTR)
        g.attrs["Installed options"] = empty()
        for r in steps:
            g.attrs[r["name"]] = 0.0
        g = ic.create_group(LOG_INSTR)
        g.attrs["Installed options"] = empty()
        for c in sweep.channels:
            if c.vector:
                g.attrs[c.name] = empty()
                g.attrs[f"___{c.name}___x_name"] = c.x_name or "x"
                g.attrs[f"___{c.name}___x_unit"] = c.x_unit or ""
            else:
                g.attrs[c.name] = 0.0
        for inst, settings in sweep.instrument_config.items():
            name = inst.replace("/", "∕")
            if name in ic:
                continue
            g = ic.create_group(name)
            for k, v in settings.items():
                try:
                    g.attrs[k.replace("/", "∕")] = v
                except Exception:  # noqa: BLE001 - an odd setting is not worth failing the file
                    pass
    tmp.replace(out_path)
    return out_path


# ---- Sweep → Labber (Labber API in its own Python) -------------------------------------------------
def write_spec(sweep: Sweep, path: str | Path) -> None:
    """The neutral spec read by labber_api_script.py (plain h5py + JSON, nothing LabLogViewer-specific)."""
    import h5py

    n, step_entries, log_entries, ts = _labber_entries(sweep)
    steps = _step_channels(sweep)[1:]                     # the API adds its own "Step index API"
    for r in steps:
        if r["name"] not in step_entries:
            step_entries[r["name"]] = np.full(n, float(r["values"][0]))
    with h5py.File(path, "w") as f:
        f.attrs["meta"] = json.dumps(dict(
            log_name=sweep.name, project=sweep.project, user=sweep.user, tags=sweep.tags,
            comment=sweep.comment, star=bool(sweep.star), n=n,
            steps=[dict(name=r["name"], unit=r["unit"]) for r in steps],
            logs=[dict(name=c.name, unit=c.unit, vector=c.vector, complex=bool(c.is_complex),
                       x_name=c.x_name, x_unit=c.x_unit) for c in sweep.channels],
            instrument_config=sweep.instrument_config), ensure_ascii=False)
        for k, r in enumerate(steps):
            f.create_dataset(f"step_values/{k}", data=np.asarray(r["values"], dtype=float))
            f.create_dataset(f"step_entries/{k}", data=np.asarray(step_entries[r["name"]], dtype=float))
        for k, c in enumerate(sweep.channels):
            f.create_dataset(f"log_data/{k}", data=log_entries[c.name])
            if c.vector:
                f.create_dataset(f"log_x/{k}", data=np.asarray(c.x, dtype=float))


_API_OK: dict[str, bool] = {}


def labber_python_candidates(configured: str = "") -> list[str]:
    out = []
    for p in (configured, os.environ.get("LABLOGVIEWER_LABBER_PYTHON", ""),
              os.environ.get("LABCONTROL_LABBER_PYTHON", ""), DEFAULT_LABBER_PYTHON):
        if p and p not in out:
            out.append(p)
    return out


def check_labber_python(python: str, timeout: float = 40.0) -> bool:
    """True when ``python`` can ``import Labber`` (cached per path for this session)."""
    if not python or not Path(python).exists():
        return False
    if python in _API_OK:
        return _API_OK[python]
    code = ("import os,sys\n"
            "for p in (r'C:\\Program Files\\Keysight\\Labber\\Script', r'C:\\Program Files\\Labber\\Script'):\n"
            "    os.path.exists(p) and sys.path.insert(0, p)\n"
            "import Labber\nprint('LABBER_OK')")
    try:
        res = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=timeout,
                             **_no_window())
        ok = "LABBER_OK" in (res.stdout or "")
    except Exception:  # noqa: BLE001
        ok = False
    _API_OK[python] = ok
    return ok


def find_labber_python(configured: str = "") -> str:
    for p in labber_python_candidates(configured):
        if check_labber_python(p):
            return p
    return ""


def _no_window() -> dict:
    if sys.platform.startswith("win"):
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def write_labber_api(sweep: Sweep, out_path: str | Path, python: str, timeout: float = 900.0) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, spec = tempfile.mkstemp(suffix=".spec.h5", prefix="llv_labber_")
    os.close(fd)
    tmp = out_path.with_name(out_path.stem + ".part" + out_path.suffix)
    try:
        write_spec(sweep, spec)
        res = subprocess.run([python, str(SCRIPT), spec, str(tmp)], capture_output=True, text=True,
                             timeout=timeout, **_no_window())
        if res.returncode != 0 or "SUCCESS" not in (res.stdout or ""):
            raise ConvertError("Labber API failed:\n" + ((res.stdout or "") + (res.stderr or ""))[-1500:])
        tmp.replace(out_path)
        return out_path
    finally:
        for p in (spec, tmp):
            try:
                os.remove(p)
            except OSError:
                pass


# ---- Sweep → Lab Control .nc --------------------------------------------------------------------------
def _var_name(name: str, used: set) -> str:
    base = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name).strip("_") or "var"
    if base[0].isdigit():
        base = "v_" + base
    out, k = base, 2
    while out in used:
        out, k = f"{base}_{k}", k + 1
    used.add(out)
    return out


def write_nc(sweep: Sweep, out_path: str | Path, source: str = "") -> Path:
    """Lab Control NetCDF (schema labcontrol-nc/1, grid layout) — opens in Lab Control, xarray and here."""
    import warnings

    import h5netcdf

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".part")
    used: set = {"timestamp", "retained"}
    sizes = sweep.sizes
    created = datetime.fromtimestamp(sweep.created or time.time(), tz=timezone.utc).astimezone()
    snapshot = dict(sweep.instrument_config)
    if sweep.fixed:
        snapshot["Step channels"] = {name: value for name, _unit, value in sweep.fixed}
    meta = {"snapshot": snapshot, "source": {"format": "labber", "file": Path(source).name if source else ""}}
    has_complex = any(c.is_complex for c in sweep.channels)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with h5netcdf.File(tmp, "w", invalid_netcdf=has_complex) as f:
            f.attrs.update({"schema": "labcontrol-nc/1", "name": sweep.name, "created": created.isoformat(),
                            "user": sweep.user, "tags": ", ".join(sweep.tags), "comment": sweep.comment,
                            "project": sweep.project, "project_id": "", "points": int(sweep.n_entries),
                            "planned_points": int(sweep.n_entries), "complete": int(bool(sweep.complete)),
                            "layout": "grid", "labcontrol": json.dumps(meta, ensure_ascii=False)})
            if sweep.star:
                f.attrs["star"] = 1
            dims: list[str] = []
            for a in sweep.axes:
                d = _var_name(a.name, used)
                dims.append(d)
                f.dimensions[d] = len(a.values)
                v = f.create_variable(d, (d,), data=np.asarray(a.values, dtype=float))
                v.attrs.update({"units": a.unit, "long_name": a.name, "target": a.name,
                                "si_units": a.unit, "si_scale": 1.0})
            x_dims: dict[tuple, str] = {}
            for c in sweep.channels:
                cdims = tuple(dims)
                if c.vector:
                    key = (c.x_name, np.asarray(c.x, dtype=float).tobytes())
                    if key not in x_dims:
                        xd = _var_name(c.x_name or "x", used)
                        x_dims[key] = xd
                        f.dimensions[xd] = len(c.x)
                        xv = f.create_variable(xd, (xd,), data=np.asarray(c.x, dtype=float))
                        xv.attrs.update({"units": c.x_unit, "long_name": c.x_name or "x"})
                    cdims = cdims + (x_dims[key],)
                data = np.asarray(c.data, dtype=complex if c.is_complex else float)
                v = f.create_variable(_var_name(c.name, used), cdims, data=data)
                v.attrs.update({"units": c.unit, "long_name": c.name, "source": c.instrument, "channel": c.name})
            if sweep.timestamps is not None and dims:
                v = f.create_variable("timestamp", tuple(dims), data=np.asarray(sweep.timestamps, dtype=float))
                v.attrs.update({"units": "s", "long_name": "量測時間（Unix 秒）"})
    tmp.replace(out_path)
    return out_path


# ---- one call per file ---------------------------------------------------------------------------------
@dataclass
class Result:
    source: str
    output: str = ""
    engine: str = ""
    error: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error


def convert_file(src: str | Path, to: str, folder: str | Path | None = None, engine: str = "auto",
                 labber_python: str = "") -> Result:
    """Convert one file. ``to`` is "hdf5" (Labber) or "nc" (Lab Control). Never overwrites."""
    src = str(src)
    result = Result(src)
    try:
        sweep = load_sweep(src)
        result.notes = list(sweep.notes)
        if not sweep.complete:
            result.notes.append("Partial measurement: only the recorded points were converted.")
        dst = target_path(src, to, folder)
        if to == "nc":
            write_nc(sweep, dst, source=src)
            result.engine = "netcdf"
        else:
            python = ""
            if engine in ("auto", "api"):
                python = labber_python if engine == "api" and labber_python else find_labber_python(labber_python)
                if engine == "api" and not check_labber_python(python):
                    raise ConvertError("Labber Python not found (it must be able to import Labber).")
            if python:
                write_labber_api(sweep, dst, python)
                result.engine = "api"
            else:
                write_labber_builtin(sweep, dst)
                result.engine = "builtin"
        result.output = str(dst)
    except ConvertError as error:
        result.error = str(error)
    except Exception as error:  # noqa: BLE001 - shown per file, the batch goes on
        result.error = f"{type(error).__name__}: {error}"
    return result


def convert_files(paths, to: str, folder=None, engine: str = "auto", labber_python: str = "",
                  progress: Callable[[int, int, str], None] | None = None,
                  cancelled: Callable[[], bool] | None = None) -> list[Result]:
    out = []
    paths = list(paths)
    for k, p in enumerate(paths):
        if cancelled is not None and cancelled():
            break
        if progress is not None:
            progress(k, len(paths), str(p))
        out.append(convert_file(p, to, folder, engine, labber_python))
    if progress is not None:
        progress(len(paths), len(paths), "")
    return out
