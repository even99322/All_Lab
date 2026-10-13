"""Fixed Rotation / Third dimension controls of the Viewer's Plot config."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QPushButton,
    QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from app.core.plot_config import OPERATIONS, THIRD_OPERATIONS, PlotConfig


class PlotConfigPanel(QWidget):
    """Edits one PlotConfig; emits ``changed`` (debounced) and ``calculate_requested``."""

    changed = Signal()
    calculate_requested = Signal(str)      # "phase_delay" | "rotation_per_x" | "fixed_rotation"
    db_toggled = Signal(bool)              # "Plot in dB" ticked / unticked
    third_channel_selected = Signal(str)   # the user picked another heatmap Y axis

    def __init__(self, mode: str, parent=None):
        super().__init__(parent)
        self.mode = mode
        self._loading = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.changed.emit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.fixed_group = QGroupBox("Fixed Rotation")
        form = QFormLayout(self.fixed_group)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        # v2.1.6: Phase delay — remove the electrical (cable) delay τ: z·exp(+i·2π·f·τ), f = the X axis
        self.phase_delay_spin = QDoubleSpinBox()
        self.phase_delay_spin.setRange(-1e5, 1e5)
        self.phase_delay_spin.setDecimals(4)
        self.phase_delay_spin.setSingleStep(0.1)
        self.phase_delay_spin.setSuffix(" ns")
        self.phase_delay_spin.setKeyboardTracking(False)
        self.phase_delay_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.phase_delay_spin.setObjectName("phaseDelaySpin")
        self.phase_delay_spin.setToolTip(
            "Electrical delay τ removed from complex data before Re/Im/Mag/Phase: z·exp(+i·2π·f·τ).\n"
            "f is the X axis (its unit Hz / MHz / GHz is taken into account). "
            "Calculate fits τ to the phase slope at both ends of the sweep."
        )
        self.phase_delay_calc = QPushButton("Calculate")
        self.phase_delay_calc.setObjectName("phaseDelayCalculate")
        form.addRow("Phase delay", self._with_button(self.phase_delay_spin, self.phase_delay_calc))
        self.rotation_per_x_spin = self._angle_spin(1e6)
        self.rotation_per_x_calc = QPushButton("Calculate")
        form.addRow("Rotation/x", self._with_button(self.rotation_per_x_spin, self.rotation_per_x_calc))
        self.fixed_rotation_spin = self._angle_spin(1e4)
        self.fixed_rotation_calc = QPushButton("Calculate")
        form.addRow("Fixed Rotation", self._with_button(self.fixed_rotation_spin, self.fixed_rotation_calc))
        self.auto_rotate_check = QCheckBox("Auto-rotate")
        self.auto_rotate_check.setToolTip(
            "Rotate every trace so that its main axis of variation lies on the real axis"
        )
        form.addRow(self.auto_rotate_check)
        self.unwrap_angle_check = QCheckBox("Unwrap angle")
        self.unwrap_angle_check.setToolTip("Remove 360° jumps of a Phase map along X")
        form.addRow(self.unwrap_angle_check)
        self.plot_in_db_check = QCheckBox("Plot in dB")
        self.plot_in_db_check.setToolTip(
            "Show the Magnitude in dB (20·log10). Only available when the plotted quantity is Magnitude"
        )
        self.plot_in_db_check.setEnabled(False)
        self.plot_in_db_check.toggled.connect(self.db_toggled.emit)
        form.addRow(self.plot_in_db_check)
        self.operation_combo = QComboBox()
        for name in OPERATIONS:
            self.operation_combo.addItem(name)
        form.addRow("Operation", self.operation_combo)
        self.bins_spin = QSpinBox()
        self.bins_spin.setRange(2, 100000)
        self.bins_spin.setValue(50)
        form.addRow("Bins", self.bins_spin)
        self.smooth_spin = QSpinBox()
        self.smooth_spin.setRange(1, 10001)
        self.smooth_spin.setToolTip("Running average over this many points (1 = off)")
        form.addRow("Smooth", self.smooth_spin)
        layout.addWidget(self.fixed_group)

        self.show_individual_check = QCheckBox("Show individual")
        self.show_individual_check.setChecked(True)
        self.show_individual_check.setToolTip("Draw every selected trace; off draws only the active trace")
        self.traces_group = QGroupBox("Traces")
        traces_layout = QVBoxLayout(self.traces_group)
        traces_layout.addWidget(self.show_individual_check)
        layout.addWidget(self.traces_group)

        self.third_group = QGroupBox("Third dimension")
        third = QFormLayout(self.third_group)
        third.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.third_channel_combo = QComboBox()
        self.third_channel_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.third_channel_combo.setMinimumWidth(110)
        third.addRow("Channel", self.third_channel_combo)
        self.third_operation_combo = QComboBox()
        for name in THIRD_OPERATIONS:
            self.third_operation_combo.addItem(name)
        third.addRow("Operation", self.third_operation_combo)
        self.third_smooth_spin = QSpinBox()
        self.third_smooth_spin.setRange(1, 10001)
        third.addRow("Smooth", self.third_smooth_spin)
        self.show_trace_list_check = QCheckBox("Show trace list")
        third.addRow(self.show_trace_list_check)
        self.ignore_x_check = QCheckBox("Ignore x-data")
        self.ignore_x_check.setToolTip("Plot against the point number instead of the X values")
        third.addRow(self.ignore_x_check)
        layout.addWidget(self.third_group)

        two_d = mode == "2d"
        self.traces_group.setVisible(not two_d)
        self.third_group.setVisible(two_d)
        self.unwrap_angle_check.setVisible(two_d)

        for spin in (self.phase_delay_spin, self.rotation_per_x_spin, self.fixed_rotation_spin):
            spin.valueChanged.connect(self._edited)
        for spin in (self.bins_spin, self.smooth_spin, self.third_smooth_spin):
            spin.valueChanged.connect(self._edited)
        for check in (self.auto_rotate_check, self.unwrap_angle_check, self.show_individual_check,
                      self.show_trace_list_check, self.ignore_x_check):
            check.toggled.connect(self._edited)
        for combo in (self.operation_combo, self.third_operation_combo):
            combo.currentIndexChanged.connect(self._edited)
        self.phase_delay_calc.clicked.connect(lambda: self.calculate_requested.emit("phase_delay"))
        self.rotation_per_x_calc.clicked.connect(lambda: self.calculate_requested.emit("rotation_per_x"))
        self.fixed_rotation_calc.clicked.connect(lambda: self.calculate_requested.emit("fixed_rotation"))
        self.operation_combo.currentIndexChanged.connect(self._update_visibility)
        self.third_channel_combo.activated.connect(
            lambda _i: self.third_channel_selected.emit(self.third_channel_combo.currentData() or "")
        )
        self._update_visibility()

    @staticmethod
    def _angle_spin(limit: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-limit, limit)
        spin.setDecimals(4)
        spin.setSingleStep(1.0)
        spin.setSuffix(" °")
        spin.setKeyboardTracking(False)
        spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return spin

    @staticmethod
    def _with_button(spin: QDoubleSpinBox, button: QPushButton) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(2)
        row_layout.addWidget(spin, 1)
        row_layout.addWidget(button)
        return row

    def _edited(self, *_args) -> None:
        if not self._loading:
            self._update_visibility()
            self._timer.start()

    def _update_visibility(self, *_args) -> None:
        histogram = self.operation_combo.currentText() in ("Histogram", "Histogram-2D")
        self.bins_spin.setVisible(histogram)
        self.fixed_group.layout().labelForField(self.bins_spin).setVisible(histogram)

    def set_db_state(self, checked: bool, enabled: bool) -> None:
        with QSignalBlocker(self.plot_in_db_check):
            self.plot_in_db_check.setChecked(checked)
        self.plot_in_db_check.setEnabled(enabled)

    def set_third_channels(self, names: list[str], current: str = "") -> None:
        with QSignalBlocker(self.third_channel_combo):
            self.third_channel_combo.clear()
            for name in names:
                self.third_channel_combo.addItem(name, name)
            index = self.third_channel_combo.findData(current)
            self.third_channel_combo.setCurrentIndex(max(index, 0))
            self.third_channel_combo.setEnabled(bool(names))

    def set_config(self, config: PlotConfig) -> None:
        self._loading = True
        try:
            widgets = (
                self.phase_delay_spin, self.rotation_per_x_spin, self.fixed_rotation_spin, self.auto_rotate_check,
                self.unwrap_angle_check, self.operation_combo, self.bins_spin, self.smooth_spin,
                self.show_individual_check, self.third_operation_combo,
                self.third_smooth_spin, self.show_trace_list_check, self.ignore_x_check,
            )
            blockers = [QSignalBlocker(widget) for widget in widgets]
            self.phase_delay_spin.setValue(config.phase_delay_ns)
            self.rotation_per_x_spin.setValue(config.rotation_per_x)
            self.fixed_rotation_spin.setValue(config.fixed_rotation)
            self.auto_rotate_check.setChecked(config.auto_rotate)
            self.unwrap_angle_check.setChecked(config.unwrap_angle)
            self.operation_combo.setCurrentText(config.operation)
            self.bins_spin.setValue(config.bins)
            self.smooth_spin.setValue(config.smooth)
            self.show_individual_check.setChecked(config.show_individual)
            self.third_operation_combo.setCurrentText(config.third_operation)
            self.third_smooth_spin.setValue(config.third_smooth)
            self.show_trace_list_check.setChecked(config.show_trace_list)
            self.ignore_x_check.setChecked(config.ignore_x_data)
            del blockers
        finally:
            self._loading = False
        self._update_visibility()

    def config(self) -> PlotConfig:
        return PlotConfig(
            phase_delay_ns=self.phase_delay_spin.value(),
            rotation_per_x=self.rotation_per_x_spin.value(),
            fixed_rotation=self.fixed_rotation_spin.value(),
            auto_rotate=self.auto_rotate_check.isChecked(),
            unwrap_angle=self.unwrap_angle_check.isChecked(),
            operation=self.operation_combo.currentText(),
            smooth=self.smooth_spin.value(),
            bins=self.bins_spin.value(),
            show_individual=self.show_individual_check.isChecked(),
            third_operation=self.third_operation_combo.currentText(),
            third_smooth=self.third_smooth_spin.value(),
            show_trace_list=self.show_trace_list_check.isChecked(),
            ignore_x_data=self.ignore_x_check.isChecked(),
        )
