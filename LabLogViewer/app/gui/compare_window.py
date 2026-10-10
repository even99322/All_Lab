"""Compare Traces: overlay, difference or ratio of 1D traces from one or several HDF5 files.

Read-only: files are opened with the normal reader and nothing is ever written back."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from app.core import compare as cmp
from app.core.data_model import Experiment, SliceError
from app.core.hdf5_reader import HDF5ReadError
from app.core.labber_parser import load_experiment
from app.plotting.complex_transform import VALID_TRANSFORMS

COLORS = ("#0A84FF", "#FF9F0A", "#30D158", "#FF453A", "#BF5AF2", "#64D2FF")
MAX_TRACES = 6
VIEWS = (("Overlay", "overlay"), ("Stacked (own X axes)", "stacked"),
         ("Difference (first − second)", "difference"), ("Ratio (first ÷ second)", "ratio"))
X_MODES = (("Native X values", "native"), ("Shared X range", "overlap"),
           ("Ignore X values (shape only)", "index"), ("Fixed step", "fixed_step"))
Y_MODES = (("Y as measured", "none"), ("Scale to 0 … 1", "minmax"),
           ("Zero mean, unit spread", "zscore"), ("Divide by largest |Y|", "max"))


def _label(name: str, unit: str | None) -> str:
    return f"{name} ({unit})" if unit else name


class _ComparePlots(pg.GraphicsLayoutWidget):
    """Plot area that follows Settings > Appearance > Scientific Plot Appearance like every other plot."""

    def __init__(self):
        super().__init__()
        from app.theme import WHITE_PLOT
        self._colors = WHITE_PLOT
        self.setBackground(self._colors.plot_background)

    def apply_scientific_plot_appearance(self, colors=None) -> None:
        if colors is not None:
            self._colors = colors
        colors = self._colors
        self.setBackground(colors.plot_background)
        for item in self.ci.items:
            if not isinstance(item, pg.PlotItem):
                continue
            for name in ("bottom", "left", "top", "right"):
                axis = item.getAxis(name)
                axis.setPen(colors.secondary)
                axis.setTextPen(colors.text)
                label = getattr(axis, "label", None)
                if label is not None and hasattr(label, "setColor"):
                    label.setColor(colors.text)
                axis.setGrid(75)
            if item.legend is not None:
                item.legend.setLabelTextColor(colors.text)
                for _sample, label in item.legend.items:
                    label.setText(label.text)
            if hasattr(item.titleLabel, "setAttr"):
                item.titleLabel.setAttr("color", colors.text)
        self.update()


class _TraceRow(QGroupBox):
    """One trace: which file, channel, quantity (I/Q/phase...), X dimension and the fixed positions."""

    def __init__(self, window: "CompareWindow", number: int):
        super().__init__(f"Trace {number}")
        self.window = window
        self.number = number
        self.dim_spins: dict[str, QSpinBox] = {}
        form = QFormLayout(self)
        self.file_combo = QComboBox()
        self.channel_combo = QComboBox()
        self.transform_combo = QComboBox()
        self.x_combo = QComboBox()
        self.dims_host = QWidget()
        self.dims_form = QFormLayout(self.dims_host)
        self.dims_form.setContentsMargins(0, 0, 0, 0)
        form.addRow("File", self.file_combo)
        form.addRow("Channel", self.channel_combo)
        form.addRow("Quantity", self.transform_combo)
        form.addRow("X axis", self.x_combo)
        form.addRow(self.dims_host)
        self.file_combo.currentIndexChanged.connect(self._file_changed)
        self.channel_combo.currentIndexChanged.connect(self._channel_changed)
        self.x_combo.currentIndexChanged.connect(self._x_changed)
        self.transform_combo.currentIndexChanged.connect(window.schedule_plot)
        self.refresh_files()

    @property
    def experiment(self) -> Experiment | None:
        return self.window.experiment_at(self.file_combo.currentData())

    def refresh_files(self) -> None:
        current = self.file_combo.currentData()
        self.file_combo.blockSignals(True)
        self.file_combo.clear()
        for key, (name, _exp) in self.window.files.items():
            self.file_combo.addItem(name, key)
        index = self.file_combo.findData(current)
        self.file_combo.setCurrentIndex(max(index, 0) if self.file_combo.count() else -1)
        self.file_combo.blockSignals(False)
        self._file_changed()

    def _file_changed(self) -> None:
        experiment = self.experiment
        previous = self.channel_combo.currentText()
        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        if experiment is not None:
            names = [n for n in experiment.log_channel_names if n in experiment.channels]
            self.channel_combo.addItems(names)
            if previous in names:
                self.channel_combo.setCurrentText(previous)
        self.channel_combo.blockSignals(False)
        self._channel_changed()

    def _channel_changed(self) -> None:
        experiment, channel = self.experiment, self.channel_combo.currentText()
        self.transform_combo.blockSignals(True)
        previous = self.transform_combo.currentText()
        self.transform_combo.clear()
        self.x_combo.blockSignals(True)
        self.x_combo.clear()
        self._dims = []
        if experiment is not None and channel:
            info = experiment.channels[channel]
            self.transform_combo.addItems(
                [t for t in VALID_TRANSFORMS if t != "raw"] if info.is_vector or info.is_complex else ["raw"])
            if previous in [self.transform_combo.itemText(i) for i in range(self.transform_combo.count())]:
                self.transform_combo.setCurrentText(previous)
            try:
                self._dims = experiment.list_dimensions(channel)
            except Exception:
                self._dims = []
            self.x_combo.addItems([d.name for d in self._dims])
        self.transform_combo.blockSignals(False)
        self.x_combo.blockSignals(False)
        self._x_changed()

    def _x_changed(self) -> None:
        while self.dims_form.rowCount():
            self.dims_form.removeRow(0)
        self.dim_spins = {}
        x_name = self.x_combo.currentText()
        for dim in self._dims:
            if dim.name == x_name or dim.size < 2:
                continue
            spin = QSpinBox()
            spin.setRange(0, dim.size - 1)
            spin.setToolTip(f"{dim.name}: 0 … {dim.size - 1}")
            spin.valueChanged.connect(self.window.schedule_plot)
            self.dim_spins[dim.name] = spin
            self.dims_form.addRow(f"{dim.name} index", spin)
        self.window.schedule_plot()

    def read_trace(self) -> cmp.Trace:
        experiment, channel = self.experiment, self.channel_combo.currentText()
        if experiment is None or not channel:
            raise cmp.CompareError("Choose a file and a channel for every trace.")
        fixed = {name: spin.value() for name, spin in self.dim_spins.items()}
        try:
            data = experiment.get_nd_slice(channel, self.x_combo.currentText(), None, fixed,
                                           self.transform_combo.currentText() or "raw")
        except SliceError as error:
            raise cmp.CompareError(str(error)) from error
        name = f"{self.file_combo.currentText()} · {channel} · {self.transform_combo.currentText()}"
        if fixed:
            name += " @ " + ", ".join(f"{k}#{v}" for k, v in fixed.items())
        unit = data.z_unit
        if self.transform_combo.currentText() == "magnitude_db":
            unit = "dB"
        elif self.transform_combo.currentText() == "phase_deg":
            unit = "deg"
        elif self.transform_combo.currentText() == "phase_rad":
            unit = "rad"
        return cmp.Trace(np.asarray(data.x_values, float), np.asarray(data.y_values, float),
                         data.x_name, data.x_unit, channel, unit, name)


class CompareWindow(QMainWindow):
    def __init__(self, experiment: Experiment | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Compare Traces")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(1180, 760)
        self.files: dict[str, tuple[str, Experiment]] = {}
        self._owned: list[Experiment] = []
        self.rows: list[_TraceRow] = []
        self._pending = False
        self._closed = False

        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        side_host = QWidget()
        side = QVBoxLayout(side_host)
        side_host.setFixedWidth(360)

        buttons = QHBoxLayout()
        self.open_button = QPushButton("Open file...")
        self.add_button = QPushButton("Add trace")
        self.remove_button = QPushButton("Remove last trace")
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        side.addLayout(buttons)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.rows_host = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.addStretch(1)
        scroll.setWidget(self.rows_host)
        side.addWidget(scroll, 1)

        options = QGroupBox("Comparison")
        opt = QFormLayout(options)
        self.view_combo = QComboBox()
        for text, value in VIEWS:
            self.view_combo.addItem(text, value)
        self.x_mode_combo = QComboBox()
        for text, value in X_MODES:
            self.x_mode_combo.addItem(text, value)
        self.y_mode_combo = QComboBox()
        for text, value in Y_MODES:
            self.y_mode_combo.addItem(text, value)
        self.step_spin = QDoubleSpinBox()
        self.step_spin.setDecimals(9)
        self.step_spin.setRange(1e-12, 1e12)
        self.step_spin.setValue(1.0)
        self.link_x_check = QCheckBox("Link X axes in stacked view")
        self.link_x_check.setChecked(True)
        opt.addRow("View", self.view_combo)
        opt.addRow("X handling", self.x_mode_combo)
        opt.addRow("Step", self.step_spin)
        opt.addRow("Y handling", self.y_mode_combo)
        opt.addRow(self.link_x_check)
        side.addWidget(options)
        self.note_label = QLabel("")
        self.note_label.setObjectName("compareNotes")
        self.note_label.setWordWrap(True)
        side.addWidget(self.note_label)
        outer.addWidget(side_host)

        self.plot_host = QWidget()
        self.plot_layout = QVBoxLayout(self.plot_host)
        self.plot_layout.setContentsMargins(0, 0, 0, 0)
        self.graph = _ComparePlots()
        self.plot_layout.addWidget(self.graph)
        outer.addWidget(self.plot_host, 1)

        self.open_button.clicked.connect(self.open_file_dialog)
        self.add_button.clicked.connect(self.add_row)
        self.remove_button.clicked.connect(self.remove_row)
        for combo in (self.view_combo, self.x_mode_combo, self.y_mode_combo):
            combo.currentIndexChanged.connect(self.schedule_plot)
        self.step_spin.valueChanged.connect(self.schedule_plot)
        self.x_mode_combo.currentIndexChanged.connect(self._sync_enabled)
        self.view_combo.currentIndexChanged.connect(self._sync_enabled)
        self._sync_enabled()
        self.link_x_check.toggled.connect(self.schedule_plot)

        if experiment is not None:
            self.add_experiment(experiment, own=False)
        self.add_row()
        self.add_row()
        if len(self.rows) == 2 and self.rows[0].channel_combo.count() > 1:
            self.rows[1].channel_combo.setCurrentIndex(1)
        self.schedule_plot()

    def _sync_enabled(self, *_args) -> None:
        self.step_spin.setEnabled(self.x_mode_combo.currentData() == "fixed_step")
        self.link_x_check.setEnabled(self.view_combo.currentData() == "stacked")

    # ---- files ---------------------------------------------------------------

    def experiment_at(self, key) -> Experiment | None:
        item = self.files.get(key)
        return item[1] if item else None

    def add_experiment(self, experiment: Experiment, own: bool = True) -> str:
        key = experiment.data_identity or experiment.source_path
        if key in self.files:
            return key
        name = Path(experiment.source_path).name
        names = [n for n, _ in self.files.values()]
        label = name if name not in names else f"{name} ({len(names) + 1})"
        self.files[key] = (label, experiment)
        if own:
            self._owned.append(experiment)
        for row in self.rows:
            row.refresh_files()
        return key

    def open_file_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open measurement log", "", "Measurement logs (*.hdf5 *.h5 *.nc);;All files (*)")
        if path:
            self.open_path(path)

    def open_path(self, path: str) -> bool:
        try:
            experiment = load_experiment(path)
        except Exception as error:
            QMessageBox.warning(self, "Cannot open file", f"{type(error).__name__}: {error}")
            return False
        self.add_experiment(experiment)
        return True

    # ---- rows ----------------------------------------------------------------

    def add_row(self) -> None:
        if len(self.rows) >= MAX_TRACES:
            return
        row = _TraceRow(self, len(self.rows) + 1)
        if self.rows and self.rows[-1].file_combo.currentData() is not None:
            row.file_combo.setCurrentIndex(self.rows[-1].file_combo.currentIndex())
        self.rows.append(row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        self.schedule_plot()

    def remove_row(self) -> None:
        if len(self.rows) <= 1:
            return
        row = self.rows.pop()
        self.rows_layout.removeWidget(row)
        row.deleteLater()
        self.schedule_plot()

    # ---- plotting --------------------------------------------------------------

    def schedule_plot(self, *_args) -> None:
        if self._pending:
            return
        self._pending = True
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self, self._redraw)

    def _redraw(self) -> None:
        self._pending = False
        if self._closed:
            return
        self._drop_extra_views()
        self.graph.clear()
        try:
            traces = [row.read_trace() for row in self.rows]
            notes = self._draw(traces)
        except cmp.CompareError as error:
            self.note_label.setText(str(error))
            return
        finally:
            self.graph.apply_scientific_plot_appearance()
        self.note_label.setText("\n".join(notes))

    def _drop_extra_views(self) -> None:
        for view, plot, slot in getattr(self, "_extra_views", []):
            try:
                plot.vb.sigResized.disconnect(slot)
                view.setXLink(None)
                plot.getAxis("right").linkedView = None
                if view.scene() is not None:
                    view.scene().removeItem(view)
            except (RuntimeError, TypeError, AttributeError):
                pass
        self._extra_views = []

    def _grid_for(self, traces: list[cmp.Trace], x_mode: str):
        """Per-trace (x, y) after the chosen X handling; native keeps the measured X."""
        if x_mode == "native":
            return [cmp._clean(t) for t in traces], False, []
        first = traces[0]
        out, notes, is_index = [], [], x_mode == "index"
        if x_mode == "index":
            n = max(cmp._clean(t)[0].size for t in traces)
            grid = np.linspace(0.0, 1.0, n)
            for t in traces:
                x, y = cmp._clean(t)
                out.append((grid, np.interp(grid, np.linspace(0.0, 1.0, x.size), y)))
            notes.append("X values ignored: every trace is stretched to the same length.")
            return out, True, notes
        low = max(cmp._clean(t)[0][0] for t in traces)
        high = min(cmp._clean(t)[0][-1] for t in traces)
        if not high > low:
            raise cmp.CompareError("The traces have no X range in common. Use 'Ignore X values' "
                                   "or 'Native X values'.")
        if x_mode == "fixed_step":
            step = self.step_spin.value()
            count = int(np.floor((high - low) / step + 1e-9)) + 1
            if count < 2:
                raise cmp.CompareError("The step is larger than the shared X range.")
            if count > 2_000_000:
                raise cmp.CompareError("The step is too small: it would create more than 2 million points.")
            grid = low + step * np.arange(count)
        else:
            x0 = cmp._clean(first)[0]
            grid = x0[(x0 >= low) & (x0 <= high)]
            if grid.size < 2:
                grid = np.linspace(low, high, 2)
        for t in traces:
            x, y = cmp._clean(t)
            out.append((grid, np.interp(grid, x, y)))
        return out, False, notes

    def _draw(self, traces: list[cmp.Trace]) -> list[str]:
        view = self.view_combo.currentData()
        x_mode = self.x_mode_combo.currentData()
        y_mode = self.y_mode_combo.currentData()
        notes: list[str] = []
        for other in traces[1:]:
            notes += [n for n in cmp.compatibility_notes(traces[0], other) if n not in notes]
        if view in ("difference", "ratio"):
            if len(traces) < 2:
                raise cmp.CompareError("Difference and ratio need at least two traces.")
            mode = "overlap" if x_mode == "native" else x_mode
            aligned = cmp.align(traces[0], traces[1], mode, step=self.step_spin.value())
            notes += [n for n in aligned.notes if n not in notes]
            y = cmp.combine(aligned, view, y_mode)
            plot = self.graph.addPlot()
            self._style(plot, "Position (0 … 1)" if aligned.x_is_index else _label(traces[0].x_label, traces[0].x_unit),
                        ("Difference" if view == "difference" else "Ratio") + (
                            f" ({traces[0].y_unit})" if view == "difference" and y_mode == "none" and traces[0].y_unit else ""))
            plot.plot(aligned.x, y, pen=pg.mkPen(COLORS[0], width=2))
            if view == "difference":
                plot.addLine(y=0, pen=pg.mkPen("#8E8E93", style=Qt.PenStyle.DashLine))
            return notes
        pairs, is_index, extra = self._grid_for(traces, x_mode)
        notes += extra
        ys = [cmp.normalise(y, y_mode) for _x, y in pairs]
        xs = [x for x, _y in pairs]
        x_title = "Position (0 … 1)" if is_index else _label(traces[0].x_label, traces[0].x_unit)
        if view == "stacked":
            previous = None
            for i, (t, x, y) in enumerate(zip(traces, xs, ys)):
                plot = self.graph.addPlot(row=i, col=0)
                self._style(plot, x_title if is_index else _label(t.x_label, t.x_unit),
                            _label(t.y_label, t.y_unit if y_mode == "none" else None))
                plot.plot(x, y, pen=pg.mkPen(COLORS[i % len(COLORS)], width=2))
                plot.setTitle(t.name)
                if previous is not None and self.link_x_check.isChecked():
                    plot.setXLink(previous)
                previous = plot
            return notes
        units = []
        for t in traces:
            u = t.y_unit or ""
            if u not in units:
                units.append(u)
        if y_mode != "none":
            units = [""]
        if len(units) > 2:
            raise cmp.CompareError("The traces have more than two different Y units. "
                                   "Choose a Y handling that scales them, or compare fewer quantities.")
        plot = self.graph.addPlot()
        self._style(plot, x_title, _label("", units[0]) if y_mode == "none" and units[0] else
                    ("Scaled Y" if y_mode != "none" else "Y"))
        legend = plot.addLegend(offset=(10, 10))
        right = None
        if len(units) == 2:
            right = pg.ViewBox()
            plot.showAxis("right")
            plot.scene().addItem(right)
            plot.getAxis("right").linkToView(right)
            right.setXLink(plot)
            plot.getAxis("right").setLabel(units[1] or "Y")
            def slot(right=right, plot=plot):
                right.setGeometry(plot.vb.sceneBoundingRect())
            plot.vb.sigResized.connect(slot)
            self._extra_views = getattr(self, "_extra_views", []) + [(right, plot, slot)]
        for i, (t, x, y) in enumerate(zip(traces, xs, ys)):
            pen = pg.mkPen(COLORS[i % len(COLORS)], width=2)
            if right is not None and (t.y_unit or "") == units[1]:
                item = pg.PlotDataItem(x, y, pen=pen)
                right.addItem(item)
                legend.addItem(item, t.name)
            else:
                plot.plot(x, y, pen=pen, name=t.name)
        if right is not None:
            right.setGeometry(plot.vb.sceneBoundingRect())
        return notes

    @staticmethod
    def _style(plot, x_title: str, y_title: str) -> None:
        plot.showGrid(x=True, y=True, alpha=0.25)
        plot.setLabel("bottom", x_title)
        plot.setLabel("left", y_title)

    def closeEvent(self, event) -> None:
        self._closed = True
        self._drop_extra_views()
        self.graph.clear()
        for experiment in self._owned:
            try:
                experiment.close()
            except Exception:
                pass
        self._owned.clear()
        super().closeEvent(event)
