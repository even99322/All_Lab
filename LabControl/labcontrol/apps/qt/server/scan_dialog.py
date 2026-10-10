"""掃描 VISA 資源：列出電腦看得到的所有儀器，送 *IDN? 辨識並建議驅動；選一台直接新增。"""
from __future__ import annotations

from typing import Optional

from PyQt6 import QtGui, QtWidgets
from PyQt6.QtCore import Qt

from ....core.station import Station
from .. import theme
from ..worker import run_bg


class ScanDialog(QtWidgets.QDialog):
    def __init__(self, station: Station, parent=None) -> None:
        super().__init__(parent)
        self.station = station
        self.chosen: Optional[dict] = None
        self.setWindowTitle("掃描 VISA 資源")
        self.resize(900, 420)
        lay = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        self.serial = QtWidgets.QCheckBox("也對序列埠（ASRL）送 *IDN?")
        b = QtWidgets.QPushButton("重新掃描")
        row.addWidget(self.serial)
        row.addStretch()
        row.addWidget(b)
        lay.addLayout(row)
        self.table = QtWidgets.QTreeWidget()
        self.table.setHeaderLabels(["位址", "*IDN?", "建議驅動", "狀態"])
        self.table.setRootIsDecorated(False)
        for i, w in enumerate((300, 300, 150)):
            self.table.setColumnWidth(i, w)
        lay.addWidget(self.table, 1)
        self.msg = QtWidgets.QLabel()
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                        | QtWidgets.QDialogButtonBox.StandardButton.Close)
        bb.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("新增選取的儀器…")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        b.clicked.connect(self.scan)
        self.table.itemDoubleClicked.connect(lambda *_: self._ok())
        self.scan()

    def scan(self) -> None:
        from ....diagnostics import scan_resources

        self.table.clear()
        self.msg.setText("掃描中（唯讀：只送 *IDN?）…")
        used = {str(i.options.get("address", "")): n for n, i in self.station.instruments.items()}
        inc = self.serial.isChecked()

        def done(res):
            for r in res:
                state = f"已在清單（{used[r.address]}）" if r.address in used else ("無回應" if r.error else "")
                it = QtWidgets.QTreeWidgetItem([r.address, r.idn or r.error, ", ".join(r.suggested), state])
                it.setData(0, Qt.ItemDataRole.UserRole, {"address": r.address,
                                                         "driver": r.suggested[0] if r.suggested else "",
                                                         "label": r.idn.split(",")[1].strip() if r.idn.count(",") >= 2
                                                         else ""})
                if r.address in used:
                    for c in range(4):
                        it.setForeground(c, QtGui.QBrush(theme.qc("muted")))
                self.table.addTopLevelItem(it)
            self.msg.setText(f"找到 {len(res)} 個資源。雙擊或按「新增選取的儀器…」加入儀器清單。" if res else
                             "沒有找到 VISA 資源（確認儀器已開機、USB / 網路已連接）。")

        def fail(m):
            self.msg.setText(f"<span style='color:{theme.c('err')}'>❌ {m}</span><br>請安裝 NI-VISA / Keysight IO Libraries；"
                             "或 pip install pyvisa-py 並在 settings.yaml 設 server.visa_backend: \"@py\"。")
        run_bg(lambda: scan_resources(identify=True, include_serial=inc), done, fail)

    def _ok(self) -> None:
        it = self.table.currentItem()
        if it is None:
            return
        self.chosen = it.data(0, Qt.ItemDataRole.UserRole)
        self.accept()
