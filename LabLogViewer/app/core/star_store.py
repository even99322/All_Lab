"""
app/core/star_store.py — v0.8

Persistent Star/Unstar state, stored entirely OUTSIDE the original
HDF5 files (a plain JSON file under the user's home directory by
default) — this file is only the local copy; the star itself lives in the HDF5 file (app/core/labber_star.py).

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
        self._clock = 0
        self._touched: dict[tuple[str, str], int] = {}
        self._pending: dict[str, dict[str, bool]] = {}      # star values still to be written into the HDF5 file
        self._read_only = False
        self._load()

    # ---- persistence --------------------------------------------------------

    def _load(self) -> None:
        raw = load_json_state(self.storage_path, {}).value
        if not isinstance(raw, dict):
            self._data = {}
            return
        pending = raw.get("native_pending")
        self._pending = {
            str(db): {str(p): bool(v) for p, v in items.items()}
            for db, items in pending.items() if isinstance(items, dict)
        } if isinstance(pending, dict) else {}
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
                "native_pending": {db: items for db, items in self._pending.items() if items},
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

    def clock(self) -> int:
        """Counter of local star changes; lets a slow scan tell which paths changed after it began."""
        with self._lock:
            return self._clock

    def toggle(self, database_id: str, relative_path: str) -> bool:
        """Flips the star state and returns the NEW state."""
        with self._lock:
            self._clock += 1
            self._touched[(database_id, relative_path)] = self._clock
            new_state = not self.is_starred(database_id, relative_path)
            self.set_starred(database_id, relative_path, new_state)
            return new_state

    # ---- the star inside the HDF5 file (written in the background, retried while the file is busy) ----

    def queue_native(self, database_id: str, relative_path: str, starred: bool) -> None:
        with self._lock:
            self._pending.setdefault(database_id, {})[relative_path] = bool(starred)
            self._save()

    def native_job(self, database_id: str, relative_path: str) -> bool | None:
        with self._lock:
            return self._pending.get(database_id, {}).get(relative_path)

    def native_done(self, database_id: str, relative_path: str, value: bool) -> None:
        """The file now holds ``value``; forget the job unless the user changed their mind meanwhile."""
        with self._lock:
            items = self._pending.get(database_id, {})
            if items.get(relative_path) == bool(value):
                del items[relative_path]
                if not items:
                    self._pending.pop(database_id, None)
                self._save()

    def native_pending_paths(self, database_id: str) -> list[str]:
        with self._lock:
            return list(self._pending.get(database_id, {}))

    def adopt_file_stars(self, database_id: str, file_stars: dict[str, bool | None], since: int | None = None) -> int:
        """Take the stars stored in the files (None: no attribute = not starred); paths with a waiting
        change of ours keep our value, and so do paths the user changed after ``since`` (the file was read
        before that change). Returns how many differed."""
        changed = 0
        with self._lock:
            waiting = self._pending.get(database_id, {})
            paths = self._data.setdefault(database_id, [])
            for relative_path, value in file_stars.items():
                if relative_path in waiting:
                    continue
                if since is not None and self._touched.get((database_id, relative_path), 0) > since:
                    continue
                want = bool(value)
                if want and relative_path not in paths:
                    paths.append(relative_path)
                    changed += 1
                elif not want and relative_path in paths:
                    paths.remove(relative_path)
                    changed += 1
            if not paths:
                self._data.pop(database_id, None)
            if changed:
                self._save()
        return changed

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
            waiting = self._pending.get(database_id, {})
            moved_job = old_relative_path in waiting
            if moved_job:
                waiting[new_relative_path] = waiting.pop(old_relative_path)
            if old_relative_path not in paths:
                if moved_job:
                    self._save()
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
