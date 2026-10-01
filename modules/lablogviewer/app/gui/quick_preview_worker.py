"""Background worker for loading Browser quick previews."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from app.core.quick_preview import build_quick_preview


class QuickPreviewWorker(QThread):
    preview_ready = Signal(object, object)
    preview_failed = Signal(object, str)

    def __init__(self, path: str, display_state: dict | None = None, parent=None):
        super().__init__(parent)
        self.path = path
        self.display_state = dict(display_state) if isinstance(display_state, dict) else None

    def run(self) -> None:
        try:
            self.preview_ready.emit(self, build_quick_preview(self.path, self.display_state))
        except Exception as exc:
            self.preview_failed.emit(self, str(exc))
