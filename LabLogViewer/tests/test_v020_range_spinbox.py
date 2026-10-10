from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.gui.plot_2d_widget import robust_color_limits, zero_centered_limits
from app.gui.range_spinbox import RangeSpinBox


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def _type(spin, text):
    spin.lineEdit().setText(text)
    spin.interpretText()


def test_value_shows_significant_digits(qapp):
    spin = RangeSpinBox()
    spin.setValue(-0.0003)
    assert spin.text() == "-0.0003"
    spin.setValue(1.017e-6)
    assert spin.text() == "1.017e-06"
    spin.setValue(-56.76)
    assert spin.text() == "-56.76"


def test_editing_does_not_reset_and_commits_on_enter(qapp):
    spin = RangeSpinBox()
    spin.setValue(-0.0003)
    seen = []
    spin.valueChanged.connect(seen.append)
    spin.lineEdit().setText("-0.000")
    assert seen == [] and spin.lineEdit().text() == "-0.000"
    _type(spin, "-0.00045")
    assert seen == [-0.00045]
    _type(spin, "1.2e-4")
    assert spin.value() == pytest.approx(1.2e-4)


def test_partial_text_is_allowed_and_garbage_is_not(qapp):
    from PySide6.QtGui import QValidator

    spin = RangeSpinBox()
    for partial in ("-", "1e", "1e-", ".", ""):
        assert spin.validate(partial, 0)[0] != QValidator.State.Invalid
    assert spin.validate("abc", 0)[0] == QValidator.State.Invalid
    assert spin.validate("1.5e-3", 0)[0] == QValidator.State.Acceptable


def test_arrow_step_scales_with_value(qapp):
    spin = RangeSpinBox()
    spin.setValue(-56.76)
    spin.stepBy(1)
    assert spin.value() == pytest.approx(-56.76 + 1.0)
    spin.setValue(1e-6)
    spin.stepBy(1)
    assert spin.value() == pytest.approx(1.1e-6)


def test_auto_range_is_nearly_full_and_not_forced_symmetric():
    rng = np.random.default_rng(1)
    z = rng.normal(0.0, 1.0, 200_000)
    low, high = robust_color_limits(z)
    assert z.min() <= low < -3.0 and 3.0 < high <= z.max()
    assert (z < low).mean() < 2e-4
    assert zero_centered_limits(-0.3, 1e-6) == (-0.3, 1e-6)
    assert zero_centered_limits(-1.0, 0.9) == (-1.0, 1.0)
