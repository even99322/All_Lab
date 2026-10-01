"""淺色 / 深色外觀（0.0.6）。

用法
----
* 啟動時 ``theme.apply(app)``：依 ``settings.yaml app.theme``（light | dark | system）設定 Qt 調色盤。
* 需要顏色的地方用語意名稱取色：``theme.c("err")``（字串，給 stylesheet / HTML）、``theme.qc("text")``（QColor）。
  不要再寫死 ``#495057`` 這類顏色 —— 深色模式會看不見。
* 一般文字 / 背景優先用 Qt 調色盤（stylesheet 寫 ``palette(text)``、``palette(base)``），切換時會自動更新。
* 自己畫的東西（流程圖、pyqtgraph 圖表）用 ``theme.on_change(obj, fn)`` 登記：現在呼叫一次，之後每次切換再呼叫；
  obj 被刪除後自動取消登記。
* ``theme.set_mode("dark")`` 即時切換所有視窗，並寫回 ``LAB/settings.yaml``。
* 淡色底（提示框、修改中欄位）用 ``theme.tint("ok")`` —— 半透明，兩種模式都適用。

新增顏色：在 ``LIGHT`` 與 ``DARK`` 兩個表都加同一個名稱。
"""
from __future__ import annotations

import logging
import weakref
from typing import Any, Callable, Dict, List, Optional, Tuple

from PyQt6 import QtCore, QtGui, QtWidgets

MODES = ("light", "dark", "system")
MODE_LABEL = {"light": "☀ 淺色", "dark": "🌙 深色", "system": "🖥 跟隨系統"}

#: 語意顏色（兩個表的名稱必須相同）
LIGHT: Dict[str, str] = {
    "window": "#f3f5f8", "base": "#ffffff", "alt": "#f7f9fc", "button": "#f6f8fb",
    "panel": "#ffffff", "panel_head": "#ffffff", "wrap": "#f3f5f8", "border": "#e2e6ec",
    "text": "#1f2937", "title": "#111827", "text2": "#4b5563", "muted": "#6b7280", "disabled": "#9aa3b0",
    "accent": "#2563eb", "curve": "#2563eb", "ok": "#16a34a", "warn": "#d97706", "orange": "#f08c00",
    "err": "#dc2626", "hl": "#0d9488",
    "canvas": "#f6f8fb", "grid": "#dfe4ea", "node": "#ffffff", "node_start": "#f1f4f8",
    "node_border": "#cfd6e0", "node_text": "#4b5563", "port": "#8b93a1", "edge": "#8b93a1",
    "plot_bg": "#ffffff", "plot_fg": "#4b5563", "highlight": "#2563eb", "tooltip": "#ffffff",
}
DARK: Dict[str, str] = {
    "window": "#16181d", "base": "#1a1c21", "alt": "#1e2126", "button": "#23262d",
    "panel": "#1d2026", "panel_head": "#1d2026", "wrap": "#16181d", "border": "#2c3038",
    "text": "#e6e8eb", "title": "#f3f4f6", "text2": "#c3c8d0", "muted": "#8b93a1", "disabled": "#5f6673",
    "accent": "#3b82f6", "curve": "#60a5fa", "ok": "#22c55e", "warn": "#f59e0b", "orange": "#f08c00",
    "err": "#ef4444", "hl": "#2dd4bf",
    "canvas": "#16181d", "grid": "#252830", "node": "#1f2228", "node_start": "#262a31",
    "node_border": "#3a3f48", "node_text": "#c3c8d0", "port": "#8b93a1", "edge": "#6b7280",
    "plot_bg": "#1a1c21", "plot_fg": "#aab1bd", "highlight": "#2f5fb3", "tooltip": "#23262d",
}
#: 半透明底色的基準色（RGB）與透明度
_TINTS = {"ok": ((64, 192, 87), 0.14, 0.45), "warn": ((250, 176, 5), 0.22, 0.55), "err": ((250, 82, 82), 0.18, 0.5),
          "info": ((34, 139, 230), 0.12, 0.45)}

log = logging.getLogger("labcontrol")


class _Notifier(QtCore.QObject):
    changed = QtCore.pyqtSignal(bool)        # is_dark


notifier: Optional[_Notifier] = None
_mode = "light"
_dark = False
_watchers: List[Tuple[Any, Callable[[Any], None]]] = []
_applying = False


# ---- 查詢 -----------------------------------------------------------------------
def mode() -> str:
    """目前設定的模式：light | dark | system。"""
    return _mode


def is_dark() -> bool:
    return _dark


def colors() -> Dict[str, str]:
    return DARK if _dark else LIGHT


def c(name: str) -> str:
    """語意顏色（#rrggbb）。"""
    return colors()[name]


def qc(name: str) -> QtGui.QColor:
    return QtGui.QColor(c(name))


def readable(color: Any) -> QtGui.QColor:
    """深色模式下把太暗的顏色（方塊顏色、迴圈顏色）調亮，讓文字 / 線條看得見；淺色模式原樣回傳。"""
    col = QtGui.QColor(color)
    if _dark:
        h, s_, l, a = col.getHslF()
        if l < 0.62:
            col = QtGui.QColor.fromHslF(max(h, 0.0), min(1.0, s_ * 0.95), 0.62 + (l * 0.1), a)
    return col


def tint(name: str = "info", border: bool = True, radius: int = 6, padding: str = "6px") -> str:
    """半透明提示框 stylesheet（淺色 / 深色都適用）。"""
    (r, g, b), a_bg, a_bd = _TINTS[name]
    s = f"background:rgba({r},{g},{b},{a_bg:.2f});"
    if border:
        s += f" border:1px solid rgba({r},{g},{b},{a_bd:.2f}); border-radius:{radius}px;"
    if padding:
        s += f" padding:{padding};"
    return s


def span(text: str, color: str) -> str:
    """HTML 片段：<span style='color:...'>text</span>，color 可以是語意名稱。"""
    return f"<span style='color:{c(color) if color in LIGHT else color}'>{text}</span>"


# ---- 登記重繪 --------------------------------------------------------------------
def on_change(obj: Any, fn: Callable[[Any], None], call_now: bool = True) -> None:
    """obj 存在時，每次切換外觀呼叫 fn(obj)。"""
    try:
        ref = weakref.ref(obj)
    except TypeError:
        ref = (lambda o=obj: o)          # 不支援 weakref 的物件：一直保留
    _watchers.append((ref, fn))
    if call_now:
        fn(obj)


def _notify() -> None:
    alive = []
    for ref, fn in list(_watchers):
        o = ref()
        if o is None:
            continue
        try:
            fn(o)
            alive.append((ref, fn))
        except RuntimeError:              # 底層 C++ 物件已刪除
            continue
        except Exception:  # noqa: BLE001 - 一個元件失敗不影響其他
            log.exception("套用外觀失敗：%r", o)
            alive.append((ref, fn))
    _watchers[:] = alive
    if notifier is not None:
        notifier.changed.emit(_dark)


def style_plot(pw: Any, *curves: Tuple[Any, str]) -> Any:
    """pyqtgraph PlotWidget 套用外觀；curves 是 (曲線, 顏色名稱) 讓線色也跟著換。"""
    import pyqtgraph as pg

    def apply(w: Any) -> None:
        w.setBackground(c("plot_bg"))
        pi = w.getPlotItem()
        for ax in ("left", "bottom", "right", "top"):
            a = pi.getAxis(ax)
            a.setPen(pg.mkPen(c("plot_fg")))
            a.setTextPen(pg.mkPen(c("plot_fg")))
        for crv, name in curves:
            try:
                opts = getattr(crv, "opts", {})
                if opts.get("pen") is not None and not (hasattr(opts["pen"], "style") and
                                                        opts["pen"].style() == QtCore.Qt.PenStyle.NoPen):
                    crv.setPen(pg.mkPen(c(name), width=1.5))
                if opts.get("symbol") is not None:
                    crv.setSymbolBrush(c(name))
            except RuntimeError:
                pass

    on_change(pw, apply)
    return pw


# ---- 調色盤 -----------------------------------------------------------------------
def palette(dark: bool) -> QtGui.QPalette:
    return _style().palette(dark)


def _style():
    """共用外觀（labmonitor/style.py，Lab Control 與 Monitor 相同）。"""
    import importlib
    import sys
    from pathlib import Path

    root = str(Path(__file__).resolve().parents[3])
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module("labmonitor.style")


def _system_is_dark() -> bool:
    try:
        return QtGui.QGuiApplication.styleHints().colorScheme() == QtCore.Qt.ColorScheme.Dark
    except Exception:  # noqa: BLE001 - Qt < 6.5
        return False


def _on_system_changed(*_a: Any) -> None:
    if _mode == "system" and not _applying:
        apply(mode="system")


def apply(app: Optional[QtWidgets.QApplication] = None, mode: Optional[str] = None) -> bool:
    """套用外觀（不寫檔）。mode 省略時讀 settings.yaml app.theme。回傳是否為深色。"""
    global _mode, _dark, notifier, _applying
    from ...settings import setting

    app = app or QtWidgets.QApplication.instance()
    m = str(mode or setting("app.theme", "light") or "light").lower()
    if m not in MODES:
        log.warning("settings.yaml app.theme=%r 不認得，改用 light", m)
        m = "light"
    first = notifier is None
    if first:
        notifier = _Notifier()
        try:
            QtGui.QGuiApplication.styleHints().colorSchemeChanged.connect(_on_system_changed)
        except Exception:  # noqa: BLE001
            pass
    _applying = True
    try:
        hints = QtGui.QGuiApplication.styleHints()
        if hasattr(hints, "unsetColorScheme") and m == "system":
            hints.unsetColorScheme()
        _mode = m
        _dark = _system_is_dark() if m == "system" else (m == "dark")
        if hasattr(hints, "setColorScheme") and m != "system":   # Qt 6.8：Windows 標題列也跟著變
            hints.setColorScheme(QtCore.Qt.ColorScheme.Dark if _dark else QtCore.Qt.ColorScheme.Light)
        if app is not None:
            if app.style().name().lower() != "fusion":
                app.setStyle("Fusion")
            app.setPalette(palette(_dark))
            # 重新套用 stylesheet → 所有 palette(...) 參照重新計算
            app.setStyleSheet(_app_stylesheet())
    finally:
        _applying = False
    _notify()
    return _dark


def _app_stylesheet() -> str:
    return _style().qss(_dark)


def set_mode(m: str, save: bool = True) -> bool:
    """切換外觀並（預設）寫回 LAB/settings.yaml app.theme。"""
    dark = apply(mode=m)
    if save:
        try:
            from ...core import labfile
            from ...settings import settings

            s = settings()
            s.set("app.theme", m)
            if s.path is not None:
                labfile.set_value(s.path, "app.theme", m)
        except Exception as e:  # noqa: BLE001 - 寫檔失敗不影響切換
            log.warning("無法把外觀寫回 settings.yaml：%s", e)
    return dark


def toggle() -> bool:
    """淺色 ↔ 深色（跟隨系統時切到相反的固定模式）。"""
    return set_mode("light" if _dark else "dark")


# ---- UI 元件 ----------------------------------------------------------------------
def make_menu(parent: QtWidgets.QWidget, title: str = "外觀") -> QtWidgets.QMenu:
    """「外觀」子選單：淺色 / 深色 / 跟隨系統（單選，打勾顯示目前設定）。"""
    menu = QtWidgets.QMenu(title, parent)
    group = QtGui.QActionGroup(menu)
    acts = {}
    for m in MODES:
        a = menu.addAction(MODE_LABEL[m])
        a.setCheckable(True)
        group.addAction(a)
        a.triggered.connect(lambda _c=False, m=m: set_mode(m))
        acts[m] = a

    def sync(_m: Any) -> None:
        acts[_mode].setChecked(True)
    on_change(menu, sync)
    return menu


def make_toggle_action(parent: QtWidgets.QWidget) -> QtGui.QAction:
    """工具列按鈕：顯示「切到另一種」的圖示（淺色時 🌙、深色時 ☀）。"""
    act = QtGui.QAction(parent)
    act.triggered.connect(lambda: toggle())

    def sync(a: QtGui.QAction) -> None:
        a.setText("☀" if _dark else "🌙")
        a.setToolTip(("切換為淺色模式" if _dark else "切換為深色模式")
                     + ("（目前跟隨系統）" if _mode == "system" else ""))
    on_change(act, sync)
    return act
