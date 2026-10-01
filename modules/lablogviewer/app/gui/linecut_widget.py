"""
app/gui/linecut_widget.py — Phase 8

A reusable Line Cut panel: two toggle buttons (X Cut / Y Cut) and, for
each one that's active, a title label + an existing Plot1DWidget
instance showing the extracted trace (spec explicitly asks to reuse
the existing 1D plot component rather than build a separate one).

This widget is deliberately "dumb": it has no idea what a Grid2DData
even is beyond calling data_model.extract_line_cut() on whatever grid
it's given, and no idea about Experiment/ChannelManager/HDF5 - it
just needs:
  - update_grid(grid): called whenever the owning heatmap's data
    changes (new Z channel, new transform, new N-D slice fixed
    values, ...) — re-renders any currently-active cut(s) against the
    new grid.
  - update_hover(x, y): called on every crosshair move — if a cut is
    active, re-renders it at the new position. This is what makes
    "Crosshair 移動後 -> Line Cut 必須更新" work without any extra
    wiring in the owning window beyond connecting Plot2DWidget's
    hover_moved signal to this method.

Supports one active X Cut and one active Y Cut simultaneously (spec:
"至少支援：一個 active X Cut、一個 active Y Cut... 兩者可以同時存在"),
implemented as a small dict keyed by cut_axis so a third cut type
could be added later without restructuring (spec: "architecture 不要
寫死成未來無法擴充").
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from app.core.data_model import Grid2DData, extract_line_cut
from app.gui.plot_widget import Plot1DWidget

TRANSFORM_LABELS = {
    "raw": "Raw (complex)",
    "real": "Real",
    "imag": "Imag",
    "magnitude": "Magnitude",
    "magnitude_db": "Magnitude (dB)",
    "phase_deg": "Phase (deg)",
    "phase_rad": "Phase (rad)",
}


class _CutPanel(QWidget):
    """One X-Cut-or-Y-Cut sub-panel: a title label over a Plot1DWidget.
    Hidden (not destroyed) when its cut is toggled off, so re-enabling
    it doesn't need to rebuild any Qt objects."""

    def __init__(self, cut_axis: str, parent=None):
        super().__init__(parent)
        self.cut_axis = cut_axis
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel("")
        self.title_label.setStyleSheet("font-weight: bold; padding: 2px;")
        layout.addWidget(self.title_label)
        self.plot_widget = Plot1DWidget()
        layout.addWidget(self.plot_widget)

    def render(self, grid: Grid2DData, requested_value: float) -> None:
        cut = extract_line_cut(grid, self.cut_axis, requested_value)

        fixed_str = "  |  ".join(
            f"{name} = {info['value']:.6g} {info['unit'] or ''}".strip()
            for name, info in cut.fixed_dims.items()
        )
        label = "X Cut" if self.cut_axis == "x" else "Y Cut"
        title = f"{label} @ {fixed_str}" if fixed_str else label
        self.title_label.setText(title)

        transform_label = TRANSFORM_LABELS.get(cut.transform, cut.transform)
        self.plot_widget.plot(
            cut.x_values,
            cut.y_values,
            x_label=f"{cut.x_name} [{cut.x_unit or '-'}]",
            y_label=f"{cut.z_name} \u2014 {transform_label}",
            title=title,
            name=cut.z_name,
        )


class CutWindow(QDialog):
    """Modeless, viewer-owned presentation for one cached line cut.

    It deliberately consumes ``Grid2DData`` only.  Moving the source
    crosshair therefore extracts a numpy row or column from already-loaded
    data and never opens or reads the source HDF5 file.
    """

    def __init__(self, cut_axis: str, parent=None):
        super().__init__(parent, Qt.Window)
        if cut_axis not in ("x", "y"):
            raise ValueError("cut_axis must be 'x' or 'y'")
        self.cut_axis = cut_axis
        self._grid: Grid2DData | None = None
        self._position: tuple[float, float] | None = None
        self._source_name: str | None = None
        self._source_pane: int | None = None
        label = "X Cut" if cut_axis == "x" else "Y Cut"
        self._update_window_title()
        self.setModal(False)
        self.resize(680, 390)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        header = QHBoxLayout()
        self.position_label = QLabel(f"{label} unavailable")
        self.position_label.setStyleSheet("font-weight: bold; padding: 2px;")
        header.addWidget(self.position_label)
        header.addStretch(1)
        self.follow_main_plot_checkbox = QCheckBox("Follow main plot")
        self.follow_main_plot_checkbox.setChecked(True)
        header.addWidget(self.follow_main_plot_checkbox)
        self.always_on_top_checkbox = QCheckBox("Always on top")
        self.always_on_top_checkbox.toggled.connect(self._set_always_on_top)
        header.addWidget(self.always_on_top_checkbox)
        layout.addLayout(header)
        self.panel = _CutPanel(cut_axis)
        layout.addWidget(self.panel, 1)

    @property
    def grid(self) -> Grid2DData | None:
        return self._grid

    def set_source_context(self, display_name: str | None, pane_id: int | None = None) -> None:
        """Update display-only ownership without using it as an identity key."""
        self._source_name = display_name.strip() if isinstance(display_name, str) else None
        self._source_pane = pane_id if isinstance(pane_id, int) and pane_id > 0 else None
        self._update_window_title()

    def _update_window_title(self) -> None:
        label = "X Cut" if self.cut_axis == "x" else "Y Cut"
        if self._source_name:
            label += f" — {self._source_name}"
        if self._source_pane is not None:
            label += f" — Pane {self._source_pane}"
        self.setWindowTitle(label)

    def set_grid(self, grid: Grid2DData | None) -> None:
        self._grid = grid
        if grid is None:
            label = "X Cut" if self.cut_axis == "x" else "Y Cut"
            self.position_label.setText(f"{label} unavailable for the active plot")
            self.panel.plot_widget.clear()
            return
        self._render()

    def set_position(self, x: float, y: float) -> None:
        self._position = (float(x), float(y))
        self._render()

    def update_source(self, grid: Grid2DData | None, x: float, y: float, *, force_position: bool = False) -> None:
        """Update from the owning Viewer with at most one cached-curve redraw."""
        self._grid = grid
        if grid is None:
            label = "X Cut" if self.cut_axis == "x" else "Y Cut"
            self.position_label.setText(f"{label} unavailable for the active plot")
            self.panel.plot_widget.clear()
            return
        if force_position or self._position is None or self.follow_main_plot_checkbox.isChecked():
            self._position = (float(x), float(y))
        self._render()

    def _render(self) -> None:
        if self._grid is None:
            return
        if self._position is None:
            x = float(self._grid.x_values[0]) if self._grid.x_values.size else 0.0
            y = float(self._grid.y_values[0]) if self._grid.y_values.size else 0.0
        else:
            x, y = self._position
        fixed = y if self.cut_axis == "x" else x
        try:
            cut = extract_line_cut(self._grid, self.cut_axis, fixed)
            fixed_name = self._grid.y_name if self.cut_axis == "x" else self._grid.x_name
            fixed_unit = self._grid.y_unit if self.cut_axis == "x" else self._grid.x_unit
            label = "X Cut" if self.cut_axis == "x" else "Y Cut"
            self.position_label.setText(
                f"{label} @ {fixed_name} = {cut.nearest_value:.6g} {fixed_unit or ''}".strip()
            )
            self.panel.render(self._grid, fixed)
        except Exception:
            self.position_label.setText("Cut unavailable for the active plot")

    def _set_always_on_top(self, enabled: bool) -> None:
        # Qt hides a native window whenever its flags change. Retain the same
        # dialog, geometry, data and visibility rather than making the user
        # invoke Show X/Y Cut again after a simple toggle.
        visible = self.isVisible()
        geometry = self.geometry()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, bool(enabled))
        self.setGeometry(geometry)
        if visible:
            self.show()
            self.raise_()


class LineCutWidget(QWidget):
    """The full Line Cut panel: [X Cut] [Y Cut] toggle buttons, plus
    the active sub-panel(s) below."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        button_row = QHBoxLayout()
        button_row.addWidget(QLabel("Line Cut:"))
        self.x_cut_button = QPushButton("X Cut")
        self.x_cut_button.setCheckable(True)
        self.y_cut_button = QPushButton("Y Cut")
        self.y_cut_button.setCheckable(True)
        button_row.addWidget(self.x_cut_button)
        button_row.addWidget(self.y_cut_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self._panels: dict[str, _CutPanel] = {"x": _CutPanel("x"), "y": _CutPanel("y")}
        for panel in self._panels.values():
            panel.setVisible(False)
            layout.addWidget(panel)

        self._grid: Grid2DData | None = None
        self._current_x: float | None = None
        self._current_y: float | None = None
        self._active: dict[str, bool] = {"x": False, "y": False}
        # ^ tracked independently of Qt widget visibility: QWidget.isVisible()
        # depends on the ENTIRE parent chain being shown, which is not
        # true in a headless/offscreen test (or before the owning
        # window is shown) even right after setVisible(True) - so
        # "is this cut active" must not be deduced from isVisible().

        self.x_cut_button.toggled.connect(lambda checked: self._on_toggled("x", checked))
        self.y_cut_button.toggled.connect(lambda checked: self._on_toggled("y", checked))

    # ---- public API ------------------------------------------------------------

    def is_active(self, cut_axis: str) -> bool:
        return self._active[cut_axis]

    def _on_toggled(self, cut_axis: str, checked: bool) -> None:
        self._active[cut_axis] = checked
        self._panels[cut_axis].setVisible(checked)
        if checked and self._grid is not None:
            self._render_if_active(cut_axis, self._current_fixed_value(cut_axis))

    def _last_value_for(self, cut_axis: str) -> float:
        """The physical coordinate the OTHER axis should be fixed at
        for this cut - i.e. for an X Cut we need the last known Y
        value, and vice versa. Falls back to the grid's first sample
        on that axis if no hover has happened yet."""
        if self._grid is None:
            return 0.0
        if cut_axis == "x":
            return float(self._grid.y_values[0]) if self._grid.y_values.size else 0.0
        return float(self._grid.x_values[0]) if self._grid.x_values.size else 0.0

    def update_grid(self, grid: Grid2DData) -> None:
        """Call whenever the heatmap's underlying grid changes (new Z
        channel, transform, or new N-D slice fixed values) -
        re-renders any active cut against the new data, preserving
        whatever crosshair position was last used."""
        self._grid = grid
        for cut_axis in ("x", "y"):
            self._render_if_active(cut_axis, self._current_fixed_value(cut_axis))

    def update_hover(self, x: float, y: float) -> None:
        """Call on every crosshair move - re-renders any active cut at
        the new position (spec: Line Cut must update live as the
        crosshair moves, without the user reopening it)."""
        self._current_x = x
        self._current_y = y
        self._render_if_active("x", y)  # X Cut fixes Y
        self._render_if_active("y", x)  # Y Cut fixes X

    def clear(self) -> None:
        self._grid = None
        self._current_x = None
        self._current_y = None
        self.x_cut_button.setChecked(False)
        self.y_cut_button.setChecked(False)
        self._active = {"x": False, "y": False}

    # ---- internal --------------------------------------------------------------

    def _current_fixed_value(self, cut_axis: str) -> float:
        if cut_axis == "x" and self._current_y is not None:
            return self._current_y
        if cut_axis == "y" and self._current_x is not None:
            return self._current_x
        return self._last_value_for(cut_axis)

    def _render_if_active(self, cut_axis: str, value: float) -> None:
        if self._grid is None or not self.is_active(cut_axis):
            return
        try:
            self._panels[cut_axis].render(self._grid, value)
        except Exception:
            # defensive: a transient invalid grid/value combination
            # (e.g. mid-update) should never crash the app - the next
            # update_grid()/update_hover() call will self-correct.
            pass
