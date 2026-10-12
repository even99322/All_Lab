"""電流目標值的位數旋鈕（0.0.30，跟實驗室 Yokogawa 網頁面板一樣）：

        ▲ ▲ ▲   ▲ ▲ ▲
      + 1 0 0 . 0 0 0  mA        Range: 0.2 A
        ▼ ▼ ▼   ▼ ▼ ▼

* 每一位上下各一個 ▲ ▼（按住會連續）；滑鼠停在那一位上滾滾輪也一樣，只動那一位。
* 正負號：▲ = 正、▼ = 負（或滾輪）。
* 範圍限制在 ±limit（預設 200 mA，settings.yaml live.dial_range_mA）；超過就停在上限。
* 雙擊數字可以直接輸入。
* 使用者改了值 → 發出 edited(SI 值)；呼叫端（DC 群組卡片）停手一下後以斜坡移過去。
  set_value() 只更新顯示，不會發出 edited。
"""
from __future__ import annotations

import math
from typing import List, Optional

from PyQt6 import QtGui, QtWidgets
from PyQt6.QtCore import Qt, pyqtSignal

MONO = ["Consolas", "Menlo", "DejaVu Sans Mono", "Courier New", "monospace"]


def _wheel_steps(ev: QtGui.QWheelEvent) -> int:
    dy = ev.angleDelta().y()
    return 0 if dy == 0 else (1 if dy > 0 else -1) * max(1, abs(dy) // 120)


class _Cell(QtWidgets.QWidget):
    """一位：▲、數字、▼。滾輪在整格上都有效。place = 10 的幾次方（顯示單位）；None = 正負號。"""

    def __init__(self, dial: "DigitDial", place: Optional[int]) -> None:
        super().__init__(dial)
        self.dial, self.place = dial, place
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.up = self._arrow("▲", +1)
        self.label = QtWidgets.QLabel("0")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.down = self._arrow("▼", -1)
        v.addWidget(self.up, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(self.label)
        v.addWidget(self.down, 0, Qt.AlignmentFlag.AlignHCenter)
        what = "正負號" if place is None else f"這一位（{dial.place_text(place)}）"
        self.setToolTip(f"▲ ▼ 或滾輪：調整{what}；雙擊數字直接輸入")

    def _arrow(self, text: str, sign: int) -> QtWidgets.QToolButton:
        b = QtWidgets.QToolButton(self)
        b.setText(text)
        b.setAutoRaise(True)
        b.setAutoRepeat(True)
        b.setAutoRepeatDelay(450)
        b.setAutoRepeatInterval(120)
        b.setFixedSize(22, 16)
        b.setStyleSheet("QToolButton { font-size:9px; padding:0; }")
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.clicked.connect(lambda _c=False: self.dial.step(self.place, sign))
        return b

    def wheelEvent(self, ev: QtGui.QWheelEvent) -> None:  # noqa: N802
        n = _wheel_steps(ev)
        if n and self.isEnabled():
            self.dial.step(self.place, n)
            ev.accept()
        else:
            ev.ignore()

    def mouseDoubleClickEvent(self, ev) -> None:  # noqa: N802
        self.dial.ask_value()


class DigitDial(QtWidgets.QFrame):
    edited = pyqtSignal(float)            # 使用者改的新目標（SI 單位，例如 A）

    def __init__(self, unit: str = "A", limit: float = 0.2, int_digits: int = 3, decimals: int = 3,
                 scale: float = 1e-3, unit_text: str = "mA", parent=None) -> None:
        super().__init__(parent)
        self.unit, self.scale, self.unit_text = unit, scale, unit_text
        self.int_digits, self.decimals = int_digits, decimals
        self.limit_ticks = int(round(abs(limit) / scale * 10 ** decimals))
        self.ticks = 0                    # 值 = ticks × 10^-decimals（顯示單位）
        self.setObjectName("digitDial")
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(4, 0, 4, 0)
        h.setSpacing(0)
        self.cells: List[_Cell] = []
        self.sign = _Cell(self, None)
        h.addWidget(self.sign)
        places = list(range(int_digits - 1, -decimals - 1, -1))
        for p in places:
            if p == -1:
                dot = QtWidgets.QLabel(".")
                dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._font(dot)
                h.addWidget(dot)
            c = _Cell(self, p)
            self.cells.append(c)
            h.addWidget(c)
        self.unit_label = QtWidgets.QLabel(f" {unit_text}")
        self._font(self.unit_label, 0.6)
        h.addWidget(self.unit_label)
        for c in [self.sign, *self.cells]:
            self._font(c.label)
        self._render()

    # ---- 外觀 --------------------------------------------------------------------------
    def _font(self, w: QtWidgets.QWidget, rel: float = 1.0) -> None:
        f = QtGui.QFont()
        f.setFamilies(MONO)
        f.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        f.setPointSizeF(20 * rel)
        f.setBold(rel >= 1.0)
        w.setFont(f)

    def place_text(self, place: int) -> str:
        return f"{10 ** place:g} {self.unit_text}"

    def _render(self) -> None:
        self.sign.label.setText("-" if self.ticks < 0 else "+")
        digits = f"{abs(self.ticks):0{self.int_digits + self.decimals}d}"[-(self.int_digits + self.decimals):]
        for c, ch in zip(self.cells, digits):
            c.label.setText(ch)
        for c in self.cells:                      # 到上限的那一位不能再往上
            up = self._with_step(c.place, +1)
            down = self._with_step(c.place, -1)
            c.up.setEnabled(abs(up) <= self.limit_ticks and up != self.ticks)
            c.down.setEnabled(abs(down) <= self.limit_ticks and down != self.ticks)
        self.setToolTip(f"目標 {self.value_display():+.{self.decimals}f} {self.unit_text}"
                        f"（範圍 ±{self.limit_ticks / 10 ** self.decimals:g} {self.unit_text}）")

    # ---- 數值 --------------------------------------------------------------------------
    def value(self) -> float:
        """SI 值（例如 A）。"""
        return self.value_display() * self.scale

    def value_display(self) -> float:
        return self.ticks / 10 ** self.decimals

    def set_value(self, si: Optional[float]) -> None:
        """只更新顯示（不發出 edited）：輪詢到的值、合併後的目標…"""
        if si is None or not math.isfinite(float(si)):
            return
        t = int(round(float(si) / self.scale * 10 ** self.decimals))
        self.ticks = max(-self.limit_ticks, min(self.limit_ticks, t))
        self._render()

    def _with_step(self, place: Optional[int], steps: int) -> int:
        if place is None:
            return abs(self.ticks) if steps > 0 else -abs(self.ticks)
        t = self.ticks + steps * 10 ** (place + self.decimals)
        return max(-self.limit_ticks, min(self.limit_ticks, t))

    def step(self, place: Optional[int], steps: int) -> None:
        if not self.isEnabled():
            return
        t = self._with_step(place, steps)
        if t == self.ticks:
            return
        self.ticks = t
        self._render()
        self.edited.emit(self.value())

    def ask_value(self) -> None:
        if not self.isEnabled():
            return
        lim = self.limit_ticks / 10 ** self.decimals
        v, ok = QtWidgets.QInputDialog.getDouble(self, "目標值", f"目標（{self.unit_text}，±{lim:g}）：",
                                                 self.value_display(), -lim, lim, self.decimals)
        if ok:
            t = int(round(v * 10 ** self.decimals))
            if t != self.ticks:
                self.ticks = max(-self.limit_ticks, min(self.limit_ticks, t))
                self._render()
                self.edited.emit(self.value())
