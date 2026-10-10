"""Data operation experience: how many distinct measurements the user has worked with.

One experience = one distinct local measurement file on which the user did a
real operation (placed a Mark, changed a transform, zoomed, ran a YIG fit ...).
Opening alone does not count; repeating operations on the same file counts once.

Stored in <data folder>/state/experience.json with an HMAC bound to this
machine; a hand-edited or copied file is not trusted (the count restarts).
Only hashes of the files are kept, never their paths.
The same file also keeps the latest time seen ("high water") so that turning
the clock back cannot revive an expired licence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from pathlib import Path

REQUIRED = 10
_SCHEMA = 1
_APP_SECRET = bytes.fromhex("6c6c762d6578702d3a9f41d2b87e05c3a4e16f2d9b8c7a0e5d4f3c2b1a09e8d7")
_lock = threading.Lock()
_cache: dict | None = None
_status = {"tampered": False}


def store_path() -> Path:
    from app.core.external_state import default_state_path

    return default_state_path("experience.json")


def _key() -> bytes:
    from app._guard.machine import machine_code

    return hashlib.sha256(_APP_SECRET + machine_code().encode("ascii")).digest()


def _mac(files: list[str], high_water: float) -> str:
    body = json.dumps({"v": _SCHEMA, "files": sorted(files), "high_water": round(float(high_water), 3)},
                      sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(_key(), body, hashlib.sha256).hexdigest()


def _file_token(identity: str) -> str:
    return hmac.new(_key(), b"file:" + identity.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    state = {"files": [], "high_water": 0.0}
    path = store_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            files = [str(f) for f in raw["files"]]
            high = float(raw["high_water"])
            if raw.get("v") == _SCHEMA and hmac.compare_digest(str(raw["mac"]), _mac(files, high)):
                state = {"files": files, "high_water": high}
            else:
                _status["tampered"] = True
        except (OSError, ValueError, KeyError, TypeError):
            _status["tampered"] = True
    _cache = state
    return state


def _save(state: dict) -> None:
    from app.core.external_state import atomic_write_json

    files = sorted(set(state["files"]))
    high = round(float(state["high_water"]), 3)
    try:
        atomic_write_json(store_path(), {"v": _SCHEMA, "files": files, "high_water": high,
                                         "mac": _mac(files, high)})
    except OSError:
        pass


def reset_cache() -> None:
    global _cache
    with _lock:
        _cache = None
        _status["tampered"] = False


def count() -> int:
    with _lock:
        return len(set(_load()["files"]))


def remaining() -> int:
    return max(0, REQUIRED - count())


def was_tampered() -> bool:
    with _lock:
        _load()
        return _status["tampered"]


def record(source_path: str) -> bool:
    """Count ``source_path`` (a local measurement) once. True if it was new."""
    from app.core.data_identity import stable_data_identity

    path = str(source_path or "")
    if not path or "://" in path or not Path(path).is_file():
        return False
    token = _file_token(stable_data_identity(path))
    with _lock:
        state = _load()
        if token in state["files"]:
            return False
        state["files"] = [*state["files"], token]
        state["high_water"] = max(state["high_water"], time.time())
        _save(state)
        return True


def trusted_now(now: float | None = None) -> float:
    """The current time, never earlier than the latest time this app has seen."""
    now = time.time() if now is None else float(now)
    with _lock:
        state = _load()
        if now > state["high_water"] + 3600:
            state["high_water"] = now
            _save(state)
        return max(now, state["high_water"])
