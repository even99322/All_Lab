"""v1.0.10: File ▸ Import into Labber Database — Lab Control .nc → Labber data logs + Database.hdf5.

The engine is app/core/labber_db (the former standalone "Labber Converter" 0.4.2): files go to
``<database>/年/月/Data_MMDD/``, their Project / User / Tags labels are merged into ``Database.hdf5`` (so Labber's
"Open Database" and this Browser both see them), sources are only read, NAS copies are checksum-verified, and an
interrupted batch can be resumed. Name clashes ask Replace / Skip / Keep both.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout,
)

from app.core import labber_db

INPUT_FILTER = "Measurement data (*.nc *.zip *.hdf5 *.h5);;All files (*)"


def _tr(text: str) -> str:
    try:
        from app.localization.manager import get_localization_manager, translate_text

        return translate_text(text, get_localization_manager().language)
    except Exception:  # noqa: BLE001
        return text


def _settings() -> QSettings:
    return QSettings("CCU QEL", "LabLogViewer")


@dataclass
class _Conflict:
    source: str
    destination: Path
    suggestion: Path
    action: str = "skip"                      # closing the question leaves the existing file alone
    ready: threading.Event = field(default_factory=threading.Event)


class _Worker(QObject):
    progress = Signal(int, int, str)
    activity = Signal(object)
    conflict = Signal(object)
    result = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, job: str, inputs, root: str, project: str, local_cache: bool, resume: bool, rebuild: bool):
        super().__init__()
        self.job, self.inputs, self.root, self.project = job, list(inputs), root, project
        self.local_cache, self.resume, self.rebuild = local_cache, resume, rebuild
        self.stop = False

    def run(self) -> None:
        try:
            if self.job == "init":
                out = labber_db.initialize_database(self.root)
            elif self.job == "sync":
                out = labber_db.reindex(self.root, self.rebuild)
            else:
                out = labber_db.convert_batch(
                    self.inputs, self.root, project=self.project,
                    progress=lambda a, b, name: self.progress.emit(a, b, name),
                    cancel=lambda: self.stop, on_conflict=self._ask, local_cache=self.local_cache,
                    resume=self.resume, activity=self.activity.emit)
            self.result.emit(out)
        except Exception as error:  # noqa: BLE001 - shown in the dialog
            self.failed.emit(f"{type(error).__name__}: {error}")
        finally:
            self.finished.emit()

    def _ask(self, source, destination, suggestion) -> str:
        request = _Conflict(source.label, Path(destination), Path(suggestion))
        self.conflict.emit(request)
        while not request.ready.wait(0.1):
            if self.stop:
                return "cancel"
        return request.action


class _InputList(QListWidget):
    """Accepts dropped files and folders."""

    dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def dragEnterEvent(self, event):  # noqa: N802 - Qt API
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):  # noqa: N802 - Qt API
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):  # noqa: N802 - Qt API
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.dropped.emit(paths)
            event.acceptProposedAction()


class LabberDatabaseDialog(QDialog):
    """Import Lab Control data into a Labber database folder (the folder that holds Database.hdf5)."""

    imported = Signal(str)                    # database root, after a batch that wrote something

    def __init__(self, parent=None, inputs=(), root: str = ""):
        super().__init__(parent)
        self.setObjectName("labberDatabaseDialog")
        self.setWindowTitle("Import into Labber Database")
        self.resize(760, 640)
        self.thread: QThread | None = None
        self.worker: _Worker | None = None
        self.report = None
        s = _settings()
        lay = QVBoxLayout(self)
        intro = QLabel("Lab Control .nc files (or folders / ZIPs) are converted to Labber data logs and added to a "
                       "Labber database: the files go to <database>/year/month/Data_MMDD and their labels into "
                       "Database.hdf5. Open the database folder in Labber (Open Database → Reload) or in this Browser. "
                       "Sources are only read.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: gray;")
        lay.addWidget(intro)

        lay.addWidget(QLabel("Data to import:"))
        self.input_list = _InputList()
        self.input_list.setObjectName("labberDbInputs")
        self.input_list.dropped.connect(self.add_inputs)
        lay.addWidget(self.input_list, 1)
        row = QHBoxLayout()
        self.add_files_button = QPushButton("Add Files...")
        self.add_files_button.clicked.connect(self._add_files)
        self.add_folder_button = QPushButton("Add Folder...")
        self.add_folder_button.clicked.connect(self._add_folder)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self._remove_selected)
        for b in (self.add_files_button, self.add_folder_button, self.remove_button):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        grid = QGridLayout()
        self.root_edit = QLineEdit(root or str(s.value("labberdb/root", "") or ""))
        self.root_edit.setObjectName("labberDbRoot")
        self.root_edit.setPlaceholderText("The folder that holds (or will hold) Database.hdf5")
        self.root_edit.textChanged.connect(lambda _t: self._sync_buttons())
        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self._browse_root)
        self.new_db_button = QPushButton("New Database...")
        self.new_db_button.setToolTip("Choose (or make) an empty folder; Database.hdf5 is created there right away.")
        self.new_db_button.clicked.connect(self._new_database)
        grid.addWidget(QLabel("Labber database folder:"), 0, 0)
        grid.addWidget(self.root_edit, 0, 1)
        grid.addWidget(self.browse_button, 0, 2)
        grid.addWidget(self.new_db_button, 0, 3)
        self.project_edit = QLineEdit(str(s.value("labberdb/project", "") or ""))
        self.project_edit.setPlaceholderText("Optional, e.g. My project → Project label My project/10/1021")
        grid.addWidget(QLabel("Project prefix:"), 1, 0)
        grid.addWidget(self.project_edit, 1, 1, 1, 3)
        lay.addLayout(grid)

        self.cache_check = QCheckBox("Copy each file to this computer first and verify the result (recommended on the NAS)")
        self.cache_check.setChecked(s.value("labberdb/local_cache", True) not in (False, "false"))
        self.resume_check = QCheckBox("Resume: skip files already imported and verified (same inputs and folder)")
        self.resume_check.setChecked(s.value("labberdb/resume", False) in (True, "true"))
        lay.addWidget(self.cache_check)
        lay.addWidget(self.resume_check)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        lay.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setObjectName("labberDbStatus")
        lay.addWidget(self.status)
        self.log = QPlainTextEdit()
        self.log.setObjectName("labberDbLog")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        lay.addWidget(self.log, 2)

        buttons = QHBoxLayout()
        self.sync_button = QPushButton("Sync Labels...")
        self.sync_button.setToolTip("Scan the database folder's Labber files and update the labels in Database.hdf5 "
                                    "(optionally remove labels that no file uses).")
        self.sync_button.clicked.connect(self.start_sync)
        self.open_button = QPushButton("Open in Browser")
        self.open_button.setToolTip("Open this database in the LabLogViewer Browser")
        self.open_button.clicked.connect(self._open_in_browser)
        self.reveal_button = QPushButton("Show Folder")
        self.reveal_button.clicked.connect(self._reveal)
        buttons.addWidget(self.sync_button)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.reveal_button)
        buttons.addStretch(1)
        self.import_button = QPushButton("Import")
        self.import_button.setObjectName("labberDbImport")
        self.import_button.setDefault(True)
        self.import_button.clicked.connect(self.start_import)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self._close_or_stop)
        buttons.addWidget(self.import_button)
        buttons.addWidget(self.close_button)
        lay.addLayout(buttons)
        self.add_inputs(inputs)
        self._sync_buttons()

    # ---- inputs -------------------------------------------------------------------------------------
    def inputs(self) -> list[str]:
        return [self.input_list.item(i).text() for i in range(self.input_list.count())]

    def add_inputs(self, paths) -> None:
        have = set(self.inputs())
        for p in paths:
            p = str(p)
            if p and p not in have and (os.path.isdir(p) or Path(p).suffix.lower() in {".nc", ".zip", ".hdf5", ".h5"}):
                self.input_list.addItem(p)
                have.add(p)
        self._sync_buttons()

    def _start_folder(self) -> str:
        items = self.inputs()
        return str(Path(items[-1]).parent) if items else str(_settings().value("labberdb/input_dir", "") or "")

    def _add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Choose Data to Import", self._start_folder(), INPUT_FILTER)
        if files:
            _settings().setValue("labberdb/input_dir", str(Path(files[0]).parent))
        self.add_inputs(files)

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a Data Folder to Import", self._start_folder())
        if folder:
            _settings().setValue("labberdb/input_dir", folder)
            self.add_inputs([folder])

    def _remove_selected(self) -> None:
        for item in self.input_list.selectedItems():
            self.input_list.takeItem(self.input_list.row(item))
        self._sync_buttons()

    # ---- database folder ----------------------------------------------------------------------------
    def root(self) -> str:
        return self.root_edit.text().strip()

    def _browse_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose the Labber Database Folder", self.root())
        if folder:
            self.root_edit.setText(folder)

    def _new_database(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose or Create an Empty Folder for the New Database",
                                                  self.root())
        if not folder:
            return
        self.root_edit.setText(folder)
        self._run("init")

    def _reveal(self) -> None:
        root = self.root()
        if not root or not os.path.isdir(root):
            return
        if sys.platform == "win32":
            os.startfile(root)  # noqa: S606 - opening a folder the user chose
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", root])

    def _open_in_browser(self) -> None:
        root = self.root()
        target = self.parent()
        while target is not None and not hasattr(target, "open_database"):
            target = target.parent()
        if root and target is not None:
            target.open_database(root)
            target.raise_()

    # ---- run ----------------------------------------------------------------------------------------
    def busy(self) -> bool:
        return self.worker is not None

    def _sync_buttons(self) -> None:
        busy = self.busy()
        has_root = bool(self.root())
        self.import_button.setEnabled(not busy and has_root and self.input_list.count() > 0)
        self.sync_button.setEnabled(not busy and has_root)
        for w in (self.add_files_button, self.add_folder_button, self.remove_button, self.browse_button,
                  self.new_db_button, self.root_edit, self.project_edit, self.cache_check, self.resume_check,
                  self.input_list):
            w.setEnabled(not busy)
        self.open_button.setEnabled(has_root and os.path.isfile(os.path.join(self.root(), "Database.hdf5")))
        self.reveal_button.setEnabled(has_root)
        self.close_button.setText(_tr("Stop") if busy else _tr("Close"))

    def start_import(self) -> None:
        if not self.inputs():
            self.status.setText(_tr("Add files or folders to import first."))
            return
        self._run("import")

    def start_sync(self, rebuild: bool | None = None) -> None:
        if rebuild is None:
            box = QMessageBox(QMessageBox.Icon.Question, _tr("Sync Labels"),
                              _tr("Also remove labels that no file uses any more?\n\n"
                                  "For example a project name left by an earlier import that no file in this "
                                  "database has now. Database.hdf5 is backed up first.\n\n"
                                  "Yes: rebuild the labels from the files\nNo: only add new labels"),
                              QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                              | QMessageBox.StandardButton.Cancel, self)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            answer = box.exec()
            if answer == QMessageBox.StandardButton.Cancel:
                return
            rebuild = answer == QMessageBox.StandardButton.Yes
        self._run("sync", rebuild=bool(rebuild))

    def _run(self, job: str, rebuild: bool = False) -> None:
        if self.busy() or not self.root():
            return
        s = _settings()
        s.setValue("labberdb/root", self.root())
        s.setValue("labberdb/project", self.project_edit.text().strip())
        s.setValue("labberdb/local_cache", self.cache_check.isChecked())
        s.setValue("labberdb/resume", self.resume_check.isChecked())
        self.report = None
        self.log.clear()
        self.progress.setRange(0, 0)
        self.status.setText(_tr({"init": "Creating the database…", "sync": "Scanning the database…"}
                                .get(job, "Checking the inputs…")))
        self.thread = QThread(self)
        self.worker = _Worker(job, self.inputs(), self.root(), self.project_edit.text().strip(),
                              self.cache_check.isChecked(), self.resume_check.isChecked(), rebuild)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.activity.connect(self._on_activity)
        self.worker.conflict.connect(self._on_conflict)
        self.worker.result.connect(self._on_result)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self._on_finished)
        self._sync_buttons()
        self.thread.start()

    def _on_progress(self, done: int, total: int, name: str) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        if done < total:
            self.status.setText(f"{done + 1}/{total} · {name}")

    STAGES = {"checking": "Checking the source is not being written", "caching": "Copying to this computer",
              "converting": "Converting and verifying", "transferring": "Writing to the database and verifying"}

    def _on_activity(self, event) -> None:
        stage = event.get("stage", "")
        name = event.get("name", "")
        if stage == "retry":
            self.status.setText(_tr(f"Network / disk hiccup, retrying {event.get('attempt')}/{event.get('total')}")
                                + f" · {name}")
        elif stage in self.STAGES:
            self.status.setText(_tr(self.STAGES[stage]) + f" · {name}")

    def _on_conflict(self, request: _Conflict) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(_tr("A file with this name already exists"))
        box.setText(_tr("The database already has a file with this name.") + "\n\n"
                    + _tr("Source:") + f" {request.source}\n" + _tr("Existing:") + f" {request.destination}\n\n"
                    + _tr("Keep both saves the new one as:") + f" {request.suggestion.name}")
        replace = box.addButton(_tr("Replace"), QMessageBox.ButtonRole.DestructiveRole)
        skip = box.addButton(_tr("Skip This File"), QMessageBox.ButtonRole.RejectRole)
        keep = box.addButton(_tr("Keep Both"), QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(keep)
        box.setEscapeButton(skip)
        box.exec()
        clicked = box.clickedButton()
        request.action = "replace" if clicked is replace else "keep" if clicked is keep else "skip"
        request.ready.set()

    def _on_result(self, report) -> None:
        self.report = report
        self.log.setPlainText("\n".join(report_lines(report)))
        if report.get("operation") == "initialize":
            self.status.setText(_tr("Database ready:") + f" {report['database_path']}")
        elif report.get("operation") == "index":
            self.status.setText(_tr(f"Scanned {report['files']} files; the database has {report['labels']} labels."))
        else:
            self.status.setText(_tr(f"Done: {report['ok']} imported, {report.get('resumed', 0)} resumed, "
                                    f"{report.get('skipped', 0)} skipped, {report.get('pending_sync', 0)} pending, "
                                    f"{report['errors']} failed."))
            if report.get("ok") or report.get("resumed"):
                self.imported.emit(report.get("database_root", self.root()))

    def _on_failed(self, message: str) -> None:
        self.status.setText(_tr("Failed"))
        self.log.setPlainText(message)

    def _on_finished(self) -> None:
        worker, self.worker = self.worker, None
        thread, self.thread = self.thread, None
        if worker is not None:
            worker.deleteLater()
        if thread is not None:
            thread.deleteLater()
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self._sync_buttons()

    def _close_or_stop(self) -> None:
        if self.worker is not None:
            self.worker.stop = True
            self.status.setText(_tr("Stopping after the current file…"))
            return
        self.close()

    def wait(self, ms: int = 120000) -> bool:
        """Tests: block until the job finishes."""
        from PySide6.QtCore import QCoreApplication, QElapsedTimer

        timer = QElapsedTimer()
        timer.start()
        while self.worker is not None and timer.elapsed() < ms:
            QCoreApplication.processEvents()
            QThread.msleep(10)
        QCoreApplication.processEvents()
        return self.worker is None

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.worker is not None:
            self.worker.stop = True
            if self.thread is not None:
                self.thread.quit()
                self.thread.wait(120000)
        super().closeEvent(event)


def report_lines(report) -> list[str]:
    """Human-readable summary of a labber_db report (shown in the dialog log)."""
    if report.get("operation") == "initialize":
        return [_tr("Database ready:") + f" {report['database_root']}", f"Database.hdf5: {report['database_path']}"]
    if report.get("operation") == "index":
        lines = [_tr(f"Scanned {report['files']} files; the database has {report['labels']} labels.")]
        if report.get("removed_labels"):
            lines.append(_tr("Removed labels that no file uses:") + " " + ", ".join(report["removed_labels"]))
        for err in report.get("errors") or []:
            lines.append(f"✘ {err['path']}: {err['error']}")
        return lines
    lines = []
    for item in report.get("files", []):
        name = Path(str(item.get("output") or item["source"]).split(" :: ")[-1]).name
        status = item.get("status")
        if status == "error":
            lines.append(f"✘ {item['source']}")
            lines.append(f"   {item.get('error', '')}")
            if item.get("reason") == "source_active":
                lines.append("   " + _tr("The source is still being measured; stop the measurement and import again."))
        elif status == "skipped" and item.get("reason") == "not_convertible":
            lines.append(f"↷ {Path(item['source'].split(' :: ')[-1]).name} · " + _tr("not converted:")
                         + f" {_tr(item.get('error', ''))}")
        elif status == "skipped":
            lines.append(f"↷ {name} · " + _tr("skipped"))
        elif status == "resumed":
            lines.append(f"✔ {name} · " + _tr("already imported (verified)"))
        elif status == "pending_sync":
            lines.append(f"⚠ {name} · " + _tr("written; labels will sync next time (Database.hdf5 was busy)"))
        else:
            if item.get("copied_existing_labber"):
                lines.append(f"✔ {name} · " + _tr("copied Labber file"))
            else:
                freq = " / ".join(map(str, item.get("trace_points", [])))
                lines.append(f"✔ {name} · " + _tr(f"{item.get('points')} sweep points × {freq} trace points"))
                if not item.get("source_complete", True):
                    lines.append("   " + _tr(f"The measurement was not finished: kept {item.get('points')} of "
                                           f"{item.get('planned_points')} points (nothing filled in)."))
            if item.get("conflict_action") == "replace":
                lines.append("   " + _tr("replaced the existing file"))
            elif item.get("conflict_action") == "keep":
                lines.append("   " + _tr("saved under a new name (kept both)"))
            if item.get("native_scan_path_warning"):
                lines.append("   " + _tr("Note: Labber's Log Browser only scans year/month/Data_MMDD; this file is in "
                                       "a sub-folder below it."))
    lines.append("")
    if report.get("report_path"):
        lines.append(_tr("Full report:") + f" {report['report_path']}")
    if report.get("database_verified"):
        lines.append("Database.hdf5: " + str(report.get("database_path", "")))
    if report.get("database_error"):
        lines.append(str(report["database_error"]))
    return lines


def open_labber_database(parent, inputs=(), root: str = "") -> LabberDatabaseDialog:
    """Open the dialog (non-modal); keeps a reference on ``parent``."""
    dialog = LabberDatabaseDialog(parent, inputs, root)
    dialogs = getattr(parent, "_labber_db_dialogs", None)
    if dialogs is None:
        dialogs = []
        setattr(parent, "_labber_db_dialogs", dialogs)
    dialogs.append(dialog)
    dialog.finished.connect(lambda _r, d=dialog: d in dialogs and dialogs.remove(d))
    dialog.show()
    return dialog
