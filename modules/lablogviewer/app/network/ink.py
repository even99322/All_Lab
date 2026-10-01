"""Annotation (pen / laser) sync: Host strokes drawn on the Client's mirror.

Points are sent relative to their drawing region (0..1), so they land on the
same place of the plot even if the Client's window has another size.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QPointF, QTimer
from PySide6.QtGui import QColor


def _norm(point: QPointF, width: float, height: float) -> list[float]:
    return [round(point.x() / width, 5), round(point.y() / height, 5)]


def snapshot_session(session, root=None) -> list:
    """Per region: strokes, the stroke in progress and the laser trail.

    With ``root``, each region also carries its rectangle in ``root`` pixels
    (the YIG mirror rebuilds that window 1:1).
    """
    if session is None or not session.canvases:
        return []
    now = time.monotonic()
    regions = []
    for canvas in session.canvases:
        width, height = max(1.0, float(canvas.width())), max(1.0, float(canvas.height()))
        strokes = list(canvas.strokes) + ([canvas.active] if canvas.active is not None else [])
        regions.append({
            "strokes": [{"points": [_norm(p, width, height) for p in stroke.points],
                         "color": stroke.color.name(), "width": float(stroke.width)} for stroke in strokes],
            "laser": [_norm(p, width, height) + [round(now - stamp, 3)] for p, stamp in canvas.laser],
            "trail": float(canvas.trail_s),
        })
        if root is not None:
            origin = canvas.mapTo(root, canvas.rect().topLeft()) if root.isAncestorOf(canvas) else \
                root.mapFromGlobal(canvas.mapToGlobal(canvas.rect().topLeft()))
            regions[-1]["rect"] = [origin.x(), origin.y(), width, height]
    return regions


class InkMirror(QObject):
    """Owns display-only annotation sheets on the Client's mirror windows."""

    def __init__(self, mirror):
        super().__init__(mirror)
        self.mirror = mirror
        self._canvases: dict[str, list] = {}
        self._timer = QTimer(self, interval=16, timeout=self._tick)

    def _regions(self, key: str):
        if key == "viewer" and self.mirror.viewer is not None:
            viewer = self.mirror.viewer
            return [[viewer.mode_stack, viewer.multi_pane_splitter]]
        if key == "yig" and self.mirror.yig_window is not None:
            return [[self.mirror.yig_window.board]]
        return []

    def apply(self, data: dict) -> None:
        from app.gui.annotation import AnnotationCanvas, Stroke

        for key in ("viewer", "yig"):
            regions = data.get(key) or []
            canvases = self._canvases.get(key, [])
            specs = self._regions(key)
            if not regions or not specs:
                for canvas in canvases:
                    canvas.hide()
                    canvas.deleteLater()
                self._canvases[key] = []
                continue
            while len(canvases) < min(len(regions), len(specs)):
                host = specs[len(canvases)][0].window()
                canvases.append(AnnotationCanvas(specs[len(canvases)], floating=False, host=host))
            self._canvases[key] = canvases
            now = time.monotonic()
            for canvas, region in zip(canvases, regions):
                canvas.sync_geometry()
                if "rect" in region:                        # pixels of the 1:1 rebuilt window
                    ox, oy, width, height = region["rect"]
                else:
                    ox, oy = 0.0, 0.0
                    width, height = max(1.0, float(canvas.width())), max(1.0, float(canvas.height()))

                def point(x, y, ox=ox, oy=oy, width=width, height=height):
                    return QPointF(ox + x * width, oy + y * height)

                canvas.strokes = [Stroke([point(x, y) for x, y in stroke["points"]],
                                         QColor(stroke["color"]), float(stroke["width"]))
                                  for stroke in region.get("strokes", [])]
                canvas.active = None
                canvas.trail_s = float(region.get("trail", canvas.trail_s))
                canvas.laser = [(point(x, y), now - age) for x, y, age in region.get("laser", [])]
                canvas.update()
        if any(canvas.laser for canvases in self._canvases.values() for canvas in canvases):
            self._timer.start()

    def _tick(self) -> None:
        busy = False
        for canvases in self._canvases.values():
            for canvas in canvases:
                canvas.sync_geometry()
                busy |= canvas.prune_laser()
                canvas.update()
        if not busy:
            self._timer.stop()

    def close(self) -> None:
        self._timer.stop()
        for canvases in self._canvases.values():
            for canvas in canvases:
                canvas.hide()
                canvas.deleteLater()
        self._canvases.clear()
