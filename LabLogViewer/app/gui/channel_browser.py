"""
app/gui/channel_browser.py — Phase 5A

Read-only channel browser panel (spec §9). Built entirely from
ChannelManager.list_step_channels() / list_log_channels() — never
touches h5py or the raw Experiment/HDF5Reader directly.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QWidget, QVBoxLayout, QLabel

from app.core.channel_manager import ChannelManager, ChannelSummary


class ChannelBrowser(QWidget):
    """Two-section tree: Step Channels / Log Channels. Each channel
    shows name, unit, instrument, and (for log channels) whether it's
    a vector/complex trace channel with its point count."""

    COLUMNS = ["Name", "Unit", "Instrument", "Type"]

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._title = QLabel("Channel Browser")
        self._title.setStyleSheet("font-weight: bold; padding: 2px;")
        layout.addWidget(self._title)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(self.COLUMNS))
        self.tree.setHeaderLabels(self.COLUMNS)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        layout.addWidget(self.tree)

        self.clear()

    def clear(self) -> None:
        self.tree.clear()
        self._title.setText("Channel Browser  (no file loaded)")

    def load(self, mgr: ChannelManager, log_name: str = "") -> None:
        self.tree.clear()
        self._title.setText(f"Channel Browser — {log_name}" if log_name else "Channel Browser")

        step_root = QTreeWidgetItem(["Step Channels", "", "", ""])
        step_root.setFirstColumnSpanned(False)
        self.tree.addTopLevelItem(step_root)
        active_names = set(mgr.list_active_sweep_axes())
        for c in mgr.list_step_channels():
            kind = "ACTIVE SWEEP" if c.name in active_names else "fixed"
            item = QTreeWidgetItem([c.name, c.unit or "-", c.instrument or "-", kind])
            step_root.addChild(item)

        log_root = QTreeWidgetItem(["Log Channels", "", "", ""])
        self.tree.addTopLevelItem(log_root)
        for c in mgr.list_log_channels():
            kind = self._log_kind_label(c)
            item = QTreeWidgetItem([c.name, c.unit or "-", c.instrument or "-", kind])
            log_root.addChild(item)

        self.tree.expandAll()
        for i in range(len(self.COLUMNS)):
            self.tree.resizeColumnToContents(i)

    @staticmethod
    def _log_kind_label(c: ChannelSummary) -> str:
        if not c.is_vector:
            return "scalar"
        complex_str = "complex" if c.is_complex else "vector"
        pts = f", N={c.n_points}" if c.n_points else ""
        return f"{complex_str}{pts}"
