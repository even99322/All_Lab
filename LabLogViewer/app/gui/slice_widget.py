"""
app/gui/slice_widget.py — Phase 7

Generic N-dimensional slice control panel.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.data_model import Dimension

DEBOUNCE_MS = 40


class _DimensionRow:
    def __init__(self, dimension: Dimension, row: int, layout: QGridLayout, on_change):
        self.dimension = dimension
        self._on_change = on_change

        unit_str = f" [{dimension.unit}]" if dimension.unit else ""
        name_label = QLabel(f"{dimension.name}{unit_str}")
        name_label.setMinimumWidth(140)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(max(dimension.size - 1, 0))
        self.slider.setValue(0)
        self.slider.setEnabled(dimension.size > 1)
        self.slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.index_spin = QSpinBox()
        self.index_spin.setMinimum(0)
        self.index_spin.setMaximum(max(dimension.size - 1, 0))
        self.index_spin.setValue(0)
        self.index_spin.setEnabled(dimension.size > 1)

        self.value_label = QLabel(self._format_value(0))
        self.value_label.setMinimumWidth(160)

        layout.addWidget(name_label, row, 0)
        layout.addWidget(self.slider, row, 1)
        layout.addWidget(QLabel("Index:"), row, 2)
        layout.addWidget(self.index_spin, row, 3)
        layout.addWidget(self.value_label, row, 4)

        self.slider.valueChanged.connect(self._on_slider_changed)
        self.index_spin.valueChanged.connect(self._on_spin_changed)

    def _format_value(self, index: int) -> str:
        if self.dimension.size == 0:
            return "Value: -"
        value = self.dimension.value_at(index)
        if abs(value) >= 1e4 or (abs(value) < 1e-3 and value != 0):
            return f"Value: {value:.4e}"
        return f"Value: {value:.6g}"

    def _on_slider_changed(self, value: int) -> None:
        self.index_spin.blockSignals(True)
        self.index_spin.setValue(value)
        self.index_spin.blockSignals(False)
        self.value_label.setText(self._format_value(value))
        self._on_change()

    def _on_spin_changed(self, value: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self.value_label.setText(self._format_value(value))
        self._on_change()

    def current_index(self) -> int:
        return self.index_spin.value()

    def set_index(self, index: int) -> None:
        index = max(0, min(index, self.dimension.size - 1)) if self.dimension.size else 0
        self.slider.blockSignals(True)
        self.index_spin.blockSignals(True)
        self.slider.setValue(index)
        self.index_spin.setValue(index)
        self.slider.blockSignals(False)
        self.index_spin.blockSignals(False)
        self.value_label.setText(self._format_value(index))


class SliceExplorerWidget(QWidget):
    slice_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)

        self._title = QLabel("Slice dimensions:")
        self._title.setStyleSheet("font-weight: bold;")
        self._layout.addWidget(self._title)

        self._grid_container = QWidget()
        self._grid = QGridLayout(self._grid_container)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self._grid_container)

        self._placeholder = QLabel("(no remaining dimensions - full X/Y coverage)")
        self._placeholder.setStyleSheet("color: gray; font-style: italic;")
        self._layout.addWidget(self._placeholder)

        self._rows: list[_DimensionRow] = []

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self.slice_changed.emit)

    def set_dimensions(self, dimensions: list[Dimension],
                        preserve_indices: dict[str, int] | None = None) -> None:
        preserve_indices = preserve_indices or {}

        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._rows.clear()

        for row_idx, dim in enumerate(dimensions):
            row = _DimensionRow(dim, row_idx, self._grid, self._schedule_change)
            if dim.name in preserve_indices:
                row.set_index(preserve_indices[dim.name])
            self._rows.append(row)

        self._placeholder.setVisible(len(dimensions) == 0)
        self._grid_container.setVisible(len(dimensions) > 0)

    def current_fixed(self) -> dict[str, int]:
        return {row.dimension.name: row.current_index() for row in self._rows}

    def clear(self) -> None:
        self.set_dimensions([])

    def _schedule_change(self) -> None:
        self._debounce_timer.start()
