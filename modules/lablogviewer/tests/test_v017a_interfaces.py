from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtWidgets import QApplication, QMessageBox, QTreeWidgetItem

from app.core.database_scanner import DatabaseScanResult, LogEntry
from app.core.star_store import StarStore
from app.gui import browser_window as browser_module
from app.gui.browser_window import BrowserWindow
from app.interfaces import InterfaceContext, InterfaceResult, InterfaceStatus
from app.interfaces.measurement.interface import MeasurementInterface
from app.interfaces.online_paper_library import (
    ONLINE_PAPER_LIBRARY_URL,
    open_online_paper_library,
)
from app.interfaces.time_domain.interface import TimeDomainInterface
from app.localization import LocalizationManager
from app.settings import SettingsStore


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def browser(qapp, tmp_path, monkeypatch):
    localizer = LocalizationManager(SettingsStore(tmp_path / "settings.json"))
    monkeypatch.setattr(browser_module, "get_localization_manager", lambda: localizer)
    window = BrowserWindow(star_store=StarStore(tmp_path / "state" / "stars.json"))
    yield window, localizer
    window.close()
    window.deleteLater()
    qapp.processEvents()


def test_interface_contract_accepts_absent_browser_context_and_stubs_are_safe():
    context = InterfaceContext()
    assert context.database_path is None
    assert context.selected_log_id is None
    assert context.selected_channel is None
    assert context.selected_dimensions is None
    assert context.metadata is None
    assert context.instrument_metadata is None

    for interface in (MeasurementInterface(), TimeDomainInterface()):
        assert interface.is_available() is False
        result = interface.launch(context)
        assert isinstance(result, InterfaceResult)
        assert result.status is InterfaceStatus.UNAVAILABLE


def test_database_browser_menu_order_labels_and_language_switch(browser):
    window, localizer = browser
    labels = [action.text().replace("&", "") for action in window.menuBar().actions()]
    assert labels.index("Interfaces") == labels.index("Processing") + 1
    # v0.19A: Network Workspace sits between Interfaces and Settings.
    assert labels.index("Network Workspace") == labels.index("Interfaces") + 1
    assert labels.index("Settings") == labels.index("Network Workspace") + 1
    assert [action.text() for action in window.interfaces_menu.actions()] == [
        "Measurement", "Time Domain", "Online Paper Library",
    ]

    localizer.set_language("zh_TW")
    labels = [action.text().replace("&", "") for action in window.menuBar().actions()]
    assert "介面" in labels
    assert labels.index("介面") == labels.index("資料處理(P)") + 1 or labels.index("介面") == labels.index("資料處理") + 1
    assert [action.text() for action in window.interfaces_menu.actions()] == [
        "量測", "時域", "線上論文庫",
    ]

    localizer.set_language("en")
    assert window.interfaces_menu.title() == "Interfaces"
    assert window.time_domain_action.text() == "Time Domain"


def test_interfaces_menu_can_open_and_actions_are_reachable(browser, qapp, monkeypatch):
    window, _localizer = browser
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args))
    window.show()
    qapp.processEvents()
    window.interfaces_menu.popup(window.mapToGlobal(QPoint(20, 20)))
    qapp.processEvents()
    assert window.interfaces_menu.isVisible()
    window.measurement_action.trigger()
    qapp.processEvents()
    assert messages[-1][2] == "Measurement interface is not configured yet."
    window.interfaces_menu.close()
    window.close()


def test_browser_passes_only_known_context_and_shows_localized_placeholders(browser, tmp_path, monkeypatch):
    window, localizer = browser
    database = tmp_path / "database"
    database.mkdir()
    source = database / "nested" / "run.hdf5"
    window.scanner_root = str(database)
    window.scan_result = DatabaseScanResult(str(database), str(database.resolve()), [])

    entry = LogEntry(
        absolute_path=str(source), relative_path="nested/run.hdf5", file_name="run.hdf5",
        log_name="Run 1", status="ok", error_message=None, size_bytes=12, mtime=1.0,
        sweep_dimension="2D · 17 × 51", metadata_complete=True,
    )
    item = QTreeWidgetItem(["run.hdf5"])
    item.setData(0, Qt.ItemDataRole.UserRole, entry)
    window.data_list.addTopLevelItem(item)
    window.data_list.setCurrentItem(item)
    context = window._build_interface_context()
    assert context.database_path == database
    assert context.database_name == "database"
    assert context.database_identity == str(database.resolve())
    assert context.selected_folder == source.parent
    assert context.selected_log_name == "Run 1"
    assert context.selected_log_path == source
    assert context.selected_log_id is None
    assert context.selected_channel is None
    assert context.selected_dimensions is None
    assert context.sweep_information == {"summary": "2D · 17 × 51"}
    assert context.metadata is None
    assert context.instrument_metadata is None

    observed = []

    class Recorder:
        def launch(self, received):
            observed.append(received)
            return InterfaceResult.unavailable()

    window.measurement_interface = Recorder()
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args))
    window.measurement_action.trigger()
    assert observed == [context]
    assert messages[-1][2] == "Measurement interface is not configured yet."

    localizer.set_language("zh_TW")
    window.time_domain_action.trigger()
    assert messages[-1][2] == "Time Domain 介面尚未設定。"


def test_paper_library_action_uses_mocked_external_launcher_only(browser, monkeypatch):
    window, _localizer = browser
    assert ONLINE_PAPER_LIBRARY_URL == "http://100.114.33.20:8080/"
    launch_calls = []
    opened = []

    def fake_open_url(url: QUrl) -> bool:
        opened.append(url.toString())
        return True

    def mocked_browser_launcher():
        launch_calls.append(True)
        return open_online_paper_library(fake_open_url)

    monkeypatch.setattr(browser_module, "open_online_paper_library", mocked_browser_launcher)
    window.online_paper_library_action.trigger()
    assert launch_calls == [True]
    assert opened == ["http://100.114.33.20:8080/"]


def test_paper_library_failed_open_shows_warning_without_network(browser, monkeypatch):
    window, _localizer = browser
    messages = []
    monkeypatch.setattr(browser_module, "open_online_paper_library", lambda: False)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args))
    window.online_paper_library_action.trigger()
    assert messages[-1][2] == "The Online Paper Library could not be opened in the default browser."
