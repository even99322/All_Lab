"""驅動測試視窗：逐項檢查驅動的 SCPI 是否被儀器接受（邏輯在 labcontrol/diagnostics.py）。"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, pyqtSignal

from ....core.station import Station
from ....core.units import parse_quantity
from ....diagnostics import FAIL, ICON, PASS, SKIP, WARN, TestItem, run_driver_test
from ....paths import lab_path, open_folder
from .. import theme
from ..worker import start_thread

COLORS = {PASS: "ok", WARN: "warn", FAIL: "err", SKIP: "muted"}       # theme 顏色名稱


class _Sig(QtCore.QObject):
    item = pyqtSignal(str, object)
    report = pyqtSignal(object, str)
    finished = pyqtSignal()


class DriverTestDialog(QtWidgets.QDialog):
    def __init__(self, station: Station, names: List[str], parent=None) -> None:
        super().__init__(parent)
        self.station, self.names = station, list(names)
        self.setWindowTitle(f"驅動測試 — {', '.join(names)}")
        self.resize(900, 680)
        self._stop = threading.Event()
        self.reports: List[Path] = []
        self.sig = _Sig()
        self.sig.item.connect(self._on_item)
        self.sig.report.connect(self._on_report)
        self.sig.finished.connect(self._on_finished)

        lay = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "逐項測試驅動：每一步之後讀儀器錯誤佇列（:SYST:ERR?），指令寫錯會標 ✖。"
            "<br><b>預設只讀取</b>，不改變儀器。下面的選項會送出設定指令，請確認後再勾選。")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        box = QtWidgets.QGroupBox("測試項目")
        g = QtWidgets.QGridLayout(box)
        self.c_read = QtWidgets.QCheckBox("讀取：連線、*IDN? 比對、讀取所有參數、trace 清單 / 頻率點（不改變儀器）")
        self.c_read.setChecked(True)
        self.c_read.setEnabled(False)
        self.c_same = QtWidgets.QCheckBox("寫回相同值：把每個可寫參數設成目前的值，驗證設定指令（儀器狀態不變）")
        self.c_acq = QtWidgets.QCheckBox("量測一次：量測儀器觸發一次掃描並讀回資料")
        self.c_step = QtWidgets.QCheckBox("小幅度輸出：電源輸出開啟時 +步進 再回原值（輸出關閉時略過）")
        self.step = QtWidgets.QLineEdit("1 uA")
        self.step.setMaximumWidth(110)
        g.addWidget(self.c_read, 0, 0, 1, 2)
        g.addWidget(self.c_same, 1, 0, 1, 2)
        g.addWidget(self.c_acq, 2, 0, 1, 2)
        g.addWidget(self.c_step, 3, 0)
        g.addWidget(self.step, 3, 1)
        lay.addWidget(box)

        row = QtWidgets.QHBoxLayout()
        self.b_start = QtWidgets.QPushButton("▶ 開始測試")
        self.b_stop = QtWidgets.QPushButton("■ 中止")
        self.b_stop.setEnabled(False)
        self.b_report = QtWidgets.QPushButton("開啟報告")
        self.b_report.setEnabled(False)
        b_folder = QtWidgets.QPushButton("開啟 logs 資料夾")
        for b in (self.b_start, self.b_stop, self.b_report, b_folder):
            row.addWidget(b)
        row.addStretch()
        self.summary = QtWidgets.QLabel()
        row.addWidget(self.summary)
        lay.addLayout(row)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["項目", "值", "說明", "耗時"])
        self.tree.setColumnWidth(0, 260)
        self.tree.setColumnWidth(1, 250)
        self.tree.setColumnWidth(2, 280)
        lay.addWidget(self.tree, 1)
        self.b_start.clicked.connect(self.start)
        self.b_stop.clicked.connect(self._stop.set)
        self.b_report.clicked.connect(lambda: self.reports and open_folder(self.reports[-1]))
        b_folder.clicked.connect(lambda: open_folder(lab_path("logs")))
        self._groups = {}

    def start(self) -> None:
        opts = {"write_same": self.c_same.isChecked(), "acquire": self.c_acq.isChecked(), "output_step": None}
        if self.c_step.isChecked():
            try:
                opts["output_step"] = parse_quantity(self.step.text())
            except Exception as e:  # noqa: BLE001
                QtWidgets.QMessageBox.warning(self, "步進", f"無法解析：{e}")
                return
        risky = [k for k, v in (("寫回相同值", opts["write_same"]), ("量測一次", opts["acquire"]),
                                ("小幅度輸出", opts["output_step"] is not None)) if v]
        if risky and QtWidgets.QMessageBox.question(
                self, "確認", f"會送出設定指令：{'、'.join(risky)}。\n確定儀器可以接受（例如沒有接敏感樣品、磁場可小幅變動）？"
        ) != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.tree.clear()
        self._groups = {}
        self._stop.clear()
        self.b_start.setEnabled(False)
        self.b_stop.setEnabled(True)
        self.summary.setText("測試中…")

        def run():
            try:
                for n in self.names:
                    if self._stop.is_set():
                        break
                    rep = run_driver_test(self.station, n, progress=lambda it, n=n: self.sig.item.emit(n, it),
                                          stop_event=self._stop, **opts)
                    path = rep.save(lab_path("logs"))
                    self.sig.report.emit(rep, str(path))
            finally:
                self.sig.finished.emit()
        start_thread(run, name="driver-test")

    def _group(self, name: str, section: str) -> QtWidgets.QTreeWidgetItem:
        key = (name, section)
        if key not in self._groups:
            top = self._groups.get((name, None))
            if top is None:
                top = QtWidgets.QTreeWidgetItem([name])
                f = top.font(0)
                f.setBold(True)
                top.setFont(0, f)
                self.tree.addTopLevelItem(top)
                top.setExpanded(True)
                self._groups[(name, None)] = top
            g = QtWidgets.QTreeWidgetItem([section])
            top.addChild(g)
            g.setExpanded(True)
            self._groups[key] = g
        return self._groups[key]

    def _on_item(self, name: str, it: TestItem) -> None:
        g = self._group(name, it.section)
        row = QtWidgets.QTreeWidgetItem([f"{ICON[it.status]}  {it.name}", it.value, it.detail,
                                         f"{it.seconds * 1e3:.0f} ms" if it.seconds else ""])
        col = theme.qc(COLORS[it.status])
        row.setForeground(0, QtGui.QBrush(col))
        if it.status in (FAIL, WARN):
            row.setForeground(2, QtGui.QBrush(col))
        row.setToolTip(2, it.detail)
        row.setToolTip(1, it.value)
        g.addChild(row)
        self.tree.scrollToItem(row)

    def _on_report(self, rep, path: str) -> None:
        self.reports.append(Path(path))
        top = self._groups.get((rep.instrument, None))
        if top is not None:
            c = rep.counts()
            top.setText(1, f"✔ {c[PASS]}  ⚠ {c[WARN]}  ✖ {c[FAIL]}  – {c[SKIP]}")
            top.setText(2, f"報告：{path}")
            top.setForeground(1, QtGui.QBrush(theme.qc(COLORS[FAIL] if c[FAIL] else COLORS[PASS])))
        self.b_report.setEnabled(True)

    def _on_finished(self) -> None:
        self.b_start.setEnabled(True)
        self.b_stop.setEnabled(False)
        self.summary.setText(f"完成，報告存在 {lab_path('logs')}" if self.reports else "已中止")
