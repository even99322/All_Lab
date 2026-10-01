"""Small, process-local diagnostics published by live Surface renderers."""

from __future__ import annotations

from threading import RLock
from time import monotonic


_lock = RLock()
_renderers: dict[int, tuple[float, dict[str, object]]] = {}


def publish_renderer_diagnostics(snapshot: dict[str, object]) -> None:
    renderer_id = snapshot.get("renderer_id")
    if not isinstance(renderer_id, int):
        return
    with _lock:
        _renderers[renderer_id] = (monotonic(), dict(snapshot))


def clear_renderer_diagnostics(renderer_id: int) -> None:
    with _lock:
        _renderers.pop(renderer_id, None)


def renderer_diagnostics() -> dict[str, object] | None:
    with _lock:
        if not _renderers:
            return None
        _, latest = max(_renderers.values(), key=lambda item: item[0])
        return dict(latest)
