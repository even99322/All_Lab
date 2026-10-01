from __future__ import annotations

import numpy as np
import pytest

from app.core.formula import (
    FormulaError, apply_formulas, formula_to_display, parse_formula,
)


def test_blank_formula_is_identity_and_x_y_are_evaluated_independently():
    x = np.array([1.0, 2.0, 3.0])
    y = np.array([2.0, 4.0, 8.0])
    result_x, result_y = apply_formulas(x, y, "", "y^2")
    assert np.array_equal(result_x, x)
    assert np.array_equal(result_y, y**2)

    result_x, result_y = apply_formulas(x, y, "x+y", "y/x")
    assert np.allclose(result_x, x + y)
    assert np.allclose(result_y, y / x)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        (r"\sqrt{y}", np.sqrt([1.0, 10.0, 100.0])),
        (r"\log_{10}(y)", np.log10([1.0, 10.0, 100.0])),
        (r"\ln(y)", np.log([1.0, 10.0, 100.0])),
        (r"\frac{y}{x}", np.array([1.0, 5.0, 100.0 / 3.0])),
        (r"\sqrt{\frac{|y|^2}{x^2 + 1}}", np.sqrt(np.array([1.0, 100.0, 10000.0]) / np.array([2.0, 5.0, 10.0]))),
        ("sin(y)", np.sin([1.0, 10.0, 100.0])),
    ],
)
def test_restricted_math_syntax(expression, expected):
    x = np.array([1.0, 2.0, 3.0])
    y = np.array([1.0, 10.0, 100.0])
    actual = apply_formulas(x, y, "", expression)[1]
    assert np.allclose(actual, expected)


def test_parser_rejects_arbitrary_python_and_unsafe_expression_features():
    for source in (
        "__import__(x)", "x.__class__", "open(1)",
        "y + os.system(1)", "sqrt.__globals__", "x[0]",
    ):
        with pytest.raises(FormulaError):
            parse_formula(source)


def test_invalid_shapes_nonfinite_only_and_deep_nesting_are_handled():
    with pytest.raises(FormulaError):
        apply_formulas(np.arange(3), np.arange(4), "", "y")
    with pytest.raises(FormulaError):
        apply_formulas(np.arange(3), np.arange(3), "", "1/0")
    with pytest.raises(FormulaError):
        parse_formula("(" * 40 + "x" + ")" * 40)
    result_x, result_y = apply_formulas(
        np.array([1.0, np.nan, 3.0]), np.array([2.0, 4.0, np.inf]), "", "y^2"
    )
    assert np.isnan(result_x[1])
    assert np.isinf(result_y[2])


def test_rendered_formula_preview_uses_safe_compiled_expression():
    tree = parse_formula(r"\sqrt{\frac{|y|^2}{x^2 + 1}}")
    display = formula_to_display(tree)
    assert "√" in display and "÷" in display and "²" in display
