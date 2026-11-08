"""1.0.31：電磁鐵的磁場校正（電流 → 磁場），用 YIG 鐵磁共振（FMR）反推。

YIG 小球（球形，消磁因子相同）的 Kittel 共振：

    f = γ' · (B + B_a)          γ' = γ/2π ≈ 28.0 GHz/T（g ≈ 2），B_a = 異向性等效場（通常幾 mT 以內，可以填 0）

量一張「電磁鐵電流 × 頻率」的 S 參數圖（VNA），每個電流找出 YIG 共振頻率 f_r(I)，就得到 B(I) = f_r / γ' − B_a，
再對 I 做多項式擬合（預設一次：B = c0 + c1·I），去掉離群點（例如共振跟腔體模態交叉、找錯的點）。

校正存在 instruments.yaml 那組電磁鐵的 ``field_cal``（跟著儀器清單同步到另一台量測電腦）：

    field_cal:
      method: yig
      coef: [c0, c1, ...]        # B[T] = Σ c_k · I[A]^k
      range: [I_min, I_max]      # 校正時的電流範圍（A）；超出範圍會提醒、外插
      gamma: 2.8e10              # γ'（Hz/T）
      offset: 0.0                # B_a（T）
      rms: ...                   # 擬合殘差（T）
      n: ...                     # 用了幾個點
      source: 檔名、trace、時間、誰做的

有了校正，電磁鐵組多一個參數 ``field``（磁場，SI = T，顯示 mT；量測流程可以直接掃磁場），
設定時換算成電流（反解多項式）再照原本的電流設定（含斜坡保護）走。
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .errors import ConfigError

GAMMA_DEFAULT = 28.0e9          # Hz/T（γ/2π，g = 2.0；電子 g = 2.0023 → 28.025 GHz/T）


# ---------------------------------------------------------------------------
# 校正（換算）
# ---------------------------------------------------------------------------
class FieldCalibration:
    def __init__(self, d: Dict[str, Any]) -> None:
        coef = [float(c) for c in (d.get("coef") or [])]
        if len(coef) < 2 or not any(coef[1:]):
            raise ConfigError("磁場校正 coef 至少要有一次項（B = c0 + c1·I）")
        self.d = dict(d)
        self.coef = coef
        rng = d.get("range") or [-math.inf, math.inf]
        self.range = (float(min(rng)), float(max(rng)))

    def field(self, current: float) -> float:
        """電流（A）→ 磁場（T）。"""
        return float(np.polyval(self.coef[::-1], float(current)))

    def slope(self) -> float:
        return self.coef[1]

    def current(self, field: float) -> float:
        """磁場（T）→ 電流（A）：反解多項式，取最接近一次近似、且在（稍微放寬的）校正範圍內的實根。"""
        b = float(field)
        lin = (b - self.coef[0]) / self.coef[1]
        if len(self.coef) == 2 or not any(self.coef[2:]):
            return lin
        p = list(self.coef[::-1])
        p[-1] -= b
        roots = [r.real for r in np.roots(p) if abs(r.imag) < 1e-9 * max(1.0, abs(r.real))]
        lo, hi = self.range
        span = (hi - lo) if math.isfinite(hi - lo) else max(abs(lin), 1.0)
        ok = [r for r in roots if lo - 0.5 * span <= r <= hi + 0.5 * span] or roots
        if not ok:
            raise ConfigError(f"磁場 {b * 1e3:g} mT 在校正曲線上找不到對應的電流")
        return float(min(ok, key=lambda r: abs(r - lin)))

    def in_range(self, current: float) -> bool:
        lo, hi = self.range
        return lo - 1e-12 <= current <= hi + 1e-12

    def field_limits(self, current_limits: Optional[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
        """電流上下限 → 磁場上下限（單調時）。"""
        if current_limits is None:
            return None
        a, b = self.field(current_limits[0]), self.field(current_limits[1])
        return (min(a, b), max(a, b))

    def describe(self) -> str:
        c1 = self.coef[1]
        s = f"B = {self.coef[0] * 1e3:+.4g} mT {c1 * 1e3:+.6g} mT/A·I"
        for k, c in enumerate(self.coef[2:], start=2):
            s += f" {c * 1e3:+.4g} mT/A^{k}·I^{k}"
        return s + f"（{c1 * 1e4:.5g} Oe/A）"


def load(options: Dict[str, Any]) -> Optional[FieldCalibration]:
    d = options.get("field_cal")
    if not isinstance(d, dict) or not d.get("coef"):
        return None
    try:
        return FieldCalibration(d)
    except (ConfigError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 電磁鐵組的「磁場」參數
# ---------------------------------------------------------------------------
def attach(inst: Any) -> bool:
    """有 field_cal 的電源（電磁鐵組）加上 ``field`` 參數（T；顯示單位 settings fieldcal.unit，預設 mT）；
    沒有校正就拿掉。回傳是否有磁場參數。"""
    from .capabilities import Source
    from .driverkit import ParamSpec

    cal = load(getattr(inst, "options", {}) or {})
    if not isinstance(inst, Source) or cal is None:
        inst.parameters.pop("field", None)
        inst.field_cal = None
        return False
    inst.field_cal = cal
    try:
        from ..settings import setting

        unit = str(setting("fieldcal.unit", "mT") or "mT")
    except Exception:  # noqa: BLE001
        unit = "mT"
    lim = None
    try:
        if inst.limits.hi != float("inf"):
            lim = cal.field_limits((inst.limits.lo, inst.limits.hi))
    except Exception:  # noqa: BLE001
        lim = None

    def get() -> float:
        return cal.field(float(inst.get_level()))

    def set_(value: Any) -> None:
        from .units import parse_quantity

        b = parse_quantity(value, unit) if isinstance(value, str) else float(value)
        cur = cal.current(b)
        if not cal.in_range(cur) and inst.station is not None:
            inst.station.bus.log(f"⚠️ {inst.name} 磁場 {b * 1e3:g} mT（{cur * 1e3:g} mA）超出校正範圍，是外插的值", "warning")
        inst.set_level(cur)

    p = inst.add_parameter("field", get=get, set=set_, unit="T", doc="磁場（由 YIG 共振校正換算）")
    p.spec = ParamSpec("field", label="磁場", unit="T", display_unit=unit, limits=lim, sweepable=True,
                       doc=cal.describe())
    return True


# ---------------------------------------------------------------------------
# 找共振
# ---------------------------------------------------------------------------
def to_db(z: np.ndarray) -> np.ndarray:
    z = np.asarray(z)
    if np.iscomplexobj(z):
        return 20 * np.log10(np.maximum(np.abs(z), 1e-15))
    z = z.astype(float)
    if np.nanmin(z) >= 0 and np.nanmax(z) <= 10:            # 線性振幅
        return 20 * np.log10(np.maximum(z, 1e-15))
    return z                                               # 已經是 dB


def find_resonances(freq: np.ndarray, data: np.ndarray, mode: str = "auto", fmin: Optional[float] = None,
                    fmax: Optional[float] = None, smooth: int = 3) -> Dict[str, Any]:
    """每一列（一個電流）找共振頻率。data 形狀 (n_current, n_freq)。

    mode：dip（穿透 S21 的吸收谷）、peak（峰）、auto（看整張圖偏離每列中位數哪一邊比較大）。
    回傳 {f: 每列共振頻率（Hz，找不到 = nan）, depth: 偏離中位數幾 dB, mode}。
    """
    f = np.asarray(freq, dtype=float)
    y = to_db(data)
    if y.ndim != 2 or y.shape[1] != f.size:
        raise ConfigError(f"資料形狀 {y.shape} 跟頻率點數 {f.size} 對不上")
    sel = np.ones(f.size, bool)
    if fmin is not None:
        sel &= f >= fmin
    if fmax is not None:
        sel &= f <= fmax
    if sel.sum() < 3:
        raise ConfigError("頻率範圍內的點太少")
    fs, ys = f[sel], y[:, sel]
    if smooth and smooth > 1:
        n = int(smooth)
        k = np.ones(n) / n
        pad = (n // 2, n - 1 - n // 2)                     # 邊緣用最近的值補（不要被 0 拉偏）
        ys = np.array([np.convolve(np.pad(r, pad, mode="edge"), k, mode="valid") for r in ys])
    base = np.nanmedian(ys, axis=1, keepdims=True)
    dev = ys - base
    if mode == "auto":
        mode = "dip" if np.nanmean(-np.nanmin(dev, axis=1)) >= np.nanmean(np.nanmax(dev, axis=1)) else "peak"
    sgn = -1.0 if mode == "dip" else 1.0
    out_f = np.full(ys.shape[0], np.nan)
    depth = np.zeros(ys.shape[0])
    for i, row in enumerate(sgn * dev):
        if not np.isfinite(row).any():
            continue
        j = int(np.nanargmax(row))
        depth[i] = float(row[j])
        x = fs[j]
        if 0 < j < len(fs) - 1 and np.isfinite(row[j - 1]) and np.isfinite(row[j + 1]):   # 拋物線內插
            a, b, c = row[j - 1], row[j], row[j + 1]
            den = a - 2 * b + c
            if den < 0:
                off = 0.5 * (a - c) / den
                if abs(off) <= 1:
                    x = fs[j] + off * (fs[j + 1] - fs[j - 1]) / 2
        out_f[i] = x
    return {"f": out_f, "depth": depth, "mode": mode}


# ---------------------------------------------------------------------------
# 擬合
# ---------------------------------------------------------------------------
def fit(current: Sequence[float], f_res: Sequence[float], gamma: float = GAMMA_DEFAULT, offset: float = 0.0,
        order: int = 1, clip: float = 3.0, min_depth: float = 0.0, depth: Optional[Sequence[float]] = None,
        exclude: Optional[Sequence[bool]] = None) -> Dict[str, Any]:
    """B = f/γ' − B_a 對 I 做多項式擬合；反覆拿掉殘差超過 clip·σ（穩健 σ）的點。

    exclude：使用者手動排除的點（True = 不用）。回傳 {coef, used（bool 陣列）, field（每點 B）, rms, r2, n, range}。
    """
    i = np.asarray(current, dtype=float)
    fr = np.asarray(f_res, dtype=float)
    bfield = fr / float(gamma) - float(offset)
    ok = np.isfinite(i) & np.isfinite(bfield)
    if depth is not None and min_depth > 0:
        ok &= np.asarray(depth, dtype=float) >= float(min_depth)
    if exclude is not None:
        ok &= ~np.asarray(exclude, dtype=bool)
    order = max(1, min(int(order), 3))
    if ok.sum() < order + 2:
        raise ConfigError(f"可用的共振點太少（{int(ok.sum())} 個），至少要 {order + 2} 個")
    used = ok.copy()
    coef = np.zeros(order + 1)
    for _ in range(6):
        coef = np.polyfit(i[used], bfield[used], order)[::-1]
        res = bfield - np.polyval(coef[::-1], i)
        sig = 1.4826 * np.nanmedian(np.abs(res[used] - np.nanmedian(res[used])))
        if not clip or sig <= 0:
            break
        new = ok & (np.abs(res) <= clip * sig)
        if new.sum() < order + 2 or (new == used).all():
            break
        used = new
    res = bfield - np.polyval(coef[::-1], i)
    rms = float(np.sqrt(np.mean(res[used] ** 2)))
    ss = float(np.sum((bfield[used] - bfield[used].mean()) ** 2))
    r2 = 1.0 - float(np.sum(res[used] ** 2)) / ss if ss > 0 else 1.0
    return {"coef": [float(c) for c in coef], "used": used, "field": bfield, "residual": res, "rms": rms, "r2": r2,
            "n": int(used.sum()), "range": [float(i[used].min()), float(i[used].max())]}


def calibration_dict(result: Dict[str, Any], gamma: float, offset: float, source: Dict[str, Any]) -> Dict[str, Any]:
    return {"method": "yig", "coef": result["coef"], "range": result["range"], "gamma": float(gamma),
            "offset": float(offset), "rms": result["rms"], "r2": result["r2"], "n": result["n"],
            "time": time.strftime("%Y-%m-%d %H:%M:%S"), **{k: v for k, v in source.items() if v not in (None, "")}}


# ---------------------------------------------------------------------------
# 讀量測檔（Lab Control .nc）
# ---------------------------------------------------------------------------
def scan_nc(path: str | Path) -> Dict[str, Any]:
    """看 .nc 裡有哪些掃描軸、頻率軸、資料：{axes: [{name, units, target, si_scale, n}], freq, traces: [...]}"""
    import xarray as xr

    with xr.open_dataset(str(path), engine="h5netcdf") as ds:
        axes, freq = [], None
        for name, c in ds.coords.items():
            if name not in ds.dims:
                continue
            u = str(c.attrs.get("units", ""))
            info = {"name": str(name), "units": u, "target": str(c.attrs.get("target", "")),
                    "si_scale": float(c.attrs.get("si_scale", 1.0) or 1.0), "n": int(ds.sizes[name]),
                    "long_name": str(c.attrs.get("long_name", name))}
            if freq is None and (u.endswith("Hz") or "freq" in str(name).lower()):
                freq = info
            else:
                axes.append(info)
        traces = [str(n) for n, v in ds.data_vars.items() if freq and freq["name"] in v.dims and len(v.dims) >= 2]
    return {"axes": axes, "freq": freq, "traces": traces}


def load_nc(path: str | Path, trace: str, axis: str, freq_axis: Optional[str] = None) -> Dict[str, Any]:
    """→ {current（A）, freq（Hz）, data（n_current × n_freq）, target, units}。其他維度取第一個。"""
    import xarray as xr
    from .units import split_unit

    info = scan_nc(path)
    fa = freq_axis or (info["freq"] or {}).get("name")
    if not fa:
        raise ConfigError("檔案裡找不到頻率軸")
    with xr.open_dataset(str(path), engine="h5netcdf") as ds:
        if trace not in ds.data_vars:
            raise ConfigError(f"檔案裡沒有 {trace}")
        v = ds[trace]
        for d in v.dims:
            if d not in (axis, fa):
                v = v.isel({d: 0})
        if axis not in v.dims:
            raise ConfigError(f"{trace} 沒有 {axis} 這個軸")
        v = v.transpose(axis, fa)
        c = ds.coords[axis]
        scale = float(c.attrs.get("si_scale", 0) or 0)
        units = str(c.attrs.get("units", ""))
        cur = np.asarray(c.values, dtype=float)
        cur = cur / scale if scale else cur * split_unit(units)[1]
        fc = ds.coords[fa]
        fu = str(fc.attrs.get("units", "Hz")) or "Hz"
        fq = np.asarray(fc.values, dtype=float) * split_unit(fu)[1]
        return {"current": cur, "freq": fq, "data": np.asarray(v.values), "target": str(c.attrs.get("target", "")),
                "units": units}


def magnet_of_target(target: str) -> str:
    """'magnet_A.level' → 'magnet_A'。"""
    return str(target or "").split(".")[0]
