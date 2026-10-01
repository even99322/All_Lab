"""Validated lightweight Browser metadata index; never a scientific cache."""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any

from app.core.external_state import atomic_write_json, default_state_path, load_json_state


DATABASE_INDEX_SCHEMA_VERSION = 1


def default_database_index_path() -> Path:
    return default_state_path("database_index.json")


class DatabaseIndexStore:
    """Cache scan-only LogEntry metadata, invalidated by path, size and mtime."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path) if storage_path else default_database_index_path()
        self._lock = RLock()
        self._by_root: dict[str, dict[str, dict[str, Any]]] = {}
        self._read_only = False
        self._load()

    def _load(self) -> None:
        raw = load_json_state(self.storage_path, {}).value
        if not isinstance(raw, dict):
            return
        schema = raw.get("schema_version", DATABASE_INDEX_SCHEMA_VERSION)
        if not isinstance(schema, int) or schema > DATABASE_INDEX_SCHEMA_VERSION:
            self._read_only = True
            return
        roots = raw.get("by_root", {})
        if isinstance(roots, dict):
            self._by_root = {
                str(root): {str(key): dict(value) for key, value in items.items() if isinstance(value, dict)}
                for root, items in roots.items() if isinstance(root, str) and isinstance(items, dict)
            }

    def get(self, root: str, relative_path: str, size_bytes: int, mtime: float) -> dict[str, Any] | None:
        with self._lock:
            item = self._by_root.get(root, {}).get(relative_path)
            if not isinstance(item, dict):
                return None
            if item.get("size_bytes") != size_bytes or item.get("mtime") != mtime:
                return None
            return dict(item)

    def replace_root(self, root: str, entries: dict[str, dict[str, Any]]) -> None:
        if self._read_only:
            return
        with self._lock:
            self._by_root[root] = {key: dict(value) for key, value in entries.items()}
            try:
                atomic_write_json(self.storage_path, {
                    "schema_version": DATABASE_INDEX_SCHEMA_VERSION,
                    "by_root": self._by_root,
                })
            except OSError:
                return

    def move_data_identity(self, old_identity: str, new_identity: str, *,
                           database_id: str | None = None,
                           old_relative_path: str | None = None,
                           new_relative_path: str | None = None) -> bool:
        del old_identity, new_identity, new_relative_path
        if database_id is None or old_relative_path is None:
            return False
        with self._lock:
            root = self._by_root.get(database_id, {})
            changed = root.pop(old_relative_path, None) is not None
            if changed:
                try:
                    atomic_write_json(self.storage_path, {
                        "schema_version": DATABASE_INDEX_SCHEMA_VERSION,
                        "by_root": self._by_root,
                    })
                except OSError:
                    return False
            return changed
