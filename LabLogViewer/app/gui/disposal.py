"""Release everything a closed Viewer owns that Qt will not delete by itself.

A parentless QWidget/QMenu (pyqtgraph's right-click menus, control pages that are not in the
visible layout, helper dialogs) is owned by Python. Its signal connections to lambdas that
capture the Viewer are invisible to Python's garbage collector, so the Viewer and everything
it owns used to stay alive after the window was closed (about 35 MB and 1,300 widgets per
opened data file). Deleting those widgets explicitly breaks the cycles.
"""

from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QGraphicsView, QWidget

try:
    from shiboken6 import delete as _delete, isValid
except ImportError:                                       # pragma: no cover
    _delete = None

    def isValid(_obj) -> bool:
        return True

_CONTAINERS = (list, tuple, set, frozenset)


def _delete_later(obj) -> None:
    try:
        if obj is not None and isValid(obj):
            obj.deleteLater()
    except Exception:
        pass


def _release_graphics(view) -> None:
    """pyqtgraph gives every ViewBox a parentless context menu and every PlotItem a parentless
    options menu; neither is deleted with the plot. Delete them and clear the scene."""
    try:
        scene = view.scene()
        items = list(scene.items()) if scene is not None else []
    except Exception:
        return
    for item in items:
        try:
            menu = getattr(item, "menu", None)
            if menu is not None and not isinstance(menu, (str, bool)) and hasattr(menu, "deleteLater"):
                _delete_later(menu)
                item.menu = None
            ctrl = getattr(item, "ctrlMenu", None)
            if ctrl is not None and hasattr(ctrl, "deleteLater"):
                _delete_later(ctrl)
                item.ctrlMenu = None
        except Exception:
            continue
    try:
        view.close()                      # pyqtgraph's close() also detaches the view from its parent
    except Exception:
        pass
    _delete_later(view)


_NEVER = ("BrowserWindow", "MainWindow")


def _expandable(value) -> bool:
    """Plain helper objects of this program (not Qt objects shared by the whole application)."""
    if isinstance(value, QObject):
        return isinstance(value, QWidget)
    return type(value).__module__.startswith("app.") and hasattr(value, "__dict__")


def dispose_widgets(owner: QObject, *, protect: tuple[type, ...] = (), limit: int = 20000) -> int:
    """Schedule deletion of every parentless widget reachable from ``owner`` through attributes
    (through the Viewer's panes, plot wrappers and helper objects). Returns how many were
    scheduled. Safe to call more than once."""
    if owner is None or not isValid(owner):
        return 0
    seen: set[int] = {id(owner)}
    count = 0
    pending: list = [vars(owner).values()]
    visited = 0
    victims: list[QWidget] = []
    views: dict[int, QGraphicsView] = {}
    while pending and visited < limit:
        for value in pending.pop():
            visited += 1
            if id(value) in seen:
                continue
            seen.add(id(value))
            if isinstance(value, dict):
                pending.append(list(value.values()))
            elif isinstance(value, _CONTAINERS):
                pending.append(list(value))
            elif isinstance(value, QWidget):
                if not isValid(value) or type(value).__name__ in _NEVER:
                    continue
                if protect and isinstance(value, protect):
                    continue
                if value.graphicsProxyWidget() is not None:
                    continue
                pending.append(list(getattr(value, "__dict__", {}).values()))
                if isinstance(value, QGraphicsView):
                    views[id(value)] = value
                if value.parent() is None:
                    victims.append(value)
            elif not isinstance(value, QObject) and _expandable(value):
                pending.append(list(value.__dict__.values()))
    for widget in victims:
        for view in [widget, *widget.findChildren(QGraphicsView)]:
            if isinstance(view, QGraphicsView):
                views[id(view)] = view
    for view in views.values():
        if isValid(view):
            _release_graphics(view)
    for widget in victims:
        widget.close()
        widget.deleteLater()
        count += 1
    return count
