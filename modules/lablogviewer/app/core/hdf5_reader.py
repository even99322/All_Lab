"""
app/core/hdf5_reader.py — Phase 2

Thin, dumb HDF5 I/O layer. Per the architecture in
docs/parser_architecture.md, this is the ONLY module in the codebase
that ever imports h5py directly. It knows nothing about Labber's
schema — it just gives lazy, structural access to any HDF5 file, plus
the tree-walking used by both the Phase 1 inspector tool and the Raw
HDF5 Explorer Mode (spec §22/§23).

Design points:
- Never eagerly loads a full dataset unless `read()` is called
  explicitly (or a caller reduces via a slice).
- `get_dataset()` returns the live h5py.Dataset handle for lazy
  slicing by upstream layers (data_model.py) — callers must not hold
  onto it after the reader is closed.
- Safe to use as a context manager; also safe to leave open for the
  lifetime of an Experiment (data_model.py does this deliberately, so
  that vector trace data — which can be tens of MB — is never forced
  into memory until the plot layer actually asks for a specific
  slice).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import h5py

from app.core.win_paths import long_path
import numpy as np


class HDF5ReadError(Exception):
    """Raised when a file can't be opened as HDF5 at all."""


@dataclass
class NodeInfo:
    """One node (group or dataset) in the HDF5 tree. Mirrors the
    structure used by tools/inspect_hdf5.py so both tools stay in sync
    and Raw HDF5 Explorer Mode can reuse this directly."""
    path: str
    kind: str  # "group" | "dataset"
    shape: tuple | None = None
    dtype: str | None = None
    attrs: dict = field(default_factory=dict)
    fields: list[str] | None = None  # compound dtype field names, if any


def _jsonable(value: Any) -> Any:
    """Best-effort conversion of h5py/numpy attribute values to plain
    Python types, safe to hand to the GUI/metadata layer without it
    needing to know about numpy scalars. Never raises."""
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
            if value.size > 1000:
                return {
                    "__truncated_array__": True,
                    "shape": list(value.shape),
                    "dtype": str(value.dtype),
                }
            return value.tolist()
        if isinstance(value, (list, tuple)):
            return [_jsonable(v) for v in value]
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        return value
    except Exception:
        return str(value)


class HDF5Reader:
    """Lazy, read-only access to one HDF5 file.

    Thread-safety note: h5py.File objects are not safe for concurrent
    access from multiple threads without care. This class serializes
    all reads through a lock so a background QThread (spec §21) can
    safely call `read()` while the GUI thread is doing something else
    with the same reader instance.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise HDF5ReadError(f"File not found: {self.path}")
        self._lock = threading.RLock()
        try:
            self._file: h5py.File | None = h5py.File(long_path(self.path), "r")
        except OSError as e:
            raise HDF5ReadError(f"Not a readable HDF5 file: {self.path} ({e})") from e

    # -- lifecycle ----------------------------------------------------

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None

    def __enter__(self) -> "HDF5Reader":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return self._file is not None

    def _require_open(self) -> h5py.File:
        if self._file is None:
            raise HDF5ReadError(f"Reader for {self.path} is closed.")
        return self._file

    # -- structural access ---------------------------------------------

    def __contains__(self, path: str) -> bool:
        with self._lock:
            f = self._require_open()
            return path.lstrip("/") in f or path == "/"

    def kind_of(self, path: str) -> str | None:
        """'group' | 'dataset' | None if path doesn't exist."""
        with self._lock:
            f = self._require_open()
            key = path.lstrip("/")
            if key == "" or key == "/":
                return "group"
            if key not in f:
                return None
            obj = f[key]
            return "dataset" if isinstance(obj, h5py.Dataset) else "group"

    def get_dataset(self, path: str) -> h5py.Dataset:
        """Returns the LIVE h5py.Dataset handle. Caller may slice it
        (dataset[i, :, :]) for lazy loading, but must not use it after
        this reader is closed."""
        with self._lock:
            f = self._require_open()
            key = path.lstrip("/")
            obj = f[key]
            if not isinstance(obj, h5py.Dataset):
                raise HDF5ReadError(f"Not a dataset: {path}")
            return obj

    def get_group_keys(self, path: str) -> list[str]:
        with self._lock:
            f = self._require_open()
            key = path.lstrip("/")
            obj = f if key in ("", "/") else f[key]
            if not isinstance(obj, h5py.Group):
                raise HDF5ReadError(f"Not a group: {path}")
            return list(obj.keys())

    def read(self, path: str, slice_: Any = None) -> np.ndarray:
        """Explicitly materializes data into memory. Pass `slice_` (a
        tuple of slices/indices, numpy-style) to load only a portion
        of a large dataset — this is the primary mechanism used to
        satisfy the "don't load the whole 84MB trace dataset at once"
        performance requirement (spec §21)."""
        ds = self.get_dataset(path)
        with self._lock:
            if slice_ is None:
                return ds[()]
            return ds[slice_]

    def get_attrs(self, path: str) -> dict:
        with self._lock:
            f = self._require_open()
            key = path.lstrip("/")
            obj = f if key in ("", "/") else f[key]
            return {str(k): _jsonable(v) for k, v in obj.attrs.items()}

    def get_scalar(self, path: str) -> Any:
        """Convenience for the common Labber pattern of a 0-d dataset
        used as a scalar instrument setting (e.g.
        Instrument config/VNA/Start frequency)."""
        ds = self.get_dataset(path)
        with self._lock:
            val = ds[()]
        return _jsonable(val)

    # -- full tree walk (Phase 1 inspector / Raw HDF5 Explorer Mode) ----

    def tree(self) -> list[NodeInfo]:
        with self._lock:
            f = self._require_open()
            nodes: list[NodeInfo] = [
                NodeInfo(path="/", kind="group", attrs=self.get_attrs("/"))
            ]

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
                            attrs={str(k): _jsonable(v) for k, v in obj.attrs.items()},
                            fields=fields,
                        )
                    )
                else:
                    nodes.append(
                        NodeInfo(
                            path=path,
                            kind="group",
                            attrs={str(k): _jsonable(v) for k, v in obj.attrs.items()},
                        )
                    )

            f.visititems(visitor)
            return nodes

    def iter_paths(self, prefix: str = "") -> Iterable[str]:
        for node in self.tree():
            if node.path.startswith(prefix):
                yield node.path
