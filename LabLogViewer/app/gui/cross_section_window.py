"""2D X/Y cross-section: the viewer's own heat map with draggable cut lines.

v1.0.9 — laid out like Labber's Log Browser: the X cut on TOP, the Y cut on the RIGHT (normal orientation, values
growing to the right), solid cut lines with a z / y / x read-out box at their crossing, and the contrast controls
(intensity histogram, Max / Min, Auto range / Full range) in a panel on the LEFT of the map. ``set_parts`` shows
the X and the Y cut separately. ``CrossSectionWindow`` is the same thing in its own window (multi-pane layouts).

It only consumes an already-loaded ``Grid2DData``; moving a line slices a numpy
array and never touches the HDF5 file.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from app.core.data_model import Grid2DData
from app.gui.plot_2d_widget import DEFAULT_COLORMAP, Plot2DWidget, robust_color_limits

HISTOGRAM_BINS = 200
LEFT_AXIS_WIDTH = 64
Y_CUT_AXIS_WIDTH = 8             # its Y values are the map's: no second set of labels next to the colour bar
Y_CUT_MIN_WIDTH = 160
BOTTOM_AXIS_HEIGHT = 48
LINE_WIDTH = 2
LINE_COLOR = "#FFFFFF"           # Labber: solid white cut lines
LINE_SHADOW = "#202020"
CUT_COLOR = "#B8902A"            # Labber's gold cut traces
MARKER_COLOR = "#1F3FA0"         # the other line's position inside a cut plot
PANEL_WIDTH = 190
# kept for older callers
X_COLOR = CUT_COLOR
Y_COLOR = CUT_COLOR


def nearest_index(values: np.ndarray, target: float) -> int:
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return 0
    return int(np.nanargmin(np.abs(values - target)))


def cut_arrays(grid: Grid2DData, x: float, y: float):
    """(x_cut, y_cut) for the lines at (x, y): value-vs-X at row ~y, value-vs-Y at column ~x."""
    z = np.asarray(grid.z_values, dtype=float)
    row = nearest_index(grid.y_values, y)
    column = nearest_index(grid.x_values, x)
    return (np.asarray(grid.x_values, dtype=float), z[row, :],
            np.asarray(grid.y_values, dtype=float), z[:, column], row, column)


def intensity_histogram(values: np.ndarray, bins: int = HISTOGRAM_BINS):
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return np.array([0.0, 1.0]), np.zeros(1)
    low, high = float(finite.min()), float(finite.max())
    if low == high:
        high = low + 1e-9
    counts, edges = np.histogram(finite, bins=bins, range=(low, high))
    return edges, counts.astype(float)


def _fmt(value: float, unit: str | None) -> str:
    return f"{value:.6g} {unit}".rstrip() if unit else f"{value:.6g}"


class CrossSection:
    """Cut plots, draggable lines and contrast controls around a Plot2DWidget."""

    def __init__(self, plot2d: Plot2DWidget):
        self.plot2d = plot2d
        self._updating = False
        self._dragging_region = False
        self._pad_timer = QTimer(plot2d)
        self._pad_timer.setSingleShot(True)
        self._pad_timer.setInterval(0)
        self._pad_timer.timeout.connect(self._align)
        self.x_cut_plot = pg.PlotItem()
        self.y_cut_plot = pg.PlotItem()
        for plot in (self.x_cut_plot, self.y_cut_plot):
            plot.hideButtons()
            plot.showGrid(x=True, y=True, alpha=0.35)
        self.x_curve = self.x_cut_plot.plot(pen=pg.mkPen(CUT_COLOR, width=1.5))
        self.y_curve = self.y_cut_plot.plot(pen=pg.mkPen(CUT_COLOR, width=1.5))
        self.x_cut_plot.hideAxis("bottom")
        self.x_cut_plot.setXLink(plot2d.plot_item)
        self.y_cut_plot.setYLink(plot2d.plot_item)

        def line(angle):
            item = pg.InfiniteLine(angle=angle, movable=True, pen=pg.mkPen(LINE_COLOR, width=LINE_WIDTH),
                                   hoverPen=pg.mkPen(LINE_COLOR, width=LINE_WIDTH + 2))
            item.setZValue(20)
            return item

        self.h_line, self.v_line = line(0), line(90)
        for item in (self.h_line, self.v_line):
            plot2d.plot_item.addItem(item, ignoreBounds=True)
            item.sigPositionChanged.connect(self._on_line_moved)
        self.readout = pg.TextItem(anchor=(0, 0), color="#111111", fill=pg.mkBrush(255, 255, 255, 225),
                                   border=pg.mkPen("#666666"))
        self.readout.setZValue(30)
        plot2d.plot_item.addItem(self.readout, ignoreBounds=True)
        self.x_marker = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(MARKER_COLOR, width=2))
        self.y_marker = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen(MARKER_COLOR, width=2))
        self.x_cut_plot.addItem(self.x_marker, ignoreBounds=True)
        self.y_cut_plot.addItem(self.y_marker, ignoreBounds=True)

        # ---- contrast panel (left of the map): vertical intensity histogram + Max / Min + Auto / Full range ----
        self.hist_plot = pg.PlotItem(viewBox=_HistViewBox(self._fit_hist_view))
        self.hist_plot.hideButtons()
        self.hist_plot.setMenuEnabled(False)
        self.hist_plot.setMouseEnabled(x=False, y=True)
        self.hist_plot.hideAxis("bottom")
        self.hist_plot.getViewBox().invertX(True)            # bars grow towards the map
        self.hist_bars = pg.BarGraphItem(x0=[0.0], y0=[0.0], width=[0.0], height=[1.0],
                                         brush=(70, 90, 120, 230), pen=pg.mkPen(None))
        self.hist_plot.addItem(self.hist_bars)
        self.hist_region = pg.LinearRegionItem(values=(0.0, 1.0), orientation="horizontal",
                                               brush=(40, 80, 220, 45),
                                               pen=pg.mkPen((40, 80, 220), width=2))
        self.hist_region.setZValue(10)
        self.hist_plot.addItem(self.hist_region, ignoreBounds=True)
        self._region_timer = QTimer(plot2d)
        self._region_timer.setSingleShot(True)
        self._region_timer.setInterval(40)
        self._region_timer.timeout.connect(self._on_region_changed)
        self.hist_region.sigRegionChanged.connect(lambda *_: self._region_timer.start())

        self.panel = QWidget()
        self.panel.setObjectName("contrastPanel")
        self.panel.setFixedWidth(PANEL_WIDTH)
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(4, 4, 6, 4)
        panel_layout.setSpacing(4)
        title = QLabel("Contrast")
        title.setStyleSheet("font-weight: 600;")
        panel_layout.addWidget(title)
        self.hist_view = pg.GraphicsLayoutWidget()
        self.hist_view.setObjectName("contrastHistogram")
        self.hist_view.ci.addItem(self.hist_plot, row=0, col=0)
        panel_layout.addWidget(self.hist_view, 1)
        self.controls = QWidget()
        grid = QGridLayout(self.controls)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(3)
        self.max_spin, self.min_spin = self._spin(), self._spin()
        self.auto_button, self.full_button = QPushButton("Auto range"), QPushButton("Full range")
        self.auto_button.setObjectName("contrastAutoRange")
        self.full_button.setObjectName("contrastFullRange")
        grid.addWidget(QLabel("Max:"), 0, 0)
        grid.addWidget(self.max_spin, 0, 1)
        grid.addWidget(QLabel("Min:"), 1, 0)
        grid.addWidget(self.min_spin, 1, 1)
        grid.addWidget(self.auto_button, 2, 0, 1, 2)
        grid.addWidget(self.full_button, 3, 0, 1, 2)
        panel_layout.addWidget(self.controls)
        self.max_spin.editingFinished.connect(self._on_spin_changed)
        self.min_spin.editingFinished.connect(self._on_spin_changed)
        self.auto_button.clicked.connect(self.auto_range)
        self.full_button.clicked.connect(self.full_range)
        self.panel.hide()

        self._attached = False
        self._full_limits = (0.0, 1.0)
        self.parts = {"x": True, "y": True}
        plot2d.view_box.sigResized.connect(lambda *_: self._pad_timer.start())
        plot2d.view_box.sigRangeChanged.connect(lambda *_: self._place_readout())

    @staticmethod
    def _spin() -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(6)
        spin.setRange(-1e300, 1e300)
        spin.setKeyboardTracking(False)
        spin.setMinimumWidth(90)
        return spin

    # ---- layout -------------------------------------------------------------

    def set_parts(self, x: bool | None = None, y: bool | None = None) -> None:
        """Show the X cut (top) and / or the Y cut (right); neither → the plain heat map."""
        if x is not None:
            self.parts["x"] = bool(x)
        if y is not None:
            self.parts["y"] = bool(y)
        if not (self.parts["x"] or self.parts["y"]):
            self.detach()
            return
        if self._attached:
            self._layout_items()
            self._refit_layout()
            self._pad_timer.start()
        else:
            self.attach()

    def _layout_items(self) -> None:
        plot2d = self.plot2d
        ci = plot2d.graphics_widget.ci
        for item in (self.x_cut_plot, self.y_cut_plot, plot2d.plot_item):
            if item in ci.items:
                ci.removeItem(item)
        show_x, show_y = self.parts["x"], self.parts["y"]
        row = 1 if show_x else 0
        if show_x:
            ci.addItem(self.x_cut_plot, row=0, col=0)
        ci.addItem(plot2d.plot_item, row=row, col=0)
        if show_y:
            ci.addItem(self.y_cut_plot, row=row, col=1)
        for index in (0, 1, 2):
            ci.layout.setColumnStretchFactor(index, (3 if index == 0 else 1 if index == 1 else 0) if show_y
                                             else (1 if index == 0 else 0))
            ci.layout.setRowStretchFactor(index, (1 if index == 0 else 3 if index == 1 else 0) if show_x
                                          else (1 if index == 0 else 0))
        self.x_cut_plot.setVisible(show_x)
        self.y_cut_plot.setVisible(show_y)
        self.h_line.setVisible(show_x)            # the X cut is taken along the horizontal line
        self.v_line.setVisible(show_y)            # the Y cut along the vertical one
        self.x_marker.setVisible(show_y)
        self.y_marker.setVisible(show_x)

    def attach(self) -> None:
        if self._attached:
            return
        plot2d = self.plot2d
        self._layout_items()
        plot2d.plot_item.setTitle(None)
        for plot in (plot2d.plot_item, self.x_cut_plot):
            plot.getAxis("left").setWidth(LEFT_AXIS_WIDTH)
        self.y_cut_plot.getAxis("left").setWidth(Y_CUT_AXIS_WIDTH)
        self.y_cut_plot.getAxis("left").setStyle(showValues=False)
        self.y_cut_plot.setMinimumWidth(Y_CUT_MIN_WIDTH)
        for plot in (plot2d.plot_item, self.y_cut_plot):
            plot.getAxis("bottom").setHeight(BOTTOM_AXIS_HEIGHT)
        plot2d._vline.setVisible(False)
        plot2d._hline.setVisible(False)
        if self.panel.parent() is not plot2d.left_panel_host:
            plot2d.left_panel_layout.addWidget(self.panel)
        self.panel.show()
        plot2d.left_panel_host.show()
        self._attached = True
        self.readout.setVisible(True)
        self._refit_layout()
        self.on_plot()
        self._pad_timer.start()

    def _refit_layout(self) -> None:
        # Removing or adding cells leaves the central layout at its old size; re-fit it to the view.
        view = self.plot2d.graphics_widget
        size = view.size()
        view.resizeEvent(QResizeEvent(size, size))

    def detach(self) -> None:
        if not self._attached:
            return
        plot2d = self.plot2d
        ci = plot2d.graphics_widget.ci
        for item in (self.x_cut_plot, self.y_cut_plot, plot2d.plot_item):
            if item in ci.items:
                ci.removeItem(item)
        ci.addItem(plot2d.plot_item, row=0, col=0)
        # A stretch of 0 caps the lone plot at its size hint, so it would stay tiny.
        for index in (0, 1, 2):
            ci.layout.setColumnStretchFactor(index, 1 if index == 0 else 0)
            ci.layout.setRowStretchFactor(index, 1 if index == 0 else 0)
        plot2d.plot_item.getAxis("left").setWidth(None)
        plot2d.plot_item.getAxis("bottom").setHeight(None)
        plot2d.plot_item.getAxis("left").setStyle(autoExpandTextSpace=True)
        self.panel.hide()
        plot2d.left_panel_host.hide()
        self.h_line.setVisible(False)
        self.v_line.setVisible(False)
        self.readout.setVisible(False)
        self._attached = False
        self._refit_layout()
        grid = plot2d._grid
        if grid is not None:
            plot2d.plot(grid, colormap=plot2d._colormap_name, z_min=plot2d._z_min, z_max=plot2d._z_max)

    def _align(self) -> None:
        """The X cut's plotting area spans exactly the map's (the colour bar sits right of the map)."""
        if not self._attached:
            return
        plot2d = self.plot2d
        pad = plot2d.plot_item.sceneBoundingRect().right() - plot2d.view_box.sceneBoundingRect().right()
        axis = self.x_cut_plot.getAxis("right")
        self.x_cut_plot.showAxis("right")
        axis.setStyle(showValues=False, tickLength=0)
        axis.setPen(pg.mkPen(None))
        axis.setWidth(max(float(pad), 1.0))
        self._place_readout()

    # ---- data ---------------------------------------------------------------

    def on_plot(self) -> None:
        plot2d = self.plot2d
        grid = plot2d._grid
        if not self._attached:
            return
        if grid is None or np.asarray(grid.z_values).size == 0:
            self.x_curve.setData([], [])
            self.y_curve.setData([], [])
            return
        x = np.asarray(grid.x_values, dtype=float)
        y = np.asarray(grid.y_values, dtype=float)
        x0, x1 = float(np.nanmin(x)), float(np.nanmax(x))
        y0, y1 = float(np.nanmin(y)), float(np.nanmax(y))
        keep = self.line_position() if getattr(self, "_positioned", False) else None
        self._updating = True
        try:
            self.x_cut_plot.setLabel("left", f"{grid.z_name}")
            self.y_cut_plot.setLabel("bottom", f"{grid.z_name}")
            self.v_line.setBounds((x0, x1))
            self.h_line.setBounds((y0, y1))
            if keep is not None and x0 <= keep[0] <= x1 and y0 <= keep[1] <= y1:
                position = keep
            else:
                position = (0.5 * (x0 + x1), 0.5 * (y0 + y1))
            self.v_line.setValue(position[0])
            self.h_line.setValue(position[1])
            self._positioned = True
        finally:
            self._updating = False
        self._update_histogram()
        self.sync_levels()
        self._update_cuts()
        self._pad_timer.start()

    def line_position(self) -> tuple[float, float]:
        return float(self.v_line.value()), float(self.h_line.value())

    def set_line_position(self, x: float, y: float) -> None:
        self.v_line.setValue(float(x))
        self.h_line.setValue(float(y))

    def _on_line_moved(self) -> None:
        if self._updating:
            return
        self._update_cuts()

    def _update_cuts(self) -> None:
        grid = self.plot2d._grid
        if grid is None or not self._attached:
            return
        x, y = self.line_position()
        xs, x_cut, ys, y_cut, row, column = cut_arrays(grid, x, y)
        dots = {"symbol": "o", "symbolSize": 3, "symbolPen": None, "symbolBrush": CUT_COLOR}
        self.x_curve.setData(xs, x_cut, **(dots if xs.size <= 400 else {"symbol": None}))
        self.y_curve.setData(y_cut, ys, **(dots if ys.size <= 400 else {"symbol": None}))
        self.x_marker.setValue(x)
        self.y_marker.setValue(y)
        z = float(np.asarray(grid.z_values, dtype=float)[row, column])
        z_unit = getattr(grid, "z_unit", None)
        self.plot2d.coord_label.setText(
            f"{grid.x_name} = {xs[column]:.6g} {grid.x_unit or ''}   "
            f"{grid.y_name} = {ys[row]:.6g} {grid.y_unit or ''}   {grid.z_name} = {z:.6g}"
        )
        self.readout.setText(f"z: {_fmt(z, z_unit)}\ny: {_fmt(ys[row], grid.y_unit)}\nx: {_fmt(xs[column], grid.x_unit)}")
        self._place_readout()

    def _place_readout(self) -> None:
        """The z / y / x box sits just below-right of the crossing, flipped near the right / bottom edge."""
        if not self._attached:
            return
        x, y = self.line_position()
        (x0, x1), (y0, y1) = self.plot2d.view_box.viewRange()
        fx = (x - x0) / (x1 - x0) if x1 != x0 else 0.0
        fy = (y - y0) / (y1 - y0) if y1 != y0 else 1.0
        self.readout.setAnchor((1.0 if fx > 0.75 else 0.0, 1.0 if fy < 0.25 else 0.0))
        self.readout.setPos(x, y)

    # ---- contrast -----------------------------------------------------------

    def contrast(self) -> tuple[float, float]:
        return float(self.min_spin.value()), float(self.max_spin.value())

    def sync_levels(self) -> None:
        plot2d = self.plot2d
        if plot2d._z_min is None or plot2d._z_max is None:
            return
        self._updating = True
        try:
            self.min_spin.setValue(plot2d._z_min)
            self.max_spin.setValue(plot2d._z_max)
            self.hist_region.setRegion((plot2d._z_min, plot2d._z_max))
        finally:
            self._updating = False
        if not self._dragging_region:
            self._fit_hist_view()

    def _fit_hist_view(self) -> None:
        plot2d = self.plot2d
        if plot2d._z_min is None or plot2d._z_max is None:
            return
        low, high = float(plot2d._z_min), float(plot2d._z_max)
        margin = max((high - low) * HIST_VIEW_MARGIN, 1e-300)
        self.hist_plot.setYRange(low - margin, high + margin, padding=0)

    def set_contrast(self, low: float, high: float) -> None:
        if not (np.isfinite(low) and np.isfinite(high)) or low == high:
            return
        low, high = (low, high) if low < high else (high, low)
        plot2d = self.plot2d
        plot2d.apply_levels(low, high)
        self.sync_levels()

    def auto_range(self) -> None:
        grid = self.plot2d._grid
        if grid is not None:
            self.set_contrast(*robust_color_limits(np.asarray(grid.z_values, dtype=float)))

    def full_range(self) -> None:
        grid = self.plot2d._grid
        if grid is None:
            return
        z = np.asarray(grid.z_values, dtype=float)
        z = z[np.isfinite(z)]
        if z.size:
            self.set_contrast(float(z.min()), float(z.max()))

    def _update_histogram(self) -> None:
        grid = self.plot2d._grid
        if grid is None:
            self.hist_bars.setOpts(x0=[0.0], y0=[0.0], width=[0.0], height=[1.0])
            return
        edges, counts = intensity_histogram(np.asarray(grid.z_values, dtype=float))
        self.hist_edges, self.hist_counts = edges, counts
        self.hist_bars.setOpts(x0=np.zeros(counts.size), y0=edges[:-1], width=counts, height=np.diff(edges))
        self.hist_plot.setLabel("left", f"{grid.z_name}")
        self._fit_hist_view()
        self.hist_plot.setXRange(0.0, float(counts.max()) if counts.size else 1.0, padding=0.05)

    def _on_region_changed(self) -> None:
        if self._updating:
            return
        low, high = self.hist_region.getRegion()
        self._dragging_region = True
        try:
            self.set_contrast(low, high)
        finally:
            self._dragging_region = False

    def _on_spin_changed(self) -> None:
        if self._updating:
            return
        self.set_contrast(self.min_spin.value(), self.max_spin.value())

    # ---- theme --------------------------------------------------------------

    def apply_colors(self, colors) -> None:
        for plot in (self.x_cut_plot, self.y_cut_plot):
            for axis in plot.axes.values():
                item = axis["item"]
                item.setPen(colors.secondary)
                item.setTextPen(colors.text)
                item.setGrid(75)
                label = getattr(item, "label", None)
                if label is not None and hasattr(label, "setColor"):
                    label.setColor(colors.text)
        for axis in self.hist_plot.axes.values():
            item = axis["item"]
            item.setPen(colors.secondary)
            item.setTextPen(colors.text)
            label = getattr(item, "label", None)
            if label is not None and hasattr(label, "setColor"):
                label.setColor(colors.text)
        self.hist_view.setBackground(colors.plot_background)
        self.panel.setStyleSheet(
            f"#contrastPanel, #contrastPanel QWidget {{ background: {colors.plot_background}; color: {colors.text}; }}"
            f"#contrastPanel QDoubleSpinBox {{ background: {colors.panel}; border: 1px solid {colors.border}; }}"
            f"#contrastPanel QPushButton {{ background: {colors.panel}; border: 1px solid {colors.border};"
            f" padding: 3px; }}"
        )


HIST_VIEW_MARGIN = 0.5


class _HistViewBox(pg.ViewBox):
    """Histogram view: wheel zooms X, middle/left drag on empty space pans, double-click refits."""

    allow_pan = True          # v1.0.8: also with the Viewer's Pointer tool (see plot_gestures.install_pointer_lock)

    def __init__(self, on_reset):
        super().__init__(enableMenu=False)
        self._on_reset = on_reset

    def mouseDoubleClickEvent(self, ev) -> None:
        self._on_reset()
        ev.accept()


class CrossSectionWindow(QWidget):
    """The same cross-section in its own window (multi-pane layouts)."""

    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("2D X/Y Cross-section")
        self.resize(900, 820)
        self._source_name: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        header = QHBoxLayout()
        header.addStretch(1)
        self.always_on_top_checkbox = QCheckBox("Always on top")
        self.always_on_top_checkbox.toggled.connect(self._set_always_on_top)
        header.addWidget(self.always_on_top_checkbox)
        layout.addLayout(header)
        self.plot2d = Plot2DWidget()
        layout.addWidget(self.plot2d, 1)
        self.plot2d.set_cross_section_enabled(True)

    @property
    def cross(self) -> CrossSection:
        return self.plot2d._cross

    @property
    def grid(self) -> Grid2DData | None:
        return self.plot2d._grid

    @property
    def v_line(self):
        return self.cross.v_line

    @property
    def h_line(self):
        return self.cross.h_line

    @property
    def x_curve(self):
        return self.cross.x_curve

    @property
    def y_curve(self):
        return self.cross.y_curve

    def line_position(self) -> tuple[float, float]:
        return self.cross.line_position()

    def set_line_position(self, x: float, y: float) -> None:
        self.cross.set_line_position(x, y)

    def contrast(self) -> tuple[float, float]:
        return self.cross.contrast()

    def set_contrast(self, low: float, high: float) -> None:
        self.cross.set_contrast(low, high)

    def auto_range(self) -> None:
        self.cross.auto_range()

    def full_range(self) -> None:
        self.cross.full_range()

    def set_source_name(self, name: str | None) -> None:
        self._source_name = name
        self.setWindowTitle(f"2D X/Y Cross-section — {name}" if name else "2D X/Y Cross-section")

    def set_grid(self, grid: Grid2DData | None, *, colormap: str | None = None,
                 z_min: float | None = None, z_max: float | None = None) -> None:
        plot2d = self.plot2d
        if grid is None or np.asarray(grid.z_values).size == 0:
            plot2d.clear()
            self.cross.on_plot()
            return
        if z_min is None and z_max is None and plot2d._grid is not None:
            z_min, z_max = plot2d._z_min, plot2d._z_max
        plot2d.plot(grid, colormap=colormap or plot2d._colormap_name, z_min=z_min, z_max=z_max)

    def _set_always_on_top(self, enabled: bool) -> None:
        visible, geometry = self.isVisible(), self.geometry()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, bool(enabled))
        self.setGeometry(geometry)
        if visible:
            self.show()
            self.raise_()

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)
