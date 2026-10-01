"""English-only diagnostic dialog shown after an unclean application exit."""

from __future__ import annotations

import platform
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from app import __version__
from app.core.session_lifecycle import StartupRecovery


class RecoveryReportDialog(QDialog):
    def __init__(self, recovery: StartupRecovery, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LabLogViewer — Recovery Report")
        self.setMinimumSize(520, 330)

        lines = [
            "The previous session did not close normally.",
            "",
            "LabLogViewer started in Safe Recovery Mode.",
            "The last Database was restored where available, but previous Viewer windows and processing operations were not reopened.",
            "",
            f"Last recorded operation: {recovery.last_operation}",
            f"Operation timestamp: {recovery.last_operation_time}",
            f"Last completed safe checkpoint: {recovery.last_checkpoint}",
            f"Application version: v{__version__}",
            f"Operating system: {platform.system() or 'Unknown'}",
            f"Database: {Path(recovery.database_path).name if recovery.database_path else 'Unavailable'}",
        ]
        if recovery.session_state_unavailable:
            lines.append("Volatile session state: malformed or newer-schema data was ignored.")
        if recovery.session_corrupt:
            lines.append("A backup of the malformed session file was retained where possible.")
        if recovery.lifecycle_corrupt:
            lines.append("Lifecycle state was malformed; the application used conservative recovery defaults.")
        if recovery.last_exception:
            lines.extend([
                "",
                "Captured unhandled Python exception:",
                recovery.last_exception,
            ])
        else:
            lines.extend(["", "Possible cause: Unknown (no exception was captured)."])

        layout = QVBoxLayout(self)
        summary = QLabel(
            "LabLogViewer started in Safe Recovery Mode. Previous Viewer windows and processing operations were skipped."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        self.report_text = QPlainTextEdit()
        self.report_text.setReadOnly(True)
        self.report_text.setPlainText("\n".join(lines))
        layout.addWidget(self.report_text, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.copy_button = QPushButton("Copy Report")
        self.close_button = QPushButton("Close")
        self.copy_button.clicked.connect(self.copy_report)
        self.close_button.clicked.connect(self.close)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    def copy_report(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.report_text.toPlainText())
