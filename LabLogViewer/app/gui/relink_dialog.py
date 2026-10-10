"""Re-link moved data (hidden tool, needs a licence with the "relink" feature)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from app.core import relink


def _browser():
    from app.gui.browser_window import BrowserWindow

    return next((w for w in QApplication.topLevelWidgets() if isinstance(w, BrowserWindow)), None)


def _state_dir() -> Path:
    browser = _browser()
    if browser is not None:
        return browser.star_store.storage_path.parent
    from app.core.data_location import data_root, STATE_FOLDER

    return data_root() / STATE_FOLDER


def _sessions_dir() -> Path:
    from app.analysis.yig_fitting.core.paths import sub_dir

    return Path(sub_dir("sessions"))


def _open_viewers() -> int:
    from app.gui.main_window import MainWindow

    return sum(1 for w in QApplication.topLevelWidgets() if isinstance(w, MainWindow) and w.isVisible())


def reload_browser_stores() -> None:
    browser = _browser()
    if browser is None:
        return
    for name in ("star_store", "tag_store", "comment_store", "viewer_display_state_store", "axis_preset_store",
                 "overlay_store", "mark_store", "named_view_store", "session_store"):
        store = getattr(browser, name, None)
        if store is not None and hasattr(store, "reload"):
            store.reload()
    if getattr(browser, "scan_result", None) is not None:
        browser.reload_database()


class _Search(QObject):
    progress = Signal(int, int)
    done = Signal(list)

    def __init__(self, records, root):
        super().__init__()
        self.records, self.root, self.cancelled = records, root, False

    def run(self):
        self.done.emit(relink.match(self.records, self.root, progress=self.progress.emit,
                                    cancel=lambda: self.cancelled))


class RelinkDialog(QDialog):
    def __init__(self, localizer, parent=None):
        super().__init__(parent)
        self.localizer = localizer
        text = localizer.text
        self.setObjectName("relinkDialog")
        self.setWindowTitle(text("relink.title"))
        self.resize(980, 620)
        self.records: list[relink.OldRecord] = []
        self.matches: list[relink.Match] = []
        self._thread: QThread | None = None
        layout = QVBoxLayout(self)
        intro = QLabel(text("relink.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.missing_label = QLabel()
        layout.addWidget(self.missing_label)
        row = QHBoxLayout()
        row.addWidget(QLabel(text("relink.new_folder")))
        self.folder = QLineEdit()
        self.folder.setObjectName("relinkFolder")
        row.addWidget(self.folder, 1)
        browse = QPushButton(text("relink.browse"))
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        self.search_button = QPushButton(text("relink.search"))
        self.search_button.clicked.connect(self.search)
        row.addWidget(self.search_button)
        layout.addLayout(row)
        # Quick way: the files kept their place inside a folder that moved / a share renamed.
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel(text("relink.old_folder")))
        self.old_folder = QComboBox()
        self.old_folder.setObjectName("relinkOldFolder")
        self.old_folder.setEditable(True)
        path_row.addWidget(self.old_folder, 1)
        self.path_button = QPushButton(text("relink.by_path"))
        self.path_button.setObjectName("relinkByPath")
        self.path_button.setToolTip(text("relink.by_path_tip"))
        self.path_button.clicked.connect(self.match_by_path)
        path_row.addWidget(self.path_button)
        layout.addLayout(path_row)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.table = QTreeWidget()
        self.table.setObjectName("relinkTable")
        self.table.setRootIsDecorated(False)
        self.table.setHeaderLabels([text("relink.old_path"), text("relink.new_path"), text("relink.match"),
                                    text("relink.records")])
        self.table.setColumnWidth(0, 360)
        self.table.setColumnWidth(1, 360)
        layout.addWidget(self.table, 1)
        self.note = QLabel(text("relink.guess_note"))
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QHBoxLayout()
        self.apply_button = QPushButton(text("relink.apply"))
        self.apply_button.setObjectName("relinkApply")
        self.apply_button.clicked.connect(self._apply_clicked)
        self.undo_button = QPushButton(text("relink.undo"))
        self.undo_button.clicked.connect(self._undo_clicked)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.undo_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh_missing()

    # -- steps ------------------------------------------------------------------------------
    def refresh_missing(self) -> None:
        self.records = relink.find_missing(_state_dir())
        with_fp = sum(1 for r in self.records if r.fingerprint)
        self.missing_label.setText(self.localizer.text("relink.missing").format(count=len(self.records),
                                                                                 fingerprinted=with_fp))
        self.matches = []
        current = self.old_folder.currentText()
        self.old_folder.clear()
        self.old_folder.addItems(relink.suggest_old_prefixes(self.records))
        if current:
            self.old_folder.setEditText(current)
        self._fill()
        self._update_buttons()

    def match_by_path(self) -> None:
        old, new = self.old_folder.currentText().strip(), self.folder.text().strip()
        if not old or not new or not Path(new).is_dir() or not self.records:
            return
        self.matches = relink.match_by_prefix(self.records, old, new)
        self._fill()
        self._update_buttons()

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self.localizer.text("relink.new_folder"), self.folder.text())
        if folder:
            self.folder.setText(folder)

    def search(self, wait: bool = False) -> None:
        root = self.folder.text().strip()
        if not root or not Path(root).is_dir() or not self.records:
            return
        self.search_button.setEnabled(False)
        self.progress.setRange(0, max(1, len(self.records)))
        self.progress.setValue(0)
        self.progress.show()
        self._worker = _Search(self.records, root)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        # Queued: the search runs in the worker thread, widgets may only change in this one.
        queued = Qt.ConnectionType.QueuedConnection
        self._worker.progress.connect(self._progressed, queued)
        self._worker.done.connect(self._found, queued)
        self._worker.done.connect(self._thread.quit)
        self._thread.start()
        if wait:
            while self._thread.isRunning() or self.search_button.isEnabled() is False:
                QApplication.processEvents()
                self._thread.wait(20)

    def _progressed(self, index: int, _total: int) -> None:
        self.progress.setValue(index)

    def _found(self, matches: list) -> None:
        self.matches = matches
        self.progress.hide()
        self.search_button.setEnabled(True)
        self._fill()
        self._update_buttons()

    def _fill(self) -> None:
        text = self.localizer.text
        self.table.clear()
        matched = {m.old.identity: m for m in self.matches}
        for record in self.records:
            found = matched.get(record.identity)
            item = QTreeWidgetItem([record.identity, found.new_path if found else "",
                                    text(f"relink.conf_{found.confidence}") if found else text("relink.not_found"),
                                    ", ".join(sorted(p.removesuffix(".json") for p in record.places))])
            item.setToolTip(0, record.identity)
            if found is not None:
                item.setToolTip(1, found.new_path)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked if found.selected else Qt.CheckState.Unchecked)
            item.setData(0, Qt.ItemDataRole.UserRole, record.identity)
            self.table.addTopLevelItem(item)

    def _collect_selection(self) -> list[relink.Match]:
        by_identity = {m.old.identity: m for m in self.matches}
        for index in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(index)
            found = by_identity.get(item.data(0, Qt.ItemDataRole.UserRole))
            if found is not None:
                found.selected = item.checkState(0) == Qt.CheckState.Checked
        return [m for m in self.matches if m.selected]

    def _update_buttons(self) -> None:
        from app.core.data_location import data_root

        self.apply_button.setEnabled(bool(self.matches))
        self.undo_button.setEnabled(bool(self._undoable(data_root())))

    @staticmethod
    def _undoable(root):
        return [b for b in relink.list_backups(root) if not (b / "undone").exists()]

    def _database_roots(self) -> list[str]:
        """Where stars and Tags go: the Browser's open database first, then the chosen folder."""
        from app.core.database_scanner import DatabaseScanner

        roots = []
        browser = _browser()
        result = getattr(browser, "scan_result", None) if browser is not None else None
        if result is not None and getattr(result, "database_id", None):
            roots.append(result.database_id)
        folder = self.folder.text().strip()
        if folder:
            roots.append(DatabaseScanner.compute_database_id(folder))
        return roots

    def conflicts(self) -> list[str]:
        return relink.preview_conflicts(self._collect_selection(), _state_dir(), _sessions_dir(),
                                        self._database_roots())

    def apply(self, prefer_old: bool = False) -> relink.Result | None:
        """Re-link the ticked rows (no questions; used by the buttons and tests)."""
        from app._guard import gate
        from app.core.data_location import data_root

        if gate.relink_allowed() is None:
            return None
        chosen = self._collect_selection()
        if not chosen:
            return None
        result = relink.apply(chosen, _state_dir(), _sessions_dir(), self._database_roots(), data_root(),
                              prefer_old=prefer_old)
        reload_browser_stores()
        self.refresh_missing()
        return result

    def _apply_clicked(self) -> None:
        text = self.localizer.text
        if _open_viewers():
            QMessageBox.information(self, text("relink.title"), text("relink.close_viewers"))
            return
        count = len(self._collect_selection())
        if not count:
            return
        conflicts = self.conflicts()
        prefer_old = False
        if conflicts:
            box = QMessageBox(QMessageBox.Icon.Question, text("relink.title"),
                              text("relink.conflict_question").format(count=len(conflicts)), parent=self)
            box.setDetailedText("\n".join(conflicts))
            use_old = box.addButton(text("relink.use_old"), QMessageBox.ButtonRole.AcceptRole)
            keep_new = box.addButton(text("relink.keep_new"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() not in (use_old, keep_new):
                return
            prefer_old = box.clickedButton() is use_old
        elif QMessageBox.question(self, text("relink.title"), text("relink.confirm").format(count=count)) \
                != QMessageBox.StandardButton.Yes:
            return
        result = self.apply(prefer_old=prefer_old)
        if result is not None:
            message = text("relink.done").format(count=result.moved, backup=result.backup.name)
            if result.conflicts and not prefer_old:
                message += "\n\n" + text("relink.conflicts") + "\n" + "\n".join(result.conflicts[:12])
                if len(result.conflicts) > 12:
                    message += "\n…"
            QMessageBox.information(self, text("relink.title"), message)

    def undo_last(self) -> bool:
        from app.core.data_location import data_root

        backups = self._undoable(data_root())
        if not backups:
            return False
        relink.undo(backups[0], _state_dir(), _sessions_dir())
        reload_browser_stores()
        self.refresh_missing()
        return True

    def _undo_clicked(self) -> None:
        text = self.localizer.text
        if _open_viewers():
            QMessageBox.information(self, text("relink.title"), text("relink.close_viewers"))
            return
        if QMessageBox.question(self, text("relink.title"), text("relink.undo_confirm")) \
                == QMessageBox.StandardButton.Yes and self.undo_last():
            QMessageBox.information(self, text("relink.title"), text("relink.undone"))

    def closeEvent(self, event):  # noqa: N802 - Qt API spelling
        if self._thread is not None and self._thread.isRunning():
            self._worker.cancelled = True
            self._thread.quit()
            self._thread.wait(3000)
        super().closeEvent(event)


def open_relink(localizer, parent=None) -> RelinkDialog | None:
    from app._guard import gate

    if gate.relink_allowed() is None:
        return None
    dialog = RelinkDialog(localizer, parent)
    dialog.show()
    return dialog
