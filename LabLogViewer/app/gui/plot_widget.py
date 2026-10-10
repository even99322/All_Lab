"""
app/gui/plot_widget.py — Phase 5A

Basic 1D plot widget wrapping pyqtgraph (spec §X.A). Takes already-
computed numpy arrays (x, y) plus axis labels — knows nothing about
Experiment/ChannelManager/HDF5 at all, so it stays reusable for the
2D/heatmap plot widget added in a later phase.
"""

from __future__ import annotations

from contextlib import contextmanager
import numpy as np
import pyqtgraph as pg
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QVBoxLayout, QWidget

from app.palette import TRACE_DEFAULT
from app.core.trace_overlay import SEQUENTIAL, build_trace_styles
from app.theme import WHITE_PLOT

pg.setConfigOptions(antialias=True, background="w", foreground="k")


class Plot1DWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.legend = self.plot_widget.addLegend(offset=(8, 8))
        self.legend.hide()
        layout.addWidget(self.plot_widget)

        self._curve = None
        self._curves: dict[int, pg.PlotDataItem] = {}
        self._data_point_items: dict[int, pg.ScatterPlotItem] = {}
        self._trace_data: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        self._trace_colors: dict[int, QColor] = {}
        self._active_trace: int | None = None
        self._color_mode = SEQUENTIAL
        self._show_data_points = False
        self._point_size_mode = "Auto"
        self._manual_point_size = 4.0
        self._plot_colors = WHITE_PLOT
        self.plot_widget.getPlotItem().getViewBox().sigRangeChanged.connect(
            lambda *_args: self.refresh_data_points()
        )

    def plot(self, x: np.ndarray, y: np.ndarray, *, x_label: str = "", y_label: str = "",
             title: str = "", name: str = "") -> None:
        self.plot_traces(
            {0: (x, y)}, active_trace=0, x_label=x_label, y_label=y_label,
            title=title, trace_labels={0: name}, auto_range=True,
        )

    def plot_traces(
        self,
        traces: dict[int, tuple[np.ndarray, np.ndarray]],
        *,
        active_trace: int | None,
        x_label: str = "",
        y_label: str = "",
        title: str = "",
        trace_labels: dict[int, str] | None = None,
        color_mode: str = SEQUENTIAL,
        auto_range: bool = False,
    ) -> tuple[int, ...]:
        """Render valid traces while reusing each trace's PlotDataItem."""
        ordered = tuple(traces)
        styles = build_trace_styles(ordered, active_trace, color_mode)
        valid: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        for trace, (x_values, y_values) in traces.items():
            x = np.asarray(x_values, dtype=float).reshape(-1)
            y = np.asarray(y_values, dtype=float).reshape(-1)
            if x.size != y.size:
                continue
            finite = np.isfinite(x) & np.isfinite(y)
            x, y = x[finite], y[finite]
            if not x.size:
                continue
            valid[trace] = (x, y)

        for trace in tuple(self._curves):
            if trace not in valid:
                self.plot_widget.removeItem(self._curves.pop(trace))
        for trace in tuple(self._data_point_items):
            if trace not in valid:
                self.plot_widget.removeItem(self._data_point_items.pop(trace))

        for trace, (x, y) in valid.items():
            curve = self._curves.get(trace)
            if curve is None:
                curve = pg.PlotDataItem()
                curve.setClipToView(True)
                curve.setDownsampling(auto=True, method="peak")
                self.plot_widget.addItem(curve)
                self._curves[trace] = curve
            style = styles[trace]
            color = QColor(*style.color)
            color.setAlphaF(style.opacity)
            self._trace_colors[trace] = color
            curve.setData(x, y)
            curve.setPen(pg.mkPen(color=color, width=style.width))
            curve.setZValue(style.z)

        self._trace_data = valid
        self._active_trace = active_trace
        self._color_mode = color_mode
        self._curve = self._curves.get(active_trace)
        if self._curve is None and valid:
            self._curve = self._curves[next(iter(valid))]

        self._update_legend(tuple(valid), active_trace, trace_labels or {})
        self.plot_widget.setLabel("bottom", x_label)
        self.plot_widget.setLabel("left", y_label)
        self.plot_widget.setTitle(title)
        if auto_range:
            self.plot_widget.autoRange()
        self.refresh_data_points()
        return tuple(valid)

    def set_data_points(self, visible: bool, size_mode: str = "Auto", manual_size: float = 4.0) -> None:
        """Configure real-sample point rendering without changing curve data."""
        self._show_data_points = bool(visible)
        self._point_size_mode = "Manual" if size_mode == "Manual" else "Auto"
        self._manual_point_size = max(1.0, min(float(manual_size), 12.0))
        self.refresh_data_points()

    @staticmethod
    def _auto_point_size(visible_count: int) -> float:
        if visible_count <= 501:
            return 5.0
        if visible_count <= 1001:
            return 3.5
        if visible_count <= 5000:
            return 2.5
        return 1.8

    def _visible_sample_positions(self, x: np.ndarray) -> np.ndarray:
        if not x.size:
            return np.array([], dtype=int)
        low, high = sorted(self.plot_widget.getPlotItem().getViewBox().viewRange()[0])
        return np.flatnonzero((x >= low) & (x <= high))

    def _visible_point_indices(self, x: np.ndarray) -> np.ndarray:
        positions = self._visible_sample_positions(x)
        if not positions.size:
            return positions
        # Keep responsive screen-space density while always selecting real samples.
        budget = max(80, int(max(self.plot_widget.width(), 1) * 0.65))
        stride = max(1, int(np.ceil(positions.size / budget)))
        selected = positions[::stride]
        if selected[-1] != positions[-1]:
            selected = np.append(selected, positions[-1])
        return selected

    def refresh_data_points(self) -> None:
        """Update only scatter items from cached displayed arrays; no I/O."""
        if not self._show_data_points:
            for item in self._data_point_items.values():
                item.hide()
            return
        for trace, (x, y) in self._trace_data.items():
            indices = self._visible_point_indices(x)
            item = self._data_point_items.get(trace)
            if item is None:
                item = pg.ScatterPlotItem(symbol="o", pen=None)
                self.plot_widget.addItem(item)
                self._data_point_items[trace] = item
            color = self._trace_colors.get(trace, QColor(TRACE_DEFAULT))
            visible_count = self._visible_sample_positions(x).size
            size = (self._manual_point_size if self._point_size_mode == "Manual"
                    else self._auto_point_size(visible_count))
            item.setData(x=x[indices], y=y[indices], size=size, brush=pg.mkBrush(color))
            item.setZValue(30)
            item.show()

    def update_active_trace(self, active_trace: int | None) -> None:
        """Move emphasis without replacing or reloading any curve data."""
        if not self._trace_data:
            return
        styles = build_trace_styles(tuple(self._trace_data), active_trace, self._color_mode)
        for trace, curve in self._curves.items():
            if trace not in styles:
                continue
            style = styles[trace]
            color = QColor(*style.color)
            color.setAlphaF(style.opacity)
            self._trace_colors[trace] = color
            curve.setPen(pg.mkPen(color=color, width=style.width))
            curve.setZValue(style.z)
        self._active_trace = active_trace
        self._curve = self._curves.get(active_trace, self._curve)
        self.refresh_data_points()

    def _update_legend(
        self, traces: tuple[int, ...], active_trace: int | None, labels: dict[int, str]
    ) -> None:
        self.legend.clear()
        if len(traces) <= 1:
            self.legend.hide()
            return
        self.legend.show()
        if len(traces) <= 8:
            for trace in traces:
                label = labels.get(trace) or f"Trace {trace + 1}"
                if trace == active_trace:
                    label += " (Active)"
                self.legend.addItem(self._curves[trace], label)
            return
        first, last = traces[0] + 1, traces[-1] + 1
        active = f"Trace {active_trace + 1}" if active_trace is not None else "None"
        representative = self._curves.get(active_trace, self._curves[traces[0]])
        self.legend.addItem(
            representative,
            f"{len(traces)} traces · {first}–{last} · Active: {active}",
        )

    def clear(self) -> None:
        for curve in tuple(self._curves.values()):
            self.plot_widget.removeItem(curve)
        self._curves.clear()
        for item in tuple(self._data_point_items.values()):
            self.plot_widget.removeItem(item)
        self._data_point_items.clear()
        self._trace_data.clear()
        self._trace_colors.clear()
        self._curve = None
        self.legend.clear()
        self.legend.hide()
        self.plot_widget.setTitle("")

    def apply_scientific_plot_appearance(self, colors) -> None:
        """Restyle scientific plot chrome without changing the plotted data."""
        self._plot_colors = colors
        self.plot_widget.setBackground(colors.plot_background)
        plot_item = self.plot_widget.getPlotItem()
        for axis in plot_item.axes.values():
            axis_item = axis["item"]
            axis_item.setPen(colors.secondary)
            axis_item.setTextPen(colors.text)
            label = getattr(axis_item, "label", None)
            if label is not None and hasattr(label, "setColor"):
                label.setColor(colors.text)
        set_legend_color = getattr(self.legend, "setLabelTextColor", None)
        if callable(set_legend_color):
            set_legend_color(colors.text)
        title = getattr(plot_item, "titleLabel", None)
        if title is not None and hasattr(title, "setAttr"):
            title.setAttr("color", colors.text)
        for axis_name in ("bottom", "left", "top", "right"):
            axis_item = plot_item.getAxis(axis_name)
            if axis_item is not None:
                axis_item.setGrid(75)
        self.plot_widget.update()

    @contextmanager
    def temporary_scientific_plot_appearance(self, colors):
        previous = self._plot_colors
        self.apply_scientific_plot_appearance(colors)
        try:
            yield
        finally:
            self.apply_scientific_plot_appearance(previous)
