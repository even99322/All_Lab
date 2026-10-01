"""
去除極端雜訊（純函式，不依賴 Qt；單次擬合、連續擬合子行程、預覽共用）

1. 自動尖峰偵測（Hampel 濾波）：
   以滑動中位數為基準，偏離量 > k × 局部尺度 的點視為尖峰。
   局部尺度 = max(滑動 MAD, 整條曲線的點間雜訊)，避免平滑段 MAD≈0 時誤判。
   標記後再向兩側擴張幾點，一併去掉尖峰旁的小擾動。
2. 手動排除頻段：例如 "3.0564-3.0569, 3.0528~3.0532"（GHz）
"""
import re
import numpy as np

try:
    from scipy.ndimage import median_filter, binary_dilation
except ImportError:
    def median_filter(values, size, mode="nearest"):
        half = int(size) // 2
        padded = np.pad(np.asarray(values), (half, half), mode="edge")
        windows = np.lib.stride_tricks.sliding_window_view(padded, int(size))
        return np.median(windows, axis=-1)

    def binary_dilation(values, iterations=1):
        result = np.asarray(values, dtype=bool).copy()
        for _ in range(int(iterations)):
            expanded = result.copy()
            expanded[1:] |= result[:-1]
            expanded[:-1] |= result[1:]
            result = expanded
        return result

_NUM = r"\d+(?:\.\d*)?(?:[eE][+-]?\d+)?"
_RANGE = re.compile(rf"({_NUM})\s*[-~:]\s*({_NUM})")


def parse_ranges(text):
    """'3.0564-3.0569, 3.0528~3.0532' → [(3.0564, 3.0569), (3.0528, 3.0532)]（GHz）"""
    out = []
    for a, b in _RANGE.findall(text or ""):
        a, b = float(a), float(b)
        if a > b:
            a, b = b, a
        if b > a:
            out.append((a, b))
    return out


def format_ranges(ranges):
    return ", ".join(f"{a:.6f}-{b:.6f}" for a, b in ranges)


def _noise_scale(x):
    """整條曲線的點間雜訊（以一階差分的 MAD 估計，對線性趨勢不敏感）"""
    d = np.diff(x)
    if d.size == 0:
        return 0.0
    return 1.4826 * float(np.median(np.abs(d - np.median(d)))) / np.sqrt(2)


def spike_mask(s, half=5, k=5.0, dilate=1, components="abs"):
    """
    回傳 True = 尖峰點
    s          : 複數陣列（整條切片）
    half       : 滑動視窗半寬（點數），視窗 = 2*half+1；需大於尖峰寬度的 2 倍
    k          : 門檻（幾倍局部尺度）
    dilate     : 向兩側擴張點數
    components : "abs" 只看 |S|；"all" 同時檢查 |S|、Re、Im
    """
    s = np.asarray(s)
    n = s.size
    bad = np.zeros(n, bool)
    if n < 5:
        return bad
    size = int(min(2 * max(1, half) + 1, n if n % 2 else n - 1))
    comps = [np.abs(s)] if components == "abs" else [np.abs(s), s.real, s.imag]
    for x in comps:
        x = np.asarray(x, float)
        finite = np.isfinite(x)
        if not finite.all():
            x = np.where(finite, x, np.interp(np.arange(n), np.flatnonzero(finite), x[finite])
                         if finite.any() else 0.0)
        med = median_filter(x, size=size, mode="nearest")
        dev = np.abs(x - med)
        mad = 1.4826 * median_filter(dev, size=size, mode="nearest")
        scale = np.maximum(mad, _noise_scale(x))
        scale = np.where(scale > 0, scale, np.finfo(float).tiny)
        bad |= dev > k * scale
    if dilate > 0 and bad.any():
        bad = binary_dilation(bad, iterations=int(dilate))
    return bad


def clean_mask(freq, s, cfg):
    """
    回傳 True = 保留的點
    cfg: dict(spike: bool, half, k, dilate, components, ranges: [(a, b) GHz])；None 表示不處理
    """
    freq = np.asarray(freq, float)
    s = np.asarray(s)
    keep = np.isfinite(s) if np.iscomplexobj(s) else np.isfinite(s.astype(float))
    if not cfg:
        return keep
    if cfg.get("spike"):
        keep &= ~spike_mask(s, cfg.get("half", 5), cfg.get("k", 5.0),
                            cfg.get("dilate", 1), cfg.get("components", "abs"))
    for a, b in cfg.get("ranges") or []:
        keep &= ~((freq >= a * 1e9) & (freq <= b * 1e9))
    return keep


def is_active(cfg):
    return bool(cfg) and (bool(cfg.get("spike")) or bool(cfg.get("ranges")))
