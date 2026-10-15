"""量測監控（對應 Labber 的 Measurement 視窗；0.0.25 起是開始量測後彈出的獨立視窗）。

  * 控制：開始、暫停 / 繼續、退回上一點、停止；手動步進模式（單次 / 循環 / 記錄並下一點）。
  * 左側通道表：掃描通道的目前值與進度、量測通道的最新結果；點量測通道切換圖上顯示的通道。
  * 右側：最新曲線與 2D 影像（最內圈 × 頻率；外圈每換一個值重畫）。1.0.10：曲線跟即時數據一樣是 VNA 式的
    trace / diagram——每條 trace = 通道 ＋ 格式（dB Mag、Phase、Smith、Polar、Delay…），拖 trace 名稱疊圖或放旁邊；
    影像畫「目前的 trace」（點 trace 名稱或左邊的通道換）。
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
from ..live.traces import FORMATS, FormatBar, TracePlots, is_polar, key_fmt, key_src, normalize_groups, tkey, view_data
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
    #: 1.0.1：匯出執行緒存好檔 → 回到主執行緒才叫讀檔程式（本機連線要在 Qt 的主執行緒）
    open_reader = pyqtSignal(object, bool)

    def __init__(self, station: Station, parent=None, bus=None) -> None:
        super().__init__(parent)
        self.station = station
        self.open_reader.connect(lambda paths, split: open_in_lablogviewer(self.station, paths, split))
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
        self.bridge.on("manual.waiting", lambda p: self._show_loop(bool(p.get("loop"))))      # 1.0.10：預設循環
        self.bridge.on("manual.loop", lambda p: self._show_loop(bool(p.get("loop"))))
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
        self.b_loop = QtWidgets.QPushButton("🔁 循環量測")
        self.b_loop.setCheckable(True)
        self.b_loop.setToolTip("手動步進時每一點預設一直重複量測（settings.yaml run_defaults.manual_loop）；按這裡停止 / 再開始")
        self.chk_retain = QtWidgets.QCheckBox("保留此點所有暫存")
        self.b_accept = QtWidgets.QPushButton("✅ 記錄並下一點")
        self.lbl_shots = QtWidgets.QLabel("暫存 0 筆")
        self.b_review = QtWidgets.QPushButton("👀 檢視 / 挑選保留數據")
        self.b_review.setToolTip("量測中提前看保留點的每一筆 trace、打星號、選要用哪一筆（量完還會再確認一次）")
        self.b_review.clicked.connect(self.open_review)
        for w in (self.b_once, self.b_loop, self.chk_retain, self.b_accept, self.lbl_shots):
            mr.addWidget(w)
        mr.addStretch()
        row.insertWidget(5, self.b_review)           # 控制列（手動步進或「每點重複量測」都用得到）
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
        # 1.0.10：跟即時數據一樣的 trace / diagram
        self.traces = TracePlots()
        fmt0 = {"real": "re", "imag": "im"}.get(str(setting("monitor.trace_display", "db")), "db")
        self.traces.set_groups([[fmt0 if fmt0 in FORMATS else "db"]])
        self.fmtbar = FormatBar(self.traces, lambda: self.show_ch or "")
        self.fmtbar.setToolTip("點圖上的 trace 名稱選一條，這排按鈕改它的格式；＋ Trace 加一條（可以拖到別張圖）")
        self.disp = self.fmtbar                       # 相容
        tb.addWidget(self.fmtbar)
        tb.addStretch()
        self.b_log = QtWidgets.QToolButton()
        self.b_log.setText("訊息")
        self.b_log.setCheckable(True)
        self.b_log.setChecked(True)
        tb.addWidget(self.b_log)
        pv.addLayout(tb)
        pg.setConfigOptions(antialias=True)
        self.traces.active_changed.connect(self._active_trace)
        self.traces.groups_changed.connect(self._save_traces)
        self.traces.message.connect(self._log)
        self.heat_plot = theme.style_plot(pg.PlotWidget())
        self.img = pg.ImageItem()
        try:
            self.img.setColorMap(pg.colormap.get(setting("monitor.colormap", "viridis")))
        except Exception:  # noqa: BLE001
            self.img.setColorMap(pg.colormap.get("viridis"))
        self.heat_plot.addItem(self.img)
        vs = QtWidgets.QSplitter(Qt.Orientation.Vertical)
        vs.addWidget(self.traces)
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
        self.b_line.toggled.connect(self.traces.setVisible)
        self.b_img.toggled.connect(self.heat_plot.setVisible)
        self.b_img.toggled.connect(lambda on: on and self.refill_heat())   # 1.0.2：再打開時補回已量的內容
        self.b_log.toggled.connect(self.log.setVisible)
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

    def _show_loop(self, on: bool) -> None:
        """按鈕跟著量測端的循環狀態（每一點一進來預設就在循環）。"""
        if not self.manual_row.isVisible() and self.runner is not None and getattr(self.runner, "manual", False):
            self.chk_manual.blockSignals(True)
            self.chk_manual.setChecked(True)
            self.chk_manual.blockSignals(False)
            self.manual_row.setVisible(True)
        self.b_loop.blockSignals(True)
        self.b_loop.setChecked(on)
        self.b_loop.setText("⏹ 停止循環" if on else "🔁 循環量測")
        self.b_loop.blockSignals(False)

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
        self._restore_traces()
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
        self.x_unit = vec.x_unit or "Hz"
        self.heat_plot.setTitle(self.show_ch)
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
        data = {n: np.asarray(shot[n]) for n in self.vec_names if n in shot}
        title = "　".join(f"{k} = {v * a.display_scale:.6g} {a.display_unit}"
                         for a in getattr(self, "axes", []) for k, v in setpoints.items() if k == a.name)
        self.traces.update_data(title, self.x, data, getattr(self, "x_unit", "Hz"))
        y = self._heat_fn()(np.asarray(shot[self.show_ch]))
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
        if self.last_shot and self.heat is not None and self.show_ch in self.last_shot:
            self.heat[:] = np.nan
            self.outer_key = None
            self._draw(self.last_shot, getattr(self, "last_index", 0), {})
            self.refill_heat()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self.refill_heat()

    def refill_heat(self) -> None:
        """1.0.2：熱圖從量測數據重畫（目前這一段外圈的所有點）——關掉 / 隱藏後再打開、換顯示方式時不會只剩最新幾條。"""
        runner, heat = self.runner, self.heat
        ds = getattr(runner, "dataset", None) if runner is not None else None
        if ds is None or heat is None or self.x is None or not len(ds) or self.show_ch is None:
            return
        try:
            plan = runner.plan
            fn = self._heat_fn()
            cur = self.outer_key
            if cur is None:
                cur = tuple(plan.multi_index(ds.records[-1].index)[:-1])
            heat[:] = np.nan
            for r in list(ds.records):
                mi = plan.multi_index(r.index)
                if tuple(mi[:-1]) != cur or self.show_ch not in r.data:
                    continue
                y = fn(np.asarray(r.data[self.show_ch]))
                if mi[-1] < heat.shape[1] and len(y) == heat.shape[0]:
                    heat[:, mi[-1]] = y
            self.outer_key = cur
            finite = heat[np.isfinite(heat)]
            if finite.size:
                self.img.setImage(np.nan_to_num(heat, nan=float(finite.min())), autoLevels=True)
                self.heat_plot.getPlotItem().getViewBox().autoRange()
        except Exception:  # noqa: BLE001 — 只是畫面
            pass

    # ---- 1.0.10：trace（跟即時數據一樣）-------------------------------------------------
    @property
    def plot(self):
        """相容：第一張圖的 PlotWidget。"""
        cells = self.traces.canvases()
        return cells[0].pw if cells else None

    def _heat_fn(self):
        """影像畫目前的 trace 的格式（Polar / Smith 沒辦法畫成影像 → dB）。"""
        k = self.traces.active
        fmt = key_fmt(k) if k and not is_polar(k) else "db"
        x = self.x

        def fn(z):
            return view_data(fmt, x if x is not None and len(x) == np.size(z) else np.arange(np.size(z)), z)[1]
        return fn

    def _ui(self):
        from ..live.vna_panel import _UIState

        return _UIState()

    def _restore_traces(self) -> None:
        """量測開始：trace 的來源是這次的量測通道；上次的排法（同名通道）照用。"""
        names = list(self.vec_names)
        self.traces.set_sources(names)
        saved = self._ui().load().get("monitor_tgroups") or []
        groups = [[k for k in g if isinstance(k, str) and key_src(k) in names] for g in saved if isinstance(g, list)]
        groups = normalize_groups([g for g in groups if g])
        if not groups and self.show_ch:
            cur = self.traces.views
            fmts = [key_fmt(k) for k in cur] or ["db"]
            groups = [[tkey(self.show_ch, fmts[0])]] + [[tkey(self.show_ch, f)] for f in fmts[1:]]
        if groups:
            self.traces.set_groups(groups)
            src = key_src(self.traces.active)
            if src in names:
                self.show_ch = src

    def _save_traces(self, groups) -> None:
        if all(key_src(k) for g in groups for k in g):
            self._ui().save(monitor_tgroups=[list(g) for g in groups])

    def _active_trace(self, k: str) -> None:
        """換了目前的 trace：影像跟著畫它的通道與格式。"""
        src = key_src(k) if k else ""
        if src and src in self.vec_names and src != self.show_ch and hasattr(self, "for_ds"):
            self.show_ch = src
            self._setup_axes(self.for_ds)
        if self.heat is not None:
            self._redraw_last()

    def _on_table_click(self, it: QtWidgets.QTreeWidgetItem, _col: int) -> None:
        """點左邊的量測通道 = 目前的 trace 改畫這個通道（像 VNA 的 Meas 鍵）。"""
        name = it.data(0, Qt.ItemDataRole.UserRole)
        if not name or name not in self.vec_names or not hasattr(self, "for_ds"):
            return
        k = self.traces.active
        if k and key_src(k) != name:
            self.traces.retype(k, src=name)
        elif name != self.show_ch:
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
        exp, out = self.exp, self.output
        if Experiment.needs_review(ds) and any(len(r.shots) > 1 for r in ds.retained):
            self._log(f"有 {len(ds.retained)} 個保留點：在 QC 視窗挑好每一點要用的數據後存檔")
            self._final_review(ds, lambda d: start_thread(lambda: self._export(exp, d, out), name="export"))
            return
        start_thread(lambda: self._export(exp, ds, out), name="export")

    # ---- QC 挑選（1.0.7）------------------------------------------------------------
    def open_review(self) -> None:
        """量測中提前挑（不存檔；量完會再開一次最後確認）。"""
        ds = getattr(self.runner, "dataset", None) if self.runner is not None else None
        if ds is None or not any(r.retained and len(r.shots) > 1 for r in ds.records):
            self._log("ℹ 目前沒有保留多筆數據的點（手動步進勾「保留此點所有暫存」，或「執行」分頁設「每點重複量測」）")
            return
        from .qc_window import QCWindow

        self._qc = QCWindow(ds, final=False, parent=self.window())
        self._qc.show()

    def _final_review(self, ds, then) -> None:
        from .qc_window import QCWindow

        w = QCWindow(ds, final=True, parent=self.window())
        w.done.connect(then)
        self._qc = w
        w.show()
        w.raise_()

    def _export(self, exp: Experiment, ds, output) -> None:
        try:
            paths = exp.export(ds, output)
        except Exception as e:  # noqa: BLE001
            self.station.bus.log(f"❌ 匯出失敗：{e}", "error")
            return
        try:
            self.open_reader.emit(list(paths or []), bool(output.split_by))
        except RuntimeError:                       # 視窗已經關了
            pass

    def close_bridge(self) -> None:
        self.bridge.close()


def open_in_lablogviewer(station, paths, split: bool) -> None:
    """0.0.31：量完存好檔 → 用讀檔程式開啟（雙層分檔不開；settings lablogviewer.open_after_measure）。"""
    from ....integrations import lablogviewer

    try:
        r = lablogviewer.after_measure(list(paths or []), split)
    except Exception as e:  # noqa: BLE001
        station.bus.log(f"⚠ 無法用讀檔程式開啟：{e}", "warning")
        return
    if r == "missing":
        station.bus.log("ℹ 找不到讀檔程式（LabLogViewer）：從平台下載頁安裝後，量完會自動開啟數據", "info")
    elif r:
        station.bus.log("📖 已用讀檔程式開啟這次的數據")


def _local_runner(run_id: Optional[str]):
    for r in list(Runner._instances):
        if r.run_id == run_id:
            return r
    return None
