"""GUI-thread frame rendering from immutable, already-loaded plot arrays."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import ceil
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import Mapping

import numpy as np

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from app.palette import ANIMATION_LABEL
from app.gui.plot_export import PaneRenderSurface, render_composite_image
from app.gui.plot_widget import Plot1DWidget
from app.gui.plot_2d_widget import Plot2DWidget
from app.core.data_model import Grid2DData
from app.core.animation_export import AnimationExportError, validate_gif_output


@dataclass(frozen=True)
class AnimationPane:
    target: QRect
    title: str
    kind: str = "1d"
    x_values: np.ndarray | None = None
    frames: Mapping[int, np.ndarray] = field(default_factory=dict)
    x_label: str = ""
    y_label: str = ""
    x_range: tuple[float, float] | None = None
    y_range: tuple[float, float] | None = None
    grid: Grid2DData | None = None
    colormap: str = "LabLog BWR"
    z_range: tuple[float, float] | None = None
    reveal_trace_ids: tuple[int, ...] = ()


class AnimationRenderSession:
    """Owns offscreen plot clones; it never mutates the source Viewer."""

    def __init__(self, panes: tuple[AnimationPane, ...], size: QSize):
        self.panes = panes
        self.size = QSize(max(1, size.width()), max(1, size.height()))
        self.widgets: list[Plot1DWidget | Plot2DWidget] = []
        for pane in panes:
            widget = Plot2DWidget() if pane.kind == "2d" else Plot1DWidget()
            widget.resize(max(1, pane.target.width()), max(1, pane.target.height()))
            widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            widget.ensurePolished()
            widget.show()
            self.widgets.append(widget)
        QApplication.processEvents()

    @staticmethod
    def _activate_plot_layout(widget: Plot1DWidget | Plot2DWidget) -> None:
        """Resolve deferred Qt/pyqtgraph layout before vector capture."""
        layout = widget.layout()
        if layout is not None:
            layout.setGeometry(widget.rect())
            layout.activate()
        if isinstance(widget, Plot1DWidget):
            widget.plot_widget.ensurePolished()
            widget.plot_widget.getPlotItem().layout.activate()
            widget.plot_widget.getPlotItem().updateGeometry()
        else:
            widget.graphics_widget.ensurePolished()
            widget.graphics_widget.ci.layout.activate()
            widget.plot_item.layout.activate()
            widget.plot_item.updateGeometry()

    def render(self, trace_id: int, *, target_size: tuple[int, int] | None = None,
               overlay_lines: tuple[str, ...] = ()) -> QImage:
        surfaces: list[PaneRenderSurface] = []
        for pane, widget in zip(self.panes, self.widgets):
            if pane.kind == "2d":
                if not isinstance(widget, Plot2DWidget) or pane.grid is None or pane.z_range is None:
                    raise ValueError("The 2D animation snapshot is incomplete.")
                grid = pane.grid
                if pane.reveal_trace_ids:
                    # In vector-log 2D data, one acquired sweep entry is one
                    # measured grid row.  Hide not-yet-reached rows instead of
                    # interpolating or repeating any experimental samples.
                    visible_rows = np.zeros(grid.z_values.shape[0], dtype=bool)
                    visible_rows[[row for row in pane.reveal_trace_ids if row <= trace_id]] = True
                    displayed = np.array(grid.z_values, copy=True)
                    displayed[~visible_rows, :] = np.nan
                    grid = replace(grid, z_values=displayed)
                widget.plot(grid, colormap=pane.colormap, z_min=pane.z_range[0], z_max=pane.z_range[1])
                self._activate_plot_layout(widget)
                surfaces.append(PaneRenderSurface(widget.graphics_widget, pane.target))
                continue
            if not isinstance(widget, Plot1DWidget) or pane.x_values is None or pane.x_range is None or pane.y_range is None:
                raise ValueError("The 1D animation snapshot is incomplete.")
            values = np.asarray(pane.frames[trace_id], dtype=float)
            widget.plot(
                pane.x_values, values, x_label=pane.x_label, y_label=pane.y_label,
                title=pane.title, name=f"Trace {trace_id + 1}",
            )
            widget.plot_widget.getViewBox().setRange(
                xRange=pane.x_range, yRange=pane.y_range, padding=0.0,
            )
            self._activate_plot_layout(widget)
            surfaces.append(PaneRenderSurface(widget.plot_widget, pane.target))
        scale = None
        if target_size is not None:
            scale = max(2, ceil(target_size[0] / self.size.width()), ceil(target_size[1] / self.size.height()))
        image = render_composite_image(surfaces, self.size, scale=scale)
        if target_size is not None and (image.width(), image.height()) != target_size:
            image = image.scaled(*target_size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        if overlay_lines:
            painter = QPainter(image)
            try:
                painter.setPen(QColor(ANIMATION_LABEL["text"]))
                painter.setBrush(QColor(*ANIMATION_LABEL["fill"]))
                line_height = 24
                width = max(180, max(painter.fontMetrics().horizontalAdvance(line) for line in overlay_lines) + 20)
                painter.drawRoundedRect(12, 12, width, line_height * len(overlay_lines) + 12, 4, 4)
                for index, text in enumerate(overlay_lines):
                    painter.drawText(22, 35 + index * line_height, text)
            finally:
                painter.end()
        return image

    def close(self) -> None:
        for widget in self.widgets:
            widget.hide()
            widget.deleteLater()
        self.widgets.clear()


class AnimationEncodeWorker(Thread):
    """Bounded encoder queue; GUI rendering stays on the Qt thread."""

    _SENTINEL = object()

    def __init__(self, destination: str | Path, format_name: str, *, fps: int,
                 duration: float, expected_frames: int | None = None):
        super().__init__(daemon=True)
        self.destination = Path(destination)
        expected_suffix = ".gif" if format_name == "GIF" else ".mp4"
        if self.destination.suffix.lower() != expected_suffix:
            raise ValueError(f"{format_name} output path must use the {expected_suffix} extension.")
        # The encoder selects a container from the suffix.  Keep `.mp4`/`.gif`
        # visible while still ensuring an incomplete output is never presented
        # as the requested destination.
        self.temporary = self.destination.with_name(
            f"{self.destination.stem}.part{self.destination.suffix}"
        )
        self.format_name = format_name
        self.fps = fps
        self.duration = duration
        self.expected_frames = expected_frames
        self.validated_frame_count: int | None = None
        self.queue: Queue[object] = Queue(maxsize=3)
        self.cancelled = Event()
        self.finishing = Event()
        self.finished = Event()
        self.error: str | None = None
        self.frames_written = 0

    def submit(self, image: QImage) -> bool:
        if self.cancelled.is_set() or self.queue.full():
            return False
        converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
        buffer = converted.bits()
        array = np.frombuffer(buffer, dtype=np.uint8).reshape(converted.height(), converted.width(), 4).copy()
        self.queue.put_nowait(array)
        return True

    def finish(self) -> None:
        self.finishing.set()
        try:
            self.queue.put_nowait(self._SENTINEL)
        except Full:
            pass

    def cancel(self) -> None:
        self.cancelled.set()

    def run(self) -> None:
        writer = None
        try:
            if self.format_name == "GIF":
                import imageio_ffmpeg
            else:
                import imageio.v2 as imageio
                writer = imageio.get_writer(
                    self.temporary, format="FFMPEG", mode="I", fps=self.fps,
                    codec="libx264", macro_block_size=2, pixelformat="yuv420p",
                )
            try:
                while not self.cancelled.is_set():
                    try:
                        frame = self.queue.get(timeout=0.05)
                    except Empty:
                        if self.finishing.is_set():
                            break
                        continue
                    if frame is self._SENTINEL:
                        break
                    array = np.asarray(frame, dtype=np.uint8)
                    if self.format_name == "GIF":
                        if writer is None:
                            height, width, channels = array.shape
                            writer = imageio_ffmpeg.write_frames(
                                self.temporary, (width, height),
                                pix_fmt_in="rgba" if channels == 4 else "rgb24",
                                pix_fmt_out="pal8", fps=100, codec="gif",
                                macro_block_size=1, output_params=["-loop", "0"],
                            )
                            writer.send(None)
                        writer.send(np.ascontiguousarray(array))
                    else:
                        writer.append_data(array[..., :3])
                    self.frames_written += 1
            finally:
                if writer is not None:
                    writer.close()
            if self.cancelled.is_set() or not self.frames_written:
                self.temporary.unlink(missing_ok=True)
            else:
                if self.format_name == "GIF":
                    if (self.expected_frames is not None
                            and self.frames_written != self.expected_frames):
                        raise AnimationExportError(
                            f"Encoder received {self.frames_written} frames; "
                            f"expected {self.expected_frames}."
                        )
                    self.validated_frame_count = validate_gif_output(
                        self.temporary,
                        expected_frames=(
                            self.frames_written if self.expected_frames is None
                            else self.expected_frames
                        ),
                        expected_duration_ms=10,
                    )
                self.temporary.replace(self.destination)
        except Exception as error:
            self.error = str(error)
            self.temporary.unlink(missing_ok=True)
        finally:
            self.finished.set()
