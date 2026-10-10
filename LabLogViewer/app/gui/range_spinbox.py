"""Spin box for colour-range limits: free typing, any magnitude, no reset while editing."""
from __future__ import annotations

import math
import re

from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QAbstractSpinBox, QDoubleSpinBox

_PARTIAL = re.compile(r"^[+-]?(\d*\.?\d*)([eE][+-]?\d*)?$")


class RangeSpinBox(QDoubleSpinBox):
    """Shows significant digits (``-56.76``, ``1.017e-06``) instead of a fixed number of decimals.

    The typed text is applied on Enter or when the field loses focus, never per keystroke, so
    deleting the last digit of ``-0.0003`` leaves ``-0.000`` to keep editing instead of being
    rewritten as ``0.0000``. Arrow keys and the buttons still apply immediately, stepping by one
    tenth of the value's magnitude."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(-1e9, 1e9)
        self.setDecimals(15)
        self.setKeyboardTracking(False)
        self.setAccelerated(True)
        self.setStepType(QAbstractSpinBox.StepType.DefaultStepType)

    def textFromValue(self, value: float) -> str:  # noqa: N802 - Qt API spelling
        return f"{value:.6g}"

    def valueFromText(self, text: str) -> float:  # noqa: N802 - Qt API spelling
        try:
            return float(text.strip())
        except ValueError:
            return self.value()

    def validate(self, text: str, pos: int):  # noqa: A003 - Qt API
        stripped = text.strip()
        try:
            number = float(stripped)
        except ValueError:
            number = None
        if number is not None and math.isfinite(number):
            return QValidator.State.Acceptable, text, pos
        if _PARTIAL.match(stripped):
            return QValidator.State.Intermediate, text, pos
        return QValidator.State.Invalid, text, pos

    def stepBy(self, steps: int) -> None:  # noqa: N802 - Qt API spelling
        value = self.value()
        step = 10.0 ** (math.floor(math.log10(abs(value))) - 1) if value else 0.01
        self.setValue(value + steps * step)
