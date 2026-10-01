"""Restricted mathematical expressions for non-destructive Viewer formulas.

Parsing and the safety whitelist come from :mod:`app.core.safe_expr`, shared
with YIG Analysis: LaTeX (``\\frac``, ``\\sqrt``, ...), Viewer style (``x^2``,
``|y|``) and Python style (``x**2``, ``np.log10(y)``) are all accepted. The
result is a small private tuple tree evaluated with numpy; nothing is executed.
"""

from __future__ import annotations

import ast
import math
from typing import Any

import numpy as np

from app.core import safe_expr


class FormulaError(ValueError):
    """A formula is malformed, unsupported, or incompatible with its data."""


_VARIABLES = {"x", "y"}
_OPERATORS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Pow: "^"}
_VIEWER_FUNCTIONS = {
    "sqrt": np.sqrt, "log10": np.log10, "ln": np.log,
    "log": np.log, "exp": np.exp, "sin": np.sin,
    "cos": np.cos, "tan": np.tan, "abs": np.abs,
}
_MAX_TERMS = 256


def _to_tree(node: ast.AST):
    """Validated Python AST -> the Viewer's tuple tree."""
    if isinstance(node, ast.Constant):
        number = float(node.value)
        if not math.isfinite(number):
            raise FormulaError("Numeric constants must be finite.")
        return ("number", number)
    if isinstance(node, ast.Name):
        if node.id in _VARIABLES:
            return ("variable", node.id)
        if node.id in ("pi", "e"):
            return ("constant", node.id)
        raise FormulaError(f"Unsupported name: {node.id}.")
    if isinstance(node, ast.Attribute):                     # np.pi / np.e
        if node.attr in ("pi", "e"):
            return ("constant", node.attr)
        raise FormulaError(f"Unsupported name: {ast.unparse(node)}.")
    if isinstance(node, ast.UnaryOp):
        return ("unary+" if isinstance(node.op, ast.UAdd) else "unary-", _to_tree(node.operand))
    if isinstance(node, ast.BinOp):
        return (_OPERATORS[type(node.op)], _to_tree(node.left), _to_tree(node.right))
    if isinstance(node, ast.Call):
        function = node.func
        if isinstance(function, ast.Name):
            name = function.id
        else:
            kind, attr = safe_expr._np_attribute(function)
            name = attr if kind == "np" and attr in safe_expr.FUNCTIONS else f"{kind}.{attr}"
        return ("call", name, *(_to_tree(argument) for argument in node.args))
    raise FormulaError(f"Unsupported syntax: {type(node).__name__}.")


def parse_formula(text: str | None):
    """Parse a restricted expression into a private, non-executable tree."""
    if text is None or not text.strip():
        return None
    try:
        tree = safe_expr.parse(text, variables=_VARIABLES, allow_complex=False,
                               operators=tuple(_OPERATORS))
    except safe_expr.SafeExprError as error:
        raise FormulaError(str(error)) from None
    if sum(1 for _ in ast.walk(tree)) > 2 * _MAX_TERMS:
        raise FormulaError("Formula has too many terms.")
    return _to_tree(tree.body)


def _function(name: str):
    if name in _VIEWER_FUNCTIONS:
        return _VIEWER_FUNCTIONS[name]
    if name.startswith("np.emath."):
        return getattr(np.emath, name.split(".", 2)[2])
    if name.startswith("np."):
        return getattr(np, name[3:])
    return safe_expr.FUNCTIONS[name]


def evaluate_formula(tree, *, x: Any, y: Any):
    if tree is None:
        return None
    if tree[0] == "number":
        return tree[1]
    if tree[0] == "variable":
        return np.asarray(x if tree[1] == "x" else y)
    if tree[0] == "constant":
        return math.pi if tree[1] == "pi" else math.e
    if tree[0] == "unary+":
        return +evaluate_formula(tree[1], x=x, y=y)
    if tree[0] == "unary-":
        return -evaluate_formula(tree[1], x=x, y=y)
    if tree[0] == "call":
        return _function(tree[1])(*(evaluate_formula(argument, x=x, y=y) for argument in tree[2:]))
    left = evaluate_formula(tree[1], x=x, y=y)
    right = evaluate_formula(tree[2], x=x, y=y)
    operators = {
        "+": np.add, "-": np.subtract, "*": np.multiply,
        "/": np.divide, "^": np.power,
    }
    return operators[tree[0]](left, right)


def apply_formulas(x_values, y_values, x_formula: str = "", y_formula: str = "") -> tuple[np.ndarray, np.ndarray]:
    """Apply X/Y formulas simultaneously to the current displayed X/Y arrays.

    Viewer callers resolve axes and apply the existing Real/Imaginary/
    Magnitude/Phase transform, dB, and Unwrap first. Formula is therefore a
    final non-destructive display operation; both expressions reference the
    same pre-formula arrays rather than feeding one output into the other.
    """
    x = np.asarray(x_values)
    y = np.asarray(y_values)
    if x.shape != y.shape:
        raise FormulaError("X and Y formulas require matching sample shapes.")
    x_tree, y_tree = parse_formula(x_formula), parse_formula(y_formula)
    try:
        with np.errstate(all="ignore"):
            out_x = x if x_tree is None else np.asarray(evaluate_formula(x_tree, x=x, y=y))
            out_y = y if y_tree is None else np.asarray(evaluate_formula(y_tree, x=x, y=y))
            out_x, out_y = np.broadcast_arrays(out_x, out_y)
            out_x, out_y = np.asarray(out_x), np.asarray(out_y)
    except (ArithmeticError, FloatingPointError, TypeError, ValueError) as error:
        if isinstance(error, FormulaError):
            raise
        raise FormulaError(f"Formula could not be evaluated: {error}") from error
    if out_x.shape != x.shape:
        raise FormulaError("Formula result must preserve the sample shape.")
    if not np.any(np.isfinite(out_x) & np.isfinite(out_y)):
        raise FormulaError("Formula produced no finite displayed samples.")
    return out_x, out_y


_ATOMS = ("number", "variable", "constant", "call")


def _latex_bare(tree) -> str:
    """LaTeX without the parentheses a sum gets inside products / powers."""
    if tree is not None and tree[0] in ("+", "-"):
        right = formula_to_latex(tree[2]) if tree[0] == "-" else _latex_bare(tree[2])
        return f"{_latex_bare(tree[1])} {tree[0]} {right}"
    return formula_to_latex(tree)


def formula_to_latex(tree) -> str:
    """Hand-written style LaTeX: parentheses only where precedence needs them."""
    if tree is None:
        return r"\text{identity}"
    kind = tree[0]
    if kind == "number":
        text = f"{tree[1]:g}"
        if "e" in text:                                   # 1e+09 -> 10^{9}, 2.5e-3 -> 2.5 \times 10^{-3}
            mantissa, exponent = text.split("e")
            power = rf"10^{{{int(exponent)}}}"
            return power if mantissa == "1" else rf"{mantissa} \times {power}"
        return text
    if kind == "variable":
        return tree[1]
    if kind == "constant":
        return r"\pi" if tree[1] == "pi" else "e"
    if kind == "unary+":
        return "+" + formula_to_latex(tree[1])
    if kind == "unary-":
        inner = tree[1]
        text = formula_to_latex(inner)
        return "-" + (text if inner[0] in _ATOMS or inner[0] in ("^", "/", "+", "-") else rf"\left({text}\right)")
    if kind == "call":
        name = tree[1]
        if len(tree) != 3:
            arguments = ", ".join(_latex_bare(item) for item in tree[2:])
            return rf"\operatorname{{{name.rsplit('.', 1)[-1]}}}\left({arguments}\right)"
        argument = _latex_bare(tree[2])
        if name == "sqrt":
            return rf"\sqrt{{{argument}}}"
        if name == "log10":
            return rf"\log_{{10}}\left({argument}\right)"
        latex_name = {"ln": r"\ln", "log": r"\ln", "exp": r"\exp",
                      "sin": r"\sin", "cos": r"\cos", "tan": r"\tan"}.get(
            name, rf"\operatorname{{{name.rsplit('.', 1)[-1]}}}")
        return rf"\left|{argument}\right|" if name == "abs" else rf"{latex_name}\left({argument}\right)"
    if kind == "/":
        return rf"\frac{{{_latex_bare(tree[1])}}}{{{_latex_bare(tree[2])}}}"
    if kind == "^":
        base = tree[1]
        text = formula_to_latex(base)
        if base[0] not in ("number", "variable", "constant", "call", "+", "-") or \
                (base[0] == "number" and base[1] < 0):
            text = rf"\left({text}\right)"
        return rf"{text}^{{{_latex_bare(tree[2])}}}"
    left, right = formula_to_latex(tree[1]), formula_to_latex(tree[2])
    if kind == "*":
        return rf"{left} \cdot {right}"
    return rf"\left({_latex_bare(tree)}\right)"


def formula_to_display(tree) -> str:
    """Render a compact platform-native symbolic preview with Unicode math."""
    if tree is None:
        return "identity"
    kind = tree[0]
    if kind == "number":
        return f"{tree[1]:g}"
    if kind == "variable":
        return tree[1]
    if kind == "constant":
        return "π" if tree[1] == "pi" else "e"
    if kind == "unary+":
        return "+" + formula_to_display(tree[1])
    if kind == "unary-":
        return "−" + formula_to_display(tree[1])
    if kind == "call":
        name = tree[1]
        if len(tree) != 3:
            return f"{name.rsplit('.', 1)[-1]}({', '.join(formula_to_display(item) for item in tree[2:])})"
        argument = formula_to_display(tree[2])
        if name == "sqrt":
            return f"√({argument})"
        if name == "log10":
            return f"log₁₀({argument})"
        if name in ("ln", "log"):
            return f"ln({argument})"
        if name == "abs":
            return f"|{argument}|"
        return f"{name.rsplit('.', 1)[-1]}({argument})"
    left, right = formula_to_display(tree[1]), formula_to_display(tree[2])
    if kind == "^":
        superscripts = str.maketrans("0123456789+-()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾")
        exponent = right.translate(superscripts)
        return f"{left}{exponent}" if exponent != right else f"{left}^({right})"
    operator = {"*": " × ", "/": " ÷ ", "+": " + ", "-": " − "}[kind]
    return f"({left}{operator}{right})"


def formula_terms(tree) -> list[tuple[str, str]]:
    """Top-level ``+`` / ``-`` terms as (sign, LaTeX), for wrapping long previews."""
    if tree is None:
        return []
    if tree[0] in ("+", "-"):
        right = formula_to_latex(tree[2]) if tree[0] == "-" else _latex_bare(tree[2])
        return formula_terms(tree[1]) + [(tree[0], right)]
    return [("", formula_to_latex(tree))]
