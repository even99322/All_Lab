"""
app/gui/plot_2d_widget.py — Phase 6 / Phase 8

2D heatmap widget wrapping pyqtgraph. Takes an already-computed
data_model.Grid2DData (x_values, y_values, z_values + labels/units) —
knows nothing about Experiment/ChannelManager/HDF5 at all, mirroring
the existing Plot1DWidget's separation of concerns.

Supports: heatmap rendering, zoom/pan/auto-range (native to
pyqtgraph's ViewBox), colormap selection, manual/auto color range,
and a mouse-following crosshair with X/Y/Z coordinate readout.

Emits `hover_moved(x, y, z)` on every valid crosshair position so the
owning Viewer can update its independent X/Y Cut windows from the same
cached grid and cursor position.
"""

from __future__ import annotations

from contextlib import contextmanager
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget, QLabel

from app.theme import WHITE_PLOT
from app.gui.fonts import mono_family

pg.setConfigOptions(antialias=True, background="w", foreground="k")

DEFAULT_COLORMAP = "LabLog BWR"
COLORMAPS = [DEFAULT_COLORMAP, "CoolWarm", "bwr", "viridis", "plasma", "inferno", "magma", "gray"]


def get_colormap(name: str) -> pg.ColorMap:
    """Resolve a display colormap, including LabLogViewer's reference LUT."""
    if name == DEFAULT_COLORMAP:
        return pg.ColorMap(
            np.array([0.0, 0.25, 0.5, 0.75, 1.0]),
            np.array([
                [125, 0, 0, 255],
                [235, 80, 80, 255],
                [255, 255, 255, 255],
                [90, 150, 240, 255],
                [0, 35, 130, 255],
            ], dtype=np.ubyte),
        )
    cmap = pg.colormap.get("coolwarm" if name == "CoolWarm" else name, source="matplotlib")
    if cmap is None:
        raise ValueError(f"Unknown colormap '{name}'. Valid options: {COLORMAPS}")
    return cmap


def robust_color_limits(values: np.ndarray) -> tuple[float, float]:
    """Return display-only limits resistant to isolated extreme pixels."""
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return 0.0, 1.0
    if finite.size < 100:
        low, high = float(finite.min()), float(finite.max())
    else:
        low, high = (float(value) for value in np.percentile(finite, (1.0, 99.0)))
    if low == high:
        scale = max(abs(low), 1.0)
        low -= scale * 5e-10
        high += scale * 5e-10
    return low, high


class Plot2DWidget(QWidget):
    """Displays a Grid2DData as a heatmap with a colorbar and a
    coordinate-readout crosshair.

    ``color_range_changed`` reports an interactive ColorBarItem level
    adjustment.  The owner remains the source of truth for persisted display
    state; this widget only exposes the already-rendered range and never
    reloads scientific data to do so.

    `hover_moved(x, y, z)` is emitted alongside the coordinate label
    update so Viewer-owned Cut windows can follow the cached cursor."""

    hover_moved = Signal(float, float, float)
    color_range_changed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.coord_label = QLabel("X = —    Y = —    Z = —")
        self.coord_label.setStyleSheet(f"font-family: '{mono_family()}'; padding: 2px;")
        self.coord_label.setMaximumHeight(20)
        layout.addWidget(self.coord_label)

        self.graphics_widget = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graphics_widget)

        self.plot_item: pg.PlotItem = self.graphics_widget.addPlot()
        # Hide pyqtgraph's floating in-plot quick controls (Auto-range / menu).
        # Navigation and the normal right-click context menu remain available.
        self.plot_item.hideButtons()
        self.graphics_widget.ci.layout.setContentsMargins(0, 0, 0, 0)
        self.graphics_widget.ci.layout.setSpacing(0)
        self.plot_item.layout.setContentsMargins(0, 0, 0, 0)
        self.plot_item.layout.setVerticalSpacing(1)
        self.plot_item.setAspectLocked(False)
        self.view_box = self.plot_item.getViewBox()

        self.img_item = pg.ImageItem()
        self.plot_item.addItem(self.img_item)

        self.color_bar: pg.ColorBarItem | None = None

        # crosshair
        self._vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#888", width=1))
        self._hline = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#888", width=1))
        self._vline.setVisible(False)
        self._hline.setVisible(False)
        self.plot_item.addItem(self._vline, ignoreBounds=True)
        self.plot_item.addItem(self._hline, ignoreBounds=True)

        self.graphics_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)

        self._grid = None  # current data_model.Grid2DData
        self._colormap_name = DEFAULT_COLORMAP
        self._z_min: float | None = None
        self._z_max: float | None = None
        self._setting_color_levels = False
        self._plot_colors = WHITE_PLOT
        self.last_hover: tuple[float, float, float] | None = None  # Phase 8: (x, y, z)
        # ^ the most recent VALID (snapped-to-sample) crosshair position;
        # None until the mouse has hovered over the plot at least once.
        # Cut windows fall back to the grid's first sample when the user
        # has not moved the crosshair yet.
        self.apply_scientific_plot_appearance(WHITE_PLOT)

    # ---- public API ------------------------------------------------------------

    def plot(self, grid, *, colormap: str = DEFAULT_COLORMAP,
              z_min: float | None = None, z_max: float | None = None) -> None:
        """Renders a Grid2DData. `z_min`/`z_max` are None for auto
        range (computed from the data); pass explicit floats for a
        manual color range."""
        self._grid = grid
        self._colormap_name = colormap

        x_values = np.asarray(grid.x_values, dtype=float)
        y_values = np.asarray(grid.y_values, dtype=float)
        z_values = np.asarray(grid.z_values, dtype=float)

        # pyqtgraph's ImageItem expects data indexed [x, y] (first axis
        # is horizontal) - our Grid2DData stores [y, x] (row=Y, col=X,
        # the natural numpy/image convention) - so transpose here, at
        # the GUI boundary, rather than anywhere in the data model.
        img_data = z_values.T  # (nx, ny)

        if z_min is None or z_max is None:
            auto_min, auto_max = robust_color_limits(img_data)
            z_min = auto_min if z_min is None else z_min
            z_max = auto_max if z_max is None else z_max
        if z_min == z_max:
            z_max = z_min + 1e-9
        self._z_min, self._z_max = z_min, z_max

        self._setting_color_levels = True
        try:
            self.img_item.setImage(img_data, autoLevels=False)
            self.img_item.setLevels((z_min, z_max))

            cmap = get_colormap(colormap)
            self.img_item.setColorMap(cmap)

            transform_label = grid.transform.replace("_", " ")
            z_label = f"{grid.z_name} [{transform_label}]" if grid.z_name else transform_label

            # position/scale the image so pixel coordinates map to the
            # actual physical axis range (assumes roughly-uniform spacing
            # for placement purposes only - the crosshair readout below
            # uses the real, possibly-non-uniform x_values/y_values arrays
            # via nearest-index lookup, so hover readout is always exact
            # even if the image placement is a linear approximation).
            x0, x1 = float(x_values.min()), float(x_values.max())
            y0, y1 = float(y_values.min()), float(y_values.max())
            w = max(x1 - x0, 1e-12)
            h = max(y1 - y0, 1e-12)
            self.img_item.setRect(x0, y0, w, h)

            if self.color_bar is None:
                self.color_bar = pg.ColorBarItem(values=(z_min, z_max), colorMap=cmap, label=z_label)
                self.color_bar.sigLevelsChanged.connect(self._on_colorbar_levels_changed)
                self.color_bar.setImageItem(self.img_item, insert_in=self.plot_item)
            else:
                self.color_bar.setColorMap(cmap)
                self.color_bar.setLevels((z_min, z_max))
                try:
                    self.color_bar.setLabels(right=z_label)
                except Exception:
                    pass  # non-critical - colorbar label just won't update
        finally:
            self._setting_color_levels = False

        self.plot_item.setLabel("bottom", f"{grid.x_name} [{grid.x_unit or '-'}]")
        self.plot_item.setLabel("left", f"{grid.y_name} [{grid.y_unit or '-'}]")
        self.plot_item.setTitle(f"{grid.z_name}  ({grid.y_name} \u00d7 {grid.x_name})")

        self.plot_item.autoRange()
        self.apply_scientific_plot_appearance(self._plot_colors)

    def _on_colorbar_levels_changed(self, color_bar) -> None:
        """Publish a user-driven ColorBarItem level change without replotting."""
        if self._setting_color_levels or color_bar is not self.color_bar:
            return
        values = getattr(color_bar, "values", None)
        if not isinstance(values, (tuple, list)) or len(values) != 2:
            return
        try:
            low, high = float(values[0]), float(values[1])
        except (TypeError, ValueError):
            return
        if not np.isfinite(low) or not np.isfinite(high) or low == high:
            return
        self._z_min, self._z_max = low, high
        self.color_range_changed.emit(low, high)

    def set_color_range(self, z_min: float | None, z_max: float | None) -> None:
        """Re-applies a color range to the currently-plotted grid
        WITHOUT recomputing/re-fetching the underlying data - satisfies
        the 'changing color range must not re-read HDF5' requirement."""
        if self._grid is None:
            return
        self.plot(self._grid, colormap=self._colormap_name, z_min=z_min, z_max=z_max)

    def set_colormap(self, colormap: str) -> None:
        """Re-applies a colormap to the currently-plotted grid without
        recomputing the data - same rationale as set_color_range()."""
        if self._grid is None:
            return
        self.plot(self._grid, colormap=colormap, z_min=self._z_min, z_max=self._z_max)

    def auto_range_color(self) -> tuple[float, float] | None:
        """Recomputes an auto (min/max-of-data) color range for the
        currently-plotted grid and applies it. Returns the (min, max)
        used, or None if nothing is plotted."""
        if self._grid is None:
            return None
        self.plot(self._grid, colormap=self._colormap_name, z_min=None, z_max=None)
        return self._z_min, self._z_max

    def clear(self) -> None:
        self._grid = None
        self.img_item.clear()
        self.plot_item.setTitle("")
        self.coord_label.setText("X = —    Y = —    Z = —")
        self._vline.setVisible(False)
        self._hline.setVisible(False)

    def apply_scientific_plot_appearance(self, colors) -> None:
        """Restyle plot chrome, preserving the scientific image and colorbar."""
        self._plot_colors = colors
        self.graphics_widget.setBackground(colors.plot_background)
        for axis in self.plot_item.axes.values():
            axis_item = axis["item"]
            axis_item.setPen(colors.secondary)
            axis_item.setTextPen(colors.text)
            axis_item.setGrid(75)
            label = getattr(axis_item, "label", None)
            if label is not None and hasattr(label, "setColor"):
                label.setColor(colors.text)
        self._vline.setPen(pg.mkPen(colors.secondary, width=1))
        self._hline.setPen(pg.mkPen(colors.secondary, width=1))
        self.coord_label.setStyleSheet(
            f"font-family: '{mono_family()}'; padding: 2px; color: {colors.text}; "
            f"background-color: {colors.plot_background};"
        )
        title = getattr(self.plot_item, "titleLabel", None)
        if title is not None:
            title.setText(title.text, color=colors.text)
        if self.color_bar is not None:
            for side in ("left", "right", "top", "bottom"):
                color_axis = self.color_bar.getAxis(side)
                color_axis.setPen(colors.secondary)
                color_axis.setTextPen(colors.text)
            self.color_bar.axis.setGrid(75)
        self.graphics_widget.update()

    @contextmanager
    def temporary_scientific_plot_appearance(self, colors):
        previous = self._plot_colors
        self.apply_scientific_plot_appearance(colors)
        try:
            yield
        finally:
            self.apply_scientific_plot_appearance(previous)

    # ---- crosshair / coordinate readout --------------------------------------

    def _on_mouse_moved(self, scene_pos) -> None:
        if self._grid is None:
            return
        if not self.plot_item.sceneBoundingRect().contains(scene_pos):
            self._vline.setVisible(False)
            self._hline.setVisible(False)
            return

        view_pos = self.view_box.mapSceneToView(scene_pos)
        x_val, y_val = view_pos.x(), view_pos.y()

        x_values = self._grid.x_values
        y_values = self._grid.y_values
        z_values = self._grid.z_values  # (ny, nx)

        if x_val < x_values.min() or x_val > x_values.max() or \
           y_val < y_values.min() or y_val > y_values.max():
            self._vline.setVisible(False)
            self._hline.setVisible(False)
            return

        ix = int(np.argmin(np.abs(x_values - x_val)))
        iy = int(np.argmin(np.abs(y_values - y_val)))
        self._set_crosshair_indices(ix, iy)

    def set_crosshair_position(self, x_value: float, y_value: float) -> None:
        """Snap a programmatic position to a real grid cell and publish it.

        Mark/numeric positioning and mouse hover share this one cache-only
        path, keeping all Cut consumers on the same coordinates.
        """
        if self._grid is None:
            return
        ix = int(np.argmin(np.abs(self._grid.x_values - x_value)))
        iy = int(np.argmin(np.abs(self._grid.y_values - y_value)))
        self._set_crosshair_indices(ix, iy)

    def _set_crosshair_indices(self, ix: int, iy: int) -> None:
        """Update the visual/readout state from valid, snapped grid indices."""
        if self._grid is None:
            return
        x_values = self._grid.x_values
        y_values = self._grid.y_values
        z_values = self._grid.z_values
        z_val = z_values[iy, ix]

        self._vline.setPos(x_values[ix])
        self._hline.setPos(y_values[iy])
        self._vline.setVisible(True)
        self._hline.setVisible(True)

        grid = self._grid
        self.coord_label.setText(
            f"{grid.x_name} = {self._fmt(x_values[ix])} {grid.x_unit or ''}    "
            f"{grid.y_name} = {self._fmt(y_values[iy])} {grid.y_unit or ''}    "
            f"{grid.z_name} = {self._fmt(z_val)}"
        )

        self.last_hover = (float(x_values[ix]), float(y_values[iy]), float(z_val))
        self.hover_moved.emit(*self.last_hover)

    def get_last_hover_or_default(self) -> tuple[float, float, float] | None:
        """Return the last crosshair position or a first-sample default,
        allowing Show X/Y Cut to display a trace before any hover."""
        if self.last_hover is not None:
            return self.last_hover
        if self._grid is None:
            return None
        grid = self._grid
        if grid.x_values.size == 0 or grid.y_values.size == 0:
            return None
        x0, y0 = float(grid.x_values[0]), float(grid.y_values[0])
        z0 = float(grid.z_values[0, 0])
        return (x0, y0, z0)

    @staticmethod
    def _fmt(value: float) -> str:
        if not np.isfinite(value):
            return str(value)
        if abs(value) >= 1e4 or (abs(value) < 1e-3 and value != 0):
            return f"{value:.4e}"
        return f"{value:.4g}"
