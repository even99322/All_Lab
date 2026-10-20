"""即時監控：VNA。

上半部（可以拉小 / 收起）：選 VNA、RF、S 參數、參數（含掃描時間）。
S 參數按鈕依 VNA 的 port 數自動產生（4 port → S11…S44 的 4×4 矩陣，例如 S31、S43）：選 VNA 時自動偵測，
「偵測 port」按鈕重新偵測；ZNA 上已經有 trace 的參數用粗體標示，沒有的量測時自動建立。
下半部：曲線，可以複選 dB / Linear / Real / Imag / Phase / Unwrap phase / IQ（最多 5 種）。
1.0.6 像 VNA 儀器的 diagram：每張圖可以疊幾種曲線（左右兩軸），疊圖與並排同時存在——拖曳圖的 ⠿ 或曲線名稱，
放到另一張圖中間 = 疊上去、放到邊緣 = 放在旁邊；版面：自動（2 張上下、3 張上左右＋下、4 張四方格…）、上下、左右。
可以疊加數據點；「⧉ 曲線視窗」把圖拿出來單獨一個視窗。

刷新：「連續」＋ 間隔（秒）。間隔 = 最短（預設）→ 上一次讀完立刻讀下一次，刷新間隔就是實際量測時間。
VNA 掃描速度用「掃描時間」「自動掃描時間」、點數、IF 頻寬控制。
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, pyqtSignal

from ....core.units import parse_quantity, split_unit
from ....paths import lab_path
from ....settings import setting
from .. import theme
from ..worker import run_bg
from .traces import (DROP_AFTER, DROP_BEFORE, DROP_CENTER, FORMATS, MAX_TRACES, PLOT_MIME, FormatBar,  # noqa: F401
                     PlotCell, TracePlots, can_overlay, cell_id, grid_positions, key_fmt, key_src, layout_positions,
                     migrate, normalize_groups, overlay_sides, tkey, trace_label, valid_key, view_data)

SPARAMS = ["S11", "S12", "S21", "S22"]
#: 即時監控顯示的 VNA 參數（依序；驅動沒有的略過）
PREFERRED = ["start_freq", "stop_freq", "center_freq", "span", "points", "power", "if_bw", "averages",
             "sweep_time", "sweep_time_auto"]
#: 相容：格式 key → (按鈕文字, y 軸標題)
VIEWS = {k: (v[0], v[1]) for k, v in FORMATS.items()}


def _fmt(v: Any, p: Dict[str, Any]) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "ON" if v else "OFF"
    du = p.get("display_unit") or p.get("unit") or ""
    sc = split_unit(du)[1] if du else 1.0
    try:
        return f"{float(v) / (sc or 1.0):.9g}"
    except (TypeError, ValueError):
        return str(v)


class _UIState:
    """LAB/live_ui.json：記住曲線種類、S 參數、刷新間隔（每台電腦自己的偏好）。"""

    def __init__(self) -> None:
        self.path = lab_path("live_ui.json")

    def load(self) -> Dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return {}

    def save(self, **kw: Any) -> None:
        d = self.load()
        d.update(kw)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


MAX_VIEWS = MAX_TRACES
SOURCE_TIP = "目前的 trace 量哪個 S 參數（點圖上的 trace 名稱換一條）；粗體 = ZNA 上已經有這條 trace"


class PlotWindow(QtWidgets.QMainWindow):
    closed = pyqtSignal()

    def __init__(self, w: QtWidgets.QWidget, title: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
        # 有 parent（Qt 擁有，關閉時由 Qt 刪除）：避免在 closeEvent 裡被 Python 回收而當機
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(title)
        self.resize(900, 700)
        self.setCentralWidget(w)
        w.show()

    def closeEvent(self, ev) -> None:
        w = self.takeCentralWidget()
        if w is not None:
            w.setParent(None)
        self.closed.emit()
        super().closeEvent(ev)


#: 1.0.12：常用設定（快速切換）存的參數——起訖頻率（不存中心 / 頻寬，免得互相蓋掉）、點數、功率、IF、平均、掃描時間
PRESET_KEYS = ["start_freq", "stop_freq", "points", "power", "if_bw", "averages", "sweep_time_auto", "sweep_time"]
MAX_PRESETS = 12
DEFAULT_AVG = 10


class AvgEdit(QtWidgets.QWidget):
    """1.0.12：VNA 平均 = 勾選開關 ＋ 次數。關掉時次數保留（不再用 0 表示關閉）；寫入 VNA 時關閉 = 0。"""

    changed = pyqtSignal()
    stepped = pyqtSignal()

    def __init__(self, count: int = DEFAULT_AVG, parent=None) -> None:
        super().__init__(parent)
        from ..digit_edit import WheelLineEdit

        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        self.check = QtWidgets.QCheckBox("開")
        self.check.setObjectName("vnaAvgOn")
        self.count = WheelLineEdit(str(max(1, int(count))))
        self.count.setObjectName("vnaAvgCount")
        self.count.setMinimumWidth(50)
        self.count.setEnabled(False)
        self.count.setToolTip("平均次數（關掉時保留，下次打開沿用）")
        h.addWidget(self.check)
        h.addWidget(self.count, 1)
        self.check.clicked.connect(lambda on: (self.count.setEnabled(on), self.changed.emit()))
        self.count.textEdited.connect(lambda _t: self.changed.emit())
        self.count.stepped.connect(self.stepped.emit)

    def remembered(self) -> int:
        try:
            return max(1, int(round(float(self.count.text()))))
        except ValueError:
            return DEFAULT_AVG

    def value(self) -> int:
        """寫進 VNA 的值：關閉 = 0，開啟 = 次數（≥ 1）。"""
        return self.remembered() if self.check.isChecked() else 0

    def set_value(self, n: Any, remembered: Optional[int] = None) -> None:
        try:
            n = int(round(float(n or 0)))
        except (TypeError, ValueError):
            n = 0
        self.check.setChecked(n >= 1)
        self.count.setText(str(n if n >= 1 else max(1, int(remembered or self.remembered()))))
        self.count.setEnabled(n >= 1)


class VNAPanel(QtWidgets.QWidget):
    message = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.backend = None
        self.params: Dict[str, Dict[str, Any]] = {}      # name → describe 的參數
        self.rf_param: Optional[Dict[str, Any]] = None
        self.edits: Dict[str, QtWidgets.QWidget] = {}
        self.labels: Dict[str, QtWidgets.QLabel] = {}
        self.dirty: set = set()
        self.busy = False
        self._reading = False
        self._gen = 0
        self._cont_gen = 0
        self._cols = 0
        self.last: Optional[tuple] = None
        self.plot_window: Optional[PlotWindow] = None
        self.ui = _UIState()
        saved = self.ui.load()

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.split = QtWidgets.QSplitter(Qt.Orientation.Vertical)
        self.split.setChildrenCollapsible(True)
        v.addWidget(self.split, 1)

        # ---- 上半部：參數 ----
        top = QtWidgets.QWidget()
        tl = QtWidgets.QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 4)
        tl.setSpacing(6)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        self.vna = QtWidgets.QComboBox()
        self.vna.setMinimumWidth(110)
        self.vna.currentIndexChanged.connect(lambda _i: self.load_params())
        row.addWidget(self.vna)
        self.rf = QtWidgets.QCheckBox("RF 輸出")
        self.rf.clicked.connect(self.set_rf)
        row.addWidget(self.rf)
        row.addSpacing(6)
        self.b_ports = QtWidgets.QPushButton("掃描 port")
        self.b_ports.setToolTip("問 VNA 有幾個 port，並用目前的設定把全部 S 參數（4 port = 16 個）掃一次：\n"
                                "只顯示有接線的 port 組合（穿透高於門檻的 Sij 與那些 port 的 Sii）；\n"
                                "其他的可以在「儀器伺服器 → 右鍵 VNA → S 參數顯示…」打開")
        self.b_ports.clicked.connect(lambda: self.detect_ports())
        row.addWidget(self.b_ports)
        self.ports_label = QtWidgets.QLabel("")
        self.ports_label.setProperty("role", "muted")
        row.addWidget(self.ports_label)
        row.addStretch(1)
        tl.addLayout(row)
        self.sgroup = QtWidgets.QButtonGroup(self)
        self.sgroup.setExclusive(True)
        self.sgroup.buttonToggled.connect(lambda b, on: on and not self._quiet_sbtn and self._sparam_changed())
        self._quiet_sbtn = False
        self.sbtn: Dict[str, QtWidgets.QPushButton] = {}
        self.sbox = QtWidgets.QWidget()
        self.sgrid = QtWidgets.QGridLayout(self.sbox)
        self.sgrid.setContentsMargins(0, 0, 0, 0)
        self.sgrid.setHorizontalSpacing(4)
        self.sgrid.setVerticalSpacing(4)
        srow = QtWidgets.QHBoxLayout()
        srow.addWidget(self.sbox)
        srow.addStretch(1)
        tl.addLayout(srow)
        #: 每台 VNA 偵測到的 port 資訊 {名稱: {ports, sparams, traces, source}}
        self.port_info: Dict[str, Dict[str, Any]] = {}
        self._detecting: set = set()
        self._want_sparam = saved.get("sparam") or "S21"
        self._build_sbuttons(SPARAMS, {})
        # 1.0.12：常用設定（快速切換）——點一下套用；右鍵 重新命名 / 用目前設定覆蓋 / 刪除
        prow = QtWidgets.QHBoxLayout()
        prow.setSpacing(4)
        plab = QtWidgets.QLabel("常用")
        plab.setProperty("role", "muted")
        prow.addWidget(plab)
        from ..flow_layout import FlowLayout

        self.preset_box = QtWidgets.QWidget()
        self.preset_flow = FlowLayout(self.preset_box, spacing=4)
        prow.addWidget(self.preset_box, 1)
        self.b_save_preset = QtWidgets.QPushButton("＋ 存成常用")
        self.b_save_preset.setObjectName("vnaSavePreset")
        self.b_save_preset.setToolTip("把目前的設定（起訖頻率、點數、功率、IF、平均、掃描時間、S 參數）存成一個常用設定，"
                                      "之後點一下就切換過去")
        self.b_save_preset.clicked.connect(lambda: self.save_preset())
        prow.addWidget(self.b_save_preset, 0, Qt.AlignmentFlag.AlignTop)
        tl.addLayout(prow)
        self.preset_buttons: List[QtWidgets.QPushButton] = []
        self._avg_memory: Dict[str, int] = dict(saved.get("avg_count") or {})
        self._rebuild_presets()
        self.form_box = QtWidgets.QWidget()
        self.form = QtWidgets.QGridLayout(self.form_box)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setHorizontalSpacing(8)
        self.form.setVerticalSpacing(4)
        tl.addWidget(self.form_box)
        row = QtWidgets.QHBoxLayout()
        self.b_apply = QtWidgets.QPushButton("套用設定")
        self.b_apply.setProperty("primary", True)
        self.b_apply.clicked.connect(self.apply)
        self.b_readp = QtWidgets.QPushButton("讀取設定")
        self.b_readp.clicked.connect(self.read_params)
        hint = QtWidgets.QLabel("掃描速度：掃描時間 / 自動、點數、IF 頻寬")
        hint.setProperty("role", "muted")
        self.b_center = QtWidgets.QPushButton("⌖ 最低點置中")
        self.b_center.setObjectName("vnaCenterOnMin")
        self.b_center.setToolTip("把目前曲線的最低點（|S| 最小）的頻率設成中心頻率，頻寬不變（1.0.2）")
        self.b_center.clicked.connect(self.center_on_minimum)
        row.addWidget(self.b_apply)
        row.addWidget(self.b_readp)
        row.addWidget(self.b_center)
        row.addWidget(hint, 1)
        tl.addLayout(row)
        tl.addStretch(1)
        self.top_scroll = QtWidgets.QScrollArea()
        self.top_scroll.setWidgetResizable(True)
        self.top_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.top_scroll.setWidget(top)
        self.split.addWidget(self.top_scroll)

        # ---- 下半部：曲線 ----
        bot = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(bot)
        bl.setContentsMargins(0, 4, 0, 0)
        bl.setSpacing(4)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        self.b_read = QtWidgets.QPushButton("▶ 量測")
        self.b_read.setToolTip("量一次曲線")
        self.b_read.clicked.connect(self.read_trace)
        self.cont = QtWidgets.QCheckBox("循環")
        self.cont.setToolTip("一直重複量測（間隔見右邊）")
        self.cont.toggled.connect(self._cont_toggled)
        self.interval = QtWidgets.QDoubleSpinBox()
        self.interval.setRange(0.0, 600.0)
        self.interval.setDecimals(1)
        self.interval.setSingleStep(0.5)
        self.interval.setSuffix(" s")
        self.interval.setSpecialValueText("最短")
        self.interval.setToolTip("刷新間隔。「最短」= 讀完一次立刻讀下一次（刷新間隔 = 實際量測時間）")
        self.interval.setValue(float(saved.get("interval", setting("live.vna_interval_s", 0.0)) or 0.0))
        self.interval.valueChanged.connect(lambda val: self.ui.save(interval=val))
        self.interval.setFixedWidth(88)
        self.rate = QtWidgets.QLabel("")
        self.rate.setProperty("role", "muted")
        row.addWidget(self.b_read)
        row.addWidget(self.cont)
        self.interval_label = QtWidgets.QLabel("間隔")
        self.interval_label.setProperty("role", "muted")
        row.addWidget(self.interval_label)
        row.addWidget(self.interval)
        row.addWidget(self.rate, 1)
        self.b_plotwin = QtWidgets.QPushButton("⧉ 曲線視窗")
        self.b_plotwin.setToolTip("把曲線拿出來單獨一個視窗")
        self.b_plotwin.clicked.connect(self.popout_plots)
        row.addWidget(self.b_plotwin)
        bl.addLayout(row)
        # 1.0.10：trace 像 VNA 面板——每條 trace = S 參數 ＋ 格式，好幾條可以疊在同一張圖或分開
        sp0 = saved.get("sparam") or "S21"
        tg = saved.get("tgroups")
        if isinstance(tg, list) and tg:
            groups = normalize_groups([g for g in tg if isinstance(g, list)])
        else:                                        # 舊設定：只有格式（views / groups / mode）→ 加上 S 參數
            old = saved.get("groups")
            views = [k for k in (saved.get("views") or ["db"]) if k in FORMATS] or ["db"]
            if isinstance(old, list) and old:
                groups = normalize_groups(migrate(old, sp0))
            else:
                groups = [[tkey(sp0, k) for k in views]] if saved.get("mode") == "overlay" else \
                    [[tkey(sp0, k)] for k in views]
                groups = normalize_groups(groups)
        self.groups: List[List[str]] = groups or [[tkey(sp0, "db")]]
        self.order: List[str] = [k for g in self.groups for k in g]
        self.plots = TracePlots()
        self.view_bar = FormatBar(self.plots, lambda: self.trace())
        self.vbtn = self.view_bar.buttons
        row = self.view_bar.layout()
        row.addSpacing(8)
        self.points = QtWidgets.QCheckBox("數據點")
        self.points.setToolTip("在曲線上加畫每個量測點")
        self.points.setChecked(bool(saved.get("points", False)))
        self.points.toggled.connect(self._points_changed)
        row.addWidget(self.points)
        row.addStretch(1)
        bl.addWidget(self.view_bar)
        self.tool_bar = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(self.tool_bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.overlay = QtWidgets.QCheckBox("全部疊圖")
        self.overlay.setToolTip("全部的 trace 畫在同一張圖：第一種單位用左軸，另一種單位用右軸（例如 dB 左、Phase 右）；\n"
                                "疊不下的（第三種單位、Polar / Smith）另開一張。\n"
                                "只想疊其中幾條：拖 trace 名稱到另一張圖的中間；拖到邊緣 = 自己一張放旁邊")
        self.overlay.setChecked(len(self.groups) == 1 and len(self.order) > 1)
        self.overlay.toggled.connect(self._mode_changed)
        row.addWidget(self.overlay)
        self.layout_box = QtWidgets.QComboBox()
        self.layout_box.setObjectName("plotLayout")
        for key, text in (("auto", "自動排列"), ("rows", "上下排列"), ("cols", "左右並排")):
            self.layout_box.addItem(text, key)
        self.layout_box.setToolTip("圖的排法：自動（2 張上下、3 張上左右＋下、4 張四方格…）、全部上下、全部左右")
        self.layout_box.setCurrentIndex(max(0, self.layout_box.findData(saved.get("layout") or "auto")))
        self.layout_box.currentIndexChanged.connect(self._layout_changed)
        row.addWidget(self.layout_box)
        for text, tip, fn in (("自動範圍", "所有軸回到自動範圍", lambda: self.plots.auto_range()),
                              ("重設偏移", "拖曳曲線造成的上下偏移歸零", lambda: self.plots.reset_offsets()),
                              ("清除 Marker", "刪除所有 marker（單一 marker：在 marker 上按右鍵）",
                               lambda: self.plots.clear_markers())):
            b = QtWidgets.QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        hint = QtWidgets.QLabel("點 trace 名稱 = 選它 · 拖 trace 名稱或 ⠿：放中間 = 疊圖、放邊緣 = 新的圖 · 右鍵 = 換 S 參數 / 格式 · "
                                "雙擊 = marker · 拖曲線 = 上下移動")
        hint.setProperty("role", "muted")
        row.addWidget(hint, 1)
        bl.addWidget(self.tool_bar)
        # 0.0.30：x / y 軸各自運算
        self.math_bar = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(self.math_bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        from ....core.axis_math import FUNCS

        tip = ("對曲線的軸做運算（只影響畫面，不改數據）：例如 y = y**2、y = y - mean(y)、x = log(x)、x = x/1e9。\n"
               "可以用 x、y、數字、+ - * / ** 與函式：" + ", ".join(sorted(FUNCS)) + "\n按 Enter 套用；清空 = 不運算")
        lab = QtWidgets.QLabel("軸運算")
        lab.setToolTip(tip)
        row.addWidget(lab)
        self.x_expr = QtWidgets.QLineEdit(str(saved.get("x_expr") or ""))
        self.y_expr = QtWidgets.QLineEdit(str(saved.get("y_expr") or ""))
        for var, e in (("x", self.x_expr), ("y", self.y_expr)):
            e.setObjectName(f"{var}Expr")
            e.setPlaceholderText(f"{var} = {var}")
            e.setToolTip(tip)
            e.setClearButtonEnabled(True)
            e.setMinimumWidth(110)
            e.editingFinished.connect(self._math_changed)
            row.addWidget(QtWidgets.QLabel(f"{var} ="))
            row.addWidget(e, 1)
        self.math_err = QtWidgets.QLabel("")
        self.math_err.setStyleSheet(f"color:{theme.c('err')};")
        self.math_err.setWordWrap(True)
        row.addWidget(self.math_err, 1)
        bl.addWidget(self.math_bar)
        self.plots.set_points(self.points.isChecked())
        self.plots.set_layout_mode(self.layout_box.currentData())
        self.plots.set_sources(SPARAMS)
        self.plots.set_groups(self.groups)
        self.compact = False
        self.plots.order_changed.connect(self._order_changed)
        self.plots.groups_changed.connect(self._groups_changed)
        self.plots.active_changed.connect(self._active_changed)
        self.plots.message.connect(self.message.emit)
        try:
            self.plots.set_math(self.x_expr.text(), self.y_expr.text())
        except Exception as e:  # noqa: BLE001
            self.math_err.setText(f"⚠ {e}")
        self.plot_holder = QtWidgets.QStackedWidget()
        self.plot_holder.addWidget(self.plots)
        away = QtWidgets.QWidget()
        al = QtWidgets.QVBoxLayout(away)
        lab = QtWidgets.QLabel("曲線在獨立視窗中")
        lab.setProperty("role", "muted")
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        back = QtWidgets.QPushButton("放回這裡")
        back.clicked.connect(lambda: self.plot_window.close() if self.plot_window else None)
        al.addStretch(1)
        al.addWidget(lab)
        al.addWidget(back, 0, Qt.AlignmentFlag.AlignCenter)
        al.addStretch(1)
        self.plot_holder.addWidget(away)
        bl.addWidget(self.plot_holder, 1)
        self.split.addWidget(bot)
        self.split.setCollapsible(1, False)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([200, 500])

        self.empty = QtWidgets.QLabel("沒有已連線的 VNA")
        self.empty.setProperty("role", "muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.empty)
        self._show(False)

    def _show(self, has: bool) -> None:
        self.split.setVisible(has)
        self.empty.setVisible(not has)

    # ---- 曲線種類 / 視窗 -------------------------------------------------------------
    def selected_views(self) -> List[str]:
        """目前的 trace（依排列順序）。"""
        return list(self.order)

    def set_views(self, views: List[str]) -> None:
        """指定 trace（"S21:db"；只寫格式 = 目前的 S 參數）：每條一張（「全部疊圖」勾著 → 一張）。"""
        sp = self.trace()
        views = [k if ":" in k else tkey(sp, k) for k in dict.fromkeys(views)]
        views = [k for k in views if valid_key(k)][:MAX_TRACES] or [tkey(sp, "db")]
        self.groups = normalize_groups([views] if self.overlay.isChecked() else [[k] for k in views])
        self.order = [k for g in self.groups for k in g]
        if not getattr(self, "compact", False):
            self.plots.set_groups(self.groups, emit=True)
        self._save_groups()

    def _save_groups(self) -> None:
        self.ui.save(tgroups=self.groups, sparam=self.trace())

    def _mode_changed(self, on: bool) -> None:
        self.groups = normalize_groups([list(self.order)] if on else [[k] for k in self.order])
        self.order = [k for g in self.groups for k in g]
        if not getattr(self, "compact", False):
            self.plots.set_groups(self.groups)
        self._save_groups()
        if on and len(self.groups) > 1:
            self.message.emit("全部疊圖只畫兩種單位（左右兩軸），其他的另開一張；Polar / Smith 只跟 Polar / Smith 疊")

    def _layout_changed(self, _i: int) -> None:
        mode = self.layout_box.currentData()
        self.plots.set_layout_mode(mode)
        self.ui.save(layout=mode)

    def _groups_changed(self, groups: List[List[str]]) -> None:
        """拖曳 / 新增 / 刪除 / 改 trace 之後：記住分組，「全部疊圖」跟著目前狀態；新的 S 參數 → 重新讀一次。"""
        if getattr(self, "compact", False):
            return
        need = set(self.plots.sources_used()) - set(self._have_sources())
        self.groups = [list(g) for g in groups]
        self.order = [k for g in self.groups for k in g]
        self.overlay.blockSignals(True)
        self.overlay.setChecked(len(self.groups) == 1 and len(self.order) > 1)
        self.overlay.blockSignals(False)
        self._save_groups()
        if need and not self.cont.isChecked():
            self.read_trace()

    def _have_sources(self) -> List[str]:
        data = self.last[2] if self.last else None
        return list(data) if isinstance(data, dict) else []

    def _active_changed(self, k: str) -> None:
        """目前的 trace 換了：S 參數按鈕跟著它。"""
        sp = key_src(k) if k else ""
        b = self.sbtn.get(sp)
        if b is not None and not b.isChecked():
            self._quiet_sbtn = True
            b.setChecked(True)
            self._quiet_sbtn = False
        if sp:
            self._want_sparam = sp

    def set_compact(self, on: bool) -> None:
        """嵌在主視窗時只顯示目前 S 參數的 dB 曲線與「量測 / 循環」；其餘在獨立視窗。"""
        self.compact = bool(on)
        self.top_scroll.setVisible(not on)
        for w in (self.view_bar, self.tool_bar, self.math_bar, self.b_plotwin, self.interval, self.interval_label):
            w.setVisible(not on)
        if on:
            if self.plot_window is not None:
                self.plot_window.close()
            self.plots.set_groups([[tkey(self.trace(), "db")]])
        else:
            self.plots.set_groups(self.groups)

    def _math_changed(self) -> None:
        x, y = self.x_expr.text().strip(), self.y_expr.text().strip()
        if (x, y) == getattr(self, "_math_last", None):
            return
        try:
            self.plots.set_math(x, y)
        except Exception as e:  # noqa: BLE001
            self.math_err.setText(f"⚠ {e}")
            return
        self._math_last = (x, y)
        err = getattr(self.plots.math, "error", "")
        self.math_err.setText(f"⚠ {err}" if err else "")
        self.ui.save(x_expr=x, y_expr=y)
        if self.plots.math.active:
            self.message.emit(f"軸運算：{self.plots.math_text()}")

    def _order_changed(self, order: List[str]) -> None:
        self.order = list(order)

    def _points_changed(self, on: bool) -> None:
        self.plots.set_points(on)
        self.ui.save(points=bool(on))

    def _sparam_changed(self, *_a) -> None:
        """S 參數按鈕：改目前的 trace 量的 S 參數（像 VNA 的 Meas 鍵）。"""
        sp = self.trace()
        self._want_sparam = sp
        k = self.plots.active
        if getattr(self, "compact", False):
            self.plots.set_groups([[tkey(sp, "db")]])
        elif k and key_src(k) != sp:
            self.plots.retype(k, src=sp)
        self.ui.save(sparam=sp)
        if not self.cont.isChecked():
            self.read_trace()

    # ---- S 參數按鈕（依 port 數）-------------------------------------------------------
    def _build_sbuttons(self, params: List[str], traces: Dict[str, List[str]], visible: Optional[List[str]] = None,
                        levels: Optional[Dict[str, float]] = None) -> None:
        """重建 S 參數按鈕：n port → n×n 矩陣（第 i 列 = Si1…Sin）；ZNA 上已有 trace 的用粗體。"""
        from ....core.sparams import ports_of

        for b in list(self.sbtn.values()):
            self.sgroup.removeButton(b)
            b.setParent(None)
            b.deleteLater()
        self.sbtn.clear()
        have = {k.upper(): v for k, v in (traces or {}).items()}
        levels = dict(levels or {})
        show = set(visible) if visible else set(params)
        for k, sp in enumerate(params):
            if sp not in show:                       # 1.0.10：沒接線的 port 組合不顯示（儀器伺服器可以打開）
                continue
            b = QtWidgets.QPushButton(sp)
            b.setCheckable(True)
            b.setFixedWidth(50)
            lv = f"，掃描時 {levels[sp]:.1f} dB" if sp in levels else ""
            if sp.upper() in have:
                f = b.font()
                f.setBold(True)
                b.setFont(f)
                b.setToolTip(f"目前的 trace 量 {sp}（ZNA 上已有：{', '.join(have[sp.upper()])}{lv}）")
            else:
                b.setToolTip(f"目前的 trace 量 {sp}（ZNA 上沒有這條 trace，量測時自動建立 LC_{sp}{lv}）")
            pp = ports_of(sp)
            r, c = (pp[0] - 1, pp[1] - 1) if pp else (0, k)
            self.sgroup.addButton(b)
            self.sbtn[sp] = b
            self.sgrid.addWidget(b, r, c)
            b.setEnabled(not self.busy)
        k = self.plots.active if hasattr(self, "plots") else ""
        cur = key_src(k) if k else self._want_sparam
        want = cur if cur in self.sbtn else (self._want_sparam if self._want_sparam in self.sbtn else
                                             ("S21" if "S21" in self.sbtn else next(iter(self.sbtn), None)))
        if want:
            self._quiet_sbtn = True
            self.sbtn[want].setChecked(True)
            self._quiet_sbtn = False
            if hasattr(self, "plots") and not getattr(self, "compact", False) and self.port_info.get(self.name or ""):
                for k in list(self.plots.views):                 # 原本的 S 參數不見了（port 變少 / 沒接線）
                    if key_src(k) not in self.sbtn:
                        self.plots.retype(k, src=want)
        if hasattr(self, "plots"):
            self.plots.set_sources([p for p in params if p in self.sbtn])

    def _apply_ports(self, name: Optional[str]) -> None:
        info = self.port_info.get(name or "")
        if info:
            params = list(info.get("sparams") or SPARAMS)
            vis = [s for s in (info.get("visible") or params) if s in params] or params
            self._build_sbuttons(params, info.get("traces") or {}, vis, info.get("levels") or {})
            hidden = len(params) - len(vis)
            txt = f"{info.get('ports')} port"
            if hidden:
                txt += f" · 顯示有接線的 {len(vis)} 個" + ("（伺服器手動選的）" if info.get("manual") else "")
            self.ports_label.setText(txt)
            lv = info.get("levels") or {}
            self.ports_label.setToolTip(f"來源：{info.get('source', '')}"
                                        + ("\n掃描（|S| 中位數）：" + "　".join(f"{k} {v:.1f} dB" for k, v in lv.items())
                                           if lv else "")
                                        + ("\n其他的在「儀器伺服器 → 右鍵 VNA → S 參數顯示…」打開" if hidden else ""))
        else:
            self._build_sbuttons(SPARAMS, {})
            self.ports_label.setText("")

    def detect_ports(self, quiet: bool = False) -> None:
        """「偵測 port」：問 VNA 的 port 數與現有 trace，重建 S 參數按鈕。"""
        be, name, gen = self.backend, self.name, self._gen
        if be is None or not name or name in self._detecting:
            return
        if not hasattr(be, "vna_ports"):
            return
        self._detecting.add(name)
        self.b_ports.setEnabled(False)
        self.ports_label.setText("偵測中…")

        def done():
            self._detecting.discard(name)
            self.b_ports.setEnabled(not self.busy)

        def ok(info):
            done()
            if gen != self._gen:
                return
            self.port_info[name] = dict(info or {})
            if name == self.name:
                self._apply_ports(name)
            if not quiet:
                vis = (info or {}).get("visible") or []
                self.message.emit(f"{name}：{info.get('ports')} port（{info.get('source', '')}），"
                                  f"有接線 / 顯示 {len(vis) or len(info.get('sparams') or [])} 個 S 參數："
                                  + ", ".join(vis))

        def fail(m):
            done()
            if name == self.name:
                self._apply_ports(name)
            self.message.emit(f"❌ 偵測 {name} 的 port 失敗：{m}")
        scan = (not quiet) and hasattr(be, "vna_scan")          # 按鈕：用目前設定重新掃描哪些 port 有接線
        run_bg(lambda: be.vna_scan(name) if scan else be.vna_ports(name), ok, fail)

    def _on_ports(self, p: Dict[str, Any]) -> None:
        name, info = p.get("name"), p.get("info")
        if not name or not isinstance(info, dict):
            return
        self.port_info[name] = dict(info)
        if name == self.name:
            self._apply_ports(name)

    def popout_plots(self) -> None:
        if self.plot_window is not None:
            self.plot_window.raise_()
            return
        self.plot_holder.removeWidget(self.plots)
        self.plot_holder.setCurrentIndex(0)
        w = PlotWindow(self.plots, f"VNA 曲線 — {self.vna.currentText() or 'VNA'}", self)
        w.closed.connect(self._plots_back)
        self.plot_window = w
        self.b_plotwin.setEnabled(False)
        w.show()

    def _plots_back(self) -> None:
        self.plot_window = None
        self.plot_holder.insertWidget(0, self.plots)
        self.plot_holder.setCurrentWidget(self.plots)
        self.plots.show()
        self.b_plotwin.setEnabled(True)

    # ---- 資料 ---------------------------------------------------------------------
    def set_backend(self, backend) -> None:
        self.backend = backend
        self._gen += 1
        st = getattr(backend, "station", None)
        if st is not None and getattr(self, "_ports_bus", None) is not st.bus:
            from ..bridge import QtEventBridge

            self._ports_bus = st.bus                  # 1.0.10：連線時的掃描 / 伺服器改顯示 → 按鈕跟著變
            self._ports_bridge = QtEventBridge(st.bus, "vna.ports", parent=self)
            self._ports_bridge.on("vna.ports", self._on_ports)
        self.params, self.last = {}, None
        self.port_info.clear()
        self._detecting.clear()
        self.cont.setChecked(False)
        self.plots.clear()
        self.reload()

    def reload(self) -> None:
        be, gen = self.backend, self._gen
        if be is None:
            return

        def ok(vnas):
            if gen != self._gen:
                return
            cur = self.vna.currentData()
            self.vna.blockSignals(True)
            self.vna.clear()
            for x in vnas or []:
                self.vna.addItem(x.get("label") or x["name"], x["name"])
                if x.get("ports"):
                    self.port_info[x["name"]] = dict(x["ports"])
            i = self.vna.findData(cur)
            self.vna.setCurrentIndex(max(i, 0))
            self.vna.blockSignals(False)
            self._show(bool(vnas))
            if not vnas:
                self.cont.setChecked(False)
            if vnas and (cur != self.vna.currentData() or not self.params):
                self.load_params()
        run_bg(be.vnas, ok, lambda m: self.message.emit(f"讀取 VNA 失敗：{m}"))

    @property
    def name(self) -> Optional[str]:
        return self.vna.currentData()

    def load_params(self) -> None:
        be, name, gen = self.backend, self.name, self._gen
        if be is None or not name:
            return
        self._apply_ports(name)
        if name not in self.port_info:            # 自動偵測（每台 VNA 一次；之後按「偵測 port」重新偵測）
            self.detect_ports(quiet=True)

        def ok(d):
            if gen != self._gen:
                return
            allp = {p["name"]: p for g in d.get("groups") or [] for p in g["params"]}
            self.params = {k: allp[k] for k in PREFERRED if k in allp}
            self.rf_param = allp.get("output")
            self._build_form()
            self.read_params()
        run_bg(lambda: be.describe(name), ok, lambda m: self.message.emit(f"讀取 {name} 參數失敗：{m}"))

    def _build_form(self) -> None:
        if not hasattr(self, "_wheel_apply"):
            self._wheel_apply = QtCore.QTimer(self)
            self._wheel_apply.setSingleShot(True)
            self._wheel_apply.timeout.connect(lambda: self.dirty and not self.busy and self.apply())
        for w in list(self.edits.values()) + list(self.labels.values()):
            w.setParent(None)
            w.deleteLater()
        self.edits.clear()
        self.labels.clear()
        self.dirty.clear()
        for k, p in self.params.items():
            du = p.get("display_unit") or p.get("unit") or ""
            lab = QtWidgets.QLabel(f"{p.get('label') or k}" + (f"（{du}）" if du and p.get("kind") != "bool" else ""))
            lab.setProperty("role", "muted")
            lab.setToolTip(p["ref"] + (f"\n{p['doc']}" if p.get("doc") else ""))
            if k == "averages" and p.get("settable"):
                e = AvgEdit(self._avg_memory.get(self.name or "", DEFAULT_AVG))
                e.changed.connect(lambda k=k: self._mark(k))
                e.stepped.connect(lambda k=k: (self._mark(k), self._wheel_apply.start(700)))
                e.count.returnPressed.connect(self.apply)
                lab.setText("平均")
                lab.setToolTip(lab.toolTip() + "\n勾選 = 開啟平均並用右邊的次數；不勾 = 關閉（次數保留）")
            elif p.get("kind") == "bool" and p.get("settable"):
                e: QtWidgets.QWidget = QtWidgets.QCheckBox()
                e.clicked.connect(lambda _c, k=k: self._mark(k))
            elif p.get("settable"):
                from ..digit_edit import WheelLineEdit

                e = WheelLineEdit()                  # 0.0.28：滾輪 / ↑↓ 調整游標那一位，停手 0.7 秒後寫入 VNA
                e.textEdited.connect(lambda _t, k=k: self._mark(k))
                e.stepped.connect(lambda k=k: (self._mark(k), self._wheel_apply.start(700)))
                e.returnPressed.connect(self.apply)
                e.setMinimumWidth(70)
            else:
                e = QtWidgets.QLabel("—")
            e.setToolTip(lab.toolTip())
            e.setEnabled(not self.busy or isinstance(e, QtWidgets.QLabel))
            self.edits[k], self.labels[k] = e, lab
        self._cols = 0
        self._layout_form()

    def _layout_form(self) -> None:
        cols = 3 if self.width() >= 760 else (2 if self.width() >= 420 else 1)
        if cols == self._cols or not self.edits:
            return
        self._cols = cols
        while self.form.count():
            self.form.takeAt(0)
        for i, k in enumerate(self.edits):
            r, c = divmod(i, cols)
            self.form.addWidget(self.labels[k], r, c * 2)
            self.form.addWidget(self.edits[k], r, c * 2 + 1)
        for c in range(6):
            self.form.setColumnStretch(c, 1 if c % 2 and c < cols * 2 else 0)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._layout_form()

    def _mark(self, k: str) -> None:
        self.dirty.add(k)
        self.edits[k].setStyleSheet(f"border:1px solid {theme.c('warn')}; border-radius:6px;")

    def read_params(self) -> None:
        be, gen = self.backend, self._gen
        refs = [p["ref"] for p in self.params.values()]
        rf = self.rf_param
        if rf:
            refs.append(rf["ref"])
        if be is None or not refs:
            return

        def ok(vals):
            if gen != self._gen:
                return
            for k, p in self.params.items():
                v, err = vals.get(p["ref"], (None, ""))
                e = self.edits.get(k)
                if e is None or k in self.dirty:
                    continue
                if isinstance(e, AvgEdit):
                    if not err:
                        e.set_value(v, self._avg_memory.get(self.name or ""))
                        self._remember_avg(e)
                elif isinstance(e, QtWidgets.QCheckBox):
                    e.setChecked(bool(v))
                else:
                    e.setText("錯誤" if err else _fmt(v, p))
                if err:
                    e.setToolTip(err)
            if rf:
                v, err = vals.get(rf["ref"], (None, ""))
                self.rf.setChecked(bool(v))
        run_bg(lambda: be.get(refs), ok, lambda m: self.message.emit(f"讀取設定失敗：{m}"))

    def pending(self) -> List[tuple]:
        """要寫入的 (ref, 值)。改了掃描時間 → 同時關掉自動掃描時間。"""
        todo = []
        for k in self.params:
            if k not in self.dirty:
                continue
            p, e = self.params[k], self.edits[k]
            if isinstance(e, AvgEdit):
                todo.append((p["ref"], e.value()))
                self._remember_avg(e)
                continue
            if isinstance(e, QtWidgets.QCheckBox):
                todo.append((p["ref"], bool(e.isChecked())))
                continue
            text = e.text().strip()
            du = p.get("display_unit") or p.get("unit") or ""
            val = parse_quantity(text, du) if p.get("kind") in ("float", "int") else text
            if p.get("kind") == "int":
                val = int(round(val))
            todo.append((p["ref"], val))
        if "sweep_time" in self.dirty and "sweep_time_auto" in self.params and "sweep_time_auto" not in self.dirty:
            todo.append((self.params["sweep_time_auto"]["ref"], False))
        return todo

    def apply(self) -> None:
        be = self.backend
        if be is None or not self.dirty or self.busy:
            return
        try:
            todo = self.pending()
        except Exception as e:  # noqa: BLE001
            self.message.emit(f"無法解析：{e}")
            return

        def work():
            return [(r, be.set(r, v)) for r, v in todo]

        def ok(_res):
            for k in list(self.dirty):
                self.edits[k].setStyleSheet("")
            self.dirty.clear()
            self.message.emit(f"✔ 已套用 {len(todo)} 項 VNA 設定")
            self.read_params()
            if not self.cont.isChecked():
                self.read_trace()
        run_bg(work, ok, lambda m: self.message.emit(f"❌ 套用失敗：{m}"))

    # ---- 1.0.12：平均次數記憶、常用設定 -----------------------------------------------------
    def _remember_avg(self, e: "AvgEdit") -> None:
        name = self.name or ""
        n = e.remembered()
        if self._avg_memory.get(name) != n:
            self._avg_memory[name] = n
            self.ui.save(avg_count=dict(self._avg_memory))

    def presets(self) -> List[Dict[str, Any]]:
        return [p for p in (self.ui.load().get("vna_presets") or []) if isinstance(p, dict) and p.get("name")]

    def _save_presets(self, presets: List[Dict[str, Any]]) -> None:
        self.ui.save(vna_presets=presets[:MAX_PRESETS])
        self._rebuild_presets()

    def current_settings(self) -> Dict[str, Any]:
        """表單上目前的值（含還沒套用的修改）：{key: 文字 / bool / [平均開, 次數]}，加上 S 參數。"""
        values: Dict[str, Any] = {}
        for k in PRESET_KEYS:
            e = self.edits.get(k)
            if e is None or not self.params.get(k, {}).get("settable"):
                continue
            if isinstance(e, AvgEdit):
                values[k] = [e.check.isChecked(), e.remembered()]
            elif isinstance(e, QtWidgets.QCheckBox):
                values[k] = bool(e.isChecked())
            elif isinstance(e, QtWidgets.QLineEdit) and e.text().strip() and e.text() != "錯誤":
                values[k] = e.text().strip()
        if values.get("sweep_time_auto") is True:
            values.pop("sweep_time", None)
        return {"values": values, "sparam": self._want_sparam}

    def save_preset(self, name: Optional[str] = None, index: Optional[int] = None) -> Optional[Dict[str, Any]]:
        cur = self.current_settings()
        if not cur["values"]:
            self.message.emit("還沒有讀到 VNA 的設定：先連線並選 VNA")
            return None
        presets = self.presets()
        if index is None and len(presets) >= MAX_PRESETS:
            self.message.emit(f"常用設定最多 {MAX_PRESETS} 個：先刪掉一個（右鍵）")
            return None
        if name is None and index is None:
            v = cur["values"]
            guess = "—".join(x for x in (v.get("start_freq"), v.get("stop_freq")) if x) or f"設定 {len(presets) + 1}"
            name, ok = QtWidgets.QInputDialog.getText(self, "存成常用設定", "名稱：", text=guess)
            if not ok or not str(name).strip():
                return None
        item = {"name": str(name if name is not None else presets[index]["name"]).strip(), **cur}
        if index is None:
            presets.append(item)
        else:
            presets[index] = item
        self._save_presets(presets)
        self.message.emit(f"✔ 已存常用設定「{item['name']}」")
        return item

    def load_preset(self, index: int, apply: bool = True) -> bool:
        """把常用設定填進表單（標成待寫入）並套用到 VNA。"""
        presets = self.presets()
        if not 0 <= index < len(presets) or self.busy:
            return False
        pr = presets[index]
        for k, v in (pr.get("values") or {}).items():
            e = self.edits.get(k)
            if e is None:
                continue
            if isinstance(e, AvgEdit):
                on, n = (v if isinstance(v, (list, tuple)) and len(v) == 2 else (bool(v), v))
                e.set_value(int(n) if on else 0, int(n) if n else None)
            elif isinstance(e, QtWidgets.QCheckBox):
                e.setChecked(bool(v))
            elif isinstance(e, QtWidgets.QLineEdit):
                e.setText(str(v))
            else:
                continue
            self._mark(k)
        sp = pr.get("sparam")
        if sp and sp in self.sbtn and not self.sbtn[sp].isChecked():
            self.sbtn[sp].setChecked(True)
        for i, b in enumerate(self.preset_buttons):
            b.setChecked(i == index)
        self.message.emit(f"切換到常用設定「{pr['name']}」")
        if apply:
            self.apply()
        return True

    def _rebuild_presets(self) -> None:
        for b in self.preset_buttons:
            self.preset_flow.removeWidget(b)
            b.setParent(None)
            b.deleteLater()
        self.preset_buttons = []
        for i, pr in enumerate(self.presets()):
            b = QtWidgets.QPushButton(pr["name"])
            b.setCheckable(True)
            b.setObjectName("vnaPreset")
            v = pr.get("values") or {}
            b.setToolTip("點一下套用：" + "、".join(f"{self.params.get(k, {}).get('label') or k} {x if not isinstance(x, list) else ('開 ' + str(x[1]) if x[0] else '關')}"
                                                  for k, x in v.items()) + (f"、{pr['sparam']}" if pr.get("sparam") else "")
                         + "\n右鍵：重新命名 / 用目前設定覆蓋 / 刪除")
            b.clicked.connect(lambda _c, i=i: self.load_preset(i))
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, i=i, b=b: self._preset_menu(i, b.mapToGlobal(pos)))
            b.setEnabled(not self.busy)
            self.preset_flow.addWidget(b)
            self.preset_buttons.append(b)
        self.preset_box.updateGeometry()

    def _preset_menu(self, index: int, where) -> None:
        presets = self.presets()
        if not 0 <= index < len(presets):
            return
        m = QtWidgets.QMenu(self)
        a_ren = m.addAction("重新命名…")
        a_over = m.addAction("用目前的設定覆蓋")
        m.addSeparator()
        a_del = m.addAction("刪除")
        a_left = m.addAction("往前移") if index > 0 else None
        act = m.exec(where)
        if act is a_ren:
            name, ok = QtWidgets.QInputDialog.getText(self, "重新命名", "名稱：", text=presets[index]["name"])
            if ok and str(name).strip():
                presets[index]["name"] = str(name).strip()
                self._save_presets(presets)
        elif act is a_over:
            self.save_preset(index=index)
        elif act is a_del:
            self.delete_preset(index)
        elif a_left is not None and act is a_left:
            presets.insert(index - 1, presets.pop(index))
            self._save_presets(presets)

    def delete_preset(self, index: int) -> None:
        presets = self.presets()
        if 0 <= index < len(presets):
            gone = presets.pop(index)
            self._save_presets(presets)
            self.message.emit(f"已刪除常用設定「{gone['name']}」")

    def minimum_frequency(self) -> Optional[float]:
        """目前曲線 |S| 最小處的頻率（Hz）；沒有曲線 → None。"""
        if not self.last:
            return None
        x, data = np.asarray(self.last[1], dtype=float), self.last[2]
        if isinstance(data, dict):
            k = getattr(getattr(self, "plots", None), "active", "") or ""
            z = data.get(key_src(k)) if k else None
            z = np.asarray(z if z is not None else next(iter(data.values()), []))
        else:
            z = np.asarray(data)
        mag = np.abs(z).astype(float)
        ok = np.isfinite(mag) & np.isfinite(x)
        if x.size != mag.size or not ok.any():
            return None
        k = int(np.argmin(np.where(ok, mag, np.inf)))
        return float(x[k])

    def center_on_minimum(self) -> None:
        """1.0.2：最低點的頻率 → 中心頻率（頻寬不變），寫入 VNA 後重新量一次。"""
        be = self.backend
        f0 = self.minimum_frequency()
        if be is None or f0 is None or self.busy:
            self.message.emit("先量一條曲線，才能把最低點置中")
            return
        x = np.asarray(self.last[1], dtype=float)
        span = float(abs(x[-1] - x[0])) if x.size > 1 else 0.0
        refs = {k: p["ref"] for k, p in self.params.items()}
        if "center_freq" in refs:
            todo = [(refs["center_freq"], f0)] + ([(refs["span"], span)] if "span" in refs and span else [])
        elif "start_freq" in refs and "stop_freq" in refs and span:
            lo, hi = f0 - span / 2, f0 + span / 2
            up = lo > float(x[0])
            todo = [(refs["stop_freq"], hi), (refs["start_freq"], lo)] if up else \
                [(refs["start_freq"], lo), (refs["stop_freq"], hi)]
        else:
            self.message.emit("這台 VNA 沒有可以設定的中心頻率 / 起訖頻率")
            return

        def ok(_res):
            self.message.emit(f"✔ 中心頻率 → {f0 / 1e9:.6f} GHz（最低點）")
            self.read_params()
            if not self.cont.isChecked():
                self.read_trace()
        run_bg(lambda: [be.set(r, v) for r, v in todo], ok, lambda m: self.message.emit(f"❌ 置中失敗：{m}"))

    def set_rf(self, on: bool) -> None:
        rf = self.rf_param
        if self.backend is None or rf is None:
            return
        be = self.backend
        run_bg(lambda: be.set(rf["ref"], bool(on)), lambda _r: self.message.emit(f"RF 輸出 {'ON' if on else 'OFF'}"),
               lambda m: (self.message.emit(f"❌ RF 切換失敗：{m}"), self.rf.setChecked(not on)))

    def trace(self) -> str:
        b = self.sgroup.checkedButton()
        return b.text() if b else "S21"

    # ---- 讀曲線 / 連續 -----------------------------------------------------------------
    def _cont_toggled(self, on: bool) -> None:
        self._cont_gen += 1
        if on:
            self.read_trace(cont=self._cont_gen)
        else:
            self.rate.setText(self.rate.text().split("　")[0])

    def read_trace(self, cont: Optional[int] = None) -> None:
        be, name, gen = self.backend, self.name, self._gen
        if self._reading and cont is not None:
            QtCore.QTimer.singleShot(50, lambda c=cont: self._next(c))     # 上一次還在讀：等它讀完再接著連續
            return
        if self._reading and cont is None:
            self._pending_read = True                 # 正在讀：讀完再讀一次（例如剛加了另一個 S 參數的 trace）
            return
        if be is None or not name or self._reading or self.busy:
            return
        srcs = self.plots.sources_used() or [self.trace()]
        tr = ", ".join(srcs)
        self._reading = True
        self.b_read.setEnabled(False)
        t0 = time.monotonic()

        def done():
            self._reading = False
            self.b_read.setEnabled(not self.busy)
            if getattr(self, "_pending_read", False):
                self._pending_read = False
                if not self.cont.isChecked():
                    QtCore.QTimer.singleShot(0, self.read_trace)

        def ok(res):
            done()
            if gen != self._gen:
                return
            dt = time.monotonic() - t0
            x, data, unit = res
            self.last = (tr, x, data, unit)
            self.plots.update_data(tr, x, data, unit)
            txt = f"讀取 {dt:.2f} s"
            if cont is not None and cont == self._cont_gen and self.cont.isChecked():
                wait = max(0.0, float(self.interval.value()) - dt)
                period = dt + wait
                txt += f"　每 {period:.2f} s（{1 / period if period > 0 else 0:.1f} 次/s）"
                QtCore.QTimer.singleShot(int(wait * 1000), lambda c=cont: self._next(c))
            self.rate.setText(txt)

        def fail(m):
            done()
            self.cont.setChecked(False)
            self.message.emit(f"❌ 讀取 {name} {tr} 失敗：{m}")
        def work():
            if hasattr(be, "vna_traces"):
                return be.vna_traces(name, srcs, True)
            data, x, unit = {}, None, "Hz"
            for s in srcs:
                x, data[s], unit = be.vna_trace(name, s, True)
            return x, data, unit
        run_bg(work, ok, fail)

    def _next(self, cont: int) -> None:
        if cont == self._cont_gen and self.cont.isChecked() and not self.busy:
            self.read_trace(cont=cont)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        for w in (self.b_apply, self.b_read, self.rf, self.cont, self.b_ports, *self.sbtn.values(),
                  *self.edits.values(), *getattr(self, "preset_buttons", []), self.b_save_preset):
            w.setEnabled(not busy)
        if busy:
            self.cont.setChecked(False)

    def close_windows(self) -> None:
        if self.plot_window is not None:
            self.plot_window.close()
