"""LaTeX 公式轉圖片：手寫排版（分數、矩陣、cases、aligned），預覽框依公式大小自動調整。

排版由 app.gui.math_render 負責（matplotlib mathtext + 自製矩陣版面，不需安裝 LaTeX）。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QSizePolicy

from app.gui.math_render import MathRenderError, check_latex, render_image, split_equations
from app.palette import STATUS

split_lines = split_equations

__all__ = ["FormulaImage", "check_latex", "render_pixmap", "split_lines"]


def render_pixmap(latex, fontsize=16, color=None):
    """回傳 (QPixmap 或 None, 錯誤訊息 或 None)"""
    try:
        return render_image(latex, fontsize=fontsize, color=color), None
    except MathRenderError as error:
        return None, str(error)
    except Exception as error:                                   # never break the dialog
        return None, f"Equation rendering failed: {error}"


class FormulaImage(QScrollArea):
    """顯示 LaTeX 公式圖。

    高度跟著公式變化（多行、矩陣會長高，單行會縮小），上限為 max_height 或所在視窗高度的 60%，
    超過才出現捲軸；fit_width=True 時太寬會等比例縮小（最多縮到 60%，再寬就改為可左右捲動）。
    """

    MIN_SCALE = 0.6

    def __init__(self, parent=None, fontsize=16, max_height=None, fit_width=False):
        super().__init__(parent)
        self.fontsize = fontsize
        self.fit_width = fit_width
        self.max_height = max_height
        self._pm = None
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        self.setWidget(self.label)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_latex(self, latex):
        pm, err = render_pixmap(latex, self.fontsize)
        self._pm = pm
        if pm is None:
            self.label.setPixmap(type(self.label.pixmap())())
            self.label.setText(err or "")
            self.label.setStyleSheet("color: %s;" % STATUS["tex_error"]
                                     if err and ("Could not" in err or "failed" in err.lower()) else "")
            self._fit_height(self.label.sizeHint().height())
        else:
            self.label.setStyleSheet("")
            self.label.setText("")
            self._apply()
        return err

    def _height_cap(self):
        cap = self.max_height or 10_000
        window = self.window()
        if window is not None and window is not self and window.height() > 200:
            cap = min(cap, int(window.height() * 0.6))
        return max(cap, 40)

    def _fit_height(self, content_height):
        frame = 2 * self.frameWidth() + 8
        scroll = self.horizontalScrollBar().sizeHint().height() if self._needs_horizontal_scroll() else 0
        self.setFixedHeight(min(self._height_cap(), content_height + frame + scroll))

    def _needs_horizontal_scroll(self):
        return self.horizontalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff and \
            self._pm is not None and self._logical_width(self._pm) > self.viewport().width()

    @staticmethod
    def _logical_width(pm):
        return pm.width() / pm.devicePixelRatio()

    def _apply(self):
        if self._pm is None:
            return
        pm = self._pm
        ratio = pm.devicePixelRatio()
        avail = self.viewport().width() - 6
        width = self._logical_width(pm)
        policy = Qt.ScrollBarPolicy.ScrollBarAsNeeded
        if self.fit_width and avail > 20 and width > avail:
            scale = avail / width
            if scale >= self.MIN_SCALE:
                pm = pm.scaledToWidth(int(avail * ratio), Qt.TransformationMode.SmoothTransformation)
                pm.setDevicePixelRatio(ratio)
                policy = Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        elif self.fit_width:
            policy = Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        self.setHorizontalScrollBarPolicy(policy)
        self.label.setPixmap(pm)
        self._fit_height(int(round(pm.height() / ratio)))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._pm is not None and ev.oldSize().width() != ev.size().width():
            self._apply()
