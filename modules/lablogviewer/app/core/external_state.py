"""Shared, small-footprint persistence primitives for external user state."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)
# A helper process (the isolated 3D window) may only write the files it owns;
# every other shared record stays owned by the main process.
_write_allowed = None


def restrict_writes(allowed_names: set[str] | None) -> None:
    """Allow atomic JSON writes only to files with these names (None = all)."""
    global _write_allowed
    _write_allowed = None if allowed_names is None else set(allowed_names)
# Folder used before v0.18D; its contents are copied into the data folder.
STATE_DIRECTORY_NAME = ".lablogviewer"


def default_state_path(filename: str) -> Path:
    """Return the standard external state location without touching HDF5.

    Lives in ``<data folder>/state`` (see :mod:`app.core.data_location`).
    """
    from app.core.data_location import state_path

    return state_path(filename)


@dataclass(frozen=True)
class JsonLoadResult:
    value: Any
    recovered_from_corruption: bool = False
    backup_path: Path | None = None


def backup_corrupt_state(path: str | Path) -> Path | None:
    """Preserve an unreadable document for recovery instead of deleting it."""
    source = Path(path)
    if not source.exists():
        return None
    backup = source.with_suffix(source.suffix + ".bak")
    try:
        shutil.copy2(source, backup)
        return backup
    except OSError as error:
        LOGGER.warning("Could not back up invalid external state %s: %s", source, error)
        return None


def load_json_state(path: str | Path, default: Any) -> JsonLoadResult:
    """Read one JSON document, retaining a backup if it is malformed."""
    source = Path(path)
    if not source.exists():
        return JsonLoadResult(default)
    try:
        return JsonLoadResult(json.loads(source.read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        backup = backup_corrupt_state(source)
        LOGGER.warning("Ignoring malformed external state %s: %s", source, error)
        return JsonLoadResult(default, recovered_from_corruption=True, backup_path=backup)


def _is_windows() -> bool:
    return os.name == "nt"


def _replace_with_retry(source: str, destination: Path, attempts: int = 12) -> None:
    """os.replace, retried briefly when Windows reports the target as in use.

    Antivirus scanners, OneDrive / search indexers or the 3D process reading the
    same record can hold a file open for a moment; Windows then refuses the
    replace with PermissionError. Waiting up to ~1.5 s rides that out.
    """
    delay = 0.02
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if not _is_windows() or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.25)


def atomic_write_json(path: str | Path, payload: Any) -> None:
    """Durably replace an external JSON document without partial writes."""
    destination = Path(path)
    if _write_allowed is not None and destination.name not in _write_allowed:
        LOGGER.debug("Skipped writing %s from a helper process", destination)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(payload, temporary, indent=2, ensure_ascii=False, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        _replace_with_retry(temporary_name, destination)
        if os.name != "nt":
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except OSError:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise
