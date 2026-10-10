"""Pet journal: recent research (Lv.20), notification history (Lv.30) and research statistics (Lv.50).

Stored in ``<data folder>/state/pet_journal.json``; never touches measurement files. Entries are
recorded at every level, so unlocking a feature shows history that already exists. Essential alerts
are still shown immediately by the pet at every level; only the history list is a Lv.30 feature.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from app.core.external_state import atomic_write_json, default_state_path

MAX_RECENT = 40
MAX_NOTES = 120
RECENT_KINDS = ("measurement", "analysis", "export", "debackground", "operation")


def path() -> Path:
    return default_state_path("pet_journal.json")


def load() -> dict:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    data.setdefault("recent", [])
    data.setdefault("notes", [])
    data.setdefault("stats", {})
    data.setdefault("presets", {})
    return data


def save(data: dict) -> None:
    atomic_write_json(path(), data, indent=None)


def record_research(kind: str, key: str, title: str | None = None) -> None:
    """Recent research + daily statistics (called for every research hook, duplicates included)."""
    data = load()
    day = time.strftime("%Y-%m-%d")
    stats = data["stats"].setdefault(day, {})
    stats[kind] = int(stats.get(kind, 0)) + 1
    if kind in RECENT_KINDS and key:
        target = key.split("|", 1)[-1]
        old = next((r for r in data["recent"] if r.get("path") == target and r.get("kind") == kind), None)
        recent = [r for r in data["recent"] if r is not old]
        recent.insert(0, {"kind": kind, "path": target, "title": title or Path(target).name,
                          "time": time.time(), "pinned": bool(old and old.get("pinned"))})
        pinned = [r for r in recent if r.get("pinned")]
        loose = [r for r in recent if not r.get("pinned")][: max(0, MAX_RECENT - len(pinned))]
        data["recent"] = pinned + loose
    save(data)


def set_pinned(index: int, pinned: bool) -> None:
    data = load()
    if 0 <= index < len(data["recent"]):
        data["recent"][index]["pinned"] = bool(pinned)
        data["recent"].sort(key=lambda r: (not r.get("pinned"), -float(r.get("time", 0))))
        save(data)


def add_note(level: str, text: str) -> None:
    """level: "info", "success", "warning" or "error"."""
    data = load()
    data["notes"].insert(0, {"level": level, "text": str(text)[:300], "time": time.time(), "read": False})
    del data["notes"][MAX_NOTES:]
    save(data)


def unread_count() -> int:
    return sum(1 for n in load()["notes"] if not n.get("read"))


def mark_all_read() -> None:
    data = load()
    for n in data["notes"]:
        n["read"] = True
    save(data)


def clear_notes() -> None:
    data = load()
    data["notes"] = []
    save(data)


def summary() -> dict:
    """Counts for the dashboard: today, this month, total, research days."""
    data = load()
    today = time.strftime("%Y-%m-%d")
    month = today[:7]

    def add(into, counts):
        for k, v in counts.items():
            into[k] = into.get(k, 0) + int(v)
    out = {"today": {}, "month": {}, "total": {}, "days_month": 0, "days_total": 0}
    for day, counts in data["stats"].items():
        add(out["total"], counts)
        out["days_total"] += 1
        if day.startswith(month):
            add(out["month"], counts)
            out["days_month"] += 1
        if day == today:
            add(out["today"], counts)
    return out


def save_preset(name: str, quick_actions: list[str]) -> None:
    data = load()
    data["presets"][name] = list(quick_actions)
    save(data)


def presets() -> dict:
    return load()["presets"]
