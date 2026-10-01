"""節點設定視窗（雙擊節點開啟），仿 Labber Measurement Editor 的 Step setup。

    edit_block(parent, doc, block) → 修改後的 Block 複本；取消回傳 None
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Optional

import numpy as np
from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt

from ....core.units import parse_quantity, split_unit
from ....scheme import Block
from ....scheme.compile import default_axis_name
from ....settings import setting
from .. import theme
from .doc import WorkbenchDoc


def unit_choices(base: str, current: str) -> list:
    ch = list((setting("editor.units.choices", {}) or {}).get(base) or [])
    if current and current not in ch:
        ch.insert(0, current)
    return ch or [current or ""]


def decimals(unit: str) -> int:
    return int((setting("editor.units.decimals", {}) or {}).get(unit, 6))


def dspin(lo: float = -1e12, hi: float = 1e12, dec: int = 6, suffix: str = "") -> QtWidgets.QDoubleSpinBox:
    w = QtWidgets.QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(dec)
    w.setKeyboardTracking(False)
    if suffix:
        w.setSuffix(f" {suffix}")
    w.setMinimumWidth(130)
    return w


def ispin(lo: int = 0, hi: int = 10_000_000) -> QtWidgets.QSpinBox:
    w = QtWidgets.QSpinBox()
    w.setRange(lo, hi)
    w.setKeyboardTracking(False)
    w.setMinimumWidth(100)
    return w


def edit_block(parent: QtWidgets.QWidget, doc: WorkbenchDoc, b: Block) -> Optional[Block]:
    if b.kind == "set":
        dlg: QtWidgets.QDialog = StepDialog(parent, doc, b)
    elif b.kind == "measure":
        dlg = MeasureDialog(parent, doc, b)
    elif b.kind == "wait":
        dlg = WaitDialog(parent, b)
    else:
        saves = getattr(doc.result, "saves", []) or []
        main = not saves or saves[0].id == b.id
        dlg = SaveDialog(parent, b, main)
    if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
        return dlg.result_block()  # type: ignore[attr-defined]
    return None


# ---------------------------------------------------------------------------
class SaveDialog(QtWidgets.QDialog):
    """Data 節點：放的位置決定分檔；可以有多個 Data（各自的檔名 / 格式）。第一個 Data 的檔名在右下「檔案設置」。"""

    def __init__(self, parent, b: Block, main: bool) -> None:
        super().__init__(parent)
        from .files_panel import COLLISIONS

        self.b, self.main = copy.deepcopy(b), main
        self.setWindowTitle("Data 節點（主要）" if main else "Data 節點（額外輸出）")
        self.resize(520, 300)
        v = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(
            "Data 節點決定「分檔」：接在某個迴圈的「每一點 ⟲」裡 → 那個迴圈每走一個值存一個檔；"
            "接在所有迴圈之後 → 整個量測一個檔。\n可以放多個 Data：例如外圈每個值一個檔，另外再存一個完整的檔。"
            + ("\n\n這是第一個（主要）Data：檔名、資料夾、格式、Labber tags 在右下「檔案設置」。" if main else ""))
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        v.addWidget(note)
        form = QtWidgets.QFormLayout()
        self.file_name = QtWidgets.QLineEdit(b.file_name)
        self.file_name.setPlaceholderText("（方案名稱_2.hdf5）")
        self.f_labber = QtWidgets.QCheckBox("Labber")
        self.f_hdf5 = QtWidgets.QCheckBox("HDF5（原生）")
        self.f_labber.setChecked("labber" in b.formats)
        self.f_hdf5.setChecked("hdf5" in b.formats)
        fm = QtWidgets.QHBoxLayout()
        fm.addWidget(self.f_labber)
        fm.addWidget(self.f_hdf5)
        fm.addStretch(1)
        self.collision = QtWidgets.QComboBox()
        for k, label in COLLISIONS:
            self.collision.addItem(label, k)
        self.collision.setCurrentIndex(max(0, self.collision.findData(b.collision)))
        form.addRow("檔名", self.file_name)
        form.addRow("格式", fm)
        form.addRow("同名檔案", self.collision)
        box = QtWidgets.QWidget()
        box.setLayout(form)
        box.setEnabled(not main)
        v.addWidget(box)
        v.addStretch(1)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                        | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def result_block(self) -> Block:
        b = self.b
        if not self.main:
            b.file_name = self.file_name.text().strip()
            b.formats = [k for k, cb in (("labber", self.f_labber), ("hdf5", self.f_hdf5)) if cb.isChecked()]
            b.collision = self.collision.currentData()
        return b


class StepDialog(QtWidgets.QDialog):
    """Labber 的 Step setup：單一值 / 範圍（起訖或中心寬度）、步進或點數、線性 / 對數、
    每步等待、來回掃、掃完後的動作，右側預覽所有 step 值。"""

    def __init__(self, parent, doc: WorkbenchDoc, b: Block) -> None:
        super().__init__(parent)
        self.doc, self.b = doc, copy.deepcopy(b)
        self.t = doc.catalog.target(b.target)
        self.setWindowTitle(f"Step 設定 — {self.t.label if self.t else b.target}")
        self.resize(820, 460)
        self._loading = True

        lay = QtWidgets.QHBoxLayout(self)
        left = QtWidgets.QVBoxLayout()
        lay.addLayout(left, 0)

        # 通道
        g0 = QtWidgets.QGroupBox("通道")
        f0 = QtWidgets.QFormLayout(g0)
        self.target = QtWidgets.QComboBox()
        for t in doc.catalog.targets:
            self.target.addItem(t.label, t.ref)
        self.target.setCurrentIndex(max(0, self.target.findData(b.target)))
        self.axis_name = QtWidgets.QLineEdit(b.axis_name)
        self.axis_name.setPlaceholderText(default_axis_name(self.t) if self.t else "")
        self.unit = QtWidgets.QComboBox()
        f0.addRow("參數", self.target)
        f0.addRow("資料軸名稱", self.axis_name)
        f0.addRow("單位", self.unit)
        left.addWidget(g0)

        # 值
        g1 = QtWidgets.QGroupBox("Step 值")
        v1 = QtWidgets.QVBoxLayout(g1)
        mode_row = QtWidgets.QHBoxLayout()
        self.r_single = QtWidgets.QRadioButton("單一值（固定，開始前設定一次）")
        self.r_range = QtWidgets.QRadioButton("範圍（掃描迴圈）")
        grp = QtWidgets.QButtonGroup(self)
        grp.addButton(self.r_single)
        grp.addButton(self.r_range)
        mode_row.addWidget(self.r_single)
        mode_row.addWidget(self.r_range)
        v1.addLayout(mode_row)
        self.single_box = QtWidgets.QWidget()
        fs = QtWidgets.QFormLayout(self.single_box)
        fs.setContentsMargins(0, 0, 0, 0)
        self.value = dspin()
        fs.addRow("值", self.value)
        v1.addWidget(self.single_box)

        self.range_box = QtWidgets.QWidget()
        fr = QtWidgets.QGridLayout(self.range_box)
        fr.setContentsMargins(0, 0, 0, 0)
        self.r_ss = QtWidgets.QRadioButton("起點 / 終點")
        self.r_cs = QtWidgets.QRadioButton("中心 / 寬度")
        g_rm = QtWidgets.QButtonGroup(self)
        g_rm.addButton(self.r_ss)
        g_rm.addButton(self.r_cs)
        self.lbl_a, self.lbl_b = QtWidgets.QLabel("起點"), QtWidgets.QLabel("終點")
        self.a, self.bb = dspin(), dspin()
        self.r_step = QtWidgets.QRadioButton("步進")
        self.r_pts = QtWidgets.QRadioButton("點數")
        g_sp = QtWidgets.QButtonGroup(self)
        g_sp.addButton(self.r_step)
        g_sp.addButton(self.r_pts)
        self.step = dspin(0, 1e12)
        self.points = ispin(1, 10_000_000)
        self.interp = QtWidgets.QComboBox()
        self.interp.addItem("線性", "linear")
        self.interp.addItem("對數", "log")
        fr.addWidget(self.r_ss, 0, 0)
        fr.addWidget(self.r_cs, 0, 1)
        fr.addWidget(self.lbl_a, 1, 0)
        fr.addWidget(self.a, 1, 1)
        fr.addWidget(self.lbl_b, 2, 0)
        fr.addWidget(self.bb, 2, 1)
        fr.addWidget(self.r_step, 3, 0)
        fr.addWidget(self.step, 3, 1)
        fr.addWidget(self.r_pts, 4, 0)
        fr.addWidget(self.points, 4, 1)
        fr.addWidget(QtWidgets.QLabel("間隔"), 5, 0)
        fr.addWidget(self.interp, 5, 1)
        v1.addWidget(self.range_box)
        left.addWidget(g1)

        # 進階
        g2 = QtWidgets.QGroupBox("進階（Labber Advanced）")
        f2 = QtWidgets.QFormLayout(g2)
        self.settle = dspin(0, 1e6, 3, "s")
        self.alternate = QtWidgets.QCheckBox("來回掃（外圈每走一步，這一軸反向；省去回起點的時間）")
        self.after = QtWidgets.QComboBox()
        self.after.addItem("回到第一點", "start")
        self.after.addItem("停在最後一點", "stay")
        self.after.addItem("到指定值", "value")
        self.after_value = dspin()
        after_row = QtWidgets.QHBoxLayout()
        after_row.addWidget(self.after)
        after_row.addWidget(self.after_value)
        self.interleave = QtWidgets.QCheckBox("電流異步（兩台交錯：每一點只有一台前進「步進」，平均走一半）")
        self.interleave.setToolTip("與舊 sweep_main 的 DC 異步量測相同：A 先 +步進 → 量測 → B +步進 → 量測 …\n"
                                   "只適用於兩台交錯的電磁鐵組；步進 = 每台每次前進的電流")
        f2.addRow("每一步後等待", self.settle)
        f2.addRow("", self.interleave)
        f2.addRow("", self.alternate)
        f2.addRow("掃完後", after_row)
        left.addWidget(g2)
        left.addStretch()

        # 預覽
        right = QtWidgets.QVBoxLayout()
        lay.addLayout(right, 1)
        import pyqtgraph as pg

        self.plot = pg.PlotWidget()
        self.plot.showGrid(True, True, 0.3)
        self.plot.setLabel("left", "Step #")
        self.curve = self.plot.plot(pen=None, symbol="o", symbolSize=5, symbolBrush=theme.c("curve"), symbolPen=None)
        theme.style_plot(self.plot, (self.curve, "curve"))
        right.addWidget(self.plot, 1)
        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        right.addWidget(self.info)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                        | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        right.addWidget(bb)

        self._load()
        for w in (self.r_single, self.r_range, self.r_ss, self.r_cs, self.r_step, self.r_pts, self.alternate,
                  self.interleave):
            w.toggled.connect(self._changed)
        for w in (self.value, self.a, self.bb, self.step, self.settle, self.after_value):
            w.valueChanged.connect(self._changed)
        self.points.valueChanged.connect(self._changed)
        self.interp.currentIndexChanged.connect(self._changed)
        self.after.currentIndexChanged.connect(self._changed)
        self.unit.currentTextChanged.connect(self._on_unit)
        self.target.currentIndexChanged.connect(self._on_target)
        self._loading = False
        self._changed()

    # ---- 載入 / 同步 --------------------------------------------------------
    def _set_unit_widgets(self) -> None:
        u = self.b.unit
        dec = decimals(u)
        for w in (self.value, self.a, self.bb, self.step, self.after_value):
            w.setDecimals(dec)
            w.setSuffix(f" {u}" if u else "")
        self.plot.setLabel("bottom", self.t.short if self.t else "", u)

    def _load(self) -> None:
        b, t = self.b, self.t
        base = split_unit(b.unit or (t.unit if t else ""))[0]
        self.unit.blockSignals(True)
        self.unit.clear()
        self.unit.addItems(unit_choices(base, b.unit or ""))
        self.unit.setCurrentText(b.unit)
        self.unit.blockSignals(False)
        self._set_unit_widgets()
        self.r_range.setChecked(b.is_loop)
        self.r_single.setChecked(not b.is_loop)
        self.value.setValue(b.value if b.value is not None else (b.start or 0.0))
        cs = b.range_mode == "centerspan"
        self.r_cs.setChecked(cs)
        self.r_ss.setChecked(not cs)
        start = b.start if b.start is not None else (b.value or 0.0)
        stop = b.stop if b.stop is not None else start
        if cs:
            self.a.setValue((start + stop) / 2)
            self.bb.setValue(abs(stop - start))
        else:
            self.a.setValue(start)
            self.bb.setValue(stop)
        self.r_pts.setChecked(bool(b.points))
        self.r_step.setChecked(not b.points)
        self.step.setValue(b.step or 1.0)
        self.points.setValue(b.points or b.sweep_values_count() or 11)
        self.interp.setCurrentIndex(1 if b.interp == "log" else 0)
        self.settle.setValue(b.settle or 0.0)
        self.alternate.setChecked(b.alternate)
        self.interleave.setChecked(bool(b.interleave))
        self.after.setCurrentIndex(max(0, self.after.findData(b.after)))
        self.after_value.setValue(b.after_value or 0.0)

    def _start_stop(self):
        if self.r_cs.isChecked():
            c, span = self.a.value(), self.bb.value()
            return c - span / 2, c + span / 2
        return self.a.value(), self.bb.value()

    def _on_unit(self, new: str) -> None:
        if self._loading or not new or new == self.b.unit:
            return
        bo, fo = split_unit(self.b.unit)
        bn, fn = split_unit(new)
        k = fo / fn if bo == bn and fn else 1.0
        self._loading = True
        for w in (self.value, self.a, self.bb, self.step, self.after_value):
            w.setValue(w.value() * k)
        self.b.unit = new
        self._set_unit_widgets()
        self._loading = False
        self._changed()

    def _on_target(self) -> None:
        ref = self.target.currentData()
        t = self.doc.catalog.target(ref)
        if t is None or ref == self.b.target:
            return
        self.b.target, self.t = ref, t
        if split_unit(self.b.unit)[0] != split_unit(t.unit)[0]:
            self.b.unit = t.unit
        self.axis_name.setPlaceholderText(default_axis_name(t))
        self._loading = True
        self._load()
        self._loading = False
        self._changed()

    def _values(self) -> Optional[np.ndarray]:
        from ....measure.sweep import Axis

        a, z = self._start_stop()
        d: Dict[str, Any] = {"target": "x", "unit": "", "start": a, "stop": z}
        if self.r_pts.isChecked():
            d["num"] = self.points.value()
            if self.interp.currentData() == "log":
                d["interp"] = "log"
        else:
            if self.step.value() == 0:
                return None
            d["step"] = self.step.value() / (2 if self._async() else 1)
        try:
            return Axis.from_config(d).values
        except Exception:  # noqa: BLE001
            return None

    def _async(self) -> bool:
        return bool(self.t and self.t.interleave and self.r_range.isChecked() and self.interleave.isChecked())

    def _changed(self) -> None:
        if self._loading:
            return
        rng = self.r_range.isChecked()
        can = bool(self.t and self.t.interleave)
        self.interleave.setVisible(can)
        self.interleave.setEnabled(rng)
        if self._async() and self.r_pts.isChecked():
            self.r_step.setChecked(True)          # 異步用步進（每台每次前進的電流）
        self.r_step.setText("每台步進" if self._async() else "步進")
        self.r_pts.setEnabled(not self._async())
        self.single_box.setVisible(not rng)
        self.range_box.setVisible(rng)
        cs = self.r_cs.isChecked()
        self.lbl_a.setText("中心" if cs else "起點")
        self.lbl_b.setText("寬度" if cs else "終點")
        self.step.setEnabled(self.r_step.isChecked())
        self.points.setEnabled(self.r_pts.isChecked())
        self.interp.setEnabled(self.r_pts.isChecked())
        self.after_value.setVisible(self.after.currentData() == "value")
        for w in (self.alternate, self.after, self.after_value):
            w.setEnabled(rng)
        vals = self._values() if rng else np.array([self.value.value()])
        msgs = []
        ok = True
        if vals is None or not len(vals):
            self.curve.setData([], [])
            msgs.append(theme.span("範圍無效（對數間隔需起訖同號且不為 0；步進不可為 0）", "err"))
            ok = False
        else:
            self.curve.setData(vals, np.arange(len(vals)))
            msgs.append(f"<b>{len(vals):,}</b> 點" + (f"：{vals[0]:.6g} → {vals[-1]:.6g} {self.b.unit}"
                                                      if len(vals) > 1 else ""))
            if self._async():
                msgs.append(f"電流異步：平均每點 {self.step.value() / 2:.6g} {self.b.unit}，"
                            f"兩台輪流各走 {self.step.value():.6g} {self.b.unit}")
            if self.t and self.t.limits:
                scale = split_unit(self.b.unit)[1] or 1.0
                lo, hi = self.t.limits[0] / scale, self.t.limits[1] / scale
                inside = vals.min() >= lo - 1e-12 and vals.max() <= hi + 1e-12
                msgs.append(("安全上下限" if inside else theme.span("超出安全上下限", "err"))
                            + f"：[{lo:.6g}, {hi:.6g}] {self.b.unit}")
            if self.t and self.t.ramp_rate and rng and len(vals) > 1:
                scale = split_unit(self.b.unit)[1] or 1.0
                msgs.append(f"斜坡速率 {self.t.ramp_rate / scale:.6g} {self.b.unit}/s"
                            + (f"，單次跳動上限 {self.t.max_jump / scale:.6g} {self.b.unit}" if self.t.max_jump else ""))
        self.info.setText("<br>".join(msgs))
        self._ok = ok

    def _accept(self) -> None:
        if self.r_range.isChecked() and not getattr(self, "_ok", True):
            return
        self.accept()

    def result_block(self) -> Block:
        b = self.b
        b.axis_name = self.axis_name.text().strip()
        b.settle = self.settle.value()
        if self.r_range.isChecked():
            b.mode = "sweep"
            b.start, b.stop = (round(v, 12) for v in self._start_stop())
            b.range_mode = "centerspan" if self.r_cs.isChecked() else "startstop"
            if self.r_pts.isChecked():
                b.points, b.step = self.points.value(), None
                b.interp = self.interp.currentData()
            else:
                b.points, b.step, b.interp = None, self.step.value(), "linear"
            b.alternate = self.alternate.isChecked()
            b.interleave = self._async()
            b.after = self.after.currentData()
            b.after_value = self.after_value.value() if b.after == "value" else None
        else:
            b.mode = "fixed"
            b.value = self.value.value()
            b.interleave = False
        return b


# ---------------------------------------------------------------------------
class MeasureDialog(QtWidgets.QDialog):
    """量測節點（Labber 的 Log channels + 儀器設定）。"""

    def __init__(self, parent, doc: WorkbenchDoc, b: Block) -> None:
        super().__init__(parent)
        self.doc, self.b = doc, copy.deepcopy(b)
        self.setWindowTitle("量測設定")
        self.resize(460, 520)
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.inst = QtWidgets.QComboBox()
        for m in doc.catalog.measurers:
            self.inst.addItem(f"{m.head} · {m.label}", m.ref)
        self.inst.setCurrentIndex(max(0, self.inst.findData(b.instrument)))
        form.addRow("儀器", self.inst)
        self.traces = QtWidgets.QListWidget()
        self.traces.setMaximumHeight(90)
        form.addRow("量測通道（Log channels）", self.traces)
        lay.addLayout(form)
        self.box = QtWidgets.QGroupBox("儀器設定（開始量測時寫入儀器；會存進資料檔）")
        self.sform = QtWidgets.QFormLayout(self.box)
        lay.addWidget(self.box, 1)
        self.est = QtWidgets.QLabel()
        self.est.setStyleSheet("color:palette(text)")
        lay.addWidget(self.est)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                        | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.inst.currentIndexChanged.connect(self._on_inst)
        self._fields: Dict[str, Any] = {}
        self._build()

    def _on_inst(self) -> None:
        ref = self.inst.currentData()
        if ref != self.b.instrument:
            m = self.doc.catalog.measurer(ref)
            self.b.instrument = ref
            self.b.traces = [m.traces[0]] if m and m.traces else []
            self.b.settings = dict(m.defaults) if m else {}
            self._build()

    def _build(self) -> None:
        b = self.b
        m = self.doc.catalog.measurer(b.instrument)
        self.traces.clear()
        for tr in (m.traces if m else []):
            it = QtWidgets.QListWidgetItem(tr)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if tr in b.traces else Qt.CheckState.Unchecked)
            self.traces.addItem(it)
        while self.sform.rowCount():
            self.sform.removeRow(0)
        self._fields = {}
        for f in (m.fields if m else []):
            raw = (b.settings or {}).get(f.key)
            if f.kind == "bool":
                w = QtWidgets.QCheckBox()
                w.setChecked(str(raw).strip().upper() in ("1", "ON", "TRUE", "YES") if raw is not None else False)
            elif f.kind == "enum" and f.choices:
                w = QtWidgets.QComboBox()
                w.addItems([str(c) for c in f.choices])
                w.setCurrentText(str(raw) if raw is not None else "")
            elif f.kind == "str":
                w = QtWidgets.QLineEdit("" if raw is None else str(raw))
            elif f.kind == "int" or not f.unit:
                w = ispin(-10_000_000, 10_000_000)
                try:
                    w.setValue(int(float(raw)) if raw is not None else 0)
                except (TypeError, ValueError):
                    w.setValue(0)
            else:
                w = dspin(-1e12, 1e12, decimals(f.unit), f.unit)
                try:
                    w.setValue(parse_quantity(raw, f.unit) / split_unit(f.unit)[1] if raw is not None else 0.0)
                except Exception:  # noqa: BLE001
                    w.setValue(0.0)
            self._fields[f.key] = (f, w)
            self.sform.addRow(f.label, w)

    def result_block(self) -> Block:
        b = self.b
        b.traces = [self.traces.item(i).text() for i in range(self.traces.count())
                    if self.traces.item(i).checkState() == Qt.CheckState.Checked]
        s = dict(b.settings or {})
        for key, (f, w) in self._fields.items():
            if isinstance(w, QtWidgets.QCheckBox):
                s[key] = w.isChecked()
            elif isinstance(w, QtWidgets.QComboBox):
                s[key] = w.currentText()
            elif isinstance(w, QtWidgets.QLineEdit):
                s[key] = w.text()
            elif isinstance(w, QtWidgets.QSpinBox):
                s[key] = int(w.value())
            else:
                s[key] = f"{w.value():.9g} {f.unit}"
        b.settings = s
        return b


class WaitDialog(QtWidgets.QDialog):
    def __init__(self, parent, b: Block) -> None:
        super().__init__(parent)
        self.b = copy.deepcopy(b)
        self.setWindowTitle("等待")
        lay = QtWidgets.QFormLayout(self)
        self.sec = dspin(0, 1e6, 3, "s")
        self.sec.setValue(b.seconds or 0.0)
        lay.addRow("等待時間", self.sec)
        lay.addRow(QtWidgets.QLabel("在迴圈內：每一點都等待；在最外層：開始量測前等待一次。"))
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                        | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)

    def result_block(self) -> Block:
        self.b.seconds = self.sec.value()
        return self.b
