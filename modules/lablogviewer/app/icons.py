"""Semantic, theme-aware application icons loaded from packaged resources.

UI code asks for a semantic name (``icon("reload")``); this module resolves
the packaged asset, recolors it for the current application theme at paint
time, and falls back to an empty icon (keeping control text) when an asset is
missing. Sharing an icon never shares behavior: callers keep their own actions.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QAbstractButton, QToolButton

from app.theme import current_theme_colors


logger = logging.getLogger(__name__)

# Star colors live in app/palette.py.
from app.palette import STAR_EDGE, STAR_FILL  # noqa: E402

ICON_FILES: dict[str, str] = {
    "open": "open.svg",
    "reload": "reload.svg",
    "tag": "tag.svg",
    "analysis": "analysis.svg",
    "theme_light": "theme_light.svg",
    "theme_dark": "theme_dark.svg",
    "theme_system": "theme_system.svg",
    "pointer": "pointer.svg",
    "drag_share": "drag_share.png",
    "trace": "trace.svg",
    "show_trace": "show_trace.svg",
    "show": "show.svg",
    "hide": "hide.svg",
    "save": "save.svg",
    "export": "export.svg",
    "maximize": "maximize.svg",
    "folder": "folder.svg",
    "folder_open": "folder_open.svg",
    "star": "star.svg",
    "star_filled": "star.svg",
    "filter": "filter.svg",
    "back": "back.svg",
    "debackground": "debackground.svg",
    "copy": "copy.svg",
    "view_all": "view_all.svg",
    "annotate": "annotate.svg",
    "pen": "pen.svg",
    "laser": "laser.svg",
    "clear": "clear.svg",
    "zoom": "zoom.svg",
}

_svg_sources: dict[str, bytes | None] = {}
_png_masks: dict[str, QImage | None] = {}
_pixmap_cache: dict[tuple, QPixmap] = {}
_missing: set[str] = set()
# Qt calls QIconEngine.isNull() on every paint and size-hint pass, and an item
# view re-checks every row's icon when one row changes. Parsing SVG there made
# large lists (e.g. the 855-row Trace Manager) quadratic, so validity and
# recolored renderers are cached by SVG content.
_svg_valid: dict[bytes, bool] = {}
_svg_renderers: dict[bytes, QSvgRenderer] = {}
_icons: dict[str, QIcon] = {}


def icon_directory() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        bundled = Path(bundle_root) / "icons"
        if bundled.is_dir():
            return bundled
    # <version folder>/icons, next to app/.
    return Path(__file__).resolve().parent.parent / "icons"


def missing_icons() -> tuple[str, ...]:
    return tuple(sorted(_missing))


def _report_missing(name: str, reason: str) -> None:
    if name not in _missing:
        _missing.add(name)
        logger.warning("Icon %r unavailable (%s); keeping the control's text.", name, reason)


def _personal_svg(name: str) -> bytes | None:
    try:
        from app.core.personal import icon_override

        return icon_override(name)
    except Exception:
        return None


def reload_personal_icons() -> None:
    """Forget cached artwork after the user replaced or restored an icon."""
    for cache in (_svg_sources, _png_masks, _pixmap_cache, _svg_valid, _svg_renderers):
        cache.clear()
    _missing.clear()
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        for widget in app.allWidgets():
            widget.update()


def _svg_bytes(name: str) -> bytes | None:
    if name not in _svg_sources:
        personal = _personal_svg(name)
        if personal is not None:
            _svg_sources[name] = personal
            return personal
        path = icon_directory() / ICON_FILES[name]
        try:
            _svg_sources[name] = path.read_bytes()
        except OSError as error:
            _svg_sources[name] = None
            _report_missing(name, str(error))
    return _svg_sources[name]


def _png_mask(name: str) -> QImage | None:
    if name not in _png_masks:
        image = QImage(str(icon_directory() / ICON_FILES[name]))
        if image.isNull():
            _png_masks[name] = None
            _report_missing(name, "image could not be read")
        else:
            _png_masks[name] = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    return _png_masks[name]


def is_available(name: str) -> bool:
    if name not in ICON_FILES:
        return False
    if ICON_FILES[name].endswith(".svg") or _personal_svg(name) is not None:
        source = _svg_bytes(name)
        if source is None:
            return False
        valid = _svg_valid.get(source)
        if valid is None:
            valid = _svg_valid[source] = QSvgRenderer(source).isValid()
        if not valid:
            _report_missing(name, "invalid SVG")
        return valid
    return _png_mask(name) is not None


def _svg_renderer(source: bytes) -> QSvgRenderer:
    renderer = _svg_renderers.get(source)
    if renderer is None:
        if len(_svg_renderers) > 256:
            _svg_renderers.clear()
        renderer = _svg_renderers[source] = QSvgRenderer(source)
        renderer.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
    return renderer


def _colors_for(name: str, mode: QIcon.Mode) -> tuple[str, str | None]:
    """Return (stroke, fill) for the current theme."""
    if name == "star_filled":
        return STAR_EDGE, STAR_FILL
    colors = current_theme_colors()
    if mode == QIcon.Mode.Disabled:
        return colors.secondary, None
    return colors.text, None


def _render(name: str, painter: QPainter, rect: QRectF, mode: QIcon.Mode) -> None:
    stroke, fill = _colors_for(name, mode)
    if ICON_FILES[name].endswith(".png") and _personal_svg(name) is None:
        mask = _png_mask(name)
        if mask is None:
            return
        side = min(rect.width(), rect.height())
        target = QRectF(0, 0, side, side)
        target.moveCenter(rect.center())
        tinted = QImage(mask.size(), QImage.Format.Format_ARGB32_Premultiplied)
        tinted.fill(Qt.GlobalColor.transparent)
        tint = QPainter(tinted)
        tint.drawImage(0, 0, mask)
        tint.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        tint.fillRect(tinted.rect(), QColor(stroke))
        tint.end()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(target, tinted)
        painter.restore()
        return
    source = _svg_bytes(name)
    if source is None:
        return
    source = source.replace(b"currentColor", stroke.encode("ascii"))
    if fill is not None:
        source = source.replace(b'fill="none" stroke=', f'fill="{fill}" stroke='.encode("ascii"))
    renderer = _svg_renderer(source)
    if not renderer.isValid():
        _report_missing(name, "invalid SVG")
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, rect)
    painter.restore()


class _ThemedIconEngine(QIconEngine):
    def __init__(self, name: str):
        super().__init__()
        self._name = name

    def clone(self) -> QIconEngine:
        return _ThemedIconEngine(self._name)

    def paint(self, painter: QPainter, rect, mode: QIcon.Mode, state: QIcon.State) -> None:
        _render(self._name, painter, QRectF(rect), mode)

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float) -> QPixmap:  # noqa: N802
        scale = max(float(scale), 1.0)
        width = max(1, round(size.width() * scale))
        height = max(1, round(size.height() * scale))
        key = (self._name, _colors_for(self._name, mode), width, height, scale)
        cached = _pixmap_cache.get(key)
        if cached is not None:
            return QPixmap(cached)
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        _render(self._name, painter, QRectF(0, 0, width, height), mode)
        painter.end()
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(scale)
        if len(_pixmap_cache) > 512:
            _pixmap_cache.clear()
        _pixmap_cache[key] = pixmap
        return QPixmap(pixmap)

    def actualSize(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QSize:  # noqa: N802
        return size

    def iconName(self) -> str:  # noqa: N802
        return f"lablog-{self._name}"

    def isNull(self) -> bool:  # noqa: N802
        return not is_available(self._name)


def icon(name: str, fallback: QIcon | None = None) -> QIcon:
    """Theme-aware icon for a semantic name; ``fallback`` or empty if unavailable."""
    if name not in ICON_FILES:
        _report_missing(name, "unknown semantic icon name")
        return fallback if fallback is not None else QIcon()
    if not is_available(name):
        return fallback if fallback is not None else QIcon()
    # The engine resolves theme colors at paint time, so one shared QIcon per
    # name stays correct across Light/Dark switches and lets Qt skip redundant
    # item updates when the same icon is set again.
    cached = _icons.get(name)
    if cached is None:
        cached = _icons[name] = QIcon(_ThemedIconEngine(name))
    return cached


def make_icon_only(
    button: QAbstractButton, name: str, text: str, size: int = 18, *, flat: bool = False,
) -> QAbstractButton:
    """Show only the icon; the control text stays as tooltip and accessible name.

    When the asset is unavailable the text is displayed instead.
    """
    rendered = icon(name)
    button.setIcon(rendered)
    button.setIconSize(QSize(size, size))
    button.setText(text)
    label = text.replace("&", "")
    button.setToolTip(label)
    button.setAccessibleName(label)
    if flat:
        button.setProperty("lvFlat", True)
    if isinstance(button, QToolButton):
        button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextOnly if rendered.isNull()
            else Qt.ToolButtonStyle.ToolButtonIconOnly
        )
    return button
