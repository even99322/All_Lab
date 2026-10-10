"""Network Workspace panel (Host / Client) and the Browser's top status bar."""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from app.network.host import DEFAULT_PORT
from app.network.mirror_chrome import LEVEL_COLORS
from app.network.workspace import workspace


def windows_firewall_notice_needed(store, platform: str | None = None) -> bool:
    """Windows asks once whether LabLogViewer may accept connections; explain it first."""
    import sys

    return (platform or sys.platform) == "win32" and not store.network_flag("firewall_notice_shown")


def ensure_user_name(parent) -> bool:
    """First use: ask for this computer's name (any language)."""
    space = workspace()
    if space.user_name():
        return True
    name, ok = QInputDialog.getText(parent, space.text("net.name_title"), space.text("net.name_prompt"))
    if not ok or not name.strip():
        return False
    space.store().set_network_user_name(name)
    return True


class NetworkPanel(QDialog):
    discovered = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.space = workspace()
        text = self.space.text
        self.setWindowTitle(text("net.title"))
        self.setProperty("networkAllowed", True)                 # usable while connected as a Client
        self.setModal(False)
        self.resize(680, 640)
        layout = QVBoxLayout(self)
        name_row = QHBoxLayout()
        self.name_label = QLabel()
        rename = QPushButton(text("net.rename"))
        rename.clicked.connect(self._rename)
        name_row.addWidget(self.name_label, 1)
        name_row.addWidget(rename)
        layout.addLayout(name_row)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._host_page(), text("net.host"))
        self.tabs.addTab(self._client_page(), text("net.client"))
        layout.addWidget(self.tabs, 1)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.space.changed.connect(self.refresh)
        self.space.notice.connect(self.notice.setText)
        self.space.copy_received.connect(self._copy_received)
        self.discovered.connect(self._show_hosts)
        self._hosts: list[dict] = []
        self.refresh()
        QTimer.singleShot(0, self.search)

    # Host --------------------------------------------------------------------------------
    def _host_page(self) -> QWidget:
        text = self.space.text
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        # Values use the full width (macOS keeps fields at their hint size, which wrapped them).
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.session_edit = QLineEdit(self.space.store().network_session_name())
        self.session_edit.setPlaceholderText(text("net.session_placeholder"))
        form.addRow(text("net.session"), self.session_edit)
        self.host_status = QLabel()
        form.addRow(text("net.status"), self.host_status)
        self.join_code = QLabel()
        self.join_code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.join_code.setStyleSheet("font-size: 20px; font-weight: 700; letter-spacing: 3px;")
        form.addRow(text("net.join_code"), self.join_code)
        self.sharing = QLabel()
        self.sharing.setWordWrap(True)
        form.addRow(text("net.sharing"), self.sharing)
        layout.addLayout(form)
        group = QGroupBox(text("net.clients"))
        box = QVBoxLayout(group)
        self.client_list = QListWidget()
        box.addWidget(self.client_list)
        self.send_copy = QPushButton(text("net.send_copy"))
        self.send_copy.setToolTip(text("net.send_copy_tip"))
        self.send_copy.clicked.connect(self._send_copy)
        box.addWidget(self.send_copy)
        layout.addWidget(group, 1)
        row = QHBoxLayout()
        self.start_host = QPushButton(text("net.start_hosting"))
        self.stop_host = QPushButton(text("net.stop_hosting"))
        self.start_host.clicked.connect(self._start_hosting)
        self.stop_host.clicked.connect(self.space.stop_hosting)
        row.addStretch(1)
        row.addWidget(self.start_host)
        row.addWidget(self.stop_host)
        layout.addLayout(row)
        return page

    def _start_hosting(self) -> None:
        if not ensure_user_name(self):
            return
        session = self.session_edit.text().strip() or f"{self.space.user_name()}"
        if windows_firewall_notice_needed(self.space.store()):
            QMessageBox.information(self, self.space.text("net.title"), self.space.text("net.firewall_notice"))
            self.space.store().set_network_flag("firewall_notice_shown", True)
        self.space.store().set_network_session_name(session)
        try:
            self.space.start_hosting(session)
        except (OSError, RuntimeError) as error:
            QMessageBox.warning(self, self.space.text("net.title"), self.space.text("net.cannot_host").format(reason=error))

    def _send_copy(self) -> None:
        item = self.client_list.currentItem()
        publisher = self.space.publisher
        if item is None or publisher is None or publisher.measurement_id is None:
            return
        answer = QMessageBox.question(self, self.space.text("net.send_copy"),
                                      self.space.text("net.send_copy_confirm").format(name=item.text()))
        if answer == QMessageBox.StandardButton.Yes:
            self.space.host.send_copy(item.data(Qt.ItemDataRole.UserRole), publisher.measurement_id)

    # Client --------------------------------------------------------------------------------
    def _client_page(self) -> QWidget:
        text = self.space.text
        page = QWidget()
        layout = QVBoxLayout(page)
        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(text("net.search_placeholder"))
        self.search_edit.textChanged.connect(lambda _text: self._show_hosts(self._hosts))
        refresh = QPushButton(text("net.search"))
        refresh.clicked.connect(self.search)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(refresh)
        layout.addLayout(search_row)
        self.host_list = QListWidget()
        layout.addWidget(self.host_list, 1)
        address_row = QHBoxLayout()
        self.address_edit = QLineEdit()
        self.address_edit.setObjectName("networkAddress")
        self.address_edit.setPlaceholderText(text("net.address_placeholder"))
        self.address_edit.setToolTip(text("net.address_tip"))
        address_row.addWidget(QLabel(text("net.address")))
        address_row.addWidget(self.address_edit, 1)
        layout.addLayout(address_row)
        form = QFormLayout()
        # Values use the full width (macOS keeps fields at their hint size, which wrapped them).
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.code_edit = QLineEdit()
        self.code_edit.setMaxLength(6)
        self.code_edit.setPlaceholderText("000000")
        form.addRow(text("net.join_code"), self.code_edit)
        self.client_status = QLabel()
        self.client_status.setWordWrap(True)
        form.addRow(text("net.status"), self.client_status)
        self.host_info = QLabel()
        self.host_info.setWordWrap(True)
        form.addRow(text("net.host_info"), self.host_info)
        layout.addLayout(form)
        folders_row = QHBoxLayout()
        self.folders_label = QLabel()
        self.folders_label.setWordWrap(True)
        folders = QPushButton(text("net.shared_folders"))
        folders.setToolTip(text("net.shared_folders_tip"))
        folders.clicked.connect(self._edit_folders)
        folders_row.addWidget(self.folders_label, 1)
        folders_row.addWidget(folders)
        layout.addLayout(folders_row)
        row = QHBoxLayout()
        self.connect_button = QPushButton(text("net.connect"))
        self.disconnect_button = QPushButton(text("net.disconnect"))
        self.connect_button.clicked.connect(self._connect)
        self.disconnect_button.clicked.connect(self.space.disconnect)
        row.addStretch(1)
        row.addWidget(self.connect_button)
        row.addWidget(self.disconnect_button)
        layout.addLayout(row)
        return page

    def search(self) -> None:
        from app.network.discovery import discover

        self.client_status.setText(self.space.text("net.searching"))
        port = int(os.environ.get("LABLOGVIEWER_NET_DISCOVERY", "47810"))
        threading.Thread(target=lambda: self.discovered.emit(discover(1.5, port=port)), daemon=True).start()

    def _show_hosts(self, hosts: list) -> None:
        from app.network.discovery import search

        self._hosts = hosts
        self.host_list.clear()
        for host in search(hosts, self.search_edit.text()):
            label = self.space.text("net.host_row").format(
                host=host.get("host_name", "?"), session=host.get("session_name", ""),
                clients=host.get("clients", 0), max=host.get("max_clients", 5))
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, host)
            self.host_list.addItem(item)
        if self.space.role == "offline":
            self.client_status.setText(self.space.text("net.found").format(count=self.host_list.count())
                                       if self.host_list.count() else self.space.text("net.none_found"))

    def _target(self) -> tuple[str, int] | None:
        """A typed address wins over the list (for networks that block search)."""
        typed = self.address_edit.text().strip()
        if typed:
            host, _, port = typed.partition(":")
            try:
                return host.strip(), int(port) if port.strip() else DEFAULT_PORT
            except ValueError:
                return None
        item = self.host_list.currentItem()
        if item is None:
            return None
        host = item.data(Qt.ItemDataRole.UserRole)
        return str(host["address"]), int(host["port"])

    def _connect(self) -> None:
        target = self._target()
        if target is None:
            QMessageBox.information(self, self.space.text("net.title"), self.space.text("net.pick_host"))
            return
        if not ensure_user_name(self):
            return
        try:
            self.space.connect_to(target[0], target[1], self.code_edit.text().strip())
        except RuntimeError as error:
            QMessageBox.warning(self, self.space.text("net.title"), str(error))

    def _edit_folders(self) -> None:
        folders = self.space.shared_folders()
        folder = QFileDialog.getExistingDirectory(self, self.space.text("net.shared_folders"))
        if folder and folder not in folders:
            folders.append(folder)
            self.space.store().set_network_shared_folders(folders)
        self.refresh()

    def _copy_received(self, path: str) -> None:
        QMessageBox.information(self, self.space.text("net.title"), self.space.text("net.copy_received").format(path=path))

    def _rename(self) -> None:
        name, ok = QInputDialog.getText(self, self.space.text("net.name_title"), self.space.text("net.name_prompt"),
                                        text=self.space.user_name())
        if ok and name.strip():
            self.space.store().set_network_user_name(name)
            self.refresh()

    # state ------------------------------------------------------------------------------------
    def refresh(self) -> None:
        space, text = self.space, self.space.text
        self.name_label.setText(text("net.this_computer").format(name=space.user_name() or "—"))
        hosting = space.role == "host"
        self.start_host.setEnabled(space.role == "offline")
        self.stop_host.setEnabled(hosting)
        self.session_edit.setEnabled(space.role == "offline")
        self.host_status.setText(text("net.hosting").format(count=len(space.host_clients)) if hosting
                                 else text("net.not_hosting"))
        self.join_code.setText(space.host.join_code if hosting else "—")
        publisher = space.publisher
        viewer = getattr(publisher, "viewer", None)
        experiment = getattr(viewer, "experiment", None)
        self.sharing.setText(experiment.log_name if hosting and experiment is not None else
                             (text("net.open_viewer") if hosting else "—"))
        self.client_list.clear()
        for client_id, info in space.host_clients.items():
            quality = info.get("quality") or {}
            level = quality.get("level", "unknown")
            item = QListWidgetItem(f"● {info.get('name', '')}   {info.get('address', '')}   "
                                   f"{text('net.level_' + level)}")
            item.setForeground(QColor(LEVEL_COLORS.get(level, "#9e9e9e")))
            item.setData(Qt.ItemDataRole.UserRole, client_id)
            self.client_list.addItem(item)
        self.send_copy.setEnabled(hosting and bool(space.host_clients))
        client = space.role == "client"
        self.connect_button.setEnabled(space.role == "offline")
        self.disconnect_button.setEnabled(client)
        self.tabs.setTabEnabled(0, space.role != "client")
        self.tabs.setTabEnabled(1, space.role != "host")
        if client:
            state = text("net.state_" + space.client_state) if space.client_state else ""
            quality = space.client_quality
            self.client_status.setText(f"{state}  ·  {text('net.level_' + quality.level)} {quality.text()}")
            info = space.client.host_info if space.client else {}
            self.host_info.setText(text("net.host_row").format(
                host=info.get("host_name", "?"), session=info.get("session_name", ""),
                clients=info.get("clients", "?"), max=info.get("max_clients", 5)))
        elif space.last_reason:
            reason_key = "net.reason_" + space.last_reason
            reason = text(reason_key)
            self.client_status.setText(text("net.state_disconnected") + " · " + (reason if reason != reason_key else space.last_reason))
            self.host_info.setText("—")
        folders = space.shared_folders()
        self.folders_label.setText(text("net.shared_folders_list").format(
            folders=", ".join(folders) if folders else text("net.none")))


_panel: "NetworkPanel | None" = None


def open_network_panel(parent=None) -> "NetworkPanel":
    """The one Network Workspace window of this application."""
    global _panel
    from shiboken6 import isValid

    if _panel is None or not isValid(_panel):
        _panel = NetworkPanel(None)
    _panel.show()
    _panel.raise_()
    _panel.activateWindow()
    return _panel


_relayed: dict = {}
_relay_bars: list = []


def set_relayed_status(status: dict) -> None:
    """3D process: show the main process's network status (it owns the session)."""
    _relayed.clear()
    _relayed.update(status)
    for bar in list(_relay_bars):
        try:
            bar.refresh()
        except RuntimeError:
            _relay_bars.remove(bar)


def status_summary() -> dict:
    space, text = workspace(), workspace().text
    if space.role == "host":
        return {"text": text("net.hosting").format(count=len(space.host_clients)), "level": "good"}
    if space.role == "client":
        info = space.client.host_info if space.client else {}
        quality = space.client_quality
        return {"text": text("net.following").format(host=info.get("host_name", ""), session=info.get("session_name", ""))
                + f"  ·  {text('net.level_' + quality.level)} {quality.text()}", "level": quality.level}
    return {"text": text("net.offline"), "level": "unknown"}


class NetworkStatusBar(QWidget):
    """Slim bar at the very top of every window: status + quality; click opens the panel.

    ``opener`` replaces the local panel (the 3D process asks the main process).
    """

    def __init__(self, parent=None, opener=None):
        super().__init__(parent)
        self._opener = opener
        if opener is not None:
            _relay_bars.append(self)
        self.space = workspace()
        self.setObjectName("networkStatusBar")
        self.setProperty("networkAllowed", True)
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 0, 6, 0)
        self.button = QToolButton()
        self.button.setText(self.space.text("net.title"))
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.button.clicked.connect(self.open_panel)
        self.dot = QLabel("●")
        self.status = QLabel()
        row.addWidget(self.button)
        row.addWidget(self.dot)
        row.addWidget(self.status)
        row.addStretch(1)
        self.panel: NetworkPanel | None = None
        self.space.changed.connect(self.refresh)
        self.refresh()

    def open_panel(self) -> None:
        if self._opener is not None:
            self._opener()
            return
        self.panel = open_network_panel(self.window())

    def refresh(self) -> None:
        summary = dict(_relayed) if self._opener is not None and _relayed else status_summary()
        self.status.setText(summary.get("text", ""))
        self.dot.setStyleSheet(f"color: {LEVEL_COLORS.get(summary.get('level'), '#9e9e9e')};")


def install_network_bar(window, opener=None) -> NetworkStatusBar:
    """Put the Network Workspace bar in the first row of a QMainWindow."""
    from PySide6.QtWidgets import QToolBar

    bar = QToolBar(window)
    bar.setObjectName("networkStatusToolbar")
    bar.setMovable(False)
    bar.setFloatable(False)
    status = NetworkStatusBar(bar, opener=opener)
    bar.addWidget(status)
    first = next((tb for tb in window.findChildren(QToolBar) if tb is not bar and tb.parent() is window), None)
    if first is not None:
        window.insertToolBar(first, bar)
        window.insertToolBarBreak(first)
    else:
        window.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
    window.network_bar = status
    return status
