"""公式檔載入、初值估計、表達式解析（純函式，不依賴 Qt）"""
import os
import inspect
import importlib.util
import numpy as np

from app.core.safe_expr import evaluate_number


def parse_num(text):
    """解析表格輸入，支援 pi/2、np.deg2rad(30)、\\frac{1}{2} 等表達式（安全計算，不執行程式碼）"""
    text = str(text).strip()
    if text == "":
        raise ValueError("This field cannot be blank.")
    return evaluate_number(text)


def load_formula_module(path):
    """載入 .py，回傳 (module, {函式名: 函式})；未受信任或含高危程式碼的檔案不會被執行"""
    from .trust import require_trusted

    require_trusted(path)
    name = "user_formula_" + os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    funcs = {}
    for fname, obj in inspect.getmembers(mod, inspect.isfunction):
        if obj.__module__ != mod.__name__ or fname.startswith("_") or fname.startswith("guess"):
            continue
        if len(inspect.signature(obj).parameters) >= 2:
            funcs[fname] = obj
    return mod, funcs


_MOD_CACHE = {}


def _load_cached(path):
    """依 (路徑, 修改時間) 快取；公式檔被修改後會自動重新載入（給擬合子行程用）"""
    key = (os.path.abspath(path), os.path.getmtime(path))
    if key not in _MOD_CACHE:
        _MOD_CACHE.clear()
        _MOD_CACHE[key] = load_formula_module(path)
    return _MOD_CACHE[key]


def get_function(path, func_name):
    funcs = _load_cached(path)[1]
    if func_name not in funcs:
        raise KeyError(f"Function {func_name} was not found in the model file.")
    return funcs[func_name]


def get_module(path):
    return _load_cached(path)[0]


# ----------------------------------------------------------------------------- 單位
FREQ_FACTORS = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}


def freq_factor(unit):
    """頻率單位 → 換算成 Hz 的倍率；非頻率單位回傳 None"""
    return FREQ_FACTORS.get(str(unit).strip().lower())


def heuristic_unit(name):
    l = name.lower()
    for suf, u in (("_ghz", "GHz"), ("_mhz", "MHz"), ("_khz", "kHz"), ("_hz", "Hz"),
                   ("_ns", "ns"), ("_rad", "rad"), ("_deg", "deg")):
        if l.endswith(suf):
            return u
    if "fano" in l or "theta" in l or "phi" in l:
        return "rad"
    if l in ("a", "amp", "amplitude") or l.startswith("amp"):
        return ""
    if l in ("t", "tau", "delay"):
        return "ns"
    if l[0] in "wf":
        return "GHz"
    if l[0] in "gk" or l.startswith(("alpha", "kappa", "gamma")):
        return "MHz"
    return ""


def get_units(module, func_name, names):
    """單位來源優先序：UNITS_<func> > UNITS > 名稱推測"""
    units = {n: heuristic_unit(n) for n in names}
    for attr in ("UNITS", f"UNITS_{func_name}"):
        d = getattr(module, attr, None) if module is not None else None
        if isinstance(d, dict):
            units.update({k: str(v) for k, v in d.items() if k in units})
    return units


def param_names(func):
    return list(inspect.signature(func).parameters)[1:]


def heuristic_guess(names, freq, s):
    """依參數名稱估計 (初值, 下界, 上界)"""
    fs, fe = freq[0] / 1e9, freq[-1] / 1e9
    f0 = freq[np.argmin(np.abs(s))] / 1e9
    amp = float(np.mean([np.abs(s[0]), np.abs(s[-1])]))
    ph = np.unwrap(np.angle(s))
    slope = (ph[-1] - ph[0]) / (freq[-1] - freq[0]) if freq[-1] != freq[0] else 0
    delay = float(np.clip(-slope / (2 * np.pi) * 1e9, -199, 199))
    phi0 = float((ph[0] - slope * freq[0] + np.pi) % (2 * np.pi) - np.pi)

    out = {}
    for n in names:
        l = n.lower()
        if "fano" in l:
            out[n] = (0.0, -np.pi, np.pi)
        elif l in ("phi_0", "phi0", "phase0", "phase_0"):
            out[n] = (phi0, -np.pi, np.pi)
        elif "ghz" in l or l[0] in "wf":
            out[n] = (f0, fs, fe)
        elif "theta" in l or "phi" in l:
            out[n] = (np.pi, 0.0, 2 * np.pi)
        elif l in ("a", "amp", "amplitude") or l.startswith("amp"):
            out[n] = (amp, 0.5 * amp, 1.5 * amp)
        elif l in ("t", "tau", "delay", "t_ns", "delay_ns"):
            out[n] = (delay, -200.0, 200.0)
        elif "mhz" in l or l[0] in "gk" or l.startswith(("alpha", "kappa", "gamma")):
            out[n] = (5.0, 0.0, 100.0)
        else:
            out[n] = (1.0, -np.inf, np.inf)
    return out


def guess_params(module, func_name, names, freq, s):
    """先用啟發式，再以公式檔中的 guess_<func>/guess 覆蓋。回傳 (dict, 來源, 警告訊息或 None)"""
    guess = heuristic_guess(names, freq, s)
    gfn = None
    if module is not None:
        gfn = getattr(module, "guess_" + func_name, None) or getattr(module, "guess", None)
    if not callable(gfn):
        return guess, "Built-in heuristic", None
    try:
        user = gfn(freq, s)
        guess.update({k: v for k, v in user.items() if k in guess})
        return guess, gfn.__name__, None
    except Exception as e:
        return guess, "Built-in heuristic", f"{gfn.__name__} failed; using built-in estimates: {e}"


def model_to_complex(y, n):
    """把公式輸出轉為 (complex 或 None, magnitude)"""
    y = np.asarray(y)
    if np.iscomplexobj(y) and y.size == n:
        return y, np.abs(y)
    if y.size == 2 * n:
        c = y[:n] + 1j * y[n:]
        return c, np.abs(c)
    if y.size == n:
        return None, np.abs(y)
    raise ValueError(f"Model output has {y.size} values for {n} data points (expected N or 2N).")
