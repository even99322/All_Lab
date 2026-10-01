"""QC 判定（由 sweep_main.py 的 evaluate_trace 與 IQ 象限上色邏輯移出，成為與 UI 無關的純函式）。

Qt 的 QC 挑選視窗、未來的自動挑選 hook、離線分析腳本都呼叫同一份程式碼。
"""
from __future__ import annotations

from typing import Dict, NamedTuple

import numpy as np

try:
    from scipy.signal import find_peaks
except ImportError:  # pragma: no cover
    find_peaks = None


class TraceScore(NamedTuple):
    mag_pass: bool
    prom_pass: bool
    slope_pass: bool
    min_mag: float
    max_prominence: float
    max_slope: float

    @property
    def all_pass(self) -> bool:
        return self.mag_pass and self.prom_pass and self.slope_pass

    @property
    def n_pass(self) -> int:
        return int(self.mag_pass) + int(self.prom_pass) + int(self.slope_pass)


def evaluate_trace(trace: np.ndarray, mag_thresh: float = -70, phase_thresh: float = 50,
                   slope_thresh: float = 50) -> TraceScore:
    """Magnitude 深度 + Phase 突起度 + 變化率（與舊版演算法相同）。"""
    trace = np.asarray(trace)
    mag_db = 20 * np.log10(np.abs(trace))
    min_mag = float(np.min(mag_db))
    mag_pass = min_mag <= mag_thresh

    phase_deg = np.degrees(np.unwrap(np.angle(trace)))
    phase_deg = phase_deg - np.min(phase_deg)
    max_prom = 0.0
    max_slope = 0.0
    prom_pass = slope_pass = False

    if find_peaks is not None:
        peaks, pp = find_peaks(phase_deg, prominence=phase_thresh)
        dips, pd = find_peaks(-phase_deg, prominence=phase_thresh)
        extrema = list(peaks) + list(dips)
        proms = list(pp.get("prominences", [])) + list(pd.get("prominences", []))
        if extrema:
            b = int(np.argmax(proms))
            idx = extrema[b]
            max_prom = float(proms[b])
            prom_pass = max_prom >= phase_thresh
            if 0 < idx < len(phase_deg) - 1:
                left = abs(phase_deg[idx] - phase_deg[idx - 1])
                right = abs(phase_deg[idx] - phase_deg[idx + 1])
                max_slope = float(min(left, right))
                slope_pass = max_slope >= slope_thresh
    else:
        avg = np.mean(phase_deg)
        diff = np.diff(phase_deg)
        if len(diff):
            j = int(np.argmax(np.abs(diff)))
            max_prom = float(max(abs(phase_deg[j] - avg), abs(phase_deg[j + 1] - avg)))
            max_slope = float(abs(diff[j]))
            prom_pass = max_prom >= phase_thresh
            slope_pass = max_slope >= slope_thresh
    return TraceScore(mag_pass, prom_pass, slope_pass, min_mag, max_prom, max_slope)


def iq_quadrant_classes(trace: np.ndarray, radius: float = 1e-3) -> Dict[str, np.ndarray]:
    """IQ 象限分類（舊 QC 視窗的上色規則）。回傳每點類別：
        'major'     半徑內、屬於多數象限（藍）
        'deep_lone' 最深點所在象限與多數不同且只有它一點（紅）
        'deep_group'最深點所在象限與多數不同且有同伴（黃）
        'other'     半徑內其他象限（灰）
        'out'       半徑外（暗灰）
    """
    trace = np.asarray(trace)
    I, Q = trace.real, trace.imag
    in_range = I ** 2 + Q ** 2 <= radius ** 2
    quads = np.select([(I >= 0) & (Q >= 0), (I < 0) & (Q >= 0), (I < 0) & (Q < 0)], [1, 2, 3], default=4)
    counts = {q: int(np.sum((quads == q) & in_range)) for q in (1, 2, 3, 4)}
    major = max(counts, key=counts.get) if any(counts.values()) else 1
    deep_q = int(quads[int(np.argmin(I ** 2 + Q ** 2))])
    cls = np.full(trace.shape, "out", dtype=object)
    cls[in_range] = "other"
    cls[in_range & (quads == major)] = "major"
    if deep_q != major:
        cls[in_range & (quads == deep_q)] = "deep_lone" if counts[deep_q] == 1 else "deep_group"
    return {"class": cls, "quadrant": quads, "major": major, "deep": deep_q}
