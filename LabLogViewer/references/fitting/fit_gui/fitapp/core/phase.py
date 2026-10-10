"""
相位 / 節點工具（純函式，不依賴 Qt）

物理：YIG 在傳輸線上的位置相位 φ = k_m·x = 2π f_m · (x / v_g) + 常數
    → φ 隨共振頻率 f_m 線性變化：
        φ(f_m) = φ_ref + 2π · T · (f_m − f_ref)        T = x / v_g（單位 ns）
    κ_m = κ_b sin²φ、Δ_m ∝ sin 2φ 的週期都是 π，
    所以 φ = nπ 時耦合消失（2D 圖上訊號斷掉的「節點」），
    相鄰節點間距 Δf_node = 1 / (2T)。

    從單片擬合得到的 φ 只能確定到 mod π（還可能有 φ ↔ −φ 的鏡像解），
    因此相位直線以「折疊 + 網格搜尋」估計，不做一般的 unwrap。

提供：
    wrap / node_distance            —— 週期折疊、到最近節點的距離
    phase_line / node_frequencies   —— 直線求值、節點頻率
    fit_kappa_line                  —— 由逐片 κ_eff = κ_b sin²φ 估計 T、φ_ref（建議）
    fit_phase_line                  —— 由逐片 φ 估計 T、φ_ref
    line_from_nodes                 —— 由節點頻率估計 T、φ_ref
    detect_nodes                    —— 由訊號深度自動找節點
    run_global_fit                  —— 多片全域擬合（共用參數 + 每片參數 + 相位直線）
"""
import time
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix

from .fitting import detect_mode, fit_statistics
from .formula import model_to_complex

TWO_PI_NS = 2 * np.pi * 1e-9      # 2π·T(ns)·f(Hz) → rad

ROLES = {
    "slice": "每片獨立",
    "shared": "全片共用",
    "fixed": "固定",
    "link": "相位直線",
}


# ============================================================================ 基本
def wrap(x, period=np.pi):
    """折疊到 [-P/2, P/2)"""
    x = np.asarray(x, float)
    return (x + period / 2) % period - period / 2


def node_distance(phi, period=np.pi):
    """到最近節點 nP 的距離（rad）"""
    return np.abs(wrap(phi, period))


def phase_line(f_hz, T_ns, phi_ref, f_ref_hz):
    return phi_ref + TWO_PI_NS * T_ns * (np.asarray(f_hz, float) - f_ref_hz)


def node_frequencies(T_ns, phi_ref, f_ref_hz, fmin_hz, fmax_hz, period=np.pi):
    """回傳 [(n, f_n_hz), ...]，φ(f_n) = n·P"""
    if not T_ns or not np.isfinite(T_ns):
        return []
    k = TWO_PI_NS * T_ns
    ph = sorted((phase_line(fmin_hz, T_ns, phi_ref, f_ref_hz),
                 phase_line(fmax_hz, T_ns, phi_ref, f_ref_hz)))
    out = []
    for n in range(int(np.ceil(ph[0] / period)), int(np.floor(ph[1] / period)) + 1):
        f = f_ref_hz + (n * period - phi_ref) / k
        if fmin_hz <= f <= fmax_hz:
            out.append((n, float(f)))
    out.sort(key=lambda t: t[1])
    return out


# ============================================================================ 由 κ_eff 估計直線
def fit_kappa_line(f_hz, kappa, period=np.pi, T_max_ns=20.0, f_ref_hz=None,
                   weights=None, clip_sigma=4.0, T_min_ns=0.0):
    """
    單片擬合時 κ_b 與 φ 無法分開（只有 κ_eff = κ_b sin²φ 可觀測），
    所以以 κ_eff(f) = κ_b sin²(φ_ref + 2πT(f − f_ref)) 估計相位直線。
    sin² 對 φ → −φ 對稱，T 的正負無法由 κ 判斷，慣例回傳 T > 0。

    回傳 dict(T_ns, T_err, phi_ref, phi_ref_err, kappa_b, kappa_b_err, f_ref, rms, r2,
              n_used, n_total, inlier, f, kappa, node_spacing_hz)
    """
    f = np.asarray(f_hz, float)
    y = np.asarray(kappa, float)
    good = np.isfinite(f) & np.isfinite(y)
    f, y = f[good], y[good]
    if len(f) < 5:
        raise ValueError("至少需要 5 片成功的擬合結果才能由 κ_eff 估計相位直線")
    if f_ref_hz is None:
        f_ref_hz = float(0.5 * (f.min() + f.max()))
    w = np.ones(len(f)) if weights is None else np.asarray(weights, float)[good]
    span = max(float(f.max() - f.min()), 1.0)
    x = f - f_ref_hz
    om = 2 * np.pi / period          # sin²(πφ/P) = (1 − cos(ωφ))/2，ω = 2π/P
    dT = period / (TWO_PI_NS * span) / 16
    T_max = max(abs(float(T_max_ns)), 8 * dT)
    grid = np.arange(max(dT, abs(T_min_ns)), T_max + dT / 2, dT)
    if grid.size > 400000:
        raise ValueError("T 搜尋範圍相對頻率跨度太大，請縮小「T 搜尋上限」")

    sw = np.sqrt(w)
    ys = y * sw
    best = (np.inf, None)
    for s0 in range(0, grid.size, 2000):
        Tg = grid[s0:s0 + 2000]
        th = om * TWO_PI_NS * Tg[:, None] * x[None, :]          # (G, N)
        C, Sn = np.cos(th) * sw, np.sin(th) * sw
        one = np.broadcast_to(sw, C.shape)
        # 對每個 T 解 3×3 正規方程
        A = np.stack([one, C, Sn], axis=-1)                      # (G, N, 3)
        AtA = np.einsum("gni,gnj->gij", A, A)
        Aty = np.einsum("gni,n->gi", A, ys)
        try:
            coef = np.linalg.solve(AtA, Aty[..., None])[..., 0]
        except np.linalg.LinAlgError:
            continue
        res = ys[None, :] - np.einsum("gni,gi->gn", A, coef)
        cost = np.sum(res ** 2, axis=1)
        i = int(np.argmin(cost))
        if cost[i] < best[0]:
            a, b, c = coef[i]
            best = (cost[i], (Tg[i], a, b, c))
    if best[1] is None:
        raise ValueError("相位直線估計失敗")
    T0, a, b, c = best[1]
    # a + b cosθ + c sinθ = κ_b/2 − κ_b/2 cos(θ + ωφ_ref)
    kb0 = max(2 * a, 2 * np.hypot(b, c), 1e-30)
    pr0 = float(np.arctan2(c, -b) / om)

    def model(q, xx):
        kb, T, pr = q
        return kb * np.sin(np.pi / period * (pr + TWO_PI_NS * T * xx)) ** 2

    inl = np.ones(len(f), bool)
    q = np.array([kb0, T0, pr0])
    for _ in range(4):
        sol = least_squares(lambda qq: sw[inl] * (model(qq, x[inl]) - y[inl]), q,
                            x_scale=[kb0, dT * 16, 0.3])
        q = sol.x
        r_all = model(q, x) - y
        sig = 1.4826 * np.median(np.abs(r_all[inl])) + 1e-30
        new = np.abs(r_all) <= clip_sigma * sig
        if new.sum() < 5 or np.array_equal(new, inl):
            break
        inl = new
    kb, T, pr = q
    if T < 0:
        T, pr = -T, -pr
    if kb < 0:
        kb = -kb
    pr = float(wrap(pr, period))
    errs = [np.nan] * 3
    try:
        J, r = sol.jac, sol.fun
        cov = np.linalg.inv(J.T @ J) * (r @ r) / max(1, len(r) - 3)
        errs = list(np.sqrt(np.clip(np.diag(cov), 0, None)))
    except np.linalg.LinAlgError:
        pass
    r_all = model([kb, T, pr], x) - y
    ss = np.sum((y[inl] - y[inl].mean()) ** 2)
    return dict(T_ns=float(T), T_err=float(errs[1]), phi_ref=pr, phi_ref_err=float(errs[2]),
                kappa_b=float(kb), kappa_b_err=float(errs[0]), f_ref=float(f_ref_hz),
                rms=float(np.sqrt(np.mean(r_all[inl] ** 2))),
                r2=float(1 - np.sum(r_all[inl] ** 2) / ss) if ss > 0 else np.nan,
                n_used=int(inl.sum()), n_total=int(len(f)), inlier=inl, f=f, kappa=y,
                node_spacing_hz=float(period / (TWO_PI_NS * T)))


# ============================================================================ 由逐片 φ 估計直線
def _resultant(d, period):
    z = np.exp(1j * 2 * np.pi / period * d)
    m = z.mean(axis=-1)
    return np.abs(m), np.angle(m) * period / (2 * np.pi)


def fit_phase_line(f_hz, phi, period=np.pi, T_max_ns=20.0, f_ref_hz=None,
                   allow_mirror=True, weights=None, clip_sigma=3.0):
    """
    以折疊殘差估計 φ = φ_ref + 2π T (f − f_ref)（mod period）

    allow_mirror：同時嘗試 −φ（對稱解），取較好的
    回傳 dict(T_ns, T_err, phi_ref, f_ref, sign, rms, n_used, inlier, resid, phi_unwrapped)
    """
    f = np.asarray(f_hz, float)
    p = np.asarray(phi, float)
    good = np.isfinite(f) & np.isfinite(p)
    f, p = f[good], p[good]
    if len(f) < 3:
        raise ValueError("至少需要 3 片成功的擬合結果才能估計相位直線")
    if f_ref_hz is None:
        f_ref_hz = float(0.5 * (f.min() + f.max()))
    span = max(float(f.max() - f.min()), 1.0)
    x = f - f_ref_hz
    # 網格解析度：T 改變 dT 時，最遠端相位差 ≲ P/16
    dT = period / (TWO_PI_NS * span) / 16
    T_max = max(abs(float(T_max_ns)), 4 * dT)
    grid = np.arange(-T_max, T_max + dT / 2, dT)
    if grid.size > 400000:
        raise ValueError("T 搜尋範圍相對頻率跨度太大，請縮小「T 搜尋上限」")

    best = None
    for sign in ((1, -1) if allow_mirror else (1,)):
        ps = sign * p
        R = np.empty(grid.size)
        for s0 in range(0, grid.size, 2000):
            Tg = grid[s0:s0 + 2000, None]
            d = ps[None, :] - TWO_PI_NS * Tg * x[None, :]
            R[s0:s0 + 2000], _ = _resultant(d, period)
        i = int(np.argmax(R))
        if best is None or R[i] > best[0] + 1e-9:
            best = (R[i], sign, grid[i])
    _, sign, T0 = best
    ps = sign * p
    _, phi0 = _resultant(ps - TWO_PI_NS * T0 * x, period)

    inl = np.ones(len(f), bool)
    w = np.ones(len(f)) if weights is None else np.asarray(weights, float)[good]
    T, pr = T0, phi0
    for _ in range(3):
        def res(q):
            return np.sqrt(w[inl]) * wrap(ps[inl] - q[1] - TWO_PI_NS * q[0] * x[inl], period)
        sol = least_squares(res, [T, pr], x_scale=[dT * 16, 0.3])
        T, pr = sol.x
        r_all = wrap(ps - pr - TWO_PI_NS * T * x, period)
        sig = 1.4826 * np.median(np.abs(r_all[inl])) + 1e-12
        new = np.abs(r_all) <= clip_sigma * max(sig, 1e-3)
        if new.sum() < 3 or np.array_equal(new, inl):
            break
        inl = new

    J = sol.jac
    r = sol.fun
    dof = max(1, len(r) - 2)
    T_err = np.nan
    try:
        cov = np.linalg.inv(J.T @ J) * (r @ r) / dof
        T_err = float(np.sqrt(cov[0, 0]))
        pr_err = float(np.sqrt(cov[1, 1]))
    except np.linalg.LinAlgError:
        pr_err = np.nan

    # 以符號 sign 定義的直線；回報時換回原始 φ 的座標：φ ≈ sign·(φ_ref + 2πT x)
    T_out, pr_out = sign * T, sign * pr
    pr_out = float(wrap(pr_out, period))
    model = phase_line(f, T_out, pr_out, f_ref_hz)
    resid = wrap(p - model, period)
    unwrapped = model + resid
    return dict(T_ns=float(T_out), T_err=T_err, phi_ref=float(pr_out), phi_ref_err=pr_err,
                f_ref=float(f_ref_hz), sign=int(sign),
                rms=float(np.sqrt(np.mean(resid[inl] ** 2))),
                n_used=int(inl.sum()), n_total=int(len(f)),
                inlier=inl, resid=resid, f=f, phi_unwrapped=unwrapped,
                node_spacing_hz=float(period / (TWO_PI_NS * abs(T_out))) if T_out else np.inf)


# ============================================================================ 由節點估計直線
def line_from_nodes(nodes_hz, period=np.pi, f_ref_hz=None, n0=0, sign=1):
    """
    相鄰節點 φ 相差一個週期：f_n = f_a + n · Δf，T = P / (2π Δf)
    sign：φ 隨頻率增加(+1) 或減少(-1)（只由節點無法判斷，需由擬合的 φ 決定）
    """
    f = np.sort(np.asarray([x for x in nodes_hz if np.isfinite(x)], float))
    if len(f) < 2:
        raise ValueError("至少需要 2 個節點頻率")
    d = np.diff(f)
    base = np.median(d)
    if base <= 0:
        raise ValueError("節點頻率重複")
    n = np.concatenate([[0], np.cumsum(np.maximum(1, np.round(d / base)))]).astype(float)
    A = np.vstack([np.ones_like(n), n]).T
    (fa, df), *_ = np.linalg.lstsq(A, f, rcond=None)
    resid = f - (fa + df * n)
    T = sign * period / (TWO_PI_NS * df)
    if f_ref_hz is None:
        f_ref_hz = float(0.5 * (f.min() + f.max()))
    # φ(fa) = n0·P
    phi_ref = n0 * period + TWO_PI_NS * T * (f_ref_hz - fa)
    return dict(T_ns=float(T), phi_ref=float(phi_ref), f_ref=float(f_ref_hz),
                spacing_hz=float(df), rms_hz=float(np.sqrt(np.mean(resid ** 2))),
                n_index=n.astype(int).tolist(), nodes=f.tolist())


# ============================================================================ 自動偵測節點
def signal_depth(freq, s, f1, f2, smooth=5):
    """
    視窗內訊號強度：max | |S| − 中位數 | / 中位數（同時涵蓋凹陷與凸起）
    先做 smooth 點滑動平均以壓低雜訊
    """
    m = (freq >= f1) & (freq <= f2)
    if m.sum() < max(3, smooth):
        return np.nan
    a = np.abs(np.asarray(s)[m])
    if smooth and smooth > 1:
        a = np.convolve(a, np.ones(smooth) / smooth, mode="valid")
    med = np.median(a)
    return float(np.max(np.abs(a - med)) / med) if med > 0 else np.nan


def detect_nodes(f_hz, depth, rel_thresh=0.35, min_sep_hz=None, smooth=3):
    """
    訊號深度 vs 共振頻率 → 局部最小且低於 rel_thresh × (高分位深度) 的位置視為節點
    回傳節點頻率 list（Hz），以拋物線內插細化
    """
    f = np.asarray(f_hz, float)
    d = np.asarray(depth, float)
    g = np.isfinite(f) & np.isfinite(d)
    f, d = f[g], d[g]
    if len(f) < 5:
        return []
    o = np.argsort(f)
    f, d = f[o], d[o]
    if smooth and smooth > 1:
        h = smooth // 2
        d = np.array([np.median(d[max(0, i - h):i + h + 1]) for i in range(len(d))])
    top = np.percentile(d, 90)
    thr = rel_thresh * top
    if min_sep_hz is None:
        min_sep_hz = 3 * np.median(np.diff(f)) if len(f) > 1 else 0
    low = d < thr
    nodes = []
    i = 0
    while i < len(f):
        if not low[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(f) and low[j + 1]:
            j += 1
        seg = slice(i, j + 1)
        k = i + int(np.argmin(d[seg]))
        # 區段兩側要有訊號（避免把資料邊緣的弱訊號當節點）
        if i > 0 and j < len(f) - 1:
            fk = f[k]
            if 0 < k < len(f) - 1:
                x = f[k - 1:k + 2]
                y = d[k - 1:k + 2]
                try:
                    a, b, _ = np.polyfit(x - x[1], y, 2)
                    if a > 0:
                        fk = float(np.clip(x[1] - b / (2 * a), x[0], x[2]))
                except Exception:
                    pass
            if not nodes or fk - nodes[-1] >= min_sep_hz:
                nodes.append(float(fk))
        i = j + 1
    return nodes


# ============================================================================ 全域擬合
def run_global_fit(func, slices, names, roles, init, lo, hi, link=None, opts=None,
                   progress=None, progress_interval=0.4):
    """
    slices : list of dict(freq, s, params(每片初值), lo, hi（每片邊界，可選）, idx)
    roles  : 每個參數的角色 slice / shared / fixed / link
    init, lo, hi : 共用參數的初值與邊界（完整長度）
    link   : dict(phase, freq, ffac, T, phi_ref, f_ref, fit_T, fit_phi) 或 None
             phase 參數 = φ_ref + 2π T (freq 參數·ffac − f_ref)
    回傳 dict(shared, shared_err, T, T_err, phi_ref, phi_ref_err, params (K×n), errors (K×n),
              r2 (K), r2_complex (K), chi2_red, nfev, elapsed, message, success)
    """
    opts = opts or {}
    names = list(names)
    n = len(names)
    roles = list(roles)
    init, lo, hi = (np.asarray(v, float).copy() for v in (init, lo, hi))
    K = len(slices)
    if K == 0:
        raise ValueError("沒有可用的切片")

    li = None
    if link:
        pi_, fi_ = names.index(link["phase"]), names.index(link["freq"])
        if roles[fi_] not in ("slice", "shared"):
            raise ValueError(f"相位直線需要頻率參數 {link['freq']} 為「每片獨立」或「全片共用」")
        roles[pi_] = "link"
        li = dict(p=pi_, f=fi_, ffac=float(link["ffac"]), f_ref=float(link["f_ref"]))
    elif "link" in roles:
        raise ValueError("有參數設為「相位直線」，但沒有提供相位直線設定")

    sh = [j for j in range(n) if roles[j] == "shared"]
    sl = [j for j in range(n) if roles[j] == "slice"]
    if not sl and not sh:
        raise ValueError("沒有可擬合的參數")

    # ---- 參數向量配置
    x0, xl, xh = [], [], []
    for j in sh:
        x0.append(init[j]); xl.append(lo[j]); xh.append(hi[j])
    link_cols = {}
    if li is not None:
        if link.get("fit_T", True):
            link_cols["T"] = len(x0)
            x0.append(float(link["T"])); xl.append(-np.inf); xh.append(np.inf)
        if link.get("fit_phi", True):
            link_cols["phi"] = len(x0)
            x0.append(float(link["phi_ref"])); xl.append(-np.inf); xh.append(np.inf)
    n_glob = len(x0)
    for k, S in enumerate(slices):
        pk = np.asarray(S["params"], float)
        lk = np.asarray(S.get("lo", lo), float)
        hk = np.asarray(S.get("hi", hi), float)
        for j in sl:
            x0.append(pk[j]); xl.append(lk[j]); xh.append(hk[j])
    x0, xl, xh = (np.asarray(v, float) for v in (x0, xl, xh))
    span = np.where(np.isfinite(xh - xl), (xh - xl) * 1e-9, 1e-12)
    x0 = np.clip(x0, xl + span, xh - span)
    ns = len(sl)

    # ---- 資料
    data = []
    base = init.copy()
    for k, S in enumerate(slices):
        f = np.asarray(S["freq"], float)
        s = np.asarray(S["s"], complex)
        full = base.copy()
        pk = np.asarray(S["params"], float)
        for j in sl:
            full[j] = pk[j]
        for j in range(n):
            if roles[j] == "fixed":
                full[j] = init[j]
        if li is not None:
            full[li["p"]] = phase_line(full[li["f"]] * li["ffac"], link["T"],
                                       link["phi_ref"], li["f_ref"])
        mode = detect_mode(func, f, full)
        y = np.abs(s) if mode == "magnitude" else np.hstack([s.real, s.imag])
        if opts.get("weight"):
            sg = np.abs(s) + opts.get("eps", 0.05)
            sg = sg if mode == "magnitude" else np.hstack([sg, sg])
        else:
            sg = np.ones_like(y)
        data.append(dict(f=f, s=s, y=y, w=1.0 / sg, mode=mode, n=len(f)))
    offs = np.concatenate([[0], np.cumsum([d["y"].size for d in data])])
    M = int(offs[-1])
    if M <= len(x0):
        raise ValueError("數據點數少於自由參數數量")

    def unpack(x, k):
        full = init.copy()
        for c, j in enumerate(sh):
            full[j] = x[c]
        b = n_glob + k * ns
        for c, j in enumerate(sl):
            full[j] = x[b + c]
        T = x[link_cols["T"]] if "T" in link_cols else (link["T"] if link else 0.0)
        pr = x[link_cols["phi"]] if "phi" in link_cols else (link["phi_ref"] if link else 0.0)
        if li is not None:
            full[li["p"]] = phase_line(full[li["f"]] * li["ffac"], T, pr, li["f_ref"])
        return full, T, pr

    def model_y(k, full):
        d = data[k]
        y = np.asarray(func(d["f"], *full))
        if d["mode"] == "complex_direct":
            y = np.hstack([y.real, y.imag])
        return np.real(y)

    t0 = time.perf_counter()
    st = {"nfev": 0, "last": t0}

    def residual(x):
        out = np.empty(M)
        for k in range(K):
            full, _, _ = unpack(x, k)
            d = data[k]
            out[offs[k]:offs[k + 1]] = (model_y(k, full) - d["y"]) * d["w"]
        st["nfev"] += 1
        if progress is not None:
            now = time.perf_counter()
            if now - st["last"] >= progress_interval:
                st["last"] = now
                progress(st["nfev"], float(out @ out), now - t0)
        return out

    sp = lil_matrix((M, len(x0)), dtype=np.int8)
    for k in range(K):
        r0, r1 = offs[k], offs[k + 1]
        if n_glob:
            sp[r0:r1, :n_glob] = 1
        b = n_glob + k * ns
        if ns:
            sp[r0:r1, b:b + ns] = 1

    sol = least_squares(
        residual, x0, bounds=(xl, xh), jac_sparsity=sp, method="trf", x_scale="jac",
        loss=opts.get("loss", "linear"),
        ftol=max(opts.get("tol", 1e-10), 1e-12), xtol=max(opts.get("tol", 1e-10), 1e-12),
        max_nfev=int(opts.get("max_nfev", 200)),
    )
    x = sol.x
    r = sol.fun
    dof = max(1, M - len(x))
    chi2_red = float(r @ r / dof)

    # ---- 誤差（J 為稀疏矩陣）
    err_x = np.full(len(x), np.nan)
    try:
        J = sol.jac
        JTJ = (J.T @ J)
        JTJ = JTJ.toarray() if hasattr(JTJ, "toarray") else np.asarray(JTJ)
        cov = np.linalg.pinv(JTJ) * chi2_red
        err_x = np.sqrt(np.clip(np.diag(cov), 0, None))
    except Exception:
        pass

    P = np.empty((K, n))
    E = np.full((K, n), np.nan)
    r2 = np.empty(K)
    r2c = np.empty(K)
    for k in range(K):
        full, T, pr = unpack(x, k)
        P[k] = full
        for c, j in enumerate(sh):
            E[k, j] = err_x[c]
        b = n_glob + k * ns
        for c, j in enumerate(sl):
            E[k, j] = err_x[b + c]
        d = data[k]
        cm, mag = model_to_complex(func(d["f"], *full), d["n"])
        stt = fit_statistics(d["s"], cm, mag)
        r2[k], r2c[k] = stt["r2"], stt["r2_complex"]

    T = x[link_cols["T"]] if "T" in link_cols else (link["T"] if link else np.nan)
    pr = x[link_cols["phi"]] if "phi" in link_cols else (link["phi_ref"] if link else np.nan)
    T_err = err_x[link_cols["T"]] if "T" in link_cols else np.nan
    pr_err = err_x[link_cols["phi"]] if "phi" in link_cols else np.nan
    if li is not None:
        E[:, li["p"]] = np.abs(TWO_PI_NS * T * li["ffac"]) * E[:, li["f"]]

    shared = {names[j]: float(x[c]) for c, j in enumerate(sh)}
    shared_err = {names[j]: float(err_x[c]) for c, j in enumerate(sh)}
    return dict(names=names, roles=roles, shared=shared, shared_err=shared_err,
                T=float(T), T_err=float(T_err), phi_ref=float(pr), phi_ref_err=float(pr_err),
                f_ref=float(li["f_ref"]) if li else np.nan,
                params=P, errors=E, r2=r2, r2_complex=r2c,
                idx=[S.get("idx") for S in slices], chi2_red=chi2_red,
                nfev=int(sol.nfev), n_points=M, n_free=int(len(x)),
                elapsed=time.perf_counter() - t0,
                message=str(sol.message), success=bool(sol.success))
