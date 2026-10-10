"""The "What's New" window: shown once on the first start after an update, and from
Settings > About > What's New... at any time. Content: help/whats_new/<version>.<en|zh>.md."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QMessageBox, QTextBrowser, QVBoxLayout


class WhatsNewDialog(QDialog):
    def __init__(self, localizer, version: str, previous: str | None = None, parent=None,
                 since: str | None = None):
        super().__init__(parent)
        from app.core.app_update import whats_new_text

        text = localizer.text
        language = "zh" if str(getattr(localizer, "language", "en")).startswith("zh") else "en"
        self.setObjectName("whatsNewDialog")
        self.setWindowTitle(text("whatsnew.title").format(version=version))
        self.resize(620, 520)
        layout = QVBoxLayout(self)
        heading = QLabel(text("whatsnew.updated").format(old=previous, new=version) if previous
                         else text("whatsnew.heading").format(version=version))
        heading.setWordWrap(True)
        heading.setStyleSheet("font-size: 17px; font-weight: 700;")
        layout.addWidget(heading)
        self.body = QTextBrowser()
        self.body.setObjectName("whatsNewBody")
        self.body.setOpenExternalLinks(False)
        self.body.setMarkdown(whats_new_text(version, language, since) or text("whatsnew.none"))
        layout.addWidget(self.body, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


def after_start(result, localizer, parent=None) -> QDialog | None:
    """What the start-up check found, shown to the user (non-blocking)."""
    text = localizer.text
    if result.newer_data:
        QMessageBox.warning(parent, text("whatsnew.newer_title"), text("whatsnew.newer_data"))
        return None
    if result.error:
        QMessageBox.warning(parent, text("whatsnew.upgrade_failed_title"),
                            text("whatsnew.upgrade_failed").format(reason=result.error))
    if not result.show_whats_new:
        return None
    dialog = WhatsNewDialog(localizer, result.current_version, result.previous_version or text("whatsnew.earlier"),
                            parent, since=result.previous_version)
    dialog.show()
    dialog.raise_()
    return dialog
