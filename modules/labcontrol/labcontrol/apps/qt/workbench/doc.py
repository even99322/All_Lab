"""工作台共用狀態：方案（節點圖）、選取、編譯結果、復原 / 重做。

四個面板（流程圖、儀器參數、即時監控、檔案設置）都只跟這個物件溝通：
    doc.mutate(fn, origin)     修改方案（自動存復原點、重新編譯、發出 changed）
    doc.changed(origin)        發出修改的元件可以略過重建，避免打斷正在輸入的欄位
    doc.selection_changed(id)  選取的節點 id（或 None）
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal

from ....scheme import Block, Catalog, CompileResult, Scheme, compile_scheme
from ....scheme.graph import BODY, NEXT, START, SchemeGraph
from ....settings import setting


class WorkbenchDoc(QObject):
    changed = pyqtSignal(object)
    selection_changed = pyqtSignal(object)

    def __init__(self, scheme: Scheme, catalog: Catalog, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.scheme = scheme
        self.scheme.ensure_graph()
        self.scheme.sync_from_graph()
        self.selected: Optional[str] = None
        self.path: Optional[str] = None
        self.dirty = False
        self._undo: List[dict] = []
        self._redo: List[dict] = []
        self.result: CompileResult = compile_scheme(self.scheme, catalog)

    # ---- 讀取 -------------------------------------------------------------
    @property
    def graph(self) -> SchemeGraph:
        return self.scheme.graph

    def node(self, nid: Optional[str]) -> Optional[Block]:
        return self.graph.nodes.get(nid) if nid else None

    # ---- 修改 -------------------------------------------------------------
    def mutate(self, fn: Callable[[Scheme], Any], origin: object = None, select: Any = "keep") -> Any:
        snap = self.scheme.to_dict()
        out = fn(self.scheme)
        self.scheme.sync_from_graph()
        if self.scheme.to_dict() == snap:
            return out
        self._undo.append(snap)
        del self._undo[:-300]
        self._redo.clear()
        self._after_change(origin)
        if select != "keep":
            self.select(select)
        elif self.selected and self.selected != START and self.node(self.selected) is None:
            self.select(None)
        return out

    def move_nodes(self, positions: Dict[str, Tuple[float, float]], origin: object = None) -> None:
        def fn(s: Scheme) -> None:
            s.graph.pos.update(positions)
        self.mutate(fn, origin)

    def replace_scheme(self, scheme: Scheme, path: Optional[str] = None) -> None:
        scheme.ensure_graph()
        scheme.sync_from_graph()
        self._undo.clear()
        self._redo.clear()
        self.scheme = scheme
        self.path = path
        self.selected = None
        self._after_change(None)
        self.dirty = False
        self.selection_changed.emit(None)

    def set_catalog(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self._after_change(None)

    def undo(self) -> None:
        if self._undo:
            self._redo.append(self.scheme.to_dict())
            self.scheme = Scheme.from_dict(self._undo.pop())
            self.scheme.ensure_graph()
            self._after_change(None)

    def redo(self) -> None:
        if self._redo:
            self._undo.append(self.scheme.to_dict())
            self.scheme = Scheme.from_dict(self._redo.pop())
            self.scheme.ensure_graph()
            self._after_change(None)

    def select(self, nid: Optional[str]) -> None:
        if nid != self.selected:
            self.selected = nid
            self.selection_changed.emit(nid)

    def recompile(self) -> None:
        self.result = compile_scheme(self.scheme, self.catalog)

    def _after_change(self, origin: object) -> None:
        self.dirty = True
        self.recompile()
        self.changed.emit(origin)

    # ---- 建立節點 -----------------------------------------------------------
    def new_block(self, spec: Dict[str, Any]) -> Block:
        """spec：{"kind": "set", "target": ref} | {"kind": "measure", "instrument": 名稱, "trace": 通道}
        | {"kind": "save"} | {"kind": "wait"}。初始值來自 settings.yaml editor.new_blocks 與 driver 預設。"""
        nb = setting("editor.new_blocks", {}) or {}
        kind = spec["kind"]
        if kind == "set":
            t = self.catalog.target(spec["target"])
            unit = t.unit if t else ""
            src = bool(t and t.group in ("magnet", "source"))
            d = dict(nb.get("dc_set" if src else "param_set") or {})
            d.pop("unit", None)
            mode = spec.get("mode") or d.pop("mode", "fixed")
            d.pop("mode", None)
            fields = {k: d[k] for k in ("start", "stop", "step", "points", "settle", "value") if k in d}
            if not src and t is not None and mode == "sweep" and "start" not in fields:
                lo, hi = t.limits or (0.0, 1.0)
                from ....core.units import split_unit
                scale = split_unit(unit)[1] or 1.0
                fields.update(start=lo / scale, stop=hi / scale, points=11)
            return Block("set", target=spec["target"], mode=mode, unit=unit, **fields)
        if kind == "measure":
            m = self.catalog.measurer(spec["instrument"])
            traces = [spec["trace"]] if spec.get("trace") else ([m.traces[0]] if m and m.traces else [])
            return Block("measure", instrument=spec["instrument"], traces=traces,
                         settings=dict(m.defaults) if m else {})
        if kind == "save":
            return Block("save")
        return Block("wait", seconds=float((nb.get("wait") or {}).get("seconds", 1.0)))

    def add_node(self, spec: Dict[str, Any], pos: Optional[Tuple[float, float]] = None,
                 after: Optional[str] = None, on_link: Optional[Tuple[str, str]] = None,
                 origin: object = None) -> Block:
        """新增節點。on_link=(src, port)：插在那條線上；after=節點 id：接在它後面；都沒有 = 不連線。"""
        blk = self.new_block(spec)

        def fn(s: Scheme) -> None:
            g = s.graph
            p = pos
            if p is None:
                p = self._auto_pos(g, on_link[0] if on_link else after, on_link[1] if on_link else None)
            g.add(blk, p)
            if on_link is not None:
                g.insert_on_link(on_link[0], on_link[1], blk.id)
            elif after is not None:
                g.append_after(after, blk.id)
        self.mutate(fn, origin, select=blk.id)
        return blk

    def append_to_flow(self, spec: Dict[str, Any], origin: object = None) -> Block:
        """「加到流程」：接在選取的節點後面；沒有選取就接在主流程最後。"""
        g = self.graph
        after = self.selected if (self.selected == START or self.selected in g.nodes) else None
        if after is None:
            after = self._main_tail(g)
        return self.add_node(spec, after=after, origin=origin)

    @staticmethod
    def _main_tail(g: SchemeGraph) -> str:
        """主流程最後一個節點（量測所在的最內層迴圈 body 尾端優先，讓「加量測」自然落在迴圈裡）。"""
        cur = START
        while True:
            nxt = g.links.get((cur, NEXT))
            if nxt is None:
                return cur
            cur = nxt

    @staticmethod
    def _auto_pos(g: SchemeGraph, anchor: Optional[str], port: Optional[str]) -> Tuple[float, float]:
        from ....scheme.graph import DX, DY

        if anchor is None:
            xs = [p[0] for p in g.pos.values()] or [0.0]
            ys = [p[1] for p in g.pos.values()] or [0.0]
            return (max(xs) + DX, min(ys))
        ax, ay = g.pos.get(anchor, (0.0, 0.0))
        node = g.nodes.get(anchor)
        use_body = port == BODY or (port is None and node is not None and node.is_loop
                                    and (anchor, BODY) not in g.links)
        cand = (ax + DX, ay + DY) if use_body else (ax, ay + DY)
        taken = set((round(x), round(y)) for x, y in g.pos.values())
        while (round(cand[0]), round(cand[1])) in taken:
            cand = (cand[0] + 30, cand[1] + 30)
        return cand

    def auto_layout(self, origin: object = None) -> None:
        def fn(s: Scheme) -> None:
            g = s.graph
            sx, sy = g.pos.get(START, (0.0, 0.0))
            fresh = SchemeGraph.from_blocks(g.to_blocks(), origin=(sx, sy))
            for nid, p in fresh.pos.items():
                if nid in g.pos:
                    g.pos[nid] = p
            loose = g.loose()
            if loose:
                right = max(p[0] for n, p in fresh.pos.items()) + 1.5 * 300
                for i, nid in enumerate(loose):
                    g.pos[nid] = (right, sy + i * 110)
        self.mutate(fn, origin)
