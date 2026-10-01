"""Help window, two ways to read the same guides (the buttons on the right switch):

Folders (default)  app/gui/help_folders.py: one folder per topic; opening a folder lays
                   its guides out as a stack of cards.
List               a home page of grouped rows, and article pages that start with the
                   shortest working procedure (one picture per step), followed by folding
                   sections (options, what the result means, if it does not work, reference).

The right-hand rail also copies everything for an AI assistant in one click.
Content: app/gui/help_content.py (index, article files, AI export).
"""

from __future__ import annotations

import math
import os
import platform

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from app import __version__
from app.gui.help_folders import FolderMode
from app.gui.help_content import (  # noqa: F401 - re-exported for callers and tests
    Article, all_articles, article_text, help_directory, image_path, lang_folder, load_article, load_index,
)

# window class name -> article opened by F1
CONTEXT_PAGES = {
    "BrowserWindow": "browser-database", "MainWindow": "viewer-1d", "ThreeDAnalysisWindow": "three-d",
    "YigMirrorFittingWindow": "yig-overview", "NodeAntinodeWindow": "yig-phase-node",
    "NetworkPanel": "network-client", "SettingsDialog": "settings-general", "YigMirrorWindow": "network-client",
    "LicenseDialog": "licenses", "RelinkDialog": "licenses", "FigureBuilderWindow": "figure-builder",
    "TagQueryDialog": "tags-largest-folder", "DeBackgroundDialog": "browser-debackground",
}

UI_TEXT = {
    "en": {"title": "Help", "tips": "LabLogViewer Help", "subtitle": "Step-by-step guides for every window.",
           "search": "Search", "results": "Search Results", "none": "Nothing matches.", "export": "Export for AI…",
           "back": "Back", "enlarge": "Click a picture to enlarge it.",
           "to_list": "Show as a list", "to_folders": "Show as folders", "copy_ai": "Copy all guides for an AI assistant",
           "copied": "Copied for AI — paste it into your assistant", "home": "All topics",
           "save_ai": "Export for AI (preview, save as file)…"},
    "zh": {"title": "說明", "tips": "LabLogViewer 說明", "subtitle": "每個視窗的逐步教學。",
           "search": "搜尋", "results": "搜尋結果", "none": "沒有符合的項目。", "export": "匯出給 AI⋯",
           "back": "返回", "enlarge": "點圖片可以放大。",
           "to_list": "改用清單顯示", "to_folders": "改用資料夾顯示", "copy_ai": "一鍵複製全部說明給 AI",
           "copied": "已複製給 AI，貼到你的 AI 助理即可", "home": "全部主題",
           "save_ai": "匯出給 AI（預覽、存成檔案）⋯"},
}


def _lang(localizer) -> str:
    return lang_folder(str(getattr(localizer, "language", "en")))


def _colors():
    try:
        from app.theme import current_theme_colors

        return current_theme_colors()
    except Exception:
        return None


def _dark() -> bool:
    colors = _colors()
    return colors is not None and QColor(colors.window).lightness() < 128


def _card_color() -> QColor:
    colors = _colors()
    if colors is None:
        return QColor("#FFFFFF")
    return QColor("#1C1C1E") if _dark() else QColor("#FFFFFF")


def _page_color() -> QColor:
    return QColor("#000000") if _dark() else QColor("#F2F2F7")


def _text_color() -> QColor:
    return QColor("#FFFFFF") if _dark() else QColor("#1C1C1E")


def _secondary_color() -> QColor:
    return QColor("#98989F") if _dark() else QColor("#6C6C70")


def _separator_color() -> QColor:
    return QColor("#38383A") if _dark() else QColor("#D8D8DC")


# -- glyphs (vector, drawn in white on a coloured tile) -----------------------------------------
def draw_glyph(painter: QPainter, name: str, rect: QRectF, color: QColor) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = min(rect.width(), rect.height())
    c = rect.center()
    pen = QPen(color, max(1.4, s * 0.075), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    x, y, r = c.x(), c.y(), s * 0.36

    def P(dx, dy):
        return QPointF(x + dx * r, y + dy * r)

    if name == "sparkles":
        for (dx, dy, k) in ((0, 0, 1.0), (0.7, -0.7, 0.45)):
            path = QPainterPath(P(dx, dy - k))
            for ax, ay in ((dx + 0.18 * k, dy - 0.18 * k), (dx + k, dy), (dx + 0.18 * k, dy + 0.18 * k), (dx, dy + k),
                           (dx - 0.18 * k, dy + 0.18 * k), (dx - k, dy), (dx - 0.18 * k, dy - 0.18 * k), (dx, dy - k)):
                path.lineTo(P(ax, ay))
            painter.fillPath(path, color)
    elif name == "folder":
        path = QPainterPath(P(-1, -0.55))
        for ax, ay in ((-0.35, -0.55), (-0.2, -0.35), (1, -0.35), (1, 0.8), (-1, 0.8)):
            path.lineTo(P(ax, ay))
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "star":
        path = QPainterPath()
        for k in range(10):
            radius = 1.0 if k % 2 == 0 else 0.42
            a = -math.pi / 2 + k * math.pi / 5
            point = P(radius * math.cos(a), radius * math.sin(a) + 0.08)
            path.moveTo(point) if k == 0 else path.lineTo(point)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "tag":
        path = QPainterPath(P(-1, -0.2))
        for ax, ay in ((-0.2, -1), (1, -1), (1, 0.2), (0.2, 1)):
            path.lineTo(P(ax, ay))
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawEllipse(P(0.5, -0.5), r * 0.15, r * 0.15)
    elif name == "eye":
        path = QPainterPath(P(-1, 0))
        path.quadTo(P(0, -1), P(1, 0))
        path.quadTo(P(0, 1), P(-1, 0))
        painter.drawPath(path)
        painter.drawEllipse(c, r * 0.3, r * 0.3)
    elif name == "layers":
        for dy in (-0.45, 0.0, 0.45):
            path = QPainterPath(P(-1, dy))
            path.lineTo(P(0, dy - 0.45))
            path.lineTo(P(1, dy))
            path.lineTo(P(0, dy + 0.45))
            path.closeSubpath()
            painter.drawPath(path)
    elif name == "chart":
        painter.drawLine(P(-1, 0.9), P(1, 0.9))
        painter.drawLine(P(-1, 0.9), P(-1, -0.9))
        path = QPainterPath(P(-0.8, 0.5))
        path.cubicTo(P(-0.3, -0.9), P(0.1, 0.9), P(0.9, -0.6))
        painter.drawPath(path)
    elif name == "grid":
        for i in range(3):
            for j in range(3):
                painter.drawRect(QRectF(P(-1 + i * 0.7, -1 + j * 0.7), QSize(int(r * 0.55), int(r * 0.55))))
    elif name == "panes":
        painter.drawRoundedRect(QRectF(P(-1, -0.8), P(1, 0.8)), r * 0.15, r * 0.15)
        painter.drawLine(P(0, -0.8), P(0, 0.8))
        painter.drawLine(P(-1, 0), P(1, 0))
    elif name == "function":
        font = QFont(painter.font())
        font.setPixelSize(int(s * 0.62))
        font.setItalic(True)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "fx")
    elif name == "pin":
        path = QPainterPath(P(0, 1))
        path.cubicTo(P(-0.9, 0), P(-0.8, -1), P(0, -1))
        path.cubicTo(P(0.8, -1), P(0.9, 0), P(0, 1))
        painter.drawPath(path)
        painter.drawEllipse(P(0, -0.35), r * 0.25, r * 0.25)
    elif name == "magnifier":
        painter.drawEllipse(P(-0.2, -0.2), r * 0.65, r * 0.65)
        painter.drawLine(P(0.28, 0.28), P(0.95, 0.95))
    elif name == "share":
        painter.drawLine(P(0, 0.3), P(0, -1))
        painter.drawLine(P(-0.4, -0.6), P(0, -1))
        painter.drawLine(P(0.4, -0.6), P(0, -1))
        path = QPainterPath(P(-0.45, -0.3))
        for ax, ay in ((-0.9, -0.3), (-0.9, 1), (0.9, 1), (0.9, -0.3), (0.45, -0.3)):
            path.lineTo(P(ax, ay))
        painter.drawPath(path)
    elif name == "cube":
        path = QPainterPath(P(0, -1))
        for ax, ay in ((0.9, -0.5), (0.9, 0.5), (0, 1), (-0.9, 0.5), (-0.9, -0.5), (0, -1)):
            path.lineTo(P(ax, ay))
        painter.drawPath(path)
        painter.drawLine(P(-0.9, -0.5), P(0, 0))
        painter.drawLine(P(0.9, -0.5), P(0, 0))
        painter.drawLine(P(0, 0), P(0, 1))
    elif name == "sphere":
        painter.drawEllipse(c, r, r)
        for dx in (-0.35, 0.0, 0.35):
            painter.drawLine(P(dx, 0.35), P(dx, -0.35))
            painter.drawLine(P(dx - 0.12, -0.2), P(dx, -0.35))
            painter.drawLine(P(dx + 0.12, -0.2), P(dx, -0.35))
    elif name == "wave":
        path = QPainterPath(P(-1, 0))
        for k in range(1, 41):
            t = k / 40
            path.lineTo(P(-1 + 2 * t, -0.7 * math.sin(2 * math.pi * 1.5 * t)))
        painter.drawPath(path)
    elif name == "nodes":
        path = QPainterPath(P(-1, 0))
        for k in range(1, 41):
            t = k / 40
            path.lineTo(P(-1 + 2 * t, -0.7 * abs(math.sin(2 * math.pi * t))))
        painter.drawPath(path)
        painter.setBrush(color)
        for t in (0.0, 0.5, 1.0):
            painter.drawEllipse(P(-1 + 2 * t, 0), r * 0.13, r * 0.13)
    elif name == "pen":
        painter.drawLine(P(-0.8, 0.8), P(0.7, -0.7))
        painter.drawLine(P(-0.5, 0.95), P(-0.95, 0.5))
        painter.drawLine(P(0.4, -0.95), P(0.95, -0.4))
    elif name == "network":
        for k, radius in enumerate((0.3, 0.65, 1.0)):
            rect_ = QRectF(P(-radius, -radius + 0.5), P(radius, radius + 0.5))
            painter.drawArc(rect_, 45 * 16, 90 * 16)
        painter.setBrush(color)
        painter.drawEllipse(P(0, 0.55), r * 0.12, r * 0.12)
    elif name == "gear":
        painter.drawEllipse(c, r * 0.35, r * 0.35)
        for k in range(8):
            a = k * math.pi / 4
            painter.drawLine(P(0.6 * math.cos(a), 0.6 * math.sin(a)), P(math.cos(a), math.sin(a)))
        painter.drawEllipse(c, r * 0.65, r * 0.65)
    elif name == "palette":
        painter.drawEllipse(c, r, r * 0.85)
        painter.setBrush(color)
        for ax, ay in ((-0.45, -0.3), (0.0, -0.5), (0.45, -0.3), (0.5, 0.2)):
            painter.drawEllipse(P(ax, ay), r * 0.12, r * 0.12)
    elif name == "key":
        painter.drawEllipse(P(-0.5, 0), r * 0.4, r * 0.4)
        painter.drawLine(P(-0.1, 0), P(1, 0))
        painter.drawLine(P(0.7, 0), P(0.7, 0.35))
        painter.drawLine(P(0.45, 0), P(0.45, 0.3))
    elif name == "drive":
        painter.drawRoundedRect(QRectF(P(-1, -0.6), P(1, 0.6)), r * 0.2, r * 0.2)
        painter.drawLine(P(-1, 0.1), P(1, 0.1))
        painter.setBrush(color)
        painter.drawEllipse(P(0.6, 0.35), r * 0.08, r * 0.08)
    elif name == "shield":
        path = QPainterPath(P(0, -1))
        path.lineTo(P(0.85, -0.65))
        path.cubicTo(P(0.85, 0.3), P(0.4, 0.75), P(0, 1))
        path.cubicTo(P(-0.4, 0.75), P(-0.85, 0.3), P(-0.85, -0.65))
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(P(-0.35, 0), P(-0.05, 0.3))
        painter.drawLine(P(-0.05, 0.3), P(0.4, -0.25))
    elif name == "lifebuoy":
        painter.drawEllipse(c, r, r)
        painter.drawEllipse(c, r * 0.45, r * 0.45)
        for a in (45, 135, 225, 315):
            rad = math.radians(a)
            painter.drawLine(P(0.45 * math.cos(rad), 0.45 * math.sin(rad)), P(math.cos(rad), math.sin(rad)))
    elif name == "keyboard":
        painter.drawRoundedRect(QRectF(P(-1, -0.6), P(1, 0.6)), r * 0.15, r * 0.15)
        painter.setBrush(color)
        for j in (-0.25, 0.1):
            for i in range(-3, 4):
                painter.drawEllipse(P(i * 0.26, j), r * 0.05, r * 0.05)
        painter.drawLine(P(-0.5, 0.38), P(0.5, 0.38))
    elif name == "ripple":
        for radius in (0.3, 0.65, 1.0):
            painter.drawEllipse(c, r * radius, r * radius)
    elif name == "figure":
        painter.drawRect(QRectF(P(-1, 0.2), P(1, 0.6)))
        path = QPainterPath(P(-1, -0.2))
        for k in range(1, 31):
            t = k / 30
            path.lineTo(P(-1 + 2 * t, -0.2 - 0.6 * math.sin(2 * math.pi * 1.5 * t)))
        painter.drawPath(path)
    elif name == "window":
        painter.drawRoundedRect(QRectF(P(-1, -0.8), P(1, 0.8)), r * 0.2, r * 0.2)
        painter.drawLine(P(-1, -0.4), P(1, -0.4))
    else:
        painter.drawEllipse(c, r, r)
    painter.restore()


class GlyphTile(QWidget):
    """A glyph in white on a rounded coloured square (the Tips-style list icon)."""

    def __init__(self, glyph: str, tint: str, size: int = 30, parent=None):
        super().__init__(parent)
        self.glyph, self.tint = glyph, QColor(tint)
        self.setFixedSize(size, size)

    def paintEvent(self, _event):  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, self.tint.lighter(118))
        gradient.setColorAt(1.0, self.tint.darker(108))
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, rect.width() * 0.24, rect.width() * 0.24)
        draw_glyph(painter, self.glyph, rect.adjusted(3, 3, -3, -3), QColor("#FFFFFF"))


class RoundedCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(0)

    def paintEvent(self, _event):  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_card_color())
        painter.drawRoundedRect(QRectF(self.rect()), 18, 18)


def _label(text: str, size: int, weight=QFont.Weight.Normal, color: QColor | None = None, wrap=True) -> QLabel:
    label = QLabel(text)
    label.setProperty("_lv_content_value", True)
    font = QFont(label.font())
    font.setPixelSize(size)
    font.setWeight(weight)
    label.setFont(font)
    label.setWordWrap(wrap)
    if color is not None:
        label.setStyleSheet(f"color: {color.name()}; background: transparent;")
    else:
        label.setStyleSheet("background: transparent;")
    return label


class Separator(QWidget):
    def __init__(self, indent: int = 0, parent=None):
        super().__init__(parent)
        self.indent = indent
        self.setFixedHeight(1)

    def paintEvent(self, _event):  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        painter.fillRect(self.indent, 0, self.width() - self.indent, 1, _separator_color())


class ArticleRow(QWidget):
    clicked = Signal(str)

    def __init__(self, entry: dict, lang: str, parent=None):
        super().__init__(parent)
        self.article_id = entry["id"]
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName(f"helpRow_{self.article_id}")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 11, 14, 11)
        row.setSpacing(14)
        row.addWidget(GlyphTile(entry.get("icon", "window"), entry.get("tint", "#0A84FF"), 32))
        texts = QVBoxLayout()
        texts.setSpacing(1)
        texts.addWidget(_label(entry["title"][lang], 15, QFont.Weight.DemiBold, _text_color()))
        subtitle = entry.get("subtitle", {}).get(lang, "")
        if subtitle:
            texts.addWidget(_label(subtitle, 12, QFont.Weight.Normal, _secondary_color()))
        row.addLayout(texts, 1)
        row.addWidget(_label("›", 22, QFont.Weight.Normal, _secondary_color(), wrap=False))
        self._hover = False

    def enterEvent(self, _event):  # noqa: N802 - Qt API spelling
        self._hover = True
        self.update()

    def leaveEvent(self, _event):  # noqa: N802 - Qt API spelling
        self._hover = False
        self.update()

    def paintEvent(self, _event):  # noqa: N802 - Qt API spelling
        if self._hover:
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(255, 255, 255, 18) if _dark() else QColor(0, 0, 0, 10))

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt API spelling
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.article_id)


class _Page(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.body = QWidget()
        self.body.setObjectName("helpPageBody")
        self.column = QVBoxLayout(self.body)
        self.column.setContentsMargins(28, 22, 28, 90)
        self.column.setSpacing(10)
        self.setWidget(self.body)

    def paint_background(self) -> None:
        color = _page_color().name()
        self.setStyleSheet(f"QScrollArea {{ background: {color}; border: none; }}")
        self.body.setStyleSheet(f"#helpPageBody {{ background: {color}; }}")

    def clear(self) -> None:
        while self.column.count():
            item = self.column.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                _clear_layout(item.layout())


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


class HomePage(_Page):
    open_article = Signal(str)

    def build(self, index: dict, lang: str, query: str = "") -> None:
        self.paint_background()
        self.clear()
        text = UI_TEXT[lang]
        if not query:
            self.column.addWidget(_label(text["tips"], 30, QFont.Weight.Bold, _text_color()))
            self.column.addWidget(_label(text["subtitle"], 14, QFont.Weight.Normal, _secondary_color()))
            self.column.addSpacing(8)
            groups = [(g["title"][lang], g["articles"]) for g in index["groups"]]
        else:
            needle = query.casefold()
            matches = []
            for entry in all_articles(index):
                haystack = " ".join([entry["title"]["en"], entry["title"]["zh"], entry.get("keywords", ""),
                                     entry.get("subtitle", {}).get(lang, ""), article_text(entry["id"], lang)])
                if needle in haystack.casefold():
                    matches.append(entry)
            groups = [(text["results"], matches)]
        for title, entries in groups:
            self.column.addSpacing(10)
            self.column.addWidget(_label(title, 20, QFont.Weight.Bold, _text_color()))
            card = RoundedCard()
            if not entries:
                none = _label(text["none"], 14, QFont.Weight.Normal, _secondary_color())
                none.setContentsMargins(16, 14, 16, 14)
                card.layout_.addWidget(none)
            for number, entry in enumerate(entries):
                if number:
                    card.layout_.addWidget(Separator(62))
                row = ArticleRow(entry, lang)
                row.clicked.connect(self.open_article)
                card.layout_.addWidget(row)
            self.column.addWidget(card)
        self.column.addStretch(1)


class BackButton(QPushButton):
    """Round translucent back button (drawn, so no theme style sheet can square it)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("helpBack")
        self.setFixedSize(42, 42)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)

    def paintEvent(self, _event):  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor(255, 255, 255, 110), 1.2))
        painter.setBrush(QColor(255, 255, 255, 105 if self.underMouse() else 70))
        painter.drawEllipse(rect)
        painter.setPen(QPen(QColor("#FFFFFF"), 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        c = rect.center()
        painter.drawPolyline([QPointF(c.x() + 3, c.y() - 8), QPointF(c.x() - 5, c.y()), QPointF(c.x() + 3, c.y() + 8)])


class Hero(QWidget):
    """Gradient header: glyph tile, title and one-sentence summary."""

    back = Signal()

    def __init__(self, entry: dict, article: Article, lang: str, parent=None):
        super().__init__(parent)
        self.tint = QColor(entry.get("tint", "#0A84FF"))
        self.glyph = entry.get("icon", "window")
        column = QVBoxLayout(self)
        column.setContentsMargins(28, 18, 28, 26)
        top = QHBoxLayout()
        back = BackButton()
        back.setToolTip(UI_TEXT[lang]["back"])
        back.clicked.connect(self.back)
        top.addWidget(back)
        top.addStretch(1)
        column.addLayout(top)
        tile = GlyphTile(self.glyph, "#FFFFFF", 64)
        tile.tint = QColor(255, 255, 255, 235)
        tile.paintEvent = lambda e, t=tile: self._paint_tile(t)
        column.addWidget(tile, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(18)
        column.addWidget(_label(article.title, 28, QFont.Weight.Bold, QColor("#FFFFFF")))
        if article.summary:
            column.addWidget(_label(article.summary, 16, QFont.Weight.DemiBold, QColor(255, 255, 255, 190)))
        self.setMinimumHeight(250)

    def _paint_tile(self, tile) -> None:
        painter = QPainter(tile)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(tile.rect())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(rect, rect.width() * 0.24, rect.width() * 0.24)
        draw_glyph(painter, self.glyph, rect.adjusted(9, 9, -9, -9), self.tint.darker(115))

    def paintEvent(self, _event):  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        rect = QRectF(self.rect())
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, self.tint.lighter(125))
        gradient.setColorAt(0.55, self.tint)
        gradient.setColorAt(1.0, self.tint.darker(170))
        path = QPainterPath()
        path.addRoundedRect(rect, 22, 22)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillPath(path, gradient)


class Thumbnail(QLabel):
    clicked = Signal(object)

    def __init__(self, path, width: int = 190, parent=None):
        super().__init__(parent)
        from app.gui.help_content import image_focus

        self.path = path
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        source = QPixmap(str(path))
        height = int(width * 0.62)
        focus = image_focus(os.path.basename(str(path)))
        if focus is not None and not source.isNull():
            # zoom the thumbnail onto the outlined control, keeping the thumbnail's shape
            fx, fy, fw, fh = focus
            sw, sh = source.width(), source.height()
            box_w = max(fw * sw * 1.5, sw * 0.28)
            box_h = max(fh * sh * 1.5, box_w * height / width)
            box_w = max(box_w, box_h * width / height)
            box_w, box_h = min(box_w, sw), min(box_h, sh)
            cx, cy = (fx + fw / 2) * sw, (fy + fh / 2) * sh
            x = min(max(0.0, cx - box_w / 2), sw - box_w)
            y = min(max(0.0, cy - box_h / 2), sh - box_h)
            source = source.copy(int(x), int(y), int(box_w), int(box_h))
        scaled = source.scaled(width * 2, height * 2, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
        crop = scaled.copy(max(0, (scaled.width() - width * 2) // 2), max(0, (scaled.height() - height * 2) // 2),
                           width * 2, height * 2) if scaled.width() >= width * 2 else scaled
        crop.setDevicePixelRatio(2.0)
        rounded = QPixmap(crop.size())
        rounded.setDevicePixelRatio(2.0)
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, crop.width() / 2, crop.height() / 2), 12, 12)
        painter.setClipPath(clip)
        painter.drawPixmap(0, 0, crop)
        painter.end()
        self.setPixmap(rounded)
        self.setFixedSize(width, height)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt API spelling
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.path)


class Lightbox(QDialog):
    """A screenshot at full size (click or Esc to close)."""

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(os.path.basename(str(path)))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel()
        pixmap = QPixmap(str(path))
        screen = (parent.screen() if parent is not None else QApplication.primaryScreen()).availableGeometry()
        pixmap = pixmap.scaled(min(pixmap.width(), int(screen.width() * 0.9)), min(pixmap.height(), int(screen.height() * 0.88)),
                               Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        label.setPixmap(pixmap)
        layout.addWidget(label)
        label.mousePressEvent = lambda _e: self.accept()


class StepRow(QWidget):
    def __init__(self, number: int, step, lang: str, on_image, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 14, 16, 14)
        row.setSpacing(16)
        path = image_path(step.image, lang)
        if path is not None:
            thumb = Thumbnail(path, 440)
            thumb.clicked.connect(on_image)
            row.addWidget(thumb, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        head = QHBoxLayout()
        badge = QLabel(str(number))
        badge.setFixedSize(24, 24)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(f"background: {QColor('#0A84FF').name()}; color: white; border-radius: 12px;"
                            " font-weight: 700; font-size: 12px;")
        head.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        title = _label(step.title, 17, QFont.Weight.Bold, _text_color())
        head.addWidget(title, 1)
        texts.addLayout(head)
        if step.text:
            body = _markdown_label(step.text, 14, _secondary_color())
            texts.addWidget(body)
        texts.addStretch(1)
        row.addLayout(texts, 1)


def _markdown_label(markdown: str, size: int, color: QColor) -> QLabel:
    label = QLabel()
    label.setProperty("_lv_content_value", True)
    label.setTextFormat(Qt.TextFormat.MarkdownText)
    label.setText(markdown)
    label.setWordWrap(True)
    label.setOpenExternalLinks(False)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    font = QFont(label.font())
    font.setPixelSize(size)
    label.setFont(font)
    label.setStyleSheet(f"color: {color.name()}; background: transparent;")
    return label


class FoldingSection(RoundedCard):
    """Section title with a chevron; the text folds open."""

    def __init__(self, title: str, body: str, lang: str, expanded: bool = False, parent=None):
        super().__init__(parent)
        self.header = QPushButton()
        self.header.setObjectName("helpSectionHeader")
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setFlat(True)
        self.header.setProperty("_lv_content_value", True)
        self.title = title
        self.header.setStyleSheet(f"QPushButton {{ text-align: left; padding: 14px 18px; font-size: 16px;"
                                  f" font-weight: 600; color: {_text_color().name()}; background: transparent; border: none; }}")
        self.header.clicked.connect(self.toggle)
        self.layout_.addWidget(self.header)
        self.body = _markdown_label(body, 14, _text_color())
        self.body.setContentsMargins(18, 0, 18, 16)
        self.layout_.addWidget(self.body)
        self.set_expanded(expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        self.body.setVisible(expanded)
        self.header.setText(("⌄  " if expanded else "›  ") + self.title)

    def toggle(self) -> None:
        self.set_expanded(not self.expanded)


class ArticlePage(_Page):
    go_back = Signal()

    def build(self, entry: dict, article: Article, lang: str, open_sections: bool = False) -> None:
        self.paint_background()
        self.clear()
        hero = Hero(entry, article, lang)
        hero.back.connect(self.go_back)
        self.column.addWidget(hero)
        self.column.addSpacing(12)
        self.sections: list[FoldingSection] = []
        if article.steps:
            self.column.addWidget(_label(article.steps_title, 20, QFont.Weight.Bold, _text_color()))
            if any(step.image for step in article.steps):
                self.column.addWidget(_label(UI_TEXT[lang]["enlarge"], 12, QFont.Weight.Normal, _secondary_color()))
            card = RoundedCard()
            for number, step in enumerate(article.steps, 1):
                if number > 1:
                    card.layout_.addWidget(Separator(16))
                card.layout_.addWidget(StepRow(number, step, lang, self._enlarge))
            self.column.addWidget(card)
        for section in article.sections:
            if not section.title:
                note = _markdown_label(section.body, 13, _secondary_color())
                note.setContentsMargins(6, 4, 6, 4)
                self.column.addWidget(note)
                continue
            self.column.addSpacing(6)
            folding = FoldingSection(section.title, section.body, lang, expanded=open_sections)
            self.sections.append(folding)
            self.column.addWidget(folding)
        self.column.addStretch(1)
        self.verticalScrollBar().setValue(0)

    def _enlarge(self, path) -> None:
        Lightbox(path, self.window()).exec()


class SearchCapsule(QFrame):
    """Floating search field at the bottom of the home page."""

    def __init__(self, placeholder: str, parent=None):
        super().__init__(parent)
        self.setObjectName("helpSearchCapsule")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 6, 18, 6)
        icon = QWidget()
        icon.setFixedSize(20, 20)
        icon.paintEvent = lambda e, w=icon: self._paint_icon(w)
        layout.addWidget(icon)
        self.edit = QLineEdit()
        self.edit.setObjectName("helpSearch")
        self.edit.setPlaceholderText(placeholder)
        self.edit.setFrame(False)
        self.edit.setStyleSheet(f"QLineEdit {{ background: transparent; border: none; font-size: 16px;"
                                f" color: {_text_color().name()}; }}")
        layout.addWidget(self.edit, 1)
        self.setFixedHeight(48)

    def _paint_icon(self, widget) -> None:
        painter = QPainter(widget)
        draw_glyph(painter, "magnifier", QRectF(widget.rect()), _secondary_color())

    def paintEvent(self, _event):  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(_separator_color(), 1))
        color = QColor("#2C2C2E") if _dark() else QColor("#FFFFFF")
        color.setAlpha(245)
        painter.setBrush(color)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)


class RailButton(QAbstractButton):
    """A round glass button on the right-hand rail (glyph only, tooltip says what it does).
    QAbstractButton, not QPushButton: the app-wide push-button style would resize it."""

    def __init__(self, glyph: str, name: str, parent=None):
        super().__init__(parent)
        self.glyph = glyph
        self.setObjectName(name)
        self.setFixedSize(44, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False

    def enterEvent(self, _event):  # noqa: N802 - Qt API spelling
        self._hover = True
        self.update()

    def leaveEvent(self, _event):  # noqa: N802 - Qt API spelling
        self._hover = False
        self.update()

    def paintEvent(self, _event):  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        dark = _dark()
        base = QColor("#2C2C2E") if dark else QColor("#FFFFFF")
        if self._hover:
            base = base.lighter(125) if dark else QColor("#EDEDF2")
        if self.isDown():
            base = base.darker(115)
        painter.setPen(QPen(_separator_color(), 1))
        painter.setBrush(base)
        painter.drawEllipse(rect)
        shine = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        shine.setColorAt(0.0, QColor(255, 255, 255, 60 if dark else 120))
        shine.setColorAt(0.5, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shine)
        painter.drawEllipse(rect.adjusted(1, 1, -1, -1))
        draw_glyph(painter, self.glyph, rect.adjusted(11, 11, -11, -11), QColor("#0A84FF"))


class Toast(QLabel):
    """A short message at the bottom of the window that fades by itself."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("helpToast")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("QLabel { background: rgba(30,30,32,230); color: white; border-radius: 18px;"
                           " padding: 8px 18px; font-size: 14px; }")
        self.hide()
        from PySide6.QtCore import QTimer

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def show_message(self, text: str) -> None:
        self.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - 28)
        self.show()
        self.raise_()
        self.timer.start(2200)


class HelpWindow(QMainWindow):
    MODES = ("folders", "list")

    def __init__(self, localizer, parent=None):
        super().__init__(parent)
        self.localizer = localizer
        self.setObjectName("helpWindow")
        self.resize(1120, 860)
        self.index = load_index()
        self.mode = "folders"
        # folder mode (default)
        self.folder_mode = FolderMode()
        self.folder_mode.image_clicked.connect(lambda path: Lightbox(path, self).exec())
        # list mode
        self.stack = QStackedWidget()
        self.home = HomePage()
        self.home.open_article.connect(self.show_article)
        self.article_page = ArticlePage()
        self.article_page.go_back.connect(self.show_home)
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.article_page)
        self.modes = QStackedWidget()
        self.modes.addWidget(self.folder_mode)
        self.modes.addWidget(self.stack)
        # the rail on the right: all topics, switch the look, copy for AI, export dialog
        self.rail = QWidget()
        self.rail.setObjectName("helpRail")
        rail = QVBoxLayout(self.rail)
        rail.setContentsMargins(8, 18, 12, 18)
        rail.setSpacing(12)
        self.home_button = RailButton("grid", "helpRailHome")
        self.home_button.clicked.connect(self.go_home)
        self.mode_button = RailButton("panes", "helpRailMode")
        self.mode_button.clicked.connect(self.toggle_mode)
        self.copy_ai_button = RailButton("sparkles", "helpRailCopyAi")
        self.copy_ai_button.clicked.connect(self.copy_for_ai)
        self.save_ai_button = RailButton("share", "helpRailExportAi")
        self.save_ai_button.clicked.connect(lambda: AiExportDialog(self.localizer, self).exec())
        for button in (self.home_button, self.mode_button, self.copy_ai_button, self.save_ai_button):
            rail.addWidget(button)
        rail.addStretch(1)
        central = QWidget()
        central.setObjectName("helpCentral")
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.modes, 1)
        row.addWidget(self.rail)
        self.setCentralWidget(central)
        self.export_button = QPushButton()
        self.export_button.setObjectName("helpExportAi")
        self.export_button.clicked.connect(lambda: AiExportDialog(self.localizer, self).exec())
        self.search = SearchCapsule("", self)
        self.search.edit.textChanged.connect(self._search)
        self.search_edit = self.search.edit
        self.toast = Toast(self)
        self._current: str | None = None
        self.retranslate()
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.go_home)
        if hasattr(localizer, "language_changed"):
            localizer.language_changed.connect(lambda _language: self.retranslate())
        try:
            from app.theme import get_theme_manager

            manager = get_theme_manager()
            if manager is not None:
                manager.theme_changed.connect(lambda _t: self.retranslate())
        except Exception:
            pass

    @property
    def lang(self) -> str:
        return _lang(self.localizer)

    def retranslate(self) -> None:
        text = UI_TEXT[self.lang]
        self.setWindowTitle(text["title"])
        self.search.edit.setPlaceholderText(text["search"])
        self.export_button.setText(text["export"])
        self.home_button.setToolTip(text["home"])
        self.copy_ai_button.setToolTip(text["copy_ai"])
        self.save_ai_button.setToolTip(text["save_ai"])
        page = _page_color().name()
        self.centralWidget().setStyleSheet(f"#helpCentral, #helpRail {{ background: {page}; }}")
        self.folder_mode.build(self.index, self.lang, _dark(), text["search"])
        self.set_mode(self.mode)
        if self._current is None:
            self.show_home()
        else:
            self.show_article(self._current)

    # -- modes -------------------------------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        self.mode = mode
        text = UI_TEXT[self.lang]
        folders = mode == "folders"
        self.modes.setCurrentWidget(self.folder_mode if folders else self.stack)
        self.mode_button.glyph = "panes" if folders else "folder"
        self.mode_button.setToolTip(text["to_list"] if folders else text["to_folders"])
        self.mode_button.update()
        self.search.setVisible(not folders and self._current is None)
        self._place_search()

    def toggle_mode(self) -> None:
        """Switch the look and keep the place: the guide on screen stays on screen."""
        if self.mode == "folders":
            stack = self.folder_mode.stack
            showing = self.folder_mode.body.currentWidget() is stack and stack.group is not None
            self.set_mode("list")
            if showing:
                self.show_article(stack.current_article())
            else:
                self.show_home()
        else:
            current = self._current
            self.set_mode("folders")
            if current:
                self.folder_mode.open_article(current)
            else:
                self.folder_mode.show_folders()

    def go_home(self) -> None:
        if self.mode == "folders":
            self.folder_mode.show_folders()
        else:
            self.show_home()

    def show_topic(self, article_id: str) -> None:
        """Show one guide in whichever look is on."""
        if self.mode == "folders":
            self.folder_mode.open_article(article_id)
        else:
            self.show_article(article_id)

    def copy_for_ai(self) -> None:
        QApplication.clipboard().setText(build_ai_export(include_state=True))
        self.toast.show_message(UI_TEXT[self.lang]["copied"])

    # -- list mode ---------------------------------------------------------------------------------
    def show_home(self) -> None:
        self._current = None
        self.home.build(self.index, self.lang, self.search.edit.text().strip())
        self.home.column.insertWidget(0, self.export_button, 0, Qt.AlignmentFlag.AlignRight)
        self.stack.setCurrentWidget(self.home)
        self.search.setVisible(self.mode == "list")
        self._place_search()

    def _search(self, text: str) -> None:
        if self._current is None:
            self.home.build(self.index, self.lang, text.strip())
            self.home.column.insertWidget(0, self.export_button, 0, Qt.AlignmentFlag.AlignRight)
        else:
            self.show_home()

    def entry(self, article_id: str) -> dict | None:
        return next((a for a in all_articles(self.index) if a["id"] == article_id), None)

    def show_article(self, article_id: str) -> None:
        entry = self.entry(article_id)
        if entry is None:
            return
        self._current = article_id
        self.current_article = load_article(article_id, self.lang)
        self.article_page.build(entry, self.current_article, self.lang)
        self.stack.setCurrentWidget(self.article_page)
        self.search.hide()

    def resizeEvent(self, event):  # noqa: N802 - Qt API spelling
        super().resizeEvent(event)
        self._place_search()

    def _place_search(self) -> None:
        width = min(560, self.modes.width() - 60)
        self.search.setGeometry((self.modes.width() - width) // 2, self.height() - 70, width, 48)
        self.search.raise_()


# -- AI export -----------------------------------------------------------------------------------
def _redact(text: str) -> str:
    from pathlib import Path

    home = str(Path.home())
    user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    text = text.replace(home, "~")
    if user and len(user) > 2:
        text = text.replace(user, "<user>")
    return text


def state_summary() -> str:
    """What is open right now (no data, paths or user names)."""
    from PySide6 import __version__ as pyside_version

    lines = [f"- LabLogViewer: {__version__}", f"- OS: {platform.system()} {platform.release()} ({platform.machine()})",
             f"- Python {platform.python_version()}, PySide6 {pyside_version}"]
    for window in [w for w in QApplication.topLevelWidgets() if w.isVisible() and w.windowTitle()]:
        lines.append(f"- Open window: {type(window).__name__} — {window.windowTitle()}")
    try:
        from app.network.workspace import workspace

        space = workspace()
        lines.append(f"- Network Workspace: {space.role}, quality {space.client_quality.level}")
    except Exception:
        pass
    return _redact("\n".join(lines))


def build_ai_export(include_state: bool) -> str:
    from app.gui.help_content import build_ai_export as build

    return build(include_state, state_summary() if include_state else "")


class AiExportDialog(QDialog):
    def __init__(self, localizer, parent=None):
        super().__init__(parent)
        text = localizer.text
        self.setWindowTitle(text("help.export_ai"))
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        self.include_state = QCheckBox(text("help.include_state"))
        self.include_state.toggled.connect(self._refresh)
        layout.addWidget(self.include_state)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview, 1)
        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton(text("help.save_file"), QDialogButtonBox.ButtonRole.AcceptRole)
        copy = buttons.addButton(text("help.copy_text"), QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        self.save_button.clicked.connect(self._save)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.preview.toPlainText()))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    def _refresh(self) -> None:
        self.preview.setPlainText(build_ai_export(self.include_state.isChecked()))

    def _save(self) -> None:
        from pathlib import Path

        path, _ = QFileDialog.getSaveFileName(self, "", f"LabLogViewer_{__version__}_guide_for_AI.md", "Markdown (*.md)")
        if path:
            Path(path).write_text(self.preview.toPlainText(), encoding="utf-8")
            self.accept()


_help: HelpWindow | None = None


def open_help(parent=None, article_id: str | None = None) -> HelpWindow:
    """The one Help window; ``article_id`` (or the window's context page) is shown."""
    global _help
    from shiboken6 import isValid
    from app.localization import get_localization_manager

    if _help is None or not isValid(_help):
        _help = HelpWindow(get_localization_manager())
    if article_id is None and parent is not None:
        article_id = CONTEXT_PAGES.get(type(parent.window()).__name__)
    if article_id:
        _help.show_topic(article_id)
    _help.show()
    _help.raise_()
    _help.activateWindow()
    return _help


def install_f1(window) -> None:
    shortcut = QShortcut(QKeySequence(Qt.Key.Key_F1), window)
    shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
    shortcut.activated.connect(lambda: open_help(window))
