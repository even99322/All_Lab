"""Database-hierarchy picker shared by De-background Target/Background."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout,
)

from app.core.database_scanner import DatabaseScanResult, LogEntry


class DataPickerDialog(QDialog):
    def __init__(self, scan_result: DatabaseScanResult, title: str,
                 preselected_path: str | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 480)
        self.selected_entry: LogEntry | None = None
        self._entries = [entry for entry in scan_result.entries if entry.status == "ok"]
        self._items_by_folder: dict[tuple[str, ...], QTreeWidgetItem] = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Database: {scan_result.root_path}"))
        split = QSplitter(Qt.Horizontal, self)
        self.folder_tree = QTreeWidget(split)
        self.folder_tree.setHeaderHidden(True)
        self.file_tree = QTreeWidget(split)
        self.file_tree.setColumnCount(2)
        self.file_tree.setHeaderLabels(["Log name", "Relative path"])
        self.file_tree.setRootIsDecorated(False)
        self.file_tree.setAlternatingRowColors(True)
        split.addWidget(self.folder_tree)
        split.addWidget(self.file_tree)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        layout.addWidget(split, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_button = self.buttons.button(QDialogButtonBox.Ok)
        self.ok_button.setEnabled(False)
        self.buttons.accepted.connect(self._accept_selection)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.folder_tree.itemSelectionChanged.connect(self._populate_files)
        self.file_tree.itemSelectionChanged.connect(self._sync_ok)
        self.file_tree.itemDoubleClicked.connect(lambda *_args: self._accept_selection())
        self._populate_folders(scan_result.root_path)
        if preselected_path:
            self._select_path(preselected_path)

    def _populate_folders(self, root_path: str) -> None:
        root = QTreeWidgetItem([Path(root_path).name or root_path])
        root.setData(0, Qt.UserRole, ())
        self.folder_tree.addTopLevelItem(root)
        self._items_by_folder[()] = root
        folders: set[tuple[str, ...]] = set()
        for entry in self._entries:
            for length in range(1, len(entry.folder_parts) + 1):
                folders.add(entry.folder_parts[:length])
        for parts in sorted(folders, key=lambda value: tuple(p.casefold() for p in value)):
            item = QTreeWidgetItem([parts[-1]])
            item.setData(0, Qt.UserRole, parts)
            self._items_by_folder[parts[:-1]].addChild(item)
            self._items_by_folder[parts] = item
        self.folder_tree.expandAll()
        self.folder_tree.setCurrentItem(root)

    def _populate_files(self) -> None:
        self.file_tree.clear()
        folder = self.folder_tree.currentItem()
        folder_parts = tuple(folder.data(0, Qt.UserRole) or ()) if folder else ()
        for entry in sorted(
            (value for value in self._entries if value.folder_parts == folder_parts),
            key=lambda value: (value.log_name.casefold(), value.relative_path.casefold()),
        ):
            item = QTreeWidgetItem([entry.log_name, entry.relative_path])
            item.setData(0, Qt.UserRole, entry)
            item.setToolTip(0, entry.absolute_path)
            self.file_tree.addTopLevelItem(item)
        self.file_tree.resizeColumnToContents(0)
        self._sync_ok()

    def _sync_ok(self) -> None:
        item = self.file_tree.currentItem()
        self.ok_button.setEnabled(item is not None and item.data(0, Qt.UserRole) is not None)

    def _select_path(self, path: str) -> None:
        target = str(Path(path).expanduser().resolve())
        entry = next((value for value in self._entries if str(Path(value.absolute_path).resolve()) == target), None)
        if entry is None:
            return
        folder = self._items_by_folder.get(entry.folder_parts)
        if folder is not None:
            self.folder_tree.setCurrentItem(folder)
        for row in range(self.file_tree.topLevelItemCount()):
            item = self.file_tree.topLevelItem(row)
            candidate = item.data(0, Qt.UserRole)
            if candidate is not None and candidate.relative_path == entry.relative_path:
                self.file_tree.setCurrentItem(item)
                break

    def _accept_selection(self) -> None:
        item = self.file_tree.currentItem()
        entry = item.data(0, Qt.UserRole) if item is not None else None
        if entry is not None:
            self.selected_entry = entry
            self.accept()
