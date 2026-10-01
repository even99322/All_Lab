"""
YIG 鏡像 / 節點模型（配合「相位 / 節點」分頁使用）

理想反射：
    r = 1 − κ_m e^{iθ_F} / ( Γ_m/2 − i (ω − ω_m − Δ_m) )
    κ_m = κ_b sin²φ                 （φ = nπ → κ_m = 0：節點，訊號消失）
    Γ_m = (κ_m + α_r) / 2
    Δ_m = −(γ_0 / 4) sin 2φ          （γ_0 與 α_r 獨立）
環境：S = A e^{i[φ_0 − 2π(ω − ω_m)τ]} · r ，最後取共軛（與 S11_single 相同慣例）

φ 由 YIG 位置決定：φ = k_m x = 2π f_m (x / v_g) + 常數
    → 在「相位 / 節點」分頁把 phi 設為「相位直線」，
      φ = φ_ref + 2π T (w_m − f_ref)，T = x / v_g（ns）
    κ_b、α_r、γ_0、τ、θ_F 可設為全片共用；w_m、A、φ_0 每片獨立。
"""
import numpy as np

MODEL_ID = "lablogviewer.yig.s11.node"
MODEL_VERSION = "1.0"
PHYSICAL_COUPLING_RELATION = "kappa_b * sin(phi)**2"

UNITS = {
    "w_m": "GHz",
    "alpha_r": "MHz",
    "kappa_b": "MHz",
    "gamma_0": "MHz",
    "phi": "rad",
    "A": "",
    "phi_0": "rad",
    "t": "ns",
    "theta_fano": "rad",
}


def _node_ideal(w, w_m, alpha_r, kappa_b, phi, gamma_0, theta_fano):
    w_m = w_m * 1e9
    alpha_r = alpha_r * 1e6
    kappa_b = kappa_b * 1e6
    gamma_0 = gamma_0 * 1e6
    kappa_m = kappa_b * np.sin(phi) ** 2
    Gamma = (kappa_m + alpha_r) / 2
    D_m = -(gamma_0 / 4) * np.sin(2 * phi)
    return 1 - kappa_m * np.exp(1j * theta_fano) / (Gamma / 2 - 1j * (w - w_m - D_m))


def S11_node(w, w_m, alpha_r, kappa_b, phi, gamma_0, A, phi_0, t, theta_fano):
    r = _node_ideal(w, w_m, alpha_r, kappa_b, phi, gamma_0, theta_fano)
    env = A * np.exp(1j * (phi_0 - 2 * np.pi * (w - w_m * 1e9) * t * 1e-9))
    s = np.conj(env * r)
    return np.hstack([s.real, s.imag])


def S11_node_abs(w, w_m, alpha_r, kappa_b, phi, gamma_0, A):
    """只擬合 |S|（沒有相位資訊時用）"""
    return A * np.abs(_node_ideal(w, w_m, alpha_r, kappa_b, phi, gamma_0, 0.0))


def _env_guess(freq, s):
    amp = float(np.mean([np.abs(s[0]), np.abs(s[-1])]))
    ph = np.unwrap(np.angle(s))
    slope = (ph[-1] - ph[0]) / (freq[-1] - freq[0])
    delay_ns = -slope / (2 * np.pi) * 1e9
    phi0 = ph[0] - slope * freq[0]
    phi0 = (phi0 + np.pi) % (2 * np.pi) - np.pi
    return amp, phi0, delay_ns


def guess_S11_node(freq, s):
    f0 = freq[np.argmin(np.abs(s))] / 1e9
    amp, phi0, delay = _env_guess(freq, s)
    # 共軛後相位斜率反號
    return {
        "w_m": (f0, freq[0] / 1e9, freq[-1] / 1e9),
        "alpha_r": (2.0, 0.0, 100.0),
        "kappa_b": (10.0, 0.0, 200.0),
        "phi": (np.pi / 2, -np.pi, 2 * np.pi),
        "gamma_0": (0.0, -100.0, 100.0),
        "A": (amp, 0.5 * amp, 1.5 * amp),
        "phi_0": (-phi0, -np.pi, np.pi),
        "t": (float(np.clip(-delay, -199, 199)), -200.0, 200.0),
        "theta_fano": (0.0, -np.pi, np.pi),
    }


def guess_S11_node_abs(freq, s):
    g = guess_S11_node(freq, s)
    return {k: g[k] for k in ("w_m", "alpha_r", "kappa_b", "phi", "gamma_0", "A")}
