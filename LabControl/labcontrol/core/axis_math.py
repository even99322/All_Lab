"""即時數據曲線的 x / y 軸運算（0.0.30）：例如 y = y**2、x = log(x)、y = y - mean(y)。

    f = compile_axis("y**2", "y")         # 也可以寫成 "y = y**2"
    f(x=頻率陣列, y=數值陣列) → 新的 y 陣列

只允許數字、x、y、pi、e、四則與次方運算和下面的 numpy 函式（不會執行任意程式碼）。
"""
from __future__ import annotations

import ast
import re
from typing import Callable, Dict, Optional

import numpy as np

FUNCS: Dict[str, Callable] = {
    "log": np.log, "ln": np.log, "log10": np.log10, "log2": np.log2, "exp": np.exp, "sqrt": np.sqrt,
    "abs": np.abs, "sign": np.sign, "sin": np.sin, "cos": np.cos, "tan": np.tan, "arcsin": np.arcsin,
    "arccos": np.arccos, "arctan": np.arctan, "sinh": np.sinh, "cosh": np.cosh, "tanh": np.tanh,
    "deg": np.degrees, "rad": np.radians, "floor": np.floor, "ceil": np.ceil, "round": np.round,
    "mean": np.nanmean, "median": np.nanmedian, "std": np.nanstd, "min": np.nanmin, "max": np.nanmax,
    "db": lambda v: 20 * np.log10(np.maximum(np.abs(v), 1e-300)),
    "undb": lambda v: 10 ** (np.asarray(v) / 20),
}
CONSTS = {"pi": np.pi, "e": np.e}
_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv, ast.USub, ast.UAdd)


class AxisMathError(ValueError):
    pass


def _check(node: ast.AST) -> None:
    for n in ast.walk(node):
        if isinstance(n, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Load)) or isinstance(n, _OPS):
            continue
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            continue
        if isinstance(n, ast.Name) and (n.id in ("x", "y", "np", "numpy") or n.id in CONSTS or n.id in FUNCS):
            continue
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in ("np", "numpy") \
                and n.attr in FUNCS:
            continue                                   # np.log(x) 也可以
        if isinstance(n, ast.Call) and not n.keywords and (
                (isinstance(n.func, ast.Name) and n.func.id in FUNCS)
                or (isinstance(n.func, ast.Attribute) and n.func.attr in FUNCS)):
            continue
        what = getattr(n, "id", None) or getattr(n, "attr", None) or type(n).__name__
        raise AxisMathError(f"不能用「{what}」：只能用 x、y、數字、+ - * / ** 與 {', '.join(sorted(FUNCS))}")


def normalize(text: str, var: str) -> str:
    """「y = y**2」→「y**2」；空白 / 只寫變數本身 → ""（不運算）。"""
    t = (text or "").strip()
    t = re.sub(rf"^\s*{var}\s*=(?!=)\s*", "", t)
    return "" if t in ("", var) else t


def compile_axis(text: str, var: str) -> Optional[Callable[..., np.ndarray]]:
    """text（例如 "log(x)"）→ f(x=, y=) → 陣列；空白 → None。語法錯誤 / 不允許的寫法 → AxisMathError。"""
    expr = normalize(text, var)
    if not expr:
        return None
    try:
        tree = ast.parse(expr.replace("^", "**"), mode="eval")
    except SyntaxError as e:
        raise AxisMathError(f"寫法錯誤：{e.msg}") from None
    _check(tree)
    tree = ast.fix_missing_locations(_Floats().visit(tree))     # 9**9**9 用浮點數算：溢位而不是算很久
    try:
        code = compile(tree, f"<{var}>", "eval")
    except (OverflowError, ValueError, MemoryError) as e:
        raise AxisMathError(f"算不出來：{e}") from None
    env = {"__builtins__": {}, **CONSTS, **FUNCS, "np": _NP, "numpy": _NP}

    def f(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        try:
            with np.errstate(all="ignore"):
                out = eval(code, env, {"x": x, "y": y})  # noqa: S307 — 只剩上面檢查過的節點
            out = np.asarray(out, float)
        except (ArithmeticError, ValueError, TypeError) as e:
            raise AxisMathError(f"算不出來：{e}") from None
        ref = x if var == "x" else y
        if out.shape != ref.shape:
            out = np.broadcast_to(out, ref.shape).astype(float)
        return out
    f.expr = expr  # type: ignore[attr-defined]
    f(np.array([1.0, 2.0]), np.array([1.0, 2.0]))          # 先試算一次：寫錯（例如 log() 沒有參數）現在就知道
    return f


class _Floats(ast.NodeTransformer):
    def visit_Constant(self, node: ast.Constant):  # noqa: N802
        return ast.copy_location(ast.Constant(float(node.value)), node)


class _NPProxy:
    """np.log 之類：只開放 FUNCS 裡的函式。"""

    def __getattr__(self, name: str):
        if name in FUNCS:
            return FUNCS[name]
        raise AttributeError(name)


_NP = _NPProxy()


class AxisMath:
    """一組 x、y 運算（即時數據的曲線用）。"""

    def __init__(self) -> None:
        self.x_text = ""
        self.y_text = ""
        self.fx: Optional[Callable] = None
        self.fy: Optional[Callable] = None

    def set(self, x_text: str = "", y_text: str = "") -> None:
        """設定運算；任何一個寫錯 → AxisMathError，原本的不變。"""
        fx, fy = compile_axis(x_text, "x"), compile_axis(y_text, "y")
        self.fx, self.fy = fx, fy
        self.x_text, self.y_text = (fx.expr if fx else ""), (fy.expr if fy else "")  # type: ignore[attr-defined]

    @property
    def active(self) -> bool:
        return self.fx is not None or self.fy is not None

    def apply(self, x: np.ndarray, y: np.ndarray):
        """(x, y) → (x', y')：y' 用原本的 x、y 算；x' 用原本的 x、y 算（兩個互不影響）。"""
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        nx = self.fx(x, y) if self.fx else x
        ny = self.fy(x, y) if self.fy else y
        return nx, ny
