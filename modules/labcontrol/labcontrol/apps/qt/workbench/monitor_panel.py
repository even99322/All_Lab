"""右上：即時監控（對應 Labber 的 Measurement 視窗）。

  * 控制：開始、暫停 / 繼續、退回上一點、停止；手動步進模式（單次 / 循環 / 記錄並下一點）。
  * 左側通道表：掃描通道的目前值與進度、量測通道的最新結果；點量測通道切換圖上顯示的通道。
  * 右側：最新曲線（dB / 相位 / 實部 / 虛部）與 2D 影像（最內圈 × 頻率；外圈每換一個值重畫）。
  * 量測在背景執行緒；畫面只透過 EventBus 事件更新。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, pyqtSignal

from ....core.station import Station
from ....measure.experiment import Experiment, OutputPlan
from ....measure.runner import Runner
from ....settings import setting
from .. import theme
from ..bridge import QtEventBridge
from ..worker import start_thread

STATE_TEXT = {"idle": ("待機", "#868e96"), "preparing": ("準備中", "#1971c2"), "running": ("量測中", "#2b8a3e"),
              "paused": ("已暫停", "#f08c00"), "finished": ("完成", "#2b8a3e"), "aborted": ("已中斷", "#e8590c"),
              "failed": ("失敗", "#e03131")}
DISPLAY = {"db": ("|S| (dB)", lambda z: 20 * np.log10(np.abs(z) + 1e-30)),
           "phase": ("相位 (rad)", lambda z: np.unwrap(np.angle(z))),
           "real": ("實部", np.real), "imag": ("虛部", np.imag)}


class MonitorPanel(QtWidgets.QWidget):
    start_requested = pyqtSignal()
    finished = pyqtSignal(str)

    def __init__(self, station: Station, parent=None, bus=None) -> None:
        super().__init__(parent)
        self.station = station
        self.runner: Optional[Runner] = None
        self.exp: Optional[Experiment] = None
        self.output: Optional[OutputPlan] = None
        self.heat: Optional[np.ndarray] = None
        self.x: Optional[np.ndarray] = None
        self.vec_names: List[str] = []
        self.show_ch: Optional[str] = None
        self.last_shot: Dict[str, Any] = {}
        self.outer_key = None

        self._own_run_id: Optional[str] = None
        #: run.started 時找出對應的 runner（本機：Runner 清單；遠端：RemoteRunnerProxy）
        self.runner_resolver = _local_runner
        self.bridge = QtEventBridge(bus or station.bus, parent=self)
        self.bridge.on("log", lambda p: self._log(p["message"]))
        self.bridge.on("run.state", self._on_state)
        self.bridge.on("run.progress", self._on_progress)
        self.bridge.on("run.started", self._on_started)
        self.bridge.on("point.shot", self._on_shot)
        self.bridge.on("point.committed", lambda p: self.lbl_shots.setText("暫存 0 筆"))
        self.bridge.on("point.rollback", self._on_rollback)
        self.bridge.on("manual.shots", lambda p: self.lbl_shots.setText(f"暫存 {p['count']} 筆"))
        self.bridge.on("run.finished", self._on_finished)
        self.bridge.on("run.ramp", self._on_ramp)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # ---- 控制列 ----
        row = QtWidgets.QHBoxLayout()
        self.b_start = QtWidgets.QPushButton("▶ 開始量測")
        theme.on_change(self.b_start, lambda b: b.setStyleSheet(
            "QPushButton{background:#2b8a3e;color:white;font-weight:600;padding:4px 12px;border-radius:4px;}"
            f"QPushButton:disabled{{background:{theme.c('disabled')};color:{theme.c('window')};}}"))
        self.b_pause = QtWidgets.QPushButton("⏸ 暫停")
        self.b_back = QtWidgets.QPushButton("⏪ 退回")
        self.b_stop = QtWidgets.QPushButton("⏹ 停止")
        self.chk_manual = QtWidgets.QCheckBox("手動步進")
        for w in (self.b_start, self.b_pause, self.b_back, self.b_stop, self.chk_manual):
            row.addWidget(w)
        row.addStretch()
        self.state = QtWidgets.QLabel()
        row.addWidget(self.state)
        lay.addLayout(row)
        self.b_start.clicked.connect(self.start_requested.emit)
        self.b_pause.clicked.connect(lambda: self.runner and self.runner.toggle_pause())
        self.b_back.clicked.connect(lambda: self.runner and self.runner.rollback())
        self.b_stop.clicked.connect(self._stop)
        self.chk_manual.toggled.connect(self._on_manual)

        self.manual_row = QtWidgets.QWidget()
        mr = QtWidgets.QHBoxLayout(self.manual_row)
        mr.setContentsMargins(0, 0, 0, 0)
        self.b_once = QtWidgets.QPushButton("單次量測")
        self.b_loop = QtWidgets.QPushButton("循環量測")
        self.b_loop.setCheckable(True)
        self.chk_retain = QtWidgets.QCheckBox("保留此點所有暫存")
        self.b_accept = QtWidgets.QPushButton("✅ 記錄並下一點")
        self.lbl_shots = QtWidgets.QLabel("暫存 0 筆")
        for w in (self.b_once, self.b_loop, self.chk_retain, self.b_accept, self.lbl_shots):
            mr.addWidget(w)
        mr.addStretch()
        self.b_once.clicked.connect(lambda: self.runner and self.runner.measure_once())
        self.b_loop.toggled.connect(lambda on: self.runner and (self.runner.loop_start() if on else self.runner.loop_stop()))
        self.b_accept.clicked.connect(self._accept)
        self.manual_row.setVisible(False)
        lay.addWidget(self.manual_row)

        prow = QtWidgets.QHBoxLayout()
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFormat("%v / %m 點")
        self.progress.setMaximumHeight(16)
        self.eta = QtWidgets.QLabel("")
        self.eta.setMinimumWidth(150)
        prow.addWidget(self.progress, 1)
        prow.addWidget(self.eta)
        lay.addLayout(prow)

        # ---- 通道表 + 圖 ----
        split = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        self.table = QtWidgets.QTreeWidget()
        self.table.setHeaderLabels(["通道", "目前值"])
        self.table.setRootIsDecorated(False)
        self.table.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemClicked.connect(self._on_table_click)
        split.addWidget(self.table)

        plots = QtWidgets.QWidget()
        pv = QtWidgets.QVBoxLayout(plots)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(2)
        tb = QtWidgets.QHBoxLayout()
        self.b_line = QtWidgets.QToolButton()
        self.b_line.setText("曲線")
        self.b_img = QtWidgets.QToolButton()
        self.b_img.setText("影像")
        for b in (self.b_line, self.b_img):
            b.setCheckable(True)
            b.setChecked(True)
            tb.addWidget(b)
        self.disp = QtWidgets.QComboBox()
        for k, (lab, _f) in DISPLAY.items():
            self.disp.addItem(lab, k)
        self.disp.setCurrentIndex(max(0, self.disp.findData(setting("monitor.trace_display", "db"))))
        tb.addWidget(self.disp)
        tb.addStretch()
        self.b_log = QtWidgets.QToolButton()
        self.b_log.setText("訊息")
        self.b_log.setCheckable(True)
        self.b_log.setChecked(True)
        tb.addWidget(self.b_log)
        pv.addLayout(tb)
        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget()
        self.plot.showGrid(True, True, 0.25)
        self.curve = self.plot.plot(pen=pg.mkPen(theme.c("curve"), width=1.5))
        theme.style_plot(self.plot, (self.curve, "curve"))
        self.heat_plot = theme.style_plot(pg.PlotWidget())
        self.img = pg.ImageItem()
        try:
            self.img.setColorMap(pg.colormap.get(setting("monitor.colormap", "viridis")))
        except Exception:  # noqa: BLE001
            self.img.setColorMap(pg.colormap.get("viridis"))
        self.heat_plot.addItem(self.img)
        vs = QtWidgets.QSplitter(Qt.Orientation.Vertical)
        vs.addWidget(self.plot)
        vs.addWidget(self.heat_plot)
        pv.addWidget(vs, 1)
        split.addWidget(plots)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 520])
        lay.addWidget(split, 1)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(78)
        self.log.setStyleSheet("font-size:11px;")
        lay.addWidget(self.log)
        self.b_line.toggled.connect(self.plot.setVisible)
        self.b_img.toggled.connect(self.heat_plot.setVisible)
        self.b_log.toggled.connect(self.log.setVisible)
        self.disp.currentIndexChanged.connect(lambda _i: self._redraw_last())
        self._set_state("idle")

    # ---- 預覽（尚未開始）--------------------------------------------------------
    def set_preview(self, result) -> None:
        if self.runner is not None and self.runner.is_active:
            return
        self.table.clear()
        for lp in result.loops:
            it = QtWidgets.QTreeWidgetItem([f"⟲ {lp.axis_name}", f"{lp.n} 點"])
            it.setForeground(0, QtGui.QBrush(theme.qc("curve")))
            self.table.addTopLevelItem(it)
        for m in result.measures:
            for tr in m.traces:
                it = QtWidgets.QTreeWidgetItem([f"● {m.instrument} - {tr}", "—"])
                self.table.addTopLevelItem(it)
        self.progress.setMaximum(max(1, result.total_points))
        self.progress.setValue(0)

    # ---- 執行 ------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self.runner is not None and self.runner.is_active

    def run(self, exp: Experiment, output: OutputPlan) -> None:
        if self.busy:
            return
        self.exp, self.output = exp, output
        self.log.clear()
        self.runner = exp.create_runner(output, manual=self.chk_manual.isChecked())
        self._own_run_id = self.runner.run_id
        self.runner.start()

    def _stop(self) -> None:
        if self.runner is not None and self.runner.is_active:
            self.runner.stop()

    def _on_manual(self, on: bool) -> None:
        self.manual_row.setVisible(on)
        if self.runner is not None:
            self.runner.set_manual(on)

    def _accept(self) -> None:
        if self.runner:
            self.runner.accept(self.chk_retain.isChecked())
            self.chk_retain.setChecked(False)

    def _log(self, msg: str) -> None:
        self.log.appendPlainText(msg)

    def _set_state(self, st: str) -> None:
        text, color = STATE_TEXT.get(st, (st, "#868e96"))
        self.state.setText(f"<span style='background:{color};color:white;border-radius:4px;padding:2px 8px;"
                           f"font-weight:600'>{text}</span>")
        running = st in ("preparing", "running", "paused")
        self.b_start.setEnabled(not running)
        for b in (self.b_pause, self.b_back, self.b_stop):
            b.setEnabled(running)
        self.b_pause.setText("▶ 繼續" if st == "paused" else "⏸ 暫停")

    # ---- 事件 ------------------------------------------------------------
    def _on_state(self, p: Dict[str, Any]) -> None:
        if self.runner is not None and hasattr(self.runner, "state_value"):
            self.runner.state_value = p["state"]
        self._set_state(p["state"])

    def _on_started(self, p: Dict[str, Any]) -> None:
        ds = p["dataset"]
        r = self.runner_resolver(p.get("run_id")) if self.runner_resolver else None
        if r is not None:
            self.runner = r          # 別人（遠端 / 節點）啟動的量測也能在這裡控制
        self.table.clear()
        self.axis_rows = {}
        for a in ds.axes:
            it = QtWidgets.QTreeWidgetItem([f"⟲ {a.name}", "—"])
            it.setForeground(0, QtGui.QBrush(theme.qc("curve")))
            self.table.addTopLevelItem(it)
            self.axis_rows[a.name] = it
        self.ch_rows = {}
        self.vec_names = [c.name for c in ds.channels if c.vector]
        for c in ds.channels:
            it = QtWidgets.QTreeWidgetItem([f"● {c.name}", "—"])
            it.setData(0, Qt.ItemDataRole.UserRole, c.name)
            self.table.addTopLevelItem(it)
            self.ch_rows[c.name] = it
        self.axes = ds.axes
        self.inner = ds.axes[-1]
        self.inner_vals = self.inner.values * self.inner.display_scale
        self.outer_axes = ds.axes[:-1]
        self.outer_key = None
        self.last_shot = {}
        self.progress.setMaximum(max(1, len(self.runner.plan) if self.runner else 1))
        self.progress.setValue(0)
        vec = next((c for c in ds.channels if c.vector), None)
        self.show_ch = vec.name if vec else None
        self._setup_axes(ds)

    def _setup_axes(self, ds) -> None:
        vec = next((c for c in ds.channels if c.name == self.show_ch), None)
        if vec is None or vec.x_values is None:
            self.x = None
            self.heat = None
            return
        self.x = np.asarray(vec.x_values)
        self.heat = np.full((len(self.x), len(self.inner_vals)), np.nan)
        span_y = (self.inner_vals[-1] - self.inner_vals[0]) or 1e-9
        self.img.setImage(np.zeros(self.heat.shape))
        self.img.setRect(QtCore.QRectF(self.x[0], self.inner_vals[0], (self.x[-1] - self.x[0]) or 1e-9, span_y))
        self.plot.setLabel("bottom", vec.x_name, vec.x_unit)
        self.plot.setTitle(self.show_ch)
        self.heat_plot.setLabel("bottom", vec.x_name, vec.x_unit)
        self.heat_plot.setLabel("left", self.inner.name, self.inner.display_unit)
        self.for_ds = ds

    def _on_progress(self, p: Dict[str, Any]) -> None:
        self.progress.setMaximum(max(1, p["total"]))
        self.progress.setValue(p["index"])
        eta = p["eta"]
        self.eta.setText("剩餘 計算中" if eta is None else
                         f"剩餘 {int(eta // 3600):d}:{int(eta % 3600 // 60):02d}:{int(eta % 60):02d}")
        disp = p.get("display", {})
        mi = self.runner.plan.multi_index(min(p["index"], p["total"] - 1)) if self.runner else ()
        for k, (name, it) in enumerate(getattr(self, "axis_rows", {}).items()):
            n = len(self.axes[k].values) if k < len(self.axes) else 0
            idx = f"  [{mi[k] + 1}/{n}]" if k < len(mi) else ""
            it.setText(1, f"{disp.get(name, '—')}{idx}")

    def _on_ramp(self, p: Dict[str, Any]) -> None:
        try:
            unit = self.station.parameter(p["target"]).unit or ""
        except Exception:  # noqa: BLE001
            unit = ""
        v = float(p["value"])
        to, rate = p.get("to"), p.get("rate")
        left = ""
        try:
            if to is not None and rate:
                sec = abs(float(to) - v) / float(rate)
                left = f"，剩約 {int(sec // 60)}:{int(sec % 60):02d}" if sec >= 1 else ""
        except (TypeError, ValueError):
            pass
        scale, du = (1e3, "m" + unit) if unit in ("A", "V") and abs(v) < 1 else (1.0, unit)
        dest = f" → {float(to) * scale:.4f}" if to is not None else ""
        self.eta.setText(f"斜坡移動 {p['target']}：{v * scale:.4f}{dest} {du}{left}")

    def _on_shot(self, p: Dict[str, Any]) -> None:
        shot = p["shot"]
        self.last_shot = shot
        self.last_index = p["index"]
        for name, it in getattr(self, "ch_rows", {}).items():
            v = shot.get(name)
            if v is None:
                continue
            arr = np.asarray(v)
            if arr.ndim and arr.size > 1:
                db = 20 * np.log10(np.abs(arr) + 1e-30)
                k = int(np.argmin(db))
                xs = f" @ {self.x[k] / 1e9:.6f} GHz" if self.x is not None and name == self.show_ch and \
                    len(self.x) == len(db) else ""
                it.setText(1, f"最低 {db[k]:.2f} dB{xs}")
            else:
                it.setText(1, f"{complex(arr).real:.6g}" if np.iscomplexobj(arr) else f"{float(arr):.6g}")
        self._draw(shot, p["index"], p.get("setpoints", {}))

    def _draw(self, shot: Dict[str, Any], index: int, setpoints: Dict[str, float]) -> None:
        if self.show_ch is None or self.show_ch not in shot or self.x is None:
            return
        z = np.asarray(shot[self.show_ch])
        key = self.disp.currentData() or "db"
        label, fn = DISPLAY[key]
        y = fn(z)
        self.plot.setLabel("left", label)
        self.curve.setData(self.x, y)
        if self.heat is None or self.runner is None:
            return
        mi = self.runner.plan.multi_index(index)
        j, outer = mi[-1], tuple(mi[:-1])
        if outer != self.outer_key:
            self.outer_key = outer
            self.heat[:] = np.nan
            title = ", ".join(f"{a.name} = {setpoints.get(a.name, 0) * a.display_scale:.6g} {a.display_unit}"
                              for a in self.outer_axes)
            self.heat_plot.setTitle(title)
        if j < self.heat.shape[1] and len(y) == self.heat.shape[0]:
            self.heat[:, j] = y
            finite = self.heat[np.isfinite(self.heat)]
            self.img.setImage(np.nan_to_num(self.heat, nan=float(finite.min()) if finite.size else 0.0),
                              autoLevels=True)

    def _redraw_last(self) -> None:
        if self.last_shot and self.heat is not None:
            self.heat[:] = np.nan
            self.outer_key = None
            self._draw(self.last_shot, getattr(self, "last_index", 0), {})

    def _on_table_click(self, it: QtWidgets.QTreeWidgetItem, _col: int) -> None:
        name = it.data(0, Qt.ItemDataRole.UserRole)
        if name and name in self.vec_names and name != self.show_ch and hasattr(self, "for_ds"):
            self.show_ch = name
            self._setup_axes(self.for_ds)
            self._redraw_last()

    def _on_rollback(self, p: Dict[str, Any]) -> None:
        if self.heat is not None and self.runner is not None:
            mi = self.runner.plan.multi_index(p["index"])
            if tuple(mi[:-1]) == self.outer_key:
                self.heat[:, mi[-1]:] = np.nan

    def _on_finished(self, p: Dict[str, Any]) -> None:
        ds = p.get("dataset")
        self._log(f"結束：{STATE_TEXT.get(p['status'], (p['status'],))[0]}  {ds.summary() if ds else ''}")
        self.finished.emit(p["status"])
        if ds is None or not len(ds) or self.output is None or self.exp is None:
            return
        if p.get("run_id") != self._own_run_id:
            return                   # 不是這個畫面啟動的量測（例如遠端要求的）：由啟動者負責匯出
        if Experiment.needs_review(ds):
            self._log(f"有 {len(ds.retained)} 個保留點；QC 挑選視窗尚未移植，先以最後一筆匯出。")
        exp, out = self.exp, self.output
        start_thread(lambda: self._export(exp, ds, out), name="export")

    def _export(self, exp: Experiment, ds, output) -> None:
        try:
            exp.export(ds, output)
        except Exception as e:  # noqa: BLE001
            self.station.bus.log(f"❌ 匯出失敗：{e}", "error")

    def close_bridge(self) -> None:
        self.bridge.close()


def _local_runner(run_id: Optional[str]):
    for r in list(Runner._instances):
        if r.run_id == run_id:
            return r
    return None
