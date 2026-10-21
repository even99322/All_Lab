"""
求解器模式說明視窗 (Adaptive / Sweep)，含 QPainter 動畫。

動畫皆為「原理示意」：網格以 2D 三角形代表 HFSS 的 3D 四面體，
Interpolating 以單調三次插值代表 HFSS 的有理函數擬合，數值不代表實際模擬結果。
"""
import math
import numpy as np

from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, pyqtSignal, QObject, QEvent
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPolygonF, QFont, QPixmap, QPainterPath
from PyQt6.QtWidgets import (QDialog, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout, QTextBrowser,
                             QPushButton, QButtonGroup, QLabel, QSizePolicy, QApplication)

# ----------------------------------------------------------------------
# 顏色 (依系統淺色 / 深色模式自動切換)
# ----------------------------------------------------------------------
THEMES = {
    "light": dict(
        BG="#FFFFFF", PANEL="#F7F9FA", AXIS="#546E7A", GRID="#E3E8EB", TEXT="#263238", MUTED="#6B7C86",
        TRUE="#A7B6BE", PT="#E65100", FIT="#1565C0", THR="#C62828", OK="#2E7D32", SEL="#E53935",
        BOX_START="#ECEFF1", BOX_STEP="#E3F2FD", BOX_CHECK="#FFF3E0", BOX_END="#E8F5E9",
        CMAP=[(0.0, (236, 244, 252)), (0.45, (100, 160, 225)), (0.75, (30, 90, 190)), (1.0, (255, 179, 0))],
        MESH_EDGE=(69, 90, 100, 110),
        # HTML
        H3="#1565C0", H4="#37474F", TAG_BG="#E3F2FD", TAG_FG="#0D47A1", WARN="#C62828", OKT="#2E7D32",
        TD_BORDER="#CFD8DC", TH_BG="#ECEFF1", TH_FG="#263238", LINK="#1565C0",
    ),
    "dark": dict(
        BG="#1F2124", PANEL="#26292D", AXIS="#9AAAB3", GRID="#383D43", TEXT="#E8ECEF", MUTED="#A3B0B8",
        TRUE="#6E7C85", PT="#FFA24C", FIT="#64B5F6", THR="#FF6B6B", OK="#7BD17F", SEL="#FF5252",
        BOX_START="#33383D", BOX_STEP="#1C3350", BOX_CHECK="#4A3718", BOX_END="#1E3D27",
        CMAP=[(0.0, (38, 46, 58)), (0.45, (40, 95, 170)), (0.75, (70, 150, 235)), (1.0, (255, 190, 40))],
        MESH_EDGE=(160, 175, 185, 70),
        H3="#64B5F6", H4="#CFD8DC", TAG_BG="#1C3350", TAG_FG="#90CAF9", WARN="#FF8A80", OKT="#81C784",
        TD_BORDER="#4A535B", TH_BG="#353B41", TH_FG="#E8ECEF", LINK="#90CAF9",
    ),
}
THEME = dict(THEMES["light"])
C_BG = C_PANEL = C_AXIS = C_GRID = C_TEXT = C_MUTED = C_TRUE = C_PT = C_FIT = C_THR = C_OK = C_SEL = None
C_COPPER = QColor(205, 127, 50, 190)


def is_dark_palette(widget=None):
    """以實際使用中的調色盤判斷 (Windows 深色模式 + Fusion 時，Qt 會自動換成深色調色盤)。"""
    pal = widget.palette() if widget is not None else QApplication.palette()
    return pal.window().color().lightness() < 128


def apply_theme(dark):
    global C_BG, C_PANEL, C_AXIS, C_GRID, C_TEXT, C_MUTED, C_TRUE, C_PT, C_FIT, C_THR, C_OK, C_SEL
    THEME.clear()
    THEME.update(THEMES["dark" if dark else "light"])
    THEME["name"] = "dark" if dark else "light"
    C_BG, C_PANEL, C_AXIS, C_GRID = (QColor(THEME[k]) for k in ("BG", "PANEL", "AXIS", "GRID"))
    C_TEXT, C_MUTED, C_TRUE, C_PT = (QColor(THEME[k]) for k in ("TEXT", "MUTED", "TRUE", "PT"))
    C_FIT, C_THR, C_OK, C_SEL = (QColor(THEME[k]) for k in ("FIT", "THR", "OK", "SEL"))


apply_theme(False)


def _font(size=9, bold=False):
    f = QFont()
    f.setPointSizeF(size)
    f.setBold(bold)
    return f


# ----------------------------------------------------------------------
# 示意用的「真實」響應：帶一個高 Q 凹陷的 S21
# ----------------------------------------------------------------------
F_MIN, F_MAX = 2.0, 9.0
_F0, _Q, _A = 6.3, 35.0, 0.95


def true_s21_db(f):
    f = np.asarray(f, float)
    s = (1 - _A / (1 + 1j * 2 * _Q * (f - _F0) / _F0)) * (0.97 - 0.01 * (f - 2)) * (1 + 0.02 * np.sin(2.2 * f))
    return 20 * np.log10(np.abs(s))


def pchip(x, y, xq):
    """單調三次 Hermite 插值 (Fritsch–Carlson)。"""
    x = np.asarray(x, float); y = np.asarray(y, float); xq = np.asarray(xq, float)
    h = np.diff(x); d = np.diff(y) / h
    m = np.zeros_like(y)
    if len(x) == 2:
        m[:] = d[0]
    else:
        for k in range(1, len(x) - 1):
            if d[k - 1] * d[k] <= 0:
                m[k] = 0
            else:
                w1, w2 = 2 * h[k] + h[k - 1], h[k] + 2 * h[k - 1]
                m[k] = (w1 + w2) / (w1 / d[k - 1] + w2 / d[k])
        m[0], m[-1] = d[0], d[-1]
    i = np.clip(np.searchsorted(x, xq) - 1, 0, len(x) - 2)
    t = (xq - x[i]) / h[i]
    h00 = 2 * t ** 3 - 3 * t ** 2 + 1; h10 = t ** 3 - 2 * t ** 2 + t
    h01 = -2 * t ** 3 + 3 * t ** 2; h11 = t ** 3 - t ** 2
    return h00 * y[i] + h10 * h[i] * m[i] + h01 * y[i + 1] + h11 * h[i] * m[i + 1]


# ======================================================================
# Adaptive：時間軸控制器 (網格動畫、收斂圖、頻點圖共用)
# ======================================================================
DELTA_S = [None, 0.31, 0.12, 0.047, 0.016, 0.009]      # 每個 pass 的 ΔS (示意)
THRESHOLD = 0.02
MIN_CONVERGED = 2
N_PASSES = len(DELTA_S)


class AdaptiveTimeline(QObject):
    changed = pyqtSignal()
    PASS_MS = 1800
    HOLD_MS = 2600

    def __init__(self, parent=None):
        super().__init__(parent)
        self.t = 0
        self.playing = True
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    @property
    def total(self):
        return N_PASSES * self.PASS_MS + self.HOLD_MS

    def _tick(self):
        if not self.playing:
            return
        self.t = (self.t + 40) % self.total
        self.changed.emit()

    def state(self):
        """回傳 (pass_index 0-based, phase 0~1, finished)"""
        if self.t >= N_PASSES * self.PASS_MS:
            return N_PASSES - 1, 1.0, True
        return self.t // self.PASS_MS, (self.t % self.PASS_MS) / self.PASS_MS, False

    def restart(self):
        self.t = 0
        self.changed.emit()


# ----------------------------------------------------------------------
# 網格產生 (2D 三角形示意)
# ----------------------------------------------------------------------
_W, _H = 1.0, 0.6
_STRIP_Y = (0.27, 0.33)
_GAP_X = (0.44, 0.56)
_CORNERS = [(_GAP_X[0], _STRIP_Y[0]), (_GAP_X[0], _STRIP_Y[1]), (_GAP_X[1], _STRIP_Y[0]), (_GAP_X[1], _STRIP_Y[1])]


def _dist_seg(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / (vx * vx + vy * vy)))
    return math.hypot(px - ax - t * vx, py - ay - t * vy)


def _field_strength(x, y):
    """場強示意：金屬邊緣與間隙轉角最強。"""
    segs = []
    for x0, x1 in ((0.0, _GAP_X[0]), (_GAP_X[1], _W)):
        for yy in _STRIP_Y:
            segs.append((x0, yy, x1, yy))
    for gx in _GAP_X:
        segs.append((gx, _STRIP_Y[0], gx, _STRIP_Y[1]))
    d_edge = min(_dist_seg(x, y, *s) for s in segs)
    d_corner = min(math.hypot(x - cx, y - cy) for cx, cy in _CORNERS)
    return (0.15 + 1.2 * math.exp(-(d_edge / 0.05) ** 2) + 0.35 * math.exp(-(d_edge / 0.15) ** 2)
            + 2.0 * math.exp(-(d_corner / 0.05) ** 2))


def _area(t):
    (x1, y1), (x2, y2), (x3, y3) = t
    return abs((x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)) / 2


def _bisect(t):
    a, b, c = t
    edges = [(a, b, c), (b, c, a), (c, a, b)]
    p, q, r = max(edges, key=lambda e: math.dist(e[0], e[1]))
    m = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
    return [(p, m, r), (m, q, r)]


def build_meshes(n_passes=N_PASSES, frac=0.25):
    nx, ny = 12, 7
    tris = []
    for i in range(nx):
        for j in range(ny):
            x0, x1 = i * _W / nx, (i + 1) * _W / nx
            y0, y1 = j * _H / ny, (j + 1) * _H / ny
            if (i + j) % 2 == 0:
                tris += [((x0, y0), (x1, y0), (x1, y1)), ((x0, y0), (x1, y1), (x0, y1))]
            else:
                tris += [((x0, y0), (x1, y0), (x0, y1)), ((x1, y0), (x1, y1), (x0, y1))]
    meshes, selections = [], []
    for _ in range(n_passes):
        cen = [((a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3) for a, b, c in tris]
        g = np.array([_field_strength(*p) for p in cen])
        err = np.array([_area(t) for t in tris]) * g ** 2
        order = np.argsort(err)[::-1]
        sel = set(order[:int(len(tris) * frac)].tolist())
        meshes.append((tris, g))
        selections.append(sel)
        new = []
        for idx, t in enumerate(tris):
            if idx in sel:
                for child in _bisect(t):
                    new += _bisect(child)
            else:
                new.append(t)
        tris = new
    return meshes, selections


def _cmap(v):
    """0~1 → 淺藍 → 藍 → 橘黃"""
    v = max(0.0, min(1.0, v))
    stops = THEME["CMAP"]
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if v <= p1:
            k = (v - p0) / (p1 - p0)
            return QColor(*[int(c0[i] + k * (c1[i] - c0[i])) for i in range(3)])
    return QColor(*stops[-1][1])


class MeshView(QWidget):
    def __init__(self, timeline, parent=None):
        super().__init__(parent)
        self.tl = timeline
        self.tl.changed.connect(self.update)
        self.meshes, self.selections = build_meshes()
        self._cache = {}
        self.setMinimumSize(360, 250)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _domain_rect(self):
        r = QRectF(self.rect()).adjusted(12, 30, -12, -34)
        scale = min(r.width() / _W, r.height() / _H)
        w, h = _W * scale, _H * scale
        return QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h), scale

    def _map(self, dom, scale, p):
        return QPointF(dom.left() + p[0] * scale, dom.bottom() - p[1] * scale)

    def _mesh_pixmap(self, k):
        key = (k, self.width(), self.height(), THEME["name"])
        if key in self._cache:
            return self._cache[key]
        pm = QPixmap(self.size())
        pm.fill(C_PANEL)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dom, scale = self._domain_rect()
        tris, g = self.meshes[k]
        gmax = float(np.percentile(g, 99))
        edge_pen = QPen(QColor(*THEME["MESH_EDGE"]), 0.6)
        for t, gv in zip(tris, g):
            poly = QPolygonF([self._map(dom, scale, v) for v in t])
            p.setPen(edge_pen)
            p.setBrush(_cmap(gv / gmax))
            p.drawPolygon(poly)
        # 金屬走線 (中間有間隙)
        p.setPen(QPen(QColor(120, 70, 20), 1.2))
        p.setBrush(C_COPPER)
        for x0, x1 in ((0.0, _GAP_X[0]), (_GAP_X[1], _W)):
            a = self._map(dom, scale, (x0, _STRIP_Y[1]))
            b = self._map(dom, scale, (x1, _STRIP_Y[0]))
            p.drawRect(QRectF(a, b))
        p.end()
        self._cache = {kk: vv for kk, vv in self._cache.items() if kk[1:] == key[1:]}
        self._cache[key] = pm
        return pm

    def paintEvent(self, _):
        k, phase, finished = self.tl.state()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(0, 0, self._mesh_pixmap(k))
        dom, scale = self._domain_rect()

        if not finished and 0.55 <= phase:
            # 標出「誤差最大、即將被細分」的元素
            tris, _ = self.meshes[k]
            p.setPen(QPen(C_SEL, 1.4))
            sel_fill = QColor(C_SEL); sel_fill.setAlpha(60)
            p.setBrush(sel_fill)
            for idx in self.selections[k]:
                p.drawPolygon(QPolygonF([self._map(dom, scale, v) for v in tris[idx]]))

        n = len(self.meshes[k][0])
        p.setPen(C_TEXT)
        p.setFont(_font(10, True))
        p.drawText(QRectF(12, 4, self.width() - 24, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"Pass {k + 1}　元素數 {n}")
        if finished:
            msg, col = "✔ 已收斂，網格固定 → 交給 Sweep 使用", C_OK
        elif phase < 0.55:
            msg, col = "① 用目前網格求解場 (顏色 = 場強)", C_FIT
        else:
            msg, col = "② 誤差最大的 25% 元素 (紅框) 將被細分", C_SEL
        p.setPen(col)
        p.setFont(_font(9, True))
        p.drawText(QRectF(12, self.height() - 28, self.width() - 24, 22),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, msg)
        p.end()

    def resizeEvent(self, e):
        self._cache.clear()
        super().resizeEvent(e)


class ConvergenceChart(QWidget):
    def __init__(self, timeline, parent=None):
        super().__init__(parent)
        self.tl = timeline
        self.tl.changed.connect(self.update)
        self.setMinimumSize(260, 250)

    def paintEvent(self, _):
        k, phase, finished = self.tl.state()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), C_PANEL)
        plot = QRectF(self.rect()).adjusted(52, 30, -14, -40)
        lo, hi = math.log10(0.004), math.log10(1.2)

        def ymap(v):
            return plot.bottom() - (math.log10(v) - lo) / (hi - lo) * plot.height()

        p.setFont(_font(10, True)); p.setPen(C_TEXT)
        p.drawText(QRectF(8, 4, self.width() - 16, 22), Qt.AlignmentFlag.AlignLeft, "ΔS 收斂過程")
        p.setFont(_font(8))
        for v in (0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1):
            y = ymap(v)
            p.setPen(QPen(C_GRID, 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(C_MUTED)
            p.drawText(QRectF(0, y - 8, plot.left() - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{v:g}")
        # 門檻
        y_thr = ymap(THRESHOLD)
        p.setPen(QPen(C_THR, 1.6, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(plot.left(), y_thr), QPointF(plot.right(), y_thr))
        p.setPen(C_THR)
        p.drawText(QRectF(plot.left() + 4, y_thr - 18, plot.width(), 16), Qt.AlignmentFlag.AlignRight,
                   f"Max Delta S = {THRESHOLD}")

        bw = plot.width() / N_PASSES
        streak = 0
        shown = N_PASSES if finished else k + 1
        for i in range(N_PASSES):
            x = plot.left() + i * bw
            p.setPen(C_MUTED)
            p.drawText(QRectF(x, plot.bottom() + 4, bw, 16), Qt.AlignmentFlag.AlignCenter, f"{i + 1}")
            if i >= shown or DELTA_S[i] is None:
                if i == 0 and shown >= 1:
                    p.drawText(QRectF(x, plot.bottom() - 34, bw, 30), Qt.AlignmentFlag.AlignCenter, "無前次\n可比")
                continue
            v = DELTA_S[i]
            grow = 1.0 if (i < k or finished) else min(1.0, phase / 0.4)
            y = plot.bottom() - (plot.bottom() - ymap(v)) * grow
            ok = v < THRESHOLD
            streak = streak + 1 if ok else 0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(C_OK if ok else C_FIT)
            p.drawRoundedRect(QRectF(x + bw * 0.22, y, bw * 0.56, plot.bottom() - y), 3, 3)
            if grow >= 1:
                p.setPen(C_TEXT)
                p.drawText(QRectF(x, y - 16, bw, 14), Qt.AlignmentFlag.AlignCenter, f"{v:g}")
        p.setPen(C_MUTED)
        p.drawText(QRectF(plot.left(), self.height() - 18, plot.width(), 16), Qt.AlignmentFlag.AlignCenter, "Pass")
        if finished:
            p.setPen(C_OK); p.setFont(_font(9, True))
            p.drawText(QRectF(8, 4, self.width() - 16, 22), Qt.AlignmentFlag.AlignRight,
                       f"✔ 連續 {MIN_CONVERGED} 次 < 門檻 → 收斂")
        p.end()


class FreqPlacementView(QWidget):
    """Single / Multi / Broadband 的自適應頻點位置。"""
    def __init__(self, timeline, parent=None):
        super().__init__(parent)
        self.tl = timeline
        self.tl.changed.connect(self.update)
        self.mode = "Single"
        self.setMinimumHeight(150)
        self.fd = np.linspace(F_MIN, F_MAX, 500)
        self.yd = true_s21_db(self.fd)

    def set_mode(self, mode):
        self.mode = mode
        self.update()

    def freqs_for_pass(self, k):
        if self.mode == "Single":
            return [9.0]
        if self.mode == "Multi-Frequencies":
            return [2.5, 6.3, 9.0]
        # Broadband (示意)：由頻段兩端開始，逐步在頻段內加入頻點
        return [[2.0, 9.0], [2.0, 5.5, 9.0]][k] if k < 2 else [2.0, 3.75, 5.5, 7.25, 9.0]

    def paintEvent(self, _):
        k, phase, finished = self.tl.state()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), C_PANEL)
        plot = QRectF(self.rect()).adjusted(40, 34, -20, -30)

        def xmap(f):
            return plot.left() + (f - F_MIN) / (F_MAX - F_MIN) * plot.width()

        def ymap(db):
            return plot.top() + (2 - db) / 30 * plot.height()

        path = QPainterPath(QPointF(xmap(self.fd[0]), ymap(self.yd[0])))
        for f, y in zip(self.fd[1:], self.yd[1:]):
            path.lineTo(QPointF(xmap(f), ymap(y)))
        p.setPen(QPen(C_TRUE, 1.5))
        p.drawPath(path)
        p.setPen(QPen(C_AXIS, 1))
        p.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))
        p.setFont(_font(8)); p.setPen(C_MUTED)
        for f in (2, 3, 4, 5, 7, 8, 9):
            x = xmap(f)
            p.drawLine(QPointF(x, plot.bottom()), QPointF(x, plot.bottom() + 4))
            p.drawText(QRectF(x - 20, plot.bottom() + 5, 40, 14), Qt.AlignmentFlag.AlignCenter, f"{f}")
        p.drawText(QRectF(plot.left(), plot.bottom() + 5, plot.width(), 14), Qt.AlignmentFlag.AlignCenter, "頻率 (GHz)")
        p.drawText(QRectF(xmap(_F0) + 6, ymap(-15), 120, 14), Qt.AlignmentFlag.AlignLeft, "← 高 Q 共振")

        freqs = self.freqs_for_pass(k)
        solving = (not finished) and phase < 0.55
        pulse = 0.5 + 0.5 * math.sin(phase * math.pi * 8) if solving else 0
        for f in freqs:
            x = xmap(f)
            col = QColor(C_PT)
            p.setPen(QPen(col, 2))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            r = 6 + 4 * pulse
            col.setAlpha(int(90 + 120 * pulse) if solving else 200)
            p.setBrush(col); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(x, plot.top()), r, r)

        # 累計求解次數
        total = sum(len(self.freqs_for_pass(i)) for i in range(k + 1))
        p.setPen(C_TEXT); p.setFont(_font(9, True))
        head = {"Single": "Single：只在 1 個頻率收斂網格",
                "Multi-Frequencies": "Multi-Frequencies：在指定的多個頻率同時收斂",
                "Broadband": "Broadband：自動在頻段內選頻點 (示意)"}[self.mode]
        p.drawText(QRectF(8, 4, self.width() - 16, 18), Qt.AlignmentFlag.AlignLeft, head)
        p.setFont(_font(9)); p.setPen(C_FIT)
        p.drawText(QRectF(8, 4, self.width() - 16, 18), Qt.AlignmentFlag.AlignRight,
                   f"Pass {k + 1}：本輪解 {len(freqs)} 個頻點　累計 {total} 次求解")
        p.end()


# ======================================================================
# Sweep 動畫
# ======================================================================
class SweepView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(300)
        self.fd = np.linspace(F_MIN, F_MAX, 900)
        self.yd = true_s21_db(self.fd)
        self.mode = "Interpolating"
        self.playing = True
        self._prepare()
        self.t = 0
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _prepare(self):
        # Discrete：36 個等距點
        self.disc_x = np.linspace(F_MIN, F_MAX, 36)
        # Interpolating：在擬合誤差最大處加點 (示意 HFSS 的誤差估計)
        pts = [F_MIN, (F_MIN + F_MAX) / 2, F_MAX]
        self.interp_steps = []
        for _ in range(40):
            xs = np.array(sorted(pts))
            fit = pchip(xs, true_s21_db(xs), self.fd)
            diff = np.abs(fit - self.yd)
            self.interp_steps.append((xs, fit, float(diff.max())))
            if diff.max() < 0.25:
                break
            pts.append(float(self.fd[np.argmax(diff)]))

    def set_mode(self, mode):
        self.mode = mode
        self.t = 0
        self.update()

    def restart(self):
        self.t = 0
        self.update()

    def _duration(self):
        if self.mode == "Discrete":
            return len(self.disc_x) * 160 + 3500
        if self.mode == "Interpolating":
            return len(self.interp_steps) * 420 + 3500
        return 2200 + 2200 + 3500

    def _tick(self):
        if self.playing:
            self.t = (self.t + 40) % self._duration()
            self.update()

    # ---- 座標 ----
    def _plot(self):
        return QRectF(self.rect()).adjusted(52, 40, -20, -58)

    def _xm(self, f):
        pl = self._plot()
        return pl.left() + (f - F_MIN) / (F_MAX - F_MIN) * pl.width()

    def _ym(self, db):
        pl = self._plot()
        return pl.top() + (3 - db) / 33 * pl.height()

    def _curve(self, p, xs, ys, pen):
        path = QPainterPath(QPointF(self._xm(xs[0]), self._ym(ys[0])))
        for x, y in zip(xs[1:], ys[1:]):
            path.lineTo(QPointF(self._xm(x), self._ym(y)))
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    def _dots(self, p, xs, highlight=None):
        p.setPen(QPen(C_PANEL, 1.2))
        p.setBrush(C_PT)
        for x in xs:
            p.drawEllipse(QPointF(self._xm(x), self._ym(float(true_s21_db(x)))), 4, 4)
        if highlight is not None:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(C_PT, 2))
            p.drawEllipse(QPointF(self._xm(highlight), self._ym(float(true_s21_db(highlight)))), 9, 9)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), C_PANEL)
        pl = self._plot()
        p.setFont(_font(8))
        for db in (0, -5, -10, -15, -20, -25, -30):
            y = self._ym(db)
            p.setPen(QPen(C_GRID, 1)); p.drawLine(QPointF(pl.left(), y), QPointF(pl.right(), y))
            p.setPen(C_MUTED)
            p.drawText(QRectF(0, y - 8, pl.left() - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{db}")
        for f in range(2, 10):
            x = self._xm(f)
            p.setPen(QPen(C_GRID, 1)); p.drawLine(QPointF(x, pl.top()), QPointF(x, pl.bottom()))
            p.setPen(C_MUTED)
            p.drawText(QRectF(x - 20, pl.bottom() + 4, 40, 14), Qt.AlignmentFlag.AlignCenter, f"{f}")
        p.drawText(QRectF(pl.left(), pl.bottom() + 18, pl.width(), 14), Qt.AlignmentFlag.AlignCenter, "頻率 (GHz)")
        p.save(); p.translate(12, pl.center().y()); p.rotate(-90)
        p.drawText(QRectF(-60, -8, 120, 16), Qt.AlignmentFlag.AlignCenter, "S21 (dB)")
        p.restore()
        # 真實響應 (灰色虛線)
        self._curve(p, self.fd, self.yd, QPen(C_TRUE, 1.6, Qt.PenStyle.DashLine))

        getattr(self, f"_paint_{self.mode.lower()}")(p, pl)

        # 圖例
        p.setFont(_font(8))
        lx = pl.right() - 250
        items = [(C_TRUE, "真實響應 (未知)", Qt.PenStyle.DashLine), (C_FIT, "Sweep 得到的曲線", Qt.PenStyle.SolidLine)]
        for i, (col, txt, st) in enumerate(items):
            y = pl.bottom() - 34 + i * 15
            p.setPen(QPen(col, 2, st)); p.drawLine(QPointF(lx, y), QPointF(lx + 22, y))
            p.setPen(C_TEXT); p.drawText(QRectF(lx + 28, y - 7, 200, 14), Qt.AlignmentFlag.AlignLeft, txt)
        y = pl.bottom() - 34 + 2 * 15
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(C_PT); p.drawEllipse(QPointF(lx + 11, y), 4, 4)
        p.setPen(C_TEXT); p.drawText(QRectF(lx + 28, y - 7, 200, 14), Qt.AlignmentFlag.AlignLeft, "實際做矩陣求解的頻點")
        p.end()

    def _header(self, p, left, right, right_color=C_FIT):
        p.setFont(_font(10, True)); p.setPen(C_TEXT)
        p.drawText(QRectF(10, 6, self.width() - 20, 20), Qt.AlignmentFlag.AlignLeft, left)
        p.setFont(_font(9, True)); p.setPen(right_color)
        p.drawText(QRectF(10, 6, self.width() - 20, 20), Qt.AlignmentFlag.AlignRight, right)

    def _footer(self, p, text, color=C_TEXT):
        p.setFont(_font(9, True)); p.setPen(color)
        p.drawText(QRectF(10, self.height() - 22, self.width() - 20, 18), Qt.AlignmentFlag.AlignLeft, text)

    def _paint_discrete(self, p, pl):
        n = min(len(self.disc_x), self.t // 160 + 1)
        xs = self.disc_x[:n]
        if n >= 2:
            self._curve(p, xs, true_s21_db(xs), QPen(C_FIT, 2))
        self._dots(p, xs, highlight=xs[-1] if n < len(self.disc_x) else None)
        self._header(p, "Discrete：每個頻點各做一次完整求解",
                     f"已求解 {n} / {len(self.disc_x)} 點　(時間 ∝ 點數)")
        if n == len(self.disc_x):
            depth = float(true_s21_db(self.disc_x).min())
            self._footer(p, f"⚠ 點距 0.2 GHz 大於共振頻寬 → 凹陷只量到 {depth:.1f} dB (真實約 {self.yd.min():.0f} dB)。"
                            f"要抓到它需要更密的點 = 更多時間", C_THR)
        else:
            self._footer(p, "每個點都是精確解，但點與點之間沒有任何資訊 (畫圖只是連線)", C_MUTED)

    def _paint_interpolating(self, p, pl):
        i = min(len(self.interp_steps) - 1, self.t // 420)
        xs, fit, err = self.interp_steps[i]
        self._curve(p, self.fd, fit, QPen(C_FIT, 2))
        new_pt = None
        if i > 0:
            prev = set(self.interp_steps[i - 1][0].tolist())
            added = [x for x in xs.tolist() if x not in prev]
            new_pt = added[0] if added else None
        self._dots(p, xs, highlight=new_pt)
        done = i == len(self.interp_steps) - 1
        self._header(p, "Interpolating：在誤差最大的地方補點，直到擬合誤差夠小",
                     f"求解 {len(xs)} 點　最大誤差 {err:.2f} dB", C_OK if done else C_FIT)
        if done:
            self._footer(p, f"✔ 只解了 {len(xs)} 個頻點就得到完整曲線；求解點自動集中在共振附近", C_OK)
        else:
            self._footer(p, "藍線 = 目前的擬合；在與真實響應差最多的頻率補一個求解點 (橘圈)", C_MUTED)

    def _paint_fast(self, p, pl):
        center = (F_MIN + F_MAX) / 2
        t = self.t
        xc = self._xm(center)
        if t < 2200:
            k = t / 2200
            r = 10 + 14 * abs(math.sin(k * math.pi * 3))
            col = QColor(C_PT); col.setAlpha(170)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(col)
            p.drawEllipse(QPointF(xc, self._ym(float(true_s21_db(center)))), r, r)
            # 進度條
            bar = QRectF(self._xm(2.4), self._ym(-14), self._xm(5.0) - self._xm(2.4), 10)
            p.setPen(QPen(C_AXIS, 1)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawRect(bar)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(C_PT)
            p.drawRect(QRectF(bar.left(), bar.top(), bar.width() * k, bar.height()))
            p.setPen(C_TEXT); p.setFont(_font(8))
            p.drawText(QRectF(bar.left(), bar.bottom() + 2, bar.width(), 14), Qt.AlignmentFlag.AlignCenter,
                       "在中心頻率做一次大型求解，建立降階模型 (ALPS)…")
            self._header(p, "Fast：一次大型求解 + 降階模型推算整個頻段", "求解中：1 次 (較吃記憶體)", C_PT)
            self._footer(p, "這一次求解比單一頻點的 Discrete 慢、記憶體需求也更大", C_MUTED)
        else:
            k = min(1.0, (t - 2200) / 2200)
            half = (F_MAX - F_MIN) / 2 * k
            mask = (self.fd >= center - half) & (self.fd <= center + half)
            if mask.sum() >= 2:
                self._curve(p, self.fd[mask], self.yd[mask], QPen(C_FIT, 2.2))
            self._dots(p, [center])
            self._header(p, "Fast：一次大型求解 + 降階模型推算整個頻段", "完整求解 1 次", C_OK if k >= 1 else C_FIT)
            if k >= 1:
                self._footer(p, "✔ 整個頻段 (含每個頻率的場) 都由模型得到；高 Q 共振的極點會被直接捕捉", C_OK)
            else:
                self._footer(p, "模型從中心頻率往兩側展開 (頻寬越寬，離中心越遠準確度越需要注意)", C_MUTED)


# ======================================================================
# 流程總覽圖
# ======================================================================
class FlowView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(190)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), C_BG)
        w = self.width()
        boxes = [("初始網格", "Lambda 細化\n(λ/3)"), ("求解", "在 Adaptive\n頻率解場"),
                 ("估計誤差", "找出場變化\n最劇烈的元素"), ("細化網格", "細分誤差最大的\nX% 元素"),
                 ("收斂？", "ΔS < Max ΔS\n連續 N 次"), ("Sweep", "用固定網格\n算整個頻段")]
        n = len(boxes)
        bw, bh = min(140, (w - 40) / n - 16), 78
        gap = (w - 20 - n * bw) / (n - 1)
        y = 40
        rects = []
        for i, (title, sub) in enumerate(boxes):
            r = QRectF(10 + i * (bw + gap), y, bw, bh)
            rects.append(r)
            col = QColor({0: THEME["BOX_START"], 5: THEME["BOX_END"], 4: THEME["BOX_CHECK"]}.get(i, THEME["BOX_STEP"]))
            p.setPen(QPen(C_AXIS, 1.2)); p.setBrush(col)
            p.drawRoundedRect(r, 8, 8)
            p.setPen(C_TEXT); p.setFont(_font(10, True))
            p.drawText(QRectF(r.left(), r.top() + 6, r.width(), 20), Qt.AlignmentFlag.AlignCenter, title)
            p.setFont(_font(8)); p.setPen(C_MUTED)
            p.drawText(QRectF(r.left() + 4, r.top() + 28, r.width() - 8, 46), Qt.AlignmentFlag.AlignCenter, sub)
        p.setPen(QPen(C_AXIS, 1.6))
        for a, b in zip(rects, rects[1:]):
            y0 = a.center().y()
            p.drawLine(QPointF(a.right(), y0), QPointF(b.left() - 2, y0))
            p.drawPolygon(QPolygonF([QPointF(b.left() - 2, y0), QPointF(b.left() - 9, y0 - 4), QPointF(b.left() - 9, y0 + 4)]))
        # 回圈箭頭：收斂？否 → 求解
        a, b = rects[4], rects[1]
        yb = a.bottom() + 36
        path = QPainterPath(QPointF(a.center().x(), a.bottom()))
        path.lineTo(QPointF(a.center().x(), yb)); path.lineTo(QPointF(b.center().x(), yb)); path.lineTo(QPointF(b.center().x(), b.bottom() + 6))
        p.setPen(QPen(C_THR, 1.6)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(path)
        p.setBrush(C_THR)
        p.drawPolygon(QPolygonF([QPointF(b.center().x(), b.bottom() + 1), QPointF(b.center().x() - 4, b.bottom() + 9),
                                 QPointF(b.center().x() + 4, b.bottom() + 9)]))
        p.setFont(_font(9, True))
        p.drawText(QRectF(b.center().x(), yb + 2, a.center().x() - b.center().x(), 18), Qt.AlignmentFlag.AlignCenter,
                   "否 → 下一個 Pass (達到最大迭代次數也會停止)")
        p.setPen(C_OK)
        p.drawText(QRectF(rects[4].right(), rects[4].center().y() - 22, gap, 18), Qt.AlignmentFlag.AlignCenter, "是")
        p.setPen(C_TEXT); p.setFont(_font(10, True))
        p.drawText(QRectF(10, 6, w / 2, 22), Qt.AlignmentFlag.AlignLeft, "Adaptive 自適應網格")
        p.drawText(QRectF(rects[5].left() - 20, 6, rects[5].width() + 30, 22), Qt.AlignmentFlag.AlignCenter, "Frequency Sweep")
        p.setPen(QPen(C_MUTED, 1, Qt.PenStyle.DashLine))
        xs = (rects[4].right() + rects[5].left()) / 2
        p.drawLine(QPointF(xs, 10), QPointF(xs, self.height() - 8))
        p.end()


# ======================================================================
# 文字內容
# ======================================================================
_CSS = """
<style>
body { font-size: 10.5pt; color: #263238; line-height: 150%; }
h3 { color: #1565C0; margin-bottom: 4px; }
h4 { color: #37474F; margin-bottom: 2px; margin-top: 10px; }
.tag { background: #E3F2FD; color: #1565C0; padding: 1px 6px; }
.warn { color: #C62828; }
.ok { color: #2E7D32; }
.muted { color: #78909C; font-size: 9pt; }
table { border-collapse: collapse; }
td, th { border: 1px solid #CFD8DC; padding: 5px 8px; vertical-align: top; }
th { background: #ECEFF1; }
</style>
"""

HTML_OVERVIEW = _CSS + """
<h3>HFSS 求解分成兩個階段</h3>
<p>HFSS 用<b>有限元素法 (FEM)</b> 把結構切成許多小四面體，在每個四面體裡近似電磁場。
網格要切多細才夠準，事先並不知道，所以分兩步：</p>
<ol>
<li><b>Adaptive (自適應網格)</b>：在一個或幾個「自適應頻率」上反覆「求解 → 找誤差 → 細化」，直到 S 參數不再變化。
    這一步決定了<b>網格</b>。</li>
<li><b>Frequency Sweep (掃頻)</b>：網格固定下來，用它算出整個頻段 (例如 2–9 GHz) 的 S 參數。
    這一步決定了<b>頻率響應怎麼取得</b>。</li>
</ol>
<p><b>關鍵觀念</b>：網格只在「自適應頻率」上被驗證過。掃頻時整個頻段都用同一套網格，
所以 <span class="tag">Adaptive 模式</span> 決定「哪些頻率的準確度有保證」，
<span class="tag">Sweep 模式</span> 決定「頻段內要實際解幾次、中間的點怎麼得到」。兩者要搭配著選。</p>
<p class="muted">各分頁的動畫為原理示意：網格用 2D 三角形代表 3D 四面體；數值不代表實際模擬結果。</p>
"""

HTML_ADAPTIVE = {
    "common": _CSS + """
<h3>自適應網格的每一個 Pass</h3>
<ol>
<li>用目前網格在自適應頻率<b>求解一次場</b>。</li>
<li>估計每個元素的誤差：場在元素裡變化越劇烈，誤差越大 — 通常在<b>金屬邊緣、轉角、間隙、介質交界</b>。</li>
<li>把誤差最大的一部分元素細分。比例 = <span class="tag">每次網格細化 (%)</span> (預設 30%)。</li>
<li>用新網格再解一次，比較前後兩次的 S 參數：<br>
    <b>ΔS = max |S<sub>ij</sub><sup>(本次)</sup> − S<sub>ij</sub><sup>(上次)</sup>|</b> (所有 Port 組合取最大)。</li>
<li>ΔS 連續 <span class="tag">最小連續收斂次數</span> 次都小於 <span class="tag">Max Delta S</span> → 收斂。
    或達到 <span class="tag">最大迭代次數</span> 就停止 (此時是<b>未收斂</b>，Message Manager 會警告)。</li>
</ol>
<p><b>參數怎麼調</b>：Max Delta S 0.02 是一般用途；做精密比對可用 0.01 (較慢)。
細化 % 越大每次長得越快、pass 數較少，但最終網格可能比需要的大。
最小連續收斂 2 次可以避免「剛好一次變化小」的假收斂。</p>
""",
    "Single": """
<h4>🎯 Single — 單一頻率自適應 <span class="ok">(最快、最常用)</span></h4>
<p>只在 <b>1 個頻率</b>收斂網格，每個 pass 只解 1 次。</p>
<p><b>為什麼通常選最高頻</b>：頻率越高、波長越短，需要的網格越細。在最高頻收斂的網格，
對較低頻率 (波長更長) 通常也夠細，所以一次就能涵蓋整個頻段。</p>
<p><b>建議值</b>：Sweep 結束頻率 (或略高一點)。</p>
<p><b>適用</b>：微帶線、共面波導、耦合線、一般被動元件 — 響應平滑、沒有尖銳共振的結構。</p>
<p class="warn">⚠ 限制：若結構在較低頻率有高 Q 共振 (動畫中 6.3 GHz 的凹陷)，共振時的場分佈和最高頻完全不同，
網格沒有在那裡被驗證 → 共振頻率或深度可能有誤差。這時改用 Multi-Frequencies 把共振頻率加進去。</p>
""",
    "Multi-Frequencies": """
<h4>📊 Multi-Frequencies — 多頻點自適應</h4>
<p>在<b>多個指定頻率</b>上同時收斂。每個 pass 在每個頻率各解一次，細化時綜合所有頻率的誤差，
最終網格要讓<b>每一個頻率</b>都滿足收斂條件。</p>
<p><b>建議值</b>：頻段頭、中、尾 + 已知的共振頻率。例如 <code>2.5GHz, 6.3GHz, 9GHz</code>。</p>
<p><b>成本</b>：每個 pass 的時間 ≈ 頻點數 × Single；最終網格通常也比 Single 大。</p>
<p><b>適用</b>：濾波器 (通帶與阻帶都要準)、雙頻/多頻天線、多工器、已知共振位置的結構。</p>
""",
    "Broadband": """
<h4>🌌 Broadband — 寬頻自適應 <span class="warn">(最耗時)</span></h4>
<p>只給頻段上下限 (本工具自動使用 Sweep 起始/結束頻率)，由 HFSS 在頻段內<b>自動挑選多個自適應頻率</b>，
並要求它們都收斂。動畫中頻點逐步增加的方式為示意。</p>
<p><b>優點</b>：不需要事先知道共振在哪裡；對未知結構最保險。</p>
<p><b>缺點</b>：每個 pass 要解多個頻率，且低頻端也要收斂，總時間通常是 Single 的數倍。</p>
<p><b>適用</b>：超寬頻 (UWB) 天線、SI 分析、特性未知的複雜結構。</p>
<p class="warn">⚠ 若 Wave Port 太大，高頻端會激發高階模態，ΔS 一直跳動、很難收斂，會一路細化到最大迭代次數。
先看工具的「高階模態警告」，必要時用 Port 縮放基數。</p>
""",
}

HTML_SWEEP = {
    "common": _CSS + """
<h3>掃頻：網格固定之後，怎麼得到整個頻段</h3>
<p>三種方法的差別在於：<b>真正做矩陣求解的頻點有幾個</b>，以及<b>中間的頻點怎麼得到</b>。
動畫中灰色虛線是「真實響應」(模擬前未知)，橘點是實際求解的頻點，藍線是該方法最後給你的曲線。</p>
""",
    "Discrete": """
<h4>🧱 Discrete — 離散掃頻 <span class="warn">(最精準但最慢)</span></h4>
<p>對<b>每一個頻點</b>都做一次完整的 FEM 矩陣求解。每個點都是該網格下的精確解，但點與點之間沒有資訊。</p>
<ul>
<li><b>時間 ∝ 點數</b>：701 點 ≈ 701 次求解。HPC 的 Tasks &gt; 1 可以把頻點分給多個任務平行計算。</li>
<li>可以保存<b>每個頻點的場</b> (進階設定「S 參數掃頻存場」) — 但檔案會非常大。</li>
<li class="warn">點數不夠密時，窄共振會落在兩點之間 (動畫中凹陷只量到約 −3 dB，真實約 −26 dB)。</li>
</ul>
<p><b>適用</b>：少量關鍵頻點的驗證、需要特定頻率場型時 (本工具的「場型模擬」就是自動建立單點 Discrete)。</p>
<p class="muted">不建議把 Discrete 用在幾百點的寬頻 S 參數 — 這是最慢的組合。</p>
""",
    "Interpolating": """
<h4>🚀 Interpolating — 插值掃頻 <span class="ok">(寬頻 S 參數首選)</span></h4>
<p>先解頻段兩端與中間幾個點，用<b>有理函數</b>擬合整條曲線；接著在<b>擬合誤差最大的地方</b>再補一個求解點，
重複直到誤差 &lt; <span class="tag">插值誤差容許</span> (預設 0.5%) 或達到 <span class="tag">插值最大求解數</span> (預設 250)。</p>
<ul>
<li>通常 <b>10~40 次</b>求解就能產生數百到數千點的平滑曲線。</li>
<li>求解點會<b>自動集中在響應變化劇烈的地方</b> (動畫中點聚集在凹陷附近)。</li>
<li>預設只保留 S 參數，不存場。</li>
<li>「強制被動性」可避免插值出 |S| &gt; 1 的非物理結果；「強制因果性」只在時域分析時需要。</li>
</ul>
<p class="warn">⚠ 極高 Q 的超窄峰若一開始完全沒被任何取樣點「看到」，仍可能被漏掉。重要頻點可另外用 Discrete 驗證。</p>
<p class="muted">動畫用單調三次插值示意；HFSS 實際使用有理函數擬合，捕捉共振所需的點通常更少。</p>
""",
    "Fast": """
<h4>⚡ Fast — 快速掃頻 (ALPS)</h4>
<p>在<b>中心頻率做一次較大型的求解</b>，並建立降階模型 (Adaptive Lanczos-Padé Sweep)，
用它推算整個頻段的響應 — <b>包含每個頻率的場</b>。</p>
<ul>
<li>只有 1 次求解，但這次的記憶體與時間都比單點求解大很多；大型模型可能記憶體不足。</li>
<li>模型直接捕捉系統的極點與零點，所以對<b>高 Q 共振</b> (濾波器、共振腔) 特別準確。</li>
<li>頻寬很寬時，離中心頻率越遠越需要檢查準確度。</li>
</ul>
<p><b>適用</b>：窄頻、高 Q 結構；想看任意頻率的場，又不想跑大量 Discrete。</p>
""",
}

HTML_CHOOSE = _CSS + """
<h3>怎麼選：常見情境建議</h3>
<table width="100%">
<tr><th>情境</th><th>Adaptive</th><th>Sweep</th><th>說明</th></tr>
<tr><td>一般 MS / CPW 傳輸線、2–9 GHz</td><td><b>Single</b> @ 最高頻</td><td><b>Interpolating</b></td>
    <td>最快、最常用的組合。</td></tr>
<tr><td>有已知共振的濾波器 / 共振器</td><td><b>Multi</b> (頭、尾 + 共振頻率)</td><td><b>Interpolating</b> 或 <b>Fast</b></td>
    <td>把共振頻率放進自適應頻點，網格才會在共振時被驗證。</td></tr>
<tr><td>高 Q、窄頻共振腔</td><td>Single / Multi @ 共振附近</td><td><b>Fast</b></td>
    <td>一次求解就能精準捕捉尖峰，也有場可看。</td></tr>
<tr><td>特性未知、超寬頻</td><td><b>Broadband</b></td><td><b>Interpolating</b></td>
    <td>最保險但最慢；注意 Port 高階模。</td></tr>
<tr><td>要看場型 / 做 GIF 動畫</td><td>沿用 S 參數的網格</td><td>(工具自動建立單點 Discrete)</td>
    <td>勾選「場型沿用 S 參數的收斂網格」可省下重新 Adaptive 的時間。</td></tr>
<tr><td>最終驗證關鍵頻點</td><td>同上</td><td>Discrete 少數幾點</td>
    <td>和 Interpolating 結果比對，確認沒有漏掉窄峰。</td></tr>
</table>
<h4>相對時間 (粗略、同一網格下)</h4>
<table>
<tr><th>組合</th><th>自適應求解次數</th><th>掃頻求解次數</th></tr>
<tr><td>Single + Interpolating</td><td>≈ pass 數</td><td>≈ 10~40</td></tr>
<tr><td>Multi (3 頻) + Interpolating</td><td>≈ 3 × pass 數</td><td>≈ 10~40</td></tr>
<tr><td>Broadband + Interpolating</td><td>≈ 多頻 × pass 數</td><td>≈ 10~40</td></tr>
<tr><td class="warn">Broadband + Discrete 701 點</td><td>≈ 多頻 × pass 數</td><td class="warn">701</td></tr>
</table>
<p class="muted">實際時間還取決於網格大小、Port 數、HPC 設定。</p>
"""

HTML_PARAMS = _CSS + """
<h3>參數速查</h3>
<h4>主控制面板 — 求解器與掃頻</h4>
<table width="100%">
<tr><th width="24%">參數</th><th>意義</th><th width="24%">建議</th></tr>
<tr><td>Adaptive 模式 / 頻率</td><td>在哪些頻率收斂網格 (見 Adaptive 分頁)</td><td>Single @ 最高頻</td></tr>
<tr><td>Sweep 模式</td><td>怎麼取得整個頻段 (見 Sweep 分頁)</td><td>Interpolating</td></tr>
<tr><td>Sweep 起始 / 結束頻率</td><td>輸出 S 參數的頻段；Broadband 也用它當自適應範圍</td><td>—</td></tr>
<tr><td>空氣腔設定頻率</td><td>決定空氣腔大小 (約 λ/4 距離)；頻率越低空氣腔越大</td><td>頻段中心或最低頻</td></tr>
<tr><td>寬頻掃頻點數</td><td>輸出點數。Interpolating 時幾乎不影響時間；Discrete 時時間 ∝ 點數</td><td>401~1001</td></tr>
<tr><td>最大迭代次數</td><td>自適應最多幾個 pass</td><td>15~30</td></tr>
<tr><td>Max Delta S</td><td>收斂門檻</td><td>0.02 (精密 0.01)</td></tr>
<tr><td>每次網格細化 (%)</td><td>每個 pass 細分多少比例的元素</td><td>30</td></tr>
<tr><td>最小連續收斂次數</td><td>ΔS 要連續幾次低於門檻才算收斂</td><td>2</td></tr>
<tr><td>場型沿用 S 參數網格</td><td>場型模擬直接用已收斂網格，只加算單點掃頻</td><td>開啟</td></tr>
</table>
<h4>進階設定 — Adaptive</h4>
<table width="100%">
<tr><td width="24%">最少 Pass 數</td><td>至少跑幾個 pass 才允許收斂，避免過早停止</td><td width="24%">1 (強共振結構 3~4)</td></tr>
<tr><td>基底函數階數</td><td>元素內場的多項式階數。Mixed 會自動在大元素用高階、小元素用低階</td><td>Mixed Order</td></tr>
<tr><td>Lambda 細化 / Target</td><td>自適應前先把網格切到 λ×Target 以下；可減少 pass 數</td><td>開啟 / 0.3333</td></tr>
<tr><td>矩陣求解器</td><td>Direct 穩定但吃記憶體；Iterative 省記憶體，適合大型模型</td><td>Direct</td></tr>
</table>
<h4>進階設定 — Sweep</h4>
<table width="100%">
<tr><td width="24%">插值誤差容許 (%)</td><td>Interpolating 的停止條件</td><td width="24%">0.5</td></tr>
<tr><td>插值最大求解數</td><td>Interpolating 最多求解幾個頻點</td><td>250</td></tr>
<tr><td>強制被動性</td><td>保證 |S| ≤ 1，輸出給電路模擬時建議開啟</td><td>開啟</td></tr>
<tr><td>強制因果性</td><td>時域分析 (TDR、眼圖) 才需要</td><td>關閉</td></tr>
<tr><td>S 參數掃頻存場</td><td>保存掃頻每個頻點的場；檔案非常大</td><td>關閉</td></tr>
</table>
<h4>進階設定 — Wave Port / 邊界 / 材料</h4>
<table width="100%">
<tr><td width="24%">模態數</td><td>每個 Port 求解的模態數；傳輸線 1 即可</td><td width="24%">1</td></tr>
<tr><td>參考阻抗 / 重正規化</td><td>S 參數相對於哪個阻抗；與 VNA 量測比較時用 50 Ω 並開啟重正規化</td><td>50 Ω / 開啟</td></tr>
<tr><td>特性阻抗定義</td><td>Zpi (功率-電流)、Zpv (功率-電壓)、Zvi (電壓-電流)</td><td>Zpi</td></tr>
<tr><td>De-embed</td><td>把參考面往結構內移，扣掉 Port 到 DUT 之間的線長</td><td>0</td></tr>
<tr><td>開放邊界類型</td><td>Radiation 一般用途；PML 吸收更好，適合輻射強的結構</td><td>Radiation</td></tr>
<tr><td>無限大地平面</td><td>空氣腔底面視為無限大金屬地，可減少網格</td><td>關閉</td></tr>
<tr><td>εr / tanδ 覆寫</td><td>複製一份材料並改用自訂值 (板材批次、量測擬合)</td><td>留空</td></tr>
</table>
<h4>HPC</h4>
<p>Tasks = 1 時 AEDT 自動分配；Discrete / Interpolating 掃頻可設 Tasks &gt; 1 讓多個頻點平行求解
(每個任務核心數 = 總核心 ÷ Tasks，記憶體用量約 × Tasks)。GPU 需要對應授權。</p>
"""


def _css():
    t = THEME
    return f"""
<style>
body {{ font-size: 10.5pt; color: {t['TEXT']}; line-height: 150%; }}
h3 {{ color: {t['H3']}; margin-bottom: 4px; }}
h4 {{ color: {t['H4']}; margin-bottom: 2px; margin-top: 10px; }}
a {{ color: {t['LINK']}; }}
code {{ color: {t['TAG_FG']}; }}
.tag {{ background: {t['TAG_BG']}; color: {t['TAG_FG']}; padding: 1px 6px; }}
.warn {{ color: {t['WARN']}; }}
.ok {{ color: {t['OKT']}; }}
.muted {{ color: {t['MUTED']}; font-size: 9pt; }}
table {{ border-collapse: collapse; }}
td, th {{ border: 1px solid {t['TD_BORDER']}; padding: 5px 8px; vertical-align: top; color: {t['TEXT']}; }}
th {{ background: {t['TH_BG']}; color: {t['TH_FG']}; }}
</style>
"""


def _themed(html):
    """把內容裡的預設 (淺色) CSS 換成目前主題的 CSS。"""
    return _css() + html.replace(_CSS, "")


# ======================================================================
# 對話框
# ======================================================================
def _segmented(options, on_change, default):
    box = QHBoxLayout()
    group = QButtonGroup(box)
    group.setExclusive(True)
    btns = {}
    for opt in options:
        b = QPushButton(opt)
        b.setCheckable(True)
        b.setStyleSheet("QPushButton { padding: 6px 14px; font-weight: bold; }"
                        "QPushButton:checked { background: #1565C0; color: white; }")
        group.addButton(b)
        box.addWidget(b)
        btns[opt] = b
        b.clicked.connect(lambda _=False, o=opt: on_change(o))
    btns[default].setChecked(True)
    box.addStretch()
    return box, btns


_BROWSERS = []


def _browser(html):
    tb = QTextBrowser()
    tb.setOpenExternalLinks(True)
    _set_html(tb, html)
    return tb


def _set_html(tb, html):
    tb._raw_html = html
    tb.setHtml(_themed(html))


class ModeHelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("求解器模式說明 — Adaptive 與 Sweep")
        self.resize(1080, 800)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self._dark = is_dark_palette(parent)
        apply_theme(self._dark)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # ① 總覽
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(FlowView())
        v.addWidget(_browser(HTML_OVERVIEW), 1)
        tabs.addTab(w, "① 整體流程")

        # ② Adaptive
        w = QWidget(); v = QVBoxLayout(w)
        self.timeline = AdaptiveTimeline(self)
        seg, self._adaptive_btns = _segmented(["Single", "Multi-Frequencies", "Broadband"], self._set_adaptive_mode, "Single")
        seg.addWidget(self._play_button(self.timeline))
        rb = QPushButton("⟲ 重播"); rb.clicked.connect(self.timeline.restart); seg.addWidget(rb)
        v.addLayout(seg)
        top = QHBoxLayout()
        top.addWidget(MeshView(self.timeline), 3)
        top.addWidget(ConvergenceChart(self.timeline), 2)
        v.addLayout(top, 5)
        self.freq_view = FreqPlacementView(self.timeline)
        v.addWidget(self.freq_view, 2)
        self.adaptive_text = _browser("")
        v.addWidget(self.adaptive_text, 4)
        tabs.addTab(w, "② Adaptive 自適應網格")
        self._set_adaptive_mode("Single")

        # ③ Sweep
        w = QWidget(); v = QVBoxLayout(w)
        self.sweep_view = SweepView()
        seg, self._sweep_btns = _segmented(["Discrete", "Interpolating", "Fast"], self._set_sweep_mode, "Interpolating")
        seg.addWidget(self._play_button(self.sweep_view))
        rb = QPushButton("⟲ 重播"); rb.clicked.connect(self.sweep_view.restart); seg.addWidget(rb)
        v.addLayout(seg)
        v.addWidget(self.sweep_view, 5)
        self.sweep_text = _browser("")
        v.addWidget(self.sweep_text, 4)
        tabs.addTab(w, "③ Sweep 掃頻")
        self._set_sweep_mode("Interpolating")

        # ④ ⑤
        tabs.addTab(_browser(HTML_CHOOSE), "④ 如何選擇")
        tabs.addTab(_browser(HTML_PARAMS), "⑤ 參數速查")

        close = QPushButton("關閉")
        close.clicked.connect(self.close)
        row = QHBoxLayout(); row.addStretch(); row.addWidget(close)
        lay.addLayout(row)

    def _play_button(self, target):
        b = QPushButton("⏸ 暫停")

        def toggle():
            target.playing = not target.playing
            b.setText("⏸ 暫停" if target.playing else "▶ 播放")
        b.clicked.connect(toggle)
        return b

    def _set_adaptive_mode(self, mode):
        self._adaptive_btns[mode].setChecked(True)
        self.freq_view.set_mode(mode)
        self.timeline.restart()
        # 先顯示所選模式的說明，再附上共通的 pass 流程
        _set_html(self.adaptive_text, HTML_ADAPTIVE[mode] + "<hr>" + HTML_ADAPTIVE["common"].replace(_CSS, ""))

    def _set_sweep_mode(self, mode):
        self._sweep_btns[mode].setChecked(True)
        self.sweep_view.set_mode(mode)
        _set_html(self.sweep_text, HTML_SWEEP[mode] + "<hr>" + HTML_SWEEP["common"].replace(_CSS, ""))

    def changeEvent(self, e):
        """系統切換淺色 / 深色時，重新套用配色。"""
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange, QEvent.Type.StyleChange):
            self.refresh_theme()
        super().changeEvent(e)

    def refresh_theme(self):
        dark = is_dark_palette(self)
        if dark == getattr(self, "_dark", None):
            return
        self._dark = dark
        apply_theme(dark)
        for tb in self.findChildren(QTextBrowser):
            if hasattr(tb, "_raw_html"):
                tb.setHtml(_themed(tb._raw_html))
        for w in self.findChildren(QWidget):
            if hasattr(w, "_cache"):
                w._cache.clear()
            w.update()
