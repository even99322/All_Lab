"""Top menus that act directly: "Network Workspace" and "Settings".

Clicking the menu title opens its window at once (the drop-down closes
immediately). A single item with the same name stays inside as a fallback for
platforms where a menu cannot be dismissed programmatically.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu


def direct_menu(window, title: str, item_text: str, callback: Callable[[], None]) -> QMenu:
    menu = window.menuBar().addMenu(title)
    menu.menuAction().setMenuRole(QAction.MenuRole.NoRole)
    action = QAction(item_text, window)
    action.setMenuRole(QAction.MenuRole.NoRole)
    action.triggered.connect(lambda _checked=False: callback())
    menu.addAction(action)
    menu.direct_action = action

    def opened() -> None:
        QTimer.singleShot(0, menu.close)
        QTimer.singleShot(0, callback)

    menu.aboutToShow.connect(opened)
    return menu


def install_top_menus(window, *, settings: Callable[[], None], network: Callable[[], None],
                      help: Callable[[], None] | None = None) -> QMenu:
    """Network Workspace, Settings, then Help (returns the Settings menu)."""
    window.network_menu = direct_menu(window, "Network Workspace", "Network Workspace...", network)
    menu = direct_menu(window, "&Settings", "Settings...", settings)
    if help is not None:
        window.help_menu = direct_menu(window, "Help", "LabLogViewer Help...", help)
    return menu
