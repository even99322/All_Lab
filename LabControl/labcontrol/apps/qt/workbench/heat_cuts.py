"""1.0.11：量測監控的影像加 X / Y 截面（像讀檔程式 / Labber 的 2D 圖）。

    ┌────────┬──────────────────────┐
    │ 讀值    │ X 截面（橫線那一列）   │
    ├────────┼──────────────────────┤
    │ Y 截面  │ 影像（十字線可拖）     │
    │（直線那 │                      │
    │  一行） │                      │
    └────────┴──────────────────────┘

影像的資料是 heat[ix, j]：ix = 曲線的 x（頻率…），j = 最內圈的掃描點。
橫線跟著「最新量到的那一點」走（拖過就停在你放的位置；重新開始量測時再跟著走）。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore

from .. import theme


def _nearest(values: np.ndarray, v: float) -> int:
    return int(np.argmin(np.abs(np.asarray(values, dtype=float) - v))) if len(values) else 0


class HeatView(pg.GraphicsLayoutWidget):
    """影像 + 上方 X 截面 + 左邊 Y 截面 + 可拖的十字線。``heat_plot`` 的相容介面：setTitle / setLabel / getPlotItem。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.ci.setSpacing(2)
        self.main = pg.PlotItem()
        self.img = pg.ImageItem()
        self.main.addItem(self.img)
        self.xcut = pg.PlotItem()
        self.ycut = pg.PlotItem()
        self.readout = pg.LabelItem(justify="left")
        for p in (self.main, self.xcut, self.ycut):
            p.showGrid(x=True, y=True, alpha=0.25)
        self.xcut.setXLink(self.main)
        self.ycut.setYLink(self.main)
        self.ycut.invertX(True)                       # 曲線往左長，跟讀檔程式一樣
        self.xcut.hideAxis("bottom")
        self.ycut.hideAxis("left")
        self.xcurve = self.xcut.plot([], [], pen=pg.mkPen(theme.qc("curve"), width=1.5))
        self.ycurve = self.ycut.plot([], [], pen=pg.mkPen("#d62728", width=1.5))
        self.vline = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen("#d62728", width=1.5, style=QtCore.Qt.PenStyle.DashLine))
        self.hline = pg.InfiniteLine(angle=0, movable=True, pen=pg.mkPen("#1f77b4", width=1.5, style=QtCore.Qt.PenStyle.DashLine))
        for line in (self.vline, self.hline):
            line.setZValue(20)
            self.main.addItem(line)
        self.vline.sigPositionChanged.connect(self.update_cuts)
        self.hline.sigPositionChanged.connect(self.update_cuts)
        self.hline.sigDragged.connect(self._stop_follow)
        self.heat: Optional[np.ndarray] = None
        self.x = np.zeros(0)
        self.y = np.zeros(0)
        self.follow = True                            # 橫線跟著最新那一點
        self.value_label = ""
        self.cuts_on = False
        self.title = ""
        self.set_cuts(True)
        theme.on_change(self, HeatView._apply_theme)

    def _apply_theme(self) -> None:
        self.setBackground(theme.qc("plot_bg"))
        fg = pg.mkPen(theme.qc("plot_fg"))
        for p in (self.main, self.xcut, self.ycut):
            for ax in ("left", "bottom", "right", "top"):
                a = p.getAxis(ax)
                a.setPen(fg)
                a.setTextPen(fg)
        self.xcurve.setPen(pg.mkPen(theme.qc("curve"), width=1.5))
        self.readout.setAttr("color", theme.qc("plot_fg").name())

    # ---- 相容 PlotWidget ------------------------------------------------------------------------
    def getPlotItem(self):  # noqa: N802
        return self.main

    def setTitle(self, title) -> None:  # noqa: N802
        """有截面時標題放在左上角的讀值區（影像不加標題，Y 截面才對得齊）。"""
        self.title = str(title or "")
        self.main.setTitle(None if self.cuts_on or not self.title else self.title)
        self.update_cuts()

    def setLabel(self, side, text=None, units=None) -> None:  # noqa: N802
        self.main.setLabel(side, text, units)

    def addItem(self, item) -> None:  # noqa: N802
        self.main.addItem(item)

    # ---- 版面 ----------------------------------------------------------------------------------
    def set_cuts(self, on: bool) -> None:
        on = bool(on)
        for it in (self.readout, self.xcut, self.ycut, self.main):
            if it.scene() is not None and it in self.ci.items:
                self.ci.removeItem(it)
        if on:
            self.ci.addItem(self.readout, row=0, col=0)
            self.ci.addItem(self.xcut, row=0, col=1)
            self.ci.addItem(self.ycut, row=1, col=0)
            self.ci.addItem(self.main, row=1, col=1)
            lay = self.ci.layout
            lay.setRowStretchFactor(0, 1)
            lay.setRowStretchFactor(1, 3)
            lay.setColumnStretchFactor(0, 1)
            lay.setColumnStretchFactor(1, 3)
        else:
            self.ci.addItem(self.main, row=0, col=0)
            lay = self.ci.layout
            for k in (0, 1):
                lay.setRowStretchFactor(k, 1)
                lay.setColumnStretchFactor(k, 1)
        for it in (self.readout, self.xcut, self.ycut):
            it.setVisible(on)
        self.vline.setVisible(on)
        self.hline.setVisible(on)
        self.cuts_on = on
        self.main.setTitle(None if on or not getattr(self, "title", "") else self.title)
        self.update_cuts()

    # ---- 資料 ----------------------------------------------------------------------------------
    def reset(self, x: np.ndarray, y: np.ndarray, shape) -> None:
        """新的量測 / 換通道：座標、空白影像、十字線放中間，橫線重新跟著最新那一點。"""
        self.x = np.asarray(x, dtype=float)
        self.y = np.asarray(y, dtype=float)
        self.heat = None
        self.img.setImage(np.zeros(shape))
        x0, x1 = (float(self.x[0]), float(self.x[-1])) if len(self.x) else (0.0, 1.0)
        y0, y1 = (float(self.y[0]), float(self.y[-1])) if len(self.y) else (0.0, 1.0)
        self.img.setRect(QtCore.QRectF(x0, y0, (x1 - x0) or 1e-9, (y1 - y0) or 1e-9))
        self.follow = True
        for line, v in ((self.vline, (x0 + x1) / 2), (self.hline, y0)):
            line.blockSignals(True)
            line.setValue(v)
            line.blockSignals(False)
        self.xcurve.setData([], [])
        self.ycurve.setData([], [])
        self.readout.setText("")

    def show_heat(self, heat: np.ndarray, latest: Optional[int] = None, auto_range: bool = False) -> None:
        """畫影像（NaN = 還沒量到）；``latest`` = 最新量到的那一欄（橫線跟過去）。"""
        self.heat = heat
        finite = heat[np.isfinite(heat)]
        self.img.setImage(np.nan_to_num(heat, nan=float(finite.min()) if finite.size else 0.0), autoLevels=True)
        if latest is not None and self.follow and 0 <= latest < len(self.y):
            self.hline.blockSignals(True)
            self.hline.setValue(float(self.y[latest]))
            self.hline.blockSignals(False)
        if auto_range:
            self.main.getViewBox().autoRange()
        self.update_cuts()

    def _stop_follow(self, *_a) -> None:
        self.follow = False

    def cut_indices(self) -> tuple[int, int]:
        return _nearest(self.x, float(self.vline.value())), _nearest(self.y, float(self.hline.value()))

    def update_cuts(self, *_a) -> None:
        heat = self.heat
        if not self.cuts_on or heat is None or heat.ndim != 2 or not len(self.x) or not len(self.y) \
                or heat.shape != (len(self.x), len(self.y)):
            return
        ix, j = self.cut_indices()
        row = heat[:, j]                              # X 截面：橫線那一點的整條曲線
        col = heat[ix, :]                             # Y 截面：直線那個 x 在各掃描點的值
        ok = np.isfinite(row)
        self.xcurve.setData(self.x[ok], row[ok])
        ok = np.isfinite(col)
        self.ycurve.setData(col[ok], self.y[ok])
        z = heat[ix, j]
        zs = f"{z:.5g}" if np.isfinite(z) else "—"
        head = f"<b>{self.title}</b><br>" if self.title else ""
        self.readout.setText(f"{head}x = {self.x[ix]:.6g}<br>y = {self.y[j]:.6g}<br>{self.value_label or 'z'} = {zs}",
                             size="8pt")

    def set_value_label(self, text: str) -> None:
        self.value_label = text or ""
        self.xcut.setLabel("left", self.value_label)
        self.ycut.setLabel("bottom", self.value_label)
        self.update_cuts()
