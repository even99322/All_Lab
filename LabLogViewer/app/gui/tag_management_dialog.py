"""Category-aware Tag library management."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from app.core.tag_store import TagAssignmentConflict, TagStore


class TagManagementDialog(QDialog):
    def __init__(self, tag_store: TagStore, parent=None):
        super().__init__(parent)
        self.tag_store = tag_store
        self.renamed_tags: dict[str, str] = {}
        self.deleted_tags: set[str] = set()
        self.setWindowTitle("Manage Tags")
        self.resize(460, 520)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Available Tags"))
        self.tag_list = QListWidget()
        self.tag_list.currentItemChanged.connect(self._sync_action_buttons)
        layout.addWidget(self.tag_list, 1)
        self.category_combo = QComboBox()
        for category in tag_store.categories():
            self.category_combo.addItem(category, userData=category)
        self.category_combo.currentIndexChanged.connect(self._change_category)
        layout.addWidget(QLabel("Category"))
        layout.addWidget(self.category_combo)

        actions = QHBoxLayout()
        self.create_button = QPushButton("Add Tag...")
        self.rename_button = QPushButton("Rename...")
        self.delete_button = QPushButton("Delete")
        self.create_button.clicked.connect(self._prompt_create)
        self.rename_button.clicked.connect(self._prompt_rename)
        self.delete_button.clicked.connect(self._confirm_delete)
        actions.addWidget(self.create_button)
        actions.addWidget(self.rename_button)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    def _refresh(self, selected: str | None = None) -> None:
        self.tag_list.clear()
        for category in self.tag_store.categories():
            for name in self.tag_store.tags_by_category()[category]:
                item = QListWidgetItem(f"{name}    —    {category}")
                item.setData(Qt.UserRole, name)
                self.tag_list.addItem(item)
        if selected:
            for index in range(self.tag_list.count()):
                item = self.tag_list.item(index)
                if item.data(Qt.UserRole) == selected:
                    self.tag_list.setCurrentItem(item)
                    break
        self._sync_action_buttons()

    def _selected_name(self) -> str | None:
        item = self.tag_list.currentItem()
        return str(item.data(Qt.UserRole)) if item else None

    def _sync_action_buttons(self, *_items) -> None:
        name = self._selected_name()
        enabled = name is not None
        self.rename_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)
        self.category_combo.setEnabled(enabled)
        if name:
            self.category_combo.blockSignals(True)
            index = self.category_combo.findData(self.tag_store.category_for(name))
            if index >= 0:
                self.category_combo.setCurrentIndex(index)
            self.category_combo.blockSignals(False)

    def create_tag(self, name: str, category: str = "Other") -> str:
        normalized = self.tag_store.normalize_name(name)
        if not normalized:
            return "empty"
        if normalized in self.tag_store.list_tags():
            return "duplicate"
        if not self.tag_store.create_tag(normalized, category):
            return "duplicate"
        self._refresh(normalized)
        return "created"

    def rename_tag(self, old_name: str, new_name: str) -> str:
        normalized = self.tag_store.normalize_name(new_name)
        if not normalized:
            return "empty"
        if normalized in self.tag_store.list_tags():
            return "duplicate"
        if not self.tag_store.rename_tag(old_name, normalized):
            return "missing"
        self.renamed_tags[old_name] = normalized
        self._refresh(normalized)
        return "renamed"

    def delete_tag(self, name: str) -> bool:
        deleted = self.tag_store.delete_tag(name)
        if deleted:
            self.deleted_tags.add(name)
            self._refresh()
        return deleted

    def _prompt_create(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Tag", "Tag name:")
        if not ok:
            return
        category, category_ok = QInputDialog.getItem(
            self, "Tag Category", "Category:", list(self.tag_store.categories()), 4, False
        )
        if category_ok:
            self._show_validation(self.create_tag(name, category))

    def _prompt_rename(self) -> None:
        old_name = self._selected_name()
        if old_name is None:
            return
        name, ok = QInputDialog.getText(self, "Rename Tag", "New name:", text=old_name)
        if ok and name.strip() != old_name:
            self._show_validation(self.rename_tag(old_name, name))

    def _change_category(self, _index: int) -> None:
        name = self._selected_name()
        if name is None:
            return
        category = self.category_combo.currentData()
        try:
            self.tag_store.set_tag_category(name, category)
        except TagAssignmentConflict:
            answer = QMessageBox.question(
                self, "Resolve category conflicts",
                f"Moving {name!r} to {category} conflicts with existing per-Data assignments. "
                "Replace the conflicting single-select assignments explicitly?",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                self._sync_action_buttons()
                return
            try:
                self.tag_store.set_tag_category(name, category, replace_conflicts=True)
            except Exception as exc:
                QMessageBox.warning(self, "Category not changed", str(exc))
                self._sync_action_buttons()
                return
        self._refresh(name)

    def _confirm_delete(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        count = self.tag_store.assignment_count(name)
        detail = (f"This Tag is assigned to {count} Data entr{'y' if count == 1 else 'ies'}.\n\n"
                  if count else "This Tag is not assigned to any Data entries.\n\n")
        answer = QMessageBox.question(
            self, "Delete Tag", f'Delete Tag "{name}"?\n\n{detail}'
            "Deleting it will remove the Tag from those entries.",
            QMessageBox.Cancel | QMessageBox.Yes, QMessageBox.Cancel,
        )
        if answer == QMessageBox.Yes:
            self.delete_tag(name)

    def _show_validation(self, result: str) -> None:
        if result == "empty":
            QMessageBox.warning(self, "Invalid Tag", "Tag name cannot be empty.")
        elif result == "duplicate":
            QMessageBox.information(self, "Tag already exists", "That Tag name already exists.")
