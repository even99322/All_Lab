"""
耦合模式 S21（含 Fano 相位與環境背景）

理想響應：
    S_ideal = 1 + e^{iθ_F} · κ / [ i(ω_p − ω_w) − (κ + α) + g² / ( i(ω_p − ω_d) − ξ ) ]

量測模型（環境背景）：
    S21 = A · exp{ i[ φ_0 − 2π (ω_p − ω_c) τ ] } · S_ideal ,   ω_c = (ω_w + ω_d)/2

本檔包含：
    S21_coupled / S21_coupled_abs          耦合雙模
    S21_single_mode / S21_single_mode_abs  單模（g = 0）

單位：ω_p 為量測頻率 (Hz)；ω_w, ω_d 為 GHz；κ, α, g, ξ 為 MHz（與頻率同為「一般頻率」，非角頻率）；
      τ 為 ns；φ_0, θ_F 為 rad；A 無因次。
"""
import numpy as np

UNITS_S21_coupled = {
    "w_w": "GHz", "w_d": "GHz",
    "kappa": "MHz", "alpha": "MHz", "g": "MHz", "xi": "MHz",
    "A": "", "phi_0": "rad", "t": "ns", "theta_fano": "rad",
}
UNITS_S21_coupled_abs = {k: v for k, v in UNITS_S21_coupled.items()
                         if k not in ("phi_0", "t")}


def _ideal(w, w_w, w_d, kappa, alpha, g, xi, theta_fano):
    ww, wd = w_w * 1e9, w_d * 1e9
    k, a, gg, x = kappa * 1e6, alpha * 1e6, g * 1e6, xi * 1e6
    inner = gg ** 2 / (1j * (w - wd) - x)
    return 1 + np.exp(1j * theta_fano) * k / (1j * (w - ww) - (k + a) + inner)


def S21_coupled(w, w_w, w_d, kappa, alpha, g, xi, A, phi_0, t, theta_fano):
    """複數擬合（建議）：回傳 [Re, Im]"""
    s = _ideal(w, w_w, w_d, kappa, alpha, g, xi, theta_fano)
    w_c = (w_w + w_d) / 2 * 1e9
    env = A * np.exp(1j * (phi_0 - 2 * np.pi * (w - w_c) * t * 1e-9))
    s = env * s
    # 若 IQ 圖的繞行方向與數據相反，改用 s = s.conjugate()
    return np.hstack([s.real, s.imag])


def S21_coupled_abs(w, w_w, w_d, kappa, alpha, g, xi, A, theta_fano):
    """只擬合 |S21|（原始公式的形式）：φ_0 與 τ 在絕對值中消失，不需要"""
    return A * np.abs(_ideal(w, w_w, w_d, kappa, alpha, g, xi, theta_fano))


def _guess(freq, s):
    fs, fe = freq[0] / 1e9, freq[-1] / 1e9
    f0 = freq[np.argmin(np.abs(s))] / 1e9
    amp = float(np.mean([np.abs(s[0]), np.abs(s[-1])]))
    ph = np.unwrap(np.angle(s))
    slope = (ph[-1] - ph[0]) / (freq[-1] - freq[0])
    w_c = (freq[0] + freq[-1]) / 2
    phi0 = float((ph[0] - slope * (freq[0] - w_c) + np.pi) % (2 * np.pi) - np.pi)
    delay = float(np.clip(-slope / (2 * np.pi) * 1e9, -199, 199))
    return dict(
        w_w=(f0, fs, fe), w_d=(f0, fs, fe),
        kappa=(1.0, 1e-4, 100.0), alpha=(0.5, 0.0, 100.0),
        g=(2.0, 0.0, 200.0), xi=(0.5, 1e-4, 100.0),
        A=(amp, 0.5 * amp, 1.5 * amp), phi_0=(phi0, -np.pi, np.pi),
        t=(delay, -200.0, 200.0), theta_fano=(0.0, -np.pi, np.pi),
    )


def guess_S21_coupled(freq, s):
    return _guess(freq, s)


def guess_S21_coupled_abs(freq, s):
    g = _guess(freq, s)
    g.pop("phi_0")
    g.pop("t")
    return g


# =============================================================================
# 單模：S21 = 1 + κ / [ i(ω − ω_w) − (κ + α) ]   （上式 g = 0 的情形）
#   加 Fano 相位與環境背景：
#   S21 = A · exp{ i[φ_0 − 2π(ω − ω_w)τ] } · { 1 + e^{iθ_F} κ / [ i(ω − ω_w) − (κ + α) ] }
# =============================================================================
UNITS_S21_single_mode = {
    "w_w": "GHz", "kappa": "MHz", "alpha": "MHz",
    "A": "", "phi_0": "rad", "t": "ns", "theta_fano": "rad",
}
UNITS_S21_single_mode_abs = {k: v for k, v in UNITS_S21_single_mode.items()
                             if k not in ("phi_0", "t")}


def _ideal_single(w, w_w, kappa, alpha, theta_fano):
    ww = w_w * 1e9
    k, a = kappa * 1e6, alpha * 1e6
    return 1 + np.exp(1j * theta_fano) * k / (1j * (w - ww) - (k + a))


def S21_single_mode(w, w_w, kappa, alpha, A, phi_0, t, theta_fano):
    """複數擬合（建議）：回傳 [Re, Im]"""
    s = _ideal_single(w, w_w, kappa, alpha, theta_fano)
    env = A * np.exp(1j * (phi_0 - 2 * np.pi * (w - w_w * 1e9) * t * 1e-9))
    s = env * s
    # 若 IQ 圖的繞行方向與數據相反，改用 s = s.conjugate()
    return np.hstack([s.real, s.imag])


def S21_single_mode_abs(w, w_w, kappa, alpha, A, theta_fano):
    """只擬合 |S21|：φ_0、τ 在絕對值中消失"""
    return A * np.abs(_ideal_single(w, w_w, kappa, alpha, theta_fano))


def _guess_single(freq, s):
    g = _guess(freq, s)
    for key in ("w_d", "g", "xi"):
        g.pop(key)
    # 由谷底深度估計 κ/(κ+α)：|S|min/A ≈ |1 − κ/(κ+α)|
    depth = float(np.clip(np.abs(s).min() / max(g["A"][0], 1e-12), 0, 0.99))
    # 由 -3dB 半高寬估計總線寬 κ+α（MHz）
    mag = np.abs(s)
    half = (mag.min() + g["A"][0]) / 2
    below = freq[mag < half]
    width = (below[-1] - below[0]) / 2 / 1e6 if below.size > 1 else 1.0
    width = max(width, 1e-3)
    kappa = width * (1 - depth)
    g["kappa"] = (max(kappa, 1e-3), 1e-4, 100 * width)
    g["alpha"] = (max(width - kappa, 1e-3), 0.0, 100 * width)
    return g


def guess_S21_single_mode(freq, s):
    return _guess_single(freq, s)


def guess_S21_single_mode_abs(freq, s):
    g = _guess_single(freq, s)
    g.pop("phi_0")
    g.pop("t")
    return g


def _g_of_detuning(delta, g0, kappa, A_d, xi, rho, alpha):
    """
    （底線開頭 → 不會出現在 GUI 函式選單）
    第二式：g(Δ) = sqrt( sqrt(g0² κ A_d² − (ξ − ρA_d)² Δ²) − (ξ − ρA_d)(κ + α) )
    用於連續擬合後，把各切片擬合出的 g 對 Δ 再擬合一次（根號內為負時回傳 NaN）
    """
    b = xi - rho * A_d
    inner = g0 ** 2 * kappa * A_d ** 2 - b ** 2 * np.asarray(delta, float) ** 2
    with np.errstate(invalid="ignore"):
        return np.sqrt(np.sqrt(inner) - b * (kappa + alpha))
