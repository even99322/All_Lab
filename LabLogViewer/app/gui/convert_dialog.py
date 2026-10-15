"""v1.0.7: File ▸ Convert — Lab Control .nc ⇄ Labber .hdf5 for one or many files (app/core/labber_convert.py)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QThread, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from app.core import labber_convert as lc

TITLES = {"hdf5": "Lab Control .nc  →  Labber .hdf5", "nc": "Labber .hdf5  →  Lab Control .nc"}
PICK = {"hdf5": "Lab Control files (*.nc *.lm.h5);;All files (*)",
        "nc": "Labber log files (*.hdf5 *.h5);;All files (*)"}


def _tr(text: str) -> str:
    try:
        from app.localization.manager import get_localization_manager, translate_text

        return translate_text(text, get_localization_manager().language)
    except Exception:  # noqa: BLE001
        return text


def _settings() -> QSettings:
    return QSettings("CCU QEL", "LabLogViewer")


def pick_files(parent, to: str, folder: str = "") -> list[str]:
    files, _ = QFileDialog.getOpenFileNames(parent, "Choose Files to Convert", folder, PICK[to])
    return files


class _Worker(QThread):
    step = Signal(int, int, str)
    one = Signal(object)

    def __init__(self, files, to, folder, engine, python):
        super().__init__()
        self.files, self.to, self.folder, self.engine, self.python = files, to, folder, engine, python
        self.stop = False

    def run(self) -> None:
        for k, path in enumerate(self.files):
            if self.stop:
                break
            self.step.emit(k, len(self.files), str(path))
            self.one.emit(lc.convert_file(path, self.to, self.folder, self.engine, self.python))
        self.step.emit(len(self.files), len(self.files), "")


class ConvertDialog(QDialog):
    """Convert files; ``to`` is "hdf5" (to Labber) or "nc" (to Lab Control). Outputs never overwrite."""

    converted = Signal(list)                  # output paths, when a batch finishes

    def __init__(self, parent=None, to: str = "hdf5", files=()):
        super().__init__(parent)
        self.to = to
        self.worker: _Worker | None = None
        self.outputs: list[str] = []
        self.results: list[lc.Result] = []
        self.setObjectName("convertDialog")
        self.setWindowTitle("Convert Files")
        self.resize(640, 520)
        s = _settings()
        lay = QVBoxLayout(self)
        head = QLabel(TITLES[to])
        head.setObjectName("convertDirection")
        head.setStyleSheet("font-weight: 600; font-size: 14px;")
        lay.addWidget(head)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        lay.addWidget(self.file_list, 1)
        row = QHBoxLayout()
        self.add_button = QPushButton("Add Files...")
        self.add_button.clicked.connect(self._add_files)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self._remove_selected)
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        lay.addLayout(row)

        out = QGridLayout()
        self.same_folder = QRadioButton("Same folder as each file")
        self.other_folder = QRadioButton("This folder:")
        group = QButtonGroup(self)
        group.addButton(self.same_folder)
        group.addButton(self.other_folder)
        self.folder_edit = QLineEdit(str(s.value("convert/folder", "") or ""))
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse_folder)
        out.addWidget(QLabel("Save to:"), 0, 0)
        out.addWidget(self.same_folder, 0, 1, 1, 2)
        out.addWidget(self.other_folder, 1, 1)
        out.addWidget(self.folder_edit, 1, 2)
        out.addWidget(browse, 1, 3)
        (self.other_folder if s.value("convert/folder_mode", "same") == "other" else self.same_folder).setChecked(True)
        lay.addLayout(out)

        self.labber_box = QWidget()
        form = QFormLayout(self.labber_box)
        form.setContentsMargins(0, 0, 0, 0)
        self.engine_combo = QComboBox()
        for text, key in (("Automatic (Labber API when available)", "auto"), ("Labber API", "api"),
                          ("Built-in", "builtin")):
            self.engine_combo.addItem(text, key)
        engine = str(s.value("convert/engine", "auto") or "auto")
        self.engine_combo.setCurrentIndex(max(0, lc.ENGINES.index(engine) if engine in lc.ENGINES else 0))
        form.addRow("Writer:", self.engine_combo)
        prow = QHBoxLayout()
        self.python_edit = QLineEdit(str(s.value("convert/labber_python", "") or ""))
        self.python_edit.setPlaceholderText(lc.DEFAULT_LABBER_PYTHON)
        pbrowse = QPushButton("Browse...")
        pbrowse.clicked.connect(self._browse_python)
        prow.addWidget(self.python_edit, 1)
        prow.addWidget(pbrowse)
        holder = QWidget()
        holder.setLayout(prow)
        prow.setContentsMargins(0, 0, 0, 0)
        form.addRow("Labber Python:", holder)
        note = QLabel("The Labber API needs the lab's Labber Python (3.8) environment; without it the built-in "
                      "writer makes the same file layout.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        form.addRow(note)
        self.labber_box.setVisible(to == "hdf5")
        lay.addWidget(self.labber_box)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        lay.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        lay.addWidget(self.log, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.convert_button = QPushButton("Convert")
        self.convert_button.setDefault(True)
        self.convert_button.clicked.connect(self.start)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self._close_or_cancel)
        buttons.addWidget(self.convert_button)
        buttons.addWidget(self.close_button)
        lay.addLayout(buttons)
        self.add_files(files)

    # ---- files --------------------------------------------------------------------------------------
    def files(self) -> list[str]:
        return [self.file_list.item(i).text() for i in range(self.file_list.count())]

    def add_files(self, files) -> None:
        have = set(self.files())
        for f in files:
            f = str(f)
            if f and f not in have and lc.kind_of(f) == ("nc" if self.to == "hdf5" else "hdf5"):
                self.file_list.addItem(f)
                have.add(f)
        self.convert_button.setEnabled(self.file_list.count() > 0 and self.worker is None)

    def _add_files(self) -> None:
        start = str(Path(self.files()[-1]).parent) if self.files() else ""
        self.add_files(pick_files(self, self.to, start))

    def _remove_selected(self) -> None:
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))
        self.convert_button.setEnabled(self.file_list.count() > 0 and self.worker is None)

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Save Converted Files To", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)
            self.other_folder.setChecked(True)

    def _browse_python(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Labber Python", self.python_edit.text(),
                                              "Python (python*.exe python*);;All files (*)")
        if path:
            self.python_edit.setText(path)

    # ---- run ----------------------------------------------------------------------------------------
    def folder(self) -> str | None:
        return self.folder_edit.text().strip() or None if self.other_folder.isChecked() else None

    def start(self) -> None:
        files = self.files()
        if not files or self.worker is not None:
            return
        folder = self.folder()
        if self.other_folder.isChecked() and not folder:
            self.log.appendPlainText(_tr("Choose the folder to save to."))
            return
        s = _settings()
        s.setValue("convert/folder_mode", "other" if self.other_folder.isChecked() else "same")
        s.setValue("convert/folder", self.folder_edit.text().strip())
        engine = self.engine_combo.currentData() or "auto"
        s.setValue("convert/engine", engine)
        s.setValue("convert/labber_python", self.python_edit.text().strip())
        self.outputs, self.results = [], []
        self.progress.setRange(0, len(files))
        self.progress.setValue(0)
        self.convert_button.setEnabled(False)
        self.close_button.setText(_tr("Cancel"))
        self.worker = _Worker(files, self.to, folder, engine if self.to == "hdf5" else "builtin",
                              self.python_edit.text().strip())
        self.worker.step.connect(self._on_step)
        self.worker.one.connect(self._on_result)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_step(self, k: int, total: int, path: str) -> None:
        self.progress.setValue(k)
        if path:
            self.log.appendPlainText(f"… {Path(path).name}")

    def _on_result(self, res: lc.Result) -> None:
        self.results.append(res)
        name = Path(res.source).name
        if res.ok:
            self.outputs.append(res.output)
            how = {"api": "Labber API", "builtin": _tr("built-in"), "netcdf": "NetCDF"}.get(res.engine, res.engine)
            self.log.appendPlainText(f"✔ {name}  →  {res.output}   ({how})")
        else:
            self.log.appendPlainText(f"✘ {name}: {_tr(res.error)}")
        for note in res.notes:
            self.log.appendPlainText(f"   ⚠ {_tr(note)}")

    def _on_finished(self) -> None:
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.deleteLater()
        ok = sum(r.ok for r in self.results)
        failed = len(self.results) - ok
        self.log.appendPlainText(_tr(f"Done: {ok} converted, {failed} failed."))
        self.close_button.setText(_tr("Close"))
        self.convert_button.setEnabled(self.file_list.count() > 0)
        if self.outputs:
            self.converted.emit(list(self.outputs))

    def wait(self, ms: int = 60000) -> bool:
        """Tests: block until the batch finishes."""
        from PySide6.QtCore import QCoreApplication, QElapsedTimer

        timer = QElapsedTimer()
        timer.start()
        while self.worker is not None and timer.elapsed() < ms:
            QCoreApplication.processEvents()
            QThread.msleep(10)
        QCoreApplication.processEvents()
        return self.worker is None

    def _close_or_cancel(self) -> None:
        if self.worker is not None:
            self.worker.stop = True
            self.log.appendPlainText(_tr("Stopping after the current file…"))
            return
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.worker is not None:
            self.worker.stop = True
            self.worker.wait(120000)
        super().closeEvent(event)


def open_convert(parent, to: str, files=()) -> ConvertDialog | None:
    """Open the dialog (asking for files first when none were given). Keeps a reference on ``parent``."""
    files = [str(f) for f in files if lc.kind_of(f) == ("nc" if to == "hdf5" else "hdf5")]
    if not files:
        files = pick_files(parent, to)
        if not files:
            return None
    dialog = ConvertDialog(parent, to, files)
    dialogs = getattr(parent, "_convert_dialogs", None)
    if dialogs is None:
        dialogs = []
        setattr(parent, "_convert_dialogs", dialogs)
    dialogs.append(dialog)
    dialog.finished.connect(lambda _r, d=dialog: d in dialogs and dialogs.remove(d))
    dialog.show()
    return dialog
