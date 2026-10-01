"""External, per-data last Viewer display state for Browser Quick Preview."""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any

from app.core.data_identity import stable_data_identity
from app.core.external_state import atomic_write_json, default_state_path, load_json_state


VIEWER_DISPLAY_STATE_SCHEMA_VERSION = 1


def default_viewer_display_state_path() -> Path:
    return default_state_path("viewer_display_states.json")


class ViewerDisplayStateStore:
    """Small, schema-aware display snapshots keyed by canonical source path.

    This is intentionally not a session store.  It records only the display
    choices that a lightweight Browser preview can safely reconstruct.
    """

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path) if storage_path else default_viewer_display_state_path()
        self._lock = RLock()
        self._states: dict[str, dict[str, Any]] = {}
        self._read_only = False
        self._load()

    def _load(self) -> None:
        raw = load_json_state(self.storage_path, {}).value
        if not isinstance(raw, dict):
            return
        schema = raw.get("schema_version", VIEWER_DISPLAY_STATE_SCHEMA_VERSION)
        if not isinstance(schema, int) or schema > VIEWER_DISPLAY_STATE_SCHEMA_VERSION:
            self._read_only = True
            return
        source = raw.get("by_data", {})
        if not isinstance(source, dict):
            return
        self._states = {
            str(identity): dict(state)
            for identity, state in source.items()
            if isinstance(identity, str) and isinstance(state, dict)
        }

    def get(self, source_path: str | Path) -> dict[str, Any] | None:
        identity = stable_data_identity(source_path)
        with self._lock:
            state = self._states.get(identity)
            return dict(state) if isinstance(state, dict) else None

    def set(self, source_path: str | Path, state: dict[str, Any]) -> None:
        """Stage and durably save a state snapshot (legacy public behavior)."""
        self.stage(source_path, state)
        self.flush()

    def stage(self, source_path: str | Path, state: dict[str, Any]) -> None:
        """Expose a meaningful state immediately without an eager disk write."""
        if self._read_only or not isinstance(state, dict):
            return
        identity = stable_data_identity(source_path)
        with self._lock:
            self._states[identity] = dict(state)

    def flush(self, *, strict: bool = False) -> None:
        """Atomically persist all staged display states."""
        if self._read_only:
            return
        with self._lock:
            try:
                atomic_write_json(self.storage_path, {
                    "schema_version": VIEWER_DISPLAY_STATE_SCHEMA_VERSION,
                    "by_data": self._states,
                })
            except OSError:
                if strict:
                    raise
                # A sandboxed/read-only home must not prevent Viewer close;
                # the in-memory state remains useful for this process.
                return

    def move_data_identity(self, old_identity: str, new_identity: str, **_context) -> bool:
        with self._lock:
            if self._read_only:
                raise RuntimeError("Viewer display state is from a newer schema and cannot be migrated.")
            if new_identity in self._states:
                raise ValueError("Viewer display state already exists for the destination Data identity.")
            if old_identity not in self._states:
                return False
            value = self._states.pop(old_identity)
            self._states[new_identity] = value
            try:
                self.flush(strict=True)
            except Exception:
                self._states.pop(new_identity, None)
                self._states[old_identity] = value
                raise
            return True
