"""Lazy, plot-only drag-to-share support.

The helper deliberately accepts a renderer callback from the Viewer.  It has
no knowledge of Labber files or scientific data; a drag begins only after the
standard Qt movement threshold and carries a PNG rendered by the same path as
Copy/Save Plot.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import uuid
from typing import Callable

from PySide6.QtCore import QEvent, QMimeData, QObject, QPoint, Qt, QUrl
from PySide6.QtGui import QDrag, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from app.core.external_state import default_state_path
from app.gui.plot_export import write_png


def _safe_stem(value: str) -> str:
    """Return a portable, short filename stem while preserving useful names."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return (cleaned or "LabLogViewer_plot")[:120]


class DragShareManager:
    """Own only LabLogViewer-created temporary PNGs.

    Files remain available after a drag returns because Finder and office
    applications may consume file URLs asynchronously.  A later application
    start removes only old files inside this dedicated directory.
    """

    def __init__(self, directory: str | Path | None = None):
        self.directory = Path(directory) if directory is not None else default_state_path("drag-share")
        self.cleanup_stale()

    def cleanup_stale(self, *, older_than: timedelta = timedelta(days=7)) -> int:
        cutoff = datetime.now(timezone.utc) - older_than
        removed = 0
        try:
            candidates = tuple(self.directory.glob("lablogviewer-*.png"))
        except OSError:
            return 0
        for candidate in candidates:
            try:
                modified = datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc)
                if modified < cutoff:
                    candidate.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    def create_png(self, image: QImage, stem: str) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination = self.directory / f"lablogviewer-{_safe_stem(stem)}-{timestamp}-{uuid.uuid4().hex[:8]}.png"
        if not write_png(image, destination):
            raise OSError(f"Could not write drag image {destination}")
        return destination

    def start_drag(self, source: QWidget, image: QImage, stem: str) -> None:
        path = self.create_png(image, stem)
        mime = QMimeData()
        mime.setImageData(image)
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        drag = QDrag(source)
        drag.setMimeData(mime)
        preview = QPixmap.fromImage(image)
        if preview.width() > 240 or preview.height() > 180:
            preview = preview.scaled(240, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        drag.setPixmap(preview)
        drag.exec(Qt.CopyAction)


class DragShareController(QObject):
    """Observe a plot surface and start one native PNG drag on demand.

    ``render`` receives the target pane locked at mouse-down and whether all
    panes should be composed.  Returning ``False`` for ordinary events leaves
    pyqtgraph's click, mark and navigation handlers in charge until a share
    drag truly starts.
    """

    def __init__(
        self,
        surface: QWidget,
        pane_id: int | None,
        manager: DragShareManager,
        render: Callable[[int | None, bool], tuple[QImage, str] | None],
        can_start: Callable[[], bool],
        is_multi_pane: Callable[[], bool],
        parent: QObject | None = None,
    ):
        super().__init__(parent or surface)
        self.surface = surface
        self.pane_id = pane_id
        self.manager = manager
        self._render = render
        self._can_start = can_start
        self._is_multi_pane = is_multi_pane
        self._start_global: QPoint | None = None
        self._drag_all_panes = False
        self._drag_started = False
        self._watched: list[QWidget] = []
        self._install(surface)
        viewport = getattr(surface, "viewport", None)
        if callable(viewport):
            candidate = viewport()
            if isinstance(candidate, QWidget):
                self._install(candidate)

    def _install(self, widget: QWidget) -> None:
        if widget not in self._watched:
            widget.installEventFilter(self)
            self._watched.append(widget)

    @staticmethod
    def use_pointer_pane(modifiers: Qt.KeyboardModifiers) -> bool:
        return bool(modifiers & Qt.AltModifier)

    def _clear_press(self) -> None:
        self._start_global = None
        self._drag_started = False

    @staticmethod
    def _global_position(event) -> QPoint:
        position = getattr(event, "globalPosition", None)
        if callable(position):
            return position().toPoint()
        return event.globalPos()

    def eventFilter(self, watched: QObject, event) -> bool:  # noqa: N802 - Qt API spelling
        event_type = event.type()
        if event_type == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton and self._can_start():
                self._start_global = self._global_position(event)
                # This is intentionally locked here, not recomputed from the
                # mouse position later.  A drag never changes Active Pane.
                self._drag_all_panes = self._is_multi_pane() and not self.use_pointer_pane(event.modifiers())
                self._drag_started = False
            else:
                self._clear_press()
            return False
        if event_type == QEvent.MouseButtonRelease:
            self._clear_press()
            return False
        if event_type != QEvent.MouseMove or self._start_global is None or self._drag_started:
            return False
        if not (event.buttons() & Qt.LeftButton):
            self._clear_press()
            return False
        if (self._global_position(event) - self._start_global).manhattanLength() < QApplication.startDragDistance():
            return False
        self._drag_started = True
        payload = self._render(self.pane_id, self._drag_all_panes)
        self._clear_press()
        if payload is None:
            return False
        image, filename = payload
        if image.isNull():
            return False
        try:
            self.manager.start_drag(self.surface, image, filename)
        except OSError:
            return False
        return True
