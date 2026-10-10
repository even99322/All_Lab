"""Settings > Appearance > Advanced > Performance Monitor: live view of lag, CPU, memory and
where the UI thread spends its time (see app/core/perf_monitor.py)."""

from __future__ import annotations

from datetime import datetime

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    QHeaderView,
)

from app.core.perf_monitor import monitor


class PerformanceWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Performance Monitor")
        self.resize(760, 760)
        self.monitor = monitor()
        layout = QVBoxLayout(self)
        self.summary = QLabel("")
        self.summary.setObjectName("performanceSummary")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.plots = {}
        self.curves = {}
        for key, title, color in (("lag", "UI lag (ms)", "#FF453A"), ("cpu", "CPU (% of one core)", "#0A84FF"),
                                  ("memory", "Memory (MB)", "#30D158")):
            plot = pg.PlotWidget()
            plot.setMinimumHeight(110)
            plot.setLabel("left", title)
            plot.showGrid(x=True, y=True, alpha=0.25)
            plot.setMenuEnabled(False)
            self.plots[key] = plot
            self.curves[key] = plot.plot(pen=pg.mkPen(color, width=2))
            layout.addWidget(plot)

        layout.addWidget(QLabel("Where the UI thread spends its time"))
        self.places = QTableWidget(0, 2)
        self.places.setHorizontalHeaderLabels(["Share (%)", "Function"])
        self.places.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.places.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.places, 1)
        layout.addWidget(QLabel("Recent stalls (the UI froze for at least 0.15 s)"))
        self.stalls = QTableWidget(0, 3)
        self.stalls.setHorizontalHeaderLabels(["Time", "Duration (ms)", "Where it was stuck"])
        self.stalls.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.stalls.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.stalls, 1)

        row = QHBoxLayout()
        self.toggle_button = QPushButton("Pause")
        self.reset_button = QPushButton("Reset")
        self.copy_button = QPushButton("Copy Report")
        self.save_button = QPushButton("Save Report...")
        for button in (self.toggle_button, self.reset_button, self.copy_button, self.save_button):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self.toggle_button.clicked.connect(self._toggle)
        self.reset_button.clicked.connect(self.monitor.reset)
        self.copy_button.clicked.connect(self._copy)
        self.save_button.clicked.connect(self._save)
        self._listening = False
        self._listen()

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.monitor.report())

    def _toggle(self) -> None:
        if self.monitor.running:
            self.monitor.stop()
            self.toggle_button.setText("Resume")
        else:
            self.monitor.start()
            self.toggle_button.setText("Pause")

    def _refresh(self) -> None:
        samples = list(self.monitor.samples)
        if not samples:
            return
        start = samples[0].when
        xs = [s.when - start for s in samples]
        self.curves["lag"].setData(xs, [s.lag_ms for s in samples])
        self.curves["cpu"].setData(xs, [s.cpu_percent for s in samples])
        self.curves["memory"].setData(xs, [s.rss_mb for s in samples])
        last = samples[-1]
        self.summary.setText(
            f"UI lag {last.lag_ms:.0f} ms   CPU {last.cpu_percent:.0f} %   Memory {last.rss_mb:.0f} MB   "
            f"Threads {last.threads}   Qt widgets {last.widgets}")
        if len(samples) % 5 == 0:
            self._fill_tables()

    def _fill_tables(self) -> None:
        places = self.monitor.top_places(15)
        self.places.setRowCount(len(places))
        for row, (place, share) in enumerate(places):
            self.places.setItem(row, 0, QTableWidgetItem(f"{share:.1f}"))
            self.places.setItem(row, 1, QTableWidgetItem(place))
        stalls = self.monitor.recent_stalls()[:30]
        self.stalls.setRowCount(len(stalls))
        for row, stall in enumerate(stalls):
            self.stalls.setItem(row, 0, QTableWidgetItem(datetime.fromtimestamp(stall.when).strftime("%H:%M:%S")))
            self.stalls.setItem(row, 1, QTableWidgetItem(f"{stall.seconds * 1000:.0f}"))
            self.stalls.setItem(row, 2, QTableWidgetItem(stall.where))

    def _save(self) -> None:
        from app.core.data_location import data_root

        folder = data_root() / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"performance-{datetime.now():%Y%m%d-%H%M%S}.txt"
        path.write_text(self.monitor.report(), encoding="utf-8")
        self.summary.setText(f"Report saved: {path}")

    def _listen(self) -> None:
        if not self._listening:
            self.monitor.updated.connect(self._refresh)
            self._listening = True
        self.monitor.start()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API spelling
        super().showEvent(event)
        self._listen()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API spelling
        # Closed means idle: stop sampling and stop refreshing. The window object is
        # kept and re-shown, so repeated opening never builds new widgets.
        self.monitor.stop()
        if self._listening:
            try:
                self.monitor.updated.disconnect(self._refresh)
            except (RuntimeError, TypeError):
                pass
            self._listening = False
        super().closeEvent(event)
