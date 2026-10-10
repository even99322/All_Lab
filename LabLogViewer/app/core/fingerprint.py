"""Content fingerprints for measurement files (history records survive moves).

History records (stars, tags, comments, marks, views, analysis sessions) are
keyed by the file's path. When files move (e.g. into a central database) the
paths change. For every file LabLogViewer opens, this module remembers what
the file *is*: size, a sampled SHA-256 (first / middle / last MiB), the full
SHA-256 and Labber's log name and creation time. A later re-link can match
old records to the moved files by content instead of by path.

Computed in a background thread; the HDF5 file is only read.
Stored in <data folder>/state/data_fingerprints.json.
"""

from __future__ import annotations

import hashlib
import os
import threading
from datetime import datetime
from pathlib import Path

from app.core.data_identity import stable_data_identity
from app.core.external_state import atomic_write_json, default_state_path, load_json_state

SAMPLE = 1024 * 1024
_lock = threading.Lock()
_pending: set[str] = set()


def store_path() -> Path:
    return default_state_path("data_fingerprints.json")


def sampled_sha256(path: str | os.PathLike, size: int | None = None) -> str:
    size = os.path.getsize(path) if size is None else size
    digest = hashlib.sha256(str(size).encode())
    with open(path, "rb") as stream:
        for offset in sorted({0, max(0, size // 2 - SAMPLE // 2), max(0, size - SAMPLE)}):
            stream.seek(offset)
            digest.update(stream.read(SAMPLE))
    return digest.hexdigest()


def full_sha256(path: str | os.PathLike) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(path: str | os.PathLike, log_name: str | None = None, created: float | None = None) -> dict:
    size = os.path.getsize(path)
    return {"size": size, "sample_sha256": sampled_sha256(path, size), "sha256": full_sha256(path),
            "file_name": Path(path).name, "log_name": log_name, "created": created,
            "recorded": datetime.now().isoformat(timespec="seconds")}


def load() -> dict:
    data = load_json_state(store_path(), {}).value
    return data if isinstance(data, dict) else {}


def lookup(identity: str) -> dict | None:
    value = load().get(identity)
    return value if isinstance(value, dict) else None


def remember(source_path: str | os.PathLike, log_name: str | None = None, created: float | None = None,
             *, background: bool = True) -> None:
    """Record the fingerprint of a local measurement (once per unchanged file)."""
    path = str(source_path)
    if path.startswith("lablogviewer-remote://") or not os.path.isfile(path):
        return
    identity = stable_data_identity(path)
    size = os.path.getsize(path)
    known = lookup(identity)
    if known is not None and known.get("size") == size:
        return
    with _lock:
        if identity in _pending:
            return
        _pending.add(identity)

    def work():
        try:
            entry = fingerprint(path, log_name, created)
            with _lock:
                data = load()
                data[identity] = entry
                atomic_write_json(store_path(), data)
        except OSError:
            pass
        finally:
            with _lock:
                _pending.discard(identity)

    if background:
        threading.Thread(target=work, name="llv-fingerprint", daemon=True).start()
    else:
        work()
