"""
公式庫（純函式 / 資料層，不依賴 Qt）

    config/formula_library.json   索引：標題、檔案、函式、LaTeX、備註、單位…
    config/formulas/              收錄時複製進來的公式檔（可攜、不怕原檔被移動）

另含：Python 算式 → LaTeX 轉換（不需 sympy），供公式產生器自動產生顯示用的公式圖。
"""
import ast
import hashlib
import json
import os
import shutil
import time
import uuid

from .paths import config_dir

# ============================================================================= 算式 → LaTeX
GREEK = {
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta", "theta",
    "vartheta", "iota", "kappa", "lambda", "mu", "nu", "xi", "pi", "rho", "sigma", "tau",
    "upsilon", "phi", "varphi", "chi", "psi", "omega",
    "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Phi", "Psi", "Omega",
}
ALIASES = {"w": r"\omega", "lam": r"\lambda", "lamb": r"\lambda", "eps": r"\epsilon"}

_P_ADD, _P_MUL, _P_UNARY, _P_POW, _P_ATOM = 1, 2, 3, 4, 5


SUB_ALIASES = {"fano": "F"}


def _sym(token, alias=True):
    if token in GREEK:
        return "\\" + token
    if alias and token in ALIASES:
        return ALIASES[token]
    if not alias and token in SUB_ALIASES:
        return SUB_ALIASES[token]
    if len(token) == 1 or token.isdigit():
        return token
    return r"\mathrm{" + token + "}"


def name_to_latex(name, freq_var=None):
    if freq_var and name == freq_var:
        return r"\omega_p" if name == "w" else _sym(name)
    parts = name.split("_")
    base = parts[0] or name
    sub = [p for p in parts[1:] if p]
    # g0 → g_0
    if not sub:
        i = len(base)
        while i > 0 and base[i - 1].isdigit():
            i -= 1
        if 0 < i < len(base):
            base, sub = base[:i], [base[i:]]
    out = _sym(base)
    if sub:
        out += "_{" + ",".join(_sym(s, alias=False) for s in sub) + "}"
    return out


def _num(v):
    if isinstance(v, complex):
        if v.real == 0:
            im = v.imag
            return "i" if im == 1 else ("-i" if im == -1 else f"{_num(im)}i")
        return f"({_num(v.real)}+{_num(v.imag)}i)"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


class _Tex:
    def __init__(self, freq_var):
        self.fv = freq_var

    def __call__(self, node):
        return self.go(node)[0]

    def paren(self, node, min_prec):
        s, p = self.go(node)
        return (r"\left(" + s + r"\right)") if p < min_prec else s

    def go(self, n):
        if isinstance(n, ast.Name):
            return name_to_latex(n.id, self.fv), _P_ATOM
        if isinstance(n, ast.Constant):
            s = _num(n.value)
            return s, (_P_UNARY if s.startswith("-") else _P_ATOM)
        if isinstance(n, ast.UnaryOp):
            op = {ast.USub: "-", ast.UAdd: "+"}.get(type(n.op), "")
            return op + self.paren(n.operand, _P_POW), _P_UNARY
        if isinstance(n, ast.BinOp):
            if isinstance(n.op, (ast.Add, ast.Sub)):
                sign = "+" if isinstance(n.op, ast.Add) else "-"
                left = self.paren(n.left, _P_ADD)
                right_min = _P_ADD if sign == "+" else _P_MUL
                right = self.paren(n.right, right_min)
                return f"{left} {sign} {right}", _P_ADD
            if isinstance(n.op, ast.Div):
                return r"\frac{" + self(n.left) + "}{" + self(n.right) + "}", _P_MUL
            if isinstance(n.op, ast.Mult):
                left = self.paren(n.left, _P_MUL)
                right = self.paren(n.right, _P_MUL + 1)
                sep = r" \cdot " if (left[-1:].isdigit() and right[:1].isdigit()) else r"\,"
                return left + sep + right, _P_MUL
            if isinstance(n.op, ast.Pow):
                base = self.paren(n.left, _P_ATOM)
                return base + "^{" + self(n.right) + "}", _P_POW
        if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute)):
            fn = n.func.id if isinstance(n.func, ast.Name) else n.func.attr
            args = [self(a) for a in n.args]
            a0 = args[0] if args else ""
            if fn == "sqrt":
                return r"\sqrt{" + a0 + "}", _P_ATOM
            if fn == "exp":
                return "e^{" + a0 + "}", _P_POW
            if fn == "abs":
                return r"\left|" + a0 + r"\right|", _P_ATOM
            if fn == "conj":
                return r"\left(" + a0 + r"\right)^{*}", _P_POW
            if fn in ("sin", "cos", "tan", "sinh", "cosh", "tanh", "log", "arctan"):
                return "\\" + fn + r"\left(" + ", ".join(args) + r"\right)", _P_ATOM
            return r"\mathrm{" + fn + r"}\left(" + ", ".join(args) + r"\right)", _P_ATOM
        return r"\mathrm{?}", _P_ATOM


def expr_to_latex(src, freq_var="w", imag_i=True, lhs="S"):
    """多行算式 → LaTeX 行列表"""
    from .codegen import preprocess
    tree = ast.parse(preprocess(src, imag_i).strip(), mode="exec")
    tex = _Tex(freq_var)
    lines = []
    for k, st in enumerate(tree.body):
        last = k == len(tree.body) - 1
        if isinstance(st, ast.Assign):
            name = st.targets[0].id if isinstance(st.targets[0], ast.Name) else "?"
            left = lhs if last else name_to_latex(name, freq_var)
            lines.append(f"{left} = {tex(st.value)}")
        elif isinstance(st, ast.Expr):
            lines.append(f"{lhs} = {tex(st.value)}")
    return lines


def latex_for_spec(spec, func_name=None):
    """公式產生器設定 → 完整顯示用 LaTeX（理想模型 + Fano + 環境）"""
    from .codegen import env_names, parse_model
    fv = spec.get("freq_var", "w") or "w"
    parsed = parse_model(spec["expr"], fv, spec.get("imag_i", True))
    en = env_names(parsed["params"])
    fano, env = spec.get("fano", True), spec.get("env", True)
    is_abs = bool(func_name) and func_name.endswith("_abs") and func_name != spec.get("name")
    ideal = r"S_{\mathrm{ideal}}"
    lines = expr_to_latex(spec["expr"], fv, spec.get("imag_i", True), lhs=ideal)
    cur = ideal
    w = name_to_latex(fv, fv)
    if fano:
        bg = expr_to_latex(spec.get("background", "1") or "1", fv, spec.get("imag_i", True), lhs="B")[0]
        bg = bg.split("=", 1)[1].strip()
        th = name_to_latex(en["theta_fano"])
        lines.append(rf"S_F = {bg} + \left({ideal} - {bg}\right) e^{{i {th}}}")
        cur = "S_F"
    if is_abs:
        amp = name_to_latex(en["A"]) + r"\," if env else ""
        lines.append(rf"\left|S_{{21}}\right| = {amp}\left|{cur}\right|")
    elif env:
        a, p0 = (name_to_latex(en[k]) for k in ("A", "phi_0"))
        t = r"\tau" if en["t"] == "t" else name_to_latex(en["t"])
        lines.append(rf"S_{{21}} = {a}\, e^{{i\left[{p0} - 2\pi\left({w} - \omega_c\right){t}\right]}}\, {cur}")
    else:
        lines.append(rf"S_{{21}} = {cur}")
    return "\n".join(lines)


# ============================================================================= 公式庫
def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class FormulaLibrary:
    FIELDS = ("id", "title", "file", "func", "latex", "notes", "units", "created", "updated", "source")

    def __init__(self, path=None):
        self.path = path or os.path.join(config_dir(), "formula_library.json")
        self.folder = os.path.dirname(self.path)
        self.formula_dir = os.path.join(self.folder, "formulas")
        os.makedirs(self.formula_dir, exist_ok=True)
        self.entries = []
        self.options = {"copy_files": True}
        self.load()

    # ---------------------------------------------------------------- 存取
    def load(self):
        for p in (self.path, self.path + ".bak"):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self.entries = list(data.get("formulas", []))
                self.options.update(data.get("options", {}))
                return
            except (OSError, ValueError):
                continue
        self.entries = []

    def save(self):
        data = {"version": 1, "options": self.options, "formulas": self.entries}
        tmp = self.path + ".tmp"
        os.makedirs(self.folder, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        if os.path.exists(self.path):
            try:
                shutil.copy2(self.path, self.path + ".bak")
            except OSError:
                pass
        os.replace(tmp, self.path)

    def resolve(self, entry):
        """相對路徑（公式庫資料夾內）→ 絕對路徑"""
        f = entry.get("file", "")
        return f if os.path.isabs(f) else os.path.normpath(os.path.join(self.folder, f))

    def _store_path(self, path):
        ap = os.path.abspath(path)
        try:
            rel = os.path.relpath(ap, self.folder)
            if not rel.startswith(".."):
                return rel.replace("\\", "/")
        except ValueError:
            pass
        return ap

    # ---------------------------------------------------------------- 操作
    def get(self, eid):
        return next((e for e in self.entries if e["id"] == eid), None)

    def find(self, path, func):
        ap = os.path.abspath(path) if path else ""
        for e in self.entries:
            if e.get("func") == func and os.path.abspath(self.resolve(e)) == ap:
                return e
        return None

    def copy_into_library(self, path):
        """把公式檔複製到 config/formulas/；內容相同的檔案不重複複製"""
        dst_dir = self.formula_dir
        src = os.path.abspath(path)
        if os.path.dirname(src) == os.path.abspath(dst_dir):
            return src
        md5 = _md5(src)
        for fn in os.listdir(dst_dir):
            cand = os.path.join(dst_dir, fn)
            if fn.endswith(".py") and os.path.isfile(cand) and _md5(cand) == md5:
                return cand
        stem, ext = os.path.splitext(os.path.basename(src))
        dst = os.path.join(dst_dir, stem + ext)
        k = 2
        while os.path.exists(dst):
            dst = os.path.join(dst_dir, f"{stem}_{k}{ext}")
            k += 1
        shutil.copy2(src, dst)
        return dst

    def add(self, path, func, title=None, latex="", notes="", units=None, source="file", copy=None):
        copy = self.options.get("copy_files", True) if copy is None else copy
        real = self.copy_into_library(path) if copy else os.path.abspath(path)
        e = dict(id=uuid.uuid4().hex[:10], title=title or func, file=self._store_path(real),
                 func=func, latex=latex, notes=notes, units=dict(units or {}),
                 created=_now(), updated=_now(), source=source,
                 origin=os.path.abspath(path))
        self.entries.append(e)
        self.save()
        return e

    def upsert(self, path, func, title=None, latex="", notes="", units=None, source="file"):
        """同一檔案（或已複製進公式庫的相同內容）同一函式已存在 → 更新；否則新增"""
        existing = self.find(path, func)
        if existing is None and self.options.get("copy_files", True):
            try:
                existing = self.find(self.copy_into_library(path), func)
            except OSError:
                existing = None
        if existing:
            return self.update(existing["id"], latex=latex or existing.get("latex", ""),
                               units=dict(units or existing.get("units") or {}),
                               notes=notes or existing.get("notes", ""),
                               title=title or existing.get("title", func))
        return self.add(path, func, title=title or func, latex=latex, notes=notes,
                        units=units, source=source)

    def update(self, eid, **kw):
        e = self.get(eid)
        if e is None:
            raise KeyError(eid)
        for k, v in kw.items():
            e[k] = v
        e["updated"] = _now()
        self.save()
        return e

    def remove(self, eid, delete_file=False):
        e = self.get(eid)
        if e is None:
            return
        self.entries.remove(e)
        if delete_file:
            p = self.resolve(e)
            used = any(os.path.abspath(self.resolve(x)) == os.path.abspath(p) for x in self.entries)
            inside = os.path.abspath(p).startswith(os.path.abspath(self.formula_dir))
            if inside and not used and os.path.exists(p):
                os.remove(p)
        self.save()

    def search(self, text):
        t = (text or "").strip().lower()
        if not t:
            return list(self.entries)
        return [e for e in self.entries
                if t in (e.get("title", "") + " " + e.get("func", "") + " " + e.get("notes", "")).lower()]
