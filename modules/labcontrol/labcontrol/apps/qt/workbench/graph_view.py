"""左上：量測方案流程圖（節點圖編輯器）。

操作：
  * 拖動節點：自由擺放。
  * 從節點的輸出點（下方「完成後」/ 右側「每一點 ⟲」）拖到另一個節點 → 連線；拖到空白處 → 選單新增節點並連上。
  * 從左下儀器參數列表拖進來：放在連線上 = 插入；放在節點上 = 接在它後面；放在空白處 = 未連接的節點。
  * 雙擊節點：Labber 式設定視窗。右鍵：編輯、切換掃描 / 固定、刪除…
  * Delete：刪除選取的節點或連線。Ctrl + 滾輪：縮放；中鍵拖曳：平移。
  * 掃描節點的迴圈範圍會自動畫框（框越外面 = 迴圈越外層）。
"""
from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Tuple

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import QPointF, QRectF, Qt

from ....scheme import Block
from ....scheme.compile import format_duration
from ....scheme.graph import BODY, NEXT, START, GraphError
from ....settings import setting
from .. import theme
from .doc import WorkbenchDoc

MIME = "application/x-labcontrol-node"
W, H = 232.0, 68.0
PORT_R = 7.0
LEVEL_COLORS = ["#1c6dd0", "#7b2cbf", "#c2255c", "#0c8a7d", "#e8590c"]


def kind_color(key: str) -> QtGui.QColor:
    cols = setting("editor.colors", {}) or {}
    c = cols.get(key) or (cols.get("measure") if key not in ("dc", "param", "save", "wait", "start") else None)
    if c:
        return QtGui.QColor(c)
    return theme.qc("title" if key == "start" else "text2")


def _fmt(v) -> str:
    if v is None:
        return "?"
    return f"{v:.6g}" if isinstance(v, float) else str(v)


# ---------------------------------------------------------------------------
class PortItem(QtWidgets.QGraphicsEllipseItem):
    def __init__(self, node: "NodeItem", port: str, pos: QPointF, color: QtGui.QColor, label: str = "") -> None:
        super().__init__(-PORT_R, -PORT_R, 2 * PORT_R, 2 * PORT_R, node)
        self.node, self.port = node, port
        self.setPos(pos)
        self.setBrush(QtGui.QBrush(color))
        self.setPen(QtGui.QPen(theme.qc("node"), 2))
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setZValue(3)
        self.setAcceptHoverEvents(True)
        self.setToolTip({NEXT: "完成後 →（拖到下一個節點）", BODY: "每一點 ⟲（迴圈內容：拖到要在每一點執行的節點）",
                         "in": "輸入"}.get(port, port))
        if label:
            t = QtWidgets.QGraphicsSimpleTextItem(label, node)
            t.setBrush(QtGui.QBrush(theme.readable(color)))
            f = t.font()
            f.setPointSizeF(8)
            t.setFont(f)
            br = t.boundingRect()
            if port == BODY:
                t.setPos(pos.x() + 10, pos.y() - br.height() - 2)
            else:
                t.setPos(pos.x() + 10, pos.y() - 2)

    def hoverEnterEvent(self, e) -> None:
        self.setScale(1.35)

    def hoverLeaveEvent(self, e) -> None:
        self.setScale(1.0)

    def scene_center(self) -> QPointF:
        return self.mapToScene(QPointF(0, 0))


class NodeItem(QtWidgets.QGraphicsObject):
    def __init__(self, view: "FlowView", nid: str) -> None:
        super().__init__()
        self.view, self.nid = view, nid
        self.setFlags(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        self.ports: Dict[str, PortItem] = {}
        self.title, self.sub, self.badge, self.color_key = "", "", "", "wait"
        self.level = "ok"
        self._drop_hl = False
        self.refresh()

    # ---- 內容 ------------------------------------------------------------
    @property
    def doc(self) -> WorkbenchDoc:
        return self.view.doc

    def block(self) -> Optional[Block]:
        return self.doc.node(self.nid)

    def refresh(self) -> None:
        for p in self.ports.values():
            p.setParentItem(None)
            if p.scene():
                p.scene().removeItem(p)
        for c in list(self.childItems()):
            if isinstance(c, QtWidgets.QGraphicsSimpleTextItem):
                c.setParentItem(None)
                if c.scene():
                    c.scene().removeItem(c)
        self.ports = {}
        b = self.block()
        self.title, self.sub, self.badge, self.color_key = describe(self.doc, self.nid, b)
        col = kind_color(self.color_key)
        if self.nid != START:
            self.ports["in"] = PortItem(self, "in", QPointF(W / 2, 0), theme.qc("port"))
        self.ports[NEXT] = PortItem(self, NEXT, QPointF(W / 2, H), theme.qc("text2"),
                                    "完成後" if b is not None and b.is_loop else "")
        if b is not None and b.is_loop:
            self.ports[BODY] = PortItem(self, BODY, QPointF(W, H / 2), col, "每一點 ⟲")
        iss = self.doc.result.issues_for(self.nid) if self.nid != START else []
        self.level = "error" if any(i.level == "error" for i in iss) else \
            "warning" if any(i.level == "warning" for i in iss) else "ok"
        self.setToolTip("\n".join(f"{'✖' if i.level == 'error' else '⚠' if i.level == 'warning' else 'ℹ'} "
                                  f"{i.message}" for i in iss) or self.sub)
        self.update()

    # ---- 繪圖 ------------------------------------------------------------
    def boundingRect(self) -> QRectF:
        return QRectF(-4, -4, W + 8, H + 8)

    def paint(self, p: QtGui.QPainter, opt, widget=None) -> None:
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        col = kind_color(self.color_key)
        r = QRectF(0, 0, W, H)
        sel = self.isSelected() or self.doc.selected == self.nid
        border = theme.qc("err") if self.level == "error" else theme.qc("orange") if self.level == "warning" else \
            theme.qc("accent") if sel else theme.qc("node_border")
        if self._drop_hl:
            border = theme.qc("hl")
        p.setPen(QtGui.QPen(border, 2.6 if (sel or self.level != "ok" or self._drop_hl) else 1.2))
        p.setBrush(theme.qc("node") if self.nid != START else theme.qc("node_start"))
        p.drawRoundedRect(r, 9, 9)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(0, 0, 7, H), 4, 4)
        f = p.font()
        f.setPointSizeF(10.5)
        f.setBold(True)
        p.setFont(f)
        p.setPen(theme.readable(col))
        p.drawText(QRectF(16, 6, W - 90, 22), Qt.AlignmentFlag.AlignVCenter, self.title)
        f.setBold(False)
        f.setPointSizeF(8.8)
        p.setFont(f)
        p.setPen(theme.qc("node_text"))
        fm = QtGui.QFontMetrics(f)
        lines = self.sub.split("\n")[:2]
        for i, line in enumerate(lines):
            p.drawText(QRectF(16, 29 + i * 17, W - 22, 17), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(line, Qt.TextElideMode.ElideRight, int(W - 24)))
        if self.badge:
            f.setPointSizeF(8)
            f.setBold(True)
            p.setFont(f)
            bw = QtGui.QFontMetrics(f).horizontalAdvance(self.badge) + 14
            br = QRectF(W - bw - 8, 8, bw, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(br, 9, 9)
            p.setPen(QtGui.QColor("white"))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, self.badge)

    # ---- 互動 ------------------------------------------------------------
    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.view.node_moved(self)
        return super().itemChange(change, value)

    def mousePressEvent(self, e) -> None:
        self.view.doc.select(self.nid)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        super().mouseReleaseEvent(e)
        self.view.commit_moves()

    def mouseDoubleClickEvent(self, e) -> None:
        self.view.edit_node(self.nid)

    def contextMenuEvent(self, e) -> None:
        self.view.node_menu(self.nid, e.screenPos())


class EdgeItem(QtWidgets.QGraphicsPathItem):
    def __init__(self, view: "FlowView", src: str, port: str, dst: str, color: QtGui.QColor) -> None:
        super().__init__()
        self.view, self.src, self.port, self.dst = view, src, port, dst
        self.color = color
        self.setZValue(1)
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self._hl = False
        self.setToolTip("每一點 ⟲（迴圈內容）" if port == BODY else "完成後 →")
        self.update_path()

    def update_path(self) -> None:
        a = self.view.items_by_id.get(self.src)
        b = self.view.items_by_id.get(self.dst)
        if a is None or b is None or self.port not in a.ports:
            return
        p1 = a.ports[self.port].scene_center()
        p2 = b.ports["in"].scene_center()
        path = QtGui.QPainterPath(p1)
        dy = max(40.0, abs(p2.y() - p1.y()) * 0.5)
        left_in = b.mapToScene(QPointF(0, H / 2))
        if self.port == BODY and left_in.x() > p1.x() + 20 and abs(left_in.y() - p1.y()) < H * 1.5:
            # 迴圈內容在右邊同一列：水平接到節點左側，看起來像手繪的「往右展開」
            dx = max(30.0, (left_in.x() - p1.x()) * 0.5)
            path.cubicTo(p1 + QPointF(dx, 0), left_in - QPointF(dx, 0), left_in)
        elif self.port == BODY:
            dx = max(50.0, abs(p2.x() - p1.x()) * 0.5)
            path.cubicTo(p1 + QPointF(dx, 0), p2 - QPointF(0, dy), p2)
        else:
            path.cubicTo(p1 + QPointF(0, dy), p2 - QPointF(0, dy), p2)
        self.setPath(path)
        self._apply_pen()

    def _apply_pen(self) -> None:
        w = 3.2 if (self.isSelected() or self._hl) else 2.0
        c = theme.qc("hl") if self._hl else (theme.qc("accent") if self.isSelected() else theme.readable(self.color))
        pen = QtGui.QPen(c, w)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self.setPen(pen)

    def set_highlight(self, on: bool) -> None:
        if on != self._hl:
            self._hl = on
            self._apply_pen()

    def shape(self) -> QtGui.QPainterPath:
        s = QtGui.QPainterPathStroker()
        s.setWidth(14)
        return s.createStroke(self.path())

    def paint(self, p: QtGui.QPainter, opt, widget=None) -> None:
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(self.pen())
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(self.path())
        # 箭頭
        path = self.path()
        end = path.pointAtPercent(1.0)
        before = path.pointAtPercent(0.97)
        ang = math.atan2(end.y() - before.y(), end.x() - before.x())
        sz = 9.0
        pts = [end, end - QPointF(math.cos(ang - 0.45) * sz, math.sin(ang - 0.45) * sz),
               end - QPointF(math.cos(ang + 0.45) * sz, math.sin(ang + 0.45) * sz)]
        p.setBrush(self.pen().color())
        p.drawPolygon(QtGui.QPolygonF(pts))

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._apply_pen()
        return super().itemChange(change, value)


# ---------------------------------------------------------------------------
def describe(doc: WorkbenchDoc, nid: str, b: Optional[Block]) -> Tuple[str, str, str, str]:
    """(標題, 說明, 標籤, 顏色 key)"""
    cat = doc.catalog
    if nid == START:
        return "開始", "量測從這裡開始", "", "start"
    if b is None:
        return "?", "", "", "wait"
    if b.kind == "set":
        t = cat.target(b.target)
        name = t.short if t else (b.target or "（未選目標）")
        key = "param" if (t is None or t.group == "param") else "dc"
        title = (t.kind_label if t and t.group != "param" else "參數") if t else "設定"
        if b.is_loop:
            n = b.sweep_values_count()
            step = f"{b.points} 點" if b.points else f"步進 {_fmt(b.step)}"
            extra = []
            if b.interp == "log":
                extra.append("對數")
            if b.alternate:
                extra.append("來回")
            sub = f"{name}\n{_fmt(b.start)} → {_fmt(b.stop)} {b.unit} · {step}" + \
                  (f"（{n} 點）" if n and not b.points else "") + (f" · {'、'.join(extra)}" if extra else "")
            return title, sub, "掃描 ⟲", key
        return title, f"{name}\n= {_fmt(b.value)} {b.unit}", "固定", key
    if b.kind == "measure":
        m = cat.measurer(b.instrument)
        pts = (b.settings or {}).get("points")
        sub = f"{m.short if m else b.instrument} · {'/'.join(b.traces) or '—'}" + (f" · {pts} 點" if pts else "")
        return (m.head if m else "量測"), sub, "量測", ((m.kind or "measure") if m else "measure")
    if b.kind == "save":
        r = doc.result
        extra = next((e for e in getattr(r, "extras", []) or [] if e["block"] == b.id), None)
        if extra is not None:
            names = "、".join(lp.target.short for lp in r.loops[:extra["depth"]])
            fmts = "+".join(extra["formats"]) or "未選格式"
            sub = (f"每個{names}值存一個檔" if extra["depth"] else "整個量測一個檔") + f"\n{extra['file_name']}（{fmts}）"
            return "Data（額外）", sub, "存檔", "save"
        if r.save is not None and r.save.id == b.id and r.split_depth:
            names = "、".join(lp.target.short for lp in r.loops[:r.split_depth])
            sub = f"每個{names}值存一個檔\n共 {r.n_files} 個檔"
        else:
            sub = "整個量測存成一個檔\n檔名與格式見右下「檔案設置」"
        return "Data", sub, "存檔", "save"
    if b.kind == "wait":
        return "等待", f"{_fmt(b.seconds)} s", "", "wait"
    return b.kind, "", "", "wait"


# ---------------------------------------------------------------------------
class FlowScene(QtWidgets.QGraphicsScene):
    def __init__(self, view: "FlowView") -> None:
        super().__init__()
        self.view = view

    def drawBackground(self, p: QtGui.QPainter, rect: QRectF) -> None:
        p.fillRect(rect, theme.qc("canvas"))
        step = 24
        p.setPen(QtGui.QPen(theme.qc("grid"), 1.4))
        x0 = int(math.floor(rect.left() / step) * step)
        y0 = int(math.floor(rect.top() / step) * step)
        pts = [QPointF(x, y) for x in range(x0, int(rect.right()) + step, step)
               for y in range(y0, int(rect.bottom()) + step, step)]
        if len(pts) < 20000:
            p.drawPoints(QtGui.QPolygonF(pts))
        self.view.draw_loop_frames(p)


class FlowView(QtWidgets.QGraphicsView):
    def __init__(self, doc: WorkbenchDoc, parent: Optional[QtWidgets.QWidget] = None) -> None:
        self._scene = None
        super().__init__(parent)
        self.doc = doc
        self._scene = FlowScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.RubberBandDrag)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setAcceptDrops(True)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.items_by_id: Dict[str, NodeItem] = {}
        self.edges: List[EdgeItem] = []
        self._moved: Dict[str, Tuple[float, float]] = {}
        self._link_drag: Optional[Tuple[PortItem, QtWidgets.QGraphicsPathItem]] = None
        self._pan: Optional[QPointF] = None
        self._drop_edge: Optional[EdgeItem] = None
        self._drop_node: Optional[NodeItem] = None
        self._rebuilding = False
        self._first = True
        doc.changed.connect(self._on_changed)
        doc.selection_changed.connect(self._on_selection)
        self._scene.selectionChanged.connect(self._scene_selection)
        self.rebuild()
        theme.on_change(self, FlowView._theme_changed, call_now=False)

    # ---- 重建 ------------------------------------------------------------
    def _on_changed(self, origin: object) -> None:
        if origin is self:
            for it in self.items_by_id.values():
                it.refresh()
            self.viewport().update()
            return
        self.rebuild()

    def rebuild(self) -> None:
        self._rebuilding = True
        self._scene.clear()
        self.items_by_id.clear()
        self.edges.clear()
        g = self.doc.graph
        for nid in [START, *g.nodes]:
            it = NodeItem(self, nid)
            x, y = g.pos.get(nid, (0.0, 0.0))
            it.setPos(x, y)
            self._scene.addItem(it)
            self.items_by_id[nid] = it
            if nid == self.doc.selected:
                it.setSelected(True)
        depth = {}
        for (src, port), dst in g.links.items():
            if port == BODY:
                depth[src] = len(g.loop_parents(src))
        for (src, port), dst in g.links.items():
            col = QtGui.QColor(LEVEL_COLORS[depth.get(src, 0) % len(LEVEL_COLORS)]) if port == BODY \
                else theme.qc("edge")
            e = EdgeItem(self, src, port, dst, col)
            self._scene.addItem(e)
            self.edges.append(e)
        self._update_scene_rect()
        self._rebuilding = False
        if self._first:
            self._first = False
            QtCore.QTimer.singleShot(0, self.fit_all)

    def _theme_changed(self) -> None:
        self.rebuild()
        self.resetCachedContent()
        self.viewport().update()

    def _update_scene_rect(self) -> None:
        r = self._scene.itemsBoundingRect().adjusted(-400, -300, 600, 500)
        self._scene.setSceneRect(r)

    def fit_all(self) -> None:
        r = self._scene.itemsBoundingRect().adjusted(-60, -60, 60, 60)
        if r.isValid():
            self.fitInView(r, Qt.AspectRatioMode.KeepAspectRatio)
            if self.transform().m11() > 1.0:
                self.resetTransform()
                self.centerOn(r.center())

    # ---- 迴圈框 -------------------------------------------------------------
    def draw_loop_frames(self, p: QtGui.QPainter) -> None:
        g = self.doc.graph
        loops = [n for n, b in g.nodes.items() if b.is_loop and (n, BODY) in g.links]
        if not loops:
            return
        info = {lp.block.id: lp for lp in self.doc.result.loops}
        # 由外到內畫（外層框較大）
        for nid in sorted(loops, key=lambda n: len(g.loop_parents(n))):
            members = [nid, *g.body_nodes(nid)]
            rects = [self.items_by_id[m].sceneBoundingRect() for m in members if m in self.items_by_id]
            if not rects:
                continue
            r = rects[0]
            for rr in rects[1:]:
                r = r.united(rr)
            inner_depth = self._max_inner_depth(nid)
            pad = 14 + 12 * inner_depth
            r = r.adjusted(-pad, -pad - 18, pad, pad)
            lvl = len(g.loop_parents(nid))
            col = theme.readable(LEVEL_COLORS[lvl % len(LEVEL_COLORS)])
            fill = QtGui.QColor(col)
            fill.setAlpha(22 if theme.is_dark() else 14)
            pen = QtGui.QPen(col, 1.6, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(fill)
            p.drawRoundedRect(r, 14, 14)
            b = g.nodes[nid]
            t = self.doc.catalog.target(b.target)
            lp = info.get(nid)
            name = lp.axis_name if lp else (t.short if t else b.target)
            n = lp.n if lp else b.sweep_values_count()
            label = f"⟲ 迴圈 {lvl + 1}：{name}（{n} 點）"
            f = p.font()
            f.setPointSizeF(9)
            f.setBold(True)
            p.setFont(f)
            p.setPen(col)
            p.drawText(QRectF(r.left() + 12, r.top() + 2, r.width() - 24, 18),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)

    def _max_inner_depth(self, nid: str) -> int:
        g = self.doc.graph
        best = 0
        for m in g.body_nodes(nid):
            b = g.nodes.get(m)
            if b is not None and b.is_loop and (m, BODY) in g.links:
                best = max(best, 1 + self._max_inner_depth(m))
        return best

    # ---- 移動 ------------------------------------------------------------
    def node_moved(self, it: NodeItem) -> None:
        if self._rebuilding:
            return
        self._moved[it.nid] = (it.pos().x(), it.pos().y())
        for e in self.edges:
            if e.src == it.nid or e.dst == it.nid:
                e.update_path()
        self.viewport().update()

    def commit_moves(self) -> None:
        if self._moved:
            moved, self._moved = self._moved, {}
            self.doc.move_nodes(moved, origin=self)
            self._update_scene_rect()

    # ---- 選取 ------------------------------------------------------------
    def _scene_selection(self) -> None:
        if self._rebuilding:
            return
        sel = [i for i in self._scene.selectedItems() if isinstance(i, NodeItem)]
        if len(sel) == 1:
            self.doc.select(sel[0].nid)
        elif not sel:
            self.doc.select(None)
        self.viewport().update()

    def _on_selection(self, nid) -> None:
        it = self.items_by_id.get(nid) if nid else None
        if it is not None and not it.isSelected():
            self._rebuilding = True
            self._scene.clearSelection()
            it.setSelected(True)
            self._rebuilding = False
            self.ensureVisible(it, 40, 40)
        self.viewport().update()

    # ---- 滑鼠：連線、平移 -------------------------------------------------------
    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.MiddleButton:
            self._pan = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        item = self.itemAt(e.position().toPoint())
        if isinstance(item, PortItem) and item.port in (NEXT, BODY) and e.button() == Qt.MouseButton.LeftButton:
            line = QtWidgets.QGraphicsPathItem()
            line.setPen(QtGui.QPen(theme.qc("accent"), 2, Qt.PenStyle.DashLine))
            line.setZValue(10)
            self._scene.addItem(line)
            self._link_drag = (item, line)
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QtGui.QMouseEvent) -> None:
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - d.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - d.y()))
            return
        if self._link_drag is not None:
            port, line = self._link_drag
            p1 = port.scene_center()
            p2 = self.mapToScene(e.position().toPoint())
            path = QtGui.QPainterPath(p1)
            path.cubicTo(p1 + QPointF(40 if port.port == BODY else 0, 0 if port.port == BODY else 40),
                         p2 - QPointF(0, 40), p2)
            line.setPath(path)
            self._hover_target(self._node_at(e.position().toPoint()))
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent) -> None:
        if self._pan is not None and e.button() == Qt.MouseButton.MiddleButton:
            self._pan = None
            self.unsetCursor()
            return
        if self._link_drag is not None:
            port, line = self._link_drag
            self._link_drag = None
            self._scene.removeItem(line)
            self._hover_target(None)
            target = self._node_at(e.position().toPoint())
            src = port.node.nid
            if target is not None and target.nid not in (src, START):
                try:
                    def fn(s):
                        s.graph.connect(src, port.port, target.nid)
                    self.doc.mutate(fn)
                except GraphError as err:
                    QtWidgets.QToolTip.showText(e.globalPosition().toPoint(), str(err), self)
            elif target is None:
                self._create_menu(self.mapToScene(e.position().toPoint()), e.globalPosition().toPoint(),
                                  link=(src, port.port))
            return
        super().mouseReleaseEvent(e)

    def _node_at(self, pt: QtCore.QPoint) -> Optional[NodeItem]:
        for it in self.items(pt):
            if isinstance(it, NodeItem):
                return it
            if isinstance(it, PortItem):
                return it.node
        return None

    def _hover_target(self, node: Optional[NodeItem]) -> None:
        for it in self.items_by_id.values():
            hl = it is node
            if it._drop_hl != hl:
                it._drop_hl = hl
                it.update()

    def wheelEvent(self, e: QtGui.QWheelEvent) -> None:
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            s = self.transform().m11() * f
            if 0.25 <= s <= 2.5:
                self.scale(f, f)
            return
        super().wheelEvent(e)

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            return
        super().keyPressEvent(e)

    def delete_selected(self) -> None:
        nodes = [i.nid for i in self._scene.selectedItems() if isinstance(i, NodeItem) and i.nid != START]
        edges = [(i.src, i.port) for i in self._scene.selectedItems() if isinstance(i, EdgeItem)]
        if not nodes and not edges:
            return

        def fn(s):
            for src, port in edges:
                s.graph.disconnect(src, port)
            for n in nodes:
                s.graph.remove(n)
        self.doc.mutate(fn, select=None)

    # ---- 拖放（來自儀器參數列表） -----------------------------------------------
    def dragEnterEvent(self, e: QtGui.QDragEnterEvent) -> None:
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e: QtGui.QDragMoveEvent) -> None:
        if not e.mimeData().hasFormat(MIME):
            return super().dragMoveEvent(e)
        e.acceptProposedAction()
        pt = e.position().toPoint()
        node = self._node_at(pt)
        edge = None if node else self._edge_at(pt)
        self._hover_target(node)
        if edge is not self._drop_edge:
            if self._drop_edge is not None:
                self._drop_edge.set_highlight(False)
            self._drop_edge = edge
            if edge is not None:
                edge.set_highlight(True)

    def dragLeaveEvent(self, e) -> None:
        self._clear_drop()

    def _clear_drop(self) -> None:
        self._hover_target(None)
        if self._drop_edge is not None:
            self._drop_edge.set_highlight(False)
            self._drop_edge = None

    def _edge_at(self, pt: QtCore.QPoint) -> Optional[EdgeItem]:
        for it in self.items(QtCore.QRect(pt.x() - 6, pt.y() - 6, 12, 12)):
            if isinstance(it, EdgeItem):
                return it
        return None

    def dropEvent(self, e: QtGui.QDropEvent) -> None:
        if not e.mimeData().hasFormat(MIME):
            return super().dropEvent(e)
        spec = json.loads(bytes(e.mimeData().data(MIME)).decode("utf-8"))
        pt = e.position().toPoint()
        node = self._node_at(pt)
        edge = self._drop_edge
        self._clear_drop()
        e.acceptProposedAction()
        sp = self.mapToScene(pt)
        pos = (sp.x() - W / 2, sp.y() - H / 2)
        if node is not None:
            blk = self.doc.add_node(spec, after=node.nid)
        elif edge is not None:
            blk = self.doc.add_node(spec, pos=pos, on_link=(edge.src, edge.port))
        else:
            blk = self.doc.add_node(spec, pos=pos)
        if spec.get("kind") == "set":
            QtCore.QTimer.singleShot(0, lambda: self.edit_node(blk.id))   # Labber：拖進來就開 step 設定

    # ---- 選單與對話框 -----------------------------------------------------------
    def contextMenuEvent(self, e: QtGui.QContextMenuEvent) -> None:
        item = self.itemAt(e.pos())
        if isinstance(item, (NodeItem, PortItem)) or (item is not None and isinstance(item.parentItem(), NodeItem)):
            return super().contextMenuEvent(e)
        if isinstance(item, EdgeItem):
            m = QtWidgets.QMenu(self)
            m.addAction("刪除這條連線", lambda: self.doc.mutate(lambda s: s.graph.disconnect(item.src, item.port)))
            self._add_submenus(m, self.mapToScene(e.pos()), link=(item.src, item.port), title_prefix="在這裡插入")
            m.exec(e.globalPos())
            return
        self._create_menu(self.mapToScene(e.pos()), e.globalPos())

    def _add_submenus(self, m: QtWidgets.QMenu, scene_pt: QPointF, link=None, title_prefix: str = "新增") -> None:
        cat = self.doc.catalog
        pos = (scene_pt.x() - W / 2, scene_pt.y() - 10)

        def add(spec):
            if link is not None:
                blk = self.doc.add_node(spec, pos=pos, on_link=link)
            else:
                blk = self.doc.add_node(spec, pos=pos)
            if spec.get("kind") == "set":
                QtCore.QTimer.singleShot(0, lambda: self.edit_node(blk.id))

        groups = [("電磁鐵組", [t for t in cat.targets if t.group == "magnet"]),
                  ("電源", [t for t in cat.targets if t.group == "source"]),
                  ("儀器參數", [t for t in cat.targets if t.group == "param" and t.common])]
        for title, items in groups:
            if not items:
                continue
            sub = m.addMenu(f"{title_prefix}：{title}")
            for t in items:
                sub.addAction(t.label, lambda t=t: add({"kind": "set", "target": t.ref}))
        if cat.measurers:
            sub = m.addMenu(f"{title_prefix}：量測")
            for mm in cat.measurers:
                sub.addAction(f"{mm.head} · {mm.label}", lambda mm=mm: add({"kind": "measure", "instrument": mm.ref}))
        m.addAction(f"{title_prefix}：Data（分檔點）", lambda: add({"kind": "save"}))
        m.addAction(f"{title_prefix}：等待", lambda: add({"kind": "wait"}))

    def _create_menu(self, scene_pt: QPointF, global_pt: QtCore.QPoint, link=None) -> None:
        m = QtWidgets.QMenu(self)
        self._add_submenus(m, scene_pt, link=link, title_prefix="接上" if link else "新增")
        if link is None:
            m.addSeparator()
            m.addAction("自動排列", self.doc.auto_layout)
            m.addAction("全部顯示", self.fit_all)
        m.exec(global_pt)

    def node_menu(self, nid: str, global_pt: QtCore.QPoint) -> None:
        m = QtWidgets.QMenu(self)
        b = self.doc.node(nid)
        if nid == START:
            m.addAction("自動排列", self.doc.auto_layout)
            m.exec(global_pt)
            return
        m.addAction("編輯…", lambda: self.edit_node(nid))
        if b is not None and b.kind == "set":
            if b.is_loop:
                m.addAction("改成固定值", lambda: self.doc.mutate(lambda s: s.graph.set_mode(nid, "fixed")))
            else:
                m.addAction("改成掃描（迴圈）", lambda: self.doc.mutate(lambda s: s.graph.set_mode(nid, "sweep")))
        m.addAction("複製", lambda: self._duplicate(nid))
        if self.doc.graph.incoming(nid) is not None:
            m.addAction("中斷輸入連線", lambda: self.doc.mutate(
                lambda s: s.graph.disconnect(*s.graph.incoming(nid))))
        m.addSeparator()
        m.addAction("刪除", lambda: self.doc.mutate(lambda s: s.graph.remove(nid), select=None))
        m.exec(global_pt)

    def _duplicate(self, nid: str) -> None:
        import copy

        from ....scheme.model import new_id

        b = copy.deepcopy(self.doc.node(nid))
        b.id = new_id()
        x, y = self.doc.graph.pos.get(nid, (0, 0))
        self.doc.mutate(lambda s: s.graph.add(b, (x + 40, y + 40)), select=b.id)

    def edit_node(self, nid: str) -> None:
        from .dialogs import edit_block

        if nid == START:
            return
        b = self.doc.node(nid)
        if b is None:
            return
        new = edit_block(self, self.doc, b)
        if new is None:
            return

        def fn(s):
            g = s.graph
            old = g.nodes[nid]
            if old.kind == "set" and old.mode != new.mode:
                g.set_mode(nid, new.mode)
            new.id = nid
            g.nodes[nid] = new
        self.doc.mutate(fn)


class FlowPanel(QtWidgets.QWidget):
    """流程圖 + 小工具列（新增節點、自動排列、全部顯示、縮放）。"""

    def __init__(self, doc: WorkbenchDoc, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.view = FlowView(doc)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(6, 3, 6, 3)
        add = QtWidgets.QToolButton()
        add.setText("＋ 新增節點")
        add.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QtWidgets.QMenu(add)
        menu.aboutToShow.connect(lambda: self._fill_add(menu))
        add.setMenu(menu)
        bar.addWidget(add)
        for text, fn in (("自動排列", doc.auto_layout), ("全部顯示", self.view.fit_all),
                         ("刪除", self.view.delete_selected)):
            b = QtWidgets.QToolButton()
            b.setText(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        self.summary = QtWidgets.QLabel()
        self.summary.setStyleSheet("color:palette(text);")
        bar.addWidget(self.summary)
        lay.addLayout(bar)
        lay.addWidget(self.view, 1)
        hint = QtWidgets.QLabel("拖動節點自由擺放 · 從節點的圓點拉線連接 · 從左下列表拖入參數 / 量測 · 雙擊編輯 · 右鍵更多")
        hint.setStyleSheet("color:palette(placeholder-text); padding:2px 8px; font-size:11px;")
        lay.addWidget(hint)
        doc.changed.connect(lambda _o: self._summary())
        theme.on_change(self, lambda w: w._summary())

    def _fill_add(self, menu: QtWidgets.QMenu) -> None:
        menu.clear()
        center = self.view.mapToScene(self.view.viewport().rect().center())
        self.view._add_submenus(menu, center, title_prefix="新增")

    def _summary(self) -> None:
        r = self.doc.result
        err = sum(1 for i in r.issues if i.level == "error")
        state = theme.span(f"✖ {err} 個錯誤", "err") if err else theme.span("✔ 可以執行", "ok")
        self.summary.setText(f"{state} · {r.total_points:,} 點 · 預估 {format_duration(r.est_seconds)}")
