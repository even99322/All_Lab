"""主視窗：只負責版面與元件，不含邏輯（邏輯在 controller.py）"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QGridLayout, QGroupBox, QPushButton, QLabel,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QSplitter, QTabWidget,
    QPlainTextEdit, QSlider, QScrollArea, QProgressBar, QLineEdit,
)

from .plots import DataPlotWidget, FitPlotWidget
from .param_table import ParamTable
from .batch_panel import BatchPanel
from .phase_panel import PhasePanel
from .roll_table import RollTable
from .latex_view import FormulaImage
from ..core.batch import INIT_MODES


class MainWindow(QMainWindow):
    closing = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("S 參數擬合工具")
        self.resize(1500, 950)
        self._build()

    def closeEvent(self, ev):
        self.closing.emit()
        super().closeEvent(ev)

    def _build(self):
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(self._build_data_group())
        lv.addWidget(self._build_clean_group())
        lv.addWidget(self._build_formula_group())
        lv.addWidget(self._build_option_group())
        lv.addWidget(self._build_action_group())
        lv.addWidget(self._build_output_group())
        lv.addWidget(self._build_batch_group())
        lv.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(left)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(360)

        self.tabs = QTabWidget()
        self.data_plot = DataPlotWidget()
        self.fit_plot = FitPlotWidget()
        self.tabs.addTab(self.data_plot, "數據預覽")
        self.tabs.addTab(self.fit_plot, "擬合結果")
        self.batch_panel = BatchPanel()
        self.tabs.addTab(self.batch_panel, "連續擬合")
        self.phase_panel = PhasePanel()
        self.tabs.addTab(self.phase_panel, "相位 / 節點")

        self.table = ParamTable()
        self.txt_result = QPlainTextEdit()
        self.txt_result.setReadOnly(True)
        self.txt_result.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.txt_result.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")

        tw = QWidget()
        tv = QVBoxLayout(tw)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.addWidget(QLabel("參數表（初值/邊界可輸入 pi、pi/2、np.deg2rad(30) 等表達式）"))
        tv.addWidget(self.table)
        bottom = QSplitter(Qt.Orientation.Horizontal)
        bottom.addWidget(tw)
        bottom.addWidget(self.txt_result)
        bottom.setSizes([700, 400])

        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.tabs)
        right.addWidget(bottom)
        right.setSizes([700, 250])

        main = QSplitter(Qt.Orientation.Horizontal)
        main.addWidget(scroll)
        main.addWidget(right)
        main.setSizes([360, 1140])
        self.setCentralWidget(main)

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(160)
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.lbl_engine = QLabel("擬合引擎：啟動中…")
        self.statusBar().addPermanentWidget(self.lbl_engine)
        self.statusBar().addPermanentWidget(self.progress)

    def _build_data_group(self):
        g = QGroupBox("① 數據")
        gl = QGridLayout(g)
        self.btn_open = QPushButton("開啟數據檔…")
        self.lbl_file = QLabel("尚未載入")
        self.lbl_file.setWordWrap(True)
        self.cmb_s = QComboBox()
        self.cmb_axis = QComboBox()
        self.spin_scale = QDoubleSpinBox()
        self.spin_scale.setDecimals(6)
        self.spin_scale.setRange(-1e12, 1e12)
        self.spin_scale.setValue(1.0)
        self.spin_scale.setKeyboardTracking(False)
        self.slider_idx = QSlider(Qt.Orientation.Horizontal)
        self.spin_idx = QSpinBox()
        self.spin_f1 = QDoubleSpinBox()
        self.spin_f2 = QDoubleSpinBox()
        for sp in (self.spin_f1, self.spin_f2):
            sp.setDecimals(6)
            sp.setRange(0, 1e4)
            sp.setSuffix(" GHz")
            sp.setKeyboardTracking(False)
        self.btn_full = QPushButton("全頻段")
        self.lbl_npts = QLabel("")
        self.chk_zoom = QCheckBox("預覽範圍跟隨遮罩")
        self.chk_zoom.setChecked(True)
        r = 0
        gl.addWidget(self.btn_open, r, 0, 1, 3); r += 1
        gl.addWidget(self.lbl_file, r, 0, 1, 3); r += 1
        gl.addWidget(QLabel("S 參數"), r, 0); gl.addWidget(self.cmb_s, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("掃描軸"), r, 0); gl.addWidget(self.cmb_axis, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("軸倍率"), r, 0); gl.addWidget(self.spin_scale, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("切片"), r, 0); gl.addWidget(self.slider_idx, r, 1); gl.addWidget(self.spin_idx, r, 2); r += 1
        gl.addWidget(QLabel("頻率起點"), r, 0); gl.addWidget(self.spin_f1, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("頻率終點"), r, 0); gl.addWidget(self.spin_f2, r, 1, 1, 2); r += 1
        gl.addWidget(self.btn_full, r, 1); gl.addWidget(self.lbl_npts, r, 2); r += 1
        gl.addWidget(self.chk_zoom, r, 0, 1, 3); r += 1
        gl.addWidget(QLabel("提示：在切片圖上拖曳可選頻率範圍；\n點 2D 圖可切換切片"), r, 0, 1, 3)
        return g

    def _build_clean_group(self):
        g = QGroupBox("雜訊處理（去除極端雜訊）")
        cl = QGridLayout(g)
        self.chk_spike = QCheckBox("自動去除尖峰（Hampel 濾波）")
        self.spin_sp_half = QSpinBox()
        self.spin_sp_half.setRange(2, 500)
        self.spin_sp_half.setValue(5)
        self.spin_sp_half.setSuffix(" 點")
        self.spin_sp_half.setToolTip("滑動中位數視窗半寬；需大於尖峰寬度的 2 倍")
        self.spin_sp_k = QDoubleSpinBox()
        self.spin_sp_k.setRange(1.0, 1000.0)
        self.spin_sp_k.setDecimals(1)
        self.spin_sp_k.setValue(5.0)
        self.spin_sp_k.setSuffix(" σ")
        self.spin_sp_k.setToolTip("偏離滑動中位數超過幾倍局部雜訊就視為尖峰；越大越保守")
        self.spin_sp_dil = QSpinBox()
        self.spin_sp_dil.setRange(0, 100)
        self.spin_sp_dil.setValue(1)
        self.spin_sp_dil.setSuffix(" 點")
        self.spin_sp_dil.setToolTip("尖峰兩側一併去除的點數")
        self.cmb_sp_comp = QComboBox()
        self.cmb_sp_comp.addItem("只檢查 |S|", "abs")
        self.cmb_sp_comp.addItem("檢查 |S|、Re、Im", "all")
        self.txt_excl = QLineEdit()
        self.txt_excl.setPlaceholderText("排除頻段 GHz，例如 3.0564-3.0569, 3.0528-3.0532")
        self.chk_excl_drag = QCheckBox("在切片圖拖曳 → 新增排除頻段")
        self.btn_excl_clear = QPushButton("清除排除")
        self.lbl_clean = QLabel("")
        self.lbl_clean.setStyleSheet("color: gray;")
        r = 0
        cl.addWidget(self.chk_spike, r, 0, 1, 3); r += 1
        cl.addWidget(QLabel("視窗半寬"), r, 0); cl.addWidget(self.spin_sp_half, r, 1, 1, 2); r += 1
        cl.addWidget(QLabel("門檻"), r, 0); cl.addWidget(self.spin_sp_k, r, 1, 1, 2); r += 1
        cl.addWidget(QLabel("擴張"), r, 0); cl.addWidget(self.spin_sp_dil, r, 1, 1, 2); r += 1
        cl.addWidget(QLabel("檢查分量"), r, 0); cl.addWidget(self.cmb_sp_comp, r, 1, 1, 2); r += 1
        cl.addWidget(QLabel("手動排除"), r, 0); cl.addWidget(self.txt_excl, r, 1, 1, 2); r += 1
        cl.addWidget(self.chk_excl_drag, r, 0, 1, 2); cl.addWidget(self.btn_excl_clear, r, 2); r += 1
        cl.addWidget(self.lbl_clean, r, 0, 1, 3)
        return g

    def _build_formula_group(self):
        g = QGroupBox("② 公式")
        fl = QGridLayout(g)
        self.btn_formula = QPushButton("載入公式 .py…")
        self.btn_reload = QPushButton("重新載入")
        self.btn_builder = QPushButton("公式產生器…")
        self.btn_builder.setToolTip("輸入理想 S 參數模型，自動加上 Fano 相位與環境誤差並產生公式檔")
        self.lbl_formula = QLabel("尚未載入")
        self.lbl_formula.setWordWrap(True)
        self.cmb_func = QComboBox()
        self.lbl_sig = QLabel("")
        self.lbl_sig.setWordWrap(True)
        self.lbl_sig.setStyleSheet("color: gray;")
        self.btn_library = QPushButton("公式庫…")
        self.btn_library.setStyleSheet("font-weight: bold;")
        self.btn_library.setToolTip("從已加入的公式中選擇（顯示公式圖與備註）")
        self.btn_add_lib = QPushButton("加入公式庫")
        self.img_formula = FormulaImage(fontsize=14, max_height=160, fit_width=True)
        self.img_formula.setVisible(False)
        self.lbl_lib_notes = QLabel("")
        self.lbl_lib_notes.setWordWrap(True)
        self.lbl_lib_notes.setStyleSheet("color: #555;")
        self.lbl_lib_notes.setVisible(False)
        fl.addWidget(self.btn_library, 0, 0); fl.addWidget(self.btn_builder, 0, 1)
        fl.addWidget(self.btn_formula, 1, 0); fl.addWidget(self.btn_reload, 1, 1)
        fl.addWidget(self.lbl_formula, 2, 0, 1, 2)
        fl.addWidget(QLabel("函式"), 3, 0); fl.addWidget(self.cmb_func, 3, 1)
        fl.addWidget(self.lbl_sig, 4, 0, 1, 2)
        fl.addWidget(self.img_formula, 5, 0, 1, 2)
        fl.addWidget(self.lbl_lib_notes, 6, 0, 1, 2)
        fl.addWidget(self.btn_add_lib, 7, 0, 1, 2)
        return g

    def _build_option_group(self):
        g = QGroupBox("③ 擬合設定")
        sl = QGridLayout(g)
        self.chk_weight = QCheckBox("谷底加權  σ = |S| + ε")
        self.chk_weight.setChecked(True)
        self.spin_eps = QDoubleSpinBox()
        self.spin_eps.setDecimals(4); self.spin_eps.setRange(1e-4, 10); self.spin_eps.setValue(0.05)
        self.cmb_method = QComboBox(); self.cmb_method.addItems(["trf", "dogbox"])
        self.cmb_loss = QComboBox(); self.cmb_loss.addItems(["linear", "soft_l1", "huber", "cauchy", "arctan"])
        self.spin_maxfev = QSpinBox(); self.spin_maxfev.setRange(100, 10_000_000); self.spin_maxfev.setValue(300000)
        self.cmb_tol = QComboBox(); self.cmb_tol.addItems(["1e-12", "1e-10", "1e-8", "1e-6"])
        sl.addWidget(self.chk_weight, 0, 0, 1, 2)
        sl.addWidget(QLabel("ε"), 1, 0); sl.addWidget(self.spin_eps, 1, 1)
        sl.addWidget(QLabel("method"), 2, 0); sl.addWidget(self.cmb_method, 2, 1)
        sl.addWidget(QLabel("loss"), 3, 0); sl.addWidget(self.cmb_loss, 3, 1)
        sl.addWidget(QLabel("maxfev"), 4, 0); sl.addWidget(self.spin_maxfev, 4, 1)
        sl.addWidget(QLabel("ftol / xtol"), 5, 0); sl.addWidget(self.cmb_tol, 5, 1)
        return g

    def _build_action_group(self):
        g = QGroupBox("④ 執行")
        al = QGridLayout(g)
        self.btn_guess = QPushButton("自動初值")
        self.btn_preview = QPushButton("繪製初值曲線")
        self.btn_fit = QPushButton("開始擬合")
        self.btn_fit.setStyleSheet("font-weight: bold; padding: 6px;")
        self.btn_cancel = QPushButton("中止")
        self.lbl_fit_progress = QLabel("")
        self.btn_apply = QPushButton("擬合值 → 初值")
        self.btn_copy = QPushButton("複製結果文字")
        self.btn_save_cfg = QPushButton("儲存參數設定")
        self.btn_load_cfg = QPushButton("載入參數設定")
        self.btn_export = QPushButton("匯出結果 CSV")
        al.addWidget(self.btn_guess, 0, 0); al.addWidget(self.btn_preview, 0, 1)
        al.addWidget(self.btn_fit, 1, 0); al.addWidget(self.btn_cancel, 1, 1)
        al.addWidget(self.lbl_fit_progress, 2, 0, 1, 2)
        al.addWidget(self.btn_apply, 3, 0); al.addWidget(self.btn_copy, 3, 1)
        al.addWidget(self.btn_save_cfg, 4, 0); al.addWidget(self.btn_load_cfg, 4, 1)
        al.addWidget(self.btn_export, 5, 0, 1, 2)
        return g

    def _build_output_group(self):
        g = QGroupBox("擬合結果輸出")
        ol = QGridLayout(g)
        self.txt_outdir = QLineEdit()
        self.txt_outdir.setPlaceholderText("留空 = 數據檔旁的 fit_results 資料夾")
        self.btn_outdir = QPushButton("瀏覽…")
        self.btn_outdir_open = QPushButton("開啟")
        self.lbl_outdir = QLabel("")
        self.lbl_outdir.setWordWrap(True)
        self.lbl_outdir.setStyleSheet("color: gray;")
        self.chk_auto_single = QCheckBox("單次擬合完成後自動存檔（CSV + PNG）")
        self.chk_auto_batch = QCheckBox("連續擬合完成後自動匯出（CSV + 參數圖）")
        ol.addWidget(QLabel("資料夾"), 0, 0); ol.addWidget(self.txt_outdir, 0, 1, 1, 2)
        ol.addWidget(self.btn_outdir, 1, 1); ol.addWidget(self.btn_outdir_open, 1, 2)
        ol.addWidget(self.lbl_outdir, 2, 0, 1, 3)
        ol.addWidget(self.chk_auto_single, 3, 0, 1, 3)
        ol.addWidget(self.chk_auto_batch, 4, 0, 1, 3)
        return g

    def _build_batch_group(self):
        g = QGroupBox("⑤ 連續擬合")
        bl = QGridLayout(g)
        self.spin_b_start = QSpinBox()
        self.spin_b_end = QSpinBox()
        self.btn_b_start_cur = QPushButton("目前")
        self.btn_b_end_cur = QPushButton("目前")
        self.spin_b_step = QSpinBox()
        self.spin_b_step.setRange(1, 100000)
        self.cmb_b_track = QComboBox()
        self.cmb_b_track2 = QComboBox()
        self.spin_b_w2 = QDoubleSpinBox()
        self.spin_b_w2.setDecimals(4)
        self.spin_b_w2.setRange(0.0001, 1e6)
        self.spin_b_w2.setValue(10.0)
        self.spin_b_w2.setSuffix(" MHz")
        self.spin_b_w2.setKeyboardTracking(False)
        self.btn_b_w2_same = QPushButton("=視窗1")
        self.spin_b_off2 = QDoubleSpinBox()
        self.spin_b_off2.setDecimals(4)
        self.spin_b_off2.setRange(-1e6, 1e6)
        self.spin_b_off2.setSuffix(" MHz")
        self.spin_b_off2.setKeyboardTracking(False)
        self.cmb_b_init = QComboBox()
        for key, label in INIT_MODES.items():
            self.cmb_b_init.addItem(label, key)
        self.chk_b_bound = QCheckBox("頻率參數邊界 = 視窗範圍")
        self.chk_b_bound.setChecked(True)
        self.spin_b_r2 = QDoubleSpinBox()
        self.spin_b_r2.setDecimals(3)
        self.spin_b_r2.setRange(-10, 1)
        self.spin_b_r2.setSingleStep(0.05)
        self.spin_b_r2.setValue(0.9)
        self.chk_b_stop = QCheckBox("失敗時停止")
        self.roll_table = RollTable()
        self.chk_roll_clip = QCheckBox("滾動邊界不超出參數表邊界")
        self.lbl_b_window = QLabel("")
        self.lbl_b_window.setWordWrap(True)
        self.lbl_b_window.setStyleSheet("color: gray;")
        self.btn_batch = QPushButton("開始連續擬合")
        self.btn_batch.setStyleSheet("font-weight: bold; padding: 6px;")
        self.btn_batch_cancel = QPushButton("中止")
        self.batch_progress = QProgressBar()
        self.batch_progress.setFormat("%v / %m")
        self.batch_progress.setValue(0)

        r = 0
        bl.addWidget(QLabel("起始切片"), r, 0); bl.addWidget(self.spin_b_start, r, 1); bl.addWidget(self.btn_b_start_cur, r, 2); r += 1
        bl.addWidget(QLabel("結束切片"), r, 0); bl.addWidget(self.spin_b_end, r, 1); bl.addWidget(self.btn_b_end_cur, r, 2); r += 1
        bl.addWidget(QLabel("間隔"), r, 0); bl.addWidget(self.spin_b_step, r, 1, 1, 2); r += 1
        bl.addWidget(QLabel("追蹤參數 1"), r, 0); bl.addWidget(self.cmb_b_track, r, 1, 1, 2); r += 1
        bl.addWidget(QLabel("追蹤參數 2"), r, 0); bl.addWidget(self.cmb_b_track2, r, 1, 1, 2); r += 1
        bl.addWidget(QLabel("視窗 2 寬度"), r, 0); bl.addWidget(self.spin_b_w2, r, 1); bl.addWidget(self.btn_b_w2_same, r, 2); r += 1
        bl.addWidget(QLabel("視窗 2 偏移"), r, 0); bl.addWidget(self.spin_b_off2, r, 1, 1, 2); r += 1
        bl.addWidget(QLabel("初值來源"), r, 0); bl.addWidget(self.cmb_b_init, r, 1, 1, 2); r += 1
        bl.addWidget(QLabel("R² 門檻"), r, 0); bl.addWidget(self.spin_b_r2, r, 1, 1, 2); r += 1
        bl.addWidget(self.chk_b_bound, r, 0, 1, 3); r += 1
        bl.addWidget(self.chk_b_stop, r, 0, 1, 3); r += 1
        lbl_roll = QLabel("參數滾動追蹤（例如 phi）：初值 = 上一片值（或外插），邊界 = 初值 ± 範圍")
        lbl_roll.setWordWrap(True)
        bl.addWidget(lbl_roll, r, 0, 1, 3); r += 1
        bl.addWidget(self.roll_table, r, 0, 1, 3); r += 1
        bl.addWidget(self.chk_roll_clip, r, 0, 1, 3); r += 1
        bl.addWidget(self.lbl_b_window, r, 0, 1, 3); r += 1
        bl.addWidget(self.btn_batch, r, 0, 1, 2); bl.addWidget(self.btn_batch_cancel, r, 2); r += 1
        bl.addWidget(self.batch_progress, r, 0, 1, 3); r += 1
        note = QLabel("視窗 1：寬度 = 目前遮罩寬度，中心 = 上一片追蹤參數 1 + "
                      "（遮罩中心 − 追蹤參數 1 初值）。\n"
                      "視窗 2：中心 = 上一片追蹤參數 2 + 偏移，寬度自訂（預覽圖橘色虛線）。\n"
                      "啟用追蹤參數 2 時，單次擬合與連續擬合都使用兩視窗的聯集。"
                      "起始片使用參數表初值。")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        bl.addWidget(note, r, 0, 1, 3)
        return g
