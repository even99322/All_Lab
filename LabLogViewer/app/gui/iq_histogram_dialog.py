"""Histogram-2D for one complex trace: how often each (real, imaginary) value occurs."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

from app.core.data_model import Grid2DData
from app.core.plot_config import histogram_iq
from app.gui.plot_2d_widget import Plot2DWidget


class IQHistogramDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Histogram-2D (I/Q)")
        self.resize(560, 520)
        layout = QVBoxLayout(self)
        self.summary = QLabel("")
        layout.addWidget(self.summary)
        self.plot = Plot2DWidget()
        layout.addWidget(self.plot, 1)
        self.grid: Grid2DData | None = None

    def show_trace(self, values, bins: int, *, unit: str | None, name: str) -> bool:
        values = np.asarray(values)
        finite = values[np.isfinite(values)]
        if not np.iscomplexobj(values) or finite.size == 0:
            self.grid = None
            self.summary.setText("Histogram-2D needs complex (I/Q) data.")
            return False
        real, imag, counts = histogram_iq(finite, bins)
        self.grid = Grid2DData(
            x_values=real, y_values=imag, z_values=counts, x_name="Real", x_unit=unit,
            y_name="Imag", y_unit=unit, z_name="Counts", z_unit=None, transform="counts",
        )
        self.plot.plot(self.grid)
        self.summary.setText(f"{name}: {finite.size} points, {bins} x {bins} bins")
        return True
