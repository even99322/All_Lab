"""遠端儀器控制視窗：透過 Hub 讀寫某個節點上的儀器參數（任何電腦都能用）。"""
from __future__ import annotations

from typing import Any, Dict

from PyQt6 import QtCore, QtWidgets

from .bg import run_bg
from .client import HubClient

PREFIX = {"G": 1e9, "M": 1e6, "k": 1e3, "": 1.0, "m": 1e-3, "u": 1e-6, "µ": 1e-6, "n": 1e-9, "p": 1e-12}


def unit_scale(display: str, unit: str) -> float:
    if not unit or not display or display == unit:
        return 1.0
    if display.endswith(unit):
        return PREFIX.get(display[: -len(unit)], 1.0)
    return 1.0


def fmt_value(v: Any, p: Dict[str, Any]) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "ON" if v else "OFF"
    if isinstance(v, (int, float)) and p.get("kind") in ("float", "int"):
        s = unit_scale(p.get("display_unit") or "", p.get("unit") or "")
        return f"{v / s:.9g}"
    if isinstance(v, dict):
        return f"（陣列 {v.get('n', '?')} 點）"
    return str(v)


class ParamRow(QtCore.QObject):
    def __init__(self, p: Dict[str, Any]) -> None:
        super().__init__()
        self.p = p
        self.dirty = False
        kind = p.get("kind")
        if not p.get("settable"):
            self.w: QtWidgets.QWidget = QtWidgets.QLabel("—")
            self.w.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        elif kind == "bool":
            self.w = QtWidgets.QCheckBox()
            self.w.toggled.connect(self._mark)
        elif kind == "enum" and p.get("choices"):
            self.w = QtWidgets.QComboBox()
            self.w.addItems([str(c) for c in p["choices"]])
            self.w.currentIndexChanged.connect(self._mark)
        else:
            self.w = QtWidgets.QLineEdit()
            self.w.textEdited.connect(self._mark)
        unit = p.get("display_unit") or p.get("unit") or ""
        self.label = QtWidgets.QLabel(f"{p.get('label') or p['name']}" + (f"（{unit}）" if unit else ""))
        tip = p["ref"] + (f"\n範圍：{p['limits']} {p.get('unit', '')}" if p.get("limits") else "") + \
            (f"\n{p['doc']}" if p.get("doc") else "")
        self.label.setToolTip(tip)
        self.w.setToolTip(tip)

    def _mark(self, *_a) -> None:
        self.dirty = True
        self.w.setStyleSheet("background:rgba(250,176,5,0.25);")

    def show(self, v: Any, err: str = "") -> None:
        self.dirty = False
        self.w.setStyleSheet("background:rgba(250,82,82,0.2);" if err else "")
        self.w.blockSignals(True)
        try:
            if isinstance(self.w, QtWidgets.QCheckBox):
                self.w.setChecked(bool(v))
            elif isinstance(self.w, QtWidgets.QComboBox):
                i = self.w.findText(str(v))
                if i >= 0:
                    self.w.setCurrentIndex(i)
            elif isinstance(self.w, QtWidgets.QLineEdit):
                self.w.setText(fmt_value(v, self.p))
            else:
                self.w.setText("錯誤" if err else (fmt_value(v, self.p) or "—"))
            if err:
                self.w.setToolTip(err)
        finally:
            self.w.blockSignals(False)

    def value(self) -> Any:
        if isinstance(self.w, QtWidgets.QCheckBox):
            return bool(self.w.isChecked())
        if isinstance(self.w, QtWidgets.QComboBox):
            return self.w.currentText()
        text = self.w.text().strip()
        unit = self.p.get("display_unit") or self.p.get("unit") or ""
        if self.p.get("kind") in ("float", "int") and unit and not any(c.isalpha() for c in text.replace("e", "")):
            return f"{text} {unit}"
        return text


class RemoteInstrumentDialog(QtWidgets.QDialog):
    def __init__(self, client: HubClient, node: str, name: str, parent=None) -> None:
        super().__init__(parent)
        self.client, self.node, self.name = client, node, name
        self.rows: Dict[str, ParamRow] = {}
        self.setWindowTitle(f"{name} @ {node} — 遠端儀器控制")
        self.resize(560, 640)
        lay = QtWidgets.QVBoxLayout(self)
        self.head = QtWidgets.QLabel(f"<b style='font-size:15px'>{name}</b>　節點 {node}")
        lay.addWidget(self.head)
        bar = QtWidgets.QHBoxLayout()
        self.b_conn = QtWidgets.QPushButton("▶ 連線")
        self.b_disc = QtWidgets.QPushButton("■ 中斷")
        self.b_read = QtWidgets.QPushButton("讀取全部")
        self.b_write = QtWidgets.QPushButton("寫入變更")
        self.auto = QtWidgets.QCheckBox("自動讀取")
        for b in (self.b_conn, self.b_disc, self.b_read, self.b_write):
            bar.addWidget(b)
        bar.addStretch()
        bar.addWidget(self.auto)
        lay.addLayout(bar)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        lay.addWidget(self.scroll, 1)
        self.msg = QtWidgets.QLabel("讀取參數表…")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        self.b_conn.clicked.connect(lambda: self._cmd("connect", {"names": [self.name]}, "連線"))
        self.b_disc.clicked.connect(lambda: self._cmd("disconnect", {"names": [self.name]}, "中斷"))
        self.b_read.clicked.connect(self.read_all)
        self.b_write.clicked.connect(self.write_dirty)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.read_all)
        self.auto.toggled.connect(lambda on: self.timer.start(2000) if on else self.timer.stop())
        self._busy = False
        self.load()

    # ---- 參數表 ------------------------------------------------------------
    def load(self) -> None:
        run_bg(lambda: self.client.command(self.node, "describe", {"name": self.name}), self._build,
               lambda m: self.msg.setText(f"❌ {m}"))

    def _build(self, d: Dict[str, Any]) -> None:
        self.desc = d
        body = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(body)
        self.rows.clear()
        for g in d.get("groups") or []:
            box = QtWidgets.QGroupBox(f"通道 {g['channel']}" if g.get("channel") else ("輸出" if g.get("source") else "設定"))
            form = QtWidgets.QFormLayout(box)
            for p in g["params"]:
                r = ParamRow(p)
                row = QtWidgets.QHBoxLayout()
                row.addWidget(r.w, 1)
                if p.get("settable"):
                    b = QtWidgets.QToolButton()
                    b.setText("寫入")
                    b.clicked.connect(lambda _c=False, ref=p["ref"]: self.write([ref]))
                    row.addWidget(b)
                form.addRow(r.label, row)
                r.show(p.get("value"))
                self.rows[p["ref"]] = r
            v.addWidget(box)
        v.addStretch()
        self.scroll.setWidget(body)
        self._header(d)
        if d.get("connected"):
            self.read_all()
        else:
            self.msg.setText("尚未連線：按「▶ 連線」（唯讀連線，不改變輸出）")

    def _header(self, d: Dict[str, Any]) -> None:
        st = "量測使用中（唯讀）" if d.get("lease") else ("已連線" if d.get("connected") else "未連線")
        self.head.setText(f"<b style='font-size:15px'>{self.name}</b>　{d.get('label', '')}<br>"
                          f"<span style='color:gray'>節點 {self.node} · {d.get('driver', '')} · {d.get('address', '')}"
                          f" · {st}</span>" + (f"<br><span style='color:gray'>{d.get('idn')}</span>" if d.get("idn") else ""))
        self.b_write.setEnabled(not d.get("lease"))

    # ---- 讀寫 ------------------------------------------------------------
    def read_all(self) -> None:
        if self._busy:
            return
        self._busy = True

        def ok(res):
            self._busy = False
            for ref, r in (res or {}).items():
                row = self.rows.get(ref)
                if row is not None and not row.dirty:
                    row.show(r.get("value"), r.get("error", ""))
            self.msg.setText(f"✔ 已讀取 {len(res or {})} 個參數（{QtCore.QTime.currentTime().toString('HH:mm:ss')}）")

        def err(m):
            self._busy = False
            self.msg.setText(f"❌ {m}")
        run_bg(lambda: self.client.command(self.node, "get_all", {"name": self.name}), ok, err)

    def write_dirty(self) -> None:
        refs = [ref for ref, r in self.rows.items() if r.dirty]
        if not refs:
            self.msg.setText("沒有改過的欄位（改過的欄位會變黃色）")
            return
        self.write(refs)

    def write(self, refs) -> None:
        vals = [(ref, self.rows[ref].value()) for ref in refs]
        self.msg.setText(f"寫入 {len(vals)} 個參數…")

        def work():
            out = {}
            for ref, v in vals:
                try:
                    out[ref] = (self.client.command(self.node, "set", {"ref": ref, "value": v}, wait=120), "")
                except Exception as e:  # noqa: BLE001
                    out[ref] = (None, str(e))
            return out

        def ok(out):
            bad = {k: e for k, (_v, e) in out.items() if e}
            for ref, (v, e) in out.items():
                if not e:
                    self.rows[ref].show(v)
            self.msg.setText("✔ 已寫入" if not bad else "❌ " + "；".join(f"{k}：{e}" for k, e in bad.items()))
        run_bg(work, ok, lambda m: self.msg.setText(f"❌ {m}"))

    def _cmd(self, cmd: str, args: Dict[str, Any], label: str) -> None:
        self.msg.setText(f"{label}中…")

        def ok(res):
            bad = {k: v for k, v in (res or {}).items() if v}
            self.msg.setText(f"❌ {label}失敗：" + "；".join(bad.values()) if bad else f"✔ 已{label}")
            self.load()
        run_bg(lambda: self.client.command(self.node, cmd, args), ok, lambda m: self.msg.setText(f"❌ {m}"))

    def closeEvent(self, e) -> None:
        self.timer.stop()
        super().closeEvent(e)
