"""
app/gui/log_entries_widget.py — v0.9B

The Labber-style "Log Entries" table: the PRIMARY Trace selection UI
(spec §9/§10/§11), replacing v0.9A's plain "Trace N / Total" label as
the main representation — that label is demoted to a compact status
line, and the v0.8/v0.9A Sweep spinbox is kept as an optional quick
"jump to entry" control (spec §9: "Sweep = optional quick selection").

Columns are generated dynamically from the CURRENT experiment's
"entries"-domain axis candidates (step channels + scalar log channels)
via ChannelManager - never hardcoded to "Average Current" or any
other specific channel name (spec §11).

`TraceSelectionState` is the source of truth for the active trace, ordered
selected traces, and Shift anchor. The Qt table reflects that state and sends
mouse modifiers into it; only active-trace changes emit `entry_selected` and
therefore trigger the existing single-trace plot path.

Like every other file under app/gui/, this module must never import
h5py — all data comes through ChannelManager/Experiment, already
parsed.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.core.channel_manager import AxisCandidate, ChannelManager
from app.core.trace_selection import TraceSelectionState

MAX_ROWS = 5000  # keep the table responsive even for a very long sweep;
                    # matches the same pragmatic cap used by the Data
                    # Table view (app/core/data_table.py)


class TraceTableWidget(QTableWidget):
    """Routes mouse modifiers to the application selection model."""

    row_clicked = Signal(int, object)

    def mousePressEvent(self, event) -> None:
        index = self.indexAt(event.position().toPoint())
        if event.button() == Qt.LeftButton and index.isValid():
            self.setFocus(Qt.MouseFocusReason)
            self.row_clicked.emit(index.row(), event.modifiers())
            event.accept()
            return
        super().mousePressEvent(event)


class LogEntriesWidget(QWidget):
    """A QTableWidget of "entries"-domain candidates (Log Entries), one
    row per sweep entry, columns generated dynamically from the
    current file. Emits `entry_selected(int)` whenever the current row
    changes, for any reason (click, keyboard, or programmatic
    select_row() call from the Sweep control)."""

    entry_selected = Signal(int)
    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = TraceTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        layout.addWidget(self.table)

        self.selection_state = TraceSelectionState()
        self._n_entries = 0
        self._truncated = False
        self._suppress_signal = False
        self._active_indicator_row: int | None = None

        self.table.row_clicked.connect(self._on_row_clicked)

    # ---- population ------------------------------------------------------------

    def populate(self, mgr: ChannelManager, entries_candidates: list[AxisCandidate] | None = None) -> None:
        """Builds the table from the current experiment's
        'entries'-domain axis candidates - each becomes a column;
        each sweep entry becomes a row. `entries_candidates` can be
        passed in directly (already-filtered) to avoid recomputing
        list_axis_candidates() when the caller already has it."""
        if entries_candidates is None:
            entries_candidates = [c for c in mgr.list_axis_candidates() if c.domain == "entries"]

        self._suppress_signal = True
        updates_enabled = self.table.updatesEnabled()
        self.table.setUpdatesEnabled(False)
        try:
            self.table.clear()
            self.table.setRowCount(0)

            if not entries_candidates:
                # No scalar step/log columns are available at all (e.g.
                # a single-point file where every step channel is
                # fixed and Labber never recorded per-entry values) -
                # fall back to just a "#" column sized to however many
                # sweep entries the file's vector channel(s) report,
                # so a single-entry file still shows "Log Entries: 1"
                # rather than an empty table with no explanation.
                fallback_n = max(
                    (vt.n_entries for vt in mgr.experiment.vector_traces.values()), default=0
                )
                self._n_entries = fallback_n
                self._truncated = False
                if fallback_n:
                    self.table.setColumnCount(1)
                    self.table.setHorizontalHeaderLabels(["#"])
                    self.table.setRowCount(min(fallback_n, MAX_ROWS))
                    for row in range(min(fallback_n, MAX_ROWS)):
                        self.table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
                else:
                    self.table.setColumnCount(0)
                self._reset_selection()
                return

            # n_entries = the length of any entries-domain candidate's
            # data - they're all the same length by construction
            # (spec's domain concept: "entries" always means "one
            # value per sweep entry" for THIS experiment).
            sample = mgr.get_axis_data(entries_candidates[0])
            n_entries = len(sample)
            self._n_entries = n_entries
            self._truncated = n_entries > MAX_ROWS
            n_rows = min(n_entries, MAX_ROWS)

            headers = ["#"] + [c.name for c in entries_candidates]
            self.table.setColumnCount(len(headers))
            self.table.setHorizontalHeaderLabels(headers)
            self.table.setRowCount(n_rows)

            columns_data = [mgr.get_axis_data(c) for c in entries_candidates]

            for row in range(n_rows):
                self.table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
                for col_idx, data in enumerate(columns_data):
                    value = data[row] if row < len(data) else None
                    text = self._format_value(value)
                    self.table.setItem(row, col_idx + 1, QTableWidgetItem(text))

            # A full resizeColumnsToContents() scans every cell.  On a
            # several-thousand-entry experiment it dominated Viewer first
            # paint, despite users being able to resize these columns anyway.
            self.table.setColumnWidth(0, 52)
            for col, header in enumerate(headers[1:], start=1):
                self.table.setColumnWidth(col, min(220, max(96, len(header) * 8 + 24)))
        finally:
            self.table.setUpdatesEnabled(updates_enabled)
            self._suppress_signal = False

        self._reset_selection()

    @staticmethod
    def _format_value(value) -> str:
        if value is None:
            return ""
        if isinstance(value, (float, np.floating)):
            if not np.isfinite(value):
                return str(value)
            if abs(value) >= 1e4 or (abs(value) < 1e-3 and value != 0):
                return f"{value:.4e}"
            return f"{value:.6g}"
        return str(value)

    # ---- selection ---------------------------------------------------------------

    def current_row(self) -> int:
        return self.selection_state.active_trace or 0

    def row_count(self) -> int:
        return self.table.rowCount()

    def total_entries(self) -> int:
        return self._n_entries

    def is_truncated(self) -> bool:
        return self._truncated

    def select_row(self, row: int, modifiers=Qt.NoModifier) -> None:
        """Programmatically selects a row (used by the Sweep control
        and by keyboard Up/Down handling in the owning window) -
        clamped to the valid range, matching QSpinBox's own clamping
        behavior elsewhere in this app for consistent boundary
        handling (spec: can't go below the first or past the last
        entry)."""
        if self.table.rowCount() == 0:
            return
        row = max(0, min(row, self.table.rowCount() - 1))
        if modifiers & Qt.ShiftModifier:
            self._apply_selection(row, "range")
        elif modifiers & (Qt.ControlModifier | Qt.MetaModifier):
            self._apply_selection(row, "toggle")
        else:
            self._apply_selection(row, "single")

    def move_active(self, delta: int, *, extend: bool = False) -> None:
        old_active = self.selection_state.active_trace
        if self.selection_state.move_active(delta, extend=extend):
            self._sync_table_selection()
            self.selection_changed.emit()
            if self.selection_state.active_trace != old_active:
                self.entry_selected.emit(self.current_row())

    def ordered_selection(self) -> tuple[int, ...]:
        return self.selection_state.ordered_selection()

    def set_active(self, row: int) -> None:
        old_active = self.selection_state.active_trace
        if self.selection_state.set_active(row):
            self._sync_table_selection()
            self.selection_changed.emit()
            if self.selection_state.active_trace != old_active:
                self.entry_selected.emit(self.current_row())

    def restore_selection(
        self, selected, *, active=None, visible=None, reference=None,
    ) -> bool:
        if not self.selection_state.restore(
            selected, active=active, visible=visible, reference=reference,
        ):
            return False
        self._sync_table_selection()
        self.selection_changed.emit()
        self.entry_selected.emit(self.current_row())
        return True

    def _reset_selection(self) -> None:
        self.selection_state.reset(range(self.table.rowCount()))
        self._active_indicator_row = None
        self._sync_table_selection()

    def _on_row_clicked(self, row: int, modifiers) -> None:
        self.select_row(row, modifiers)

    def _apply_selection(self, row: int, mode: str) -> None:
        old_active = self.selection_state.active_trace
        if mode == "range":
            changed = self.selection_state.range_select(row)
        elif mode == "toggle":
            changed = self.selection_state.toggle_select(row)
        else:
            changed = self.selection_state.single_select(row)
        if not changed:
            return
        self._sync_table_selection()
        self.selection_changed.emit()
        if self.selection_state.active_trace != old_active:
            self.entry_selected.emit(self.current_row())

    def _sync_table_selection(self) -> None:
        model = self.table.selectionModel()
        if model is None:
            return
        selection = QItemSelection()
        rows = self.selection_state.ordered_selection()
        if rows and self.table.columnCount():
            group_start = previous = rows[0]
            for row in (*rows[1:], None):
                if row is not None and row == previous + 1:
                    previous = row
                    continue
                selection.select(
                    self.table.model().index(group_start, 0),
                    self.table.model().index(previous, self.table.columnCount() - 1),
                )
                if row is not None:
                    group_start = previous = row
        model.select(selection, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        active = self.selection_state.active_trace
        if active is not None and self.table.columnCount():
            model.setCurrentIndex(
                self.table.model().index(active, 0), QItemSelectionModel.NoUpdate
            )
            self.table.scrollTo(self.table.model().index(active, 0))
        self._update_active_indicator(active)

    def _update_active_indicator(self, active: int | None) -> None:
        if self._active_indicator_row is not None \
                and self._active_indicator_row < self.table.rowCount():
            self.table.setVerticalHeaderItem(
                self._active_indicator_row, QTableWidgetItem("")
            )
        if active is not None and active < self.table.rowCount():
            item = QTableWidgetItem(">")
            item.setTextAlignment(Qt.AlignCenter)
            self.table.setVerticalHeaderItem(active, item)
        self._active_indicator_row = active

    def clear(self) -> None:
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self._n_entries = 0
        self._truncated = False
        self.selection_state.reset(())
        self._active_indicator_row = None
