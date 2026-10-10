"""Independent Node / Antinode analysis window for one Viewer Data Model."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
    QMainWindow, QPushButton, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.palette import ANALYSIS
from app.core.data_model import Grid2DData
from app.core.node_antinode import (
    NodeAntinodeError, NodeAntinodeParameters, NodeAntinodeResult,
    analyze_nodes_and_antinodes, get_node_analysis_grid,
)
from app.gui.plot_2d_widget import Plot2DWidget


_UNIT_TO_HZ = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}


class _AnalysisWorker(QThread):
    completed = Signal(object, object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, experiment, channel_name: str, sweep_axis_name: str,
                 parameters: NodeAntinodeParameters, parent=None):
        super().__init__(parent)
        self.experiment = experiment
        self.channel_name = channel_name
        self.sweep_axis_name = sweep_axis_name
        self.parameters = parameters

    def run(self) -> None:
        try:
            grid = get_node_analysis_grid(
                self.experiment, self.channel_name, self.sweep_axis_name
            )
            if self.isInterruptionRequested():
                self.cancelled.emit()
                return
            result = analyze_nodes_and_antinodes(
                grid.y_values, grid.x_values, grid.z_values, self.parameters,
                cancel_check=self.isInterruptionRequested,
            )
            self.completed.emit(grid, result)
        except Exception as error:
            if self.isInterruptionRequested():
                self.cancelled.emit()
            else:
                self.failed.emit(str(error))


class NodeAntinodeWindow(QMainWindow):
    """Modeless tool window owned by a Viewer, never by the Browser sidebar."""

    analysis_completed = Signal(object, object)
    analysis_failed = Signal(str)

    def __init__(self, experiment, parent=None):
        super().__init__(parent, Qt.Window)
        self.experiment = experiment
        self.source_identity = experiment.data_identity
        self.setWindowTitle(f"Node / Antinode Finder — {experiment.display_name}")
        self.resize(1120, 780)
        self._worker: _AnalysisWorker | None = None
        self._plot_items: list[object] = []
        self._source_grid: Grid2DData | None = None
        self._result: NodeAntinodeResult | None = None
        self._hz_per_axis_unit = 1.0
        from app.settings.dialog import install_settings_menu
        self.settings_menu = install_settings_menu(self)
        from app.gui.network_panel import install_network_bar

        install_network_bar(self)

        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        self.setCentralWidget(central)

        controls = QWidget(central)
        form = QFormLayout(controls)
        form.setContentsMargins(0, 0, 0, 0)
        self.channel_combo = QComboBox(controls)
        form.addRow("Complex S parameter", self.channel_combo)
        self.sweep_combo = QComboBox(controls)
        form.addRow("Sweep Axis", self.sweep_combo)
        self.detection_mode_combo = QComboBox(controls)
        self.detection_mode_combo.addItems(["Node", "Antinode", "Both"])
        self.detection_mode_combo.setCurrentText("Both")
        form.addRow("Detection Mode", self.detection_mode_combo)

        parameter_grid = QGridLayout()
        self.window_spin = QDoubleSpinBox(controls)
        self.window_spin.setRange(1e-12, 1e15)
        self.window_spin.setDecimals(4)
        self.window_spin.setValue(0.25)
        self.window_spin.setSuffix(" GHz")
        parameter_grid.addWidget(QLabel("Trajectory window half-width", controls), 0, 0)
        parameter_grid.addWidget(self.window_spin, 0, 1)
        self.smoothing_spin = QSpinBox(controls)
        self.smoothing_spin.setRange(1, 101)
        self.smoothing_spin.setValue(3)
        parameter_grid.addWidget(QLabel("Smoothing points", controls), 0, 2)
        parameter_grid.addWidget(self.smoothing_spin, 0, 3)
        self.distance_spin = QSpinBox(controls)
        self.distance_spin.setRange(1, 100000)
        self.distance_spin.setValue(10)
        parameter_grid.addWidget(QLabel("Minimum distance", controls), 1, 0)
        parameter_grid.addWidget(self.distance_spin, 1, 1)
        self.prominence_spin = QDoubleSpinBox(controls)
        self.prominence_spin.setRange(0.0, 1e6)
        self.prominence_spin.setDecimals(6)
        self.prominence_spin.setValue(0.0002)
        parameter_grid.addWidget(QLabel("Prominence", controls), 1, 2)
        parameter_grid.addWidget(self.prominence_spin, 1, 3)
        form.addRow(parameter_grid)

        threshold_row = QHBoxLayout()
        self.manual_threshold = QCheckBox("Manual dip-depth threshold", controls)
        self.threshold_spin = QDoubleSpinBox(controls)
        self.threshold_spin.setRange(0.0, 1e9)
        self.threshold_spin.setDecimals(6)
        self.threshold_spin.setValue(0.1)
        self.threshold_spin.setEnabled(False)
        self.manual_threshold.toggled.connect(self.threshold_spin.setEnabled)
        self.effective_threshold_label = QLabel("Effective threshold: median dip depth", controls)
        threshold_row.addWidget(self.manual_threshold)
        threshold_row.addWidget(self.threshold_spin)
        threshold_row.addWidget(self.effective_threshold_label)
        threshold_row.addStretch(1)
        self.analyze_button = QPushButton("Analyze", controls)
        self.analyze_button.clicked.connect(self._start_analysis)
        self.cancel_button = QPushButton("Cancel", controls)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_analysis)
        threshold_row.addWidget(self.cancel_button)
        threshold_row.addWidget(self.analyze_button)
        form.addRow(threshold_row)
        layout.addWidget(controls)

        self.status_label = QLabel("Choose an S parameter and run the analysis.", central)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self._populate_channels()
        self.channel_combo.currentTextChanged.connect(self._update_parameter_limits)
        self.manual_threshold.toggled.connect(self._update_effective_threshold_label)
        self.threshold_spin.valueChanged.connect(self._update_effective_threshold_label)
        self._update_parameter_limits()
        self._update_effective_threshold_label()

        self.heatmap = Plot2DWidget(central)
        self.average_plot = pg.PlotWidget(central)
        self.average_plot.getPlotItem().hideButtons()
        self.average_plot.addLegend()
        self.average_plot.setLabel("bottom", "Sweep")
        self.average_plot.setLabel("left", "Average |S|")
        plots = QSplitter(Qt.Horizontal, central)
        plots.addWidget(self.heatmap)
        plots.addWidget(self.average_plot)
        plots.setStretchFactor(0, 1)
        plots.setStretchFactor(1, 1)
        layout.addWidget(plots, 1)

        self.results_table = QTableWidget(0, 9, central)
        self.results_table.setHorizontalHeaderLabels(
            ["Candidate Type", "Candidate #", "Sweep Index (0-based)",
             "Sweep Parameter Value", "Unit", "Exact Resonance Frequency",
             "Unit", "T_avg |S|", "Status"]
        )
        self.results_table.setMaximumHeight(190)
        self.results_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.results_table)

    def _populate_channels(self) -> None:
        self.sweep_combo.addItems([axis.channel.name for axis in self.experiment.step_axes])
        if len(self.experiment.step_axes) != 1:
            self.sweep_combo.setEnabled(False)
            self.channel_combo.setEnabled(False)
            self.analyze_button.setEnabled(False)
            self.status_label.setText(
                "This Data has multiple or no active sweep dimensions; the current analyzer requires one."
            )
            return
        valid = []
        for name, trace in self.experiment.vector_traces.items():
            if not trace.complex or not trace.x_name:
                continue
            axis_unit = (trace.x_unit or "").strip().lower()
            if axis_unit not in _UNIT_TO_HZ:
                continue
            try:
                dimensions = self.experiment.list_dimensions(name)
            except Exception:
                continue
            if len(dimensions) == 2:
                valid.append(name)
        self.channel_combo.addItems(valid)
        if not valid:
            self.channel_combo.setEnabled(False)
            self.analyze_button.setEnabled(False)
            self.status_label.setText(
                "No compatible complex S-parameter with a named frequency axis and one sweep dimension was found."
            )

    def _update_parameter_limits(self) -> None:
        trace = self.experiment.vector_traces.get(self.channel_combo.currentText())
        if trace is None:
            return
        maximum = max(1, min(101, int(trace.n_entries)))
        self.smoothing_spin.setMaximum(maximum)
        unit = (trace.x_unit or "").strip()
        hz_per_unit = _UNIT_TO_HZ.get(unit.lower())
        if hz_per_unit is not None:
            default_width = 0.25e9 / hz_per_unit
            self.window_spin.setRange(
                max(default_width * 1e-9, 1e-12), max(default_width * 1000, 1.0)
            )
            self.window_spin.setDecimals(8 if default_width < 1 else 4)
            self.window_spin.setSingleStep(max(default_width / 10, 1e-8))
            self.window_spin.setSuffix(f" {unit}")
            self.window_spin.setValue(default_width)

    def _update_effective_threshold_label(self) -> None:
        if self.manual_threshold.isChecked():
            self.effective_threshold_label.setText(
                f"Effective threshold: {self.threshold_spin.value():.6g}"
            )
        else:
            self.effective_threshold_label.setText("Effective threshold: median dip depth")

    def _start_analysis(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        channel_name = self.channel_combo.currentText()
        trace = self.experiment.vector_traces.get(channel_name)
        if trace is None:
            self.status_label.setText("Choose a compatible complex S-parameter.")
            return
        unit = (trace.x_unit or "").strip().lower()
        self._hz_per_axis_unit = _UNIT_TO_HZ.get(unit, 0.0)
        if not self._hz_per_axis_unit:
            self.status_label.setText(f"Unsupported frequency unit: {trace.x_unit or '(missing)'}")
            return
        threshold = self.threshold_spin.value() if self.manual_threshold.isChecked() else None
        parameters = NodeAntinodeParameters(
            window_half_width=self.window_spin.value(),
            smoothing_points=self.smoothing_spin.value(),
            minimum_distance=self.distance_spin.value(),
            prominence=self.prominence_spin.value(),
            dip_depth_threshold=threshold,
            detection_mode=self.detection_mode_combo.currentText(),
        )
        self.analyze_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("Cancel")
        self._result = None
        self.results_table.setRowCount(0)
        self.average_plot.clear()
        self.heatmap.plot_item.clear()
        self._plot_items.clear()
        self._source_grid = None
        self.status_label.setText("Loading the selected Data Model and analyzing…")
        worker = _AnalysisWorker(
            self.experiment, channel_name, self.sweep_combo.currentText(), parameters, self
        )
        self._worker = worker
        worker.completed.connect(self._show_result)
        worker.failed.connect(self._show_error)
        worker.cancelled.connect(self._show_cancelled)
        worker.finished.connect(self._worker_finished)
        worker.start()

    def _worker_finished(self) -> None:
        self.analyze_button.setEnabled(self.channel_combo.isEnabled())
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Cancel")

    def _cancel_analysis(self) -> None:
        worker = self._worker
        if worker is not None and worker.isRunning():
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText("Cancelling…")
            worker.requestInterruption()

    def _show_cancelled(self) -> None:
        self.status_label.setText("Analysis cancelled. No result was applied.")

    def _show_error(self, message: str) -> None:
        self.status_label.setText(f"Analysis unavailable: {message}")
        self.analysis_failed.emit(message)

    def _show_result(self, source_grid: Grid2DData, result: NodeAntinodeResult) -> None:
        self._clear_plot_items()
        self._source_grid = source_grid
        self._result = result
        display_grid = Grid2DData(
            x_values=source_grid.x_values,
            y_values=source_grid.y_values,
            z_values=np.abs(source_grid.z_values),
            x_name=source_grid.x_name,
            x_unit=source_grid.x_unit,
            y_name=source_grid.y_name,
            y_unit=source_grid.y_unit,
            z_name=source_grid.z_name,
            z_unit="|S|",
            transform="magnitude",
            acquisition=source_grid.acquisition,
        )
        self.heatmap.plot(display_grid)
        plot = self.heatmap.plot_item
        self._plot_items.append(plot.plot(
            result.fitted_frequency, result.sweep_values,
            pen=pg.mkPen(ANALYSIS["node_finder"]["fitted"], width=2, style=Qt.DashLine), name="Fitted trajectory",
        ))
        for indices, color, symbol, name in (
            (np.flatnonzero(result.valid_fit_mask), ANALYSIS["node_finder"]["fit_traces"], "+", "Fit traces"),
            (result.node_indices, ANALYSIS["node_finder"]["nodes"], "o", "Nodes"),
            (result.antinode_indices, ANALYSIS["node_finder"]["antinodes"], "t", "Antinodes"),
        ):
            if indices.size:
                x_values = result.dip_frequencies[indices] if name == "Fit traces" else result.exact_resonance_frequencies[indices]
                item = pg.ScatterPlotItem(
                    x=x_values, y=result.sweep_values[indices], symbol=symbol,
                    size=9 if name != "Fit traces" else 6,
                    pen=pg.mkPen(color, width=1), brush=pg.mkBrush(color), name=name,
                )
                plot.addItem(item)
                self._plot_items.append(item)

        self.average_plot.clear()
        self.average_plot.addLegend()
        self.average_plot.plot(
            result.sweep_values, result.average_transmission,
            pen=pg.mkPen(ANALYSIS["node_finder"]["average"], width=1), name="Average transmission",
        )
        self.average_plot.plot(
            result.sweep_values, result.smoothed_transmission,
            pen=pg.mkPen(ANALYSIS["node_finder"]["smoothed"], width=2), name="Smoothed",
        )
        for indices, color, symbol, name in (
            (result.node_indices, ANALYSIS["node_finder"]["nodes"], "o", "Nodes"),
            (result.antinode_indices, ANALYSIS["node_finder"]["antinodes"], "t", "Antinodes"),
        ):
            self.average_plot.plot(
                result.sweep_values[indices], result.smoothed_transmission[indices],
                pen=None, symbol=symbol, symbolSize=9,
                symbolBrush=color, symbolPen=color, name=name,
            )
        self.average_plot.setLabel(
            "bottom", f"{source_grid.y_name} [{source_grid.y_unit or '-'}]"
        )
        self._fill_results_table(result, source_grid.y_unit, source_grid.x_unit)
        self.effective_threshold_label.setText(
            f"Effective threshold: {result.dip_depth_threshold:.6g}"
        )
        self.status_label.setText(
            f"{source_grid.z_name}: {len(result.node_indices)} Node Candidates, "
            f"{len(result.antinode_indices)} Antinode Candidates; fit used "
            f"{int(np.count_nonzero(result.valid_fit_mask))} of {result.sweep_values.size} traces."
        )
        self.analysis_completed.emit(source_grid, result)

    def _clear_plot_items(self) -> None:
        if self._source_grid is None:
            self._plot_items.clear()
            return
        plot = self.heatmap.plot_item
        for item in self._plot_items:
            try:
                plot.removeItem(item)
            except Exception:
                continue
        self._plot_items.clear()

    def _fill_results_table(self, result: NodeAntinodeResult,
                            sweep_unit: str, frequency_unit: str) -> None:
        records = []
        for candidate_type, indices in (
            ("Node Candidate", result.node_indices),
            ("Antinode Candidate", result.antinode_indices),
        ):
            for ordinal, index in enumerate(indices, start=1):
                records.append((candidate_type, ordinal, int(index)))
        self.results_table.setRowCount(len(records))
        for row, (kind, ordinal, index) in enumerate(records):
            values = (
                kind,
                str(ordinal),
                str(index),
                f"{result.sweep_values[index]:.8g}",
                sweep_unit or "-",
                f"{result.exact_resonance_frequencies[index]:.9g}",
                frequency_unit or "-",
                f"{result.average_transmission[index]:.8g}",
                "Detected",
            )
            for column, value in enumerate(values):
                self.results_table.setItem(row, column, QTableWidgetItem(value))

    def closeEvent(self, event: QCloseEvent) -> None:
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            worker.wait()
        super().closeEvent(event)
