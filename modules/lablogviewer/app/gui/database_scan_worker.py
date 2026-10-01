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

from PySide6.QtCore import QThread, Signal

from app.core.database_scanner import DatabaseScanner, DatabaseScanResult
from app.core.database_index_store import DatabaseIndexStore


class DatabaseScanWorker(QThread):
    progress = Signal(int, int)          # (files_done, files_total)
    finished_scan = Signal(object)        # DatabaseScanResult
    failed = Signal(str)                   # unexpected top-level failure

    def __init__(self, root_path: str, index_store: DatabaseIndexStore | None = None,
                 *, fast_listing: bool = False, parent=None):
        super().__init__(parent)
        self.root_path = root_path
        self.index_store = index_store
        self.fast_listing = fast_listing

    def run(self) -> None:
        try:
            result = DatabaseScanner.scan(
                self.root_path,
                progress_callback=lambda done, total: self.progress.emit(done, total),
                index_store=self.index_store,
                fast_listing=self.fast_listing,
            )
            self.finished_scan.emit(result)
        except Exception as e:
            # DatabaseScanner.scan() already isolates per-file errors
            # into LogEntry.status="error" - this only fires for a
            # genuinely unexpected failure in the scan loop itself
            # (e.g. an OS-level error enumerating the root directory).
            self.failed.emit(str(e))
