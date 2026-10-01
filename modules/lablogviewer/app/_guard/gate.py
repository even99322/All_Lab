"""The only questions the rest of the app asks about locks.

    personal_colors_status()  -> (unlocked, remaining experiences, licence or None)
    relink_allowed()          -> licence granting the Re-link tool, or None
    migrate_allowed()         -> licence granting the Data Transfer window, or None
    note_operation(path)      -> count a data operation on ``path``
"""

from __future__ import annotations

import time

from app._guard import experience, license as licenses

_TTL = 30.0
_unlocked_cache: tuple[float, bool] | None = None
_listeners: list = []


def invalidate() -> None:
    global _unlocked_cache
    _unlocked_cache = None


def add_unlock_listener(callback) -> None:
    """``callback()`` runs when Personal colours become unlocked while the app is running."""
    _listeners.append(callback)


def personal_colors_status():
    granted = licenses.feature_granted("personal_colors")
    left = experience.remaining()
    return (granted is not None or left == 0), left, granted


def personal_colors_unlocked() -> bool:
    # Checked again where colours are saved and applied, not only where the page is drawn.
    global _unlocked_cache
    now = time.monotonic()
    if _unlocked_cache is None or now - _unlocked_cache[0] > _TTL:
        _unlocked_cache = (now, personal_colors_status()[0])
    return _unlocked_cache[1]


def relink_allowed():
    return licenses.feature_granted("relink")


def migrate_allowed():
    """Licence granting the Data Transfer window, or None."""
    return licenses.feature_granted("migrate")


def note_operation(source_path) -> bool:
    """Count one data operation; True if this file was a new experience."""
    try:
        was_locked = experience.remaining() > 0
        new = experience.record(str(source_path or ""))
    except Exception:
        return False
    if new and was_locked and experience.remaining() == 0:
        invalidate()
        for callback in tuple(_listeners):
            try:
                callback()
            except Exception:
                pass
    return new
