"""The system's fixed-width font by name (Menlo, Consolas ...). Asking Qt for a family it
does not have ("monospace", "Consolas" on macOS) makes it scan every font once (~0.2 s)."""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def mono_family() -> str:
    from PySide6.QtGui import QFontDatabase

    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


def mono_font(point_size: float | None = None):
    from PySide6.QtGui import QFont, QFontDatabase

    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    if point_size:
        font.setPointSizeF(point_size)
    return QFont(font)
