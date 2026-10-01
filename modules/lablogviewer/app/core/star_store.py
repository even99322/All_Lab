"""
app/core/star_store.py — v0.8

Persistent Star/Unstar state, stored entirely OUTSIDE the original
HDF5 files (a plain JSON file under the user's home directory by
default) — Star must never write to or modify a Labber HDF5 file.

Keyed by (database_id, relative_path) — spec explicitly forbids using
just the filename, since two different scanned folders can each
contain a file with the same name (e.g. 'Experiment_A/test.hdf5' vs
'Experiment_B/test.hdf5') and those must not collide.

No Qt dependency — plain Python + json, so it's directly unit
testable and reusable from both the Browser GUI and any future
headless tooling.
"""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from app.core.external_state import atomic_write_json, default_state_path, load_json_state


STAR_SCHEMA_VERSION = 1


def default_storage_path() -> Path:
    return default_state_path("stars.json")


class StarStore:
    """`self._data` shape: {database_id: [relative_path, ...]}."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path) if storage_path else default_storage_path()
        self._lock = RLock()
        self._data: dict[str, list[str]] = {}
        self._read_only = False
        self._load()

    # ---- persistence --------------------------------------------------------

    def _load(self) -> None:
        raw = load_json_state(self.storage_path, {}).value
        if not isinstance(raw, dict):
            self._data = {}
            return
        schema = raw.get("schema_version")
        if schema is not None:
            if not isinstance(schema, int) or schema > STAR_SCHEMA_VERSION:
                self._read_only = True
                return
            raw = raw.get("by_database", {})
        if not isinstance(raw, dict):
            self._data = {}
            return
        cleaned: dict[str, list[str]] = {}
        for db_id, paths in raw.items():
            if isinstance(paths, list):
                cleaned[str(db_id)] = [str(p) for p in paths if isinstance(p, str)]
        self._data = cleaned

    def _save(self) -> None:
        if not self._read_only:
            atomic_write_json(self.storage_path, {
                "schema_version": STAR_SCHEMA_VERSION,
                "by_database": self._data,
            })

    # ---- public API ------------------------------------------------------------

    def is_starred(self, database_id: str, relative_path: str) -> bool:
        with self._lock:
            return relative_path in self._data.get(database_id, [])

    def set_starred(self, database_id: str, relative_path: str, starred: bool) -> None:
        with self._lock:
            paths = self._data.setdefault(database_id, [])
            if starred:
                if relative_path not in paths:
                    paths.append(relative_path)
            else:
                if relative_path in paths:
                    paths.remove(relative_path)
                if not paths:
                    self._data.pop(database_id, None)
            self._save()

    def toggle(self, database_id: str, relative_path: str) -> bool:
        """Flips the star state and returns the NEW state."""
        with self._lock:
            new_state = not self.is_starred(database_id, relative_path)
            self.set_starred(database_id, relative_path, new_state)
            return new_state

    def starred_paths_for(self, database_id: str) -> set[str]:
        with self._lock:
            return set(self._data.get(database_id, []))

    def move_data_identity(self, old_identity: str, new_identity: str, *,
                           database_id: str | None = None,
                           old_relative_path: str | None = None,
                           new_relative_path: str | None = None) -> bool:
        del old_identity, new_identity
        if database_id is None or old_relative_path is None or new_relative_path is None:
            return False
        with self._lock:
            paths = self._data.get(database_id, [])
            if new_relative_path in paths:
                raise ValueError("Star state already exists for the destination filename.")
            if old_relative_path not in paths:
                return False
            index = paths.index(old_relative_path)
            paths[index] = new_relative_path
            try:
                if self._read_only:
                    raise RuntimeError("Star state is from a newer schema and cannot be migrated.")
                self._save()
            except Exception:
                paths[index] = old_relative_path
                raise
            return True

    def reload(self) -> None:
        """Re-reads the state file from disk - used after external
        changes, or simply to confirm persistence in tests."""
        with self._lock:
            self._load()
