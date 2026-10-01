"""編輯器共用狀態：方案、選取、編譯結果、復原 / 重做。流程圖、屬性面板、參數表都只跟它溝通。"""
from __future__ import annotations

from typing import Callable, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from ....scheme import Block, Catalog, CompileResult, Scheme, compile_scheme


class SchemeDoc(QObject):
    #: origin = 發出修改的元件（讓它自己不必重建畫面，避免打斷正在輸入的欄位）
    changed = pyqtSignal(object)
    selection_changed = pyqtSignal(object)   # block id 或 None

    def __init__(self, scheme: Scheme, catalog: Catalog, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.scheme = scheme
        self.catalog = catalog
        self.selected: Optional[str] = None
        self.path: Optional[str] = None
        self.dirty = False
        self._undo: List[dict] = []
        self._redo: List[dict] = []
        self.result: CompileResult = compile_scheme(scheme, catalog)

    # ---- 讀取 -------------------------------------------------------------
    def block(self, bid: Optional[str]) -> Optional[Block]:
        if bid is None:
            return None
        try:
            return self.scheme.find(bid)[0]
        except KeyError:
            return None

    @property
    def selected_block(self) -> Optional[Block]:
        return self.block(self.selected)

    # ---- 修改 -------------------------------------------------------------
    def mutate(self, fn: Callable[[Scheme], object], origin: object = None, select: Optional[str] = "keep") -> object:
        """所有修改都走這裡：先存復原點、套用、重新編譯、通知。"""
        snap = self.scheme.to_dict()
        out = fn(self.scheme)
        if self.scheme.to_dict() == snap:
            return out
        self._undo.append(snap)
        del self._undo[:-200]
        self._redo.clear()
        self._after_change(origin)
        if select != "keep":
            self.select(select)
        elif self.selected and self.block(self.selected) is None:
            self.select(None)
        return out

    def set_field(self, bid: str, field: str, value: object, origin: object = None) -> None:
        def fn(s: Scheme) -> None:
            setattr(s.find(bid)[0], field, value)
        self.mutate(fn, origin)

    def replace_scheme(self, scheme: Scheme, path: Optional[str] = None) -> None:
        self._undo.clear()
        self._redo.clear()
        self.scheme = scheme
        self.path = path
        self.selected = None
        self._after_change(None)
        self.dirty = False
        self.selection_changed.emit(None)

    def undo(self) -> None:
        if self._undo:
            self._redo.append(self.scheme.to_dict())
            self.scheme = Scheme.from_dict(self._undo.pop())
            self._after_change(None)
            self.selection_changed.emit(self.selected if self.block(self.selected) else None)

    def redo(self) -> None:
        if self._redo:
            self._undo.append(self.scheme.to_dict())
            self.scheme = Scheme.from_dict(self._redo.pop())
            self._after_change(None)
            self.selection_changed.emit(self.selected if self.block(self.selected) else None)

    def select(self, bid: Optional[str]) -> None:
        if bid != self.selected:
            self.selected = bid
            self.selection_changed.emit(bid)

    def _after_change(self, origin: object) -> None:
        self.dirty = True
        self.result = compile_scheme(self.scheme, self.catalog)
        self.changed.emit(origin)

    # ---- 新增方塊（palette / 流程圖「＋」共用）--------------------------------
    def new_block(self, spec: dict) -> Block:
        """spec = {"kind": ..., "target"/"instrument": ...}（由 palette 產生）"""
        from ....settings import setting

        nb = setting("editor.new_blocks", {}) or {}
        kind = spec["kind"]
        if kind == "set":
            t = self.catalog.target(spec["target"])
            unit = t.unit if t else ""
            d = dict(nb.get("dc_set" if (t and t.group in ("magnet", "source")) else "param_set") or {})
            d.pop("unit", None)          # 單位跟著目標（instruments.yaml display_unit）
            mode = d.pop("mode", "fixed")
            fields = {k: d[k] for k in ("start", "stop", "step", "points", "settle", "value") if k in d}
            return Block("set", target=spec["target"], mode=mode, unit=unit, **fields)
        if kind == "measure":
            m = self.catalog.measurer(spec["instrument"])
            return Block("measure", instrument=spec["instrument"], traces=[m.traces[0]] if m and m.traces else [],
                         settings=dict(m.defaults) if m else {})
        if kind == "save":
            return Block("save", file_name=f"{self.scheme.name}.hdf5",
                         formats=list(setting("data.default_formats", ["labber"]) or []),
                         collision=setting("data.collision", "underscore"))
        return Block("wait", seconds=float((nb.get("wait") or {}).get("seconds", 1.0)))

    def insert_at_gap(self, gap: int, spec: dict) -> Block:
        """gap = 流程圖第 gap 個方塊之前的位置（0 = 最上面，len = 最下面）。"""
        blk = self.new_block(spec)

        def fn(s: Scheme) -> None:
            flat = s.flat()
            if gap <= 0 or not flat:
                s.blocks.insert(0, blk)
            else:
                prev = flat[gap - 1]
                s.insert(blk, after=prev.id, inside=prev.is_loop)
        self.mutate(fn, select=blk.id)
        return blk

    def insert_near_selection(self, spec: dict) -> Block:
        """palette 點兩下：放在選取方塊之後（選取的是迴圈 → 放進迴圈）；Data 預設放最後。"""
        blk = self.new_block(spec)
        sel = self.selected_block

        def fn(s: Scheme) -> None:
            if blk.kind == "save" or sel is None:
                s.blocks.append(blk)
            else:
                s.insert(blk, after=sel.id, inside=sel.is_loop)
        self.mutate(fn, select=blk.id)
        return blk
