"""Unresponsiveness watchdog.

A background thread checks that the UI thread keeps processing events. If it
stops for ``threshold`` seconds, the stacks of all threads are written to
``<data folder>/logs/unresponsive-<process>-<time>.txt`` (what was running
when it froze). When the UI recovers, the report path is announced on the
application's status bars. Nothing is killed or changed.
"""

from __future__ import annotations

import faulthandler
import threading
import time
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

_watchdog = None


class Watchdog(QObject):
    recovered = Signal(float, str)

    def __init__(self, app, name: str, threshold: float = 8.0, parent=None):
        super().__init__(parent or app)
        self.name = name
        self.threshold = threshold
        self._beat = time.monotonic()
        self._report: str | None = None
        self._stalled_since: float | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._heartbeat)
        self._timer.start()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="lablogviewer-watchdog", daemon=True)
        self._thread.start()
        app.aboutToQuit.connect(self._stop.set)
        self.recovered.connect(_announce)

    def _heartbeat(self) -> None:
        now = time.monotonic()
        if self._report is not None and self._stalled_since is not None:
            self.recovered.emit(now - self._stalled_since, self._report)
            self._report = None
        self._stalled_since = None
        self._beat = now

    def _watch(self) -> None:
        while not self._stop.wait(1.0):
            stalled = time.monotonic() - self._beat
            if stalled >= self.threshold and self._report is None:
                self._stalled_since = self._beat
                self._report = self._write_report(stalled)

    def _write_report(self, stalled: float) -> str:
        try:
            from app.core.data_location import data_root

            folder = data_root() / "logs"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"unresponsive-{self.name}-{datetime.now():%Y%m%d-%H%M%S}.txt"
            with path.open("w", encoding="utf-8") as stream:
                stream.write(f"LabLogViewer ({self.name}) UI unresponsive for {stalled:.1f} s\n\n")
                stream.flush()
                faulthandler.dump_traceback(file=stream, all_threads=True)
            return str(path)
        except Exception:                                   # a watchdog must never raise
            return ""


def _announce(seconds: float, report: str) -> None:
    from PySide6.QtWidgets import QApplication, QMainWindow

    text = f"LabLogViewer was unresponsive for {seconds:.0f} s."
    if report:
        text += f" Diagnostic report: {report}"
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, QMainWindow) and widget.isVisible():
            widget.statusBar().showMessage(text, 15000)


def install_watchdog(app, name: str = "main", threshold: float = 8.0) -> Watchdog:
    global _watchdog
    if _watchdog is None:
        _watchdog = Watchdog(app, name, threshold)
    return _watchdog
