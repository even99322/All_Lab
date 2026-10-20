"""v1.0.9: a new version pops up as a notification in the bottom-right corner of the screen (like a system
notification), with "Update now" (download + install from the lab hub), "What's new" and "Later".

Shown when an "update" notice is added (hub push or GitHub feed), and once at start when an update notice is
still unread. Off with LABLOGVIEWER_NO_UPDATE_TOAST=1 (tests that do not want it).
"""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, QPoint, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout

from app import __version__
from app.core import notifications

MARGIN = 16
MAX_LINES = 4

_BRIDGE: "_Bridge | None" = None


def _language(localizer) -> str:
    return "zh" if str(getattr(localizer, "language", "en")).startswith("zh") else "en"


class UpdateToast(QFrame):
    """The bottom-right card for one update notice."""

    def __init__(self, localizer, record: dict):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.localizer, self.record = localizer, record
        self.setObjectName("updateToast")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        text = localizer.text
        details = dict(record.get("details") or {})
        self.version = str(details.get("version") or record.get("params", {}).get("version", ""))
        details.setdefault("version", self.version)
        self.details = details
        dark = QGuiApplication.palette().window().color().lightness() < 128
        bg, fg, dim, edge = (("#2C303A", "#F5F5F7", "#B9BCC4", "#4A4F5C") if dark
                             else ("#FFFFFF", "#1D1D1F", "#5C5F66", "#C9CCD3"))
        self.setStyleSheet(
            f"#updateToast {{ background: {bg}; border: 1px solid {edge}; border-radius: 12px; }}"
            f"#updateToast QLabel {{ color: {fg}; background: transparent; }}"
            f"#updateToastLines {{ color: {dim}; }}"
            f"#updateToast QPushButton {{ padding: 4px 12px; }}")
        self.setFixedWidth(360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 12)
        top = QHBoxLayout()
        title = QLabel(text("toast.update_title").format(version=self.version))
        title.setObjectName("updateToastTitle")
        title.setStyleSheet("font-weight: 700; font-size: 14px;")
        top.addWidget(title, 1)
        close = QToolButton()
        close.setObjectName("updateToastClose")
        close.setText("✕")
        close.setAutoRaise(True)
        close.clicked.connect(self.close)
        top.addWidget(close)
        lay.addLayout(top)
        lines = notifications.update_lines(record, _language(localizer))
        sub = text("toast.update_body").format(current=__version__, version=self.version)
        if lines:
            shown = "".join(f"<br>• {line}" for line in lines[:MAX_LINES])
            more = f"<br>{text('toast.more').format(n=len(lines) - MAX_LINES)}" if len(lines) > MAX_LINES else ""
            sub = f"{sub}{shown}{more}"
        body = QLabel(sub)
        body.setObjectName("updateToastLines")
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(body)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.later_button = QPushButton(text("toast.later"))
        self.later_button.clicked.connect(self.close)
        self.more_button = QPushButton(text("toast.whats_new"))
        self.more_button.clicked.connect(self._whats_new)
        from app.core import hub_update

        self.installable = hub_update.can_install(details)
        self.update_button = QPushButton(text("update.now") if self.installable else text("toast.download"))
        self.update_button.setObjectName("updateToastNow")
        self.update_button.setDefault(True)
        self.update_button.clicked.connect(self._update_now)
        for b in (self.later_button, self.more_button, self.update_button):
            buttons.addWidget(b)
        lay.addLayout(buttons)

    def place(self) -> None:
        """Bottom-right corner of the screen the app is on (above the taskbar / Dock)."""
        self.adjustSize()
        window = QApplication.activeWindow() or next(iter(QApplication.topLevelWidgets()), None)
        screen = (window.screen() if window is not None else None) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(QPoint(area.right() - self.width() - MARGIN, area.bottom() - self.height() - MARGIN))

    def _whats_new(self) -> None:
        from app.gui.notification_bell import UpdatePreviewDialog

        self.close()
        dialog = UpdatePreviewDialog(self.localizer, self.record, QApplication.activeWindow())
        dialog.exec()

    def download_url(self) -> str:
        install = self.details.get("install") or {}
        return str(install.get("url") or self.details.get("url") or "")

    def _update_now(self) -> None:
        if self.installable:
            from app.gui.hub_updates import install_now

            self.hide()
            if not install_now(QApplication.activeWindow(), self.localizer, self.details):
                self.show()
                return
            self.close()
            return
        url = self.download_url()
        if not url:
            try:
                from app.core import hub_update

                url = str(hub_update.installer_for(self.details).get("url") or "")
            except Exception:  # noqa: BLE001
                url = ""
        if url:
            QDesktopServices.openUrl(QUrl(url))
        self.close()


class _Bridge(QObject):
    """notifications may be added from any thread: show the toast on the GUI thread."""

    event = Signal(str, object)

    def __init__(self, localizer):
        super().__init__(QApplication.instance())
        self.localizer = localizer
        self.toast: UpdateToast | None = None
        self.shown: set[str] = set()
        self.event.connect(self._on_event)
        self._listener = lambda event, record: self.event.emit(event, record)
        notifications.add_listener(self._listener)

    def _on_event(self, event: str, record) -> None:
        if event == "added" and isinstance(record, dict) and record.get("kind") == "update":
            self.show_for(record)

    def show_for(self, record: dict) -> UpdateToast | None:
        version = str((record.get("details") or {}).get("version") or record.get("params", {}).get("version", ""))
        if not version or version in self.shown:
            return None
        self.shown.add(version)
        if self.toast is not None:
            try:
                self.toast.close()
            except RuntimeError:
                pass
        self.toast = UpdateToast(self.localizer, record)
        self.toast.place()
        self.toast.show()
        self.toast.raise_()
        return self.toast


def pending_update() -> dict | None:
    """The newest unread "update" notice newer than this version (and not skipped)."""
    from app.core.app_update import version_key

    skipped = set(notifications.skipped_versions())
    best = None
    for record in notifications.items():
        if record.get("kind") != "update" or record.get("read"):
            continue
        version = str((record.get("details") or {}).get("version") or record.get("params", {}).get("version", ""))
        if not version or version in skipped or version_key(version) <= version_key(__version__):
            continue
        if best is None or version_key(version) > version_key(
                str((best.get("details") or {}).get("version") or best.get("params", {}).get("version", ""))):
            best = record
    return best


def install(localizer, show_pending: bool = True) -> "_Bridge | None":
    """Once per process (Browser window start)."""
    global _BRIDGE
    if os.environ.get("LABLOGVIEWER_NO_UPDATE_TOAST"):
        return None
    if _BRIDGE is None:
        _BRIDGE = _Bridge(localizer)
        if show_pending:
            record = pending_update()
            if record is not None:
                QTimer.singleShot(1500, lambda r=record: _BRIDGE is not None and _BRIDGE.show_for(r))
    return _BRIDGE
