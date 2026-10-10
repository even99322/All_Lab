"""
app/core/cache.py — Phase 3

A small, dependency-free LRU cache for lazily-loaded channel data
slices, sitting in front of Experiment.get_data(). Exists to satisfy
the performance requirements in spec §21:

  "3. 每次 slider 改變重新讀取所有資料" must be avoided — repeated
  slice requests for the same (channel, transform, slice) key hit
  this cache instead of re-reading from HDF5 every time a Slice
  Explorer slider moves by one step and then moves back.

Deliberately NOT tied to Qt/threading here — this is pure Python so it
is unit-testable on its own; the GUI layer's QThread/QRunnable workers
(future app/utils/workers.py, Phase 10) call through a
CachedExperiment instance from a background thread, and this cache's
lock keeps that safe.
"""

from __future__ import annotations

import sys
import threading
from collections import OrderedDict
from typing import Any, Callable

import numpy as np

from app.core.data_model import Experiment


class LRUDataCache:
    """Simple thread-safe LRU cache keyed by an arbitrary hashable key,
    with an approximate byte-size budget (not just item count) since a
    single vector-trace slice can be tens of MB while a scalar slice
    is a few hundred bytes."""

    def __init__(self, max_bytes: int = 256 * 1024 * 1024):
        self.max_bytes = max_bytes
        self._store: "OrderedDict[Any, Any]" = OrderedDict()
        self._sizes: "OrderedDict[Any, int]" = OrderedDict()
        self._current_bytes = 0
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    def get(self, key: Any) -> Any | None:
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                self.hits += 1
                return self._store[key]
            self.misses += 1
            return None

    def put(self, key: Any, value: Any) -> None:
        """Stores any value - numpy arrays and dataclasses exposing an
        `.nbytes` property (e.g. data_model.Grid2DData) are sized
        precisely; anything else falls back to sys.getsizeof."""
        with self._lock:
            nbytes = self._approx_nbytes(value)
            if key in self._store:
                self._current_bytes -= self._sizes.pop(key)
                del self._store[key]
            self._store[key] = value
            self._sizes[key] = nbytes
            self._current_bytes += nbytes
            self._evict_if_needed()

    @staticmethod
    def _approx_nbytes(value: Any) -> int:
        nbytes = getattr(value, "nbytes", None)
        if nbytes is not None:
            try:
                return int(nbytes)
            except Exception:
                pass
        if isinstance(value, tuple):
            total = 0
            found = False
            for v in value:
                n = getattr(v, "nbytes", None)
                if n is not None:
                    try:
                        total += int(n)
                        found = True
                    except Exception:
                        pass
            if found:
                return total
        return sys.getsizeof(value)

    def _evict_if_needed(self) -> None:
        while self._current_bytes > self.max_bytes and self._store:
            oldest_key, _ = self._store.popitem(last=False)
            self._current_bytes -= self._sizes.pop(oldest_key, 0)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._sizes.clear()
            self._current_bytes = 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "items": len(self._store),
                "bytes": self._current_bytes,
                "max_bytes": self.max_bytes,
                "hits": self.hits,
                "misses": self.misses,
            }


class CachedExperiment:
    """Wraps an Experiment and transparently caches get_data() calls.
    This is the object the GUI/plot layer should hold onto in later
    phases, rather than the raw Experiment, whenever repeated slicing
    (sliders, line cuts) is expected."""

    def __init__(self, experiment: Experiment, cache: LRUDataCache | None = None):
        self.experiment = experiment
        self.cache = cache or LRUDataCache()

    def get_data(self, channel_name: str, transform: str = "raw",
                 entry_slice: Any = None) -> np.ndarray:
        key = (channel_name, transform, self._slice_key(entry_slice))
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        data = self.experiment.get_data(channel_name, transform=transform, entry_slice=entry_slice)
        self.cache.put(key, data)
        return data

    def get_2d_data(self, x_channel: str, y_channel: str, z_channel: str, transform: str = "raw"):
        """Cached wrapper over Experiment.get_2d_data() - the Z-surface
        transform (e.g. magnitude_db over an 855x501 grid) is the most
        expensive repeated operation in the 2D plot GUI (colormap /
        color-range changes must NOT re-trigger it), so this is cached
        just like get_data()."""
        key = ("2d", x_channel, y_channel, z_channel, transform)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        grid = self.experiment.get_2d_data(x_channel, y_channel, z_channel, transform=transform)
        self.cache.put(key, grid)
        return grid

    # ---- N-D Slice Explorer support (Phase 7) --------------------------------

    def get_full_nd_array(self, z_channel: str, transform: str = "raw"):
        """Cached wrapper over Experiment.get_full_nd_array(). This is
        the single most important cache entry for the Slice Explorer:
        it is read from HDF5 (and transformed) at most ONCE per
        (z_channel, transform) combination, however many times the
        user subsequently drags a slice slider - every slider move
        after the first hits this cache and only pays for a cheap
        in-memory numpy re-index (see get_nd_slice below / spec §6)."""
        key = ("nd_full", z_channel, transform)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        result = self.experiment.get_full_nd_array(z_channel, transform=transform)
        self.cache.put(key, result)
        return result

    def get_nd_slice(self, z_channel: str, x_dim: str, y_dim: str | None = None,
                      fixed: dict | None = None, transform: str = "raw"):
        """Cached-array-backed equivalent of Experiment.get_nd_slice().
        Deliberately does NOT cache the sliced RESULT itself (unlike
        get_2d_data/get_data) - the result is cheap enough to recompute
        from the cached full array on every call (pure numpy indexing,
        sub-millisecond even for the 855x501 sample) that caching the
        full array is sufficient to satisfy the "don't re-read HDF5 on
        every slider move" requirement without the extra bookkeeping
        of a second cache layer keyed by every possible fixed-index
        combination."""
        from app.core.data_model import _slice_full_array  # local import: avoids
        # a hard cache.py -> data_model private-symbol dependency at module load time
        dims, arr = self.get_full_nd_array(z_channel, transform=transform)
        return _slice_full_array(self.experiment, z_channel, dims, arr, x_dim, y_dim, fixed, transform)

    @staticmethod
    def _slice_key(entry_slice: Any) -> Any:
        """slice objects aren't hashable-friendly across equal-but-
        distinct instances in all cases; normalize to a tuple."""
        if entry_slice is None:
            return None
        if isinstance(entry_slice, slice):
            return ("slice", entry_slice.start, entry_slice.stop, entry_slice.step)
        if isinstance(entry_slice, tuple):
            return tuple(CachedExperiment._slice_key(s) for s in entry_slice)
        if isinstance(entry_slice, int):
            return entry_slice
        return str(entry_slice)

    def __getattr__(self, item):
        # delegate anything not explicitly overridden (channels,
        # step_axes, sweep_dimension_summary, etc.) straight to the
        # wrapped Experiment, so CachedExperiment is a drop-in
        # replacement wherever read-only Experiment attributes are used.
        return getattr(self.experiment, item)
