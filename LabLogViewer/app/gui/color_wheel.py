"""Colour wheel: hue ring around a saturation / value square (plus a hex field)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QConicalGradient, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget


class ColorWheel(QWidget):
    colorChanged = Signal(QColor)          # while dragging
    colorPicked = Signal(QColor)           # on release

    RING = 0.16                            # ring thickness / radius

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hue, self._sat, self._val = 0.0, 1.0, 1.0
        self._drag: str | None = None
        self.setMinimumSize(200, 200)
        self.setObjectName("personalColorWheel")

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API spelling
        return QSize(240, 240)

    # value -------------------------------------------------------------------------------
    def color(self) -> QColor:
        return QColor.fromHsvF(self._hue, self._sat, self._val)

    def setColor(self, color: QColor) -> None:  # noqa: N802 - Qt API spelling
        hue, sat, val, _alpha = color.getHsvF()
        if hue >= 0:                           # greys have no hue: keep the ring where it is
            self._hue = hue
        self._sat, self._val = sat, val
        self.update()

    # geometry ----------------------------------------------------------------------------
    def _center_radius(self) -> tuple[QPointF, float]:
        side = min(self.width(), self.height()) - 8
        return QPointF(self.width() / 2, self.height() / 2), side / 2

    def _square(self) -> QRectF:
        center, radius = self._center_radius()
        inner = radius * (1 - self.RING) - 6
        half = inner / math.sqrt(2)
        return QRectF(center.x() - half, center.y() - half, 2 * half, 2 * half)

    # painting ------------------------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.35)          # locked / nothing selected
        center, radius = self._center_radius()
        ring = QConicalGradient(center, 0)
        for step in range(13):
            ring.setColorAt(step / 12, QColor.fromHsvF((1 - step / 12) % 1.0, 1, 1))
        width = radius * self.RING
        painter.setPen(QPen(ring, width))
        painter.drawEllipse(center, radius - width / 2, radius - width / 2)
        square = self._square()
        horizontal = QLinearGradient(square.topLeft(), square.topRight())
        horizontal.setColorAt(0, QColor(255, 255, 255))
        horizontal.setColorAt(1, QColor.fromHsvF(self._hue, 1, 1))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillRect(square, horizontal)
        vertical = QLinearGradient(square.topLeft(), square.bottomLeft())
        vertical.setColorAt(0, QColor(0, 0, 0, 0))
        vertical.setColorAt(1, QColor(0, 0, 0, 255))
        painter.fillRect(square, vertical)
        # handles
        angle = self._hue * 2 * math.pi
        ring_point = QPointF(center.x() + (radius - width / 2) * math.cos(angle),
                             center.y() - (radius - width / 2) * math.sin(angle))
        self._handle(painter, ring_point, QColor.fromHsvF(self._hue, 1, 1), width * 0.55)
        square_point = QPointF(square.left() + self._sat * square.width(),
                               square.top() + (1 - self._val) * square.height())
        self._handle(painter, square_point, self.color(), 9)
        painter.end()

    @staticmethod
    def _handle(painter: QPainter, point: QPointF, fill: QColor, radius: float) -> None:
        painter.setPen(QPen(QColor(255, 255, 255), 3))
        painter.setBrush(fill)
        painter.drawEllipse(point, radius, radius)
        painter.setPen(QPen(QColor(0, 0, 0, 90), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(point, radius + 1.5, radius + 1.5)

    # interaction -----------------------------------------------------------------------------
    def _pick(self, position: QPointF) -> None:
        center, radius = self._center_radius()
        if self._drag == "ring":
            angle = math.atan2(center.y() - position.y(), position.x() - center.x())
            self._hue = (angle / (2 * math.pi)) % 1.0
        elif self._drag == "square":
            square = self._square()
            self._sat = min(1.0, max(0.0, (position.x() - square.left()) / square.width()))
            self._val = min(1.0, max(0.0, 1 - (position.y() - square.top()) / square.height()))
        self.update()
        self.colorChanged.emit(self.color())

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API spelling
        position = event.position()
        center, radius = self._center_radius()
        distance = math.hypot(position.x() - center.x(), position.y() - center.y())
        if self._square().contains(position):
            self._drag = "square"
        elif radius * (1 - self.RING) - 4 <= distance <= radius + 2:
            self._drag = "ring"
        else:
            self._drag = None
            return
        self._pick(position)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API spelling
        if self._drag is not None:
            self._pick(event.position())

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802 - Qt API spelling
        if self._drag is not None:
            self._drag = None
            self.colorPicked.emit(self.color())
