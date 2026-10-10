"""The translucent notice bubble above the desktop pet: a bold title and one line of text.

It follows the pet while shown, fades out after SHOW_SECONDS, and a click on it asks the host to
open the notification list. Several notices wait in a queue and appear one after another.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

SHOW_SECONDS = 6.0
FADE_SECONDS = 0.5
WIDTH = 260
PAD = 12
TAIL = 6
BACKGROUND_ALPHA = 148        # about 58 % opaque
HEAD_GAP = 3                   # pixels between the tail and the top of the pet's drawing


class NoticeBubble(QWidget):
    clicked = Signal()
    finished = Signal()                 # hidden (timed out or clicked): the next queued notice may show

    def __init__(self, pet_window):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.NoDropShadowWindowHint)
        self.pet = pet_window
        self.setObjectName("petNoticeBubble")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.title, self.body = "", ""
        self.callback = None            # called when the bubble is clicked
        self._left = 0.0
        self._head = 0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

    def _fonts(self):
        title = QFont("PingFang TC", 11, QFont.Weight.Bold)
        body = QFont("PingFang TC", 10)
        return title, body

    def show_notice(self, title: str, body: str) -> None:
        self.title, self.body = title, body
        title_font, body_font = self._fonts()
        from PySide6.QtGui import QFontMetrics

        inner = WIDTH - 2 * PAD
        t = QFontMetrics(title_font).boundingRect(0, 0, inner, 1000, int(Qt.TextFlag.TextWordWrap), title)
        b = QFontMetrics(body_font).boundingRect(0, 0, inner, 1000, int(Qt.TextFlag.TextWordWrap), body)
        self._title_h, self._body_h = t.height(), b.height()
        self.setFixedSize(WIDTH, 2 * PAD + self._title_h + 4 + self._body_h + TAIL)
        self._head = self._head_top()
        self._left = SHOW_SECONDS
        self.setWindowOpacity(1.0)
        self._follow()
        self.show()
        self.raise_()
        self._timer.start()

    def _follow(self) -> None:
        geometry = self.pet.frameGeometry()
        x = geometry.center().x() - self.width() // 2
        y = geometry.top() + self._head - self.height() - HEAD_GAP + 1
        screen = QGuiApplication.screenAt(geometry.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = max(area.left() + 4, min(x, area.right() - self.width() - 4))
        y = max(area.top() + 4, y)
        self.move(x, y)

    def _head_top(self) -> int:
        """Rows of empty space above what the pet actually draws."""
        try:
            image = self.pet.grab().toImage()
            for row in range(image.height()):
                for col in range(0, image.width(), 2):
                    if image.pixelColor(col, row).alpha() > 40:
                        return row
        except Exception:
            pass
        return 0

    def _tick(self) -> None:
        self._left -= self._timer.interval() / 1000
        if not self.pet.isVisible():
            self.dismiss()
            return
        self._follow()
        if self._left <= FADE_SECONDS:
            self.setWindowOpacity(max(0.0, self._left / FADE_SECONDS))
        if self._left <= 0:
            self.dismiss()

    def dismiss(self) -> None:
        self._timer.stop()
        was_visible = self.isVisible()
        self.hide()
        if was_visible:
            self.finished.emit()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dismiss()
            self.clicked.emit()
            if self.callback is not None:
                self.callback()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(0.5, 0.5, self.width() - 1, self.height() - TAIL - 1)
        path = QPainterPath()
        path.addRoundedRect(body, 14, 14)
        cx = self.width() / 2
        path.moveTo(cx - 7, body.bottom() - 0.5)
        path.lineTo(cx, body.bottom() + TAIL - 1)
        path.lineTo(cx + 7, body.bottom() - 0.5)
        p.setPen(QColor(255, 255, 255, 60))
        p.setBrush(QColor(24, 26, 32, BACKGROUND_ALPHA))
        p.drawPath(path.simplified())
        title_font, body_font = self._fonts()
        inner = int(WIDTH - 2 * PAD)
        p.setPen(QColor(255, 255, 255, 245))
        p.setFont(title_font)
        p.drawText(QRectF(PAD, PAD, inner, self._title_h), int(Qt.TextFlag.TextWordWrap), self.title)
        p.setPen(QColor(255, 255, 255, 190))
        p.setFont(body_font)
        p.drawText(QRectF(PAD, PAD + self._title_h + 4, inner, self._body_h), int(Qt.TextFlag.TextWordWrap), self.body)
        p.end()
