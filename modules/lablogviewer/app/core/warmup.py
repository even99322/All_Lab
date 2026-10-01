"""Load slow libraries in the background after start-up.

Matplotlib is imported only when first needed (start-up stays quick), but its first import
can build a font cache (seconds on a new computer or a new installation). Doing that in a
background thread shortly after start keeps the first 2D plot, formula or network session
from stalling the window. Safe to call more than once.
"""

from __future__ import annotations

import threading

_started = threading.Event()


def warm_now() -> None:
    """Load Matplotlib and its font cache now, in this thread."""
    try:
        from matplotlib import font_manager
        from matplotlib import mathtext  # noqa: F401 - formula rendering

        font_manager.findfont("DejaVu Sans")
    except Exception:
        pass                                      # the normal import later reports any problem


def start_background_warmup() -> None:
    if _started.is_set():
        return
    _started.set()
    threading.Thread(target=warm_now, name="llv-warmup", daemon=True).start()
