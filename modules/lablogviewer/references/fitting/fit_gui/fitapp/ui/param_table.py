"""參數表元件"""
import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView

from ..core.formula import parse_num

COL_NAME, COL_UNIT, COL_P0, COL_LO, COL_HI, COL_FIX, COL_FIT, COL_ERR = range(8)
HEADERS = ["參數", "單位", "初值", "下界", "上界", "固定", "擬合值", "標準誤差"]


class ParamTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(0, len(HEADERS), parent)
        self.setHorizontalHeaderLabels(HEADERS)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for c in (COL_UNIT, COL_FIX):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.verticalHeader().setVisible(False)

    @staticmethod
    def _ro(text=""):
        it = QTableWidgetItem(text)
        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return it

    def names(self):
        return [self.item(r, COL_NAME).text() for r in range(self.rowCount())]

    def units(self):
        return [self.item(r, COL_UNIT).text().strip() for r in range(self.rowCount())]

    def set_params(self, names, units=None):
        units = units or {}
        self.setRowCount(len(names))
        for r, n in enumerate(names):
            self.setItem(r, COL_NAME, self._ro(n))
            self.setItem(r, COL_UNIT, QTableWidgetItem(units.get(n, "")))
            for c in (COL_P0, COL_LO, COL_HI):
                self.setItem(r, c, QTableWidgetItem(""))
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Unchecked)
            self.setItem(r, COL_FIX, chk)
            self.setItem(r, COL_FIT, self._ro())
            self.setItem(r, COL_ERR, self._ro())

    def rows(self):
        """回傳原始文字 [dict(name, unit, p0, lo, hi, fixed), ...]"""
        return [dict(name=self.item(r, COL_NAME).text(),
                     unit=self.item(r, COL_UNIT).text().strip(),
                     p0=self.item(r, COL_P0).text(),
                     lo=self.item(r, COL_LO).text(),
                     hi=self.item(r, COL_HI).text(),
                     fixed=self.item(r, COL_FIX).checkState() == Qt.CheckState.Checked)
                for r in range(self.rowCount())]

    def set_rows(self, rows):
        """rows: {name: dict(unit, p0, lo, hi, fixed)}；缺少的欄位保持不變"""
        for r, n in enumerate(self.names()):
            p = rows.get(n)
            if not p:
                continue
            for key, col in (("unit", COL_UNIT), ("p0", COL_P0), ("lo", COL_LO), ("hi", COL_HI)):
                if key in p and p[key] is not None:
                    self.item(r, col).setText(str(p[key]))
            if "fixed" in p:
                self.item(r, COL_FIX).setCheckState(
                    Qt.CheckState.Checked if p["fixed"] else Qt.CheckState.Unchecked)

    def values(self):
        """解析並驗證，回傳 (names, units, p0, lo, hi, fixed)"""
        names, units, p0, lo, hi, fixed = [], [], [], [], [], []
        for row in self.rows():
            n, fx = row["name"], row["fixed"]
            try:
                v = parse_num(row["p0"])
                l = parse_num(row["lo"]) if row["lo"].strip() else -np.inf
                h = parse_num(row["hi"]) if row["hi"].strip() else np.inf
            except Exception as e:
                raise ValueError(f"參數 {n} 的數值無法解析：{e}")
            if not fx and not (l < h):
                raise ValueError(f"參數 {n}：下界必須小於上界（若要固定請勾選「固定」）")
            if not fx and not (l <= v <= h):
                raise ValueError(f"參數 {n}：初值 {v} 不在邊界 [{l}, {h}] 內")
            names.append(n); units.append(row["unit"])
            p0.append(v); lo.append(l); hi.append(h); fixed.append(fx)
        return (names, units, np.array(p0, float), np.array(lo, float),
                np.array(hi, float), np.array(fixed, bool))

    def set_guess(self, guess):
        for r, n in enumerate(self.names()):
            if n not in guess:
                continue
            p0, lo, hi = guess[n]
            self.item(r, COL_P0).setText(f"{p0:.10g}")
            self.item(r, COL_LO).setText("" if np.isneginf(lo) else f"{lo:.10g}")
            self.item(r, COL_HI).setText("" if np.isposinf(hi) else f"{hi:.10g}")

    def set_p0(self, values):
        for r, v in enumerate(values):
            if r < self.rowCount() and np.isfinite(v):
                self.item(r, COL_P0).setText(f"{v:.10g}")

    def set_fit(self, params, errors):
        for r, (v, e) in enumerate(zip(params, errors)):
            self.item(r, COL_FIT).setText(f"{v:.8g}")
            self.item(r, COL_ERR).setText("固定" if not np.isfinite(e) else f"{e:.3g}")

    def clear_fit(self):
        for r in range(self.rowCount()):
            self.item(r, COL_FIT).setText("")
            self.item(r, COL_ERR).setText("")
