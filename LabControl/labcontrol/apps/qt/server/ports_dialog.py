"""1.0.10：儀器伺服器「S 參數顯示…」——即時數據要顯示 VNA 的哪些 port 組合。

連線時會用目前的設定把 n×n 個 S 參數掃一次，只顯示有接線的（穿透高於門檻的 Sij 與那些 port 的 Sii）；
這裡可以看每個 S 參數掃描到的大小、手動勾選要顯示哪些、重新掃描，或回到「只顯示有接線的」。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt

from ....core import sparams as sp
from .. import theme
from ..worker import run_bg


class PortVisibilityDialog(QtWidgets.QDialog):
    def __init__(self, backend: Any, name: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.backend, self.name = backend, name
        self.info: Dict[str, Any] = {}
        self.checks: Dict[str, QtWidgets.QCheckBox] = {}
        self.setWindowTitle(f"S 參數顯示 — {name}")
        v = QtWidgets.QVBoxLayout(self)
        self.head = QtWidgets.QLabel("讀取中…")
        self.head.setWordWrap(True)
        v.addWidget(self.head)
        self.grid_box = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(self.grid_box)
        self.grid.setHorizontalSpacing(10)
        v.addWidget(self.grid_box)
        hint = QtWidgets.QLabel("勾選 = 即時數據顯示這個 S 參數。數值 = 連線時用當時設定掃描的 |S| 中位數；"
                                "綠色 = 判斷為有接線。")
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        v.addWidget(hint)
        row = QtWidgets.QHBoxLayout()
        self.b_scan = QtWidgets.QPushButton("重新掃描")
        self.b_scan.setToolTip("用 VNA 目前的設定把全部 S 參數掃一次，重新判斷哪些 port 有接線")
        self.b_scan.clicked.connect(self.rescan)
        self.b_auto = QtWidgets.QPushButton("只顯示有接線的")
        self.b_auto.clicked.connect(lambda: self._save(None))
        self.b_all = QtWidgets.QPushButton("全部勾選")
        self.b_all.clicked.connect(lambda: [c.setChecked(True) for c in self.checks.values()])
        for b in (self.b_scan, self.b_auto, self.b_all):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(lambda: self._save(self.selected()))
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.load()

    def selected(self) -> List[str]:
        return [k for k, c in self.checks.items() if c.isChecked()]

    def _busy(self, on: bool, text: str = "") -> None:
        for b in (self.b_scan, self.b_auto, self.b_all):
            b.setEnabled(not on)
        if text:
            self.head.setText(text)

    def load(self) -> None:
        self._busy(True, "讀取中…（第一次會用目前設定掃描全部 S 參數）")
        run_bg(lambda: self.backend.vna_ports(self.name), self.show_info, self._fail)

    def rescan(self) -> None:
        self._busy(True, "掃描中…")
        run_bg(lambda: self.backend.vna_scan(self.name), self.show_info, self._fail)

    def _fail(self, m: str) -> None:
        self._busy(False, f"❌ {m}")

    def show_info(self, info: Dict[str, Any]) -> None:
        self._busy(False)
        self.info = dict(info or {})
        n = int(self.info.get("ports") or 0)
        params = list(self.info.get("sparams") or sp.sparam_names(n or 2))
        levels = self.info.get("levels") or {}
        wired = set(self.info.get("wired") or [])
        visible = set(self.info.get("visible") or params)
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.checks.clear()
        for j in range(1, n + 1):
            lab = QtWidgets.QLabel(f"激發 {j}")
            lab.setProperty("role", "muted")
            self.grid.addWidget(lab, 0, j, alignment=Qt.AlignmentFlag.AlignCenter)
        for name in params:
            pp = sp.ports_of(name)
            if not pp:
                continue
            if pp[1] == 1:
                lab = QtWidgets.QLabel(f"接收 {pp[0]}")
                lab.setProperty("role", "muted")
                self.grid.addWidget(lab, pp[0], 0)
            lv = levels.get(name)
            c = QtWidgets.QCheckBox(f"{name}" + (f"  {lv:.1f} dB" if lv is not None else ""))
            c.setChecked(name in visible)
            if name in wired:
                c.setStyleSheet(f"color:{theme.c('ok')}; font-weight:600;")
            self.checks[name] = c
            self.grid.addWidget(c, pp[0], pp[1])
        when = ""
        if self.info.get("scanned"):
            import time

            when = time.strftime("%m/%d %H:%M", time.localtime(float(self.info["scanned"])))
        self.head.setText(f"{self.name}：{n} port" + (f"，{when} 掃描" if when else "，還沒掃描")
                          + ("；目前是手動選的顯示" if self.info.get("manual") else "；目前只顯示有接線的"))

    def _save(self, params: Optional[List[str]]) -> None:
        if params is not None and not params:
            self.head.setText("至少要勾一個")
            return
        self._busy(True, "儲存中…")

        def ok(info):
            self.show_info(info)
            self.accept()
        run_bg(lambda: self.backend.vna_set_visible(self.name, params), ok, self._fail)
