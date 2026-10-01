"""右下：檔案設置（對應 Labber 的 Log name / Project / User / Tags / Comment / Timing）。

分頁：
  檔案        檔名、資料夾、同名處理、格式、Labber tags 與註解、分檔結果、實際存檔路徑預覽
  執行        每點量測前等待、移動 / 停靠速率、錯誤處理、自動暫停
  檢查與估時   錯誤與警告（點一下跳到節點）、總點數、預估時間
沒填的欄位一律使用 settings.yaml 的預設值（欄位裡灰色字）。
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Callable

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt

from ....core.units import parse_quantity
from ....data.naming import day_folder, stale_date_in_filename
from ....scheme.compile import file_settings, format_duration
from ....settings import setting
from .. import theme
from .dialogs import dspin, ispin
from .doc import WorkbenchDoc

COLLISIONS = [("underscore", "建立新檔（加 _2、_3…）"), ("paren", "建立新檔（加 (2)、(3)…）"), ("overwrite", "覆寫")]


class FilePanel(QtWidgets.QTabWidget):
    def __init__(self, doc: WorkbenchDoc, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self._loading = False
        self.addTab(self._file_tab(), "檔案")
        self.addTab(self._run_tab(), "執行")
        self.addTab(self._check_tab(), "檢查與估時")
        doc.changed.connect(self._on_changed)
        theme.on_change(self, lambda w: w._refresh_derived(), call_now=False)
        self.load()

    # ---- 檔案 -----------------------------------------------------------
    def _file_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(w)
        outer.setContentsMargins(6, 6, 6, 6)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)
        self.fname = QtWidgets.QLineEdit()
        self.date_hint = QtWidgets.QPushButton()
        self.date_hint.setFlat(True)
        theme.on_change(self.date_hint, lambda b: b.setStyleSheet(f"color:{theme.c('warn')}; text-align:left;"))
        self.date_hint.setVisible(False)
        self.root = QtWidgets.QLineEdit()
        browse = QtWidgets.QToolButton()
        browse.setText("…")
        self.pattern = QtWidgets.QLineEdit()
        self.collision = QtWidgets.QComboBox()
        for k, v in COLLISIONS:
            self.collision.addItem(v, k)
        self.f_labber = QtWidgets.QCheckBox("Labber")
        self.f_hdf5 = QtWidgets.QCheckBox("HDF5（原生）")
        fmts = QtWidgets.QHBoxLayout()
        fmts.addWidget(self.f_labber)
        fmts.addWidget(self.f_hdf5)
        fmts.addStretch()
        self.project = QtWidgets.QLineEdit()
        self.user = QtWidgets.QLineEdit()
        self.tags = QtWidgets.QLineEdit()
        self.comment = QtWidgets.QPlainTextEdit()
        self.comment.setMaximumHeight(52)
        self.comment.setPlaceholderText("註解（寫進 Labber 檔；儀器設定會自動存，不用寫在這裡）")
        root_row = QtWidgets.QHBoxLayout()
        root_row.addWidget(self.root, 1)
        root_row.addWidget(browse)
        r = 0
        for label, widget in (("檔名", self.fname), ("", self.date_hint), ("資料根目錄", root_row),
                              ("資料夾規則", self.pattern), ("同名檔案", self.collision), ("格式", fmts)):
            grid.addWidget(QtWidgets.QLabel(label), r, 0)
            if isinstance(widget, QtWidgets.QLayout):
                grid.addLayout(widget, r, 1)
            else:
                grid.addWidget(widget, r, 1)
            r += 1
        lab = QtWidgets.QLabel("Labber")
        lab.setStyleSheet("font-weight:600; color:palette(text); margin-top:4px;")
        grid.addWidget(lab, r, 0)
        r += 1
        for label, widget in (("Project", self.project), ("User", self.user), ("Tags", self.tags),
                              ("註解", self.comment)):
            grid.addWidget(QtWidgets.QLabel(label), r, 0)
            grid.addWidget(widget, r, 1)
            r += 1
        grid.setColumnStretch(1, 1)
        outer.addLayout(grid)
        self.where = QtWidgets.QLabel()
        self.where.setWordWrap(True)
        self.where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.where.setStyleSheet(theme.tint("ok"))
        outer.addWidget(self.where)
        outer.addStretch()

        self.fname.editingFinished.connect(lambda: self._out("file_name", self.fname.text().strip()))
        self.date_hint.clicked.connect(self._fix_date)
        self.root.editingFinished.connect(lambda: self._out("root", self.root.text().strip()))
        browse.clicked.connect(self._browse)
        self.pattern.editingFinished.connect(lambda: self._out("folder_pattern", self.pattern.text().strip()))
        self.collision.currentIndexChanged.connect(lambda _i: self._out("collision", self.collision.currentData()))
        self.f_labber.toggled.connect(lambda _v: self._formats())
        self.f_hdf5.toggled.connect(lambda _v: self._formats())
        self.project.editingFinished.connect(lambda: self._out("project", self.project.text().strip()))
        self.user.editingFinished.connect(lambda: self._out("user", self.user.text().strip() or None))
        self.tags.editingFinished.connect(self._tags)
        self.comment.textChanged.connect(lambda: self._comment_timer.start(600))
        self._comment_timer = QtCore.QTimer(self)
        self._comment_timer.setSingleShot(True)
        self._comment_timer.timeout.connect(lambda: self._out("comment", self.comment.toPlainText().strip()))
        return w

    # ---- 執行 -----------------------------------------------------------
    def _run_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        unit = setting("editor.new_blocks.dc_set.unit", "mA")
        self.rate_unit = f"{unit}/s"
        self.delay = dspin(0, 1e6, 3, "s")
        self.approach = dspin(0, 1e6, 4, self.rate_unit)
        self.park_rate = dspin(0, 1e6, 4, self.rate_unit)
        self.on_error = QtWidgets.QComboBox()
        self.on_error.addItem("重試後自動暫停（等人處理）", "pause")
        self.on_error.addItem("重試後中斷量測", "stop")
        self.retries = ispin(0, 20)
        self.dip = QtWidgets.QCheckBox("吸收峰型態變化（1 ⇌ 2 個）自動暫停")
        f.addRow("每點量測前等待", self.delay)
        f.addRow("移到起點速率", self.approach)
        f.addRow("結束後停靠速率", self.park_rate)
        f.addRow("量測錯誤", self.on_error)
        f.addRow("重試次數", self.retries)
        f.addRow("自動判斷", self.dip)
        note = QtWidgets.QLabel("數值 0 = 使用儀器的斜坡速率。各軸「掃完後回到第一點 / 停在最後」在該掃描節點設定。"
                                "沒改過的欄位使用 settings.yaml 的 run_defaults。")
        note.setWordWrap(True)
        note.setStyleSheet("color:palette(placeholder-text); font-size:11px;")
        f.addRow(note)
        self.delay.valueChanged.connect(lambda v: self._run("point_delay", v or None))
        self.approach.valueChanged.connect(lambda v: self._run("approach_rate", f"{v:g} {self.rate_unit}" if v else None))
        self.park_rate.valueChanged.connect(lambda v: self._run("park_rate", f"{v:g} {self.rate_unit}" if v else None))
        self.on_error.currentIndexChanged.connect(lambda _i: self._run("on_error", self.on_error.currentData()))
        self.retries.valueChanged.connect(lambda v: self._run("retries", int(v)))
        self.dip.toggled.connect(self._dip)
        return w

    # ---- 檢查 -----------------------------------------------------------
    def _check_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        self.est = QtWidgets.QLabel()
        self.est.setWordWrap(True)
        v.addWidget(self.est)
        self.issue_list = QtWidgets.QListWidget()
        self.issue_list.itemClicked.connect(self._issue_clicked)
        v.addWidget(self.issue_list, 1)
        return w

    # ---- 寫回方案 ---------------------------------------------------------
    def _mutate(self, fn: Callable) -> None:
        if not self._loading:
            self.doc.mutate(fn, origin=self)

    def _out(self, key: str, value: Any) -> None:
        def fn(s):
            if value in (None, "") and key != "user":
                s.output.pop(key, None)
            elif value is None:
                s.output.pop(key, None)
            else:
                s.output[key] = value
        self._mutate(fn)

    def _formats(self) -> None:
        f = [k for k, cb in (("labber", self.f_labber), ("hdf5", self.f_hdf5)) if cb.isChecked()]
        self._out("formats", f)

    def _tags(self) -> None:
        text = self.tags.text().strip()
        self._out("tags", [t.strip() for t in text.split(",") if t.strip()] if text else None)

    def _run(self, key: str, value: Any) -> None:
        def fn(s):
            if value is None:
                s.run.pop(key, None)
            else:
                s.run[key] = value
        self._mutate(fn)

    def _dip(self, on: bool) -> None:
        def fn(s):
            s.hooks = [h for h in s.hooks if h.get("type") != "dip_shape_pause"]
            if on:
                s.hooks.append({"type": "dip_shape_pause", "enabled": True})
        self._mutate(fn)

    def _browse(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "資料根目錄", self.root.text() or
                                                       str(setting("data.root", "")))
        if d:
            self.root.setText(d)
            self._out("root", d)

    def _fix_date(self) -> None:
        w = stale_date_in_filename(self.fname.text())
        if w:
            self.fname.setText(w[2])
            self._out("file_name", w[2])

    # ---- 讀取 -----------------------------------------------------------
    def _on_changed(self, origin: object) -> None:
        if origin is self:
            self._refresh_derived()
        else:
            self.load()

    def load(self) -> None:
        self._loading = True
        try:
            s = self.doc.scheme
            o = s.output
            fs = file_settings(s, self.doc.result.save)
            self.fname.setText(o.get("file_name", ""))
            self.fname.setPlaceholderText(fs["file_name"])
            self.root.setText(o.get("root", ""))
            self.root.setPlaceholderText(str(setting("data.root", "")))
            self.pattern.setText(o.get("folder_pattern", ""))
            self.pattern.setPlaceholderText(str(setting("data.folder_pattern", "{yyyy}/{mm}/Data_{mmdd}")))
            self.collision.setCurrentIndex(max(0, self.collision.findData(fs["collision"])))
            self.f_labber.setChecked("labber" in fs["formats"])
            self.f_hdf5.setChecked("hdf5" in fs["formats"])
            self.project.setText(o.get("project", ""))
            self.project.setPlaceholderText(f"{setting('labber.project', 'auto')}（auto = MM/MMDD）")
            self.user.setText(o.get("user") or "")
            self.user.setPlaceholderText(str(setting("labber.user", "")))
            self.tags.setText(", ".join(o["tags"]) if o.get("tags") is not None else "")
            self.tags.setPlaceholderText(", ".join(fs["tags"]) + "（逗號分隔）")
            if self.comment.toPlainText().strip() != o.get("comment", ""):
                self.comment.setPlainText(o.get("comment", ""))
            run = {**(setting("run_defaults", {}) or {}), **s.run}
            self.delay.setValue(_num(run.get("point_delay"), "s"))
            self.approach.setValue(_num(s.run.get("approach_rate"), self.rate_unit))
            self.park_rate.setValue(_num(s.run.get("park_rate"), self.rate_unit))
            self.approach.setSpecialValueText(f"預設（{run.get('approach_rate') or '儀器速率'}）")
            self.park_rate.setSpecialValueText(f"預設（{run.get('park_rate') or '儀器速率'}）")
            self.on_error.setCurrentIndex(max(0, self.on_error.findData(run.get("on_error", "pause"))))
            self.retries.setValue(int(run.get("retries", 1)))
            self.dip.setChecked(any(h.get("type") == "dip_shape_pause" and h.get("enabled", True) for h in s.hooks))
        finally:
            self._loading = False
        self._refresh_derived()

    def _refresh_derived(self) -> None:
        s, r = self.doc.scheme, self.doc.result
        fs = file_settings(s, r.save)
        now = _dt.datetime.now()
        w = stale_date_in_filename(fs["file_name"], now)
        self.date_hint.setVisible(bool(w))
        if w:
            self.date_hint.setText(f"⚠ 檔名日期 {w[0]} 不是今天 → 按這裡改成「{w[2]}」")
        root = s.output.get("root") or setting("data.root", "") or "./data"
        pattern = s.output.get("folder_pattern") or setting("data.folder_pattern", "{yyyy}/{mm}/Data_{mmdd}")
        try:
            folder = day_folder(root, pattern, now)
        except Exception as e:  # noqa: BLE001
            folder = Path(f"（資料夾規則錯誤：{e}）")
        if not fs["formats"]:
            split = "沒有選格式：只寫 _raw/ 原始檔"
        elif r.split_depth:
            names = "、".join(lp.axis_name for lp in r.loops[:r.split_depth])
            ex = setting("data.split_pattern", "{stem}_{index:03d}_{label}")
            split = f"每個 <b>{names}</b> 值存一個檔，共 <b>{r.n_files}</b> 個（檔名規則 {ex}）"
        else:
            split = "整個量測存成 <b>一個檔</b>" + ("（多維 Labber 檔）" if len(r.loops) > 1 else "")
        for e in getattr(r, "extras", []) or []:
            names = "、".join(lp.axis_name for lp in r.loops[:e["depth"]])
            split += (f"<br>＋ 額外 Data：<b>{e['file_name']}</b>（{'+'.join(e['formats']) or '未選格式'}），"
                      + (f"每個 {names} 值一個檔" if e["depth"] else "整個量測一個檔"))
        self.where.setText(f"<b>存到</b> {folder}<br><b>檔名</b> {fs['file_name']}"
                           f"（{dict(COLLISIONS).get(fs['collision'], fs['collision'])}）<br>{split}<br>"
                           + theme.span("原始資料另存 _raw/（當機不遺失）", "muted"))
        # 檢查
        self.issue_list.clear()
        for i in r.issues:
            icon = {"error": "✖", "warning": "⚠", "info": "ℹ"}[i.level]
            it = QtWidgets.QListWidgetItem(f"{icon} {i.message}")
            it.setForeground(QtGui.QBrush(theme.qc({"error": "err", "warning": "warn", "info": "accent"}[i.level])))
            it.setData(Qt.ItemDataRole.UserRole, i.block_id)
            self.issue_list.addItem(it)
        parts = "、".join(f"{k} {format_duration(v)}" for k, v in r.est_breakdown.items() if v)
        err = sum(1 for i in r.issues if i.level == "error")
        self.est.setText(f"{theme.span(f'✖ 有 {err} 個錯誤', 'err') if err else theme.span('✔ 可以執行', 'ok')}"
                         f"　·　<b>{r.total_points:,}</b> 點　·　預估 <b>{format_duration(r.est_seconds)}</b>"
                         + (f"<br>{theme.span(parts, 'muted')}" if parts else ""))
        self.setTabText(2, f"檢查與估時{f'（{err}）' if err else ''}")

    def _issue_clicked(self, it: QtWidgets.QListWidgetItem) -> None:
        bid = it.data(Qt.ItemDataRole.UserRole)
        if bid:
            self.doc.select(bid)


def _num(v: Any, unit: str) -> float:
    if v in (None, ""):
        return 0.0
    try:
        from ....core.units import split_unit

        return float(parse_quantity(v, unit)) / (split_unit(unit)[1] or 1.0)
    except Exception:  # noqa: BLE001
        return 0.0
