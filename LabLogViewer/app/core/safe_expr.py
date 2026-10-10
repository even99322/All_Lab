"""Safe math expressions shared by the Viewer Formula and YIG Analysis.

One engine, three steps, no ``eval``/``exec`` of user text:

1. :func:`normalize` turns every accepted spelling into one Python-style
   expression: LaTeX (``\\frac{a}{b}``, ``\\sqrt{x}``, ``\\cdot``, ``\\pi``,
   ``\\sin``, ``\\log_{10}``, ``\\left|x\\right|``), Viewer style (``x^2``,
   ``|x|``) and Python style (``x**2``, ``np.log10(x)``), plus full-width
   ``− · × ÷ （）`` and ``π``.
2. :func:`validate` checks the parsed syntax tree against a whitelist:
   numbers, ``+ - * / ** %``, parentheses, known constants and variables,
   math functions and ``np.<math function>``. Imports, attribute access other
   than ``np.<name>``, names starting with ``__``, strings, subscripts,
   lambdas, comprehensions, comparisons and keyword arguments are refused.
3. :func:`evaluate` walks the validated tree with numpy.

YIG's Formula Builder embeds validated expressions into generated model code,
so that code contains only the template and whitelisted math.
"""

from __future__ import annotations

import ast
import operator
import re

import numpy as np


class SafeExprError(ValueError):
    """An expression is malformed or uses something outside the whitelist."""


MAX_NESTING = 32
MAX_NODES = 2000

CONSTANTS = {"pi": np.pi, "e": np.e, "inf": np.inf}

# Bare function names (also usable as np.<name>).
FUNCTIONS = {
    "sqrt": np.sqrt, "exp": np.exp, "log": np.log, "ln": np.log, "log10": np.log10, "log2": np.log2,
    "sin": np.sin, "cos": np.cos, "tan": np.tan, "arcsin": np.arcsin, "arccos": np.arccos,
    "arctan": np.arctan, "arctan2": np.arctan2, "sinh": np.sinh, "cosh": np.cosh, "tanh": np.tanh,
    "arcsinh": np.arcsinh, "arccosh": np.arccosh, "arctanh": np.arctanh,
    "abs": np.abs, "conj": np.conj, "real": np.real, "imag": np.imag, "angle": np.angle,
    "deg2rad": np.deg2rad, "rad2deg": np.rad2deg, "hypot": np.hypot, "sign": np.sign,
}
# np.<name>: numpy's element-wise math functions (ufuncs compute only) plus a
# few non-ufunc helpers. Never file / process access (np.load, np.save, ...).
NP_FUNCTIONS = {name for name in dir(np) if isinstance(getattr(np, name, None), np.ufunc)} | {
    "real", "imag", "angle", "round", "around", "clip", "unwrap", "sinc", "i0",
}
NP_CONSTANTS = {"pi": np.pi, "e": np.e, "inf": np.inf, "nan": np.nan, "euler_gamma": np.euler_gamma}
NP_EMATH = {"sqrt", "log", "log2", "log10", "logn", "power", "arccos", "arcsin", "arctanh"}

_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_GREEK = sorted(("alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi "
                 "rho sigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Sigma Phi Psi "
                 "Omega").split(), key=len, reverse=True)
_LATEX_FUNCTIONS = sorted((name for name in FUNCTIONS if name.isalpha()), key=len, reverse=True)


# -- 1. normalize --------------------------------------------------------------
def _group_end(text: str, start: int) -> int:
    if start >= len(text) or text[start] != "{":
        raise SafeExprError("LaTeX command requires a braced argument.")
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise SafeExprError("Unclosed LaTeX brace.")


def _latex(text: str) -> str:
    value = text.replace(r"\left", "").replace(r"\right", "")
    value = value.replace(r"\cdot", "*").replace(r"\times", "*").replace(r"\div", "/")
    value = value.replace(r"\lvert", "|").replace(r"\rvert", "|")
    value = value.replace(r"\pi", "pi").replace(r"\mathrm{e}", "e")
    value = re.sub(r"\\log_\{?10\}?", "log10", value)
    for name in _LATEX_FUNCTIONS:
        value = re.sub(r"\\" + name + r"(?![A-Za-z])", name, value)
    value = re.sub(r"\\mathrm\{i\}", "i", value)
    value = re.sub(r"\\(" + "|".join(_GREEK) + r")(?![A-Za-z])", r"\1", value)
    value = re.sub(r"_\{([A-Za-z0-9]+)\}", r"_\1", value)             # w_{0} -> w_0
    # Structural commands, expanded recursively; no TeX macros are executed.
    while True:
        match = re.search(r"\\(frac|sqrt)\s*", value)
        if match is None:
            break
        command, cursor = match.group(1), match.end()
        end = _group_end(value, cursor)
        first, cursor = _latex(value[cursor + 1:end]), end + 1
        if command == "frac":
            while cursor < len(value) and value[cursor].isspace():
                cursor += 1
            end = _group_end(value, cursor)
            second, cursor = _latex(value[cursor + 1:end]), end + 1
            replacement = f"(({first})/({second}))"
        else:
            replacement = f"sqrt({first})"
        value = value[:match.start()] + replacement + value[cursor:]
    return value.replace("{", "(").replace("}", ")")


def _bars(text: str) -> str:
    """|a| -> abs(a); bars alternate open / close (as in the Viewer before).

    A function written right before a bar (LaTeX ``\\log_{10}|y|``) gets it as
    its argument: ``log10(abs(y))``.
    """
    if "|" not in text:
        return text
    out: list[str] = []
    closing: str | None = None
    for char in text:
        if char != "|":
            out.append(char)
        elif closing is None:
            written = "".join(out)
            name = re.search(r"([A-Za-z_][A-Za-z0-9_.]*)\s*$", written)
            wraps = bool(name) and name.group(1).rsplit(".", 1)[-1] in FUNCTIONS
            out.append("(abs(" if wraps else "abs(")
            closing = "))" if wraps else ")"
        else:
            out.append(closing)
            closing = None
    if closing is not None:
        raise SafeExprError("Unclosed absolute-value bar |.")
    return "".join(out)


_NUMBER_BEFORE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)(?![\d.]|[eE][+-]?\d)[ \t]*(?=[A-Za-z_(])(?![jJ](?![\w(]))")
_CLOSE_BEFORE = re.compile(r"\)[ \t]*(?=[A-Za-z0-9_(])")
_NAME_SPACE_NAME = re.compile(r"(?<![\w.])([A-Za-z_]\w*)[ \t]+(?=[A-Za-z_0-9(])")
_KEYWORDS = {"and", "or", "not", "in", "is", "if", "else", "for", "lambda", "import", "from", "as"}


def _implicit_multiplication(text: str) -> str:
    """Hand-written products: ``20log10(y)``, ``2 pi x``, ``3(x+1)``, ``(a)(b)``, ``(a)x``.

    Only positions that are a syntax error in Python get a ``*``; ``2j`` (complex)
    and ``1e9`` (exponent) are left alone, so valid input never changes meaning.
    A function name followed by a space keeps its call (``sqrt (x)``).
    """
    text = _NUMBER_BEFORE.sub(r"\1*", text)
    text = _CLOSE_BEFORE.sub(")*", text)

    def names(match: re.Match) -> str:
        name = match.group(1)
        if name in FUNCTIONS or name in _KEYWORDS or name == "np":
            return match.group(0)
        return name + "*"

    return "\n".join(_NAME_SPACE_NAME.sub(names, line) for line in text.split("\n"))


def normalize(text: str, *, max_length: int = 512) -> str:
    """Any accepted spelling -> one Python-style expression (not yet validated)."""
    if len(text) > max_length:
        raise SafeExprError(f"Formula is too long (maximum {max_length} characters).")
    value = text.strip()
    for old, new in (("−", "-"), ("·", "*"), ("×", "*"), ("÷", "/"), ("（", "("), ("）", ")"), ("π", "pi")):
        value = value.replace(old, new)
    value = _implicit_multiplication(_bars(_latex(value)).replace("^", "**"))
    depth = 0
    for char in value:
        depth += char == "("
        depth -= char == ")"
        if depth > MAX_NESTING:
            raise SafeExprError("Formula nesting is too deep.")
    return value


# -- 2. validate ---------------------------------------------------------------
def _np_attribute(node: ast.Attribute) -> tuple[str, str] | None:
    """('np', name) / ('np.emath', name) for an allowed np attribute, else None."""
    if isinstance(node.value, ast.Name) and node.value.id == "np":
        return "np", node.attr
    if (isinstance(node.value, ast.Attribute) and node.value.attr == "emath"
            and isinstance(node.value.value, ast.Name) and node.value.value.id == "np"):
        return "np.emath", node.attr
    return None


def validate(tree: ast.AST, *, variables: set[str] | None = None, free_names: bool = False,
             allow_assign: bool = False, allow_complex: bool = True,
             operators: tuple = tuple(_BINARY), functions: set[str] | None = None) -> ast.AST:
    """Raise :class:`SafeExprError` unless ``tree`` is whitelisted math.

    ``variables``: names that may be read. ``free_names``: any other plain
    identifier may be read too (YIG model parameters become function
    arguments). Function names may only be called, never passed around.
    """
    variables = set(variables or ())
    functions = set(FUNCTIONS) if functions is None else set(functions)
    assigned: set[str] = set()
    count = 0

    def fail(message: str, node: ast.AST | None = None):
        line = getattr(node, "lineno", None)
        raise SafeExprError(f"{message} (line {line})" if line and allow_assign else message)

    def check_name(name: str, node) -> None:
        if name.startswith("__"):
            fail(f"Name '{name}' is not allowed.", node)

    def visit(node: ast.AST, depth: int) -> None:
        nonlocal count
        count += 1
        if count > MAX_NODES:
            fail("Formula has too many terms.")
        if depth > 3 * MAX_NESTING:
            fail("Formula nesting is too deep.")
        if isinstance(node, (ast.Expression,)):
            visit(node.body, depth + 1)
        elif isinstance(node, ast.Module):
            for statement in node.body:
                visit(statement, depth + 1)
        elif isinstance(node, ast.Expr):
            visit(node.value, depth + 1)
        elif isinstance(node, ast.Assign) and allow_assign:
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                fail("Use 'name = expression' for intermediate variables.", node)
            target = node.targets[0].id
            check_name(target, node)
            if target in FUNCTIONS or target in CONSTANTS or target == "np":
                fail(f"'{target}' is a reserved math name.", node)
            visit(node.value, depth + 1)
            assigned.add(target)
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in operators:
                fail(f"Operator '{type(node.op).__name__}' is not supported.", node)
            visit(node.left, depth + 1)
            visit(node.right, depth + 1)
        elif isinstance(node, ast.UnaryOp):
            if type(node.op) not in _UNARY:
                fail(f"Operator '{type(node.op).__name__}' is not supported.", node)
            visit(node.operand, depth + 1)
        elif isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, bool) or not isinstance(value, (int, float, complex)):
                fail("Only numbers are allowed as constants.", node)
            if isinstance(value, complex) and not allow_complex:
                fail("Complex numbers are not supported here.", node)
        elif isinstance(node, ast.Name):
            name = node.id
            check_name(name, node)
            if name in CONSTANTS or name in variables or name in assigned:
                return
            if name in FUNCTIONS or name == "np":
                fail(f"'{name}' can only be used as a function call.", node)
            if not free_names:
                fail(f"Unsupported name: {name}.", node)
        elif isinstance(node, ast.Attribute):
            found = _np_attribute(node)
            if found is None or found[0] != "np" or found[1] not in NP_CONSTANTS:
                fail(f"Attribute '{ast.unparse(node)}' is not allowed.", node)
        elif isinstance(node, ast.Call):
            if node.keywords:
                fail("Keyword arguments are not supported.", node)
            function = node.func
            if isinstance(function, ast.Name):
                if function.id not in functions:
                    fail(f"Unsupported function: {function.id}.", node)
            elif isinstance(function, ast.Attribute):
                found = _np_attribute(function)
                allowed = NP_FUNCTIONS if found and found[0] == "np" else NP_EMATH
                if found is None or found[1] not in allowed:
                    fail(f"Function '{ast.unparse(function)}' is not allowed.", node)
            else:
                fail("Unsupported function call.", node)
            if not node.args:
                fail("A function needs at least one argument.", node)
            for argument in node.args:
                if isinstance(argument, ast.Starred):
                    fail("Unsupported function argument.", node)
                visit(argument, depth + 1)
        else:
            fail(f"Unsupported syntax: {type(node).__name__}.", node)

    visit(tree, 0)
    return tree


def parse(text: str, *, mode: str = "eval", max_length: int = 512, **rules) -> ast.AST:
    """normalize + ast.parse + validate."""
    source = normalize(text, max_length=max_length)
    if not source:
        raise SafeExprError("The expression is empty.")
    try:
        tree = ast.parse(source, mode=mode)
    except SyntaxError as error:
        raise SafeExprError(f"Syntax error: {error.msg}.") from None
    except (RecursionError, MemoryError):
        raise SafeExprError("Formula nesting is too deep.") from None
    return validate(tree, **rules)


# -- 3. evaluate ---------------------------------------------------------------
def function_for(node: ast.AST):
    """The numpy callable of a validated call target."""
    if isinstance(node, ast.Name):
        return FUNCTIONS[node.id]
    kind, name = _np_attribute(node)
    return getattr(np.emath if kind == "np.emath" else np, name)


def evaluate(node: ast.AST, names: dict | None = None):
    """Evaluate a validated expression tree; integers are computed as floats."""
    names = names or {}
    if isinstance(node, ast.Expression):
        return evaluate(node.body, names)
    if isinstance(node, ast.Constant):
        value = node.value
        return float(value) if isinstance(value, int) else value
    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        return CONSTANTS[node.id]
    if isinstance(node, ast.Attribute):
        return NP_CONSTANTS[node.attr]
    if isinstance(node, ast.UnaryOp):
        return _UNARY[type(node.op)](evaluate(node.operand, names))
    if isinstance(node, ast.BinOp):
        return _BINARY[type(node.op)](evaluate(node.left, names), evaluate(node.right, names))
    if isinstance(node, ast.Call):
        return function_for(node.func)(*(evaluate(argument, names) for argument in node.args))
    raise SafeExprError(f"Unsupported syntax: {type(node).__name__}.")


def evaluate_number(text) -> float:
    """A numeric field (e.g. ``pi/4``, ``np.deg2rad(30)``, ``2.5e9``) -> float."""
    tree = parse(str(text).strip())
    try:
        return float(evaluate(tree))
    except ZeroDivisionError:
        raise SafeExprError("Division by zero.") from None
