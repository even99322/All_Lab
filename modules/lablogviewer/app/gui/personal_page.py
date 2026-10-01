"""Settings > Personal: the user's own colours (colour wheel / hex) and icons (SVG)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app.core import personal
from app.gui.color_wheel import ColorWheel

GROUP_KEYS = {"app_light": "personal.group_app_light", "app_dark": "personal.group_app_dark",
              "plot_white": "personal.group_plot_white", "plot_dark": "personal.group_plot_dark",
              "pens": "personal.group_pens"}
# (foreground, background) pairs checked for readable contrast
CONTRAST_PAIRS = {"app_light": [("text", "window"), ("text", "panel"), ("text", "input")],
                  "app_dark": [("text", "window"), ("text", "panel"), ("text", "input")],
                  "plot_white": [("text", "background")], "plot_dark": [("text", "background")]}
MIN_CONTRAST = 3.0


def _swatch(color: str) -> QIcon:
    pixmap = QPixmap(18, 18)
    pixmap.fill(QColor(color))
    return QIcon(pixmap)


def apply_everywhere(theme_manager) -> None:
    """Make a Personal change visible immediately."""
    from app.gui.annotation import apply_personal_pens

    apply_personal_pens()
    if theme_manager is not None:
        theme_manager.refresh_personal()


class PersonalPage(QWidget):
    def __init__(self, localizer, theme_manager, parent=None):
        super().__init__(parent)
        self.localizer = localizer
        self.theme_manager = theme_manager
        text = localizer.text
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._colors_tab(), text("personal.colors"))
        self.tabs.addTab(self._icons_tab(), text("personal.icons"))
        layout.addWidget(self.tabs)
        self._apply_timer = QTimer(self, singleShot=True, interval=120, timeout=self._commit)
        self._pending: tuple[str, str, str] | None = None

    # -- colours ---------------------------------------------------------------------------
    def _colors_tab(self) -> QWidget:
        text = self.localizer.text
        page = QWidget()
        outer = QVBoxLayout(page)
        # Locked until 10 data operation experiences (or a licence); the icons tab is never locked.
        banner = QHBoxLayout()
        self.lock_label = QLabel()
        self.lock_label.setObjectName("personalLock")
        self.lock_label.setWordWrap(True)
        banner.addWidget(self.lock_label, 1)
        self.license_button = QPushButton(text("license.open"))
        self.license_button.clicked.connect(self._open_licenses)
        banner.addWidget(self.license_button)
        outer.addLayout(banner)
        row = QHBoxLayout()
        outer.addLayout(row, 1)
        self.tree = QTreeWidget()
        self.tree.setObjectName("personalColorTree")
        self.tree.setHeaderLabels([text("personal.item"), text("personal.code")])
        self.tree.setMinimumWidth(330)
        self.tree.setColumnWidth(0, 210)
        self.tree.currentItemChanged.connect(self._selected)
        row.addWidget(self.tree, 3)
        side = QVBoxLayout()
        self.wheel = ColorWheel()
        self.wheel.setMinimumSize(220, 220)
        self.wheel.colorChanged.connect(self._wheel_moved)
        self.wheel.colorPicked.connect(lambda color: self._choose(color.name().upper()))
        side.addWidget(self.wheel, 1)
        hex_row = QHBoxLayout()
        hex_row.addWidget(QLabel(text("personal.code")))
        self.hex_edit = QLineEdit()
        self.hex_edit.setObjectName("personalHex")
        self.hex_edit.setPlaceholderText("#1A2B3C")
        self.hex_edit.setMaxLength(7)
        self.hex_edit.editingFinished.connect(self._hex_entered)
        hex_row.addWidget(self.hex_edit)
        side.addLayout(hex_row)
        self.contrast = QLabel()
        self.contrast.setWordWrap(True)
        side.addWidget(self.contrast)
        buttons = QHBoxLayout()
        self.reset_one = QPushButton(text("personal.reset_one"))
        self.reset_group = QPushButton(text("personal.reset_group"))
        self.reset_all = QPushButton(text("personal.reset_all"))
        self.reset_one.clicked.connect(lambda: self._reset("one"))
        self.reset_group.clicked.connect(lambda: self._reset("group"))
        self.reset_all.clicked.connect(lambda: self._reset("all"))
        for button in (self.reset_one, self.reset_group, self.reset_all):
            buttons.addWidget(button)
        side.addLayout(buttons)
        row.addLayout(side, 2)
        self._fill_tree()
        self.refresh_lock()
        return page

    # -- lock ------------------------------------------------------------------------------
    def colors_locked(self) -> bool:
        return not personal.colors_unlocked()

    def refresh_lock(self) -> None:
        from app._guard import gate

        gate.invalidate()
        unlocked, left, granted = gate.personal_colors_status()
        text = self.localizer.text
        if not unlocked:
            self.lock_label.setText(text("personal.locked").format(remaining=left))
        elif granted is not None and left > 0:
            self.lock_label.setText(text("personal.unlocked_license").format(date=granted.expires.isoformat()))
        else:
            self.lock_label.setText(text("personal.unlocked"))
        self.lock_label.setStyleSheet("" if unlocked else "font-weight: 600;")
        for widget in (self.reset_group, self.reset_all):
            widget.setEnabled(unlocked)
        self._selected(self.tree.currentItem())

    def _open_licenses(self) -> None:
        from app.gui.license_dialog import LicenseDialog

        dialog = LicenseDialog(self.localizer, self)
        dialog.licenses_changed.connect(self._licenses_changed)
        dialog.exec()

    def _licenses_changed(self) -> None:
        self.refresh_lock()
        apply_everywhere(self.theme_manager)
        self._fill_tree()

    def _fill_tree(self) -> None:
        text = self.localizer.text
        current = self._current_key()
        self.tree.clear()
        for group in personal.GROUPS:
            parent = QTreeWidgetItem([text(GROUP_KEYS[group]), ""])
            parent.setData(0, Qt.ItemDataRole.UserRole, (group, None))
            self.tree.addTopLevelItem(parent)
            for key in personal.defaults(group):
                value = personal.color(group, key)
                label = text(f"personal.field_{key}")
                changed = key in personal.overrides(group)
                item = QTreeWidgetItem([label + (" *" if changed else ""), value])
                item.setIcon(1, _swatch(value))
                item.setData(0, Qt.ItemDataRole.UserRole, (group, key))
                parent.addChild(item)
                if current == (group, key):
                    self.tree.setCurrentItem(item)
            parent.setExpanded(True)
        self._update_contrast()

    def _current_key(self):
        item = self.tree.currentItem() if hasattr(self, "tree") else None
        return item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None

    def _selected(self, item, _previous=None) -> None:
        key = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        selected = bool(key and key[1])
        enabled = selected and not self.colors_locked()
        for widget in (self.wheel, self.hex_edit, self.reset_one):
            widget.setEnabled(enabled)
        if selected:
            value = personal.color(*key)
            self.wheel.setColor(QColor(value))
            self.hex_edit.setText(value)

    def _wheel_moved(self, color: QColor) -> None:
        self.hex_edit.setText(color.name().upper())

    def _hex_entered(self) -> None:
        value = self.hex_edit.text().strip()
        if not value.startswith("#"):
            value = "#" + value
        if personal.HEX.match(value):
            self.wheel.setColor(QColor(value))
            self._choose(value.upper())
        else:
            key = self._current_key()
            if key and key[1]:
                self.hex_edit.setText(personal.color(*key))

    def _choose(self, value: str) -> None:
        key = self._current_key()
        if not key or not key[1]:
            return
        self._pending = (key[0], key[1], value)
        self._apply_timer.start()

    def _commit(self) -> None:
        if self._pending is None:
            return
        group, key, value = self._pending
        self._pending = None
        try:
            personal.set_color(group, key, value)
        except personal.ColorsLocked:
            self.refresh_lock()
            return
        apply_everywhere(self.theme_manager)
        self._fill_tree()

    def _reset(self, scope: str) -> None:
        if self.colors_locked():
            return
        key = self._current_key()
        if scope == "all":
            personal.reset_colors()
        elif key:
            personal.reset_colors(key[0], key[1] if scope == "one" else None)
        apply_everywhere(self.theme_manager)
        self._fill_tree()
        if key and key[1]:
            self._selected(self.tree.currentItem())

    def _update_contrast(self) -> None:
        text = self.localizer.text
        warnings = []
        for group, pairs in CONTRAST_PAIRS.items():
            for foreground, background in pairs:
                ratio = personal.contrast_ratio(personal.color(group, foreground), personal.color(group, background))
                if ratio < MIN_CONTRAST:
                    warnings.append(text("personal.low_contrast").format(
                        group=text(GROUP_KEYS[group]), fg=text(f"personal.field_{foreground}"),
                        bg=text(f"personal.field_{background}"), ratio=ratio))
        self.contrast.setText("\n".join(warnings))
        self.contrast.setStyleSheet("color: #d32f2f;" if warnings else "")

    # -- icons -----------------------------------------------------------------------------
    def _icons_tab(self) -> QWidget:
        from app.icons import ICON_FILES

        text = self.localizer.text
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel(text("personal.icons_note"))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.icon_list = QListWidget()
        self.icon_list.setObjectName("personalIconList")
        for name in ICON_FILES:
            self.icon_list.addItem(QListWidgetItem(name))
        layout.addWidget(self.icon_list, 1)
        self.follow_theme = QCheckBox(text("personal.follow_theme"))
        self.follow_theme.setChecked(True)
        layout.addWidget(self.follow_theme)
        buttons = QHBoxLayout()
        replace = QPushButton(text("personal.replace_icon"))
        restore = QPushButton(text("personal.restore_icon"))
        restore_all = QPushButton(text("personal.restore_all_icons"))
        replace.clicked.connect(self._replace_icon)
        restore.clicked.connect(lambda: self._restore_icon(False))
        restore_all.clicked.connect(lambda: self._restore_icon(True))
        for button in (replace, restore, restore_all):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self._refresh_icons()
        return page

    def _refresh_icons(self) -> None:
        from app.icons import icon

        for index in range(self.icon_list.count()):
            item = self.icon_list.item(index)
            name = item.text().rstrip(" *")
            custom = personal.icon_override(name) is not None
            item.setText(name + (" *" if custom else ""))
            item.setIcon(icon(name))

    def replace_icon_from(self, name: str, path: str) -> str | None:
        """Validate and use ``path`` for icon ``name``; returns an error text or None."""
        from app.icons import reload_personal_icons

        try:
            personal.set_icon(name, path, self.follow_theme.isChecked())
        except (personal.SvgRejected, OSError) as error:
            return str(error)
        reload_personal_icons()
        self._refresh_icons()
        return None

    def _replace_icon(self) -> None:
        item = self.icon_list.currentItem()
        if item is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, self.localizer.text("personal.replace_icon"), "", "SVG (*.svg)")
        if not path:
            return
        error = self.replace_icon_from(item.text().rstrip(" *"), path)
        if error:
            QMessageBox.warning(self, self.localizer.text("personal.icons"),
                                self.localizer.text("personal.svg_rejected").format(reason=error))

    def _restore_icon(self, everything: bool) -> None:
        from app.icons import reload_personal_icons

        item = self.icon_list.currentItem()
        if everything:
            personal.reset_icon()
        elif item is not None:
            personal.reset_icon(item.text().rstrip(" *"))
        reload_personal_icons()
        self._refresh_icons()
