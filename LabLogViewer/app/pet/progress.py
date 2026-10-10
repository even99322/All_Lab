"""Research EXP for the current pet (prototype; final tuning comes with the measurement software).

Design target: ~20 measurements of normal research a day reach Lv.50 in about half a year.
Opening a measurement for basic analysis is the small base unit; opening the program once a day
gives even less; operator-type (professional) analysis pays 1.5x the base, external analysis such
as YIG fitting 2.0x. Consecutive days with the program opened add a bonus: +2 % per 10-day block.

Anti-farm: every award has an identity key; the same key never pays twice (re-opening the same
measurement, repeating the same export of the same file on the same day, ...). Pet clicks, drags and
animations give 0 EXP. Results do not matter: a poor fit is still research activity.
"""

from __future__ import annotations

import hashlib
import time

from app.pet import registry

BASE = 10                   # basic data analysis: a measurement opened for the first time
TOTAL_EXP = 100_000
STREAK_BLOCK_DAYS = 10
STREAK_BONUS_PER_BLOCK = 0.02
WEIGHTS = {
    "daily": 3,                         # the program opened today (less than basic analysis)
    "measurement": BASE,                # basic data analysis (per file identity)
    "export": BASE,                     # data exported (per file + day)
    "operation": int(BASE * 1.5),       # operator-type analysis: transforms, formula, marks, zoom (per file)
    "analysis": int(BASE * 2.0),        # external analysis: YIG fitting ... (per file + kind + day)
    "debackground": int(BASE * 2.0),    # de-background result (per output file)
}
MILESTONES = (1, 10, 20, 30, 40, 50)
CAPACITY = {1: (5, 2), 10: (10, 3), 20: (20, 4), 30: (30, 5), 40: (40, 6), 50: (50, 8)}   # (backpack, quick actions)
_SEEN_LIMIT = 50_000


def exp_for_level(level: int) -> int:
    level = max(1, min(registry.MAX_LEVEL, int(level)))
    return int(round(TOTAL_EXP * ((level - 1) / (registry.MAX_LEVEL - 1)) ** 1.5))


def level_for_exp(exp: int) -> int:
    level = 1
    while level < registry.MAX_LEVEL and exp >= exp_for_level(level + 1):
        level += 1
    return level


def milestone(level: int) -> int:
    """Highest look / feature milestone (1, 10, ... 50) reached at ``level``."""
    return max(m for m in MILESTONES if m <= level)


def capacity(level: int) -> tuple[int, int]:
    return CAPACITY[milestone(level)]


def identity(kind: str, key: str, per_day: bool = False) -> str:
    raw = f"{kind}|{key}|{time.strftime('%Y-%m-%d') if per_day else ''}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:20]


def streak_multiplier(streak_days: int) -> float:
    return 1.0 + STREAK_BONUS_PER_BLOCK * (max(0, int(streak_days)) // STREAK_BLOCK_DAYS)


def note_open_day(state: dict, today: str | None = None) -> bool:
    """Update the consecutive-day streak; True the first time the program opens on ``today``."""
    import datetime as _dt

    today = today or time.strftime("%Y-%m-%d")
    last = state.get("last_open_day")
    if last == today:
        return False
    streak = int(state.get("streak", 0))
    try:
        gap = (_dt.date.fromisoformat(today) - _dt.date.fromisoformat(last)).days if last else None
    except ValueError:
        gap = None
    state["streak"] = streak + 1 if gap == 1 else 1
    state["last_open_day"] = today
    return True


def award(state: dict, kind: str, key: str, *, per_day: bool = False) -> int:
    """Add EXP for one research event to the current pet; returns EXP granted (0 if duplicate)."""
    pet = state.get("current")
    if pet is None or kind not in WEIGHTS or not key:
        return 0
    seen = state.setdefault("seen", [])
    ident = identity(kind, key, per_day)
    if ident in seen:
        return 0
    seen.append(ident)
    if len(seen) > _SEEN_LIMIT:
        del seen[: len(seen) - _SEEN_LIMIT]
    gain = max(1, int(round(WEIGHTS[kind] * streak_multiplier(state.get("streak", 0)))))
    exp = state.setdefault("exp", {})
    exp[pet] = int(exp.get(pet, 0)) + gain
    state["levels"][pet] = level_for_exp(exp[pet])
    return gain
