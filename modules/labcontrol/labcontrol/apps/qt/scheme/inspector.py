"""右側屬性面板：依選取方塊種類顯示對應表單；沒選取時顯示整個方案的執行設定。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt

from ....core.units import parse_quantity, split_unit
from ....scheme import Block
from ....settings import setting
from ....scheme.compile import _measure_time, default_axis_name
from .doc import SchemeDoc

def unit_choices(base: str, current: str) -> list:
    """單位下拉選單：settings.yaml editor.units.choices。"""
    ch = list((setting("editor.units.choices", {}) or {}).get(base) or [])
    if current and current not in ch:
        ch.insert(0, current)
    return ch or [current or ""]


def decimals(unit: str) -> int:
    return int((setting("editor.units.decimals", {}) or {}).get(unit, 6))


def spin(lo: float = -1e9, hi: float = 1e9, dec: int = 4, suffix: str = "", step: float = 1.0) -> QtWidgets.QDoubleSpinBox:
    w = QtWidgets.QDoubleSpinBox()
    w.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
    w.setMinimumWidth(70)
    w.setRange(lo, hi)
    w.setDecimals(dec)
    w.setSingleStep(step)
    w.setKeyboardTracking(False)
    w.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
    if suffix:
        w.setSuffix(f" {suffix}")
    return w


def ispin(lo: int = 0, hi: int = 10_000_000) -> QtWidgets.QSpinBox:
    w = QtWidgets.QSpinBox()
    w.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
    w.setMinimumWidth(60)
    w.setRange(lo, hi)
    w.setKeyboardTracking(False)
    return w


class _Page(QtWidgets.QWidget):
    def __init__(self, doc: SchemeDoc) -> None:
        super().__init__()
        self.doc = doc
        self.block: Optional[Block] = None
        self._loading = False
        self.form = QtWidgets.QFormLayout(self)
        self.form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.issues = QtWidgets.QLabel()
        self.issues.setWordWrap(True)
        self.issues.setTextFormat(Qt.TextFormat.RichText)

    def set(self, field: str, value: Any) -> None:
        if not self._loading and self.block is not None:
            self.doc.set_field(self.block.id, field, value, origin=self)

    def refresh_issues(self) -> None:
        if self.block is None:
            self.issues.setText("")
            return
        rows = []
        for i in self.doc.result.issues_for(self.block.id):
            c = {"error": "#e03131", "warning": "#e67700", "info": "#1971c2"}[i.level]
            icon = {"error": "✖", "warning": "⚠", "info": "ℹ"}[i.level]
            rows.append(f"<div style='color:{c}'>{icon} {i.message}</div>")
        self.issues.setText("".join(rows))

    def load(self, b: Optional[Block]) -> None:
        self.block = b
        self._loading = True
        try:
            self._load(b)
        finally:
            self._loading = False
        self.refresh_issues()

    def _load(self, b: Optional[Block]) -> None:  # 子類別實作
        pass


# ---------------------------------------------------------------------------
class SetPage(_Page):
    def __init__(self, doc: SchemeDoc) -> None:
        super().__init__(doc)
        self.target = QtWidgets.QComboBox()
        for grp, label in (("magnet", "電磁鐵組"), ("source", "單台電源"), ("param", "儀器參數")):
            items = [t for t in doc.catalog.targets if t.group == grp]
            if items:
                self.target.addItem(f"── {label} ──")
                self.target.model().item(self.target.count() - 1).setEnabled(False)
                for t in items:
                    self.target.addItem(t.label, t.ref)
        self.mode_fixed = QtWidgets.QRadioButton("固定值")
        self.mode_sweep = QtWidgets.QRadioButton("掃描（迴圈）")
        mode = QtWidgets.QHBoxLayout()
        mode.addWidget(self.mode_fixed)
        mode.addWidget(self.mode_sweep)
        self._g_mode = QtWidgets.QButtonGroup(self)      # 兩組 radio 各自互斥
        self._g_mode.addButton(self.mode_fixed)
        self._g_mode.addButton(self.mode_sweep)
        mode.addStretch()
        self.unit = QtWidgets.QComboBox()
        self.unit.setEditable(True)
        self.value = spin()
        self.start = spin()
        self.stop = spin()
        self.by_step = QtWidgets.QRadioButton("步進")
        self.by_points = QtWidgets.QRadioButton("點數")
        self.step = spin(0, 1e12)
        self.points = ispin(1)
        self._g_by = QtWidgets.QButtonGroup(self)
        self._g_by.addButton(self.by_step)
        self._g_by.addButton(self.by_points)
        sp = QtWidgets.QHBoxLayout()
        sp.addWidget(self.by_step)
        sp.addWidget(self.step, 1)
        sp.addWidget(self.by_points)
        sp.addWidget(self.points, 1)
        self.settle = spin(0, 3600, 3, "s", 0.1)
        self.axis_name = QtWidgets.QLineEdit()
        self.count = QtWidgets.QLabel()
        self.count.setStyleSheet("color:#495057")

        self.form.addRow("目標", self.target)
        self.form.addRow("模式", mode)
        self.form.addRow("單位", self.unit)
        self.form.addRow("設定值", self.value)
        self.form.addRow("起點", self.start)
        self.form.addRow("終點", self.stop)
        self.form.addRow("間隔", sp)
        self.form.addRow("每點等待", self.settle)
        self.form.addRow("資料軸名稱", self.axis_name)
        self.form.addRow("", self.count)
        self.form.addRow(self.issues)

        self.target.currentIndexChanged.connect(self._on_target)
        self.mode_fixed.toggled.connect(lambda on: on and self._mode("fixed"))
        self.mode_sweep.toggled.connect(lambda on: on and self._mode("sweep"))
        self.unit.currentTextChanged.connect(self._on_unit)
        self.value.valueChanged.connect(lambda v: self.set("value", v))
        self.start.valueChanged.connect(lambda v: self.set("start", v))
        self.stop.valueChanged.connect(lambda v: self.set("stop", v))
        self.step.valueChanged.connect(lambda v: self.set("step", v))
        self.points.valueChanged.connect(lambda v: self.set("points", v) if self.by_points.isChecked() else None)
        self.by_step.toggled.connect(self._on_by)
        self.settle.valueChanged.connect(lambda v: self.set("settle", v))
        self.axis_name.editingFinished.connect(lambda: self.set("axis_name", self.axis_name.text().strip()))

    def _mode(self, mode: str) -> None:
        if not self._loading and self.block is not None:
            bid = self.block.id
            self.doc.mutate(lambda s: s.set_mode(bid, mode), origin=None)

    def _on_target(self) -> None:
        ref = self.target.currentData()
        if ref and not self._loading and self.block is not None:
            t = self.doc.catalog.target(ref)
            bid = self.block.id

            def fn(s):
                b = s.find(bid)[0]
                b.target = ref
                if t and split_unit(b.unit)[0] != split_unit(t.unit)[0]:
                    b.unit = t.unit
            self.doc.mutate(fn, origin=None)

    def _on_unit(self, new: str) -> None:
        if self._loading or self.block is None or not new or new == self.block.unit:
            return
        old = self.block.unit
        base_o, fo = split_unit(old)
        base_n, fn_ = split_unit(new)
        k = fo / fn_ if base_o == base_n and fn_ else 1.0
        bid = self.block.id

        def fn(s):
            b = s.find(bid)[0]
            b.unit = new
            for f in ("value", "start", "stop", "step"):
                v = getattr(b, f)
                if v is not None:
                    setattr(b, f, round(v * k, 12))
        self.doc.mutate(fn, origin=None)

    def _on_by(self, by_step: bool) -> None:
        if self._loading or self.block is None:
            return
        bid, pts = self.block.id, self.points.value()

        def fn(s):
            b = s.find(bid)[0]
            if by_step:
                b.points = None
                if not b.step:
                    b.step = 1.0
            else:
                b.points = max(2, pts or b.sweep_values_count() or 11)
        self.doc.mutate(fn, origin=None)

    def _load(self, b: Optional[Block]) -> None:
        if b is None:
            return
        i = self.target.findData(b.target)
        self.target.setCurrentIndex(i if i >= 0 else 0)
        self.mode_sweep.setChecked(b.is_loop)
        self.mode_fixed.setChecked(not b.is_loop)
        t = self.doc.catalog.target(b.target)
        base = split_unit(b.unit or (t.unit if t else ""))[0]
        self.unit.clear()
        self.unit.addItems(unit_choices(base, b.unit or ""))
        self.unit.setCurrentText(b.unit)
        for w in (self.value, self.start, self.stop, self.step):
            w.setSuffix(f" {b.unit}" if b.unit else "")
            w.setDecimals(decimals(b.unit))
        self.value.setValue(b.value or 0.0)
        self.start.setValue(b.start or 0.0)
        self.stop.setValue(b.stop or 0.0)
        self.step.setValue(b.step or 0.0)
        self.points.setValue(b.points or b.sweep_values_count() or 0)
        self.by_points.setChecked(bool(b.points))
        self.by_step.setChecked(not b.points)
        self.step.setEnabled(not b.points)
        self.points.setEnabled(bool(b.points))
        self.settle.setValue(b.settle or 0.0)
        self.axis_name.setText(b.axis_name)
        self.axis_name.setPlaceholderText(default_axis_name(t) if t else "")
        for w in (self.start, self.stop, self.step, self.points, self.by_step, self.by_points, self.axis_name):
            w.setEnabled(b.is_loop and (w is not self.step or not b.points) and (w is not self.points or bool(b.points)))
        self.value.setEnabled(not b.is_loop)
        self.settle.setEnabled(True)

    def refresh_issues(self) -> None:
        super().refresh_issues()
        b = self.block
        if b is None:
            return
        if b.is_loop:
            lp = next((x for x in self.doc.result.loops if x.block.id == b.id), None)
            n = lp.n if lp else b.sweep_values_count()
            self.count.setText(f"共 {n} 點" + ("（Labber step channel 名稱 = 資料軸名稱）" if n else ""))
        else:
            self.count.setText("開始量測前設定一次")


# ---------------------------------------------------------------------------
class MeasurePage(_Page):
    def __init__(self, doc: SchemeDoc) -> None:
        super().__init__(doc)
        self.inst = QtWidgets.QComboBox()
        for m in doc.catalog.measurers:
            self.inst.addItem(f"{m.head} · {m.label}", m.ref)
        self.traces = QtWidgets.QListWidget()
        self.traces.setMaximumHeight(92)
        self.traces.setFlow(QtWidgets.QListView.Flow.LeftToRight)
        self.traces.setWrapping(True)
        self.settings_box = QtWidgets.QGroupBox("儀器設定（Labber 的 Instrument config）")
        self.settings_form = QtWidgets.QFormLayout(self.settings_box)
        self.est = QtWidgets.QLabel()
        self.est.setStyleSheet("color:#495057")
        self.form.addRow("儀器", self.inst)
        self.form.addRow("量測通道", self.traces)
        self.form.addRow(self.settings_box)
        self.form.addRow("", self.est)
        self.form.addRow(self.issues)
        self.inst.currentIndexChanged.connect(self._on_inst)
        self.traces.itemChanged.connect(self._on_traces)
        self._fields: Dict[str, QtWidgets.QWidget] = {}

    def _on_inst(self) -> None:
        if self._loading or self.block is None:
            return
        ref = self.inst.currentData()
        m = self.doc.catalog.measurer(ref)
        bid = self.block.id

        def fn(s):
            b = s.find(bid)[0]
            if b.instrument != ref:
                b.instrument = ref
                b.traces = [m.traces[0]] if m else []
                b.settings = dict(m.defaults) if m else {}
        self.doc.mutate(fn, origin=None)

    def _on_traces(self) -> None:
        if self._loading:
            return
        sel = [self.traces.item(i).text() for i in range(self.traces.count())
               if self.traces.item(i).checkState() == Qt.CheckState.Checked]
        self.set("traces", sel)

    def _set_setting(self, key: str, value: Any) -> None:
        if self._loading or self.block is None:
            return
        bid = self.block.id

        def fn(s):
            b = s.find(bid)[0]
            b.settings = {**b.settings, key: value}
        self.doc.mutate(fn, origin=self)

    def _load(self, b: Optional[Block]) -> None:
        if b is None:
            return
        self.inst.setCurrentIndex(max(0, self.inst.findData(b.instrument)))
        m = self.doc.catalog.measurer(b.instrument)
        self.traces.clear()
        for tr in (m.traces if m else []):
            it = QtWidgets.QListWidgetItem(tr)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if tr in b.traces else Qt.CheckState.Unchecked)
            self.traces.addItem(it)
        while self.settings_form.rowCount():
            self.settings_form.removeRow(0)
        self._fields = {}
        for f in (m.fields if m else []):   # 欄位來自 driver 的參數表（measure_setting=True）
            key, unit = f.key, f.unit
            raw = (b.settings or {}).get(key)
            if f.kind == "bool":
                w = QtWidgets.QCheckBox()
                w.setChecked(str(raw).strip().upper() in ("1", "ON", "TRUE", "YES") if raw is not None else False)
                w.toggled.connect(lambda v, k=key: self._set_setting(k, bool(v)))
            elif f.kind in ("enum", "str"):
                if f.kind == "enum" and f.choices:
                    w = QtWidgets.QComboBox()
                    w.addItems([str(c) for c in f.choices])
                    w.setCurrentText(str(raw) if raw is not None else "")
                    w.currentTextChanged.connect(lambda v, k=key: self._set_setting(k, v))
                else:
                    w = QtWidgets.QLineEdit("" if raw is None else str(raw))
                    w.editingFinished.connect(lambda k=key, w=w: self._set_setting(k, w.text()))
            elif f.kind == "int" or not unit:
                w = ispin(-10_000_000, 10_000_000)
                try:
                    w.setValue(int(float(raw)) if raw is not None else 0)
                except (TypeError, ValueError):
                    w.setValue(0)
                if unit:
                    w.setSuffix(f" {unit}")
                w.valueChanged.connect(lambda v, k=key: self._set_setting(k, int(v)))
            else:
                w = spin(-1e12, 1e12, decimals(unit), unit)
                try:
                    w.setValue(parse_quantity(raw, unit) / split_unit(unit)[1] if raw is not None else 0.0)
                except Exception:  # noqa: BLE001
                    w.setValue(0.0)
                w.valueChanged.connect(lambda v, k=key, u=unit: self._set_setting(k, f"{v:.9g} {u}"))
            self._fields[key] = w
            self.settings_form.addRow(f.label, w)

    def refresh_issues(self) -> None:
        super().refresh_issues()
        if self.block is not None:
            t = _measure_time(self.block, self.doc.catalog)
            self.est.setText(f"估計每次量測約 {t:.2f} s")


# ---------------------------------------------------------------------------
class SavePage(_Page):
    def __init__(self, doc: SchemeDoc) -> None:
        super().__init__(doc)
        self.fname = QtWidgets.QLineEdit()
        self.labber = QtWidgets.QCheckBox("Labber（與舊檔相同結構）")
        self.hdf5 = QtWidgets.QCheckBox("labcontrol HDF5（原生）")
        self.collision = QtWidgets.QComboBox()
        for k, v in (("underscore", "自動遞增（底線 _2）"), ("paren", "自動遞增（括號 (2)）"), ("overwrite", "直接覆寫")):
            self.collision.addItem(v, k)
        self.where = QtWidgets.QLabel()
        self.where.setWordWrap(True)
        self.where.setStyleSheet("background:#ebfbee; border:1px solid #b2f2bb; border-radius:6px; padding:8px;")
        self.form.addRow("檔名", self.fname)
        fm = QtWidgets.QVBoxLayout()
        fm.addWidget(self.labber)
        fm.addWidget(self.hdf5)
        self.form.addRow("格式", fm)
        self.form.addRow("同名檔案", self.collision)
        self.form.addRow(self.where)
        note = QtWidgets.QLabel("另外每次量測都會在當日資料夾的 _raw/ 逐點寫入原始檔，當機也不會遺失。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#868e96")
        self.form.addRow(note)
        self.form.addRow(self.issues)
        self.fname.editingFinished.connect(lambda: self.set("file_name", self.fname.text().strip()))
        self.labber.toggled.connect(lambda _v: self._formats())
        self.hdf5.toggled.connect(lambda _v: self._formats())
        self.collision.currentIndexChanged.connect(lambda _i: self.set("collision", self.collision.currentData()))

    def _formats(self) -> None:
        f = (["labber"] if self.labber.isChecked() else []) + (["hdf5"] if self.hdf5.isChecked() else [])
        self.set("formats", f)

    def _load(self, b: Optional[Block]) -> None:
        if b is None:
            return
        self.fname.setText(b.file_name)
        self.labber.setChecked("labber" in b.formats)
        self.hdf5.setChecked("hdf5" in b.formats)
        self.collision.setCurrentIndex(max(0, self.collision.findData(b.collision)))

    def refresh_issues(self) -> None:
        super().refresh_issues()
        r = self.doc.result
        if self.block is None:
            return
        if r.save is not None and r.save.id == self.block.id and r.split_depth:
            names = "、".join(lp.axis_name for lp in r.loops[:r.split_depth])
            ex = (f"{self.block.file_name.rsplit('.', 1)[0] or '檔名'}_001_"
                  f"{r.loops[0].values[0] * r.loops[0].display_scale:g}{r.loops[0].display_unit}")
            self.where.setText(f"<b>Data 在「{names}」迴圈裡</b><br>{names}每走一個值存一個檔，共 {r.n_files} 個檔"
                               f"<br><span style='color:#495057'>檔名例：</span><br><code>{ex}</code>")
        else:
            self.where.setText("<b>Data 在所有迴圈之外</b><br>整個實驗存成一個檔<br>（多維掃描存成多維 Labber 檔）")


class WaitPage(_Page):
    def __init__(self, doc: SchemeDoc) -> None:
        super().__init__(doc)
        self.sec = spin(0, 86400, 3, "s", 0.5)
        self.form.addRow("等待", self.sec)
        hint = QtWidgets.QLabel("放在迴圈裡 = 加到該迴圈每點的等待；放在最外層 = 開始量測前等待一次。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#868e96")
        self.form.addRow(hint)
        self.form.addRow(self.issues)
        self.sec.valueChanged.connect(lambda v: self.set("seconds", v))

    def _load(self, b: Optional[Block]) -> None:
        if b is not None:
            self.sec.setValue(b.seconds or 0.0)


# ---------------------------------------------------------------------------
class SchemePage(_Page):
    """沒有選取方塊時：整個方案的設定。"""

    def __init__(self, doc: SchemeDoc) -> None:
        super().__init__(doc)
        self.name = QtWidgets.QLineEdit()
        self.snake = QtWidgets.QCheckBox("蛇形（內圈來回掃，不必每圈斜坡回起點）")
        self.approach = spin(0.001, 1000, 3, "mA/s", 0.1)
        self.park = QtWidgets.QComboBox()
        self.park.addItem("斜坡回到起點", "start")
        self.park.addItem("停在最後一點", "none")
        self.park_rate = spin(0.001, 1000, 3, "mA/s", 0.1)
        self.on_error = QtWidgets.QComboBox()
        self.on_error.addItem("重試後自動暫停（等人處理）", "pause")
        self.on_error.addItem("重試後中斷量測", "stop")
        self.retries = ispin(0, 10)
        self.dip = QtWidgets.QCheckBox("吸收峰型態變化（1⇌2）自動暫停")
        self.root = QtWidgets.QLineEdit()
        self.root.setPlaceholderText("預設用 settings.yaml 的 data.root")
        self.form.addRow("方案名稱", self.name)
        self.form.addRow("", self.snake)
        self.form.addRow("移到起點速率", self.approach)
        self.form.addRow("結束後", self.park)
        self.form.addRow("停靠速率", self.park_rate)
        self.form.addRow("量測錯誤", self.on_error)
        self.form.addRow("重試次數", self.retries)
        self.form.addRow("自動判斷", self.dip)
        self.form.addRow("資料根目錄", self.root)
        self.form.addRow(self.issues)
        self.name.editingFinished.connect(lambda: self._scheme(lambda s: setattr(s, "name", self.name.text().strip() or s.name)))
        self.snake.toggled.connect(lambda v: self._scheme(lambda s: setattr(s, "snake", v)))
        self.approach.valueChanged.connect(lambda v: self._run("approach_rate", f"{v:g} mA/s"))
        self.park.currentIndexChanged.connect(lambda _i: self._run("park", self.park.currentData()))
        self.park_rate.valueChanged.connect(lambda v: self._run("park_rate", f"{v:g} mA/s"))
        self.on_error.currentIndexChanged.connect(lambda _i: self._run("on_error", self.on_error.currentData()))
        self.retries.valueChanged.connect(lambda v: self._run("retries", int(v)))
        self.dip.toggled.connect(self._dip)
        self.root.editingFinished.connect(lambda: self._scheme(lambda s: s.output.__setitem__("root", self.root.text().strip())
                                                                if self.root.text().strip() else s.output.pop("root", None)))

    def _scheme(self, fn) -> None:
        if not self._loading:
            self.doc.mutate(fn, origin=self)

    def _run(self, key: str, value: Any) -> None:
        self._scheme(lambda s: s.run.__setitem__(key, value))

    def _dip(self, on: bool) -> None:
        def fn(s):
            s.hooks = [h for h in s.hooks if h.get("type") != "dip_shape_pause"]
            if on:
                s.hooks.append({"type": "dip_shape_pause", "prominence_db": 3, "enabled": True})
        self._scheme(fn)

    def _load(self, b: Optional[Block]) -> None:
        s = self.doc.scheme
        self.name.setText(s.name)
        self.snake.setChecked(s.snake)
        run = {**(setting("run_defaults", {}) or {}), **s.run}   # 方案沒寫 → settings.yaml run_defaults
        try:
            self.approach.setValue(parse_quantity(run.get("approach_rate") or "0.5 mA/s") * 1e3)
            self.park_rate.setValue(parse_quantity(run.get("park_rate") or "1 mA/s") * 1e3)
        except Exception:  # noqa: BLE001
            pass
        self.park.setCurrentIndex(max(0, self.park.findData(run.get("park", "start"))))
        self.on_error.setCurrentIndex(max(0, self.on_error.findData(run.get("on_error", "pause"))))
        self.retries.setValue(int(run.get("retries", 1)))
        self.dip.setChecked(any(h.get("type") == "dip_shape_pause" and h.get("enabled", True) for h in s.hooks))
        self.root.setText(s.output.get("root", "") or "")

    def refresh_issues(self) -> None:
        rows = []
        for i in self.doc.result.issues:
            if i.block_id is None:
                c = {"error": "#e03131", "warning": "#e67700", "info": "#1971c2"}[i.level]
                rows.append(f"<div style='color:{c}'>{'✖⚠ℹ'[['error', 'warning', 'info'].index(i.level)]} {i.message}</div>")
        self.issues.setText("".join(rows))


class Inspector(QtWidgets.QWidget):
    def __init__(self, doc: SchemeDoc, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.title = QtWidgets.QLabel()
        self.title.setStyleSheet("font-size:15px; font-weight:600; padding:4px 2px;")
        self.stack = QtWidgets.QStackedWidget()
        self.pages = {"set": SetPage(doc), "measure": MeasurePage(doc), "save": SavePage(doc),
                      "wait": WaitPage(doc), None: SchemePage(doc)}
        for p in self.pages.values():
            self.stack.addWidget(p)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.addWidget(self.title)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setWidget(self.stack)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(scroll, 1)
        self.setMinimumWidth(320)
        doc.selection_changed.connect(lambda _b: self.reload())
        doc.changed.connect(self._changed)
        self.reload()

    def _current_page(self) -> _Page:
        b = self.doc.selected_block
        return self.pages[b.kind if b else None]

    def reload(self) -> None:
        b = self.doc.selected_block
        page = self._current_page()
        self.stack.setCurrentWidget(page)
        titles = {"set": "設定方塊（DC set / 儀器參數）", "measure": "量測方塊", "save": "Data（存檔）", "wait": "等待"}
        self.title.setText(titles[b.kind] if b else "方案設定")
        page.load(b)

    def _changed(self, origin: object) -> None:
        page = self._current_page()
        if origin is page:
            page.refresh_issues()
        else:
            self.reload()

