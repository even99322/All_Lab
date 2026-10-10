"""「相位 / 節點」分頁：只負責版面（邏輯在 phase_controller.py）"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QGroupBox, QPushButton, QLabel, QComboBox, QSpinBox,
    QDoubleSpinBox, QCheckBox, QSplitter, QScrollArea, QPlainTextEdit, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QHBoxLayout,
)

from .plots import PhasePlotWidget
from ..core.phase import ROLES

NONE = "（無）"
SOURCES = [
    ("kappa", "κ_eff = 耦合 × sin²φ（建議）"),
    ("phi", "φ 擬合值（mod π）"),
    ("nodes", "節點頻率"),
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
    lb.setStyleSheet("color: gray;")
    return lb


class RoleTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(0, 3, parent)
        self.setHorizontalHeaderLabels(["參數", "單位", "全域擬合角色"])
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
        lv.addWidget(self._build_link_group())
        lv.addWidget(self._build_global_group())
        lv.addStretch()
        scroll = QScrollArea()
        scroll.setWidget(left)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(380)

        self.plot = PhasePlotWidget()
        self.txt = QPlainTextEdit()
        self.txt.setReadOnly(True)
        self.txt.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.txt.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.plot)
        right.addWidget(self.txt)
        right.setSizes([650, 220])

        sp = QSplitter(Qt.Orientation.Horizontal)
        sp.addWidget(scroll)
        sp.addWidget(right)
        sp.setSizes([400, 900])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(sp)

    # ------------------------------------------------------------------ 群組
    def _build_map_group(self):
        g = QGroupBox("① 參數對應")
        gl = QGridLayout(g)
        self.cmb_phase = QComboBox()
        self.cmb_freq = QComboBox()
        self.cmb_kappa = QComboBox()
        self.chk_kappa_sin = QCheckBox("κ_eff = 耦合參數 × sin²(相位參數)")
        self.chk_kappa_sin.setChecked(True)
        self.chk_kappa_sin.setToolTip("公式本身已是有效耦合（沒有 sin²φ）時取消勾選")
        self.spin_period = _dspin(3, 0.001, 100, 1.0, " π", 0.5)
        self.spin_period.setToolTip("節點週期：κ_b sin²φ → π")
        r = 0
        gl.addWidget(QLabel("相位參數 φ"), r, 0); gl.addWidget(self.cmb_phase, r, 1); r += 1
        gl.addWidget(QLabel("共振頻率參數"), r, 0); gl.addWidget(self.cmb_freq, r, 1); r += 1
        gl.addWidget(QLabel("耦合參數 κ_b"), r, 0); gl.addWidget(self.cmb_kappa, r, 1); r += 1
        gl.addWidget(self.chk_kappa_sin, r, 0, 1, 2); r += 1
        gl.addWidget(QLabel("節點週期 P"), r, 0); gl.addWidget(self.spin_period, r, 1); r += 1
        gl.addWidget(_gray("φ = k_m x = 2π f_m (x/v_g) → φ 隨共振頻率線性變化；"
                           "φ = nP 時 κ_m = 0，2D 圖上訊號消失（節點）。"), r, 0, 1, 2)
        return g

    def _build_line_group(self):
        g = QGroupBox("② 相位直線  φ = φ_ref + 2π·T·(f_m − f_ref)")
        gl = QGridLayout(g)
        self.cmb_source = QComboBox()
        for key, label in SOURCES:
            self.cmb_source.addItem(label, key)
        self.spin_tmax = _dspin(3, 0.01, 1e4, 20.0, " ns")
        self.spin_tmax.setToolTip("網格搜尋 T 的上限；節點間距 = 1/(2T)")
        self.btn_estimate = QPushButton("由連續擬合結果估計")
        self.btn_estimate.setStyleSheet("font-weight: bold;")
        self.txt_nodes = QLineEdit()
        self.txt_nodes.setPlaceholderText("節點頻率 GHz，例如 4.26, 4.59, 4.93")
        self.spin_thr = _dspin(2, 0.01, 0.99, 0.35, "", 0.05)
        self.spin_thr.setToolTip("訊號強度低於 (門檻 × 90% 分位數) 的局部最小值視為節點")
        self.btn_detect = QPushButton("自動偵測節點")
        self.btn_from_nodes = QPushButton("由節點估計")
        self.spin_T = _dspin(6, -1e5, 1e5, 0.0, " ns", 0.01)
        self.spin_phi_ref = _dspin(5, -1e3, 1e3, 0.0, " rad", 0.05)
        self.spin_fref = _dspin(6, 0, 1e4, 5.0, " GHz", 0.1)
        self.spin_kb = _dspin(6, 0, 1e9, 0.0, "", 0.1)
        self.spin_kb.setToolTip("估計的 κ_b（單位同耦合參數）；0 = 不使用")
        self.chk_show_nodes = QCheckBox("在數據預覽標示節點")
        self.chk_show_nodes.setChecked(True)
        self.btn_redraw = QPushButton("重畫")
        self.lbl_line = _gray("")
        r = 0
        gl.addWidget(QLabel("估計來源"), r, 0); gl.addWidget(self.cmb_source, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("T 搜尋上限"), r, 0); gl.addWidget(self.spin_tmax, r, 1, 1, 2); r += 1
        gl.addWidget(self.btn_estimate, r, 0, 1, 3); r += 1
        gl.addWidget(QLabel("節點"), r, 0); gl.addWidget(self.txt_nodes, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("偵測門檻"), r, 0); gl.addWidget(self.spin_thr, r, 1)
        gl.addWidget(self.btn_detect, r, 2); r += 1
        gl.addWidget(self.btn_from_nodes, r, 2); r += 1
        gl.addWidget(QLabel("T = x/v_g"), r, 0); gl.addWidget(self.spin_T, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("φ_ref"), r, 0); gl.addWidget(self.spin_phi_ref, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("f_ref"), r, 0); gl.addWidget(self.spin_fref, r, 1, 1, 2); r += 1
        gl.addWidget(QLabel("κ_b（估計）"), r, 0); gl.addWidget(self.spin_kb, r, 1, 1, 2); r += 1
        gl.addWidget(self.chk_show_nodes, r, 0, 1, 2); gl.addWidget(self.btn_redraw, r, 2); r += 1
        gl.addWidget(self.lbl_line, r, 0, 1, 3); r += 1
        gl.addWidget(_gray("建議流程：⑤ 一般連續擬合（φ、κ_b 自由；w_m 滾動追蹤勾「外插」才能穿過節點）"
                           "→ 估計直線 → ④ 全域擬合（求共用參數與精確 T）"
                           "→ ③ 相位連結連續擬合（固定共用參數）→ ④ 再做一次全域擬合。\n"
                           "單片擬合只能決定 κ_eff = κ_b sin²φ，所以預設用 κ_eff 估計；"
                           "sin² 對 φ↔−φ 對稱，T 的正負無法分辨（慣例取正）。"), r, 0, 1, 3)
        return g

    def _build_link_group(self):
        g = QGroupBox("③ 相位連結的連續擬合（使用 ⑤ 的切片/視窗設定）")
        gl = QGridLayout(g)
        self.cmb_link_mode = QComboBox()
        self.cmb_link_mode.addItem("硬連結：φ 由直線決定（不擬合）", "hard")
        self.cmb_link_mode.addItem("軟連結：φ 初值 = 直線，± 範圍內擬合", "soft")
        self.spin_soft = _dspin(3, 0.001, 10, 0.3, " rad", 0.05)
        self.chk_skip = QCheckBox("略過節點附近的切片（訊號太弱）")
        self.chk_skip.setChecked(True)
        self.spin_guard = _dspin(3, 0.0, 1.5, 0.12, " rad", 0.02)
        self.lbl_guard = _gray("")
        self.chk_fix_shared = QCheckBox("固定「全片共用」參數（用全域擬合值 / κ_b 估計值）")
        self.chk_fix_shared.setChecked(True)
        self.chk_fix_shared.setToolTip(
            "φ 被綁在 w_m 上時，單片若同時放開 κ_b、γ_0，w_m 會與它們互相抵換而偏掉；\n"
            "固定共用參數後 w_m 才有唯一解。")
        self.lbl_fix = _gray("")
        self.btn_link_batch = QPushButton("開始相位連結連續擬合")
        self.btn_link_batch.setStyleSheet("font-weight: bold; padding: 5px;")
        r = 0
        gl.addWidget(QLabel("模式"), r, 0); gl.addWidget(self.cmb_link_mode, r, 1); r += 1
        gl.addWidget(QLabel("軟連結範圍"), r, 0); gl.addWidget(self.spin_soft, r, 1); r += 1
        gl.addWidget(self.chk_skip, r, 0, 1, 2); r += 1
        gl.addWidget(QLabel("|φ − nP| <"), r, 0); gl.addWidget(self.spin_guard, r, 1); r += 1
        gl.addWidget(self.lbl_guard, r, 0, 1, 2); r += 1
        gl.addWidget(self.chk_fix_shared, r, 0, 1, 2); r += 1
        gl.addWidget(self.lbl_fix, r, 0, 1, 2); r += 1
        gl.addWidget(self.btn_link_batch, r, 0, 1, 2); r += 1
        gl.addWidget(_gray("略過（或失敗）的切片不更新追蹤，視窗以前兩片成功值線性外插穿過節點，"
                           "過了節點再接著擬合。"), r, 0, 1, 2)
        return g

    def _build_global_group(self):
        g = QGroupBox("④ 全域擬合（多片同時擬合，共用參數）")
        gl = QGridLayout(g)
        self.spin_stride = QSpinBox()
        self.spin_stride.setRange(1, 10000)
        self.spin_stride.setValue(1)
        self.spin_stride.setToolTip("每隔幾片取一片（片數多時可加快）")
        self.chk_ok_only = QCheckBox("只用成功的切片")
        self.chk_ok_only.setChecked(True)
        self.role_table = RoleTable()
        self.btn_default_roles = QPushButton("預設角色")
        self.chk_fit_T = QCheckBox("擬合 T")
        self.chk_fit_T.setChecked(True)
        self.chk_fit_phi = QCheckBox("擬合 φ_ref")
        self.chk_fit_phi.setChecked(True)
        self.spin_gnfev = QSpinBox()
        self.spin_gnfev.setRange(5, 100000)
        self.spin_gnfev.setValue(200)
        self.spin_gnfev.setToolTip("最多迭代的函式評估次數（每次會算完所有切片）")
        self.lbl_gslices = _gray("")
        self.btn_global = QPushButton("開始全域擬合")
        self.btn_global.setStyleSheet("font-weight: bold; padding: 5px;")
        self.btn_global_cancel = QPushButton("中止")
        self.lbl_gprog = _gray("")
        self.btn_apply_shared = QPushButton("共用值 → 參數表初值")
        self.btn_to_batch = QPushButton("結果 → 連續擬合分頁")
        self.btn_to_batch.setToolTip("把每片的全域擬合結果放到「連續擬合」分頁，可逐片調閱擬合圖")
        self.btn_export = QPushButton("匯出全域結果 CSV")
        r = 0
        gl.addWidget(QLabel("取樣間隔"), r, 0); gl.addWidget(self.spin_stride, r, 1)
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
        gl.addWidget(_gray("資料來源 = 「連續擬合」分頁目前的結果（視窗、去雜訊設定與每片初值）。\n"
                           "「相位直線」：φ 由 T、φ_ref 與該片共振頻率決定；"
                           "「每片獨立」：每片各自一個值；「全片共用」：所有片同一個值。"), r, 0, 1, 3)
        return g
