"""Pet choice and progress, stored in ``<data folder>/state/pet.json``.

Rule (levels are decided later): the first pet can be chosen freely; after that the user may
adopt a NEW pet only when the current one has reached MAX_LEVEL. Pets the user adopted before can
be switched back to at any time. A hidden pet unlocked by a licence can be chosen at any time, but
the licence does not open any other pet. Nothing here touches measurement data.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.external_state import atomic_write_json, default_state_path
from app.pet import registry


def store_path() -> Path:
    return default_state_path("pet.json")


def _read() -> dict:
    try:
        data = json.loads(store_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load() -> dict:
    data = _read()
    levels = data.get("levels") if isinstance(data.get("levels"), dict) else {}
    current = data.get("current") if registry.get(data.get("current")) else None
    adopted = [p for p in data.get("adopted", []) if registry.get(p)] if isinstance(data.get("adopted"), list) else []
    if current and current not in adopted:
        adopted.append(current)
    exp = {k: int(v) for k, v in (data.get("exp") or {}).items() if registry.get(k) and isinstance(v, (int, float))}
    quick = [a for a in data.get("quick_actions", ["yig", "export"]) if isinstance(a, str)] \
        if isinstance(data.get("quick_actions", []), list) else ["yig", "export"]
    return {
        "enabled": bool(data.get("enabled", current is not None)),
        "float_on_top": bool(data.get("float_on_top", True)),
        "current": current,
        "adopted": adopted,
        "exp": exp,
        "seen": [s for s in data.get("seen", []) if isinstance(s, str)] if isinstance(data.get("seen"), list) else [],
        "quick_actions": quick,
        "used_potions": [x for x in data.get("used_potions", []) if isinstance(x, str)] if isinstance(data.get("used_potions"), list) else [],
        "reselect_tokens": int(data.get("reselect_tokens", 0)) if isinstance(data.get("reselect_tokens", 0), (int, float)) else 0,
        "looks": {k: int(v) for k, v in (data.get("looks") or {}).items() if registry.get(k) and isinstance(v, (int, float))},
        "streak": int(data.get("streak", 0)) if isinstance(data.get("streak", 0), (int, float)) else 0,
        "last_open_day": data.get("last_open_day") if isinstance(data.get("last_open_day"), str) else None,
        "levels": {k: max(1, min(registry.MAX_LEVEL, int(v))) for k, v in levels.items()
                   if registry.get(k) and isinstance(v, (int, float))},
    }


def _machine() -> str:
    try:
        from app._guard.machine import machine_code

        return machine_code()
    except Exception:
        return ""


def _unlock_records() -> list[dict]:
    values = _read().get("unlocked_hidden")
    return [v for v in values if isinstance(v, dict) and isinstance(v.get("pet"), str)] if isinstance(values, list) else []


def unlocked_hidden() -> list[str]:
    """Hidden pets unlocked ON THIS COMPUTER (records copied from another computer do not count)."""
    here = _machine()
    return [r["pet"] for r in _unlock_records() if here and r.get("machine") == here]


def remember_unlock(pet_id: str) -> None:
    here = _machine()
    if not here or pet_id in unlocked_hidden():
        return
    data = _read()
    data["unlocked_hidden"] = _unlock_records() + [{"pet": pet_id, "machine": here}]
    atomic_write_json(store_path(), {"version": 1, **data})


def save(state: dict) -> None:
    keep = {"unlocked_hidden": _unlock_records()}
    atomic_write_json(store_path(), {"version": 1, **keep, **state})


def level_of(state: dict, pet_id: str) -> int:
    return int(state["levels"].get(pet_id, 1))


def can_choose(state: dict, pet_id: str) -> tuple[bool, str]:
    """(allowed, reason key) for adopting ``pet_id`` now."""
    info = registry.get(pet_id)
    if info is None:
        return False, "pet.reason.unknown"
    if info.hidden:
        if not registry.hidden_unlocked(info):
            return False, "pet.reason.hidden_locked"
        return True, ""
    current = state.get("current")
    if current is None or current == pet_id or pet_id in state.get("adopted", []):
        return True, ""                                   # first pet, or one adopted before
    regular = [p for p in state.get("adopted", []) if not registry.get(p).hidden]
    if not regular or all(level_of(state, p) >= registry.MAX_LEVEL for p in regular):
        return True, ""                                   # every regular pet so far is fully raised
    if int(state.get("reselect_tokens", 0)) > 0:
        return True, ""                                   # a growth potion granted a re-selection
    return False, "pet.reason.raise_current"


def choose(pet_id: str) -> dict:
    state = load()
    allowed, reason = can_choose(state, pet_id)
    if not allowed:
        raise PermissionError(reason)
    info = registry.get(pet_id)
    if pet_id not in state["adopted"] and not info.hidden and state.get("reselect_tokens", 0) > 0:
        regular = [p for p in state["adopted"] if not registry.get(p).hidden]
        if regular and not all(level_of(state, p) >= registry.MAX_LEVEL for p in regular):
            state["reselect_tokens"] -= 1                 # this adoption used the re-selection chance
    state["current"] = pet_id
    state["enabled"] = True
    if pet_id not in state["adopted"]:
        state["adopted"].append(pet_id)
    state["levels"].setdefault(pet_id, 1)
    save(state)
    return state


def set_enabled(enabled: bool) -> dict:
    state = load()
    state["enabled"] = bool(enabled) and state["current"] is not None
    save(state)
    return state


def set_float_on_top(on: bool) -> dict:
    state = load()
    state["float_on_top"] = bool(on)
    save(state)
    return state


def look_of(state: dict, pet_id: str) -> int:
    """Look (milestone 1/10/.../50) shown for ``pet_id``: chosen by the user, never above the level reached."""
    from app.pet import progress

    top = progress.milestone(level_of(state, pet_id))
    chosen = int(state.get("looks", {}).get(pet_id, top))
    return progress.milestone(min(chosen, top))


def set_look(pet_id: str, look: int) -> dict:
    state = load()
    state.setdefault("looks", {})[pet_id] = int(look)
    save(state)
    return state


def apply_potions(potions) -> list[str]:
    """Apply unused growth potions ([(licence id, features)]) once; returns the features used."""
    from app.pet import progress

    state = load()
    used = []
    for licence_id, features in potions:
        if licence_id in state["used_potions"]:
            continue
        needs_pet = any(not f == "pet_reselect" for f in features)
        if needs_pet and state["current"] is None:
            continue                                       # keep it until a pet is adopted
        for f in features:
            if f.startswith("pet_exp_"):
                pet = state["current"]
                state["exp"][pet] = int(state["exp"].get(pet, 0)) + int(f[8:])
                state["levels"][pet] = progress.level_for_exp(state["exp"][pet])
            elif f.startswith("pet_level_"):
                pet = state["current"]
                target = int(f[10:])
                state["exp"][pet] = progress.exp_for_level(target)
                state["levels"][pet] = target
            elif f == "pet_reselect":
                state["reselect_tokens"] = int(state.get("reselect_tokens", 0)) + 1
            used.append(f)
        state["used_potions"].append(licence_id)
    if used:
        save(state)
    return used
