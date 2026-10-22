import os
import time
import queue as _queue
import subprocess
import multiprocessing as mp

import pandas as pd
import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure

from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QGridLayout, QLabel, QLineEdit, QPushButton,
                             QComboBox, QFileDialog, QTextEdit, QGroupBox,
                             QTabWidget, QTableWidget, QTableWidgetItem,
                             QHeaderView, QMessageBox, QAbstractItemView, QCheckBox,
                             QScrollArea, QProgressBar, QInputDialog, QStatusBar)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon

from config import (SCRIPT_DIR, RESOURCE_DIR, SETTINGS_FILE, ConfigManager, APP_VERSION, DEFAULT_LOCAL_ROOT,
                    DEFAULT_AEDT_VERSION, SUPPORTED_AEDT_VERSIONS)
from hfss_worker import process_entry, stop_entry, STEP_LABELS
import yig
from utils import (get_cpu_info, backup_folder_detached, parse_freq_ghz, parse_freq_list_ghz,
                   is_valid_project_name, project_paths, find_existing_project)

_MP = mp.get_context("spawn")


def _tip(widget, text):
    widget.setToolTip(text)
    return widget


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"HFSS自動化工具 {APP_VERSION} (AEDT 2026 R1)")
        self.resize(780, 1000)

        icon_path = os.path.join(RESOURCE_DIR, "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.physical_cores, self.logical_cores = get_cpu_info()
        self.safe_limit = max(1, self.physical_cores - 2)

        # 工作程序狀態
        self.proc = None
        self.msg_queue = None
        self.stop_proc = None
        self.current_task = None
        self.task_start_time = 0
        self.confirmed_project = None      # 本次開啟後已確認 (開啟舊檔或新建) 的專案名稱
        self.help_dialog = None

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(100)
        self.poll_timer.timeout.connect(self._poll_worker)
        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(1000)
        self.clock_timer.timeout.connect(self._update_clock)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tab_main = QWidget()
        self.tab_adv = QWidget()
        self.tab_yig = QWidget()
        self.tab_plot = QWidget()
        for widget, title in ((self.tab_main, "主控制面板"), (self.tab_adv, "進階設定"), (self.tab_yig, "YIG 磁性球")):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(widget)
            self.tabs.addTab(scroll, title)
        self.tabs.addTab(self.tab_plot, "S參數繪製面板")

        self.init_main_tab()
        self.init_advanced_tab()
        self.init_yig_tab()
        self.tabs.currentChanged.connect(self._yig_tab_shown)
        self.init_plot_tab()
        self.init_status_bar()
        from update_ui import UpdateController          # v3.3：開啟時檢查新版、推播（跟量測程式一樣）
        self.updater = UpdateController(self)
        from usage import UsageReporter                 # v3.4：平台「授權」頁看得到這台電腦有沒有占用 HFSS 授權
        self.usage = UsageReporter(self, self._usage_task)
        self.dxf_editor = None
        self._register_settings()
        self.load_settings()
        self._refresh_project_label()

    # 深色 / 淺色模式下都清楚的強調色與次要文字色
    def _is_dark(self):
        return self.palette().window().color().lightness() < 128

    def _accent(self):
        return "#64B5F6" if self._is_dark() else "#1565C0"

    def _muted(self):
        return "#A3B0B8" if self._is_dark() else "#546E7A"

    # ==================================================================
    # 主控制面板
    # ==================================================================
    def init_main_tab(self):
        main_layout = QVBoxLayout(self.tab_main)

        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("選擇建模方式:"))
        self.combo_type = QComboBox()
        self.combo_type.addItems(["MS", "CPW"])
        type_layout.addWidget(self.combo_type)
        type_layout.addSpacing(20)
        type_layout.addWidget(QLabel("AEDT 版本:"))
        self.combo_aedt_version = QComboBox()
        self.combo_aedt_version.setEditable(True)
        self.combo_aedt_version.addItems(SUPPORTED_AEDT_VERSIONS)
        self.combo_aedt_version.setCurrentText(DEFAULT_AEDT_VERSION)
        type_layout.addWidget(self.combo_aedt_version)
        type_layout.addStretch()
        main_layout.addLayout(type_layout)

        # ---------------- 路徑 ----------------
        path_group = QGroupBox("專案路徑設置 (需全英路徑)")
        path_layout = QGridLayout()

        self.edit_dxf = QLineEdit()
        btn_dxf = QPushButton("瀏覽...")
        btn_dxf.clicked.connect(lambda: self.browse_file(self.edit_dxf))
        path_layout.addWidget(QLabel("DXF 檔案路徑 :"), 0, 0)
        dxf_row = QHBoxLayout()
        dxf_row.setContentsMargins(0, 0, 0, 0)
        dxf_row.addWidget(self.edit_dxf, 1)
        btn_dxf_edit = QPushButton("✏ 編輯")
        btn_dxf_edit.setToolTip("DXF 編輯器：改板材大小、用節點畫微波線、螺絲孔 / 過孔（沒有檔案時從範本開始）")
        btn_dxf_edit.clicked.connect(self.open_dxf_editor)
        dxf_row.addWidget(btn_dxf_edit)
        path_layout.addLayout(dxf_row, 0, 1)
        path_layout.addWidget(btn_dxf, 0, 2)

        self.chk_enable_sync = QCheckBox("啟用 NAS 遠端同步 :")
        self.chk_enable_sync.setChecked(True)
        self.chk_enable_sync.setStyleSheet("font-weight: bold; color: #4CAF50;")
        self.edit_save_dir = QLineEdit(os.path.join(os.path.expanduser("~"), "Desktop", "HFSS_Storage"))
        btn_dir = QPushButton("瀏覽...")
        btn_dir.clicked.connect(lambda: self.browse_dir(self.edit_save_dir))
        self.chk_enable_sync.toggled.connect(self.edit_save_dir.setEnabled)
        self.chk_enable_sync.toggled.connect(btn_dir.setEnabled)
        path_layout.addWidget(self.chk_enable_sync, 1, 0)
        path_layout.addWidget(self.edit_save_dir, 1, 1)
        path_layout.addWidget(btn_dir, 1, 2)

        self.edit_local_temp = QLineEdit(DEFAULT_LOCAL_ROOT)
        btn_local_temp = QPushButton("瀏覽...")
        btn_local_temp.clicked.connect(lambda: self.browse_dir(self.edit_local_temp))
        path_layout.addWidget(QLabel("本地專案根目錄 :"), 2, 0)
        path_layout.addWidget(self.edit_local_temp, 2, 1)
        path_layout.addWidget(btn_local_temp, 2, 2)

        self.edit_project_name = _tip(QLineEdit("HFSS_Project"), "只能使用英文、數字、底線、減號")
        btn_open_folder = QPushButton("📂 開啟資料夾")
        btn_open_folder.clicked.connect(self.open_project_folder)
        path_layout.addWidget(QLabel("專案名稱:"), 3, 0)
        path_layout.addWidget(self.edit_project_name, 3, 1)
        path_layout.addWidget(btn_open_folder, 3, 2)

        self.edit_csv_name = QLineEdit("S_para.csv")
        path_layout.addWidget(QLabel("CSV 檔名:"), 4, 0)
        path_layout.addWidget(self.edit_csv_name, 4, 1)

        self.lbl_project_dir = QLabel("")
        self.lbl_project_dir.setStyleSheet(f"color: {self._muted()}; font-size: 11px;")
        self.lbl_project_dir.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_layout.addWidget(self.lbl_project_dir, 5, 0, 1, 3)

        for w in (self.edit_project_name, self.edit_local_temp, self.edit_save_dir):
            w.textChanged.connect(self._on_project_location_changed)
        self.edit_csv_name.textChanged.connect(self._refresh_project_label)
        self.chk_enable_sync.toggled.connect(self._on_project_location_changed)

        path_group.setLayout(path_layout)
        main_layout.addWidget(path_group)

        # ---------------- 幾何 ----------------
        geom_group = QGroupBox("材料與幾何參數")
        geom_layout = QGridLayout()
        self.edit_thick_top, self.edit_thick_die, self.edit_thick_bot = QLineEdit("0.035"), QLineEdit("0.813"), QLineEdit("0.035")
        geom_layout.addWidget(QLabel("Top 厚(mm):"), 0, 0); geom_layout.addWidget(self.edit_thick_top, 0, 1)
        geom_layout.addWidget(QLabel("Die 厚(mm):"), 0, 2); geom_layout.addWidget(self.edit_thick_die, 0, 3)
        geom_layout.addWidget(QLabel("Bot 厚(mm):"), 0, 4); geom_layout.addWidget(self.edit_thick_bot, 0, 5)

        self.edit_mat_cu, self.edit_mat_die = QLineEdit("copper"), QLineEdit("Rogers RO4003 (tm)")
        geom_layout.addWidget(QLabel("導體材質:"), 1, 0); geom_layout.addWidget(self.edit_mat_cu, 1, 1)
        geom_layout.addWidget(QLabel("介電質材質:"), 1, 2); geom_layout.addWidget(self.edit_mat_die, 1, 3)

        self.edit_port_scale = QLineEdit("1.0")
        self.lbl_suggest_scale = QLabel("")
        self.lbl_suggest_scale.setStyleSheet("color: #FF9800; font-weight: bold;")
        geom_layout.addWidget(QLabel("Port縮放基數:"), 1, 4)
        h_scale_layout = QHBoxLayout()
        h_scale_layout.addWidget(self.edit_port_scale)
        h_scale_layout.addWidget(self.lbl_suggest_scale)
        geom_layout.addLayout(h_scale_layout, 1, 5)
        geom_group.setLayout(geom_layout)
        main_layout.addWidget(geom_group)

        # ---------------- 求解器 ----------------
        sim_group = QGroupBox("求解器與掃頻設定")
        sim_layout = QGridLayout()
        self.combo_adaptive_mode = QComboBox()
        self.combo_adaptive_mode.addItems(["Single", "Multi-Frequencies", "Broadband"])
        self.edit_adaptive_freqs = QLineEdit("9GHz")
        self.combo_sweep_mode = QComboBox()
        self.combo_sweep_mode.addItems(["Interpolating", "Fast", "Discrete"])
        sim_layout.addWidget(QLabel("Adaptive模式:"), 0, 0); sim_layout.addWidget(self.combo_adaptive_mode, 0, 1)
        sim_layout.addWidget(QLabel("Adaptive頻率:"), 0, 2); sim_layout.addWidget(self.edit_adaptive_freqs, 0, 3)
        sim_layout.addWidget(QLabel("Sweep模式:"), 0, 4); sim_layout.addWidget(self.combo_sweep_mode, 0, 5)
        self.combo_adaptive_mode.currentTextChanged.connect(self.update_adaptive_ui)

        self.edit_freq_start, self.edit_freq_stop, self.edit_freq_open = QLineEdit("2GHz"), QLineEdit("9GHz"), QLineEdit("5.5GHz")
        sim_layout.addWidget(QLabel("Sweep起始頻率:"), 1, 0); sim_layout.addWidget(self.edit_freq_start, 1, 1)
        sim_layout.addWidget(QLabel("Sweep結束頻率:"), 1, 2); sim_layout.addWidget(self.edit_freq_stop, 1, 3)
        sim_layout.addWidget(QLabel("空氣腔設定頻率:"), 1, 4); sim_layout.addWidget(self.edit_freq_open, 1, 5)

        self.edit_points, self.edit_passes, self.edit_deltas = QLineEdit("701"), QLineEdit("30"), QLineEdit("0.02")
        sim_layout.addWidget(QLabel("寬頻掃頻點數:"), 2, 0); sim_layout.addWidget(self.edit_points, 2, 1)
        sim_layout.addWidget(QLabel("最大迭代次數:"), 2, 2); sim_layout.addWidget(self.edit_passes, 2, 3)
        sim_layout.addWidget(QLabel("Max Delta S:"), 2, 4); sim_layout.addWidget(self.edit_deltas, 2, 5)

        self.edit_max_refine, self.edit_min_converged = QLineEdit("30"), QLineEdit("2")
        sim_layout.addWidget(QLabel("每次網格細化 (%):"), 3, 0); sim_layout.addWidget(self.edit_max_refine, 3, 1)
        sim_layout.addWidget(QLabel("最小連續收斂次數:"), 3, 2); sim_layout.addWidget(self.edit_min_converged, 3, 3)
        self.btn_mode_help = QPushButton("❓ 模式說明 (含動畫)")
        self.btn_mode_help.setStyleSheet(f"font-weight: bold; color: {self._accent()};")
        self.btn_mode_help.clicked.connect(self.show_mode_help)
        sim_layout.addWidget(self.btn_mode_help, 3, 4, 1, 2)

        self.edit_field_freqs = QLineEdit("2.5, 5.5, 8.5")
        sim_layout.addWidget(QLabel("場型指定頻率 (GHz):"), 4, 0, 1, 2)
        sim_layout.addWidget(self.edit_field_freqs, 4, 2, 1, 4)

        self.chk_field_reuse = QCheckBox("場型沿用 S 參數的收斂網格 (免重新 Adaptive，大幅縮短場型模擬時間)")
        self.chk_field_reuse.setChecked(True)
        sim_layout.addWidget(self.chk_field_reuse, 5, 0, 1, 6)
        sim_group.setLayout(sim_layout)
        main_layout.addWidget(sim_group)

        # ---------------- HPC ----------------
        hpc_group = QGroupBox("HPC 運算效能資源分配")
        hpc_layout = QGridLayout()
        lbl_cpu_info = QLabel(f"🖥️ 系統偵測：{self.physical_cores} 核心 / {self.logical_cores} 執行緒 (安全上限: {self.safe_limit})")
        lbl_cpu_info.setStyleSheet("color: #4CAF50; font-weight: bold;")
        hpc_layout.addWidget(lbl_cpu_info, 0, 0, 1, 4)
        self.edit_hpc_total = QLineEdit(str(self.safe_limit))
        hpc_layout.addWidget(QLabel("總核心預算 (Total):"), 1, 0); hpc_layout.addWidget(self.edit_hpc_total, 1, 1)
        self.edit_hpc_tasks = QLineEdit("1")
        hpc_layout.addWidget(QLabel("平行任務數 (Tasks):"), 1, 2); hpc_layout.addWidget(self.edit_hpc_tasks, 1, 3)
        self.edit_hpc_cores = QLineEdit(str(self.safe_limit))
        hpc_layout.addWidget(QLabel("每任務核心 (Cores):"), 2, 0); hpc_layout.addWidget(self.edit_hpc_cores, 2, 1)
        self.edit_hpc_gpus = QLineEdit("0")
        hpc_layout.addWidget(QLabel("GPU 數量 (需授權):"), 2, 2); hpc_layout.addWidget(self.edit_hpc_gpus, 2, 3)
        self.edit_hpc_total.editingFinished.connect(self.sync_hpc_from_total)
        self.edit_hpc_tasks.textChanged.connect(self.sync_hpc_from_tasks)
        self.edit_hpc_cores.textChanged.connect(self.sync_hpc_from_cores)
        lbl_hpc_tip = QLabel("💡 Tasks=1 時由 AEDT 自動分配；Tasks>1 時強制使用您指定的分配 (Discrete/Interpolating 掃頻可平行解多個頻點)。")
        lbl_hpc_tip.setWordWrap(True)
        lbl_hpc_tip.setStyleSheet("color: #888888; font-size: 11px;")
        hpc_layout.addWidget(lbl_hpc_tip, 3, 0, 1, 4)
        hpc_group.setLayout(hpc_layout)
        main_layout.addWidget(hpc_group)

        # ---------------- 按鈕 ----------------
        btn_layout = QVBoxLayout()
        pipe_row = QHBoxLayout()
        self.btn_pipeline = QPushButton("⚡ 一鍵全流程：建模 → S參數 → 場型")
        self.btn_pipeline.setStyleSheet("font-size: 14px; font-weight: bold; padding: 10px; background-color: #1565C0; color: white;")
        pipe_row.addWidget(self.btn_pipeline, 3)
        opt_col = QVBoxLayout()
        self.chk_pipe_gif = QCheckBox("含 GIF 匯出")
        self.chk_pipe_close = QCheckBox("完成後關閉並備份")
        opt_col.addWidget(self.chk_pipe_gif)
        opt_col.addWidget(self.chk_pipe_close)
        pipe_row.addLayout(opt_col, 1)
        btn_layout.addLayout(pipe_row)

        self.btn_open = QPushButton("開啟專案")
        self.btn_build = QPushButton("建立 3D 模型與邊界條件")
        self.btn_sim_s = QPushButton("執行 S參數 寬頻模擬")
        self.btn_sim_fields = QPushButton("執行 場型模擬")
        self.btn_gif = QPushButton("匯出場型 GIF 動畫")
        self.btn_close = QPushButton("儲存、備份並關閉專案")
        for btn in [self.btn_open, self.btn_build, self.btn_sim_s, self.btn_sim_fields, self.btn_gif]:
            btn.setStyleSheet("font-size: 13px; font-weight: bold; padding: 8px;")
            btn_layout.addWidget(btn)
        self.btn_close.setStyleSheet("font-size: 13px; font-weight: bold; padding: 8px; background-color: #D32F2F; color: white;")
        btn_layout.addWidget(self.btn_close)

        self.step_buttons = [self.btn_build, self.btn_sim_s, self.btn_sim_fields, self.btn_gif, self.btn_close]
        for b in self.step_buttons:
            b.setEnabled(False)

        self.btn_open.clicked.connect(lambda: self.run_task('OPEN'))
        self.btn_build.clicked.connect(lambda: self.run_task('BUILD'))
        self.btn_sim_s.clicked.connect(lambda: self.run_task('SIM_S'))
        self.btn_sim_fields.clicked.connect(lambda: self.run_task('SIM_FIELDS'))
        self.btn_gif.clicked.connect(lambda: self.run_task('EXPORT_GIF'))
        self.btn_close.clicked.connect(lambda: self.run_task('CLOSE'))
        self.btn_pipeline.clicked.connect(self.run_pipeline)
        main_layout.addLayout(btn_layout)

        # ---------------- 執行狀態 ----------------
        run_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(14)
        self.lbl_stage = QLabel("待命")
        self.lbl_stage.setStyleSheet("font-weight: bold;")
        self.lbl_elapsed = QLabel("00:00")
        self.lbl_elapsed.setStyleSheet("font-family: Consolas;")
        self.btn_abort = QPushButton("🛑 中止")
        self.btn_abort.setEnabled(False)
        self.btn_abort.setStyleSheet("font-weight: bold; padding: 4px 12px;")
        self.btn_abort.clicked.connect(self.abort_task)
        run_row.addWidget(self.lbl_stage)
        run_row.addWidget(self.progress, 1)
        run_row.addWidget(self.lbl_elapsed)
        run_row.addWidget(self.btn_abort)
        main_layout.addLayout(run_row)

        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setMinimumHeight(220)
        self.log_console.setStyleSheet("background-color: #1e1e1e; color: #00FF00; font-family: Consolas;")
        main_layout.addWidget(QLabel("執行日誌 (執行中介面可正常操作，HFSS 在獨立程序中運算):"))
        main_layout.addWidget(self.log_console)

    # ==================================================================
    # 進階設定
    # ==================================================================
    def init_advanced_tab(self):
        lay = QVBoxLayout(self.tab_adv)
        note = QLabel("以下參數都有合理的預設值，不確定時保持預設即可。滑鼠停在欄位上可看說明，"
                      "詳細原理見主控制面板的「❓ 模式說明」。")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {self._muted()};")
        lay.addWidget(note)

        # ---- Adaptive ----
        g = QGroupBox("Adaptive 自適應網格 (進階)")
        gl = QGridLayout()
        self.edit_min_passes = _tip(QLineEdit("1"), "至少要跑幾個 pass 才允許判定收斂。結構有強共振時可設 3~4，避免過早停止。")
        self.combo_basis = _tip(QComboBox(), "有限元素基底函數階數。Mixed 會在大元素用高階、小元素用低階，通常最有效率。")
        self.combo_basis.addItems(["Mixed Order", "First Order", "Second Order", "Zero Order"])
        self.chk_lambda = _tip(QCheckBox("初始網格 Lambda 細化"), "開始 adaptive 前，先依波長把網格切到指定大小，可減少 pass 數。")
        self.chk_lambda.setChecked(True)
        self.edit_lambda_target = _tip(QLineEdit("0.3333"), "初始網格最大元素尺寸 (以材料中波長為單位)。0.3333 = λ/3；0.2 更細但初始網格較大。")
        self.combo_solver = _tip(QComboBox(), "Direct：穩定、記憶體用量大。Iterative：記憶體少，適合大型模型，但病態問題可能收斂較慢。")
        self.combo_solver.addItems(["Direct (直接法)", "Iterative (迭代法)"])
        gl.addWidget(QLabel("最少 Pass 數:"), 0, 0); gl.addWidget(self.edit_min_passes, 0, 1)
        gl.addWidget(QLabel("基底函數階數:"), 0, 2); gl.addWidget(self.combo_basis, 0, 3)
        gl.addWidget(self.chk_lambda, 1, 0, 1, 2)
        gl.addWidget(QLabel("Lambda Target:"), 1, 2); gl.addWidget(self.edit_lambda_target, 1, 3)
        gl.addWidget(QLabel("矩陣求解器:"), 2, 0); gl.addWidget(self.combo_solver, 2, 1, 1, 3)
        self.chk_lambda.toggled.connect(self.edit_lambda_target.setEnabled)
        g.setLayout(gl)
        lay.addWidget(g)

        # ---- Sweep ----
        g = QGroupBox("Sweep 掃頻 (進階)")
        gl = QGridLayout()
        self.edit_interp_tol = _tip(QLineEdit("0.5"), "Interpolating 的誤差容許 (%)。越小越準，但需要更多求解點。")
        self.edit_interp_max = _tip(QLineEdit("250"), "Interpolating 最多求解幾個頻點。到上限仍未收斂會停止並給警告。")
        self.chk_passivity = _tip(QCheckBox("強制被動性 (Enforce Passivity)"), "確保插值結果不會出現 |S|>1 的非物理增益，匯出給電路模擬時建議開啟。")
        self.chk_passivity.setChecked(True)
        self.chk_causality = _tip(QCheckBox("強制因果性 (Enforce Causality)"), "時域 (TDR / 眼圖) 分析才需要，一般 S 參數不必開。")
        self.chk_sweep_fields = _tip(QCheckBox("S 參數掃頻存場 (Save Fields)"), "Discrete / Fast 掃頻時保存每個頻點的場資料。檔案會非常大，只有需要看任意頻率場圖時才開。")
        gl.addWidget(QLabel("插值誤差容許 (%):"), 0, 0); gl.addWidget(self.edit_interp_tol, 0, 1)
        gl.addWidget(QLabel("插值最大求解數:"), 0, 2); gl.addWidget(self.edit_interp_max, 0, 3)
        gl.addWidget(self.chk_passivity, 1, 0, 1, 2); gl.addWidget(self.chk_causality, 1, 2, 1, 2)
        gl.addWidget(self.chk_sweep_fields, 2, 0, 1, 4)
        g.setLayout(gl)
        lay.addWidget(g)

        # ---- Port ----
        g = QGroupBox("Wave Port")
        gl = QGridLayout()
        self.edit_port_modes = _tip(QLineEdit("1"), "每個 Port 求解的模態數。一般傳輸線 1 即可；Port 太大或有高階模時才增加。")
        self.edit_port_z = _tip(QLineEdit("50"), "重正規化的參考阻抗 (Ω)。")
        self.chk_port_renorm = _tip(QCheckBox("重正規化到參考阻抗"), "開啟：S 參數以上面的參考阻抗表示 (與 VNA 量測一致)。關閉：以 Port 本身的特性阻抗表示。")
        self.chk_port_renorm.setChecked(True)
        self.edit_port_deembed = _tip(QLineEdit("0"), "De-embed 距離 (mm)。正值會把參考面往結構內移動，用來扣掉 Port 到 DUT 之間的傳輸線。")
        self.combo_port_zdef = _tip(QComboBox(), "特性阻抗定義。Zpi (功率-電流) 最常用於微帶/共面波導。")
        self.combo_port_zdef.addItems(["Zpi", "Zpv", "Zvi"])
        gl.addWidget(QLabel("模態數:"), 0, 0); gl.addWidget(self.edit_port_modes, 0, 1)
        gl.addWidget(QLabel("參考阻抗 (Ω):"), 0, 2); gl.addWidget(self.edit_port_z, 0, 3)
        gl.addWidget(self.chk_port_renorm, 1, 0, 1, 2)
        gl.addWidget(QLabel("特性阻抗定義:"), 1, 2); gl.addWidget(self.combo_port_zdef, 1, 3)
        gl.addWidget(QLabel("De-embed (mm):"), 2, 0); gl.addWidget(self.edit_port_deembed, 2, 1)
        g.setLayout(gl)
        lay.addWidget(g)

        # ---- 邊界 ----
        g = QGroupBox("邊界條件 / 空氣腔")
        gl = QGridLayout()
        self.combo_boundary = _tip(QComboBox(), "Radiation：一般用途，入射角大時會有少量反射。PML：吸收效果更好，適合天線 / 輻射強的結構，但網格較多。")
        self.combo_boundary.addItems(["Radiation", "PML"])
        self.chk_infinite_gp = _tip(QCheckBox("底面設為無限大地平面 (-Z)"), "把空氣腔底面當成無限大金屬地。板子背面完整接地、只關心上半空間時可減少網格。")
        gl.addWidget(QLabel("開放邊界類型:"), 0, 0); gl.addWidget(self.combo_boundary, 0, 1)
        gl.addWidget(self.chk_infinite_gp, 0, 2, 1, 2)
        g.setLayout(gl)
        lay.addWidget(g)

        # ---- 材料覆寫 ----
        g = QGroupBox("介電材料覆寫 (留空 = 使用材料庫數值)")
        gl = QGridLayout()
        self.edit_er = _tip(QLineEdit(""), "覆寫介電常數 εr。例如板材批次實測值、或與量測擬合後的修正值。")
        self.edit_tand = _tip(QLineEdit(""), "覆寫損耗正切 tanδ。")
        gl.addWidget(QLabel("εr:"), 0, 0); gl.addWidget(self.edit_er, 0, 1)
        gl.addWidget(QLabel("tanδ:"), 0, 2); gl.addWidget(self.edit_tand, 0, 3)
        g.setLayout(gl)
        lay.addWidget(g)

        # ---- GIF ----
        g = QGroupBox("場型 GIF 動畫 (輸出到 專案資料夾\\GIF)")
        gl = QGridLayout()
        self.edit_gif_frames = QLineEdit("18")
        self.combo_gif_view = QComboBox()
        self.combo_gif_view.addItems(["top", "isometric", "bottom", "front", "back", "left", "right"])
        self.edit_gif_ms = QLineEdit("80")
        self.edit_gif_w, self.edit_gif_h = QLineEdit("1280"), QLineEdit("720")
        gl.addWidget(QLabel("每週期幀數:"), 0, 0); gl.addWidget(self.edit_gif_frames, 0, 1)
        gl.addWidget(QLabel("視角:"), 0, 2); gl.addWidget(self.combo_gif_view, 0, 3)
        gl.addWidget(QLabel("每幀 (ms):"), 1, 0); gl.addWidget(self.edit_gif_ms, 1, 1)
        gl.addWidget(QLabel("解析度 (寬×高):"), 1, 2)
        wh = QHBoxLayout(); wh.addWidget(self.edit_gif_w); wh.addWidget(QLabel("×")); wh.addWidget(self.edit_gif_h)
        gl.addLayout(wh, 1, 3)
        g.setLayout(gl)
        lay.addWidget(g)

        lbl = QLabel(f"設定檔位置: {SETTINGS_FILE}")
        lbl.setStyleSheet("color: #888888; font-size: 11px;")
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(lbl)
        btn_reset = QPushButton("進階設定恢復預設值")
        btn_reset.clicked.connect(self.reset_advanced)
        lay.addWidget(btn_reset)
        lay.addStretch()

    # ==================================================================
    # YIG 磁性球
    # ==================================================================
    def init_yig_tab(self):
        lay = QVBoxLayout(self.tab_yig)
        self.chk_yig = QCheckBox("在訊號線上方放置 YIG 球 (建模時自動建立球體、材料、偏置磁場與網格設定)")
        self.chk_yig.setStyleSheet("font-weight: bold;")
        lay.addWidget(self.chk_yig)
        row = QHBoxLayout()
        self.btn_yig_build = QPushButton("🧲 建立 / 更新 YIG 球")
        self.btn_yig_build.setToolTip("在已經建好的模型上放 YIG 球（已經有的話換成目前的參數），不用重新建模。"
                                      "沒有模型時請先在主控制面板「建立 3D 模型」或勾著這個再建模。")
        self.btn_yig_build.clicked.connect(self._run_yig_task)
        # v3.5：跟「建立 3D 模型」一樣——開啟專案後才能按、每次任務結束都會重新打開（3.4 跑完任何任務後就一直是灰的）
        self.btn_yig_build.setEnabled(bool(self.btn_build.isEnabled()))
        self.step_buttons.append(self.btn_yig_build)
        row.addWidget(self.btn_yig_build)
        row.addWidget(QLabel("（不用重新建模；改了直徑、位置、偏置也是按這個）"))
        row.addStretch(1)
        lay.addLayout(row)

        g = QGroupBox("幾何")
        gl = QGridLayout(g)
        self.edit_yig_d = _tip(QLineEdit("0.4"), "YIG 球直徑 (mm)")
        self.edit_yig_gap = _tip(QLineEdit("0.1"), "球的底部到訊號線頂面的距離 (mm)。球心高度 = 走線頂面 + 間隙 + 半徑。")
        self.combo_yig_pos = QComboBox()
        self.combo_yig_pos.addItems(["訊號線中央 + 偏移", "絕對座標"])
        self.edit_yig_x, self.edit_yig_y = QLineEdit("0"), QLineEdit("0")
        _tip(self.edit_yig_x, "訊號線中央模式：相對走線中心的偏移 ΔX (mm)；絕對座標模式：球心 X (mm)")
        _tip(self.edit_yig_y, "訊號線中央模式：相對走線中心的偏移 ΔY (mm)；絕對座標模式：球心 Y (mm)")
        self.edit_yig_mesh = _tip(QLineEdit("0.05"), "球體內網格最大邊長 (mm)。建議 ≤ 直徑 / 8。")
        gl.addWidget(QLabel("直徑 (mm):"), 0, 0); gl.addWidget(self.edit_yig_d, 0, 1)
        gl.addWidget(QLabel("離走線間隙 (mm):"), 0, 2); gl.addWidget(self.edit_yig_gap, 0, 3)
        gl.addWidget(QLabel("位置:"), 1, 0); gl.addWidget(self.combo_yig_pos, 1, 1)
        self.lbl_yig_x, self.lbl_yig_y = QLabel("ΔX (mm):"), QLabel("ΔY (mm):")
        gl.addWidget(self.lbl_yig_x, 2, 0); gl.addWidget(self.edit_yig_x, 2, 1)
        gl.addWidget(self.lbl_yig_y, 2, 2); gl.addWidget(self.edit_yig_y, 2, 3)
        gl.addWidget(QLabel("球體網格邊長 (mm):"), 3, 0); gl.addWidget(self.edit_yig_mesh, 3, 1)
        self.combo_yig_pos.currentIndexChanged.connect(self._yig_pos_labels)
        lay.addWidget(g)

        g = QGroupBox("材料 (YIG)")
        gl = QGridLayout(g)
        self.edit_yig_ms = _tip(QLineEdit("0.175"), "飽和磁化 μ0·Ms = 4πMs (T)。YIG 約 0.175 T (1750 G)。")
        self.edit_yig_dh = _tip(QLineEdit("0.2"), "共振線寬 ΔH (Oe)。拋光單晶球約 0.2~0.5 Oe。")
        self.edit_yig_dhf = _tip(QLineEdit("9.4"), "ΔH 的量測頻率 (GHz)，留空則不設定。")
        self.edit_yig_er = QLineEdit("15")
        self.edit_yig_tand = QLineEdit("0.0002")
        self.edit_yig_g = _tip(QLineEdit("2.0"), "Landé g 因子，只用於計算 Kittel 頻率 (YIG ≈ 2.0)。")
        gl.addWidget(QLabel("4πMs (T):"), 0, 0); gl.addWidget(self.edit_yig_ms, 0, 1)
        gl.addWidget(QLabel("ΔH (Oe):"), 0, 2); gl.addWidget(self.edit_yig_dh, 0, 3)
        gl.addWidget(QLabel("ΔH 頻率 (GHz):"), 0, 4); gl.addWidget(self.edit_yig_dhf, 0, 5)
        gl.addWidget(QLabel("εr:"), 1, 0); gl.addWidget(self.edit_yig_er, 1, 1)
        gl.addWidget(QLabel("tanδ:"), 1, 2); gl.addWidget(self.edit_yig_tand, 1, 3)
        gl.addWidget(QLabel("Landé g:"), 1, 4); gl.addWidget(self.edit_yig_g, 1, 5)
        lay.addWidget(g)

        g = QGroupBox("偏置磁場 (均勻)")
        gl = QGridLayout(g)
        self.combo_yig_bmode = _tip(QComboBox(), "HFSS 的 Bias 欄位是球體內部場 (A/m)。選「外加磁場」時，程式自動扣除球體去磁場 Ms/3。")
        self.combo_yig_bmode.addItems(["外加磁場 μ0H (mT)", "HFSS 內部場 Bias (A/m)"])
        self.edit_yig_bias = QLineEdit("250")
        self.combo_yig_bdir = QComboBox()
        self.combo_yig_bdir.addItems(list(yig.BIAS_DIRECTIONS.keys()))
        gl.addWidget(QLabel("輸入方式:"), 0, 0); gl.addWidget(self.combo_yig_bmode, 0, 1)
        gl.addWidget(QLabel("數值:"), 0, 2); gl.addWidget(self.edit_yig_bias, 0, 3)
        gl.addWidget(QLabel("方向:"), 0, 4); gl.addWidget(self.combo_yig_bdir, 0, 5)
        self.lbl_yig_calc = QLabel("")
        self.lbl_yig_calc.setWordWrap(True)
        self.lbl_yig_calc.setStyleSheet(f"color: {self._accent()}; font-weight: bold;")
        gl.addWidget(self.lbl_yig_calc, 1, 0, 1, 6)
        lay.addWidget(g)

        g = QGroupBox("求解 (高 Q 共振)")
        gl = QGridLayout(g)
        self.chk_yig_adapt = _tip(QCheckBox("自動把 Kittel 頻率加入 Adaptive 頻點 (Single 會自動改成 Multi-Frequencies)"),
                                  "讓網格在共振時被驗證；否則共振頻率與深度可能不準。")
        self.chk_yig_adapt.setChecked(True)
        self.chk_yig_fine = QCheckBox("在 Kittel 頻率附近另外細掃 (另存 *_kittel.csv)")
        self.chk_yig_fine.setChecked(True)
        self.edit_yig_span = _tip(QLineEdit("50"), "細掃範圍 = Kittel ± 此值 (MHz)")
        self.edit_yig_pts = QLineEdit("1001")
        self.combo_yig_ftype = QComboBox()
        self.combo_yig_ftype.addItems(["Interpolating", "Discrete", "Fast"])
        self.edit_yig_ftol = _tip(QLineEdit("0.1"), "細掃為 Interpolating 時的誤差容許 (%)")
        gl.addWidget(self.chk_yig_adapt, 0, 0, 1, 6)
        gl.addWidget(self.chk_yig_fine, 1, 0, 1, 6)
        gl.addWidget(QLabel("範圍 ± (MHz):"), 2, 0); gl.addWidget(self.edit_yig_span, 2, 1)
        gl.addWidget(QLabel("點數:"), 2, 2); gl.addWidget(self.edit_yig_pts, 2, 3)
        gl.addWidget(QLabel("方式:"), 3, 0); gl.addWidget(self.combo_yig_ftype, 3, 1)
        gl.addWidget(QLabel("誤差容許 (%):"), 3, 2); gl.addWidget(self.edit_yig_ftol, 3, 3)
        lay.addWidget(g)

        note = QLabel("💡 YIG 線寬只有 MHz 等級：一般寬頻掃頻 (數 MHz 間距) 幾乎一定看不到共振，請以 Kittel 細掃結果為準。"
                      "Fast 掃頻對 ferrite 的準確度請先用 Discrete 驗證。")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {self._muted()};")
        lay.addWidget(note)

        # v3.4：3D 座標預覽（第一次切到這個分頁才建立）
        self.yig_view_box = QGroupBox("3D 座標預覽（上基板表面＋波導＋YIG 球；滑鼠移到頂點顯示座標）")
        self.yig_view_lay = QVBoxLayout(self.yig_view_box)
        self.yig_view = None
        self.yig_view_msg = QLabel("切到這個分頁時會讀 DXF 畫出來。")
        self.yig_view_lay.addWidget(self.yig_view_msg)
        lay.addWidget(self.yig_view_box)
        lay.addStretch()
        self._yig_view_timer = QTimer(self)
        self._yig_view_timer.setSingleShot(True)
        self._yig_view_timer.setInterval(500)
        self._yig_view_timer.timeout.connect(self.refresh_yig_view)
        for w in (self.edit_yig_d, self.edit_yig_gap, self.edit_yig_x, self.edit_yig_y):
            w.textChanged.connect(lambda _t: self.yig_view is not None and self._yig_view_timer.start())
        self.combo_yig_pos.currentIndexChanged.connect(lambda _i: self.yig_view is not None and self._yig_view_timer.start())

        for w in (self.edit_yig_ms, self.edit_yig_bias, self.edit_yig_g, self.edit_yig_dh, self.edit_yig_span,
                  self.edit_yig_pts):
            w.textChanged.connect(self._update_yig_calc)
        self.combo_yig_bmode.currentIndexChanged.connect(self._on_yig_bmode)
        self._yig_bmode_prev = 0
        self._update_yig_calc()

    def _run_yig_task(self):
        if not self.chk_yig.isChecked():
            self.chk_yig.setChecked(True)
        self.run_task('YIG')

    def _yig_tab_shown(self, index):
        """切到 YIG 分頁：第一次建立 3D 預覽，之後每次重新讀 DXF。"""
        if self.tabs.widget(index) is None or self.tabs.tabText(index) != "YIG 磁性球":
            return
        if self.yig_view is None:
            try:
                from yig_view import make_view
                self.yig_view = make_view(self.yig_view_box)
            except Exception as e:  # noqa: BLE001 - 沒有 3D 繪圖也要能用其他功能
                self.yig_view_msg.setText(f"沒辦法建立 3D 預覽：{e}")
                return
            self.yig_view.on_set_yig = self._set_yig_xy
            self.yig_view.btn_refresh.clicked.connect(self.refresh_yig_view)
            self.yig_view_lay.addWidget(self.yig_view)
        self.refresh_yig_view()

    def _yig_preview_params(self):
        def f(edit, default):
            try:
                return float(edit.text())
            except (TypeError, ValueError):
                return default
        auto = self.combo_yig_pos.currentIndex() == 0
        return {"yig_diameter": f(self.edit_yig_d, 0.4), "yig_gap": f(self.edit_yig_gap, 0.1),
                "yig_pos_mode": "auto" if auto else "abs",
                "yig_dx": f(self.edit_yig_x, 0.0) if auto else 0.0, "yig_dy": f(self.edit_yig_y, 0.0) if auto else 0.0,
                "yig_x": f(self.edit_yig_x, 0.0), "yig_y": f(self.edit_yig_y, 0.0)}

    def refresh_yig_view(self):
        if self.yig_view is None:
            return
        path = self.edit_dxf.text().strip()
        if not path or not os.path.exists(path):
            self.yig_view_msg.setText("先在主控制面板選 DXF 檔（或用「✏ 編輯」畫一個），這裡才畫得出來。")
            self.yig_view.set_scene(None)
            return
        try:
            from dxf_model import Design
            from yig_view import scene_from

            def f(edit, default):
                try:
                    return float(edit.text())
                except (TypeError, ValueError):
                    return default
            design = Design.load(path)
            scene = scene_from(design, f(self.edit_thick_die, 0.8), f(self.edit_thick_top, 0.035),
                               self._yig_preview_params())
        except Exception as e:  # noqa: BLE001
            self.yig_view_msg.setText(f"讀不了 DXF：{e}")
            self.yig_view.set_scene(None)
            return
        sph = scene["sphere"]
        self.yig_view_msg.setText(f"{os.path.basename(path)}（{design.mode}）　上基板表面 z = {scene['z'][0]:g} mm、"
                                  f"走線頂面 z = {scene['z'][1]:g} mm"
                                  + (f"　YIG 球心 ({sph[0][0]:.4g}, {sph[0][1]:.4g}, {sph[0][2]:.4g}) mm" if sph else ""))
        self.yig_view.set_scene(scene)

    def _set_yig_xy(self, x, y):
        """3D 預覽點選的頂點 → YIG 球心放在它正上方（絕對座標）。"""
        self.combo_yig_pos.setCurrentIndex(1)
        self.edit_yig_x.setText(f"{x:.4f}".rstrip("0").rstrip("."))
        self.edit_yig_y.setText(f"{y:.4f}".rstrip("0").rstrip("."))
        self.refresh_yig_view()

    def _yig_pos_labels(self, idx):
        if idx == 0:
            self.lbl_yig_x.setText("ΔX (mm):"); self.lbl_yig_y.setText("ΔY (mm):")
        else:
            self.lbl_yig_x.setText("X (mm):"); self.lbl_yig_y.setText("Y (mm):")

    def _on_yig_bmode(self, idx):
        """切換輸入方式時，把目前數值換算成新單位，維持同一個物理場。"""
        try:
            ms, g = float(self.edit_yig_ms.text()), float(self.edit_yig_g.text())
            old_mode = "external_mT" if self._yig_bmode_prev == 0 else "internal_Apm"
            fld = yig.bias_fields(float(self.edit_yig_bias.text()), old_mode, ms, g)
            self.edit_yig_bias.setText(f"{fld['B_ext_mT']:.4g}" if idx == 0 else f"{fld['H_int']:.0f}")
        except ValueError:
            pass
        self._yig_bmode_prev = idx
        self._update_yig_calc()

    def _update_yig_calc(self, *_):
        try:
            mode = "external_mT" if self.combo_yig_bmode.currentIndex() == 0 else "internal_Apm"
            fld = yig.bias_fields(float(self.edit_yig_bias.text()), mode, float(self.edit_yig_ms.text()),
                                  float(self.edit_yig_g.text()))
            lw = yig.linewidth_mhz(float(self.edit_yig_dh.text()), float(self.edit_yig_g.text()))
            txt = (f"外加 {fld['B_ext_mT']:.2f} mT → HFSS Bias (內部場) {fld['H_int']:.0f} A/m　|　"
                   f"Kittel ≈ {fld['f_kittel']:.4f} GHz　|　線寬 ≈ {lw:.2f} MHz")
            try:
                step = 2 * float(self.edit_yig_span.text()) / max(1, int(self.edit_yig_pts.text()) - 1)
                txt += f"　|　細掃間距 {step:.3f} MHz" + ("  ⚠ 大於線寬" if step > lw else "")
            except ValueError:
                pass
            if fld['H_int'] <= 0:
                txt = "⚠ 偏置場不足：內部場 ≤ 0，YIG 未飽和"
            self.lbl_yig_calc.setText(txt)
        except (ValueError, ZeroDivisionError):
            self.lbl_yig_calc.setText("⚠ 請輸入數字")

    def reset_advanced(self):
        self.edit_min_passes.setText("1"); self.combo_basis.setCurrentIndex(0)
        self.chk_lambda.setChecked(True); self.edit_lambda_target.setText("0.3333"); self.combo_solver.setCurrentIndex(0)
        self.edit_interp_tol.setText("0.5"); self.edit_interp_max.setText("250")
        self.chk_passivity.setChecked(True); self.chk_causality.setChecked(False); self.chk_sweep_fields.setChecked(False)
        self.edit_port_modes.setText("1"); self.edit_port_z.setText("50"); self.chk_port_renorm.setChecked(True)
        self.edit_port_deembed.setText("0"); self.combo_port_zdef.setCurrentIndex(0)
        self.combo_boundary.setCurrentIndex(0); self.chk_infinite_gp.setChecked(False)
        self.edit_er.setText(""); self.edit_tand.setText("")
        self.edit_gif_frames.setText("18"); self.combo_gif_view.setCurrentIndex(0); self.edit_gif_ms.setText("80")
        self.edit_gif_w.setText("1280"); self.edit_gif_h.setText("720")

    def init_status_bar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.lbl_status_project = QLabel("")
        sb.addPermanentWidget(self.lbl_status_project)

    def show_mode_help(self):
        from mode_help import ModeHelpDialog
        if self.help_dialog is None:
            self.help_dialog = ModeHelpDialog(self)
        self.help_dialog.show()
        self.help_dialog.raise_()
        self.help_dialog.activateWindow()

    def update_adaptive_ui(self, mode):
        current_text = self.edit_adaptive_freqs.text()
        if mode == "Single":
            self.edit_adaptive_freqs.setEnabled(True)
            if "," in current_text or "Auto" in current_text:
                self.edit_adaptive_freqs.setText(self.edit_freq_stop.text() or "9GHz")
        elif mode == "Multi-Frequencies":
            self.edit_adaptive_freqs.setEnabled(True)
            if "," not in current_text or "Auto" in current_text:
                self.edit_adaptive_freqs.setText("2.5GHz, 5.5GHz, 9GHz")
        elif mode == "Broadband":
            self.edit_adaptive_freqs.setEnabled(False)
            self.edit_adaptive_freqs.setText("Auto (由Sweep範圍決定)")

    def update_suggested_ui(self, val):
        self.lbl_suggest_scale.setText(f"(建議: {val})" if val < 0.99 else "(安全)")

    # ==================================================================
    # 專案資料夾
    # ==================================================================
    def _paths(self, name=None):
        name = name if name is not None else self.edit_project_name.text().strip()
        nas_root = self.edit_save_dir.text().strip() if self.chk_enable_sync.isChecked() else ""
        return project_paths(self.edit_local_temp.text().strip() or DEFAULT_LOCAL_ROOT, nas_root, name,
                             self.edit_csv_name.text().strip())

    def _refresh_project_label(self):
        pp = self._paths()
        txt = f"📁 本地專案資料夾: {pp['local_dir']}   (CSV → {os.path.basename(pp['csv_path'])})"
        if pp['storage_dir']:
            txt += f"\n☁️ NAS 鏡像: {pp['storage_dir']}"
        self.lbl_project_dir.setText(txt)
        state = f"目前專案: {self.confirmed_project}" if self.confirmed_project else "尚未開啟專案"
        self.lbl_status_project.setText(state)

    def _on_project_location_changed(self, *_):
        if self.confirmed_project and self.edit_project_name.text().strip() != self.confirmed_project:
            self.confirmed_project = None
            if not self._is_running():
                for b in self.step_buttons:
                    b.setEnabled(False)
        self._refresh_project_label()

    def open_project_folder(self):
        d = self._paths()['local_dir']
        if not os.path.isdir(d):
            QMessageBox.information(self, "資料夾不存在", f"尚未建立:\n{d}")
            return
        try:
            os.startfile(d)          # Windows
        except AttributeError:
            subprocess.Popen(["xdg-open", d])

    def _resolve_project(self):
        """確認專案名稱：同名時詢問是否開啟舊檔；選「否」則要求輸入新名稱。

        回傳 (name, pull_from) ；使用者取消時回傳 None。
        """
        name = self.edit_project_name.text().strip()
        pp = self._paths(name)
        while True:
            if not is_valid_project_name(name):
                name, ok = QInputDialog.getText(
                    self, "專案名稱不合法",
                    f"「{name}」含有不支援的字元。\n專案名稱只能使用英文、數字、底線 (_) 與減號 (-)：",
                    text=self._suggest_name(name))
                if not ok:
                    return None
                name = name.strip()
                continue

            nas_root = self.edit_save_dir.text().strip() if self.chk_enable_sync.isChecked() else ""
            found = find_existing_project(pp['local_root'], nas_root, name, check_nas=bool(nas_root))
            if not found:
                self.log_msg(f"🆕 新專案「{name}」，將建立於 {self._paths(name)['local_dir']}")
                break

            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("發現同名專案")
            box.setText(f"已存在名為「{name}」的專案。\n\n位置 ({found['where']}):\n{found['aedt']}\n\n是否開啟已有檔案？")
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                                   QMessageBox.StandardButton.Cancel)
            box.button(QMessageBox.StandardButton.Yes).setText("是，開啟已有檔案")
            box.button(QMessageBox.StandardButton.No).setText("否，建立新專案")
            box.button(QMessageBox.StandardButton.Cancel).setText("取消")
            ans = box.exec()
            if ans == QMessageBox.StandardButton.Yes:
                target = self._paths(name)['project_path_local']
                same = os.path.normcase(os.path.abspath(found['aedt'])) == os.path.normcase(os.path.abspath(target))
                self.edit_project_name.setText(name)
                return name, (None if same else found)
            if ans != QMessageBox.StandardButton.No:
                return None
            new_name, ok = QInputDialog.getText(self, "新專案名稱", "請輸入新的專案名稱 (英文、數字、_、-)：",
                                                text=self._suggest_name(name))
            if not ok:
                return None
            name = new_name.strip()

        self.edit_project_name.setText(name)
        return name, None

    def _suggest_name(self, name):
        import re
        base = re.sub(r"[^A-Za-z0-9_\-]+", "_", os.path.splitext(name or "")[0]).strip("_") or "HFSS_Project"
        root = self.edit_local_temp.text().strip() or DEFAULT_LOCAL_ROOT
        nas = self.edit_save_dir.text().strip() if self.chk_enable_sync.isChecked() else ""
        candidate, i = base, 2
        while find_existing_project(root, nas, candidate, check_nas=bool(nas)):
            candidate = f"{base}_{i}"
            i += 1
        return candidate

    # ==================================================================
    # S 參數繪圖分頁
    # ==================================================================
    def init_plot_tab(self):
        plot_layout = QVBoxLayout(self.tab_plot)
        control_layout = QHBoxLayout()
        self.btn_load_csv = QPushButton("載入 S參數 CSV 數據")
        self.btn_load_csv.setStyleSheet("font-weight: bold; padding: 8px; background-color: #2196F3; color: white;")
        self.btn_load_csv.clicked.connect(self.load_csv_data)
        self.btn_plot = QPushButton("繪製勾選項目")
        self.btn_plot.setStyleSheet("font-weight: bold; padding: 8px; background-color: #FF9800; color: white;")
        self.btn_plot.clicked.connect(self.update_plot)
        control_layout.addWidget(self.btn_load_csv)
        control_layout.addWidget(self.btn_plot)
        plot_layout.addLayout(control_layout)

        split_layout = QVBoxLayout()
        self.table_params = QTableWidget()
        self.table_params.setColumnCount(6)
        self.table_params.setHorizontalHeaderLabels(["參數", "dB", "Linear", "Real", "Imag", "Phase"])
        self.table_params.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_params.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table_params.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table_params.cellClicked.connect(self.toggle_cell_checkbox)
        split_layout.addWidget(self.table_params, 1)

        canvas_layout = QVBoxLayout()
        self.figure = Figure()
        self.canvas = FigureCanvas(self.figure)
        self.toolbar = NavigationToolbar(self.canvas, self.tab_plot)
        canvas_layout.addWidget(self.toolbar)
        canvas_layout.addWidget(self.canvas)
        split_layout.addLayout(canvas_layout, 3)
        plot_layout.addLayout(split_layout)

        self.plot_df = None
        self.s_param_list = []

    def toggle_cell_checkbox(self, row, col):
        if col > 0:
            item = self.table_params.item(row, col)
            if item:
                new_state = Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked
                item.setCheckState(new_state)

    def load_csv_data(self):
        default_dir = self._paths()['local_dir']
        if not os.path.isdir(default_dir):
            default_dir = self.edit_local_temp.text()
        file_path, _ = QFileDialog.getOpenFileName(self, "選擇 S 參數 CSV", default_dir, "CSV Files (*.csv)")
        if not file_path:
            return
        try:
            self.plot_df = pd.read_csv(file_path)
            s_set = set()
            for col in self.plot_df.columns:
                if col.startswith("S") and "_" in col:
                    s_name = col.split("_")[0]
                    if len(s_name) >= 3 and s_name[1:].isdigit():
                        s_set.add(s_name)
            self.s_param_list = sorted(s_set)
            self.populate_table()
            QMessageBox.information(self, "成功", f"✅ 成功載入 {len(self.s_param_list)} 個 S 參數！\n請在表格勾選要繪製的項目。")
        except Exception as e:
            QMessageBox.critical(self, "錯誤", f"❌ 讀取 CSV 失敗:\n{e}")

    def populate_table(self):
        self.table_params.setRowCount(len(self.s_param_list))
        for row, s_name in enumerate(self.s_param_list):
            item_name = QTableWidgetItem(s_name)
            item_name.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table_params.setItem(row, 0, item_name)
            for col in range(1, 6):
                chk_item = QTableWidgetItem("")
                chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                checked = col == 1 and s_name in ["S11", "S21"]
                chk_item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                self.table_params.setItem(row, col, chk_item)

    def update_plot(self):
        if self.plot_df is None:
            QMessageBox.warning(self, "警告", "⚠️ 請先載入 CSV 數據！")
            return
        self.figure.clear()
        type_names = {1: "dB", 2: "Linear", 3: "Real", 4: "Imag", 5: "Phase"}
        plot_requests = {t: [] for t in type_names.values()}
        active_types = []
        for row in range(self.table_params.rowCount()):
            s_name = self.table_params.item(row, 0).text()
            for col in range(1, 6):
                if self.table_params.item(row, col).checkState() == Qt.CheckState.Checked:
                    t_name = type_names[col]
                    if t_name not in active_types:
                        active_types.append(t_name)
                    plot_requests[t_name].append(s_name)

        num_plots = len(active_types)
        if num_plots == 0:
            QMessageBox.information(self, "提示", "請至少勾選一個項目來繪製！")
            self.canvas.draw()
            return
        rows, cols = {1: (1, 1), 2: (2, 1), 3: (3, 1), 4: (2, 2)}.get(num_plots, (3, 2))
        axes = self.figure.subplots(rows, cols, squeeze=False)
        axes_flat = list(axes.flatten())
        for ax in axes_flat[num_plots:]:
            ax.set_visible(False)

        freqs = self.plot_df[self.plot_df.columns[0]].values
        suffix_map = {"dB": "_dB", "Real": "_real", "Imag": "_imag", "Phase": "_phase"}
        for ax, t_name in zip(axes_flat[:num_plots], active_types):
            for s_name in plot_requests[t_name]:
                try:
                    if t_name == "Linear":
                        y_vals = 10 ** (self.plot_df[f"{s_name}_dB"] / 20)
                    else:
                        y_vals = self.plot_df[f"{s_name}{suffix_map[t_name]}"].values
                    ax.plot(freqs, y_vals, label=s_name, linewidth=2)
                except Exception as e:
                    print(f"Plot Error for {s_name} {t_name}: {e}")
            ax.set_title(f"{t_name} Response", fontsize=10, fontweight='bold')
            ax.set_xlabel("Frequency (GHz)", fontsize=9)
            ax.set_ylabel(t_name, fontsize=9)
            ax.grid(True, linestyle=':', alpha=0.7)
            ax.legend(loc='best', fontsize=8)
        self.figure.tight_layout()
        self.canvas.draw()

    # ==================================================================
    # HPC 聯動
    # ==================================================================
    def sync_hpc_from_total(self):
        try:
            val = min(int(self.edit_hpc_total.text()), self.safe_limit)
            self.edit_hpc_total.setText(str(val))
            self.edit_hpc_tasks.setText("1")
            self.edit_hpc_cores.setText(str(val))
        except ValueError:
            pass

    def sync_hpc_from_tasks(self):
        if not self.edit_hpc_tasks.hasFocus():
            return
        try:
            total = int(self.edit_hpc_total.text())
            tasks = min(max(1, int(self.edit_hpc_tasks.text())), total)
            self.edit_hpc_cores.blockSignals(True)
            self.edit_hpc_cores.setText(str(total // tasks))
            self.edit_hpc_cores.blockSignals(False)
        except ValueError:
            pass

    def sync_hpc_from_cores(self):
        if not self.edit_hpc_cores.hasFocus():
            return
        try:
            total = int(self.edit_hpc_total.text())
            cores = min(max(1, int(self.edit_hpc_cores.text())), total)
            self.edit_hpc_tasks.blockSignals(True)
            self.edit_hpc_tasks.setText(str(total // cores))
            self.edit_hpc_tasks.blockSignals(False)
        except ValueError:
            pass

    # ==================================================================
    # 設定檔 (~/LAB/HFSS/hfss_settings.json，key 與舊版相容)
    # ==================================================================
    def _register_settings(self):
        self._settings_map = {
            'combo_type': self.combo_type, 'aedt_version': self.combo_aedt_version,
            'dxf_file': self.edit_dxf, 'enable_sync': self.chk_enable_sync,
            'save_dir': self.edit_save_dir, 'local_temp': self.edit_local_temp,
            'project_name': self.edit_project_name, 'csv_name': self.edit_csv_name,
            'thick_top': self.edit_thick_top, 'thick_die': self.edit_thick_die, 'thick_bot': self.edit_thick_bot,
            'mat_cu': self.edit_mat_cu, 'mat_die': self.edit_mat_die, 'port_scale': self.edit_port_scale,
            'adaptive_mode': self.combo_adaptive_mode, 'adaptive_freqs': self.edit_adaptive_freqs,
            'sweep_mode': self.combo_sweep_mode,
            'freq_start': self.edit_freq_start, 'freq_stop': self.edit_freq_stop, 'freq_open': self.edit_freq_open,
            'points': self.edit_points, 'passes': self.edit_passes, 'deltas': self.edit_deltas,
            'max_refine': self.edit_max_refine, 'min_converged': self.edit_min_converged,
            'field_freqs': self.edit_field_freqs, 'field_reuse_mesh': self.chk_field_reuse,
            'hpc_total': self.edit_hpc_total, 'hpc_cores': self.edit_hpc_cores, 'hpc_tasks': self.edit_hpc_tasks,
            'hpc_gpus': self.edit_hpc_gpus,
            'pipe_gif': self.chk_pipe_gif, 'pipe_close': self.chk_pipe_close,
            # 進階
            'min_passes': self.edit_min_passes, 'basis_order': self.combo_basis,
            'lambda_refine': self.chk_lambda, 'lambda_target': self.edit_lambda_target,
            'matrix_solver': self.combo_solver,
            'interp_tol': self.edit_interp_tol, 'interp_max_solns': self.edit_interp_max,
            'enforce_passivity': self.chk_passivity, 'enforce_causality': self.chk_causality,
            'sweep_save_fields': self.chk_sweep_fields,
            'port_modes': self.edit_port_modes, 'port_impedance': self.edit_port_z,
            'port_renormalize': self.chk_port_renorm, 'port_deembed': self.edit_port_deembed,
            'port_zdef': self.combo_port_zdef,
            'open_boundary': self.combo_boundary, 'infinite_ground': self.chk_infinite_gp,
            'er_override': self.edit_er, 'tand_override': self.edit_tand,
            'gif_frames': self.edit_gif_frames, 'gif_view': self.combo_gif_view, 'gif_ms': self.edit_gif_ms,
            'gif_width': self.edit_gif_w, 'gif_height': self.edit_gif_h,
            # YIG
            'yig_enable': self.chk_yig, 'yig_diameter': self.edit_yig_d, 'yig_gap': self.edit_yig_gap,
            'yig_pos_mode': self.combo_yig_pos, 'yig_x': self.edit_yig_x, 'yig_y': self.edit_yig_y,
            'yig_mesh': self.edit_yig_mesh, 'yig_ms': self.edit_yig_ms, 'yig_dh': self.edit_yig_dh,
            'yig_dhf': self.edit_yig_dhf, 'yig_er': self.edit_yig_er, 'yig_tand': self.edit_yig_tand,
            'yig_g': self.edit_yig_g, 'yig_bias_mode': self.combo_yig_bmode, 'yig_bias': self.edit_yig_bias,
            'yig_bias_dir': self.combo_yig_bdir, 'yig_add_adaptive': self.chk_yig_adapt,
            'yig_fine': self.chk_yig_fine, 'yig_span': self.edit_yig_span, 'yig_pts': self.edit_yig_pts,
            'yig_ftype': self.combo_yig_ftype, 'yig_ftol': self.edit_yig_ftol,
        }

    def load_settings(self):
        settings = ConfigManager.load_settings()
        if not settings:
            return
        # 舊版存的是完整 CSV 路徑 → 只取檔名 (CSV 現在固定放在專案資料夾)
        if 'csv_name' not in settings and settings.get('csv_path'):
            settings['csv_name'] = os.path.basename(settings['csv_path'])
        for key, w in self._settings_map.items():
            if key not in settings:
                continue
            val = settings[key]
            try:
                if isinstance(w, QCheckBox):
                    w.setChecked(bool(val))
                elif isinstance(w, QComboBox):
                    if w.isEditable():
                        w.setCurrentText(str(val))
                    else:
                        idx = w.findText(str(val))
                        if idx >= 0:
                            w.setCurrentIndex(idx)
                elif key == 'adaptive_freqs' and settings.get('adaptive_mode') == 'Broadband':
                    continue
                else:
                    w.setText(str(val))
            except Exception:
                pass
        try:
            if int(self.edit_hpc_cores.text()) > self.safe_limit:
                self.edit_hpc_cores.setText(str(self.safe_limit))
        except ValueError:
            pass
        self.update_adaptive_ui(self.combo_adaptive_mode.currentText())
        self._yig_bmode_prev = self.combo_yig_bmode.currentIndex()
        self._yig_pos_labels(self.combo_yig_pos.currentIndex())
        self._update_yig_calc()
        self.log_msg(f"📁 已讀取設定檔: {SETTINGS_FILE}")

    def save_settings(self):
        settings = {}
        for key, w in self._settings_map.items():
            if isinstance(w, QCheckBox):
                settings[key] = w.isChecked()
            elif isinstance(w, QComboBox):
                settings[key] = w.currentText()
            else:
                settings[key] = w.text()
        ConfigManager.save_settings(settings)

    def open_dxf_editor(self):
        """v3.3：DXF 編輯器；存檔後這裡的 DXF 路徑換成存的那個檔。"""
        from dxf_editor import DxfEditor
        if self.dxf_editor is not None and self.dxf_editor.isVisible():
            self.dxf_editor.raise_()
            self.dxf_editor.activateWindow()
            return
        path = self.edit_dxf.text().strip()
        self.dxf_editor = DxfEditor(path if path and os.path.exists(path) else None,
                                    mode=self.combo_type.currentText(), on_saved=self._dxf_saved)
        icon = self.windowIcon()
        if not icon.isNull():
            self.dxf_editor.setWindowIcon(icon)
        self.dxf_editor.show()

    def _dxf_saved(self, path):
        self.edit_dxf.setText(path)
        if self.dxf_editor is not None:
            self.combo_type.setCurrentText(self.dxf_editor.design.mode)
        self.statusBar().showMessage(f"DXF 已存檔並套用：{path}", 8000)
        self.refresh_yig_view()

    def _usage_task(self):
        """平台「授權」頁顯示的「正在跑什麼」。"""
        if not self._is_running():
            return None

        def num(edit):
            try:
                return float(edit.text())
            except (TypeError, ValueError):
                return None
        return {"name": self.current_task or "", "project": self.confirmed_project or self.edit_project_name.text(),
                "started": self.task_start_time or None, "cores": num(self.edit_hpc_cores),
                "tasks": num(self.edit_hpc_tasks), "gpus": num(self.edit_hpc_gpus)}

    def closeEvent(self, event):
        if self.dxf_editor is not None and self.dxf_editor.isVisible() and not self.dxf_editor.close():
            event.ignore()
            return
        if self._is_running():
            ans = QMessageBox.question(self, "任務執行中",
                                       "HFSS 任務仍在執行中，確定要關閉嗎？\n(工作程序會被終止；AEDT 端已送出的計算可能仍在進行)")
            if ans != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._kill_worker()
        self.save_settings()
        # 關閉時在背景啟動 robocopy 同步，不會卡住關閉動作
        if self.chk_enable_sync.isChecked() and self.confirmed_project:
            pp = self._paths(self.confirmed_project)
            if pp['storage_dir']:
                backup_folder_detached(pp['local_dir'], pp['storage_dir'])
        if self.help_dialog:
            self.help_dialog.close()
        self.updater.stop()
        try:
            self.usage.close()
        except Exception:  # noqa: BLE001 - 平台連不到也要能關
            pass
        event.accept()

    # ==================================================================
    # 雜項
    # ==================================================================
    def browse_file(self, line_edit):
        file, _ = QFileDialog.getOpenFileName(self, "選擇 DXF", "", "DXF Files (*.dxf)")
        if file:
            line_edit.setText(file)

    def browse_dir(self, line_edit):
        folder = QFileDialog.getExistingDirectory(self, "選擇資料夾")
        if folder:
            line_edit.setText(folder)

    def log_msg(self, msg):
        self.log_console.append(msg)
        self.log_console.verticalScrollBar().setValue(self.log_console.verticalScrollBar().maximum())

    def set_buttons_state(self, state):
        self.btn_open.setEnabled(state)
        self.btn_pipeline.setEnabled(state)
        for b in self.step_buttons:
            b.setEnabled(state)
        if hasattr(self, "btn_yig_build"):
            self.btn_yig_build.setEnabled(state)

    # ==================================================================
    # 參數收集
    # ==================================================================
    def _collect_params(self, task_name, name):
        """讀取並驗證所有欄位；格式錯誤時丟出 ValueError。"""
        def num(widget, label, cast=float, minimum=None, allow_blank=False):
            txt = widget.text().strip()
            if allow_blank and not txt:
                return None
            try:
                v = cast(txt)
            except ValueError:
                raise ValueError(f"「{label}」必須是數字 (目前: '{widget.text()}')")
            if minimum is not None and v < minimum:
                raise ValueError(f"「{label}」必須 ≥ {minimum}")
            return v

        def freq(widget, label):
            try:
                parse_freq_ghz(widget.text())
            except ValueError:
                raise ValueError(f"「{label}」頻率格式錯誤 (範例: 9GHz、9000MHz)")
            return widget.text().strip()

        cores = num(self.edit_hpc_cores, "每任務核心", int, 1)
        if cores > self.safe_limit:
            self.log_msg(f"⚠️ HPC 核心數 ({cores}) 過高，已自動降至安全上限 ({self.safe_limit})。")
            cores = self.safe_limit
            self.edit_hpc_cores.setText(str(cores))

        f_start, f_stop = freq(self.edit_freq_start, "Sweep起始頻率"), freq(self.edit_freq_stop, "Sweep結束頻率")
        if parse_freq_ghz(f_stop) <= parse_freq_ghz(f_start):
            raise ValueError("Sweep 結束頻率必須大於起始頻率")
        try:
            parse_freq_list_ghz(self.edit_field_freqs.text())
        except ValueError:
            raise ValueError("「場型指定頻率」格式錯誤 (範例: 2.5, 5.5, 8.5)")

        basis_map = {"Mixed Order": -1, "First Order": 1, "Second Order": 2, "Zero Order": 0}
        params = {
            'task': task_name,
            'aedt_version': self.combo_aedt_version.currentText().strip() or DEFAULT_AEDT_VERSION,
            'model_type': self.combo_type.currentText(),
            'dxf_file': self.edit_dxf.text(),
            'enable_sync': self.chk_enable_sync.isChecked(),
            **self._paths(name),
            'design_name': f"{name}_Design",
            'thick_top': num(self.edit_thick_top, "Top 厚", float, 0),
            'thick_die': num(self.edit_thick_die, "Die 厚", float, 0),
            'thick_bot': num(self.edit_thick_bot, "Bot 厚", float, 0),
            'mat_copper': self.edit_mat_cu.text().strip(),
            'mat_dielectric': self.edit_mat_die.text().strip(),
            'port_scale': num(self.edit_port_scale, "Port縮放基數", float, 0.01),
            'adaptive_mode': self.combo_adaptive_mode.currentText(),
            'adaptive_freqs': self.edit_adaptive_freqs.text(),
            'sweep_mode': self.combo_sweep_mode.currentText(),
            'freq_start': f_start,
            'freq_stop': f_stop,
            'freq_open': freq(self.edit_freq_open, "空氣腔設定頻率"),
            'sweep_points': num(self.edit_points, "掃頻點數", int, 2),
            'max_passes': num(self.edit_passes, "最大迭代次數", int, 1),
            'max_deltas': num(self.edit_deltas, "Max Delta S", float, 0),
            'max_refine': num(self.edit_max_refine, "網格細化 %", int, 1),
            'min_converged': num(self.edit_min_converged, "最小連續收斂次數", int, 1),
            'field_freqs': self.edit_field_freqs.text(),
            'field_reuse_mesh': self.chk_field_reuse.isChecked(),
            'hpc_cores': cores,
            'hpc_tasks': num(self.edit_hpc_tasks, "平行任務數", int, 1),
            'hpc_gpus': num(self.edit_hpc_gpus, "GPU 數量", int, 0),
            # 進階
            'min_passes': num(self.edit_min_passes, "最少 Pass 數", int, 1),
            'basis_order': basis_map[self.combo_basis.currentText()],
            'lambda_refine': self.chk_lambda.isChecked(),
            'lambda_target': num(self.edit_lambda_target, "Lambda Target", float, 0.01),
            'iterative_solver': self.combo_solver.currentIndex() == 1,
            'interp_tol': num(self.edit_interp_tol, "插值誤差容許", float, 0.001),
            'interp_max_solns': num(self.edit_interp_max, "插值最大求解數", int, 5),
            'enforce_passivity': self.chk_passivity.isChecked(),
            'enforce_causality': self.chk_causality.isChecked(),
            'sweep_save_fields': self.chk_sweep_fields.isChecked(),
            'port_modes': num(self.edit_port_modes, "模態數", int, 1),
            'port_impedance': num(self.edit_port_z, "參考阻抗", float, 0.001),
            'port_renormalize': self.chk_port_renorm.isChecked(),
            'port_deembed': num(self.edit_port_deembed, "De-embed", float),
            'port_zdef': self.combo_port_zdef.currentText(),
            'open_boundary': self.combo_boundary.currentText(),
            'infinite_ground': self.chk_infinite_gp.isChecked(),
            'er_override': num(self.edit_er, "εr", float, 1.0, allow_blank=True),
            'tand_override': num(self.edit_tand, "tanδ", float, 0.0, allow_blank=True),
            'gif_frames': num(self.edit_gif_frames, "GIF 幀數", int, 2),
            'gif_view': self.combo_gif_view.currentText(),
            'gif_ms': num(self.edit_gif_ms, "GIF 每幀 ms", int, 10),
            'gif_width': num(self.edit_gif_w, "GIF 寬", int, 160),
            'gif_height': num(self.edit_gif_h, "GIF 高", int, 120),
        }
        params['yig_enable'] = self.chk_yig.isChecked()
        if params['yig_enable']:
            params.update({
                'yig_diameter': num(self.edit_yig_d, "YIG 直徑", float, 0.001),
                'yig_gap': num(self.edit_yig_gap, "YIG 間隙", float, 0),
                'yig_pos_mode': "auto" if self.combo_yig_pos.currentIndex() == 0 else "abs",
                'yig_mesh_mm': num(self.edit_yig_mesh, "YIG 網格邊長", float, 1e-4),
                'yig_ms_t': num(self.edit_yig_ms, "4πMs", float, 0.001),
                'yig_dh_oe': num(self.edit_yig_dh, "ΔH", float, 0),
                'yig_dh_freq': num(self.edit_yig_dhf, "ΔH 頻率", float, 0, allow_blank=True),
                'yig_er': num(self.edit_yig_er, "YIG εr", float, 1),
                'yig_tand': num(self.edit_yig_tand, "YIG tanδ", float, 0),
                'yig_g': num(self.edit_yig_g, "Landé g", float, 0.1),
                'yig_bias_mode': "external_mT" if self.combo_yig_bmode.currentIndex() == 0 else "internal_Apm",
                'yig_bias_value': num(self.edit_yig_bias, "偏置場", float),
                'yig_bias_dir': self.combo_yig_bdir.currentText(),
                'yig_add_adaptive': self.chk_yig_adapt.isChecked(),
                'yig_fine_sweep': self.chk_yig_fine.isChecked(),
                'yig_span_mhz': num(self.edit_yig_span, "細掃範圍", float, 0.001),
                'yig_fine_points': num(self.edit_yig_pts, "細掃點數", int, 2),
                'yig_fine_type': self.combo_yig_ftype.currentText(),
                'yig_fine_tol': num(self.edit_yig_ftol, "細掃誤差容許", float, 0.001),
            })
            xy_x = num(self.edit_yig_x, "YIG X", float); xy_y = num(self.edit_yig_y, "YIG Y", float)
            if params['yig_pos_mode'] == "auto":
                params['yig_dx'], params['yig_dy'] = xy_x, xy_y
            else:
                params['yig_x'], params['yig_y'] = xy_x, xy_y
            if yig.bias_fields(params['yig_bias_value'], params['yig_bias_mode'], params['yig_ms_t'])['H_int'] <= 0:
                raise ValueError("YIG 偏置場不足：換算後的內部場 ≤ 0，請提高偏置場。")
        if params['adaptive_mode'] != 'Broadband':
            try:
                parse_freq_list_ghz(params['adaptive_freqs'])
            except ValueError:
                raise ValueError("「Adaptive頻率」格式錯誤")
        return params

    # ==================================================================
    # 啟動 / 監看工作程序
    # ==================================================================
    def _is_running(self):
        return self.proc is not None and self.proc.is_alive()

    def run_pipeline(self):
        steps = ['BUILD', 'SIM_S', 'SIM_FIELDS']
        if self.chk_pipe_gif.isChecked():
            steps.append('EXPORT_GIF')
        if self.chk_pipe_close.isChecked():
            steps.append('CLOSE')
        self.run_task('PIPELINE', steps)

    def run_task(self, task_name, steps=None):
        if self._is_running():
            QMessageBox.information(self, "執行中", "已有任務在執行，請等待完成或先按「中止」。")
            return

        pull_from = None
        name = self.edit_project_name.text().strip()
        # 開啟專案 / 一鍵流程 (或名稱已變更) 時確認專案：同名 → 詢問開啟舊檔或改名
        if task_name in ('OPEN', 'PIPELINE') or self.confirmed_project != name:
            resolved = self._resolve_project()
            if resolved is None:
                self.log_msg("⏹️ 已取消。")
                return
            name, pull_from = resolved

        try:
            params = self._collect_params(task_name, name)
        except ValueError as e:
            QMessageBox.warning(self, "參數錯誤", str(e))
            return
        if steps:
            params['steps'] = steps
        if pull_from:
            params['pull_from'] = pull_from
        self.save_settings()

        self.confirmed_project = name
        self._refresh_project_label()
        self.current_task = task_name
        self.set_buttons_state(False)
        self.btn_abort.setEnabled(True)
        self.progress.setRange(0, 0)          # 忙碌動畫
        self.lbl_stage.setText("啟動中…")
        self.task_start_time = time.time()
        self._update_clock()

        self.msg_queue = _MP.Queue()
        self.proc = _MP.Process(target=process_entry, args=(params, self.msg_queue), daemon=False)
        self.proc.start()
        self._done_received = False
        self.poll_timer.start()
        self.clock_timer.start()

    def _poll_worker(self):
        """每 100 ms 把工作程序送來的訊息取出 (不會阻塞介面)。"""
        for _ in range(200):
            try:
                msg = self.msg_queue.get_nowait()
            except _queue.Empty:
                break
            except Exception:
                break
            kind = msg[0]
            if kind == 'log':
                self.log_msg(msg[1])
            elif kind == 'stage':
                self.lbl_stage.setText(msg[1])
            elif kind == 'scale':
                self.update_suggested_ui(msg[1])
            elif kind == 'done':
                self._done_received = True
                self.on_task_finished(*msg[1:])

        if self.proc is not None and not self.proc.is_alive() and not self._done_received:
            # 被中止或異常結束
            self._done_received = True
            self.log_msg(f"⚠️ 工作程序已結束 (exit code {self.proc.exitcode})。")
            self.on_task_finished(False, self.current_task, {})

    def _update_clock(self):
        m, s = divmod(int(time.time() - self.task_start_time), 60)
        h, m = divmod(m, 60)
        self.lbl_elapsed.setText(f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}")

    def abort_task(self):
        if not self._is_running():
            return
        ans = QMessageBox.question(
            self, "中止任務",
            "確定要中止目前的任務嗎？\n\n會先要求 AEDT 停止模擬，再結束工作程序。\n"
            "(建模途中中止可能留下不完整的模型，重新建模即可)")
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.btn_abort.setEnabled(False)
        self.lbl_stage.setText("中止中…")
        self.log_msg("🛑 使用者要求中止...")
        # 1) 另開程序請 AEDT 停止模擬 (不阻塞介面)
        try:
            self.stop_proc = _MP.Process(target=stop_entry, args=(self.combo_aedt_version.currentText(), self.msg_queue))
            self.stop_proc.start()
        except Exception:
            pass
        # 2) 給 AEDT 15 秒收尾，之後強制結束工作程序
        QTimer.singleShot(15000, self._kill_worker)

    def _kill_worker(self):
        if self.proc is not None and self.proc.is_alive():
            self.proc.terminate()
            self.log_msg("🛑 工作程序已強制結束。")
        if self.stop_proc is not None and self.stop_proc.is_alive():
            self.stop_proc.terminate()

    def on_task_finished(self, success, task_name, payload):
        self.poll_timer.stop()
        self.clock_timer.stop()
        self._update_clock()
        self.progress.setRange(0, 1)
        self.progress.setValue(1 if success else 0)
        self.btn_abort.setEnabled(False)
        self.lbl_stage.setText("完成" if success else "失敗 / 已中止")

        self.btn_open.setEnabled(True)
        self.btn_pipeline.setEnabled(True)
        closed = task_name == 'CLOSE' or (task_name == 'PIPELINE' and self.chk_pipe_close.isChecked() and success)
        if closed or (task_name == 'OPEN' and not success):
            for b in self.step_buttons:
                b.setEnabled(False)
            if closed:
                self.confirmed_project = None
        else:
            self.btn_close.setEnabled(True)
            self.btn_build.setEnabled(True)
            self.btn_yig_build.setEnabled(True)
            has_model = payload.get('has_model', False)
            if task_name == 'OPEN' and success and has_model:
                self.log_msg("🔍 偵測到專案內已有 3D 模型，直接為您解鎖模擬功能！")
            for b in (self.btn_sim_s, self.btn_sim_fields, self.btn_gif):
                b.setEnabled(has_model)
        self._refresh_project_label()

        elapsed = int(time.time() - self.task_start_time)
        mins, secs = divmod(elapsed, 60)
        label = STEP_LABELS.get(task_name, task_name)
        self.log_msg(f"⏱️ 任務 [{label}] {'完成' if success else '失敗'}，總計耗時: {mins} 分 {secs} 秒\n")
        if self.proc is not None:
            self.proc.join(timeout=0.1)
