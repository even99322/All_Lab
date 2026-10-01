"""繪圖元件：2D 預覽只在換資料時重畫，切片/範圍變動只更新線條（避免卡頓）"""
import warnings
import numpy as np
import matplotlib
from matplotlib.figure import Figure
from matplotlib.widgets import SpanSelector
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QScrollArea
from matplotlib.ticker import MaxNLocator

matplotlib.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "PingFang TC",
                                          "Noto Sans CJK TC", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False
warnings.filterwarnings("ignore", message="Tight layout not applied")
warnings.filterwarnings("ignore", message="Glyph .* missing from font")


class _CanvasWidget(QWidget):
    def __init__(self, figsize, parent=None, scroll=False):
        super().__init__(parent)
        self.fig = Figure(figsize=figsize)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.toolbar)
        self.scroll = None
        if scroll:  # 空間不足時可捲動，不會把圖擠扁
            self.scroll = QScrollArea()
            self.scroll.setWidgetResizable(True)
            self.scroll.setWidget(self.canvas)
            lay.addWidget(self.scroll)
        else:
            lay.addWidget(self.canvas)


class DataPlotWidget(_CanvasWidget):
    sliceClicked = pyqtSignal(float)          # 點擊 2D 圖的 y 值
    rangeSelected = pyqtSignal(float, float)  # 切片圖拖曳的頻率範圍 (GHz)

    def __init__(self, parent=None):
        super().__init__((8, 7), parent)
        self.f = None
        self.yv = None
        self.db = None
        self.yname = ""
        self.idx = 0
        self.frange = (0.0, 0.0)
        self.frange2 = None        # 第二視窗 (GHz)，None = 不使用
        self.ax_map = None
        self.ax_sl = None
        self.span = None
        self.zoom = True           # 繪圖範圍跟隨遮罩
        self.track = None          # 連續擬合軌跡 (y, f_ghz, ok)
        self.track_art = []
        self.removed = None        # 目前切片被去除的點（布林陣列，長度 = 頻率點數）
        self.exclude_ranges = []   # 手動排除頻段 (GHz)
        self.exclude_art = []
        self.nodes = []            # 節點頻率 (GHz)
        self.node_art = []
        self.canvas.mpl_connect("button_press_event", self._on_click)

    def set_map(self, f_ghz, yv, db, yname, title):
        """重建整張圖（只在換檔案 / 換 S 參數 / 換軸時呼叫）"""
        self.f, self.yv, self.db, self.yname = f_ghz, yv, db, yname
        self.idx = min(self.idx, db.shape[1] - 1)
        fig = self.fig
        fig.clear()
        self.ax_map = self.hline = self.v1 = self.v2 = None
        self.v3 = self.v4 = None
        self.track_art = []
        self.node_art = []

        if db.shape[1] > 1:
            self.ax_map, self.ax_sl = fig.subplots(2, 1)
            order = np.argsort(yv)
            img = db[:, order].T
            finite = img[np.isfinite(img)]
            vmin, vmax = (np.percentile(finite, [1, 99.5]) if finite.size else (None, None))
            y0, y1 = float(yv[order[0]]), float(yv[order[-1]])
            if y0 == y1:
                y1 = y0 + 1
            self.ax_map.imshow(img, aspect="auto", origin="lower", cmap="viridis",
                               extent=[f_ghz[0], f_ghz[-1], y0, y1],
                               vmin=vmin, vmax=vmax, interpolation="nearest")
            self.hline = self.ax_map.axhline(yv[self.idx], color="r", lw=1)
            self.v1 = self.ax_map.axvline(f_ghz[0], color="w", ls="--", lw=1)
            self.v2 = self.ax_map.axvline(f_ghz[-1], color="w", ls="--", lw=1)
            self.v3 = self.ax_map.axvline(f_ghz[0], color="orange", ls="--", lw=1, visible=False)
            self.v4 = self.ax_map.axvline(f_ghz[-1], color="orange", ls="--", lw=1, visible=False)
            self.ax_map.set_xlabel("Frequency (GHz)")
            self.ax_map.set_ylabel(yname)
            self.ax_map.set_title(f"|{title}| (dB)")
        else:
            self.ax_sl = fig.add_subplot(1, 1, 1)

        self.l_full, = self.ax_sl.plot(f_ghz, db[:, self.idx], color="0.6", lw=1, label="full")
        self.l_sel, = self.ax_sl.plot([], [], color="C0", lw=1.5, label="fit range")
        self.l_bad, = self.ax_sl.plot([], [], "x", color="red", ms=6, mew=1.2, label="removed")
        self.exclude_art = []
        self.span2 = self.ax_sl.axvspan(f_ghz[0], f_ghz[0], color="orange", alpha=0.12,
                                        visible=False, label="window 2")
        self.ax_sl.set_xlabel("Frequency (GHz)")
        self.ax_sl.set_ylabel("Magnitude (dB)")
        self.ax_sl.grid(alpha=0.3)
        self.ax_sl.legend(loc="best")
        fig.tight_layout()
        self.span = SpanSelector(self.ax_sl, self._on_span, "horizontal", useblit=True,
                                 props=dict(alpha=0.2, facecolor="tab:orange"))
        self._draw_track()
        self._draw_excludes()
        self._draw_nodes()
        self._update()

    def set_slice(self, idx):
        if self.db is None:
            return
        self.idx = int(np.clip(idx, 0, self.db.shape[1] - 1))
        self._update()

    def set_range(self, f1, f2):
        self.frange = tuple(sorted((f1, f2)))
        if self.db is not None:
            self._update()

    def set_range2(self, rng):
        """第二視窗 (f1, f2) GHz；None 表示不使用"""
        self.frange2 = None if rng is None else tuple(sorted(rng))
        if self.db is not None:
            self._update()

    def set_removed(self, mask):
        """標記目前切片被去除的點（None = 無）"""
        self.removed = None if mask is None else np.asarray(mask, bool)
        if self.db is not None:
            self._update()

    def set_exclude_ranges(self, ranges):
        self.exclude_ranges = list(ranges or [])
        if self.db is not None:
            self._draw_excludes()
            self._update()

    def set_nodes(self, f_ghz):
        """在 2D 圖與切片圖上標示節點頻率（φ = nπ）；傳空 list 清除"""
        self.nodes = list(f_ghz or [])
        if self.db is not None:
            self._draw_nodes()
            self.canvas.draw_idle()

    def _draw_nodes(self):
        for a in self.node_art:
            try:
                a.remove()
            except ValueError:
                pass
        self.node_art = []
        for f in self.nodes:
            if self.ax_map is not None:
                self.node_art.append(self.ax_map.axvline(f, color="#ff66cc", ls=":", lw=1.2))
            self.node_art.append(self.ax_sl.axvline(f, color="#ff66cc", ls=":", lw=1.0))

    def _draw_excludes(self):
        for a in self.exclude_art:
            try:
                a.remove()
            except ValueError:
                pass
        self.exclude_art = []
        for a, b in self.exclude_ranges:
            self.exclude_art.append(self.ax_sl.axvspan(a, b, color="red", alpha=0.10, lw=0))
            if self.ax_map is not None:
                self.exclude_art.append(self.ax_map.axvspan(a, b, color="red", alpha=0.18, lw=0))

    def set_zoom(self, on):
        self.zoom = bool(on)
        if self.db is not None:
            self._update()

    def set_track(self, y=None, f_ghz=None, ok=None, f2_ghz=None):
        """在 2D 圖上疊加連續擬合的頻率軌跡（f2_ghz 為第二追蹤參數）；傳 None 清除"""
        self.track = None if y is None else (np.asarray(y), np.asarray(f_ghz), np.asarray(ok, bool),
                                             None if f2_ghz is None else np.asarray(f2_ghz))
        if self.db is not None:
            self._draw_track()
            self.canvas.draw_idle()

    def _draw_track(self):
        for a in self.track_art:
            try:
                a.remove()
            except ValueError:
                pass
        self.track_art = []
        if self.ax_map is None or self.track is None:
            return
        y, f, ok, f2 = self.track
        xl, yl = self.ax_map.get_xlim(), self.ax_map.get_ylim()
        for ff, col in ((f, "#ff4d4d"), (f2, "#ffa31a")):
            if ff is None:
                continue
            if ok.any():
                self.track_art.append(self.ax_map.plot(ff[ok], y[ok], "o", ms=3, mfc="none",
                                                       mec=col, mew=0.8)[0])
            if (~ok).any():
                self.track_art.append(self.ax_map.plot(ff[~ok], y[~ok], "x", ms=4,
                                                       color="#bbbbbb", mew=0.8)[0])
        self.ax_map.set_xlim(xl)
        self.ax_map.set_ylim(yl)

    def _update(self):
        f, y = self.f, self.db[:, self.idx]
        f1, f2 = self.frange
        m = (f >= f1) & (f <= f2)
        r2 = self.frange2
        if r2 is not None:
            m |= (f >= r2[0]) & (f <= r2[1])
        self.l_full.set_ydata(y)
        bad = self.removed if (self.removed is not None and self.removed.shape == y.shape) else None
        if bad is not None:
            self.l_bad.set_data(f[bad], y[bad])
            good_y = np.where(bad, np.nan, y)
        else:
            self.l_bad.set_data([], [])
            good_y = y
        # 聯集中不連續的部分與被去除的點用 NaN 斷開，避免畫出跨越空白的連線
        ys = np.where(m, good_y, np.nan)
        self.l_sel.set_data(f, ys)
        on2 = r2 is not None
        if on2:
            xy = self.span2.get_xy() if hasattr(self.span2, "get_xy") else None
            try:
                self.span2.set_x(r2[0])
                self.span2.set_width(r2[1] - r2[0])
            except AttributeError:
                if xy is not None:
                    xy[:, 0] = [r2[0], r2[0], r2[1], r2[1], r2[0]][:len(xy)]
                    self.span2.set_xy(xy)
        self.span2.set_visible(on2)
        if self.hline is not None:
            yy = self.yv[self.idx]
            self.hline.set_ydata([yy, yy])
            self.v1.set_xdata([f1, f1])
            self.v2.set_xdata([f2, f2])
            self.v3.set_visible(on2)
            self.v4.set_visible(on2)
            if on2:
                self.v3.set_xdata([r2[0], r2[0]])
                self.v4.set_xdata([r2[1], r2[1]])
        if self.zoom and m.sum() >= 2:
            lo, hi = (f1, f2) if not on2 else (min(f1, r2[0]), max(f2, r2[1]))
            pad = (hi - lo) * 0.03
            xr = (lo - pad, hi + pad) if on2 else (lo, hi)
            yy = good_y[(f >= xr[0]) & (f <= xr[1])]
        else:
            xr = (f[0], f[-1])
            yy = good_y   # y 軸範圍不含被去除的尖峰
        self.ax_sl.set_xlim(*xr)
        if self.ax_map is not None:
            self.ax_map.set_xlim(*xr)
        fin = yy[np.isfinite(yy)]
        if fin.size:
            pad = max(0.05 * np.ptp(fin), 0.1)
            self.ax_sl.set_ylim(fin.min() - pad, fin.max() + pad)
        self.ax_sl.set_title(f"切片 #{self.idx}   {self.yname} = {self.yv[self.idx]:.6g}")
        self.canvas.draw_idle()

    def _on_span(self, xmin, xmax):
        if xmax > xmin:
            self.rangeSelected.emit(xmin, xmax)

    def _on_click(self, ev):
        if self.ax_map is None or ev.inaxes is not self.ax_map or ev.ydata is None:
            return
        if getattr(self.toolbar, "mode", ""):
            return
        self.sliceClicked.emit(float(ev.ydata))


class FitPlotWidget(_CanvasWidget):
    def __init__(self, parent=None, min_height=None):
        super().__init__((12, 7), parent, scroll=min_height is not None)
        if min_height:
            self.canvas.setMinimumHeight(min_height)

    def plot(self, f, s, c, mag, label="Fitted", title=None, removed=None):
        """removed: (f_removed, s_removed) 被去除的雜訊點，以灰色 × 標示（不影響座標範圍）"""
        fig = self.fig
        fig.clear()
        axs = fig.subplots(2, 3)
        fg = f / 1e9
        xl = "Frequency (GHz)"
        with np.errstate(divide="ignore"):
            axs[0, 0].plot(fg, 20 * np.log10(np.abs(s)), "b", label="Measured")
            axs[0, 0].plot(fg, 20 * np.log10(mag), "r--", label=label)
            if removed is not None and len(removed[0]):
                yl = axs[0, 0].get_ylim()
                rf, rs = removed
                ydb = np.clip(20 * np.log10(np.abs(rs)), *yl)
                axs[0, 0].plot(np.asarray(rf) / 1e9, ydb, "x", color="0.5", ms=5,
                               label=f"removed ({len(rf)})")
                axs[0, 0].set_ylim(yl)
        axs[0, 1].plot(fg, np.unwrap(np.angle(s)), "b", label="Measured")
        axs[0, 2].plot(s.real, s.imag, "b", label="Measured")
        axs[1, 0].plot(fg, s.real, "b", label="Measured")
        axs[1, 1].plot(fg, s.imag, "b", label="Measured")
        if c is not None:
            axs[0, 1].plot(fg, np.unwrap(np.angle(c)), "r--", label=label)
            axs[0, 2].plot(c.real, c.imag, "r--", label=label)
            axs[1, 0].plot(fg, c.real, "r--", label=label)
            axs[1, 1].plot(fg, c.imag, "r--", label=label)
            axs[1, 2].plot(fg, np.abs(s - c), "k", lw=1)
            axs[1, 2].set(xlabel=xl, ylabel="|S − fit|", title="Residual")
        else:
            axs[1, 2].plot(fg, np.abs(s) - mag, "k", lw=1)
            axs[1, 2].set(xlabel=xl, ylabel="|S| − fit", title="Residual")
        axs[1, 2].grid(alpha=0.3)
        axs[0, 0].set(xlabel=xl, ylabel="Magnitude (dB)", title="Magnitude")
        axs[0, 1].set(xlabel=xl, ylabel="Phase (rad)", title="Phase")
        axs[0, 2].set(xlabel="Real", ylabel="Imag", title="IQ Plane")
        axs[0, 2].set_aspect("equal", adjustable="datalim")
        axs[1, 0].set(xlabel=xl, ylabel="Real", title="Real Part")
        axs[1, 1].set(xlabel=xl, ylabel="Imag", title="Imaginary Part")
        for a in axs.flat:
            a.xaxis.set_major_locator(MaxNLocator(4))
            a.tick_params(labelsize=8)
            a.title.set_fontsize(10)
            a.xaxis.label.set_fontsize(9)
            a.yaxis.label.set_fontsize(9)
        for a in (axs[0, 0], axs[0, 1], axs[0, 2], axs[1, 0], axs[1, 1]):
            a.grid(alpha=0.3)
            a.legend(fontsize=7)
        if title:
            fig.suptitle(title, fontsize=10)
            fig.tight_layout(rect=(0, 0, 1, 0.93))
        else:
            fig.tight_layout()
        self.canvas.draw_idle()


class ParamPlotWidget(_CanvasWidget):
    """連續擬合：參數隨掃描軸 / 追蹤頻率變化；點擊資料點可選取該切片"""
    pointClicked = pyqtSignal(int)   # 回傳 row 編號

    def __init__(self, parent=None):
        super().__init__((6, 4), parent)
        self.ax = self.fig.add_subplot(1, 1, 1)
        self._x = self._y = self._rows = None
        self.canvas.mpl_connect("button_press_event", self._on_click)

    def plot(self, x, y, err, ok, rows, xlabel, ylabel, sel_row=None, title=""):
        ax = self.ax
        ax.clear()
        x, y = np.asarray(x, float), np.asarray(y, float)
        err = None if err is None else np.asarray(err, float)
        ok = np.asarray(ok, bool)
        self._x, self._y, self._rows = x, y, list(rows)
        if ok.any():
            e = None if err is None else np.where(np.isfinite(err[ok]), err[ok], 0)
            ax.errorbar(x[ok], y[ok], yerr=e, fmt="o-", ms=3, lw=1, color="C0",
                        ecolor="C0", elinewidth=0.8, capsize=0, label="OK")
        bad = ~ok & np.isfinite(y)
        if bad.any():
            ax.plot(x[bad], y[bad], "x", color="0.6", ms=5, label="失敗 / 低 R²")
        if sel_row is not None and sel_row in self._rows:
            i = self._rows.index(sel_row)
            if np.isfinite(y[i]):
                ax.plot([x[i]], [y[i]], "o", ms=10, mfc="none", mec="r", mew=1.5)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(alpha=0.3)
        if ok.any() or bad.any():
            ax.legend(fontsize=8, loc="best")
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def clear(self):
        self.ax.clear()
        self._x = self._y = self._rows = None
        self.canvas.draw_idle()

    def _on_click(self, ev):
        if ev.inaxes is not self.ax or self._x is None or getattr(self.toolbar, "mode", ""):
            return
        good = np.isfinite(self._x) & np.isfinite(self._y)
        if not good.any():
            return
        pts = self.ax.transData.transform(np.column_stack([self._x[good], self._y[good]]))
        d = np.hypot(pts[:, 0] - ev.x, pts[:, 1] - ev.y)
        i = int(np.argmin(d))
        if d[i] < 15:
            self.pointClicked.emit(int(np.array(self._rows)[good][i]))


class AllParamsPlotWidget(_CanvasWidget):
    """連續擬合：所有參數 vs X 的小圖陣列；點任一資料點可選取該切片"""
    pointClicked = pyqtSignal(int)

    ROW_PX = 190   # 每列小圖最小高度；超過可視範圍時可捲動

    def __init__(self, parent=None):
        super().__init__((10, 7), parent, scroll=True)
        self._axes = []      # [(ax, x, y, rows, sel_artist)]
        self.canvas.mpl_connect("button_press_event", self._on_click)

    def plot(self, x, series, ok, rows, xlabel, sel_row=None, title=""):
        """
        series: list of dict(y, err, label, fixed)
        """
        fig = self.fig
        fig.clear()
        self._axes = []
        n = len(series)
        if n == 0:
            self.canvas.draw_idle()
            return
        ncol = 3 if n <= 9 else 4
        nrow = int(np.ceil(n / ncol))
        x = np.asarray(x, float)
        ok = np.asarray(ok, bool)
        rows = list(rows)
        self.canvas.setMinimumHeight(nrow * self.ROW_PX + 40)
        axs = fig.subplots(nrow, ncol, sharex=True, squeeze=False)
        for k, ser in enumerate(series):
            ax = axs[k // ncol][k % ncol]
            y = np.asarray(ser["y"], float)
            err = None if ser.get("err") is None else np.asarray(ser["err"], float)
            if ok.any():
                e = None if err is None else np.where(np.isfinite(err[ok]), err[ok], 0)
                ax.errorbar(x[ok], y[ok], yerr=e, fmt="o-", ms=2.5, lw=0.9, color="C0",
                            ecolor="C0", elinewidth=0.6, capsize=0)
            bad = ~ok & np.isfinite(y)
            if bad.any():
                ax.plot(x[bad], y[bad], "x", color="0.6", ms=4)
            fin = y[ok & np.isfinite(y)]
            if fin.size:  # 誤差棒過大時不讓 y 軸被撐爆
                lo, hi = fin.min(), fin.max()
                pad = (hi - lo) * 0.1 or abs(hi) * 0.05 or 1.0
                ax.set_ylim(lo - pad, hi + pad)
            sel_art, = ax.plot([], [], "o", ms=8, mfc="none", mec="r", mew=1.4)
            ax.set_title(ser["label"] + ("（固定）" if ser.get("fixed") else ""), fontsize=9)
            ax.tick_params(labelsize=8)
            ax.xaxis.set_major_locator(MaxNLocator(5))
            ax.yaxis.set_major_locator(MaxNLocator(5))
            ax.grid(alpha=0.3)
            ax.yaxis.get_major_formatter().set_useOffset(False)
            self._axes.append((ax, x, y, rows, sel_art))
        for k in range(n, nrow * ncol):
            axs[k // ncol][k % ncol].set_visible(False)
        for c in range(ncol):
            # 每欄最下面一個可見的圖標 X 軸
            for r in range(nrow - 1, -1, -1):
                if r * ncol + c < n:
                    axs[r][c].set_xlabel(xlabel, fontsize=9)
                    axs[r][c].tick_params(labelbottom=True)
                    break
        if title:
            fig.suptitle(title, fontsize=10)
            fig.tight_layout(rect=(0, 0, 1, 0.96))
        else:
            fig.tight_layout()
        self.set_selected(sel_row, draw=False)
        self.canvas.draw_idle()

    def set_selected(self, sel_row, draw=True):
        for ax, x, y, rows, art in self._axes:
            if sel_row is not None and sel_row in rows:
                i = rows.index(sel_row)
                art.set_data([x[i]], [y[i]])
            else:
                art.set_data([], [])
        if draw:
            self.canvas.draw_idle()

    def clear(self):
        self.fig.clear()
        self._axes = []
        self.canvas.draw_idle()

    def _on_click(self, ev):
        if getattr(self.toolbar, "mode", ""):
            return
        for ax, x, y, rows, _ in self._axes:
            if ev.inaxes is not ax:
                continue
            good = np.isfinite(x) & np.isfinite(y)
            if not good.any():
                return
            pts = ax.transData.transform(np.column_stack([x[good], y[good]]))
            d = np.hypot(pts[:, 0] - ev.x, pts[:, 1] - ev.y)
            i = int(np.argmin(d))
            if d[i] < 15:
                self.pointClicked.emit(int(np.array(rows)[good][i]))
            return


class PhasePlotWidget(_CanvasWidget):
    """相位 / 節點：κ_eff、φ（折疊）、訊號強度 vs 共振頻率"""

    def __init__(self, parent=None):
        super().__init__((8, 8), parent, scroll=True)
        self.canvas.setMinimumHeight(620)

    def plot(self, kap=None, phi=None, depth=None, line=None, nodes=(), period=np.pi,
             frange=None, glob=None, title=""):
        """
        kap   : (f_hz, κ, inlier 或 None, 單位)
        phi   : (f_hz, φ)
        depth : (f_hz, d, detected_nodes_hz)
        line  : dict(T_ns, phi_ref, f_ref, kappa_b 或 None)
        glob  : (f_hz, φ) 全域擬合每片的相位
        """
        from ..core.phase import phase_line
        fig = self.fig
        fig.clear()
        axs = fig.subplots(3, 1, sharex=True)
        if frange is None:
            xs = [a[0] for a in (kap, phi, depth) if a is not None and len(a[0])]
            frange = (min(np.min(x) for x in xs), max(np.max(x) for x in xs)) if xs else (0, 1)
        fg = np.linspace(frange[0], frange[1], 2000)
        G = 1e9

        ax = axs[0]
        if kap is not None and len(kap[0]):
            f, k, inl, unit = kap
            inl = np.ones(len(f), bool) if inl is None else np.asarray(inl, bool)
            ax.plot(f[inl] / G, k[inl], "o", ms=3, color="C0", label="κ_eff（逐片）")
            if (~inl).any():
                ax.plot(f[~inl] / G, k[~inl], "x", ms=4, color="0.6", label="離群")
            ax.set_ylabel(f"κ_eff [{unit}]" if unit else "κ_eff")
        if line and line.get("kappa_b") is not None and np.isfinite(line["kappa_b"]):
            ph = phase_line(fg, line["T_ns"], line["phi_ref"], line["f_ref"])
            ax.plot(fg / G, line["kappa_b"] * np.sin(np.pi / period * ph) ** 2, "-", color="C3",
                    lw=1.2, label="κ_b sin²φ(f)")
        ax.set_title("耦合強度  κ_eff = κ_b sin²φ")

        ax = axs[1]
        P = period
        if phi is not None and len(phi[0]):
            ax.plot(phi[0] / G, np.mod(phi[1], P), "o", ms=3, color="C0", label="φ（逐片，mod P）")
        if glob is not None and len(glob[0]):
            ax.plot(glob[0] / G, np.mod(glob[1], P), "s", ms=4, mfc="none", color="C2",
                    label="φ（全域擬合）")
        if line:
            ph = np.mod(phase_line(fg, line["T_ns"], line["phi_ref"], line["f_ref"]), P)
            ph[np.abs(np.diff(ph, prepend=ph[0])) > P / 2] = np.nan
            ax.plot(fg / G, ph, "-", color="C3", lw=1.2, label="相位直線")
        ax.set_ylim(-0.05 * P, 1.05 * P)
        ax.set_ylabel("φ mod P [rad]")
        ax.set_title("相位（週期 P 折疊；節點 = 0 / P）")

        ax = axs[2]
        if depth is not None and len(depth[0]):
            f, d, det = depth
            o = np.argsort(f)
            ax.plot(np.asarray(f)[o] / G, np.asarray(d)[o], ".-", ms=3, lw=0.8, color="C0",
                    label="訊號強度")
            for x in det or []:
                ax.axvline(x / G, color="C1", ls="--", lw=1)
        ax.set_ylabel("max||S|−中位數| / 中位數")
        ax.set_xlabel("共振頻率 (GHz)")
        ax.set_title("訊號強度（虛線 = 自動偵測的節點）")

        for ax in axs:
            for n, f in nodes:
                ax.axvline(f / G, color="#ff66cc", ls=":", lw=1.2)
            ax.grid(alpha=0.3)
            h, _ = ax.get_legend_handles_labels()
            if h:
                ax.legend(loc="best", fontsize=8)
            ax.yaxis.set_major_locator(MaxNLocator(5))
        if nodes:
            ymax = axs[0].get_ylim()[1]
            for n, f in nodes:
                axs[0].text(f / G, ymax, f"n={n}", ha="center", va="bottom", fontsize=8,
                            color="#cc3399")
        if title:
            fig.suptitle(title, fontsize=10)
        fig.tight_layout(rect=(0, 0, 1, 0.97 if title else 1))
        self.canvas.draw_idle()
