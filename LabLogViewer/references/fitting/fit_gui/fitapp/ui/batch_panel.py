"""連續擬合結果面板：結果表 + 參數變化圖 + 單片擬合圖"""
import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QAbstractItemView, QTabWidget,
)

from .plots import ParamPlotWidget, FitPlotWidget, AllParamsPlotWidget

X_MODES = [("track", "追蹤頻率 1"), ("track2", "追蹤頻率 2"), ("axis", "掃描軸"), ("index", "切片索引")]
COLS = ["#", "切片", "掃描軸", "追蹤 1", "追蹤 2", "R²", "nfev", "狀態"]


class BatchPanel(QWidget):
    rowSelected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        bar = QHBoxLayout()
        self.cmb_param = QComboBox()
        self.cmb_param.setMinimumWidth(140)
        self.cmb_x = QComboBox()
        for key, label in X_MODES:
            self.cmb_x.addItem(label, key)
        self.chk_err = QCheckBox("誤差棒")
        self.chk_err.setChecked(True)
        self.chk_skip_fixed = QCheckBox("隱藏固定參數")
        self.chk_skip_fixed.setChecked(True)
        self.chk_ok_only = QCheckBox("只顯示成功")
        self.chk_track = QCheckBox("在 2D 圖顯示軌跡")
        self.chk_track.setChecked(True)
        self.chk_live = QCheckBox("即時顯示最新一片")
        self.chk_live.setChecked(True)
        self.btn_to_p0 = QPushButton("此片結果 → 初值")
        self.btn_goto = QPushButton("預覽跳到此片")
        self.btn_export = QPushButton("匯出 CSV")
        self.btn_import = QPushButton("載入 CSV")
        self.btn_clear = QPushButton("清除")
        bar.addWidget(QLabel("Y："))
        bar.addWidget(self.cmb_param)
        bar.addWidget(QLabel("X："))
        bar.addWidget(self.cmb_x)
        for wdg in (self.chk_err, self.chk_ok_only, self.chk_skip_fixed, self.chk_track, self.chk_live):
            bar.addWidget(wdg)
        bar.addStretch()
        for wdg in (self.btn_to_p0, self.btn_goto, self.btn_export, self.btn_import, self.btn_clear):
            bar.addWidget(wdg)
        lay.addLayout(bar)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_sel)

        self.param_plot = ParamPlotWidget()
        self.all_plot = AllParamsPlotWidget()
        self.fit_plot = FitPlotWidget(min_height=460)
        self.param_plot.pointClicked.connect(self.select_row)
        self.all_plot.pointClicked.connect(self.select_row)

        # 參數圖：全部參數（小圖陣列）/ 單一參數
        self.plot_tabs = QTabWidget()
        self.plot_tabs.addTab(self.all_plot, "全部參數")
        self.plot_tabs.addTab(self.param_plot, "單一參數")

        plots = QSplitter(Qt.Orientation.Vertical)
        plots.addWidget(self.plot_tabs)
        plots.addWidget(self.fit_plot)
        plots.setStretchFactor(0, 2)
        plots.setStretchFactor(1, 3)
        plots.setSizes([520, 380])
        main = QSplitter(Qt.Orientation.Horizontal)
        main.addWidget(self.table)
        main.addWidget(plots)
        main.setStretchFactor(0, 1)
        main.setStretchFactor(1, 3)
        main.setSizes([380, 1000])
        lay.addWidget(main)

    # ------------------------------------------------------------------
    def set_params(self, names, units):
        cur = self.cmb_param.currentData()
        self.cmb_param.blockSignals(True)
        self.cmb_param.clear()
        for n, u in zip(names, units):
            self.cmb_param.addItem(f"{n} [{u}]" if u else n, n)
        self.cmb_param.addItem("R² (dB)", "__r2")
        self.cmb_param.addItem("R² (complex)", "__r2c")
        self.cmb_param.addItem("χ²_red", "__chi2")
        i = self.cmb_param.findData(cur)
        self.cmb_param.setCurrentIndex(max(i, 0))
        self.cmb_param.blockSignals(False)

    def set_track_header(self, label, label2=None):
        self.table.setHorizontalHeaderItem(3, QTableWidgetItem(label))
        self.table.setHorizontalHeaderItem(4, QTableWidgetItem(label2 or "追蹤 2"))
        self.table.setColumnHidden(4, label2 is None)

    def set_axis_header(self, label):
        self.table.setHorizontalHeaderItem(2, QTableWidgetItem(label))

    def clear(self):
        self.table.setRowCount(0)
        self.param_plot.clear()
        self.all_plot.clear()
        self.fit_plot.fig.clear()
        self.fit_plot.canvas.draw_idle()

    def add_row(self, rec, track_val, track_val2=None):
        r = self.table.rowCount()
        self.table.insertRow(r)
        status = rec["msg"] or ("OK" if rec["ok"] else "失敗")

        def fmt(v):
            return "" if v is None or not np.isfinite(v) else f"{v:.7g}"
        vals = [str(r), str(rec["idx"]), f"{rec['axis_val']:.6g}", fmt(track_val), fmt(track_val2),
                "" if not np.isfinite(rec["r2"]) else f"{rec['r2']:.5f}",
                str(rec["nfev"]), status]
        color = None if rec["ok"] else (QColor(225, 232, 245) if rec.get("skipped")
                                        else QColor(255, 225, 225))
        for c, v in enumerate(vals):
            it = QTableWidgetItem(v)
            if color is not None:
                it.setBackground(color)
            self.table.setItem(r, c, it)
        return r

    def selected_row(self):
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def select_row(self, r):
        if 0 <= r < self.table.rowCount():
            self.table.selectRow(r)
            self.table.scrollToItem(self.table.item(r, 0))

    def _on_sel(self):
        r = self.selected_row()
        if r is not None:
            self.rowSelected.emit(r)

    def all_mode(self):
        return self.plot_tabs.currentWidget() is self.all_plot
