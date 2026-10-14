"""QC 挑選視窗（1.0.7，移植自「電磁鐵異步量測工具 v3」的 ReviewSelectionWindow）。

每個保留點（手動步進時勾「保留此點所有暫存」，或「執行」分頁「每點重複量測」> 1）有多筆 trace，在這裡挑要用哪一筆：

    左：保留的點（設定值、已選第幾筆）      中：這一點的每一筆 trace（Mag / Jump / Slope 是否達標、⭐）
    右：Magnitude（dB）、Unwrapped Phase（從 0 起）、IQ 平面（半徑內依象限上色）

判定條件（labcontrol/analysis/qc.py）：Mag 深於、Phase 突變 Jump、變化率 Slope；可以把⭐與全達標的置頂。
量測中按「檢視 / 挑選保留數據」= 提前挑（之後還能改）；量完自動打開 = 最後確認，按「完成 QC」或關閉視窗後才存檔。
挑選結果寫回 PointRecord.selected；⭐ 記在 PointRecord.notes["qc_star"]。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, pyqtSignal

from ....analysis.qc import evaluate_trace, iq_quadrant_classes
from .. import theme

IQ_BRUSH = {"major": (0, 150, 255, 255), "deep_lone": (255, 0, 0, 255), "deep_group": (255, 200, 0, 255),
            "other": (150, 150, 150, 200), "out": (100, 100, 100, 50)}


def qc_channels(ds) -> List[Any]:
    """可以挑的通道：複數向量（VNA trace）優先，其次任何向量通道。"""
    vec = [c for c in ds.channels if c.vector]
    return [c for c in vec if c.complex] + [c for c in vec if not c.complex]


def point_label(ds, rec) -> str:
    parts = []
    for a in ds.axes:
        v = rec.setpoints.get(a.name)
        if v is None:
            continue
        unit = a.display_unit or a.unit
        parts.append(f"{a.name} {float(a.to_display(v)):.6g} {unit}".strip())
    return f"#{rec.index + 1}  " + ("  ".join(parts) if parts else "")


class QCWindow(QtWidgets.QWidget):
    """``final=True``：量完後的最後確認（done 送出後才存檔）；False：量測中提前挑。"""

    done = pyqtSignal(object)              # dataset

    def __init__(self, dataset, final: bool = True, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.ds = dataset
        self.final = final
        self._emitted = False
        self.setObjectName("qcWindow")
        self.setWindowTitle("QC 挑選：確認每個保留點要用的數據（完成後存檔）" if final
                            else "QC 挑選：提前檢視保留的數據")
        self.resize(1400, 880)
        self.records = [r for r in dataset.records if r.retained and len(r.shots) > 1]
        self.selection: Dict[int, int] = {id(r): r.selected % len(r.shots) for r in self.records}
        self.stars: Dict[int, set] = {id(r): set(r.notes.get("qc_star") or []) for r in self.records}
        self.mapping: List[int] = []
        chans = qc_channels(dataset)
        self.channel = chans[0] if chans else None

        v = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("判定條件："))
        self.mag = self._spin(-200, 0, -70, "Mag 深於 ", " dB")
        self.jump = self._spin(0, 360, 50, "突變 Jump > ", "°")
        self.slope = self._spin(0, 360, 50, "變化率 Slope > ", "°")
        self.radius = self._spin(1e-6, 10, 1e-3, "IQ 半徑 ", "", decimals=6)
        for w in (self.mag, self.jump, self.slope, self.radius):
            top.addWidget(w)
        self.sort_top = QtWidgets.QCheckBox("⭐ 與全達標的置頂")
        self.sort_top.setChecked(True)
        self.sort_top.toggled.connect(self.refresh_traces)
        top.addWidget(self.sort_top)
        self.chan_box = QtWidgets.QComboBox()
        for c in chans:
            self.chan_box.addItem(c.name, c.name)
        self.chan_box.setVisible(len(chans) > 1)
        self.chan_box.currentIndexChanged.connect(self._channel_changed)
        top.addWidget(self.chan_box)
        top.addStretch()
        v.addLayout(top)

        split = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        v.addWidget(split, 1)
        self.points = QtWidgets.QListWidget()
        self.points.setObjectName("qcPoints")
        self.points.currentRowChanged.connect(lambda _r: self.refresh_traces())
        self.traces = QtWidgets.QListWidget()
        self.traces.setObjectName("qcTraces")
        self.traces.currentRowChanged.connect(self._trace_changed)
        self.traces.itemDoubleClicked.connect(lambda _i: self.toggle_star())
        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.plot_mag = pg.PlotWidget(title="Magnitude (dB)")
        self.curve_mag = self.plot_mag.plot(pen=pg.mkPen("#f2b705", width=1.5), symbol="o", symbolSize=3)
        self.plot_phase = pg.PlotWidget(title="Unwrapped Phase（從 0 起, °）")
        self.curve_phase = self.plot_phase.plot(pen=pg.mkPen("#e03131", width=1.5), symbol="o", symbolSize=3)
        self.plot_phase.setXLink(self.plot_mag)
        self.plot_iq = pg.PlotWidget(title="IQ 平面")
        self.plot_iq.setLabel("bottom", "I (Real)")
        self.plot_iq.setLabel("left", "Q (Imag)")
        self.plot_iq.showGrid(True, True)
        self.plot_iq.setAspectLocked(True)
        self.scatter = pg.ScatterPlotItem(size=6, pen=None)
        self.plot_iq.addItem(self.scatter)
        for p in (self.plot_mag, self.plot_phase):
            p.showGrid(True, True, 0.25)
        rl.addWidget(self.plot_mag, 1)
        rl.addWidget(self.plot_phase, 1)
        rl.addWidget(self.plot_iq, 1)
        brow = QtWidgets.QHBoxLayout()
        self.b_star = QtWidgets.QPushButton("⭐ 打星號（或雙擊清單）")
        self.b_star.clicked.connect(self.toggle_star)
        brow.addWidget(self.b_star)
        self.b_last = QtWidgets.QPushButton("全部用最後一筆")
        self.b_last.clicked.connect(self.use_last_everywhere)
        brow.addWidget(self.b_last)
        self.b_best = QtWidgets.QPushButton("每點選最佳（⭐ > 全達標 > 最深）")
        self.b_best.clicked.connect(self.pick_best_everywhere)
        brow.addWidget(self.b_best)
        rl.addLayout(brow)
        self.b_done = QtWidgets.QPushButton("💾 完成 QC：用目前的選擇存檔" if final else "💾 套用目前的選擇")
        self.b_done.setObjectName("qcDone")
        self.b_done.setStyleSheet("QPushButton{background:%s;color:white;font-weight:700;padding:10px;border-radius:6px;}"
                                  % ("#2b8a3e" if final else "#1971c2"))
        self.b_done.clicked.connect(self.finish)
        rl.addWidget(self.b_done)
        split.addWidget(self.points)
        split.addWidget(self.traces)
        split.addWidget(right)
        split.setSizes([260, 340, 800])
        self.empty = QtWidgets.QLabel("沒有保留的點（手動步進時勾「保留此點所有暫存」，或「執行」分頁設「每點重複量測」）")
        self.empty.setVisible(not self.records)
        v.addWidget(self.empty)
        self._fill_points()
        if self.records:
            self.points.setCurrentRow(0)
        self._connect_thresholds()

    @staticmethod
    def _spin(lo: float, hi: float, val: float, prefix: str, suffix: str, decimals: int = 1) -> QtWidgets.QDoubleSpinBox:
        s = QtWidgets.QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(decimals)
        s.setValue(val)
        s.setPrefix(prefix)
        s.setSuffix(suffix)
        return s

    def _connect_thresholds(self) -> None:
        for w in (self.mag, self.jump, self.slope, self.radius):
            w.valueChanged.connect(self.refresh_traces)

    # ---- 資料 -------------------------------------------------------------------------
    def _trace(self, rec, i: int) -> Optional[np.ndarray]:
        if self.channel is None:
            return None
        v = rec.shots[i].get(self.channel.name)
        return None if v is None else np.asarray(v)

    def _x(self, n: int) -> np.ndarray:
        x = None if self.channel is None else self.channel.x_values
        if x is not None and len(x) == n:
            return np.asarray(x, dtype=float)
        return np.arange(n, dtype=float)

    def _score(self, rec, i: int):
        t = self._trace(rec, i)
        if t is None or t.size == 0:
            return None
        if not np.iscomplexobj(t):
            t = t.astype(complex)
        return evaluate_trace(t, self.mag.value(), self.jump.value(), self.slope.value())

    def _channel_changed(self, _i: int) -> None:
        name = self.chan_box.currentData()
        self.channel = next((c for c in self.ds.channels if c.name == name), self.channel)
        self.refresh_traces()

    # ---- 清單 -------------------------------------------------------------------------
    def _fill_points(self) -> None:
        row = self.points.currentRow()
        self.points.blockSignals(True)
        self.points.clear()
        for rec in self.records:
            sel = self.selection[id(rec)]
            star = " ⭐" if sel in self.stars[id(rec)] else ""
            self.points.addItem(f"{point_label(self.ds, rec)}\n    用第 {sel + 1} / {len(rec.shots)} 筆{star}")
        self.points.blockSignals(False)
        if 0 <= row < self.points.count():
            self.points.setCurrentRow(row)

    def current_record(self):
        row = self.points.currentRow()
        return self.records[row] if 0 <= row < len(self.records) else None

    def refresh_traces(self, *_a) -> None:
        rec = self.current_record()
        self.traces.blockSignals(True)
        self.traces.clear()
        self.mapping = []
        if rec is None:
            self.traces.blockSignals(False)
            return
        info: List[Tuple[int, Any, bool]] = []
        for i in range(len(rec.shots)):
            info.append((i, self._score(rec, i), i in self.stars[id(rec)]))
        if self.sort_top.isChecked():
            info.sort(key=lambda t: (t[2], bool(t[1] and t[1].all_pass), t[1].n_pass if t[1] else 0, -t[0]),
                      reverse=True)
        ok = theme.c("ok") if hasattr(theme, "c") else "#2b8a3e"
        for i, sc, starred in info:
            self.mapping.append(i)
            mark = (lambda b: "✅" if b else "❌")
            text = f"{'⭐ ' if starred else ''}Tr {i + 1:03d}"
            if sc is not None:
                text += f" | Mag {mark(sc.mag_pass)} | Jump {mark(sc.prom_pass)} | Slope {mark(sc.slope_pass)}"
            item = QtWidgets.QListWidgetItem(text)
            if sc is not None and sc.all_pass:
                item.setForeground(QtGui.QColor(ok))
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            elif sc is not None and sc.n_pass:
                item.setForeground(QtGui.QColor("#1971c2"))
            elif sc is not None:
                item.setForeground(QtGui.QColor("#e03131"))
            self.traces.addItem(item)
        sel = self.selection[id(rec)]
        row = self.mapping.index(sel) if sel in self.mapping else 0
        self.traces.setCurrentRow(row)
        self.traces.blockSignals(False)
        self._trace_changed(row)

    def _trace_changed(self, row: int) -> None:
        rec = self.current_record()
        if rec is None or not (0 <= row < len(self.mapping)):
            return
        i = self.mapping[row]
        self.selection[id(rec)] = i
        t = self._trace(rec, i)
        if t is None:
            return
        z = t if np.iscomplexobj(t) else t.astype(complex)
        x = self._x(len(z))
        mag = 20 * np.log10(np.maximum(np.abs(z), 1e-15))
        ph = np.degrees(np.unwrap(np.angle(z)))
        ph = ph - np.min(ph)
        self.curve_mag.setData(x, mag)
        self.curve_phase.setData(x, ph)
        sc = self._score(rec, i)
        if sc is not None:
            self.plot_mag.setTitle(f"Magnitude — 最深 {sc.min_mag:.2f} dB")
            self.plot_phase.setTitle(f"Phase — Jump {sc.max_prominence:.1f}°，Slope {sc.max_slope:.1f}°")
        cls = iq_quadrant_classes(z, self.radius.value())["class"]
        self.scatter.setData(x=z.real, y=z.imag, brush=[pg.mkBrush(*IQ_BRUSH[c]) for c in cls])
        r = self.radius.value()
        if np.any(cls != "out"):
            self.plot_iq.setXRange(-r, r)
            self.plot_iq.setYRange(-r, r)
        else:                                  # 整條都在半徑外：看全部
            self.plot_iq.autoRange()
        self.b_star.setText("取消星號" if i in self.stars[id(rec)] else "⭐ 打星號（或雙擊清單）")
        self._update_point_text(rec)

    def _update_point_text(self, rec) -> None:
        row = self.records.index(rec)
        it = self.points.item(row)
        if it is not None:
            sel = self.selection[id(rec)]
            star = " ⭐" if sel in self.stars[id(rec)] else ""
            it.setText(f"{point_label(self.ds, rec)}\n    用第 {sel + 1} / {len(rec.shots)} 筆{star}")

    # ---- 動作 -------------------------------------------------------------------------
    def toggle_star(self) -> None:
        rec = self.current_record()
        row = self.traces.currentRow()
        if rec is None or not (0 <= row < len(self.mapping)):
            return
        i = self.mapping[row]
        s = self.stars[id(rec)]
        s.symmetric_difference_update({i})
        self.selection[id(rec)] = i
        self.refresh_traces()

    def use_last_everywhere(self) -> None:
        for rec in self.records:
            self.selection[id(rec)] = len(rec.shots) - 1
        self._fill_points()
        self.refresh_traces()

    def best_index(self, rec) -> int:
        stars = self.stars[id(rec)]
        scores = [(i, self._score(rec, i)) for i in range(len(rec.shots))]

        def key(t):
            i, sc = t
            return (i in stars, bool(sc and sc.all_pass), sc.n_pass if sc else 0, -(sc.min_mag if sc else 0.0), i)
        return max(scores, key=key)[0]

    def pick_best_everywhere(self) -> None:
        for rec in self.records:
            self.selection[id(rec)] = self.best_index(rec)
        self._fill_points()
        self.refresh_traces()

    def apply(self) -> None:
        for rec in self.records:
            rec.select(self.selection[id(rec)])
            rec.notes["qc_star"] = sorted(self.stars[id(rec)])

    def finish(self) -> None:
        self.apply()
        self._emitted = True
        self.done.emit(self.ds)
        self.close()

    def closeEvent(self, ev) -> None:  # noqa: N802
        if not self._emitted:                # 直接關掉 = 用目前的選擇（與舊工具相同；量完的資料一定會存）
            self.apply()
            self._emitted = True
            self.done.emit(self.ds)
        super().closeEvent(ev)
