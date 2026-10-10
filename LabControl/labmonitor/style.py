"""共用外觀：Lab Control 與 Lab Control Monitor 都用這一份；0.0.19 起和讀檔模塊（LabLogViewer 2.0）同一套
（同樣的淺色 / 深色顏色、玻璃按鈕、分頁、輸入框、樹狀與數字框的箭頭）。

只依賴 PyQt6。顏色集中在 DARK / LIGHT；按鈕樣式用屬性：
    btn.setProperty("primary", True)   # 主要動作（藍底）
    btn.setProperty("danger", True)    # 危險動作（紅框）
    label.setProperty("role", "h1")    # 標題；"muted" 次要文字；"card" 卡片框（QFrame）
"""
from __future__ import annotations

from typing import Dict

# 顏色與讀檔模塊（LabLogViewer 2.0 app/palette.py 的 APP_LIGHT / APP_DARK）相同，兩個程式看起來是同一套
DARK: Dict[str, str] = {
    "bg": "#0B0F14", "surface": "#141A21", "surface2": "#202B35", "input": "#10171E", "list": "#10171E",
    "list_alt": "#1B2630", "border": "#35424E", "strong": "#4A5865", "text": "#E6E6E6", "muted": "#A5B0BA",
    "faint": "#66717C", "accent": "#21C6E8", "accent_hover": "#5AD6EF", "accent_soft": "rgba(33,198,232,0.18)",
    "sel": "#2D6E77", "sel_text": "#E6E6E6", "ok": "#22c55e", "warn": "#f59e0b", "err": "#ef4444",
    "err_soft": "rgba(239,68,68,0.55)", "tooltip": "#141A21", "toolbar": "#1B2630", "arrow": "#A5B0BA",
}
LIGHT: Dict[str, str] = {
    "bg": "#F7F4EE", "surface": "#FFFFFF", "surface2": "#F8FAFC", "input": "#FFFFFF", "list": "#FFFFFF",
    "list_alt": "#F1F3F5", "border": "#BDCDE1", "strong": "#A7B9CF", "text": "#1D1D1F", "muted": "#6B7075",
    "faint": "#A3A8AD", "accent": "#297FA8", "accent_hover": "#3592BF", "accent_soft": "rgba(41,127,168,0.14)",
    "sel": "#D7E9FA", "sel_text": "#1D1D1F", "ok": "#166534", "warn": "#b45309", "err": "#b91c1c",
    "err_soft": "rgba(185,28,28,0.5)", "tooltip": "#FFFFFF", "toolbar": "#F1F3F5", "arrow": "#6B7075",
}
#: 玻璃按鈕（讀檔模塊 BUTTON_GLASS）：(顏色, 透明度) 的漸層
_GLASS = {
    False: {"body": (("#FFFFFF", 0.98), ("#FFFFFF", 0.80), ("#EEF3F7", 0.78), ("#E4EBF1", 0.92)),
            "edge": ("#0F1720", 0.16), "rim": ("#FFFFFF", 1.0), "low_edge": ("#0F1720", 0.26),
            "hover": (("#FFFFFF", 1.0), ("#E9F2F8", 0.95)), "pressed": (0.20, 0.08)},
    True: {"body": (("#FFFFFF", 0.16), ("#FFFFFF", 0.09), ("#FFFFFF", 0.05), ("#FFFFFF", 0.08)),
           "edge": ("#FFFFFF", 0.14), "rim": ("#FFFFFF", 0.30), "low_edge": ("#000000", 0.45),
           "hover": (("#FFFFFF", 0.22), ("#FFFFFF", 0.10)), "pressed": (0.16, 0.30)},
}


def tokens(dark: bool) -> Dict[str, str]:
    return DARK if dark else LIGHT


def _rgba(color: str, alpha: float) -> str:
    c = color.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{round(alpha * 255)})"


_GLYPHS = {
    "branch_closed": '<path d="M4.5 2.5 L8 6 L4.5 9.5"/>',
    "branch_open": '<path d="M2.5 4.5 L6 8 L9.5 4.5"/>',
    "spin_up": '<path d="M2.5 7.5 L6 4 L9.5 7.5"/>',
    "spin_down": '<path d="M2.5 4.5 L6 8 L9.5 4.5"/>',
    "combo_down": '<path d="M2.5 4.5 L6 8 L9.5 4.5"/>',
}


def _glyph(kind: str, color: str) -> str:
    """小箭頭（樹狀展開、數字框上下、下拉）畫成 SVG 檔，顏色跟著外觀（同讀檔模塊）。"""
    import tempfile
    from pathlib import Path

    folder = Path(tempfile.gettempdir()) / "labcontrol-glyphs"
    path = folder / f"{kind}_{color.lstrip('#').lower()}.svg"
    try:
        if not path.exists():
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
                            f'<g fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round" '
                            f'stroke-linejoin="round">{_GLYPHS[kind]}</g></svg>', encoding="utf-8")
    except OSError:
        return ""
    return path.as_posix()


def _glass_buttons(t: Dict[str, str], dark: bool) -> str:
    g = _GLASS[dark]
    top, upper, lower, bottom = (_rgba(*stop) for stop in g["body"])
    edge, rim, low = _rgba(*g["edge"]), _rgba(*g["rim"]), _rgba(*g["low_edge"])
    h_top, h_bot = (_rgba(*stop) for stop in g["hover"])
    p_top, p_bot = (_rgba(t["accent"], a) for a in g["pressed"])
    body = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {0}, stop:0.48 {1}, stop:0.52 {2}, stop:1 {3})"
    checked = body.format(*(_rgba(t["accent"], a) for a in (0.30, 0.22, 0.16, 0.24)))
    disabled = body.format(*(_rgba(t["surface2"], a) for a in (0.55, 0.45, 0.40, 0.45)))
    return f"""
QPushButton {{ color:{t['text']}; background-color:{body.format(top, upper, lower, bottom)};
    border:1px solid {edge}; border-top-color:{rim}; border-bottom-color:{low}; border-radius:10px;
    padding:4px 14px; min-height:18px; }}
QPushButton:hover {{ background-color:{body.format(h_top, h_top, h_bot, h_bot)};
    border-color:{_rgba(t['accent'], 0.55)}; border-top-color:{rim}; }}
QPushButton:pressed {{ background-color:{body.format(p_top, p_top, p_bot, p_bot)}; border-color:{_rgba(t['accent'], 0.75)}; }}
QPushButton:checked, QPushButton:checked:hover, QPushButton[primary="true"] {{ background-color:{checked};
    border:1px solid {t['accent']}; border-top-color:{rim}; }}
QPushButton[primary="true"] {{ font-weight:600; }}
QPushButton[primary="true"]:hover {{ border-color:{t['accent_hover']}; }}
QPushButton:disabled, QPushButton[primary="true"]:disabled {{ color:{t['faint']}; background-color:{disabled};
    border-color:{_rgba(t['border'], 0.6)}; font-weight:400; }}
QPushButton[danger="true"] {{ color:{t['err']}; border-color:{t['err_soft']}; }}
QPushButton[danger="true"]:hover {{ border-color:{t['err']}; }}
QPushButton:focus {{ outline:none; }}
QPushButton:default {{ border-color:{_rgba(t['accent'], 0.8)}; border-top-color:{rim}; }}
QToolButton {{ color:{t['text']}; background-color:{body.format(top, upper, lower, bottom)};
    border:1px solid {edge}; border-top-color:{rim}; border-bottom-color:{low}; border-radius:8px; padding:2px 6px; }}
QToolButton:hover {{ border-color:{_rgba(t['accent'], 0.55)}; }}
QToolButton:checked {{ background-color:{checked}; border:1px solid {t['accent']}; }}
QToolButton::menu-indicator {{ image:none; width:0px; }}
"""


def qss(dark: bool) -> str:
    t = tokens(dark)
    arrow = t["arrow"]
    closed, opened = _glyph("branch_closed", arrow), _glyph("branch_open", arrow)
    up, down, combo = _glyph("spin_up", arrow), _glyph("spin_down", arrow), _glyph("combo_down", arrow)
    return _glass_buttons(t, dark) + f"""
QWidget {{ color:{t['text']}; }}
QMainWindow, QDialog, QMenuBar {{ background:{t['bg']}; }}
QToolTip {{ color:{t['text']}; background:{t['tooltip']}; border:1px solid {t['border']}; padding:4px 6px; }}

QToolBar {{ background:{t['toolbar']}; border:none; border-bottom:1px solid {t['border']}; padding:4px 8px; spacing:4px; }}
QToolBar::separator {{ width:1px; background:{t['border']}; margin:5px 6px; }}
QToolBar QToolButton {{ background:transparent; border:1px solid transparent; border-radius:6px; padding:4px 9px; }}
QToolBar QToolButton:hover {{ background:{t['list_alt']}; border:1px solid {t['border']}; }}
QToolBar QToolButton:pressed, QToolBar QToolButton:checked, QToolBar QToolButton:checked:hover {{
    background:{t['sel']}; border:1px solid {t['accent']}; }}
QToolBar QToolButton:disabled {{ color:{t['faint']}; background:transparent; border:1px solid transparent; }}
QToolBar QToolButton[primary="true"] {{ background:{_rgba(t['accent'], 0.22)}; border:1px solid {t['accent']}; font-weight:600; }}
QToolBar QToolButton[primary="true"]:hover {{ background:{_rgba(t['accent'], 0.32)}; }}
QToolBar QToolButton[primary="true"]:disabled {{ background:transparent; border-color:{t['border']}; color:{t['faint']}; }}
QToolBar QToolButton[danger="true"] {{ color:{t['err']}; border-color:{t['err_soft']}; }}
QToolBar QLabel {{ color:{t['muted']}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QComboBox {{
    color:{t['text']}; background:{t['input']}; border:1px solid {t['border']}; border-radius:4px; padding:3px 6px;
    selection-background-color:{t['sel']}; selection-color:{t['sel_text']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QAbstractSpinBox:focus, QComboBox:focus {{ border:1px solid {t['accent']}; }}
QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {{ color:{t['faint']}; }}
QComboBox::drop-down {{ border:none; width:18px; }}
QComboBox::down-arrow {{ image:url("{combo}"); width:10px; height:10px; }}
QComboBox QAbstractItemView {{ background:{t['surface']}; border:1px solid {t['border']};
    selection-background-color:{t['sel']}; selection-color:{t['sel_text']}; }}
QAbstractSpinBox {{ padding-right:15px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ subcontrol-origin:border; width:14px; background:transparent;
    border-left:1px solid {t['border']}; }}
QAbstractSpinBox::up-button {{ subcontrol-position:top right; border-bottom:1px solid {t['border']}; }}
QAbstractSpinBox::down-button {{ subcontrol-position:bottom right; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background:{t['list_alt']}; }}
QAbstractSpinBox::up-button:pressed, QAbstractSpinBox::down-button:pressed {{ background:{t['sel']}; }}
QAbstractSpinBox::up-arrow {{ image:url("{up}"); width:9px; height:9px; }}
QAbstractSpinBox::down-arrow {{ image:url("{down}"); width:9px; height:9px; }}
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off,
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {{ image:none; }}

QCheckBox, QRadioButton {{ spacing:6px; }}
QCheckBox::indicator, QTreeView::indicator, QGroupBox::indicator {{ width:14px; height:14px; border:1px solid {t['strong']};
    border-radius:3px; background:{t['input']}; }}
QCheckBox::indicator:hover {{ border-color:{t['accent']}; }}
QCheckBox::indicator:checked, QTreeView::indicator:checked, QGroupBox::indicator:checked {{
    background:{t['accent']}; border-color:{t['accent']}; }}
QCheckBox::indicator:disabled {{ border-color:{t['border']}; }}
QRadioButton::indicator {{ width:14px; height:14px; border:1px solid {t['strong']}; border-radius:8px; background:{t['input']}; }}
QRadioButton::indicator:checked {{ background:{t['accent']}; border-color:{t['accent']}; }}

QTabWidget::pane {{ background:{t['surface']}; border:1px solid {t['border']}; top:-1px; }}
QTabBar::tab {{ color:{t['muted']}; background:{t['surface']}; border:1px solid {t['border']};
    border-bottom:2px solid {t['border']}; padding:4px 12px; }}
QTabBar::tab:selected {{ color:{t['text']}; background:{t['sel']}; border-bottom:2px solid {t['accent']}; }}
QTabBar::tab:hover:!selected {{ color:{t['text']}; background:{t['list_alt']}; }}

QTreeView, QListView, QTableView {{ color:{t['text']}; background:{t['list']}; alternate-background-color:{t['list_alt']};
    border:1px solid {t['border']}; selection-background-color:{t['sel']}; selection-color:{t['sel_text']}; outline:0; }}
QTreeView::item, QListView::item {{ padding:2px 0; }}
QTreeView::item:hover, QListView::item:hover {{ background:{t['list_alt']}; }}
QTreeView::item:selected, QListView::item:selected {{ background:{t['sel']}; color:{t['sel_text']}; }}
QTreeView::branch {{ background:transparent; border-image:none; }}
QTreeView::branch:has-children:!has-siblings:closed, QTreeView::branch:closed:has-children:has-siblings {{ image:url("{closed}"); }}
QTreeView::branch:open:has-children:!has-siblings, QTreeView::branch:open:has-children:has-siblings {{ image:url("{opened}"); }}
QHeaderView::section {{ color:{t['text']}; background:{t['list_alt']}; border:1px solid {t['border']}; padding:4px 6px; }}

QGroupBox {{ color:{t['text']}; background:{t['surface']}; border:1px solid {t['border']}; margin-top:14px;
    padding:10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin:margin; subcontrol-position:top left; left:6px; padding:0 3px; color:{t['muted']}; }}
QFrame[role="card"] {{ background:{t['surface']}; border:1px solid {t['border']}; border-radius:10px; }}
QLabel[role="h1"] {{ font-size:18px; font-weight:600; }}
QLabel[role="h2"] {{ font-size:14px; font-weight:600; }}
QLabel[role="muted"] {{ color:{t['muted']}; }}
QLabel[role="big"] {{ font-size:22px; font-weight:600; }}

QScrollBar:vertical {{ background:transparent; width:10px; margin:2px; }}
QScrollBar:horizontal {{ background:transparent; height:10px; margin:2px; }}
QScrollBar::handle {{ background:{t['border']}; border-radius:3px; min-height:24px; min-width:24px; }}
QScrollBar::handle:hover {{ background:{t['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}
QScrollArea {{ border:none; background:transparent; }}
QSplitter::handle {{ background:{t['border']}; }}
QSplitter::handle:hover {{ background:{t['accent']}; }}

QStatusBar {{ background:{t['bg']}; border-top:1px solid {t['border']}; color:{t['muted']}; }}
QStatusBar QLabel {{ color:{t['muted']}; }}
QMenu {{ color:{t['text']}; background:{t['surface']}; border:1px solid {t['border']}; padding:4px; }}
QMenu::item {{ padding:5px 22px 5px 14px; }}
QMenu::item:selected {{ background:{t['sel']}; color:{t['sel_text']}; }}
QMenu::separator {{ height:1px; background:{t['border']}; margin:4px 6px; }}
QProgressBar {{ border:1px solid {t['border']}; border-radius:4px; background:{t['list_alt']}; text-align:center;
    color:{t['text']}; min-height:14px; }}
QProgressBar::chunk {{ background:{t['accent']}; border-radius:3px; }}
QSlider::groove:horizontal {{ height:4px; background:{t['border']}; border-radius:2px; }}
QSlider::handle:horizontal {{ width:14px; margin:-6px 0; border-radius:7px; background:{t['accent']}; }}
"""


def palette(dark: bool):
    from PyQt6 import QtGui

    t = tokens(dark)
    P, G = QtGui.QPalette.ColorRole, QtGui.QPalette.ColorGroup
    pal = QtGui.QPalette()
    c = QtGui.QColor
    for role, key in ((P.Window, "bg"), (P.WindowText, "text"), (P.Base, "list"), (P.AlternateBase, "list_alt"),
                      (P.ToolTipBase, "tooltip"), (P.ToolTipText, "text"), (P.Text, "text"), (P.Button, "surface2"),
                      (P.ButtonText, "text"), (P.BrightText, "err"), (P.Link, "accent"), (P.LinkVisited, "accent"),
                      (P.Highlight, "sel"), (P.PlaceholderText, "muted"), (P.Mid, "border"), (P.Midlight, "surface2"),
                      (P.Dark, "strong"), (P.Light, "surface"), (P.Shadow, "bg")):
        pal.setColor(role, c(t[key]))
    pal.setColor(P.HighlightedText, c(t["sel_text"]))
    for r in (P.WindowText, P.Text, P.ButtonText):
        pal.setColor(G.Disabled, r, c(t["faint"]))
    return pal


def system_dark() -> bool:
    try:
        from PyQt6 import QtCore, QtGui

        return QtGui.QGuiApplication.styleHints().colorScheme() == QtCore.Qt.ColorScheme.Dark
    except Exception:  # noqa: BLE001
        return False


def apply(app, dark: bool) -> None:
    """套用到整個程式（Fusion + 調色盤 + stylesheet）。"""
    if app.style().name().lower() != "fusion":
        app.setStyle("Fusion")
    app.setPalette(palette(dark))
    app.setStyleSheet(qss(dark))


def mark(widget, **props) -> None:
    """設定樣式屬性並立即重新套用：mark(btn, primary=True)。"""
    for k, v in props.items():
        widget.setProperty(k, v)
    st = widget.style()
    st.unpolish(widget)
    st.polish(widget)
