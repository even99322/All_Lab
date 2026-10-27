"""v3.9：傳輸線的特性阻抗（DXF 編輯器「MS ⇄ CPW 自動轉換」用）。

    微帶線（MS）：Hammerstad & Jensen 的合成公式（給 Z0 → W/h），分析用 Wheeler / Hammerstad（給 W → Z0）。
    接地共面波導（CPW，底下還有地 = GCPW，這個工具的 CPW 就是這種）：保角映射
        k = W/(W+2G)、k3 = tanh(πW/4h)/tanh(π(W+2G)/4h)
        εeff = (1 + εr·q) / (1 + q)，q = K(k')/K(k) · K(k3)/K(k3')
        Z0 = 60π / √εeff / (K(k)/K(k') + K(k3)/K(k3'))
    （Wadell, Transmission Line Design Handbook 3.4.3；忽略銅厚。）誤差約幾 %，最後以 HFSS 為準。
"""
from __future__ import annotations

import math
import re
from typing import Optional

# 常用板材的 εr（設計值；HFSS 材料庫的名稱）
MATERIAL_ER = {
    "rogers ro4003": 3.55, "rogers ro4350": 3.66, "rogers ro4350b": 3.66, "rogers rt/duroid 5880": 2.2,
    "rogers rt/duroid 5870": 2.33, "rogers rt/duroid 6002": 2.94, "rogers rt/duroid 6010": 10.2,
    "rogers ro3003": 3.0, "rogers ro3006": 6.15, "rogers ro3010": 10.2, "fr4_epoxy": 4.4, "fr4": 4.4,
    "arlon ad255": 2.55, "taconic tlc": 3.2, "alumina_92pct": 9.2, "al2_o3_ceramic": 9.8, "silicon": 11.9,
    "sapphire": 9.4, "quartz": 3.78, "teflon (tm)": 2.1, "polyimide": 3.5,
}


def material_er(name: str) -> Optional[float]:
    n = re.sub(r"\s*\(tm\)\s*", " ", str(name or "").lower()).strip()
    if n in MATERIAL_ER:
        return MATERIAL_ER[n]
    for k, v in MATERIAL_ER.items():
        kk = re.sub(r"\s*\(tm\)\s*", " ", k).strip()
        if kk and (n.startswith(kk) or kk in n):
            return v
    return None


def ellip_k(k: float) -> float:
    """第一類完全橢圓積分 K(k)（AGM）。"""
    k = min(max(k, 0.0), 1 - 1e-15)
    a, b = 1.0, math.sqrt(1 - k * k)
    for _ in range(60):
        a, b = (a + b) / 2, math.sqrt(a * b)
        if abs(a - b) < 1e-15:
            break
    return math.pi / (2 * a)


def _kk(k: float) -> float:
    """K(k)/K(k')。"""
    kp = math.sqrt(max(1 - k * k, 0.0))
    return ellip_k(k) / ellip_k(kp)


# ---- 微帶線 ----
def ms_eeff(w: float, h: float, er: float) -> float:
    """微帶線的有效介電常數（準靜態）。"""
    u = w / h
    return (er + 1) / 2 + (er - 1) / 2 / math.sqrt(1 + 12 / u)


C0_MM_GHZ = 299.792458        # 光速：mm·GHz


def guided_mm(f_ghz: float, eeff: float) -> float:
    """導波波長 λg = c / (f √εeff)，mm。"""
    return C0_MM_GHZ / (f_ghz * math.sqrt(eeff))


def ms_z0(w: float, h: float, er: float) -> float:
    u = w / h
    eeff = ms_eeff(w, h, er)
    if u <= 1:
        return 60 / math.sqrt(eeff) * math.log(8 / u + u / 4)
    return 120 * math.pi / math.sqrt(eeff) / (u + 1.393 + 0.667 * math.log(u + 1.444))


def ms_width(z0: float, h: float, er: float) -> float:
    """給目標阻抗 → 線寬（mm，跟 h 同單位）。先用合成公式，再用分析公式二分法修正。"""
    a = z0 / 60 * math.sqrt((er + 1) / 2) + (er - 1) / (er + 1) * (0.23 + 0.11 / er)
    u = 8 * math.exp(a) / (math.exp(2 * a) - 2)
    if u > 2:
        b = 377 * math.pi / (2 * z0 * math.sqrt(er))
        u = 2 / math.pi * (b - 1 - math.log(2 * b - 1) + (er - 1) / (2 * er) * (math.log(b - 1) + 0.39 - 0.61 / er))
    lo, hi = u / 3, u * 3
    for _ in range(80):
        mid = math.sqrt(lo * hi)
        if ms_z0(mid * h, h, er) > z0:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi) * h


# ---- 接地共面波導 ----
def gcpw_eeff_z0(w: float, g: float, h: float, er: float):
    k = w / (w + 2 * g)
    k3 = math.tanh(math.pi * w / (4 * h)) / math.tanh(math.pi * (w + 2 * g) / (4 * h))
    a, c = _kk(k), _kk(k3)          # K(k)/K(k')、K(k3)/K(k3')
    q = c / a
    eeff = (1 + er * q) / (1 + q)
    z0 = 60 * math.pi / math.sqrt(eeff) / (a + c)
    return eeff, z0


def gcpw_z0(w: float, g: float, h: float, er: float) -> float:
    return gcpw_eeff_z0(w, g, h, er)[1]


def gcpw_gap(z0: float, w: float, h: float, er: float) -> Optional[float]:
    """固定線寬，找間隙；做不到（太寬 / 太窄）→ None。"""
    lo, hi = 1e-4 * h, 50 * h
    if not (gcpw_z0(w, lo, h, er) <= z0 <= gcpw_z0(w, hi, h, er)):
        return None
    for _ in range(100):
        mid = math.sqrt(lo * hi)
        if gcpw_z0(w, mid, h, er) < z0:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


def gcpw_width(z0: float, g: float, h: float, er: float) -> Optional[float]:
    """固定間隙，找線寬。"""
    lo, hi = 1e-4 * h, 50 * h
    if not (gcpw_z0(hi, g, h, er) <= z0 <= gcpw_z0(lo, g, h, er)):
        return None
    for _ in range(100):
        mid = math.sqrt(lo * hi)
        if gcpw_z0(mid, g, h, er) > z0:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)
