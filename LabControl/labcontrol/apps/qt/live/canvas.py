"""即時監控的曲線畫布：一張圖可以畫多種資料、左右兩個 Y 軸。

  * 曲線：拖曳曲線本身 = 上下移動那條曲線（只改顯示，數值讀數不變；「重設偏移」復原）。
  * 數軸：在軸上拖曳 = 縮放那個軸（左 / 右 Y 軸各自縮放，下方頻率軸縮放頻率）；滾輪照常縮放。
  * 空白處拖曳 = 平移；雙擊 = 在那個頻率加一個 marker（可以很多個）；marker 可以拖，右鍵刪除。
  * 每個 marker 在下方列出各曲線在那個頻率的值。
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt, pyqtSignal

from .. import theme

#: 曲線顏色（theme 顏色名稱）依序使用
SERIES_COLORS = ["curve", "orange", "ok", "err", "accent"]
MARKER_COLORS = ["#f59e0b", "#22c55e", "#ef4444", "#a855f7", "#06b6d4", "#eab308", "#f97316", "#14b8a6"]


class ScaleAxis(pg.AxisItem):
    """在軸上用左鍵拖曳 = 縮放這個軸（往上 / 往右拖 = 放大）。"""

    def mouseDragEvent(self, ev) -> None:
        vb = self.linkedView()
        if vb is None or ev.button() != Qt.MouseButton.LeftButton:
            return super().mouseDragEvent(ev)
        ev.accept()
        d = ev.pos() - ev.lastPos()
        center = vb.mapSceneToView(ev.buttonDownScenePos())
        if self.orientation in ("left", "right"):
            vb.scaleBy(y=math.exp(d.y() * 0.01), center=center)
        else:
            vb.scaleBy(x=math.exp(-d.x() * 0.01), center=center)


class MarkerLine(pg.InfiniteLine):
    removed = pyqtSignal(object)

    def mouseClickEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.RightButton:
            ev.accept()
            self.removed.emit(self)
            return
        super().mouseClickEvent(ev)


class DragViewBox(pg.ViewBox):
    """左鍵拖曳在曲線上 = 上下移動曲線；空白處 = 平移；雙擊 = 加 marker。"""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.canvas: Optional["TraceCanvas"] = None
        self._drag: Optional[str] = None

    def mouseDragEvent(self, ev, axis=None) -> None:
        c = self.canvas
        if c is not None and ev.button() == Qt.MouseButton.LeftButton and axis is None:
            if ev.isStart():
                self._drag = c.curve_at(ev.buttonDownScenePos())
            if self._drag is not None:
                ev.accept()
                vb = c.series_vb(self._drag)
                y1 = vb.mapSceneToView(ev.scenePos()).y()
                y0 = vb.mapSceneToView(ev.lastScenePos()).y()
                c.shift(self._drag, y1 - y0)
                if ev.isFinish():
                    self._drag = None
                return
        super().mouseDragEvent(ev, axis)

    def mouseClickEvent(self, ev) -> None:
        if self.canvas is not None and ev.button() == Qt.MouseButton.LeftButton and ev.double():
            ev.accept()
            p = self.mapSceneToView(ev.scenePos())
            self.canvas.add_marker_at(p.x(), p.y())
            return
        super().mouseClickEvent(ev)


class TraceCanvas(QtWidgets.QWidget):
    """series：[(key, side)]，side = "L" / "R"。view_fn(key, x, z) → (x, y)；label_fn(key) → 軸標題。"""

    markers_changed = pyqtSignal()

    def __init__(self, view_fn: Callable, label_fn: Callable[[str], str], unit_fn: Callable[[str], str],
                 short_fn: Optional[Callable[[str], str]] = None, parent=None) -> None:
        super().__init__(parent)
        self.view_fn, self.label_fn, self.unit_fn = view_fn, label_fn, unit_fn
        self.short_fn = short_fn or label_fn
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        self.vb = DragViewBox()
        self.vb.canvas = self
        axes = {k: ScaleAxis(k) for k in ("left", "right", "bottom")}
        self.pw = pg.PlotWidget(viewBox=self.vb, axisItems=axes)
        self.pw.showGrid(x=True, y=True, alpha=0.25)
        self.plot = self.pw.getPlotItem()
        self.legend = self.plot.addLegend(offset=(8, 8))
        v.addWidget(self.pw, 1)
        self.readout = QtWidgets.QLabel("")
        self.readout.setTextFormat(Qt.TextFormat.RichText)
        self.readout.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.readout.setVisible(False)
        v.addWidget(self.readout)
        self.vb2: Optional[pg.ViewBox] = None
        self.series: List[Tuple[str, str]] = []
        self.curves: Dict[str, pg.PlotDataItem] = {}
        self.offsets: Dict[str, float] = {}
        self.points = False
        self.last: Optional[tuple] = None
        self.data: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
        self.markers: List[MarkerLine] = []
        self.iq_dots: List[Any] = []
        theme.on_change(self, lambda w: w._style())

    # ---- 軸 / 曲線 ------------------------------------------------------------------
    def _ensure_vb2(self) -> pg.ViewBox:
        if self.vb2 is None:
            vb2 = pg.ViewBox()
            vb2.setAcceptedMouseButtons(Qt.MouseButton.NoButton)      # 滑鼠事件交給主畫布（拖曲線 / 平移）
            self.plot.scene().addItem(vb2)
            from PyQt6 import sip

            sip.transferto(vb2, None)        # 由 scene 刪除；Python 回收時不要再刪一次（會當機）
            self.plot.getAxis("right").linkToView(vb2)
            vb2.setXLink(self.vb)
            self.vb.sigResized.connect(self._sync_vb2)
            self.vb2 = vb2
            self._sync_vb2()
        return self.vb2

    def _sync_vb2(self) -> None:
        if self.vb2 is not None:
            self.vb2.setGeometry(self.vb.sceneBoundingRect())
            self.vb2.linkedViewChanged(self.vb, self.vb2.XAxis)

    def series_vb(self, key: str) -> pg.ViewBox:
        side = dict(self.series).get(key, "L")
        return self.vb2 if side == "R" and self.vb2 is not None else self.vb

    def is_iq(self) -> bool:
        return any(k == "iq" for k, _s in self.series)

    def set_series(self, series: List[Tuple[str, str]]) -> None:
        series = list(series)
        if series == self.series:
            return
        for k, c in list(self.curves.items()):
            vb = self.series_vb(k)
            vb.removeItem(c)
            try:
                self.legend.removeItem(c)
            except Exception:  # noqa: BLE001
                pass
        self.curves.clear()
        self.series = series
        keep = {k for k, _s in series}
        self.data = {k: v for k, v in self.data.items() if k in keep}
        right = [k for k, s in series if s == "R"]
        if right:
            self._ensure_vb2()
        self.plot.showAxis("right", bool(right))
        for i, (k, side) in enumerate(series):
            c = pg.PlotDataItem([], [])
            c.color_name = SERIES_COLORS[i % len(SERIES_COLORS)]
            (self.vb2 if side == "R" else self.vb).addItem(c)
            self.legend.addItem(c, self._legend_name(k))
            self.curves[k] = c
            self.offsets.setdefault(k, 0.0)
        left = [k for k, s in series if s == "L"]
        self.plot.setLabel("left", " / ".join(self.label_fn(k) for k in left))      # 標題已含單位
        if right:
            self.plot.setLabel("right", " / ".join(self.label_fn(k) for k in right))
        self.legend.setVisible(len(series) > 1)
        self._style()
        if self.last is not None:
            self.update_data(*self.last)
        self._update_markers()

    def _legend_name(self, k: str) -> str:
        off = self.offsets.get(k, 0.0)
        return self.label_fn(k) + (f"（偏移 {off:+.4g}）" if off else "")

    def _style(self) -> None:
        self.pw.setBackground(theme.c("plot_bg"))
        for ax in ("left", "bottom", "right"):
            a = self.plot.getAxis(ax)
            a.setPen(pg.mkPen(theme.c("plot_fg")))
            a.setTextPen(pg.mkPen(theme.c("plot_fg")))
        for c in self.curves.values():
            col = theme.c(c.color_name)
            c.setPen(pg.mkPen(col, width=1.5))
            if self.points:
                c.setSymbol("o")
                c.setSymbolSize(3)
                c.setSymbolBrush(pg.mkBrush(theme.c("orange") if len(self.curves) == 1 else col))
                c.setSymbolPen(None)
            else:
                c.setSymbol(None)
        self.legend.setLabelTextColor(theme.c("text") if "text" in theme.colors() else "#e6e8eb")

    def set_points(self, on: bool) -> None:
        self.points = bool(on)
        self._style()

    def update_data(self, tr: str, x, z, unit: str) -> None:
        self.last = (tr, x, z, unit)
        x = np.asarray(x, float)
        self.freq = x
        for k, c in self.curves.items():
            xx, yy = self.view_fn(k, x, z)
            xx, yy = np.asarray(xx, float), np.asarray(yy, float)
            self.data[k] = (xx, yy)
            c.setData(xx, yy + self.offsets.get(k, 0.0))
        if self.is_iq():
            self.plot.setLabel("bottom", "Real")
            self.vb.setAspectLocked(True)
        else:
            self.vb.setAspectLocked(False)
            self.plot.setLabel("bottom", "頻率", units=unit or "Hz")
        self._update_markers()

    def clear(self) -> None:
        self.last = None
        self.data.clear()
        for c in self.curves.values():
            c.setData([], [])
        self._update_markers()

    # ---- 拖曲線 ----------------------------------------------------------------------
    def curve_at(self, scene_pos, tol: float = 8.0) -> Optional[str]:
        best, best_d = None, tol
        for k in self.curves:
            if k not in self.data:
                continue
            xx, yy = self.data[k]
            if not len(xx):
                continue
            vb = self.series_vb(k)
            rect = vb.sceneBoundingRect()
            (x0, x1), (y0, y1) = vb.viewRange()
            if x1 == x0 or y1 == y0:
                continue
            sx = rect.left() + (xx - x0) / (x1 - x0) * rect.width()
            sy = rect.top() + (y1 - (yy + self.offsets.get(k, 0.0))) / (y1 - y0) * rect.height()
            d = np.hypot(sx - scene_pos.x(), sy - scene_pos.y())
            i = int(np.nanargmin(d)) if np.isfinite(d).any() else -1
            if i >= 0 and d[i] < best_d:
                best, best_d = k, float(d[i])
        return best

    def shift(self, key: str, dy: float) -> None:
        self.offsets[key] = self.offsets.get(key, 0.0) + dy
        if key in self.data:
            xx, yy = self.data[key]
            self.curves[key].setData(xx, yy + self.offsets[key])
        self._rename(key)

    def _rename(self, key: str) -> None:
        for sample, label in self.legend.items:
            if getattr(sample, "item", None) is self.curves.get(key):
                label.setText(self._legend_name(key))

    def reset_offsets(self) -> None:
        for k in list(self.offsets):
            self.offsets[k] = 0.0
            self._rename(k)
        if self.last is not None:
            self.update_data(*self.last)

    def auto_range(self) -> None:
        self.vb.enableAutoRange()
        if self.vb2 is not None:
            self.vb2.enableAutoRange(axis=pg.ViewBox.YAxis)

    # ---- markers ------------------------------------------------------------------------
    def add_marker_at(self, x: float, y: float = 0.0) -> None:
        if self.is_iq():                                 # IQ 圖：最近的量測點 → 它的頻率
            if "iq" not in self.data or not len(getattr(self, "freq", [])):
                return
            xx, yy = self.data["iq"]
            i = int(np.nanargmin(np.hypot(xx - x, yy - y)))
            x = float(self.freq[i])
        self.add_marker(x)

    def add_marker(self, f: float) -> MarkerLine:
        n = len(self.markers)
        col = MARKER_COLORS[n % len(MARKER_COLORS)]
        m = MarkerLine(pos=f, angle=90, movable=True, pen=pg.mkPen(col, width=1.2, style=Qt.PenStyle.DashLine),
                       label=f"M{n + 1}", labelOpts={"position": 0.96, "color": col, "movable": True})
        m.color = col
        m.removed.connect(self.remove_marker)
        m.sigPositionChanged.connect(lambda _m: self._update_markers())
        self.markers.append(m)
        if not self.is_iq():
            self.vb.addItem(m)
        self._update_markers()
        self.markers_changed.emit()
        return m

    def remove_marker(self, m: MarkerLine) -> None:
        if m in self.markers:
            self.markers.remove(m)
            if m.scene() is not None:
                self.vb.removeItem(m)
            for i, mk in enumerate(self.markers):
                mk.label.setFormat(f"M{i + 1}")
            self._update_markers()
            self.markers_changed.emit()

    def clear_markers(self) -> None:
        for m in list(self.markers):
            self.remove_marker(m)

    def marker_values(self) -> List[Dict[str, Any]]:
        """[{name, freq, values: {key: 值（不含偏移）}}]"""
        out = []
        f = getattr(self, "freq", None)
        for i, m in enumerate(self.markers):
            fx = float(m.value())
            vals = {}
            for k, (xx, yy) in self.data.items():
                if k == "iq":
                    if f is not None and len(f):
                        j = int(np.argmin(np.abs(f - fx)))
                        vals[k] = complex(xx[j], yy[j])
                elif len(xx):
                    order = np.argsort(xx)
                    vals[k] = float(np.interp(fx, xx[order], yy[order]))
            out.append({"name": f"M{i + 1}", "freq": fx, "values": vals, "color": m.color})
        return out

    def _update_markers(self) -> None:
        for d in self.iq_dots:
            self.vb.removeItem(d)
        self.iq_dots = []
        rows = []
        for mv in self.marker_values():
            parts = []
            for k, v in mv["values"].items():
                if isinstance(v, complex):
                    parts.append(f"IQ {v.real:.4g}{v.imag:+.4g}j")
                    dot = pg.ScatterPlotItem([v.real], [v.imag], size=9, brush=pg.mkBrush(mv["color"]), pen=None)
                    txt = pg.TextItem(mv["name"], color=mv["color"], anchor=(0, 1))
                    txt.setPos(v.real, v.imag)
                    for it in (dot, txt):
                        self.vb.addItem(it)
                        self.iq_dots.append(it)
                else:
                    u = self.unit_fn(k)
                    parts.append(f"{self.short_fn(k)} <b>{v:.5g}</b>{(' ' + u) if u else ''}")
            rows.append(f"<span style='color:{mv['color']}'><b>{mv['name']}</b></span> "
                        f"{mv['freq'] / 1e9:.6f} GHz　" + "　".join(parts))
        self.readout.setText("<br>".join(rows))
        self.readout.setVisible(bool(rows))
