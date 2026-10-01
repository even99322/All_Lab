"""The application icon (Dock / taskbar and every window), chosen in Settings > Appearance.

Four designs ship in icons/app/app_icon_<n>.png (1024 px; also the source for the
packaged .icns / .ico). No licence or experience is needed to change it. The change
shows at once in this process; the 3D and Figure Builder processes follow when they
reload settings. The icon of the packaged app file itself (Finder / Explorer) is fixed
when it is built.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon

COUNT = 4
DEFAULT = 4
_cache: dict[int, QIcon] = {}


def icon_path(number: int) -> Path:
    from app.icons import icon_directory

    return icon_directory() / "app" / f"app_icon_{number}.png"


def app_icon(number: int) -> QIcon:
    number = number if 1 <= int(number) <= COUNT else DEFAULT
    if number not in _cache:
        _cache[number] = QIcon(str(icon_path(number)))
    return _cache[number]


def apply_app_icon(store=None) -> int:
    """Use the chosen icon for the application (every window follows); returns its number."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return DEFAULT
    number = store.app_icon() if store is not None and hasattr(store, "app_icon") else DEFAULT
    icon = app_icon(number)
    if not icon.isNull():
        app.setWindowIcon(icon)
        for window in app.topLevelWidgets():
            window.setWindowIcon(icon)
    return number
