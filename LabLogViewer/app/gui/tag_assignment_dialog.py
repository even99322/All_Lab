"""Categorized Tag assignment with category-aware single-select controls."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QGroupBox, QInputDialog, QLabel,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from app.core.tag_store import SINGLE_SELECT_CATEGORIES, TagStore
from app.gui.tag_management_dialog import TagManagementDialog


class TagAssignmentDialog(QDialog):
    def __init__(self, log_name: str, tag_store: TagStore,
                 assigned_tags: set[str], parent=None):
        super().__init__(parent)
        self.tag_store = tag_store
        self._available_tags = tag_store.list_tags()
        self._categories = {tag: tag_store.category_for(tag) for tag in self._available_tags}
        self.pending_tags: list[str] = []
        self.pending_tag_categories: dict[str, str] = {}
        self.setWindowTitle(f"Tags - {log_name}")
        self.resize(420, 540)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(log_name))
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.checkbox_panel = QWidget()
        self.checkbox_layout = QVBoxLayout(self.checkbox_panel)
        self.checkbox_layout.setContentsMargins(6, 6, 6, 6)
        self.tag_checkboxes: dict[str, QCheckBox] = {}
        self.scroll_area.setWidget(self.checkbox_panel)
        layout.addWidget(self.scroll_area, 1)

        self.new_tag_button = QPushButton("+ New Tag")
        self.new_tag_button.clicked.connect(self._prompt_for_new_tag)
        layout.addWidget(self.new_tag_button)
        self.manage_tags_button = QPushButton("Manage Tags...")
        self.manage_tags_button.clicked.connect(self._open_tag_manager)
        layout.addWidget(self.manage_tags_button)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._rebuild_checkboxes(tag_store.normalize_tags(assigned_tags))

    def _rebuild_checkboxes(self, checked: set[str]) -> None:
        while self.checkbox_layout.count():
            item = self.checkbox_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.tag_checkboxes = {}
        for category in self.tag_store.categories():
            group = QGroupBox(category)
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(8, 5, 8, 5)
            for name in self._available_tags:
                if self._categories.get(name) != category:
                    continue
                checkbox = QCheckBox(name)
                checkbox.setProperty("_lv_content_value", True)
                checkbox.setChecked(name in checked)
                checkbox.toggled.connect(lambda state, tag=name: self._on_tag_toggled(tag, state))
                group_layout.addWidget(checkbox)
                self.tag_checkboxes[name] = checkbox
            self.checkbox_layout.addWidget(group)
        self.checkbox_layout.addStretch(1)

    def _on_tag_toggled(self, name: str, checked: bool) -> None:
        if not checked:
            return
        category = self._categories.get(name, "Other")
        if category in SINGLE_SELECT_CATEGORIES:
            for other, checkbox in self.tag_checkboxes.items():
                if other != name and self._categories.get(other) == category:
                    checkbox.setChecked(False)
        if name == "Flux":
            self.tag_checkboxes.get("BG") and self.tag_checkboxes["BG"].setChecked(False)
        elif name == "BG":
            self.tag_checkboxes.get("Flux") and self.tag_checkboxes["Flux"].setChecked(False)

    def selected_tags(self) -> set[str]:
        return {name for name, checkbox in self.tag_checkboxes.items() if checkbox.isChecked()}

    def create_tag(self, name: str, category: str = "Other") -> str:
        normalized = self.tag_store.normalize_name(name)
        if not normalized:
            return "empty"
        if normalized in self._available_tags:
            return "duplicate"
        if category not in self.tag_store.categories():
            return "invalid_category"
        self._available_tags.append(normalized)
        self._categories[normalized] = category
        self.pending_tags.append(normalized)
        self.pending_tag_categories[normalized] = category
        checked = self.selected_tags()
        checked.add(normalized)
        self._rebuild_checkboxes(checked)
        return "created"

    def _prompt_for_new_tag(self) -> None:
        name, ok = QInputDialog.getText(self, "New Tag", "Tag name:")
        if not ok:
            return
        category, category_ok = QInputDialog.getItem(
            self, "Tag Category", "Category:", list(self.tag_store.categories()), 4, False
        )
        if not category_ok:
            return
        result = self.create_tag(name, category)
        if result == "empty":
            QMessageBox.warning(self, "Invalid Tag", "Tag name cannot be empty.")
        elif result == "duplicate":
            QMessageBox.information(self, "Tag already exists", "That Tag is already available.")

    def _open_tag_manager(self) -> None:
        checked = self.selected_tags()
        manager = TagManagementDialog(self.tag_store, self)
        manager.exec()
        for old_name, new_name in manager.renamed_tags.items():
            if old_name in checked:
                checked.remove(old_name)
                checked.add(new_name)
        checked -= manager.deleted_tags
        self._available_tags = self.tag_store.list_tags()
        self._categories = {tag: self.tag_store.category_for(tag) for tag in self._available_tags}
        self.pending_tags = [name for name in self.pending_tags if name in self._available_tags]
        self.pending_tag_categories = {
            name: category for name, category in self.pending_tag_categories.items()
            if name in self._available_tags
        }
        self._rebuild_checkboxes(checked & set(self._available_tags))
