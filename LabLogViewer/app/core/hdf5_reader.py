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

import errno
import itertools
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import h5py

from app.core.win_paths import long_path
import numpy as np


_DECODE_THREADS = 2
_MIN_PARALLEL_CHUNKS = 16


def set_decode_threads(count: int) -> None:
    global _DECODE_THREADS
    _DECODE_THREADS = max(1, int(count))


def _read_gzip_parallel(ds: "h5py.Dataset") -> np.ndarray | None:
    """Decode a many-chunk gzip dataset with zlib in worker threads
    (zlib releases the GIL; h5py's own decode is serialized). Returns
    None when the dataset is not a plain gzip-chunked one."""
    if _DECODE_THREADS < 2 or ds.compression != "gzip" or ds.shuffle \
            or ds.scaleoffset or ds.fletcher32 or not ds.chunks \
            or ds.dtype.hasobject or ds.dtype.names:
        return None
    shape, chunks = ds.shape, ds.chunks
    grid = [range(0, n, c) for n, c in zip(shape, chunks)]
    nchunks = 1
    for g in grid:
        nchunks *= len(g)
    if nchunks < _MIN_PARALLEL_CHUNKS:
        return None
    out = np.empty(shape, dtype=ds.dtype)
    raws = []
    for off in itertools.product(*grid):
        mask, raw = ds.id.read_direct_chunk(off)
        if mask:
            return None
        raws.append((off, raw))

    def decode(item):
        off, raw = item
        arr = np.frombuffer(zlib.decompress(raw), dtype=ds.dtype).reshape(chunks)
        sl = tuple(slice(o, min(o + c, n)) for o, c, n in zip(off, chunks, shape))
        out[sl] = arr[tuple(slice(0, s.stop - s.start) for s in sl)]

    with ThreadPoolExecutor(max_workers=_DECODE_THREADS) as ex:
        list(ex.map(decode, raws))
    return out


class HDF5ReadError(Exception):
    """Raised when a file can't be opened as HDF5 at all."""

    def __init__(self, message: str = "", transient: bool = False):
        super().__init__(message)
        self.transient = transient


_TRANSIENT_ERRNOS = {errno.EAGAIN, errno.EBUSY, errno.EIO, errno.ETIMEDOUT, errno.ESTALE}


def _is_transient(exc: OSError) -> bool:
    """Errors a busy network drive can raise once and then not again."""
    if exc.errno in _TRANSIENT_ERRNOS:
        return True
    text = str(exc).lower()
    return "unable to lock file" in text or "resource temporarily unavailable" in text


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
        self._file: h5py.File | None = self._open_read_only(self.path)
        # v2.1.0: Lab Control xarray/NetCDF (.nc) files are HDF5 too; they are presented as a
        # Labber-shaped virtual tree so the parser / Experiment read them unchanged.
        self._virtual = None
        try:
            from app.core.labcontrol_nc import build_labber_view, is_labcontrol_nc, is_labcontrol_raw, raw_as_nc

            if is_labcontrol_nc(self._file):
                self._virtual = build_labber_view(self._file)
            elif is_labcontrol_raw(self._file):         # v2.1.3: Lab Control _raw/*.lm.h5 (read-only)
                self._raw_nc = raw_as_nc(self._file)
                self._virtual = build_labber_view(self._raw_nc)
                self._virtual_raw = True
        except Exception:
            self._virtual = None

    @property
    def is_labcontrol_nc(self) -> bool:
        return self._virtual is not None and not getattr(self, "_virtual_raw", False)

    def _vnode(self, path: str):
        key = "/" + path.strip("/") if path.strip("/") else "/"
        return self._virtual.get(key)

    @staticmethod
    def _open_read_only(path: Path, attempts: int = 5) -> "h5py.File":
        """Open for reading without HDF5 file locking (a network drive can refuse the lock with
        errno 35/11 even though nothing is wrong with the file) and retry transient errors."""
        error: OSError | None = None
        for attempt in range(attempts):
            try:
                return h5py.File(long_path(path), "r", locking=False)
            except OSError as exc:
                error = exc
                if not _is_transient(exc) or attempt == attempts - 1:
                    break
                time.sleep(0.25 * (attempt + 1))
        assert error is not None
        if _is_transient(error):
            raise HDF5ReadError(
                f"The file is temporarily busy (the drive did not answer in time): {path} ({error})",
                transient=True) from error
        raise HDF5ReadError(f"Not a readable HDF5 file: {path} ({error})") from error

    # -- lifecycle ----------------------------------------------------

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None
            raw = getattr(self, "_raw_nc", None)
            if raw is not None:
                raw.close()
                self._raw_nc = None

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
        if self._virtual is not None:
            return self._vnode(path) is not None
        with self._lock:
            f = self._require_open()
            return path.lstrip("/") in f or path == "/"

    def kind_of(self, path: str) -> str | None:
        """'group' | 'dataset' | None if path doesn't exist."""
        if self._virtual is not None:
            node = self._vnode(path)
            return node.kind if node is not None else None
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
        if self._virtual is not None:
            node = self._vnode(path)
            if node is None or node.kind != "dataset":
                raise HDF5ReadError(f"Not a dataset: {path}")
            return node.data
        with self._lock:
            f = self._require_open()
            key = path.lstrip("/")
            obj = f[key]
            if not isinstance(obj, h5py.Dataset):
                raise HDF5ReadError(f"Not a dataset: {path}")
            return obj

    def get_group_keys(self, path: str) -> list[str]:
        if self._virtual is not None:
            node = self._vnode(path)
            if node is None or node.kind != "group":
                raise HDF5ReadError(f"Not a group: {path}")
            return list(node.children)
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
        if self._virtual is not None:
            return np.asarray(ds) if slice_ is None else np.asarray(ds[slice_])
        with self._lock:
            if slice_ is None:
                try:
                    fast = _read_gzip_parallel(ds)
                except Exception:
                    fast = None
                if fast is not None:
                    return fast
                return ds[()]
            return ds[slice_]

    def get_attrs(self, path: str) -> dict:
        if self._virtual is not None:
            node = self._vnode(path)
            return {str(k): _jsonable(v) for k, v in (node.attrs if node else {}).items()}
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
        if self._virtual is not None:
            return _jsonable(np.asarray(ds)[()])
        with self._lock:
            val = ds[()]
        return _jsonable(val)

    # -- full tree walk (Phase 1 inspector / Raw HDF5 Explorer Mode) ----

    def tree(self) -> list[NodeInfo]:
        if self._virtual is not None:
            nodes = []
            for path in sorted(self._virtual, key=lambda p: (p != "/", p)):
                node = self._virtual[path]
                attrs = {str(k): _jsonable(v) for k, v in node.attrs.items()}
                if node.kind == "dataset":
                    data = np.asarray(node.data)
                    nodes.append(NodeInfo(path=path, kind="dataset", shape=data.shape, dtype=str(data.dtype),
                                          attrs=attrs, fields=list(data.dtype.names) if data.dtype.names else None))
                else:
                    nodes.append(NodeInfo(path=path, kind="group", attrs=attrs))
            return nodes
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
