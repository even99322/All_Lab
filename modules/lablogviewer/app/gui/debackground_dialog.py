"""Compact Browser-owned De-background workflow window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout,
)

from app.core.debackground import (
    DeBackgroundInspection,
)
from app.core.database_scanner import LogEntry
from app.gui.data_picker_dialog import DataPickerDialog
from app.gui.debackground_worker import (
    DeBackgroundInspectionWorker,
    DeBackgroundProcessingWorker,
)


class DeBackgroundDialog(QDialog):
    processing_stopped = Signal()

    def __init__(self, browser, target: LogEntry | None = None):
        super().__init__(browser)
        self.browser = browser
        self.setWindowTitle("DE-BACKGROUND")
        self.setWindowModality(Qt.NonModal)
        self.resize(640, 560)
        self.setMinimumSize(560, 500)

        self.target_path: str | None = None
        self.background_path: str | None = None
        self._inspection: DeBackgroundInspection | None = None
        self._inspection_generation = 0
        self._inspection_worker: DeBackgroundInspectionWorker | None = None
        self._processing_worker: DeBackgroundProcessingWorker | None = None
        self._processing = False
        self._close_when_cancelled = False
        self._output_customized = False
        self._confirmed_output: Path | None = None
        self._last_output_path: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        root.addWidget(self._make_source_group("Target Data", "target"))
        root.addWidget(self._make_source_group("Background Data", "background"))

        parameter_row = QHBoxLayout()
        parameter_row.addWidget(QLabel("S Parameter"))
        self.channel_combo = QComboBox()
        self.channel_combo.setObjectName("debackgroundSParameter")
        self.channel_combo.setEnabled(False)
        self.channel_combo.currentTextChanged.connect(self._on_channel_changed)
        parameter_row.addWidget(self.channel_combo, 1)
        root.addLayout(parameter_row)

        self.compatibility_group = QGroupBox("COMPATIBILITY")
        self.compatibility_grid = QGridLayout(self.compatibility_group)
        self.compatibility_grid.setColumnStretch(2, 1)
        self._check_widgets: dict[str, tuple[QLabel, QLabel]] = {}
        for row, name in enumerate((
            "S Parameter", "Target", "Background shape", "Frequency points",
            "Frequency grid", "Frequency range", "Denominator",
        )):
            label = QLabel(name)
            status = QLabel("Pending")
            detail = QLabel("Select Target and Background Data.")
            detail.setWordWrap(True)
            self.compatibility_grid.addWidget(label, row, 0, Qt.AlignTop)
            self.compatibility_grid.addWidget(status, row, 1, Qt.AlignTop)
            self.compatibility_grid.addWidget(detail, row, 2, Qt.AlignTop)
            self._check_widgets[name] = (status, detail)
        root.addWidget(self.compatibility_group)

        output_group = QGroupBox("Output")
        output_layout = QVBoxLayout(output_group)
        output_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setObjectName("debackgroundOutputPath")
        self.output_edit.setPlaceholderText("Targetname_debg.hdf5")
        self.output_edit.setToolTip(
            "Complex division is calculated first. Output components use the legacy-compatible precision observed in the supplied Labber _debg file, while preserving the Target dataset dtype and layout."
        )
        self.output_edit.textEdited.connect(self._on_output_edited)
        output_row.addWidget(self.output_edit, 1)
        self.output_browse_button = QPushButton("Choose…")
        self.output_browse_button.clicked.connect(self._choose_output)
        output_row.addWidget(self.output_browse_button)
        output_layout.addLayout(output_row)
        self.precision_note = QLabel("Legacy-compatible numeric precision; Labber trace layout is retained.")
        self.precision_note.setObjectName("debackgroundPrecisionNote")
        self.precision_note.setToolTip(self.output_edit.toolTip())
        output_layout.addWidget(self.precision_note)
        root.addWidget(output_group)

        self.status_label = QLabel("Choose a Target and Background with a matching complex S-parameter.")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        root.addWidget(self.progress_bar)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.open_viewer_button = QPushButton("Open in Viewer")
        self.open_viewer_button.setObjectName("debackgroundOpenViewer")
        self.open_viewer_button.setEnabled(False)
        self.open_viewer_button.clicked.connect(self._open_in_viewer)
        self.open_viewer_button.hide()
        actions.addWidget(self.open_viewer_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel_or_close)
        actions.addWidget(self.cancel_button)
        self.generate_button = QPushButton("Generate")
        self.generate_button.setObjectName("debackgroundGenerate")
        self.generate_button.setEnabled(False)
        self.generate_button.clicked.connect(self._generate)
        actions.addWidget(self.generate_button)
        root.addLayout(actions)

        self._set_pending_checks()
        if target is not None and target.status == "ok":
            self._set_source("target", target.absolute_path)

    def _make_source_group(self, title: str, role: str) -> QGroupBox:
        group = QGroupBox(title)
        row = QHBoxLayout(group)
        label = QLabel("Not selected")
        label.setObjectName(f"debackground{role.title()}Label")
        label.setWordWrap(True)
        row.addWidget(label, 1)
        select = QPushButton("Select…")
        select.setObjectName(f"debackground{role.title()}Select")
        select.clicked.connect(lambda _checked=False, selected_role=role: self._select_source(selected_role))
        clear = QPushButton("Clear")
        clear.setObjectName(f"debackground{role.title()}Clear")
        clear.clicked.connect(lambda _checked=False, selected_role=role: self._set_source(selected_role, None))
        row.addWidget(select)
        row.addWidget(clear)
        setattr(self, f"{role}_label", label)
        setattr(self, f"{role}_select_button", select)
        setattr(self, f"{role}_clear_button", clear)
        return group

    def _select_source(self, role: str) -> None:
        result = self.browser.scan_result
        if result is None:
            QMessageBox.information(self, "Database required", "Open a Database in the Browser before selecting Data.")
            return
        current = getattr(self, f"{role}_path")
        picker = DataPickerDialog(
            result,
            "Select Target Data" if role == "target" else "Select Background Data",
            preselected_path=current,
            parent=self,
        )
        if picker.exec() == QDialog.Accepted and picker.selected_entry is not None:
            self._set_source(role, picker.selected_entry.absolute_path)

    def _set_source(self, role: str, path: str | None) -> None:
        if self._processing:
            return
        normalized = str(Path(path).expanduser().resolve()) if path else None
        setattr(self, f"{role}_path", normalized)
        label = getattr(self, f"{role}_label")
        if normalized:
            label.setText(Path(normalized).name)
            label.setToolTip(normalized)
        else:
            label.setText("Not selected")
            label.setToolTip("")
        self._last_output_path = None
        self.open_viewer_button.setEnabled(False)
        self.open_viewer_button.hide()
        if role == "target" and normalized and not self._output_customized:
            self.output_edit.setText(str(Path(normalized).with_name(f"{Path(normalized).stem}_debg.hdf5")))
        self._schedule_inspection()

    def _on_output_edited(self, _text: str) -> None:
        self._output_customized = True
        self._confirmed_output = None                  # typed by hand: ask again if it is taken
        self._sync_generate_enabled()

    def _choose_output(self) -> None:
        target = Path(self.target_path) if self.target_path else Path.home() / "output_debg.hdf5"
        path, _selected_filter = QFileDialog.getSaveFileName(
            self, "Choose De-background Output", str(target.with_name(f"{target.stem}_debg.hdf5")),
            "Labber HDF5 (*.hdf5 *.h5)",
        )
        if path:
            output = Path(path)
            if not output.suffix:
                output = output.with_suffix(".hdf5")
            self._confirmed_output = output if output.exists() else None
            self._output_customized = True
            self.output_edit.setText(str(output))
            self._sync_generate_enabled()

    def _schedule_inspection(self) -> None:
        self._inspection_generation += 1
        self._inspection = None
        self._populate_channel_options((), None)
        self._set_pending_checks()
        self._sync_generate_enabled()
        if not self.target_path or not self.background_path:
            self.status_label.setText("Choose both Target and Background Data.")
            return
        self.status_label.setText("Checking S parameters, Frequency grid, and denominator…")
        worker = DeBackgroundInspectionWorker(
            self._inspection_generation,
            self.target_path,
            self.background_path,
            None,
            parent=self.browser,
        )
        worker.inspected.connect(self._on_inspected)
        worker.failed.connect(self._on_inspection_failed)
        worker.finished.connect(worker.deleteLater)
        self._inspection_worker = worker
        worker.start()

    def _on_channel_changed(self, channel: str) -> None:
        if not channel or not self.target_path or not self.background_path:
            self._sync_generate_enabled()
            return
        if self._inspection is not None and channel == self._inspection.selected_channel:
            return
        self._inspection_generation += 1
        generation = self._inspection_generation
        self._inspection = None
        self._set_pending_checks()
        self.status_label.setText(f"Checking {channel} compatibility…")
        worker = DeBackgroundInspectionWorker(
            generation, self.target_path, self.background_path, channel, parent=self.browser,
        )
        worker.inspected.connect(self._on_inspected)
        worker.failed.connect(self._on_inspection_failed)
        worker.finished.connect(worker.deleteLater)
        self._inspection_worker = worker
        worker.start()
        self._sync_generate_enabled()

    def _populate_channel_options(self, options: tuple[str, ...], selected: str | None) -> None:
        blocker = QSignalBlocker(self.channel_combo)
        self.channel_combo.clear()
        self.channel_combo.addItems(list(options))
        if selected:
            index = self.channel_combo.findText(selected)
            if index >= 0:
                self.channel_combo.setCurrentIndex(index)
        self.channel_combo.setEnabled(bool(options) and not self._processing)
        del blocker

    def _on_inspected(self, generation: int, inspection: DeBackgroundInspection) -> None:
        if generation != self._inspection_generation:
            return
        current_target, current_background = self.target_path, self.background_path
        if inspection.target_path != current_target or inspection.background_path != current_background:
            return
        self._inspection = inspection
        self._populate_channel_options(inspection.channel_options, inspection.selected_channel)
        self._render_checks(inspection.checks)
        if inspection.can_generate:
            self.status_label.setText("Compatible. Complex-domain division will use the acquired measurements only.")
        else:
            self.status_label.setText("Generation is disabled until all compatibility checks pass.")
        self._sync_generate_enabled()

    def _on_inspection_failed(self, generation: int, message: str) -> None:
        if generation != self._inspection_generation:
            return
        self._inspection = None
        self._render_checks(())
        self._check_widgets["S Parameter"][0].setText("FAIL")
        self._check_widgets["S Parameter"][1].setText(message)
        self.status_label.setText("Could not validate the selected files.")
        self._sync_generate_enabled()

    def _set_pending_checks(self) -> None:
        for status, detail in self._check_widgets.values():
            status.setText("Pending")
            detail.setText("Select Target and Background Data.")

    def _render_checks(self, checks) -> None:
        statuses = {check.label: check for check in checks}
        for name, (status_label, detail_label) in self._check_widgets.items():
            check = statuses.get(name)
            if check is None:
                status_label.setText("Pending")
                detail_label.setText("Waiting for compatibility check.")
                continue
            status_label.setText({"ok": "OK", "fail": "MISMATCH", "warning": "WARNING", "pending": "Pending"}.get(check.status, "Pending"))
            detail_label.setText(check.detail)
            detail_label.setToolTip(check.detail)

    def _sync_generate_enabled(self) -> None:
        channel = self.channel_combo.currentText()
        self.generate_button.setEnabled(
            not self._processing
            and self._inspection is not None
            and self._inspection.can_generate
            and bool(channel)
            and channel == self._inspection.selected_channel
            and bool(self.output_edit.text().strip())
        )
        self.target_select_button.setEnabled(not self._processing)
        self.target_clear_button.setEnabled(not self._processing and self.target_path is not None)
        self.background_select_button.setEnabled(not self._processing)
        self.background_clear_button.setEnabled(not self._processing and self.background_path is not None)
        self.output_edit.setEnabled(not self._processing)
        self.output_browse_button.setEnabled(not self._processing)
        self.channel_combo.setEnabled(not self._processing and self.channel_combo.count() > 0)

    def _generate(self) -> None:
        if not self._inspection or not self._inspection.can_generate:
            return
        output_text = self.output_edit.text().strip()
        if not output_text:
            return
        output = Path(output_text).expanduser()
        if not output.suffix:
            output = output.with_suffix(".hdf5")
            self.output_edit.setText(str(output))
        if output.suffix.casefold() not in {".h5", ".hdf5"}:
            QMessageBox.warning(self, "Invalid output", "Choose an output path ending in .h5 or .hdf5.")
            return
        protected = {Path(self.target_path).resolve(), Path(self.background_path).resolve()}
        if output.resolve() in protected:
            QMessageBox.warning(self, "Invalid output", "Output cannot replace the Target or Background source file.")
            return
        overwrite = False
        if output.exists() and output == self._confirmed_output:
            overwrite = True                           # "Overwrite" was already chosen in the save dialog
        elif output.exists():
            from app.gui.save_target import _text, numbered

            keep = numbered(output)
            box = QMessageBox(self)
            box.setWindowTitle(_text("save.exists_title"))
            box.setText(_text("save.exists_text").format(name=output.name, folder=str(output.parent)))
            box.setInformativeText(_text("save.exists_info").format(new_name=keep.name))
            replace = box.addButton(_text("save.overwrite"), QMessageBox.DestructiveRole)
            keep_both = box.addButton(_text("save.keep_both"), QMessageBox.AcceptRole)
            another = box.addButton(_text("save.choose_another"), QMessageBox.ActionRole)
            box.addButton(_text("save.cancel"), QMessageBox.RejectRole)
            box.setDefaultButton(keep_both)
            box.exec()
            clicked = box.clickedButton()
            if clicked is replace:
                overwrite = True
            elif clicked is keep_both:
                output = keep
                self.output_edit.setText(str(output))
            elif clicked is another:
                self._choose_output()
                return
            else:
                return

        self._processing = True
        journal = getattr(self, "operation_journal", None)
        if callable(journal):
            journal("De-background processing started")
        self._last_output_path = None
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.status_label.setText("Preparing processing…")
        self.cancel_button.setText("Cancel Processing")
        self.open_viewer_button.setEnabled(False)
        self._sync_generate_enabled()
        worker = DeBackgroundProcessingWorker(
            self.target_path,
            self.background_path,
            self.channel_combo.currentText(),
            str(output),
            overwrite=overwrite,
            parent=self,
        )
        worker.progress.connect(self._on_progress)
        worker.succeeded.connect(self._on_processing_succeeded)
        worker.failed.connect(self._on_processing_failed)
        worker.cancelled.connect(self._on_processing_cancelled)
        worker.finished.connect(self._on_worker_finished)
        self._processing_worker = worker
        worker.start()

    def _on_progress(self, value: int, message: str) -> None:
        self.progress_bar.setValue(value)
        self.status_label.setText(message)

    def _on_processing_succeeded(self, path: str) -> None:
        self._processing = False
        self._last_output_path = path
        self.progress_bar.setValue(100)
        self.status_label.setText(f"De-background completed.\n{Path(path).name}")
        self.cancel_button.setText("Done")
        self.cancel_button.setEnabled(True)
        self.open_viewer_button.show()
        self.open_viewer_button.setEnabled(True)
        self._sync_generate_enabled()
        self.browser._on_debackground_completed(path)

    def _on_processing_failed(self, message: str) -> None:
        self._processing = False
        self.status_label.setText(f"De-background failed.\n{message}")
        self.cancel_button.setText("Cancel")
        self.cancel_button.setEnabled(True)
        self.progress_bar.hide()
        self._sync_generate_enabled()

    def _on_processing_cancelled(self) -> None:
        self._processing = False
        self.status_label.setText("Processing cancelled. No partial output was kept.")
        self.cancel_button.setText("Cancel")
        self.cancel_button.setEnabled(True)
        self.progress_bar.hide()
        self._sync_generate_enabled()
        if self._close_when_cancelled:
            QTimer.singleShot(0, self.close)

    def _on_worker_finished(self) -> None:
        self._processing_worker = None
        if self._close_when_cancelled:
            self.processing_stopped.emit()
            QTimer.singleShot(0, self.close)

    def _cancel_or_close(self) -> None:
        if self._processing_worker is not None and self._processing_worker.isRunning():
            self._processing_worker.request_cancel()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Cancelling safely…")
            return
        self.close()

    def _open_in_viewer(self) -> None:
        if self._last_output_path:
            self.browser.open_generated_output(self._last_output_path)

    @property
    def is_processing(self) -> bool:
        return self._processing_worker is not None and self._processing_worker.isRunning()

    def cancel_for_shutdown(self) -> None:
        self._close_when_cancelled = True
        worker = self._processing_worker
        if worker is not None and worker.isRunning():
            worker.request_cancel()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Cancelling safely before application shutdown…")
        else:
            self.processing_stopped.emit()

    def closeEvent(self, event: QCloseEvent) -> None:
        worker = self._processing_worker
        if worker is not None and worker.isRunning():
            self._close_when_cancelled = True
            worker.request_cancel()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Cancelling safely before closing…")
            event.ignore()
            return
        event.accept()
