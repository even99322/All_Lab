"""Compact native dialog for one pane's numerical scientific export."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from app.core.data_export import (
    ACTIVE_TRACE,
    CURRENT_TRANSFORM,
    DISPLAYED,
    FULL_DATA,
    FULL_RANGE,
    RAW,
    SELECTED_TRACES,
    VISIBLE_TRACES,
    VISIBLE_X_RANGE,
)


@dataclass(frozen=True)
class ExportOptions:
    scope: str
    representation: str
    range_kind: str
    format: str
    destination: str


class DataExportDialog(QDialog):
    """A deliberately small dialog; it configures export but never loads data."""

    def __init__(self, parent: QWidget | None, *, is_2d: bool,
                 default_path: str, visible_range_available: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Data")
        self.setModal(True)
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.scope_group = QButtonGroup(self)
        scope_box = QGroupBox("Scope")
        scope_layout = QVBoxLayout(scope_box)
        self.scope_buttons: dict[str, QRadioButton] = {}
        scope_items = ((ACTIVE_TRACE, "Active Trace"), (SELECTED_TRACES, "Selected Traces"),
                       (VISIBLE_TRACES, "Visible Traces"), (FULL_DATA, "Full Data"))
        for key, label in scope_items:
            button = QRadioButton(label)
            self.scope_group.addButton(button)
            self.scope_buttons[key] = button
            scope_layout.addWidget(button)
        self.scope_buttons[FULL_DATA if is_2d else ACTIVE_TRACE].setChecked(True)
        if is_2d:
            for key, button in self.scope_buttons.items():
                button.setEnabled(key == FULL_DATA)
            scope_layout.addWidget(QLabel("2D export uses the full valid grid."))
        layout.addWidget(scope_box)

        self.representation_group = QButtonGroup(self)
        representation_box = QGroupBox("Representation")
        representation_layout = QVBoxLayout(representation_box)
        self.representation_buttons: dict[str, QRadioButton] = {}
        for key, label in ((RAW, "Raw Data"), (CURRENT_TRANSFORM, "Current Transform"),
                           (DISPLAYED, "Displayed Data")):
            button = QRadioButton(label)
            self.representation_group.addButton(button)
            self.representation_buttons[key] = button
            representation_layout.addWidget(button)
        self.representation_buttons[DISPLAYED].setChecked(True)
        layout.addWidget(representation_box)

        self.range_group = QButtonGroup(self)
        range_box = QGroupBox("Range")
        range_layout = QVBoxLayout(range_box)
        self.range_buttons: dict[str, QRadioButton] = {}
        for key, label in ((FULL_RANGE, "Full Range"), (VISIBLE_X_RANGE, "Visible X Range")):
            button = QRadioButton(label)
            self.range_group.addButton(button)
            self.range_buttons[key] = button
            range_layout.addWidget(button)
        self.range_buttons[FULL_RANGE].setChecked(True)
        self.range_buttons[VISIBLE_X_RANGE].setEnabled(visible_range_available)
        layout.addWidget(range_box)

        self.format_group = QButtonGroup(self)
        format_box = QGroupBox("Format")
        format_layout = QHBoxLayout(format_box)
        self.format_buttons: dict[str, QRadioButton] = {}
        for key, label in (("csv", "CSV"), ("npz", "NPZ")):
            button = QRadioButton(label)
            self.format_group.addButton(button)
            self.format_buttons[key] = button
            format_layout.addWidget(button)
        self.format_buttons["csv"].setChecked(True)
        layout.addWidget(format_box)

        destination_row = QHBoxLayout()
        destination_row.addWidget(QLabel("Destination:"))
        self.confirmed_path: str | None = None
        self.destination_edit = QLineEdit(default_path)
        self.destination_edit.textEdited.connect(lambda _text: setattr(self, "confirmed_path", None))
        destination_row.addWidget(self.destination_edit, 1)
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._browse)
        destination_row.addWidget(browse_button)
        layout.addLayout(destination_row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        export = QPushButton("Export")
        export.setDefault(True)
        export.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(export)
        layout.addLayout(buttons)
        self.format_group.buttonClicked.connect(self._update_extension)

    @staticmethod
    def _checked(mapping: dict[str, QRadioButton]) -> str:
        return next(key for key, button in mapping.items() if button.isChecked())

    def options(self) -> ExportOptions:
        return ExportOptions(
            scope=self._checked(self.scope_buttons),
            representation=self._checked(self.representation_buttons),
            range_kind=self._checked(self.range_buttons),
            format=self._checked(self.format_buttons),
            destination=self.destination_edit.text().strip(),
        )

    def _update_extension(self, *_args) -> None:
        path = Path(self.destination_edit.text().strip())
        selected = self._checked(self.format_buttons)
        if path.name:
            self.destination_edit.setText(str(path.with_suffix(f".{selected}")))

    def _browse(self) -> None:
        extension = self._checked(self.format_buttons)
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Scientific Data", self.destination_edit.text().strip(),
            "CSV data (*.csv)" if extension == "csv" else "NumPy archive (*.npz)",
        )
        if path:
            self.destination_edit.setText(path)
            self.confirmed_path = path if Path(path).exists() else None   # "Overwrite" already chosen
