"""Modeless, read-only Labber metadata dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QPlainTextEdit, QVBoxLayout


class MetadataDialog(QDialog):
    """Read-only information supplied by the parsed Experiment model.

    LabLogViewer's editable research note intentionally lives with the
    selected Data in the Browser. Keeping this dialog read-only avoids two
    competing editors for one Data-owned external comment.
    """

    def __init__(self, _comment_store=None, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Metadata")
        self.setModal(False)
        self.resize(540, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.summary_text = QPlainTextEdit(self)
        self.summary_text.setReadOnly(True)
        self.summary_text.setMaximumBlockCount(2000)
        self.summary_text.setPlainText("(no file loaded)")
        layout.addWidget(self.summary_text)

    def set_metadata(self, text: str, _source_path=None, **_context) -> None:
        self.summary_text.setPlainText(text)

    def flush_comment(self) -> None:
        """Compatibility no-op for existing Viewer lifecycle callers."""
