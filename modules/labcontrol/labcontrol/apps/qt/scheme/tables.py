"""Labber 式參數表（步進序列 / 固定設定 / 量測通道）＋ 檢查與估時 ＋ YAML 預覽。

與流程圖是同一份資料的兩種看法：在這裡改數字，流程圖同步更新，反之亦然。
"""
from __future__ import annotations

from typing import Any, List, Optional

import yaml
from PyQt6 import QtGui, QtWidgets
from PyQt6.QtCore import Qt

from ....scheme import Block, format_duration
from ....scheme.compile import default_axis_name
from .doc import SchemeDoc

ROLE_BID = Qt.ItemDataRole.UserRole
ROLE_FIELD = Qt.ItemDataRole.UserRole + 1


def _item(text: Any, bid: Optional[str] = None, field: Optional[str] = None, editable: bool = False,
          align_right: bool = False) -> QtWidgets.QTableWidgetItem:
    it = QtWidgets.QTableWidgetItem("" if text is None else (f"{text:g}" if isinstance(text, float) else str(text)))
    it.setData(ROLE_BID, bid)
    it.setData(ROLE_FIELD, field)
    flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
    if editable:
        flags |= Qt.ItemFlag.ItemIsEditable
    else:
        it.setForeground(QtGui.QColor("#495057"))
    it.setFlags(flags)
    if align_right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return it


class _Table(QtWidgets.QTableWidget):
    def __init__(self, headers: List[str]) -> None:
        super().__init__(0, len(headers))
        self.setHorizontalHeaderLabels(headers)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.horizontalHeader().setStretchLastSection(True)
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked
                             | QtWidgets.QAbstractItemView.EditTrigger.EditKeyPressed
                             | QtWidgets.QAbstractItemView.EditTrigger.SelectedClicked)


class ParamTables(QtWidgets.QWidget):
    def __init__(self, doc: SchemeDoc, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self._loading = False
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)

        head = QtWidgets.QHBoxLayout()
        t = QtWidgets.QLabel("<b>步進序列</b>（Step sequence，由外層到內層；雙擊儲存格修改）")
        head.addWidget(t)
        head.addStretch()
        self.btn_outer = QtWidgets.QPushButton("＋ 外層迴圈")
        self.btn_inner = QtWidgets.QPushButton("＋ 內層迴圈")
        self.btn_up = QtWidgets.QPushButton("▲ 移到外層")
        self.btn_down = QtWidgets.QPushButton("▼ 移到內層")
        self.btn_del = QtWidgets.QPushButton("刪除迴圈")
        for b in (self.btn_outer, self.btn_inner, self.btn_up, self.btn_down, self.btn_del):
            head.addWidget(b)
        lay.addLayout(head)
        self.steps = _Table(["層", "資料軸名稱（step channel）", "目標", "起點", "終點", "步進", "點數", "單位",
                             "每點等待 s", "存檔"])
        lay.addWidget(self.steps, 3)

        mid = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("<b>固定設定</b>（開始量測前設定一次）"))
        self.fixed = _Table(["目標", "值", "單位", "來源"])
        left.addWidget(self.fixed)
        right = QtWidgets.QVBoxLayout()
        right.addWidget(QtWidgets.QLabel("<b>量測通道</b>（Log channels）"))
        self.logs = _Table(["儀器", "通道", "Labber 名稱", "x 軸"])
        right.addWidget(self.logs)
        mid.addLayout(left, 1)
        mid.addLayout(right, 1)
        lay.addLayout(mid, 2)

        self.steps.itemChanged.connect(self._edited)
        self.fixed.itemChanged.connect(self._edited)
        for tbl in (self.steps, self.fixed, self.logs):
            tbl.itemSelectionChanged.connect(lambda tbl=tbl: self._select_from(tbl))
        self.btn_outer.clicked.connect(self._add_outer)
        self.btn_inner.clicked.connect(self._add_inner)
        self.btn_up.clicked.connect(lambda: self._reorder(-1))
        self.btn_down.clicked.connect(lambda: self._reorder(+1))
        self.btn_del.clicked.connect(self._delete_loop)
        doc.changed.connect(lambda origin: origin is self or self.rebuild())
        doc.selection_changed.connect(lambda _b: self._sync_selection())
        self.rebuild()

    # ---- 建表 ---------------------------------------------------------------
    def rebuild(self) -> None:
        self._loading = True
        try:
            s, cat, r = self.doc.scheme, self.doc.catalog, self.doc.result
            loops = [(b, d) for b, d, _ in s.walk() if b.is_loop]
            chain_ids = [lp.block.id for lp in r.loops]
            self.steps.setRowCount(len(loops))
            for row, (b, depth) in enumerate(loops):
                t = cat.target(b.target)
                lp = next((x for x in r.loops if x.block.id == b.id), None)
                split = lp is not None and chain_ids.index(b.id) < r.split_depth
                cells = [
                    _item("外" * depth + "▸" if depth else "最外層", b.id),
                    _item(b.axis_name or (default_axis_name(t) if t else ""), b.id, "axis_name", True),
                    _item(t.label if t else b.target, b.id),
                    _item(b.start, b.id, "start", True, True),
                    _item(b.stop, b.id, "stop", True, True),
                    _item(None if b.points else b.step, b.id, "step", not b.points, True),
                    _item(lp.n if lp else b.sweep_values_count(), b.id, "points", True, True),
                    _item(b.unit, b.id),
                    _item(b.settle, b.id, "settle", True, True),
                    _item("每個值一檔" if split else "", b.id),
                ]
                cells[0].setText(["最外層", "第 2 層", "第 3 層", "第 4 層", "第 5 層"][min(depth, 4)])
                if r.issues_for(b.id) and any(i.level == "error" for i in r.issues_for(b.id)):
                    for c in cells:
                        c.setBackground(QtGui.QColor("#fff0f0"))
                for col, c in enumerate(cells):
                    self.steps.setItem(row, col, c)
            self.steps.resizeColumnsToContents()

            rows = []
            for b in s.flat():
                if b.kind == "set" and not b.is_loop:
                    t = cat.target(b.target)
                    rows.append([_item(t.label if t else b.target, b.id), _item(b.value, b.id, "value", True, True),
                                 _item(b.unit, b.id), _item("設定方塊", b.id)])
            for b in s.flat():
                if b.kind == "measure":
                    m = cat.measurer(b.instrument)
                    for k, v in (b.settings or {}).items():
                        rows.append([_item(f"{m.short if m else b.instrument} {m.field_label(k) if m else k}", b.id),
                                     _item(v, b.id, f"settings.{k}", True, True), _item("", b.id),
                                     _item("量測方塊", b.id)])
            self.fixed.setRowCount(len(rows))
            for i, row in enumerate(rows):
                for j, c in enumerate(row):
                    self.fixed.setItem(i, j, c)
            self.fixed.resizeColumnsToContents()

            rows = []
            for b in s.flat():
                if b.kind == "measure":
                    m = cat.measurer(b.instrument)
                    for tr in b.traces:
                        rows.append([_item(m.short if m else b.instrument, b.id), _item(tr, b.id),
                                     _item(f"{m.labber_name if m else b.instrument} - {tr}", b.id),
                                     _item("Frequency (Hz)", b.id)])
            self.logs.setRowCount(len(rows))
            for i, row in enumerate(rows):
                for j, c in enumerate(row):
                    self.logs.setItem(i, j, c)
            self.logs.resizeColumnsToContents()
        finally:
            self._loading = False
        self._sync_selection()

    # ---- 編輯 ---------------------------------------------------------------
    def _edited(self, it: QtWidgets.QTableWidgetItem) -> None:
        if self._loading:
            return
        bid, field = it.data(ROLE_BID), it.data(ROLE_FIELD)
        if not bid or not field:
            return
        text = it.text().strip()
        if field.startswith("settings."):
            key = field.split(".", 1)[1]

            def fn(s):
                b = s.find(bid)[0]
                val: Any = text
                if key in ("points", "averages"):
                    try:
                        val = int(float(text))
                    except ValueError:
                        return
                b.settings = {**b.settings, key: val}
            self.doc.mutate(fn, origin=self)
            return
        if field == "axis_name":
            self.doc.set_field(bid, field, text, origin=self)
            return
        try:
            v = float(text)
        except ValueError:
            self.rebuild()
            return
        if field == "points":
            def fn(s):
                b = s.find(bid)[0]
                b.points = max(2, int(v))
            self.doc.mutate(fn, origin=None)   # 改點數 → 步進欄位改成由點數決定，整表重建
        else:
            self.doc.set_field(bid, field, v, origin=self)

    def _select_from(self, tbl: QtWidgets.QTableWidget) -> None:
        if self._loading:
            return
        items = tbl.selectedItems()
        if items:
            self.doc.select(items[0].data(ROLE_BID))

    def _sync_selection(self) -> None:
        self._loading = True
        try:
            for tbl in (self.steps, self.fixed, self.logs):
                tbl.clearSelection()
                for r in range(tbl.rowCount()):
                    it = tbl.item(r, 0)
                    if it is not None and it.data(ROLE_BID) == self.doc.selected:
                        tbl.selectRow(r)
                        break
        finally:
            self._loading = False

    def _new_loop(self) -> Block:
        mags = [t for t in self.doc.catalog.targets if t.group == "magnet"]
        used = {b.target for b in self.doc.scheme.flat() if b.kind == "set"}
        pick = next((t for t in mags if t.ref not in used), mags[0] if mags else None)
        return self.doc.new_block({"kind": "set", "target": pick.ref if pick else ""})

    def _add_outer(self) -> None:
        loop = self._new_loop()
        self.doc.mutate(lambda s: s.add_outer_loop(loop), select=loop.id)

    def _add_inner(self) -> None:
        loop = self._new_loop()
        self.doc.mutate(lambda s: s.add_inner_loop(loop), select=loop.id)

    def _selected_loop(self) -> Optional[Block]:
        b = self.doc.selected_block
        return b if b is not None and b.is_loop else None

    def _reorder(self, direction: int) -> None:
        b = self._selected_loop()
        if b is None:
            return
        bid = b.id
        if direction > 0:     # 往內：與子迴圈交換
            self.doc.mutate(lambda s: s.swap_with_inner(bid))
        else:                 # 往外：與父迴圈交換
            anc = self.doc.scheme.ancestors(bid)
            if anc:
                pid = anc[-1].id
                self.doc.mutate(lambda s: s.swap_with_inner(pid))

    def _delete_loop(self) -> None:
        b = self._selected_loop()
        if b is not None:
            bid = b.id
            self.doc.mutate(lambda s: s.remove(bid), select=None)


class IssuesPanel(QtWidgets.QWidget):
    """檢查結果與估時。點一行 → 選取對應方塊。"""

    def __init__(self, doc: SchemeDoc, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.summary = QtWidgets.QLabel()
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.list = QtWidgets.QListWidget()
        lay.addWidget(self.list, 1)
        self.list.itemClicked.connect(lambda it: self.doc.select(it.data(ROLE_BID)) if it.data(ROLE_BID) else None)
        doc.changed.connect(lambda _o: self.rebuild())
        self.rebuild()

    def rebuild(self) -> None:
        r = self.doc.result
        br = "、".join(f"{k} {format_duration(v)}" for k, v in r.est_breakdown.items() if v > 0.5)
        state = "<span style='color:#2b8a3e'>✔ 可以執行</span>" if r.ok else "<span style='color:#e03131'>✖ 有錯誤，無法執行</span>"
        files = f"{r.n_files} 個檔" if r.n_files else "只寫 raw 檔"
        self.summary.setText(f"{state}　　總點數 <b>{r.total_points:,}</b>　·　預估 <b>{format_duration(r.est_seconds)}</b>"
                             f"（{br or '—'}）　·　{files}")
        self.list.clear()
        icons = {"error": "✖", "warning": "⚠", "info": "ℹ"}
        colors = {"error": "#e03131", "warning": "#e67700", "info": "#1971c2"}
        for i in sorted(r.issues, key=lambda i: ["error", "warning", "info"].index(i.level)):
            it = QtWidgets.QListWidgetItem(f"{icons[i.level]}  {i.message}")
            it.setForeground(QtGui.QColor(colors[i.level]))
            it.setData(ROLE_BID, i.block_id)
            self.list.addItem(it)
        if not r.issues:
            self.list.addItem("沒有問題")


class YamlView(QtWidgets.QWidget):
    def __init__(self, doc: SchemeDoc, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.which = QtWidgets.QComboBox()
        self.which.addItems(["方案檔（存檔內容）", "編譯後的實驗設定（交給引擎執行）"])
        lay.addWidget(self.which)
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
        lay.addWidget(self.text, 1)
        self.which.currentIndexChanged.connect(lambda _i: self.rebuild())
        doc.changed.connect(lambda _o: self.rebuild())
        self.rebuild()

    def rebuild(self) -> None:
        if self.which.currentIndex() == 0:
            data = self.doc.scheme.to_dict()
        else:
            cfg = self.doc.result.config
            data = {k: v for k, v in cfg.items() if k != "scheme"} if cfg else {"錯誤": [i.message for i in self.doc.result.issues if i.level == "error"]}
        self.text.setPlainText(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))

