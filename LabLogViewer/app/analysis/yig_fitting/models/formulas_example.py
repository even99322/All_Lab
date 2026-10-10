"""
公式檔範例 —— 給 main.py (fitapp) 載入用

規則：
1. 檔案內所有「第一個參數為頻率 w (Hz)」的函式都會出現在下拉選單。
   以底線開頭的函式、guess_* 函式會被忽略。
2. 函式回傳值可以是：
   - np.hstack([real, imag])  (長度 2N)  → 複數擬合（與 notebook 相同）
   - 複數陣列 (長度 N)                   → 自動拆成 real/imag 擬合
   - 實數陣列 (長度 N)                   → 擬合 |S|（線性振幅）
3. （選用）定義 guess_<函式名>(freq, s) 回傳 {參數名: (初值, 下界, 上界)}，
   GUI 會在「自動初值」時呼叫；沒定義就用內建的啟發式估計。
   freq: 遮罩後頻率 (Hz)；s: 遮罩後複數 S 參數。
4. （選用）定義單位：
   UNITS = {參數名: 單位}              → 所有函式共用
   UNITS_<函式名> = {參數名: 單位}      → 只套用在該函式（優先）
   沒定義的參數會依名稱推測（_GHz/_MHz 後綴、theta/phi→rad、t→ns …），
   也可以在 GUI 參數表的「單位」欄直接修改。
   頻率類單位（Hz/kHz/MHz/GHz）會被連續擬合用來移動視窗。
"""
import numpy as np

MODEL_ID = "lablogviewer.yig.s11.single"
MODEL_VERSION = "1.0"
PHYSICAL_COUPLING_RELATION = "kappa_b * sin(phi)**2"

# 共用單位（空字串 = 無因次）
UNITS = {
    "A": "",
    "phi_0": "rad",
    "t": "ns",
    "theta_fano": "rad",
}

UNITS_S11_single = {
    "w_m": "GHz",
    "alpha_r": "MHz",
    "kappa_b": "MHz",
    "phi": "rad",
}

UNITS_S11_RSMEP = {
    "w1_GHz": "GHz", "w2_GHz": "GHz",
    "g1_MHz": "MHz", "g2_MHz": "MHz",
    "k1_MHz": "MHz", "k2_MHz": "MHz",
    "theta1": "rad", "theta2": "rad",
}
UNITS_S11_RSMEP_poly = UNITS_S11_RSMEP


# ---------------------------------------------------------------------------
# 共用：背景估計
# ---------------------------------------------------------------------------
def _env_guess(freq, s):
    amp = np.mean([np.abs(s[0]), np.abs(s[-1])])
    ph = np.unwrap(np.angle(s))
    slope = (ph[-1] - ph[0]) / (freq[-1] - freq[0])
    delay_ns = -slope / (2 * np.pi) * 1e9
    phi0 = ph[0] - slope * freq[0]
    phi0 = (phi0 + np.pi) % (2 * np.pi) - np.pi
    return amp, phi0, delay_ns


# ---------------------------------------------------------------------------
# 單顆 YIG
# ---------------------------------------------------------------------------
def S11_single(w, w_m, alpha_r, kappa_b, phi, A, phi_0, t, theta_fano):
    w_m = w_m * 1e9
    alpha_r = alpha_r * 1e6
    kappa_b = kappa_b * 1e6

    kappa_m = kappa_b * (np.sin(phi) ** 2)
    Gamma = (kappa_m + alpha_r) / 2
    D_m = -(alpha_r / 4) * np.sin(2 * phi)

    s_value_ideal = 1 - ((kappa_m * np.exp(1j * theta_fano)) / ((Gamma / 2) - 1j * (w - w_m - D_m)))

    delay_s = t * 1e-9
    env_phase = phi_0 - 2 * np.pi * (w - w_m) * delay_s

    s_value = A * np.exp(1j * env_phase) * s_value_ideal
    s_value = s_value.conjugate()
    return np.hstack([s_value.real, s_value.imag])


def guess_S11_single(freq, s):
    f0 = freq[np.argmin(np.abs(s))] / 1e9
    amp, phi0, delay = _env_guess(freq, s)
    return {
        "w_m": (f0, freq[0] / 1e9, freq[-1] / 1e9),
        "alpha_r": (10, 0, 100),
        "kappa_b": (10, 0, 100),
        "phi": (np.pi, 0, 2 * np.pi),
        "A": (amp, 0.5 * amp, 1.5 * amp),
        "phi_0": (phi0, -np.pi, np.pi),
        "t": (delay, -200, 200),
        "theta_fano": (0, -np.pi, np.pi),
    }


# ---------------------------------------------------------------------------
# coherent RSMEP（兩顆 YIG，多項式形式）
# ---------------------------------------------------------------------------
def S11_RSMEP_poly(w, w1_GHz, w2_GHz, g1_MHz, g2_MHz, k1_MHz, k2_MHz, A, phi_0, t, theta_fano):
    w1 = w1_GHz * 1e9
    w2 = w2_GHz * 1e9
    g1 = g1_MHz * 1e6
    g2 = g2_MHz * 1e6
    k1 = k1_MHz * 1e6
    k2 = k2_MHz * 1e6
    delay_s = t * 1e-9

    C_RZ = (w1 * w2 - g1 * (g2 - 2 * k2) - 4 * k1 * k2) - 1j * (w1 * (g2 - 2 * k2) + w2 * g1)
    C_res = (w1 * w2 - g1 * (g2 + 2 * k2) - 4 * k1 * k2) - 1j * (w1 * (g2 + 2 * k2) + w2 * g1)

    num = w ** 2 - (w1 + w2 - 1j * (g1 + g2 - 2 * k2)) * w + C_RZ
    den = w ** 2 - (w1 + w2 - 1j * (g1 + g2 + 2 * k2)) * w + C_res

    s_value_ideal = num / den
    s_value_ideal = 1 - (1 - s_value_ideal) * np.exp(1j * theta_fano)
    s_value_ideal = s_value_ideal.conjugate()

    w_center = (w1 + w2) / 2
    env_phase = phi_0 - 2 * np.pi * (w - w_center) * delay_s
    s_value = A * np.exp(1j * env_phase) * s_value_ideal
    return np.hstack([s_value.real, s_value.imag])


# ---------------------------------------------------------------------------
# coherent RSMEP（兩顆 YIG，含 theta1/theta2，數值穩定版）
# ---------------------------------------------------------------------------
def S11_RSMEP(w, w1_GHz, w2_GHz, g1_MHz, g2_MHz, k1_MHz, k2_MHz, theta1, theta2, A, phi_0, t, theta_fano):
    w1 = w1_GHz * 1e9
    w2 = w2_GHz * 1e9
    g1 = g1_MHz * 1e6
    g2 = g2_MHz * 1e6
    k1 = k1_MHz * 1e6
    k2 = k2_MHz * 1e6
    delay_s = t * 1e-9

    dw1 = w - w1
    dw2 = w - w2
    Gamma = np.sqrt(k1 * k2)

    term1_num = dw1 + 1j * g1 - 1j * k1 * (1.0 - np.exp(-2j * (theta1 + theta2)))
    term2_num = dw2 + 1j * g2 - 1j * k2 * (1.0 - np.exp(-2j * theta2))
    cross_num = 1j * Gamma * np.exp(-1j * theta1) * (1.0 - np.exp(-2j * theta2))
    num = (term1_num * term2_num) - (cross_num ** 2)

    term1_den = dw1 + 1j * g1 + 1j * k1 * (1.0 - np.exp(2j * (theta1 + theta2)))
    term2_den = dw2 + 1j * g2 + 1j * k2 * (1.0 - np.exp(2j * theta2))
    cross_den = -1j * Gamma * np.exp(1j * theta1) * (1.0 - np.exp(2j * theta2))
    den = (term1_den * term2_den) - (cross_den ** 2)

    s_value_ideal = num / den
    s_value_ideal = 1 - (1 - s_value_ideal) * np.exp(1j * theta_fano)
    s_value_ideal = s_value_ideal.conjugate()

    w_center = (w1 + w2) / 2
    env_phase = phi_0 - 2 * np.pi * (w - w_center) * delay_s
    s_value = A * np.exp(1j * env_phase) * s_value_ideal
    return np.hstack([s_value.real, s_value.imag])


def guess_S11_RSMEP(freq, s):
    fs, fe = freq[0] / 1e9, freq[-1] / 1e9
    f0 = freq[np.argmin(np.abs(s))] / 1e9
    amp, phi0, delay = _env_guess(freq, s)
    return {
        "w1_GHz": (f0, fs, fe),
        "w2_GHz": (f0, fs, fe),
        "g1_MHz": (1.0, 0.01, 20.0),
        "g2_MHz": (1.0, 0.01, 20.0),
        "k1_MHz": (5.0, 0.001, 500.0),
        "k2_MHz": (5.0, 0.001, 500.0),
        "theta1": (np.pi, 0, 2 * np.pi),
        "theta2": (np.pi / 2, 0, 2 * np.pi),
        "A": (amp, 0.5, 1.5),
        "phi_0": (phi0, -np.pi, np.pi),
        "t": (np.clip(delay, -199, 199), -200.0, 200.0),
        "theta_fano": (0.0, -np.pi, np.pi),
    }
