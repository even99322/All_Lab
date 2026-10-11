"""滾輪 / ↑↓ 調整游標所在的那一位數（像儀器面板的旋鈕，0.0.28）。

    5.02|48 GHz  → 滾輪往上 → 5.0258（動的是游標左邊那一位：0.001）
    游標在最前面 → 動第一位；滑鼠停在欄位上（沒點進去）滾 → 動滑鼠指著的那一位。

* WheelLineEdit：一般輸入欄（即時數據的 VNA 參數）。改了值發出 stepped，呼叫端自己決定何時寫進儀器。
* DigitSpinBox：取代 QDoubleSpinBox（方案的量測方塊設定），滾輪 / ↑↓ 也是動游標那一位，而不是固定 1。
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

from PyQt6 import QtCore, QtGui, QtWidgets

_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)")


def digit_step(text: str, cursor: int, steps: int) -> Optional[Tuple[str, int]]:
    """把 text 裡第一個數字、游標左邊那一位加 steps；回傳 (新文字, 新游標位置)；沒有數字 → None。"""
    m = _NUM.search(text or "")
    if not m or steps == 0:
        return None
    num = m.group(0)
    start, end = m.span()
    if end < len(text) and text[end:end + 1] in "eE" and re.match(r"[eE][-+]?\d", text[end:]):
        return None                                    # 科學記號不動（看不出是哪一位）
    digits = [i for i, ch in enumerate(num) if ch.isdigit()]
    if not digits:
        return None
    rel = min(max(cursor - start, 0), len(num))
    left = [i for i in digits if i < rel]
    di = left[-1] if left else digits[0]               # 游標左邊那一位（在最前面 → 第一位）
    point = num.find(".")
    point = len(num) if point < 0 else point
    place = (point - di - 1) if di < point else -(di - point)
    try:
        value = Decimal(num)
    except InvalidOperation:
        return None
    decimals = max(0, len(num) - point - 1) if "." in num else 0
    new = value + Decimal(steps) * (Decimal(10) ** place)
    fmt = f"{new:.{decimals}f}"
    if num.startswith("+") and not fmt.startswith("-"):
        fmt = "+" + fmt
    out = text[:start] + fmt + text[end:]
    # 游標留在同一位：從小數點（或數字結尾）往回算
    new_point = fmt.find(".")
    new_point = len(fmt) if new_point < 0 else new_point
    offset_from_point = (point - (rel)) if rel <= point else -(rel - point)
    pos = start + new_point - offset_from_point
    return out, max(start, min(start + len(fmt), pos))


def cursor_after_char_at(le: QtWidgets.QLineEdit, point: QtCore.QPoint) -> int:
    """滑鼠指著的那個字的「後面」（digit_step 動游標左邊那一位）。"""
    keep = le.cursorPosition()
    edges = []
    for i in range(len(le.text()) + 1):
        le.setCursorPosition(i)
        edges.append(le.cursorRect().center().x())
    le.setCursorPosition(keep)
    x = point.x()
    for i in range(len(edges) - 1):
        if edges[i] <= x < edges[i + 1]:
            return i + 1
    return len(edges) - 1 if x >= edges[-1] else 1


def _wheel_steps(ev: QtGui.QWheelEvent) -> int:
    dy = ev.angleDelta().y()
    return 0 if dy == 0 else (1 if dy > 0 else -1) * max(1, abs(dy) // 120)


class WheelLineEdit(QtWidgets.QLineEdit):
    """滾輪 / ↑↓ 調整游標所在那一位的輸入欄；改了值發出 stepped。"""

    stepped = QtCore.pyqtSignal()

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.setToolTip("滾輪或 ↑↓：調整游標所在的那一位數")

    def step_digit(self, steps: int, cursor: Optional[int] = None) -> bool:
        r = digit_step(self.text(), self.cursorPosition() if cursor is None else cursor, steps)
        if r is None:
            return False
        self.setText(r[0])
        self.setCursorPosition(r[1])
        self.stepped.emit()
        return True

    def wheelEvent(self, ev: QtGui.QWheelEvent) -> None:  # noqa: N802
        if not self.isEnabled() or self.isReadOnly():
            return super().wheelEvent(ev)
        cursor = None
        if not self.hasFocus():                          # 沒點進去：動滑鼠指著的那一位
            cursor = cursor_after_char_at(self, ev.position().toPoint())
            self.setFocus(Qt_focus_reason())
            self.setCursorPosition(cursor)
        if self.step_digit(_wheel_steps(ev), cursor):
            ev.accept()
        else:
            super().wheelEvent(ev)

    def keyPressEvent(self, ev: QtGui.QKeyEvent) -> None:  # noqa: N802
        if ev.key() in (QtCore.Qt.Key.Key_Up, QtCore.Qt.Key.Key_Down) and not self.isReadOnly():
            if self.step_digit(1 if ev.key() == QtCore.Qt.Key.Key_Up else -1):
                ev.accept()
                return
        super().keyPressEvent(ev)


def Qt_focus_reason():  # noqa: N802
    return QtCore.Qt.FocusReason.MouseFocusReason


class DigitSpinBox(QtWidgets.QDoubleSpinBox):
    """QDoubleSpinBox：滾輪 / ↑↓ / 箭頭動游標所在那一位（沒點進去時動滑鼠指著的那一位）。"""

    def stepBy(self, steps: int) -> None:  # noqa: N802
        le = self.lineEdit()
        text = le.text()
        r = digit_step(text, le.cursorPosition(), steps)
        if r is None:
            return super().stepBy(steps)
        m = _NUM.search(r[0][len(self.prefix()):] if self.prefix() else r[0])
        try:
            value = float(m.group(0)) if m else None
        except ValueError:
            value = None
        if value is None:
            return super().stepBy(steps)
        self.setValue(value)
        le.setCursorPosition(r[1])

    def wheelEvent(self, ev: QtGui.QWheelEvent) -> None:  # noqa: N802
        if not self.hasFocus():
            le = self.lineEdit()
            self.setFocus(Qt_focus_reason())
            le.setCursorPosition(cursor_after_char_at(le, le.mapFrom(self, ev.position().toPoint())))
        super().wheelEvent(ev)
