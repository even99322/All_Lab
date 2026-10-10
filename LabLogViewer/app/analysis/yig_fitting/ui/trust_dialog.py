"""Ask the user before an unknown Python model file is imported."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout,
)

from app.localization import get_localization_manager

from ..core import trust


class TrustDialog(QDialog):
    def __init__(self, path: Path, report: trust.ScanReport, parent=None):
        super().__init__(parent)
        text = get_localization_manager().text
        self.setWindowTitle(text("trust.title"))
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        intro = QLabel(text("trust.intro").format(name=path.name))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        location = QLabel(str(path))
        location.setWordWrap(True)
        location.setObjectName("trustPath")
        layout.addWidget(location)
        if report.warnings:
            self.warnings = QLabel(text("trust.warnings") + "\n" + report.describe(report.warnings))
            self.warnings.setObjectName("trustWarnings")
            self.warnings.setWordWrap(True)
            layout.addWidget(self.warnings)
        self.code = QPlainTextEdit()
        self.code.setReadOnly(True)
        self.code.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.code.setFont(font)
        try:
            source = trust.read_source(path)
        except (OSError, UnicodeDecodeError, SyntaxError, LookupError) as error:
            source = str(error)
        width = len(str(source.count("\n") + 1))
        self.code.setPlainText("\n".join(f"{index:>{width}}  {line}"
                                         for index, line in enumerate(source.splitlines(), 1)))
        layout.addWidget(self.code, 1)
        buttons = QDialogButtonBox()
        self.trust_button = buttons.addButton(text("trust.trust"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def ask_to_trust(path: Path, report: trust.ScanReport) -> bool:
    dialog = TrustDialog(path, report, QApplication.activeWindow())
    return dialog.exec() == QDialog.DialogCode.Accepted


def install() -> None:
    trust.set_prompt(ask_to_trust)
