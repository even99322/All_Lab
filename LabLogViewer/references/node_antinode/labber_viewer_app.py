# -*- coding: utf-8 -*-
"""
labber_viewer_app.py

PyQt6 + matplotlib 的 Labber HDF5 資料檢視器。

功能對應原始 labber_toolkit.py / read.ipynb：
    1. 讀取單一 Labber HDF5 檔案 (load_labber_data)
    2. 讀取「量測檔 + 背景檔」並執行去背 (get_debackgrounded_data, 模式 - 或 /)
    3. 選擇 S 參數 / Step 軸 (Current, Temperature ...)，繪製 2D pcolormesh 熱圖
    4. 頻率範圍裁切
    5. 點擊熱圖任一列 -> 自動顯示該 Step 值對應的 1D 頻率-幅值切片
    6. IQ 平面圖 (在指定中心頻率 + 視窗內，畫出 -I vs Q)
    7. 共振節點 (node) 自動偵測：線性擬合軌跡 + 動態視窗峰值搜尋 (對應 read.ipynb 進階分析區塊)
    8. 匯出目前圖片 (PNG) / 匯出目前切片數據 (CSV)

執行方式：
    python labber_viewer_app.py
"""

import os
import sys
import csv
import traceback

import numpy as np

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QComboBox, QCheckBox, QDoubleSpinBox,
    QSpinBox, QPlainTextEdit, QScrollArea, QTabWidget, QFileDialog, QMessageBox,
    QSplitter, QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy,
)

import matplotlib
import matplotlib.font_manager as fm
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT

import labber_core as LT


def _setup_cjk_font():
    """讓 matplotlib 找到可顯示中文的字型，避免圖上中文字變成方框。"""
    extra_paths = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "C:/Windows/Fonts/msjh.ttc",
        "C:/Windows/Fonts/msyh.ttc",
    ]
    for p in extra_paths:
        try:
            if os.path.exists(p):
                fm.fontManager.addfont(p)
        except Exception:
            pass

    candidates = [
        "Noto Sans CJK TC", "Noto Sans CJK SC", "Noto Sans CJK JP",
        "Microsoft JhengHei", "Microsoft YaHei", "PingFang TC", "PingFang SC",
        "Heiti TC", "WenQuanYi Zen Hei", "SimHei", "Arial Unicode MS",
    ]
    available = {f.name for f in fm.fontManager.ttflist}
    chosen = [c for c in candidates if c in available]
    if chosen:
        matplotlib.rcParams["font.sans-serif"] = chosen + list(matplotlib.rcParams.get("font.sans-serif", []))
        matplotlib.rcParams["font.family"] = "sans-serif"
    matplotlib.rcParams["axes.unicode_minus"] = False


_setup_cjk_font()

try:
    from scipy.signal import find_peaks
    HAS_SCIPY = True
except Exception:
    HAS_SCIPY = False


COLORMAPS = ["coolwarm", "viridis", "jet", "plasma", "magma", "inferno", "cividis", "RdBu_r", "turbo"]


# ==========================================================
#  Matplotlib 畫布小工具
# ==========================================================
class PlotPanel(QWidget):
    """包含一個 Figure + NavigationToolbar 的可重用畫布元件"""

    def __init__(self, nrows=1, ncols=1, figsize=(6, 5), parent=None):
        super().__init__(parent)
        self.fig = Figure(figsize=figsize, tight_layout=True)
        if nrows * ncols == 1:
            self.axes = [self.fig.add_subplot(111)]
        else:
            axs = self.fig.subplots(nrows, ncols)
            self.axes = list(np.atleast_1d(axs).flatten())
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas)

    def ax(self, i=0):
        return self.axes[i]

    def draw(self):
        self.canvas.draw_idle()

    def clear(self):
        for a in self.axes:
            a.clear()


# ==========================================================
#  主視窗
# ==========================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Labber HDF5 檢視器")
        self.resize(1500, 950)

        # ---------------- 資料狀態 ----------------
        self.data_raw = None          # labber_core 回傳的原始 dict
        self.is_2d = False
        self.freq_full = None         # 原始頻率軸 (未裁切)
        self.freq = None              # 目前 (裁切後) 頻率軸
        self.step_name = None
        self.step_values = None       # 目前選擇的 step 軸數值 (1D)
        self.s_name = None
        self.S_full = None            # 目前選擇 S 參數，未裁切；2D: shape (n_step, n_freq)；1D: shape (n_freq,)
        self.S_crop = None            # 裁切後
        self.crop_idx = (0, None)     # (start_idx, stop_idx) 對應 freq_full
        self.current_step_idx = 0
        self.node_result = None

        self._build_ui()

    # -------------------------------------------------------
    #  UI 建構
    # -------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # ---------- 左側：控制面板 ----------
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_widget.setMaximumWidth(430)
        left_widget.setMinimumWidth(380)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        controls = QWidget()
        self.controls_layout = QVBoxLayout(controls)
        self.controls_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(controls)

        left_layout.addWidget(scroll, stretch=1)

        log_group = QGroupBox("紀錄")
        log_layout = QVBoxLayout(log_group)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(160)
        log_layout.addWidget(self.log_box)
        left_layout.addWidget(log_group)

        splitter.addWidget(left_widget)

        # ---------- 右側：圖表分頁 ----------
        self.tabs = QTabWidget()
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self._build_file_group()
        self._build_display_group()
        self._build_crop_group()
        self._build_slice_group()
        self._build_iq_group()
        self._build_node_group()
        self._build_export_group()

        self._build_tab_overview()
        self._build_tab_iq()
        self._build_tab_node()

        self._set_2d_controls_enabled(False)
        self.log(">>> 歡迎使用 Labber HDF5 檢視器。請先讀取檔案。")

    # ---------------- 檔案群組 ----------------
    def _build_file_group(self):
        g = QGroupBox("1. 檔案讀取")
        v = QVBoxLayout(g)

        btn_single = QPushButton("讀取單一 HDF5 檔案")
        btn_single.clicked.connect(self.on_load_single)
        v.addWidget(btn_single)

        h = QHBoxLayout()
        h.addWidget(QLabel("去背模式:"))
        self.debg_mode_combo = QComboBox()
        self.debg_mode_combo.addItems(["- (相減)", "/ (相除)"])
        h.addWidget(self.debg_mode_combo)
        v.addLayout(h)

        btn_debg = QPushButton("讀取去背數據 (選擇 測量檔 + 背景檔)")
        btn_debg.clicked.connect(self.on_load_debackground)
        v.addWidget(btn_debg)

        self.info_label = QLabel("尚未讀取任何檔案。")
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: #555; padding-top: 4px;")
        v.addWidget(self.info_label)

        self.controls_layout.addWidget(g)

    # ---------------- 顯示設定群組 ----------------
    def _build_display_group(self):
        g = QGroupBox("2. 顯示設定")
        grid = QGridLayout(g)

        grid.addWidget(QLabel("S 參數:"), 0, 0)
        self.s_param_combo = QComboBox()
        self.s_param_combo.currentTextChanged.connect(self.on_s_param_changed)
        grid.addWidget(self.s_param_combo, 0, 1)

        grid.addWidget(QLabel("Step 軸:"), 1, 0)
        self.step_combo = QComboBox()
        self.step_combo.currentTextChanged.connect(self.on_step_channel_changed)
        grid.addWidget(self.step_combo, 1, 1)

        grid.addWidget(QLabel("色彩對應:"), 2, 0)
        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(COLORMAPS)
        self.cmap_combo.currentTextChanged.connect(self.refresh_heatmap)
        grid.addWidget(self.cmap_combo, 2, 1)

        self.db_checkbox = QCheckBox("以 dB 顯示 (20·log10|S|)")
        self.db_checkbox.setChecked(True)
        self.db_checkbox.stateChanged.connect(self.refresh_all_plots)
        grid.addWidget(self.db_checkbox, 3, 0, 1, 2)

        self.controls_layout.addWidget(g)

    # ---------------- 頻率裁切群組 ----------------
    def _build_crop_group(self):
        g = QGroupBox("3. 頻率範圍裁切 (單位: GHz)")
        grid = QGridLayout(g)

        grid.addWidget(QLabel("起始:"), 0, 0)
        self.crop_start_spin = QDoubleSpinBox()
        self.crop_start_spin.setDecimals(6)
        self.crop_start_spin.setRange(-1e6, 1e6)
        grid.addWidget(self.crop_start_spin, 0, 1)

        grid.addWidget(QLabel("結束:"), 1, 0)
        self.crop_stop_spin = QDoubleSpinBox()
        self.crop_stop_spin.setDecimals(6)
        self.crop_stop_spin.setRange(-1e6, 1e6)
        grid.addWidget(self.crop_stop_spin, 1, 1)

        btn_apply = QPushButton("套用裁切")
        btn_apply.clicked.connect(self.on_apply_crop)
        grid.addWidget(btn_apply, 2, 0)

        btn_reset = QPushButton("重置 (使用全範圍)")
        btn_reset.clicked.connect(self.on_reset_crop)
        grid.addWidget(btn_reset, 2, 1)

        self.controls_layout.addWidget(g)

    # ---------------- 切片群組 ----------------
    def _build_slice_group(self):
        g = QGroupBox("4. Step 切片 (可直接點擊熱圖)")
        v = QVBoxLayout(g)

        h = QHBoxLayout()
        h.addWidget(QLabel("Step 索引:"))
        self.slice_spin = QSpinBox()
        self.slice_spin.setRange(0, 0)
        self.slice_spin.valueChanged.connect(self.on_slice_idx_changed)
        h.addWidget(self.slice_spin)
        v.addLayout(h)

        self.slice_value_label = QLabel("目前 Step 值: -")
        v.addWidget(self.slice_value_label)

        self.controls_layout.addWidget(g)

    # ---------------- IQ 圖群組 ----------------
    def _build_iq_group(self):
        g = QGroupBox("5. IQ 平面圖")
        grid = QGridLayout(g)

        grid.addWidget(QLabel("中心頻率 (GHz):"), 0, 0)
        self.iq_center_spin = QDoubleSpinBox()
        self.iq_center_spin.setDecimals(6)
        self.iq_center_spin.setRange(-1e6, 1e6)
        grid.addWidget(self.iq_center_spin, 0, 1)

        grid.addWidget(QLabel("視窗半寬 (MHz):"), 1, 0)
        self.iq_halfwin_spin = QDoubleSpinBox()
        self.iq_halfwin_spin.setDecimals(4)
        self.iq_halfwin_spin.setRange(0.0001, 1e6)
        self.iq_halfwin_spin.setValue(200.0)
        grid.addWidget(self.iq_halfwin_spin, 1, 1)

        self.iq_autoscale_check = QCheckBox("自動縮放座標軸")
        self.iq_autoscale_check.setChecked(True)
        self.iq_autoscale_check.stateChanged.connect(self._on_iq_autoscale_toggle)
        grid.addWidget(self.iq_autoscale_check, 2, 0, 1, 2)

        grid.addWidget(QLabel("手動範圍 r:"), 3, 0)
        self.iq_range_spin = QDoubleSpinBox()
        self.iq_range_spin.setDecimals(6)
        self.iq_range_spin.setRange(1e-9, 1e6)
        self.iq_range_spin.setValue(0.001)
        self.iq_range_spin.setEnabled(False)
        grid.addWidget(self.iq_range_spin, 3, 1)

        btn_iq = QPushButton("繪製 IQ 圖 (使用目前 Step 切片)")
        btn_iq.clicked.connect(self.plot_iq)
        grid.addWidget(btn_iq, 4, 0, 1, 2)

        self.controls_layout.addWidget(g)

    # ---------------- 節點偵測群組 ----------------
    def _build_node_group(self):
        g = QGroupBox("6. 共振節點自動偵測 (需 2D 資料)")
        grid = QGridLayout(g)

        grid.addWidget(QLabel("動態視窗半寬 (GHz):"), 0, 0)
        self.node_window_spin = QDoubleSpinBox()
        self.node_window_spin.setDecimals(4)
        self.node_window_spin.setRange(0.0001, 1e6)
        self.node_window_spin.setValue(0.25)
        grid.addWidget(self.node_window_spin, 0, 1)

        grid.addWidget(QLabel("平滑視窗長度:"), 1, 0)
        self.node_smooth_spin = QSpinBox()
        self.node_smooth_spin.setRange(1, 101)
        self.node_smooth_spin.setValue(3)
        grid.addWidget(self.node_smooth_spin, 1, 1)

        grid.addWidget(QLabel("峰值最小間距 (點數):"), 2, 0)
        self.node_distance_spin = QSpinBox()
        self.node_distance_spin.setRange(1, 100000)
        self.node_distance_spin.setValue(10)
        grid.addWidget(self.node_distance_spin, 2, 1)

        grid.addWidget(QLabel("峰值最小顯著度 (prominence):"), 3, 0)
        self.node_prominence_spin = QDoubleSpinBox()
        self.node_prominence_spin.setDecimals(6)
        self.node_prominence_spin.setRange(0.0, 1e6)
        self.node_prominence_spin.setValue(0.0002)
        grid.addWidget(self.node_prominence_spin, 3, 1)

        self.node_manual_thresh_check = QCheckBox("手動設定波谷深度門檻 (預設用中位數)")
        self.node_manual_thresh_check.stateChanged.connect(
            lambda s: self.node_thresh_spin.setEnabled(s == Qt.CheckState.Checked.value or s == 2)
        )
        grid.addWidget(self.node_manual_thresh_check, 4, 0, 1, 2)

        self.node_thresh_spin = QDoubleSpinBox()
        self.node_thresh_spin.setDecimals(6)
        self.node_thresh_spin.setRange(0.0, 1e6)
        self.node_thresh_spin.setValue(0.1)
        self.node_thresh_spin.setEnabled(False)
        grid.addWidget(self.node_thresh_spin, 5, 0, 1, 2)

        btn_node = QPushButton("執行節點偵測")
        btn_node.clicked.connect(self.run_node_detection)
        grid.addWidget(btn_node, 6, 0, 1, 2)

        if not HAS_SCIPY:
            btn_node.setEnabled(False)
            grid.addWidget(QLabel("(未安裝 scipy，此功能停用)"), 7, 0, 1, 2)

        self.controls_layout.addWidget(g)

    # ---------------- 匯出群組 ----------------
    def _build_export_group(self):
        g = QGroupBox("7. 匯出")
        v = QVBoxLayout(g)

        btn_png = QPushButton("儲存目前分頁圖片 (PNG)")
        btn_png.clicked.connect(self.export_current_figure)
        v.addWidget(btn_png)

        btn_csv = QPushButton("匯出目前切片數據 (CSV)")
        btn_csv.clicked.connect(self.export_slice_csv)
        v.addWidget(btn_csv)

        self.controls_layout.addWidget(g)

    # ---------------- 分頁：總覽 (熱圖 + 切片) ----------------
    def _build_tab_overview(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        split = QSplitter(Qt.Orientation.Vertical)

        self.heat_panel = PlotPanel(figsize=(7, 4.5))
        self.slice_panel = PlotPanel(figsize=(7, 3))
        split.addWidget(self.heat_panel)
        split.addWidget(self.slice_panel)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        layout.addWidget(split)

        self.heat_panel.canvas.mpl_connect("button_press_event", self.on_heatmap_click)

        self.tabs.addTab(w, "2D 熱圖 / 1D 切片")

    # ---------------- 分頁：IQ ----------------
    def _build_tab_iq(self):
        self.iq_panel = PlotPanel(figsize=(6, 6))
        self.tabs.addTab(self.iq_panel, "IQ 平面圖")

    # ---------------- 分頁：節點偵測 ----------------
    def _build_tab_node(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        self.node_panel = PlotPanel(nrows=2, ncols=1, figsize=(7, 8))
        layout.addWidget(self.node_panel, stretch=3)

        self.node_table = QTableWidget(0, 4)
        self.node_table.setHorizontalHeaderLabels(["#", "Step 值", "頻率 (GHz)", "平均 |S|"])
        self.node_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.node_table.setMaximumHeight(220)
        layout.addWidget(self.node_table, stretch=1)

        self.tabs.addTab(w, "共振節點偵測")

    # =========================================================
    #  工具函式
    # =========================================================
    def log(self, msg):
        self.log_box.appendPlainText(str(msg))
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

    def _set_2d_controls_enabled(self, enabled):
        self.step_combo.setEnabled(enabled)
        self.slice_spin.setEnabled(enabled)

    def _to_db_or_linear(self, complex_arr):
        mag = np.abs(complex_arr)
        if self.db_checkbox.isChecked():
            with np.errstate(divide="ignore"):
                return 20 * np.log10(np.where(mag > 0, mag, np.nan))
        return mag

    def _mag_label(self):
        return "|S| (dB)" if self.db_checkbox.isChecked() else "|S| (linear)"

    # =========================================================
    #  檔案讀取
    # =========================================================
    def on_load_single(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "選擇 Labber HDF5 文件", "", "Labber HDF5 (*.hdf5 *.h5 *.he5);;All Files (*)"
        )
        if not path:
            return
        data = LT.load_labber_data(path, log=self.log)
        if data is None:
            QMessageBox.warning(self, "讀取失敗", "無法讀取此檔案，詳見紀錄區訊息。")
            return
        self.apply_loaded_data(data)

    def on_load_debackground(self):
        meas_path, _ = QFileDialog.getOpenFileName(
            self, "選擇 MEASUREMENT 文件", "", "Labber HDF5 (*.hdf5 *.h5 *.he5);;All Files (*)"
        )
        if not meas_path:
            return
        bg_path, _ = QFileDialog.getOpenFileName(
            self, "選擇 BACKGROUND 文件", "", "Labber HDF5 (*.hdf5 *.h5 *.he5);;All Files (*)"
        )
        if not bg_path:
            return
        mode = "-" if self.debg_mode_combo.currentIndex() == 0 else "/"
        data = LT.get_debackgrounded_data(meas_path, bg_path, mode=mode, log=self.log)
        if data is None:
            QMessageBox.warning(self, "去背失敗", "無法完成去背處理，詳見紀錄區訊息。")
            return
        self.apply_loaded_data(data)

    def apply_loaded_data(self, data):
        self.data_raw = data
        self.is_2d = data["is_2d"]
        self.freq_full = data["frequency"]
        self.node_result = None
        self.node_table.setRowCount(0)

        # --- S 參數下拉選單 ---
        self.s_param_combo.blockSignals(True)
        self.s_param_combo.clear()
        self.s_param_combo.addItems(list(data["s_params"].keys()))
        self.s_param_combo.blockSignals(False)

        # --- Step 軸下拉選單 ---
        self.step_combo.blockSignals(True)
        self.step_combo.clear()
        if self.is_2d:
            self.step_combo.addItems(list(data["step_channels"].keys()))
        self.step_combo.blockSignals(False)

        self._set_2d_controls_enabled(self.is_2d)

        n_step = len(data["step_channels"].get(self.step_combo.currentText(), [])) if self.is_2d else 0
        info = (
            f"檔案: {data['filename']}\n"
            f"2D 掃描: {'是' if self.is_2d else '否'}\n"
            f"頻率點數: {len(self.freq_full) if self.freq_full is not None else 0}\n"
            f"S 參數: {list(data['s_params'].keys())}"
        )
        if self.is_2d:
            info += f"\nStep 軸選項: {list(data['step_channels'].keys())}"
        self.info_label.setText(info)

        if self.s_param_combo.count() == 0:
            self.log("警告: 此檔案未包含任何 S 參數，無法繪圖。")
            return

        # 先靜默設定 Step 軸 (不觸發重繪)，確保 on_s_param_changed 重繪熱圖時
        # self.step_values 已經就緒，兩者形狀才會一致。
        if self.is_2d and self.step_combo.count() > 0:
            self._load_step_channel(self.step_combo.currentText(), refresh=False)
        else:
            self.step_values = None
            self.step_name = None

        # 觸發第一個 S 參數的載入並重繪 (若 combo 內容與前一次相同，text 不會變 -> 手動呼叫一次)
        self.on_s_param_changed(self.s_param_combo.currentText())

    # =========================================================
    #  S 參數 / Step 軸 切換
    # =========================================================
    def on_s_param_changed(self, name):
        if not name or self.data_raw is None:
            return
        self.s_name = name
        raw = self.data_raw["s_params"][name]
        if self.is_2d:
            # raw shape: (n_freq, n_step) -> 轉置成 (n_step, n_freq) 方便逐列切片
            self.S_full = raw.T if raw.ndim == 2 else raw
        else:
            self.S_full = raw
        self.on_reset_crop(refresh=False)
        self.refresh_all_plots()

    def on_step_channel_changed(self, name):
        self._load_step_channel(name, refresh=True)

    def _load_step_channel(self, name, refresh=True):
        if not name or self.data_raw is None or not self.is_2d:
            self.step_values = None
            self.step_name = None
            return
        self.step_name = name
        self.step_values = self.data_raw["step_channels"][name]

        self.slice_spin.blockSignals(True)
        self.slice_spin.setRange(0, max(0, len(self.step_values) - 1))
        self.slice_spin.setValue(0)
        self.slice_spin.blockSignals(False)
        self.current_step_idx = 0
        self._update_slice_value_label()
        if refresh:
            self.refresh_all_plots()

    # =========================================================
    #  頻率裁切
    # =========================================================
    def on_apply_crop(self):
        if self.freq_full is None or self.S_full is None:
            return
        start_f = self.crop_start_spin.value() * 1e9
        stop_f = self.crop_stop_spin.value() * 1e9
        if stop_f <= start_f:
            QMessageBox.warning(self, "範圍錯誤", "結束頻率必須大於起始頻率。")
            return
        start_idx = int(np.abs(self.freq_full - start_f).argmin())
        stop_idx = int(np.abs(self.freq_full - stop_f).argmin())
        if stop_idx <= start_idx:
            stop_idx = start_idx + 1
        self.crop_idx = (start_idx, stop_idx)
        self._apply_crop_indices()
        self.log(f">>> 已套用頻率裁切: index [{start_idx}:{stop_idx}] "
                  f"({self.freq_full[start_idx]/1e9:.6f} ~ {self.freq_full[stop_idx-1]/1e9:.6f} GHz)")
        self.refresh_all_plots()

    def on_reset_crop(self, refresh=True):
        if self.freq_full is None:
            return
        self.crop_idx = (0, len(self.freq_full))
        self._apply_crop_indices()
        self.crop_start_spin.blockSignals(True)
        self.crop_stop_spin.blockSignals(True)
        self.crop_start_spin.setValue(self.freq_full[0] / 1e9)
        self.crop_stop_spin.setValue(self.freq_full[-1] / 1e9)
        self.crop_start_spin.blockSignals(False)
        self.crop_stop_spin.blockSignals(False)
        if refresh:
            self.refresh_all_plots()

    def _apply_crop_indices(self):
        start, stop = self.crop_idx
        self.freq = self.freq_full[start:stop]
        if self.S_full is None:
            self.S_crop = None
        elif self.is_2d:
            self.S_crop = self.S_full[:, start:stop]
        else:
            self.S_crop = self.S_full[start:stop]

    # =========================================================
    #  切片 index 變更 / 熱圖點擊
    # =========================================================
    def on_slice_idx_changed(self, value):
        self.current_step_idx = value
        self._update_slice_value_label()
        self.refresh_heatmap(keep_view=True)
        self.refresh_slice()

    def _update_slice_value_label(self):
        if self.step_values is not None and 0 <= self.current_step_idx < len(self.step_values):
            v = self.step_values[self.current_step_idx]
            self.slice_value_label.setText(f"目前 Step 值: {v:.6g}  (index={self.current_step_idx})")
        else:
            self.slice_value_label.setText("目前 Step 值: -")

    def on_heatmap_click(self, event):
        if not self.is_2d or self.step_values is None or event.inaxes is None:
            return
        if event.ydata is None:
            return
        idx = int(np.abs(self.step_values - event.ydata).argmin())
        idx = max(0, min(idx, self.slice_spin.maximum()))
        self.slice_spin.setValue(idx)  # 會觸發 on_slice_idx_changed

    # =========================================================
    #  繪圖：總覽 (熱圖 + 切片)
    # =========================================================
    def refresh_all_plots(self):
        self.refresh_heatmap()
        self.refresh_slice()

    def refresh_heatmap(self, keep_view=False):
        panel = self.heat_panel
        ax = panel.ax(0)

        xlim, ylim = None, None
        if keep_view:
            xlim, ylim = ax.get_xlim(), ax.get_ylim()

        ax.clear()

        if self.S_crop is None or self.freq is None:
            ax.set_title("尚無資料")
            panel.draw()
            return

        cmap = self.cmap_combo.currentText()

        if self.is_2d and self.step_values is not None:
            mat = self._to_db_or_linear(self.S_crop)  # shape (n_step, n_freq)
            mesh = ax.pcolormesh(self.freq / 1e9, self.step_values, mat, shading="auto", cmap=cmap)
            self._colorbar(panel, mesh, ax)
            if 0 <= self.current_step_idx < len(self.step_values):
                ax.axhline(self.step_values[self.current_step_idx], color="white", lw=1.0, ls="--", alpha=0.8)
            if self.node_result is not None:
                nr = self.node_result
                ax.plot(nr["fit_line_freq"] / 1e9, nr["I"], "w--", lw=1.5, label="擬合軌跡")
                ax.scatter(nr["fit_f"] / 1e9, nr["fit_I"], color="magenta", s=6, alpha=0.6, label="擬合用點")
                ax.plot(nr["node_freqs"] / 1e9, nr["node_currents"], "ro", ms=6,
                        markeredgecolor="white", label="節點")
                ax.legend(loc="best", fontsize=8)
            ax.set_xlabel("Frequency (GHz)")
            ax.set_ylabel(self.step_name or "Step")
            ax.set_title(f"{self.s_name}  —  {self.data_raw['filename']}")
        else:
            y = self._to_db_or_linear(self.S_crop)
            ax.plot(self.freq / 1e9, y)
            ax.set_xlabel("Frequency (GHz)")
            ax.set_ylabel(self._mag_label())
            ax.set_title(f"{self.s_name}  —  {self.data_raw['filename']} (1D)")
            ax.grid(alpha=0.3)

        if keep_view and xlim is not None:
            ax.set_xlim(xlim)
            ax.set_ylim(ylim)

        panel.draw()

    def _colorbar(self, panel, mesh, ax):
        # 每次都重建 colorbar 避免疊加殘留
        if hasattr(panel, "_cbar") and panel._cbar is not None:
            try:
                panel._cbar.remove()
            except Exception:
                pass
        panel._cbar = panel.fig.colorbar(mesh, ax=ax, label=self._mag_label())

    def refresh_slice(self):
        panel = self.slice_panel
        ax = panel.ax(0)
        ax.clear()

        if not self.is_2d:
            ax.set_title("此檔案非 2D 掃描，無切片可顯示 (上方已直接繪製完整曲線)")
            panel.draw()
            return

        if self.S_crop is None or self.freq is None or self.step_values is None:
            panel.draw()
            return

        idx = self.current_step_idx
        if not (0 <= idx < self.S_crop.shape[0]):
            panel.draw()
            return

        y = self._to_db_or_linear(self.S_crop[idx, :])
        ax.plot(self.freq / 1e9, y, "b.-", ms=3)
        ax.set_xlabel("Frequency (GHz)")
        ax.set_ylabel(self._mag_label())
        step_val = self.step_values[idx]
        ax.set_title(f"切片: {self.step_name} = {step_val:.6g}  (index={idx})")
        ax.grid(alpha=0.3)
        panel.draw()

    # =========================================================
    #  IQ 圖
    # =========================================================
    def _on_iq_autoscale_toggle(self, state):
        self.iq_range_spin.setEnabled(not self.iq_autoscale_check.isChecked())

    def plot_iq(self):
        if self.S_full is None or self.freq_full is None:
            QMessageBox.information(self, "尚無資料", "請先讀取檔案。")
            return

        center = self.iq_center_spin.value() * 1e9
        half_win = self.iq_halfwin_spin.value() * 1e6

        start_f = center - half_win
        stop_f = center + half_win
        start_idx = int(np.abs(self.freq_full - start_f).argmin())
        stop_idx = int(np.abs(self.freq_full - stop_f).argmin())
        if stop_idx <= start_idx:
            stop_idx = start_idx + 1

        if self.is_2d:
            idx = self.current_step_idx
            if not (0 <= idx < self.S_full.shape[0]):
                QMessageBox.warning(self, "索引錯誤", "目前 Step 索引超出範圍。")
                return
            s_slice = self.S_full[idx, start_idx:stop_idx]
            title_extra = f"  |  {self.step_name} = {self.step_values[idx]:.6g}" if self.step_values is not None else ""
        else:
            s_slice = self.S_full[start_idx:stop_idx]
            title_extra = ""

        if s_slice.size == 0:
            QMessageBox.warning(self, "範圍錯誤", "選取的頻率視窗內沒有數據點，請調整中心頻率或視窗寬度。")
            return

        I = np.real(s_slice)
        Q = np.imag(s_slice)

        panel = self.iq_panel
        ax = panel.ax(0)
        ax.clear()
        ax.plot(-I, Q, "b.-")
        ax.grid(alpha=0.4)
        ax.set_xlabel("-I")
        ax.set_ylabel("Q")
        ax.set_title(f"{self.s_name}  IQ 平面圖 @ {center/1e9:.6f} GHz ± {half_win/1e6:.4f} MHz{title_extra}",
                     fontsize=10)
        # 用 datalim 調整而非 box，避免當 I/Q 數值範圍差異極大時，
        # 子圖被壓縮成一條細長的窄帶。
        ax.set_aspect("equal", adjustable="datalim")

        if not self.iq_autoscale_check.isChecked():
            r = self.iq_range_spin.value()
            ax.set_xlim(-r / 10, r)
            ax.set_ylim(-r / 2, r / 2)

        panel.draw()
        self.log(f">>> 已繪製 IQ 圖：{start_idx}:{stop_idx} ({s_slice.size} 點)")

    # =========================================================
    #  共振節點自動偵測 (對應 read.ipynb 進階分析區塊)
    # =========================================================
    def run_node_detection(self):
        if not HAS_SCIPY:
            QMessageBox.warning(self, "缺少套件", "未安裝 scipy，無法執行節點偵測。")
            return
        if not self.is_2d or self.S_crop is None or self.step_values is None:
            QMessageBox.information(self, "不適用", "節點偵測僅適用於 2D 掃描資料。")
            return

        try:
            I = np.asarray(self.step_values, dtype=float)
            f_values = self.freq
            data_debg = self.S_crop  # shape (n_step, n_freq)

            n_step = len(I)
            raw_dip_freqs = np.zeros(n_step)
            raw_dip_depths = np.zeros(n_step)

            for i in range(n_step):
                s_slice = np.abs(data_debg[i, :])
                min_idx = int(np.argmin(s_slice))
                raw_dip_freqs[i] = f_values[min_idx]
                raw_dip_depths[i] = np.max(s_slice) - s_slice[min_idx]

            if self.node_manual_thresh_check.isChecked():
                threshold = self.node_thresh_spin.value()
            else:
                threshold = float(np.median(raw_dip_depths))
            valid_mask = raw_dip_depths > threshold

            if valid_mask.sum() < 2:
                QMessageBox.warning(self, "資料不足", "符合門檻的資料點過少，無法進行線性擬合，請調整門檻。")
                return

            fit_I = I[valid_mask]
            fit_f = raw_dip_freqs[valid_mask]
            p = np.polyfit(fit_I, fit_f, 1)
            slope_a, intercept_b = p[0], p[1]
            self.log(f"✅ 自動提取軌跡方程式成功：f = {slope_a/1e6:.5f} [MHz/單位] * x + {intercept_b/1e9:.5f} [GHz]")

            window_size = self.node_window_spin.value() * 1e9
            avg_transmission = np.zeros(n_step)
            exact_res_freqs = np.zeros(n_step)

            for i, current in enumerate(I):
                center_freq = slope_a * current + intercept_b
                mask = (f_values >= center_freq - window_size) & (f_values <= center_freq + window_size)
                if np.any(mask):
                    s21_abs_slice = np.abs(data_debg[i, mask])
                    f_slice = f_values[mask]
                    avg_transmission[i] = np.mean(s21_abs_slice)
                    exact_res_freqs[i] = f_slice[np.argmin(s21_abs_slice)]
                else:
                    avg_transmission[i] = np.nan
                    exact_res_freqs[i] = np.nan

            window_len = self.node_smooth_spin.value()
            if window_len > 1:
                kernel = np.ones(window_len) / window_len
                smooth_trans = np.convolve(avg_transmission, kernel, mode="same")
                half = window_len // 2
                if half > 0:
                    smooth_trans[:half] = avg_transmission[:half]
                    smooth_trans[-half:] = avg_transmission[-half:]
            else:
                smooth_trans = avg_transmission.copy()

            distance = self.node_distance_spin.value()
            prominence = self.node_prominence_spin.value()
            peaks, _ = find_peaks(smooth_trans, distance=distance, prominence=prominence)

            node_currents = I[peaks]
            node_vals = smooth_trans[peaks]
            node_freqs = exact_res_freqs[peaks]

            self.node_result = {
                "I": I,
                "fit_I": fit_I,
                "fit_f": fit_f,
                "slope_a": slope_a,
                "intercept_b": intercept_b,
                "fit_line_freq": slope_a * I + intercept_b,
                "avg_transmission": avg_transmission,
                "smooth_trans": smooth_trans,
                "node_currents": node_currents,
                "node_vals": node_vals,
                "node_freqs": node_freqs,
            }

            self.log(f"✅ 成功定位 {len(peaks)} 個節點：")
            for j in range(len(peaks)):
                self.log(f"   Node {j+1}: Step={node_currents[j]:.4g}, "
                         f"頻率={node_freqs[j]/1e9:.6f} GHz, 平均|S|={node_vals[j]:.5f}")

            self._draw_node_plots()
            self._fill_node_table()
            self.refresh_heatmap(keep_view=True)  # 疊加節點標記到主熱圖

        except Exception as e:
            self.log(f"節點偵測發生錯誤: {e}")
            self.log(traceback.format_exc())
            QMessageBox.critical(self, "錯誤", f"節點偵測過程發生錯誤:\n{e}")

    def _draw_node_plots(self):
        nr = self.node_result
        panel = self.node_panel
        ax1, ax2 = panel.ax(0), panel.ax(1)
        ax1.clear()
        ax2.clear()

        S_db = self._to_db_or_linear(self.S_crop)
        mesh = ax1.pcolormesh(self.step_values, self.freq / 1e9, S_db.T, cmap="jet", shading="auto")
        ax1.plot(nr["I"], nr["fit_line_freq"] / 1e9, "w--", lw=2, label="Auto-fitted Trajectory")
        ax1.scatter(nr["fit_I"], nr["fit_f"] / 1e9, color="magenta", s=6, alpha=0.5, label="Valid Points")
        ax1.plot(nr["node_currents"], nr["node_freqs"] / 1e9, "ro", ms=6, markeredgecolor="white", label="Nodes")
        ax1.set_xlabel(self.step_name or "Step")
        ax1.set_ylabel("Frequency (GHz)")
        ax1.set_title("Automatic Trajectory Tracking & Node Positions")
        ax1.legend(loc="best", fontsize=8)
        panel.fig.colorbar(mesh, ax=ax1, label=self._mag_label())

        ax2.plot(nr["I"], nr["avg_transmission"], "b-", alpha=0.3, label="Raw Avg Transmission")
        ax2.plot(nr["I"], nr["smooth_trans"], "g-", lw=2, label="Smoothed Transmission")
        ax2.plot(nr["node_currents"], nr["node_vals"], "rx", ms=10, mew=2, label="Extracted Nodes")
        for j in range(len(nr["node_currents"])):
            ax2.annotate(f"n={j+1}", (nr["node_currents"][j], nr["node_vals"][j]),
                         textcoords="offset points", xytext=(0, 8), color="red", ha="center", fontsize=9)
        ax2.set_xlabel(self.step_name or "Step")
        ax2.set_ylabel("Average |S|")
        ax2.set_title("Transmission Amplitude & Dynamic Node Detection")
        ax2.grid(alpha=0.4)
        ax2.legend(fontsize=8)

        panel.draw()

    def _fill_node_table(self):
        nr = self.node_result
        n = len(nr["node_currents"])
        self.node_table.setRowCount(n)
        for j in range(n):
            self.node_table.setItem(j, 0, QTableWidgetItem(str(j + 1)))
            self.node_table.setItem(j, 1, QTableWidgetItem(f"{nr['node_currents'][j]:.6g}"))
            self.node_table.setItem(j, 2, QTableWidgetItem(f"{nr['node_freqs'][j]/1e9:.6f}"))
            self.node_table.setItem(j, 3, QTableWidgetItem(f"{nr['node_vals'][j]:.6g}"))

    # =========================================================
    #  匯出
    # =========================================================
    def export_current_figure(self):
        idx = self.tabs.currentIndex()
        panel_map = {0: self.heat_panel, 1: self.iq_panel, 2: self.node_panel}
        panel = panel_map.get(idx)
        if panel is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "儲存圖片", "figure.png", "PNG Image (*.png)")
        if not path:
            return
        panel.fig.savefig(path, dpi=200)
        self.log(f">>> 圖片已儲存: {path}")

    def export_slice_csv(self):
        if self.freq is None or self.S_crop is None:
            QMessageBox.information(self, "尚無資料", "請先讀取檔案。")
            return

        if self.is_2d:
            idx = self.current_step_idx
            if not (0 <= idx < self.S_crop.shape[0]):
                return
            s_slice = self.S_crop[idx, :]
            default_name = f"{self.s_name}_idx{idx}.csv"
        else:
            s_slice = self.S_crop
            default_name = f"{self.s_name}.csv"

        path, _ = QFileDialog.getSaveFileName(self, "匯出切片 CSV", default_name, "CSV (*.csv)")
        if not path:
            return

        with open(path, "w", newline="", encoding="utf-8") as fp:
            writer = csv.writer(fp)
            writer.writerow(["frequency_Hz", "real", "imag", "abs", "abs_dB", "phase_deg"])
            mag = np.abs(s_slice)
            with np.errstate(divide="ignore"):
                mag_db = 20 * np.log10(np.where(mag > 0, mag, np.nan))
            phase = np.degrees(np.angle(s_slice))
            for fq, re, im, m, mdb, ph in zip(self.freq, np.real(s_slice), np.imag(s_slice), mag, mag_db, phase):
                writer.writerow([fq, re, im, m, mdb, ph])

        self.log(f">>> 切片數據已匯出: {path}")


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
