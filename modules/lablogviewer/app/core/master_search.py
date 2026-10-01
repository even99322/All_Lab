"""Tag search across the largest data folder (Settings > General > Largest data folder).

Future data will live in one very large folder (tens of thousands to millions
of files). Searching it never walks the disk: it looks only at the Tag records
LabLogViewer already keeps (every database opened below that folder), so a
search costs time proportional to the tagged files, not to the folder size.

Each result keeps the database it belongs to, so stars, Tags, comments and
Rename work on it exactly as on a file of the open database.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.core.database_scanner import LogEntry
from app.core.tag_query import matches_tag_query

# Automatic Tags are only assigned to files at most this many folders below the opened database.
AUTO_TAG_MAX_DEPTH = 4


@dataclass
class MasterEntry(LogEntry):
    database_id: str = ""
    source_folder: str = ""          # folder relative to the largest data folder


def folder_depth(relative_path: str) -> int:
    parent = Path(relative_path.replace("\\", "/")).parent
    return 0 if str(parent) in ("", ".") else len(parent.parts)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def search(tag_store, root: str | Path, tags: set[str], mode: str, index_store=None,
           check_exists: bool = True) -> list[MasterEntry]:
    """Tagged files under ``root`` whose Tags match (AND / OR), from Tag records only."""
    root = Path(root).expanduser().resolve()
    selected = set(tags)
    if not selected:
        return []
    by_root = getattr(index_store, "_by_root", {}) if index_store is not None else {}
    results: list[MasterEntry] = []
    with tag_store._lock:
        states = {db: dict(paths) for db, paths in tag_store._states.items()}
    for database_id, paths in states.items():
        database = Path(database_id)
        if not _inside(database, root):
            continue
        cached = by_root.get(database_id, {}) if isinstance(by_root, dict) else {}
        for relative_path, state in paths.items():
            effective = set(state.get("explicit_tags", [])) | set(state.get("auto_tags", []))
            if not effective or not matches_tag_query(effective, selected, mode.upper()):
                continue
            absolute = database / relative_path
            if check_exists and not os.path.isfile(absolute):
                continue
            info = cached.get(relative_path, {}) if isinstance(cached, dict) else {}
            try:
                stat = absolute.stat() if check_exists else None
            except OSError:
                stat = None
            folder = absolute.parent.relative_to(root).as_posix() if _inside(absolute.parent, root) else ""
            results.append(MasterEntry(
                absolute_path=str(absolute), relative_path=relative_path, file_name=absolute.name,
                log_name=info.get("log_name") or absolute.stem, status="ok", error_message=None,
                size_bytes=int(info.get("size_bytes") or (stat.st_size if stat else 0)),
                mtime=float(info.get("mtime") or (stat.st_mtime if stat else 0.0)),
                creation_time=info.get("creation_time"), sweep_dimension=info.get("sweep_dimension") or "",
                metadata_complete=bool(info), database_id=database_id,
                source_folder="." if folder == "" else folder))
    results.sort(key=lambda e: (e.source_folder.lower(), e.log_name.lower()))
    return results
