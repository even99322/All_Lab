"""Session-only Multi-Pane canvases and independent visualization state."""

from __future__ import annotations

from app.palette import PANE

from dataclasses import dataclass, field

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QPushButton, QStackedWidget, QVBoxLayout

from app.core.mark_model import MarkManager
from app.gui.mark_overlay import MarkOverlay
from app.gui.plot_2d_widget import DEFAULT_COLORMAP, Plot2DWidget
from app.gui.plot_widget import Plot1DWidget


ONE_PANE = "1 Pane"
TWO_SIDE = "2 Panes · Side by Side"
TWO_STACKED = "2 Panes · Stacked"
THREE_PANES = "3 Panes"
FOUR_PANES = "4 Panes · 2 × 2"
PANE_LAYOUTS = (ONE_PANE, TWO_SIDE, TWO_STACKED, THREE_PANES, FOUR_PANES)


@dataclass
class PaneState:
    pane_id: int
    plot_mode: int = 0
    x_axis: dict | None = None
    y_axis: dict | None = None
    transform_name: str = "Magnitude"
    db: bool = False
    unwrap: bool = False
    show_data_points: bool = False
    point_size_mode: str = "Auto"
    manual_point_size: float = 4.0
    trace_index: int = 0
    z_name: str = ""
    grid_x_name: str = ""
    grid_y_name: str = ""
    grid_transform: str = "magnitude_db"
    colormap: str = DEFAULT_COLORMAP
    auto_color: bool = True
    color_min: float = 0.0
    color_max: float = 1.0
    x_range: tuple[float, float] | None = None
    y_range: tuple[float, float] | None = None
    x_formula: str = ""
    y_formula: str = ""
    formula_enabled: bool = False
    mark_managers: dict[int, MarkManager] = field(
        default_factory=lambda: {0: MarkManager(), 1: MarkManager()}
    )


class PaneFrame(QFrame):
    """One lightweight plot surface with stable session identity."""

    activated = Signal(int)
    x_range_changed = Signal(int, float, float)
    hover_moved = Signal(int, float, float, float)

    def __init__(self, pane_id: int, parent=None):
        super().__init__(parent)
        self.pane_id = int(pane_id)
        self.setObjectName(f"paneFrame{self.pane_id}")
        # Splitter layouts deliberately permit temporarily tiny panes; users
        # can drag a neighbour large for close inspection without a hard grid.
        self.setMinimumSize(1, 1)
        layout = QVBoxLayout(self)
        self._frame_layout = layout
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self.header = QPushButton(f"Pane {self.pane_id}")
        self.header.setFlat(True)
        self.header.setMaximumHeight(24)
        self.header.clicked.connect(lambda: self.activated.emit(self.pane_id))
        layout.addWidget(self.header)
        self.stack = QStackedWidget()
        self.plot_1d = Plot1DWidget()
        self.plot_2d = Plot2DWidget()
        self.stack.addWidget(self.plot_1d)
        self.stack.addWidget(self.plot_2d)
        layout.addWidget(self.stack, 1)
        self.mark_overlays = {
            0: MarkOverlay(self.plot_1d.plot_widget.getPlotItem(), self.plot_1d.plot_widget),
            1: MarkOverlay(self.plot_2d.plot_item, self.plot_2d.graphics_widget),
        }
        self.plot_1d.plot_widget.scene().sigMouseClicked.connect(
            lambda *_: self.activated.emit(self.pane_id)
        )
        self.plot_2d.graphics_widget.scene().sigMouseClicked.connect(
            lambda *_: self.activated.emit(self.pane_id)
        )
        self.plot_2d.hover_moved.connect(
            lambda x, y, z: self.hover_moved.emit(self.pane_id, x, y, z)
        )
        self.plot_1d.plot_widget.getViewBox().sigXRangeChanged.connect(
            lambda _view, values: self.x_range_changed.emit(
                self.pane_id, float(values[0]), float(values[1])
            )
        )
        self.plot_2d.view_box.sigXRangeChanged.connect(
            lambda _view, values: self.x_range_changed.emit(
                self.pane_id, float(values[0]), float(values[1])
            )
        )
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        if active:
            self.setStyleSheet(
                "QFrame#paneFrame%d { border: 2px solid %s; background: %s; }"
                "QPushButton { font-weight: 600; color: %s; }" % (
                    self.pane_id, PANE["active_border"], PANE["active_background"], PANE["active_title"])
            )
        else:
            self.setStyleSheet(
                "QFrame#paneFrame%d { border: 1px solid %s; background: %s; }"
                "QPushButton { color: %s; }" % (
                    self.pane_id, PANE["inactive_border"], PANE["inactive_background"], PANE["inactive_title"])
            )

    def set_mode(self, mode: int) -> None:
        is_2d = int(mode) == 1
        self.stack.setCurrentIndex(1 if is_2d else 0)
        if is_2d:
            self._frame_layout.setContentsMargins(1, 1, 1, 1)
            self._frame_layout.setSpacing(0)
            self.header.setMaximumHeight(22)
        else:
            self._frame_layout.setContentsMargins(3, 3, 3, 3)
            self._frame_layout.setSpacing(2)
            self.header.setMaximumHeight(24)

    def set_title(self, detail: str) -> None:
        self.header.setText(f"Pane {self.pane_id} · {detail}")

    def clear(self) -> None:
        self.plot_1d.clear()
        self.plot_2d.clear()
        for overlay in self.mark_overlays.values():
            overlay.clear()
