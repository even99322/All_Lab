"""Re-link history records to measurement files that were moved (licence feature "relink").

History records are keyed by where a file was. When files are moved (for
example into a new unified database), their stars, tags, comments, marks,
views, 3D views and YIG sessions stay attached to the old paths. This module:

1. ``find_missing``   lists old paths that have records but no file any more;
2. ``match``          looks for each one in a chosen folder, by content:
                        "exact"   full SHA-256 equal to the remembered fingerprint
                        "guess"   same file name and size (no fingerprint to compare)
3. ``apply``          backs up the record files, then moves the records to the new paths;
4. ``undo``           puts the backup back.

Only the record files are changed; measurement files are only read.
Viewers must be closed while records are rewritten (the Browser's own stores
are reloaded afterwards).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.core.data_identity import stable_data_identity
from app.core.external_state import atomic_write_json
from app.core.record_merge import merge_record

SUFFIXES = {".hdf5", ".h5"}
# record file -> the key under which entries are stored by data identity ("" = top level)
BY_IDENTITY = {
    "viewer_display_states.json": "by_data", "marks.json": "datasets", "overlays.json": "by_data",
    "named_views.json": "by_data", "axis_presets.json": "by_data", "comments.json": "by_data",
    "three_d_states.json": "", "data_fingerprints.json": "",
}
BY_DATABASE = {"stars.json": ("by_database",), "comments.json": ("by_database",),
               "tags.json": ("entry_states", "assignments")}
RECORD_FILES = sorted({*BY_IDENTITY, *BY_DATABASE, "session.json"})   # only these are backed up / restored
BACKUP_FOLDER = "relink_backups"


@dataclass
class OldRecord:
    identity: str
    places: set[str] = field(default_factory=set)      # record files that mention it
    fingerprint: dict | None = None


@dataclass
class Match:
    old: OldRecord
    new_path: str
    confidence: str                                    # "exact" or "guess"
    selected: bool = True


@dataclass
class Result:
    backup: Path
    moved: int
    conflicts: list[str]


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _section(data, key):
    if not isinstance(data, dict):
        return None
    value = data if key == "" else data.get(key)
    return value if isinstance(value, dict) else None


def _identity(database_id: str, relative_path: str) -> str:
    return str(Path(database_id) / relative_path)


def _exists(identity: str) -> bool:
    return "://" not in identity and os.path.isfile(identity)


def find_missing(state_dir: Path) -> list[OldRecord]:
    """Old paths that still own records but whose file is gone (read only)."""
    state_dir = Path(state_dir)
    found: dict[str, OldRecord] = {}

    def note(identity: str, place: str):
        if not isinstance(identity, str) or not identity or _exists(identity) or "://" in identity:
            return
        found.setdefault(identity, OldRecord(identity)).places.add(place)

    for name, key in BY_IDENTITY.items():
        section = _section(_read(state_dir / name), key)
        for identity in section or {}:
            note(identity, name)
    for name, keys in BY_DATABASE.items():
        data = _read(state_dir / name)
        for key in keys:
            section = _section(data, key) or {}
            for database_id, entries in section.items():
                for relative in (entries if isinstance(entries, (list, dict)) else []):
                    note(_identity(database_id, relative), name)
    for identity, fingerprint in (_section(_read(state_dir / "data_fingerprints.json"), "") or {}).items():
        if identity in found and isinstance(fingerprint, dict):
            found[identity].fingerprint = fingerprint
    # The fingerprint list alone is not a record the user made.
    return sorted((r for r in found.values() if r.places - {"data_fingerprints.json"}),
                  key=lambda r: r.identity.casefold())


def _files(root: Path, cancel=None):
    for folder, _dirs, names in os.walk(root):
        if cancel is not None and cancel():
            return
        for name in names:
            if Path(name).suffix.lower() in SUFFIXES and not name.startswith("."):
                yield Path(folder) / name


def match(records: list[OldRecord], new_root: str | Path, progress=None, cancel=None) -> list[Match]:
    """Find each old record's file under ``new_root`` (files are only read)."""
    from app.core import fingerprint as fp

    files = list(_files(Path(new_root), cancel))
    by_size: dict[int, list[Path]] = {}
    by_name_size: dict[tuple[str, int], list[Path]] = {}
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        by_size.setdefault(size, []).append(path)
        by_name_size.setdefault((path.name.casefold(), size), []).append(path)
    hashes: dict[Path, tuple[str, str]] = {}
    results: list[Match] = []
    for index, record in enumerate(records):
        if cancel is not None and cancel():
            break
        if progress is not None:
            progress(index, len(records))
        known = record.fingerprint or {}
        found = None
        if known.get("sha256") and isinstance(known.get("size"), int):
            for path in by_size.get(known["size"], []):
                try:
                    if path not in hashes:
                        sample = fp.sampled_sha256(path, known["size"])
                        hashes[path] = (sample, "")
                    if hashes[path][0] != known.get("sample_sha256"):
                        continue
                    if not hashes[path][1]:
                        hashes[path] = (hashes[path][0], fp.full_sha256(path))
                    if hashes[path][1] == known["sha256"]:
                        found = Match(record, str(path), "exact")
                        break
                except OSError:
                    continue
        if found is None:
            name = Path(record.identity).name.casefold()
            size = known.get("size")
            candidates = (by_name_size.get((name, size), []) if isinstance(size, int)
                          else [p for (n, _s), ps in by_name_size.items() if n == name for p in ps])
            if len(candidates) == 1:
                found = Match(record, str(candidates[0]), "guess", selected=False)
        if found is not None:
            results.append(found)
    if progress is not None:
        progress(len(records), len(records))
    return results


def suggest_old_prefixes(records: list[OldRecord]) -> list[str]:
    """Folders worth offering as "old folder": the one the missing files share, then each folder above it."""
    groups: dict[str, list[str]] = {}
    for record in records:
        if "://" not in record.identity:
            parent = Path(record.identity).parent
            groups.setdefault(parent.anchor, []).append(str(parent))
    suggestions: list[str] = []
    for parents in groups.values():
        folder = Path(os.path.commonpath(parents))
        while len(folder.parts) > 2 and len(suggestions) < 12:     # never offer "/" or "/Users"
            suggestions.append(str(folder))
            folder = folder.parent
    return suggestions


def match_by_prefix(records: list[OldRecord], old_prefix: str | Path, new_prefix: str | Path) -> list[Match]:
    """The files kept their place inside a folder that moved (or a share mounted under a new name).

    Only file names and sizes are checked, nothing is read, so this is quick on a NAS.
    """
    old_prefix, new_prefix = Path(old_prefix), Path(new_prefix)
    results: list[Match] = []
    for record in records:
        path = Path(record.identity)
        if not path.is_relative_to(old_prefix):
            continue
        candidate = new_prefix / path.relative_to(old_prefix)
        try:
            size = candidate.stat().st_size
        except OSError:
            continue
        if not candidate.is_file():
            continue
        known = (record.fingerprint or {}).get("size")
        if isinstance(known, int) and known != size:
            continue                                  # same name, different file
        results.append(Match(record, str(candidate), "path"))
    return results


# -- rewrite -----------------------------------------------------------------------------
def _move_key(section: dict, old: str, new: str, conflicts: list[str], label: str, key: str = "",
              prefer_old: bool = False) -> bool:
    if old not in section:
        return False
    if new in section:
        merged, conflict = merge_record(label, key, section[new], section[old])
        if conflict:
            conflicts.append(f"{label}: {new}")
            if not prefer_old:
                return False
            merged = section[old]
        section[new] = merged
        section.pop(old)
        return True
    section[new] = section.pop(old)
    return True


def _database_of(identity: str, roots: list[str]) -> tuple[str, str] | None:
    path = Path(identity)
    for root in sorted(roots, key=len, reverse=True):
        try:
            return root, path.relative_to(root).as_posix()
        except ValueError:
            continue
    return None


def _replace_strings(value, mapping: dict[str, str]):
    if isinstance(value, dict):
        return {key: _replace_strings(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_strings(item, mapping) for item in value]
    if isinstance(value, str) and value in mapping:
        return mapping[value]
    return value


def _session_file(sessions: Path, identity: str) -> Path:
    return sessions / f"{hashlib.sha256(identity.encode('utf-8')).hexdigest()}.json"


def backups_dir(data_root: Path) -> Path:
    return Path(data_root) / BACKUP_FOLDER


def _rewrite(mapping: dict[str, str], state_dir: Path, sessions_dir: Path, new_roots: list[str],
             prefer_old: bool, write: bool) -> tuple[set[str], list[str]]:
    conflicts: list[str] = []
    moved: set[str] = set()
    for name, key in BY_IDENTITY.items():
        path = state_dir / name
        data = _read(path)
        section = _section(data, key)
        if not section:
            continue
        changed = False
        for old, new in mapping.items():
            if _move_key(section, old, new, conflicts, name, key, prefer_old):
                changed = True
                moved.add(old)
        if changed and write:
            atomic_write_json(path, data)

    for name, keys in BY_DATABASE.items():
        path = state_dir / name
        data = _read(path)
        if not isinstance(data, dict):
            continue
        changed = False
        for key in keys:
            section = _section(data, key)
            if section is None:
                continue
            roots = list(section)
            used_roots: set[str] = set()
            for old, new in mapping.items():
                where = _database_of(old, roots)
                if where is None:
                    continue
                old_root, old_relative = where
                new_where = next(((r, Path(new).relative_to(r).as_posix()) for r in new_roots
                                  if Path(new).is_relative_to(r)), None)
                if new_where is None:
                    continue
                new_root, new_relative = new_where
                entries = section.get(old_root)
                if isinstance(entries, list) and old_relative in entries:
                    target = section.setdefault(new_root, [])
                    entries.remove(old_relative)
                    if new_relative not in target:     # a star is a star: nothing to compare
                        target.append(new_relative)
                elif isinstance(entries, dict) and old_relative in entries:
                    target = section.setdefault(new_root, {})
                    if not isinstance(target, dict):
                        continue
                    if new_relative in target:
                        merged, conflict = merge_record(name, key, target[new_relative], entries[old_relative])
                        if conflict:
                            conflicts.append(f"{name}: {new}")
                            if not prefer_old:
                                continue
                            merged = entries[old_relative]
                        entries.pop(old_relative)
                        target[new_relative] = merged
                    else:
                        target[new_relative] = entries.pop(old_relative)
                else:
                    continue
                used_roots.add(new_root)
                if not entries and old_root != new_root:
                    section.pop(old_root, None)
                changed = True
                moved.add(old)
            for root in used_roots:
                if not section.get(root):
                    section.pop(root, None)
        if changed and write:
            atomic_write_json(path, data)

    session = _read(state_dir / "session.json")
    if isinstance(session, dict) and write:
        rewritten = _replace_strings(session, mapping)
        if rewritten != session:
            atomic_write_json(state_dir / "session.json", rewritten)

    for old, new in mapping.items():
        source, target = _session_file(sessions_dir, old), _session_file(sessions_dir, new)
        if not source.is_file():
            continue
        if target.exists():
            conflicts.append(f"YIG session: {new}")
            if not prefer_old:
                continue
        moved.add(old)
        if not write:
            continue
        content = _read(source)
        if content is not None:
            atomic_write_json(target, _replace_strings(content, {old: new, os.path.realpath(old): new}))
            source.unlink()
        else:
            os.replace(source, target)
    return moved, sorted(set(conflicts))


def _roots(new_database_id: str | list[str]) -> list[str]:
    return [new_database_id] if isinstance(new_database_id, str) else [r for r in new_database_id if r]


def preview_conflicts(matches: list[Match], state_dir: str | Path, sessions_dir: str | Path,
                      new_database_id: str | list[str]) -> list[str]:
    """Records the user made under both paths that differ (nothing is written)."""
    mapping = {m.old.identity: stable_data_identity(m.new_path) for m in matches if m.selected}
    return _rewrite(mapping, Path(state_dir), Path(sessions_dir), _roots(new_database_id),
                    prefer_old=False, write=False)[1]


def apply(matches: list[Match], state_dir: str | Path, sessions_dir: str | Path, new_database_id: str | list[str],
          data_root: str | Path, prefer_old: bool = False) -> Result:
    """Back up, then move every selected record to its new path.

    ``new_database_id`` may list several databases (the one open in the Browser, then
    the chosen folder): a file's stars and Tags go to the first one holding it, because
    the Browser looks them up under the database it has open. Records the program made
    by itself at the new path give way to the old ones; for two real, different records
    ``prefer_old`` picks the old one (otherwise the new path's record is kept).
    """
    state_dir, sessions_dir = Path(state_dir), Path(sessions_dir)
    chosen = [m for m in matches if m.selected]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = backups_dir(Path(data_root)) / stamp
    suffix = 1
    while backup.exists():
        suffix += 1
        backup = backups_dir(Path(data_root)) / f"{stamp}-{suffix}"
    (backup / "state").mkdir(parents=True)
    for name in RECORD_FILES:
        if (state_dir / name).is_file():
            shutil.copy2(state_dir / name, backup / "state" / name)
    if sessions_dir.is_dir():
        shutil.copytree(sessions_dir, backup / "sessions")
    mapping = {m.old.identity: stable_data_identity(m.new_path) for m in chosen}
    atomic_write_json(backup / "manifest.json", {
        "created": datetime.now().isoformat(timespec="seconds"), "new_database": _roots(new_database_id),
        "pairs": mapping, "had_sessions": sessions_dir.is_dir()})
    moved, conflicts = _rewrite(mapping, state_dir, sessions_dir, _roots(new_database_id), prefer_old, write=True)
    return Result(backup=backup, moved=len(moved), conflicts=conflicts)


def list_backups(data_root: str | Path) -> list[Path]:
    folder = backups_dir(Path(data_root))
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if (p / "manifest.json").is_file()), reverse=True)


def undo(backup: str | Path, state_dir: str | Path, sessions_dir: str | Path) -> None:
    """Put back the record files saved before a re-link."""
    backup, state_dir, sessions_dir = Path(backup), Path(state_dir), Path(sessions_dir)
    manifest = _read(backup / "manifest.json")
    if not isinstance(manifest, dict) or not (backup / "state").is_dir():
        raise ValueError("This is not a re-link backup.")
    for name in RECORD_FILES:
        if (backup / "state" / name).is_file():
            shutil.copy2(backup / "state" / name, state_dir / name)
    if manifest.get("had_sessions"):
        for new in manifest.get("pairs", {}).values():
            _session_file(sessions_dir, new).unlink(missing_ok=True)
        shutil.copytree(backup / "sessions", sessions_dir, dirs_exist_ok=True)
    (backup / "undone").write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
