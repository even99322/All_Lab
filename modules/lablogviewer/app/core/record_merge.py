"""Combine the record of one measurement file found under two paths (Re-link, Data Transfer).

``existing`` is what the new path already has, ``incoming`` is what the old path had.
Most "collisions" are records the program made by itself when the new path was
first opened (automatic Tags, the first view, a fingerprint); those give way to
the user's old work. Only two real user records that differ are reported.
"""

from __future__ import annotations

import copy

_TAG_LISTS = ("explicit_tags", "auto_tags", "suppressed_auto_tags")
# records that only remember how a file was last shown: the old user view wins
_VIEW_SNAPSHOTS = {"viewer_display_states.json", "three_d_states.json"}
_NAMED_LISTS = {"overlays.json", "axis_presets.json"}          # [{"name": ...}, ...]


def _empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _unique_name(name: str, taken: set[str]) -> str:
    index = 2
    while f"{name} ({index})" in taken:
        index += 1
    return f"{name} ({index})"


def _merge_named_list(existing: list, incoming: list) -> list:
    result = copy.deepcopy(existing)
    taken = {item.get("name") for item in result if isinstance(item, dict)}
    for item in incoming:
        if not isinstance(item, dict) or item in result:
            continue
        item = copy.deepcopy(item)
        if item.get("name") in taken:
            item["name"] = _unique_name(str(item.get("name")), taken)
        taken.add(item.get("name"))
        result.append(item)
    return result


def _merge_named_dict(existing: dict, incoming: dict) -> dict:
    result = copy.deepcopy(existing)
    for name, value in incoming.items():
        if name not in result:
            result[name] = copy.deepcopy(value)
        elif result[name] != value:
            result[_unique_name(name, set(result))] = copy.deepcopy(value)
    return result


def _user_tagged(state: dict) -> bool:
    return bool(state.get("user_decided")) or bool(state.get("explicit_tags")) \
        or bool(state.get("suppressed_auto_tags"))


def _merge_tag_state(existing: dict, incoming: dict):
    if not _user_tagged(incoming):
        return existing, False
    merged = copy.deepcopy(incoming)
    # group and Flux default describe the file's new place; the automatic ones are redone there
    for key in ("group_id", "flux_default", "auto_tags"):
        if key in existing:
            merged[key] = copy.deepcopy(existing[key])
    if not _user_tagged(existing):
        return merged, False
    for key in ("explicit_tags", "suppressed_auto_tags"):
        values = list(existing.get(key) or [])
        values += [tag for tag in incoming.get(key) or [] if tag not in values]
        merged[key] = values
    merged["suppressed_auto_tags"] = [t for t in merged["suppressed_auto_tags"]
                                      if t not in merged["explicit_tags"]]
    merged["user_decided"] = True
    if {"Flux", "BG"}.issubset(merged["explicit_tags"]):
        return existing, True                      # contradicting choices: the user decides
    return merged, False


def _marks_state_empty(context: dict) -> bool:
    if not isinstance(context, dict):
        return _empty(context)                     # an unknown shape counts as the user's
    state = context.get("state")
    if not isinstance(state, dict):
        return _empty(state)
    return not state.get("marks") and not state.get("annotations")


def _merge_marks(existing: dict, incoming: dict):
    have = dict((existing or {}).get("contexts") or {})
    conflict = False
    for key, value in ((incoming or {}).get("contexts") or {}).items():
        if key not in have or _marks_state_empty(have[key]):
            have[key] = copy.deepcopy(value)
        elif not _marks_state_empty(value) and have[key] != value:
            conflict = True                        # both carry Marks: numbering would clash
    return {**(existing or {}), "contexts": have}, conflict


def merge_record(name: str, key: str, existing, incoming):
    """Return ``(value to keep, real_conflict)`` for one record under one path."""
    if existing == incoming or _empty(incoming):
        return existing, False
    if _empty(existing):
        return copy.deepcopy(incoming), False
    if name == "data_fingerprints.json":
        return existing, False                     # describes the file now at the new path
    if name in _VIEW_SNAPSHOTS:
        return copy.deepcopy(incoming), False
    if name == "tags.json" and key == "entry_states" and isinstance(existing, dict) \
            and isinstance(incoming, dict):
        return _merge_tag_state(existing, incoming)
    if name == "tags.json" and key == "assignments" and isinstance(existing, list) \
            and isinstance(incoming, list):
        return existing + [t for t in incoming if t not in existing], False
    if name == "marks.json" and isinstance(existing, dict) and isinstance(incoming, dict):
        return _merge_marks(existing, incoming)
    if name in _NAMED_LISTS and isinstance(existing, list) and isinstance(incoming, list):
        return _merge_named_list(existing, incoming), False
    if name == "named_views.json" and isinstance(existing, dict) and isinstance(incoming, dict):
        return _merge_named_dict(existing, incoming), False
    if name == "comments.json" and isinstance(existing, str) and isinstance(incoming, str):
        if incoming.strip() in existing:
            return existing, False
        if existing.strip() in incoming:
            return incoming, False
        return f"{existing.rstrip()}\n\n{incoming.lstrip()}", False
    return existing, True
