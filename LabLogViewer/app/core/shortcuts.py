"""Single-key shortcuts of the Viewer (v2.1.6), changeable in Settings > Shortcuts.

Each action has a default key; the user's choice is kept in QSettings ("shortcuts/<action>"). An empty key turns
the shortcut off. Open Viewers rebind at once (``notifier.changed``).
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QSettings, Signal

# (action, default key) — the order is the order in Settings
ACTIONS: tuple[tuple[str, str], ...] = (
    ("reset_view", "Esc"),          # back to the full (global) view of every plot in the window
    ("point_mark", "M"),            # Point Mark on / off (click the plot to place it)
    ("plot_1d", "1"),               # Plot Mode 1D
    ("plot_2d", "2"),               # Plot Mode 2D
    ("maximize", "F"),              # Maximize Plot on / off
    ("data_points", "P"),           # show the measured samples on 1D curves
    ("auto_color", "A"),            # 2D: auto color range now
    ("hide_controls", "H"),         # hide / show the control panel
    ("phase_delay", "D"),           # phase delay (electrical delay) processing
    ("traces", "T"),                # trace manager
)
DEFAULTS = dict(ACTIONS)


def _settings() -> QSettings:
    return QSettings("CCU QEL", "LabLogViewer")


def key_for(action: str) -> str:
    value = _settings().value(f"shortcuts/{action}", None)
    return DEFAULTS.get(action, "") if value is None else str(value)


def bindings() -> dict[str, str]:
    return {action: key_for(action) for action, _default in ACTIONS}


def conflicts(table: dict[str, str] | None = None) -> dict[str, list[str]]:
    """{key: [actions]} for keys used by more than one action."""
    seen: dict[str, list[str]] = {}
    for action, key in (table or bindings()).items():
        if key:
            seen.setdefault(key.casefold(), []).append(action)
    return {k: v for k, v in seen.items() if len(v) > 1}


class _Notifier(QObject):
    changed = Signal()


notifier = _Notifier()


def set_key(action: str, key: str) -> None:
    if action not in DEFAULTS:
        raise KeyError(action)
    _settings().setValue(f"shortcuts/{action}", str(key or ""))
    notifier.changed.emit()


def reset() -> None:
    s = _settings()
    for action, _default in ACTIONS:
        s.remove(f"shortcuts/{action}")
    notifier.changed.emit()
