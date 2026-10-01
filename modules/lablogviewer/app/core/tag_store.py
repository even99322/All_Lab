"""Persistent Tag management and smart defaults for LabLogViewer."""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import RLock
from typing import Iterable

from app.core.external_state import atomic_write_json, default_state_path

logger = logging.getLogger(__name__)


TAG_CATEGORIES = ("Project", "Level", "Board Design", "Data Analysis", "Other")
SINGLE_SELECT_CATEGORIES = frozenset({"Project", "Level"})
DEFAULT_CATEGORY_TAGS = {
    "Project": ("LRCPAEP", "BIC", "CM", "RSMEP"),
    "Level": ("LA", "LR"),
    "Board Design": ("Mirror",),
    "Data Analysis": ("Flux", "BG", "De-background"),
    "Other": ("Good Data", "Best Data", "Debug", "singleYIG", "doubleYIG"),
}
DEFAULT_TAGS = tuple(tag for category in TAG_CATEGORIES for tag in DEFAULT_CATEGORY_TAGS[category])
SCHEMA_VERSION = 4
MAX_RECENT_QUERIES = 5
RECENT_QUERY_MAX_AGE_SECONDS = 14 * 24 * 60 * 60
_SESSION_RE = re.compile(r"^Data_\d+$", re.IGNORECASE)
_FLUX_RE = re.compile(r"(?:^|[\s_-])Flux-dep(?:$|[\s_.-])", re.IGNORECASE)
_BG_RE = re.compile(r"(?:^|[\s_.-])BG(?:$|[\s_.-])", re.IGNORECASE)
_DEBG_RE = re.compile(r"(?:_debg|(?:^|[\s_.-])(?:debg|de[-_ ]?background)(?:$|[\s_.-]))", re.IGNORECASE)


class TagAssignmentConflict(ValueError):
    """Raised when a Data assignment violates category cardinality."""


def _canonical_tag_name(value: object) -> str:
    name = str(value).strip() if isinstance(value, str) else ""
    folded = name.casefold().replace("_", "-")
    known = {
        "lrcpaep/ccep": "LRCPAEP", "lrcpaep": "LRCPAEP",
        "best-data": "Best Data", "best data": "Best Data", "best_data": "Best Data",
        "good-data": "Good Data", "good data": "Good Data",
        "debug": "Debug", "debg": "De-background", "de-background": "De-background",
        "debackground": "De-background", "de background": "De-background",
    }
    if folded in known:
        return known[folded]
    for canonical in ("BIC", "CM", "RSMEP", "LA", "LR", "Mirror", "Flux", "BG"):
        if name.casefold() == canonical.casefold():
            return canonical
    return name


def _default_category(name: str) -> str:
    for category, names in DEFAULT_CATEGORY_TAGS.items():
        if name in names:
            return category
    return "Other"


def default_tag_storage_path() -> Path:
    return default_state_path("tags.json")


def default_legacy_tag_paths(storage_path: Path | None = None) -> list[Path]:
    directory = (storage_path or default_tag_storage_path()).parent
    return [
        directory / "tag_metadata.json", directory / "metadata.json",
        directory / "tag_assignments.json", directory / "legacy_tags.json",
        directory / "tags_v1.json",
    ]


def session_group_id(relative_path: str) -> str:
    """Return the structural session path, preferring the nearest Data_#### ancestor."""
    parent_parts = Path(relative_path.replace("\\", "/")).parent.parts
    for index in range(len(parent_parts) - 1, -1, -1):
        if _SESSION_RE.fullmatch(parent_parts[index]):
            return Path(*parent_parts[:index + 1]).as_posix()
    return Path(*parent_parts).as_posix() if parent_parts else "."


def is_flux_named(relative_path: str, log_name: str | None = None) -> bool:
    """Match the real-data Flux-dep naming token without inspecting HDF5 values."""
    names = [Path(relative_path).stem]
    if log_name:
        names.append(log_name)
    return any(_FLUX_RE.search(name) is not None for name in names)


@dataclass
class TagMigrationReport:
    sources_inspected: list[str] = field(default_factory=list)
    sources_found: list[str] = field(default_factory=list)
    discovered_tags: list[str] = field(default_factory=list)
    imported_tags: list[str] = field(default_factory=list)
    duplicate_tags_skipped: list[str] = field(default_factory=list)
    migrated_assignments: int = 0
    unmatched_assignments: list[str] = field(default_factory=list)
    malformed_sources: list[str] = field(default_factory=list)
    preserved_conflicts: list[str] = field(default_factory=list)
    schema_migrated_from: int | None = None


class TagStoreError(RuntimeError):
    """Raised when persisted Tag state cannot be loaded without data loss."""


class TagStore:
    """External Tag state keyed by database identity and relative path."""

    def __init__(self, storage_path: str | Path | None = None,
                 legacy_paths: list[str | Path] | None = None):
        self.storage_path = Path(storage_path) if storage_path else default_tag_storage_path()
        candidates = legacy_paths if legacy_paths is not None else default_legacy_tag_paths(self.storage_path)
        self.legacy_paths = [Path(path) for path in candidates if Path(path) != self.storage_path]
        self._lock = RLock()
        self._available: list[str] = list(DEFAULT_TAGS)
        self._categories: dict[str, str] = {
            tag: _default_category(tag) for tag in DEFAULT_TAGS
        }
        self._states: dict[str, dict[str, dict[str, object]]] = {}
        self._group_defaults: dict[str, dict[str, list[str]]] = {}
        self._recent_queries: list[dict[str, object]] = []
        self.migration_report = TagMigrationReport()
        self._pending_taxonomy_migration_schema: int | None = None
        if self.storage_path.exists():
            self._load_current()
        else:
            self._migrate_legacy()
            self._migrate_loaded_taxonomy(SCHEMA_VERSION)
            self._save()

    @staticmethod
    def normalize_name(name: object) -> str:
        return str(name).strip() if isinstance(name, str) else ""

    @staticmethod
    def _safe_identity(database_id: object, relative_path: object) -> tuple[str, str] | None:
        if not isinstance(database_id, str) or not isinstance(relative_path, str):
            return None
        database_id = database_id.strip()
        relative_path = relative_path.strip().replace("\\", "/")
        relative = Path(relative_path)
        if not database_id or not Path(database_id).is_absolute():
            return None
        if not relative_path or relative.is_absolute() or ".." in relative.parts:
            return None
        return database_id, relative_path

    def _load_current(self) -> None:
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict):
                raise ValueError("tags.json root must be an object")
            available = raw.get("available_tags", [])
            assignments = raw.get("assignments", {})
            if not isinstance(available, list) or not isinstance(assignments, dict):
                raise ValueError("tags.json has an invalid schema")
            schema = raw.get("schema_version", 1)
            if not isinstance(schema, int) or schema > SCHEMA_VERSION:
                raise ValueError(f"unsupported tags.json schema version: {schema}")
            old_available = [*DEFAULT_TAGS, *available] if schema < 2 else list(available)
            if schema < SCHEMA_VERSION:
                raw_states = raw.get("entry_states", {})
                if isinstance(raw_states, dict):
                    for paths in raw_states.values():
                        if isinstance(paths, dict):
                            for value in paths.values():
                                if isinstance(value, dict):
                                    for key in ("explicit_tags", "auto_tags", "suppressed_auto_tags"):
                                        if isinstance(value.get(key), list):
                                            old_available.extend(value[key])
                raw_groups = raw.get("group_defaults", {})
                if isinstance(raw_groups, dict):
                    for groups in raw_groups.values():
                        if isinstance(groups, dict):
                            for tags in groups.values():
                                if isinstance(tags, list):
                                    old_available.extend(tags)
                raw_recent = raw.get("recent_queries", [])
                if isinstance(raw_recent, list):
                    for query in raw_recent:
                        if isinstance(query, dict) and isinstance(query.get("tags"), list):
                            old_available.extend(query["tags"])
            raw_categories = raw.get("tag_categories", {})
            if not isinstance(raw_categories, dict):
                raise ValueError("tag_categories must be an object")
            self._available = []
            self._categories = {}
            for original in old_available:
                source_name = self.normalize_name(original)
                if not source_name:
                    continue
                name = _canonical_tag_name(source_name) if schema < SCHEMA_VERSION else source_name
                category = raw_categories.get(original, raw_categories.get(name))
                if category not in TAG_CATEGORIES:
                    category = _default_category(name)
                if schema < SCHEMA_VERSION and source_name != name:
                    self._add_available_in_memory(source_name, category=category)
                self._add_available_in_memory(name, category=category)
            self._states = {}
            self._group_defaults = {}
            self._recent_queries = []
            if schema >= 2:
                self._load_states(raw.get("entry_states", {}))
                self._load_group_defaults(raw.get("group_defaults", {}))
            else:
                self._import_assignment_mapping(assignments, source=str(self.storage_path),
                                                report_unmatched=False)
            recent_changed = False
            if schema >= 3:
                raw_recent = raw.get("recent_queries", [])
                if not isinstance(raw_recent, list):
                    raise ValueError("recent_queries must be a list")
                self._recent_queries = self._normalize_recent_queries(raw_recent, time.time())
                recent_changed = self._recent_queries != raw_recent
            report = raw.get("migration_report")
            if isinstance(report, dict):
                allowed = TagMigrationReport.__dataclass_fields__
                self.migration_report = TagMigrationReport(
                    **{key: value for key, value in report.items() if key in allowed}
                )
            if schema < SCHEMA_VERSION:
                self.migration_report.schema_migrated_from = schema
            if schema < SCHEMA_VERSION or recent_changed:
                if schema < SCHEMA_VERSION:
                    try:
                        self._backup_before_taxonomy_migration()
                    except TagStoreError as exc:
                        # Keep the original JSON untouched and serve a compatible
                        # in-memory view until a backup can be created safely.
                        self._pending_taxonomy_migration_schema = schema
                        logger.warning("Tag taxonomy is using in-memory compatibility mode: %s", exc)
                    self._migrate_loaded_taxonomy(schema)
                if self._pending_taxonomy_migration_schema is None:
                    self._save()
        except Exception as exc:
            backup = self.storage_path.with_suffix(".json.bak")
            try:
                shutil.copy2(self.storage_path, backup)
            except OSError:
                backup = None
            suffix = f" A backup was saved to {backup}." if backup else ""
            raise TagStoreError(f"Could not load {self.storage_path}: {exc}.{suffix}") from exc

    def _load_states(self, states: object) -> None:
        if not isinstance(states, dict):
            raise ValueError("entry_states must be an object")
        for database_id, paths in states.items():
            if not isinstance(paths, dict):
                raise ValueError("entry_states database values must be objects")
            for relative_path, value in paths.items():
                identity = self._safe_identity(database_id, relative_path)
                if identity is None or not isinstance(value, dict):
                    raise ValueError("entry_states contains an invalid identity")
                self._states.setdefault(identity[0], {})[identity[1]] = self._clean_state(value)

    def _load_group_defaults(self, groups: object) -> None:
        if not isinstance(groups, dict):
            raise ValueError("group_defaults must be an object")
        for database_id, values in groups.items():
            if not isinstance(database_id, str) or not Path(database_id).is_absolute() or not isinstance(values, dict):
                raise ValueError("group_defaults contains an invalid database identity")
            for group_id, tags in values.items():
                if not isinstance(group_id, str) or not isinstance(tags, list):
                    raise ValueError("group_defaults contains an invalid group")
                self._group_defaults.setdefault(database_id, {})[group_id] = self._clean_tags(tags)

    def _clean_tags(self, values: Iterable[object]) -> list[str]:
        result: list[str] = []
        for value in values:
            name = self.normalize_name(value)
            if name and name in self._available and name not in result:
                result.append(name)
        return result

    def _clean_state(self, value: dict) -> dict[str, object]:
        for key in ("explicit_tags", "auto_tags", "suppressed_auto_tags"):
            if not isinstance(value.get(key, []), list):
                raise ValueError(f"entry state {key} must be a list")
        return {
            "explicit_tags": self._clean_tags(value.get("explicit_tags", [])),
            "auto_tags": self._clean_tags(value.get("auto_tags", [])),
            "suppressed_auto_tags": self._clean_tags(value.get("suppressed_auto_tags", [])),
            "initialized": bool(value.get("initialized", True)),
            "group_id": str(value.get("group_id", ".")),
            "flux_default": bool(value.get("flux_default", False)),
            "user_decided": bool(value.get("user_decided", bool(value.get("explicit_tags")))),
        }

    def _migrate_legacy(self) -> None:
        report = TagMigrationReport(sources_inspected=[str(path) for path in self.legacy_paths])
        self.migration_report = report
        for path in self.legacy_paths:
            if not path.exists() or not path.is_file():
                continue
            report.sources_found.append(str(path))
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
                self._import_legacy_value(raw, str(path))
            except Exception as exc:
                report.malformed_sources.append(f"{path}: {exc}")

    def _record_tag(self, value: object) -> None:
        name = self.normalize_name(value)
        if not name:
            return
        if name not in self.migration_report.discovered_tags:
            self.migration_report.discovered_tags.append(name)
        if name in self._available:
            if name not in self.migration_report.duplicate_tags_skipped:
                self.migration_report.duplicate_tags_skipped.append(name)
            return
        self._available.append(name)
        self.migration_report.imported_tags.append(name)

    def _import_legacy_value(self, raw: object, source: str) -> None:
        if isinstance(raw, list):
            for name in raw:
                self._record_tag(name)
            return
        if not isinstance(raw, dict):
            raise ValueError("legacy tag metadata must be an object or tag-name list")
        for key in ("available_tags", "tag_names", "tags"):
            values = raw.get(key)
            if isinstance(values, list):
                for name in values:
                    self._record_tag(name)
        assignments = raw.get("assignments", raw.get("tag_assignments"))
        if isinstance(assignments, dict):
            self._import_assignment_mapping(assignments, source=source, report_unmatched=True)
        elif isinstance(assignments, list):
            for index, record in enumerate(assignments):
                if not isinstance(record, dict):
                    self.migration_report.unmatched_assignments.append(f"{source} record {index}")
                    continue
                identity = self._safe_identity(record.get("database_id"), record.get("relative_path"))
                tags = record.get("tags")
                if identity is None or not isinstance(tags, list):
                    self.migration_report.unmatched_assignments.append(f"{source} record {index}")
                    continue
                self._merge_assignment(*identity, tags)
        elif assignments is not None:
            self.migration_report.unmatched_assignments.append(f"{source}: assignments")

    def _import_assignment_mapping(self, assignments: dict, *, source: str,
                                   report_unmatched: bool) -> None:
        for database_id, paths in assignments.items():
            if not isinstance(paths, dict):
                if report_unmatched:
                    self.migration_report.unmatched_assignments.append(f"{source}: {database_id}")
                continue
            for relative_path, tags in paths.items():
                identity = self._safe_identity(database_id, relative_path)
                if identity is None or not isinstance(tags, list):
                    if report_unmatched:
                        self.migration_report.unmatched_assignments.append(
                            f"{source}: {database_id} / {relative_path}"
                        )
                    continue
                self._merge_assignment(*identity, tags)

    def _merge_assignment(self, database_id: str, relative_path: str,
                          tags: list[object]) -> None:
        for value in tags:
            self._record_tag(value)
        valid = self._clean_tags(tags)
        self._states.setdefault(database_id, {})[relative_path] = {
            "explicit_tags": valid, "auto_tags": [], "suppressed_auto_tags": [],
            "initialized": True, "group_id": session_group_id(relative_path),
            "flux_default": is_flux_named(relative_path),
            "user_decided": True,
        }
        self.migration_report.migrated_assignments += 1

    def _add_available_in_memory(self, name: object, *, category: str | None = None) -> bool:
        normalized = self.normalize_name(name)
        if not normalized or normalized in self._available:
            return False
        self._available.append(normalized)
        self._categories[normalized] = category if category in TAG_CATEGORIES else _default_category(normalized)
        return True

    def _backup_before_taxonomy_migration(self) -> None:
        base = self.storage_path.with_name(self.storage_path.name + ".pre-taxonomy-v4.bak")
        backup = base
        suffix = 1
        while backup.exists():
            backup = base.with_name(f"{base.name}.{suffix}")
            suffix += 1
        try:
            shutil.copy2(self.storage_path, backup)
        except OSError as exc:
            raise TagStoreError(
                f"Cannot safely migrate Tag state: backup creation failed for {backup}: {exc}"
            ) from exc

    def _migrate_loaded_taxonomy(self, schema: int) -> None:
        """Normalize known legacy labels and preserve conflicting legacy values."""
        renamed: dict[str, str] = {}
        old_names = list(self._available)
        self._available = list(DEFAULT_TAGS)
        self._categories = {tag: _default_category(tag) for tag in DEFAULT_TAGS}
        for old_name in old_names:
            canonical = _canonical_tag_name(old_name)
            category = _default_category(canonical)
            if canonical not in self._available:
                self._available.append(canonical)
                self._categories[canonical] = category
            renamed[old_name] = canonical

        def convert(values: Iterable[object], *, enforce_single: bool,
                    preserve_conflicts: bool = False) -> list[str]:
            result: list[str] = []
            seen_single: set[str] = set()
            for value in values:
                old = self.normalize_name(value)
                name = renamed.get(old, _canonical_tag_name(old))
                category = self._categories.get(name, _default_category(name))
                if enforce_single and category in SINGLE_SELECT_CATEGORIES and category in seen_single:
                    if preserve_conflicts:
                        legacy_name = f"Legacy {category}: {name}"
                        self.migration_report.preserved_conflicts.append(
                            f"{category} value {name} retained as {legacy_name}"
                        )
                        if legacy_name not in self._available:
                            self._available.append(legacy_name)
                            self._categories[legacy_name] = "Other"
                        name = legacy_name
                    else:
                        continue
                elif enforce_single and category in SINGLE_SELECT_CATEGORIES:
                    seen_single.add(category)
                if name not in self._available:
                    self._available.append(name)
                    self._categories[name] = "Other"
                if name not in result:
                    result.append(name)
            return result

        for paths in self._states.values():
            for state in paths.values():
                for key in ("explicit_tags", "auto_tags", "suppressed_auto_tags"):
                    state[key] = convert(state.get(key, []), enforce_single=True, preserve_conflicts=True)
                effective = set(state["explicit_tags"]) | set(state["auto_tags"])
                if "Flux" in effective and "BG" in effective:
                    # Preserve both pieces of legacy information without violating
                    # the new storage rule. Flux retains priority for Flux-named data.
                    loser = "BG" if state.get("flux_default") else "Flux"
                    target = f"Legacy Data Analysis: {loser}"
                    self.migration_report.preserved_conflicts.append(
                        f"{loser} retained as {target} in legacy entry state"
                    )
                    if target not in self._available:
                        self._available.append(target)
                        self._categories[target] = "Other"
                    for key in ("explicit_tags", "auto_tags"):
                        if loser in state[key]:
                            state[key] = [target if tag == loser else tag for tag in state[key]]
        for groups in self._group_defaults.values():
            for group_id, tags in groups.items():
                converted = convert(tags, enforce_single=True, preserve_conflicts=True)
                if {"Flux", "BG"}.issubset(converted):
                    legacy_bg = "Legacy Data Analysis: BG"
                    self.migration_report.preserved_conflicts.append(
                        f"Group {group_id}: BG retained as {legacy_bg}"
                    )
                    if legacy_bg not in self._available:
                        self._available.append(legacy_bg)
                        self._categories[legacy_bg] = "Other"
                    converted[converted.index("BG")] = legacy_bg
                groups[group_id] = converted
        for query in self._recent_queries:
            query["tags"] = convert(query.get("tags", []), enforce_single=False)
        self._recent_queries = self._normalize_recent_queries(self._recent_queries, time.time())
        self._available = list(dict.fromkeys([*DEFAULT_TAGS, *self._available]))
        for tag in self._available:
            self._categories.setdefault(tag, _default_category(tag))
        if schema < SCHEMA_VERSION:
            self.migration_report.schema_migrated_from = schema

    @staticmethod
    def _effective(state: dict[str, object]) -> set[str]:
        return set(state["explicit_tags"]) | set(state["auto_tags"])

    def _ordered(self, tags: Iterable[str]) -> list[str]:
        values = set(tags)
        return [name for name in self._available if name in values]

    def _assignments_payload(self) -> dict[str, dict[str, list[str]]]:
        payload: dict[str, dict[str, list[str]]] = {}
        for database_id, paths in self._states.items():
            for relative_path, state in paths.items():
                effective = self._ordered(self._effective(state))
                if effective:
                    payload.setdefault(database_id, {})[relative_path] = effective
        return payload

    def _normalize_recent_queries(self, values: Iterable[object], now: float) -> list[dict[str, object]]:
        cutoff = now - RECENT_QUERY_MAX_AGE_SECONDS
        valid: list[dict[str, object]] = []
        seen: set[tuple[str, frozenset[str]]] = set()
        candidates: list[tuple[float, str, list[str]]] = []
        for value in values:
            if not isinstance(value, dict):
                continue
            mode = str(value.get("mode", "")).upper()
            tags = value.get("tags")
            used = value.get("last_used")
            if mode not in {"AND", "OR"} or not isinstance(tags, list):
                continue
            if any(not isinstance(tag, str) for tag in tags):
                continue
            if not isinstance(used, (int, float)) or float(used) < cutoff:
                continue
            normalized = self._clean_tags(tags)
            if not normalized or len(normalized) != len(set(tags)) or set(normalized) != set(tags):
                continue
            candidates.append((float(used), mode, self._ordered(normalized)))
        for used, mode, tags in sorted(candidates, key=lambda item: item[0], reverse=True):
            key = (mode, frozenset(tags))
            if key in seen:
                continue
            seen.add(key)
            valid.append({"tags": tags, "mode": mode, "last_used": used})
            if len(valid) == MAX_RECENT_QUERIES:
                break
        return valid

    def _save(self) -> None:
        if self._pending_taxonomy_migration_schema is not None:
            try:
                self._backup_before_taxonomy_migration()
            except TagStoreError as exc:
                logger.warning("Tag state remains read-only until a migration backup can be created: %s", exc)
                return
            self._pending_taxonomy_migration_schema = None
        payload = {
            "schema_version": SCHEMA_VERSION, "available_tags": self._available,
            "tag_categories": self._categories,
            "assignments": self._assignments_payload(), "entry_states": self._states,
            "group_defaults": self._group_defaults,
            "recent_queries": self._recent_queries,
            "migration_report": asdict(self.migration_report),
        }
        atomic_write_json(self.storage_path, payload)

    def list_tags(self) -> list[str]:
        with self._lock:
            return list(self._available)

    def categories(self) -> tuple[str, ...]:
        return TAG_CATEGORIES

    def category_for(self, name: str) -> str:
        with self._lock:
            if name not in self._categories:
                raise ValueError(f"Unknown Tag: {name}")
            return self._categories[name]

    def tags_by_category(self) -> dict[str, list[str]]:
        with self._lock:
            return {
                category: [name for name in self._available if self._categories.get(name) == category]
                for category in TAG_CATEGORIES
            }

    def create_tag(self, name: str, category: str = "Other") -> bool:
        with self._lock:
            if category not in TAG_CATEGORIES:
                raise ValueError(f"Unknown Tag category: {category}")
            name = _canonical_tag_name(name)
            if not self._add_available_in_memory(name, category=category):
                return False
            self._save()
            return True

    def set_tag_category(self, name: str, category: str, *, replace_conflicts: bool = False) -> None:
        if category not in TAG_CATEGORIES:
            raise ValueError(f"Unknown Tag category: {category}")
        with self._lock:
            if name not in self._categories:
                raise ValueError(f"Unknown Tag: {name}")
            if self._categories[name] == category:
                return
            conflicts: list[tuple[dict[str, object], str]] = []
            group_conflicts: list[tuple[list[str], str]] = []
            for paths in self._states.values():
                for state in paths.values():
                    if name not in self._effective(state):
                        continue
                    if category in SINGLE_SELECT_CATEGORIES:
                        for other in self._effective(state):
                            if other != name and self._categories.get(other) == category:
                                conflicts.append((state, other))
                    if category == "Data Analysis" and name in {"Flux", "BG"}:
                        other = "BG" if name == "Flux" else "Flux"
                        if other in self._effective(state) and self._categories.get(other) == "Data Analysis":
                            conflicts.append((state, other))
            for values in self._group_defaults.values():
                for tags in values.values():
                    if name not in tags:
                        continue
                    if category in SINGLE_SELECT_CATEGORIES:
                        group_conflicts.extend(
                            (tags, other) for other in tags
                            if other != name and self._categories.get(other) == category
                        )
                    if category == "Data Analysis" and name in {"Flux", "BG"}:
                        other = "BG" if name == "Flux" else "Flux"
                        if other in tags and self._categories.get(other) == "Data Analysis":
                            group_conflicts.append((tags, other))
            if (conflicts or group_conflicts) and not replace_conflicts:
                raise TagAssignmentConflict(
                    f"Moving {name!r} to {category} would create "
                    f"{len(conflicts) + len(group_conflicts)} assignment conflict(s)."
                )
            if replace_conflicts:
                for state, other in conflicts:
                    for key in ("explicit_tags", "auto_tags", "suppressed_auto_tags"):
                        state[key] = [tag for tag in state[key] if tag != other]
                for tags, other in group_conflicts:
                    tags[:] = [tag for tag in tags if tag != other]
            self._categories[name] = category
            self._save()

    def rename_tag(self, old_name: str, new_name: str) -> bool:
        old_name = self.normalize_name(old_name)
        new_name = self.normalize_name(new_name)
        with self._lock:
            if not old_name or old_name not in self._available or not new_name or new_name in self._available:
                return False
            self._available[self._available.index(old_name)] = new_name
            self._categories[new_name] = self._categories.pop(old_name, _default_category(new_name))
            for paths in self._states.values():
                for state in paths.values():
                    for key in ("explicit_tags", "auto_tags", "suppressed_auto_tags"):
                        state[key] = [new_name if name == old_name else name for name in state[key]]
            for groups in self._group_defaults.values():
                for group_id, tags in groups.items():
                    groups[group_id] = [new_name if name == old_name else name for name in tags]
            for query in self._recent_queries:
                query["tags"] = [new_name if name == old_name else name for name in query["tags"]]
            self._recent_queries = self._normalize_recent_queries(self._recent_queries, time.time())
            self._save()
            return True

    def assignment_count(self, name: str) -> int:
        with self._lock:
            return sum(name in self._effective(state)
                       for paths in self._states.values() for state in paths.values())

    def delete_tag(self, name: str) -> bool:
        name = self.normalize_name(name)
        with self._lock:
            if name not in self._available:
                return False
            self._available.remove(name)
            self._categories.pop(name, None)
            for paths in self._states.values():
                for state in paths.values():
                    for key in ("explicit_tags", "auto_tags", "suppressed_auto_tags"):
                        state[key] = [tag for tag in state[key] if tag != name]
            for groups in self._group_defaults.values():
                for group_id, tags in groups.items():
                    groups[group_id] = [tag for tag in tags if tag != name]
            self._recent_queries = [
                query for query in self._recent_queries if name not in query["tags"]
            ]
            self._save()
            return True

    def recent_queries(self, *, now: float | None = None) -> list[dict[str, object]]:
        with self._lock:
            normalized = self._normalize_recent_queries(
                self._recent_queries, time.time() if now is None else now
            )
            if normalized != self._recent_queries:
                self._recent_queries = normalized
                self._save()
            return [dict(query, tags=list(query["tags"])) for query in self._recent_queries]

    def move_data_identity(self, old_identity: str, new_identity: str, *,
                           database_id: str | None = None,
                           old_relative_path: str | None = None,
                           new_relative_path: str | None = None) -> bool:
        del old_identity, new_identity
        if database_id is None or old_relative_path is None or new_relative_path is None:
            return False
        with self._lock:
            paths = self._states.get(database_id, {})
            if new_relative_path in paths:
                raise ValueError("Tag state already exists for the destination filename.")
            if old_relative_path not in paths:
                return False
            state = paths.pop(old_relative_path)
            paths[new_relative_path] = state
            try:
                self._save()
            except Exception:
                paths.pop(new_relative_path, None)
                paths[old_relative_path] = state
                raise
            return True

    def record_query(self, tags: Iterable[str], mode: str, *,
                     now: float | None = None) -> None:
        mode = mode.upper()
        if mode not in {"AND", "OR"}:
            raise ValueError("Tag query mode must be AND or OR.")
        with self._lock:
            normalized = self._validate_tags(tags)
            if not normalized:
                raise ValueError("At least one Tag is required.")
            used = time.time() if now is None else float(now)
            key = (mode, frozenset(normalized))
            remaining = [
                query for query in self._recent_queries
                if (query["mode"], frozenset(query["tags"])) != key
            ]
            self._recent_queries = self._normalize_recent_queries(
                [{"tags": normalized, "mode": mode, "last_used": used}, *remaining], used
            )
            self._save()

    def clear_recent_queries(self) -> None:
        with self._lock:
            if self._recent_queries:
                self._recent_queries = []
                self._save()

    def tags_for(self, database_id: str, relative_path: str) -> set[str]:
        with self._lock:
            state = self._states.get(database_id, {}).get(relative_path)
            return self._effective(state) if state else set()

    def state_for(self, database_id: str, relative_path: str) -> dict[str, object]:
        with self._lock:
            state = self._states.get(database_id, {}).get(relative_path)
            return dict(state) if state else {}

    def group_defaults_for(self, database_id: str, group_id: str) -> set[str]:
        with self._lock:
            return set(self._group_defaults.get(database_id, {}).get(group_id, []))

    def set_group_defaults(self, database_id: str, group_id: str,
                           tags: Iterable[str]) -> None:
        if not Path(database_id).is_absolute():
            raise ValueError("Group defaults require an absolute database id.")
        with self._lock:
            normalized = self._validate_assignment_tags(tags)
            self._group_defaults.setdefault(database_id, {})[group_id] = normalized
            self._save()

    def _validate_tags(self, tags: Iterable[str]) -> list[str]:
        normalized: list[str] = []
        for value in tags:
            name = self.normalize_name(value)
            canonical = _canonical_tag_name(name)
            if canonical in self._available:
                name = canonical
            if not name or name in normalized:
                continue
            if name not in self._available:
                raise ValueError(f"Unknown Tag: {name}")
            normalized.append(name)
        return self._ordered(normalized)

    def normalize_tags(self, tags: Iterable[str]) -> set[str]:
        """Normalize known legacy spellings without applying assignment rules."""
        with self._lock:
            return set(self._validate_tags(tags))

    def _validate_assignment_tags(self, tags: Iterable[str]) -> list[str]:
        normalized = self._validate_tags(tags)
        by_category: dict[str, str] = {}
        for name in normalized:
            category = self._categories.get(name, "Other")
            if category in SINGLE_SELECT_CATEGORIES:
                if category in by_category and by_category[category] != name:
                    raise TagAssignmentConflict(f"Only one {category} Tag may be assigned to a Data entry.")
                by_category[category] = name
        if ({"Flux", "BG"}.issubset(normalized)
                and self._categories.get("Flux") == "Data Analysis"
                and self._categories.get("BG") == "Data Analysis"):
            raise TagAssignmentConflict("Flux and BG cannot both be assigned to the same Data entry.")
        return normalized

    def initialize_entries(self, database_id: str, entries: Iterable[object]) -> None:
        if not Path(database_id).is_absolute():
            raise ValueError("Smart defaults require an absolute database id.")
        from app.core.master_search import AUTO_TAG_MAX_DEPTH, folder_depth

        # Automatic Tags only reach files at most AUTO_TAG_MAX_DEPTH folders below the opened
        # database; deeper files (e.g. the rest of a huge shared folder) are left as they are.
        records = [(str(entry.relative_path), getattr(entry, "log_name", None), entry) for entry in entries
                   if folder_depth(str(entry.relative_path)) <= AUTO_TAG_MAX_DEPTH]
        with self._lock:
            changed = False
            groups = self._group_defaults.setdefault(database_id, {})
            # v0.10A assignments are explicit. Their union bootstraps one stable
            # session default, excluding Flux because Flux is entry-specific.
            for group_id in {session_group_id(path) for path, _, _ in records}:
                if group_id in groups:
                    continue
                inherited: set[str] = set()
                for path, _, _ in records:
                    state = self._states.get(database_id, {}).get(path)
                    if state and session_group_id(path) == group_id:
                        inherited.update(tag for tag in state["explicit_tags"] if tag != "Flux")
                if inherited:
                    groups[group_id] = self._ordered(inherited)
                    changed = True
            for relative_path, log_name, entry in records:
                group_id = session_group_id(relative_path)
                names = (relative_path, log_name or "", getattr(entry, "file_name", ""),
                         getattr(entry, "sweep_dimension", ""))
                metadata = getattr(entry, "metadata", None)
                metadata_text = " ".join(str(value) for value in metadata.values()) if isinstance(metadata, dict) else ""
                flux_default = (any(is_flux_named(name) for name in names if name)
                                or "flux" in metadata_text.casefold())
                candidates = set(groups.get(group_id, []))
                if flux_default and self._categories.get("Flux") == "Data Analysis":
                    candidates.discard("BG")
                    candidates.add("Flux")
                elif any(_BG_RE.search(name) for name in names if name) and self._categories.get("BG") == "Data Analysis":
                    candidates.discard("Flux")
                    candidates.add("BG")
                if any(_DEBG_RE.search(name) for name in names if name) and self._categories.get("De-background") == "Data Analysis":
                    candidates.add("De-background")
                paths = self._states.setdefault(database_id, {})
                state = paths.get(relative_path)
                explicit = set(state["explicit_tags"]) if state else set()
                if "Flux" in explicit:
                    candidates.discard("BG")
                elif "BG" in explicit:
                    candidates.discard("Flux")
                elif "Flux" in candidates:
                    candidates.discard("BG")
                elif {"Flux", "BG"}.issubset(candidates):
                    candidates.discard("BG")
                if state is None:
                    paths[relative_path] = {
                        "explicit_tags": [], "auto_tags": self._ordered(candidates),
                        "suppressed_auto_tags": [], "initialized": True,
                        "group_id": group_id, "flux_default": flux_default,
                        "user_decided": False,
                    }
                    changed = True
                    continue
                suppressed = set(state["suppressed_auto_tags"])
                explicit = set(state["explicit_tags"])
                if state.get("user_decided"):
                    desired_auto = set(state["auto_tags"]) & candidates - suppressed - explicit
                    suppressed.update(candidates - self._effective(state))
                    state["suppressed_auto_tags"] = self._ordered(suppressed)
                else:
                    desired_auto = candidates - suppressed - explicit
                if (set(state["auto_tags"]) != desired_auto or state.get("group_id") != group_id
                        or state.get("flux_default") != flux_default):
                    state["auto_tags"] = self._ordered(desired_auto)
                    state["group_id"] = group_id
                    state["flux_default"] = flux_default
                    state["initialized"] = True
                    changed = True
            if changed:
                self._save()

    def set_tags(self, database_id: str, relative_path: str,
                 tags: set[str] | list[str] | tuple[str, ...], *,
                 group_id: str | None = None, flux_default: bool | None = None) -> None:
        identity = self._safe_identity(database_id, relative_path)
        if identity is None:
            raise ValueError("Tag assignments require an absolute database id and relative file path.")
        with self._lock:
            selected = self._validate_assignment_tags(tags)
            database_id, relative_path = identity
            existing = self._states.get(database_id, {}).get(relative_path)
            resolved_group = group_id or (str(existing.get("group_id")) if existing else session_group_id(relative_path))
            resolved_flux = (bool(existing.get("flux_default")) if flux_default is None and existing
                             else bool(flux_default))
            groups = self._group_defaults.setdefault(database_id, {})
            established_defaults = False
            if resolved_group not in groups and selected:
                defaults = [name for name in selected if name != "Flux"]
                if defaults:
                    groups[resolved_group] = defaults
                    established_defaults = True
            candidates = set(groups.get(resolved_group, []))
            if resolved_flux and self._categories.get("Flux") == "Data Analysis":
                candidates.discard("BG")
                candidates.add("Flux")
            selected_set = set(selected)
            self._states.setdefault(database_id, {})[relative_path] = {
                "explicit_tags": selected, "auto_tags": [],
                "suppressed_auto_tags": self._ordered(candidates - selected_set),
                "initialized": True, "group_id": resolved_group,
                "flux_default": resolved_flux,
                "user_decided": True,
            }
            if established_defaults:
                for path, state in self._states[database_id].items():
                    if path == relative_path or state.get("group_id") != resolved_group or state.get("user_decided"):
                        continue
                    inherited = set(groups[resolved_group])
                    if state.get("flux_default") and "Flux" in self._available:
                        inherited.add("Flux")
                    inherited -= set(state["suppressed_auto_tags"]) | set(state["explicit_tags"])
                    state["auto_tags"] = self._ordered(inherited)
            self._save()

    def reload(self) -> None:
        with self._lock:
            self._load_current()
