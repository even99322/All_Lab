"""Rectangle zoom tool and Mac trackpad gestures for pyqtgraph plots (1D / 2D).

RectZoom      With the Viewer's Zoom tool active, a left-button drag draws a
              rectangle and zooms to it. Right-click or Esc steps back one
              level (all the way to the view before the first zoom).
Trackpad      Pinch zooms around the fingers; a two-finger scroll pans (a
              mouse wheel keeps zooming as before). Installed application
              wide for every pyqtgraph view.
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget


def _view_box_at(view: pg.GraphicsView, position: QPoint) -> pg.ViewBox | None:
    scene_pos = view.mapToScene(position)
    for item in view.scene().items(scene_pos):
        if isinstance(item, pg.ViewBox):
            return item
        parent = item.parentItem()
        while parent is not None:
            if isinstance(parent, pg.ViewBox):
                return parent
            parent = parent.parentItem()
    return None


class _Band(QWidget):
    """The zoom rectangle (drawn over the plot)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setObjectName("zoomRectangle")

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        accent = self.palette().color(self.palette().ColorRole.Highlight)
        fill = QColor(accent)
        fill.setAlpha(45)
        painter.fillRect(self.rect(), fill)
        painter.setPen(QPen(accent, 1.5, Qt.PenStyle.DashLine))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class RectZoom(QObject):
    """Rectangle zoom for every pyqtgraph view inside ``host`` while enabled."""

    MIN_DRAG = 6

    def __init__(self, host: QWidget):
        super().__init__(host)
        self.host = host
        self.enabled = False
        self._history: dict[int, list] = {}
        self._drag: tuple[pg.GraphicsView, pg.ViewBox, QPoint] | None = None
        self._band: _Band | None = None
        self.user_zoomed = None          # optional callback after a zoom drawn by the user

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        app = QApplication.instance()
        if self.enabled:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)
            self._cancel()
        for view in self.host.findChildren(pg.GraphicsView):
            view.viewport().setCursor(Qt.CursorShape.CrossCursor if self.enabled else Qt.CursorShape.ArrowCursor)

    def history_depth(self, view_box: pg.ViewBox) -> int:
        return len(self._history.get(id(view_box), []))

    def zoom_to(self, view_box: pg.ViewBox, x_range, y_range) -> None:
        self._history.setdefault(id(view_box), []).append([list(axis) for axis in view_box.viewRange()])
        view_box.setRange(xRange=x_range, yRange=y_range, padding=0)

    def back(self, view_box: pg.ViewBox) -> bool:
        stack = self._history.get(id(view_box))
        if not stack:
            return False
        x_range, y_range = stack.pop()
        view_box.setRange(xRange=x_range, yRange=y_range, padding=0)
        return True

    def _view_for(self, watched) -> pg.GraphicsView | None:
        if not isinstance(watched, QWidget):
            return None
        parent = watched.parentWidget()
        if isinstance(parent, pg.GraphicsView) and watched is parent.viewport() and self.host.isAncestorOf(parent):
            return parent
        return None

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API spelling
        kind = event.type()
        if kind == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape and self.host.isActiveWindow():
            for view in self.host.findChildren(pg.GraphicsView):
                if view.underMouse():
                    box = _view_box_at(view, view.mapFromGlobal(view.cursor().pos()))
                    if box is not None and self.back(box):
                        return True
            return False
        view = self._view_for(watched)
        if view is None:
            return False
        if kind == QEvent.Type.MouseButtonPress:
            position = event.position().toPoint()
            box = _view_box_at(view, position)
            if box is None:
                return False
            if event.button() == Qt.MouseButton.RightButton:
                self.back(box)
                return True
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag = (view, box, position)
                self._band = _Band(view.viewport())
                self._band.setGeometry(QRect(position, position))
                self._band.show()
                return True
        elif kind == QEvent.Type.MouseMove and self._drag is not None:
            self._band.setGeometry(QRect(self._drag[2], event.position().toPoint()).normalized())
            return True
        elif kind == QEvent.Type.MouseButtonRelease and self._drag is not None:
            view, box, start = self._drag
            end = event.position().toPoint()
            self._cancel()
            if abs(end.x() - start.x()) >= self.MIN_DRAG and abs(end.y() - start.y()) >= self.MIN_DRAG:
                first = box.mapSceneToView(view.mapToScene(start))
                second = box.mapSceneToView(view.mapToScene(end))
                self.zoom_to(box, sorted((first.x(), second.x())), sorted((first.y(), second.y())))
                if self.user_zoomed is not None:
                    self.user_zoomed()
            return True
        elif kind in (QEvent.Type.MouseButtonDblClick, QEvent.Type.ContextMenu):
            return True
        return False

    def _cancel(self) -> None:
        if self._band is not None:
            self._band.hide()
            self._band.deleteLater()
        self._band = None
        self._drag = None


class TrackpadGestures(QObject):
    """Pinch = zoom, two-finger scroll = pan, for every pyqtgraph view."""

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API spelling
        kind = event.type()
        if kind not in (QEvent.Type.NativeGesture, QEvent.Type.Wheel):
            return False
        if not isinstance(watched, QWidget):
            return False
        view = watched.parentWidget()
        if not isinstance(view, pg.GraphicsView) or watched is not view.viewport():
            return False
        position = event.position().toPoint()
        box = _view_box_at(view, position)
        if box is None:
            return False
        if kind == QEvent.Type.NativeGesture:
            if event.gestureType() != Qt.NativeGestureType.ZoomNativeGesture:
                return False
            factor = max(0.2, min(5.0, 1.0 - float(event.value())))
            center = box.mapSceneToView(view.mapToScene(position))
            box.scaleBy((factor, factor), center=center)
            return True
        # Trackpad scrolls come in phases (begin / update / end / momentum); a mouse wheel does not.
        if event.phase() == Qt.ScrollPhase.NoScrollPhase or event.pixelDelta().isNull():
            return False
        (x0, x1), (y0, y1) = box.viewRange()
        rect = box.sceneBoundingRect()
        if rect.width() <= 0 or rect.height() <= 0:
            return False
        delta = event.pixelDelta()
        box.translateBy(x=-delta.x() * (x1 - x0) / rect.width(), y=delta.y() * (y1 - y0) / rect.height())
        return True


_trackpad: TrackpadGestures | None = None


def install_trackpad_gestures(app) -> TrackpadGestures:
    global _trackpad
    if _trackpad is None:
        _trackpad = TrackpadGestures(app)
        app.installEventFilter(_trackpad)
    return _trackpad
