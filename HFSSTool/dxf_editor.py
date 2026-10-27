"""DXF 編輯器（v3.3）：改板材大小、用節點畫 / 改微波線、螺絲孔、過孔，存成 HFSS 建模用的 DXF。

v3.9（參考 AutoCAD）：縮放以游標為中心（不再跳回原位）；點節點就能在左邊輸入座標 / 長度 / 角度；
標註（水平 / 垂直 / 對齊，存成 DXF 標註）、量距離；物件鎖點（F3：端點、中點、圓心、四分點、輪廓角）、正交（F8）；
底下的指令列可以打座標（x,y、@dx,dy、@長度<角度、直接打長度）與指令（L、REC、C、DIM、DI、M、CO、RO、MI、Z、U…）；
移動 / 複製 / 旋轉 / 鏡像；MS ⇄ CPW 一鍵轉換（依 50 Ω 算線寬 / 間隙，可自動加過孔）。

    ┌──────────┬──────────────────────────────┬──────────┐
    │ 設計      │  畫布（mm，Y 軸向上，格線吸附）   │ 屬性      │
    │ 模式 / 板材│  左鍵：選取、拖節點、拖整個圖形    │ 線寬 / 間隙│
    │ 新微波線  │  雙擊線段：加節點；右鍵：刪節點    │ 節點表格   │
    │ 圖層      │  滾輪：縮放；中鍵 / 空白鍵：平移   │          │
    └──────────┴──────────────────────────────┴──────────┘

工具：F1 選取、F2 平移、P 微波線（點節點，雙擊或 Enter 結束，Shift = 0/45/90°）、G 多邊形、R 矩形、C 圓（螺絲孔 / 過孔）、
D 標註、M 量距離、Z 框選放大。
資料與存檔在 dxf_model.py。
"""
from __future__ import annotations

import math
import os
from typing import Callable, List, Optional, Tuple

import re

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (QAction, QActionGroup, QBrush, QColor, QFont, QKeySequence, QPainter, QPainterPath,
                         QPainterPathStroker, QPen, QPolygonF, QShortcut, QTransform)
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDockWidget, QDoubleSpinBox, QFileDialog,
                             QFormLayout, QGraphicsItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView,
                             QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QMainWindow, QMenu, QMessageBox, QPushButton, QRadioButton, QTableWidget,
                             QTableWidgetItem, QToolBar, QToolButton, QVBoxLayout, QWidget)

import dxf_geom as G
import tline
from dxf_model import BOARD_LAYERS, DIM_LAYER, LAYER_INFO, LAYERS, PATH_LAYER, Design, Dim, DxfError, Shape

FILL = {"b": "#7CB342", "db": "#7CB342", "ub": "#FFA726", "g": "#E53935", "h": "#9E9E9E", "v": "#1E88E5"}
ALPHA = {"b": 70, "db": 70, "ub": 110, "g": 170, "h": 200, "v": 200}
DRAW_ORDER = ("b", "db", "ub", "g", "h", "v")          # 後面的畫在上面、先被點到
NODE_PX = 7
SNAP_EDGE_PX = 10
OSNAP_PX = 12
OSNAP_RANK = {"end": 0, "cen": 1, "mid": 2, "edge": 3, "qua": 4}
OSNAP_NAME = {"end": "端點", "cen": "圓心", "mid": "中點", "edge": "輪廓角", "qua": "四分點"}
ZOOM_MIN, ZOOM_MAX = 0.02, 5000.0          # px / mm（太大會超過捲軸的整數範圍 → 畫面跳回中間）
DRAW_TOOLS = ("path", "poly", "rect", "circle", "arc", "bezier", "bezpoly", "dim", "dist")
DIM_COLOR = "#00ACC1"


def _qc(name: str, alpha: int = 255) -> QColor:
    c = QColor(name)
    c.setAlpha(alpha)
    return c


def _poly(pts) -> QPolygonF:
    return QPolygonF([QPointF(x, y) for x, y in pts])


def _fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:g}"


# =====================================================================================================
class Canvas(QGraphicsView):
    """畫布：自己處理滑鼠（選取、拖節點、畫圖），每次改動重畫整個場景（圖形不多，簡單又不會不同步）。"""

    changed = pyqtSignal()              # 模型改了（已存復原點）
    selection_changed = pyqtSignal()
    cursor_moved = pyqtSignal(float, float, str)

    def __init__(self, editor: "DxfEditor") -> None:
        super().__init__()
        self.ed = editor
        self.setScene(QGraphicsScene(self))
        self.scene().setSceneRect(-1e4, -1e4, 2e4, 2e4)   # ±10 m：平移不會被卡住，放大到最大也不會超出捲軸範圍
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # v3.9：縮放自己算（以游標為中心），不靠 AnchorUnderMouse（沒有滑鼠移動事件時會以畫面中心縮放，看起來像被拉回原位）
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)    # 旁邊的面板變寬變窄時畫面不跟著跳
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.scale(10, -10)                         # 1 mm = 10 px，Y 軸向上
        self.tool = "select"
        self.selected: Optional[int] = None
        self.drag: Optional[dict] = None             # {"kind": node/body/pan, ...}
        self.draft: List[Tuple[float, float]] = []   # 畫圖中的節點
        self.cursor = (0.0, 0.0)
        self.space = False
        self.active_node: Optional[int] = None      # v3.9：點選的節點（左邊可以打座標）
        self.selected_dim: Optional[int] = None
        self.osnap_hit = None                       # ((x, y), 種類)
        self.measure = None                         # 量距離的結果 ((x1, y1), (x2, y2))
        self._snap_cache = None                     # [(點, 種類, 圖形索引)]
        self._zoom_box = None

    # ---- 座標 ---------------------------------------------------------------------------------
    @property
    def design(self) -> Design:
        return self.ed.design

    def px(self) -> float:
        """1 px 是幾 mm。"""
        return 1.0 / max(abs(self.transform().m11()), 1e-12)

    def snap_candidates(self):
        if self._snap_cache is None:
            out = []
            for idx, s in enumerate(self.design.shapes):
                if len(s.pts) >= 1:
                    out += [(p, k, idx) for p, k in s.snap_points()]
            try:
                gen = self.design.generated()
            except Exception:  # noqa: BLE001
                gen = {}
            for verts in gen.get("g", []) + gen.get("ub", []):
                out += [((v[0], v[1]), "edge", -1) for v in verts]
            self._snap_cache = out
        return self._snap_cache

    def osnap(self, x: float, y: float, exclude: Optional[int] = None):
        """游標附近（OSNAP_PX 內）最好的鎖點；端點 > 圓心 > 中點 > 輪廓角 > 四分點。"""
        if not self.ed.osnap_on():
            return None
        tol = OSNAP_PX * self.px()
        best = None
        for (px_, py_), kind, idx in self.snap_candidates():
            if exclude is not None and idx == exclude:
                continue
            d = math.hypot(px_ - x, py_ - y)
            if d <= tol:
                key = (OSNAP_RANK[kind], d)
                if best is None or key < best[0]:
                    best = (key, ((px_, py_), kind))
        return best[1] if best else None

    def snap(self, x: float, y: float, free: bool = False, exclude: Optional[int] = None) -> Tuple[float, float]:
        self.osnap_hit = None
        if free:
            return x, y
        hit = self.osnap(x, y, exclude)
        if hit is not None:
            self.osnap_hit = hit
            return hit[0]
        bb = self.design.board_bounds()
        tol = SNAP_EDGE_PX * self.px()
        if bb:                                      # 先吸附板邊（Wave Port 要在板邊）
            for edge in (bb[0], bb[2]):
                if abs(x - edge) < tol:
                    x = edge
                    break
            for edge in (bb[1], bb[3]):
                if abs(y - edge) < tol:
                    y = edge
                    break
        step = self.ed.grid_step()
        if step > 0:
            if not bb or x not in (bb[0], bb[2]):
                x = round(x / step) * step
            if not bb or y not in (bb[1], bb[3]):
                y = round(y / step) * step
        return round(x, 9), round(y, 9)

    def fit(self) -> None:
        x0, y0, x1, y1 = self.design.bounds()
        w, h = max(x1 - x0, 1e-3), max(y1 - y0, 1e-3)
        r = QRectF(x0 - w * 0.08, y0 - h * 0.08, w * 1.16, h * 1.16)
        self.resetTransform()
        self.scale(1, -1)
        self.fitInView(r, Qt.AspectRatioMode.KeepAspectRatio)
        self.rebuild()

    def zoom(self, f: float, view_pos=None) -> None:
        """以 view_pos（畫面座標，預設中心）為中心縮放：那一點在縮放前後都在游標下面。"""
        cur = abs(self.transform().m11())
        f = max(ZOOM_MIN / cur, min(ZOOM_MAX / cur, f))
        if abs(f - 1) < 1e-9:
            return
        vp = view_pos if view_pos is not None else QPointF(self.viewport().rect().center())
        vp = QPointF(vp)
        before = self.mapToScene(vp.toPoint())
        self.scale(f, f)
        after = self.mapFromScene(before)
        d = QPointF(after) - vp
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + int(round(d.x())))
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + int(round(d.y())))
        self.rebuild()

    def zoom_rect(self, r: QRectF) -> None:
        if r.width() <= 0 or r.height() <= 0:
            return
        self.resetTransform()
        self.scale(1, -1)
        self.fitInView(r, Qt.AspectRatioMode.KeepAspectRatio)
        cur = abs(self.transform().m11())
        if cur > ZOOM_MAX:
            self.zoom(ZOOM_MAX / cur)
        self.rebuild()

    # ---- 畫 ---------------------------------------------------------------------------------
    def _pen(self, color, width: float = 1.0, style=Qt.PenStyle.SolidLine) -> QPen:
        p = QPen(QColor(color), width, style)
        p.setCosmetic(True)
        return p

    def shape_path(self, s: Shape) -> QPainterPath:
        path = QPainterPath()
        if s.kind == "circle":
            (x, y), r = s.pts[0], s.r
            path.addEllipse(QPointF(x, y), r, r)
            return path
        ring = s.ring() if len(s.pts) >= 2 else []
        if len(ring) >= 3:
            path.addPolygon(_poly(ring))
            path.closeSubpath()
        elif s.pts:                                 # 太短 / 退化：給一條細線讓它點得到
            line = _line_path(s.centerline() or s.pts)
            st = QPainterPathStroker()
            st.setWidth(max(s.width, 6 * self.px()))
            path = st.createStroke(line)
        return path

    def handles(self, s: Shape) -> List[Tuple[str, int, Tuple[float, float]]]:
        """選取的圖形可以拖的點：n = 節點、c = Bézier 控制點、m = 線段中點（拖彎成圓弧）、r = 圓的半徑。"""
        if s.kind == "circle":
            return [("n", 0, s.pts[0]), ("r", 1, (s.pts[0][0] + s.r, s.pts[0][1]))]
        out = [("n" if s.is_anchor(i) else "c", i, p) for i, p in enumerate(s.pts)]
        if not s.bezier:
            s.norm()
            n = len(s.pts)
            for k in range(s.n_segs()):
                a, b = s.pts[k], s.pts[(k + 1) % n]
                out.append(("m", k, G.arc_mid(a, b, s.bulges[k])))
        return out

    def rebuild(self) -> None:
        sc = self.scene()
        sc.clear()
        self._snap_cache = None
        d = self.design
        hidden = self.ed.hidden_layers()
        dark = self.palette().window().color().lightness() < 128
        edge = "#ECEFF1" if dark else "#263238"
        for f in d.foreign:
            if f["layer"] in hidden or len(f["pts"]) < 2:
                continue
            sc.addPath(_line_path(f["pts"]), self._pen("#90A4AE", 1, Qt.PenStyle.DashLine))
        gen = d.generated()
        order = {name: i for i, name in enumerate(DRAW_ORDER)}
        items = sorted(enumerate(d.shapes), key=lambda kv: order.get(kv[1].layer, 9))
        # 自動上地畫在訊號線下面
        if "ub" not in hidden:
            for verts in gen.get("ub", []):
                sc.addPolygon(_poly(G.ring_points(verts)), self._pen(FILL["ub"], 1, Qt.PenStyle.DashLine),
                              QBrush(_qc(FILL["ub"], 80)))
        for idx, s in items:
            if s.layer in hidden or (s.kind == "path" and PATH_LAYER in hidden and "g" in hidden):
                continue
            color = FILL.get(s.layer, "#8E24AA")
            it = sc.addPath(self.shape_path(s), self._pen(edge if s.layer in ("h", "v") else color, 1),
                            QBrush(_qc(color, ALPHA.get(s.layer, 120))))
            it.setData(0, idx)
            if s.kind == "path" and PATH_LAYER not in hidden and len(s.pts) >= 2:
                sc.addPath(_line_path(s.centerline()), self._pen(edge, 1, Qt.PenStyle.DashDotLine))
        # 埠（微波線端點在板邊）
        r = 5 * self.px()
        for (x, y), _side in d.ports():
            sc.addEllipse(QRectF(x - r, y - r, 2 * r, 2 * r), self._pen("#00C853", 2), QBrush(Qt.BrushStyle.NoBrush))
        # 選取
        if self.selected is not None and self.selected < len(d.shapes):
            s = d.shapes[self.selected]
            sc.addPath(self.shape_path(s), self._pen("#2979FF", 2.5))
            h = NODE_PX / 2 * self.px()
            hs = self.handles(s)
            if s.bezier:                                  # 控制點連到它的錨點
                for kind, i, (x, y) in hs:
                    if kind == "c":
                        a = s.pts[i - 1] if i % 3 == 1 else s.pts[(i + 1) % len(s.pts)]
                        sc.addLine(a[0], a[1], x, y, self._pen("#2979FF", 1, Qt.PenStyle.DashLine))
            last = len(s.pts) - 1
            for kind, i, (x, y) in hs:
                if kind == "m":
                    dm = QPolygonF([QPointF(x, y - h), QPointF(x + h, y), QPointF(x, y + h), QPointF(x - h, y)])
                    sc.addPolygon(dm, self._pen("#F57C00", 1.2), QBrush(QColor("#FFE0B2")))
                elif kind == "c":
                    sc.addEllipse(QRectF(x - h * 0.8, y - h * 0.8, 1.6 * h, 1.6 * h), self._pen("#2979FF", 1.2),
                                  QBrush(QColor("#BBDEFB")))
                else:
                    end = s.kind == "path" and i in (0, last)
                    fill = "#00C853" if end else "#FFFFFF"
                    if s.kind != "circle" and not s.bezier and s.fillets and i < len(s.fillets) and s.fillets[i] > 0:
                        fill = "#CE93D8"                  # 有圓角的節點
                    sc.addRect(QRectF(x - h, y - h, 2 * h, 2 * h), self._pen("#2979FF", 1.5), QBrush(QColor(fill)))
            if self.active_node is not None and self.active_node < len(s.pts):
                x, y = s.pts[self.active_node]
                hh = h * 1.6
                sc.addRect(QRectF(x - hh, y - hh, 2 * hh, 2 * hh), self._pen("#FF1744", 2.5), QBrush(Qt.BrushStyle.NoBrush))
            if self.ed.show_lengths() and s.kind in ("path", "poly") and not s.bezier:
                self._draw_lengths(sc, s)
        # 標註
        if DIM_LAYER not in hidden:
            for i, dm in enumerate(d.dims):
                self._draw_dim(sc, dm, DIM_COLOR if i != self.selected_dim else "#2979FF", i)
        if self.measure:
            self._draw_measure(sc, *self.measure)
        # 畫圖中
        if self.draft:
            self._draw_draft(sc)
        self.viewport().update()

    def _text(self, sc, text: str, pos, color: str, ang: float = 0.0, above: bool = True, data=None):
        it = QGraphicsSimpleTextItem(text)
        f = QFont()
        f.setPointSizeF(9)
        it.setFont(f)
        it.setBrush(QBrush(QColor(color)))
        it.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        br = it.boundingRect()
        it.setTransform(QTransform().rotate(-ang).translate(-br.width() / 2, -br.height() - 1 if above else 1))
        it.setPos(QPointF(*pos))
        if data is not None:
            it.setData(1, data)
        sc.addItem(it)
        return it

    def _arrow(self, sc, tip, toward, color: str) -> None:
        L = 8 * self.px()
        dx, dy = toward[0] - tip[0], toward[1] - tip[1]
        n = math.hypot(dx, dy) or 1e-12
        ux, uy = dx / n, dy / n
        w = L * 0.35
        poly = QPolygonF([QPointF(*tip), QPointF(tip[0] + ux * L - uy * w, tip[1] + uy * L + ux * w),
                          QPointF(tip[0] + ux * L + uy * w, tip[1] + uy * L - ux * w)])
        sc.addPolygon(poly, self._pen(color, 1), QBrush(QColor(color)))

    def _draw_dim(self, sc, dm: Dim, color: str, idx=None) -> None:
        (e1a, e1b), (e2a, e2b), (a, b), mid, ang = dm.geometry()
        pen = self._pen(color, 1)
        ext = 3 * self.px()
        for p0, p1 in ((e1a, e1b), (e2a, e2b)):            # 延伸線：離物件一點點、超過標註線一點點
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            L = math.hypot(dx, dy)
            if L > 1e-12:
                ux, uy = dx / L, dy / L
                sc.addLine(p0[0] + ux * ext, p0[1] + uy * ext, p1[0] + ux * ext * 2, p1[1] + uy * ext * 2, pen)
        it = sc.addLine(a[0], a[1], b[0], b[1], self._pen(color, 1.3))
        if idx is not None:
            it.setData(1, idx)
        if math.hypot(b[0] - a[0], b[1] - a[1]) > 1e-12:
            self._arrow(sc, a, b, color)
            self._arrow(sc, b, a, color)
        prefix = {"h": "↔ ", "v": "↕ ", "a": ""}.get(dm.kind, "")
        self._text(sc, prefix + _fmt(dm.value()), mid, color, ang, data=idx)

    def _draw_measure(self, sc, p1, p2) -> None:
        color = "#FF6D00"
        sc.addLine(p1[0], p1[1], p2[0], p2[1], self._pen(color, 1.5, Qt.PenStyle.DashLine))
        L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        ang = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0]))
        txt = f"{_fmt(L)} mm　ΔX {_fmt(p2[0] - p1[0])}　ΔY {_fmt(p2[1] - p1[1])}　{ang:.2f}°"
        shown = ang if -90 < ang <= 90 else ang - 180 if ang > 0 else ang + 180
        self._text(sc, txt, ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2), color, shown)

    def _draw_lengths(self, sc, s: Shape) -> None:
        """選取的微波線 / 多邊形：每一段的長度（中心線）。"""
        s.norm()
        n = len(s.pts)
        for k in range(s.n_segs()):
            a, b = s.pts[k], s.pts[(k + 1) % n]
            L = G.seg_length(a, b, s.bulges[k])
            m = G.arc_mid(a, b, s.bulges[k])
            ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
            if ang > 90 or ang <= -90:
                ang -= 180 if ang > 0 else -180
            self._text(sc, _fmt(L), m, "#7E57C2", ang, above=False)

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        """物件鎖點的記號（AutoCAD：□ 端點、△ 中點、○ 圓心、◇ 四分點、✕ 輪廓角）、框選放大的框。"""
        if self._zoom_box is not None:
            a, b = self._zoom_box
            pen = QPen(QColor("#2979FF"), 0, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QBrush(_qc("#2979FF", 30)))
            painter.drawRect(QRectF(QPointF(*a), QPointF(*b)).normalized())
        if not self.osnap_hit or self.tool == "pan":
            return
        (x, y), kind = self.osnap_hit
        h = 6 * self.px()
        pen = QPen(QColor("#FFD600"), 2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if kind == "end":
            painter.drawRect(QRectF(x - h, y - h, 2 * h, 2 * h))
        elif kind == "mid":
            painter.drawPolygon(QPolygonF([QPointF(x - h, y - h), QPointF(x + h, y - h), QPointF(x, y + h)]))
        elif kind == "cen":
            painter.drawEllipse(QPointF(x, y), h, h)
        elif kind == "qua":
            painter.drawPolygon(QPolygonF([QPointF(x, y - h), QPointF(x + h, y), QPointF(x, y + h), QPointF(x - h, y)]))
        else:
            painter.drawLine(QPointF(x - h, y - h), QPointF(x + h, y + h))
            painter.drawLine(QPointF(x - h, y + h), QPointF(x + h, y - h))

    def _draft_shape(self) -> Optional[Shape]:
        """畫到一半的圖形（含游標那一點），預覽用。"""
        pts = self.draft + [self.cursor]
        ed = self.ed
        if self.tool == "path" and len(pts) >= 2:
            f = ed.new_fillet.value()
            return Shape("path", "g", pts, width=ed.new_width.value(), fillets=[0.0] + [f] * (len(pts) - 2) + [0.0])
        if self.tool == "bezier" and len(pts) >= 2:
            return Shape("path", "g", G.bezier_from_anchors(pts), width=ed.new_width.value(), smooth="bezier")
        if self.tool == "bezpoly" and len(pts) >= 3:
            return Shape("poly", ed.current_layer(), G.bezier_from_anchors(pts, closed=True), smooth="bezier")
        if self.tool == "arc" and len(pts) == 3:
            return Shape("path", "g", [pts[0], pts[2]], width=ed.new_width.value(),
                         bulges=[G.arc3_bulge(pts[0], pts[1], pts[2])])
        return None

    def dim_kind(self, p1, p2, cur) -> str:
        """自動：游標往上下拉 → 水平距離；往左右拉 → 垂直距離（跟 AutoCAD 的 DIMLINEAR 一樣）。"""
        k = self.ed.dim_mode()
        if k != "auto":
            return k
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        if abs(p2[0] - p1[0]) < 1e-12:
            return "v"
        if abs(p2[1] - p1[1]) < 1e-12:
            return "h"
        x0, x1 = sorted((p1[0], p2[0]))
        y0, y1 = sorted((p1[1], p2[1]))
        if x0 <= cur[0] <= x1 and not (y0 <= cur[1] <= y1):
            return "h"
        if y0 <= cur[1] <= y1 and not (x0 <= cur[0] <= x1):
            return "v"
        return "h" if abs(cur[1] - my) >= abs(cur[0] - mx) else "v"

    def _draw_draft(self, sc) -> None:
        pts = self.draft + [self.cursor]
        pen = self._pen("#2979FF", 1.5, Qt.PenStyle.DashLine)
        if self.tool == "dist":
            self._draw_measure(sc, self.draft[0], self.cursor)
            return
        if self.tool == "dim":
            if len(self.draft) == 1:
                sc.addLine(self.draft[0][0], self.draft[0][1], self.cursor[0], self.cursor[1],
                           self._pen(DIM_COLOR, 1, Qt.PenStyle.DashLine))
            else:
                p1, p2 = self.draft
                self._draw_dim(sc, Dim(self.dim_kind(p1, p2, self.cursor), p1, p2, self.cursor), DIM_COLOR)
            return
        if self.tool == "rect" and len(self.draft) == 1:
            (x0, y0), (x1, y1) = self.draft[0], self.cursor
            sc.addRect(QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)), pen)
            return
        if self.tool == "circle" and len(self.draft) == 1:
            (cx, cy), (x1, y1) = self.draft[0], self.cursor
            rr = math.hypot(x1 - cx, y1 - cy)
            sc.addEllipse(QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr), pen)
            return
        shape = self._draft_shape()
        if shape is not None:
            ring = shape.ring()
            color = FILL["g"] if shape.kind == "path" else FILL.get(shape.layer, "#8E24AA")
            if len(ring) >= 3:
                sc.addPolygon(_poly(ring), self._pen(color, 1), QBrush(_qc(color, 90)))
            if shape.kind == "path":
                sc.addPath(_line_path(shape.centerline()), self._pen("#2979FF", 1, Qt.PenStyle.DashDotLine))
        sc.addPath(_line_path(pts), pen)
        h = 3 * self.px()
        for x, y in self.draft:
            sc.addRect(QRectF(x - h, y - h, 2 * h, 2 * h), self._pen("#2979FF", 1), QBrush(QColor("#FFFFFF")))

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        dark = self.palette().window().color().lightness() < 128
        painter.fillRect(rect, QColor("#1E2227" if dark else "#FAFAFA"))
        step = max(self.ed.spin_grid.value(), 1e-4)
        factors = (2, 2.5, 2)                       # 0.1 → 0.2 → 0.5 → 1 …（格線至少 12 px）
        k = 0
        while step / self.px() < 12:
            step *= factors[k % 3]
            k += 1
        pen = QPen(QColor("#2C323A" if dark else "#E6E8EB"), 0)
        painter.setPen(pen)
        x = math.floor(rect.left() / step) * step
        while x <= rect.right():
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += step
        y = math.floor(rect.top() / step) * step
        while y <= rect.bottom():
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += step
        painter.setPen(QPen(QColor("#546E7A" if dark else "#B0BEC5"), 0))
        painter.drawLine(QPointF(0, rect.top()), QPointF(0, rect.bottom()))
        painter.drawLine(QPointF(rect.left(), 0), QPointF(rect.right(), 0))

    # ---- 選取 / 點擊判斷 -------------------------------------------------------------------------
    def node_at(self, view_pos) -> Optional[Tuple[str, int]]:
        """游標下的把手 → (種類, 索引)；節點優先於控制點、線段中點。"""
        if self.selected is None or self.selected >= len(self.design.shapes):
            return None
        s = self.design.shapes[self.selected]
        best = None
        rank = {"n": 0, "r": 0, "c": 1, "m": 2}
        for kind, i, (x, y) in self.handles(s):
            p = self.mapFromScene(QPointF(x, y))
            if abs(p.x() - view_pos.x()) <= NODE_PX and abs(p.y() - view_pos.y()) <= NODE_PX:
                if best is None or rank[kind] < rank[best[0]]:
                    best = (kind, i)
        return best

    def shape_at(self, x: float, y: float) -> Optional[int]:
        hidden = self.ed.hidden_layers()
        order = {name: i for i, name in enumerate(DRAW_ORDER)}
        cands = sorted(enumerate(self.design.shapes), key=lambda kv: -order.get(kv[1].layer, 9))
        tol = 4 * self.px()
        for idx, s in cands:
            if s.layer in hidden:
                continue
            path = self.shape_path(s)
            if path.contains(QPointF(x, y)) or path.intersects(QRectF(x - tol, y - tol, 2 * tol, 2 * tol)):
                return idx
        return None

    def select(self, idx: Optional[int]) -> None:
        self.selected = idx
        self.rebuild()
        self.selection_changed.emit()

    # ---- 滑鼠 ---------------------------------------------------------------------------------
    def _scene_xy(self, ev) -> Tuple[float, float]:
        p = self.mapToScene(ev.position().toPoint())
        return p.x(), p.y()

    def ref_point(self):
        """正交 / 45° 的參考點：畫圖中的上一點，或拖節點時這個節點原本的位置。"""
        if self.draft:
            return self.draft[-1]
        if self.drag and self.drag.get("kind") == "node" and self.drag.get("h") == "n":
            o = self.drag["orig"]
            i = self.drag["i"]
            if o.kind != "circle" and len(o.pts) > 1:
                j = i - 1 if i > 0 else i + 1
                if o.closed or 0 <= j < len(o.pts):
                    return o.pts[j % len(o.pts)]
        return None

    def _constrain(self, x: float, y: float, ev) -> Tuple[float, float]:
        """Shift：從上一個節點只能 0 / 45 / 90°；正交（F8）：只能水平 / 垂直。"""
        ref = self.ref_point()
        shift = ev is not None and bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if ref is None or self.osnap_hit is not None:
            return x, y
        if self.ed.ortho_on() and not shift:
            x0, y0 = ref
            return (x, y0) if abs(x - x0) >= abs(y - y0) else (x0, y)
        if not shift:
            return x, y
        x0, y0 = ref
        dx, dy = x - x0, y - y0
        ang = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
        dist = math.hypot(dx, dy)
        if abs(math.cos(ang)) < 1e-9:
            return x0, y0 + math.copysign(abs(dy), math.sin(ang))
        if abs(math.sin(ang)) < 1e-9:
            return x0 + math.copysign(abs(dx), math.cos(ang)), y0
        d = dist / math.sqrt(2)
        return self.snap(x0 + math.copysign(d, math.cos(ang)), y0 + math.copysign(d, math.sin(ang)))

    def mousePressEvent(self, ev) -> None:
        x, y = self._scene_xy(ev)
        if ev.button() == Qt.MouseButton.MiddleButton or (ev.button() == Qt.MouseButton.LeftButton
                                                         and (self.tool == "pan" or self.space)):
            self.drag = {"kind": "pan", "pos": ev.position()}
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if ev.button() == Qt.MouseButton.RightButton:
            self._context(ev, x, y)
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        free = bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)
        if self.tool == "zoom":
            self._zoom_box = [(x, y), (x, y)]
            self.drag = {"kind": "zoom"}
            return
        if self.tool == "select":
            h = self.node_at(ev.position())
            if h is not None:
                self.ed.checkpoint()
                s = self.design.shapes[self.selected]
                self.drag = {"kind": "node", "h": h[0], "i": h[1], "free": free, "orig": s.copy()}
                if h[0] == "n":
                    self.active_node = h[1]
                    self.ed.table.setCurrentCell(h[1], 0)
                    self.rebuild()
                    self.selection_changed.emit()
                return
            di = self.dim_at(x, y)
            if di is not None:
                self.selected_dim = di
                self.active_node = None
                self.selected = None
                self.rebuild()
                self.selection_changed.emit()
                return
            idx = self.shape_at(x, y)
            self.selected_dim = None
            if idx != self.selected or self.active_node is not None:
                self.active_node = None
                self.select(idx)
            if idx is not None:
                self.ed.checkpoint()
                self.drag = {"kind": "body", "start": self.snap(x, y, free), "orig": self.design.shapes[idx].copy()}
            return
        sx, sy = self._constrain(*self.snap(x, y, free), ev)
        self.add_point((sx, sy))

    def add_point(self, p) -> None:
        """畫圖工具加一點（滑鼠點、或指令列打的座標）。"""
        sx, sy = p
        if self.tool == "dist":
            if len(self.draft) == 1:
                self.measure = (self.draft[0], (sx, sy))
                self.draft = []
                self.ed.report_measure(*self.measure)
            else:
                self.measure = None
                self.draft = [(sx, sy)]
            self.rebuild()
            return
        if self.tool == "dim" and len(self.draft) == 2:
            self._finish((sx, sy))
            return
        if self.tool in ("rect", "circle") and self.draft:
            self._finish((sx, sy))
            return
        if self.tool == "arc" and len(self.draft) == 2:
            self._finish((sx, sy))
            return
        self.draft.append((sx, sy))
        self.cursor = (sx, sy)
        self.rebuild()

    def dim_at(self, x: float, y: float) -> Optional[int]:
        if DIM_LAYER in self.ed.hidden_layers():
            return None
        tol = 5 * self.px()
        for i, dm in enumerate(self.design.dims):
            _e1, _e2, (a, b), mid, _ang = dm.geometry()
            if _seg_dist(a, b, (x, y)) <= tol or math.hypot(mid[0] - x, mid[1] - y) <= 4 * tol:
                return i
        return None

    def _drag_handle(self, s: Shape, x: float, y: float, sx: float, sy: float, alt: bool) -> None:
        kind, i, o = self.drag["h"], self.drag["i"], self.drag["orig"]
        if s.kind == "circle":
            if kind == "n":
                s.pts[0] = (sx, sy)
            else:
                s.r = max(1e-4, math.hypot(sx - s.pts[0][0], sy - s.pts[0][1]))
            return
        if kind == "m":                                    # 拖線段中點：彎成圓弧（不吸附格線，比較順）
            n = len(s.pts)
            s.bulges[i] = round(G.bulge_from_sagitta(s.pts[i], s.pts[(i + 1) % n], (x, y)), 6)
            if abs(s.bulges[i]) < 0.01:
                s.bulges[i] = 0.0                          # 拉回弦附近 → 直線
            return
        if not s.bezier:
            s.pts[i] = (sx, sy)
            return
        n = len(s.pts)
        if kind == "n":                                    # 錨點：兩邊的控制點一起移
            dx, dy = sx - o.pts[i][0], sy - o.pts[i][1]
            s.pts[i] = (sx, sy)
            neigh = [(i - 1) % n, (i + 1) % n] if s.closed else [j for j in (i - 1, i + 1) if 0 <= j < n]
            for j in neigh:
                s.pts[j] = (o.pts[j][0] + dx, o.pts[j][1] + dy)
            return
        s.pts[i] = (sx, sy)                                # 控制點；沒按 Alt 時對面的控制點跟著保持平滑
        if alt:
            return
        anchor, other = (i - 1, i - 2) if i % 3 == 1 else (i + 1, i + 2)
        if s.closed:
            anchor, other = anchor % n, other % n
        elif not (0 <= other < n and 0 <= anchor < n):
            return
        a = s.pts[anchor]
        vx, vy = sx - a[0], sy - a[1]
        L = math.hypot(vx, vy)
        ol = math.hypot(o.pts[other][0] - a[0], o.pts[other][1] - a[1])
        if L > 1e-12:
            s.pts[other] = (a[0] - vx / L * ol, a[1] - vy / L * ol)

    def mouseMoveEvent(self, ev) -> None:
        x, y = self._scene_xy(ev)
        alt = bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)
        if self.drag and self.drag["kind"] == "pan":
            d = ev.position() - self.drag["pos"]
            self.drag["pos"] = ev.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(round(d.x())))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(round(d.y())))
            return
        if self.drag and self.drag["kind"] == "zoom":
            self._zoom_box[1] = (x, y)
            self.viewport().update()
            return
        dragging = bool(self.drag and self.drag["kind"] in ("node", "body"))
        exclude = self.selected if dragging else None
        if dragging and self.drag["kind"] == "body":
            exclude = self.selected
        sx, sy = self._constrain(*self.snap(x, y, alt or bool(self.drag and self.drag.get("free")), exclude), ev)
        self.cursor = (sx, sy)
        info = ""
        if self.draft:
            x0, y0 = self.draft[-1]
            L = math.hypot(sx - x0, sy - y0)
            info = f"長 {_fmt(L)} mm　角度 {math.degrees(math.atan2(sy - y0, sx - x0)):.2f}°　ΔX {_fmt(sx - x0)}　ΔY {_fmt(sy - y0)}"
        if self.osnap_hit:
            info = f"［{OSNAP_NAME[self.osnap_hit[1]]}］" + (f"　{info}" if info else "")
        self.cursor_moved.emit(sx, sy, info)
        if not self.draft and not dragging:
            self.viewport().update()                 # 鎖點記號
        if self.drag and self.selected is not None:
            s = self.design.shapes[self.selected]
            if self.drag["kind"] == "node":
                self._drag_handle(s, x, y, sx, sy, alt)
                if self.drag["h"] == "m":
                    b = s.bulges[self.drag["i"]]
                    self.cursor_moved.emit(sx, sy, f"弧 {math.degrees(4 * math.atan(b)):.1f}°")
            elif self.drag["kind"] == "body":
                ox, oy = self.drag["start"]
                o = self.drag["orig"]
                s.pts = [(px + sx - ox, py + sy - oy) for px, py in o.pts]
            self.drag["moved"] = True
            self.rebuild()
            return
        if self.draft:
            self.rebuild()
        if self.tool == "select":
            hover = self.node_at(ev.position())
            self.setCursor(Qt.CursorShape.SizeAllCursor if hover else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, ev) -> None:
        if self.drag and self.drag["kind"] == "zoom":
            (x0, y0), (x1, y1) = self._zoom_box
            self._zoom_box = None
            self.drag = None
            if abs(self.mapFromScene(QPointF(x0, y0)).x() - self.mapFromScene(QPointF(x1, y1)).x()) > 6:
                self.zoom_rect(QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized())
            else:
                self.zoom(1.6, ev.position())
            self.viewport().update()
            return
        if self.drag:
            moved = self.drag.get("moved")
            kind = self.drag["kind"]
            self.drag = None
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.tool == "pan" else Qt.CursorShape.ArrowCursor)
            if kind in ("node", "body"):
                if moved:
                    self.changed.emit()
                else:
                    self.ed.drop_checkpoint()
                self.selection_changed.emit()

    def mouseDoubleClickEvent(self, ev) -> None:
        if self.tool in ("dim", "dist", "zoom"):
            self.mousePressEvent(ev)                 # 連點兩下也算兩點
            return
        if self.tool in ("path", "poly", "bezier", "bezpoly"):
            if self.draft and len(self.draft) >= 2 and self.draft[-1] == self.draft[-2]:
                self.draft.pop()
            self._finish(None)
            return
        if self.tool == "select" and self.selected is not None:
            s = self.design.shapes[self.selected]
            if s.kind in ("path", "poly"):
                x, y = self._scene_xy(ev)
                self.ed.checkpoint()
                if s.bezier:
                    seg, t, _d = G.nearest_bezier(s.pts, (x, y), s.closed)
                    new = s.insert_after(seg, t=t)
                else:
                    seg = _nearest_seg(s, (x, y))
                    new = s.insert_after(seg, self.snap(x, y))
                self.rebuild()
                self.changed.emit()
                self.selection_changed.emit()
                self.ed.table.setCurrentCell(new, 0)

    def wheelEvent(self, ev) -> None:
        dy = ev.angleDelta().y() or ev.angleDelta().x()
        if not dy:
            return
        self.zoom(1.2 ** (dy / 120.0), ev.position())

    def keyPressEvent(self, ev) -> None:
        k = ev.key()
        if k == Qt.Key.Key_Space and not ev.isAutoRepeat():
            self.space = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.draft:
            self._finish(None)
        elif k == Qt.Key.Key_Escape:
            if self.draft:
                self.draft = []
                self.rebuild()
            elif self.measure:
                self.measure = None
                self.rebuild()
            elif self.active_node is not None:
                self.active_node = None
                self.rebuild()
                self.selection_changed.emit()
            else:
                self.selected_dim = None
                self.select(None)
        elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.draft:
                self.draft.pop()
                self.rebuild()
            elif self.selected_dim is not None:
                self.ed.delete_dim(self.selected_dim)
            else:
                self.ed.delete_selected()
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down) and self.selected is not None:
            step = max(self.ed.spin_grid.value(), 1e-4)
            if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                step *= 10
            if ev.modifiers() & Qt.KeyboardModifier.AltModifier:
                step /= 10
            dx = {Qt.Key.Key_Left: -step, Qt.Key.Key_Right: step}.get(k, 0.0)
            dy = {Qt.Key.Key_Down: -step, Qt.Key.Key_Up: step}.get(k, 0.0)
            self.ed.nudge(dx, dy)
        else:
            super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Space and not ev.isAutoRepeat():
            self.space = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().keyReleaseEvent(ev)

    # ---- 畫完 ---------------------------------------------------------------------------------
    def _finish(self, last) -> None:
        pts = list(self.draft) + ([last] if last is not None else [])
        tool = self.tool
        self.draft = []
        shape = None
        ed = self.ed
        if tool == "dim":
            if len(pts) == 3 and pts[0] != pts[1]:
                ed.checkpoint()
                self.design.dims.append(Dim(self.dim_kind(pts[0], pts[1], pts[2]), pts[0], pts[1], pts[2]))
                self.selected_dim = len(self.design.dims) - 1
                self.changed.emit()
            self.rebuild()
            return
        if tool == "dist":
            self.rebuild()
            return
        if tool == "path" and len(pts) >= 2:
            f = ed.new_fillet.value()
            shape = Shape("path", "g", pts, width=ed.new_width.value(), gap=ed.new_gap.value(),
                          fillets=[0.0] + [f] * (len(pts) - 2) + [0.0])
        elif tool == "bezier" and len(pts) >= 2:
            shape = Shape("path", "g", G.bezier_from_anchors(pts), width=ed.new_width.value(), gap=ed.new_gap.value(),
                          smooth="bezier")
        elif tool == "arc" and len(pts) == 3:
            b = G.arc3_bulge(pts[0], pts[1], pts[2])
            shape = Shape("path", "g", [pts[0], pts[2]], width=ed.new_width.value(), gap=ed.new_gap.value(), bulges=[b])
        elif tool == "poly" and len(pts) >= 3:
            shape = Shape("poly", ed.current_layer(), pts)
        elif tool == "bezpoly" and len(pts) >= 3:
            shape = Shape("poly", ed.current_layer(), G.bezier_from_anchors(pts, closed=True), smooth="bezier")
        elif tool == "rect" and len(pts) == 2:
            (x0, y0), (x1, y1) = pts
            if x0 != x1 and y0 != y1:
                shape = Shape("poly", ed.current_layer(),
                              [(min(x0, x1), min(y0, y1)), (max(x0, x1), min(y0, y1)), (max(x0, x1), max(y0, y1)),
                               (min(x0, x1), max(y0, y1))])
        elif tool == "circle" and len(pts) == 2:
            r = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
            if r > 0:
                layer = ed.current_layer()
                shape = Shape("circle", layer if layer in ("h", "v") else "h", [pts[0]], r=r)
        if shape is None:
            self.rebuild()
            return
        shape.norm()
        ed.checkpoint()
        self.design.shapes.append(shape)
        self.selected = len(self.design.shapes) - 1
        self.rebuild()
        self.changed.emit()
        self.selection_changed.emit()

    def _context(self, ev, x: float, y: float) -> None:
        if self.draft:
            self._finish(None)
            return
        m = QMenu(self)
        h = self.node_at(ev.position())
        ed = self.ed
        if h is not None:
            s = self.design.shapes[self.selected]
            kind, i = h
            if kind == "n" and s.kind != "circle":
                a = m.addAction("刪除這個節點")
                a.setEnabled(s.can_delete(i))
                a.triggered.connect(lambda: ed.delete_node(i))
                if not s.bezier and (s.closed or 0 < i < len(s.pts) - 1):
                    m.addAction("設圓角…", lambda: ed.ask_fillet(i))
                    if s.fillets and i < len(s.fillets) and s.fillets[i]:
                        m.addAction("取消圓角", lambda: ed.set_fillet(i, 0.0))
            elif kind == "m":
                m.addAction("改成直線", lambda: ed.set_bulge(i, 0.0))
                m.addAction("圓弧 90°（往右彎）", lambda: ed.set_bulge(i, math.tan(math.radians(90) / 4)))
                m.addAction("圓弧 90°（往左彎）", lambda: ed.set_bulge(i, -math.tan(math.radians(90) / 4)))
                m.addAction("半圓", lambda: ed.set_bulge(i, 1.0))
                m.addAction("反向彎", lambda: ed.set_bulge(i, -s.bulges[i]))
        else:
            di = self.dim_at(x, y)
            idx = None if di is not None else self.shape_at(x, y)
            if di is not None:
                self.selected_dim = di
                self.rebuild()
                m.addAction("刪除這個標註", lambda: ed.delete_dim(di))
                m.addAction("刪除全部標註", ed.delete_all_dims)
            if idx is not None:
                self.select(idx)
                m.addAction("刪除（Delete）", ed.delete_selected)
                m.addAction("複製一份", ed.duplicate_selected)
                m.addSeparator()
                m.addAction("移動…（M）", ed.ask_move)
                m.addAction("複製到…（CO）", ed.ask_copy)
                m.addAction("旋轉…（RO）", ed.ask_rotate)
                m.addAction("左右鏡像", lambda: ed.mirror_selected("x"))
                m.addAction("上下鏡像", lambda: ed.mirror_selected("y"))
                m.addAction("置中到板子（水平）", lambda: ed.center_selected("x"))
                m.addAction("置中到板子（垂直）", lambda: ed.center_selected("y"))
                m.addSeparator()
                s = self.design.shapes[idx]
                if s.kind in ("path", "poly"):
                    if s.bezier:
                        m.addAction("改回折線（只留錨點）", ed.to_polyline)
                    else:
                        m.addAction("變成平滑曲線（Bézier）", ed.to_bezier)
                if s.kind in ("poly", "circle"):
                    sub = m.addMenu("改到圖層")
                    for layer in LAYERS[self.design.mode]:
                        act = sub.addAction(f"{layer}　{LAYER_INFO[layer][0]}")
                        act.triggered.connect(lambda _c=False, la=layer: ed.set_layer(la))
        if not m.isEmpty():
            m.exec(ev.globalPosition().toPoint())


def _line_path(pts) -> QPainterPath:
    path = QPainterPath()
    if not pts:
        return path
    path.moveTo(QPointF(*pts[0]))
    for p in pts[1:]:
        path.lineTo(QPointF(*p))
    return path


def _nearest_seg(s: Shape, p) -> int:
    """直線 / 圓弧段裡最接近 p 的那一段。"""
    s.norm()
    n = len(s.pts)
    best, best_d = 0, float("inf")
    for k in range(s.n_segs()):
        a, b = s.pts[k], s.pts[(k + 1) % n]
        if s.bulges[k]:
            arc = G.bulge_arc(a, b, s.bulges[k])
            samples = G.flatten([("A", arc[0], arc[1], arc[2], arc[3], a, b)], 0.01) if arc else [a, b]
        else:
            samples = [a, b]
        for j in range(len(samples) - 1):
            d = _seg_dist(samples[j], samples[j + 1], p)
            if d < best_d:
                best, best_d = k, d
    return best


def _seg_dist(a, b, p) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1])


# =====================================================================================================
class ConvertDialog(QDialog):
    """v3.9：MS ⇄ CPW 一鍵轉換。依板材 εr、厚度與目標阻抗算線寬 / 間隙（tline.py），可以自動加過孔。"""

    def __init__(self, ed: "DxfEditor") -> None:
        super().__init__(ed)
        self.ed = ed
        self.setWindowTitle("MS ⇄ CPW 自動轉換")
        sub = ed.substrate() or {}
        cur = ed.design.mode
        paths = [x for x in ed.design.shapes if x.kind == "path"]
        w0 = paths[0].width if paths else ed.new_width.value()
        g0 = paths[0].gap if paths else ed.new_gap.value()
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        self.rb_ms, self.rb_cpw = QRadioButton("MS（微帶線）"), QRadioButton("CPW（接地共面波導）")
        (self.rb_cpw if cur == "MS" else self.rb_ms).setChecked(True)
        row.addWidget(QLabel(f"目前：{cur} →"))
        row.addWidget(self.rb_ms)
        row.addWidget(self.rb_cpw)
        v.addLayout(row)
        f = QFormLayout()
        self.sp_er = QDoubleSpinBox()
        self.sp_er.setRange(1.0, 200.0)
        self.sp_er.setDecimals(3)
        er = sub.get("er") or tline.material_er(sub.get("material", "")) or 3.55
        self.sp_er.setValue(float(er))
        self.sp_h = ed._spin(0.001, 100, float(sub.get("h") or 0.813))
        self.sp_z0 = QDoubleSpinBox()
        self.sp_z0.setRange(5, 300)
        self.sp_z0.setDecimals(2)
        self.sp_z0.setSuffix(" Ω")
        self.sp_z0.setValue(50.0)
        mat = sub.get("material") or ""
        f.addRow("板材 εr", self.sp_er)
        f.addRow("介電層厚度 h", self.sp_h)
        if mat:
            f.addRow("", QLabel(f"<span style='color:gray'>主視窗：{mat}" + (f"，εr 覆寫 {sub['er']:g}" if sub.get("er") else "")
                                + "</span>"))
        f.addRow("目標阻抗 Z0", self.sp_z0)
        self.combo_fix = QComboBox()
        self.combo_fix.addItem("CPW：保持線寬，算間隙", "w")
        self.combo_fix.addItem("CPW：保持間隙，算線寬", "g")
        self.combo_fix.addItem("自己填（不自動算）", "manual")
        f.addRow("怎麼算", self.combo_fix)
        self.sp_w = ed._spin(0.001, 1000, w0)
        self.sp_g = ed._spin(0.001, 1000, max(g0, 0.05))
        f.addRow("線寬 W", self.sp_w)
        f.addRow("間隙 G（CPW）", self.sp_g)
        self.lbl_z = QLabel("")
        f.addRow("算出來", self.lbl_z)
        v.addLayout(f)
        self.via = QGroupBox("自動加過孔（CPW，沿微波線兩側）")
        self.via.setCheckable(True)
        self.via.setChecked(True)
        vf = QFormLayout(self.via)
        self.sp_vd = ed._spin(0.01, 10, 0.3)
        self.sp_vp = ed._spin(0.05, 100, 1.0)
        self.sp_vc = ed._spin(0.0, 100, 0.2)
        vf.addRow("過孔直徑", self.sp_vd)
        vf.addRow("間距（中心到中心）", self.sp_vp)
        vf.addRow("離間隙邊", self.sp_vc)
        v.addWidget(self.via)
        self.chk_replace_vias = QCheckBox("先刪掉原本的過孔")
        self.chk_replace_vias.setChecked(True)
        v.addWidget(self.chk_replace_vias)
        note = QLabel("<span style='color:gray'>公式：MS = Hammerstad–Jensen；CPW = 底下有地的共面波導（保角映射）。"
                      "忽略銅厚，誤差約幾 %，最後以 HFSS 的 Port 阻抗為準。</span>")
        note.setWordWrap(True)
        v.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("轉換")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._busy = False
        for w_ in (self.sp_er, self.sp_h, self.sp_z0):
            w_.valueChanged.connect(lambda _v: self.recalc())
        self.sp_w.valueChanged.connect(lambda _v: self.recalc("w"))
        self.sp_g.valueChanged.connect(lambda _v: self.recalc("g"))
        self.combo_fix.currentIndexChanged.connect(lambda _i: self.recalc())
        self.rb_ms.toggled.connect(lambda _v: self.recalc())
        self.recalc()

    def target(self) -> str:
        return "MS" if self.rb_ms.isChecked() else "CPW"

    def recalc(self, edited: str = "") -> None:
        if self._busy:
            return
        self._busy = True
        try:
            er, h, z0 = self.sp_er.value(), self.sp_h.value(), self.sp_z0.value()
            mode, fix = self.target(), self.combo_fix.currentData()
            cpw = mode == "CPW"
            self.sp_g.setEnabled(cpw)
            self.via.setEnabled(cpw)
            self.combo_fix.setEnabled(cpw)
            msg = ""
            if not cpw:
                if fix != "manual" and edited != "w":
                    self.sp_w.setValue(round(tline.ms_width(z0, h, er), 4))
                z = tline.ms_z0(self.sp_w.value(), h, er)
                msg = f"MS：W = {_fmt(self.sp_w.value())} mm → Z0 ≈ {z:.1f} Ω"
            else:
                if fix == "w" and edited != "g":
                    g = tline.gcpw_gap(z0, self.sp_w.value(), h, er)
                    if g is None:
                        msg = "這個線寬做不到目標阻抗（換「保持間隙，算線寬」或改線寬）。"
                    else:
                        self.sp_g.setValue(round(g, 4))
                elif fix == "g" and edited != "w":
                    w = tline.gcpw_width(z0, self.sp_g.value(), h, er)
                    if w is None:
                        msg = "這個間隙做不到目標阻抗。"
                    else:
                        self.sp_w.setValue(round(w, 4))
                if not msg:
                    eeff, z = tline.gcpw_eeff_z0(self.sp_w.value(), self.sp_g.value(), h, er)
                    msg = f"CPW：W = {_fmt(self.sp_w.value())}、G = {_fmt(self.sp_g.value())} mm → Z0 ≈ {z:.1f} Ω（εeff {eeff:.2f}）"
            self.lbl_z.setText(msg)
        finally:
            self._busy = False


class DxfEditor(QMainWindow):
    saved = pyqtSignal(str)

    def __init__(self, path: Optional[str] = None, mode: str = "MS", parent=None,
                 on_saved: Optional[Callable[[str], None]] = None, substrate: Optional[Callable[[], dict]] = None) -> None:
        super().__init__(parent)
        self.substrate = substrate or (lambda: {})          # v3.9：{"er", "h", "material"}（主視窗的板材設定）
        self.cmd_pending: Optional[str] = None
        self.setWindowTitle("DXF 編輯器")
        self.resize(1280, 820)
        self.design = Design.template(mode)
        self.undo_stack: List[str] = []
        self.redo_stack: List[str] = []
        self.dirty = False
        self._sync = False
        if on_saved:
            self.saved.connect(on_saved)
        self.canvas = Canvas(self)
        self.setCentralWidget(self.canvas)
        self._build_toolbar()
        self._build_left()
        self._build_right()
        self._build_command()
        self.status = QLabel("")
        self.statusBar().addPermanentWidget(self.status)
        self.canvas.changed.connect(self._on_changed)
        self.canvas.selection_changed.connect(self._refresh_props)
        self.canvas.cursor_moved.connect(lambda x, y, info: self.statusBar().showMessage(
            f"X {_fmt(x)}  Y {_fmt(y)} mm" + (f"　{info}" if info else "")))
        if path and os.path.exists(path):
            self.open_file(path)
        else:
            self._after_load()

    # ---- 介面 ---------------------------------------------------------------------------------
    def _build_toolbar(self) -> None:
        tb = QToolBar("工具")
        tb.setMovable(False)
        self.addToolBar(tb)
        new = QToolButton()
        new.setText("新增 ▾")
        new.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        nm = QMenu(new)
        nm.addAction("MS 範本（板材＋一條直的微波線）", lambda: self.new_design("MS"))
        nm.addAction("CPW 範本（下板材＋自動上地＋微波線）", lambda: self.new_design("CPW"))
        nm.addSeparator()
        nm.addAction("空白 MS（只有板材，自己畫）", lambda: self.new_design("MS", trace=False))
        nm.addAction("空白 CPW（只有下板材，自己畫）", lambda: self.new_design("CPW", trace=False))
        new.setMenu(nm)
        tb.addWidget(new)
        for text, key, fn in (("開啟…", QKeySequence.StandardKey.Open, self.open_dialog),
                              ("儲存", QKeySequence.StandardKey.Save, self.save),
                              ("另存新檔…", QKeySequence.StandardKey.SaveAs, self.save_as)):
            a = QAction(text, self)
            a.setShortcut(key)
            a.triggered.connect(fn)
            tb.addAction(a)
        tb.addSeparator()
        self.act_undo = QAction("↶ 復原", self)
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_undo.triggered.connect(self.undo)
        self.act_redo = QAction("↷ 重做", self)
        self.act_redo.setShortcuts([QKeySequence.StandardKey.Redo, QKeySequence("Ctrl+Y")])
        self.act_redo.triggered.connect(self.redo)
        tb.addAction(self.act_undo)
        tb.addAction(self.act_redo)
        tb.addSeparator()
        a = QAction("全部顯示（F）", self)
        a.setShortcut(QKeySequence("F"))
        a.triggered.connect(self.canvas.fit)
        tb.addAction(a)
        tb.addSeparator()
        self.chk_snap = QCheckBox("吸附格線")
        self.chk_snap.setChecked(True)
        self.chk_snap.toggled.connect(lambda _v: self.canvas.viewport().update())
        tb.addWidget(self.chk_snap)
        self.spin_grid = QDoubleSpinBox()
        self.spin_grid.setDecimals(3)
        self.spin_grid.setRange(0.001, 100)
        self.spin_grid.setValue(0.1)
        self.spin_grid.setSuffix(" mm")
        self.spin_grid.valueChanged.connect(lambda _v: self.canvas.viewport().update())
        tb.addWidget(self.spin_grid)
        # 第二排：畫圖工具
        self.addToolBarBreak()
        tb2 = QToolBar("畫圖")
        tb2.setMovable(False)
        self.addToolBar(tb2)
        grp = QActionGroup(self)
        self.tool_actions = {}
        for tool, text, keys, tip in (
                ("select", "選取", ("F1", "V"), "點圖形選取；拖節點、拖整個圖形；雙擊線段加節點；拖橘色菱形把線段彎成圓弧"),
                ("pan", "平移", ("F2", "H"), "拖曳移動畫面（也可以按住空白鍵或滑鼠中鍵）"),
                ("path", "微波線", ("P",), "點節點畫中心線（線寬、轉角圓角在左邊設定）；雙擊或 Enter 結束"),
                ("arc", "三點弧", ("A",), "起點 → 弧上一點 → 終點，畫一段圓弧微波線"),
                ("bezier", "曲線", ("B",), "點幾個錨點，畫出平滑的 Bézier 微波線；之後可以拖藍色控制點調整"),
                ("poly", "多邊形", ("G",), "在目前圖層畫封閉多邊形"),
                ("bezpoly", "曲線多邊形", ("K",), "在目前圖層畫平滑的封閉曲線"),
                ("rect", "矩形", ("R",), "兩個對角點"),
                ("circle", "圓（孔）", ("C",), "圓心 → 半徑（螺絲孔 h、過孔 v）"),
                ("dim", "標註", ("D",), "點兩點（會鎖到端點 / 中點 / 圓心），再點標註線的位置；存成 DXF 標註（圖層 dim，HFSS 不匯入）"),
                ("dist", "量距離", ("M",), "點兩點：顯示距離、ΔX、ΔY、角度（不存檔）"),
                ("zoom", "框選放大", ("Z",), "拖一個框放大；點一下 = 放大")):
            a = QAction(f"{text}（{keys[0]}）", self)
            a.setCheckable(True)
            a.setToolTip(tip)
            a.setShortcuts([QKeySequence(k) for k in keys])
            a.triggered.connect(lambda _c=False, t=tool: self.set_tool(t))
            grp.addAction(a)
            tb2.addAction(a)
            self.tool_actions[tool] = a
        self.tool_actions["select"].setChecked(True)
        tb2.addSeparator()
        tb2.addWidget(QLabel(" 標註："))
        self.combo_dim = QComboBox()
        self.combo_dim.addItem("自動（水平 / 垂直）", "auto")
        self.combo_dim.addItem("水平距離", "h")
        self.combo_dim.addItem("垂直距離", "v")
        self.combo_dim.addItem("對齊（實際距離）", "a")
        tb2.addWidget(self.combo_dim)
        # 第三排：AutoCAD 的狀態列開關
        self.addToolBarBreak()
        tb3 = QToolBar("輔助")
        tb3.setMovable(False)
        self.addToolBar(tb3)
        self.chk_osnap = QCheckBox("物件鎖點（F3）")
        self.chk_osnap.setChecked(True)
        self.chk_osnap.setToolTip("游標靠近端點 / 中點 / 圓心 / 四分點 / 微波線輪廓角時自動鎖上（黃色記號）")
        self.chk_ortho = QCheckBox("正交（F8）")
        self.chk_ortho.setToolTip("畫圖 / 拖節點時只能水平或垂直")
        self.chk_lengths = QCheckBox("顯示線段長度")
        self.chk_lengths.setChecked(True)
        self.chk_lengths.toggled.connect(lambda _v: self.canvas.rebuild())
        for w_ in (self.chk_osnap, self.chk_ortho, self.chk_lengths):
            tb3.addWidget(w_)
        QShortcut(QKeySequence("F3"), self, activated=self.chk_osnap.toggle)
        QShortcut(QKeySequence("F8"), self, activated=self.chk_ortho.toggle)
        tb3.addSeparator()
        for text, fn, tip in (("移動…", self.ask_move, "選取的圖形 / 節點移動 ΔX、ΔY（指令 M）"),
                              ("複製到…", self.ask_copy, "複製選取的圖形到 ΔX、ΔY（指令 CO）"),
                              ("旋轉…", self.ask_rotate, "繞圖形中心旋轉（指令 RO）"),
                              ("⇋ 左右鏡像", lambda: self.mirror_selected("x"), "對圖形中心的垂直線翻轉（指令 MI X）"),
                              ("⇵ 上下鏡像", lambda: self.mirror_selected("y"), "對圖形中心的水平線翻轉（指令 MI Y）")):
            b = QAction(text, self)
            b.setToolTip(tip)
            b.triggered.connect(fn)
            tb3.addAction(b)
        tb3.addSeparator()
        conv = QAction("⇄ MS / CPW 自動轉換…", self)
        conv.setToolTip("依板材 εr、厚度與目標阻抗（50 Ω）自動算線寬 / 間隙，切換建模方式；CPW 可以自動加過孔")
        conv.triggered.connect(self.convert_dialog)
        tb3.addAction(conv)
        tb3.addSeparator()
        tb3.addWidget(QLabel("  Alt：不吸附　Shift：0/45/90°　方向鍵：微調　右鍵：結束 / 選單"))

    def _spin(self, lo=-1e5, hi=1e5, val=0.0, dec=4) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(dec)
        s.setValue(val)
        s.setSuffix(" mm")
        s.setKeyboardTracking(False)
        return s

    def _build_left(self) -> None:
        w = QWidget()
        v = QVBoxLayout(w)
        g = QGroupBox("建模方式")
        f = QFormLayout(g)
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["MS", "CPW"])
        self.combo_mode.currentTextChanged.connect(self.change_mode)
        f.addRow("模式", self.combo_mode)
        self.chk_auto = QCheckBox("自動產生上地（板材 − 微波線＋間隙）")
        self.chk_auto.toggled.connect(self._toggle_auto)
        f.addRow(self.chk_auto)
        self.chk_bridge = QCheckBox("端對端的微波線之間也挖空（耦合間隙）")
        self.chk_bridge.setToolTip("例如共振腔：兩段微波線頭對頭、中間留耦合間隙時，間隙兩側的槽連起來，地不會伸進間隙裡。\n"
                                   "兩端距離 ≤ 3 ×（線寬 + 2 × 間隙）而且面對面才算。")
        self.chk_bridge.setChecked(True)
        self.chk_bridge.toggled.connect(self._toggle_bridge)
        f.addRow(self.chk_bridge)
        v.addWidget(g)

        # v3.9：點節點就能打座標
        self.node_box = QGroupBox("選取的節點")
        f = QFormLayout(self.node_box)
        self.lbl_node = QLabel("")
        f.addRow(self.lbl_node)
        self.sp_nx, self.sp_ny = self._spin(), self._spin()
        self.sp_nlen = self._spin(0.0, 1e5, 0.0)
        self.sp_nang = QDoubleSpinBox()
        self.sp_nang.setRange(-360, 360)
        self.sp_nang.setDecimals(3)
        self.sp_nang.setSuffix(" °")
        self.sp_nang.setKeyboardTracking(False)
        f.addRow("X", self.sp_nx)
        f.addRow("Y", self.sp_ny)
        f.addRow("距上一點", self.sp_nlen)
        f.addRow("角度", self.sp_nang)
        self.sp_nx.valueChanged.connect(lambda _v: self._node_xy_changed())
        self.sp_ny.valueChanged.connect(lambda _v: self._node_xy_changed())
        self.sp_nlen.valueChanged.connect(lambda _v: self._node_polar_changed())
        self.sp_nang.valueChanged.connect(lambda _v: self._node_polar_changed())
        row = QHBoxLayout()
        for text, step in (("◀ 上一個", -1), ("下一個 ▶", 1)):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, k=step: self.step_node(k))
            row.addWidget(b)
        f.addRow(row)
        self.node_box.setToolTip("點選圖形後再點它的節點（白色方塊）。X / Y 是絕對座標；「距上一點」「角度」改的是到上一個節點的長度與方向")
        self.node_box.setVisible(False)
        v.addWidget(self.node_box)

        g = QGroupBox("板材大小")
        f = QFormLayout(g)
        self.sp_x0, self.sp_y0 = self._spin(), self._spin()
        self.sp_w, self.sp_h = self._spin(0.001, 1e5, 30), self._spin(0.001, 1e5, 20)
        f.addRow("寬 W", self.sp_w)
        f.addRow("高 H", self.sp_h)
        f.addRow("左下 X", self.sp_x0)
        f.addRow("左下 Y", self.sp_y0)
        self.chk_follow = QCheckBox("微波線端點跟著板邊")
        self.chk_follow.setChecked(True)
        f.addRow(self.chk_follow)
        b = QPushButton("套用板材大小")
        b.clicked.connect(self.apply_board)
        f.addRow(b)
        v.addWidget(g)

        g = QGroupBox("新畫的微波線")
        f = QFormLayout(g)
        self.new_width = self._spin(0.001, 1000, 1.0)
        self.new_gap = self._spin(0.0, 1000, 0.2)
        f.addRow("線寬", self.new_width)
        f.addRow("間隙（CPW）", self.new_gap)
        self.new_fillet = self._spin(0.0, 1000, 0.0)
        self.new_fillet.setToolTip("微波線工具（P）畫的轉角自動加這個半徑的圓角；0 = 尖角。之後可以在節點表格逐點改")
        f.addRow("轉角圓角", self.new_fillet)
        v.addWidget(g)

        g = QGroupBox("圖層")
        gl = QVBoxLayout(g)
        gl.addWidget(QLabel("多邊形、矩形、圓畫在："))
        self.combo_layer = QComboBox()
        gl.addWidget(self.combo_layer)
        self.list_layers = QListWidget()
        self.list_layers.itemChanged.connect(lambda _i: self.canvas.rebuild())
        gl.addWidget(self.list_layers)
        v.addWidget(g)

        self.lbl_check = QLabel("")
        self.lbl_check.setWordWrap(True)
        v.addWidget(self.lbl_check)
        v.addStretch(1)
        dock = QDockWidget("設計", self)
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        dock.setWidget(w)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

    def _build_right(self) -> None:
        w = QWidget()
        self.props = QVBoxLayout(w)
        self.lbl_sel = QLabel("")
        self.lbl_sel.setWordWrap(True)
        self.props.addWidget(self.lbl_sel)
        self.form = QFormLayout()
        self.props.addLayout(self.form)
        self.sp_width = self._spin(0.001, 1000, 1.0)
        self.sp_gap = self._spin(0.0, 1000, 0.2)
        self.sp_r = self._spin(0.0001, 1000, 1.0)
        self.sp_cx, self.sp_cy = self._spin(), self._spin()
        self.combo_shape_layer = QComboBox()
        for sp in (self.sp_width, self.sp_gap, self.sp_r, self.sp_cx, self.sp_cy):
            sp.valueChanged.connect(self._props_changed)
        self.combo_shape_layer.currentTextChanged.connect(self._layer_changed)
        self.form.addRow("圖層", self.combo_shape_layer)
        self.form.addRow("線寬", self.sp_width)
        self.form.addRow("間隙", self.sp_gap)
        self.form.addRow("圓心 X", self.sp_cx)
        self.form.addRow("圓心 Y", self.sp_cy)
        self.form.addRow("半徑", self.sp_r)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["X (mm)", "Y (mm)", "圓角 R", "弧 °"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._table_changed)
        self.props.addWidget(self.table)
        row = QHBoxLayout()
        self.btn_add_node = QPushButton("加節點")
        self.btn_add_node.clicked.connect(self.add_node_after)
        self.btn_del_node = QPushButton("刪節點")
        self.btn_del_node.clicked.connect(lambda: self.delete_node(self.table.currentRow()))
        self.btn_reverse = QPushButton("反轉方向")
        self.btn_reverse.clicked.connect(self.reverse_selected)
        for b in (self.btn_add_node, self.btn_del_node, self.btn_reverse):
            row.addWidget(b)
        self.props.addLayout(row)
        self.btn_delete = QPushButton("刪除這個圖形")
        self.btn_delete.clicked.connect(self.delete_selected)
        self.props.addWidget(self.btn_delete)
        self.props.addStretch(1)
        dock = QDockWidget("屬性", self)
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        dock.setWidget(w)
        dock.setMinimumWidth(260)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

    def _build_command(self) -> None:
        """AutoCAD 式指令列：打座標或指令。"""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 2, 6, 2)
        self.lbl_prompt = QLabel("指令：")
        self.cmd = QLineEdit()
        self.cmd.setPlaceholderText("座標 x,y／@dx,dy／@長度<角度；或指令 L、REC、C、A、DIM、DI、M、CO、RO、MI、Z、U、OS、OR（Enter 執行）")
        self.cmd.returnPressed.connect(self._cmd_enter)
        self.lbl_cmd_msg = QLabel("")
        self.lbl_cmd_msg.setStyleSheet("color: gray;")
        h.addWidget(self.lbl_prompt)
        h.addWidget(self.cmd, 1)
        h.addWidget(self.lbl_cmd_msg, 1)
        dock = QDockWidget("指令列", self)
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        dock.setTitleBarWidget(QWidget())
        dock.setWidget(w)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)

    # ---- 指令列 ---------------------------------------------------------------------------------
    _NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"

    def parse_point(self, text: str):
        """x,y / @dx,dy（相對上一點）/ @L<θ（極座標）/ 只打長度（沿游標方向）→ (x, y) 或 None。"""
        t = text.strip().replace("，", ",").replace(" ", "")
        c = self.canvas
        ref = c.draft[-1] if c.draft else None
        if c.active_node is not None and c.tool == "select":
            sh = self._selected()
            ref = sh.pts[c.active_node] if sh else None
        m = re.fullmatch(rf"(@?)({self._NUM}),({self._NUM})", t)
        if m:
            x, y = float(m.group(2)), float(m.group(3))
            if m.group(1):
                if ref is None:
                    return None
                return ref[0] + x, ref[1] + y
            return x, y
        m = re.fullmatch(rf"@?({self._NUM})<({self._NUM})", t)
        if m and ref is not None:
            L, a = float(m.group(1)), math.radians(float(m.group(2)))
            return ref[0] + L * math.cos(a), ref[1] + L * math.sin(a)
        m = re.fullmatch(self._NUM, t)
        if m and ref is not None:                    # 直接打長度：沿著游標的方向
            L = float(t)
            dx, dy = c.cursor[0] - ref[0], c.cursor[1] - ref[1]
            n = math.hypot(dx, dy)
            if c.tool == "circle" and len(c.draft) == 1:
                return ref[0] + L, ref[1]             # 圓：打的是半徑
            if n < 1e-12:
                return ref[0] + L, ref[1]
            return ref[0] + dx / n * L, ref[1] + dy / n * L
        return None

    COMMANDS = {"L": "path", "LINE": "path", "PL": "path", "PLINE": "path", "P": "path", "A": "arc", "ARC": "arc",
                "REC": "rect", "RECTANG": "rect", "R": "rect", "C": "circle", "CIRCLE": "circle", "POL": "poly",
                "G": "poly", "B": "bezier", "SPL": "bezier", "SPLINE": "bezier", "DIM": "dim", "DLI": "dim",
                "DIMLINEAR": "dim", "DAL": "dim", "DIMALIGNED": "dim", "DI": "dist", "DIST": "dist",
                "MEA": "dist", "MEASUREGEOM": "dist", "PAN": "pan", "SEL": "select", "V": "select"}

    def _msg(self, text: str) -> None:
        self.lbl_cmd_msg.setText(text)

    def _cmd_enter(self) -> None:
        text = self.cmd.text().strip()
        self.cmd.clear()
        self.run_command(text)
        self.canvas.setFocus() if not text else self.cmd.setFocus()

    def run_command(self, text: str) -> bool:
        c = self.canvas
        up = text.strip().upper()
        if self.cmd_pending:
            return self._pending_input(text)
        if not up:
            if c.draft:
                c._finish(None)
                self._msg("完成")
            return True
        if c.tool in DRAW_TOOLS or (c.tool == "select" and c.active_node is not None):
            p = self.parse_point(text)
            if p is not None:
                if c.tool == "select":
                    self.move_active_node(p)
                    self._msg(f"節點移到 ({_fmt(p[0])}, {_fmt(p[1])})")
                else:
                    c.add_point(p)
                    self._msg(f"點 ({_fmt(p[0])}, {_fmt(p[1])})")
                return True
        word = up.split()[0]
        arg = up[len(word):].strip()
        if word in self.COMMANDS:
            tool = self.COMMANDS[word]
            if word in ("DAL", "DIMALIGNED"):
                self.combo_dim.setCurrentIndex(self.combo_dim.findData("a"))
            elif word in ("DLI", "DIMLINEAR"):
                self.combo_dim.setCurrentIndex(self.combo_dim.findData("auto"))
            self.set_tool(tool)
            self.cmd.setFocus()
            self._msg({"path": "微波線：點或打座標，Enter 結束", "rect": "矩形：第一角，再打 @寬,高",
                       "circle": "圓：圓心，再打半徑", "dim": "標註：第一點、第二點、標註線位置",
                       "dist": "量距離：第一點、第二點"}.get(tool, LAYER_INFO.get(tool, (tool,))[0]))
            return True
        if word in ("Z", "ZE", "ZOOM", "F", "FIT"):
            self.canvas.fit()
        elif word in ("U", "UNDO"):
            self.undo()
        elif word in ("REDO",):
            self.redo()
        elif word in ("E", "ERASE", "DEL"):
            if c.selected_dim is not None:
                self.delete_dim(c.selected_dim)
            else:
                self.delete_selected()
        elif word in ("OS", "OSNAP"):
            self.chk_osnap.toggle()
            self._msg("物件鎖點 " + ("開" if self.chk_osnap.isChecked() else "關"))
        elif word in ("OR", "ORTHO"):
            self.chk_ortho.toggle()
            self._msg("正交 " + ("開" if self.chk_ortho.isChecked() else "關"))
        elif word in ("GRID", "SN", "SNAP") and arg:
            try:
                self.spin_grid.setValue(float(arg))
                self.chk_snap.setChecked(True)
            except ValueError:
                self._msg("格線間距要是數字")
        elif word in ("M", "MOVE", "CO", "CP", "COPY", "RO", "ROTATE", "MI", "MIRROR"):
            if self._selected() is None:
                self._msg("先選一個圖形")
                return False
            kind = {"M": "move", "MOVE": "move", "CO": "copy", "CP": "copy", "COPY": "copy", "RO": "rotate",
                    "ROTATE": "rotate", "MI": "mirror", "MIRROR": "mirror"}[word]
            if arg:
                return self._pending_input(arg, kind)
            self.cmd_pending = kind
            self.lbl_prompt.setText({"move": "位移 ΔX,ΔY：", "copy": "複製到 ΔX,ΔY：", "rotate": "旋轉角度（°，逆時針）：",
                                     "mirror": "鏡像 X（左右）/ Y（上下）："}[kind])
            self.cmd.setFocus()
        else:
            self._msg(f"不認得：{text}")
            return False
        return True

    def _pending_input(self, text: str, kind: Optional[str] = None) -> bool:
        kind = kind or self.cmd_pending
        self.cmd_pending = None
        self.lbl_prompt.setText("指令：")
        t = text.strip().replace("，", ",").replace(" ", "").upper()
        try:
            if kind in ("move", "copy"):
                dx, dy = (float(v) for v in t.lstrip("@").split(","))
                (self.move_selected if kind == "move" else self.copy_selected)(dx, dy)
            elif kind == "rotate":
                self.rotate_selected(float(t))
            elif kind == "mirror":
                if t not in ("X", "Y"):
                    raise ValueError
                self.mirror_selected(t.lower())
        except ValueError:
            self._msg(f"格式不對：{text}")
            return False
        self._msg("完成")
        return True

    # ---- AutoCAD 式修改 ---------------------------------------------------------------------------
    def osnap_on(self) -> bool:
        return self.chk_osnap.isChecked()

    def ortho_on(self) -> bool:
        return self.chk_ortho.isChecked()

    def show_lengths(self) -> bool:
        return self.chk_lengths.isChecked()

    def dim_mode(self) -> str:
        return self.combo_dim.currentData() or "auto"

    def report_measure(self, p1, p2) -> None:
        L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        ang = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0]))
        self._msg(f"距離 {_fmt(L)} mm　ΔX {_fmt(p2[0] - p1[0])}　ΔY {_fmt(p2[1] - p1[1])}　角度 {ang:.3f}°")

    def delete_dim(self, i: int) -> None:
        if 0 <= i < len(self.design.dims):
            self.checkpoint()
            del self.design.dims[i]
            self.canvas.selected_dim = None
            self._commit()

    def delete_all_dims(self) -> None:
        if self.design.dims:
            self.checkpoint()
            self.design.dims = []
            self.canvas.selected_dim = None
            self._commit()

    def _ask_xy(self, title: str):
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, title, "ΔX, ΔY (mm)：", text="0, 0")
        if not ok:
            return None
        try:
            dx, dy = (float(v) for v in text.replace("，", ",").replace("@", "").split(","))
            return dx, dy
        except ValueError:
            QMessageBox.information(self, "DXF 編輯器", "請輸入兩個數字，例如 1.5, -2")
            return None

    def ask_move(self) -> None:
        if self.canvas.active_node is not None and self._selected() is not None:
            r = self._ask_xy("移動節點")
            if r:
                x, y = self._selected().pts[self.canvas.active_node]
                self.move_active_node((x + r[0], y + r[1]))
            return
        if self._selected() is not None:
            r = self._ask_xy("移動")
            if r:
                self.move_selected(*r)

    def ask_copy(self) -> None:
        if self._selected() is not None:
            r = self._ask_xy("複製到")
            if r:
                self.copy_selected(*r)

    def ask_rotate(self) -> None:
        if self._selected() is None:
            return
        from PyQt6.QtWidgets import QInputDialog
        a, ok = QInputDialog.getDouble(self, "旋轉", "角度（°，逆時針為正；繞圖形中心）：", 90.0, -360, 360, 3)
        if ok:
            self.rotate_selected(a)

    def move_selected(self, dx: float, dy: float) -> None:
        s = self._selected()
        if s is None:
            return
        self.checkpoint()
        s.translate(dx, dy)
        self._commit()

    def copy_selected(self, dx: float, dy: float) -> None:
        s = self._selected()
        if s is None:
            return
        self.checkpoint()
        c = s.copy()
        c.handle = ""
        c.translate(dx, dy)
        self.design.shapes.append(c)
        self.canvas.selected = len(self.design.shapes) - 1
        self.canvas.active_node = None
        self._commit()

    def rotate_selected(self, deg: float) -> None:
        s = self._selected()
        if s is None:
            return
        self.checkpoint()
        s.rotate(deg)
        self._commit()

    def mirror_selected(self, axis: str) -> None:
        s = self._selected()
        if s is None:
            return
        self.checkpoint()
        s.mirror(axis)
        self._commit()

    def center_selected(self, axis: str) -> None:
        s = self._selected()
        bb = self.design.board_bounds()
        if s is None or not bb:
            return
        cx, cy = s.center()
        self.checkpoint()
        if axis == "x":
            s.translate((bb[0] + bb[2]) / 2 - cx, 0)
        else:
            s.translate(0, (bb[1] + bb[3]) / 2 - cy)
        self._commit()

    def nudge(self, dx: float, dy: float) -> None:
        s = self._selected()
        if s is None:
            return
        i = self.canvas.active_node
        if i is not None and i < len(s.pts):
            self.move_active_node((s.pts[i][0] + dx, s.pts[i][1] + dy))
        else:
            self.move_selected(dx, dy)

    # ---- 節點座標 ---------------------------------------------------------------------------------
    def move_active_node(self, p) -> None:
        s = self._selected()
        i = self.canvas.active_node
        if s is None or i is None or i >= len(s.pts):
            return
        self.checkpoint()
        x, y = round(p[0], 9), round(p[1], 9)
        if s.bezier and s.is_anchor(i):                     # 錨點：控制點一起移
            dx, dy = x - s.pts[i][0], y - s.pts[i][1]
            n = len(s.pts)
            for j in ([(i - 1) % n, (i + 1) % n] if s.closed else [j for j in (i - 1, i + 1) if 0 <= j < n]):
                s.pts[j] = (s.pts[j][0] + dx, s.pts[j][1] + dy)
        if s.kind == "circle":
            s.pts[0] = (x, y)
        else:
            s.pts[i] = (x, y)
        self._commit()

    def step_node(self, k: int) -> None:
        s = self._selected()
        if s is None or s.kind == "circle" or not s.pts:
            return
        n = len(s.pts)
        i = self.canvas.active_node if self.canvas.active_node is not None else (-1 if k > 0 else 0)
        for _ in range(n):
            i = (i + k) % n
            if s.is_anchor(i):
                break
        self.canvas.active_node = i
        self.table.setCurrentCell(i, 0)
        self.canvas.rebuild()
        self._refresh_node_box()

    def _prev_node(self, s: Shape, i: int):
        if i > 0:
            j = i - 1
            while j > 0 and not s.is_anchor(j):
                j -= 1
            return s.pts[j]
        if s.closed and len(s.pts) > 1:
            j = len(s.pts) - 1
            while j > 0 and not s.is_anchor(j):
                j -= 1
            return s.pts[j]
        return None

    def _refresh_node_box(self) -> None:
        s = self._selected()
        i = self.canvas.active_node
        show = s is not None and i is not None and i < len(s.pts)
        self.node_box.setVisible(show)
        if not show:
            return
        self._sync = True
        x, y = s.pts[i]
        what = "圓心" if s.kind == "circle" else ("錨點" if s.is_anchor(i) else "控制點")
        self.lbl_node.setText(f"第 {i + 1} 個{what}（共 {len(s.pts)} 個）")
        self.sp_nx.setValue(x)
        self.sp_ny.setValue(y)
        prev = self._prev_node(s, i) if s.kind != "circle" else None
        for w_ in (self.sp_nlen, self.sp_nang):
            w_.setEnabled(prev is not None)
        if prev is not None:
            self.sp_nlen.setValue(math.hypot(x - prev[0], y - prev[1]))
            self.sp_nang.setValue(math.degrees(math.atan2(y - prev[1], x - prev[0])))
        self._sync = False

    def _node_xy_changed(self) -> None:
        if not self._sync:
            self.move_active_node((self.sp_nx.value(), self.sp_ny.value()))

    def _node_polar_changed(self) -> None:
        s = self._selected()
        i = self.canvas.active_node
        if self._sync or s is None or i is None:
            return
        prev = self._prev_node(s, i)
        if prev is None:
            return
        L, a = self.sp_nlen.value(), math.radians(self.sp_nang.value())
        self.move_active_node((prev[0] + L * math.cos(a), prev[1] + L * math.sin(a)))

    # ---- 狀態 ---------------------------------------------------------------------------------
    def grid_step(self) -> float:
        return self.spin_grid.value() if self.chk_snap.isChecked() else 0.0

    def hidden_layers(self) -> set:
        out = set()
        for i in range(self.list_layers.count()):
            it = self.list_layers.item(i)
            if it.checkState() != Qt.CheckState.Checked:
                out.add(it.data(Qt.ItemDataRole.UserRole))
        return out

    def current_layer(self) -> str:
        return self.combo_layer.currentData() or LAYERS[self.design.mode][0]

    def set_tool(self, tool: str) -> None:
        self.canvas.tool = tool
        self.canvas.draft = []
        if tool != "dist":
            self.canvas.measure = None
        self.tool_actions[tool].setChecked(True)
        self.canvas.setCursor(Qt.CursorShape.OpenHandCursor if tool == "pan" else
                              Qt.CursorShape.CrossCursor if tool == "zoom" else
                              Qt.CursorShape.CrossCursor if tool != "select" else Qt.CursorShape.ArrowCursor)
        if tool in ("circle",) and self.current_layer() not in ("h", "v"):
            self.combo_layer.setCurrentIndex(max(0, self.combo_layer.findData("h")))
        self.canvas.rebuild()
        self.canvas.setFocus()

    def _title(self) -> None:
        name = os.path.basename(self.design.path) if self.design.path else "未命名"
        self.setWindowTitle(f"DXF 編輯器 — {name}{' *' if self.dirty else ''}（{self.design.mode}）")

    def _after_load(self) -> None:
        self._sync = True
        self.combo_mode.setCurrentText(self.design.mode)
        self.chk_auto.setChecked(self.design.auto_ground)
        self.chk_bridge.setChecked(self.design.bridge_gaps)
        self._sync = False
        self._refresh_layers()
        self._refresh_board()
        self.undo_stack, self.redo_stack = [], []
        self.dirty = False
        self.canvas.selected = None
        self._refresh_props()
        self._update_check()
        self._title()
        self.canvas.fit()

    def _refresh_layers(self) -> None:
        mode = self.design.mode
        self.list_layers.blockSignals(True)
        hidden = self.hidden_layers() if self.list_layers.count() else set()
        self.list_layers.clear()
        others = ({s.layer for s in self.design.shapes} | {f["layer"] for f in self.design.foreign}) \
            - set(LAYERS[mode]) - {PATH_LAYER}
        others -= {DIM_LAYER}
        names = list(LAYERS[mode]) + [PATH_LAYER, DIM_LAYER] + sorted(others)
        for name in names:
            desc = "標註（不匯入）" if name == DIM_LAYER else LAYER_INFO.get(name, ("其他",))[0]
            it = QListWidgetItem(f"{name}　{desc}")
            it.setData(Qt.ItemDataRole.UserRole, name)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked if name in hidden else Qt.CheckState.Checked)
            if name in FILL:
                it.setForeground(QBrush(QColor(FILL[name])))
            self.list_layers.addItem(it)
        self.list_layers.blockSignals(False)
        cur = self.combo_layer.currentData()
        for combo in (self.combo_layer, self.combo_shape_layer):
            combo.blockSignals(True)
            combo.clear()
            for name in LAYERS[mode]:
                combo.addItem(f"{name}　{LAYER_INFO[name][0]}", name)
            combo.blockSignals(False)
        i = self.combo_layer.findData(cur)
        self.combo_layer.setCurrentIndex(i if i >= 0 else 0)
        self.chk_auto.setEnabled(mode == "CPW")
        self.chk_bridge.setEnabled(mode == "CPW")

    def _refresh_board(self) -> None:
        bb = self.design.board_bounds()
        if not bb:
            return
        for sp, val in ((self.sp_x0, bb[0]), (self.sp_y0, bb[1]), (self.sp_w, bb[2] - bb[0]), (self.sp_h, bb[3] - bb[1])):
            sp.blockSignals(True)
            sp.setValue(val)
            sp.blockSignals(False)

    def _update_check(self) -> None:
        warns = self.design.check()
        ports = self.design.ports()
        txt = f"<b>Wave Port：{len(ports)} 個</b>" + (f"（{'、'.join(s for _p, s in ports)}）" if ports else "")
        if warns:
            txt += "<br><span style='color:#E53935'>⚠ " + "<br>⚠ ".join(warns) + "</span>"
        else:
            txt += "<br><span style='color:#43A047'>✔ 可以給 HFSS 建模</span>"
        self.lbl_check.setText(txt)

    def _refresh_props(self) -> None:
        self._sync = True
        idx = self.canvas.selected
        s = self.design.shapes[idx] if idx is not None and idx < len(self.design.shapes) else None
        rows = {"圖層": s is not None and s.kind != "path", "線寬": s is not None and s.kind == "path",
                "間隙": s is not None and s.kind == "path" and self.design.mode == "CPW",
                "圓心 X": s is not None and s.kind == "circle", "圓心 Y": s is not None and s.kind == "circle",
                "半徑": s is not None and s.kind == "circle"}
        for i in range(self.form.rowCount()):
            label = self.form.itemAt(i, QFormLayout.ItemRole.LabelRole).widget()
            field = self.form.itemAt(i, QFormLayout.ItemRole.FieldRole).widget()
            show = rows.get(label.text(), False)
            label.setVisible(show)
            field.setVisible(show)
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        nodes = s is not None and s.kind in ("path", "poly")
        for wdg in (self.table, self.btn_add_node, self.btn_del_node, self.btn_reverse):
            wdg.setVisible(nodes)
        self.btn_delete.setVisible(s is not None)
        if s is None:
            self.lbl_sel.setText("點選圖形來編輯。<br><br>拖白色方塊：移動節點<br>拖橘色菱形：把線段彎成圓弧<br>"
                                 "雙擊線段：加節點<br>右鍵節點：刪除、設圓角<br>拖圖形：整個移動<br>"
                                 "表格「圓角 R」：轉角半徑；「弧 °」：到下一點那段的圓心角（正 = 往右彎）")
        else:
            kind = {"path": "微波線（中心線＋線寬）", "poly": "多邊形", "circle": "圓"}[s.kind]
            if s.bezier:
                kind = "曲線微波線（Bézier）" if s.kind == "path" else "曲線多邊形（Bézier）"
            extra = "<br><span style='color:gray'>方塊 = 錨點、藍圈 = 控制點（拖控制點會保持平滑，按住 Alt 可以折角）</span>" \
                if s.bezier else ""
            total = ""
            if s.kind in ("path", "poly"):
                cl = s.centerline()
                if s.closed and cl:
                    cl = cl + [cl[0]]
                L = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(cl, cl[1:]))
                total = f"<br>{'中心線長度' if s.kind == 'path' else '周長'} {_fmt(L)} mm"
            self.lbl_sel.setText(f"<b>{kind}</b>　圖層 {s.layer}{total}{extra}")
            if s.kind == "path":
                self.sp_width.setValue(s.width)
                self.sp_gap.setValue(s.gap)
            elif s.kind == "circle":
                self.sp_cx.setValue(s.pts[0][0])
                self.sp_cy.setValue(s.pts[0][1])
                self.sp_r.setValue(s.r)
            if s.kind != "path":
                self.combo_shape_layer.setCurrentIndex(max(0, self.combo_shape_layer.findData(s.layer)))
            if nodes:
                self._fill_table(s)
        self.table.blockSignals(False)
        self._sync = False
        if s is None or self.canvas.active_node is None or self.canvas.active_node >= len(s.pts):
            self.canvas.active_node = None
        self._refresh_node_box()
        dm = self.canvas.selected_dim
        if s is None and dm is not None and dm < len(self.design.dims):
            d_ = self.design.dims[dm]
            self.lbl_sel.setText(f"<b>標註</b>　{ {'h': '水平', 'v': '垂直', 'a': '對齊'}[d_.kind] }距離 = {_fmt(d_.value())} mm"
                                 "<br><span style='color:gray'>Delete 刪除</span>")

    # ---- 改動（每次改之前存復原點）-----------------------------------------------------------------
    def checkpoint(self) -> None:
        self.undo_stack.append(self.design.snapshot())
        if len(self.undo_stack) > 200:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def drop_checkpoint(self) -> None:
        if self.undo_stack:
            self.undo_stack.pop()

    def _on_changed(self) -> None:
        self.dirty = True
        self._refresh_board()
        self._update_check()
        self._title()

    def _commit(self) -> None:
        self.canvas.rebuild()
        self._on_changed()
        self._refresh_props()

    def undo(self) -> None:
        if not self.undo_stack:
            return
        self.redo_stack.append(self.design.snapshot())
        self.design.restore(self.undo_stack.pop())
        self._restore_ui()

    def redo(self) -> None:
        if not self.redo_stack:
            return
        self.undo_stack.append(self.design.snapshot())
        self.design.restore(self.redo_stack.pop())
        self._restore_ui()

    def _restore_ui(self) -> None:
        if self.canvas.selected is not None and self.canvas.selected >= len(self.design.shapes):
            self.canvas.selected = None
        if self.canvas.selected_dim is not None and self.canvas.selected_dim >= len(self.design.dims):
            self.canvas.selected_dim = None
        self._sync = True
        self.combo_mode.setCurrentText(self.design.mode)
        self.chk_auto.setChecked(self.design.auto_ground)
        self.chk_bridge.setChecked(self.design.bridge_gaps)
        self._sync = False
        self._refresh_layers()
        self._commit()

    def _selected(self) -> Optional[Shape]:
        i = self.canvas.selected
        return self.design.shapes[i] if i is not None and i < len(self.design.shapes) else None

    def _props_changed(self) -> None:
        s = self._selected()
        if self._sync or s is None:
            return
        self.checkpoint()
        if s.kind == "path":
            s.width, s.gap = self.sp_width.value(), self.sp_gap.value()
        elif s.kind == "circle":
            s.pts[0] = (self.sp_cx.value(), self.sp_cy.value())
            s.r = self.sp_r.value()
        self.canvas.rebuild()
        self._on_changed()

    def _layer_changed(self, _t) -> None:
        if not self._sync and self.combo_shape_layer.currentData():
            self.set_layer(self.combo_shape_layer.currentData())

    def set_layer(self, layer: str) -> None:
        s = self._selected()
        if s is None or s.kind == "path" or s.layer == layer:
            return
        self.checkpoint()
        s.layer = layer
        self._commit()

    def _fill_table(self, s: Shape) -> None:
        """節點表格：X、Y、圓角 R（節點）、弧 °（到下一點那一段）；Bézier 第三欄是錨點 / 控制點。"""
        if s.bezier:
            self.table.setColumnCount(3)
            self.table.setHorizontalHeaderLabels(["X (mm)", "Y (mm)", "種類"])
        else:
            self.table.setColumnCount(4)
            self.table.setHorizontalHeaderLabels(["X (mm)", "Y (mm)", "圓角 R", "弧 °"])
            s.norm()
        self.table.setRowCount(len(s.pts))
        n = len(s.pts)
        ro = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        for r, (x, y) in enumerate(s.pts):
            self.table.setItem(r, 0, QTableWidgetItem(_fmt(x)))
            self.table.setItem(r, 1, QTableWidgetItem(_fmt(y)))
            if s.bezier:
                it = QTableWidgetItem("錨點" if s.is_anchor(r) else "控制點")
                it.setFlags(ro)
                self.table.setItem(r, 2, it)
                continue
            can_fillet = s.closed or 0 < r < n - 1
            it = QTableWidgetItem(_fmt(s.fillets[r]) if s.fillets[r] else "")
            if not can_fillet:
                it.setFlags(ro)
            self.table.setItem(r, 2, it)
            has_seg = r < s.n_segs()
            b = s.bulges[r] if has_seg else 0.0
            it = QTableWidgetItem(f"{math.degrees(4 * math.atan(b)):.2f}".rstrip("0").rstrip(".") if b else "")
            if not has_seg:
                it.setFlags(ro)
            self.table.setItem(r, 3, it)

    def _table_changed(self, item: QTableWidgetItem) -> None:
        s = self._selected()
        if self._sync or s is None:
            return
        r, c = item.row(), item.column()
        text = item.text().strip()
        try:
            v = float(text) if text else 0.0
        except ValueError:
            self._refresh_props()
            return
        self.checkpoint()
        if c in (0, 1):
            x, y = s.pts[r]
            s.pts[r] = (v, y) if c == 0 else (x, v)
        elif c == 2 and not s.bezier:
            s.norm()
            s.fillets[r] = max(0.0, v)
        elif c == 3 and not s.bezier:
            s.norm()
            v = max(-359.0, min(359.0, v))
            s.bulges[r] = math.tan(math.radians(v) / 4.0)
        self.canvas.rebuild()
        self._on_changed()
        if c >= 2:
            self._refresh_props()

    def set_fillet(self, i: int, r: float) -> None:
        s = self._selected()
        if s is None or s.bezier or s.kind == "circle":
            return
        self.checkpoint()
        s.norm()
        s.fillets[i] = max(0.0, r)
        self._commit()

    def ask_fillet(self, i: int) -> None:
        s = self._selected()
        if s is None:
            return
        s.norm()
        from PyQt6.QtWidgets import QInputDialog
        cur = s.fillets[i] or max(s.width * 2, 1.0) if s.kind == "path" else (s.fillets[i] or 1.0)
        r, ok = QInputDialog.getDouble(self, "圓角", "轉角半徑 (mm)：", cur, 0.0, 1e4, 4)
        if ok:
            self.set_fillet(i, r)

    def set_bulge(self, i: int, b: float) -> None:
        s = self._selected()
        if s is None or s.bezier or s.kind == "circle":
            return
        self.checkpoint()
        s.norm()
        s.bulges[i] = b
        self._commit()

    def to_bezier(self) -> None:
        """折線 → 經過同樣節點的平滑曲線（之後拖控制點微調）。"""
        s = self._selected()
        if s is None or s.kind not in ("path", "poly") or s.bezier:
            return
        self.checkpoint()
        s.pts = G.bezier_from_anchors(s.pts, closed=s.closed)
        s.smooth, s.bulges, s.fillets = "bezier", [], []
        self._commit()

    def to_polyline(self) -> None:
        s = self._selected()
        if s is None or not s.bezier:
            return
        self.checkpoint()
        s.pts = [p for i, p in enumerate(s.pts) if s.is_anchor(i)]
        s.smooth = ""
        s.norm()
        self._commit()

    def add_node_after(self) -> None:
        s = self._selected()
        if s is None or s.kind not in ("path", "poly"):
            return
        r = self.table.currentRow()
        if s.bezier:
            seg = max(0, min((r if r >= 0 else len(s.pts) - 1) // 3, len(G.bezier_segments(s.pts, s.closed)) - 1))
            self.checkpoint()
            new = s.insert_after(seg)
        else:
            s.norm()
            segs = s.n_segs()
            r = segs - 1 if r < 0 or r >= segs else r
            self.checkpoint()
            new = s.insert_after(r)
        self._commit()
        self.table.setCurrentCell(new, 0)

    def delete_node(self, i: int) -> None:
        s = self._selected()
        if s is None or s.kind not in ("path", "poly") or not (0 <= i < len(s.pts)):
            return
        if not s.can_delete(i):
            msg = ("控制點不能刪：刪它旁邊的錨點，或右鍵圖形「改回折線」。" if s.bezier and not s.is_anchor(i)
                   else "節點不能再少了（多邊形至少 3 個、微波線至少 2 個）。")
            QMessageBox.information(self, "DXF 編輯器", msg)
            return
        self.checkpoint()
        s.delete_node(i)
        self._commit()

    def reverse_selected(self) -> None:
        s = self._selected()
        if s is None or s.kind not in ("path", "poly"):
            return
        self.checkpoint()
        s.pts.reverse()
        if not s.bezier:
            s.norm()
            if s.closed:                                  # 封閉：第 i 段（i → i+1）變成（n-1-i → …）
                n = len(s.pts)
                s.bulges = [-s.bulges[(n - 2 - k) % n] for k in range(n)]
            else:
                s.bulges = [-b for b in reversed(s.bulges)]
            s.fillets = list(reversed(s.fillets))
        self._commit()

    def delete_selected(self) -> None:
        i = self.canvas.selected
        if i is None or i >= len(self.design.shapes):
            return
        self.checkpoint()
        del self.design.shapes[i]
        self.canvas.selected = None
        self._commit()

    def duplicate_selected(self) -> None:
        s = self._selected()
        if s is None:
            return
        self.checkpoint()
        c = s.copy()
        c.handle = ""
        step = max(self.spin_grid.value(), 0.5)
        c.translate(step, -step)
        self.design.shapes.append(c)
        self.canvas.selected = len(self.design.shapes) - 1
        self._commit()

    def apply_board(self) -> None:
        self.checkpoint()
        try:
            self.design.set_board(self.sp_x0.value(), self.sp_y0.value(), self.sp_w.value(), self.sp_h.value(),
                                  follow=self.chk_follow.isChecked())
        except DxfError as e:
            self.drop_checkpoint()
            QMessageBox.warning(self, "DXF 編輯器", str(e))
            return
        self._commit()
        self.canvas.fit()

    def change_mode(self, mode: str) -> None:
        if self._sync or mode == self.design.mode:
            return
        self.checkpoint()
        old_board, new_board = BOARD_LAYERS[self.design.mode][0], BOARD_LAYERS[mode][0]
        for s in self.design.shapes:
            if s.layer == old_board:
                s.layer = new_board
        if mode == "MS":
            self.design.shapes = [s for s in self.design.shapes if s.layer not in ("ub", "v")]
        self.design.mode = mode
        self.design.auto_ground = mode == "CPW" and not any(s.layer == "ub" for s in self.design.shapes)
        self._sync = True
        self.chk_auto.setChecked(self.design.auto_ground)
        self._sync = False
        self._refresh_layers()
        self._commit()

    def _toggle_bridge(self, on: bool) -> None:
        if self._sync or on == self.design.bridge_gaps:
            return
        self.checkpoint()
        self.design.bridge_gaps = on
        self._commit()

    def _toggle_auto(self, on: bool) -> None:
        if self._sync or on == self.design.auto_ground:
            return
        self.checkpoint()
        self.design.auto_ground = on
        self._commit()

    def convert_dialog(self) -> None:
        dlg = ConvertDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.convert_design(dlg.target(), dlg.sp_w.value(), dlg.sp_g.value(),
                            vias=(dlg.sp_vp.value(), dlg.sp_vd.value(), dlg.sp_vc.value())
                            if dlg.target() == "CPW" and dlg.via.isChecked() else None,
                            replace_vias=dlg.chk_replace_vias.isChecked())

    def convert_design(self, mode: str, width: float, gap: float, vias=None, replace_vias: bool = True) -> int:
        """MS ⇄ CPW；回傳加了幾個過孔。"""
        self.checkpoint()
        self.design.convert(mode, width, gap)
        n = 0
        if mode == "CPW" and vias:
            if replace_vias:
                self.design.shapes = [s for s in self.design.shapes if s.layer != "v"]
            new = self.design.via_fence(*vias)
            self.design.shapes += new
            n = len(new)
        self.new_width.setValue(width)
        if mode == "CPW":
            self.new_gap.setValue(gap)
        self.canvas.selected = None
        self.canvas.active_node = None
        self._sync = True
        self.combo_mode.setCurrentText(mode)
        self.chk_auto.setChecked(self.design.auto_ground)
        self._sync = False
        self._refresh_layers()
        self._commit()
        self.statusBar().showMessage(f"已轉成 {mode}：線寬 {_fmt(width)} mm"
                                     + (f"、間隙 {_fmt(gap)} mm、過孔 {n} 個" if mode == "CPW" else ""), 8000)
        return n

    # ---- 檔案 ---------------------------------------------------------------------------------
    def _ask_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, "DXF 編輯器", "目前的修改還沒存檔，要先存嗎？",
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                 | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Save:
            return self.save()
        return r == QMessageBox.StandardButton.Discard

    def new_design(self, mode: str, trace: bool = True) -> None:
        if not self._ask_discard():
            return
        self.design = Design.template(mode, width=self.new_width.value(), gap=self.new_gap.value(), trace=trace)
        self._after_load()
        self.dirty = True
        self._title()

    def open_dialog(self) -> None:
        if not self._ask_discard():
            return
        start = os.path.dirname(self.design.path) if self.design.path else ""
        f, _ = QFileDialog.getOpenFileName(self, "開啟 DXF", start, "DXF (*.dxf)")
        if f:
            self.open_file(f)

    def open_file(self, path: str) -> bool:
        try:
            self.design = Design.load(path)
        except DxfError as e:
            QMessageBox.warning(self, "DXF 編輯器", str(e))
            return False
        self._after_load()
        return True

    def save(self) -> bool:
        if not self.design.path:
            return self.save_as()
        return self._save_to(self.design.path)

    def save_as(self) -> bool:
        start = self.design.path or os.path.join(os.path.expanduser("~"), "Desktop", "board.dxf")
        f, _ = QFileDialog.getSaveFileName(self, "另存 DXF", start, "DXF (*.dxf)")
        if not f:
            return False
        if not f.lower().endswith(".dxf"):
            f += ".dxf"
        return self._save_to(f)

    def _save_to(self, path: str) -> bool:
        warns = self.design.check()
        if warns:
            r = QMessageBox.warning(self, "DXF 編輯器", "存檔前的檢查：\n\n• " + "\n• ".join(warns) + "\n\n仍要存檔嗎？",
                                    QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
            if r != QMessageBox.StandardButton.Save:
                return False
        if any(ord(ch) > 127 for ch in path):
            QMessageBox.information(self, "DXF 編輯器", "提醒：HFSS 專案路徑需要全英文，DXF 的路徑最好也不要有中文。")
        try:
            self.design.save(path)
        except DxfError as e:
            QMessageBox.warning(self, "DXF 編輯器", str(e))
            return False
        self.dirty = False
        self._title()
        self.statusBar().showMessage(f"已存檔：{path}", 5000)
        self.saved.emit(path)
        return True

    def closeEvent(self, ev) -> None:
        if self._ask_discard():
            ev.accept()
        else:
            ev.ignore()
