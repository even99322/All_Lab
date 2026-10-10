"""Writes waiting tag and star changes into the HDF5 files in the background (one file at a time)."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

from app.core.labber_star import write_native_star
from app.core.labber_tags import NativeTagError, write_native_tags

STOP_AFTER_FAILURES = 3          # a locked or offline drive: do not hammer every remaining file


class NativeTagWriter(QObject):
    finished = Signal(object)    # {"written": int, "failed": [(relative_path, message)], "left": int}

    def __init__(self, tag_store, database_id: str, files: dict[str, str], parent=None, star_store=None):
        super().__init__(parent)
        self.tag_store, self.database_id, self.files = tag_store, database_id, dict(files)
        self.star_store = star_store

    def start(self) -> None:
        threading.Thread(target=self._run, name="native-tags", daemon=True).start()

    def _run(self) -> None:
        written, failed, left, streak = 0, [], 0, 0
        for relative_path, absolute in self.files.items():
            if streak >= STOP_AFTER_FAILURES:
                left += 1
                continue
            job = self.tag_store.native_job(self.database_id, relative_path)
            star = self.star_store.native_job(self.database_id, relative_path) if self.star_store is not None else None
            if job is None and star is None:
                continue
            try:
                if job is not None:
                    wanted, removed = job
                    write_native_tags(absolute, wanted, removed, attempts=3)
                if star is not None:
                    write_native_star(absolute, star, attempts=3)
            except Exception as error:                      # NativeTagError, or any odd file: never lose the thread
                failed.append((relative_path, str(error)))
                streak += 1
                continue
            streak = 0
            if job is not None:
                self.tag_store.native_done(self.database_id, relative_path)
            if star is not None:
                self.star_store.native_done(self.database_id, relative_path, star)
            written += 1
        try:
            self.finished.emit({"written": written, "failed": failed, "left": left})
        except RuntimeError:
            pass
