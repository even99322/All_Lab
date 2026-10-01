"""共用外觀（簡約未來風，與 Lab APP 一致）：Lab Control 與 Lab Control Monitor 都用這一份。

只依賴 PyQt6。顏色集中在 DARK / LIGHT；按鈕樣式用屬性：
    btn.setProperty("primary", True)   # 主要動作（藍底）
    btn.setProperty("danger", True)    # 危險動作（紅框）
    label.setProperty("role", "h1")    # 標題；"muted" 次要文字；"card" 卡片框（QFrame）
"""
from __future__ import annotations

from typing import Dict

DARK: Dict[str, str] = {
    "bg": "#16181d", "surface": "#1d2026", "surface2": "#23262d", "input": "#1a1c21", "list": "#1a1c21",
    "list_alt": "#1e2126", "border": "#2c3038", "strong": "#3a3f48", "text": "#e6e8eb", "muted": "#8b93a1",
    "faint": "#5f6673", "accent": "#3b82f6", "accent_hover": "#5b95f7", "accent_soft": "rgba(59,130,246,0.18)",
    "sel": "#2f5fb3", "ok": "#22c55e", "warn": "#f59e0b", "err": "#ef4444", "err_soft": "rgba(239,68,68,0.55)",
    "tooltip": "#23262d",
}
LIGHT: Dict[str, str] = {
    "bg": "#f3f5f8", "surface": "#ffffff", "surface2": "#f6f8fb", "input": "#ffffff", "list": "#ffffff",
    "list_alt": "#f7f9fc", "border": "#e2e6ec", "strong": "#cfd6e0", "text": "#1f2937", "muted": "#6b7280",
    "faint": "#9aa3b0", "accent": "#2563eb", "accent_hover": "#3b74f0", "accent_soft": "rgba(37,99,235,0.12)",
    "sel": "#2563eb", "ok": "#16a34a", "warn": "#d97706", "err": "#dc2626", "err_soft": "rgba(220,38,38,0.55)",
    "tooltip": "#ffffff",
}


def tokens(dark: bool) -> Dict[str, str]:
    return DARK if dark else LIGHT


def qss(dark: bool) -> str:
    t = tokens(dark)
    return f"""
QWidget {{ color:{t['text']}; }}
QMainWindow, QDialog {{ background:{t['bg']}; }}
QToolTip {{ color:{t['text']}; background:{t['tooltip']}; border:1px solid {t['border']}; padding:4px 6px; }}

QToolBar {{ background:{t['surface']}; border:none; border-bottom:1px solid {t['border']}; padding:5px 8px; spacing:4px; }}
QToolBar::separator {{ width:1px; background:{t['border']}; margin:5px 6px; }}
QToolBar QToolButton {{ background:transparent; border:1px solid transparent; border-radius:6px; padding:5px 10px; }}
QToolBar QToolButton:hover {{ background:{t['surface2']}; border-color:{t['border']}; }}
QToolBar QToolButton:pressed, QToolBar QToolButton:checked {{ background:{t['accent_soft']}; border-color:{t['accent']}; }}
QToolBar QToolButton:disabled {{ color:{t['faint']}; }}
QToolBar QToolButton[primary="true"] {{ background:{t['accent']}; border-color:{t['accent']}; color:#ffffff; font-weight:600; }}
QToolBar QToolButton[primary="true"]:hover {{ background:{t['accent_hover']}; }}
QToolBar QToolButton[primary="true"]:disabled {{ background:{t['strong']}; border-color:{t['strong']}; color:{t['muted']}; }}
QToolBar QToolButton[danger="true"] {{ color:{t['err']}; border-color:{t['err_soft']}; }}
QToolBar QLabel {{ color:{t['muted']}; }}

QPushButton {{ background:{t['surface2']}; border:1px solid {t['strong']}; border-radius:6px; padding:6px 14px; }}
QPushButton:hover {{ border-color:{t['accent']}; }}
QPushButton:pressed, QPushButton:checked {{ background:{t['accent_soft']}; border-color:{t['accent']}; }}
QPushButton:disabled {{ color:{t['faint']}; border-color:{t['border']}; background:{t['surface']}; }}
QPushButton[primary="true"] {{ background:{t['accent']}; border-color:{t['accent']}; color:#ffffff; font-weight:600; }}
QPushButton[primary="true"]:hover {{ background:{t['accent_hover']}; border-color:{t['accent_hover']}; }}
QPushButton[primary="true"]:disabled {{ background:{t['strong']}; border-color:{t['strong']}; color:{t['muted']}; }}
QPushButton[danger="true"] {{ background:transparent; color:{t['err']}; border-color:{t['err_soft']}; }}
QPushButton[danger="true"]:hover {{ border-color:{t['err']}; }}
QPushButton[danger="true"]:disabled {{ color:{t['faint']}; border-color:{t['border']}; }}
QToolButton {{ border-radius:6px; padding:3px 6px; }}
QToolButton:hover {{ background:{t['surface2']}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QComboBox {{
    background:{t['input']}; border:1px solid {t['strong']}; border-radius:6px; padding:4px 8px;
    selection-background-color:{t['accent']}; selection-color:#ffffff; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QAbstractSpinBox:focus, QComboBox:focus {{ border-color:{t['accent']}; }}
QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {{ color:{t['faint']}; border-color:{t['border']}; }}
QComboBox QAbstractItemView {{ background:{t['surface']}; border:1px solid {t['border']}; selection-background-color:{t['sel']}; }}

QCheckBox, QRadioButton {{ spacing:6px; }}
QCheckBox::indicator, QTreeView::indicator, QGroupBox::indicator {{ width:14px; height:14px; border:1px solid {t['strong']};
    border-radius:4px; background:{t['input']}; }}
QCheckBox::indicator:hover {{ border-color:{t['accent']}; }}
QCheckBox::indicator:checked, QTreeView::indicator:checked, QGroupBox::indicator:checked {{
    background:{t['accent']}; border-color:{t['accent']}; }}
QCheckBox::indicator:disabled {{ border-color:{t['border']}; }}
QRadioButton::indicator {{ width:14px; height:14px; border:1px solid {t['strong']}; border-radius:8px; background:{t['input']}; }}
QRadioButton::indicator:checked {{ background:{t['accent']}; border-color:{t['accent']}; }}

QTabWidget::pane {{ border:1px solid {t['border']}; border-radius:8px; background:{t['surface']}; top:-1px; }}
QTabBar::tab {{ background:transparent; color:{t['muted']}; padding:7px 14px; border:none;
    border-bottom:2px solid transparent; margin-right:2px; }}
QTabBar::tab:selected {{ color:{t['text']}; border-bottom-color:{t['accent']}; font-weight:600; }}
QTabBar::tab:hover {{ color:{t['text']}; }}

QTreeView, QListView, QTableView {{ background:{t['list']}; alternate-background-color:{t['list_alt']};
    border:1px solid {t['border']}; border-radius:6px; selection-background-color:{t['sel']}; selection-color:#ffffff;
    outline:0; }}
QTreeView::item, QListView::item {{ padding:2px 0; }}
QTreeView::item:hover, QListView::item:hover {{ background:{t['accent_soft']}; }}
QTreeView::item:selected, QListView::item:selected {{ background:{t['sel']}; color:#ffffff; }}
QHeaderView::section {{ background:{t['surface']}; color:{t['muted']}; border:none; border-bottom:1px solid {t['border']};
    border-right:1px solid {t['border']}; padding:5px 6px; font-weight:600; }}

QGroupBox {{ border:1px solid {t['border']}; border-radius:8px; margin-top:16px; padding:10px 8px 8px 8px;
    background:{t['surface']}; }}
QGroupBox::title {{ subcontrol-origin:margin; subcontrol-position:top left; left:10px; padding:0 4px;
    color:{t['muted']}; font-weight:600; }}
QFrame[role="card"] {{ background:{t['surface']}; border:1px solid {t['border']}; border-radius:10px; }}
QLabel[role="h1"] {{ font-size:18px; font-weight:600; }}
QLabel[role="h2"] {{ font-size:14px; font-weight:600; }}
QLabel[role="muted"] {{ color:{t['muted']}; }}
QLabel[role="big"] {{ font-size:22px; font-weight:600; }}

QScrollBar:vertical {{ background:transparent; width:10px; margin:2px; }}
QScrollBar:horizontal {{ background:transparent; height:10px; margin:2px; }}
QScrollBar::handle {{ background:{t['strong']}; border-radius:3px; min-height:24px; min-width:24px; }}
QScrollBar::handle:hover {{ background:{t['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}
QScrollArea {{ border:none; background:transparent; }}
QSplitter::handle {{ background:{t['bg']}; }}
QSplitter::handle:hover {{ background:{t['accent_soft']}; }}

QStatusBar {{ background:{t['surface']}; border-top:1px solid {t['border']}; color:{t['muted']}; }}
QStatusBar QLabel {{ color:{t['muted']}; }}
QMenu {{ background:{t['surface']}; border:1px solid {t['border']}; padding:4px; }}
QMenu::item {{ padding:6px 22px 6px 14px; border-radius:4px; }}
QMenu::item:selected {{ background:{t['accent_soft']}; }}
QMenu::separator {{ height:1px; background:{t['border']}; margin:4px 6px; }}
QProgressBar {{ border:1px solid {t['border']}; border-radius:5px; background:{t['surface2']}; text-align:center;
    color:{t['text']}; min-height:14px; }}
QProgressBar::chunk {{ background:{t['accent']}; border-radius:4px; }}
QSlider::groove:horizontal {{ height:4px; background:{t['strong']}; border-radius:2px; }}
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
    pal.setColor(P.HighlightedText, c("#ffffff"))
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
