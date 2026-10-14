"""右側「Data 方塊」分頁（1.0.2）：在流程圖點一下 Data / 去背方塊，就在這裡設定。

Data：
  * 這個 Data 存什麼（主要 / 額外 / 背景）、檔名、格式；
  * 分檔命名規則：{stem}_{label}，label = 外圈的值（data_100350mA、data_100360mA…）；
    外圈的值不變（單純重複掃描）→ p1、p2、p3（data_p1、data_p2…）；
  * 數值格式：100350mA（3 位小數、去掉小數點）、100.35mA、100p35mA；
  * 預覽前幾個檔名。
去背：存到哪個資料夾、檔名後綴（_debg）。
背景＋去背（1.0.6，流程圖下方的精簡方塊）：勾選電源（不限台數）與背景電流、等待、背景檔名、去背存檔。
"""
from __future__ import annotations

import itertools
from typing import Any, List, Optional

import numpy as np
from PyQt6 import QtWidgets

from ....data.dataset import AxisSpec
from ....measure.experiment import split_name
from ....measure.segments import segment_label
from ....scheme import background as bgmod
from ....scheme.compile import norm_formats, split_stages
from ....settings import setting
from .doc import WorkbenchDoc

STYLES = [("nodot", "100350mA（3 位小數、去掉小數點）"), ("plain", "100.35mA"), ("p", "100p35mA（小數點換成 p）")]


class DataPanel(QtWidgets.QWidget):
    def __init__(self, doc: WorkbenchDoc, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.nid: Optional[str] = None
        self._loading = False
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        self.hint = QtWidgets.QLabel("在流程圖上點一下 Data、去背 或「背景＋去背」方塊，在這裡設定。")
        self.hint.setWordWrap(True)
        self.hint.setProperty("role", "muted")
        v.addWidget(self.hint)

        # ---- Data ----
        self.save_box = QtWidgets.QGroupBox("Data")
        f = QtWidgets.QFormLayout(self.save_box)
        self.role = QtWidgets.QLabel()
        self.role.setWordWrap(True)
        f.addRow(self.role)
        self.file_name = QtWidgets.QLineEdit()
        self.file_name.setObjectName("dataFileName")
        self.file_name.editingFinished.connect(lambda: self._set("file_name", self.file_name.text().strip()))
        f.addRow("檔名", self.file_name)
        fm = QtWidgets.QHBoxLayout()
        self.f_nc = QtWidgets.QCheckBox("NetCDF（.nc）")
        self.f_h5 = QtWidgets.QCheckBox("HDF5")
        for cb in (self.f_nc, self.f_h5):
            cb.toggled.connect(lambda _on: self._set_formats())
            fm.addWidget(cb)
        fm.addStretch(1)
        self.formats_row = QtWidgets.QWidget()
        self.formats_row.setLayout(fm)
        f.addRow("格式", self.formats_row)
        self.pattern = QtWidgets.QLineEdit()
        self.pattern.setObjectName("dataNamePattern")
        self.pattern.setPlaceholderText(setting("data.split_pattern", "{stem}_{label}"))
        self.pattern.setToolTip("{stem} = 檔名；{label} = 外圈的值（100350mA）或重複次數（p1）；"
                                "{n} = 第幾個（1 起）；{index:03d} = 001、002…")
        self.pattern.editingFinished.connect(lambda: self._set("name_pattern", self.pattern.text().strip()))
        f.addRow("分檔命名", self.pattern)
        self.style = QtWidgets.QComboBox()
        self.style.setObjectName("dataLabelStyle")
        for k, label in STYLES:
            self.style.addItem(label, k)
        self.style.currentIndexChanged.connect(lambda _i: self._set("label_style", self.style.currentData()))
        f.addRow("數值格式", self.style)
        rule = QtWidgets.QLabel("外圈的值有變化 → 檔名加上那個值（data_100350mA、data_100360mA…）；"
                                "值不變（單純重複掃描）→ 加上次數（data_p1、data_p2…）。")
        rule.setWordWrap(True)
        rule.setProperty("role", "muted")
        f.addRow(rule)
        self.preview = QtWidgets.QPlainTextEdit()
        self.preview.setObjectName("dataNamePreview")
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(110)
        f.addRow("預覽", self.preview)
        v.addWidget(self.save_box)

        # ---- 去背 ----
        self.debg_box = QtWidgets.QGroupBox("去背")
        g = QtWidgets.QFormLayout(self.debg_box)
        note = QtWidgets.QLabel("主量測量完 → 量背景（迴圈之後的固定設定 → 量測 → Data）→ 主數據的每個檔除以背景 trace，"
                                "另存成「原檔名＋後綴」。")
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        g.addRow(note)
        row = QtWidgets.QHBoxLayout()
        self.dest = QtWidgets.QLineEdit()
        self.dest.setObjectName("debgDest")
        self.dest.setPlaceholderText("（與數據同一個資料夾）")
        self.dest.editingFinished.connect(lambda: self._set("dest", self.dest.text().strip()))
        pick = QtWidgets.QPushButton("📂")
        pick.setToolTip("選資料夾")
        pick.clicked.connect(self._pick_dest)
        row.addWidget(self.dest, 1)
        row.addWidget(pick)
        g.addRow("存到", row)
        self.suffix = QtWidgets.QLineEdit()
        self.suffix.setObjectName("debgSuffix")
        self.suffix.editingFinished.connect(lambda: self._set("suffix", self.suffix.text().strip() or "_debg"))
        g.addRow("檔名後綴", self.suffix)
        self.debg_preview = QtWidgets.QLabel()
        self.debg_preview.setWordWrap(True)
        g.addRow(self.debg_preview)
        v.addWidget(self.debg_box)
        v.addWidget(self._build_bg_box())
        v.addStretch(1)

        doc.selection_changed.connect(self.show_block)
        doc.changed.connect(lambda _o: self.show_block(self.doc.selected))
        self.show_block(None)

    # ---- 顯示 -------------------------------------------------------------------
    def block(self):
        return self.doc.node(self.nid) if self.nid else None

    def show_block(self, nid) -> None:
        if nid == bgmod.BG_ID and bgmod.enabled(self.doc.scheme):
            self.nid = None
            self.hint.setVisible(False)
            self.save_box.setVisible(False)
            self.debg_box.setVisible(False)
            self.bg_box.setVisible(True)
            self._show_background()
            return
        self.bg_box.setVisible(False)
        b = self.doc.node(nid) if nid else None
        self.nid = nid if b is not None and b.kind in ("save", "debg") else None
        b = self.block()
        self.hint.setVisible(b is None)
        self.save_box.setVisible(b is not None and b.kind == "save")
        self.debg_box.setVisible(b is not None and b.kind == "debg")
        if b is None:
            return
        self._loading = True
        try:
            if b.kind == "save":
                self._show_save(b)
            else:
                self.dest.setText(b.dest or "")
                self.suffix.setText(b.suffix or "_debg")
                stem = self._main_stem()
                self.debg_preview.setText(f"例如：{stem}_100350mA{b.suffix or '_debg'}.nc")
        finally:
            self._loading = False

    def role_of(self, b) -> tuple:
        """(角色, 分檔層數, 檔名) 角色：main | extra | background。"""
        r = self.doc.result
        if any(b.id in ids for ids in getattr(r, "post_blocks", []) or []):
            return "background", 0, b.file_name or f"{self._main_stem()}_bg.nc"
        e = next((e for e in getattr(r, "extras", []) or [] if e["block"] == b.id), None)
        if e is not None:
            return "extra", int(e["depth"]), e["file_name"]
        return "main", int(getattr(r, "split_depth", 0) or 0), \
            self.doc.scheme.output.get("file_name") or f"{self.doc.scheme.name}.nc"

    def _main_stem(self) -> str:
        from pathlib import Path

        return Path(self.doc.scheme.output.get("file_name") or f"{self.doc.scheme.name}.nc").stem

    def _show_save(self, b) -> None:
        role, depth, name = self.role_of(b)
        loops = self.doc.result.loops[:depth] if role != "background" else []
        if role == "main":
            what = ("每次" + "、".join(lp.target.short for lp in loops) + "走一步存一個檔") if loops else "整個量測存成一個檔"
            text = f"主要 Data：{what}。檔名與格式在「檔案」分頁。"
        elif role == "extra":
            text = "額外的 Data：" + (("每次" + "、".join(lp.target.short for lp in loops) + "走一步存一個檔")
                                     if loops else "整個量測再存一個檔")
        else:
            text = "背景 Data：主量測完成後量一次背景，存成一個檔（去背用）。"
        self.role.setText(text)
        main = role == "main"
        self.file_name.setText(name if main else (b.file_name or ""))
        self.file_name.setReadOnly(main)
        self.file_name.setPlaceholderText("" if main else name)
        fmts = norm_formats(self.doc.scheme.output.get("formats") or b.formats) if main else norm_formats(b.formats)
        self.f_nc.setChecked("netcdf" in fmts)
        self.f_h5.setChecked("hdf5" in fmts)
        self.formats_row.setEnabled(not main)
        self.pattern.setText(b.name_pattern or "")
        self.pattern.setEnabled(bool(loops))
        self.style.setCurrentIndex(max(0, self.style.findData(b.label_style or setting("data.label_style", "nodot"))))
        self.style.setEnabled(bool(loops))
        self.preview.setPlainText("\n".join(self.preview_names(b, loops, name)))

    def preview_names(self, b, loops, name: str, limit: int = 4) -> List[str]:
        from pathlib import Path

        ext = ".nc" if "netcdf" in (norm_formats(b.formats) or ["netcdf"]) else ".h5"
        stem = Path(name).stem
        if not loops:
            return [stem + ext]
        axes = [AxisSpec(lp.axis_name, lp.target.ref, "", np.asarray(lp.values), lp.display_unit, lp.display_scale)
                for lp in loops]
        names = [a.name for a in axes]
        pattern = b.name_pattern or setting("data.split_pattern", "{stem}_{label}")
        style = b.label_style or setting("data.label_style", "nodot")
        keys = list(itertools.islice(itertools.product(*[range(len(a.values)) for a in axes]), limit))
        total = int(np.prod([len(a.values) for a in axes]))
        out = []
        for key in keys:
            index, label = segment_label(axes, names, key, by_index=True, style=style)
            out.append(split_name(pattern, stem, index, label) + ext)
        if total > limit:
            last = tuple(len(a.values) - 1 for a in axes)
            index, label = segment_label(axes, names, last, by_index=True, style=style)
            out += ["…", split_name(pattern, stem, index, label) + ext + f"（共 {total} 個檔）"]
        return out

    # ---- 修改 -------------------------------------------------------------------
    def _set(self, field: str, value: Any) -> None:
        if self._loading or self.nid is None:
            return
        nid = self.nid
        b = self.block()
        if b is None or getattr(b, field, None) == value:
            return

        def fn(s) -> None:
            node = s.graph.nodes.get(nid)
            if node is not None:
                setattr(node, field, value)
        self.doc.mutate(fn, origin=self)

    def _set_formats(self) -> None:
        if self._loading:
            return
        self._set("formats", [k for k, cb in (("netcdf", self.f_nc), ("hdf5", self.f_h5)) if cb.isChecked()])

    def _pick_dest(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "去背檔存到", self.dest.text() or "")
        if d:
            self.dest.setText(d)
            self._set("dest", d)

    # ---- 背景＋去背（1.0.6）--------------------------------------------------------
    def _build_bg_box(self) -> QtWidgets.QGroupBox:
        from PyQt6.QtCore import Qt

        self.bg_box = QtWidgets.QGroupBox("背景＋去背")
        self.bg_box.setObjectName("backgroundBox")
        g = QtWidgets.QFormLayout(self.bg_box)
        note = QtWidgets.QLabel("主量測完成後：勾選的電源設到背景電流（遠離共振）→ 用與主量測相同的設定量一次背景 → "
                                "存背景檔 → 主數據的每個檔除以背景，另存成「原檔名＋後綴」。")
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        g.addRow(note)
        self.bg_measure = QtWidgets.QLabel()
        self.bg_measure.setWordWrap(True)
        g.addRow("量測", self.bg_measure)
        self.bg_table = QtWidgets.QTableWidget(0, 3)
        self.bg_table.setObjectName("backgroundSources")
        self.bg_table.setHorizontalHeaderLabels(["電源（勾選要設定的）", "背景電流", "單位"])
        self.bg_table.verticalHeader().setVisible(False)
        hh = self.bg_table.horizontalHeader()
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.bg_table.setMinimumHeight(150)
        self.bg_table.itemChanged.connect(self._bg_item_changed)
        g.addRow(self.bg_table)
        self.bg_settle = QtWidgets.QDoubleSpinBox()
        self.bg_settle.setObjectName("backgroundSettle")
        self.bg_settle.setRange(0, 3600)
        self.bg_settle.setDecimals(2)
        self.bg_settle.setSuffix(" s")
        self.bg_settle.editingFinished.connect(lambda: self._set_bg("settle", float(self.bg_settle.value())))
        g.addRow("設定後等待", self.bg_settle)
        self.bg_file = QtWidgets.QLineEdit()
        self.bg_file.setObjectName("backgroundFileName")
        self.bg_file.editingFinished.connect(lambda: self._set_bg("file_name", self.bg_file.text().strip()))
        g.addRow("背景檔名", self.bg_file)
        row = QtWidgets.QHBoxLayout()
        self.bg_dest = QtWidgets.QLineEdit()
        self.bg_dest.setObjectName("backgroundDest")
        self.bg_dest.setPlaceholderText("（與數據同一個資料夾）")
        self.bg_dest.editingFinished.connect(lambda: self._set_bg("dest", self.bg_dest.text().strip()))
        pick = QtWidgets.QPushButton("📂")
        pick.clicked.connect(self._pick_bg_dest)
        row.addWidget(self.bg_dest, 1)
        row.addWidget(pick)
        g.addRow("去背存到", row)
        self.bg_suffix = QtWidgets.QLineEdit()
        self.bg_suffix.setObjectName("backgroundSuffix")
        self.bg_suffix.editingFinished.connect(
            lambda: self._set_bg("suffix", self.bg_suffix.text().strip() or "_debg"))
        g.addRow("檔名後綴", self.bg_suffix)
        self.bg_preview = QtWidgets.QLabel()
        self.bg_preview.setWordWrap(True)
        g.addRow(self.bg_preview)
        self.bg_box.setVisible(False)
        self._bg_rows: List[str] = []
        self._bg_spins: dict = {}
        self._Qt = Qt
        return self.bg_box

    def _bg_targets(self) -> List[tuple]:
        """[(ref, 名稱, 預設單位, 這台有沒有)]：這台的所有電源，加上方案裡有但這台沒有的。"""
        cands = bgmod.candidates(self.doc.catalog)
        rows = [(t.ref, t.label, t.unit, True) for t in cands]
        have = {t.ref for t in cands}
        for st in bgmod.config(self.doc.scheme)["sets"]:
            if st["target"] not in have:
                rows.append((st["target"], f"{st['target']}（這台沒有）", st.get("unit") or "mA", False))
        return rows

    def _show_background(self) -> None:
        Qt = self._Qt
        c = bgmod.config(self.doc.scheme)
        sets = {st["target"]: st for st in c["sets"]}
        rows = self._bg_targets()
        self._loading = True
        try:
            if [r[0] for r in rows] != self._bg_rows:
                self.bg_table.setRowCount(0)
                self._bg_spins = {}
                for i, (ref, label, unit, ok) in enumerate(rows):
                    self.bg_table.insertRow(i)
                    it = QtWidgets.QTableWidgetItem(label)
                    it.setData(Qt.ItemDataRole.UserRole, ref)
                    it.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                    if not ok:
                        it.setForeground(QtWidgets.QApplication.palette().placeholderText())
                    self.bg_table.setItem(i, 0, it)
                    sp = QtWidgets.QDoubleSpinBox()
                    sp.setRange(-1e6, 1e6)
                    sp.setDecimals(4)
                    sp.setKeyboardTracking(False)
                    sp.editingFinished.connect(lambda r=ref: self._bg_value_changed(r))
                    self.bg_table.setCellWidget(i, 1, sp)
                    self._bg_spins[ref] = sp
                    u = QtWidgets.QTableWidgetItem(unit)
                    u.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    self.bg_table.setItem(i, 2, u)
                self._bg_rows = [r[0] for r in rows]
                vh = self.bg_table.verticalHeader()
                shown = min(max(len(rows), 3), 8)               # 8 台以內不用捲
                self.bg_table.setMinimumHeight(self.bg_table.horizontalHeader().height() + 6 +
                                               shown * vh.defaultSectionSize())
            for i, (ref, _label, unit, _ok) in enumerate(rows):
                st = sets.get(ref)
                self.bg_table.item(i, 0).setCheckState(Qt.CheckState.Checked if st else Qt.CheckState.Unchecked)
                sp = self._bg_spins[ref]
                sp.blockSignals(True)
                sp.setValue(float(bgmod._value(st.get("value")) or 0.0) if st else sp.value())
                sp.blockSignals(False)
                sp.setEnabled(st is not None)
                self.bg_table.item(i, 2).setText((st or {}).get("unit") or unit)
            self.bg_settle.setValue(float(bgmod._value(c.get("settle")) or 0.0))
            stem = self._main_stem()
            self.bg_file.setPlaceholderText(f"{stem}_bg.nc")
            self.bg_file.setText(c.get("file_name") or "")
            self.bg_dest.setText(c.get("dest") or "")
            self.bg_suffix.setText(c.get("suffix") or "_debg")
            main, _stages, _debg = split_stages(self.doc.scheme)
            ms = bgmod._measures(main)
            self.bg_measure.setText("、".join(f"{m.instrument} {'/'.join(m.traces)}" for m in ms) +
                                    "（與主量測相同的設定）" if ms else "主流程還沒有量測")
            self.bg_preview.setText(f"會產生：{c.get('file_name') or stem + '_bg.nc'}、"
                                    f"每個主數據檔的 …{c.get('suffix') or '_debg'}.nc")
        finally:
            self._loading = False

    def _bg_sets_from_table(self) -> List[dict]:
        Qt = self._Qt
        out = []
        for i in range(self.bg_table.rowCount()):
            it = self.bg_table.item(i, 0)
            if it is None or it.checkState() != Qt.CheckState.Checked:
                continue
            ref = it.data(Qt.ItemDataRole.UserRole)
            out.append({"target": ref, "value": float(self._bg_spins[ref].value()),
                        "unit": self.bg_table.item(i, 2).text() or "mA"})
        return out

    def _bg_item_changed(self, item) -> None:
        if self._loading or item.column() != 0:
            return
        self._set_bg("sets", self._bg_sets_from_table())

    def _bg_value_changed(self, _ref: str) -> None:
        if self._loading:
            return
        self._set_bg("sets", self._bg_sets_from_table())

    def _set_bg(self, field: str, value: Any) -> None:
        if self._loading:
            return
        c = bgmod.config(self.doc.scheme)
        if c.get(field) == value:
            return

        def fn(s) -> None:
            cur = bgmod.config(s)
            cur[field] = value
            s.background = cur
        self.doc.mutate(fn, origin=self)

    def _pick_bg_dest(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "去背檔存到", self.bg_dest.text() or "")
        if d:
            self.bg_dest.setText(d)
            self._set_bg("dest", d)
