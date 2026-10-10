"""Client side of the YIG mirror: the Host's whole YIG window, rebuilt 1:1.

Controls (left settings panel, tab bars, buttons, values, tables, formula
images) are real widgets placed where the Host has them; figures are redrawn
from data with this computer's theme. Nothing is computed here and the
Client cannot operate it (view-only input filter).
"""

from __future__ import annotations

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGroupBox, QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton,
    QRadioButton, QScrollArea, QSlider, QTabBar, QTableWidget, QTableWidgetItem, QToolButton, QWidget,
)

from app.network.figure_sync import render_figure


def _pixmap(data) -> QPixmap | None:
    if data is None:
        return None
    pixmap = QPixmap()
    pixmap.loadFromData(bytes(np.asarray(data, dtype=np.uint8)), "PNG")
    return pixmap if not pixmap.isNull() else None


class _Clip(QWidget):
    """Shows only the part of an item that is visible on the Host (scroll areas)."""

    def __init__(self, parent, inner: QWidget):
        super().__init__(parent)
        self.inner = inner
        inner.setParent(self)

    def place(self, rect: list[int], clip: list[int]) -> None:
        self.setGeometry(QRect(*clip))
        self.inner.setGeometry(QRect(rect[0] - clip[0], rect[1] - clip[1], rect[2], rect[3]))


class YigMirrorWindow(QMainWindow):
    def __init__(self, space):
        super().__init__(None)
        self.space = space
        self._network_mirror = True
        self.setObjectName("yigMirrorWindow")
        self.board = QWidget()
        self.board.setObjectName("yigMirrorBoard")
        scroll = QScrollArea()
        scroll.setWidget(self.board)
        scroll.setWidgetResizable(False)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setCentralWidget(scroll)
        self.items: dict[str, tuple[str, _Clip]] = {}
        self._sized = False
        from app.network.mirror_chrome import install_mirror_banner

        install_mirror_banner(self, space)

    # -- messages -------------------------------------------------------------------------
    def apply(self, value: dict, full: bool) -> None:
        layout = value.get("layout") or {}
        content = value.get("content") or {}
        host = self.space.client.host_info.get("host_name", "") if self.space.client else ""
        self.setWindowTitle(self.space.text("net.yig_mirror_title").format(host=host, title=layout.get("title", "")))
        width, height = layout.get("size") or [1200, 800]
        self.board.setFixedSize(QSize(int(width), int(height)))
        if not self._sized:
            self._sized = True
            self.resize(int(width) + 4, int(height) + 60)
        keep = set()
        for item in layout.get("items", []):
            key, kind = item["id"], item["kind"]
            keep.add(key)
            existing = self.items.get(key)
            if existing is None or existing[0] != kind:
                if existing is not None:
                    existing[1].deleteLater()
                clip = _Clip(self.board, self._create(kind))
                self.items[key] = (kind, clip)
            else:
                clip = existing[1]
            clip.place(item["rect"], item["clip"])
            if kind == "group":
                clip.inner.setTitle(item.get("title", ""))
                clip.lower()                                 # frames behind their contents
            if key in content:
                self._fill(kind, clip.inner, content[key])
            clip.show()
        for key in [key for key in self.items if key not in keep]:
            self.items.pop(key)[1].deleteLater()

    # -- widgets --------------------------------------------------------------------------
    @staticmethod
    def _create(kind: str) -> QWidget:
        if kind == "figure":
            return FigureCanvasQTAgg(Figure())
        if kind == "table":
            table = QTableWidget()
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            return table
        if kind == "slider":
            return QSlider(Qt.Orientation.Horizontal)
        factories = {
            "group": QGroupBox, "tabbar": QTabBar, "combo": QComboBox, "spin": QLineEdit, "line": QLineEdit,
            "check": QCheckBox, "radio": QRadioButton, "button": QPushButton, "tool": QToolButton,
            "text": QPlainTextEdit, "progress": QProgressBar, "label": QLabel,
        }
        widget = factories.get(kind, QLabel)()
        widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return widget

    def _fill(self, kind: str, widget: QWidget, data: dict) -> None:
        if kind == "figure":
            render_figure(widget.figure, data)
            try:
                from app.theme import _theme_matplotlib_figure, get_theme_manager

                manager = get_theme_manager()
                if manager is not None:
                    _theme_matplotlib_figure(widget, manager.plot_colors)
            except Exception:
                pass
            widget.draw_idle()
        elif kind == "table":
            headers, cells = data.get("headers", []), data.get("cells", [])
            widget.setColumnCount(len(headers))
            widget.setHorizontalHeaderLabels(headers)
            widget.setRowCount(len(cells))
            for r, row in enumerate(cells):
                for c, text in enumerate(row):
                    widget.setItem(r, c, QTableWidgetItem(str(text)))
            if data.get("selected", -1) >= 0:
                widget.selectRow(data["selected"])
            widget.verticalScrollBar().setValue(int(data.get("vscroll", 0)))
            widget.horizontalScrollBar().setValue(int(data.get("hscroll", 0)))
        elif kind == "tabbar":
            while widget.count():
                widget.removeTab(0)
            for name in data.get("tabs", []):
                widget.addTab(name)
            widget.setCurrentIndex(int(data.get("current", 0)))
        elif kind == "combo":
            widget.clear()
            widget.addItem(data.get("text", ""))
        elif kind in {"spin", "line"}:
            widget.setText(data.get("text", ""))
            widget.setPlaceholderText(data.get("placeholder", ""))
            widget.setReadOnly(True)
        elif kind in {"check", "radio"}:
            widget.setText(data.get("text", ""))
            widget.setChecked(bool(data.get("checked")))
        elif kind in {"button", "tool"}:
            widget.setText(data.get("text", ""))
            widget.setCheckable(bool(data.get("checkable")))
            widget.setChecked(bool(data.get("checked")))
            pixmap = _pixmap(data.get("icon"))
            if pixmap is not None:
                widget.setIcon(QIcon(pixmap))
                size = data.get("icon_size") or [16, 16]
                widget.setIconSize(QSize(int(size[0]), int(size[1])))
                if kind == "tool" and not data.get("text"):
                    widget.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            widget.setEnabled(bool(data.get("enabled", True)))
        elif kind == "text":
            widget.setPlainText(data.get("text", ""))
            widget.setReadOnly(True)
        elif kind == "progress":
            widget.setRange(int(data.get("min", 0)), int(data.get("max", 100)))
            widget.setValue(int(data.get("value", 0)))
        elif kind == "slider":
            widget.setOrientation(Qt.Orientation(int(data.get("orientation", 1))))
            widget.setRange(int(data.get("min", 0)), int(data.get("max", 100)))
            widget.setValue(int(data.get("value", 0)))
        elif kind == "label":
            pixmap = _pixmap(data.get("image"))
            if pixmap is not None:
                widget.setPixmap(pixmap)
            else:
                widget.setText(data.get("text", ""))
            widget.setWordWrap(bool(data.get("wrap")))
            align = int(data.get("align", 0))
            widget.setAlignment(Qt.AlignmentFlag(align) if align else
                                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            if data.get("style"):
                widget.setStyleSheet(data["style"])

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API spelling
        # Closing only hides the YIG mirror; the next YIG update from the Host reopens it.
        mirror = getattr(self.space, "mirror", None)
        if mirror is not None and mirror.yig_window is self:
            mirror.yig_window = None
        super().closeEvent(event)
