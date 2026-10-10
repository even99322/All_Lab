"""The application icon (Dock / taskbar and every window), chosen in Settings > Appearance.

Seven designs ship in icons/app/app_icon_<n>.png (1024 px; also the source for the packaged
.icns / .ico); number 5 is the default. Number 0 means "my own picture", imported in Settings
and kept as custom_app_icon.png in the state folder. The 3D Surface window has its own
designs (icons/app/three_d_icon_<n>.png) and can instead follow the app icon. No licence or
experience is needed. The change shows at once in this process; the 3D and Figure Builder
processes follow when they reload settings.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon

COUNT = 7
DEFAULT = 5
CUSTOM = 0
THREE_D_COUNT = 3
THREE_D_DEFAULT = 3
CUSTOM_NAME = "custom_app_icon.png"
THREE_D_CUSTOM_NAME = "custom_3d_icon.png"
CUSTOM_SIZE = 1024
MIN_CUSTOM_SIZE = 32
ROLE = "main"        # the isolated 3D process sets "3d" so its Dock icon is the 3D icon
_cache: dict[int, QIcon] = {}
_three_d_cache: dict[int, QIcon] = {}
_custom_cache: dict[tuple[str, int], QIcon] = {}


def icon_path(number: int) -> Path:
    from app.icons import icon_directory

    return icon_directory() / "app" / f"app_icon_{number}.png"


def three_d_icon_path(number: int) -> Path:
    from app.icons import icon_directory

    return icon_directory() / "app" / f"three_d_icon_{number}.png"


def custom_icon_path(store=None, three_d: bool = False) -> Path:
    """Where the imported picture lives: beside the settings file (the state folder)."""
    name = THREE_D_CUSTOM_NAME if three_d else CUSTOM_NAME
    if store is not None and hasattr(store, "path"):
        return Path(store.path).parent / name
    from app.core.external_state import default_state_path

    return default_state_path(name)


def has_custom_icon(store=None, three_d: bool = False) -> bool:
    return custom_icon_path(store, three_d).is_file()


def app_icon(number: int, store=None) -> QIcon:
    if number == CUSTOM:
        path = custom_icon_path(store)
        if path.is_file():
            key = (str(path), path.stat().st_mtime_ns)
            if key not in _custom_cache:
                _custom_cache.pop(next((k for k in _custom_cache if k[0] == key[0]), None), None)
                _custom_cache[key] = QIcon(str(path))
            return _custom_cache[key]
        number = DEFAULT
    number = number if 1 <= int(number) <= COUNT else DEFAULT
    if number not in _cache:
        _cache[number] = QIcon(str(icon_path(number)))
    return _cache[number]


def three_d_icon(number: int, store=None) -> QIcon:
    if number == CUSTOM:
        path = custom_icon_path(store, True)
        if path.is_file():
            key = (str(path), path.stat().st_mtime_ns)
            if key not in _custom_cache:
                _custom_cache.pop(next((k for k in _custom_cache if k[0] == key[0]), None), None)
                _custom_cache[key] = QIcon(str(path))
            return _custom_cache[key]
        number = THREE_D_DEFAULT
    number = number if 1 <= int(number) <= THREE_D_COUNT else THREE_D_DEFAULT
    if number not in _three_d_cache:
        _three_d_cache[number] = QIcon(str(three_d_icon_path(number)))
    return _three_d_cache[number]


def current_app_icon(store=None) -> QIcon:
    number = store.app_icon() if store is not None and hasattr(store, "app_icon") else DEFAULT
    return app_icon(number, store)


def current_three_d_icon(store=None) -> QIcon:
    """The 3D window's icon: its own design, or the app icon when set to follow it."""
    if store is not None and hasattr(store, "three_d_icon_sync") and store.three_d_icon_sync():
        return current_app_icon(store)
    number = store.three_d_icon() if store is not None and hasattr(store, "three_d_icon") else THREE_D_DEFAULT
    return three_d_icon(number, store)


def import_custom_icon(source: str | Path, store=None, three_d: bool = False) -> Path:
    """Turn a picture into the custom icon (square, transparent padding, at most 1024 px).

    Raises ValueError when the file is not a readable picture or is too small.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter

    image = QImage(str(source))
    if image.isNull():
        raise ValueError("not a picture")
    if min(image.width(), image.height()) < MIN_CUSTOM_SIZE:
        raise ValueError("picture too small")
    side = min(max(image.width(), image.height()), CUSTOM_SIZE)
    scaled = image.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    square = QImage(side, side, QImage.Format_ARGB32)
    square.fill(Qt.transparent)
    painter = QPainter(square)
    painter.drawImage((side - scaled.width()) // 2, (side - scaled.height()) // 2, scaled)
    painter.end()
    target = custom_icon_path(store, three_d)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp.png")
    if not square.save(str(temporary), "PNG"):
        raise ValueError("could not save the picture")
    temporary.replace(target)
    try:                                      # Windows shortcuts need an .ico
        from PIL import Image

        Image.open(target).convert("RGBA").save(
            target.with_suffix(".ico"), sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256) if s <= side])
    except Exception:
        pass
    return target


def apply_app_icon(store=None) -> int:
    """Use the chosen icon for the application (every window follows); returns its number.

    The 3D Surface window (and the whole 3D process) uses the 3D icon instead.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return DEFAULT
    number = store.app_icon() if store is not None and hasattr(store, "app_icon") else DEFAULT
    icon = app_icon(number, store)
    three_d = current_three_d_icon(store)
    if ROLE == "3d":
        icon = three_d
    if not icon.isNull():
        app.setWindowIcon(icon)
        for window in app.topLevelWidgets():
            own = three_d if type(window).__name__ == "ThreeDAnalysisWindow" else icon
            window.setWindowIcon(own)
    return number

