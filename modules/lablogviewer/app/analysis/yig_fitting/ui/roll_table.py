"""參數滾動追蹤表：任意參數（如 phi）以「上一片擬合值 ± 範圍」作為下一片的初值與邊界"""
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView

from ..core.formula import parse_num

C_ON, C_NAME, C_UNIT, C_HALF, C_EXT = range(5)


def default_half(unit):
    u = (unit or "").strip().lower()
    return {"rad": "pi/4", "deg": "45", "ghz": "0.005", "mhz": "5", "khz": "500",
            "hz": "5e5", "ns": "5"}.get(u, "0.2")


class RollTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(0, 5, parent)
        self.setHorizontalHeaderLabels(["Track", "Parameter", "Unit", "± Range", "Extrapolate"])
        self.verticalHeader().setVisible(False)
        hh = self.horizontalHeader()
        for c in (C_ON, C_UNIT, C_EXT):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        for c in (C_NAME, C_HALF):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        self.setMinimumHeight(170)
        self.setToolTip("For tracked parameters, the next initial value uses the previous fit (or linear extrapolation).\n"
                        "Bounds are initial value ± range. Expressions such as pi/4 are accepted.")

    @staticmethod
    def _check(on):
        it = QTableWidgetItem()
        it.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        return it

    @staticmethod
    def _ro(text):
        it = QTableWidgetItem(text)
        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return it

    def set_params(self, names, units, saved=None):
        """saved: {name: dict(on, half, extrap)}；沒有記錄的參數用預設值（不追蹤）"""
        saved = saved or {}
        self.blockSignals(True)
        self.setRowCount(len(names))
        for r, (n, u) in enumerate(zip(names, units)):
            s = saved.get(n, {})
            self.setItem(r, C_ON, self._check(s.get("on", False)))
            self.setItem(r, C_NAME, self._ro(n))
            self.setItem(r, C_UNIT, self._ro(u))
            self.setItem(r, C_HALF, QTableWidgetItem(str(s.get("half", default_half(u)))))
            self.setItem(r, C_EXT, self._check(s.get("extrap", False)))
        self.blockSignals(False)

    def update_units(self, names, units):
        for r in range(self.rowCount()):
            n = self.item(r, C_NAME).text()
            if n in names:
                self.item(r, C_UNIT).setText(units[names.index(n)])

    def state(self):
        """{name: dict(on, half(文字), extrap)}"""
        out = {}
        for r in range(self.rowCount()):
            out[self.item(r, C_NAME).text()] = dict(
                on=self.item(r, C_ON).checkState() == Qt.CheckState.Checked,
                half=self.item(r, C_HALF).text().strip(),
                extrap=self.item(r, C_EXT).checkState() == Qt.CheckState.Checked)
        return out

    def config(self):
        """回傳啟用中的設定 {name: dict(half(float), extrap)}；範圍無法解析時丟出 ValueError"""
        cfg = {}
        for n, s in self.state().items():
            if not s["on"]:
                continue
            try:
                half = abs(parse_num(s["half"]))
            except Exception as e:
                raise ValueError(f"Could not parse the rolling-tracking range for {n}: {e}")
            if not np.isfinite(half) or half <= 0:
                raise ValueError(f"The rolling-tracking range for {n} must be greater than zero.")
            cfg[n] = dict(half=half, extrap=s["extrap"])
        return cfg

    def set_check(self, name, on=True):
        for r in range(self.rowCount()):
            if self.item(r, C_NAME).text() == name:
                self.item(r, C_ON).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
