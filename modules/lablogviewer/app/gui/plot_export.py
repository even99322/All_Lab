"""Render already-displayed scientific plot surfaces for copy and image export.

This module intentionally operates on Qt widgets only.  The caller supplies
the currently rendered pyqtgraph surface(s), so exporting neither reparses a
Labber file nor rebuilds a scientific representation from source data.
"""

from __future__ import annotations

from app.palette import PLOT_WHITE
from contextlib import nullcontext
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QColor, QImage, QPainter, QPicture
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import QWidget


# These are output pixels, not a request to resize a pre-rendered screenshot.
# A small on-screen Viewer therefore receives more paint-engine samples rather
# than a blurred bitmap enlargement.
MIN_RASTER_EXPORT_WIDTH = 1920
MIN_RASTER_EXPORT_HEIGHT = 1080
MIN_RASTER_EXPORT_SCALE = 2
MAX_RASTER_EXPORT_SCALE = 6


@dataclass(frozen=True)
class PaneRenderSurface:
    """A scientific plot widget placed within a composite export rectangle."""

    widget: QWidget
    target: QRect
    image_renderer: Callable[[int], QImage] | None = None
    appearance_context: Callable[[], object] | None = None


def _valid_size(size: QSize) -> QSize:
    return QSize(max(1, size.width()), max(1, size.height()))


def high_quality_scale(size: QSize, *, minimum_width: int = MIN_RASTER_EXPORT_WIDTH,
                       minimum_height: int = MIN_RASTER_EXPORT_HEIGHT) -> int:
    """Choose a paint scale independent from the visible widget density."""
    logical = _valid_size(size)
    required = max(
        MIN_RASTER_EXPORT_SCALE,
        ceil(max(1, int(minimum_width)) / logical.width()),
        ceil(max(1, int(minimum_height)) / logical.height()),
    )
    return min(MAX_RASTER_EXPORT_SCALE, max(MIN_RASTER_EXPORT_SCALE, required))


def render_widget_image(widget: QWidget, *, scale: int | None = None) -> QImage:
    """Render one plot through the same high-quality vector pipeline."""
    source_size = _valid_size(widget.size())
    return render_composite_image(
        [PaneRenderSurface(widget=widget, target=QRect(0, 0, source_size.width(), source_size.height()))],
        source_size,
        scale=scale,
    )


def render_composite_image(surfaces: list[PaneRenderSurface], size: QSize,
                           *, scale: int | None = None,
                           background: str | QColor = PLOT_WHITE.background) -> QImage:
    """Compose high-resolution panes without scaling an on-screen screenshot."""
    logical_size = _valid_size(size)
    export_scale = high_quality_scale(logical_size) if scale is None else max(1, int(scale))
    image = QImage(
        logical_size.width() * export_scale,
        logical_size.height() * export_scale,
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.fill(QColor(background))
    painter = QPainter(image)
    try:
        # QPicture records the plot paint commands rather than screen pixels.
        # Replaying it once at the final density keeps curves, labels, grids,
        # and 2D color bars sharp without Qt's HiDPI widget-render quirks.
        painter.scale(export_scale, export_scale)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for surface in surfaces:
            if not surface.widget:
                continue
            context = surface.appearance_context() if surface.appearance_context else nullcontext()
            with context:
                if surface.image_renderer is not None:
                    rendered = surface.image_renderer(export_scale)
                    painter.drawImage(surface.target, rendered)
                    continue
                picture = QPicture()
                picture_painter = QPainter(picture)
                try:
                    surface.widget.render(picture_painter)
                finally:
                    picture_painter.end()
                painter.save()
                painter.translate(surface.target.x(), surface.target.y())
                painter.scale(
                    surface.target.width() / max(1, surface.widget.width()),
                    surface.target.height() / max(1, surface.widget.height()),
                )
                painter.drawPicture(0, 0, picture)
                painter.restore()
    finally:
        painter.end()
    return image


def write_png(image: QImage, path: str | Path) -> bool:
    return image.save(str(path), "PNG")


def write_raster_svg(image: QImage, path: str | Path) -> bool:
    """Wrap a native GPU image in SVG without implying vector 3D geometry."""
    if image.isNull():
        return False
    generator = QSvgGenerator()
    generator.setFileName(str(path))
    generator.setSize(image.size())
    generator.setViewBox(QRect(0, 0, image.width(), image.height()))
    generator.setTitle("LabLogViewer 3D scene (raster image)")
    painter = QPainter(generator)
    try:
        painter.drawImage(0, 0, image)
    finally:
        painter.end()
    return Path(path).is_file() and Path(path).stat().st_size > 0


def write_svg(surfaces: list[PaneRenderSurface], size: QSize, path: str | Path,
              *, background: str | QColor = PLOT_WHITE.background) -> bool:
    """Write an SVG composition; plot callbacks are re-rendered at high DPI.

    Matplotlib callback content is embedded as a high-resolution raster image
    inside the SVG. Callers must not describe a multi-pane result as fully
    vector. Widgets without callbacks use Qt's native SVG paint engine.
    """
    logical_size = _valid_size(size)
    generator = QSvgGenerator()
    generator.setFileName(str(path))
    generator.setSize(logical_size)
    generator.setViewBox(QRect(0, 0, logical_size.width(), logical_size.height()))
    generator.setTitle("LabLogViewer scientific plot")
    export_scale = high_quality_scale(logical_size)
    painter = QPainter(generator)
    try:
        painter.fillRect(0, 0, logical_size.width(), logical_size.height(), QColor(background))
        for surface in surfaces:
            if not surface.widget:
                continue
            context = surface.appearance_context() if surface.appearance_context else nullcontext()
            with context:
                if surface.image_renderer is not None:
                    rendered = surface.image_renderer(export_scale)
                    painter.drawImage(surface.target, rendered)
                    continue
                painter.save()
                painter.translate(surface.target.x(), surface.target.y())
                x_scale = surface.target.width() / max(1, surface.widget.width())
                y_scale = surface.target.height() / max(1, surface.widget.height())
                painter.scale(x_scale, y_scale)
                surface.widget.render(painter)
                painter.restore()
    finally:
        painter.end()
    return Path(path).exists() and Path(path).stat().st_size > 0
