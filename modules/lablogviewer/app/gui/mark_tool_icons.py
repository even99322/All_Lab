"""Painter-generated scientific icons for the Mark Tool selector."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPen, QPixmap, QPolygonF

from app.core.mark_model import (
    CROSSHAIR, HORIZONTAL_LINE, POINT_MARK, RANGE, VERTICAL_LINE,
)
from app.theme import current_theme_colors


def _draw_tool(painter: QPainter, tool: str, size: float, color: QColor) -> None:
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(color, 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    mid = size / 2

    if tool == POINT_MARK:
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([
            QPointF(mid, 4), QPointF(size - 4, mid),
            QPointF(mid, size - 4), QPointF(4, mid),
        ]))
    elif tool == RANGE:
        painter.drawLine(QPointF(4, 3), QPointF(4, size - 3))
        painter.drawLine(QPointF(size - 4, 3), QPointF(size - 4, size - 3))
        painter.drawLine(QPointF(6, mid), QPointF(size - 6, mid))
        painter.drawLine(QPointF(6, mid), QPointF(9, mid - 3))
        painter.drawLine(QPointF(6, mid), QPointF(9, mid + 3))
        painter.drawLine(QPointF(size - 6, mid), QPointF(size - 9, mid - 3))
        painter.drawLine(QPointF(size - 6, mid), QPointF(size - 9, mid + 3))
    elif tool == HORIZONTAL_LINE:
        painter.drawLine(QPointF(3, mid), QPointF(size - 3, mid))
    elif tool == VERTICAL_LINE:
        painter.drawLine(QPointF(mid, 3), QPointF(mid, size - 3))
    elif tool == CROSSHAIR:
        painter.drawLine(QPointF(3, mid), QPointF(size - 3, mid))
        painter.drawLine(QPointF(mid, 3), QPointF(mid, size - 3))
        painter.setBrush(color)
        painter.drawEllipse(QRectF(mid - 1.5, mid - 1.5, 3, 3))


class _MarkToolIconEngine(QIconEngine):
    """Paints with the current theme's text color so icons stay visible in Dark."""

    def __init__(self, tool: str, size: int):
        super().__init__()
        self._tool = tool
        self._size = size

    def clone(self) -> QIconEngine:
        return _MarkToolIconEngine(self._tool, self._size)

    def paint(self, painter: QPainter, rect, mode: QIcon.Mode, state: QIcon.State) -> None:
        colors = current_theme_colors()
        color = QColor(colors.secondary if mode == QIcon.Mode.Disabled else colors.text)
        painter.save()
        painter.translate(rect.x(), rect.y())
        scale = min(rect.width(), rect.height()) / self._size
        painter.scale(scale, scale)
        _draw_tool(painter, self._tool, self._size, color)
        painter.restore()

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float) -> QPixmap:  # noqa: N802
        scale = max(float(scale), 1.0)
        pixmap = QPixmap(max(1, round(size.width() * scale)), max(1, round(size.height() * scale)))
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, pixmap.rect(), mode, state)
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        return pixmap


def mark_tool_icon(tool: str, size: int = 20) -> QIcon:
    return QIcon(_MarkToolIconEngine(tool, size))
