"""Background worker for loading Browser quick previews."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QThread, Signal

from app.core.quick_preview import build_quick_preview

MAX_PREVIEW_POINTS = 1600   # per axis; the preview pane is a few hundred pixels wide


def decimate_preview(preview):
    """Thin a large 2D preview grid so it is drawn quickly; the Viewer always shows full data."""
    grid = preview.data if preview.kind == "2d" else None
    if grid is None:
        return preview
    z = grid.z_values
    ky = max(1, -(-z.shape[0] // MAX_PREVIEW_POINTS))
    kx = max(1, -(-z.shape[1] // MAX_PREVIEW_POINTS))
    if kx == 1 and ky == 1:
        return preview
    thinned = replace(grid, x_values=grid.x_values[::kx], y_values=grid.y_values[::ky],
                      z_values=z[::ky, ::kx].copy())
    return replace(preview, data=thinned)


class QuickPreviewWorker(QThread):
    preview_ready = Signal(object, object)
    preview_failed = Signal(object, str)

    def __init__(self, path: str, display_state: dict | None = None, parent=None):
        super().__init__(parent)
        self.path = path
        self.display_state = dict(display_state) if isinstance(display_state, dict) else None

    def run(self) -> None:
        try:
            self.preview_ready.emit(self, decimate_preview(build_quick_preview(self.path, self.display_state)))
        except Exception as exc:
            self.preview_failed.emit(self, str(exc))
