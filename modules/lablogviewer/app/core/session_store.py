"""Small, schema-aware workspace session storage for v0.13C."""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any
from copy import deepcopy

from app.core.external_state import atomic_write_json, default_state_path, load_json_state


SESSION_SCHEMA_VERSION = 1


def default_session_storage_path() -> Path:
    return default_state_path("session.json")


class SessionStore:
    """One application-session document, without duplicating domain stores."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path) if storage_path else default_session_storage_path()
        self._lock = RLock()
        self._state: dict[str, Any] = {}
        self._read_only = False
        self.recovered_from_corruption = False
        self.requires_safe_recovery = False
        self._load()

    def _load(self) -> None:
        loaded = load_json_state(self.storage_path, {})
        self.recovered_from_corruption = bool(loaded.recovered_from_corruption)
        self.requires_safe_recovery = self.recovered_from_corruption
        raw = loaded.value
        if not isinstance(raw, dict):
            return
        schema = raw.get("schema_version", SESSION_SCHEMA_VERSION)
        if not isinstance(schema, int) or schema > SESSION_SCHEMA_VERSION:
            self._read_only = True
            self.requires_safe_recovery = True
            return
        session = raw.get("session", {})
        if isinstance(session, dict):
            self._state = dict(session)

    def get(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def set(self, state: dict[str, Any], *, strict: bool = False) -> None:
        if self._read_only or not isinstance(state, dict):
            return
        with self._lock:
            self._state = dict(state)
            try:
                atomic_write_json(self.storage_path, {
                    "schema_version": SESSION_SCHEMA_VERSION,
                    "session": self._state,
                })
            except OSError:
                if strict:
                    raise
                # A read-only home must never make closing the GUI fail.
                return

    def move_data_identity(self, old_identity: str, new_identity: str, *,
                           database_id: str | None = None,
                           old_relative_path: str | None = None,
                           new_relative_path: str | None = None) -> bool:
        if self._read_only:
            raise RuntimeError("Session state is from a newer schema and cannot be migrated.")
        with self._lock:
            candidate = deepcopy(self._state)
            found_old = False
            found_new = False

            def replace_identity(value):
                nonlocal found_old, found_new
                if isinstance(value, dict):
                    return {key: replace_identity(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [replace_identity(item) for item in value]
                if value == old_identity:
                    found_old = True
                    return new_identity
                if value == new_identity:
                    found_new = True
                return value

            candidate = replace_identity(candidate)
            if found_old and found_new:
                raise ValueError("Session already contains the destination Data identity.")
            browser = candidate.get("browser")
            if (isinstance(browser, dict) and database_id is not None
                    and candidate.get("database_path") == database_id
                    and old_relative_path is not None
                    and browser.get("selected_relative_path") == old_relative_path):
                browser["selected_relative_path"] = new_relative_path
                found_old = True
            if not found_old:
                return False
            previous = self._state
            self._state = candidate
            try:
                self.set(candidate, strict=True)
            except Exception:
                self._state = previous
                raise
            return True

    def reload(self) -> None:
        with self._lock:
            self._state = {}
            self._read_only = False
            self.recovered_from_corruption = False
            self.requires_safe_recovery = False
            self._load()
