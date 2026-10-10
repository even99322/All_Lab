"""Auto refresh of the opened database (Settings > General, default every 2 s).

Every tick a background thread compares file sizes and modification times (no file is
opened) in the folder on screen and the folders written to last; every 30 s it walks
the whole database to find new folders. Only new or changed files are read again
(the per-file cache keeps everything else), so a refresh costs almost nothing when
nothing changed. Then, in the window:

- the list shows new / changed / removed measurements; selection and scroll stay;
- the folder tree is rebuilt only when a folder appeared or disappeared;
- a Viewer showing a file that grew (a running measurement) reloads it and keeps its view.

A file Labber is still writing may not be readable yet: it is listed by name and tried
again on the next tick instead of being marked as unreadable. Nothing runs while a
full scan is running, while the window is hidden, or while a mouse button is held.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication

from app.core import change_watch
from app.core.database_scanner import DatabaseScanner, LogEntry

CHOICES = (0, 1, 2, 5)                 # seconds; 0 = off
DEFAULT = 2
FULL_EVERY = 30.0                      # seconds between whole-database walks
WRITING_WINDOW = 120.0                 # a file modified this recently may still be written


def known_snapshot(entries: list[LogEntry]) -> change_watch.Snapshot:
    return {entry.relative_path: (entry.size_bytes, entry.mtime) for entry in entries}


def read_changed(root: Path, paths: list[str], now: float | None = None) -> dict[str, LogEntry]:
    """Parse the new / changed files; one still being written becomes a retry placeholder."""
    now = time.time() if now is None else now
    entries: dict[str, LogEntry] = {}
    for relative in paths:
        path = root / relative
        entry = DatabaseScanner._scan_one(root, path)
        if entry.status == "error" and entry.mtime and now - entry.mtime < WRITING_WINDOW:
            # probably still being written: show it by name, try again next tick
            entry = replace(entry, status="ok", error_message=None, mtime=-1.0, metadata_complete=False)
        entries[relative] = entry
    return entries


class AutoRefresher(QObject):
    finished = Signal(object)          # (changes, entries, full, generation) from the worker thread

    def __init__(self, browser, store=None):
        super().__init__(browser)
        self.browser = browser
        self.store = store
        self._busy = False
        self._last_full = 0.0
        self._generation = 0
        self._viewer_reloaded: dict[int, float] = {}
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.finished.connect(self._apply, Qt.ConnectionType.QueuedConnection)
        self.apply_setting()

    # -- setting ---------------------------------------------------------------------------------
    def interval(self) -> int:
        seconds = self.store.auto_refresh_seconds() if self.store is not None and hasattr(
            self.store, "auto_refresh_seconds") else DEFAULT
        return seconds if seconds in CHOICES else DEFAULT

    def apply_setting(self) -> None:
        import os

        seconds = self.interval()
        # Tests tick by hand: a timer in every Browser a test leaves open would slow the suite.
        under_test = "PYTEST_CURRENT_TEST" in os.environ and os.environ.get("LABLOGVIEWER_AUTO_REFRESH") != "1"
        if seconds <= 0 or under_test:
            self.timer.stop()
        else:
            self.timer.start(int(seconds * 1000))

    # -- tick --------------------------------------------------------------------------------------
    def _idle(self) -> bool:
        browser = self.browser
        workers = (getattr(browser, "_scan_worker", None), getattr(browser, "_enrichment_worker", None))
        return (getattr(browser, "scan_result", None) is not None and not self._busy
                and not any(w is not None and w.isRunning() for w in workers)
                and browser.isVisible() and not browser.isMinimized()
                and QApplication.mouseButtons() == Qt.MouseButton.NoButton)

    def tick(self, *, force_full: bool = False, wait: bool = False) -> bool:
        """One check (``wait`` runs it in this thread; tests). Returns False when skipped."""
        if not self._idle():
            return False
        result = self.browser.scan_result
        root = Path(result.root_path)
        known = known_snapshot(result.entries)
        now = time.monotonic()
        full = force_full or now - self._last_full >= FULL_EVERY
        folders = None if full else change_watch.hot_folders(known, self._current_folder())
        if full:
            self._last_full = now
        self._busy = True
        self._generation += 1
        generation = self._generation

        def work():
            disk_folders: set | None = set() if folders is None else None
            try:
                seen = (change_watch.full_snapshot(root, disk_folders) if folders is None
                        else change_watch.folder_snapshot(root, folders))
                changes = change_watch.compare(known, seen, folders)
                entries = read_changed(root, changes.added + changes.changed) if changes else {}
            except Exception:                                   # never disturb the window
                changes, entries, disk_folders = change_watch.Changes(), {}, None
            self.finished.emit((changes, entries, disk_folders, generation))

        if wait:
            work()
            QApplication.processEvents()
        else:
            threading.Thread(target=work, name="llv-auto-refresh", daemon=True).start()
        return True

    def _current_folder(self) -> tuple[str, ...] | None:
        item = self.browser.folder_tree.currentItem() if hasattr(self.browser, "folder_tree") else None
        parts = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        return tuple(parts) if isinstance(parts, (list, tuple)) else None

    # -- apply ------------------------------------------------------------------------------------
    def _apply(self, payload) -> None:
        changes, entries, disk_folders, generation = payload
        self._busy = False
        browser = self.browser
        result = browser.scan_result
        if result is None or generation != self._generation:
            return
        if disk_folders is not None and disk_folders != browser.disk_folders:
            browser.disk_folders = set(disk_folders)          # empty folders show in the tree too
            if not changes:
                browser.apply_auto_refresh(result, rebuild_tree=True, new_entries=[])
                return
            folders_changed = True
        else:
            folders_changed = False
        if not changes:
            return
        by_path = {entry.relative_path: entry for entry in result.entries}
        folders_before = {entry.folder_parts for entry in result.entries}
        for path in changes.removed:
            by_path.pop(path, None)
        by_path.update(entries)
        result.entries = sorted(by_path.values(), key=lambda entry: entry.relative_path.lower())
        folders_after = {entry.folder_parts for entry in result.entries}
        new_entries = [entries[path] for path in changes.added if path in entries]
        browser.apply_auto_refresh(result, rebuild_tree=folders_changed or folders_before != folders_after,
                                   new_entries=new_entries)
        self._reload_viewers([result.root_path + "/" + path for path in changes.changed])
        added, changed, removed = len(changes.added), len(changes.changed), len(changes.removed)
        browser.statusBar().showMessage(browser.auto_refresh_message(added, changed, removed), 4000)

    def _reload_viewers(self, paths: list[str]) -> None:
        if not paths:
            return
        from app.core.data_identity import stable_data_identity
        from app.gui.main_window import MainWindow

        changed = {stable_data_identity(path) for path in paths}
        now = time.monotonic()
        for window in QApplication.topLevelWidgets():
            if not isinstance(window, MainWindow) or getattr(window, "_network_mirror", False):
                continue
            experiment = getattr(window, "experiment", None)
            if experiment is None or experiment.data_identity not in changed:
                continue
            if now - self._viewer_reloaded.get(id(window), 0.0) < max(1.0, self.interval()):
                continue
            self._viewer_reloaded[id(window)] = now
            window.reload_keeping_view()
