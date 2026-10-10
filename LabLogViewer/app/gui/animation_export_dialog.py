"""Compact, non-scientific controls for GIF/MP4 animation export."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QVBoxLayout,
)

from app.core.animation_export import (
    ALL_TRACES, GIF, MP4, MP4_RESOLUTIONS, MP4_SPEEDS, TRACE_SCOPES,
    AnimationOptions,
)


class AnimationExportDialog(QDialog):
    """Expose only supported forward/all-pane animation choices."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Animation")
        self.setModal(True)
        self.resize(360, 360)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.format_combo = QComboBox()
        self.format_combo.addItems((MP4, GIF))
        self.scope_combo = QComboBox()
        self.scope_combo.addItems(TRACE_SCOPES)
        self.direction_label = QLabel("Forward")
        self.panes_label = QLabel("All Panes")
        self.speed_combo = QComboBox()
        for speed in MP4_SPEEDS:
            self.speed_combo.addItem(f"{speed} traces/s", speed)
        self.speed_combo.setCurrentText("60 traces/s")
        self.trace_checkbox = QCheckBox("Trace Number")
        self.trace_checkbox.setChecked(True)
        self.parameter_checkbox = QCheckBox("Sweep Parameter")
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(MP4_RESOLUTIONS)
        self.resolution_combo.setCurrentText("1080p")
        self.gif_mode_label = QLabel("Every trace · 10 ms · 1080p")
        self.gif_mode_label.setToolTip(
            "Every trace in scope is rendered at 1080p and exported in order; "
            "total duration grows with trace count."
        )
        self.global_label = QLabel("Global Range")
        form.addRow("Format", self.format_combo)
        form.addRow("Trace Scope", self.scope_combo)
        form.addRow("Direction", self.direction_label)
        form.addRow("Panes", self.panes_label)
        form.addRow("Playback Speed", self.speed_combo)
        form.addRow("Frame Information", self.trace_checkbox)
        form.addRow("", self.parameter_checkbox)
        form.addRow("Resolution", self.resolution_combo)
        form.addRow("Axis Scaling", self.global_label)
        form.addRow("Mode", self.gif_mode_label)
        layout.addLayout(form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        self.buttons.button(QDialogButtonBox.Ok).setText("Export")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.format_combo.currentTextChanged.connect(self._update_format_controls)
        self._update_format_controls(self.format_combo.currentText())

    def _update_format_controls(self, format_name: str) -> None:
        mp4 = format_name == MP4
        self.speed_combo.setVisible(mp4)
        self.trace_checkbox.setVisible(mp4)
        self.parameter_checkbox.setVisible(mp4)
        self.resolution_combo.setVisible(mp4)
        self.gif_mode_label.setVisible(not mp4)

    def options(self) -> AnimationOptions:
        return AnimationOptions(
            format=self.format_combo.currentText(),
            trace_scope=self.scope_combo.currentText() or ALL_TRACES,
            traces_per_second=int(self.speed_combo.currentData() or 60),
            resolution=self.resolution_combo.currentText() or "1080p",
            show_trace_number=self.trace_checkbox.isChecked(),
            show_sweep_parameter=self.parameter_checkbox.isChecked(),
        ).validated()
