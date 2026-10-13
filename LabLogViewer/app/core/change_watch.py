"""Cheap change detection for the opened database (auto refresh).

A snapshot records only (size, modification time) of every .hdf5 / .h5 file: no
file is opened, so it stays cheap even on shared storage. Quick checks list only a
few folders (the one on screen and those where measurements were written last);
a full walk runs now and then to catch new folders. The Browser re-reads only the
files a comparison reports as new or changed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

HDF5_SUFFIXES = (".hdf5", ".h5", ".nc")
Snapshot = dict[str, tuple[int, float]]          # relative path -> (size, mtime) as in LogEntry


def _record(root: Path, folder: Path, name: str, out: Snapshot) -> None:
    if not name.lower().endswith(HDF5_SUFFIXES) or name.startswith("."):
        return
    try:
        stat = (folder / name).stat()
    except OSError:
        return                                     # vanished or not readable right now
    out[(folder / name).relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime)


def _record_entry(root: Path, item: os.DirEntry, out: Snapshot) -> None:
    """Like ``_record`` from a directory listing entry: on Windows its size / time come with the
    listing, so a NAS folder needs one request, not one per file (v1.0.2)."""
    name = item.name
    if not name.lower().endswith(HDF5_SUFFIXES) or name.startswith("."):
        return
    try:
        if not item.is_file(follow_symlinks=False) and not (item.is_symlink() and item.is_file()):
            return
        stat = item.stat()
    except OSError:
        return
    out[Path(item.path).relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime)


def _listing(folder: str) -> list[os.DirEntry]:
    try:
        with os.scandir(folder) as entries:
            return list(entries)
    except OSError:
        return []                                  # the folder was removed or is unreachable


def full_snapshot(root: str | Path, folders_out: set[tuple[str, ...]] | None = None) -> Snapshot:
    """Every measurement file; ``folders_out`` also collects every folder (empty ones too)."""
    root = Path(root)
    out: Snapshot = {}
    pending = [str(root)]
    while pending:
        for item in _listing(pending.pop()):
            try:
                is_dir = item.is_dir(follow_symlinks=False) or (item.is_symlink() and item.is_dir())
            except OSError:
                continue
            if is_dir:
                if item.name.startswith("."):
                    continue
                pending.append(item.path)
                if folders_out is not None:
                    folders_out.add(Path(item.path).relative_to(root).parts)
            else:
                _record_entry(root, item, out)
    return out


def folders_on_disk(root: str | Path) -> set[tuple[str, ...]]:
    """Every folder under root (relative parts), empty ones included; files are not listed."""
    root = Path(root)
    found: set[tuple[str, ...]] = set()
    for folder, dirs, _names in os.walk(root, onerror=lambda _error: None):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        relative = Path(folder).relative_to(root)
        if str(relative) not in ("", "."):
            found.add(relative.parts)
    return found


def folder_snapshot(root: str | Path, folders: set[tuple[str, ...]]) -> Snapshot:
    """Only the files directly inside the given folders (relative to root)."""
    root = Path(root)
    out: Snapshot = {}
    for parts in folders:
        for item in _listing(str(root.joinpath(*parts))):
            _record_entry(root, item, out)
    return out


def folder_of(relative_path: str) -> tuple[str, ...]:
    parent = Path(relative_path).parent
    return () if str(parent) in ("", ".") else parent.parts


@dataclass
class Changes:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.added or self.changed or self.removed)


def compare(known: Snapshot, seen: Snapshot, folders: set[tuple[str, ...]] | None = None) -> Changes:
    """What differs between what the Browser shows (known) and what is on disk (seen).
    With ``folders`` only those folders were looked at: files elsewhere are not "removed"."""
    changes = Changes()
    for path, stamp in seen.items():
        if path not in known:
            changes.added.append(path)
        elif known[path] != stamp:
            changes.changed.append(path)
    for path in known:
        if path not in seen and (folders is None or folder_of(path) in folders):
            changes.removed.append(path)
    for values in (changes.added, changes.changed, changes.removed):
        values.sort()
    return changes


def hot_folders(known: Snapshot, current: tuple[str, ...] | None, count: int = 3) -> set[tuple[str, ...]]:
    """The folder on screen plus the folders whose files changed most recently."""
    newest = sorted(known.items(), key=lambda item: item[1][1], reverse=True)
    folders: set[tuple[str, ...]] = set() if current is None else {current}
    for path, _stamp in newest:
        if len(folders) >= count + (current is not None):
            break
        folders.add(folder_of(path))
    return folders
