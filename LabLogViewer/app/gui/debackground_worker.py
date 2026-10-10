"""QThread wrappers around the Qt-free De-background service."""

from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from app.core.debackground import inspect_debackground, process_debackground


class DeBackgroundInspectionWorker(QThread):
    inspected = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, generation: int, target_path: str, background_path: str,
                 selected_channel: str | None, parent=None):
        super().__init__(parent)
        self.generation = generation
        self.target_path = target_path
        self.background_path = background_path
        self.selected_channel = selected_channel

    def run(self) -> None:
        try:
            result = inspect_debackground(
                self.target_path, self.background_path, self.selected_channel,
            )
            self.inspected.emit(self.generation, result)
        except Exception as error:
            self.failed.emit(self.generation, str(error))


class DeBackgroundProcessingWorker(QThread):
    progress = Signal(int, str)
    succeeded = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, target_path: str, background_path: str, channel: str,
                 output_path: str, overwrite: bool = False, parent=None):
        super().__init__(parent)
        self.target_path = target_path
        self.background_path = background_path
        self.channel = channel
        self.output_path = output_path
        self.overwrite = overwrite
        self.cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            result = process_debackground(
                self.target_path,
                self.background_path,
                self.channel,
                self.output_path,
                overwrite=self.overwrite,
                cancel_event=self.cancel_event,
                progress=self.progress.emit,
            )
            self.succeeded.emit(str(result))
        except Exception as error:
            from app.core.debackground import DeBackgroundCancelled
            if isinstance(error, DeBackgroundCancelled):
                self.cancelled.emit()
            else:
                self.failed.emit(str(error))
