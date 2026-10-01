#!/usr/bin/env python3
"""
test_parser.py — Phase 4 CLI test tool.

Loads a real Labber HDF5 file through the full stack:

    HDF5 File -> HDF5Reader -> AutoDetector/LabberParserV2 -> Experiment
    -> ChannelManager

...and prints a human-readable Experiment Summary, plus does a real
lazy data read from each log channel (including a slice of a vector
trace) to prove get_data() actually works end-to-end, not just that
parsing succeeds.

Usage:
    python test_parser.py sample.hdf5
    python test_parser.py sample.hdf5 --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# allow running this script directly from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

from app.core.hdf5_reader import HDF5Reader
from app.core.labber_parser import AutoDetector, UnsupportedLabberFormat
from app.core.channel_manager import ChannelManager
from app.core.cache import CachedExperiment, LRUDataCache


def _jsonable(v):
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (np.generic,)):
        return v.item()
    return v


def summarize(path: str) -> dict:
    reader = HDF5Reader(path)
    try:
        experiment = AutoDetector.detect_and_parse(reader)
    except UnsupportedLabberFormat as e:
        print(f"UNSUPPORTED FORMAT: {e}")
        print("Falling back conceptually to Raw HDF5 Explorer Mode (not yet implemented).")
        reader.close()
        return {"error": str(e)}

    mgr = ChannelManager(experiment)
    cached = CachedExperiment(experiment, LRUDataCache())

    print("=" * 78)
    print(f"Experiment Summary — {Path(path).name}")
    print("=" * 78)
    print(f"Log name        : {experiment.log_name}")
    print(f"Format variant  : {experiment.format_variant}")
    print(f"Project         : {experiment.project}")
    print(f"User            : {experiment.user}")
    print(f"Tags            : {experiment.tags}")
    print(f"Comment         : {experiment.comment!r}")
    print(f"Labber version  : {experiment.version}")
    print()

    print("Step Channels:")
    for c in mgr.list_step_channels():
        active = c.name in mgr.list_active_sweep_axes()
        print(f"  - {c.name}  [{c.unit or '-'}]  {'ACTIVE SWEEP' if active else 'fixed'}")
    print()

    print("Log Channels:")
    for c in mgr.list_log_channels():
        kind = "vector/complex" if c.is_vector else "scalar"
        print(f"  - {c.name}  [{c.unit or '-'}]  ({kind}"
              f"{', N=' + str(c.n_points) if c.n_points else ''})")
    print()

    report = mgr.sweep_dimension_report()
    print("Sweep Dimensions:")
    for name, n in report["detail"].items():
        print(f"  - {name}: {n} points")
    print(f"  Active step dims : {report['n_active_step_dims']}")
    print(f"  Has vector log ch: {report['has_vector_log_channel']}")
    print(f"  Total dimensions : {report['total_dimensions']}  "
          f"(plotting-relevant: step axes + trace's own axis if any)")
    print()

    print("Complex Channels:")
    complex_found = False
    for name in experiment.vector_traces:
        if mgr.is_complex(name):
            complex_found = True
            vt = experiment.vector_traces[name]
            print(f"  - {name}: complex=True, x-axis='{vt.x_name}' [{vt.x_unit}], "
                  f"{vt.n_points} pts, {vt.n_entries} sweep entries")
    if not complex_found:
        print("  (none)")
    print()

    print("Data Shape:")
    if experiment.metadata_tree.get("scalar_data_matrix"):
        print(f"  Scalar matrix (/Data/Data): {experiment.metadata_tree['scalar_data_matrix']['shape']}")
    for name, vt in experiment.vector_traces.items():
        print(f"  Vector trace '{name}' (/Traces): points={vt.n_points}, entries={vt.n_entries}")
    print()

    print("Metadata (Instrument Config):")
    for inst_name, cfg in experiment.instrument_config.items():
        print(f"  [{inst_name}]")
        shown = 0
        for k, v in cfg.items():
            print(f"    {k} = {v}")
            shown += 1
            if shown >= 6:
                remaining = len(cfg) - shown
                if remaining > 0:
                    print(f"    ... ({remaining} more)")
                break
    print()

    # ---- prove get_data() actually works end-to-end -----------------------
    print("Live data-read verification (via CachedExperiment.get_data):")
    for name in experiment.log_channel_names:
        try:
            if name in experiment.vector_traces:
                vt = experiment.vector_traces[name]
                # read just the first sweep entry, magnitude_db transform,
                # to prove lazy slicing works without loading the whole
                # (possibly 80MB+) trace dataset. entry_slice always
                # refers to the sweep-entries axis only.
                entry_slice = slice(0, 1) if vt.n_entries > 1 else None
                data = cached.get_data(name, transform="magnitude_db", entry_slice=entry_slice)
                print(f"  {name}: magnitude_db slice shape={np.asarray(data).shape}, "
                      f"first value={np.asarray(data).flatten()[0]:.3f} dB, "
                      f"x-axis[0]={vt.x_values[0]:.6g} {vt.x_unit}, "
                      f"x-axis[-1]={vt.x_values[-1]:.6g} {vt.x_unit}")
                # second call should be a cache hit
                data2 = cached.get_data(name, transform="magnitude_db", entry_slice=entry_slice)
                assert np.allclose(np.asarray(data), np.asarray(data2), equal_nan=True)
            else:
                data = cached.get_data(name, transform="raw")
                print(f"  {name}: scalar series shape={np.asarray(data).shape}, "
                      f"first value={np.asarray(data).flatten()[0]}")
        except Exception as e:
            print(f"  {name}: FAILED to read — {e}")
    print(f"  cache stats: {cached.cache.stats()}")
    print()

    result = {
        "log_name": experiment.log_name,
        "format_variant": experiment.format_variant,
        "step_channels": [c.name for c in mgr.list_step_channels()],
        "log_channels": [c.name for c in mgr.list_log_channels()],
        "sweep_dimensions": report["detail"],
        "total_dimensions": report["total_dimensions"],
        "complex_channels": [n for n in experiment.vector_traces if mgr.is_complex(n)],
        "scalar_data_shape": experiment.metadata_tree["scalar_data_matrix"]["shape"]
            if experiment.metadata_tree.get("scalar_data_matrix") else None,
        "cache_stats": cached.cache.stats(),
    }

    experiment.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the Labber parser stack against a real HDF5 file.")
    parser.add_argument("path")
    parser.add_argument("--json", metavar="OUT", default=None)
    args = parser.parse_args()

    result = summarize(args.path)

    if args.json:
        if args.json == "-":
            print(json.dumps(result, indent=2, default=_jsonable))
        else:
            Path(args.json).write_text(json.dumps(result, indent=2, default=_jsonable))
            print(f"JSON summary written to: {args.json}")

    return 0 if "error" not in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
