from __future__ import annotations

import errno
import os

import pytest
from pathlib import Path


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def theme(qapp, tmp_path):
    from app.settings.store import SettingsStore
    from app.theme import ThemeManager

    previous_palette = qapp.palette()
    manager = ThemeManager(qapp, SettingsStore(tmp_path / "settings.json"), qapp)
    manager.set_mode("light")
    yield manager
    manager.detach()
    manager.deleteLater()
    qapp.setPalette(previous_palette)
    qapp.processEvents()


def _opaque_colors(pixmap):
    image = pixmap.toImage()
    colors = set()
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() > 150:
                colors.add(color.name().upper())
    return colors


def test_every_semantic_icon_is_packaged_and_renders(qapp, theme):
    from app.icons import ICON_FILES, icon, icon_directory

    assert icon_directory() == Path(os.path.abspath("icons"))
    for name in ICON_FILES:
        rendered = icon(name)
        assert not rendered.isNull(), name
        pixmap = rendered.pixmap(32, 32)
        assert _opaque_colors(pixmap), name


def test_icon_line_color_follows_light_and_dark_theme(qapp, theme):
    from app.icons import icon
    from app.theme import DARK, LIGHT

    light = _opaque_colors(icon("folder").pixmap(48, 48))
    theme.set_mode("dark")
    dark = _opaque_colors(icon("folder").pixmap(48, 48))
    assert LIGHT.text.upper() in light
    assert DARK.text.upper() in dark
    assert LIGHT.text.upper() not in dark


def test_star_is_yellow_filled_and_unstar_is_theme_outline(qapp, theme):
    from app.icons import STAR_FILL, icon
    from app.theme import LIGHT

    filled = icon("star_filled").pixmap(64, 64).toImage()
    outline = icon("star").pixmap(64, 64).toImage()
    assert filled.pixelColor(32, 36).name().upper() == STAR_FILL.upper()
    assert outline.pixelColor(32, 36).alpha() == 0
    assert LIGHT.text.upper() in _opaque_colors(icon("star").pixmap(64, 64))


def test_drag_share_png_has_no_background_rectangle(qapp, theme):
    from app.icons import icon

    image = icon("drag_share").pixmap(48, 48).toImage()
    for x, y in ((0, 0), (47, 0), (0, 47), (47, 47)):
        assert image.pixelColor(x, y).alpha() == 0


def test_missing_asset_falls_back_without_removing_the_control(qapp, theme, tmp_path, monkeypatch):
    import app.icons as icons
    from PySide6.QtGui import QIcon

    monkeypatch.setattr(icons, "icon_directory", lambda: tmp_path)
    monkeypatch.setattr(icons, "_svg_sources", {})
    monkeypatch.setattr(icons, "_png_masks", {})
    monkeypatch.setattr(icons, "_missing", set())
    assert icons.icon("open").isNull()
    fallback = QIcon(str(icons.icon_directory() / "missing.svg"))
    assert icons.icon("save", fallback=fallback) is fallback
    assert "open" in icons.missing_icons()


def test_browser_and_viewer_share_open_reload_assets_but_not_behavior(qapp, theme, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow
    from app.gui.main_window import MainWindow

    calls = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *args, **kwargs: calls.append("browser-open") or ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *args, **kwargs: calls.append("viewer-open") or ("", "")))
    browser = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"), theme_manager=theme)
    viewer = MainWindow(theme_manager=theme)
    try:
        assert browser.open_button.icon().name() == viewer.open_action.icon().name() == "lablog-open"
        assert browser.reload_button.icon().name() == viewer.reload_action.icon().name() == "lablog-reload"
        assert browser.open_button.text() and viewer.open_action.text()
        assert browser.open_button.toolTip()

        browser.open_button.click()
        assert calls == ["browser-open"]
        viewer.open_action.trigger()
        assert calls == ["browser-open", "viewer-open"]
        assert viewer.open_action is not getattr(browser, "open_action", None)
        assert not browser.reload_button.isEnabled()
        viewer.reload_action.trigger()
        assert calls == ["browser-open", "viewer-open"]
    finally:
        viewer.close()
        browser.close()


def test_viewer_toolbar_icons_keep_text_and_mode_state(qapp, theme):
    from PySide6.QtCore import Qt

    from app.gui.main_window import MainWindow

    window = MainWindow(theme_manager=theme)
    try:
        assert window.application_toolbar.toolButtonStyle() == Qt.ToolButtonIconOnly
        for button, label in ((window.show_tool_button, "Show"), (window.export_tool_button, "Export"),
                              (window.analysis_tool_button, "Analysis"),
                              (window.maximize_plot_button, "Maximize Plot")):
            assert button.toolButtonStyle() == Qt.ToolButtonIconOnly
            assert button.toolTip() == label and button.accessibleName() == label
        window.maximize_plot_button.setChecked(True)
        assert window.maximize_plot_button.toolTip() == "Restore Layout"
        window.maximize_plot_button.setChecked(False)
        assert window.maximize_plot_button.toolTip() == "Maximize Plot"
        for action in (window.open_action, window.reload_action, window.traces_action):
            assert not action.icon().isNull() and action.text()
            assert not action.isCheckable()
        assert window.show_tool_button.icon().name() == "lablog-show_trace"
        assert window.export_tool_button.icon().name() == "lablog-export"
        assert window.analysis_tool_button.icon().name() == "lablog-analysis"
        assert window.maximize_plot_button.icon().name() == "lablog-maximize"
        assert window.save_view_preset_button.icon().name() == "lablog-save"
        controls = window.plot_interaction_controls
        assert controls.btn_pointer.icon().name() == "lablog-pointer"
        assert controls.btn_share.icon().name() == "lablog-drag_share"
        assert controls.btn_pointer.toolButtonStyle() == Qt.ToolButtonIconOnly
        assert controls.btn_pointer.toolTip() == controls.btn_pointer.text()
        assert controls.btn_share.toolTip() == controls.btn_share.text()
        assert controls.btn_pointer.isChecked() and not controls.btn_share.isChecked()
        controls.btn_share.click()
        assert controls.share_mode and not controls.btn_pointer.isChecked()
        controls.btn_pointer.click()
        assert not controls.share_mode
    finally:
        window.close()


def test_checked_controls_have_visible_selected_surface(qapp):
    from app.theme import DARK, LIGHT, _stylesheet

    for colors in (LIGHT, DARK):
        stylesheet = _stylesheet(colors)
        assert "QToolButton:checked" in stylesheet
        assert f"border: 1px solid {colors.accent}" in stylesheet
        assert ":pressed" in stylesheet


def test_palette_matches_v017c_reference(qapp):
    from app.theme import DARK, LIGHT

    assert (LIGHT.window, LIGHT.panel, LIGHT.accent, LIGHT.text, LIGHT.selected) == (
        "#F7F4EE", "#FFFFFF", "#297FA8", "#1D1D1F", "#D7E9FA")
    assert (DARK.window, DARK.panel, DARK.accent, DARK.text, DARK.selected) == (
        "#0B0F14", "#141A21", "#21C6E8", "#E6E6E6", "#2D6E77")


def test_browser_star_cell_and_button_use_icons(qapp, theme, tmp_path):
    from PySide6.QtCore import Qt

    from app.core.database_scanner import DatabaseScanResult, LogEntry
    from app.core.star_store import StarStore
    from app.gui.browser_window import STAR_STATE_ROLE, BrowserWindow

    entry = LogEntry("run.hdf5", "run.hdf5", "run.hdf5", "run", "ok", None, 10, 1.0)
    result = DatabaseScanResult(str(tmp_path), str(tmp_path), [entry])
    window = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"), theme_manager=theme)
    try:
        window.scan_result = result
        window._populate_folder_tree(result)
        item = window.data_list.topLevelItem(0)
        window.data_list.setCurrentItem(item)
        assert item.text(0) == ""
        assert item.icon(0).name() == "lablog-star"
        assert window.star_button.icon().name() == "lablog-star"
        window.star_button.click()
        assert item.data(0, STAR_STATE_ROLE) is True
        assert item.icon(0).name() == "lablog-star_filled"
        assert window.star_button.icon().name() == "lablog-star_filled"
        assert window.star_button.text() == "Unstar"
        assert window.star_button.toolTip() == "Unstar"
        assert window.star_button.toolButtonStyle() == Qt.ToolButtonIconOnly
        assert window.star_button.iconSize().width() == 32
        assert "Unstar" in item.toolTip(0)
        root = window.folder_tree.topLevelItem(0)
        assert root.icon(0).name() in {"lablog-folder", "lablog-folder_open"}
    finally:
        window.close()


def test_rename_falls_back_when_hard_links_are_unsupported(tmp_path, monkeypatch):
    from app.core import data_rename
    from app.core.data_rename import DataRenameError, rename_hdf5_data

    source = tmp_path / "old.hdf5"
    source.write_bytes(b"labber-bytes")
    (tmp_path / "taken.hdf5").write_bytes(b"other")

    def no_links(*_args):
        raise OSError(errno.EPERM, "hard links not supported")

    monkeypatch.setattr(data_rename.os, "link", no_links)
    with pytest.raises(DataRenameError):
        rename_hdf5_data(source, "taken", database_id=str(tmp_path),
                         old_relative_path="old.hdf5", state_stores=())
    assert (tmp_path / "taken.hdf5").read_bytes() == b"other"
    destination = rename_hdf5_data(source, "new", database_id=str(tmp_path),
                                   old_relative_path="old.hdf5", state_stores=())
    assert destination.read_bytes() == b"labber-bytes"
    assert not source.exists()


def test_follow_system_windows_registry_fallback_is_inert_elsewhere(monkeypatch):
    import app.theme as theme_module

    monkeypatch.setattr(theme_module.sys, "platform", "darwin")
    assert theme_module._windows_app_color_scheme() is None


def test_clicking_star_column_toggles_star(qapp, theme, tmp_path, monkeypatch):
    from app.core.database_scanner import DatabaseScanResult, LogEntry
    from app.core.star_store import StarStore
    from app.gui.browser_window import STAR_STATE_ROLE, BrowserWindow

    entry = LogEntry("run.hdf5", "run.hdf5", "run.hdf5", "run", "ok", None, 10, 1.0)
    result = DatabaseScanResult(str(tmp_path), str(tmp_path), [entry])
    stars = tmp_path / "stars.json"
    window = BrowserWindow(star_store=StarStore(stars), theme_manager=theme)
    opened = []
    monkeypatch.setattr(window, "_open_viewer_for", lambda entry: opened.append(entry))
    try:
        window.scan_result = result
        window._populate_folder_tree(result)
        item = window.data_list.topLevelItem(0)
        window.data_list.itemClicked.emit(item, 0)
        assert item.data(0, STAR_STATE_ROLE) is True
        assert item.icon(0).name() == "lablog-star_filled"
        assert StarStore(stars).is_starred(str(tmp_path), "run.hdf5")
        window.data_list.itemClicked.emit(item, 1)
        assert item.data(0, STAR_STATE_ROLE) is True
        window.data_list.itemClicked.emit(item, 0)
        assert item.data(0, STAR_STATE_ROLE) is False
        assert not StarStore(stars).is_starred(str(tmp_path), "run.hdf5")
        assert opened == []
    finally:
        window.close()


def test_browser_toolbar_icons_are_icon_only_with_tooltips(qapp, theme, tmp_path):
    from PySide6.QtCore import Qt

    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    window = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"), theme_manager=theme)
    try:
        for button, label in ((window.open_button, "Open Database..."),
                              (window.reload_button, "Reload Database"),
                              (window.star_button, "Star"), (window.tag_button, "Tag...")):
            assert button.toolButtonStyle() == Qt.ToolButtonIconOnly
            assert button.toolTip() == label
        button = window.appearance_toggle_button
        assert button.toolTip() == "Appearance: Light"
        assert button.icon().name() == "lablog-theme_light"
        button.click()
        assert theme.mode == "dark" and button.icon().name() == "lablog-theme_dark"
        assert button.toolTip() == "Appearance: Dark"
        button.click()
        assert theme.mode == "system" and button.icon().name() == "lablog-theme_system"
        assert button.toolTip().startswith("Appearance: Follow System (")
        button.click()
        assert theme.mode == "light"
        theme.set_mode("dark")
        assert button.property("appearanceMode") == "dark"
        for button in (window.open_button, window.star_button):
            assert button.iconSize().width() == 32
    finally:
        window.close()


def test_trace_show_column_is_a_clickable_eye_without_checkbox(qapp, theme):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTreeWidgetItem

    from app.gui.main_window import MainWindow

    window = MainWindow(theme_manager=theme)
    try:
        toggled = []
        window.trace_selection.is_visible = lambda trace: trace not in toggled
        window.trace_selection.is_selected = lambda trace: True
        window.trace_selection.set_visible = lambda trace, visible: toggled.append(trace) if not visible else None
        window.update_plot = lambda *args, **kwargs: None
        window._refresh_multi_trace_panel = lambda: None
        item = QTreeWidgetItem(["", "", "", "#1"])
        item.setData(3, Qt.UserRole, 0)
        window._on_multi_trace_item_clicked(item, 0)
        assert toggled == [0]
        assert item.data(0, Qt.CheckStateRole) is None
    finally:
        window.close()


def test_mark_tool_icons_follow_theme(qapp, theme):
    from app.core.mark_model import POINT_MARK
    from app.gui.mark_tool_icons import mark_tool_icon
    from app.theme import DARK, LIGHT

    light = _opaque_colors(mark_tool_icon(POINT_MARK).pixmap(20, 20))
    theme.set_mode("dark")
    dark = _opaque_colors(mark_tool_icon(POINT_MARK).pixmap(20, 20))
    assert LIGHT.text.upper() in light and DARK.text.upper() in dark


def test_plot_settings_stack_sizes_to_current_page(qapp, theme):
    from PySide6.QtWidgets import QSizePolicy

    from app.gui.main_window import MainWindow

    window = MainWindow(theme_manager=theme)
    try:
        stack = window.plot_controls_stack
        stack.setCurrentIndex(0)
        policies = [stack.widget(i).sizePolicy().verticalPolicy() for i in range(stack.count())]
        assert policies[0] != QSizePolicy.Policy.Ignored
        assert all(policy == QSizePolicy.Policy.Ignored for policy in policies[1:])
        # v0.18B: the 3D controls live in the 3D window, not in this stack.
        assert stack.count() == 2
        assert stack.sizeHint().height() < window._nd_controls_page.sizeHint().height()
        assert window.x_formula_preview.objectName() == "formulaPreview"
        assert not window.x_formula_preview.styleSheet()
    finally:
        window.close()


def test_browser_top_row_is_uniform_flat_icon_buttons(qapp, theme, tmp_path):
    from PySide6.QtCore import Qt

    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    window = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"), theme_manager=theme)
    try:
        expected = {
            window.retrieve_button: ("lablog-filter", "Retrieve by Tags..."),
            window.clear_query_button: ("lablog-back", "Back to Folder"),
            window.debackground_button: ("lablog-debackground", "De-background"),
        }
        for button, (icon_name, label) in expected.items():
            assert button.icon().name() == icon_name
            assert button.toolTip() == label
            assert button.toolButtonStyle() == Qt.ToolButtonIconOnly
        for button in (window.open_button, window.reload_button, window.star_button, window.tag_button,
                       window.retrieve_button, window.clear_query_button, window.debackground_button):
            assert button.property("lvFlat") is True
            assert button.iconSize().width() == 32
        opened = []
        window._open_debackground = lambda: opened.append(True)
        window.debackground_action.triggered.disconnect()
        window.debackground_action.triggered.connect(lambda: opened.append(True))
        window.debackground_button.click()
        assert opened == [True]
    finally:
        window.close()


def test_menu_buttons_hide_the_drop_down_arrow(qapp):
    from app.theme import LIGHT, _stylesheet

    assert "QToolButton::menu-indicator { image: none; width: 0px; }" in _stylesheet(LIGHT)


def test_analysis_workspaces_merge_copy_and_save_into_icon_menus(qapp, theme, monkeypatch):
    from PySide6.QtCore import Qt

    from app.analysis.yig_fitting.ui.analysis_pane import FitPlotWidget
    from app.analysis.yig_fitting.ui.plots import DataPlotWidget

    calls = []
    for cls in (DataPlotWidget, FitPlotWidget):
        for name in ("copy_all", "save_active_dialog", "save_all_dialog"):
            monkeypatch.setattr(cls, name, lambda self, name=name: calls.append(name))
    monkeypatch.setattr(DataPlotWidget, "copy_active", lambda self: calls.append("copy_active"))
    monkeypatch.setattr(FitPlotWidget, "copy_pane", lambda self, pane_id: calls.append("copy_active"))
    data = DataPlotWidget()
    fit = FitPlotWidget()
    try:
        for copy_button, save_button in ((data.copy_button, data.save_button), (fit.btn_copy, fit.btn_save)):
            assert copy_button.icon().name() == "lablog-copy" and save_button.icon().name() == "lablog-save"
            assert copy_button.toolButtonStyle() == Qt.ToolButtonIconOnly
            assert [a.text() for a in copy_button.menu().actions()] == ["Copy This Pane", "Copy All Panes"]
            assert [a.text() for a in save_button.menu().actions()] == ["Save This Pane...", "Save All Panes..."]
            calls.clear()
            for action in (*copy_button.menu().actions(), *save_button.menu().actions()):
                action.trigger()
            assert calls == ["copy_active", "copy_all", "save_active_dialog", "save_all_dialog"]
        assert data.view_all_button.icon().name() == "lablog-view_all"
        assert fit.btn_view_all.icon().name() == "lablog-view_all"
        assert data.pointer_button.iconSize().width() == 20
    finally:
        data.close()
        fit.close()
