"""即時監控：VNA。

上半部（可以拉小 / 收起）：選 VNA、RF、S11 / S12 / S21 / S22、參數（含掃描時間）。
下半部：曲線，可以複選 dB / Linear / Real / Imag / Phase / Unwrap phase / IQ（最多 5 張），九宮格排法：
2 張上下、3 張上左右＋下、4 張四方格、5 張四方格＋下；拖曳圖的標題列交換位置；可以疊加數據點；
「⧉ 曲線視窗」把圖拿出來單獨一個視窗。

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

SPARAMS = ["S11", "S12", "S21", "S22"]
#: 即時監控顯示的 VNA 參數（依序；驅動沒有的略過）
PREFERRED = ["start_freq", "stop_freq", "center_freq", "span", "points", "power", "if_bw", "averages",
             "sweep_time", "sweep_time_auto"]
#: 曲線種類：key → (按鈕文字, y 軸標題)
VIEWS = {"db": ("dB", "振幅 (dB)"), "lin": ("Linear", "|S| (linear)"), "re": ("Real", "實部"),
         "im": ("Imag", "虛部"), "phase": ("Phase", "相位 (°)"), "uphase": ("Unwrap", "相位 unwrap (°)"),
         "iq": ("IQ", "Imag")}


def view_data(key: str, x: np.ndarray, z: np.ndarray):
    """(x, y) of one view. IQ → (Re, Im)."""
    z = np.asarray(z)
    if not np.iscomplexobj(z):
        z = z.astype(complex)
    if key == "db":
        return x, 20 * np.log10(np.maximum(np.abs(z), 1e-15))
    if key == "lin":
        return x, np.abs(z)
    if key == "re":
        return x, z.real
    if key == "im":
        return x, z.imag
    if key == "phase":
        return x, np.degrees(np.angle(z))
    if key == "uphase":
        return x, np.degrees(np.unwrap(np.angle(z)))
    if key == "iq":
        return z.real, z.imag
    raise KeyError(key)


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


MAX_VIEWS = 5
PLOT_MIME = "application/x-labcontrol-plot"


def grid_positions(n: int) -> List[tuple]:
    """九宮格排法（row, col, rowspan, colspan）：1 = 整格；2 = 上下；3 = 上左右＋下；4 = 四方格；5 = 四方格＋下。"""
    full = lambda r: (r, 0, 1, 2)  # noqa: E731
    if n <= 0:
        return []
    if n == 1:
        return [full(0)]
    if n == 2:
        return [full(0), full(1)]
    if n == 3:
        return [(0, 0, 1, 1), (0, 1, 1, 1), full(1)]
    if n == 4:
        return [(0, 0, 1, 1), (0, 1, 1, 1), (1, 0, 1, 1), (1, 1, 1, 1)]
    return [(0, 0, 1, 1), (0, 1, 1, 1), (1, 0, 1, 1), (1, 1, 1, 1), full(2)][:n]


#: 單位群組：同一群組可以共用一個 Y 軸（疊圖時最多兩個群組 = 左右兩軸）
UNIT_GROUP = {"db": "dB", "lin": "lin", "re": "lin", "im": "lin", "phase": "deg", "uphase": "deg", "iq": "iq"}
UNITS = {"db": "dB", "lin": "", "re": "", "im": "", "phase": "°", "uphase": "°", "iq": ""}


def overlay_sides(views: List[str]) -> List[tuple]:
    """疊圖：第一種的單位群組放左軸，第二個單位群組放右軸；第三個群組以後不畫（回傳中略過）。"""
    groups: List[str] = []
    out = []
    for k in views:
        if k == "iq":
            continue
        g = UNIT_GROUP[k]
        if g not in groups:
            if len(groups) == 2:
                continue
            groups.append(g)
        out.append((k, "L" if groups.index(g) == 0 else "R"))
    return out


def _canvas():
    from .canvas import TraceCanvas

    return TraceCanvas(view_data, lambda k: VIEWS[k][1], lambda k: UNITS[k], lambda k: VIEWS[k][0])


class PlotCell(QtWidgets.QFrame):
    """一張圖：標題列（⠿ 拖曳換位置）＋ 畫布。"""

    swap = pyqtSignal(str, str)          # 拖來的 key、放到的 key

    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key
        self.setAcceptDrops(True)
        self.setObjectName("plotcell")
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(0)
        self.head = QtWidgets.QLabel()
        self.head.setCursor(Qt.CursorShape.OpenHandCursor)
        self.head.setToolTip("拖曳標題列到另一張圖 = 交換位置")
        self.head.setContentsMargins(6, 2, 6, 2)
        v.addWidget(self.head)
        self.canvas = _canvas()
        if key != "overlay":
            self.canvas.set_series([(key, "L")])
        v.addWidget(self.canvas, 1)
        self.pw = self.canvas.pw
        self.plot = self.canvas.plot
        self._press: Optional[QtCore.QPoint] = None
        self.head.mousePressEvent = self._head_press
        self.head.mouseMoveEvent = self._head_move
        self.set_title("")
        self._hl(False)

    @property
    def curve(self):
        return self.canvas.curves.get(self.key) or next(iter(self.canvas.curves.values()), None)

    def set_title(self, tr: str) -> None:
        name = "疊圖" if self.key == "overlay" else VIEWS[self.key][0]
        self.head.setText(f"⠿  {name}" + (f"　·　{tr}" if tr else ""))

    def _hl(self, on: bool) -> None:
        self.setStyleSheet(f"#plotcell {{ border:1px {'dashed' if on else 'solid'} "
                           f"{theme.c('accent') if on else theme.c('border')}; border-radius:6px; }}")
        self.head.setStyleSheet(f"color:{theme.c('muted')}; font-size:11px;")

    def _head_press(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press = ev.position().toPoint()

    def _head_move(self, ev) -> None:
        if self._press is None or (ev.position().toPoint() - self._press).manhattanLength() < 8:
            return
        self._press = None
        md = QtCore.QMimeData()
        md.setData(PLOT_MIME, self.key.encode())
        drag = QtGui.QDrag(self)
        drag.setMimeData(md)
        drag.setPixmap(self.head.grab())
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasFormat(PLOT_MIME) and bytes(ev.mimeData().data(PLOT_MIME)).decode() != self.key:
            self._hl(True)
            ev.acceptProposedAction()

    def dragLeaveEvent(self, ev) -> None:
        self._hl(False)

    def dropEvent(self, ev) -> None:
        self._hl(False)
        self.swap.emit(bytes(ev.mimeData().data(PLOT_MIME)).decode(), self.key)
        ev.acceptProposedAction()


class TracePlots(QtWidgets.QWidget):
    """分開：最多 5 張圖，九宮格排法、拖曳標題列交換位置；疊圖：同一張畫布、左右兩個 Y 軸。
    每張圖都可以拖曲線上下移動、在軸上拖曳縮放、雙擊加 marker。"""

    order_changed = pyqtSignal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.grid = QtWidgets.QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        self.views: List[str] = []
        self.cells: Dict[str, PlotCell] = {}
        self.mode = "grid"                 # grid | overlay
        self.points = False
        self.last = None
        self._syncing = False

    @property
    def plots(self) -> Dict[str, Any]:
        if self.mode == "overlay":
            c = self._cell("overlay")
            return {k: c.plot for k in c.canvas.curves}
        return {k: self.cells[k].plot for k in self.views}

    @property
    def curves(self) -> Dict[str, Any]:
        if self.mode == "overlay":
            return dict(self._cell("overlay").canvas.curves)
        return {k: self.cells[k].curve for k in self.views}

    def canvases(self) -> List[Any]:
        keys = ["overlay"] if self.mode == "overlay" else self.views
        return [self._cell(k).canvas for k in keys]

    def _cell(self, k: str) -> PlotCell:
        c = self.cells.get(k)
        if c is None:
            c = PlotCell(k)
            c.swap.connect(self.swap)
            c.canvas.set_points(self.points)
            if k not in ("iq", "overlay"):
                c.canvas.vb.sigXRangeChanged.connect(lambda _vb, r, k=k: self._sync_x(k, r))
            self.cells[k] = c
        return c

    def set_points(self, on: bool) -> None:
        self.points = bool(on)
        for c in self.cells.values():
            c.canvas.set_points(on)

    def set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self._relayout()
            if self.last is not None:
                self.update_data(*self.last)

    def set_views(self, views: List[str]) -> None:
        views = [k for k in dict.fromkeys(views) if k in VIEWS][:MAX_VIEWS]
        if views == self.views:
            return
        self.views = views
        self._relayout()
        if self.last is not None:
            self.update_data(*self.last)

    def swap(self, a: str, b: str) -> None:
        if a not in self.views or b not in self.views or a == b:
            return
        i, j = self.views.index(a), self.views.index(b)
        self.views[i], self.views[j] = b, a
        self._relayout()
        self.order_changed.emit(list(self.views))

    def _relayout(self) -> None:
        while self.grid.count():
            self.grid.takeAt(0)
        for c in self.cells.values():
            c.hide()
        if self.mode == "overlay":
            c = self._cell("overlay")
            c.canvas.set_series(overlay_sides(self.views))
            self.grid.addWidget(c, 0, 0, 1, 2)
            c.show()
            for r in range(3):
                self.grid.setRowStretch(r, 1 if r == 0 else 0)
            return
        for k, pos in zip(self.views, grid_positions(len(self.views))):
            c = self._cell(k)
            self.grid.addWidget(c, *pos)
            c.show()
        rows = max((r + rs for r, _c, rs, _cs in grid_positions(len(self.views))), default=1)
        for r in range(3):
            self.grid.setRowStretch(r, 1 if r < rows else 0)
        for col in range(2):
            self.grid.setColumnStretch(col, 1)

    def _sync_x(self, src: str, r) -> None:
        """分開模式的頻率軸連動：用數值同步（圖寬不同時 setXLink 會依像素對齊而錯開）。"""
        if self._syncing or src not in self.views or self.mode != "grid":
            return
        self._syncing = True
        try:
            for k in self.views:
                if k != src and k != "iq":
                    self.cells[k].plot.setXRange(float(r[0]), float(r[1]), padding=0)
        finally:
            self._syncing = False

    def update_data(self, tr: str, x, z, unit: str) -> None:
        self.last = (tr, x, z, unit)
        keys = ["overlay"] if self.mode == "overlay" else self.views
        for k in keys:
            c = self._cell(k)
            c.canvas.update_data(tr, x, z, unit)
            c.set_title(tr)

    def clear(self) -> None:
        self.last = None
        for c in self.cells.values():
            c.canvas.clear()

    def reset_offsets(self) -> None:
        for cv in self.canvases():
            cv.reset_offsets()

    def auto_range(self) -> None:
        for cv in self.canvases():
            cv.auto_range()

    def clear_markers(self) -> None:
        for cv in self.canvases():
            cv.clear_markers()


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
        self.sgroup = QtWidgets.QButtonGroup(self)
        self.sgroup.setExclusive(True)
        self.sbtn: Dict[str, QtWidgets.QPushButton] = {}
        for s in SPARAMS:
            b = QtWidgets.QPushButton(s)
            b.setCheckable(True)
            b.setFixedWidth(50)
            b.setToolTip(f"量測 {s}（ZNA 上沒有這條 trace 時自動建立）")
            self.sgroup.addButton(b)
            self.sbtn[s] = b
            row.addWidget(b)
        self.sbtn[saved.get("sparam") if saved.get("sparam") in SPARAMS else "S21"].setChecked(True)
        self.sgroup.buttonClicked.connect(self._sparam_changed)
        row.addStretch(1)
        tl.addLayout(row)
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
        row.addWidget(self.b_apply)
        row.addWidget(self.b_readp)
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
        self.view_bar = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(self.view_bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.vbtn: Dict[str, QtWidgets.QPushButton] = {}
        views = [k for k in (saved.get("views") or ["db"]) if k in VIEWS][:MAX_VIEWS] or ["db"]
        self.order: List[str] = list(views)          # 圖的順序（勾選順序；拖曳可交換）
        for k, (text, _y) in VIEWS.items():
            b = QtWidgets.QPushButton(text)
            b.setCheckable(True)
            b.setChecked(k in views)
            b.setToolTip(f"可以複選（最多 {MAX_VIEWS} 張）：每種一張圖；拖曳圖的標題列可以換位置")
            b.clicked.connect(lambda on, k=k: self._toggle_view(k, on))
            self.vbtn[k] = b
            row.addWidget(b)
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
        self.overlay = QtWidgets.QCheckBox("疊圖（同一張、左右兩軸）")
        self.overlay.setToolTip("勾選的種類畫在同一張圖：第一種單位用左軸，另一種單位用右軸（例如 dB 左、Phase 右）")
        self.overlay.setChecked(saved.get("mode") == "overlay")
        self.overlay.toggled.connect(self._mode_changed)
        row.addWidget(self.overlay)
        for text, tip, fn in (("自動範圍", "所有軸回到自動範圍", lambda: self.plots.auto_range()),
                              ("重設偏移", "拖曳曲線造成的上下偏移歸零", lambda: self.plots.reset_offsets()),
                              ("清除 Marker", "刪除所有 marker（單一 marker：在 marker 上按右鍵）",
                               lambda: self.plots.clear_markers())):
            b = QtWidgets.QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        hint = QtWidgets.QLabel("雙擊 = 加 marker · 拖曲線 = 上下移動 · 在軸上拖曳 = 縮放該軸")
        hint.setProperty("role", "muted")
        row.addWidget(hint, 1)
        bl.addWidget(self.tool_bar)
        self.plots = TracePlots()
        self.plots.set_points(self.points.isChecked())
        self.plots.set_mode("overlay" if self.overlay.isChecked() else "grid")
        self.plots.set_views(self.order)
        self.compact = False
        self.plots.order_changed.connect(self._order_changed)
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
        """目前顯示的圖（依排列順序）。"""
        return list(self.order)

    def set_views(self, views: List[str]) -> None:
        views = [k for k in dict.fromkeys(views) if k in VIEWS][:MAX_VIEWS] or ["db"]
        self.order = views
        self._apply_views()

    def _toggle_view(self, k: str, on: bool) -> None:
        if on and k not in self.order and self.overlay.isChecked():
            groups = {UNIT_GROUP[x] for x in self.order if x != "iq"}
            if k == "iq" or (UNIT_GROUP[k] not in groups and len(groups) >= 2):
                self.vbtn[k].setChecked(False)
                self.message.emit("疊圖只有左右兩個 Y 軸：最多兩種單位（dB / 線性 / 角度），IQ 請用分開的圖")
                return
        if on and k not in self.order:
            if len(self.order) >= MAX_VIEWS:
                self.vbtn[k].setChecked(False)
                self.message.emit(f"最多同時顯示 {MAX_VIEWS} 張圖，請先取消一種")
                return
            self.order.append(k)
        elif not on and k in self.order:
            if len(self.order) == 1:                      # 至少一種
                self.vbtn[k].setChecked(True)
                return
            self.order.remove(k)
        self._apply_views()

    def _apply_views(self) -> None:
        for k, b in self.vbtn.items():
            b.setChecked(k in self.order)
        if not getattr(self, "compact", False):
            self.plots.set_views(self.order)
        self.ui.save(views=self.order)

    def _mode_changed(self, on: bool) -> None:
        self.plots.set_mode("overlay" if on else "grid")
        self.ui.save(mode="overlay" if on else "grid")
        if on and len(overlay_sides(self.order)) < len([k for k in self.order if k != "iq"]) or \
                (on and "iq" in self.order):
            self.message.emit("疊圖只畫前兩種單位的資料（左右兩軸），IQ 只在分開的圖裡顯示")

    def set_compact(self, on: bool) -> None:
        """嵌在主視窗時只顯示 dB 曲線與「量測 / 循環」；其餘在獨立視窗。"""
        self.compact = bool(on)
        self.top_scroll.setVisible(not on)
        for w in (self.view_bar, self.tool_bar, self.b_plotwin, self.interval, self.interval_label):
            w.setVisible(not on)
        if on:
            if self.plot_window is not None:
                self.plot_window.close()
            self.plots.set_mode("grid")
            self.plots.set_views(["db"])
        else:
            self.plots.set_mode("overlay" if self.overlay.isChecked() else "grid")
            self.plots.set_views(self.order)

    def _order_changed(self, order: List[str]) -> None:
        self.order = list(order)
        self.ui.save(views=self.order)

    def _points_changed(self, on: bool) -> None:
        self.plots.set_points(on)
        self.ui.save(points=bool(on))

    def _sparam_changed(self, *_a) -> None:
        self.ui.save(sparam=self.trace())
        if not self.cont.isChecked():
            self.read_trace()

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
        self.params, self.last = {}, None
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
            if p.get("kind") == "bool" and p.get("settable"):
                e: QtWidgets.QWidget = QtWidgets.QCheckBox()
                e.clicked.connect(lambda _c, k=k: self._mark(k))
            elif p.get("settable"):
                e = QtWidgets.QLineEdit()
                e.textEdited.connect(lambda _t, k=k: self._mark(k))
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
                if isinstance(e, QtWidgets.QCheckBox):
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
        if be is None or not name or self._reading or self.busy:
            return
        tr = self.trace()
        self._reading = True
        self.b_read.setEnabled(False)
        t0 = time.monotonic()

        def done():
            self._reading = False
            self.b_read.setEnabled(not self.busy)

        def ok(res):
            done()
            if gen != self._gen:
                return
            dt = time.monotonic() - t0
            self.last = (tr, *res)
            self.plots.update_data(tr, *res)
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
        run_bg(lambda: be.vna_trace(name, tr, True), ok, fail)

    def _next(self, cont: int) -> None:
        if cont == self._cont_gen and self.cont.isChecked() and not self.busy:
            self.read_trace(cont=cont)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        for w in (self.b_apply, self.b_read, self.rf, self.cont, *self.sbtn.values(), *self.edits.values()):
            w.setEnabled(not busy)
        if busy:
            self.cont.setChecked(False)

    def close_windows(self) -> None:
        if self.plot_window is not None:
            self.plot_window.close()
