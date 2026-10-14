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


def _canvas(math=None):
    from .canvas import TraceCanvas

    def view(k, x, z):
        xx, yy = view_data(k, x, z)
        if math is None or not math.active:
            return xx, yy
        try:
            out = math.apply(xx, yy)
            math.error = ""
            return out
        except Exception as e:  # noqa: BLE001 — 算不出來：畫原本的，並在面板上顯示錯誤
            math.error = str(e)
            return xx, yy
    return TraceCanvas(view, lambda k: VIEWS[k][1], lambda k: UNITS[k], lambda k: VIEWS[k][0])


def normalize_groups(groups: List[List[str]], limit: int = MAX_VIEWS) -> List[List[str]]:
    """1.0.6：整理圖的分組（每一組 = 一張圖，組內疊圖）。去掉重複與未知的種類、最多 ``limit`` 種；
    IQ 只能自己一張；一張圖最多兩種單位（左右兩軸），放不下的另開一張（緊接在後面）。"""
    seen: set = set()
    out: List[List[str]] = []
    for g in groups or []:
        keys = [k for k in (g if isinstance(g, (list, tuple)) else [g]) if k in VIEWS]
        cur: List[str] = []
        extra: List[List[str]] = []
        units: List[str] = []
        for k in keys:
            if k in seen or len(seen) >= limit:
                continue
            seen.add(k)
            u = UNIT_GROUP[k]
            if k == "iq" or (u not in units and len(units) == 2):
                extra.append([k])
                continue
            if u not in units:
                units.append(u)
            cur.append(k)
        out += ([cur] if cur else []) + extra
    return out


def can_overlay(keys: List[str]) -> bool:
    return "iq" not in keys and len({UNIT_GROUP[k] for k in keys}) <= 2


def cell_id(keys: List[str]) -> str:
    return "+".join(keys)


def layout_positions(n: int, mode: str = "auto") -> List[tuple]:
    """auto = 九宮格排法；rows = 上下一張一張；cols = 左右並排。→ (row, col, rowspan, colspan)，2 欄。"""
    if mode == "rows":
        return [(i, 0, 1, 2) for i in range(n)]
    if mode == "cols":
        return [(0, i, 1, 1) for i in range(n)]
    return grid_positions(n)


DROP_CENTER, DROP_BEFORE, DROP_AFTER = "center", "before", "after"


class _DropHint(QtWidgets.QLabel):
    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hide()

    def show_zone(self, zone: str, edge: str) -> None:
        p = self.parentWidget().rect()
        col = QtGui.QColor(theme.c("accent"))
        rgba = f"rgba({col.red()},{col.green()},{col.blue()},70)"
        if zone == DROP_CENTER:
            self.setGeometry(p.adjusted(p.width() // 6, p.height() // 6, -p.width() // 6, -p.height() // 6))
            self.setText("疊在這張圖")
        else:
            t = 26
            geo = {"left": QtCore.QRect(0, 0, t, p.height()), "right": QtCore.QRect(p.width() - t, 0, t, p.height()),
                   "top": QtCore.QRect(0, 0, p.width(), t),
                   "bottom": QtCore.QRect(0, p.height() - t, p.width(), t)}[edge]
            self.setGeometry(geo)
            self.setText("放旁邊" if edge in ("top", "bottom") else "")
        self.setStyleSheet(f"background:{rgba}; border:2px solid {theme.c('accent')}; border-radius:6px; "
                           f"color:{theme.c('text')}; font-weight:600;")
        self.raise_()
        self.show()


class PlotCell(QtWidgets.QFrame):
    """一張圖（1.0.6 像 VNA 的 diagram）：標題列 ⠿ = 拖整張圖，曲線名稱 = 只拖那條曲線；畫布可以疊多種曲線。

    拖到另一張圖的中間 = 疊在那張圖；拖到邊緣 = 放在那張圖旁邊（左 / 上 = 前面，右 / 下 = 後面）。"""

    dropped = pyqtSignal(str, str, str)          # 拖來的東西（cell:<id> / trace:<key>）、放到的圖 id、位置
    split_trace = pyqtSignal(str)                # 曲線右鍵「分開成獨立的圖」

    def __init__(self, keys: List[str], math=None) -> None:
        super().__init__()
        self.keys = list(keys)
        self.key = cell_id(self.keys)
        self.setAcceptDrops(True)
        self.setObjectName("plotcell")
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(0)
        bar = QtWidgets.QWidget()
        hb = QtWidgets.QHBoxLayout(bar)
        hb.setContentsMargins(4, 1, 4, 1)
        hb.setSpacing(4)
        self.grip = QtWidgets.QLabel("⠿")
        self.grip.setCursor(Qt.CursorShape.OpenHandCursor)
        self.grip.setToolTip("拖曳整張圖：放到另一張圖中間 = 疊圖，放到邊緣 = 放在旁邊")
        hb.addWidget(self.grip)
        self.chips: Dict[str, QtWidgets.QLabel] = {}
        for k in self.keys:
            chip = QtWidgets.QLabel(VIEWS[k][0])
            chip.setCursor(Qt.CursorShape.OpenHandCursor)
            chip.setToolTip("拖曳這條曲線到另一張圖 = 疊上去；拖到邊緣 = 自己一張圖放旁邊；右鍵 = 分開")
            chip.mousePressEvent = lambda ev, w=chip: self._press_at(ev, w)
            chip.mouseMoveEvent = lambda ev, k=k, w=chip: self._move_at(ev, f"trace:{k}", w)
            chip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            chip.customContextMenuRequested.connect(lambda _p, k=k: self._chip_menu(k))
            self.chips[k] = chip
            hb.addWidget(chip)
        self.head = QtWidgets.QLabel()
        self.head.setCursor(Qt.CursorShape.OpenHandCursor)
        self.head.setToolTip(self.grip.toolTip())
        hb.addWidget(self.head, 1)
        for w in (self.grip, self.head):
            w.mousePressEvent = lambda ev, w=w: self._press_at(ev, w)
            w.mouseMoveEvent = lambda ev, w=w: self._move_at(ev, f"cell:{self.key}", w)
        self.bar = bar
        v.addWidget(bar)
        self.canvas = _canvas(math)
        self.canvas.set_series(overlay_sides(self.keys) if len(self.keys) > 1 else [(self.keys[0], "L")])
        v.addWidget(self.canvas, 1)
        for w in [self.canvas, *self.canvas.findChildren(QtWidgets.QWidget)]:
            w.setAcceptDrops(False)                      # 拖放交給整張圖（PlotCell）處理
        self.pw = self.canvas.pw
        self.plot = self.canvas.plot
        self._press: Optional[QtCore.QPoint] = None
        self.hint = _DropHint(self)
        self.set_title("")
        self._hl(False)

    @property
    def curve(self):
        return self.canvas.curves.get(self.keys[0]) or next(iter(self.canvas.curves.values()), None)

    def set_title(self, tr: str) -> None:
        self.head.setText(f"　{tr}" if tr else "")

    def _hl(self, on: bool) -> None:
        self.setStyleSheet(f"#plotcell {{ border:1px {'dashed' if on else 'solid'} "
                           f"{theme.c('accent') if on else theme.c('border')}; border-radius:6px; }}")
        self.head.setStyleSheet(f"color:{theme.c('muted')}; font-size:11px;")
        self.grip.setStyleSheet(f"color:{theme.c('muted')}; font-size:13px;")
        for i, chip in enumerate(self.chips.values()):
            col = theme.c(["curve", "orange", "ok", "err", "accent"][i % 5])
            chip.setStyleSheet(f"color:{col}; border:1px solid {col}; border-radius:7px; padding:0 6px; "
                               "font-size:11px; font-weight:600;")

    # ---- 拖曳 ---------------------------------------------------------------------
    def _press_at(self, ev, w) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press = ev.position().toPoint()

    def _move_at(self, ev, payload: str, w) -> None:
        if self._press is None or (ev.position().toPoint() - self._press).manhattanLength() < 8:
            return
        self._press = None
        md = QtCore.QMimeData()
        md.setData(PLOT_MIME, payload.encode())
        drag = QtGui.QDrag(self)
        drag.setMimeData(md)
        drag.setPixmap(w.grab() if payload.startswith("trace:") else self.bar.grab())
        drag.exec(Qt.DropAction.MoveAction)

    def zone_at(self, pos: QtCore.QPoint) -> tuple:
        """(位置, 邊)：中間 1/3 以內 = 疊圖；否則依最近的邊 = 前面 / 後面。"""
        w, h = max(self.width(), 1), max(self.height(), 1)
        fx, fy = pos.x() / w, pos.y() / h
        if 0.25 <= fx <= 0.75 and 0.25 <= fy <= 0.75:
            return DROP_CENTER, ""
        d = {"left": fx, "right": 1 - fx, "top": fy, "bottom": 1 - fy}
        edge = min(d, key=d.get)
        return (DROP_BEFORE if edge in ("left", "top") else DROP_AFTER), edge

    def _payload(self, ev) -> Optional[str]:
        if not ev.mimeData().hasFormat(PLOT_MIME):
            return None
        return bytes(ev.mimeData().data(PLOT_MIME)).decode()

    def dragEnterEvent(self, ev) -> None:
        p = self._payload(ev)
        if p is None or p == f"cell:{self.key}":
            return
        self._hl(True)
        ev.acceptProposedAction()

    def dragMoveEvent(self, ev) -> None:
        p = self._payload(ev)
        if p is None or p == f"cell:{self.key}":
            ev.ignore()
            return
        zone, edge = self.zone_at(ev.position().toPoint())
        self.hint.show_zone(zone, edge)
        ev.acceptProposedAction()

    def dragLeaveEvent(self, ev) -> None:
        self._hl(False)
        self.hint.hide()

    def dropEvent(self, ev) -> None:
        self._hl(False)
        self.hint.hide()
        p = self._payload(ev)
        if p is None:
            return
        zone, _edge = self.zone_at(ev.position().toPoint())
        ev.acceptProposedAction()
        QtCore.QTimer.singleShot(0, lambda: self.dropped.emit(p, self.key, zone))   # 拖曳結束後再重排

    def _chip_menu(self, k: str) -> None:
        m = QtWidgets.QMenu(self)
        a = m.addAction("分開成獨立的圖", lambda: self.split_trace.emit(k))
        a.setEnabled(len(self.keys) > 1)
        m.exec(QtGui.QCursor.pos())


class TracePlots(QtWidgets.QWidget):
    """1.0.6：像 VNA 儀器的 diagram——每張圖可以疊多種曲線（左右兩軸），也可以分開；疊圖和並排可以同時存在。
    拖曳圖或曲線名稱重新排列；版面：自動（九宮格）、上下、左右。
    每張圖都可以拖曲線上下移動、在軸上拖曳縮放、雙擊加 marker。"""

    order_changed = pyqtSignal(list)              # 曲線種類的順序
    groups_changed = pyqtSignal(list)             # 圖的分組（每組一張圖）
    message = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.grid = QtWidgets.QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        self.groups: List[List[str]] = []
        self.cells: Dict[str, PlotCell] = {}
        self.layout_mode = "auto"
        self.points = False
        self.last = None
        self._syncing = False
        from ....core.axis_math import AxisMath

        self.math = AxisMath()             # 0.0.30：x / y 軸運算（y = y**2、x = log(x)…）
        self.math.error = ""

    def set_math(self, x_text: str, y_text: str) -> None:
        """設定軸運算並重畫；寫錯 → AxisMathError（原本的不變）。"""
        self.math.set(x_text, y_text)
        self.math.error = ""
        if self.last is not None:
            self.update_data(*self.last)

    # ---- 查詢 ---------------------------------------------------------------------
    @property
    def views(self) -> List[str]:
        return [k for g in self.groups for k in g]

    @property
    def mode(self) -> str:
        """相容：全部疊在一張 = overlay，否則 grid。"""
        return "overlay" if len(self.groups) == 1 and len(self.groups[0]) > 1 else "grid"

    def cell_for(self, k: str) -> Optional[PlotCell]:
        g = next((g for g in self.groups if k in g), None)
        return self._cell(g) if g is not None else None

    @property
    def plots(self) -> Dict[str, Any]:
        return {k: self.cell_for(k).plot for k in self.views}

    @property
    def curves(self) -> Dict[str, Any]:
        return {k: self.cell_for(k).canvas.curves.get(k) for k in self.views}

    def canvases(self) -> List[Any]:
        return [self._cell(g).canvas for g in self.groups]

    def _cell(self, keys: List[str]) -> PlotCell:
        cid = cell_id(keys)
        c = self.cells.get(cid)
        if c is None:
            c = PlotCell(keys, self.math)
            c.dropped.connect(self._on_drop)
            c.split_trace.connect(self.split_out)
            c.canvas.set_points(self.points)
            if "iq" not in keys:
                c.canvas.vb.sigXRangeChanged.connect(lambda _vb, r, cid=cid: self._sync_x(cid, r))
            self.cells[cid] = c
        return c

    # ---- 設定 ---------------------------------------------------------------------
    def set_points(self, on: bool) -> None:
        self.points = bool(on)
        for c in self.cells.values():
            c.canvas.set_points(on)

    def set_groups(self, groups: List[List[str]], emit: bool = False) -> None:
        groups = normalize_groups(groups)
        if groups == self.groups:
            return
        self.groups = groups
        self._relayout()
        if self.last is not None:
            self.update_data(*self.last)
        if emit:
            self.order_changed.emit(self.views)
            self.groups_changed.emit([list(g) for g in self.groups])

    def set_mode(self, mode: str) -> None:
        """overlay = 全部疊在一張（放不下的另開）；grid = 每種一張。"""
        views = self.views
        self.set_groups([views] if mode == "overlay" else [[k] for k in views])

    def set_views(self, views: List[str]) -> None:
        """勾選的種類：保留原本的分組，新的種類自己一張圖（加在最後）。"""
        views = [k for k in dict.fromkeys(views) if k in VIEWS][:MAX_VIEWS]
        keep = [[k for k in g if k in views] for g in self.groups]
        have = {k for g in keep for k in g}
        self.set_groups([g for g in keep if g] + [[k] for k in views if k not in have])

    def set_layout_mode(self, mode: str) -> None:
        if mode in ("auto", "rows", "cols") and mode != self.layout_mode:
            self.layout_mode = mode
            self._relayout()

    # ---- 拖曳重排 -------------------------------------------------------------------
    def _index_of(self, cid: str) -> int:
        return next((i for i, g in enumerate(self.groups) if cell_id(g) == cid), -1)

    def _on_drop(self, payload: str, target: str, zone: str) -> None:
        kind, _, what = payload.partition(":")
        if kind == "trace":
            self.move_trace(what, target, zone)
        elif kind == "cell":
            self.move_cell(what, target, zone)

    def move_trace(self, k: str, target: str, zone: str) -> bool:
        """把一條曲線移到另一張圖（中間 = 疊上去；邊緣 = 自己一張放在旁邊）。"""
        groups = [list(g) for g in self.groups]
        ti = self._index_of(target)
        src = next((g for g in groups if k in g), None)
        if ti < 0 or src is None:
            return False
        tgt = groups[ti]
        if zone == DROP_CENTER:
            if k in tgt:
                return False
            if not can_overlay(tgt + [k]):
                self.message.emit("這張圖疊不下：一張圖最多兩種單位（左右兩軸），IQ 只能自己一張")
                return False
            src.remove(k)
            tgt.append(k)
        else:
            if src == [k] and src is tgt:
                return False
            src.remove(k)
            ti = next(i for i, g in enumerate(groups) if g is tgt)
            groups.insert(ti if zone == DROP_BEFORE else ti + 1, [k])
        self.set_groups([g for g in groups if g], emit=True)
        return True

    def move_cell(self, cid: str, target: str, zone: str) -> bool:
        """整張圖：中間 = 合併成疊圖；邊緣 = 移到那張圖前面 / 後面。"""
        si, ti = self._index_of(cid), self._index_of(target)
        if si < 0 or ti < 0 or si == ti:
            return False
        groups = [list(g) for g in self.groups]
        moving = groups[si]
        tgt = groups[ti]
        if zone == DROP_CENTER:
            if not can_overlay(tgt + moving):
                self.message.emit("這兩張圖疊不起來：一張圖最多兩種單位（左右兩軸），IQ 只能自己一張")
                return False
            tgt.extend(moving)
            groups.pop(si)
        else:
            groups.pop(si)
            ti = next(i for i, g in enumerate(groups) if g is tgt)
            groups.insert(ti if zone == DROP_BEFORE else ti + 1, moving)
        self.set_groups(groups, emit=True)
        return True

    def split_out(self, k: str) -> None:
        groups = [list(g) for g in self.groups]
        i = next((i for i, g in enumerate(groups) if k in g), -1)
        if i < 0 or len(groups[i]) < 2:
            return
        groups[i].remove(k)
        groups.insert(i + 1, [k])
        self.set_groups(groups, emit=True)

    def swap(self, a: str, b: str) -> None:
        """相容（舊的拖曳換位置）：交換 a、b 所在的兩張圖。"""
        ia = next((i for i, g in enumerate(self.groups) if a in g), -1)
        ib = next((i for i, g in enumerate(self.groups) if b in g), -1)
        if ia < 0 or ib < 0 or ia == ib:
            return
        groups = [list(g) for g in self.groups]
        groups[ia], groups[ib] = groups[ib], groups[ia]
        self.set_groups(groups, emit=True)

    def _relayout(self) -> None:
        while self.grid.count():
            self.grid.takeAt(0)
        live = {cell_id(g) for g in self.groups}
        for cid in [c for c in self.cells if c not in live]:
            c = self.cells.pop(cid)                      # 分組改了：舊的圖不再用
            c.hide()
            c.deleteLater()
        positions = layout_positions(len(self.groups), self.layout_mode)
        for g, pos in zip(self.groups, positions):
            c = self._cell(g)
            self.grid.addWidget(c, *pos)
            c.show()
        rows = max((r + rs for r, _c, rs, _cs in positions), default=1)
        cols = max((c + cs for _r, c, _rs, cs in positions), default=1)
        for r in range(max(rows, self.grid.rowCount())):
            self.grid.setRowStretch(r, 1 if r < rows else 0)
        for col in range(max(cols, self.grid.columnCount(), 2)):
            self.grid.setColumnStretch(col, 1 if col < max(cols, 2 if self.layout_mode != "cols" else cols) else 0)

    def _sync_x(self, src: str, r) -> None:
        """頻率軸連動：用數值同步（圖寬不同時 setXLink 會依像素對齊而錯開）。"""
        if self._syncing or src not in self.cells:
            return
        self._syncing = True
        try:
            for g in self.groups:
                cid = cell_id(g)
                if cid != src and "iq" not in g:
                    self.cells[cid].plot.setXRange(float(r[0]), float(r[1]), padding=0)
        finally:
            self._syncing = False

    def update_data(self, tr: str, x, z, unit: str) -> None:
        self.last = (tr, x, z, unit)
        m = self.math
        for g in self.groups:
            c = self._cell(g)
            c.canvas.x_is_freq = m.fx is None
            c.canvas.update_data(tr, x, z, unit)
            c.set_title(tr + (f"　·　{self.math_text()}" if m.active else ""))
            if m.fx is not None and "iq" not in g:
                c.canvas.plot.setLabel("bottom", f"x = {m.x_text}（x = 頻率 Hz）", units="")
            if len(g) == 1:
                y0 = VIEWS[g[0]][1]
                c.canvas.plot.setLabel("left", f"y = {m.y_text}（y = {y0}）" if m.fy is not None else y0)

    def math_text(self) -> str:
        m = self.math
        return "　".join(t for t in ((f"x = {m.x_text}" if m.fx else ""), (f"y = {m.y_text}" if m.fy else "")) if t)

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
        self.b_ports = QtWidgets.QPushButton("偵測 port")
        self.b_ports.setToolTip("問 VNA 有幾個 port（INST:PORT:COUN?）與目前有哪些 trace，\n"
                                "依結果列出全部 S 參數（例如 4 port：S11…S44）")
        self.b_ports.clicked.connect(lambda: self.detect_ports())
        row.addWidget(self.b_ports)
        self.ports_label = QtWidgets.QLabel("")
        self.ports_label.setProperty("role", "muted")
        row.addWidget(self.ports_label)
        row.addStretch(1)
        tl.addLayout(row)
        self.sgroup = QtWidgets.QButtonGroup(self)
        self.sgroup.setExclusive(True)
        self.sgroup.buttonClicked.connect(self._sparam_changed)
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
        self.view_bar = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(self.view_bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.vbtn: Dict[str, QtWidgets.QPushButton] = {}
        views = [k for k in (saved.get("views") or ["db"]) if k in VIEWS][:MAX_VIEWS] or ["db"]
        self.order: List[str] = list(views)          # 圖的順序（勾選順序；拖曳可交換）
        # 1.0.6：圖的分組（每組一張圖、組內疊圖）；舊設定只有 mode → 全部疊或全部分開
        saved_groups = saved.get("groups")
        if isinstance(saved_groups, list) and saved_groups:
            self.groups = normalize_groups([g for g in saved_groups if isinstance(g, list)]) or [[k] for k in views]
        else:
            self.groups = [list(views)] if saved.get("mode") == "overlay" else [[k] for k in views]
        self.order = [k for g in self.groups for k in g]
        views = list(self.order)
        for k, (text, _y) in VIEWS.items():
            b = QtWidgets.QPushButton(text)
            b.setCheckable(True)
            b.setChecked(k in views)
            b.setToolTip(f"可以複選（最多 {MAX_VIEWS} 種）：新的種類自己一張圖；拖曳圖或曲線名稱可以疊圖 / 換位置")
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
        self.overlay = QtWidgets.QCheckBox("全部疊圖")
        self.overlay.setToolTip("勾選的種類全部畫在同一張圖：第一種單位用左軸，另一種單位用右軸（例如 dB 左、Phase 右）。\n"
                                "只想疊其中幾種：拖曳圖的標題（⠿）或曲線名稱到另一張圖的中間；拖到邊緣 = 放在旁邊")
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
        hint = QtWidgets.QLabel("拖 ⠿ 或曲線名稱：放中間 = 疊圖、放邊緣 = 放旁邊 · 雙擊 = 加 marker · "
                                "拖曲線 = 上下移動 · 在軸上拖曳 = 縮放該軸")
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
        self.plots = TracePlots()
        self.plots.set_points(self.points.isChecked())
        self.plots.set_layout_mode(self.layout_box.currentData())
        self.plots.set_groups(self.groups)
        self.compact = False
        self.plots.order_changed.connect(self._order_changed)
        self.plots.groups_changed.connect(self._groups_changed)
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
        """目前顯示的圖（依排列順序）。"""
        return list(self.order)

    def set_views(self, views: List[str]) -> None:
        views = [k for k in dict.fromkeys(views) if k in VIEWS][:MAX_VIEWS] or ["db"]
        self.order = views
        self.groups = []                       # 指定的順序：每種一張（「全部疊圖」勾著 → 一張）
        self._apply_views()

    def _toggle_view(self, k: str, on: bool) -> None:
        if on and k not in self.order and self.overlay.isChecked() and not can_overlay(self.order + [k]):
            self.vbtn[k].setChecked(False)
            self.message.emit("全部疊圖只有左右兩個 Y 軸：最多兩種單位（dB / 線性 / 角度），IQ 請用分開的圖")
            return
        if on and k not in self.order:
            if len(self.order) >= MAX_VIEWS:
                self.vbtn[k].setChecked(False)
                self.message.emit(f"最多同時顯示 {MAX_VIEWS} 種，請先取消一種")
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
        if self.overlay.isChecked():
            groups = [list(self.order)]
        else:
            keep = [[k for k in g if k in self.order] for g in self.groups]
            have = {k for g in keep for k in g}
            groups = [g for g in keep if g] + [[k] for k in self.order if k not in have]
        self.groups = normalize_groups(groups)
        self.order = [k for g in self.groups for k in g]
        if not getattr(self, "compact", False):
            self.plots.set_groups(self.groups)
        self.ui.save(views=self.order, groups=self.groups)

    def _mode_changed(self, on: bool) -> None:
        self.groups = normalize_groups([list(self.order)] if on else [[k] for k in self.order])
        if not getattr(self, "compact", False):
            self.plots.set_groups(self.groups)
        self.ui.save(groups=self.groups, mode="overlay" if on else "grid")
        if on and len(self.groups) > 1:
            self.message.emit("全部疊圖只畫兩種單位（左右兩軸），其他的另開一張；IQ 只能自己一張")

    def _layout_changed(self, _i: int) -> None:
        mode = self.layout_box.currentData()
        self.plots.set_layout_mode(mode)
        self.ui.save(layout=mode)

    def _groups_changed(self, groups: List[List[str]]) -> None:
        """拖曳重排後（圖或曲線）：記住分組，「全部疊圖」跟著目前狀態。"""
        if getattr(self, "compact", False):
            return
        self.groups = [list(g) for g in groups]
        self.order = [k for g in self.groups for k in g]
        self.overlay.blockSignals(True)
        self.overlay.setChecked(len(self.groups) == 1 and len(self.order) > 1)
        self.overlay.blockSignals(False)
        self.ui.save(views=self.order, groups=self.groups)

    def set_compact(self, on: bool) -> None:
        """嵌在主視窗時只顯示 dB 曲線與「量測 / 循環」；其餘在獨立視窗。"""
        self.compact = bool(on)
        self.top_scroll.setVisible(not on)
        for w in (self.view_bar, self.tool_bar, self.math_bar, self.b_plotwin, self.interval, self.interval_label):
            w.setVisible(not on)
        if on:
            if self.plot_window is not None:
                self.plot_window.close()
            self.plots.set_groups([["db"]])
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
        self.ui.save(views=self.order)

    def _points_changed(self, on: bool) -> None:
        self.plots.set_points(on)
        self.ui.save(points=bool(on))

    def _sparam_changed(self, *_a) -> None:
        self._want_sparam = self.trace()
        self.ui.save(sparam=self.trace())
        if not self.cont.isChecked():
            self.read_trace()

    # ---- S 參數按鈕（依 port 數）-------------------------------------------------------
    def _build_sbuttons(self, params: List[str], traces: Dict[str, List[str]]) -> None:
        """重建 S 參數按鈕：n port → n×n 矩陣（第 i 列 = Si1…Sin）；ZNA 上已有 trace 的用粗體。"""
        from ....core.sparams import ports_of

        for b in list(self.sbtn.values()):
            self.sgroup.removeButton(b)
            b.setParent(None)
            b.deleteLater()
        self.sbtn.clear()
        have = {k.upper(): v for k, v in (traces or {}).items()}
        for k, sp in enumerate(params):
            b = QtWidgets.QPushButton(sp)
            b.setCheckable(True)
            b.setFixedWidth(50)
            if sp.upper() in have:
                f = b.font()
                f.setBold(True)
                b.setFont(f)
                b.setToolTip(f"量測 {sp}（ZNA 上已有：{', '.join(have[sp.upper()])}）")
            else:
                b.setToolTip(f"量測 {sp}（ZNA 上沒有這條 trace，量測時自動建立 LC_{sp}）")
            pp = ports_of(sp)
            r, c = (pp[0] - 1, pp[1] - 1) if pp else (0, k)
            self.sgroup.addButton(b)
            self.sbtn[sp] = b
            self.sgrid.addWidget(b, r, c)
            b.setEnabled(not self.busy)
        want = self._want_sparam if self._want_sparam in self.sbtn else ("S21" if "S21" in self.sbtn else
                                                                         next(iter(self.sbtn), None))
        if want:
            self.sbtn[want].setChecked(True)

    def _apply_ports(self, name: Optional[str]) -> None:
        info = self.port_info.get(name or "")
        if info:
            self._build_sbuttons(list(info.get("sparams") or SPARAMS), info.get("traces") or {})
            have = [k for k in (info.get("traces") or {})]
            self.ports_label.setText(f"{info.get('ports')} port" + (f" · 已有 {', '.join(have)}" if have else ""))
            self.ports_label.setToolTip(f"來源：{info.get('source', '')}")
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
                have = list((info or {}).get("traces") or {})
                self.message.emit(f"{name}：{info.get('ports')} port（{info.get('source', '')}），"
                                  f"可量 {len(info.get('sparams') or [])} 個 S 參數"
                                  + (f"；ZNA 上已有 {', '.join(have)}" if have else ""))

        def fail(m):
            done()
            if name == self.name:
                self._apply_ports(name)
            self.message.emit(f"❌ 偵測 {name} 的 port 失敗：{m}")
        run_bg(lambda: be.vna_ports(name), ok, fail)

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
            if p.get("kind") == "bool" and p.get("settable"):
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

    def minimum_frequency(self) -> Optional[float]:
        """目前曲線 |S| 最小處的頻率（Hz）；沒有曲線 → None。"""
        if not self.last:
            return None
        _tr, x, z = self.last[0], np.asarray(self.last[1], dtype=float), np.asarray(self.last[2])
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
        for w in (self.b_apply, self.b_read, self.rf, self.cont, self.b_ports, *self.sbtn.values(),
                  *self.edits.values()):
            w.setEnabled(not busy)
        if busy:
            self.cont.setChecked(False)

    def close_windows(self) -> None:
        if self.plot_window is not None:
            self.plot_window.close()
