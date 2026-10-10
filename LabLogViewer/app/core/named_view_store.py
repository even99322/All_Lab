"""External, Data-owned named scientific Viewer configurations."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any

from app.core.external_state import atomic_write_json, default_state_path, load_json_state


NAMED_VIEW_SCHEMA_VERSION = 1


def default_named_view_storage_path() -> Path:
    return default_state_path("named_views.json")


class NamedViewStore:
    """Schema-aware named View state, partitioned by canonical Data identity."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path) if storage_path else default_named_view_storage_path()
        self._lock = RLock()
        self._by_data: dict[str, dict[str, dict[str, Any]]] = {}
        self._read_only = False
        self._load()

    def _load(self) -> None:
        raw = load_json_state(self.storage_path, {}).value
        if not isinstance(raw, dict):
            return
        schema = raw.get("schema_version", NAMED_VIEW_SCHEMA_VERSION)
        if not isinstance(schema, int) or schema > NAMED_VIEW_SCHEMA_VERSION:
            self._read_only = True
            return
        candidates = raw.get("by_data", {})
        if not isinstance(candidates, dict):
            return
        self._by_data = {
            str(identity): {
                str(name): deepcopy(snapshot)
                for name, snapshot in values.items()
                if isinstance(name, str) and isinstance(snapshot, dict)
            }
            for identity, values in candidates.items()
            if isinstance(identity, str) and isinstance(values, dict)
        }

    def _save(self) -> None:
        if self._read_only:
            return
        atomic_write_json(self.storage_path, {
            "schema_version": NAMED_VIEW_SCHEMA_VERSION,
            "by_data": self._by_data,
        })

    def list_names(self, data_identity: str) -> list[str]:
        with self._lock:
            return sorted(self._by_data.get(data_identity, {}), key=str.casefold)

    def get(self, data_identity: str, name: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._by_data.get(data_identity, {}).get(name)
            return deepcopy(value) if isinstance(value, dict) else None

    def save(self, data_identity: str, name: str, state: dict[str, Any], *, overwrite: bool = False) -> None:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("A View name is required.")
        if not isinstance(state, dict):
            raise ValueError("A View must contain a semantic display state.")
        with self._lock:
            target = self._by_data.setdefault(data_identity, {})
            if clean_name in target and not overwrite:
                raise ValueError("A View with this name already exists for this Data.")
            target[clean_name] = deepcopy(state)
            self._save()

    def rename(self, data_identity: str, old_name: str, new_name: str) -> bool:
        clean_name = new_name.strip()
        with self._lock:
            target = self._by_data.get(data_identity, {})
            if not clean_name or old_name not in target or (clean_name != old_name and clean_name in target):
                return False
            target[clean_name] = target.pop(old_name)
            self._save()
            return True

    def delete(self, data_identity: str, name: str) -> bool:
        with self._lock:
            target = self._by_data.get(data_identity, {})
            if name not in target:
                return False
            del target[name]
            if not target:
                self._by_data.pop(data_identity, None)
            self._save()
            return True

    def move_data_identity(self, old_identity: str, new_identity: str, **_context) -> bool:
        with self._lock:
            if self._read_only:
                raise RuntimeError("Named Views are from a newer schema and cannot be migrated.")
            if new_identity in self._by_data:
                raise ValueError("Named Views already exist for the destination Data identity.")
            if old_identity not in self._by_data:
                return False
            value = self._by_data.pop(old_identity)
            self._by_data[new_identity] = value
            try:
                self._save()
            except Exception:
                self._by_data.pop(new_identity, None)
                self._by_data[old_identity] = value
                raise
            return True

    def reload(self) -> None:
        with self._lock:
            self._by_data = {}
            self._read_only = False
            self._load()
