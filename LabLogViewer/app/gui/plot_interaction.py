"""Shared Pointer / Drag-to-Share interaction controls."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QHBoxLayout, QLabel, QMenu, QToolButton, QWidget,
)

from app.icons import make_icon_only
from app.localization import get_localization_manager
from app.theme import ANALYSIS_ICON_SIZE, TOOLBAR_ICON_SIZE


class PlotInteractionControls(QWidget):
    mode_changed = Signal(bool)          # Drag to Share on / off
    zoom_changed = Signal(bool)          # rectangle Zoom tool on / off (Viewer only)
    pan_changed = Signal(bool)           # v1.0.7: Move tool on / off (Viewer only)
    scope_changed = Signal(str)

    def __init__(self, parent=None, icon_size: int = TOOLBAR_ICON_SIZE, zoom: bool = False):
        super().__init__(parent)
        self.localizer = get_localization_manager()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        self.btn_pointer = QToolButton(self)
        self.btn_pointer.setText(self.localizer.text("interaction.pointer"))
        self.btn_pointer.setCheckable(True)
        self.btn_share = QToolButton(self)
        self.btn_share.setText(self.localizer.text("interaction.drag_share"))
        self.btn_share.setCheckable(True)
        for button, icon_name in ((self.btn_pointer, "pointer"), (self.btn_share, "drag_share")):
            make_icon_only(button, icon_name, button.text(), icon_size, flat=True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.btn_pointer, 0)
        self.mode_group.addButton(self.btn_share, 1)
        self.btn_zoom = None
        self.btn_pan = None
        if zoom:
            self.btn_pan = QToolButton(self)
            self.btn_pan.setText(self.localizer.text("interaction.pan"))
            self.btn_pan.setCheckable(True)
            make_icon_only(self.btn_pan, "pan", self.btn_pan.text(), icon_size, flat=True)
            self.btn_pan.setToolTip(self.localizer.text("interaction.pan_tip"))
            self.mode_group.addButton(self.btn_pan, 3)
            self.btn_zoom = QToolButton(self)
            self.btn_zoom.setText(self.localizer.text("interaction.zoom"))
            self.btn_zoom.setCheckable(True)
            make_icon_only(self.btn_zoom, "zoom", self.btn_zoom.text(), icon_size, flat=True)
            self.btn_zoom.setToolTip(self.localizer.text("interaction.zoom_tip"))
            self.mode_group.addButton(self.btn_zoom, 2)
        self.btn_pointer.setChecked(True)
        self.scope_label = QLabel(self.localizer.text("interaction.drag_target"), self)
        self.scope_combo = QComboBox(self)
        self.scope_combo.addItem(self.localizer.text("interaction.active_pane"), "active")
        self.scope_combo.addItem(self.localizer.text("interaction.all_panes"), "all")
        self.scope_combo.setCurrentIndex(0)
        self.scope_combo.setToolTip("Choose whether sharing includes the active plot or the current pane layout.")
        row.addWidget(self.btn_pointer)
        if self.btn_pan is not None:
            row.addWidget(self.btn_pan)                  # right of the pointer
        row.addWidget(self.btn_share)
        if self.btn_zoom is not None:
            row.addWidget(self.btn_zoom)
        row.addWidget(self.scope_label)
        row.addWidget(self.scope_combo)
        self.btn_pointer.clicked.connect(lambda: self._tool("pointer"))
        self.btn_share.clicked.connect(lambda: self._tool("share"))
        if self.btn_zoom is not None:
            self.btn_zoom.clicked.connect(lambda: self._tool("zoom"))
        if self.btn_pan is not None:
            self.btn_pan.clicked.connect(lambda: self._tool("pan"))
        self.scope_combo.currentIndexChanged.connect(
            lambda _index: self.scope_changed.emit(str(self.scope_combo.currentData()))
        )
        self.localizer.bind(self)

    #: v1.0.7: F1–F4 = the tools in toolbar order
    TOOL_KEYS = (("pointer", "F1"), ("pan", "F2"), ("share", "F3"), ("zoom", "F4"))

    def _tool(self, name: str) -> None:
        """One tool at a time: Pointer, Move, Drag-to-Share or Zoom."""
        self.zoom_changed.emit(False)
        self.pan_changed.emit(False)
        self.mode_changed.emit(name == "share")
        if name == "zoom":
            self.zoom_changed.emit(True)
        elif name == "pan":
            self.pan_changed.emit(True)

    def button_for(self, name: str):
        return {"pointer": self.btn_pointer, "pan": self.btn_pan, "share": self.btn_share,
                "zoom": self.btn_zoom}.get(name)

    def select_tool(self, name: str) -> None:
        button = self.button_for(name)
        if button is None or not button.isEnabled():
            return
        button.setChecked(True)
        self._tool(name)

    @property
    def tool(self) -> str:
        for name, _key in self.TOOL_KEYS:
            button = self.button_for(name)
            if button is not None and button.isChecked():
                return name
        return "pointer"

    def install_shortcuts(self, window) -> list:
        """F1 Pointer, F2 Move, F3 Drag-to-Share, F4 Zoom (window shortcuts); tooltips show the key."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QKeySequence, QShortcut

        made = []
        for name, key in self.TOOL_KEYS:
            button = self.button_for(name)
            if button is None:
                continue
            shortcut = QShortcut(QKeySequence(key), window)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(lambda name=name: self.select_tool(name))
            tip = button.toolTip()
            if f"({key})" not in tip:
                button.setToolTip(f"{tip} ({key})")
            made.append(shortcut)
        self._shortcuts = made
        return made

    @property
    def pan_mode(self) -> bool:
        return self.btn_pan is not None and self.btn_pan.isChecked()

    @property
    def share_mode(self) -> bool:
        return self.btn_share.isChecked()

    @property
    def zoom_mode(self) -> bool:
        return self.btn_zoom is not None and self.btn_zoom.isChecked()

    @property
    def scope(self) -> str:
        return str(self.scope_combo.currentData() or "active")

    def set_share_mode(self, enabled: bool) -> None:
        if enabled:
            self.btn_share.setChecked(True)
        elif self.btn_share.isChecked():                # leaving Share (Zoom stays selected)
            self.btn_pointer.setChecked(True)

    def set_multi_pane_available(self, available: bool) -> None:
        self.scope_combo.setEnabled(bool(available))
        self.scope_label.setEnabled(bool(available))
        if not available:
            self.scope_combo.setCurrentIndex(0)


def copy_save_buttons(parent, *, copy_active, copy_all, save_active, save_all,
                      icon_size: int = ANALYSIS_ICON_SIZE) -> tuple[QToolButton, QToolButton]:
    """One Copy and one Save icon button, each offering This Pane / All Panes."""
    localizer = get_localization_manager()
    buttons = []
    for icon_name, label_key, entries in (
        ("copy", "analysis.copy", (("analysis.copy_this", copy_active), ("analysis.copy_all", copy_all))),
        ("save", "action.save", (("analysis.save_this", save_active), ("analysis.save_all", save_all))),
    ):
        button = make_icon_only(QToolButton(parent), icon_name, localizer.text(label_key), icon_size, flat=True)
        menu = QMenu(button)
        for key, callback in entries:
            menu.addAction(localizer.text(key), callback)
        button.setMenu(menu)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        buttons.append(button)
    return buttons[0], buttons[1]
