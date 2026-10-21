"""DXF 編輯器（v3.3）：改板材大小、用節點畫 / 改微波線、螺絲孔、過孔，存成 HFSS 建模用的 DXF。

    ┌──────────┬──────────────────────────────┬──────────┐
    │ 設計      │  畫布（mm，Y 軸向上，格線吸附）   │ 屬性      │
    │ 模式 / 板材│  左鍵：選取、拖節點、拖整個圖形    │ 線寬 / 間隙│
    │ 新微波線  │  雙擊線段：加節點；右鍵：刪節點    │ 節點表格   │
    │ 圖層      │  滾輪：縮放；中鍵 / 空白鍵：平移   │          │
    └──────────┴──────────────────────────────┴──────────┘

工具：F1 選取、F2 平移、P 微波線（點節點，雙擊或 Enter 結束，Shift = 0/45/90°）、G 多邊形、R 矩形、C 圓（螺絲孔 / 過孔）。
資料與存檔在 dxf_model.py。
"""
from __future__ import annotations

import math
import os
from typing import Callable, List, Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (QAction, QActionGroup, QBrush, QColor, QKeySequence, QPainter, QPainterPath, QPainterPathStroker,
                         QPen, QPolygonF, QShortcut)
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout,
                             QGraphicsScene, QGraphicsView, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QListWidget,
                             QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QTableWidget,
                             QTableWidgetItem, QToolBar, QToolButton, QVBoxLayout, QWidget)

from dxf_model import BOARD_LAYERS, LAYER_INFO, LAYERS, PATH_LAYER, Design, DxfError, Shape

FILL = {"b": "#7CB342", "db": "#7CB342", "ub": "#FFA726", "g": "#E53935", "h": "#9E9E9E", "v": "#1E88E5"}
ALPHA = {"b": 70, "db": 70, "ub": 110, "g": 170, "h": 200, "v": 200}
DRAW_ORDER = ("b", "db", "ub", "g", "h", "v")          # 後面的畫在上面、先被點到
NODE_PX = 7
SNAP_EDGE_PX = 10


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
        self.scene().setSceneRect(-1e5, -1e5, 2e5, 2e5)   # 很大的範圍：平移不會被卡住
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
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

    # ---- 座標 ---------------------------------------------------------------------------------
    @property
    def design(self) -> Design:
        return self.ed.design

    def px(self) -> float:
        """1 px 是幾 mm。"""
        return 1.0 / max(abs(self.transform().m11()), 1e-12)

    def snap(self, x: float, y: float, free: bool = False) -> Tuple[float, float]:
        if free:
            return x, y
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
        elif s.kind == "poly":
            path.addPolygon(_poly(s.pts))
            path.closeSubpath()
        else:
            from dxf_model import _polys, _ring, path_outline
            for poly in _polys(path_outline(s.pts, s.width)):
                path.addPolygon(_poly(_ring(poly)))
                path.closeSubpath()
            if path.isEmpty() and s.pts:            # 只有一個節點：給一條細線讓它點得到
                line = QPainterPath(QPointF(*s.pts[0]))
                for p in s.pts[1:]:
                    line.lineTo(QPointF(*p))
                st = QPainterPathStroker()
                st.setWidth(max(s.width, 6 * self.px()))
                path = st.createStroke(line)
        return path

    def rebuild(self) -> None:
        sc = self.scene()
        sc.clear()
        d = self.design
        hidden = self.ed.hidden_layers()
        dark = self.palette().window().color().lightness() < 128
        edge = "#ECEFF1" if dark else "#263238"
        for f in d.foreign:
            if f["layer"] in hidden or len(f["pts"]) < 2:
                continue
            path = QPainterPath(QPointF(*f["pts"][0]))
            for p in f["pts"][1:]:
                path.lineTo(QPointF(*p))
            sc.addPath(path, self._pen("#90A4AE", 1, Qt.PenStyle.DashLine))
        gen = d.generated()
        order = {name: i for i, name in enumerate(DRAW_ORDER)}
        items = sorted(enumerate(d.shapes), key=lambda kv: order.get(kv[1].layer, 9))
        # 自動上地畫在訊號線下面
        if "ub" not in hidden:
            for pts in gen.get("ub", []):
                sc.addPolygon(_poly(pts), self._pen(FILL["ub"], 1, Qt.PenStyle.DashLine), QBrush(_qc(FILL["ub"], 80)))
        for idx, s in items:
            if s.layer in hidden or (s.kind == "path" and PATH_LAYER in hidden and "g" in hidden):
                continue
            color = FILL.get(s.layer, "#8E24AA")
            it = sc.addPath(self.shape_path(s), self._pen(edge if s.layer in ("h", "v") else color, 1),
                            QBrush(_qc(color, ALPHA.get(s.layer, 120))))
            it.setData(0, idx)
            if s.kind == "path" and PATH_LAYER not in hidden and len(s.pts) >= 2:
                line = QPainterPath(QPointF(*s.pts[0]))
                for p in s.pts[1:]:
                    line.lineTo(QPointF(*p))
                sc.addPath(line, self._pen(edge, 1, Qt.PenStyle.DashDotLine))
        # 埠（微波線端點在板邊）
        r = 5 * self.px()
        for (x, y), _side in d.ports():
            sc.addEllipse(QRectF(x - r, y - r, 2 * r, 2 * r), self._pen("#00C853", 2), QBrush(Qt.BrushStyle.NoBrush))
        # 選取
        if self.selected is not None and self.selected < len(d.shapes):
            s = d.shapes[self.selected]
            sc.addPath(self.shape_path(s), self._pen("#2979FF", 2.5))
            h = NODE_PX / 2 * self.px()
            nodes = s.pts if s.kind != "circle" else [s.pts[0], (s.pts[0][0] + s.r, s.pts[0][1])]
            for i, (x, y) in enumerate(nodes):
                fill = "#FFFFFF" if not (s.kind == "path" and i in (0, len(nodes) - 1)) else "#00C853"
                sc.addRect(QRectF(x - h, y - h, 2 * h, 2 * h), self._pen("#2979FF", 1.5), QBrush(QColor(fill)))
        # 畫圖中
        if self.draft:
            pts = self.draft + [self.cursor]
            pen = self._pen("#2979FF", 1.5, Qt.PenStyle.DashLine)
            if self.tool == "rect" and len(self.draft) == 1:
                (x0, y0), (x1, y1) = self.draft[0], self.cursor
                sc.addRect(QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)), pen)
            elif self.tool == "circle" and len(self.draft) == 1:
                (cx, cy), (x1, y1) = self.draft[0], self.cursor
                rr = math.hypot(x1 - cx, y1 - cy)
                sc.addEllipse(QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr), pen)
            else:
                if self.tool == "path" and len(pts) >= 2:
                    from dxf_model import _polys, _ring, path_outline
                    for poly in _polys(path_outline(pts, self.ed.new_width.value())):
                        sc.addPolygon(_poly(_ring(poly)), self._pen(FILL["g"], 1), QBrush(_qc(FILL["g"], 90)))
                path = QPainterPath(QPointF(*pts[0]))
                for p in pts[1:]:
                    path.lineTo(QPointF(*p))
                sc.addPath(path, pen)
        self.viewport().update()

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
    def node_at(self, view_pos) -> Optional[int]:
        if self.selected is None or self.selected >= len(self.design.shapes):
            return None
        s = self.design.shapes[self.selected]
        nodes = s.pts if s.kind != "circle" else [s.pts[0], (s.pts[0][0] + s.r, s.pts[0][1])]
        for i, (x, y) in enumerate(nodes):
            p = self.mapFromScene(QPointF(x, y))
            if abs(p.x() - view_pos.x()) <= NODE_PX and abs(p.y() - view_pos.y()) <= NODE_PX:
                return i
        return None

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

    def _constrain(self, x: float, y: float, ev) -> Tuple[float, float]:
        """Shift：從上一個節點只能 0 / 45 / 90°。"""
        if not self.draft or not (ev.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            return x, y
        x0, y0 = self.draft[-1]
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
        sx, sy = self._constrain(*self.snap(x, y, free), ev)
        if self.tool == "select":
            node = self.node_at(ev.position())
            if node is not None:
                self.ed.checkpoint()
                self.drag = {"kind": "node", "i": node, "free": free}
                return
            idx = self.shape_at(x, y)
            if idx != self.selected:
                self.select(idx)
            if idx is not None:
                self.ed.checkpoint()
                self.drag = {"kind": "body", "start": self.snap(x, y, free), "orig": self.design.shapes[idx].copy()}
            return
        # 畫圖工具
        if self.tool in ("rect", "circle") and self.draft:
            self._finish((sx, sy))
            return
        self.draft.append((sx, sy))
        if self.tool in ("rect", "circle"):
            self.cursor = (sx, sy)
        self.rebuild()

    def mouseMoveEvent(self, ev) -> None:
        x, y = self._scene_xy(ev)
        free = bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)
        if self.drag and self.drag["kind"] == "pan":
            d = ev.position() - self.drag["pos"]
            self.drag["pos"] = ev.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(round(d.x())))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(round(d.y())))
            return
        sx, sy = self._constrain(*self.snap(x, y, free or bool(self.drag and self.drag.get("free"))), ev)
        self.cursor = (sx, sy)
        info = ""
        if self.draft:
            x0, y0 = self.draft[-1]
            info = f"長 {_fmt(math.hypot(sx - x0, sy - y0))} mm"
        self.cursor_moved.emit(sx, sy, info)
        if self.drag and self.selected is not None:
            s = self.design.shapes[self.selected]
            if self.drag["kind"] == "node":
                i = self.drag["i"]
                if s.kind == "circle":
                    if i == 0:
                        s.pts[0] = (sx, sy)
                    else:
                        s.r = max(1e-4, math.hypot(sx - s.pts[0][0], sy - s.pts[0][1]))
                else:
                    s.pts[i] = (sx, sy)
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
            hover = self.node_at(ev.position()) is not None
            self.setCursor(Qt.CursorShape.SizeAllCursor if hover else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, ev) -> None:
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
        if self.tool in ("path", "poly"):
            if self.draft and len(self.draft) >= 2 and self.draft[-1] == self.draft[-2]:
                self.draft.pop()
            self._finish(None)
            return
        if self.tool == "select" and self.selected is not None:
            s = self.design.shapes[self.selected]
            if s.kind in ("path", "poly"):
                x, y = self._scene_xy(ev)
                self.ed.checkpoint()
                i, p = _nearest_segment(s.pts, (x, y), closed=s.kind == "poly")
                s.pts.insert(i + 1, self.snap(*p))
                self.rebuild()
                self.changed.emit()
                self.selection_changed.emit()

    def wheelEvent(self, ev) -> None:
        f = 1.2 if ev.angleDelta().y() > 0 else 1 / 1.2
        self.scale(f, f)
        self.rebuild()

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
            else:
                self.select(None)
        elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.draft:
                self.draft.pop()
                self.rebuild()
            else:
                self.ed.delete_selected()
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
        if tool == "path" and len(pts) >= 2:
            shape = Shape("path", "g", pts, width=self.ed.new_width.value(), gap=self.ed.new_gap.value())
        elif tool == "poly" and len(pts) >= 3:
            shape = Shape("poly", self.ed.current_layer(), pts)
        elif tool == "rect" and len(pts) == 2:
            (x0, y0), (x1, y1) = pts
            if x0 != x1 and y0 != y1:
                shape = Shape("poly", self.ed.current_layer(),
                              [(min(x0, x1), min(y0, y1)), (max(x0, x1), min(y0, y1)), (max(x0, x1), max(y0, y1)),
                               (min(x0, x1), max(y0, y1))])
        elif tool == "circle" and len(pts) == 2:
            r = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
            if r > 0:
                layer = self.ed.current_layer()
                shape = Shape("circle", layer if layer in ("h", "v") else "h", [pts[0]], r=r)
        if shape is None:
            self.rebuild()
            return
        self.ed.checkpoint()
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
        node = self.node_at(ev.position())
        if node is not None:
            s = self.design.shapes[self.selected]
            if s.kind != "circle":
                a = m.addAction("刪除這個節點")
                a.setEnabled(len(s.pts) > (3 if s.kind == "poly" else 2))
                a.triggered.connect(lambda: self.ed.delete_node(node))
        else:
            idx = self.shape_at(x, y)
            if idx is not None:
                self.select(idx)
                m.addAction("刪除（Delete）", self.ed.delete_selected)
                m.addAction("複製一份", self.ed.duplicate_selected)
                s = self.design.shapes[idx]
                if s.kind in ("poly", "circle"):
                    sub = m.addMenu("改到圖層")
                    for layer in LAYERS[self.design.mode]:
                        act = sub.addAction(f"{layer}　{LAYER_INFO[layer][0]}")
                        act.triggered.connect(lambda _c=False, la=layer: self.ed.set_layer(la))
        if not m.isEmpty():
            m.exec(ev.globalPosition().toPoint())


def _nearest_segment(pts, p, closed: bool) -> Tuple[int, Tuple[float, float]]:
    best = (0, pts[0], float("inf"))
    n = len(pts)
    for i in range(n if closed else n - 1):
        a, b = pts[i], pts[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = dx * dx + dy * dy
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
        q = (a[0] + t * dx, a[1] + t * dy)
        dist = math.hypot(q[0] - p[0], q[1] - p[1])
        if dist < best[2]:
            best = (i, q, dist)
    return best[0], best[1]


# =====================================================================================================
class DxfEditor(QMainWindow):
    saved = pyqtSignal(str)

    def __init__(self, path: Optional[str] = None, mode: str = "MS", parent=None,
                 on_saved: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(parent)
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
        grp = QActionGroup(self)
        self.tool_actions = {}
        for tool, text, keys in (("select", "選取", ("F1", "V")), ("pan", "平移", ("F2", "H")),
                                 ("path", "微波線", ("P",)), ("poly", "多邊形", ("G",)),
                                 ("rect", "矩形", ("R",)), ("circle", "圓（孔）", ("C",))):
            a = QAction(f"{text}（{keys[0]}）", self)
            a.setCheckable(True)
            a.setShortcuts([QKeySequence(k) for k in keys])
            a.triggered.connect(lambda _c=False, t=tool: self.set_tool(t))
            grp.addAction(a)
            tb.addAction(a)
            self.tool_actions[tool] = a
        self.tool_actions["select"].setChecked(True)
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
        tb.addWidget(QLabel("  Alt：不吸附　Shift：0/45/90°　右鍵：結束 / 選單"))

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
        v.addWidget(g)

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
        self.lbl_sel = QLabel("點選圖形來編輯。\n\n雙擊線段：加節點\n拖白色方塊：移動節點\n右鍵節點：刪除節點")
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
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["X (mm)", "Y (mm)"])
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
        self.tool_actions[tool].setChecked(True)
        self.canvas.setCursor(Qt.CursorShape.OpenHandCursor if tool == "pan" else
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
        names = list(LAYERS[mode]) + [PATH_LAYER] + sorted(others)
        for name in names:
            desc = LAYER_INFO.get(name, ("其他",))[0]
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
            self.lbl_sel.setText("點選圖形來編輯。\n\n雙擊線段：加節點\n拖白色方塊：移動節點\n右鍵節點：刪除節點\n"
                                 "拖圖形：整個移動")
        else:
            kind = {"path": "微波線（中心線＋線寬）", "poly": "多邊形", "circle": "圓"}[s.kind]
            self.lbl_sel.setText(f"<b>{kind}</b>　圖層 {s.layer}")
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
                self.table.setRowCount(len(s.pts))
                for r, (x, y) in enumerate(s.pts):
                    self.table.setItem(r, 0, QTableWidgetItem(_fmt(x)))
                    self.table.setItem(r, 1, QTableWidgetItem(_fmt(y)))
        self.table.blockSignals(False)
        self._sync = False

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
        self._sync = True
        self.combo_mode.setCurrentText(self.design.mode)
        self.chk_auto.setChecked(self.design.auto_ground)
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

    def _table_changed(self, item: QTableWidgetItem) -> None:
        s = self._selected()
        if self._sync or s is None:
            return
        try:
            v = float(item.text())
        except ValueError:
            self._refresh_props()
            return
        r, c = item.row(), item.column()
        self.checkpoint()
        x, y = s.pts[r]
        s.pts[r] = (v, y) if c == 0 else (x, v)
        self.canvas.rebuild()
        self._on_changed()

    def add_node_after(self) -> None:
        s = self._selected()
        if s is None or s.kind not in ("path", "poly"):
            return
        r = self.table.currentRow()
        r = len(s.pts) - 1 if r < 0 else r
        a = s.pts[r]
        b = s.pts[(r + 1) % len(s.pts)] if (s.kind == "poly" or r + 1 < len(s.pts)) else None
        new = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) if b else (a[0] + 1.0, a[1])
        self.checkpoint()
        s.pts.insert(r + 1, new)
        self._commit()
        self.table.setCurrentCell(r + 1, 0)

    def delete_node(self, i: int) -> None:
        s = self._selected()
        if s is None or s.kind not in ("path", "poly") or not (0 <= i < len(s.pts)):
            return
        if len(s.pts) <= (3 if s.kind == "poly" else 2):
            QMessageBox.information(self, "DXF 編輯器", "節點不能再少了（多邊形至少 3 個、微波線至少 2 個）。")
            return
        self.checkpoint()
        del s.pts[i]
        self._commit()

    def reverse_selected(self) -> None:
        s = self._selected()
        if s is None or s.kind not in ("path", "poly"):
            return
        self.checkpoint()
        s.pts.reverse()
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

    def _toggle_auto(self, on: bool) -> None:
        if self._sync or on == self.design.auto_ground:
            return
        self.checkpoint()
        self.design.auto_ground = on
        self._commit()

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

    def new_design(self, mode: str) -> None:
        if not self._ask_discard():
            return
        self.design = Design.template(mode, width=self.new_width.value(), gap=self.new_gap.value())
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
