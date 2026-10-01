"""External, defensive persistence for viewer Mark and annotation state."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Hashable

from app import __version__
from app.core.external_state import atomic_write_json, default_state_path, load_json_state


MARK_SCHEMA_VERSION = 1


def default_mark_storage_path() -> Path:
    return default_state_path("marks.json")


def _context_key(value: Hashable) -> str:
    """Produce a stable JSON key without trusting arbitrary application data."""
    def normalise(item):
        if isinstance(item, tuple):
            return [normalise(part) for part in item]
        if isinstance(item, list):
            return [normalise(part) for part in item]
        if isinstance(item, dict):
            return {str(key): normalise(part) for key, part in item.items()}
        if item is None or isinstance(item, (str, int, float, bool)):
            return item
        return repr(item)

    return json.dumps(normalise(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


class MarkStore:
    """Atomic per-data/per-pane Mark state, intentionally separate from HDF5."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path) if storage_path else default_mark_storage_path()
        self._lock = RLock()
        self._datasets: dict[str, dict[str, dict]] = {}
        self._read_only = False
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            raw = load_json_state(self.storage_path, {}).value
            if not isinstance(raw, dict):
                raise ValueError("marks.json root must be an object")
            if raw.get("schema_version") != MARK_SCHEMA_VERSION:
                # Do not replace state produced by a newer application.
                self._read_only = True
                return
            datasets = raw.get("datasets", {})
            if not isinstance(datasets, dict):
                raise ValueError("marks.json datasets must be an object")
            self._datasets = {
                str(data_key): dict(value.get("contexts", {}))
                for data_key, value in datasets.items()
                if isinstance(value, dict) and isinstance(value.get("contexts", {}), dict)
            }
        except Exception:
            self._datasets = {}

    def _save(self, *, strict: bool = False) -> None:
        if self._read_only:
            if strict:
                raise RuntimeError("Mark state is from a newer schema and cannot be migrated.")
            return
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": MARK_SCHEMA_VERSION,
                "app_version": __version__,
                "datasets": {
                    data_key: {"contexts": contexts}
                    for data_key, contexts in self._datasets.items()
                    if contexts
                },
            }
            atomic_write_json(self.storage_path, payload)
        except OSError:
            if strict:
                raise
            # A read-only home directory must not prevent the Viewer closing;
            # the in-memory state remains usable for this process.
            return

    def move_data_identity(self, old_identity: str, new_identity: str, **_context) -> bool:
        with self._lock:
            if self._read_only:
                raise RuntimeError("Mark state is from a newer schema and cannot be migrated.")
            if new_identity in self._datasets:
                raise ValueError("Mark state already exists for the destination Data identity.")
            if old_identity not in self._datasets:
                return False
            value = self._datasets.pop(old_identity)
            self._datasets[new_identity] = value
            try:
                self._save(strict=True)
            except Exception:
                self._datasets.pop(new_identity, None)
                self._datasets[old_identity] = value
                raise
            return True

    def reload(self) -> None:
        with self._lock:
            self._datasets = {}
            self._read_only = False
            self._load()

    def get(self, data_key: str, pane_id: int, context: Hashable) -> dict | None:
        with self._lock:
            value = self._datasets.get(str(data_key), {}).get(_context_key((int(pane_id), context)))
            state = value.get("state") if isinstance(value, dict) else None
            return dict(state) if isinstance(state, dict) else None

    def save(self, data_key: str, pane_id: int, context: Hashable, state: dict) -> None:
        """Persist a validated caller-owned state snapshot for one plot context."""
        if not isinstance(state, dict):
            return
        with self._lock:
            contexts = self._datasets.setdefault(str(data_key), {})
            contexts[_context_key((int(pane_id), context))] = {
                "pane_id": int(pane_id),
                "state": state,
            }
            self._save()

    def clear(self, data_key: str, pane_id: int, context: Hashable) -> None:
        with self._lock:
            contexts = self._datasets.get(str(data_key))
            if not contexts:
                return
            contexts.pop(_context_key((int(pane_id), context)), None)
            if not contexts:
                self._datasets.pop(str(data_key), None)
            self._save()
