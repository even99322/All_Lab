"""v0.18D: one movable data folder for everything kept outside HDF5."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fresh user home; data_location is re-resolved inside it."""
    from app.core import data_location

    user = tmp_path / "user"
    (user / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("APPDATA", str(user / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(user / "AppData" / "Local"))
    monkeypatch.setattr(data_location, "legacy_sources", _without_qt(data_location.legacy_sources))
    data_location._reset_for_tests()
    yield user
    data_location._reset_for_tests()


def _without_qt(original):
    # The running QApplication's AppData path is the developer's real folder;
    # tests only look at locations inside the fake home.
    def sources():
        from pathlib import Path

        home = Path.home()
        return [(folder, path) for folder, path in original() if path.is_relative_to(home)]
    return sources


def test_default_location_per_platform(home, monkeypatch):
    from app.core import data_location

    assert data_location.default_data_root() == home / "Documents" / "LabLogViewerData"
    monkeypatch.setattr(data_location, "_is_windows", lambda: True)
    assert data_location.default_data_root() == home / "LabLogViewerData"


def test_every_store_lives_in_the_data_folder(home):
    from app.core.external_state import default_state_path
    from app.analysis.yig_fitting.core import paths

    root = home / "Documents" / "LabLogViewerData"
    assert default_state_path("stars.json") == root / "state" / "stars.json"
    original = paths._CONFIG
    try:
        paths._CONFIG = None
        assert paths.config_dir() == str(root / "fitting")
    finally:
        paths._CONFIG = original
    assert (root / "README.txt").is_file() and (root / "lablogviewer_data.json").is_file()


def test_old_version_data_is_copied_and_originals_kept(home):
    from app.core import data_location

    old_state = home / ".lablogviewer"
    (old_state / "drag-share").mkdir(parents=True)
    (old_state / "stars.json").write_text('{"starred": 1}', encoding="utf-8")
    (old_state / "tags.json.pre-taxonomy-v4.bak").write_text("backup", encoding="utf-8")
    (old_state / "drag-share" / "a.png").write_bytes(b"png")
    old_fitting = home / "Library" / "Application Support" / "LabLogViewer" / "fitting"
    (old_fitting / "sessions").mkdir(parents=True)
    (old_fitting / "matplotlib").mkdir()
    (old_fitting / "sessions" / "abc.json").write_text("{}", encoding="utf-8")
    (old_fitting / "formula_library.json").write_text("[]", encoding="utf-8")
    (old_fitting / "matplotlib" / "fontlist.json").write_text("{}", encoding="utf-8")

    root = data_location.data_root()
    assert (root / "state" / "stars.json").read_text(encoding="utf-8") == '{"starred": 1}'
    assert (root / "state" / "tags.json.pre-taxonomy-v4.bak").is_file()
    assert (root / "state" / "drag-share" / "a.png").is_file()
    assert (root / "fitting" / "sessions" / "abc.json").is_file()
    assert (root / "fitting" / "formula_library.json").is_file()
    assert not (root / "fitting" / "matplotlib").exists()          # cache is not history
    assert (old_state / "stars.json").is_file() and (old_fitting / "formula_library.json").is_file()
    record = json.loads((root / "lablogviewer_data.json").read_text(encoding="utf-8"))["legacy_migration"]
    assert record["completed"] and "state/stars.json" in record["copied"]

    # Runs once: a later old-version write is not copied over newer data.
    (root / "state" / "stars.json").write_text('{"starred": 2}', encoding="utf-8")
    data_location._reset_for_tests()
    data_location.data_root()
    assert (root / "state" / "stars.json").read_text(encoding="utf-8") == '{"starred": 2}'


def test_migration_never_overwrites_existing_files(home):
    from app.core import data_location

    root = home / "Documents" / "LabLogViewerData"
    (root / "state").mkdir(parents=True)
    (root / "state" / "tags.json").write_text("new", encoding="utf-8")
    (home / ".lablogviewer").mkdir()
    (home / ".lablogviewer" / "tags.json").write_text("old", encoding="utf-8")
    data_location.data_root()
    assert (root / "state" / "tags.json").read_text(encoding="utf-8") == "new"


def test_move_happens_at_next_launch_and_verifies(home, tmp_path):
    from app.core import data_location

    root = data_location.data_root()
    (root / "state").mkdir(exist_ok=True)
    (root / "state" / "comments.json").write_text("{}", encoding="utf-8")
    target = data_location.normalize_choice(tmp_path / "Drive")
    (tmp_path / "Drive").mkdir()
    assert target.name == "LabLogViewerData"
    assert data_location.request_move(target) == "scheduled"
    assert data_location.data_root() == root                      # unchanged until restart
    assert data_location.pending_move() == target.resolve()

    data_location._reset_for_tests()                              # "next launch"
    assert data_location.data_root() == target.resolve()
    assert (target / "state" / "comments.json").is_file()
    assert not root.exists()                                      # moved, not duplicated
    assert data_location.pending_move() is None


def test_move_to_folder_with_data_asks_and_can_switch(home, tmp_path):
    from app.core import data_location

    data_location.data_root()
    other = tmp_path / "Shared" / "LabLogViewerData"
    (other / "state").mkdir(parents=True)
    (other / "state" / "stars.json").write_text("{}", encoding="utf-8")
    assert data_location.request_move(other) == "occupied"
    data_location.use_existing(other)
    data_location._reset_for_tests()
    assert data_location.data_root() == other.resolve()


def test_move_into_itself_is_rejected(home):
    from app.core import data_location

    root = data_location.data_root()
    with pytest.raises(ValueError):
        data_location.request_move(root / "inner" / "LabLogViewerData")


def test_missing_drive_falls_back_without_creating_a_fake_folder(home, tmp_path):
    from app.core import data_location

    unplugged = tmp_path / "Volumes" / "USB" / "LabLogViewerData"
    data_location._write_pointer({"path": str(unplugged)})
    assert data_location.data_root() == home / "Documents" / "LabLogViewerData"
    assert not unplugged.parent.exists()
    assert data_location.startup_notice()


def test_settings_general_page_shows_data_folder(home):
    import os
    from PySide6.QtWidgets import QApplication
    from app.settings.dialog import SettingsDialog

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QApplication.instance() or QApplication([])
    dialog = SettingsDialog()
    try:
        assert "LabLogViewerData" in dialog.data_folder_path.text()
        assert not dialog.data_folder_default.isEnabled()          # already the default
    finally:
        dialog.close()


# ---- 3D window tools and Settings in every window ---------------------------

def test_settings_has_a_separate_3d_section(home):
    import os
    from PySide6.QtWidgets import QApplication
    from app.settings.dialog import SettingsDialog

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QApplication.instance() or QApplication([])
    dialog = SettingsDialog()
    try:
        dialog.show_page("three_d")
        page = dialog.pages.currentWidget()
        assert dialog.three_d_profile_combo.parent() is page
        assert dialog.three_d_export_combo.parent() is page
        assert [dialog.three_d_export_combo.itemData(i) for i in range(2)] == ["publication", "screen"]
    finally:
        dialog.close()


def test_every_window_has_the_shared_settings_menu(home):
    """v0.19A: "Network Workspace" and "Settings" open their windows directly, and
    every window shows the Network Workspace bar in its first row."""
    import os
    from PySide6.QtWidgets import QApplication
    from app.settings import dialog as settings_dialog
    from app.gui import network_panel
    from app.gui.main_window import MainWindow
    from app.analysis.yig_fitting.ui.main_window import MainWindow as FittingMainWindow

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    viewer = MainWindow()
    fitting = FittingMainWindow()
    try:
        for window in (viewer, fitting):
            bar = window.menuBar().actions()
            assert bar.index(window.network_menu.menuAction()) + 1 == bar.index(window.settings_menu.menuAction())
            assert [a.text() for a in window.settings_menu.actions()] == ["Settings..."]
            window.settings_menu.aboutToShow.emit()                 # clicking the menu title
            app.processEvents()
            shared = settings_dialog._shared_dialog
            assert shared is not None and shared.isVisible()
            window.network_menu.aboutToShow.emit()
            app.processEvents()
            assert network_panel._panel is not None and network_panel._panel.isVisible()
            assert window.network_bar.status.text()                  # Offline / Hosting / Following
    finally:
        if settings_dialog._shared_dialog is not None:
            settings_dialog._shared_dialog.close()
        if network_panel._panel is not None:
            network_panel._panel.close()
        fitting.close()
        viewer.close()


def test_three_d_window_has_open_pointer_and_drag_share(home, monkeypatch):
    import os
    from PySide6.QtWidgets import QApplication, QWidget
    from app.gui.main_window import MainWindow
    from app.gui.three_d_window import ThreeDAnalysisWindow

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QApplication.instance() or QApplication([])
    viewer = MainWindow()
    window = ThreeDAnalysisWindow(viewer, QWidget(), QWidget())
    try:
        assert window.interaction_controls.btn_pointer.isChecked()
        assert window.interaction_controls.scope_combo.isHidden()
        window.interaction_controls.btn_share.click()
        assert viewer._three_d_share_mode is True
        assert not viewer.plot_interaction_controls.share_mode          # Viewer plots unaffected
        window.interaction_controls.btn_pointer.click()
        assert viewer._three_d_share_mode is False

        calls = []
        monkeypatch.setattr(viewer, "open_file_dialog", lambda parent=None: calls.append(parent))
        window.open_button.click()
        assert calls == [window]
        assert len(window.settings_menu.actions()) == 1 and window.network_bar is not None
    finally:
        window.close()
        viewer.close()
