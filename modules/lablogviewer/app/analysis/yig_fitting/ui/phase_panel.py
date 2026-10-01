"""Phase, physical Node/Antinode and linked-fit workspace."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QGroupBox, QPushButton, QLabel, QComboBox, QSpinBox,
    QDoubleSpinBox, QCheckBox, QSplitter, QScrollArea, QPlainTextEdit, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QHBoxLayout,
)

from .plots import PhasePlotWidget
from ..core.phase import ROLES
from app.gui.fonts import mono_family

NONE = "(None)"
SOURCES = [
    ("kappa", "κ_eff = κ_b sin²φ (recommended)"),
    ("phi", "Fitted φ (mod π)"),
    ("nodes", "Dip candidate frequencies"),
]


def _dspin(dec, lo, hi, val=0.0, suffix="", step=None):
    sp = QDoubleSpinBox()
    sp.setDecimals(dec)
    sp.setRange(lo, hi)
    sp.setValue(val)
    sp.setKeyboardTracking(False)
    if suffix:
        sp.setSuffix(suffix)
    if step:
        sp.setSingleStep(step)
    return sp


def _gray(text):
    lb = QLabel(text)
    lb.setWordWrap(True)
    return lb


class RoleTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(0, 3, parent)
        self.setHorizontalHeaderLabels(["Parameter", "Unit", "Global Fit Role"])
        self.verticalHeader().setVisible(False)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.setMinimumHeight(220)

    def set_rows(self, names, units, roles):
        self.setRowCount(len(names))
        for r, (n, u) in enumerate(zip(names, units)):
            for c, t in ((0, n), (1, u)):
                it = QTableWidgetItem(t)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.setItem(r, c, it)
            cb = QComboBox()
            for key, label in ROLES.items():
                cb.addItem(label, key)
            i = cb.findData(roles.get(n, "shared"))
            cb.setCurrentIndex(max(i, 0))
            self.setCellWidget(r, 2, cb)

    def roles(self):
        out = {}
        for r in range(self.rowCount()):
            out[self.item(r, 0).text()] = self.cellWidget(r, 2).currentData()
        return out

    def set_role(self, name, role):
        for r in range(self.rowCount()):
            if self.item(r, 0).text() == name:
                cb = self.cellWidget(r, 2)
                i = cb.findData(role)
                if i >= 0:
                    cb.setCurrentIndex(i)


class PhasePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(self._build_map_group())
        lv.addWidget(self._build_line_group())
        lv.addWidget(self._build_coarse_group())
        lv.addWidget(self._build_link_group())
        lv.addWidget(self._build_global_group())
        lv.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setWidget(left)
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumWidth(380)

        self.plot = PhasePlotWidget()
        self.results_table = QTableWidget(0, 7)
        self.results_table.setHorizontalHeaderLabels(
            ["Type", "Method", "Sweep", "Frequency (GHz)", "Phase (rad)", "κm (Hz)", "Status"]
        )
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.txt = QPlainTextEdit()
        self.txt.setReadOnly(True)
        self.txt.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.txt.setStyleSheet(f"font-family: '{mono_family()}';")
        self.result_splitter = QSplitter(Qt.Orientation.Vertical)
        self.result_splitter.addWidget(self.results_table)
        self.result_splitter.addWidget(self.txt)
        self.result_splitter.setSizes([150, 110])
        self.plot_splitter = QSplitter(Qt.Orientation.Vertical)
        self.plot_splitter.addWidget(self.plot)
        self.plot_splitter.addWidget(self.result_splitter)
        self.plot_splitter.setSizes([650, 260])

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self.scroll)
        self.main_splitter.addWidget(self.plot_splitter)
        self.main_splitter.setSizes([400, 900])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.main_splitter)

    # ------------------------------------------------------------------ 群組
    def _build_map_group(self):
        g = QGroupBox("PHYSICAL MAPPING")
        gl = QGridLayout(g)
        self.cmb_phase = QComboBox()
        self.cmb_freq = QComboBox()
        self.cmb_kappa = QComboBox()
        self.chk_kappa_sin = QCheckBox("κ_eff = coupling parameter × sin²(phase parameter)")
        self.chk_kappa_sin.setChecked(True)
        self.chk_kappa_sin.setToolTip("Uncheck if the model parameter is already effective coupling and has no sin²φ factor.")
        self.spin_period = _dspin(3, 0.001, 100, 1.0, " π", 0.5)
        self.spin_period.setToolTip("Phase period for κ_b sin²φ; the standard period is π.")
        r = 0
        gl.addWidget(QLabel("Phase Parameter φ"), r, 0); gl.addWidget(self.cmb_phase, r, 1); r += 1
        gl.addWidget(QLabel("Resonance Frequency Parameter"), r, 0); gl.addWidget(self.cmb_freq, r, 1); r += 1
        gl.addWidget(QLabel("Coupling Parameter κ_b"), r, 0); gl.addWidget(self.cmb_kappa, r, 1); r += 1
        gl.addWidget(self.chk_kappa_sin, r, 0, 1, 2); r += 1
        gl.addWidget(QLabel("Phase Period P"), r, 0); gl.addWidget(self.spin_period, r, 1); r += 1
        gl.addWidget(_gray("The phase is modeled as linear in resonance frequency. For κm = κb sin²φ, φ = nP is a physical Node and φ = (n + 1/2)P is a physical Antinode."), r, 0, 1, 2)
        return g

    def _build_line_group(self):
        g = QGroupBox("PHASE LINE  φ = φ_ref + 2π·T·(f_m − f_ref)")
        gl = QGridLayout(g)
        self.cmb_source = QComboBox()
        for key, label in SOURCES:
            self.cmb_source.addItem(label, key)
        self.spin_tmax = _dspin(3, 0.01, 1e4, 20.0, " ns")
        self.spin_tmax.setToolTip("Upper bound for the T grid search; adjacent Nodes are separated by 1/(2T).")
        self.btn_estimate = QPushButton("Estimate from Continuous Fit")
        self.btn_estimate.setStyleSheet("font-weight: bold;")
        self.txt_nodes = QLineEdit()
        self.txt_nodes.setPlaceholderText("Dip candidate frequencies (GHz), e.g. 4.26, 4.59, 4.93")
        self.spin_thr = _dspin(2, 0.01, 0.99, 0.35, "", 0.05)
        self.spin_thr.setToolTip("Local minima below threshold × the 90th percentile are treated as dip candidates.")
        self.btn_detect = QPushButton("Extract Dip Candidates")
        self.btn_from_nodes = QPushButton("Estimate from Candidates")
        self.spin_T = _dspin(6, -1e5, 1e5, 0.0, " ns", 0.01)
        self.spin_phi_ref = _dspin(5, -1e3, 1e3, 0.0, " rad", 0.05)
        self.spin_fref = _dspin(6, 0, 1e4, 5.0, " GHz", 0.1)
        self.spin_kb = _dspin(6, 0, 1e9, 0.0, "", 0.1)
        self.spin_kb.setToolTip("Estimated κ_b in the selected coupling parameter unit; 0 disables it.")
        self.chk_show_nodes = QCheckBox("Show Physical Node/Antinode on Data Preview")
        self.chk_show_nodes.setChecked(True)
        self.btn_redraw = QPushButton("Refresh")
        self.btn_export_points = QPushButton("Export Location Results CSV...")
        self.lbl_line = _gray("")
        r = 0
        gl.addWidget(QLabel("Estimator Source"), r, 0); gl.addWidget(self.cmb_source, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("T Search Maximum"), r, 0); gl.addWidget(self.spin_tmax, r, 1, 1, 2); r += 1
        gl.addWidget(self.btn_estimate, r, 0, 1, 3); r += 1
        gl.addWidget(QLabel("Dip Candidate Frequencies"), r, 0); gl.addWidget(self.txt_nodes, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("Candidate Threshold"), r, 0); gl.addWidget(self.spin_thr, r, 1)
        gl.addWidget(self.btn_detect, r, 2); r += 1
        gl.addWidget(self.btn_from_nodes, r, 2); r += 1
        gl.addWidget(QLabel("T = x/v_g"), r, 0); gl.addWidget(self.spin_T, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("φ_ref"), r, 0); gl.addWidget(self.spin_phi_ref, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("f_ref"), r, 0); gl.addWidget(self.spin_fref, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("κ_b (estimated)"), r, 0); gl.addWidget(self.spin_kb, r, 1, 1, 2); r += 1
        gl.addWidget(self.chk_show_nodes, r, 0, 1, 2)
        gl.addWidget(self.btn_redraw, r, 2); r += 1
        gl.addWidget(self.btn_export_points, r, 0, 1, 3); r += 1
        gl.addWidget(self.lbl_line, r, 0, 1, 3); r += 1
        gl.addWidget(_gray("Workflow: Continuous Fit → estimate the phase line → Global Linked Fit → optional phase-linked Continuous Fit.\n"
                           "A single trace identifies κ_eff = κ_b sin²φ only. The κ estimator cannot distinguish T from −T; it reports the positive convention."), r, 0, 1, 3)
        return g

    def _build_link_group(self):
        g = QGroupBox("PHASE-LINKED CONTINUOUS FIT")
        gl = QGridLayout(g)
        self.cmb_link_mode = QComboBox()
        self.cmb_link_mode.addItem("Hard link: φ fixed by phase line", "hard")
        self.cmb_link_mode.addItem("Soft link: fit φ within a range around line", "soft")
        self.spin_soft = _dspin(3, 0.001, 10, 0.3, " rad", 0.05)
        self.chk_skip = QCheckBox("Skip slices near Nodes (weak signal)")
        self.chk_skip.setChecked(True)
        self.spin_guard = _dspin(3, 0.0, 1.5, 0.12, " rad", 0.02)
        self.lbl_guard = _gray("")
        self.chk_fix_shared = QCheckBox("Fix shared parameters (use Global Fit or κ_b estimate)")
        self.chk_fix_shared.setChecked(True)
        self.chk_fix_shared.setToolTip(
            "When φ is linked to f_m, freeing κ_b and γ_0 per slice can make f_m non-identifiable.\n"
            "Fixing shared parameters helps constrain the per-slice resonance frequency.")
        self.lbl_fix = _gray("")
        self.btn_link_batch = QPushButton("Start Phase-Linked Continuous Fit")
        self.btn_link_batch.setStyleSheet("font-weight: bold; padding: 5px;")
        r = 0
        gl.addWidget(QLabel("Link Mode"), r, 0); gl.addWidget(self.cmb_link_mode, r, 1); r += 1
        gl.addWidget(QLabel("Soft-Link Range"), r, 0); gl.addWidget(self.spin_soft, r, 1); r += 1
        gl.addWidget(self.chk_skip, r, 0, 1, 2); r += 1
        gl.addWidget(QLabel("|φ − nP| <"), r, 0); gl.addWidget(self.spin_guard, r, 1); r += 1
        gl.addWidget(self.lbl_guard, r, 0, 1, 2); r += 1
        gl.addWidget(self.chk_fix_shared, r, 0, 1, 2); r += 1
        gl.addWidget(self.lbl_fix, r, 0, 1, 2); r += 1
        gl.addWidget(self.btn_link_batch, r, 0, 1, 2); r += 1
        gl.addWidget(_gray("Skipped or failed slices do not update tracking. The fit extrapolates across weak-signal Nodes."), r, 0, 1, 2)
        return g

    def _build_coarse_group(self):
        self.coarse_group = QGroupBox("Coarse Detector (empirical fallback)")
        outer = QVBoxLayout(self.coarse_group)
        self.chk_coarse_enabled = QCheckBox("Enable Coarse Detector")
        self.chk_coarse_enabled.setChecked(False)
        outer.addWidget(self.chk_coarse_enabled)
        self.coarse_controls = QWidget()
        layout = QGridLayout(self.coarse_controls)
        self.cmb_coarse_mode = QComboBox()
        self.cmb_coarse_mode.addItems(["Both", "Node", "Antinode"])
        self.spin_coarse_window = _dspin(4, 1e-6, 1e4, 0.25, " GHz")
        self.spin_coarse_smoothing = QSpinBox()
        self.spin_coarse_smoothing.setRange(1, 101)
        self.spin_coarse_smoothing.setValue(3)
        self.spin_coarse_distance = QSpinBox()
        self.spin_coarse_distance.setRange(1, 100000)
        self.spin_coarse_distance.setValue(10)
        self.spin_coarse_prominence = _dspin(6, 0.0, 1e6, 0.0002)
        self.chk_coarse_manual_threshold = QCheckBox("Manual dip-depth threshold")
        self.spin_coarse_threshold = _dspin(6, 0.0, 1e9, 0.1)
        self.spin_coarse_threshold.setEnabled(False)
        self.chk_coarse_manual_threshold.toggled.connect(self.spin_coarse_threshold.setEnabled)
        self.btn_coarse_run = QPushButton("Run Coarse Detection")
        self.btn_coarse_cancel = QPushButton("Cancel")
        self.btn_coarse_cancel.setEnabled(False)
        self.lbl_coarse_status = _gray("Disabled. Coarse candidates are empirical and are not physical extrema.")
        row = 0
        layout.addWidget(QLabel("Candidate type"), row, 0); layout.addWidget(self.cmb_coarse_mode, row, 1); row += 1
        layout.addWidget(QLabel("Trajectory half-width"), row, 0); layout.addWidget(self.spin_coarse_window, row, 1); row += 1
        layout.addWidget(QLabel("Smoothing points"), row, 0); layout.addWidget(self.spin_coarse_smoothing, row, 1); row += 1
        layout.addWidget(QLabel("Minimum sweep distance"), row, 0); layout.addWidget(self.spin_coarse_distance, row, 1); row += 1
        layout.addWidget(QLabel("Prominence"), row, 0); layout.addWidget(self.spin_coarse_prominence, row, 1); row += 1
        layout.addWidget(self.chk_coarse_manual_threshold, row, 0); layout.addWidget(self.spin_coarse_threshold, row, 1); row += 1
        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_coarse_run)
        buttons.addWidget(self.btn_coarse_cancel)
        layout.addLayout(buttons, row, 0, 1, 2); row += 1
        layout.addWidget(self.lbl_coarse_status, row, 0, 1, 2)
        self.coarse_controls.setVisible(False)
        outer.addWidget(self.coarse_controls)
        return self.coarse_group

    def _build_global_group(self):
        g = QGroupBox("GLOBAL LINKED FIT")
        gl = QGridLayout(g)
        self.spin_stride = QSpinBox()
        self.spin_stride.setRange(1, 10000)
        self.spin_stride.setValue(1)
        self.spin_stride.setToolTip("Use every Nth eligible slice to reduce runtime.")
        self.chk_ok_only = QCheckBox("Use successful slices only")
        self.chk_ok_only.setChecked(True)
        self.role_table = RoleTable()
        self.btn_default_roles = QPushButton("Reset Roles")
        self.chk_fit_T = QCheckBox("Fit T")
        self.chk_fit_T.setChecked(True)
        self.chk_fit_phi = QCheckBox("Fit φref")
        self.chk_fit_phi.setChecked(True)
        self.spin_gnfev = QSpinBox()
        self.spin_gnfev.setRange(5, 100000)
        self.spin_gnfev.setValue(200)
        self.spin_gnfev.setToolTip("Maximum objective evaluations; each evaluation includes all selected slices.")
        self.lbl_gslices = _gray("")
        self.btn_global = QPushButton("Run Global Linked Fit")
        self.btn_global.setStyleSheet("font-weight: bold; padding: 5px;")
        self.btn_global_cancel = QPushButton("Cancel")
        self.lbl_gprog = _gray("")
        self.btn_apply_shared = QPushButton("Shared Values → Parameter Initials")
        self.btn_to_batch = QPushButton("Send Results to Continuous Fit")
        self.btn_to_batch.setToolTip("Load each per-slice Global Fit result into Continuous Fit for review.")
        self.btn_export = QPushButton("Export Global Fit CSV")
        r = 0
        gl.addWidget(QLabel("Slice Stride"), r, 0); gl.addWidget(self.spin_stride, r, 1)
        gl.addWidget(self.chk_ok_only, r, 2); r += 1
        gl.addWidget(self.role_table, r, 0, 1, 3); r += 1
        gl.addWidget(self.btn_default_roles, r, 0)
        gl.addWidget(self.chk_fit_T, r, 1); gl.addWidget(self.chk_fit_phi, r, 2); r += 1
        gl.addWidget(QLabel("max nfev"), r, 0); gl.addWidget(self.spin_gnfev, r, 1, 1, 2); r += 1
        gl.addWidget(self.lbl_gslices, r, 0, 1, 3); r += 1
        hb = QHBoxLayout()
        hb.addWidget(self.btn_global, 3)
        hb.addWidget(self.btn_global_cancel, 1)
        gl.addLayout(hb, r, 0, 1, 3); r += 1
        gl.addWidget(self.lbl_gprog, r, 0, 1, 3); r += 1
        gl.addWidget(self.btn_apply_shared, r, 0, 1, 3); r += 1
        gl.addWidget(self.btn_to_batch, r, 0, 1, 3); r += 1
        gl.addWidget(self.btn_export, r, 0, 1, 3); r += 1
        gl.addWidget(_gray("Input comes from the current Continuous Fit results, including fit windows and preprocessing.\n"
                           "Roles: Phase Line, per-slice, shared, or fixed."), r, 0, 1, 3)
        return g
