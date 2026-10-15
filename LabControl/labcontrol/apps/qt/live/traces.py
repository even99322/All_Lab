"""1.0.10：像 VNA 儀器面板的 trace / diagram（即時數據與量測監控共用）。

    trace   = 資料來源（S21、VNA1 - S21…）＋ 格式（dB Mag、Phase、Smith、Polar…），key = "S21:db"
    diagram = 一張圖，可以疊好幾條 trace（直角座標最多兩種單位 = 左右兩軸；Polar / Smith 只跟 Polar / Smith 疊）

操作跟 ZNA 一樣：點一條 trace 的名稱 = 選成「目前的 trace」，上面的 S 參數 / 格式按鈕改的就是它；
拖 trace 名稱到另一張圖中間 = 疊上去、到邊緣 = 自己一張放旁邊；拖 ⠿ = 整張圖；右鍵 trace 名稱 = 換來源 / 格式、分開、刪除。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, pyqtSignal

from .. import theme

#: 格式：key → (按鈕文字, y 軸標題, 單位, 單位群組)；順序 = VNA 面板的格式鍵
FORMATS: Dict[str, Tuple[str, str, str, str]] = {
    "db": ("dB Mag", "振幅 (dB)", "dB", "dB"),
    "phase": ("Phase", "相位 (°)", "°", "deg"),
    "smith": ("Smith", "Imag", "", "polar"),
    "iq": ("Polar", "Imag", "", "polar"),
    "lin": ("Lin Mag", "|S| (linear)", "", "lin"),
    "uphase": ("Unwr Phase", "相位 unwrap (°)", "°", "deg"),
    "re": ("Real", "實部", "", "lin"),
    "im": ("Imag", "虛部", "", "lin"),
    "delay": ("Delay", "群延遲 (ns)", "ns", "delay"),
}
POLAR = {"iq", "smith"}
MAX_TRACES = 8
PLOT_MIME = "application/x-labcontrol-plot"
DROP_CENTER, DROP_BEFORE, DROP_AFTER = "center", "before", "after"
CHIP_COLORS = ["curve", "orange", "ok", "err", "accent"]


# ---- trace key ---------------------------------------------------------------------------------
def tkey(src: str, fmt: str) -> str:
    return f"{src}:{fmt}" if src else fmt


def key_fmt(k: str) -> str:
    return k.rpartition(":")[2]


def key_src(k: str) -> str:
    return k.rpartition(":")[0]


def valid_key(k: Any) -> bool:
    return isinstance(k, str) and key_fmt(k) in FORMATS


def trace_label(k: str) -> str:
    src, fmt = key_src(k), key_fmt(k)
    name = FORMATS[fmt][0] if fmt in FORMATS else fmt
    return f"{src} {name}" if src else name


def is_polar(k: str) -> bool:
    return key_fmt(k) in POLAR


def unit_group(k: str) -> str:
    return FORMATS[key_fmt(k)][3]


def view_data(fmt: str, x: np.ndarray, z: np.ndarray):
    """(x, y)；Polar / Smith → (Re, Im)；Delay = −dφ/dω（ns）。"""
    z = np.asarray(z)
    if not np.iscomplexobj(z):
        z = z.astype(complex)
    x = np.asarray(x, float)
    if fmt == "db":
        return x, 20 * np.log10(np.maximum(np.abs(z), 1e-15))
    if fmt == "lin":
        return x, np.abs(z)
    if fmt == "re":
        return x, z.real
    if fmt == "im":
        return x, z.imag
    if fmt == "phase":
        return x, np.degrees(np.angle(z))
    if fmt == "uphase":
        return x, np.degrees(np.unwrap(np.angle(z)))
    if fmt in POLAR:
        return z.real, z.imag
    if fmt == "delay":
        if z.size < 2 or x.size != z.size:
            return x, np.full(z.shape, np.nan)
        ph = np.unwrap(np.angle(z))
        with np.errstate(divide="ignore", invalid="ignore"):
            tau = -np.gradient(ph, x) / (2 * np.pi)
        return x, tau * 1e9
    raise KeyError(fmt)


def trace_data(k: str, x, data) -> Tuple[np.ndarray, np.ndarray]:
    """data = {來源: z} 或單一條 z（舊的寫法）。沒有這個來源 → 空的。"""
    src = key_src(k)
    if isinstance(data, dict):
        z = data.get(src)
        if z is None and not src and len(data) == 1:
            z = next(iter(data.values()))
        if z is None:
            return np.array([]), np.array([])
    else:
        z = data
    return view_data(key_fmt(k), x, z)


# ---- 分組規則 --------------------------------------------------------------------------------
def can_overlay(keys: List[str]) -> bool:
    keys = [k for k in keys if valid_key(k)]
    if not keys:
        return True
    pol = [is_polar(k) for k in keys]
    if any(pol):
        return all(pol)
    return len({unit_group(k) for k in keys}) <= 2


def normalize_groups(groups: List[List[str]], limit: int = MAX_TRACES) -> List[List[str]]:
    """整理圖的分組（每一組 = 一張圖，組內疊圖）。去掉重複與未知的、最多 ``limit`` 條；
    一張圖疊不下的（第三種單位、Polar / Smith 跟直角座標混在一起）另開一張，緊接在後面。"""
    seen: set = set()
    out: List[List[str]] = []
    for g in groups or []:
        keys = [k for k in (g if isinstance(g, (list, tuple)) else [g]) if valid_key(k)]
        cur: List[str] = []
        extra: List[List[str]] = []
        for k in keys:
            if k in seen or len(seen) >= limit:
                continue
            seen.add(k)
            if can_overlay(cur + [k]):
                cur.append(k)
            else:
                extra.append([k])
        out += ([cur] if cur else []) + extra
    return out


def overlay_sides(keys: List[str]) -> List[Tuple[str, str]]:
    """疊圖：第一種單位放左軸、第二種放右軸（Polar / Smith 都在同一個平面）。"""
    groups: List[str] = []
    out = []
    for k in keys:
        if is_polar(k):
            out.append((k, "L"))
            continue
        g = unit_group(k)
        if g not in groups:
            if len(groups) == 2:
                continue
            groups.append(g)
        out.append((k, "L" if groups.index(g) == 0 else "R"))
    return out


def cell_id(keys: List[str]) -> str:
    return "+".join(keys)


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
    out = []
    for i in range(n):                                  # 5 張以上：兩欄一直往下，最後落單的佔滿一列
        r, c = divmod(i, 2)
        out.append(full(r) if (i == n - 1 and c == 0) else (r, c, 1, 1))
    return out


def layout_positions(n: int, mode: str = "auto") -> List[tuple]:
    """auto = 九宮格排法；rows = 上下一張一張；cols = 左右並排。→ (row, col, rowspan, colspan)，2 欄。"""
    if mode == "rows":
        return [(i, 0, 1, 2) for i in range(n)]
    if mode == "cols":
        return [(0, i, 1, 1) for i in range(n)]
    return grid_positions(n)


def migrate(groups: Any, src: str) -> List[List[str]]:
    """舊設定（只有格式，例如 [["db", "phase"]]）→ 加上來源（S21:db…）。"""
    out = []
    for g in groups or []:
        if not isinstance(g, (list, tuple)):
            continue
        out.append([k if ":" in k else tkey(src, k) for k in g if isinstance(k, str) and valid_key(k)])
    return [g for g in out if g]


# ---- 一張圖 ----------------------------------------------------------------------------------
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
            self.setText("新的圖放這邊" if edge in ("top", "bottom") else "")
        self.setStyleSheet(f"background:{rgba}; border:2px solid {theme.c('accent')}; border-radius:6px; "
                           f"color:{theme.c('text')}; font-weight:600;")
        self.raise_()
        self.show()


class PlotCell(QtWidgets.QFrame):
    """一張圖（VNA 的 diagram）：標題列 ⠿ = 拖整張圖；trace 名稱 = 點一下選成目前的 trace、拖曳 = 只拖那條。"""

    dropped = pyqtSignal(str, str, str)          # 拖來的東西（cell:<id> / trace:<key>）、放到的圖 id、位置
    split_trace = pyqtSignal(str)
    activated = pyqtSignal(str)
    menu_requested = pyqtSignal(str)

    def __init__(self, keys: List[str], owner: "TracePlots") -> None:
        super().__init__()
        self.owner = owner
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
        for i, k in enumerate(self.keys):
            chip = QtWidgets.QLabel(f"Trc{owner.trace_number(k)} {trace_label(k)}")
            chip.setObjectName("traceChip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip("點一下 = 選這條 trace（上面的 S 參數 / 格式按鈕改它）\n"
                            "拖到另一張圖中間 = 疊上去；拖到邊緣 = 自己一張放旁邊；右鍵 = 換來源 / 格式、分開、刪除")
            chip.mousePressEvent = lambda ev, w=chip: self._press_at(ev, w)
            chip.mouseMoveEvent = lambda ev, k=k, w=chip: self._move_at(ev, f"trace:{k}", w)
            chip.mouseReleaseEvent = lambda ev, k=k: self._release(ev, k)
            chip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            chip.customContextMenuRequested.connect(lambda _p, k=k: self.menu_requested.emit(k))
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
        from .canvas import TraceCanvas

        self.canvas = TraceCanvas(owner.view, lambda k: FORMATS[key_fmt(k)][1], lambda k: FORMATS[key_fmt(k)][2],
                                  owner.short_label, polar_fn=is_polar, smith_fn=lambda k: key_fmt(k) == "smith")
        self.canvas.set_series(overlay_sides(self.keys) if len(self.keys) > 1 else [(self.keys[0], "L")])
        v.addWidget(self.canvas, 1)
        for w in [self.canvas, *self.canvas.findChildren(QtWidgets.QWidget)]:
            w.setAcceptDrops(False)                      # 拖放交給整張圖（PlotCell）處理
        self.pw = self.canvas.pw
        self.plot = self.canvas.plot
        self._press: Optional[QtCore.QPoint] = None
        self._dragged = False
        self.hint = _DropHint(self)
        self.set_title("")
        self._hl(False)

    @property
    def curve(self):
        return self.canvas.curves.get(self.keys[0]) or next(iter(self.canvas.curves.values()), None)

    def set_title(self, tr: str) -> None:
        self.head.setText(f"　{tr}" if tr else "")

    def _hl(self, on: bool) -> None:
        active = self.owner.active
        self.setStyleSheet(f"#plotcell {{ border:1px {'dashed' if on else 'solid'} "
                           f"{theme.c('accent') if on or active in self.keys else theme.c('border')}; "
                           "border-radius:6px; }")
        self.head.setStyleSheet(f"color:{theme.c('muted')}; font-size:11px;")
        self.grip.setStyleSheet(f"color:{theme.c('muted')}; font-size:13px;")
        for i, (k, chip) in enumerate(self.chips.items()):
            col = theme.c(CHIP_COLORS[i % len(CHIP_COLORS)])
            if k == active:
                chip.setStyleSheet(f"background:{col}; color:{theme.c('window')}; border:1px solid {col}; "
                                   "border-radius:7px; padding:0 6px; font-size:11px; font-weight:700;")
            else:
                chip.setStyleSheet(f"color:{col}; border:1px solid {col}; border-radius:7px; padding:0 6px; "
                                   "font-size:11px; font-weight:600;")

    # ---- 拖曳 ---------------------------------------------------------------------
    def _press_at(self, ev, w) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press = ev.position().toPoint()
            self._dragged = False

    def _release(self, ev, k: str) -> None:
        if ev.button() == Qt.MouseButton.LeftButton and not self._dragged:
            self.activated.emit(k)
        self._press = None

    def _move_at(self, ev, payload: str, w) -> None:
        if self._press is None or (ev.position().toPoint() - self._press).manhattanLength() < 8:
            return
        self._press = None
        self._dragged = True
        md = QtCore.QMimeData()
        md.setData(PLOT_MIME, payload.encode())
        drag = QtGui.QDrag(self)
        drag.setMimeData(md)
        drag.setPixmap(w.grab() if payload.startswith("trace:") else self.bar.grab())
        drag.exec(Qt.DropAction.MoveAction)

    def zone_at(self, pos: QtCore.QPoint) -> tuple:
        """(位置, 邊)：中間 1/2 以內 = 疊圖；否則依最近的邊 = 前面 / 後面。"""
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


# ---- 全部的圖 ----------------------------------------------------------------------------------
class TracePlots(QtWidgets.QWidget):
    """好幾張圖（diagram），每張可以疊幾條 trace；拖曳 trace / 圖重新排列；版面：自動（九宮格）、上下、左右。
    每張圖都可以拖曲線上下移動、在軸上拖曳縮放、雙擊加 marker。"""

    order_changed = pyqtSignal(list)              # trace 的順序
    groups_changed = pyqtSignal(list)             # 圖的分組（每組一張圖）
    active_changed = pyqtSignal(str)              # 目前的 trace
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
        self.active = ""
        self.sources: List[str] = []              # 右鍵選單可以換的來源
        self.numbers: Dict[str, int] = {}         # trace 編號（Trc1、Trc2…，像 VNA）
        self._syncing = False
        from ....core.axis_math import AxisMath

        self.math = AxisMath()             # 0.0.30：x / y 軸運算（y = y**2、x = log(x)…）
        self.math.error = ""

    # ---- 畫圖用 --------------------------------------------------------------------
    def view(self, k: str, x, data):
        xx, yy = trace_data(k, x, data)
        if np.size(xx) != np.size(yy):                    # 這個通道的點數跟頻率軸不一樣：不畫
            return np.array([]), np.array([])
        m = self.math
        if m is None or not m.active or is_polar(k) or not len(xx):
            return xx, yy
        try:
            out = m.apply(xx, yy)
            m.error = ""
            return out
        except Exception as e:  # noqa: BLE001 — 算不出來：畫原本的，並在面板上顯示錯誤
            m.error = str(e)
            return xx, yy

    def short_label(self, k: str) -> str:
        return trace_label(k)

    def trace_number(self, k: str) -> int:
        if k not in self.numbers:
            used = set(self.numbers.values())
            self.numbers[k] = next(i for i in range(1, 999) if i not in used)
        return self.numbers[k]

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

    traces = views

    @property
    def mode(self) -> str:
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

    def sources_used(self) -> List[str]:
        return list(dict.fromkeys(key_src(k) for k in self.views if key_src(k)))

    def _cell(self, keys: List[str]) -> PlotCell:
        cid = cell_id(keys)
        c = self.cells.get(cid)
        if c is None:
            c = PlotCell(keys, self)
            c.dropped.connect(self._on_drop)
            c.split_trace.connect(self.split_out)
            c.activated.connect(self.set_active)
            c.menu_requested.connect(self._trace_menu)
            c.canvas.set_points(self.points)
            if not any(is_polar(k) for k in keys):
                c.canvas.vb.sigXRangeChanged.connect(lambda _vb, r, cid=cid: self._sync_x(cid, r))
            self.cells[cid] = c
        return c

    # ---- 設定 ---------------------------------------------------------------------
    def set_points(self, on: bool) -> None:
        self.points = bool(on)
        for c in self.cells.values():
            c.canvas.set_points(on)

    def set_sources(self, sources: List[str]) -> None:
        self.sources = list(sources)

    def set_groups(self, groups: List[List[str]], emit: bool = False) -> None:
        groups = normalize_groups(groups)
        if groups == self.groups:
            return
        self.groups = groups
        for k in list(self.numbers):
            if k not in self.views:
                self.numbers.pop(k)
        for k in self.views:
            self.trace_number(k)
        if self.active not in self.views:
            self.active = self.views[0] if self.views else ""
        self._relayout()
        if self.last is not None:
            self.update_data(*self.last)
        if emit:
            self.order_changed.emit(self.views)
            self.groups_changed.emit([list(g) for g in self.groups])
        self.active_changed.emit(self.active)

    def set_mode(self, mode: str) -> None:
        """overlay = 全部疊在一張（疊不下的另開）；grid = 每條一張。"""
        views = self.views
        self.set_groups([views] if mode == "overlay" else [[k] for k in views], emit=True)

    def set_views(self, views: List[str]) -> None:
        """指定的 trace：保留原本的分組，新的自己一張圖（加在最後）。"""
        views = [k for k in dict.fromkeys(views) if valid_key(k)][:MAX_TRACES]
        keep = [[k for k in g if k in views] for g in self.groups]
        have = {k for g in keep for k in g}
        self.set_groups([g for g in keep if g] + [[k] for k in views if k not in have])

    def set_layout_mode(self, mode: str) -> None:
        if mode in ("auto", "rows", "cols") and mode != self.layout_mode:
            self.layout_mode = mode
            self._relayout()

    def set_active(self, k: str) -> None:
        if k not in self.views or k == self.active:
            if k in self.views:
                self.active_changed.emit(k)
            return
        self.active = k
        for c in self.cells.values():
            c._hl(False)
        self.active_changed.emit(k)

    # ---- 新增 / 刪除 / 改 trace（VNA 的 Trc+、Meas、Format）----------------------------------
    def add_trace(self, src: str, fmt: str, new_cell: bool = False) -> Optional[str]:
        """加一條 trace：疊在目前那張圖（疊不下或 new_cell → 自己一張放在旁邊），並選成目前的 trace。"""
        k = tkey(src, fmt)
        if not valid_key(k):
            return None
        if k in self.views:
            self.set_active(k)
            self.message.emit(f"已經有 {trace_label(k)}")
            return k
        if len(self.views) >= MAX_TRACES:
            self.message.emit(f"最多 {MAX_TRACES} 條 trace，請先刪掉一條")
            return None
        groups = [list(g) for g in self.groups]
        i = next((i for i, g in enumerate(groups) if self.active in g), len(groups) - 1)
        if not new_cell and groups and can_overlay(groups[i] + [k]):
            groups[i].append(k)
        else:
            groups.insert(i + 1, [k])
        self.active = k
        self.set_groups(groups, emit=True)
        return k

    def remove_trace(self, k: str) -> bool:
        if k not in self.views:
            return False
        if len(self.views) == 1:
            self.message.emit("至少要留一條 trace")
            return False
        groups = [[x for x in g if x != k] for g in self.groups]
        if self.active == k:
            self.active = ""
        self.set_groups([g for g in groups if g], emit=True)
        return True

    def retype(self, k: str, src: Optional[str] = None, fmt: Optional[str] = None) -> Optional[str]:
        """換 trace 的來源（S 參數）或格式；放不進原本那張圖 → 自己一張。"""
        if k not in self.views:
            return None
        nk = tkey(key_src(k) if src is None else src, key_fmt(k) if fmt is None else fmt)
        if nk == k or not valid_key(nk):
            return k
        if nk in self.views:                               # 已經有一樣的：選它就好
            self.set_active(nk)
            return nk
        groups = [list(g) for g in self.groups]
        gi = next(i for i, g in enumerate(groups) if k in g)
        g = groups[gi]
        pos = g.index(k)
        rest = g[:pos] + g[pos + 1:]
        num = self.numbers.pop(k, None)
        if num is not None:
            self.numbers[nk] = num                         # 編號跟著 trace（Trc2 換格式還是 Trc2）
        if can_overlay(rest + [nk]):
            g[pos] = nk
        else:
            g.pop(pos)
            groups.insert(gi + 1, [nk])
            self.message.emit(f"{trace_label(nk)} 跟同一張圖的 trace 疊不起來，另外放一張")
        self.active = nk
        self.set_groups([g for g in groups if g], emit=True)
        return nk

    def _trace_menu(self, k: str) -> None:
        m = QtWidgets.QMenu(self)
        if self.sources:
            sm = m.addMenu("來源")
            for s in self.sources:
                a = sm.addAction(s, lambda s=s: self.retype(k, src=s))
                a.setCheckable(True)
                a.setChecked(s == key_src(k))
        fm = m.addMenu("格式")
        for f, (text, *_r) in FORMATS.items():
            a = fm.addAction(text, lambda f=f: self.retype(k, fmt=f))
            a.setCheckable(True)
            a.setChecked(f == key_fmt(k))
        m.addSeparator()
        g = next((g for g in self.groups if k in g), [])
        a = m.addAction("分開成獨立的圖", lambda: self.split_out(k))
        a.setEnabled(len(g) > 1)
        a = m.addAction("刪除這條 trace", lambda: self.remove_trace(k))
        a.setEnabled(len(self.views) > 1)
        m.exec(QtGui.QCursor.pos())

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
        """把一條 trace 移到另一張圖（中間 = 疊上去；邊緣 = 自己一張放在旁邊）。"""
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
                self.message.emit("這張圖疊不下：直角座標最多兩種單位（左右兩軸），Polar / Smith 只能跟 Polar / Smith 疊")
                return False
            src.remove(k)
            tgt.append(k)
        else:
            if src == [k] and src is tgt:
                return False
            src.remove(k)
            ti = next(i for i, g in enumerate(groups) if g is tgt)
            groups.insert(ti if zone == DROP_BEFORE else ti + 1, [k])
        self.active = k
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
                self.message.emit("這兩張圖疊不起來：直角座標最多兩種單位（左右兩軸），Polar / Smith 只能跟 Polar / Smith 疊")
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
            c._hl(False)
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
                if cid != src and not any(is_polar(k) for k in g):
                    self.cells[cid].plot.setXRange(float(r[0]), float(r[1]), padding=0)
        finally:
            self._syncing = False

    def update_data(self, tr: str, x, data, unit: str) -> None:
        """data = {來源: 複數陣列}（或單一陣列）。"""
        self.last = (tr, x, data, unit)
        m = self.math
        for g in self.groups:
            c = self._cell(g)
            c.canvas.x_is_freq = m.fx is None
            c.canvas.update_data(tr, x, data, unit)
            own = ", ".join(dict.fromkeys(key_src(k) for k in g if key_src(k)))
            head = own if (tr or "") == ", ".join(self.sources_used()) else "　".join(x for x in (own, tr or "") if x)
            c.set_title((head or tr or "") + (f"　·　{self.math_text()}" if m.active else ""))
            polar = any(is_polar(k) for k in g)
            if m.fx is not None and not polar:
                c.canvas.plot.setLabel("bottom", f"x = {m.x_text}（x = 頻率 Hz）", units="")
            if len(g) == 1 and not polar:
                y0 = FORMATS[key_fmt(g[0])][1]
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


class FormatBar(QtWidgets.QWidget):
    """VNA 面板右邊那排格式鍵（dB Mag、Phase、Smith…）＋「＋ Trace」「－ Trace」：改的是目前的 trace。"""

    def __init__(self, plots: TracePlots, add_source: Callable[[], str], parent=None) -> None:
        super().__init__(parent)
        self.plots = plots
        self.add_source = add_source
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.group = QtWidgets.QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: Dict[str, QtWidgets.QPushButton] = {}
        for f, (text, *_r) in FORMATS.items():
            b = QtWidgets.QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(f"目前的 trace 改成 {text}（點圖上的 trace 名稱換一條）")
            b.clicked.connect(lambda _on, f=f: self._format(f))
            self.group.addButton(b)
            self.buttons[f] = b
            row.addWidget(b)
        row.addSpacing(8)
        self.b_add = QtWidgets.QPushButton("＋ Trace")
        self.b_add.setToolTip("加一條 trace（複製目前的 trace，疊在同一張圖；疊不下就放旁邊）——像 VNA 的 Trc+")
        self.b_add.clicked.connect(self.add)
        self.b_del = QtWidgets.QPushButton("刪除")
        self.b_del.setToolTip("刪除目前的 trace（右鍵 trace 名稱也可以）")
        self.b_del.clicked.connect(lambda: self.plots.remove_trace(self.plots.active))
        row.addWidget(self.b_add)
        row.addWidget(self.b_del)
        plots.active_changed.connect(self.sync)
        self.sync(plots.active)

    def _format(self, f: str) -> None:
        if self.plots.active:
            self.plots.retype(self.plots.active, fmt=f)

    def add(self) -> None:
        k = self.plots.active
        src = key_src(k) if k else self.add_source()
        fmt = key_fmt(k) if k else "db"
        others = [f for f in FORMATS if tkey(src, f) not in self.plots.views]
        if tkey(src, fmt) in self.plots.views and others:
            fmt = "phase" if "phase" in others else others[0]          # 複製一條：預設換成另一個格式
        self.plots.add_trace(src, fmt)

    def sync(self, k: str) -> None:
        f = key_fmt(k) if k else ""
        for key, b in self.buttons.items():
            b.setChecked(key == f)
        self.b_del.setEnabled(len(self.plots.views) > 1)
