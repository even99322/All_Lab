"""LaTeX 公式轉圖片（使用 matplotlib mathtext，不需安裝 LaTeX）"""
import io
from functools import lru_cache

from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.mathtext import MathTextParser
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QPalette, QPainter
from PyQt6.QtWidgets import QApplication, QLabel, QScrollArea, QSizePolicy

_PARSER = MathTextParser("path")


def split_lines(latex):
    out = []
    for ln in (latex or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("$") and ln.endswith("$") and len(ln) > 1:
            ln = ln.strip("$")
        out.append(ln)
    return out


def check_latex(latex):
    """回傳 None 或錯誤訊息"""
    for i, ln in enumerate(split_lines(latex), 1):
        try:
            _PARSER.parse("$" + ln + "$")
        except Exception as e:
            msg = str(e).strip().splitlines()
            return f"第 {i} 行 LaTeX 無法解析：{msg[-1] if msg else e}"
    return None


@lru_cache(maxsize=256)
def _render_line_png(line, fontsize, dpi, color):
    fig = Figure(figsize=(8, 1), dpi=dpi)
    FigureCanvasAgg(fig)
    fig.text(0.0, 0.5, "$" + line + "$", fontsize=fontsize, va="center", ha="left", color=color)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, transparent=True, bbox_inches="tight", pad_inches=0.04)
    return buf.getvalue()


def text_color():
    app = QApplication.instance()
    if app is None:
        return "#000000"
    return app.palette().color(QPalette.ColorRole.WindowText).name()


def render_pixmap(latex, fontsize=16, dpi=110, color=None, gap=None):
    """每行各自繪製後垂直堆疊；回傳 (QPixmap 或 None, 錯誤訊息 或 None)"""
    lines = split_lines(latex)
    if not lines:
        return None, "（沒有公式）"
    err = check_latex(latex)
    if err:
        return None, err
    color = color or text_color()
    pms = []
    try:
        for ln in lines:
            pm = QPixmap()
            pm.loadFromData(_render_line_png(ln, fontsize, dpi, color), "PNG")
            pms.append(pm)
    except Exception as e:
        return None, f"公式繪製失敗：{e}"
    gap = int(fontsize * dpi / 72 * 0.45) if gap is None else gap
    width = max(p.width() for p in pms)
    height = sum(p.height() for p in pms) + gap * (len(pms) - 1)
    out = QPixmap(width, height)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    y = 0
    for p in pms:
        painter.drawPixmap(0, y, p)
        y += p.height() + gap
    painter.end()
    return out, None


class FormulaImage(QScrollArea):
    """顯示 LaTeX 公式圖；fit_width=True 時太寬會等比例縮小，否則可捲動"""

    def __init__(self, parent=None, fontsize=16, max_height=None, fit_width=False):
        super().__init__(parent)
        self.fontsize = fontsize
        self.fit_width = fit_width
        self._pm = None
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        self.setWidget(self.label)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.StyledPanel)
        if fit_width:
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        if max_height:
            self.setMaximumHeight(max_height)

    def set_latex(self, latex):
        pm, err = render_pixmap(latex, self.fontsize)
        self._pm = pm
        if pm is None:
            self.label.setPixmap(QPixmap())
            self.label.setText(err or "")
            self.label.setStyleSheet("color: #c0392b;" if err and "無法" in err else "color: gray;")
        else:
            self.label.setStyleSheet("")
            self.label.setText("")
            self._apply()
        return err

    def _apply(self):
        if self._pm is None:
            return
        pm = self._pm
        avail = self.viewport().width() - 6
        if self.fit_width and avail > 20 and pm.width() > avail:
            pm = pm.scaledToWidth(avail, Qt.TransformationMode.SmoothTransformation)
        self.label.setPixmap(pm)
        if self.maximumHeight() < 16777215:
            self.setMinimumHeight(min(self.maximumHeight(), pm.height() + 8))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.fit_width:
            self._apply()
