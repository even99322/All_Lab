"""One-line hooks the rest of LabLogViewer calls when research happens. Never raises."""

from __future__ import annotations

from pathlib import Path


def research(kind: str, key, *, per_day: bool = False) -> None:
    try:
        from app.pet.manager import manager

        mgr = manager()
        if mgr is not None and key:
            key = str(key)
            prefix, sep, rest = key.rpartition("|")
            try:
                key = f"{prefix}{sep}{Path(rest).resolve()}"
            except (OSError, ValueError):
                pass
            mgr.award(kind, key, per_day)
    except Exception:
        pass
