"""Drop data files or folders from Explorer / Finder onto any LabLogViewer window to open them (v2.1.6).

A file opens in a Viewer, a folder opens as the Database. Drags that start inside LabLogViewer (Browser list
to the pet, marks …) are left alone: only drags from other programs are taken.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QObject

DATA_SUFFIXES = (".hdf5", ".h5", ".nc", ".lm.h5")


def dropped_paths(mime) -> list[Path]:
    if mime is None or not mime.hasUrls():
        return []
    out = []
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        p = Path(url.toLocalFile())
        if p.is_dir() or p.name.lower().endswith(DATA_SUFFIXES):
            out.append(p)
    return out


class FileDropFilter(QObject):
    def __init__(self, opener: Callable[[str], None], parent=None) -> None:
        super().__init__(parent)
        self.opener = opener

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        t = event.type()
        if t == QEvent.Show and hasattr(obj, "isWindow") and obj.isWindow() and not obj.acceptDrops():
            obj.setAcceptDrops(True)                         # Viewers opened later take drops too
            return False
        if t not in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            return False
        if not hasattr(obj, "window") or event.source() is not None:
            return False                                     # a drag inside LabLogViewer
        paths = dropped_paths(event.mimeData())
        if not paths:
            return False
        if t == QEvent.Drop:
            event.acceptProposedAction()
            for p in paths[:8]:
                self.opener(str(p))
            return True
        event.acceptProposedAction()
        return True


def install(app, opener: Callable[[str], None]) -> FileDropFilter:
    f = FileDropFilter(opener, app)
    app.installEventFilter(f)
    for w in app.topLevelWidgets():
        w.setAcceptDrops(True)
    return f
