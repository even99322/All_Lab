"""v3.9：YIG 2D 熱圖檢視（偏置 × 頻率）。讀 yigmap.MapData 存的 .npz；逐點模擬時每算完一點自動更新。

    左上：熱圖（點一下選偏置與頻率；白色虛線 = Kittel）
    右上：選到的偏置的 trace（|S| 對頻率）
    下方：選到的頻率的截面（對偏置）
"""
from __future__ import annotations

import os
from typing import Optional

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                             QVBoxLayout, QWidget)

import yigmap

VIEWS = (("dB", "|S| (dB)"), ("lin", "|S|"), ("phase", "Phase (deg)"), ("uphase", "Unwrapped phase (deg)"),
         ("re", "Re"), ("im", "Im"))
CMAPS = ("viridis", "magma", "RdBu_r", "coolwarm", "gray")


def transform(z: np.ndarray, view: str) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        if view == "dB":
            return 20 * np.log10(np.abs(z))
        if view == "lin":
            return np.abs(z)
        if view == "phase":
            return np.degrees(np.angle(z))
        if view == "uphase":
            out = np.degrees(np.unwrap(np.angle(np.where(np.isfinite(z), z, 0)), axis=1))
            return np.where(np.isfinite(z), out, np.nan)
        if view == "re":
            return z.real
        if view == "im":
            return z.imag
    return np.abs(z)


class MapViewer(QWidget):
    def __init__(self, path: Optional[str] = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("YIG 2D 熱圖")
        self.resize(1150, 780)
        self.data: Optional[yigmap.MapData] = None
        self.path = ""
        self.mtime = 0.0
        self.sel = None                   # (偏置索引, 頻率索引)
        v = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.combo_s = QComboBox()
        self.combo_view = QComboBox()
        for key, text in VIEWS:
            self.combo_view.addItem(text, key)
        self.combo_bg = QComboBox()
        self.combo_bg.addItem("不減背景", "")
        self.combo_bg.addItem("減去每個頻率的中位數（去掉不隨偏置變的背景）", "col")
        self.combo_bg.addItem("減去第一個偏置", "first")
        self.combo_cmap = QComboBox()
        self.combo_cmap.addItems(CMAPS)
        self.chk_kittel = QCheckBox("Kittel 線")
        self.chk_kittel.setChecked(True)
        btn_open = QPushButton("開啟…")
        btn_open.clicked.connect(self.open_dialog)
        for w, label in ((self.combo_s, "參數"), (self.combo_view, "視圖"), (self.combo_bg, "背景"),
                         (self.combo_cmap, "色階")):
            bar.addWidget(QLabel(label))
            bar.addWidget(w)
            w.currentIndexChanged.connect(lambda _i: self.redraw())
        self.chk_kittel.toggled.connect(lambda _v: self.redraw())
        bar.addWidget(self.chk_kittel)
        bar.addStretch(1)
        bar.addWidget(btn_open)
        v.addLayout(bar)
        self.fig = Figure(figsize=(10, 7))
        self.canvas = FigureCanvas(self.fig)
        v.addWidget(NavigationToolbar(self.canvas, self))
        v.addWidget(self.canvas, 1)
        self.lbl = QLabel("")
        v.addWidget(self.lbl)
        gs = self.fig.add_gridspec(2, 2, width_ratios=[1.6, 1], height_ratios=[1.6, 1], hspace=0.32, wspace=0.25)
        self.ax_map = self.fig.add_subplot(gs[0, 0])
        self.ax_tr = self.fig.add_subplot(gs[0, 1])
        self.ax_cut = self.fig.add_subplot(gs[1, :])
        self.cbar = None
        self.canvas.mpl_connect("button_press_event", self._click)
        self.timer = QTimer(self)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(self._check_update)
        self.timer.start()
        if path:
            self.load(path)

    # ---- 檔案 ----
    def open_dialog(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "開啟 2D 熱圖", os.path.dirname(self.path) if self.path else "",
                                           "YIG 熱圖 (*_yigmap.npz *.npz)")
        if f:
            self.load(f)

    def load(self, path: str, keep_view: bool = False) -> bool:
        try:
            d = yigmap.MapData.load(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "YIG 2D 熱圖", f"讀不了 {path}：{e}")
            return False
        self.data, self.path = d, path
        self.mtime = os.path.getmtime(path)
        cur = self.combo_s.currentText()
        self.combo_s.blockSignals(True)
        self.combo_s.clear()
        self.combo_s.addItems(sorted(d.s))
        i = self.combo_s.findText(cur if keep_view and cur else (d.default_param() or ""))
        self.combo_s.setCurrentIndex(max(0, i))
        self.combo_s.blockSignals(False)
        if not keep_view or self.sel is None:
            self.sel = (len(d.bias) // 2, int(np.nanargmin(np.abs(d.freq_ghz - np.nanmedian(d.f_kittel)))))
        self.setWindowTitle(f"YIG 2D 熱圖 — {os.path.basename(path)}（{d.done_rows()}/{len(d.bias)} 個偏置）")
        self.redraw()
        return True

    def _check_update(self) -> None:
        if self.path and os.path.exists(self.path):
            try:
                m = os.path.getmtime(self.path)
            except OSError:
                return
            if m > self.mtime + 1e-6:
                self.load(self.path, keep_view=True)

    # ---- 畫 ----
    def x_axis(self):
        d = self.data
        return (d.b_ext_mT, "External field μ0H (mT)") if d.bias_mode == "external_mT" else (d.h_int, "HFSS internal bias (A/m)")

    def values(self) -> Optional[np.ndarray]:
        d = self.data
        name = self.combo_s.currentText()
        if d is None or name not in d.s:
            return None
        z = transform(d.s[name], self.combo_view.currentData())
        bg = self.combo_bg.currentData()
        if bg == "col":
            with np.errstate(all="ignore"):
                z = z - np.nanmedian(z, axis=0, keepdims=True)
        elif bg == "first":
            z = z - z[:1]
        return z

    def redraw(self) -> None:
        d = self.data
        z = self.values()
        for ax in (self.ax_map, self.ax_tr, self.ax_cut):
            ax.clear()
        if self.cbar is not None:
            try:
                self.cbar.remove()
            except Exception:  # noqa: BLE001
                pass
            self.cbar = None
        if d is None or z is None:
            self.canvas.draw_idle()
            return
        x, xlabel = self.x_axis()
        unit = self.combo_view.currentText()
        finite = z[np.isfinite(z)]
        vmin, vmax = (np.percentile(finite, 1), np.percentile(finite, 99)) if finite.size else (0, 1)
        im = self.ax_map.pcolormesh(x, d.freq_ghz, z.T, shading="nearest", cmap=self.combo_cmap.currentText(),
                                    vmin=vmin, vmax=vmax)
        self.cbar = self.fig.colorbar(im, ax=self.ax_map, label=unit)
        if self.chk_kittel.isChecked():
            self.ax_map.plot(x, d.f_kittel, "w--", lw=1)
        self.ax_map.set_ylim(d.freq_ghz.min(), d.freq_ghz.max())
        self.ax_map.set_xlabel(xlabel)
        self.ax_map.set_ylabel("Frequency (GHz)")
        self.ax_map.set_title(f"{self.combo_s.currentText()}   {unit}", fontsize=10)
        i, j = self.sel or (0, 0)
        i, j = min(i, len(x) - 1), min(j, len(d.freq_ghz) - 1)
        self.ax_map.axvline(x[i], color="#ff5252", lw=0.8)
        self.ax_map.axhline(d.freq_ghz[j], color="#ff5252", lw=0.8)
        self.ax_tr.plot(d.freq_ghz, z[i], lw=1)
        self.ax_tr.axvline(d.f_kittel[i], color="gray", ls="--", lw=0.8)
        self.ax_tr.axvline(d.freq_ghz[j], color="#ff5252", lw=0.8)
        self.ax_tr.set_xlabel("Frequency (GHz)")
        self.ax_tr.set_title(f"Bias {x[i]:.4g}  (Kittel {d.f_kittel[i]:.4f} GHz)", fontsize=9)
        self.ax_cut.plot(x, z[:, j], ".-", lw=1, ms=3)
        self.ax_cut.axvline(x[i], color="#ff5252", lw=0.8)
        self.ax_cut.set_xlabel(xlabel)
        self.ax_cut.set_title(f"Cut at {d.freq_ghz[j]:.5f} GHz", fontsize=9)
        for ax in (self.ax_tr, self.ax_cut):
            ax.set_ylabel(unit)
            ax.grid(True, alpha=0.3)
        val = z[i, j]
        self.lbl.setText(f"偏置 {x[i]:.6g}　頻率 {d.freq_ghz[j]:.6f} GHz　值 {val:.4g} {unit}"
                         f"　（點熱圖換位置；{d.done_rows()}/{len(d.bias)} 個偏置已算完）")
        self.canvas.draw_idle()

    def _click(self, ev) -> None:
        if self.data is None or ev.inaxes is not self.ax_map or ev.xdata is None:
            return
        x, _ = self.x_axis()
        i = int(np.argmin(np.abs(x - ev.xdata)))
        j = int(np.argmin(np.abs(self.data.freq_ghz - ev.ydata)))
        self.sel = (i, j)
        self.redraw()
