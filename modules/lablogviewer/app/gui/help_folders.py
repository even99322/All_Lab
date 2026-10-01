"""Help, folder mode: every topic is a folder; opening one lays its pages out as a
stack of cards that stick at the top while the next card slides over them.

FolderWidget   PySide6 port of rare-ui's folder-component (swamimalode07/rare-ui,
               components/ui/folder-component.tsx): back plate, three paper cards
               and a translucent flap that tilts back in 3-D; hover lifts the
               cards, a click opens it. Same spring constants (stiffness 120,
               damping 13/14) and per-card delays as the original.
CardStack      one card per article (cover), per step (picture first) and per
               section; cards stick at the top, covered cards shrink and dim so
               depth shows reading progress. Opening animates the cards out of
               the folder into their places.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QTransform,
)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from app.gui.help_content import image_path, load_article

# -- springs ---------------------------------------------------------------------------------------
class Spring:
    """Damped spring (mass 1) like motion's type: "spring"; fixed small steps stay stable."""

    def __init__(self, value=0.0, stiffness=120.0, damping=13.0):
        self.value, self.target, self.velocity = float(value), float(value), 0.0
        self.stiffness, self.damping, self.delay = stiffness, damping, 0.0

    def set(self, target, delay=0.0):
        if target != self.target:
            self.target, self.delay = float(target), float(delay)

    def jump(self, value):
        self.value = self.target = float(value)
        self.velocity, self.delay = 0.0, 0.0

    def step(self, dt):
        if self.delay > 0:
            self.delay -= dt
            return
        steps = max(1, int(dt / 0.004 + 0.5))
        h = dt / steps
        for _ in range(steps):
            force = self.stiffness * (self.target - self.value) - self.damping * self.velocity
            self.velocity += force * h
            self.value += self.velocity * h

    def settled(self):
        return self.delay <= 0 and abs(self.target - self.value) < 0.05 and abs(self.velocity) < 0.05


class _Animated(QWidget):
    """Runs springs at ~60 fps while any of them moves."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._springs: list[Spring] = []
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._last = time.monotonic()

    def spring(self, value=0.0, stiffness=120.0, damping=13.0) -> Spring:
        item = Spring(value, stiffness, damping)
        self._springs.append(item)
        return item

    def animate(self):
        if not self._timer.isActive():
            self._last = time.monotonic()
            self._timer.start()

    def _tick(self):
        now = time.monotonic()
        dt = min(0.05, now - self._last)
        self._last = now
        for item in self._springs:
            item.step(dt)
        self.on_frame()
        self.update()
        if all(item.settled() for item in self._springs):
            self._timer.stop()
            self.on_settled()

    def on_frame(self):
        pass

    def on_settled(self):
        pass


# -- the folder --------------------------------------------------------------------------------------
BASE_W, BASE_H = 321.0, 270.0


def _flap_path() -> QPainterPath:
    """FLAP_PATH from the original SVG (0..321 × 0..241)."""
    p = QPainterPath(QPointF(0, 25))
    p.cubicTo(0, 11.1929, 11.1929, 0, 25, 0)
    p.lineTo(136.084, 0)
    p.cubicTo(143.044, 0, 149.689, 2.90139, 154.42, 8.00608)
    p.lineTo(178.08, 33.5343)
    p.cubicTo(182.811, 38.639, 189.456, 41.5404, 196.416, 41.5404)
    p.lineTo(296, 41.5404)
    p.cubicTo(309.807, 41.5404, 321, 52.7333, 321, 66.5404)
    p.lineTo(321, 216)
    p.cubicTo(321, 229.807, 309.807, 241, 296, 241)
    p.lineTo(25, 241)
    p.cubicTo(11.1929, 241, 0, 229.807, 0, 216)
    p.closeSubpath()
    return p


# (x, y, rotate) for closed / hover / open, and the delay when opening / hovering — from the original
CARD_STATES = [
    {"closed": (40, -10, 10), "hover": (40, -30, 14), "open": (70, -160, 18), "d_open": 0.10, "d_hover": 0.12},
    {"closed": (3, -20, 2), "hover": (3, -35, -1), "open": (0, -180, -3), "d_open": 0.05, "d_hover": 0.06},
    {"closed": (-40, -22, -5), "hover": (-40, -44, -9), "open": (-65, -170, -14), "d_open": 0.0, "d_hover": 0.0},
]
FLAP_ANGLE = {"closed": -15.0, "hover": -45.0, "open": -55.0}


class FolderWidget(_Animated):
    opened = Signal(str)
    HEADROOM = 110          # room above the folder for the cards it lifts out when opened

    def __init__(self, group: dict, lang: str, dark: bool, scale: float = 0.56, parent=None):
        super().__init__(parent)
        self.group, self.lang, self.dark, self.scale = group, lang, dark, scale
        self.tint = QColor(group.get("tint", "#50B1FD"))
        self.setObjectName(f"helpFolder_{group['id']}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.cards = [(self.spring(s["closed"][0]), self.spring(s["closed"][1]), self.spring(s["closed"][2]))
                      for s in CARD_STATES]
        self.flap = self.spring(FLAP_ANGLE["closed"], 120, 14)
        self.state = "closed"
        self._flap_path = _flap_path()
        self.setFixedSize(int(BASE_W * scale + 40), int((BASE_H + self.HEADROOM) * scale + 64))

    def set_state(self, state: str) -> None:
        self.state = state
        for (x, y, r), spec in zip(self.cards, CARD_STATES):
            delay = spec["d_open"] if state == "open" else spec["d_hover"] if state == "hover" else 0.0
            tx, ty, tr = spec[state]
            x.set(tx, delay)
            y.set(ty, delay)
            r.set(tr, delay)
        self.flap.set(FLAP_ANGLE[state])
        self.animate()

    def enterEvent(self, _event):  # noqa: N802 - Qt API spelling
        if self.state != "open":
            self.set_state("hover")

    def leaveEvent(self, _event):  # noqa: N802 - Qt API spelling
        self.set_state("closed")

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt API spelling
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_state("open")
            QTimer.singleShot(420, lambda: self.opened.emit(self.group["id"]))

    def folder_center(self) -> QPointF:
        return QPointF(self.width() / 2, (self.HEADROOM + BASE_H / 2) * self.scale)

    def paintEvent(self, _event):  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        c = self.folder_center()
        painter.save()
        painter.translate(c)
        painter.scale(self.scale, self.scale)
        # back plate with an inset glow
        back = QRectF(-BASE_W / 2, -BASE_H / 2, BASE_W, BASE_H)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.tint)
        painter.drawRoundedRect(back, 25, 25)
        for width, alpha in ((6, 40), (3, 70)):
            painter.setPen(QPen(QColor(255, 255, 255, alpha), width))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(back.adjusted(width / 2, width / 2, -width / 2, -width / 2), 24, 24)
        # paper cards
        for index, (x, y, r) in enumerate(self.cards):
            painter.save()
            painter.translate(x.value, y.value)
            painter.rotate(r.value)
            self._paint_card(painter, index)
            painter.restore()
        # the flap, tilted back around its bottom edge (rotateX with perspective 800)
        painter.save()
        bottom = 16 + 241 / 2
        transform = QTransform()
        transform.translate(0, bottom)
        transform.rotate(self.flap.value, Qt.Axis.XAxis, 800)
        transform.translate(-BASE_W / 2, -241)
        painter.setTransform(transform, combine=True)
        fill = QColor(self.tint).lighter(108)
        fill.setAlphaF(0.93)
        painter.setPen(QPen(QColor(self.tint).lighter(150), 1.2))
        painter.setBrush(fill)
        painter.drawPath(self._flap_path)
        shade = QLinearGradient(0, 0, 0, 241)
        shade.setColorAt(0.0, QColor(255, 255, 255, 60))
        shade.setColorAt(1.0, QColor(0, 0, 0, 40))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shade)
        painter.drawPath(self._flap_path)
        painter.restore()
        painter.restore()
        # name and count
        painter.setPen(QColor("#FFFFFF") if self.dark else QColor("#1C1C1E"))
        font = QFont(painter.font())
        font.setPixelSize(15)
        font.setBold(True)
        painter.setFont(font)
        label_top = c.y() + BASE_H * self.scale / 2 + 12
        painter.drawText(QRectF(0, label_top, self.width(), 20), Qt.AlignmentFlag.AlignHCenter,
                         self.group["title"][self.lang])
        font.setBold(False)
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor("#98989F") if self.dark else QColor("#6C6C70"))
        count = len(self.group["articles"])
        text = f"{count} 篇說明" if self.lang == "zh" else f"{count} guides"
        painter.drawText(QRectF(0, label_top + 22, self.width(), 18), Qt.AlignmentFlag.AlignHCenter, text)

    def _paint_card(self, painter: QPainter, index: int) -> None:
        rect = QRectF(-82, -107, 164, 214)
        painter.setPen(QPen(QColor("#E0E0E0"), 1))
        painter.setBrush(QColor("#F1F1F1"))
        painter.drawRoundedRect(rect, 20, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#D4D4D4"))
        painter.drawRoundedRect(QRectF(rect.left() + 14, rect.top() + 31, 135, 12), 6, 6)
        for row in range(9):
            y = rect.top() + 61 + row * 14.1
            painter.drawRoundedRect(QRectF(rect.left() + 15, y, 64.5, 5.9), 2.9, 2.9)
            painter.drawRoundedRect(QRectF(rect.left() + 84.4, y, 64.5, 5.9), 2.9, 2.9)


# -- cards -------------------------------------------------------------------------------------------
def _colors(dark: bool):
    return {
        "page": QColor("#000000") if dark else QColor("#F2F2F7"),
        "card": QColor("#1C1C1E") if dark else QColor("#FFFFFF"),
        "text": QColor("#FFFFFF") if dark else QColor("#1C1C1E"),
        "secondary": QColor("#98989F") if dark else QColor("#6C6C70"),
        "line": QColor("#38383A") if dark else QColor("#D8D8DC"),
    }


def _label(text, size, color, weight=QFont.Weight.Normal, markdown=False):
    label = QLabel()
    label.setProperty("_lv_content_value", True)
    if markdown:
        label.setTextFormat(Qt.TextFormat.MarkdownText)
    label.setText(text)
    label.setWordWrap(True)
    font = QFont(label.font())
    font.setPixelSize(size)
    font.setWeight(weight)
    label.setFont(font)
    label.setStyleSheet(f"color: {color.name()}; background: transparent;")
    return label


class CardSpec:
    def __init__(self, kind, article_id, **data):
        self.kind, self.article_id, self.data = kind, article_id, data
        self.pixmap: QPixmap | None = None
        self.image_rect: QRectF | None = None       # where the screenshot sits inside the card
        self.height = 0.0


def build_cards(group: dict, lang: str) -> list[CardSpec]:
    cards = []
    for entry in group["articles"]:
        article = load_article(entry["id"], lang)
        cards.append(CardSpec("cover", entry["id"], entry=entry, article=article))
        for number, step in enumerate(article.steps, 1):
            cards.append(CardSpec("step", entry["id"], number=number, step=step, total=len(article.steps),
                                  entry=entry))
        for section in article.sections:
            if section.title:
                cards.append(CardSpec("section", entry["id"], section=section, entry=entry))
    return cards


def render_card(spec: CardSpec, width: int, lang: str, dark: bool, image_max: int = 620) -> None:
    """Draw the card's content once into a pixmap (the stack only moves pictures)."""
    from app.gui.help_window import GlyphTile

    colors = _colors(dark)
    body = QWidget()
    body.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    body.setStyleSheet("background: transparent;")
    column = QVBoxLayout(body)
    column.setContentsMargins(28, 24, 28, 26)
    column.setSpacing(10)
    spec.image_rect = None
    image_label = None
    if spec.kind == "cover":
        entry, article = spec.data["entry"], spec.data["article"]
        head = QHBoxLayout()
        head.addWidget(GlyphTile(entry.get("icon", "window"), entry.get("tint", "#0A84FF"), 54))
        head.addSpacing(8)
        titles = QVBoxLayout()
        titles.addWidget(_label(article.title, 26, colors["text"], QFont.Weight.Bold))
        steps = len(article.steps)
        titles.addWidget(_label((f"{steps} 個步驟" if lang == "zh" else f"{steps} steps"), 13, colors["secondary"]))
        head.addLayout(titles, 1)
        column.addLayout(head)
        column.addWidget(_label(article.summary, 16, colors["text"], QFont.Weight.Medium))
    elif spec.kind == "step":
        step = spec.data["step"]
        path = image_path(step.image, lang)
        if path is not None:
            picture = QPixmap(str(path))
            inner = width - 56
            ratio = picture.height() / max(1, picture.width())
            shown = picture.scaledToWidth(inner * 2, Qt.TransformationMode.SmoothTransformation)
            shown.setDevicePixelRatio(2.0)
            height = min(int(inner * ratio), image_max)
            if int(inner * ratio) > image_max:
                shown = picture.scaledToHeight(image_max * 2, Qt.TransformationMode.SmoothTransformation)
                shown.setDevicePixelRatio(2.0)
            image_label = QLabel()
            image_label.setPixmap(shown)
            image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image_label.setFixedHeight(height)
            image_label.setStyleSheet("background: transparent;")
            column.addWidget(image_label)
        head = QHBoxLayout()
        badge = QLabel(str(spec.data["number"]))
        badge.setFixedSize(30, 30)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet("background: #0A84FF; color: white; border-radius: 15px; font-weight: 700; font-size: 14px;")
        head.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        head.addSpacing(6)
        head.addWidget(_label(step.title, 20, colors["text"], QFont.Weight.Bold), 1)
        head.addWidget(_label(f"{spec.data['number']} / {spec.data['total']}", 12, colors["secondary"]),
                       0, Qt.AlignmentFlag.AlignTop)
        column.addLayout(head)
        if step.text:
            column.addWidget(_label(step.text, 15, colors["secondary"], markdown=True))
    else:
        section = spec.data["section"]
        column.addWidget(_label(section.title, 20, colors["text"], QFont.Weight.Bold))
        column.addWidget(_label(section.body, 14, colors["text"], markdown=True))
    column.addStretch(1)
    body.ensurePolished()
    column.activate()
    height = column.totalHeightForWidth(width) if column.hasHeightForWidth() else column.totalSizeHint().height()
    body.setFixedSize(width, max(60, height))
    column.setGeometry(body.rect())
    pixmap = QPixmap(QSize(width, body.height()) * 2)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.GlobalColor.transparent)
    body.render(pixmap)
    spec.pixmap = pixmap
    spec.height = body.height()
    if image_label is not None:
        spec.image_rect = QRectF(image_label.geometry())
    body.deleteLater()


class CardStack(_Animated):
    """Sticky stacking cards; wheel / trackpad / keys scroll; depth = reading progress."""

    back_requested = Signal()
    image_clicked = Signal(object)
    current_changed = Signal(str)            # article id of the card at the top
    GAP = 26
    TOP = 34
    BOTTOM = 24
    PEEK = 7            # each covered level peeks out above the next one by this much
    MAX_W = 980

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("helpCardStack")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.cards: list[CardSpec] = []
        self.group: dict | None = None
        self.scroll = self.spring(0.0, 170, 26)
        self.intro: list[Spring] = []
        self.origin = QPointF(0, 0)
        self.lang, self.dark = "en", False
        self._width, self._image_max = 0, 0
        self._tops: list[float] = []
        self._placed: list[tuple[CardSpec, QRectF, float]] = []
        self._last_article = ""

    # -- content ----------------------------------------------------------------------------------
    def set_group(self, group: dict, lang: str, dark: bool, origin: QPointF | None = None,
                  article_id: str | None = None) -> None:
        self.group, self.lang, self.dark = group, lang, dark
        self.cards = build_cards(group, lang)
        self._width = 0
        self._layout()
        self.scroll.jump(self.scroll_for(self.article_index(article_id)) if article_id else 0.0)
        self.origin = origin if origin is not None else QPointF(self.width() / 2, self.height() + 200)
        # the cards leave the folder one after another and settle into a row
        self._springs = [self.scroll]
        self.intro = []
        first = self.current_index()
        for index, _card in enumerate(self.cards):
            progress = self.spring(0.0, 140, 17)
            progress.set(1.0, delay=max(0, min(index - first, 8)) * 0.07)
            self.intro.append(progress)
        self._last_article = ""
        self.animate()
        self.setFocus()
        self._report_current()

    def _card_width(self) -> int:
        return int(min(self.MAX_W, max(420, self.width() - 80)))

    def _layout(self) -> None:
        width = self._card_width()
        image_max = int(max(260, min(640, (self.height() or 800) * 0.62)))
        if width != self._width or abs(image_max - self._image_max) > 40:
            self._width, self._image_max = width, image_max
            for card in self.cards:
                render_card(card, width, self.lang, self.dark, image_max)
        self._tops = []
        y = 0.0
        for card in self.cards:
            self._tops.append(y)
            y += card.height + self.GAP

    def resizeEvent(self, event):  # noqa: N802 - Qt API spelling
        super().resizeEvent(event)
        if self.cards:
            self._layout()

    def _stick(self, index: int) -> float:
        """Where a card stops: at the top, or higher for a card taller than the window so its
        bottom is still readable before the next card arrives."""
        return min(float(self.TOP), self.height() - self.BOTTOM - self.cards[index].height)

    def scroll_for(self, index: int) -> float:
        if not self._tops:
            return 0.0
        return max(0.0, self._tops[index])

    def max_scroll(self) -> float:
        if not self._tops:
            return 0.0
        last = len(self._tops) - 1
        return max(0.0, self._tops[last] + self.TOP - self._stick(last))

    def article_index(self, article_id: str | None) -> int:
        return next((i for i, c in enumerate(self.cards) if c.article_id == article_id and c.kind == "cover"), 0)

    def jump_to(self, article_id: str) -> None:
        self.scroll.set(min(self.max_scroll(), self.scroll_for(self.article_index(article_id))))
        self.animate()
        self.setFocus()

    def current_index(self) -> int:
        s = self.scroll.target if self._timer.isActive() else self.scroll.value
        index = 0
        for i, top in enumerate(self._tops):
            if top <= s + 1:
                index = i
        return index

    def current_article(self) -> str:
        return self.cards[self.current_index()].article_id if self.cards else ""

    def _report_current(self) -> None:
        article = self.current_article()
        if article and article != self._last_article:
            self._last_article = article
            self.current_changed.emit(article)

    def on_frame(self):
        self._report_current()

    # -- input ------------------------------------------------------------------------------------
    def wheelEvent(self, event):  # noqa: N802 - Qt API spelling
        pixel = event.pixelDelta()
        delta = pixel.y() if not pixel.isNull() else event.angleDelta().y() / 120 * 110
        target = min(self.max_scroll(), max(0.0, self.scroll.target - delta))
        if not pixel.isNull():
            self.scroll.jump(target)          # trackpads already scroll smoothly
            self.update()
            self._report_current()
        else:
            self.scroll.set(target)
            self.animate()

    def keyPressEvent(self, event):  # noqa: N802 - Qt API spelling
        key = event.key()
        index = self.current_index()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Space):
            index = min(len(self._tops) - 1, index + 1)
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_PageUp):
            index = max(0, index - 1)
        elif key == Qt.Key.Key_Home:
            index = 0
        elif key == Qt.Key.Key_End:
            index = len(self._tops) - 1
        elif key == Qt.Key.Key_Escape:
            self.back_requested.emit()
            return
        else:
            super().keyPressEvent(event)
            return
        self.scroll.set(min(self.max_scroll(), self.scroll_for(index)))
        self.animate()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt API spelling
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position()
        for card, rect, scale in reversed(self._placed):
            if rect.contains(point):
                if card.image_rect is not None:
                    local = QPointF((point.x() - rect.left()) / scale, (point.y() - rect.top()) / scale)
                    if card.image_rect.contains(local):
                        path = image_path(card.data["step"].image, self.lang)
                        if path is not None:
                            self.image_clicked.emit(path)
                break

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt API spelling
        point = event.position()
        cursor = Qt.CursorShape.ArrowCursor
        for card, rect, scale in reversed(self._placed):
            if rect.contains(point):
                if card.image_rect is not None and card.image_rect.contains(
                        QPointF((point.x() - rect.left()) / scale, (point.y() - rect.top()) / scale)):
                    cursor = Qt.CursorShape.PointingHandCursor
                break
        self.setCursor(cursor)

    # -- drawing ----------------------------------------------------------------------------------
    def placements(self) -> list[tuple[int, CardSpec, QRectF, float, float, float]]:
        """(index, card, rect, scale, dim, clip bottom) for every card that can be seen, in
        painting order."""
        if not self.cards:
            return []
        s = self.scroll.value
        width = self._width
        left = (self.width() - width) / 2
        count = len(self.cards)
        sticks = [self._stick(i) for i in range(count)]
        tops = [max(self.TOP + self._tops[i] - s, sticks[i]) for i in range(count)]
        depths = []
        for i, card in enumerate(self.cards):
            depth = 0.0          # how many later cards lie on top of this one
            for j in range(i + 1, min(count, i + 7)):
                depth += max(0.0, min(1.0, (tops[i] + card.height - tops[j]) / max(1.0, card.height)))
            depths.append(depth)
        rects = []
        for i, card in enumerate(self.cards):
            depth = depths[i]
            cover = min(depth, 1.0)
            # a card taller than the window stops higher up; while it is covered it slides
            # back to the common top so that its edge joins the others
            top = tops[i] + (self.TOP - sticks[i]) * cover if tops[i] <= sticks[i] + 0.5 else tops[i]
            level = min(depth, 4.0)
            scale = 1.0 - 0.04 * level
            w = width * scale
            rects.append(QRectF(left + (width - w) / 2, top - self.PEEK * level, w, card.height * scale))
        # nothing of a covered card may show below the card lying on it
        clips = [math.inf] * count
        for i in range(count - 2, -1, -1):
            if depths[i] > 0:
                on_top = min(clips[i + 1], rects[i + 1].bottom())
                own = rects[i].bottom()
                clips[i] = own + (min(own, on_top) - own) * min(depths[i], 1.0)
        out = []
        for i, card in enumerate(self.cards):
            rect = rects[i]
            if rect.top() > self.height() + 40:
                break
            if depths[i] > 4.3 or min(rect.bottom(), clips[i]) < -10:
                continue
            out.append((i, card, rect, 1.0 - 0.04 * min(depths[i], 4.0), min(0.35, 0.07 * depths[i]), clips[i]))
        return out

    def paintEvent(self, _event):  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        colors = _colors(self.dark)
        painter.fillRect(self.rect(), colors["page"])
        self._placed = []
        for i, card, rect, scale, dim, clip in self.placements():
            intro = self.intro[i].value if i < len(self.intro) else 1.0
            painter.save()
            if clip < rect.bottom():
                painter.setClipRect(QRectF(0, -1000, self.width(), clip + 1000))
            if intro < 0.999:
                # out of the folder: small and fanned at the folder, full size in its place
                t = max(0.0, min(1.15, intro))
                cx = self.origin.x() + (rect.center().x() - self.origin.x()) * t
                cy = self.origin.y() + (rect.center().y() - self.origin.y()) * t
                size = 0.2 + 0.8 * t
                painter.translate(cx, cy)
                painter.rotate((1 - min(t, 1.0)) * ((i % 3) - 1) * 14)
                painter.scale(size, size)
                painter.translate(-rect.center().x(), -rect.center().y())
                painter.setOpacity(max(0.0, min(1.0, t * 1.6)))
            path = QPainterPath()
            path.addRoundedRect(rect, 22 * scale, 22 * scale)
            for spread, alpha in ((12, 7), (5, 11)):
                shadow = QPainterPath()
                shadow.addRoundedRect(rect.adjusted(-spread / 3, spread / 3, spread / 3, spread), 26, 26)
                painter.fillPath(shadow, QColor(0, 0, 0, alpha + (30 if self.dark else 0)))
            painter.fillPath(path, colors["card"])
            painter.setPen(QPen(colors["line"], 1))
            painter.drawPath(path)
            if card.pixmap is not None:
                painter.save()
                painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
                painter.translate(rect.topLeft())
                painter.scale(scale, scale)
                painter.drawPixmap(0, 0, card.pixmap)
                painter.restore()
            if dim > 0.01:
                painter.fillPath(path, QColor(0, 0, 0, int(255 * dim)))
            painter.restore()
            self._placed.append((card, rect, scale))
        self._paint_progress(painter, colors)

    def _paint_progress(self, painter: QPainter, colors) -> None:
        """A thin bar on the right: how far through this folder you are."""
        if not self.cards:
            return
        x = self.width() - 18
        top, bottom = 44.0, self.height() - 44.0
        painter.setPen(QPen(colors["line"], 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(x, top), QPointF(x, bottom))
        fraction = max(0.0, min(1.0, self.scroll.value / max(1.0, self.max_scroll())))
        painter.setPen(QPen(QColor("#0A84FF"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(x, top), QPointF(x, top + (bottom - top) * max(0.02, fraction)))
        painter.setPen(colors["secondary"])
        font = QFont(painter.font())
        font.setPixelSize(11)
        painter.setFont(font)
        painter.drawText(QRectF(x - 70, bottom + 8, 76, 16), Qt.AlignmentFlag.AlignRight,
                         f"{self.current_index() + 1} / {len(self.cards)}")


# -- glass tabs --------------------------------------------------------------------------------------
class GlassTabs(_Animated):
    """One glass tab per folder; a glass pill slides to the chosen one. Clicking a tab lists
    that folder's guides (tab_clicked(index, global rect))."""

    tab_clicked = Signal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("helpGlassTabs")
        self.setFixedHeight(50)
        self.setMouseTracking(True)
        self.titles: list[str] = []
        self.selected, self.hovered = -1, -1
        self.pill_x = self.spring(0.0, 220, 24)
        self.pill_w = self.spring(0.0, 220, 24)
        self.offset = 0.0
        self.dark = False

    def set_titles(self, titles: list[str], dark: bool) -> None:
        self.titles, self.dark = titles, dark
        self.update()

    def _fit(self) -> tuple[QFont, float]:
        """The largest text size and padding with which every tab fits (smallest if none)."""
        from PySide6.QtGui import QFontMetricsF

        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold)
        for size, pad in ((13, 30), (13, 22), (12, 18), (12, 14)):
            font.setPixelSize(size)
            metrics = QFontMetricsF(font)
            if sum(metrics.horizontalAdvance(t) + pad for t in self.titles) + 12 <= self.width():
                break
        return font, pad

    def _font(self) -> QFont:
        return self._fit()[0]

    def tab_rects(self) -> list[QRectF]:
        from PySide6.QtGui import QFontMetricsF

        font, pad = self._fit()
        metrics = QFontMetricsF(font)
        widths = [metrics.horizontalAdvance(t) + pad for t in self.titles]
        total = sum(widths) + 12
        x = max(6.0, (self.width() - total) / 2 + 6) - self.offset
        rects = []
        for w in widths:
            rects.append(QRectF(x, 7, w, self.height() - 14))
            x += w
        return rects

    def set_selected(self, index: int) -> None:
        rects = self.tab_rects()
        first = self.selected < 0
        self.selected = index
        if 0 <= index < len(rects):
            r = rects[index]
            if first:
                self.pill_x.jump(r.x() + self.offset)
                self.pill_w.jump(r.width())
            self.pill_x.set(r.x() + self.offset)
            self.pill_w.set(r.width())
            self._ensure_visible(r)
        self.animate()

    def _ensure_visible(self, rect: QRectF) -> None:
        if rect.left() < 0:
            self.offset += rect.left() - 12
        elif rect.right() > self.width():
            self.offset += rect.right() - self.width() + 12
        self._clamp()

    def _clamp(self) -> None:
        rects = self.tab_rects()
        if not rects:
            return
        total = rects[-1].right() - rects[0].left() + 12
        self.offset = max(0.0, min(self.offset, max(0.0, total - self.width())))

    def wheelEvent(self, event):  # noqa: N802 - Qt API spelling
        delta = event.pixelDelta() if not event.pixelDelta().isNull() else event.angleDelta() / 3
        self.offset -= delta.x() or delta.y()
        self._clamp()
        self.update()

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt API spelling
        index = next((i for i, r in enumerate(self.tab_rects()) if r.contains(event.position())), -1)
        if index != self.hovered:
            self.hovered = index
            self.setCursor(Qt.CursorShape.PointingHandCursor if index >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, _event):  # noqa: N802 - Qt API spelling
        self.hovered = -1
        self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt API spelling
        rects = self.tab_rects()
        for index, rect in enumerate(rects):
            if rect.contains(event.position()):
                top_left = self.mapToGlobal(rect.bottomLeft().toPoint())
                self.tab_clicked.emit(index, QRectF(top_left.x(), top_left.y(), rect.width(), rect.height()))
                return

    def paintEvent(self, _event):  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rects = self.tab_rects()
        if not rects:
            return
        bar = QRectF(max(1.0, rects[0].left() - 6), 1, 0, self.height() - 2)
        bar.setRight(min(self.width() - 1.0, rects[-1].right() + 6))
        # the glass bar: translucent body, light rim, soft highlight along the top
        body = QColor(255, 255, 255, 30) if self.dark else QColor(255, 255, 255, 150)
        painter.setPen(QPen(QColor(255, 255, 255, 60) if self.dark else QColor(0, 0, 0, 22), 1))
        painter.setBrush(body)
        painter.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)
        shine = QLinearGradient(0, bar.top(), 0, bar.bottom())
        shine.setColorAt(0.0, QColor(255, 255, 255, 70 if self.dark else 170))
        shine.setColorAt(0.5, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shine)
        painter.drawRoundedRect(bar.adjusted(1, 1, -1, -1), bar.height() / 2, bar.height() / 2)
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(bar, bar.height() / 2, bar.height() / 2)
        painter.setClipPath(clip)
        if 0 <= self.hovered < len(rects) and self.hovered != self.selected:
            painter.setBrush(QColor(255, 255, 255, 22) if self.dark else QColor(0, 0, 0, 12))
            r = rects[self.hovered]
            painter.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        if self.selected >= 0:
            pill = QRectF(self.pill_x.value - self.offset, 7, self.pill_w.value, self.height() - 14)
            painter.setBrush(QColor(0, 0, 0, 30))
            painter.drawRoundedRect(pill.adjusted(0, 2, 0, 2), pill.height() / 2, pill.height() / 2)
            glass = QLinearGradient(0, pill.top(), 0, pill.bottom())
            glass.setColorAt(0.0, QColor(64, 156, 255, 200) if self.dark else QColor(255, 255, 255, 255))
            glass.setColorAt(1.0, QColor(10, 110, 230, 170) if self.dark else QColor(255, 255, 255, 215))
            painter.setBrush(glass)
            painter.setPen(QPen(QColor(255, 255, 255, 120 if self.dark else 255), 1))
            painter.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
        painter.restore()
        painter.setFont(self._font())
        colors = _colors(self.dark)
        for index, (rect, title) in enumerate(zip(rects, self.titles)):
            chosen = QColor("#FFFFFF") if self.dark else QColor("#0A84FF")
            painter.setPen(chosen if index == self.selected else colors["text"])
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, title)
        # more tabs out of sight: fade that edge (the wheel scrolls the bar)
        for edge, hidden in ((0, rects[0].left() < 0), (1, rects[-1].right() > self.width())):
            if hidden:
                x0 = 0 if edge == 0 else self.width() - 40
                fade = QLinearGradient(x0, 0, x0 + 40, 0)
                page = colors["page"]
                clear = QColor(page)
                clear.setAlpha(0)
                fade.setColorAt(0.0, page if edge == 0 else clear)
                fade.setColorAt(1.0, clear if edge == 0 else page)
                painter.fillRect(QRectF(x0, 0, 40, self.height()), fade)


class ArticlePopover(QFrame):
    """The guides in one folder, under its tab; choosing one emits chosen(article id)."""

    chosen = Signal(str)

    def __init__(self, group: dict, lang: str, dark: bool, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        from app.gui.help_window import ArticleRow, Separator

        self.setObjectName("helpArticlePopover")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.dark = dark
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)
        self.rows = []
        for number, entry in enumerate(group["articles"]):
            if number:
                layout.addWidget(Separator(62))
            row = ArticleRow(entry, lang)
            row.clicked.connect(self._choose)
            layout.addWidget(row)
            self.rows.append(row)
        self.setFixedWidth(380)

    def _choose(self, article_id: str) -> None:
        self.close()
        self.chosen.emit(article_id)

    def paintEvent(self, _event):  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor(255, 255, 255, 50) if self.dark else QColor(0, 0, 0, 30), 1))
        painter.setBrush(QColor(44, 44, 46, 248) if self.dark else QColor(255, 255, 255, 250))
        painter.drawRoundedRect(rect, 18, 18)


# -- the home grid ------------------------------------------------------------------------------------
FOLDER_TINTS = {
    "start": "#FF9F0A", "browser": "#50B1FD", "viewer": "#5E5CE6", "three_d": "#30B0C7", "yig": "#FF375F",
    "tools": "#BF5AF2", "network": "#34C759", "settings": "#8E8E93", "data": "#AC8E68", "trouble": "#FF6961",
}
HOME_SECTIONS = [
    ({"en": "Basics", "zh": "基本操作"}, ["start", "browser", "viewer"]),
    ({"en": "Analysis and figures", "zh": "分析與繪圖"}, ["three_d", "yig", "tools"]),
    ({"en": "Sharing, settings and help", "zh": "共享、設定與疑難"}, ["network", "settings", "data", "trouble"]),
]


class FolderHome(QScrollArea):
    """Every topic as a folder, arranged in sections."""

    folder_opened = Signal(str, object)          # group id, folder centre (global)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("helpFolderHome")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.folders: dict[str, FolderWidget] = {}
        self._columns = 0
        self._args = None

    def build(self, index: dict, lang: str, dark: bool) -> None:
        self._args = (index, lang, dark)
        colors = _colors(dark)
        body = QWidget()
        body.setObjectName("helpFolderBody")
        body.setStyleSheet(f"#helpFolderBody {{ background: {colors['page'].name()}; }}")
        column = QVBoxLayout(body)
        column.setContentsMargins(30, 10, 30, 30)
        column.setSpacing(4)
        groups = {g["id"]: g for g in index["groups"]}
        self.folders = {}
        per_row = self._per_row()
        self._columns = per_row
        listed = set()
        sections = [(title, [g for g in ids if g in groups]) for title, ids in HOME_SECTIONS]
        rest = [g["id"] for g in index["groups"] if not any(g["id"] in ids for _t, ids in HOME_SECTIONS)]
        if rest:
            sections[-1][1].extend(rest)
        for title, ids in sections:
            if not ids:
                continue
            head = QLabel(title[lang])
            font = QFont(head.font())
            font.setPixelSize(20)
            font.setBold(True)
            head.setFont(font)
            head.setStyleSheet(f"color: {colors['text'].name()}; background: transparent;")
            column.addSpacing(14)
            column.addWidget(head)
            column.addSpacing(-30)          # the folders' headroom sits under the heading
            for start in range(0, len(ids), per_row):
                row = QHBoxLayout()
                row.setSpacing(10)
                for group_id in ids[start:start + per_row]:
                    group = dict(groups[group_id])
                    group["tint"] = FOLDER_TINTS.get(group_id, "#50B1FD")
                    folder = FolderWidget(group, lang, dark)
                    folder.opened.connect(self._opened)
                    self.folders[group_id] = folder
                    listed.add(group_id)
                    row.addWidget(folder)
                row.addStretch(1)
                column.addLayout(row)
        column.addStretch(1)
        self.setWidget(body)

    def _per_row(self) -> int:
        return max(1, (self.viewport().width() - 60) // (int(BASE_W * 0.56 + 40) + 14))

    def _opened(self, group_id: str) -> None:
        folder = self.folders[group_id]
        self.folder_opened.emit(group_id, folder.mapToGlobal(folder.folder_center().toPoint()))

    def reset(self) -> None:
        for folder in self.folders.values():
            folder.state = "closed"
            for (x, y, r), spec in zip(folder.cards, CARD_STATES):
                x.jump(spec["closed"][0])
                y.jump(spec["closed"][1])
                r.jump(spec["closed"][2])
            folder.flap.jump(FLAP_ANGLE["closed"])
            folder.update()

    def resizeEvent(self, event):  # noqa: N802 - Qt API spelling
        super().resizeEvent(event)
        if self._args is not None and self._per_row() != self._columns:
            self.build(*self._args)


# -- search field -------------------------------------------------------------------------------------
class TopSearch(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("helpTopSearch")
        self.dark = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 4, 16, 4)
        self.icon = QWidget()
        self.icon.setFixedSize(18, 18)
        self.icon.paintEvent = self._paint_icon
        layout.addWidget(self.icon)
        self.edit = QLineEdit()
        self.edit.setObjectName("helpFolderSearch")
        self.edit.setFrame(False)
        self.edit.setClearButtonEnabled(True)
        layout.addWidget(self.edit, 1)
        self.setFixedHeight(42)

    def restyle(self, dark: bool) -> None:
        self.dark = dark
        self.edit.setStyleSheet(f"QLineEdit {{ background: transparent; border: none; font-size: 15px;"
                                f" color: {_colors(dark)['text'].name()}; }}")
        self.update()

    def _paint_icon(self, _event) -> None:
        from app.gui.help_window import draw_glyph

        painter = QPainter(self.icon)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_glyph(painter, "magnifier", QRectF(self.icon.rect()), _colors(self.dark)["secondary"])

    def paintEvent(self, _event):  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(_colors(self.dark)["line"], 1))
        painter.setBrush(QColor("#1C1C1E") if self.dark else QColor("#FFFFFF"))
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)


# -- the folder mode page -------------------------------------------------------------------------------
class FolderMode(QWidget):
    """Search on top, glass tabs below it, then the folders or one folder's card stack."""

    image_clicked = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QStackedWidget
        from app.gui.help_window import HomePage

        self.setObjectName("helpFolderMode")
        self.index: dict = {}
        self.lang, self.dark = "en", False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(10)
        top = QHBoxLayout()
        top.setContentsMargins(24, 0, 24, 0)
        self.search = TopSearch()
        top.addStretch(1)
        top.addWidget(self.search, 6)
        top.addStretch(1)
        layout.addLayout(top)
        tabs_row = QHBoxLayout()
        tabs_row.setContentsMargins(16, 0, 16, 0)
        self.tabs = GlassTabs()
        tabs_row.addWidget(self.tabs)
        layout.addLayout(tabs_row)
        self.body = QStackedWidget()
        self.home = FolderHome()
        self.stack = CardStack()
        self.results = HomePage()
        self.body.addWidget(self.home)
        self.body.addWidget(self.stack)
        self.body.addWidget(self.results)
        layout.addWidget(self.body, 1)
        self.home.folder_opened.connect(self._folder_opened)
        self.tabs.tab_clicked.connect(self._tab_clicked)
        self.stack.back_requested.connect(self.show_folders)
        self.stack.image_clicked.connect(self.image_clicked)
        self.results.open_article.connect(self.open_article)
        self.search.edit.textChanged.connect(self._search)
        self.popover: ArticlePopover | None = None
        self._before_search = self.home

    def build(self, index: dict, lang: str, dark: bool, placeholder: str) -> None:
        self.index, self.lang, self.dark = index, lang, dark
        colors = _colors(dark)
        self.setStyleSheet(f"#helpFolderMode {{ background: {colors['page'].name()}; }}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.search.restyle(dark)
        self.search.edit.setPlaceholderText(placeholder)
        self.tabs.set_titles([g["title"][lang] for g in index["groups"]], dark)
        self.home.build(index, lang, dark)
        if self.body.currentWidget() is self.stack and self.stack.group is not None:
            self.open_group(self.stack.group["id"], article_id=self.stack.current_article(), animate=False)
        elif self.body.currentWidget() is self.results:
            self._search(self.search.edit.text())

    # -- navigation -----------------------------------------------------------------------------------
    def group_of(self, article_id: str) -> dict | None:
        return next((g for g in self.index["groups"] if any(a["id"] == article_id for a in g["articles"])), None)

    def show_folders(self) -> None:
        self.home.reset()
        self.body.setCurrentWidget(self.home)
        self.tabs.selected = -1
        self.tabs.update()

    def open_group(self, group_id: str, origin=None, article_id: str | None = None, animate=True) -> None:
        group = next(g for g in self.index["groups"] if g["id"] == group_id)
        self.body.setCurrentWidget(self.stack)
        if origin is not None:
            origin = QPointF(self.stack.mapFromGlobal(origin))
        self.stack.set_group(group, self.lang, self.dark, origin, article_id)
        if not animate:
            for spring in self.stack.intro:
                spring.jump(1.0)
        self.tabs.set_selected(self.index["groups"].index(group))

    def open_article(self, article_id: str) -> None:
        group = self.group_of(article_id)
        if group is None:
            return
        if self.search.edit.text():
            self.search.edit.blockSignals(True)
            self.search.edit.clear()
            self.search.edit.blockSignals(False)
        if self.body.currentWidget() is self.stack and self.stack.group is not None \
                and self.stack.group["id"] == group["id"]:
            self.stack.jump_to(article_id)
        else:
            self.open_group(group["id"], article_id=article_id)

    def _folder_opened(self, group_id: str, origin) -> None:
        self.open_group(group_id, origin)

    def _tab_clicked(self, index: int, rect) -> None:
        group = self.index["groups"][index]
        self.popover = ArticlePopover(group, self.lang, self.dark, self)
        self.popover.chosen.connect(self.open_article)
        self.popover.adjustSize()
        x = int(rect.x() + rect.width() / 2 - self.popover.width() / 2)
        screen = self.screen().availableGeometry()
        x = max(screen.left() + 8, min(x, screen.right() - self.popover.width() - 8))
        self.popover.move(x, int(rect.y()) + 6)
        self.popover.show()

    def _search(self, text: str) -> None:
        query = text.strip()
        if query:
            if self.body.currentWidget() is not self.results:
                self._before_search = self.body.currentWidget()
            self.results.build(self.index, self.lang, query)
            self.body.setCurrentWidget(self.results)
        elif self.body.currentWidget() is self.results:
            self.body.setCurrentWidget(self._before_search)
