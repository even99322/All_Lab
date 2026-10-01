"""儀器控制視窗（= Labber Instrument Server 的 driver configuration 視窗）。

  * 讀取全部（Get cfg）：從儀器讀回所有參數。
  * 寫入變更（Set cfg）：把改過的欄位（黃色）寫入儀器；勾「變更立即寫入」則每改一欄就寫。
  * 電源：目前輸出值、設定輸出（超過單次跳動上限自動斜坡，可中止）、開 / 關輸出、斜坡歸零後關閉。
  * 量測儀器：取得 trace 並畫圖（會觸發一次量測）。
  * 通訊紀錄：送出 / 收到的每一條指令；SCPI 次數與平均耗時（Labber 的 Timing statistics）。
  * 量測使用中（lease）時所有寫入停用，只能讀。
"""
from __future__ import annotations

import datetime as _dt
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, pyqtSignal

from ....core.capabilities import Source, TraceAcquirer
from ....core.instrument import Instrument, Parameter
from ....core.station import Station
from ....core.units import parse_quantity, split_unit
from ....settings import setting
from .. import theme
from ..bridge import QtEventBridge
from ..worker import run_bg

DIRTY = theme.tint("warn", border=False, padding="")      # 修改中（半透明，淺色 / 深色都看得到）
ERRS = theme.tint("err", border=False, padding="")


def status_of(station: Station, name: str) -> Tuple[str, str]:
    """(文字, 顏色)"""
    inst = station.instruments.get(name)
    if inst is None:
        return "未載入", theme.c("disabled")
    holder = station.lease_holder(name)
    if holder:
        return ("測試中" if holder.startswith("driver-test") else "量測使用中"), theme.c("accent")
    if inst.connected:
        return "已連線", theme.c("ok")
    return "未連線", theme.c("muted")


def _display_unit(inst: Instrument, p: Parameter) -> str:
    from ....diagnostics import param_unit

    sp = p.spec
    if sp is not None:
        return param_unit(p)
    if p.name == "level":
        u = p.unit or "A"
        du = inst.options.get("display_unit")
        if du and split_unit(du)[0] == u:
            return du
        return {"A": setting("editor.new_blocks.dc_set.unit", "mA"), "V": "V"}.get(u, u)
    return p.unit or ""


class ParamRow(QtCore.QObject):
    """一個參數的編輯列：顯示單位、型別對應的元件、改過（黃色）、錯誤（紅色）。"""
    edited = pyqtSignal(object)

    def __init__(self, inst: Instrument, ref: str, p: Parameter) -> None:
        super().__init__()
        self.inst, self.ref, self.p = inst, ref, p
        sp = p.spec
        self.kind = sp.kind if sp is not None else ("bool" if p.name == "output" else "float")
        self.unit = _display_unit(inst, p)
        self.scale = split_unit(self.unit)[1] if self.unit and self.kind in ("float",) else 1.0
        self.dirty = False
        self._loading = False
        if not p.settable:
            self.w: QtWidgets.QWidget = QtWidgets.QLabel("—")
            self.w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        elif self.kind == "bool":
            self.w = QtWidgets.QCheckBox()
            self.w.toggled.connect(self._changed)
        elif self.kind == "enum" and sp is not None and sp.choices:
            self.w = QtWidgets.QComboBox()
            self.w.addItems([str(c) for c in sp.choices])
            self.w.currentIndexChanged.connect(self._changed)
        else:
            self.w = QtWidgets.QLineEdit()
            self.w.setPlaceholderText("—")
            self.w.textEdited.connect(self._changed)
            self.w.returnPressed.connect(lambda: self.edited.emit(self))
        tip = []
        if sp is not None:
            if sp.limits:
                tip.append(f"上下限 [{sp.limits[0] / (self.scale or 1):g}, {sp.limits[1] / (self.scale or 1):g}] {self.unit}")
            if sp.doc:
                tip.append(sp.doc)
        self.w.setToolTip("\n".join(tip))
        self.label = QtWidgets.QLabel((sp.label if sp is not None and sp.label else p.name)
                                      + (f"（{self.unit}）" if self.unit else ""))
        self.label.setToolTip(ref)

    def _changed(self, *_a) -> None:
        if self._loading:
            return
        self.dirty = True
        self.w.setStyleSheet(DIRTY)
        if self.kind in ("bool", "enum"):
            self.edited.emit(self)

    def show_value(self, v: Any, err: str = "") -> None:
        self._loading = True
        try:
            self.dirty = False
            self.w.setStyleSheet(ERRS if err else "")
            self.w.setToolTip(err or self.w.toolTip())
            if err:
                if isinstance(self.w, QtWidgets.QLabel):
                    self.w.setText("錯誤")
                return
            if isinstance(self.w, QtWidgets.QCheckBox):
                self.w.setChecked(bool(v))
            elif isinstance(self.w, QtWidgets.QComboBox):
                self.w.setCurrentText(str(v))
            else:
                text = self._fmt(v)
                if isinstance(self.w, QtWidgets.QLineEdit):
                    self.w.setText(text)
                else:
                    self.w.setText(text + (f" {self.unit}" if self.unit and v is not None else ""))
        finally:
            self._loading = False

    def _fmt(self, v: Any) -> str:
        if v is None:
            return "—"
        if self.kind == "float":
            try:
                return f"{float(v) / (self.scale or 1.0):.10g}"
            except (TypeError, ValueError):
                return str(v)
        return str(v)

    def value(self) -> Any:
        if isinstance(self.w, QtWidgets.QCheckBox):
            return self.w.isChecked()
        if isinstance(self.w, QtWidgets.QComboBox):
            return self.w.currentText()
        text = self.w.text().strip()
        if self.kind == "int":
            return int(float(text))
        if self.kind == "float":
            return parse_quantity(text, self.unit)
        return text


class InstrumentConfigWindow(QtWidgets.QWidget):
    def __init__(self, station: Station, name: str, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.station, self.name = station, name
        self.inst: Instrument = station.instruments[name]
        self.rows: List[ParamRow] = []
        self.src_boxes: List[Dict[str, Any]] = []
        self._ramp_stop = threading.Event()
        self._traffic_n = int(setting("server.traffic_log_lines", 2000))
        self.setWindowTitle(f"{name} — 儀器控制")
        self.resize(760, 820)

        lay = QtWidgets.QVBoxLayout(self)
        head = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(f"<b style='font-size:15px'>{name}</b>　"
                                 f"{self.inst.options.get('label', '')}<br>"
                                 + theme.span(f"{getattr(self.inst, 'config_driver', self.inst.driver_name)}"
                                              f"　{self.inst.options.get('address', '')}", "muted"))
        head.addWidget(title, 1)
        self.state = QtWidgets.QLabel()
        head.addWidget(self.state)
        lay.addLayout(head)

        bar = QtWidgets.QHBoxLayout()
        self.b_conn = QtWidgets.QPushButton("▶ 連線")
        self.b_disc = QtWidgets.QPushButton("■ 中斷")
        self.b_get = QtWidgets.QPushButton("讀取全部")
        self.b_set = QtWidgets.QPushButton("寫入變更")
        self.immediate = QtWidgets.QCheckBox("變更立即寫入")
        self.auto = QtWidgets.QCheckBox("自動讀取")
        self.auto_s = QtWidgets.QDoubleSpinBox()
        self.auto_s.setRange(0.2, 3600)
        self.auto_s.setValue(2.0)
        self.auto_s.setSuffix(" s")
        self.b_test = QtWidgets.QPushButton("驅動測試…")
        for w in (self.b_conn, self.b_disc, self.b_get, self.b_set, self.immediate, self.auto, self.auto_s):
            bar.addWidget(w)
        bar.addStretch()
        bar.addWidget(self.b_test)
        lay.addLayout(bar)
        self.b_conn.clicked.connect(self.connect_inst)
        self.b_disc.clicked.connect(self.disconnect_inst)
        self.b_get.clicked.connect(self.get_all)
        self.b_set.clicked.connect(self.set_dirty)
        self.b_test.clicked.connect(self.open_test)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(lambda: self.get_all(quiet=True))
        self.auto.toggled.connect(lambda on: self.timer.start(int(self.auto_s.value() * 1000)) if on else self.timer.stop())
        self.auto_s.valueChanged.connect(lambda v: self.timer.setInterval(int(v * 1000)))

        self.tabs = QtWidgets.QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.tabs.addTab(self._settings_tab(), "設定")
        if isinstance(self.inst, TraceAcquirer):
            self.tabs.addTab(self._trace_tab(), "Trace")
        self.tabs.addTab(self._comm_tab(), "通訊紀錄")
        self.msg = QtWidgets.QLabel()
        self.msg.setWordWrap(True)
        self.msg.setStyleSheet("color:palette(text);")
        lay.addWidget(self.msg)

        self.bridge = QtEventBridge(station.bus, parent=self)
        for topic in ("instrument.connected", "instrument.closed", "instrument.error", "station.lease"):
            self.bridge.on(topic, lambda _p: self.refresh_state())
        self._tap_sig = _TapSignal()
        self._tap_sig.line.connect(self._append_traffic)
        self._tap = lambda k, s: self._tap_sig.line.emit(f"{_dt.datetime.now():%H:%M:%S.%f}"[:-3] + f"  {k}  {s}")
        self._attach_tap()
        self.refresh_state()
        if self.inst.connected:
            self.get_all(quiet=True)

    # ---- 分頁 -------------------------------------------------------------
    def _settings_tab(self) -> QtWidgets.QWidget:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        body = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(body)
        nodes = [("", self.inst)] + [(k, ch) for k, ch in self.inst.channels.items()]
        for ck, node in nodes:
            params = [(k, p) for k, p in node.parameters.items()]
            if not params and not isinstance(node, Source):
                continue
            box = QtWidgets.QGroupBox(f"通道 {ck}" if ck else ("設定" if not isinstance(node, Source) else "輸出"))
            form = QtWidgets.QFormLayout(box)
            if isinstance(node, Source):
                form.addRow(self._source_box(ck, node))
            for k, p in params:
                if isinstance(node, Source) and k in ("level", "output"):
                    continue
                ref = f"{self.name}.{ck}.{k}" if ck else f"{self.name}.{k}"
                row = ParamRow(self.inst, ref, p)
                row.edited.connect(self._row_edited)
                self.rows.append(row)
                form.addRow(row.label, row.w)
            v.addWidget(box)
        if not self.rows and not self.src_boxes:
            v.addWidget(QtWidgets.QLabel("這台儀器沒有可設定的參數。"))
        v.addStretch()
        scroll.setWidget(body)
        return scroll

    def _source_box(self, ck: str, src: Source) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        g = QtWidgets.QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        p = src.parameters["level"]  # type: ignore[attr-defined]
        unit = _display_unit(self.inst, p)
        scale = split_unit(unit)[1] or 1.0
        now = QtWidgets.QLabel("—")
        theme.on_change(now, lambda w: w.setStyleSheet(f"font-size:18px; font-weight:600; color:{theme.c('curve')};"))
        out = QtWidgets.QLabel("—")
        target = QtWidgets.QLineEdit()
        target.setPlaceholderText(f"新的輸出值（{unit}）")
        b_set = QtWidgets.QPushButton("設定輸出")
        b_stop = QtWidgets.QPushButton("中止斜坡")
        b_stop.setEnabled(False)
        b_on = QtWidgets.QPushButton("開啟輸出")
        b_off = QtWidgets.QPushButton("關閉輸出")
        b_zero = QtWidgets.QPushButton("斜坡歸零後關閉")
        lim = src.limits
        rp = src.ramp_policy
        info = QtWidgets.QLabel(
            f"安全上下限 [{lim.lo / scale:g}, {lim.hi / scale:g}] {unit}　·　斜坡 "
            f"{(rp.rate / scale if rp.rate else 0):g} {unit}/s　·　單次跳動上限 "
            f"{(rp.max_jump / scale if rp.max_jump is not None else float('inf')):g} {unit}"
            + (f"　·　解析度 {src.resolution / scale:g} {unit}" if src.resolution else ""))
        info.setStyleSheet("color:palette(placeholder-text); font-size:11px;")
        info.setWordWrap(True)
        g.addWidget(QtWidgets.QLabel("目前輸出"), 0, 0)
        g.addWidget(now, 0, 1)
        g.addWidget(out, 0, 2)
        g.addWidget(QtWidgets.QLabel("設定為"), 1, 0)
        g.addWidget(target, 1, 1)
        hb = QtWidgets.QHBoxLayout()
        hb.addWidget(b_set)
        hb.addWidget(b_stop)
        g.addLayout(hb, 1, 2)
        hb2 = QtWidgets.QHBoxLayout()
        for b in (b_on, b_off, b_zero):
            hb2.addWidget(b)
        hb2.addStretch()
        g.addLayout(hb2, 2, 0, 1, 3)
        g.addWidget(info, 3, 0, 1, 3)
        d = {"ck": ck, "src": src, "unit": unit, "scale": scale, "now": now, "out": out, "target": target,
             "set": b_set, "stop": b_stop, "on": b_on, "off": b_off, "zero": b_zero}
        self.src_boxes.append(d)
        b_set.clicked.connect(lambda: self._set_level(d))
        target.returnPressed.connect(lambda: self._set_level(d))
        b_stop.clicked.connect(self._ramp_stop.set)
        b_on.clicked.connect(lambda: self._output(d, True))
        b_off.clicked.connect(lambda: self._output(d, False))
        b_zero.clicked.connect(lambda: self._safe_off(d))
        return w

    def _trace_tab(self) -> QtWidgets.QWidget:
        import pyqtgraph as pg

        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        row = QtWidgets.QHBoxLayout()
        self.trace = QtWidgets.QComboBox()
        self.trace.addItems(self.inst.trace_list())  # type: ignore[attr-defined]
        self.tr_disp = QtWidgets.QComboBox()
        self.tr_disp.addItems(["|S| (dB)", "相位 (rad)", "實部", "虛部"])
        b = QtWidgets.QPushButton("取得 trace（量測一次）")
        row.addWidget(QtWidgets.QLabel("通道"))
        row.addWidget(self.trace)
        row.addWidget(self.tr_disp)
        row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        self.tr_plot = pg.PlotWidget()
        self.tr_plot.showGrid(True, True, 0.25)
        self.tr_curve = self.tr_plot.plot(pen=pg.mkPen(theme.c("curve"), width=1.5))
        theme.style_plot(self.tr_plot, (self.tr_curve, "curve"))
        v.addWidget(self.tr_plot, 1)
        self.tr_info = QtWidgets.QLabel("取得 trace 會觸發一次掃描（使用儀器目前的設定），結束後恢復連續掃描。")
        self.tr_info.setStyleSheet("color:palette(placeholder-text);")
        self.tr_info.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.tr_info.setWordWrap(True)
        v.addWidget(self.tr_info)
        b.clicked.connect(self.get_trace)
        self.tr_disp.currentIndexChanged.connect(lambda _i: self._draw_trace())
        self._tr_data: Optional[Tuple[np.ndarray, np.ndarray, Any]] = None
        return w

    def _comm_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        self.traffic = QtWidgets.QPlainTextEdit()
        self.traffic.setReadOnly(True)
        self.traffic.setMaximumBlockCount(self._traffic_n)
        f = QtGui.QFont("Consolas")
        f.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        self.traffic.setFont(f)
        v.addWidget(self.traffic, 2)
        row = QtWidgets.QHBoxLayout()
        b1 = QtWidgets.QPushButton("清除紀錄")
        b2 = QtWidgets.QPushButton("更新統計")
        b3 = QtWidgets.QPushButton("重設統計")
        self.send = QtWidgets.QLineEdit()
        self.send.setPlaceholderText("手動送出 SCPI（結尾 ? 會讀回）… 例：*IDN?  或 :SYST:ERR?")
        b4 = QtWidgets.QPushButton("送出")
        for x in (b1, b2, b3):
            row.addWidget(x)
        row.addWidget(self.send, 1)
        row.addWidget(b4)
        v.addLayout(row)
        self.stats = QtWidgets.QTableWidget(0, 3)
        self.stats.setHorizontalHeaderLabels(["指令", "次數", "平均耗時 (ms)"])
        self.stats.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.stats, 1)
        b1.clicked.connect(self.traffic.clear)
        b2.clicked.connect(self._fill_stats)
        b3.clicked.connect(lambda: (self.inst.transport.stats.clear() if self.inst.transport else None,
                                    self._fill_stats()))
        b4.clicked.connect(self._send_raw)
        self.send.returnPressed.connect(self._send_raw)
        if self.station.simulate:
            note = QtWidgets.QLabel("模擬模式：沒有實際通訊，這裡不會有紀錄。")
            note.setStyleSheet("color:palette(placeholder-text);")
            v.addWidget(note)
        return w

    # ---- 狀態 ------------------------------------------------------------
    def _attach_tap(self) -> None:
        tr = self.inst.transport
        if tr is not None and self._tap not in tr.taps:
            tr.taps.append(self._tap)

    def _detach_tap(self) -> None:
        tr = self.inst.transport
        if tr is not None and self._tap in tr.taps:
            tr.taps.remove(self._tap)

    def refresh_state(self) -> None:
        text, color = status_of(self.station, self.name)
        self.state.setText(f"<span style='background:{color};color:white;border-radius:4px;padding:3px 10px;"
                           f"font-weight:600'>{text}</span>")
        connected = self.inst.connected
        leased = self.station.lease_holder(self.name) is not None
        self.b_conn.setEnabled(not connected)
        self.b_disc.setEnabled(connected and not leased)
        self.b_get.setEnabled(connected)
        writable = connected and not leased
        self.b_set.setEnabled(writable)
        for r in self.rows:
            r.w.setEnabled(writable or isinstance(r.w, QtWidgets.QLabel))
        for d in self.src_boxes:
            for k in ("set", "on", "off", "zero", "target"):
                d[k].setEnabled(writable)
        if leased:
            self.msg.setText("量測 / 測試使用中：只能讀取，不能寫入（與 Labber 相同）。")
        self._attach_tap()

    def _append_traffic(self, line: str) -> None:
        self.traffic.appendPlainText(line)

    def _fill_stats(self) -> None:
        st = self.inst.transport.stats if self.inst.transport else {}
        items = sorted(st.items(), key=lambda kv: -kv[1][1])
        self.stats.setRowCount(len(items))
        for i, (k, (n, t)) in enumerate(items):
            for j, val in enumerate((k, f"{int(n)}", f"{t / max(n, 1) * 1e3:.2f}")):
                self.stats.setItem(i, j, QtWidgets.QTableWidgetItem(val))

    def _err(self, msg: str) -> None:
        self.msg.setText(f"<span style='color:{theme.c('err')}'>❌ {msg}</span>")

    # ---- 動作 ------------------------------------------------------------
    def connect_inst(self) -> None:
        self.msg.setText("連線中（唯讀，不改變輸出）…")
        run_bg(lambda: self.station.connect([self.name]),
               lambda _r: (self.msg.setText(f"✔ 已連線：{self.inst._idn}"), self.refresh_state(), self.get_all()),
               self._err)

    def disconnect_inst(self) -> None:
        self._detach_tap()
        run_bg(lambda: self.station.disconnect(self.name), lambda _r: (self.msg.setText("已中斷連線"),
                                                                       self.refresh_state()), self._err)

    def get_all(self, quiet: bool = False) -> None:
        if not self.inst.connected:
            return
        rows = list(self.rows)
        boxes = list(self.src_boxes)

        def fn():
            out = []
            for r in rows:
                try:
                    out.append((r, r.p.get(), ""))
                except Exception as e:  # noqa: BLE001
                    out.append((r, None, str(e)))
            src = []
            for d in boxes:
                try:
                    src.append((d, d["src"].get_level(), d["src"].get_output(), ""))
                except Exception as e:  # noqa: BLE001
                    src.append((d, None, None, str(e)))
            errs = self.inst.check_errors()
            return out, src, errs

        def done(res):
            out, src, errs = res
            for r, v, err in out:
                if not r.dirty:
                    r.show_value(v, err)
            for d, lv, on, err in src:
                self._show_source(d, lv, on, err)
            if errs:
                self._err("儀器錯誤佇列：" + "; ".join(errs))
            elif not quiet:
                self.msg.setText(f"✔ 已讀取 {len(out)} 個參數（{_dt.datetime.now():%H:%M:%S}）")
        run_bg(fn, done, self._err)

    def _show_source(self, d, lv, on, err="") -> None:
        if err:
            d["now"].setText("錯誤")
            d["now"].setToolTip(err)
            return
        if lv is not None:
            d["now"].setText(f"{lv / d['scale']:.7g} {d['unit']}")
        if on is not None:
            d["out"].setText("● 輸出開啟" if on else "○ 輸出關閉")
            d["out"].setStyleSheet(f"color:{theme.c('ok' if on else 'muted')}; font-weight:600;")

    def _write_rows(self, rows: List[ParamRow]) -> None:
        try:
            vals = [(r, r.value()) for r in rows]
        except Exception as e:  # noqa: BLE001
            self._err(f"數值格式錯誤：{e}")
            return

        def fn():
            res = []
            for r, v in vals:
                try:
                    r.p.set(v)
                    errs = self.inst.check_errors()
                    res.append((r, r.p.get() if r.p.gettable else v, "; ".join(errs)))
                except Exception as e:  # noqa: BLE001
                    res.append((r, None, str(e)))
            return res

        def done(res):
            bad = [(r, e) for r, _v, e in res if e]
            for r, v, e in res:
                r.show_value(v, e) if not e else r.show_value(None, e)
            if bad:
                self._err("；".join(f"{r.p.name}：{e}" for r, e in bad))
            else:
                self.msg.setText(f"✔ 已寫入 {len(res)} 個參數")
            if bad:
                self.get_all(quiet=True)
        run_bg(fn, done, self._err)

    def set_dirty(self) -> None:
        rows = [r for r in self.rows if r.dirty]
        if not rows:
            self.msg.setText("沒有改過的欄位（改過的欄位會變黃色）")
            return
        self._write_rows(rows)

    def _row_edited(self, row: ParamRow) -> None:
        if self.immediate.isChecked() and self.inst.connected:
            self._write_rows([row])

    def _set_level(self, d) -> None:
        try:
            v = parse_quantity(d["target"].text().strip(), d["unit"])
        except Exception as e:  # noqa: BLE001
            self._err(f"數值格式錯誤：{e}")
            return
        src: Source = d["src"]
        try:
            src.limits.check(v, src.full_name)  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            self._err(str(e))
            return
        self._ramp_stop.clear()
        d["stop"].setEnabled(True)
        sig = _ProgSignal()
        sig.value.connect(lambda x: self._show_source(d, x, None))
        self._keep = sig
        self.msg.setText(f"設定 {src.full_name} → {v / d['scale']:g} {d['unit']}（超過單次跳動上限會以斜坡前進）")  # type: ignore[attr-defined]

        def fn():
            cur = src.get_level()
            rp = src.ramp_policy
            if rp.rate is not None and rp.max_jump is not None and rp.needs_ramp(v - cur):
                src.ramp_to(v, rate=rp.rate, stop_event=self._ramp_stop, on_step=lambda x: sig.value.emit(x))
            else:
                src.set_level(v)
            return src.get_level(), src.get_output(), self.inst.check_errors()

        def done(res):
            lv, on, errs = res
            d["stop"].setEnabled(False)
            self._show_source(d, lv, on)
            if errs:
                self._err("儀器錯誤佇列：" + "; ".join(errs))
            else:
                self.msg.setText("✔ 已中止斜坡" if self._ramp_stop.is_set() else "✔ 已設定")

        def fail(msg):
            d["stop"].setEnabled(False)
            self._err(msg)
        run_bg(fn, done, fail)

    def _output(self, d, on: bool) -> None:
        src: Source = d["src"]
        text = (f"開啟 {src.full_name} 的輸出？\n目前設定值會立刻輸出。" if on else  # type: ignore[attr-defined]
                f"直接關閉 {src.full_name} 的輸出？\n接電磁鐵時建議改用「斜坡歸零後關閉」。")  # type: ignore[attr-defined]
        if QtWidgets.QMessageBox.question(self, "輸出", text) != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        run_bg(lambda: (src.set_output(on), src.get_level(), src.get_output()),
               lambda r: self._show_source(d, r[1], r[2]), self._err)

    def _safe_off(self, d) -> None:
        src: Source = d["src"]
        if QtWidgets.QMessageBox.question(self, "輸出", f"以斜坡速率把 {src.full_name} 歸零後關閉輸出？") != \
                QtWidgets.QMessageBox.StandardButton.Yes:  # type: ignore[attr-defined]
            return
        d["target"].setText("0")
        self._ramp_stop.clear()
        d["stop"].setEnabled(True)
        sig = _ProgSignal()
        sig.value.connect(lambda x: self._show_source(d, x, None))
        self._keep = sig

        def fn():
            src.ramp_to(0.0, stop_event=self._ramp_stop, on_step=lambda x: sig.value.emit(x))
            if not self._ramp_stop.is_set():
                src.set_output(False)
            return src.get_level(), src.get_output()
        run_bg(fn, lambda r: (d["stop"].setEnabled(False), self._show_source(d, r[0], r[1])), self._err)

    def get_trace(self) -> None:
        if not self.inst.connected:
            self._err("請先連線")
            return
        name = self.trace.currentText()
        inst = self.inst
        self.tr_info.setText("量測中…")

        def fn():
            x = inst.x_axis(name)  # type: ignore[attr-defined]
            z = np.asarray(inst.acquire(name))  # type: ignore[attr-defined]
            rf = None
            if "output" in inst.parameters:
                try:
                    rf = bool(inst.parameters["output"].get())
                except Exception:  # noqa: BLE001
                    rf = None
            return x, z, inst.check_errors(), rf

        def done(res):
            x, z, errs, rf = res
            self._tr_data = (x.values, z, x)
            self._draw_trace()
            db = 20 * np.log10(np.abs(z) + 1e-30)
            self.tr_info.setText(f"{z.size} 點　最低 {db.min():.2f} dB @ {x.values[int(np.argmin(db))]:.9g} {x.unit}"
                                 + (f"　⚠ 儀器錯誤：{'; '.join(errs)}" if errs else "")
                                 + (theme.span("　⚠ RF 輸出關閉中：這條 trace 只是雜訊（S 參數 = 雜訊 / 雜訊），"
                                               "請先開啟 RF 輸出", "warn") if rf is False else ""))
        run_bg(fn, done, lambda m: (self.tr_info.setText(""), self._err(m)))

    def _draw_trace(self) -> None:
        if not self._tr_data:
            return
        xv, z, x = self._tr_data
        i = self.tr_disp.currentIndex()
        y = [20 * np.log10(np.abs(z) + 1e-30), np.unwrap(np.angle(z)), z.real, z.imag][i]
        self.tr_curve.setData(xv, y)
        self.tr_plot.setLabel("bottom", x.name, x.unit)
        self.tr_plot.setLabel("left", self.tr_disp.currentText())

    def _send_raw(self) -> None:
        cmd = self.send.text().strip()
        tr = self.inst.transport
        if not cmd:
            return
        if tr is None:
            self._err("沒有通訊（未連線或模擬模式）")
            return
        if not cmd.endswith("?") and QtWidgets.QMessageBox.question(
                self, "手動送出", f"送出寫入指令「{cmd}」？這會直接改變儀器狀態。") != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        if self.station.lease_holder(self.name):
            self._err("量測使用中，不能手動送指令")
            return
        run_bg(lambda: tr.query(cmd) if cmd.endswith("?") else tr.write(cmd),
               lambda r: self.msg.setText(f"✔ {cmd} → {r}" if r is not None else f"✔ 已送出 {cmd}"), self._err)

    def open_test(self) -> None:
        from .test_dialog import DriverTestDialog

        DriverTestDialog(self.station, [self.name], self).show()

    def closeEvent(self, ev) -> None:
        self.timer.stop()
        self._ramp_stop.set()
        self._detach_tap()
        self.bridge.close()
        super().closeEvent(ev)


class _TapSignal(QtCore.QObject):
    line = pyqtSignal(str)


class _ProgSignal(QtCore.QObject):
    value = pyqtSignal(float)
