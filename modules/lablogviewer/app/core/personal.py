"""Personal appearance: user colours and user icons (app/palette.py stays as shipped).

Colours: overrides per group (light / dark application theme, white / dark
scientific plots, annotation pens), stored in <data folder>/state/personal.json
and applied on top of the palette defaults.

Icons: an uploaded SVG replaces a built-in icon. Only plain SVG is accepted:
no scripts, event handlers, external links, embedded images, foreign
content or DTD / entities; at most 256 KB; it must render.
"""

from __future__ import annotations

import dataclasses
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from app.core.external_state import atomic_write_json, default_state_path, load_json_state

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_SVG_BYTES = 256 * 1024

# group key -> (label key, palette object / dict name)
GROUPS = ("app_light", "app_dark", "plot_white", "plot_dark", "pens")

_cache: dict | None = None
_listeners: list = []


def store_path() -> Path:
    return default_state_path("personal.json")


def icon_folder() -> Path:
    return default_state_path("personal_icons")


def _data() -> dict:
    global _cache
    if _cache is None:
        value = load_json_state(store_path(), {}).value
        _cache = value if isinstance(value, dict) else {}
        _cache.setdefault("colors", {})
        _cache.setdefault("icons", {})
    return _cache


def _save() -> None:
    atomic_write_json(store_path(), _data())
    for callback in tuple(_listeners):
        try:
            callback()
        except Exception:
            pass


def add_listener(callback) -> None:
    _listeners.append(callback)


def reset_cache() -> None:
    global _cache
    _cache = None


# -- colours -------------------------------------------------------------------------
def defaults(group: str) -> dict[str, str]:
    from app import palette

    if group in {"app_light", "app_dark", "plot_white", "plot_dark"}:
        base = {"app_light": palette.APP_LIGHT, "app_dark": palette.APP_DARK,
                "plot_white": palette.PLOT_WHITE, "plot_dark": palette.PLOT_DARK}[group]
        return {f.name: getattr(base, f.name) for f in dataclasses.fields(base)}
    if group == "pens":
        return dict(palette.ANNOTATION["pens"], laser=palette.ANNOTATION["laser"])
    raise KeyError(group)


def overrides(group: str) -> dict[str, str]:
    value = _data()["colors"].get(group, {})
    return {k: v for k, v in value.items() if isinstance(v, str) and HEX.match(v)} if isinstance(value, dict) else {}


def color(group: str, key: str) -> str:
    return overrides(group).get(key) or defaults(group)[key]


class ColorsLocked(PermissionError):
    """Personal colours unlock after 10 data operation experiences or with a licence."""


def colors_unlocked() -> bool:
    from app._guard import gate

    return gate.personal_colors_unlocked()


def set_color(group: str, key: str, value: str) -> None:
    if not colors_unlocked():
        raise ColorsLocked("Personal colours are locked.")
    if key not in defaults(group):
        raise KeyError(key)
    if not HEX.match(value):
        raise ValueError("Use a colour code like #1A2B3C.")
    _data()["colors"].setdefault(group, {})[key] = value.upper()
    _save()


def reset_colors(group: str | None = None, key: str | None = None) -> None:
    colors = _data()["colors"]
    if group is None:
        colors.clear()
    elif key is None:
        colors.pop(group, None)
    else:
        colors.get(group, {}).pop(key, None)
    _save()


def effective(group: str, base):
    """``base`` (a palette dataclass) with this user's overrides applied (only once unlocked)."""
    if not overrides(group) or not colors_unlocked():
        return base
    changes = {k: v for k, v in overrides(group).items() if hasattr(base, k)}
    return dataclasses.replace(base, **changes) if changes else base


def contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast ratio between two #RRGGBB colours (1 .. 21)."""
    def luminance(value: str) -> float:
        channels = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    a, b = sorted((luminance(first), luminance(second)), reverse=True)
    return (a + 0.05) / (b + 0.05)


# -- icons -----------------------------------------------------------------------------
class SvgRejected(ValueError):
    pass


_FORBIDDEN_TAGS = {"script", "foreignobject", "image", "iframe", "object", "embed", "audio", "video"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def validate_svg(data: bytes) -> None:
    """Raise :class:`SvgRejected` unless ``data`` is a plain, self-contained SVG."""
    if len(data) > MAX_SVG_BYTES:
        raise SvgRejected("The SVG is larger than 256 KB.")
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data.lower():
        raise SvgRejected("DTD / entity declarations are not allowed.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise SvgRejected(f"Not valid SVG/XML: {error}") from None
    if _local(root.tag) != "svg":
        raise SvgRejected("The file is not an SVG image.")
    for element in root.iter():
        tag = _local(element.tag)
        if tag in _FORBIDDEN_TAGS:
            raise SvgRejected(f"<{tag}> is not allowed in icons.")
        for name, value in element.attrib.items():
            local = _local(name)
            if local.startswith("on"):
                raise SvgRejected(f"Event attribute '{local}' is not allowed.")
            if local == "href" and not value.strip().startswith("#"):
                raise SvgRejected("External links are not allowed.")
            if "url(" in value.lower() and not re.search(r"url\(\s*#", value, re.IGNORECASE):
                raise SvgRejected("External references are not allowed.")
        if tag == "style" and element.text and ("@import" in element.text or "url(" in element.text.lower()
                                                and not re.search(r"url\(\s*#", element.text, re.I)):
            raise SvgRejected("External styles are not allowed.")
    from PySide6.QtSvg import QSvgRenderer

    if not QSvgRenderer(data).isValid():
        raise SvgRejected("The SVG could not be drawn.")


_COLOR_ATTR = re.compile(r'((?:fill|stroke)\s*[=:]\s*["\']?)(#[0-9A-Fa-f]{3,8}|rgba?\([^)]*\)|[a-zA-Z]+)')


def _to_current_color(data: bytes) -> bytes:
    text = data.decode("utf-8", "replace")

    def swap(match: re.Match) -> str:
        value = match.group(2)
        if value.lower() in {"none", "currentcolor", "transparent", "inherit"}:
            return match.group(0)
        return match.group(1) + "currentColor"

    return _COLOR_ATTR.sub(swap, text).encode("utf-8")


def set_icon(name: str, svg_path: str | Path, follow_theme: bool = True) -> None:
    source = Path(svg_path)
    if source.suffix.lower() != ".svg":
        raise SvgRejected("Only .svg files can be used as icons.")
    data = source.read_bytes()
    validate_svg(data)
    folder = icon_folder()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{name}.svg"
    target.write_bytes(data)
    _data()["icons"][name] = {"file": target.name, "follow_theme": bool(follow_theme)}
    _save()


def reset_icon(name: str | None = None) -> None:
    icons = _data()["icons"]
    names = list(icons) if name is None else [name]
    for key in names:
        entry = icons.pop(key, None)
        if entry:
            (icon_folder() / entry.get("file", "")).unlink(missing_ok=True)
    _save()


def icon_override(name: str) -> bytes | None:
    """SVG bytes to draw instead of the built-in icon, or None."""
    entry = _data()["icons"].get(name)
    if not isinstance(entry, dict):
        return None
    path = icon_folder() / str(entry.get("file", ""))
    try:
        data = path.read_bytes()
        validate_svg(data)                     # re-checked: the file could have been edited
    except (OSError, SvgRejected):
        return None
    return _to_current_color(data) if entry.get("follow_theme", True) else data


def icon_follows_theme(name: str) -> bool:
    entry = _data()["icons"].get(name)
    return bool(entry.get("follow_theme", True)) if isinstance(entry, dict) else True

