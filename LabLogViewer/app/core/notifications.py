"""Notifications (the bell in the Browser window and the bubble above the desktop pet).

Stored in ``<data folder>/state/notifications.json``: the most recent MAX_ITEMS notices plus the
user's choices (which kinds to receive, mute everything, versions to skip). A notice keeps its
parameters, not its text, so it is shown in the language in use when it is read.

Kinds: ``update`` (a newer version is available), ``measurement`` (a measurement finished),
``streak`` (days opened in a row), ``levelup`` (the pet reached a new level).
Nothing here uses the network or starts anything; callers decide when something happened.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.core.external_state import atomic_write_json, default_state_path

KINDS = ("update", "measurement", "streak", "levelup")
MAX_ITEMS = 50
PREVIEW_ITEMS = 3                  # update lines shown in the list; the rest sit behind "… more"
STREAK_DAYS = (3, 7, 14, 30, 60, 100, 200, 365)

_listeners: list[Callable[[str, dict | None], None]] = []


def store_path() -> Path:
    return default_state_path("notifications.json")


def _read() -> dict:
    try:
        data = json.loads(store_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load() -> dict:
    data = _read()
    items = [item for item in data.get("items", []) if isinstance(item, dict) and item.get("kind") in KINDS
             and isinstance(item.get("id"), int)]
    prefs = data.get("prefs") if isinstance(data.get("prefs"), dict) else {}
    kinds = prefs.get("kinds") if isinstance(prefs.get("kinds"), dict) else {}
    skipped = prefs.get("skipped") if isinstance(prefs.get("skipped"), list) else []
    return {
        "items": items[-MAX_ITEMS:],
        "seq": int(data.get("seq", 0)) if isinstance(data.get("seq", 0), int) else 0,
        "prefs": {
            "muted": bool(prefs.get("muted", False)),
            "kinds": {kind: bool(kinds.get(kind, True)) for kind in KINDS},
            "skipped": [str(v) for v in skipped],
            "update_folder": str(prefs.get("update_folder") or ""),
        },
    }


def _save(state: dict) -> None:
    state["items"] = state["items"][-MAX_ITEMS:]
    atomic_write_json(store_path(), state)


def add_listener(function: Callable[[str, dict | None], None]) -> None:
    if function not in _listeners:
        _listeners.append(function)


def remove_listener(function: Callable[[str, dict | None], None]) -> None:
    if function in _listeners:
        _listeners.remove(function)


def _tell(event: str, record: dict | None = None) -> None:
    for function in list(_listeners):
        try:
            function(event, record)
        except Exception:
            pass


def accepts(kind: str, state: dict | None = None) -> bool:
    prefs = (state or load())["prefs"]
    return kind in KINDS and not prefs["muted"] and prefs["kinds"].get(kind, True)


def add(kind: str, params: dict | None = None, *, details: dict | None = None, key: str | None = None,
        now: datetime | None = None) -> dict | None:
    """Record a notice; returns it, or None when that kind is off, everything is muted, or ``key`` was seen."""
    state = load()
    if not accepts(kind, state):
        return None
    if key is not None and any(item.get("key") == key for item in state["items"]):
        return None
    state["seq"] += 1
    record = {
        "id": state["seq"], "kind": kind, "params": dict(params or {}), "key": key,
        "time": (now or datetime.now()).isoformat(timespec="seconds"), "read": False,
        "details": details,
    }
    state["items"].append(record)
    _save(state)
    _tell("added", record)
    return record


def merge_details(key: str, extra: dict) -> dict | None:
    """Add ``extra`` to the details of the notice with ``key`` (v2.1.6: the hub's installer for an update notice
    the GitHub feed announced first, so "Update now" shows up). Returns the record, or None if there is none."""
    state = load()
    record = next((item for item in state["items"] if item.get("key") == key), None)
    if record is None:
        return None
    details = dict(record.get("details") or {})
    if all(details.get(k) == v for k, v in extra.items()):
        return record
    details.update(extra)
    record["details"] = details
    _save(state)
    _tell("changed")
    return record


def items() -> list[dict]:
    """Newest first."""
    return list(reversed(load()["items"]))


def unread_count() -> int:
    return sum(1 for item in load()["items"] if not item.get("read"))


def get(notice_id: int) -> dict | None:
    return next((item for item in load()["items"] if item["id"] == notice_id), None)


def mark_read(notice_id: int) -> None:
    state = load()
    changed = False
    for item in state["items"]:
        if item["id"] == notice_id and not item.get("read"):
            item["read"] = True
            changed = True
    if changed:
        _save(state)
        _tell("changed")


def mark_all_read() -> None:
    state = load()
    if any(not item.get("read") for item in state["items"]):
        for item in state["items"]:
            item["read"] = True
        _save(state)
        _tell("changed")


def remove(notice_id: int) -> None:
    state = load()
    kept = [item for item in state["items"] if item["id"] != notice_id]
    if len(kept) != len(state["items"]):
        state["items"] = kept
        _save(state)
        _tell("changed")


def clear() -> None:
    state = load()
    if state["items"]:
        state["items"] = []
        _save(state)
        _tell("changed")


def set_kind_enabled(kind: str, enabled: bool) -> None:
    state = load()
    if kind in KINDS:
        state["prefs"]["kinds"][kind] = bool(enabled)
        _save(state)
        _tell("changed")


def set_muted(muted: bool) -> None:
    state = load()
    state["prefs"]["muted"] = bool(muted)
    _save(state)
    _tell("changed")


def skip_version(version: str) -> None:
    state = load()
    if version not in state["prefs"]["skipped"]:
        state["prefs"]["skipped"].append(version)
        _save(state)


def update_folder() -> str:
    """The local folder the update check reads (empty: no check is made)."""
    return load()["prefs"]["update_folder"]


def set_update_folder(folder: str) -> None:
    state = load()
    state["prefs"]["update_folder"] = str(folder or "")
    _save(state)


def skipped_versions() -> list[str]:
    return load()["prefs"]["skipped"]


# -- the events ---------------------------------------------------------------------------------
def measurement_finished(name: str, points: int | None = None, *, key: str | None = None) -> dict | None:
    """A measurement finished (called by whatever learns of it, e.g. the Measurement program's link)."""
    return add("measurement", {"name": str(name), "points": int(points) if points else 0}, key=key)


def streak_reached(days: int) -> dict | None:
    """Days opened in a row; only the milestones in STREAK_DAYS (and every 100 after 365) are announced."""
    if days not in STREAK_DAYS and not (days > 365 and days % 100 == 0):
        return None
    return add("streak", {"days": int(days)}, key=f"streak:{int(days)}:{datetime.now():%Y-%m-%d}")


def pet_level_up(level: int) -> dict | None:
    return add("levelup", {"level": int(level)}, key=None)


# -- shaping the text ---------------------------------------------------------------------------
def update_lines(record: dict, language: str) -> list[str]:
    """Every change line of an update notice, in ``language`` (English when a line has no translation)."""
    lines = []
    details = record.get("details")
    entries = details.get("items") if isinstance(details, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        text = entry.get(language) or entry.get("en") or entry.get("zh") or ""
        if isinstance(text, str) and text:
            lines.append(text)
    return lines


def describe(record: dict, text: Callable[[str], str], language: str) -> dict:
    """What to show for a notice: title, body, the first lines of an update and whether more exist."""
    kind = record["kind"]
    params = dict(record.get("params") or {})
    if kind == "update":
        lines = update_lines(record, language)
        params.setdefault("n", len(lines))
        body_key = "notice.update.text" if lines else "notice.update.text_plain"
    else:
        lines = []
        body_key = f"notice.{kind}.text"
        if kind == "measurement" and not params.get("points"):
            body_key = "notice.measurement.text_plain"
    try:
        title = text(f"notice.{kind}.title").format(**params)
        body = text(body_key).format(**params)
    except (KeyError, IndexError, ValueError):
        title, body = text(f"notice.{kind}.title"), text(body_key)
    return {
        "title": title, "body": body, "lines": lines[:PREVIEW_ITEMS], "more": len(lines) > PREVIEW_ITEMS,
        "time": record.get("time", ""), "read": bool(record.get("read")),
    }
