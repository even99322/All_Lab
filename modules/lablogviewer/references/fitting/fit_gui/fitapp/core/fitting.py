"""擬合核心（純函式，不依賴 Qt；在獨立子行程中執行）"""
import time
import numpy as np
from scipy.optimize import curve_fit

from .formula import model_to_complex


def detect_mode(func, freq, p0):
    probe = np.asarray(func(freq, *p0))
    n = len(freq)
    if np.iscomplexobj(probe) and probe.size == n:
        return "complex_direct"
    if probe.size == 2 * n:
        return "complex_split"
    if probe.size == n:
        return "magnitude"
    raise ValueError(f"公式輸出長度 {probe.size} 與數據點數 {n} 不符")


def run_fit(func, freq, s, p0, lo, hi, fixed, opts, progress=None, progress_interval=0.3,
            link=None):
    """
    執行 curve_fit 並計算統計量。
    progress(nfev, cost, elapsed) 會以 progress_interval 秒為間隔被呼叫。
    link：dict(phase=索引, freq=索引, ffac, T, phi_ref, f_ref)
          → 相位參數不擬合，由 φ = φ_ref + 2π·T(ns)·(freq·ffac − f_ref) 決定
    """
    freq = np.asarray(freq, float)
    s = np.asarray(s, complex)
    p0, lo, hi = (np.array(x, float) for x in (p0, lo, hi))
    fixed = np.asarray(fixed, bool).copy()
    n = len(freq)
    if link is not None:
        fixed[link["phase"]] = True
        if fixed[link["freq"]]:
            p0[link["phase"]] = _link_phase(link, p0[link["freq"]])

    free_idx = np.where(~fixed)[0]
    if len(free_idx) == 0:
        raise ValueError("所有參數都被固定，沒有可擬合的參數")
    if n <= len(free_idx):
        raise ValueError("數據點數少於自由參數數量")

    mode = detect_mode(func, freq, p0)
    ydata = np.abs(s) if mode == "magnitude" else np.hstack([s.real, s.imag])

    sigma = None
    if opts.get("weight"):
        sg = np.abs(s) + opts.get("eps", 0.05)
        sigma = sg if mode == "magnitude" else np.hstack([sg, sg])

    full = p0.copy()
    t_start = time.perf_counter()
    state = {"nfev": 0, "last": t_start}

    def wrapped(w, *free):
        full[free_idx] = free
        if link is not None:
            full[link["phase"]] = _link_phase(link, full[link["freq"]])
        y = np.asarray(func(w, *full))
        y = np.hstack([y.real, y.imag]) if mode == "complex_direct" else np.real(y)
        state["nfev"] += 1
        if progress is not None:
            now = time.perf_counter()
            if now - state["last"] >= progress_interval:
                state["last"] = now
                r = y - ydata
                if sigma is not None:
                    r = r / sigma
                progress(state["nfev"], float(np.sum(r ** 2)), now - t_start)
        return y

    flo, fhi = lo[free_idx], hi[free_idx]
    fp0 = p0[free_idx]
    span = np.where(np.isfinite(fhi - flo), (fhi - flo) * 1e-9, 1e-12)
    fp0 = np.clip(fp0, flo + span, fhi - span)

    method = opts.get("method", "trf")
    popt, pcov = curve_fit(
        wrapped, freq, ydata, p0=fp0, bounds=(flo, fhi), sigma=sigma,
        absolute_sigma=False, method=method, x_scale="jac",
        loss=opts.get("loss", "linear"),
        ftol=opts.get("tol", 1e-12), xtol=opts.get("tol", 1e-12),
        maxfev=opts.get("maxfev", 300000),
    )

    params = p0.copy()
    params[free_idx] = popt
    errs = np.full(len(params), np.nan)
    with np.errstate(invalid="ignore"):
        errs[free_idx] = np.sqrt(np.diag(pcov))
    if link is not None:
        params[link["phase"]] = _link_phase(link, params[link["freq"]])
        errs[link["phase"]] = abs(2 * np.pi * 1e-9 * link["T"] * link["ffac"]) * errs[link["freq"]]

    y_fit = wrapped(freq, *popt)
    resid = y_fit - ydata
    if sigma is not None:
        resid = resid / sigma
    chi2_red = float(np.sum(resid ** 2) / max(1, ydata.size - len(popt)))

    c, mag = model_to_complex(func(freq, *params), n)
    stats = fit_statistics(s, c, mag)

    return {
        "params": params, "errors": errs, "mode": mode,
        "chi2_red": chi2_red, "nfev": state["nfev"],
        "elapsed": time.perf_counter() - t_start,
        "model_complex": c, "model_mag": mag, **stats,
    }


def _link_phase(link, fval):
    return link["phi_ref"] + 2 * np.pi * 1e-9 * link["T"] * (fval * link["ffac"] - link["f_ref"])


def fit_statistics(s, c, mag):
    with np.errstate(divide="ignore"):
        meas_db = 20 * np.log10(np.abs(s))
        fit_db = 20 * np.log10(mag)
    ss_tot = np.sum((meas_db - meas_db.mean()) ** 2)
    r2 = float(1 - np.sum((meas_db - fit_db) ** 2) / ss_tot) if ss_tot > 0 else float("nan")
    r2_c = float("nan")
    if c is not None:
        den = np.sum(np.abs(s - s.mean()) ** 2)
        if den > 0:
            r2_c = float(1 - np.sum(np.abs(s - c) ** 2) / den)
    return {"r2": r2, "r2_complex": r2_c}
