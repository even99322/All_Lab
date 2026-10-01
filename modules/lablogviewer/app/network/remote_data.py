"""Measurement structure over the network, without re-parsing HDF5.

Host: :func:`describe_experiment` turns the Unified Data Model that the
existing Labber parser already produced (channels, active step axes, vector
traces, instrument config, metadata, Empty / Function / Scalar semantics as
parsed) into plain values. Nothing is re-derived from HDF5 here.

Client: :func:`build_remote_experiment` rebuilds the same ``Experiment``
object around a :class:`RemoteReader`. ``Experiment`` reads raw data only via
``_reader.read(path, slice_)``, so every existing consumer — Viewer, the
Transform layer (Real / Imag / Magnitude / Phase / dB / Unwrap on the raw
complex values), YIG — works unchanged, while raw values come from the Host.
The GUI never touches h5py. The Host's absolute file path is never sent.
"""

from __future__ import annotations

import threading
from dataclasses import fields
from typing import Callable

import numpy as np

from app.core.data_model import ChannelInfo, Experiment, StepAxis, VectorTraceInfo
from app.network.protocol import slice_key

REMOTE_SCHEME = "lablogviewer-remote://"
STRUCTURE_VERSION = 1


def _channel(info: ChannelInfo) -> dict:
    return {f.name: (list(getattr(info, f.name)) if f.name == "shape" else getattr(info, f.name))
            for f in fields(ChannelInfo)}


def describe_experiment(experiment: Experiment) -> dict:
    """Plain-value description of a parsed measurement (no file path, no reader)."""
    return {
        "structure_version": STRUCTURE_VERSION,
        "log_name": experiment.log_name,
        "file_name": str(experiment.source_path).replace("\\", "/").rsplit("/", 1)[-1],
        "creation_time": experiment.creation_time,
        "comment": experiment.comment,
        "project": experiment.project,
        "tags": list(experiment.tags or []),
        "user": experiment.user,
        "version": experiment.version,
        "channels": [_channel(info) for info in experiment.channels.values()],
        "step_axes": [{"channel": axis.channel.name, "values": np.asarray(axis.values),
                       "dim_index": int(axis.dim_index)} for axis in experiment.step_axes],
        "log_channel_names": list(experiment.log_channel_names),
        "vector_traces": [{
            "channel": name, "x_name": trace.x_name, "x_unit": trace.x_unit,
            "x_values": np.asarray(trace.x_values), "n_points": int(trace.n_points),
            "n_entries": int(trace.n_entries), "trace_path": trace.trace_path, "complex": bool(trace.complex),
        } for name, trace in experiment.vector_traces.items()],
        "instrument_config": experiment.instrument_config,
        "metadata_tree": experiment.metadata_tree,
        "format_variant": experiment.format_variant,
    }


class RemoteReaderError(OSError):
    pass


class RemoteReader:
    """Stands in for HDF5Reader on the Client: serves prefetched raw arrays.

    ``fetch(path, slice_)`` is supplied by the client connection; it is only
    used for a read that was not prefetched (the mirror normally prefetches
    exactly the reads the Host performed, so the GUI thread does not wait).
    """

    def __init__(self, fetch: Callable[[str, object], np.ndarray] | None = None):
        self._fetch = fetch
        self._cache: dict[str, np.ndarray] = {}
        self._lock = threading.Lock()
        self._closed = False

    def put(self, path: str, slice_, array: np.ndarray) -> None:
        with self._lock:
            self._cache[slice_key(path, slice_)] = array

    def has(self, path: str, slice_) -> bool:
        with self._lock:
            return slice_key(path, slice_) in self._cache

    def cached_bytes(self) -> int:
        with self._lock:
            return sum(array.nbytes for array in self._cache.values())

    def read(self, path: str, slice_=None) -> np.ndarray:
        if self._closed:
            raise RemoteReaderError("The remote measurement is closed.")
        key = slice_key(path, slice_)
        with self._lock:
            array = self._cache.get(key)
            if array is None and slice_ is not None:
                full = self._cache.get(slice_key(path, None))
                if full is not None:
                    array = full[slice_]
        if array is not None:
            return array
        if self._fetch is None:
            raise RemoteReaderError(f"Remote data {path} is not available.")
        array = self._fetch(path, slice_)
        self.put(path, slice_, array)
        return array

    def close(self) -> None:
        """Drop every received array: remote data lives only in memory."""
        with self._lock:
            self._cache.clear()
        self._closed = True

    # Experiment / debackground expect these on a reader; remote data is read-only.
    def kind_of(self, path: str):
        return "dataset" if any(key.startswith(f'["{path}"') for key in self._cache) else None


def build_remote_experiment(description: dict, reader: RemoteReader, identity: str) -> Experiment:
    """Client-side Experiment identical in structure to the Host's parsed one."""
    if description.get("structure_version") != STRUCTURE_VERSION:
        raise ValueError("Unsupported measurement structure version.")
    channels = {}
    for raw in description["channels"]:
        values = {f.name: raw.get(f.name) for f in fields(ChannelInfo) if f.name in raw}
        values["shape"] = tuple(values.get("shape") or ())
        channels[values["name"]] = ChannelInfo(**values)
    step_axes = [StepAxis(channel=channels[axis["channel"]], values=np.asarray(axis["values"]),
                          dim_index=int(axis["dim_index"])) for axis in description["step_axes"]]
    vector_traces = {}
    for raw in description["vector_traces"]:
        vector_traces[raw["channel"]] = VectorTraceInfo(
            channel=channels[raw["channel"]], x_name=raw["x_name"], x_unit=raw["x_unit"],
            x_values=np.asarray(raw["x_values"]), n_points=int(raw["n_points"]),
            n_entries=int(raw["n_entries"]), trace_path=raw["trace_path"], complex=bool(raw["complex"]),
        )
    return Experiment(
        log_name=description["log_name"],
        source_path=f"{REMOTE_SCHEME}{identity}/{description.get('file_name') or 'measurement.hdf5'}",
        creation_time=description.get("creation_time"),
        comment=description.get("comment") or "",
        project=description.get("project"),
        tags=list(description.get("tags") or []),
        user=description.get("user"),
        version=description.get("version"),
        channels=channels,
        step_axes=step_axes,
        log_channel_names=list(description["log_channel_names"]),
        vector_traces=vector_traces,
        instrument_config=description.get("instrument_config") or {},
        metadata_tree=description.get("metadata_tree") or {},
        format_variant=description.get("format_variant") or "remote",
        _reader=reader,
    )


def is_remote(experiment) -> bool:
    return str(getattr(experiment, "source_path", "")).startswith(REMOTE_SCHEME)


class RecordingReader:
    """Host side: wraps the real reader and records which (path, slice) the
    Viewer reads while drawing, so Clients can prefetch exactly those."""

    def __init__(self, inner):
        self._inner = inner
        self._lock = threading.Lock()
        self._reads: dict[str, tuple[str, object]] = {}

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def read(self, path: str, slice_=None):
        with self._lock:
            self._reads[slice_key(path, slice_)] = (path, slice_)
        return self._inner.read(path, slice_=slice_)

    def take_reads(self) -> list[tuple[str, object]]:
        with self._lock:
            reads, self._reads = list(self._reads.values()), {}
        return reads
