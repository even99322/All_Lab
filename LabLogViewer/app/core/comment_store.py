"""External comments with legacy Browser keys and canonical Data ownership."""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from app.core.external_state import atomic_write_json, default_state_path, load_json_state
from app.core.data_identity import stable_data_identity


COMMENT_SCHEMA_VERSION = 2


def default_comment_storage_path() -> Path:
    return default_state_path("comments.json")


class CommentStore:
    """Comments are external and keyed by database identity plus relative path."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path) if storage_path else default_comment_storage_path()
        self._lock = RLock()
        # ``by_database`` is retained for v0.13B compatibility.  New callers
        # use ``by_data`` so comments follow the same canonical identity as
        # Marks and Viewer display state.
        self._comments: dict[str, dict[str, str]] = {}
        self._by_data: dict[str, str] = {}
        self._read_only = False
        self._load()

    @staticmethod
    def _safe_key(database_id: object, relative_path: object) -> tuple[str, str] | None:
        if not isinstance(database_id, str) or not isinstance(relative_path, str):
            return None
        database_id = database_id.strip()
        relative_path = relative_path.strip().replace("\\", "/")
        relative = Path(relative_path)
        if not database_id or not Path(database_id).is_absolute() or not relative_path:
            return None
        if relative.is_absolute() or ".." in relative.parts:
            return None
        return database_id, relative_path

    def _load(self) -> None:
        result = load_json_state(self.storage_path, {})
        raw = result.value
        if not isinstance(raw, dict):
            return
        schema = raw.get("schema_version", 1)
        if not isinstance(schema, int) or schema > COMMENT_SCHEMA_VERSION:
            self._read_only = True
            return
        source = raw.get("by_database", raw if schema == 1 and "by_database" not in raw else {})
        if not isinstance(source, dict):
            return
        for database_id, entries in source.items():
            if not isinstance(database_id, str) or not isinstance(entries, dict):
                continue
            clean = {
                relative_path.replace("\\", "/"): text
                for relative_path, text in entries.items()
                if isinstance(relative_path, str) and isinstance(text, str)
                and self._safe_key(database_id, relative_path) is not None
            }
            if clean:
                self._comments[database_id] = clean
        canonical = raw.get("by_data", {})
        if isinstance(canonical, dict):
            self._by_data = {
                key: value for key, value in canonical.items()
                if isinstance(key, str) and isinstance(value, str) and key
            }

    def _save(self) -> None:
        if self._read_only:
            return
        atomic_write_json(self.storage_path, {
            "schema_version": COMMENT_SCHEMA_VERSION,
            "by_database": self._comments,
            "by_data": self._by_data,
        })

    def get(self, database_id: str, relative_path: str) -> str:
        key = self._safe_key(database_id, relative_path)
        if key is None:
            return ""
        with self._lock:
            return self._comments.get(key[0], {}).get(key[1], "")

    def set(self, database_id: str, relative_path: str, text: str) -> None:
        key = self._safe_key(database_id, relative_path)
        if key is None:
            return
        with self._lock:
            entries = self._comments.setdefault(key[0], {})
            if text:
                entries[key[1]] = str(text)
            else:
                entries.pop(key[1], None)
                if not entries:
                    self._comments.pop(key[0], None)
            self._save()

    def get_for_source(self, source_path: str | Path, *, database_id: str | None = None,
                       relative_path: str | None = None) -> str:
        """Return a canonical comment, lazily adopting a v0.13B record.

        The fallback is deliberately explicit: Browser scans know their
        database-relative key, while standalone Viewer launches do not.
        """
        identity = stable_data_identity(source_path)
        with self._lock:
            if identity in self._by_data:
                return self._by_data[identity]
            legacy = self.get(database_id or "", relative_path or "")
            if legacy and not self._read_only:
                self._by_data[identity] = legacy
                try:
                    self._save()
                except OSError:
                    pass
            return legacy

    def set_for_source(self, source_path: str | Path, text: str, *,
                       database_id: str | None = None,
                       relative_path: str | None = None) -> None:
        """Save the external note under canonical Data identity.

        When a legacy Browser context is supplied it is updated too, so a
        v0.13B Browser-only install and v0.13C Metadata remain consistent.
        """
        if self._read_only:
            return
        identity = stable_data_identity(source_path)
        value = str(text)
        with self._lock:
            if value:
                self._by_data[identity] = value
            else:
                self._by_data.pop(identity, None)
            key = self._safe_key(database_id or "", relative_path or "")
            if key is not None:
                entries = self._comments.setdefault(key[0], {})
                if value:
                    entries[key[1]] = value
                else:
                    entries.pop(key[1], None)
                    if not entries:
                        self._comments.pop(key[0], None)
            try:
                self._save()
            except OSError:
                return

    def move_data_identity(self, old_identity: str, new_identity: str, *,
                           database_id: str | None = None,
                           old_relative_path: str | None = None,
                           new_relative_path: str | None = None) -> bool:
        if database_id is None or old_relative_path is None or new_relative_path is None:
            return False
        with self._lock:
            if self._read_only:
                raise RuntimeError("Comment state is from a newer schema and cannot be migrated.")
            entries = self._comments.get(database_id, {})
            if new_identity in self._by_data or new_relative_path in entries:
                raise ValueError("Comment state already exists for the destination Data identity.")
            has_data = old_identity in self._by_data
            has_legacy = old_relative_path in entries
            if not has_data and not has_legacy:
                return False
            data_value = self._by_data.pop(old_identity, None)
            legacy_value = entries.pop(old_relative_path, None)
            if data_value is not None:
                self._by_data[new_identity] = data_value
            if legacy_value is not None:
                entries[new_relative_path] = legacy_value
            try:
                self._save()
            except Exception:
                self._by_data.pop(new_identity, None)
                entries.pop(new_relative_path, None)
                if data_value is not None:
                    self._by_data[old_identity] = data_value
                if legacy_value is not None:
                    entries[old_relative_path] = legacy_value
                raise
            return True

    def reload(self) -> None:
        with self._lock:
            self._comments = {}
            self._by_data = {}
            self._read_only = False
            self._load()
