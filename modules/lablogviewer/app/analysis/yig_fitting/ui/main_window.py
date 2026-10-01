"""YIG Mirror Analysis workspace layout."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QGridLayout, QGroupBox, QPushButton, QLabel,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QSplitter, QTabWidget,
    QPlainTextEdit, QSlider, QScrollArea, QProgressBar, QLineEdit,
)

from .plots import DataPlotWidget
from .analysis_pane import FitPlotWidget
from .param_table import ParamTable
from .batch_panel import BatchPanel
from .phase_panel import PhasePanel
from .roll_table import RollTable
from .latex_view import FormulaImage
from ..core.batch import INIT_MODES
from app.localization import get_localization_manager
from app.gui.fonts import mono_family


class MainWindow(QMainWindow):
    closing = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.localizer = get_localization_manager()
        self.setWindowTitle("YIG Mirror Analysis")
        self.resize(1500, 950)
        self.setMinimumSize(960, 620)
        from .trust_dialog import install as install_trust_prompt
        install_trust_prompt()                 # unknown .py models ask before they run
        self._build()
        from app.settings.dialog import install_settings_menu
        self.settings_menu = install_settings_menu(self)
        from app.gui.network_panel import install_network_bar

        install_network_bar(self)
        self.localizer.bind(self)

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

        for label in left.findChildren(QLabel):
            label.setWordWrap(True)
        self.control_scroll = QScrollArea()
        self.control_scroll.setWidget(left)
        self.control_scroll.setWidgetResizable(True)
        self.control_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scrollbar_width = self.control_scroll.verticalScrollBar().sizeHint().width()
        self.control_scroll.setMinimumWidth(max(300, left.minimumSizeHint().width() + scrollbar_width + 4))

        self.tabs = QTabWidget()
        self.data_plot = DataPlotWidget()
        self.fit_plot = FitPlotWidget()
        self.tabs.addTab(self.data_plot, "Data Preview")
        self.tabs.addTab(self.fit_plot, "Fit Results")
        self.batch_panel = BatchPanel()
        self.tabs.addTab(self.batch_panel, "Continuous Fit")
        self.phase_panel = PhasePanel()
        self.tabs.addTab(self.phase_panel, "Phase / Node")

        self.table = ParamTable()
        self.txt_result = QPlainTextEdit()
        self.txt_result.setReadOnly(True)
        self.txt_result.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.txt_result.setStyleSheet(f"font-family: '{mono_family()}';")

        tw = QWidget()
        tv = QVBoxLayout(tw)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.addWidget(QLabel("Parameter Table (expressions such as pi, pi/2, np.deg2rad(30) are accepted)"))
        tv.addWidget(self.table)
        bottom = QSplitter(Qt.Orientation.Horizontal)
        bottom.addWidget(tw)
        bottom.addWidget(self.txt_result)
        bottom.setSizes([700, 400])

        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter.addWidget(self.tabs)
        self.right_splitter.addWidget(bottom)
        self.right_splitter.setSizes([700, 250])

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self.control_scroll)
        self.main_splitter.addWidget(self.right_splitter)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(7)
        self.main_splitter.setSizes([330, 1170])
        self.setCentralWidget(self.main_splitter)

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(160)
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.lbl_engine = QLabel("Fitting engine: starting...")
        self.statusBar().addPermanentWidget(self.lbl_engine)
        self.statusBar().addPermanentWidget(self.progress)

    def _build_data_group(self):
        g = QGroupBox("DATA")
        gl = QGridLayout(g)
        self.btn_open = QPushButton("Source: Current Viewer Data")
        self.btn_open.setEnabled(False)
        self.btn_open.setToolTip("The analysis window uses a read-only snapshot of its source Viewer Data.")
        self.lbl_file = QLabel("No Data loaded")
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
        self.btn_full = QPushButton("Full Range")
        self.lbl_npts = QLabel("")
        self.chk_zoom = QCheckBox("Preview follows fit range")
        self.chk_zoom.setChecked(True)
        r = 0
        gl.addWidget(self.btn_open, r, 0, 1, 3); r += 1
        gl.addWidget(self.lbl_file, r, 0, 1, 3); r += 1
        gl.addWidget(QLabel("S Parameter"), r, 0); gl.addWidget(self.cmb_s, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("Sweep Axis"), r, 0); gl.addWidget(self.cmb_axis, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("Axis Scale"), r, 0); gl.addWidget(self.spin_scale, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("Slice"), r, 0); gl.addWidget(self.slider_idx, r, 1); gl.addWidget(self.spin_idx, r, 2); r += 1
        gl.addWidget(QLabel("Frequency Start"), r, 0); gl.addWidget(self.spin_f1, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("Frequency Stop"), r, 0); gl.addWidget(self.spin_f2, r, 1, 1, 2); r += 1
        gl.addWidget(self.btn_full, r, 1); gl.addWidget(self.lbl_npts, r, 2); r += 1
        gl.addWidget(self.chk_zoom, r, 0, 1, 3); r += 1
        gl.addWidget(QLabel("Drag on the 1D trace to select a frequency range; click the 2D map to select a slice."), r, 0, 1, 3)
        return g

    def _build_clean_group(self):
        g = QGroupBox("PREPROCESSING")
        cl = QGridLayout(g)
        self.chk_spike = QCheckBox("Hampel outlier filter")
        self.spin_sp_half = QSpinBox()
        self.spin_sp_half.setRange(2, 500)
        self.spin_sp_half.setValue(5)
        self.spin_sp_half.setSuffix(" points")
        self.spin_sp_half.setToolTip("Half-width of the rolling median window.")
        self.spin_sp_k = QDoubleSpinBox()
        self.spin_sp_k.setRange(1.0, 1000.0)
        self.spin_sp_k.setDecimals(1)
        self.spin_sp_k.setValue(5.0)
        self.spin_sp_k.setSuffix(" σ")
        self.spin_sp_k.setToolTip("Outlier threshold in local-noise standard deviations.")
        self.spin_sp_dil = QSpinBox()
        self.spin_sp_dil.setRange(0, 100)
        self.spin_sp_dil.setValue(1)
        self.spin_sp_dil.setSuffix(" points")
        self.spin_sp_dil.setToolTip("Additional points excluded on each side of a detected spike.")
        self.cmb_sp_comp = QComboBox()
        self.cmb_sp_comp.addItem("Magnitude |S|", "abs")
        self.cmb_sp_comp.addItem("Magnitude, Re, Im", "all")
        self.txt_excl = QLineEdit()
        self.txt_excl.setPlaceholderText("Excluded frequency ranges (GHz), e.g. 3.0564-3.0569, 3.0528-3.0532")
        self.chk_excl_drag = QCheckBox("Drag on trace to add exclusion range")
        self.btn_excl_clear = QPushButton("Clear Exclusions")
        self.lbl_clean = QLabel("")
        r = 0
        cl.addWidget(self.chk_spike, r, 0, 1, 3); r += 1
        cl.addWidget(QLabel("Window Half-Width"), r, 0); cl.addWidget(self.spin_sp_half, r, 1, 1, 2); r += 1
        cl.addWidget(QLabel("Threshold"), r, 0); cl.addWidget(self.spin_sp_k, r, 1, 1, 2); r += 1
        cl.addWidget(QLabel("Dilation"), r, 0); cl.addWidget(self.spin_sp_dil, r, 1, 1, 2); r += 1
        cl.addWidget(QLabel("Components"), r, 0); cl.addWidget(self.cmb_sp_comp, r, 1, 1, 2); r += 1
        cl.addWidget(QLabel("Manual Exclusion"), r, 0); cl.addWidget(self.txt_excl, r, 1, 1, 2); r += 1
        cl.addWidget(self.chk_excl_drag, r, 0, 1, 2); cl.addWidget(self.btn_excl_clear, r, 2); r += 1
        cl.addWidget(self.lbl_clean, r, 0, 1, 3)
        return g

    def _build_formula_group(self):
        g = QGroupBox("FIT MODEL")
        fl = QGridLayout(g)
        self.btn_formula = QPushButton("Load Model .py...")
        self.btn_reload = QPushButton("Reload Model")
        self.btn_builder = QPushButton("Model Builder...")
        self.btn_builder.setToolTip("Define an ideal S-parameter model and optionally include Fano phase and environment terms.")
        self.lbl_formula = QLabel("No model loaded")
        self.lbl_formula.setWordWrap(True)
        self.cmb_func = QComboBox()
        self.lbl_sig = QLabel("")
        self.lbl_sig.setWordWrap(True)
        self.btn_library = QPushButton("Model Library...")
        self.btn_library.setStyleSheet("font-weight: bold;")
        self.btn_library.setToolTip("Choose a saved model and view its equation and notes.")
        self.btn_add_lib = QPushButton("Add to Library")
        self.img_formula = FormulaImage(fontsize=14, max_height=160, fit_width=True)
        self.img_formula.setVisible(False)
        self.lbl_lib_notes = QLabel("")
        self.lbl_lib_notes.setWordWrap(True)
        self.lbl_lib_notes.setVisible(False)
        fl.addWidget(self.btn_library, 0, 0); fl.addWidget(self.btn_builder, 0, 1)
        fl.addWidget(self.btn_formula, 1, 0); fl.addWidget(self.btn_reload, 1, 1)
        fl.addWidget(self.lbl_formula, 2, 0, 1, 2)
        fl.addWidget(QLabel("Model Function"), 3, 0); fl.addWidget(self.cmb_func, 3, 1)
        fl.addWidget(self.lbl_sig, 4, 0, 1, 2)
        fl.addWidget(self.img_formula, 5, 0, 1, 2)
        fl.addWidget(self.lbl_lib_notes, 6, 0, 1, 2)
        fl.addWidget(self.btn_add_lib, 7, 0, 1, 2)
        return g

    def _build_option_group(self):
        g = QGroupBox("FIT SETTINGS")
        sl = QGridLayout(g)
        self.chk_weight = QCheckBox("Resonance weighting: σ = |S| + ε")
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
        g = QGroupBox("SINGLE FIT")
        al = QGridLayout(g)
        self.btn_guess = QPushButton("Auto Guess")
        self.btn_preview = QPushButton("Preview Initial Fit")
        self.btn_fit = QPushButton("Start Fit")
        self.btn_fit.setStyleSheet("font-weight: bold; padding: 6px;")
        self.btn_cancel = QPushButton("Cancel")
        self.lbl_fit_progress = QLabel("")
        self.btn_apply = QPushButton("Fitted → Initial")
        self.btn_copy = QPushButton("Copy Fit Report")
        self.btn_save_cfg = QPushButton("Save Settings")
        self.btn_load_cfg = QPushButton("Load Settings")
        self.btn_export = QPushButton("Export Result CSV")
        al.addWidget(self.btn_guess, 0, 0); al.addWidget(self.btn_preview, 0, 1)
        al.addWidget(self.btn_fit, 1, 0); al.addWidget(self.btn_cancel, 1, 1)
        al.addWidget(self.lbl_fit_progress, 2, 0, 1, 2)
        al.addWidget(self.btn_apply, 3, 0); al.addWidget(self.btn_copy, 3, 1)
        al.addWidget(self.btn_save_cfg, 4, 0); al.addWidget(self.btn_load_cfg, 4, 1)
        al.addWidget(self.btn_export, 5, 0, 1, 2)
        return g

    def _build_output_group(self):
        g = QGroupBox("OUTPUT")
        ol = QGridLayout(g)
        self.txt_outdir = QLineEdit()
        self.txt_outdir.setPlaceholderText("Blank uses fit_results beside the source Data.")
        self.btn_outdir = QPushButton("Browse...")
        self.btn_outdir_open = QPushButton("Open")
        self.lbl_outdir = QLabel("")
        self.lbl_outdir.setWordWrap(True)
        self.chk_auto_single = QCheckBox("Save single-fit CSV and plot automatically")
        self.chk_auto_batch = QCheckBox("Export Continuous Fit results automatically")
        ol.addWidget(QLabel("Folder"), 0, 0); ol.addWidget(self.txt_outdir, 0, 1, 1, 2)
        ol.addWidget(self.btn_outdir, 1, 1); ol.addWidget(self.btn_outdir_open, 1, 2)
        ol.addWidget(self.lbl_outdir, 2, 0, 1, 3)
        ol.addWidget(self.chk_auto_single, 3, 0, 1, 3)
        ol.addWidget(self.chk_auto_batch, 4, 0, 1, 3)
        return g

    def _build_batch_group(self):
        g = QGroupBox("CONTINUOUS FIT SETTINGS")
        bl = QGridLayout(g)
        self.spin_b_start = QSpinBox()
        self.spin_b_end = QSpinBox()
        self.btn_b_start_cur = QPushButton("Current")
        self.btn_b_end_cur = QPushButton("Current")
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
        self.btn_b_w2_same = QPushButton("= Window 1")
        self.spin_b_off2 = QDoubleSpinBox()
        self.spin_b_off2.setDecimals(4)
        self.spin_b_off2.setRange(-1e6, 1e6)
        self.spin_b_off2.setSuffix(" MHz")
        self.spin_b_off2.setKeyboardTracking(False)
        self.cmb_b_init = QComboBox()
        for key, label in INIT_MODES.items():
            self.cmb_b_init.addItem(label, key)
        self.chk_b_bound = QCheckBox("Bound frequency parameters to fit window")
        self.chk_b_bound.setChecked(True)
        self.spin_b_r2 = QDoubleSpinBox()
        self.spin_b_r2.setDecimals(3)
        self.spin_b_r2.setRange(-10, 1)
        self.spin_b_r2.setSingleStep(0.05)
        self.spin_b_r2.setValue(0.9)
        self.chk_b_stop = QCheckBox("Stop on failure")
        self.roll_table = RollTable()
        self.chk_roll_clip = QCheckBox("Keep rolling bounds within parameter bounds")
        self.lbl_b_window = QLabel("")
        self.lbl_b_window.setWordWrap(True)
        self.btn_batch = QPushButton("Start Continuous Fit")
        self.btn_batch.setStyleSheet("font-weight: bold; padding: 6px;")
        self.btn_batch_cancel = QPushButton("Cancel")
        self.batch_progress = QProgressBar()
        self.batch_progress.setFormat("%v / %m")
        self.batch_progress.setValue(0)

        r = 0
        bl.addWidget(QLabel("Start Slice"), r, 0); bl.addWidget(self.spin_b_start, r, 1); bl.addWidget(self.btn_b_start_cur, r, 2); r += 1
        bl.addWidget(QLabel("End Slice"), r, 0); bl.addWidget(self.spin_b_end, r, 1); bl.addWidget(self.btn_b_end_cur, r, 2); r += 1
        bl.addWidget(QLabel("Stride"), r, 0); bl.addWidget(self.spin_b_step, r, 1, 1, 2); r += 1
        bl.addWidget(QLabel("Tracking Parameter 1"), r, 0); bl.addWidget(self.cmb_b_track, r, 1, 1, 2); r += 1
        bl.addWidget(QLabel("Tracking Parameter 2"), r, 0); bl.addWidget(self.cmb_b_track2, r, 1, 1, 2); r += 1
        bl.addWidget(QLabel("Window 2 Width"), r, 0); bl.addWidget(self.spin_b_w2, r, 1); bl.addWidget(self.btn_b_w2_same, r, 2); r += 1
        bl.addWidget(QLabel("Window 2 Offset"), r, 0); bl.addWidget(self.spin_b_off2, r, 1, 1, 2); r += 1
        bl.addWidget(QLabel("Initial Guess Source"), r, 0); bl.addWidget(self.cmb_b_init, r, 1, 1, 2); r += 1
        bl.addWidget(QLabel("R² Threshold"), r, 0); bl.addWidget(self.spin_b_r2, r, 1, 1, 2); r += 1
        bl.addWidget(self.chk_b_bound, r, 0, 1, 3); r += 1
        bl.addWidget(self.chk_b_stop, r, 0, 1, 3); r += 1
        lbl_roll = QLabel("Rolling parameter tracking (e.g. φ): use the previous value or extrapolation with local bounds.")
        lbl_roll.setWordWrap(True)
        bl.addWidget(lbl_roll, r, 0, 1, 3); r += 1
        bl.addWidget(self.roll_table, r, 0, 1, 3); r += 1
        bl.addWidget(self.chk_roll_clip, r, 0, 1, 3); r += 1
        bl.addWidget(self.lbl_b_window, r, 0, 1, 3); r += 1
        bl.addWidget(self.btn_batch, r, 0, 1, 2); bl.addWidget(self.btn_batch_cancel, r, 2); r += 1
        bl.addWidget(self.batch_progress, r, 0, 1, 3); r += 1
        note = QLabel("Window 1 follows Tracking Parameter 1 using the current fit-window width.\n"
                      "Optional Window 2 follows Tracking Parameter 2 with a fixed width and offset.\n"
                      "When enabled, fits use the union of both windows; the first slice uses the parameter-table initial values.")
        note.setWordWrap(True)
        bl.addWidget(note, r, 0, 1, 3)
        return g
