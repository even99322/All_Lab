"""Quick actions the user can put on the pet: shortcuts to existing LabLogViewer windows.

They never unlock anything: every function stays available from the normal menus at every level.
Viewer actions run on the Viewer the user used most recently; Browser actions on the Browser.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid


@dataclass(frozen=True)
class QuickAction:
    id: str
    label_key: str
    glyph: str              # short text drawn on the hover bar / panel button
    target: str             # "viewer" or "browser"
    method: str


ACTIONS = (
    QuickAction("yig", "pet.action.yig", "YIG", "viewer", "_open_yig_fitting_window"),
    QuickAction("export", "pet.action.export", "⇪", "viewer", "_export_active_pane_data"),
    QuickAction("three_d", "pet.action.three_d", "3D", "viewer", "open_3d_window"),
    QuickAction("node_antinode", "pet.action.node_antinode", "N/A", "viewer", "_open_node_antinode_window"),
    QuickAction("compare", "pet.action.compare", "⇄", "viewer", "_open_compare_window"),
    QuickAction("formula", "pet.action.formula", "ƒ", "viewer", "_pet_focus_formula"),
    QuickAction("debackground", "pet.action.debackground", "BG", "browser", "_open_debackground"),
    QuickAction("browser", "pet.action.browser", "▤", "browser", "_pet_raise"),
)


def get(action_id: str) -> QuickAction | None:
    return next((a for a in ACTIONS if a.id == action_id), None)


class ActiveViewerTracker(QObject):
    """Remembers the Viewer (MainWindow) the user focused most recently."""

    def __init__(self):
        super().__init__()
        self._viewer = None
        app = QApplication.instance()
        if app is not None:
            app.focusWindowChanged.connect(self._focus_changed)

    def _focus_changed(self, window) -> None:
        from app.gui.main_window import MainWindow

        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, MainWindow) and widget.windowHandle() is window:
                self._viewer = widget
                return

    def viewer(self):
        from app.gui.main_window import MainWindow

        if self._viewer is not None and isValid(self._viewer) and self._viewer.isVisible():
            return self._viewer
        viewers = [w for w in QApplication.topLevelWidgets() if isinstance(w, MainWindow) and w.isVisible()]
        return viewers[-1] if viewers else None

    @staticmethod
    def browser():
        from app.gui.browser_window import BrowserWindow

        found = [w for w in QApplication.topLevelWidgets() if isinstance(w, BrowserWindow)]
        return found[0] if found else None


def run(action_id: str, tracker: ActiveViewerTracker) -> str | None:
    """Run a quick action; returns a localization key explaining why it could not run, or None."""
    action = get(action_id)
    if action is None:
        return "pet.action.unknown"
    window = tracker.viewer() if action.target == "viewer" else tracker.browser()
    if window is None:
        return "pet.action.need_viewer" if action.target == "viewer" else "pet.action.need_browser"
    if action.target == "viewer" and getattr(window, "experiment", None) is None:
        return "pet.action.need_viewer"
    if action.method == "_pet_raise":
        window.showNormal(); window.raise_(); window.activateWindow()
        return None
    if action.method == "_pet_focus_formula":
        window.showNormal(); window.raise_(); window.activateWindow()
        button = getattr(window, "formula_enable_button", None)
        if button is not None:
            button.setFocus()
        return None
    handler = getattr(window, action.method, None)
    if handler is None:
        return "pet.action.unknown"
    window.raise_()
    handler()
    return None
