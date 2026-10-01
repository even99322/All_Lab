#!/usr/bin/env python3
"""
inspect_hdf5.py — Labber HDF5 log structure inspector.

Phase 1 tool for the LabLogViewer project.

Purpose
-------
Given a real Labber-produced .hdf5 log file, this tool:

  1. Recursively walks every group/dataset in the file.
  2. Prints a terminal tree with shape / dtype / attributes.
  3. Attempts to identify Labber-specific structures:
       - step channels        (Step list / Step config / Channels)
       - log channels         (Log list)
       - scalar log matrix    (Data/Data + Data/Channel names)
       - vector/trace channels (Traces/<name>, Traces/<name>_t0dt, _N)
       - instrument configuration (Instrument config/*)
       - sweep dimensionality (Data.attrs['Step dimensions'])
       - number of completed points (Data.attrs['Entries, last trace'] /
         'Completed')
  4. Writes a machine-readable JSON summary next to the input file
     (or to a path given with --json).

This tool makes NO assumption that all Labber files share one fixed
layout. Every piece of information it reports is derived by *reading*
the actual file's groups, datasets and attributes — nothing is
hardcoded to a specific sample file. Two structurally different real
Labber files (a single-point "vector-only" log and a swept 2D log with
a vector log-channel) were used during development specifically to
avoid overfitting to one layout; see docs/hdf5_structure_report.md.

Usage
-----
    python inspect_hdf5.py file.hdf5
    python inspect_hdf5.py file.hdf5 --json out.json
    python inspect_hdf5.py file.hdf5 --no-tree --json -   # JSON to stdout
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import h5py
import numpy as np


# --------------------------------------------------------------------------
# JSON-safety helpers
# --------------------------------------------------------------------------

def _jsonable(value: Any) -> Any:
    """Best-effort conversion of h5py/numpy attribute values to JSON-safe
    Python types. Never raises: falls back to str() for anything exotic."""
    try:
        if isinstance(value, (bytes, np.bytes_)):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            if value.dtype.kind in "SU":
                return [
                    v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)
                    for v in value.tolist()
                ]
            if value.size > 200:
                return {
                    "__truncated_array__": True,
                    "shape": list(value.shape),
                    "dtype": str(value.dtype),
                    "preview": value.flatten()[:20].tolist(),
                }
            return value.tolist()
        if isinstance(value, (list, tuple)):
            return [_jsonable(v) for v in value]
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        json.dumps(value)  # will raise for non-serializable
        return value
    except Exception:
        return str(value)


def _attrs_dict(obj) -> dict:
    return {str(k): _jsonable(v) for k, v in obj.attrs.items()}


# --------------------------------------------------------------------------
# Tree walking
# --------------------------------------------------------------------------

@dataclass
class NodeInfo:
    path: str
    kind: str  # "group" | "dataset"
    shape: tuple | None = None
    dtype: str | None = None
    attrs: dict = field(default_factory=dict)
    fields: list[str] | None = None  # compound dtype field names, if any


def walk_file(f: h5py.File) -> list[NodeInfo]:
    nodes: list[NodeInfo] = []

    root = NodeInfo(path="/", kind="group", attrs=_attrs_dict(f))
    nodes.append(root)

    def visitor(name: str, obj):
        path = "/" + name
        if isinstance(obj, h5py.Dataset):
            fields = list(obj.dtype.names) if obj.dtype.names else None
            nodes.append(
                NodeInfo(
                    path=path,
                    kind="dataset",
                    shape=obj.shape,
                    dtype=str(obj.dtype),
                    attrs=_attrs_dict(obj),
                    fields=fields,
                )
            )
        else:
            nodes.append(NodeInfo(path=path, kind="group", attrs=_attrs_dict(obj)))

    f.visititems(visitor)
    return nodes


def print_tree(nodes: list[NodeInfo]) -> None:
    print(f"{'PATH':60s} {'KIND':8s} {'SHAPE':18s} DTYPE / FIELDS")
    print("-" * 110)
    for n in nodes:
        depth = n.path.count("/") - (1 if n.path == "/" else 0)
        indent = "  " * max(depth, 0)
        label = "/" if n.path == "/" else n.path.rsplit("/", 1)[-1]
        shape_str = str(n.shape) if n.shape is not None else ""
        dtype_str = ""
        if n.fields:
            dtype_str = "compound: " + ", ".join(n.fields)
        elif n.dtype:
            dtype_str = n.dtype
        line = f"{indent}{label}"
        print(f"{line:60s} {n.kind:8s} {shape_str:18s} {dtype_str}")
        for k, v in n.attrs.items():
            vs = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
            if len(vs) > 100:
                vs = vs[:100] + "..."
            print(f"{indent}    @{k} = {vs}")


# --------------------------------------------------------------------------
# Labber-specific semantic analysis
# --------------------------------------------------------------------------

def analyze_labber_structure(f: h5py.File) -> dict:
    """Derive a Labber-semantic summary purely from what is actually present
    in this file. Every lookup is defensive (uses .get / 'in' checks) so the
    tool degrades gracefully on files that don't match this layout, instead
    of raising."""

    summary: dict[str, Any] = {
        "log_name": None,
        "format_variant": "unknown",
        "root_attrs": _attrs_dict(f),
        "channels_master_list": [],
        "step_channels": [],
        "log_channels": [],
        "scalar_data_matrix": None,
        "vector_trace_channels": [],
        "instrument_config": {},
        "sweep": {},
        "tags": {},
        "warnings": [],
    }

    root_attrs = summary["root_attrs"]
    summary["log_name"] = root_attrs.get("log_name")

    # ---- Channels master table ------------------------------------------
    if "Channels" in f and isinstance(f["Channels"], h5py.Dataset):
        try:
            rows = f["Channels"][:]
            for row in rows:
                summary["channels_master_list"].append(
                    {
                        "name": _jsonable(row["name"]),
                        "instrument": _jsonable(row["instrument"]),
                        "quantity": _jsonable(row["quantity"]) if "quantity" in row.dtype.names else None,
                        "unitPhys": _jsonable(row["unitPhys"]) if "unitPhys" in row.dtype.names else None,
                    }
                )
        except Exception as e:
            summary["warnings"].append(f"Failed to read /Channels: {e}")
    else:
        summary["warnings"].append("No /Channels dataset found (unexpected for a Labber file).")

    # ---- Step list / step channels ---------------------------------------
    if "Step list" in f and isinstance(f["Step list"], h5py.Dataset):
        try:
            for row in f["Step list"][:]:
                summary["step_channels"].append(_jsonable(row["channel_name"]))
        except Exception as e:
            summary["warnings"].append(f"Failed to read /Step list: {e}")

    # ---- Log list / log channels ------------------------------------------
    if "Log list" in f and isinstance(f["Log list"], h5py.Dataset):
        try:
            for row in f["Log list"][:]:
                summary["log_channels"].append(_jsonable(row["channel_name"]))
        except Exception as e:
            summary["warnings"].append(f"Failed to read /Log list: {e}")

    # ---- Data/Data scalar matrix -------------------------------------------
    if "Data" in f and isinstance(f["Data"], h5py.Group):
        data_grp = f["Data"]
        data_attrs = _attrs_dict(data_grp)
        summary["sweep"] = {
            "step_dimensions": data_attrs.get("Step dimensions"),
            "step_index": data_attrs.get("Step index"),
            "fixed_step_index": data_attrs.get("Fixed step index"),
            "fixed_step_values": data_attrs.get("Fixed step values"),
            "completed": data_attrs.get("Completed"),
            "entries_last_trace": data_attrs.get("Entries, last trace"),
        }
        if "Data" in data_grp and isinstance(data_grp["Data"], h5py.Dataset):
            ds = data_grp["Data"]
            names = []
            if "Channel names" in data_grp and isinstance(data_grp["Channel names"], h5py.Dataset):
                try:
                    names = [_jsonable(r["name"]) for r in data_grp["Channel names"][:]]
                except Exception as e:
                    summary["warnings"].append(f"Failed to read Data/Channel names: {e}")
            summary["scalar_data_matrix"] = {
                "path": "/Data/Data",
                "shape": list(ds.shape),
                "dtype": str(ds.dtype),
                "channel_names": names,
                "note": (
                    "shape is typically (1, n_channels, n_entries): "
                    "n_channels scalar step/log channel values recorded per "
                    "sweep entry. If a log channel is itself a vector/trace "
                    "(see vector_trace_channels below), its data does NOT "
                    "live here — only its scalar step-channel siblings do."
                ),
            }

        # sweep dimensionality
        dims = data_attrs.get("Step dimensions")
        if dims is not None:
            try:
                active_dims = [d for d in dims if d and d > 1]
                summary["sweep"]["active_step_dimensions"] = active_dims
                summary["sweep"]["n_active_dims"] = len(active_dims)
            except Exception:
                pass

    # ---- Traces group (vector / complex log channels) ----------------------
    if "Traces" in f and isinstance(f["Traces"], h5py.Group):
        traces_grp = f["Traces"]
        seen_base_names = set()
        for key in traces_grp.keys():
            if key.endswith("_N") or key.endswith("_t0dt"):
                continue
            if key == "Time stamp":
                continue
            seen_base_names.add(key)

        for name in seen_base_names:
            ds = traces_grp[name]
            if not isinstance(ds, h5py.Dataset):
                continue
            entry = {
                "channel_name": name,
                "path": f"/Traces/{name}",
                "shape": list(ds.shape),
                "dtype": str(ds.dtype),
                "attrs": _attrs_dict(ds),
                "is_complex": bool(_attrs_dict(ds).get("complex", False)),
                "x_axis_name": _attrs_dict(ds).get("x, name"),
                "x_axis_unit": _attrs_dict(ds).get("x, unit"),
            }
            t0dt_key = f"{name}_t0dt"
            n_key = f"{name}_N"
            if t0dt_key in traces_grp:
                t0dt = traces_grp[t0dt_key][:]
                entry["t0dt"] = _jsonable(t0dt)
                entry["x_axis_reconstruction"] = (
                    "x = t0 + dt * arange(N); t0dt stored per-entry as "
                    "[t0, dt] pairs (one row per sweep entry, or a single "
                    "row shared by all entries)."
                )
            if n_key in traces_grp:
                entry["n_points"] = _jsonable(traces_grp[n_key][:])
            summary["vector_trace_channels"].append(entry)

    # ---- format variant classification --------------------------------------
    has_scalar_matrix = summary["scalar_data_matrix"] is not None
    has_vector = len(summary["vector_trace_channels"]) > 0
    if has_vector and has_scalar_matrix:
        summary["format_variant"] = "trace_log_channel"  # e.g. VNA S21 vector
    elif has_scalar_matrix and not has_vector:
        summary["format_variant"] = "scalar_log_channel"
    elif has_vector and not has_scalar_matrix:
        summary["format_variant"] = "vector_only"
    else:
        summary["format_variant"] = "unrecognized"

    # ---- Instrument config ---------------------------------------------------
    if "Instrument config" in f and isinstance(f["Instrument config"], h5py.Group):
        for inst_name, inst_grp in f["Instrument config"].items():
            cfg = dict(_attrs_dict(inst_grp))
            if isinstance(inst_grp, h5py.Group):
                for sub_name, sub_obj in inst_grp.items():
                    if isinstance(sub_obj, h5py.Dataset) and sub_obj.shape == ():
                        cfg[sub_name] = _jsonable(sub_obj[()])
            summary["instrument_config"][inst_name] = cfg

    # ---- Tags -----------------------------------------------------------------
    if "Tags" in f and isinstance(f["Tags"], h5py.Group):
        summary["tags"] = _attrs_dict(f["Tags"])

    return summary


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a Labber HDF5 log file.")
    parser.add_argument("path", help="Path to .hdf5 / .h5 file")
    parser.add_argument("--json", metavar="OUT", default=None,
                         help="Write JSON summary to this path ('-' for stdout). "
                              "Default: <input>.inspect.json next to the input file.")
    parser.add_argument("--no-tree", action="store_true", help="Skip printing the terminal tree")
    parser.add_argument("--no-summary", action="store_true", help="Skip printing the Labber summary")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 1

    try:
        f = h5py.File(path, "r")
    except OSError as e:
        print(f"ERROR: not a readable HDF5 file: {e}", file=sys.stderr)
        return 1

    with f:
        nodes = walk_file(f)
        if not args.no_tree:
            print(f"\n{'=' * 110}\nFILE: {path}\n{'=' * 110}")
            print_tree(nodes)

        summary = analyze_labber_structure(f)

        if not args.no_summary:
            print(f"\n{'-' * 110}\nLABBER STRUCTURE SUMMARY\n{'-' * 110}")
            print(f"log_name          : {summary['log_name']}")
            print(f"format_variant    : {summary['format_variant']}")
            print(f"step_channels     : {summary['step_channels']}")
            print(f"log_channels      : {summary['log_channels']}")
            if summary["scalar_data_matrix"]:
                print(f"scalar_data_matrix: shape={summary['scalar_data_matrix']['shape']} "
                      f"channels={summary['scalar_data_matrix']['channel_names']}")
            for vt in summary["vector_trace_channels"]:
                print(f"vector_trace_channel: {vt['channel_name']} shape={vt['shape']} "
                      f"complex={vt['is_complex']} x={vt['x_axis_name']}[{vt['x_axis_unit']}]")
            print(f"sweep             : {summary['sweep']}")
            if summary["warnings"]:
                print("WARNINGS:")
                for w in summary["warnings"]:
                    print(f"  - {w}")

        json_target = args.json
        if json_target is None:
            json_target = str(path.with_suffix(path.suffix + ".inspect.json"))

        full_report = {
            "file": str(path),
            "nodes": [
                {
                    "path": n.path,
                    "kind": n.kind,
                    "shape": list(n.shape) if n.shape is not None else None,
                    "dtype": n.dtype,
                    "fields": n.fields,
                    "attrs": n.attrs,
                }
                for n in nodes
            ],
            "labber_summary": summary,
        }

        if json_target == "-":
            print(json.dumps(full_report, indent=2, ensure_ascii=False))
        else:
            Path(json_target).write_text(json.dumps(full_report, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"\nJSON summary written to: {json_target}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
