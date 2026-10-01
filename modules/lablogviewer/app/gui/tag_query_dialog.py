"""Scrollable Boolean Tag retrieval dialog with lightweight recent history."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QRadioButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from app.core.tag_store import TagStore


class TagQueryDialog(QDialog):
    def __init__(self, tag_store: TagStore, parent=None, master_root: str = ""):
        super().__init__(parent)
        self.tag_store = tag_store
        self.master_root = master_root
        self.setWindowTitle("Retrieve by Tags")
        self.resize(440, 620)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select Tags"))
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        checkbox_panel = QWidget()
        checkbox_layout = QVBoxLayout(checkbox_panel)
        self.tag_checkboxes: dict[str, QCheckBox] = {}
        grouped = self.tag_store.tags_by_category()
        for category in self.tag_store.categories():
            group = QGroupBox(category)
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(8, 5, 8, 5)
            for name in grouped[category]:
                checkbox = QCheckBox(name)
                checkbox.toggled.connect(self._sync_retrieve_button)
                group_layout.addWidget(checkbox)
                self.tag_checkboxes[name] = checkbox
            checkbox_layout.addWidget(group)
        checkbox_layout.addStretch(1)
        self.scroll_area.setWidget(checkbox_panel)
        layout.addWidget(self.scroll_area, 1)

        mode_group_box = QGroupBox("Match")
        mode_layout = QHBoxLayout(mode_group_box)
        self.and_radio = QRadioButton("AND")
        self.or_radio = QRadioButton("OR")
        self.and_radio.setChecked(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.and_radio)
        self.mode_group.addButton(self.or_radio)
        mode_layout.addWidget(self.and_radio)
        mode_layout.addWidget(self.or_radio)
        mode_layout.addStretch(1)
        layout.addWidget(mode_group_box)

        # Where to search: the open database, or every tagged file in the largest data folder.
        scope_box = QGroupBox("Search in")
        scope_layout = QVBoxLayout(scope_box)
        self.database_radio = QRadioButton("Current database")
        self.master_radio = QRadioButton(f"Largest data folder: {master_root}" if master_root
                                         else "Largest data folder (set it in Settings > General)")
        self.master_radio.setObjectName("tagSearchMaster")
        self.master_radio.setEnabled(bool(master_root))
        self.master_radio.setToolTip(master_root)
        self.database_radio.setChecked(True)
        self.scope_group = QButtonGroup(self)
        self.scope_group.addButton(self.database_radio)
        self.scope_group.addButton(self.master_radio)
        scope_layout.addWidget(self.database_radio)
        scope_layout.addWidget(self.master_radio)
        layout.addWidget(scope_box)

        recent_header = QHBoxLayout()
        recent_header.addWidget(QLabel("Recent"))
        recent_header.addStretch(1)
        self.clear_recent_button = QPushButton("Clear Recent")
        self.clear_recent_button.clicked.connect(self._clear_recent)
        recent_header.addWidget(self.clear_recent_button)
        layout.addLayout(recent_header)
        self.recent_list = QListWidget()
        self.recent_list.setMaximumHeight(145)
        self.recent_list.itemActivated.connect(self._restore_recent_item)
        self.recent_list.itemClicked.connect(self._restore_recent_item)
        layout.addWidget(self.recent_list)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.retrieve_button = self.buttons.addButton("Retrieve", QDialogButtonBox.AcceptRole)
        self.retrieve_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._populate_recent()

    def selected_tags(self) -> set[str]:
        return {name for name, checkbox in self.tag_checkboxes.items() if checkbox.isChecked()}

    def scope(self) -> str:
        return "master" if self.master_radio.isChecked() and self.master_root else "database"

    def set_scope(self, scope: str) -> None:
        (self.master_radio if scope == "master" and self.master_root else self.database_radio).setChecked(True)

    def query_mode(self) -> str:
        return "AND" if self.and_radio.isChecked() else "OR"

    def set_query(self, tags: set[str], mode: str) -> None:
        for name, checkbox in self.tag_checkboxes.items():
            checkbox.setChecked(name in tags)
        self.and_radio.setChecked(mode.upper() == "AND")
        self.or_radio.setChecked(mode.upper() == "OR")
        self._sync_retrieve_button()

    def _sync_retrieve_button(self, *_args) -> None:
        self.retrieve_button.setEnabled(bool(self.selected_tags()))

    def _populate_recent(self) -> None:
        self.recent_list.clear()
        for query in self.tag_store.recent_queries():
            item = QListWidgetItem(f"{' + '.join(query['tags'])}    {query['mode']}")
            item.setData(Qt.UserRole, query)
            item.setToolTip(datetime.fromtimestamp(query["last_used"]).strftime("Last used %Y-%m-%d %H:%M"))
            self.recent_list.addItem(item)
        has_recent = self.recent_list.count() > 0
        self.clear_recent_button.setEnabled(has_recent)
        if not has_recent:
            empty = QListWidgetItem("No recent queries")
            empty.setFlags(Qt.NoItemFlags)
            self.recent_list.addItem(empty)

    def _restore_recent_item(self, item: QListWidgetItem) -> None:
        query = item.data(Qt.UserRole)
        if isinstance(query, dict):
            self.set_query(set(query["tags"]), str(query["mode"]))

    def _clear_recent(self) -> None:
        self.tag_store.clear_recent_queries()
        self._populate_recent()
