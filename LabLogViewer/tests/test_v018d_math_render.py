"""v0.18D: hand-written style formula previews (matrices, cases, aligned) that size to content."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("latex", [
    r"\frac{a}{b}",
    r"H = \begin{pmatrix} a & b \\ c & d \end{pmatrix}",
    r"\left[ \begin{matrix} 1 & 2 & 3 \\ 4 & 5 & 6 \end{matrix} \right]",
    r"f = \begin{cases} x & x > 0 \\ 0 & \text{otherwise} \end{cases}",
    r"\begin{aligned} a &= b \\ c &= d + e \end{aligned}",
    r"\begin{vmatrix} \begin{pmatrix} 1 & 0 \\ 0 & 1 \end{pmatrix} & x \\ y & z \end{vmatrix}",
    r"\begin{array}{lr} a & b \\ c & d \end{array}",
])
def test_environments_render(qapp, latex):
    from app.gui.math_render import check_latex, render_image

    assert check_latex(latex) is None
    assert not render_image(latex, ratio=1.0).isNull()


def test_matrix_is_taller_than_one_line_and_errors_are_reported(qapp):
    from app.gui.math_render import check_latex, render_image

    one = render_image("a + b", ratio=1.0)
    grid = render_image(r"\begin{pmatrix} a \\ b \\ c \end{pmatrix}", ratio=1.0)
    assert grid.height() > 2 * one.height()
    assert "line 1" in check_latex(r"\begin{pmatrix} a & b")
    assert "Unsupported environment" in check_latex(r"\begin{tikzpicture} x \end{tikzpicture}")


def test_formula_image_height_follows_the_equation(qapp):
    from PySide6.QtWidgets import QWidget, QVBoxLayout
    from app.analysis.yig_fitting.ui.latex_view import FormulaImage

    host = QWidget()
    host.resize(700, 900)
    image = FormulaImage(fontsize=16, fit_width=True)
    QVBoxLayout(host).addWidget(image)
    host.show()
    image.set_latex("a + b")
    small = image.height()
    image.set_latex(r"H = \begin{pmatrix} a & b \\ c & d \\ e & f \end{pmatrix}" + "\n" + r"x = \frac{1}{2}")
    assert image.height() > small + 40
    image.set_latex("a")
    assert image.height() <= small + 2
    host.close()


def test_viewer_latex_is_hand_written_style():
    from app.core.formula import _latex_bare, parse_formula

    assert _latex_bare(parse_formula("(x+1)/(y-2)")) == r"\frac{x + 1}{y - 2}"
    assert _latex_bare(parse_formula("(x*y)^2")) == r"\left(x \cdot y\right)^{2}"
    assert _latex_bare(parse_formula("x/1e9")) == r"\frac{x}{10^{9}}"
    assert _latex_bare(parse_formula("x*2.5e-6")) == r"x \cdot 2.5 \times 10^{-6}"
