"""Typeset math previews (hand-written style) with matplotlib mathtext.

mathtext draws ordinary formulas (fractions, roots, sub/superscripts, Greek,
``\\left( \\right)``, ``\\text``) but not LaTeX environments. This module adds
a small box layout on top of it:

* ``matrix``, ``pmatrix``, ``bmatrix``, ``Bmatrix``, ``vmatrix``, ``Vmatrix``,
  ``smallmatrix``, ``array{lcr}``: a grid with drawn delimiters
  (``\\left[ \\begin{matrix}...\\end{matrix} \\right]`` works too);
* ``cases``: left brace, left-aligned columns;
* ``aligned`` / ``align`` / ``split`` / ``gathered``: multi-line equations
  aligned at ``&``;
* top-level ``\\\\`` or new lines: stacked equations.

Environments nest. Everything is drawn with QPainter at the screen's pixel
ratio; no LaTeX installation or browser engine is needed.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy


class MathRenderError(ValueError):
    pass


_PARSER = None                 # Matplotlib's mathtext, loaded on the first formula (not at start-up)


def _parser():
    global _PARSER
    if _PARSER is None:
        from matplotlib.mathtext import MathTextParser

        _PARSER = MathTextParser("agg")
    return _PARSER
_BASE_DPI = 110

_MATRIX_DELIMITERS = {
    "matrix": ("", ""), "smallmatrix": ("", ""), "pmatrix": ("(", ")"), "bmatrix": ("[", "]"),
    "Bmatrix": ("{", "}"), "vmatrix": ("|", "|"), "Vmatrix": ("‖", "‖"), "array": ("", ""),
}
_ALIGNED = {"aligned", "align", "align*", "split", "gathered", "gather", "gather*", "alignedat"}
_ENVIRONMENTS = set(_MATRIX_DELIMITERS) | _ALIGNED | {"cases", "dcases"}
_DELIMITER_TOKENS = {"(": "(", ")": ")", "[": "[", "]": "]", "\\{": "{", "\\}": "}", "|": "|",
                     "\\|": "‖", "\\lbrace": "{", "\\rbrace": "}", "\\lvert": "|", "\\rvert": "|",
                     "\\lVert": "‖", "\\rVert": "‖", "\\langle": "⟨", "\\rangle": "⟩", ".": ""}


@dataclass
class Box:
    image: QImage | None
    width: float
    ascent: float
    descent: float

    @property
    def height(self) -> float:
        return self.ascent + self.descent


# -- low level -------------------------------------------------------------------
@lru_cache(maxsize=512)
def _raster(text: str, fontsize: float, dpi: float, color: str):
    try:
        from matplotlib.font_manager import FontProperties

        parsed = _parser().parse("$" + text + "$", dpi=dpi, prop=FontProperties(size=fontsize))
    except Exception as error:                                   # mathtext raises ValueError
        lines = str(error).strip().splitlines()
        raise MathRenderError(lines[-1] if lines else str(error)) from None
    alpha = np.asarray(parsed.image, dtype=np.uint8)
    height, width = alpha.shape
    rgba = np.empty((height, width, 4), np.uint8)
    qcolor = QColor(color)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = qcolor.red(), qcolor.green(), qcolor.blue()
    rgba[..., 3] = alpha
    image = QImage(rgba.tobytes(), width, height, 4 * width, QImage.Format.Format_RGBA8888).copy()
    depth = float(parsed.depth)            # parsed.height is ascent + depth
    return image, float(width), float(height) - depth, depth


class _Layout:
    def __init__(self, fontsize: float, dpi: float, color: str):
        self.fontsize, self.dpi, self.color = fontsize, dpi, color
        self.em = fontsize * dpi / 72.0
        self.axis = 0.25 * self.em               # math axis above the baseline
        self.pen = max(1.0, 0.055 * self.em)

    # text ------------------------------------------------------------------
    def text(self, source: str) -> Box | None:
        if not source.strip():
            return None
        # Hand-written look: full-size (display-style) fractions everywhere.
        source = re.sub(r"\\frac(?![A-Za-z])", r"\\dfrac", source)
        image, width, ascent, descent = _raster(source, self.fontsize, self.dpi, self.color)
        return Box(image, width, ascent, descent)

    # rows --------------------------------------------------------------------
    def row(self, source: str) -> Box:
        parts: list[Box] = []
        for kind, value in _split_environments(source):
            box = self.text(value) if kind == "text" else self.environment(*value)
            if box is not None:
                parts.append(box)
        if not parts:
            return Box(None, 0.0, 0.7 * self.em, 0.2 * self.em)
        return self.hstack(parts, gap=0.17 * self.em)

    def hstack(self, parts: list[Box], gap: float) -> Box:
        ascent = max(part.ascent for part in parts)
        descent = max(part.descent for part in parts)
        width = sum(part.width for part in parts) + gap * (len(parts) - 1)
        image = _canvas(width, ascent + descent)
        painter = QPainter(image)
        x = 0.0
        for part in parts:
            if part.image is not None:
                painter.drawImage(QPointF(x, ascent - part.ascent), part.image)
            x += part.width + gap
        painter.end()
        return Box(image, width, ascent, descent)

    def vstack(self, parts: list[Box], gap: float) -> Box:
        width = max(part.width for part in parts)
        height = sum(part.height for part in parts) + gap * (len(parts) - 1)
        image = _canvas(width, height)
        painter = QPainter(image)
        y = 0.0
        for part in parts:
            if part.image is not None:
                painter.drawImage(QPointF(0, y), part.image)
            y += part.height + gap
        painter.end()
        # Several equations: centre them on the math axis like one tall box.
        return Box(image, width, height / 2 + self.axis, height / 2 - self.axis)

    # environments --------------------------------------------------------------
    def environment(self, name: str, argument: str, body: str, left: str | None, right: str | None) -> Box:
        rows = [[cell for cell in _split_top(row, "&")] for row in _split_top(body, "\\\\")]
        rows = [row for row in rows if any(cell.strip() for cell in row)] or [[""]]
        columns = max(len(row) for row in rows)
        if name in ("cases", "dcases"):
            align = ["l"] * columns
            default = ("{", "")
            column_gap = 1.0 * self.em
        elif name in _ALIGNED:
            align = ["r" if index % 2 == 0 else "l" for index in range(columns)]
            if name.startswith("gather"):
                align = ["c"] * columns
            default = ("", "")
            column_gap = 0.0
        else:
            spec = re.sub(r"[^lcr]", "", argument) if name == "array" else ""
            align = [(spec[index] if index < len(spec) else "c") for index in range(columns)]
            default = _MATRIX_DELIMITERS[name]
            column_gap = (0.6 if name == "smallmatrix" else 1.0) * self.em
        cells = [[self.row(row[index]) if index < len(row) else Box(None, 0, 0, 0)
                  for index in range(columns)] for row in rows]
        widths = [max(row[index].width for row in cells) for index in range(columns)]
        ascents = [max(max(cell.ascent for cell in row), 0.7 * self.em) for row in cells]
        descents = [max(max(cell.descent for cell in row), 0.25 * self.em) for row in cells]
        row_gap = 0.35 * self.em
        # In aligned environments "&=" pairs sit tight; add a thin space between pairs.
        gaps = [column_gap if name not in _ALIGNED else (0.0 if index % 2 == 0 else 0.6 * self.em)
                for index in range(columns - 1)]
        grid_width = sum(widths) + sum(gaps)
        grid_height = sum(ascents) + sum(descents) + row_gap * (len(rows) - 1)
        left = default[0] if left is None else left
        right = default[1] if right is None else right
        delimiter_width = 0.45 * self.em
        pad = 0.15 * self.em
        lw = (delimiter_width + pad) if left else 0.0
        rw = (delimiter_width + pad) if right else 0.0
        extra = 0.12 * self.em if (left or right) else 0.0
        total_width, total_height = lw + grid_width + rw, grid_height + 2 * extra
        image = _canvas(total_width, total_height)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = extra
        for row_index, row in enumerate(cells):
            x = lw
            for column_index, cell in enumerate(row):
                slack = widths[column_index] - cell.width
                offset = {"l": 0.0, "r": slack, "c": slack / 2}[align[column_index]]
                if cell.image is not None:
                    painter.drawImage(QPointF(x + offset, y + ascents[row_index] - cell.ascent), cell.image)
                x += widths[column_index] + (gaps[column_index] if column_index < len(gaps) else 0.0)
            y += ascents[row_index] + descents[row_index] + row_gap
        pen = QPen(QColor(self.color), self.pen)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        if left:
            _draw_delimiter(painter, left, QRectF(0, 0, delimiter_width, total_height), True, self.pen)
        if right:
            _draw_delimiter(painter, right, QRectF(total_width - delimiter_width, 0, delimiter_width,
                                                   total_height), False, self.pen)
        painter.end()
        return Box(image, total_width, total_height / 2 + self.axis, total_height / 2 - self.axis)


def _canvas(width: float, height: float) -> QImage:
    image = QImage(max(1, math.ceil(width)), max(1, math.ceil(height)),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    return image


def _draw_delimiter(painter: QPainter, kind: str, rect: QRectF, left: bool, pen_width: float) -> None:
    inset = pen_width
    x0, x1 = rect.left() + inset, rect.right() - inset
    top, bottom = rect.top() + inset, rect.bottom() - inset
    mid = (top + bottom) / 2
    path = QPainterPath()
    if kind == "(" or kind == ")":
        outer, inner = (x0, x1) if kind == "(" else (x1, x0)
        path.moveTo(inner, top)
        path.cubicTo(outer, top + 0.15 * (bottom - top), outer, bottom - 0.15 * (bottom - top), inner, bottom)
    elif kind == "[" or kind == "]":
        outer, inner = (x0 + 0.2 * (x1 - x0), x1) if kind == "[" else (x1 - 0.2 * (x1 - x0), x0)
        path.moveTo(inner, top)
        path.lineTo(outer, top)
        path.lineTo(outer, bottom)
        path.lineTo(inner, bottom)
    elif kind == "{" or kind == "}":
        tip = x0 if kind == "{" else x1
        spine = (x0 + x1) / 2
        end = x1 if kind == "{" else x0
        path.moveTo(end, top)
        path.quadTo(spine, top, spine, top + 0.12 * (bottom - top))
        path.lineTo(spine, mid - 0.08 * (bottom - top))
        path.quadTo(spine, mid, tip, mid)
        path.quadTo(spine, mid, spine, mid + 0.08 * (bottom - top))
        path.lineTo(spine, bottom - 0.12 * (bottom - top))
        path.quadTo(spine, bottom, end, bottom)
    elif kind in ("|", "‖"):
        centre = (x0 + x1) / 2
        offsets = (0.0,) if kind == "|" else (-0.18 * (x1 - x0), 0.18 * (x1 - x0))
        for offset in offsets:
            path.moveTo(centre + offset, top)
            path.lineTo(centre + offset, bottom)
    elif kind in ("⟨", "⟩"):
        outer, inner = (x0, x1) if kind == "⟨" else (x1, x0)
        path.moveTo(inner, top)
        path.lineTo(outer, mid)
        path.lineTo(inner, bottom)
    painter.drawPath(path)


# -- parsing -----------------------------------------------------------------------
def _matching_end(text: str, start: int, name: str) -> int:
    """Index of the ``\\end{name}`` closing the environment whose body starts at ``start``."""
    depth = 1
    pattern = re.compile(r"\\(begin|end)\{" + re.escape(name) + r"\}")
    for match in pattern.finditer(text, start):
        depth += 1 if match.group(1) == "begin" else -1
        if depth == 0:
            return match.start()
    raise MathRenderError(f"Missing \\end{{{name}}}.")


def _split_environments(text: str):
    """Yield ("text", str) and ("env", (name, arg, body, left, right)) parts."""
    position = 0
    pattern = re.compile(r"(\\left\s*(\\\{|\\\||\\[a-zA-Z]+|[(\[|.])\s*)?\\begin\{([A-Za-z*]+)\}")
    while True:
        match = pattern.search(text, position)
        if match is None:
            break
        name = match.group(3)
        if name not in _ENVIRONMENTS:
            raise MathRenderError(f"Unsupported environment: {name}.")
        body_start = match.end()
        argument = ""
        if name in ("array", "alignedat") and text[body_start:body_start + 1] == "{":
            close = text.index("}", body_start)
            argument, body_start = text[body_start + 1:close], close + 1
        end = _matching_end(text, body_start, name)
        after = end + len(f"\\end{{{name}}}")
        left = right = None
        if match.group(1):
            left = _DELIMITER_TOKENS.get(match.group(2), "")
            closing = re.match(r"\s*\\right\s*(\\\}|\\\||\\[a-zA-Z]+|[)\]|.])", text[after:])
            if closing is None:
                raise MathRenderError("\\left without a matching \\right.")
            right = _DELIMITER_TOKENS.get(closing.group(1), "")
            after += closing.end()
        yield "text", text[position:match.start()]
        yield "env", (name, argument, text[body_start:end], left, right)
        position = after
    yield "text", text[position:]


def _split_top(text: str, separator: str) -> list[str]:
    """Split at ``separator`` outside braces and nested environments."""
    parts, depth, env_depth, start, index = [], 0, 0, 0, 0
    while index < len(text):
        if text.startswith("\\begin{", index):
            env_depth += 1
        elif text.startswith("\\end{", index):
            env_depth -= 1
        char = text[index]
        if char == "\\" and not text.startswith(separator, index):
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif depth == 0 and env_depth == 0 and text.startswith(separator, index):
            parts.append(text[start:index])
            index += len(separator)
            start = index
            continue
        index += 1
    parts.append(text[start:])
    return parts


def split_equations(latex: str) -> list[str]:
    """Lines of the source (``$`` delimiters removed; blank lines skipped)."""
    out = []
    for line in (latex or "").splitlines():
        line = line.strip()
        if line.startswith("$$") and line.endswith("$$") and len(line) > 3:
            line = line[2:-2]
        elif line.startswith("$") and line.endswith("$") and len(line) > 1:
            line = line.strip("$")
        if line:
            out.append(line)
    return out


def _join_environment_lines(lines: list[str]) -> list[str]:
    """Keep a multi-line \\begin{...} ... \\end{...} block together as one equation."""
    out, pending, depth = [], [], 0
    for line in lines:
        depth += len(re.findall(r"\\begin\{", line)) - len(re.findall(r"\\end\{", line))
        pending.append(line)
        if depth <= 0:
            out.append(" ".join(pending))
            pending, depth = [], 0
    if pending:
        out.append(" ".join(pending))
    return out


# -- public API --------------------------------------------------------------------
def default_text_color() -> str:
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        from app.palette import ANALYSIS

        return ANALYSIS["latex_text_fallback"]
    return app.palette().color(QPalette.ColorRole.WindowText).name()


def screen_ratio() -> float:
    app = QGuiApplication.instance()
    screen = app.primaryScreen() if app is not None else None
    return float(screen.devicePixelRatio()) if screen is not None else 1.0


def render_image(latex: str, fontsize: float = 16, color: str | None = None,
                 ratio: float | None = None) -> QPixmap:
    """LaTeX (one or more equations) -> QPixmap with device pixel ratio set."""
    ratio = ratio or screen_ratio()
    layout = _Layout(fontsize, _BASE_DPI * ratio, color or default_text_color())
    equations = []
    for number, line in enumerate(_join_environment_lines(split_equations(latex)), 1):
        rows = [row for row in _split_top(line, "\\\\") if row.strip()]
        try:
            boxes = [layout.row(row) for row in rows]
        except MathRenderError as error:
            raise MathRenderError(f"Could not parse LaTeX on line {number}: {error}") from None
        equations.extend(boxes)
    if not equations:
        raise MathRenderError("(No equation)")
    pad = 0.12 * layout.em
    box = equations[0] if len(equations) == 1 else layout.vstack(equations, gap=0.45 * layout.em)
    image = _canvas(box.width + 2 * pad, box.height + 2 * pad)
    painter = QPainter(image)
    if box.image is not None:
        painter.drawImage(QPointF(pad, pad), box.image)
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def check_latex(latex: str) -> str | None:
    try:
        render_image(latex, ratio=1.0)
    except MathRenderError as error:
        return None if str(error) == "(No equation)" else str(error)
    return None


class FormulaPreviewLabel(QLabel):
    """A label that shows a typeset formula and grows / shrinks with it.

    ``text()`` keeps the plain Unicode form (for tooltips, copying and tests);
    the picture is re-rendered when the theme's text color changes and is
    scaled down only when it is wider than the panel.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._latex: str | None = None
        self._fontsize = 13.0
        self._lhs: str | None = None
        self._terms: list[tuple[str, str]] = []
        self._plain = text
        self._pixmap: QPixmap | None = None
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.setMaximumHeight(400)

    def text(self) -> str:  # noqa: D102 - QLabel API
        return self._plain

    def setText(self, text: str) -> None:  # noqa: N802 - QLabel API
        # The localizer re-sets label text; keep the typeset picture if there is one.
        self._plain = text
        if self._pixmap is None:
            super().setText(text)

    def set_formula(self, plain: str, latex: str | None = None, fontsize: float = 13,
                    lhs: str | None = None, terms: list[tuple[str, str]] | None = None) -> None:
        """Show ``latex``; with ``lhs`` and ``terms`` a too-wide formula wraps at + / −."""
        self._plain, self._latex, self._fontsize = plain, latex, fontsize
        self._lhs, self._terms = lhs, terms or []
        self.setAccessibleName(plain)
        self._render()

    def _render(self) -> None:
        self._pixmap = None
        if self._latex:
            try:
                color = default_text_color()
                self._pixmap = render_image(self._latex, fontsize=self._fontsize, color=color)
                available = max(40, self.width() - 4)
                if self._pixmap.width() / self._pixmap.devicePixelRatio() > available and \
                        self._lhs and len(self._terms) > 1:
                    self._pixmap = self._wrapped(available, color)
            except (MathRenderError, ValueError):
                self._pixmap = None
        self._show()

    def _wrapped(self, available: float, color: str) -> QPixmap:
        """Greedy line breaking: ``lhs &= t1 + t2 \\ &\\quad + t3 ...``."""
        def width(latex: str) -> float:
            pixmap = render_image(latex, fontsize=self._fontsize, color=color)
            return pixmap.width() / pixmap.devicePixelRatio()

        lines: list[str] = []
        current = ""
        for sign, term in self._terms:
            piece = f" {sign} {term}" if sign else term
            candidate = current + piece
            prefix = f"{self._lhs} = " if not lines else r"\quad "
            if current and width(prefix + candidate) > available:
                lines.append(current)
                current = piece
            else:
                current = candidate
        lines.append(current)
        body = r" \\ ".join((f"{self._lhs} &= {line}" if index == 0 else rf"&\quad {line}")
                             for index, line in enumerate(lines))
        return render_image(r"\begin{aligned} " + body + r" \end{aligned}", fontsize=self._fontsize, color=color)

    def _show(self) -> None:
        if self._pixmap is None:
            super().setText(self._plain)
            return
        pixmap, ratio = self._pixmap, self._pixmap.devicePixelRatio()
        available = max(40, self.width() - 4)
        if pixmap.width() / ratio > available:
            pixmap = pixmap.scaledToWidth(int(available * ratio), Qt.TransformationMode.SmoothTransformation)
            pixmap.setDevicePixelRatio(ratio)
        self.setPixmap(pixmap)
        self.setMinimumHeight(max(24, int(pixmap.height() / ratio) + 4))
        self.updateGeometry()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API spelling
        super().resizeEvent(event)
        if self._latex and event.oldSize().width() != event.size().width():
            self._render()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API spelling
        super().changeEvent(event)
        if event.type() == event.Type.PaletteChange and self._latex:
            self._render()
