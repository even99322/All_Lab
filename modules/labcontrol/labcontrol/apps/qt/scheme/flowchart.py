"""流程圖畫布：照手繪草圖的畫法 —— 方塊由上往下，掃描迴圈是右側往回指的括號，括號旁寫參數與範圍。

操作：
    點方塊        選取（右側屬性面板編輯）
    點箭頭上的 ＋  在該位置插入方塊
    從左側拖曳     放到兩個方塊之間
    右鍵          轉成掃描/固定、移入/移出迴圈、包進新迴圈、上移、下移、刪除
    鍵盤          Delete 刪除 · Ctrl+↑/↓ 上下移 · Tab 移入迴圈 · Shift+Tab 移出迴圈
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import QPointF, QRectF, Qt

from ....settings import setting
from ....scheme import Block
from ....scheme.compile import default_axis_name
from .doc import SchemeDoc

MIME = "application/x-labcontrol-block"

BOX_W, BOX_H, GAP = 236, 60, 42
LEFT, TOP = 36, 28
BRACKET_GAP, LABEL_W = 26, 190

class _KindColors(dict):
    """方塊顏色：settings.yaml editor.colors；量測方塊用 MEASURE_KIND 查（沒設定 → measure）。"""

    def __missing__(self, key: str) -> QtGui.QColor:
        from ....settings import setting

        cols = setting("editor.colors", {}) or {}
        c = cols.get(key) or (cols.get("measure") if key not in ("dc", "param", "save", "wait") else None) or "#495057"
        self[key] = QtGui.QColor(c)
        return self[key]


KIND_COLOR = _KindColors()
LEVEL_COLOR = [QtGui.QColor("#1c6dd0"), QtGui.QColor("#7b2cbf"), QtGui.QColor("#c2255c"),
               QtGui.QColor("#0c8a7d"), QtGui.QColor("#e8590c")]   # 括號顏色：內 → 外
ERR = QtGui.QColor("#e03131")
WARN = QtGui.QColor("#f08c00")


@dataclass
class NodeGeom:
    block: Block
    rect: QRectF
    index: int


@dataclass
class LoopGeom:
    block: Block
    x: float
    y_top: float
    y_bot: float
    level: int


@dataclass
class FlowLayout:
    nodes: List[NodeGeom]
    loops: List[LoopGeom]
    gaps: List[QPointF]
    size: QtCore.QSize


def compute_layout(doc: SchemeDoc) -> FlowLayout:
    walk = list(doc.scheme.walk())
    nodes: List[NodeGeom] = []
    y = TOP
    for i, (b, _, _) in enumerate(walk):
        nodes.append(NodeGeom(b, QRectF(LEFT, y, BOX_W, BOX_H), i))
        y += BOX_H + GAP
    by_id = {n.block.id: n for n in nodes}

    level_cache: Dict[str, int] = {}

    def level(b: Block) -> int:
        if b.id not in level_cache:
            inner = [level(c) for c in _loop_descendants(b)]
            level_cache[b.id] = 1 + max(inner) if inner else 0
        return level_cache[b.id]

    loops = []
    max_level = -1
    for n in nodes:
        b = n.block
        if not b.is_loop:
            continue
        lv = level(b)
        max_level = max(max_level, lv)
        last = by_id[doc.scheme.last_descendant(b).id]
        loops.append(LoopGeom(b, LEFT + BOX_W + BRACKET_GAP + lv * LABEL_W, n.rect.center().y(),
                              last.rect.center().y() + (6 * lv if last is not n else 14), lv))
    cx = LEFT + BOX_W / 2
    gaps = [QPointF(cx, TOP - 14)] + [QPointF(cx, n.rect.bottom() + GAP / 2) for n in nodes]
    width = int(LEFT + BOX_W + BRACKET_GAP + (max_level + 1) * LABEL_W + 24)
    return FlowLayout(nodes, loops, gaps, QtCore.QSize(max(width, LEFT + BOX_W + 60), int(y + 20)))


def _loop_descendants(b: Block) -> List[Block]:
    out = []
    for c in b.children:
        if c.is_loop:
            out.append(c)
        out.extend(x for x in _loop_descendants(c))
    return [x for x in out if x.is_loop]


class FlowchartView(QtWidgets.QWidget):
    def __init__(self, doc: SchemeDoc, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.layout_: FlowLayout = compute_layout(doc)
        self.hover_gap: Optional[int] = None
        self.drop_gap: Optional[int] = None
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#f8f9fb"))
        self.setPalette(pal)
        doc.changed.connect(lambda _o: self.relayout())
        doc.selection_changed.connect(lambda _b: self.update())
        self.relayout()

    # ---- 版面 ---------------------------------------------------------------
    def relayout(self) -> None:
        self.layout_ = compute_layout(self.doc)
        self.setMinimumSize(self.layout_.size)
        self.resize(max(self.width(), self.layout_.size.width()), self.layout_.size.height())
        self.update()

    def sizeHint(self) -> QtCore.QSize:
        return self.layout_.size

    # ---- 繪圖 ---------------------------------------------------------------
    def _kind(self, b: Block) -> str:
        if b.kind == "set":
            t = self.doc.catalog.target(b.target)
            return "param" if t is not None and t.group == "param" else "dc"
        if b.kind == "measure":
            m = self.doc.catalog.measurer(b.instrument)
            return (m.kind or "measure") if m is not None else "measure"
        return b.kind

    def _titles(self, b: Block) -> Tuple[str, str]:
        cat = self.doc.catalog
        if b.kind == "set":
            t = cat.target(b.target)
            if t is None:
                return setting("editor.dc_block_title", "DC set"), b.target or "（選擇目標）"
            if t.group == "param":
                inst = cat.measurer(t.ref.split(".")[0])
                where = inst.short if inst else t.ref.split(".")[0]
                sub = f"{where} · 掃描（範圍見右側）" if b.is_loop else f"{where} = {b.value:g} {b.unit}" if b.value is not None else "= ?"
                return t.kind_label, sub
            return t.kind_label, t.label if b.is_loop else f"{t.short} = {b.value:g} {b.unit}" if b.value is not None else t.label
        if b.kind == "measure":
            m = cat.measurer(b.instrument)
            pts = (b.settings or {}).get("points")
            head = m.head if m is not None else "量測"
            return head, f"{m.short if m else b.instrument} · {'/'.join(b.traces) or '—'}" + (f" · {pts} 點" if pts else "")
        if b.kind == "save":
            r = self.doc.result
            if r.save is not None and r.save.id == b.id and r.split_depth:
                names = "、".join(lp.target.short for lp in r.loops[:r.split_depth])
                where = f"每個{names} 值一檔（{r.n_files} 檔）"
            else:
                where = "整個實驗一個檔"
            fmt = "+".join({"labber": "Labber", "hdf5": "HDF5"}.get(f, f) for f in b.formats) or "只存 raw"
            return "Data", f"{where} · {fmt}"
        return "等待", f"{b.seconds:g} s"

    def paintEvent(self, ev: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        lay = self.layout_
        # 流程箭頭
        pen = QtGui.QPen(QtGui.QColor("#adb5bd"), 1.4)
        for a, b in zip(lay.nodes, lay.nodes[1:]):
            self._arrow(p, QPointF(a.rect.center().x(), a.rect.bottom()),
                        QPointF(b.rect.center().x(), b.rect.top()), pen)
        # 迴圈括號（外層先畫）
        for lg in sorted(lay.loops, key=lambda g: -g.level):
            self._bracket(p, lg)
        # 方塊
        for n in lay.nodes:
            self._node(p, n)
        # 插入點
        for i, g in enumerate(lay.gaps):
            if not lay.nodes and i > 0:
                continue
            self._gap(p, g, i == self.hover_gap, i == self.drop_gap)
        if not lay.nodes:
            p.setPen(QtGui.QColor("#868e96"))
            p.drawText(QRectF(LEFT, TOP + 20, 420, 60), Qt.AlignmentFlag.AlignLeft,
                       "從左側把電源、量測與「Data」方塊拖進來，\n或從工具列選一個範本。")
        p.end()

    def _arrow(self, p: QtGui.QPainter, a: QPointF, b: QPointF, pen: QtGui.QPen) -> None:
        p.setPen(pen)
        p.drawLine(a, b)
        p.setBrush(pen.color())
        d = QtGui.QPolygonF([b, QPointF(b.x() - 4.5, b.y() - 8), QPointF(b.x() + 4.5, b.y() - 8)])
        p.drawPolygon(d)

    def _bracket(self, p: QtGui.QPainter, lg: LoopGeom) -> None:
        col = LEVEL_COLOR[lg.level % len(LEVEL_COLOR)]
        right = LEFT + BOX_W
        pen = QtGui.QPen(col, 1.8)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QtGui.QPainterPath(QPointF(right, lg.y_bot))
        path.lineTo(lg.x - 8, lg.y_bot)
        path.quadTo(lg.x, lg.y_bot, lg.x, lg.y_bot - 8)
        path.lineTo(lg.x, lg.y_top + 8)
        path.quadTo(lg.x, lg.y_top, lg.x - 8, lg.y_top)
        path.lineTo(right + 2, lg.y_top)
        p.drawPath(path)
        p.setBrush(col)
        tip = QPointF(right + 1, lg.y_top)
        p.drawPolygon(QtGui.QPolygonF([tip, QPointF(tip.x() + 9, tip.y() - 5), QPointF(tip.x() + 9, tip.y() + 5)]))
        # 標籤（對應草圖括號旁的 curr 100mA~150mA）
        b = lg.block
        lp = next((x for x in self.doc.result.loops if x.block.id == b.id), None)
        t = self.doc.catalog.target(b.target)
        name = b.axis_name or (default_axis_name(t) if t else b.target)
        n = lp.n if lp else b.sweep_values_count()
        step = f"{b.points} 點" if b.points else f"步進 {b.step:g} {b.unit}" if b.step else "步進 ?"
        rng = f"{b.start:g} → {b.stop:g} {b.unit}" if b.start is not None and b.stop is not None else "範圍未設定"
        lines = [(name, True), (rng, False), (f"{step} · {n} 點" if not b.points else step, False)]
        if self.doc.scheme.snake and lg.level == 0 and len(self.doc.result.loops) > 1:
            lines.append(("蛇形（來回）", False))
        mid = (lg.y_top + lg.y_bot) / 2
        y0 = mid - 9 * len(lines)
        f = p.font()
        for i, (txt, bold) in enumerate(lines):
            f.setBold(bold)
            f.setPointSizeF(9.5 if bold else 9)
            p.setFont(f)
            p.setPen(col if bold else QtGui.QColor("#495057"))
            p.drawText(QRectF(lg.x + 8, y0 + 18 * i, LABEL_W - 14, 18),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)
        f.setBold(False)
        p.setFont(f)

    def _node(self, p: QtGui.QPainter, n: NodeGeom) -> None:
        b = n.block
        col = KIND_COLOR[self._kind(b)]
        sel = b.id == self.doc.selected
        issues = self.doc.result.issues_for(b.id)
        err = any(i.level == "error" for i in issues)
        warn = any(i.level == "warning" for i in issues)
        r = n.rect
        fill = QtGui.QColor(col)
        fill.setAlpha(38 if sel else 16)
        p.setBrush(QtGui.QColor("white"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, 8, 8)
        p.setBrush(fill)
        border = ERR if err else col
        p.setPen(QtGui.QPen(border, 2.6 if (sel or err) else 1.3))
        p.drawRoundedRect(r, 8, 8)
        # 左側色條
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(r.left() + 1.5, r.top() + 8, 5, r.height() - 16), 2.5, 2.5)
        lg = next((g for g in self.layout_.loops if g.block.id == b.id), None)
        if lg is not None:   # 迴圈標記：與右側括號同色
            tag = LEVEL_COLOR[lg.level % len(LEVEL_COLOR)]
            p.setBrush(tag)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(r.right() - 48, r.top() + 8, 38, 16), 8, 8)
            f0 = p.font()
            f0.setPointSizeF(7.5)
            f0.setBold(True)
            p.setFont(f0)
            p.setPen(QtGui.QColor("white"))
            p.drawText(QRectF(r.right() - 48, r.top() + 8, 38, 16), Qt.AlignmentFlag.AlignCenter, "迴圈")
        head, sub = self._titles(b)
        f = p.font()
        f.setBold(True)
        f.setPointSizeF(11)
        p.setFont(f)
        p.setPen(col.darker(115))
        p.drawText(QRectF(r.left() + 16, r.top() + 6, r.width() - 30, 24),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, head)
        f.setBold(False)
        f.setPointSizeF(9)
        p.setFont(f)
        p.setPen(QtGui.QColor("#343a40"))
        sub = p.fontMetrics().elidedText(sub, Qt.TextElideMode.ElideRight, int(r.width() - 26))
        p.drawText(QRectF(r.left() + 16, r.top() + 30, r.width() - 26, 22),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, sub)
        if err or warn:
            c = ERR if err else WARN
            p.setBrush(c)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(r.right() - 12, r.top() + 12), 7, 7)
            p.setPen(QtGui.QColor("white"))
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(r.right() - 19, r.top() + 5, 14, 14), Qt.AlignmentFlag.AlignCenter, "!")
            f.setBold(False)
            p.setFont(f)

    def _gap(self, p: QtGui.QPainter, c: QPointF, hover: bool, drop: bool) -> None:
        if drop:
            p.setPen(QtGui.QPen(KIND_COLOR["dc"], 2.2))
            p.drawLine(QPointF(LEFT - 6, c.y()), QPointF(LEFT + BOX_W + 6, c.y()))
        rad = 8.5
        p.setPen(QtGui.QPen(QtGui.QColor("#1c6dd0" if hover else "#ced4da"), 1.2))
        p.setBrush(QtGui.QColor("#e7f0fc" if hover else "white"))
        p.drawEllipse(c, rad, rad)
        p.setPen(QtGui.QPen(QtGui.QColor("#1c6dd0" if hover else "#adb5bd"), 1.6))
        p.drawLine(QPointF(c.x() - 4, c.y()), QPointF(c.x() + 4, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - 4), QPointF(c.x(), c.y() + 4))

    # ---- 互動 ---------------------------------------------------------------
    def _gap_at(self, pos: QPointF, radius: float = 11) -> Optional[int]:
        for i, g in enumerate(self.layout_.gaps):
            if (not self.layout_.nodes and i > 0):
                continue
            if (g - pos).manhattanLength() <= radius * 1.4:
                return i
        return None

    def _node_at(self, pos: QPointF) -> Optional[NodeGeom]:
        return next((n for n in self.layout_.nodes if n.rect.contains(pos)), None)

    def mouseMoveEvent(self, ev: QtGui.QMouseEvent) -> None:
        g = self._gap_at(ev.position())
        if g != self.hover_gap:
            self.hover_gap = g
            self.setCursor(Qt.CursorShape.PointingHandCursor if g is not None else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, ev) -> None:
        self.hover_gap = None
        self.update()

    def mousePressEvent(self, ev: QtGui.QMouseEvent) -> None:
        self.setFocus()
        pos = ev.position()
        g = self._gap_at(pos)
        if g is not None and ev.button() == Qt.MouseButton.LeftButton:
            menu = self._add_menu(lambda spec, gap=g: self.doc.insert_at_gap(gap, spec))
            menu.exec(self.mapToGlobal(pos.toPoint()))
            return
        n = self._node_at(pos)
        self.doc.select(n.block.id if n else None)

    def contextMenuEvent(self, ev: QtGui.QContextMenuEvent) -> None:
        n = self._node_at(QPointF(ev.pos()))
        if n is None:
            return
        self.doc.select(n.block.id)
        b = n.block
        m = QtWidgets.QMenu(self)
        add = self._add_menu(lambda spec, bid=b.id: self._insert_after(bid, spec), "新增於下方")
        m.addMenu(add)
        if b.kind == "set":
            m.addAction("改成固定值" if b.is_loop else "改成掃描（迴圈）",
                        lambda: self.doc.mutate(lambda s: s.set_mode(b.id, "fixed" if b.is_loop else "sweep")))
        m.addAction("包進新迴圈", lambda: self._wrap(b.id))
        m.addSeparator()
        m.addAction("移入上方迴圈\tTab", lambda: self.doc.mutate(lambda s: s.indent(b.id)))
        m.addAction("移出迴圈\tShift+Tab", lambda: self.doc.mutate(lambda s: s.outdent(b.id)))
        m.addAction("上移\tCtrl+↑", lambda: self.doc.mutate(lambda s: s.move(b.id, -1)))
        m.addAction("下移\tCtrl+↓", lambda: self.doc.mutate(lambda s: s.move(b.id, +1)))
        m.addSeparator()
        m.addAction("刪除\tDelete", lambda: self.doc.mutate(lambda s: s.remove(b.id), select=None))
        m.exec(ev.globalPos())

    def _insert_after(self, bid: str, spec: dict) -> None:
        blk = self.doc.new_block(spec)
        self.doc.mutate(lambda s: s.insert(blk, after=bid, inside=s.find(bid)[0].is_loop), select=blk.id)

    def _wrap(self, bid: str) -> None:
        mag = next((t for t in self.doc.catalog.targets if t.group == "magnet"), None)
        loop = self.doc.new_block({"kind": "set", "target": mag.ref if mag else ""})
        self.doc.mutate(lambda s: s.wrap([bid], loop), select=loop.id)

    def _add_menu(self, on_pick, title: str = "新增方塊") -> QtWidgets.QMenu:
        m = QtWidgets.QMenu(title, self)
        cat = self.doc.catalog
        for group, label in (("magnet", "DC set（電磁鐵）"), ("source", "DC set（單台電源）"), ("param", "儀器參數")):
            items = [t for t in cat.targets if t.group == group]
            if not items:
                continue
            sub = m.addMenu(label)
            for t in items:
                sub.addAction(t.label, lambda t=t: on_pick({"kind": "set", "target": t.ref}))
        sub = m.addMenu("量測")
        for mm in cat.measurers:
            sub.addAction(f"{mm.head} · {mm.label}",
                          lambda mm=mm: on_pick({"kind": "measure", "instrument": mm.ref}))
        m.addAction("Data（存檔）", lambda: on_pick({"kind": "save"}))
        m.addAction("等待", lambda: on_pick({"kind": "wait"}))
        return m

    def keyPressEvent(self, ev: QtGui.QKeyEvent) -> None:
        b = self.doc.selected_block
        k, mod = ev.key(), ev.modifiers()
        ctrl = bool(mod & Qt.KeyboardModifier.ControlModifier)
        if b is None:
            return super().keyPressEvent(ev)
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.doc.mutate(lambda s: s.remove(b.id), select=None)
        elif ctrl and k == Qt.Key.Key_Up:
            self.doc.mutate(lambda s: s.move(b.id, -1))
        elif ctrl and k == Qt.Key.Key_Down:
            self.doc.mutate(lambda s: s.move(b.id, +1))
        elif k == Qt.Key.Key_Backtab:
            self.doc.mutate(lambda s: s.outdent(b.id))
        elif k == Qt.Key.Key_Tab:
            self.doc.mutate(lambda s: s.indent(b.id))
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            flat = self.doc.scheme.flat()
            i = [x.id for x in flat].index(b.id) + (-1 if k == Qt.Key.Key_Up else 1)
            if 0 <= i < len(flat):
                self.doc.select(flat[i].id)
        else:
            return super().keyPressEvent(ev)

    def focusNextPrevChild(self, nxt: bool) -> bool:   # 讓 Tab 留給「移入迴圈」
        return False

    # ---- 拖放 ---------------------------------------------------------------
    def _nearest_gap(self, pos: QPointF) -> int:
        gaps = self.layout_.gaps if self.layout_.nodes else self.layout_.gaps[:1]
        return min(range(len(gaps)), key=lambda i: abs(gaps[i].y() - pos.y()))

    def dragEnterEvent(self, ev: QtGui.QDragEnterEvent) -> None:
        if ev.mimeData().hasFormat(MIME):
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev: QtGui.QDragMoveEvent) -> None:
        if ev.mimeData().hasFormat(MIME):
            self.drop_gap = self._nearest_gap(ev.position())
            self.update()
            ev.acceptProposedAction()

    def dragLeaveEvent(self, ev) -> None:
        self.drop_gap = None
        self.update()

    def dropEvent(self, ev: QtGui.QDropEvent) -> None:
        spec = json.loads(bytes(ev.mimeData().data(MIME)).decode("utf-8"))
        gap = self._nearest_gap(ev.position())
        self.drop_gap = None
        self.doc.insert_at_gap(gap, spec)
        ev.acceptProposedAction()


class Palette(QtWidgets.QTreeWidget):
    """左側方塊庫：點兩下加在選取方塊後面，或拖曳到流程圖。"""

    def __init__(self, doc: SchemeDoc, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setIndentation(14)
        self.setMinimumWidth(210)
        self.itemDoubleClicked.connect(self._add)
        cat = doc.catalog
        groups = [("DC set · 電磁鐵組", [({"kind": "set", "target": t.ref}, t.label, "dc") for t in cat.targets if t.group == "magnet"]),
                  ("DC set · 單台電源", [({"kind": "set", "target": t.ref}, t.label, "dc") for t in cat.targets if t.group == "source"]),
                  ("量測", [({"kind": "measure", "instrument": m.ref}, f"{m.head} · {m.label}",
                            m.kind or "measure") for m in cat.measurers]),
                  ("儀器參數（固定或掃描）", [({"kind": "set", "target": t.ref}, t.label, "param") for t in cat.targets if t.group == "param"]),
                  ("流程", [({"kind": "save"}, "Data（存檔）", "save"), ({"kind": "wait"}, "等待", "wait")])]
        for title, items in groups:
            if not items:
                continue
            top = QtWidgets.QTreeWidgetItem([title])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.addTopLevelItem(top)
            for spec, label, kind in items:
                it = QtWidgets.QTreeWidgetItem([label])
                it.setData(0, Qt.ItemDataRole.UserRole, json.dumps(spec))
                pm = QtGui.QPixmap(10, 10)
                pm.fill(KIND_COLOR[kind])
                it.setIcon(0, QtGui.QIcon(pm))
                top.addChild(it)
            top.setExpanded(title != "儀器參數（固定或掃描）" and title != "DC set · 單台電源")

    def _add(self, item: QtWidgets.QTreeWidgetItem) -> None:
        spec = item.data(0, Qt.ItemDataRole.UserRole)
        if spec:
            self.doc.insert_near_selection(json.loads(spec))

    def startDrag(self, actions) -> None:
        item = self.currentItem()
        spec = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not spec:
            return
        md = QtCore.QMimeData()
        md.setData(MIME, spec.encode("utf-8"))
        drag = QtGui.QDrag(self)
        drag.setMimeData(md)
        drag.exec(Qt.DropAction.CopyAction)
