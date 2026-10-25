"""v3.9：YIG 2D 熱圖（偏置磁場 × 頻率）——掃描點、AEDT 參數掃描的指令、存檔 / 讀檔。不用 Qt、不用 AEDT，可以單獨測試。

存成 <CSV 檔名>_yigmap.npz：
    bias      偏置（使用者輸入的單位：外加 mT，或 HFSS 內部場 A/m）
    bias_mode "external_mT" / "internal_Apm"
    b_ext_mT  外加磁場 (mT)、h_int (A/m)、f_kittel (GHz)：每個偏置一個
    freq_ghz  頻率
    S_<名稱>  複數 [偏置, 頻率]（例如 S_S21）；還沒算到的列是 NaN
    meta      JSON（參數、時間、方式）
另外存 _yigmap.csv（長格式）、_yigmap.png（S21 dB 熱圖＋Kittel 線）。
"""
from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

import numpy as np

import yig


def bias_points(start: float, stop: float, n: int) -> np.ndarray:
    n = max(1, int(n))
    return np.linspace(float(start), float(stop), n) if n > 1 else np.array([float(start)])


def fields_for(values, mode: str, ms_t: float, g: float) -> Dict[str, np.ndarray]:
    rows = [yig.bias_fields(float(v), mode, ms_t, g) for v in values]
    return {"h_int": np.array([r["H_int"] for r in rows]), "b_ext_mT": np.array([r["B_ext_mT"] for r in rows]),
            "f_kittel": np.array([r["f_kittel"] for r in rows])}


def auto_freq_range(f_kittel, margin: float = 0.05, span_min_ghz: float = 0.05):
    """涵蓋所有 Kittel 頻率，再往外各留 margin（相對）。"""
    lo, hi = float(np.min(f_kittel)), float(np.max(f_kittel))
    pad = max((hi + lo) / 2 * margin, span_min_ghz / 2)
    return max(0.001, lo - pad), hi + pad


def parametric_args(name: str, setup: str, var: str, h0: float, h1: float, n: int, copy_mesh: bool = True) -> list:
    """Optimetrics 參數掃描：偏置（設計變數）線性 n 點；同一個幾何 → 複製網格，只有第一點跑 Adaptive。"""
    data = f"LINC {h0:.10g}A_per_meter {h1:.10g}A_per_meter {int(n)}" if n > 1 else f"{h0:.10g}A_per_meter"
    return [f"NAME:{name}", "IsEnabled:=", True,
            ["NAME:ProdOptiSetupDataV2", "SaveFields:=", False, "CopyMesh:=", bool(copy_mesh),
             "SolveWithCopiedMeshOnly:=", bool(copy_mesh)],
            ["NAME:StartingPoint"], "Sim. Setups:=", [setup],
            ["NAME:Sweeps", ["NAME:SweepDefinition", "Variable:=", var, "Data:=", data, "OffsetF1:=", False,
                             "Synchronize:=", 0]],
            ["NAME:Sweep Operations"], ["NAME:Goals"]]


class MapData:
    def __init__(self, bias, bias_mode: str, fields: Dict[str, np.ndarray], freq_ghz, meta: Optional[dict] = None):
        self.bias = np.asarray(bias, dtype=float)
        self.bias_mode = bias_mode
        self.h_int = np.asarray(fields["h_int"], dtype=float)
        self.b_ext_mT = np.asarray(fields["b_ext_mT"], dtype=float)
        self.f_kittel = np.asarray(fields["f_kittel"], dtype=float)
        self.freq_ghz = np.asarray(freq_ghz, dtype=float)
        self.s: Dict[str, np.ndarray] = {}
        self.meta = dict(meta or {})

    def set_row(self, name: str, i: int, values) -> None:
        if name not in self.s:
            self.s[name] = np.full((len(self.bias), len(self.freq_ghz)), np.nan + 1j * np.nan, dtype=complex)
        v = np.asarray(values, dtype=complex)
        if v.shape[0] != len(self.freq_ghz):
            raise ValueError(f"{name} 第 {i} 列的點數 {v.shape[0]} ≠ {len(self.freq_ghz)}")
        self.s[name][i] = v

    def nearest(self, h_int: float) -> int:
        return int(np.argmin(np.abs(self.h_int - float(h_int))))

    def done_rows(self) -> int:
        if not self.s:
            return 0
        a = next(iter(self.s.values()))
        return int(np.sum(np.any(np.isfinite(a.real), axis=1)))

    def bias_label(self) -> str:
        return "外加磁場 μ0H (mT)" if self.bias_mode == "external_mT" else "HFSS 內部場 (A/m)"

    # ---- 存檔 ----
    def save(self, base: str) -> str:
        path = base + ".npz"
        arrays = {"bias": self.bias, "b_ext_mT": self.b_ext_mT, "h_int": self.h_int, "f_kittel": self.f_kittel,
                  "freq_ghz": self.freq_ghz,
                  "meta": np.frombuffer(json.dumps(dict(self.meta, bias_mode=self.bias_mode,
                                                        saved=time.time()), ensure_ascii=False).encode(), np.uint8)}
        for k, v in self.s.items():
            arrays["S_" + k] = v
        tmp = base + ".tmp.npz"
        np.savez_compressed(tmp, **arrays)
        import os
        os.replace(tmp, path)
        return path

    def save_csv(self, base: str) -> str:
        import pandas as pd
        rows = {"bias": np.repeat(self.bias, len(self.freq_ghz)), "B_ext_mT": np.repeat(self.b_ext_mT, len(self.freq_ghz)),
                "Frequency_GHz": np.tile(self.freq_ghz, len(self.bias))}
        for k, v in self.s.items():
            flat = v.reshape(-1)
            rows[f"{k}_real"], rows[f"{k}_imag"] = flat.real, flat.imag
            with np.errstate(divide="ignore", invalid="ignore"):
                rows[f"{k}_dB"] = 20 * np.log10(np.abs(flat))
        path = base + ".csv"
        pd.DataFrame(rows).to_csv(path, index=False)
        return path

    def save_png(self, base: str, name: Optional[str] = None) -> Optional[str]:
        name = name or self.default_param()
        if name is None:
            return None
        import matplotlib
        matplotlib.use("Agg", force=False)
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        fig = Figure(figsize=(7, 4.6), dpi=130)
        FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)
        with np.errstate(divide="ignore", invalid="ignore"):
            z = 20 * np.log10(np.abs(self.s[name]))
        x = self.b_ext_mT if self.bias_mode == "external_mT" else self.h_int
        im = ax.pcolormesh(x, self.freq_ghz, z.T, shading="nearest", cmap="viridis")
        ax.plot(x, self.f_kittel, "w--", lw=1, label="Kittel")
        ax.set_ylim(self.freq_ghz.min(), self.freq_ghz.max())
        ax.set_xlabel("External field μ0H (mT)" if self.bias_mode == "external_mT" else "HFSS internal bias (A/m)")
        ax.set_ylabel("Frequency (GHz)")
        ax.set_title(f"|{name}| (dB)")
        fig.colorbar(im, ax=ax, label="dB")
        ax.legend(loc="upper left", fontsize=8)
        fig.tight_layout()
        path = base + ".png"
        fig.savefig(path)
        return path

    def default_param(self) -> Optional[str]:
        for k in ("S21", "S12", "S11"):
            if k in self.s:
                return k
        return next(iter(self.s), None)

    @classmethod
    def load(cls, path: str) -> "MapData":
        z = np.load(path, allow_pickle=False)
        meta = json.loads(bytes(z["meta"]).decode()) if "meta" in z else {}
        m = cls(z["bias"], meta.get("bias_mode", "external_mT"),
                {"h_int": z["h_int"], "b_ext_mT": z["b_ext_mT"], "f_kittel": z["f_kittel"]}, z["freq_ghz"], meta)
        for k in z.files:
            if k.startswith("S_"):
                m.s[k[2:]] = z[k]
        return m

