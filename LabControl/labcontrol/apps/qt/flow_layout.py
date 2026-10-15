"""放不下就換行的版面（1.0.10：專案 / 子專案的下拉選單一層一層往右放，空間不夠就往下）。"""
from __future__ import annotations

from typing import List

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt


class FlowLayout(QtWidgets.QLayout):
    def __init__(self, parent=None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: List[QtWidgets.QLayoutItem] = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do(QtCore.QRect(0, 0, width, 0), test=True)

    def setGeometry(self, rect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do(rect, test=False)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        s = QtCore.QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())

    def rows(self, width: int) -> List[List[int]]:
        """每一列放了哪幾個（測試用）。"""
        out: List[List[int]] = []
        x, row = 0, []
        for i, it in enumerate(self._items):
            if it.widget() is not None and not it.widget().isVisible() and not it.widget().isVisibleTo(
                    it.widget().parentWidget()):
                continue
            w = it.sizeHint().width()
            if row and x + w > width:
                out.append(row)
                x, row = 0, []
            row.append(i)
            x += w + self._spacing
        if row:
            out.append(row)
        return out

    def _do(self, rect, test: bool) -> int:
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = r.x(), r.y(), 0
        for it in self._items:
            wid = it.widget()
            if wid is not None and wid.isHidden():
                continue
            hint = it.sizeHint()
            if x + hint.width() > r.right() + 1 and line_h > 0:
                x = r.x()
                y += line_h + self._spacing
                line_h = 0
            if not test:
                it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()
