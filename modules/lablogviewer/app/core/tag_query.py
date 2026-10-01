"""Boolean Tag retrieval over existing Browser entries."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar


T = TypeVar("T")


def matches_tag_query(entry_tags: Iterable[str], selected_tags: Iterable[str], mode: str) -> bool:
    selected = set(selected_tags)
    if not selected:
        return False
    available = set(entry_tags)
    normalized_mode = mode.upper()
    if normalized_mode == "AND":
        return selected <= available
    if normalized_mode == "OR":
        return bool(selected & available)
    raise ValueError("Tag query mode must be AND or OR.")


def query_entries(entries: Iterable[T], selected_tags: Iterable[str], mode: str,
                  tag_lookup: Callable[[T], Iterable[str]]) -> list[T]:
    """Return the original entry objects whose effective Tags satisfy the query."""
    selected = set(selected_tags)
    if not selected:
        return []
    return [entry for entry in entries if matches_tag_query(tag_lookup(entry), selected, mode)]
