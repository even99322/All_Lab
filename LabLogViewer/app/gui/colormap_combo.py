"""Colormap picker with previews (v1.0.5).

Every entry shows its color ramp (low → high). At the end of the list:

* "Reverse Current Colormap" — switches between ``name`` and ``name_r``;
* "Custom Colors..." — pick 2 or 3 colors (low / middle / high) with a live preview; the result is a
  colormap named ``Custom: #rrggbb · #rrggbb · #rrggbb`` (plot_2d_widget.get_colormap resolves it), and the
  last few custom maps stay in the list (QSettings).

The item text is the colormap name, so code that reads ``currentText()`` or calls ``setCurrentText(name)``
works as with a plain QComboBox; a reversed or custom name that is not in the list yet is added on the fly.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QLabel, QPushButton, QVBoxLayout,
)

from app.gui.plot_2d_widget import (
    COLORMAPS, DEFAULT_COLORMAP, colormap_pixmap, custom_colormap_name, custom_colors, get_colormap,
    is_known_colormap,
)

REVERSE = "Reverse Current Colormap"
CUSTOM = "Custom Colors..."
_KEY = "view/custom_colormaps"
_KEEP = 6
_ROLE = Qt.ItemDataRole.UserRole + 41          # "special" marker on the two action entries


def saved_custom_maps() -> list[str]:
    raw = QSettings("CCU QEL", "LabLogViewer").value(_KEY, [])
    names = [raw] if isinstance(raw, str) else list(raw or [])
    return [n for n in names if custom_colors(n)][:_KEEP]


def remember_custom_map(name: str) -> None:
    names = [name] + [n for n in saved_custom_maps() if n != name]
    QSettings("CCU QEL", "LabLogViewer").setValue(_KEY, names[:_KEEP])


def ramp_colors(name: str, count: int = 3) -> list[str]:
    """The colors of ``name`` at evenly spaced points (to start a custom map from the current one)."""
    import numpy as np

    rgba = get_colormap(name).map(np.linspace(0.0, 1.0, count), mode="byte")
    return ["#%02x%02x%02x" % tuple(int(v) for v in row[:3]) for row in rgba]


class CustomColormapDialog(QDialog):
    """Pick low / (middle) / high colors; the preview updates immediately."""

    def __init__(self, start: str = DEFAULT_COLORMAP, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Custom Colormap")
        colors = custom_colors(start) or ramp_colors(start if is_known_colormap(start) else DEFAULT_COLORMAP)
        if len(colors) == 2:
            colors = [colors[0], ramp_colors(start, 3)[1] if is_known_colormap(start) else "#ffffff", colors[1]]
        self.colors = colors[:1] + colors[len(colors) // 2:len(colors) // 2 + 1] + colors[-1:]
        layout = QVBoxLayout(self)
        grid = QGridLayout()
        self.buttons: list[QPushButton] = []
        for row, label in enumerate(("Low", "Middle", "High")):
            grid.addWidget(QLabel(label), row, 0)
            button = QPushButton()
            button.setObjectName(f"colormapColor{row}")
            button.setMinimumWidth(120)
            button.clicked.connect(lambda _checked=False, i=row: self._pick(i))
            self.buttons.append(button)
            grid.addWidget(button, row, 1)
        self.use_middle = QCheckBox("Use a middle color")
        self.use_middle.setChecked(True)
        self.use_middle.toggled.connect(self._update)
        grid.addWidget(self.use_middle, 1, 2)
        reverse = QPushButton("Reverse")
        reverse.clicked.connect(self._reverse)
        grid.addWidget(reverse, 2, 2)
        layout.addLayout(grid)
        self.preview = QLabel()
        self.preview.setObjectName("colormapPreview")
        self.preview.setMinimumHeight(26)
        layout.addWidget(self.preview)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self._update()

    def name(self) -> str:
        used = self.colors if self.use_middle.isChecked() else [self.colors[0], self.colors[2]]
        return custom_colormap_name(used)

    def _pick(self, index: int) -> None:
        color = QColorDialog.getColor(QColor(self.colors[index]), self, "Choose Color")
        if color.isValid():
            self.colors[index] = color.name().lower()
            self._update()

    def _reverse(self) -> None:
        self.colors.reverse()
        self._update()

    def _update(self, *_args) -> None:
        for index, (button, color) in enumerate(zip(self.buttons, self.colors)):
            button.setText(color)
            button.setStyleSheet(f"background:{color}; color:{'#000' if QColor(color).lightness() > 140 else '#fff'};")
            if index == 1:
                button.setEnabled(self.use_middle.isChecked())
        self.preview.setPixmap(colormap_pixmap(self.name(), 360, 24))


class ColormapCombo(QComboBox):
    """QComboBox of colormaps with ramp icons, Reverse and Custom Colors... entries."""

    colormap_chosen = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setIconSize(QSize(72, 12))
        self._last = DEFAULT_COLORMAP
        self._filling = False
        self._fill()
        self.activated.connect(self._on_activated)
        self.currentTextChanged.connect(self._remember_last)
        self.view().viewport().installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.MouseButtonRelease and obj is self.view().viewport():
            index = self.view().indexAt(event.position().toPoint())
            special = self.itemData(index.row(), _ROLE) if index.isValid() else None
            if special:
                self.hidePopup()
                self.run_special(special)
                return True
        return super().eventFilter(obj, event)

    # ---- items ---------------------------------------------------------------------------------
    def _icon(self, name: str) -> QIcon:
        return QIcon(colormap_pixmap(name, 72, 12))

    def _fill(self) -> None:
        self._filling = True
        try:
            self.clear()
            for name in COLORMAPS:
                self.addItem(self._icon(name), name)
            for name in saved_custom_maps():
                self.addItem(self._icon(name), name)
            self.insertSeparator(self.count())
            for special in (REVERSE, CUSTOM):
                self.addItem(special)
                self.setItemData(self.count() - 1, special, _ROLE)
                item = self.model().item(self.count() - 1)
                if item is not None:                     # never becomes the current item: clicked → action
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        finally:
            self._filling = False

    def _special_start(self) -> int:
        return next((i for i in range(self.count()) if self.itemData(i, _ROLE)), self.count()) - 1   # separator

    def ensure_item(self, name: str) -> int:
        index = super().findText(name, Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchCaseSensitive)
        if index >= 0 and not self.itemData(index, _ROLE):
            return index
        if not is_known_colormap(name):
            return -1
        at = self._special_start()
        self.insertItem(at, self._icon(name), name)
        return at

    def findText(self, text, flags=Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchCaseSensitive) -> int:  # noqa: N802
        """Like QComboBox.findText; a valid reversed / custom name not listed yet is added (restoring a view)."""
        index = super().findText(text, flags)
        if index < 0 and isinstance(text, str) and is_known_colormap(text):
            index = self.ensure_item(text)
        return index

    def setCurrentText(self, text: str) -> None:  # noqa: N802 — QComboBox API
        index = self.ensure_item(text)
        if index >= 0:
            self.setCurrentIndex(index)

    def colormap(self) -> str:
        text = self.currentText()
        return text if is_known_colormap(text) else self._last

    # ---- actions -------------------------------------------------------------------------------
    def _remember_last(self, text: str) -> None:
        if not self._filling and not self.itemData(self.currentIndex(), _ROLE) and is_known_colormap(text):
            self._last = text

    def _on_activated(self, index: int) -> None:
        if not self.itemData(index, _ROLE):
            self.colormap_chosen.emit(self.currentText())

    def run_special(self, special: str) -> None:
        current = self.colormap()
        if special == REVERSE:
            colors = custom_colors(current)
            if colors:
                name = custom_colormap_name(colors[::-1])
                remember_custom_map(name)
            else:
                name = current[:-2] if current.endswith("_r") else current + "_r"
        else:
            dialog = CustomColormapDialog(current, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            name = dialog.name()
            remember_custom_map(name)
        self.setCurrentText(name)
        self.colormap_chosen.emit(name)
