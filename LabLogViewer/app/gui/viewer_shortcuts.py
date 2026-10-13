"""Single-key shortcuts of a Viewer (v2.1.6): Esc full view, M Point Mark, 1 / 2 Plot Mode, F Maximize Plot …

The keys come from app/core/shortcuts.py (Settings > Shortcuts) and are rebound in every open Viewer when they
change. A key typed into a text or number field still goes to that field (Qt asks the focused field first).
"""

from __future__ import annotations

from typing import Callable

import pyqtgraph as pg
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut

from app.core import shortcuts


class ViewerShortcuts(QObject):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.w = window
        self._items: dict[str, QShortcut] = {}
        self._saved_delay: dict[int, float] = {}
        self.handlers: dict[str, Callable[[], None]] = {
            "reset_view": self.reset_view,
            "point_mark": self.point_mark,
            "plot_1d": lambda: self.plot_mode(0),
            "plot_2d": lambda: self.plot_mode(1),
            "maximize": self.maximize,
            "data_points": self.data_points,
            "auto_color": self.auto_color,
            "hide_controls": self.hide_controls,
            "phase_delay": self.phase_delay,
            "traces": self.traces,
        }
        self.rebind()
        shortcuts.notifier.changed.connect(self.rebind)
        window.destroyed.connect(self._disconnect)

    def _disconnect(self, *_args) -> None:
        try:
            shortcuts.notifier.changed.disconnect(self.rebind)
        except (RuntimeError, TypeError):
            pass

    def rebind(self) -> None:
        for item in self._items.values():
            item.setEnabled(False)
            item.deleteLater()
        self._items = {}
        for action, key in shortcuts.bindings().items():
            if not key or action not in self.handlers:
                continue
            item = QShortcut(QKeySequence(key), self.w)
            item.setContext(Qt.ShortcutContext.WindowShortcut)
            item.setAutoRepeat(False)
            item.activated.connect(lambda a=action: self.trigger(a))
            self._items[action] = item

    def key(self, action: str) -> str:
        item = self._items.get(action)
        return item.key().toString() if item is not None else ""

    def trigger(self, action: str) -> None:
        try:
            self.handlers[action]()
        except Exception as error:                     # a shortcut never takes the Viewer down
            try:
                self.w.statusBar().showMessage(f"Shortcut failed: {error}", 4000)
            except RuntimeError:
                pass

    # ---- actions ---------------------------------------------------------------------------------
    def reset_view(self) -> None:
        """Every plot in this window back to its full range (the zoom history is cleared too)."""
        count = 0
        for view in self.w.findChildren(pg.GraphicsView):
            if not view.isVisible():
                continue
            for item in view.items():
                if isinstance(item, pg.ViewBox):
                    item.enableAutoRange(pg.ViewBox.XYAxes, True)
                    item.autoRange()
                    count += 1
        zoom = getattr(self.w, "rect_zoom", None)
        if zoom is not None and hasattr(zoom, "_history"):
            zoom._history.clear()
        if count:
            self.w.statusBar().showMessage("Full view", 1500)

    def point_mark(self) -> None:
        from app.core.mark_model import POINT_MARK

        w = self.w
        placing = w.add_mark_button.isChecked() and getattr(w, "_pending_mark_tool", None) == POINT_MARK
        if placing:
            w._cancel_mark_placement()
            w.statusBar().showMessage("Point Mark off", 1500)
            return
        index = w.mark_tool_combo.findData(POINT_MARK)
        if index < 0:
            return
        if w.mark_tool_combo.currentIndex() == index:
            w._on_mark_tool_changed()
        else:
            w.mark_tool_combo.setCurrentIndex(index)
        if w.add_mark_button.isEnabled() and not w.add_mark_button.isChecked():
            w.add_mark_button.setChecked(True)
        if w.add_mark_button.isChecked():
            w.statusBar().showMessage("Point Mark on: click the plot", 2500)

    def plot_mode(self, index: int) -> None:
        combo = self.w.mode_combo
        if combo.isEnabled() and index < combo.count():
            combo.setCurrentIndex(index)

    def maximize(self) -> None:
        button = self.w.maximize_plot_button
        if button.isEnabled() and button.isVisible():
            button.toggle()

    def data_points(self) -> None:
        box = self.w.show_data_points_checkbox
        if box.isEnabled():
            box.toggle()

    def auto_color(self) -> None:
        w = self.w
        if w.mode_combo.currentIndex() == 1 and w.auto_range_button.isEnabled():
            w.auto_range_button.click()

    def hide_controls(self) -> None:
        self.w.toggle_channels_button.toggle()

    def phase_delay(self) -> None:
        """Phase delay on / off: off keeps the value, on restores it (or fits τ the first time)."""
        w = self.w
        panel = w.plot_config_2d if w.mode_combo.currentIndex() == 1 else w.plot_config_1d
        spin = panel.phase_delay_spin
        if spin.value():
            self._saved_delay[id(panel)] = spin.value()
            spin.setValue(0.0)
            w.statusBar().showMessage("Phase delay off", 2000)
        elif self._saved_delay.get(id(panel)):
            spin.setValue(self._saved_delay[id(panel)])
            w.statusBar().showMessage(f"Phase delay on: {spin.value():.4g} ns", 2500)
        else:
            panel.calculate_requested.emit("phase_delay")

    def traces(self) -> None:
        self.w._show_trace_manager()


def install(window) -> ViewerShortcuts:
    return ViewerShortcuts(window)
