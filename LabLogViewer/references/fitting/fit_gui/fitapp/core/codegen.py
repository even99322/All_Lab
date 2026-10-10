"""
公式產生器（純函式，不依賴 Qt）

輸入「理想 S 參數模型」的算式，自動加上：
    Fano 相位   ：S = B + (S_ideal − B)·e^{iθ_F}          （B = 背景項，預設 1）
    環境誤差    ：S21 = A·exp{ i[φ_0 − 2π(w − w_c)τ] }·S
並產生可直接給 GUI 載入的公式 .py（含 UNITS、guess_ 函式、|S| 版本）。

算式寫法（Python 語法，另外支援）：
    - i 代表虛數單位（可關閉），2i → 2j；支援隱式乘法 i(…)、2(…)、)(…)
    - ^ 自動轉成 **，全形 − · × 自動轉換
    - 可多行：前面幾行「名稱 = 算式」為中間變數，最後一行為 S 的算式（或 S = ...）
    - 可用函式：sqrt（負數開根號回傳複數）、exp、sin、cos、tan、log、abs、conj、real、imag、pi …
"""
import ast
import json
import keyword
import re
import textwrap

import numpy as np

SPEC_TAG = "# FORMULA_BUILDER_SPEC = "

MATH_NAMES = {
    "np", "pi", "e", "j", "inf", "sqrt", "exp", "sin", "cos", "tan", "arcsin", "arccos",
    "arctan", "arctan2", "sinh", "cosh", "tanh", "log", "log10", "abs", "conj", "real",
    "imag", "angle", "True", "False", "None",
}

# 輸入單位 → SI（Hz / s / rad）的倍率；None = 不換算
UNIT_SCALE = {
    "Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9,
    "s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9, "ps": 1e-12,
    "rad": None, "deg": np.pi / 180, "": None,
}
FREQ_UNITS = ("Hz", "kHz", "MHz", "GHz")
UNIT_CHOICES = ["GHz", "MHz", "kHz", "Hz", "rad", "deg", "ns", "ps", "s", ""]

ROLES = {"pos": "共振位置", "width": "線寬/耦合", "other": "其他"}

ENV_BASE = ("A", "phi_0", "t", "theta_fano")


# ============================================================================= 解析
def preprocess(src, imag_i=True):
    s = src.replace("^", "**").replace("−", "-").replace("·", "*").replace("×", "*")
    s = s.replace("（", "(").replace("）", ")")
    if imag_i:
        s = re.sub(r"(?<![\w.])(\d+(?:\.\d*)?)i\b", r"\1j", s)   # 2i → 2j
        s = re.sub(r"(?<![\w.])i(?![\w])", "1j", s)               # i → 1j
    # 隱式乘法：1j(…)、2(…)、)(…)
    s = re.sub(r"(?<![\w.])(\d+(?:\.\d*)?j?)\s*\(", r"\1*(", s)
    s = re.sub(r"\)\s*\(", ")*(", s)
    return s


def parse_model(src, freq_var="w", imag_i=True):
    """
    回傳 dict(lines=[(名稱, 算式)], final=算式, params=[依出現順序的參數名])
    """
    code = preprocess(src, imag_i).strip()
    if not code:
        raise ValueError("算式是空的")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        raise ValueError(f"算式語法錯誤（第 {e.lineno} 行）：{e.msg}")

    stmts = tree.body
    lines, final = [], None
    for k, st in enumerate(stmts):
        last = k == len(stmts) - 1
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            name = st.targets[0].id
            if last:
                final = ast.unparse(st.value)
            else:
                lines.append((name, ast.unparse(st.value)))
        elif isinstance(st, ast.Expr) and last:
            final = ast.unparse(st.value)
        else:
            raise ValueError(f"第 {k + 1} 句不支援：只能寫「名稱 = 算式」，最後一行為 S 的算式")
    if final is None:
        raise ValueError("最後一行必須是 S 的算式")

    assigned = {n for n, _ in lines}
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            names.append((node.lineno, node.col_offset, node.id))
    params = []
    for _, _, n in sorted(names):
        if n in MATH_NAMES or n == freq_var or n in assigned or n in params:
            continue
        if keyword.iskeyword(n):
            continue
        params.append(n)
    if freq_var not in {n for _, _, n in names}:
        raise ValueError(f"算式裡沒有用到頻率變數「{freq_var}」")
    return dict(lines=lines, final=final, params=params)


def default_param(name):
    """依名稱推測 (單位, 類型)"""
    l = name.lower()
    if "fano" in l or "theta" in l or "phi" in l:
        return "rad", "other"
    if l.startswith(("w", "f", "omega", "nu")) or "ghz" in l:
        return "GHz", "pos"
    if l.startswith(("kappa", "gamma", "alpha", "g", "k", "xi", "delta", "chi", "j_", "eta")) or "mhz" in l:
        return "MHz", "width"
    if l in ("tau",) or l.startswith("t_"):
        return "ns", "other"
    return "", "other"


def env_names(params):
    """環境參數名稱；與使用者參數衝突時加上 _env"""
    return {b: (b + "_env" if b in params else b) for b in ENV_BASE}


# ============================================================================= 產生程式碼
def _num(x):
    return "None" if x is None else repr(float(x))


def _safe_eval(text):
    text = str(text).strip()
    if not text:
        return None
    ns = {"pi": np.pi, "np": np, "e": np.e, "inf": np.inf, "sqrt": np.sqrt}
    return float(eval(text, {"__builtins__": {}}, ns))


def generate_code(spec):
    """spec → 公式檔原始碼"""
    name = spec["name"].strip()
    if not name.isidentifier() or keyword.iskeyword(name) or name.startswith("_"):
        raise ValueError(f"函式名稱「{name}」不是合法的 Python 名稱（不能以底線開頭）")
    fv = spec.get("freq_var", "w").strip() or "w"
    parsed = parse_model(spec["expr"], fv, spec.get("imag_i", True))
    bg_src = preprocess(spec.get("background", "1") or "1", spec.get("imag_i", True))
    try:
        bg_expr = ast.unparse(ast.parse(bg_src, mode="eval").body)
    except SyntaxError as e:
        raise ValueError(f"背景項語法錯誤：{e.msg}")

    pinfo = {p["name"]: p for p in spec.get("params", [])}
    params = parsed["params"]
    # 背景項也可能用到參數
    for node in ast.walk(ast.parse(bg_expr, mode="eval")):
        if isinstance(node, ast.Name) and node.id not in MATH_NAMES and node.id != fv \
                and node.id not in params and node.id not in dict(parsed["lines"]):
            params.append(node.id)

    rows = []
    for p in params:
        u0, r0 = default_param(p)
        info = pinfo.get(p, {})
        unit = info.get("unit", u0)
        role = info.get("role", r0)
        if role == "pos" and unit not in FREQ_UNITS:
            role = "other"
        rows.append(dict(name=p, unit=unit, role=role,
                         p0=_safe_eval(info.get("p0", "")),
                         lo=_safe_eval(info.get("lo", "")),
                         hi=_safe_eval(info.get("hi", ""))))

    fano, env = spec.get("fano", True), spec.get("env", True)
    conj, make_abs, si = spec.get("conj", False), spec.get("make_abs", True), spec.get("si", True)
    en = env_names(params)
    pos = [r for r in rows if r["role"] == "pos"]
    ref = spec.get("ref", "auto")
    if ref == "auto":
        ref = "mean_pos" if pos else "window"

    def fac(r):
        return UNIT_SCALE.get(r["unit"]) if si else None

    # ---- 參考頻率 w_c
    def wc_code(argnames):
        if ref == "window":
            return f"0.5 * (np.min({fv}) + np.max({fv}))"
        if ref == "mean_pos":
            terms = [f"{r['name']} * {fac(r) or 1.0!r}" for r in pos]
            return f"({' + '.join(terms)}) / {len(terms)}" if terms else f"0.5 * (np.min({fv}) + np.max({fv}))"
        r = next((x for x in rows if x["name"] == ref), None)
        if r is None:
            raise ValueError(f"延遲參考參數「{ref}」不存在")
        return f"{ref} * {fac(r) or 1.0!r}"

    plist = ", ".join(params)
    env_args = []
    if env:
        env_args += [en["A"], en["phi_0"], en["t"]]
    if fano:
        env_args += [en["theta_fano"]]
    abs_args = ([en["A"]] if env else []) + ([en["theta_fano"]] if fano else [])

    units = {r["name"]: r["unit"] for r in rows}
    if env:
        units.update({en["A"]: "", en["phi_0"]: "rad", en["t"]: "ns"})
    if fano:
        units[en["theta_fano"]] = "rad"
    units_abs = {k: v for k, v in units.items() if k not in (en["phi_0"], en["t"])}

    expr_doc = textwrap.indent(spec["expr"].strip(), "        ")
    L = []
    L.append(f'"""\n由「公式產生器」建立：{name}\n')
    L.append("理想模型：\n" + expr_doc + "\n")
    if fano:
        L.append(f"Fano 相位：S = B + (S_ideal − B)·exp(i·{en['theta_fano']})，B = {bg_expr}")
    if env:
        L.append(f"環境誤差：S21 = {en['A']}·exp{{ i[{en['phi_0']} − 2π({fv} − w_c)·{en['t']}] }}·S"
                 f"，w_c = {ref_label(ref)}")
    L.append(f"\n函式：\n    {name}(...)      複數擬合，回傳 [Re, Im]")
    if make_abs:
        L.append(f"    {name}_abs(...)  只擬合 |S|")
    L.append("\n輸入單位見 UNITS；" + ("函式內部會換算成 Hz / s / rad。" if si else "函式內部不做單位換算。"))
    L.append(f"頻率變數 {fv} 的單位為 Hz。")
    L.append('"""')
    L.append("import numpy as np")
    L.append("from numpy import (pi, exp, sin, cos, tan, arcsin, arccos, arctan, arctan2,")
    L.append("                   sinh, cosh, tanh, log, log10, conj, real, imag, angle)")
    L.append("sqrt = np.emath.sqrt   # 負數開根號回傳複數")
    L.append("abs = np.abs  # noqa: A001")
    L.append("e = np.e")
    L.append("")
    L.append(SPEC_TAG + json.dumps(spec, ensure_ascii=False))
    L.append("")
    L.append(f"UNITS_{name} = {units!r}")
    if make_abs:
        L.append(f"UNITS_{name}_abs = {units_abs!r}")
    L.append("")
    L.append(f"_PARAMS_{name} = [  # (名稱, 單位, 類型, 初值, 下界, 上界)；None = 自動估計")
    for r in rows:
        L.append(f"    ({r['name']!r}, {r['unit']!r}, {r['role']!r}, "
                 f"{_num(r['p0'])}, {_num(r['lo'])}, {_num(r['hi'])}),")
    L.append("]")
    L.append("")
    L.append("")
    # ---- 理想模型
    L.append(f"def _ideal_{name}({fv}, {plist}):")
    L.append('    """理想模型：回傳 (S_ideal, 背景項 B)"""')
    for r in rows:
        f_ = fac(r)
        if f_ is not None and f_ != 1.0:
            L.append(f"    {r['name']} = {r['name']} * {f_!r}")
    for n, ex in parsed["lines"]:
        L.append(f"    {n} = {ex}")
    L.append(f"    _s = {parsed['final']}")
    L.append(f"    _bg = {bg_expr}")
    L.append(f"    _z = np.zeros(np.shape({fv}), dtype=complex)")
    L.append("    return _s + _z, _bg + _z")
    L.append("")
    L.append("")

    def body(for_abs):
        out = [f"    _s, _bg = _ideal_{name}({fv}, {plist})"]
        if fano:
            out.append(f"    _s = _bg + (_s - _bg) * np.exp(1j * {en['theta_fano']})   # Fano 相位")
        if for_abs:
            if env:
                out.append(f"    return {en['A']} * np.abs(_s)")
            else:
                out.append("    return np.abs(_s)")
            return out
        if env:
            out.append(f"    _wc = {wc_code(params)}")
            out.append(f"    _s = {en['A']} * np.exp(1j * ({en['phi_0']} - 2 * np.pi * ({fv} - _wc) "
                       f"* {en['t']} * 1e-9)) * _s   # 環境誤差")
        if conj:
            out.append("    _s = np.conj(_s)   # 時間慣例（IQ 圖繞行方向）")
        else:
            out.append("    # 若 IQ 圖繞行方向與數據相反，加上：_s = np.conj(_s)")
        out.append("    return np.hstack([_s.real, _s.imag])")
        return out

    L.append(f"def {name}({fv}, {', '.join(params + env_args)}):")
    L.append('    """複數擬合：回傳 [Re, Im]"""')
    L += body(False)
    L.append("")
    L.append("")
    if make_abs:
        L.append(f"def {name}_abs({fv}, {', '.join(params + abs_args)}):")
        L.append('    """只擬合 |S|（相位 φ_0 與延遲 τ 在絕對值中消失）"""')
        L += body(True)
        L.append("")
        L.append("")

    # ---- 初值估計
    L.append(GUESS_HELPERS)
    L.append(f"def _guess_{name}(freq, s):")
    L.append("    freq = np.asarray(freq, float)")
    L.append("    s = np.asarray(s)")
    L.append("    fmin, fmax = float(freq.min()), float(freq.max())")
    L.append(f"    n_pos = sum(1 for p in _PARAMS_{name} if p[2] == 'pos')")
    L.append("    dips = _fb_dips(freq, s, n_pos)")
    L.append("    hw = _fb_halfwidth(freq, s)")
    L.append("    out, k = {}, 0")
    L.append(f"    for pname, unit, role, p0, lo, hi in _PARAMS_{name}:")
    L.append("        g = _fb_default(unit, role, dips[k] if role == 'pos' and k < len(dips) else None,")
    L.append("                        hw, fmin, fmax)")
    L.append("        if role == 'pos':")
    L.append("            k += 1")
    L.append("        g = [g[0] if p0 is None else p0, g[1] if lo is None else lo, g[2] if hi is None else hi]")
    L.append("        g[0] = float(np.clip(g[0], g[1], g[2]))")
    L.append("        out[pname] = tuple(g)")
    if env or fano:
        if env:
            L.append("    vals = {n: v[0] for n, v in out.items()}")
            L.append(f"    wc = {wc_guess(ref, rows, fv)}")
            L.append("    amp, phi0, delay = _fb_env(freq, s, wc)")
            L.append(f"    out[{en['A']!r}] = (amp, 0.5 * amp, 1.5 * amp)")
            L.append(f"    out[{en['phi_0']!r}] = (phi0, -np.pi, np.pi)")
            L.append(f"    out[{en['t']!r}] = (delay, -200.0, 200.0)")
        if fano:
            L.append(f"    out[{en['theta_fano']!r}] = (0.0, -np.pi, np.pi)")
    L.append("    return out")
    L.append("")
    L.append("")
    L.append(f"def guess_{name}(freq, s):")
    L.append(f"    return _guess_{name}(freq, s)")
    L.append("")
    if make_abs:
        L.append("")
        L.append(f"def guess_{name}_abs(freq, s):")
        L.append(f"    g = _guess_{name}(freq, s)")
        if env:
            L.append(f"    g.pop({en['phi_0']!r}, None)")
            L.append(f"    g.pop({en['t']!r}, None)")
        L.append("    return g")
        L.append("")
    return "\n".join(L)


def ref_label(ref):
    if ref == "window":
        return "擬合視窗中心"
    if ref == "mean_pos":
        return "所有共振位置參數的平均"
    return ref


def wc_guess(ref, rows, fv):
    """初值估計時的 w_c（Hz）運算式，使用 vals 字典"""
    def f(r):
        return UNIT_SCALE.get(r["unit"]) or 1.0
    pos = [r for r in rows if r["role"] == "pos"]
    if ref == "mean_pos" and pos:
        return "(" + " + ".join(f"vals[{r['name']!r}] * {f(r)!r}" for r in pos) + f") / {len(pos)}"
    r = next((x for x in rows if x["name"] == ref), None)
    if r is not None:
        return f"vals[{r['name']!r}] * {f(r)!r}"
    return "0.5 * (fmin + fmax)"


GUESS_HELPERS = '''# ---------------------------------------------------------------------------- 初值估計工具
_FB_SCALE = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9, "s": 1.0, "ms": 1e-3,
             "us": 1e-6, "ns": 1e-9, "ps": 1e-12}


def _fb_dips(freq, s, n):
    """找出最明顯的 n 個凹陷（Hz，由低到高）"""
    if n <= 0:
        return []
    mag = np.abs(s)
    f = []
    try:
        from scipy.signal import find_peaks
        pk, pr = find_peaks(-mag, prominence=0.05 * np.ptp(mag))
        order = np.argsort(pr["prominences"])[::-1][:n]
        f = sorted(float(x) for x in freq[pk[order]])
    except Exception:
        pass
    while len(f) < n:
        f.append(float(freq[np.argmin(mag)]))
    return sorted(f)


def _fb_halfwidth(freq, s):
    """最深凹陷的半高半寬（Hz）"""
    mag = np.abs(s)
    m = max(1, len(mag) // 10)
    base = float(np.median(np.r_[mag[:m], mag[-m:]]))
    i = int(np.argmin(mag))
    half = (mag[i] + base) / 2
    l = i
    while l > 0 and mag[l] < half:
        l -= 1
    r = i
    while r < len(mag) - 1 and mag[r] < half:
        r += 1
    df = abs(freq[1] - freq[0]) if len(freq) > 1 else 1.0
    return max((freq[r] - freq[l]) / 2, 3 * df)


def _fb_default(unit, role, dip_hz, hw_hz, fmin, fmax):
    sc = _FB_SCALE.get(unit)
    if role == "pos" and sc:
        f0 = dip_hz if dip_hz is not None else 0.5 * (fmin + fmax)
        return f0 / sc, fmin / sc, fmax / sc
    if role == "width" and sc:
        return hw_hz / sc, 0.0, 50 * hw_hz / sc
    if unit == "rad":
        return 0.1, -2 * np.pi, 2 * np.pi
    if unit == "deg":
        return 5.0, -360.0, 360.0
    if sc and unit in ("s", "ms", "us", "ns", "ps"):
        return 0.0, -200e-9 / sc, 200e-9 / sc
    return 1.0, -np.inf, np.inf


def _fb_env(freq, s, wc):
    """背景：振幅、w_c 處的相位、電纜延遲（ns）"""
    amp = float(np.mean([np.abs(s[0]), np.abs(s[-1])]))
    ph = np.unwrap(np.angle(s))
    slope = (ph[-1] - ph[0]) / (freq[-1] - freq[0]) if freq[-1] != freq[0] else 0.0
    delay = float(np.clip(-slope / (2 * np.pi) * 1e9, -199, 199))
    phi0 = ph[0] + slope * (wc - freq[0])
    phi0 = float((phi0 + np.pi) % (2 * np.pi) - np.pi)
    return amp, phi0, delay

'''


# ============================================================================= 驗證 / 讀回
def test_values(rows, env_map, fano, env):
    vals = {}
    for r in rows:
        if r.get("p0") not in (None, ""):
            try:
                vals[r["name"]] = _safe_eval(r["p0"])
                continue
            except Exception:
                pass
        sc = UNIT_SCALE.get(r["unit"]) or 1.0
        if r["role"] == "pos":
            vals[r["name"]] = 5e9 / sc
        elif r["role"] == "width":
            vals[r["name"]] = 1e6 / sc
        else:
            vals[r["name"]] = 0.1
    if env:
        vals.update({env_map["A"]: 1.0, env_map["phi_0"]: 0.0, env_map["t"]: 0.0})
    if fano:
        vals[env_map["theta_fano"]] = 0.0
    return vals


def validate(code, spec):
    """編譯並試算一次；回傳 (ok, 訊息)"""
    ns = {}
    try:
        exec(compile(code, "<generated>", "exec"), ns)
    except Exception as e:
        return False, f"產生的程式碼無法執行：{e}"
    name = spec["name"]
    fn = ns.get(name)
    if fn is None:
        return False, "找不到產生的函式"
    import inspect
    argn = list(inspect.signature(fn).parameters)[1:]
    fv = spec.get("freq_var", "w") or "w"
    parsed = parse_model(spec["expr"], fv, spec.get("imag_i", True))
    rows = [dict(name=p, unit=(spec_param(spec, p).get("unit", default_param(p)[0])),
                 role=(spec_param(spec, p).get("role", default_param(p)[1])),
                 p0=spec_param(spec, p).get("p0", "")) for p in parsed["params"]]
    vals = test_values(rows, env_names(parsed["params"]), spec.get("fano", True), spec.get("env", True))
    w = np.linspace(4.9e9, 5.1e9, 401)
    try:
        y = np.asarray(fn(w, *[vals.get(a, 0.1) for a in argn]))
    except Exception as e:
        return False, f"試算失敗（用測試參數值）：{e}"
    if y.size != 2 * w.size:
        return False, f"輸出長度 {y.size} 不正確"
    bad = int((~np.isfinite(y)).sum())
    if bad == y.size:
        return False, "試算結果全部是 NaN/inf，請檢查算式或初值"
    guess = ns.get("guess_" + name)
    try:
        guess(w, y[:w.size] + 1j * y[w.size:])
    except Exception as e:
        return False, f"guess 函式執行失敗：{e}"
    msg = "✓ 程式碼可正常執行"
    if bad:
        msg += f"（測試時有 {bad // 2} 點為 NaN/inf，可能是除以零，擬合時通常不影響）"
    return True, msg


def spec_param(spec, name):
    for p in spec.get("params", []):
        if p.get("name") == name:
            return p
    return {}


def load_spec(path):
    """從產生過的 .py 讀回設定（找不到回傳 None）"""
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith(SPEC_TAG):
                return json.loads(line[len(SPEC_TAG):])
    return None
