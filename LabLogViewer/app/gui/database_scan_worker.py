"""
app/gui/database_scan_worker.py — v0.8

Thin QThread wrapper around app.core.database_scanner.DatabaseScanner
so the GUI stays responsive while scanning a folder (spec's
requirement: scanning must not block the GUI thread, and must not
crash on a single corrupted file).

All actual scanning logic lives in the Qt-free core module — this
file only bridges progress/completion into Qt signals.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

from app.core.database_scanner import DatabaseScanner, DatabaseScanResult, ScanCancelled
from app.core.database_index_store import DatabaseIndexStore


class DatabaseScanWorker(QThread):
    progress = Signal(int, int)          # (files_done, files_total)
    finished_scan = Signal(object)        # DatabaseScanResult
    failed = Signal(str)                   # unexpected top-level failure
    cancelled = Signal()                   # the user stopped the scan; no result is produced
    batch = Signal(object)                 # v2.1.6: list[LogEntry] parsed so far (metadata enrichment)

    def __init__(self, root_path: str, index_store: DatabaseIndexStore | None = None,
                 *, fast_listing: bool = False, priority=None, parent=None):
        super().__init__(parent)
        self.root_path = root_path
        self.index_store = index_store
        self.fast_listing = fast_listing
        self.priority = priority           # () -> folder parts parsed first (the folder on screen)

    def cancel(self) -> None:
        self.requestInterruption()

    PROGRESS_EVERY = 0.1                   # v1.0.2: at most ~10 progress updates a second (9000 files → not 9000 repaints)

    def _progress(self, done: int, total: int) -> None:
        now = time.monotonic()
        if done >= total or now - self._last_progress >= self.PROGRESS_EVERY:
            self._last_progress = now
            self.progress.emit(done, total)

    def run(self) -> None:
        self._last_progress = 0.0
        try:
            result = DatabaseScanner.scan(
                self.root_path,
                progress_callback=self._progress,
                index_store=self.index_store,
                fast_listing=self.fast_listing,
                should_cancel=self.isInterruptionRequested,
                priority=self.priority,
                batch_callback=None if self.fast_listing else (lambda entries: self.batch.emit(list(entries))),
            )
            self.finished_scan.emit(result)
        except ScanCancelled:
            self.cancelled.emit()
        except Exception as e:
            # DatabaseScanner.scan() already isolates per-file errors
            # into LogEntry.status="error" - this only fires for a
            # genuinely unexpected failure in the scan loop itself
            # (e.g. an OS-level error enumerating the root directory).
            self.failed.emit(str(e))
