"""Host side of the YIG mirror: the whole YIG window as data.

Every visible control of the YIG window (the left settings panel, tab bars,
buttons, combo boxes, numbers, check boxes, text, tables, formula images)
is described by its type, its place in pixels and its current value; figures
are sent as data (figure_sync). The Client rebuilds the same window 1:1 with
real widgets — read-only, nothing is computed there. Only items that changed
since the last message are sent.
"""

from __future__ import annotations

import hashlib

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, Qt
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractSlider, QAbstractSpinBox, QCheckBox, QComboBox, QGroupBox, QLabel, QLineEdit,
    QPlainTextEdit, QProgressBar, QRadioButton, QTabBar, QTableWidget, QTextEdit, QToolButton, QWidget,
)

from app.network.figure_sync import serialize_figure

MAX_TABLE_ROWS = 400
MAX_TEXT = 8000


def _canvas_type():
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

    return FigureCanvasQTAgg


def hook_dirty(window, hooked: dict, dirty: set) -> None:
    """Mark figures dirty whenever they redraw (other widgets are compared by value)."""
    for canvas in window.findChildren(_canvas_type()):
        key = id(canvas)
        if key not in hooked:
            hooked[key] = canvas.mpl_connect("draw_event", lambda _event, key=key: dirty.add(key))
            dirty.add(key)


def _png(pixmap) -> np.ndarray | None:
    if pixmap is None or pixmap.isNull():
        return None
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    return np.frombuffer(bytes(data), dtype=np.uint8)


def _table(table: QTableWidget) -> dict:
    rows = min(table.rowCount(), MAX_TABLE_ROWS)
    columns = table.columnCount()
    headers = [(table.horizontalHeaderItem(c).text() if table.horizontalHeaderItem(c) else str(c + 1))
               for c in range(columns)]
    cells = []
    for r in range(rows):
        row = []
        for c in range(columns):
            item = table.item(r, c)
            if item is not None:
                row.append(item.text())
                continue
            widget = table.cellWidget(r, c)
            text = ""
            if isinstance(widget, QComboBox):
                text = widget.currentText()
            elif isinstance(widget, QAbstractButton):
                text = ("☑ " if widget.isChecked() else "☐ ") + widget.text() if widget.isCheckable() else widget.text()
            elif isinstance(widget, (QLineEdit, QAbstractSpinBox)):
                text = widget.text()
            row.append(text)
        cells.append(row)
    return {"headers": headers, "cells": cells, "selected": table.currentRow(),
            "vscroll": table.verticalScrollBar().value(), "hscroll": table.horizontalScrollBar().value()}


def _describe(widget: QWidget) -> dict | None:
    """Kind and value of one leaf control; None for containers."""
    if isinstance(widget, _canvas_type()):
        return {"kind": "figure"}
    if isinstance(widget, QTableWidget):
        return {"kind": "table"}
    if isinstance(widget, QTabBar):
        return {"kind": "tabbar", "tabs": [widget.tabText(i) for i in range(widget.count())],
                "current": widget.currentIndex()}
    if isinstance(widget, QComboBox):
        return {"kind": "combo", "text": widget.currentText()}
    if isinstance(widget, QAbstractSpinBox):
        return {"kind": "spin", "text": widget.text()}
    if isinstance(widget, (QCheckBox, QRadioButton)):
        return {"kind": "check" if isinstance(widget, QCheckBox) else "radio", "text": widget.text(),
                "checked": widget.isChecked()}
    if isinstance(widget, QAbstractButton):
        icon = widget.icon()
        size = widget.iconSize()
        image = _png(icon.pixmap(size)) if not icon.isNull() and size.width() > 0 else None
        return {"kind": "tool" if isinstance(widget, QToolButton) else "button", "text": widget.text(),
                "checkable": widget.isCheckable(), "checked": widget.isChecked(), "icon": image,
                "icon_size": [size.width(), size.height()], "enabled": widget.isEnabled()}
    if isinstance(widget, QLineEdit):
        return {"kind": "line", "text": widget.text(), "placeholder": widget.placeholderText()}
    if isinstance(widget, (QPlainTextEdit, QTextEdit)):
        return {"kind": "text", "text": widget.toPlainText()[:MAX_TEXT]}
    if isinstance(widget, QProgressBar):
        return {"kind": "progress", "min": widget.minimum(), "max": widget.maximum(), "value": widget.value(),
                "format": widget.text()}
    if isinstance(widget, QAbstractSlider) and not type(widget).__name__.endswith("ScrollBar"):
        return {"kind": "slider", "min": widget.minimum(), "max": widget.maximum(), "value": widget.value(),
                "orientation": int(widget.orientation().value)}
    if isinstance(widget, QLabel):
        pixmap = widget.pixmap()
        return {"kind": "label", "text": widget.text(), "wrap": widget.wordWrap(),
                "align": int(widget.alignment().value), "image": _png(pixmap) if pixmap is not None else None,
                "style": widget.styleSheet()[:200]}
    return None


def _visible_rect(widget: QWidget, root: QWidget) -> tuple[QRect, QRect] | None:
    """(full rect, visible part) of ``widget`` in ``root`` pixels; None if hidden."""
    region = widget.visibleRegion()
    if region.isEmpty():
        return None
    top_left = widget.mapTo(root, widget.rect().topLeft())
    full = QRect(top_left, widget.size())
    visible = region.boundingRect()
    visible.translate(top_left)
    return full, visible


def snapshot(window, dirty_figures: set | None, known: set | None = None) -> tuple[dict, dict]:
    """(layout, content). Figure data only for ``dirty_figures`` and figures not
    in ``known`` (e.g. just revealed by a tab switch); None = all."""
    root = window.centralWidget() or window
    items, content = [], {}
    canvas_type = _canvas_type()

    def walk(parent: QWidget) -> None:
        # children(), not findChildren(QWidget, ""): Qt 6 treats "" as "name must be empty"
        # and skips internal parts such as scroll-area viewports and tab stacks.
        for child in [c for c in parent.children() if isinstance(c, QWidget)]:
            if not child.isVisible() or child.isWindow():
                continue
            description = _describe(child)
            if description is None:
                if isinstance(child, QGroupBox):
                    rects = _visible_rect(child, root)
                    if rects is not None:
                        key = str(id(child))
                        items.append({"id": key, "kind": "group", "rect": _r(rects[0]), "clip": _r(rects[1]),
                                      "title": child.title()})
                walk(child)
                continue
            rects = _visible_rect(child, root)
            if rects is None:
                continue
            key = str(id(child))
            entry = {"id": key, "kind": description.pop("kind"), "rect": _r(rects[0]), "clip": _r(rects[1])}
            if entry["kind"] == "figure":
                if dirty_figures is None or id(child) in dirty_figures or key not in (known or set()):
                    content[key] = serialize_figure(child.figure)
            elif entry["kind"] == "table":
                content[key] = _table(child)
            else:
                content[key] = description
            items.append(entry)

    walk(root)
    return {"title": window.windowTitle(), "size": [root.width(), root.height()], "items": items}, content


def _r(rect: QRect) -> list[int]:
    return [rect.x(), rect.y(), rect.width(), rect.height()]


def signature(value) -> str:
    """Stable digest of an item's content (arrays included)."""
    hasher = hashlib.sha1()

    def feed(item):
        if isinstance(item, np.ndarray):
            hasher.update(item.tobytes())
        elif isinstance(item, dict):
            for key in sorted(item):
                hasher.update(str(key).encode())
                feed(item[key])
        elif isinstance(item, (list, tuple)):
            for entry in item:
                feed(entry)
        else:
            hasher.update(repr(item).encode("utf-8", "replace"))

    feed(value)
    return hasher.hexdigest()
