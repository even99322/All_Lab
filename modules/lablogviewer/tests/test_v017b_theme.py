from __future__ import annotations

import json
import numpy as np
import pytest


@pytest.fixture(scope="module")
def qapp():
    import os

    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_appearance_setting_persists_the_selected_mode(tmp_path):
    from app.settings.store import SettingsStore

    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    assert store.appearance() == "system"
    store.set_appearance("dark")
    store.set_appearance("system")
    saved = json.loads(path.read_text())
    assert saved["general"]["appearance"] == "system"
    assert SettingsStore(path).appearance() == "system"
    with pytest.raises(ValueError):
        store.set_appearance("automatic-dark")


def test_browser_and_settings_controls_share_one_theme_mode(qapp, tmp_path):
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow
    from app.settings.dialog import SettingsDialog
    from app.settings.store import SettingsStore
    from app.theme import DARK, LIGHT, ThemeManager

    previous_palette = qapp.palette()
    previous_stylesheet = qapp.styleSheet()
    settings_path = tmp_path / "theme.json"
    manager = ThemeManager(qapp, SettingsStore(settings_path), qapp)
    browser = BrowserWindow(
        star_store=StarStore(tmp_path / "stars.json"), theme_manager=manager,
    )
    settings = SettingsDialog(browser.localizer, browser, manager)
    try:
        view_row = browser._toolbar_segments[-1]
        view_widgets = [view_row.itemAt(i).widget() for i in range(view_row.count())]
        assert view_widgets[-1] is browser.annotation_button      # v0.18D: top-right
        assert view_widgets[-2] is browser.debackground_button
        assert view_widgets.index(browser.appearance_toggle_button) < view_widgets.index(
            browser.debackground_button
        )
        assert browser.APPEARANCE_CYCLE == ("light", "dark", "system")
        assert [settings.appearance_combo.itemData(i)
                for i in range(settings.appearance_combo.count())] == ["light", "dark", "system"]
        # v0.19A: clicking the Settings menu opens Settings directly (one fallback item, no sub-items).
        assert [action.text() for action in browser.settings_menu.actions()] == ["Settings..."]
        assert not any(action.menu() for action in browser.settings_menu.actions())

        assert browser.appearance_toggle_button.property("appearanceMode") == "system"
        browser.appearance_toggle_button.click()
        assert manager.mode == "light"
        browser.appearance_toggle_button.click()
        assert manager.mode == "dark"
        assert settings.appearance_combo.currentData() == "dark"
        assert qapp.palette().color(qapp.palette().ColorRole.Window).name() == DARK.window.lower()

        settings.appearance_combo.setCurrentIndex(0)
        assert manager.mode == "light"
        assert browser.appearance_toggle_button.property("appearanceMode") == "light"
        assert qapp.palette().color(qapp.palette().ColorRole.Window).name() == LIGHT.window.lower()

        settings.appearance_combo.setCurrentIndex(2)
        assert manager.mode == "system"
        assert json.loads(settings_path.read_text())["general"]["appearance"] == "system"
        assert settings.appearance_combo.currentData() == "system"
        assert browser.appearance_toggle_button.property("appearanceMode") == "system"
        assert browser.scanner_root is None
        assert browser.scan_result is None
    finally:
        settings.close()
        browser.close()
        manager.detach()
        manager.deleteLater()
        qapp.setStyleSheet(previous_stylesheet)
        qapp.setPalette(previous_palette)
        qapp.processEvents()


def test_theme_restyles_plot_chrome_without_changing_scientific_colormap(qapp, tmp_path):
    from app.core.data_model import Grid2DData
    from app.gui.plot_2d_widget import DEFAULT_COLORMAP, Plot2DWidget
    from app.gui.plot_widget import Plot1DWidget
    from app.settings.store import SettingsStore
    from app.theme import ThemeManager
    from PySide6.QtWidgets import QWidget, QVBoxLayout

    previous_palette = qapp.palette()
    previous_stylesheet = qapp.styleSheet()
    manager = ThemeManager(qapp, SettingsStore(tmp_path / "theme.json"), qapp)
    host = QWidget()
    layout = QVBoxLayout(host)
    line = Plot1DWidget()
    x = np.arange(4, dtype=float)
    y = np.array([1.0, 2.0, 1.5, 3.0])
    line.plot(x, y, x_label="Frequency", y_label="S21")
    source_y = line._trace_data[0][1]
    heatmap = Plot2DWidget()
    z = np.array([[0.0, 1.0], [2.0, 3.0]])
    heatmap.plot(Grid2DData(
        x_values=np.array([1.0, 2.0]), y_values=np.array([3.0, 4.0]), z_values=z,
        x_name="Frequency", x_unit="Hz", y_name="Current", y_unit="A",
        z_name="S21", z_unit=None, transform="Magnitude (dB)",
    ))
    image = heatmap.img_item.image
    levels = tuple(heatmap.img_item.getLevels())
    color_map = heatmap._colormap_name
    layout.addWidget(line)
    layout.addWidget(heatmap)
    try:
        manager.set_mode("dark")
        assert line._trace_data[0][1] is source_y
        assert np.array_equal(line._trace_data[0][1], y)
        assert heatmap.img_item.image is image
        assert tuple(heatmap.img_item.getLevels()) == levels
        assert heatmap._colormap_name == color_map == DEFAULT_COLORMAP
        assert manager.colors.plot_background != "#FFFFFF"
    finally:
        host.close()
        manager.detach()
        manager.deleteLater()
        qapp.setStyleSheet(previous_stylesheet)
        qapp.setPalette(previous_palette)
        qapp.processEvents()


def test_scientific_and_export_plot_appearance_persist_independently(tmp_path):
    from app.settings.store import SettingsStore

    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    assert store.scientific_plot_appearance() == "white"
    assert store.export_plot_background() == "white"
    store.set_scientific_plot_appearance("dark")
    store.set_export_plot_background("white")
    restored = SettingsStore(path)
    assert restored.scientific_plot_appearance() == "dark"
    assert restored.export_plot_background() == "white"
    with pytest.raises(ValueError):
        store.set_scientific_plot_appearance("system")
    with pytest.raises(ValueError):
        store.set_export_plot_background("transparent")


def test_appearance_page_is_independent_from_quick_theme(qapp, tmp_path):
    from app.core.star_store import StarStore
    from app.settings.dialog import SettingsDialog
    from app.gui.browser_window import BrowserWindow
    from app.settings.store import SettingsStore
    from app.theme import ThemeManager

    previous_palette = qapp.palette()
    previous_stylesheet = qapp.styleSheet()
    store = SettingsStore(tmp_path / "settings.json")
    manager = ThemeManager(qapp, store, qapp)
    browser = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"), theme_manager=manager)
    try:
        assert browser.APPEARANCE_CYCLE == ("light", "dark", "system")
        browser._open_settings("appearance")
        settings = browser._settings_dialog
        assert settings is not None and settings.pages.currentIndex() == SettingsDialog.PAGES.index("appearance")
        assert [settings.scientific_plot_combo.itemData(i) for i in range(2)] == ["white", "dark"]
        assert [settings.export_plot_combo.itemData(i) for i in range(2)] == ["white", "dark"]

        settings.scientific_plot_combo.setCurrentIndex(1)
        settings.export_plot_combo.setCurrentIndex(0)
        settings.appearance_combo.setCurrentIndex(1)
        assert manager.mode == "dark"
        assert manager.scientific_plot_appearance == "dark"
        assert manager.export_plot_background == "white"
        assert browser.appearance_toggle_button.property("appearanceMode") == "dark"
        assert json.loads((tmp_path / "settings.json").read_text())["general"] == {
            "language": "en", "appearance": "dark",
            "scientific_plot_appearance": "dark", "export_plot_background": "white",
            "glass_thickness": 0.5, "glass_frost": 0.25, "three_d_profile": "balanced",
            "three_d_export_style": "publication", "app_icon": 4,
        }

        # Glass dials on the page and in the Browser toolbar stay in sync.
        settings.glass_sliders["thickness"].setValue(70)
        assert manager.glass_material == (0.7, 0.25)
        assert browser.glass_sliders["thickness"].value() == 70
        browser.glass_sliders["frost"].setValue(60)
        assert settings.glass_sliders["frost"].value() == 60
        manager.set_scientific_plot_appearance("white")
        assert settings.scientific_plot_combo.currentData() == "white"
    finally:
        browser.close()
        manager.detach()
        manager.deleteLater()
        qapp.setStyleSheet(previous_stylesheet)
        qapp.setPalette(previous_palette)
        qapp.processEvents()


def test_app_theme_and_plot_appearance_are_independent(qapp, tmp_path):
    from app.core.data_model import Grid2DData
    from app.gui.plot_2d_widget import Plot2DWidget
    from app.gui.plot_widget import Plot1DWidget
    from app.settings.store import SettingsStore
    from app.theme import DARK_PLOT, WHITE_PLOT, ThemeManager
    from PySide6.QtWidgets import QWidget, QVBoxLayout

    previous_palette = qapp.palette()
    previous_stylesheet = qapp.styleSheet()
    manager = ThemeManager(qapp, SettingsStore(tmp_path / "settings.json"), qapp)
    host = QWidget()
    layout = QVBoxLayout(host)
    line = Plot1DWidget()
    x = np.arange(5, dtype=float)
    y = np.array([0.0, 1.0, 0.5, 2.0, 1.5])
    line.plot(x, y, x_label="Frequency", y_label="S21")
    grid_values = np.array([[0.0, 1.0], [2.0, 3.0]])
    heatmap = Plot2DWidget()
    heatmap.plot(Grid2DData(
        x_values=np.array([1.0, 2.0]), y_values=np.array([3.0, 4.0]), z_values=grid_values,
        x_name="Frequency", x_unit="Hz", y_name="Current", y_unit="A",
        z_name="S21", z_unit=None, transform="Magnitude (dB)",
    ))
    layout.addWidget(line)
    layout.addWidget(heatmap)
    host.show()
    qapp.processEvents()
    source_image = heatmap.img_item.image
    source_values = line._trace_data[0][1]
    try:
        manager.set_mode("dark")
        assert qapp.palette().color(qapp.palette().ColorRole.Window).name() == "#0b0f14"
        assert manager.scientific_plot_appearance == "white"
        assert line._plot_colors == WHITE_PLOT
        manager.set_scientific_plot_appearance("dark")
        assert line._plot_colors == DARK_PLOT
        assert heatmap._plot_colors == DARK_PLOT
        assert "e6e6e6" in heatmap.plot_item.titleLabel.item.toHtml().lower()
        assert "e6e6e6" in heatmap.color_bar.getAxis("left").label.toHtml().lower()
        manager.set_mode("light")
        assert line._plot_colors == DARK_PLOT
        assert heatmap.img_item.image is source_image
        assert np.array_equal(line._trace_data[0][1], source_values)
        assert np.array_equal(heatmap._grid.z_values, grid_values)
    finally:
        host.close()
        manager.detach()
        manager.deleteLater()
        qapp.setStyleSheet(previous_stylesheet)
        qapp.setPalette(previous_palette)
        qapp.processEvents()


def test_export_renders_target_plot_appearance_then_restores_display(qapp, tmp_path):
    from contextlib import contextmanager
    from PySide6.QtCore import QRect, QSize
    from PySide6.QtWidgets import QWidget, QVBoxLayout
    from app.gui.plot_export import PaneRenderSurface, render_composite_image
    from app.gui.plot_widget import Plot1DWidget
    from app.settings.store import SettingsStore
    from app.theme import DARK_PLOT, WHITE_PLOT, ThemeManager

    previous_palette = qapp.palette()
    previous_stylesheet = qapp.styleSheet()
    manager = ThemeManager(qapp, SettingsStore(tmp_path / "settings.json"), qapp)
    manager.set_scientific_plot_appearance("dark")
    manager.set_export_plot_background("white")
    host = QWidget()
    layout = QVBoxLayout(host)
    plot = Plot1DWidget()
    plot.plot(np.arange(20.0), np.sin(np.arange(20.0)), x_label="X", y_label="Y")
    layout.addWidget(plot)
    host.resize(480, 320)
    host.show()
    qapp.processEvents()
    rendered_appearance = []

    @contextmanager
    def export_appearance():
        with plot.temporary_scientific_plot_appearance(manager.export_plot_colors):
            rendered_appearance.append(plot._plot_colors)
            yield

    try:
        assert plot._plot_colors == DARK_PLOT
        surface = PaneRenderSurface(
            plot.plot_widget, QRect(0, 0, plot.plot_widget.width(), plot.plot_widget.height()),
            appearance_context=export_appearance,
        )
        image = render_composite_image([surface], QSize(480, 320), scale=1, background="#FFFFFF")
        assert not image.isNull()
        assert rendered_appearance == [WHITE_PLOT]
        assert image.pixelColor(0, 0).name() == "#ffffff"
        assert plot._plot_colors == DARK_PLOT
    finally:
        host.close()
        manager.detach()
        manager.deleteLater()
        qapp.setStyleSheet(previous_stylesheet)
        qapp.setPalette(previous_palette)
        qapp.processEvents()


def test_viewer_controls_stay_compact_across_themes_without_changing_3d_layout(qapp, tmp_path):
    from app.gui.main_window import MainWindow
    from app.settings.store import SettingsStore
    from app.theme import ThemeManager

    previous_palette = qapp.palette()
    previous_stylesheet = qapp.styleSheet()
    manager = ThemeManager(qapp, SettingsStore(tmp_path / "settings.json"), qapp)
    window = MainWindow(theme_manager=manager)
    window.resize(1280, 760)
    window.show()
    qapp.processEvents()
    try:
        assert window.main_splitter.sizes()[0] >= 260
        assert window.transform_combo.width() >= 145
        pane_row_height = window.pane_controls_host.height()
        main_splitter_y = window.main_splitter.y()
        assert abs(pane_row_height - window.pane_controls_host.sizeHint().height()) <= 2
        manager.set_mode("dark")
        qapp.processEvents()
        assert window.pane_controls_host.height() == pane_row_height
        assert window.main_splitter.y() == main_splitter_y
        assert window.mode_combo.currentIndex() == 0

        window.mode_combo.setCurrentIndex(1)
        qapp.processEvents()
        heatmap_row_height = window.pane_controls_host.height()
        heatmap_splitter_y = window.main_splitter.y()
        assert window.transform_combo_2d.width() >= 145
        assert window.colormap_combo.width() >= 145
        manager.set_mode("light")
        manager.set_mode("dark")
        qapp.processEvents()
        assert window.pane_controls_host.height() == heatmap_row_height
        assert window.main_splitter.y() == heatmap_splitter_y

        # v0.18B: 3D opens in its own window; the Viewer layout is untouched.
        window.open_3d_window()
        qapp.processEvents()
        assert not window.pane_controls_host.isHidden()
        assert window.main_splitter.y() == heatmap_splitter_y
    finally:
        window.close()
        manager.detach()
        manager.deleteLater()
        qapp.setStyleSheet(previous_stylesheet)
        qapp.setPalette(previous_palette)
        qapp.processEvents()


def test_analysis_fit_controls_remain_present_and_scrollable(qapp):
    from PySide6.QtWidgets import QGroupBox
    from app.analysis.yig_fitting.ui.main_window import MainWindow

    window = MainWindow()
    try:
        titles = {group.title() for group in window.control_scroll.widget().findChildren(QGroupBox)}
        assert {
            "DATA", "PREPROCESSING", "FIT MODEL", "FIT SETTINGS", "SINGLE FIT",
            "OUTPUT", "CONTINUOUS FIT SETTINGS",
        } <= titles
        for name in (
            "cmb_s", "cmb_axis", "spin_f1", "spin_f2", "chk_spike", "txt_excl",
            "btn_library", "btn_builder", "chk_weight", "cmb_method", "cmb_loss",
            "btn_fit", "btn_cancel", "btn_copy", "btn_export", "txt_outdir",
            "spin_b_start", "spin_b_end", "cmb_b_track", "roll_table", "btn_batch",
            "btn_batch_cancel", "batch_progress",
        ):
            assert hasattr(window, name), name
        assert window.control_scroll.widgetResizable()
    finally:
        window.close()


def test_analysis_plots_keep_selected_style_after_replot_and_export(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QWidget, QVBoxLayout
    from app.analysis.yig_fitting.ui import plots
    from app.analysis.yig_fitting.ui.plots import DataPlotWidget, ParamPlotWidget
    from app.settings.store import SettingsStore
    from app.theme import DARK_PLOT, WHITE_PLOT, ThemeManager

    previous_palette = qapp.palette()
    previous_stylesheet = qapp.styleSheet()
    manager = ThemeManager(qapp, SettingsStore(tmp_path / "settings.json"), qapp)
    manager.set_scientific_plot_appearance("dark")
    manager.set_export_plot_background("white")
    monkeypatch.setattr(plots, "get_theme_manager", lambda: manager)

    host = QWidget()
    layout = QVBoxLayout(host)
    parameter_plot = ParamPlotWidget()
    data_plot = DataPlotWidget()
    layout.addWidget(parameter_plot)
    host.resize(900, 700)
    host.show()
    qapp.processEvents()
    try:
        parameter_plot.plot(
            np.arange(4.0), np.array([1.0, 2.0, 1.5, 2.5]), None,
            np.ones(4, dtype=bool), list(range(4)), "Sweep", "κm",
        )
        data_plot.map_pane.apply_scientific_plot_appearance(DARK_PLOT, draw=False)
        data_plot.slice_pane.apply_scientific_plot_appearance(DARK_PLOT, draw=False)
        data_plot.set_map(
            np.linspace(5.0, 5.1, 12), np.array([1.0, 2.0, 3.0]),
            np.arange(36.0).reshape(12, 3), "Current", "S21",
        )
        qapp.processEvents()
        assert parameter_plot._plot_colors == DARK_PLOT
        assert parameter_plot.ax.get_facecolor() == pytest.approx((0.0784, 0.1020, 0.1294, 1.0), abs=0.01)
        assert data_plot.map_pane._plot_colors == DARK_PLOT
        assert data_plot.slice_pane._plot_colors == DARK_PLOT

        image = parameter_plot._render_composite()
        assert not image.isNull()
        assert image.pixelColor(0, 0).name() == WHITE_PLOT.background.lower()
        assert parameter_plot._plot_colors == DARK_PLOT
        assert parameter_plot.ax.get_facecolor() == pytest.approx((0.0784, 0.1020, 0.1294, 1.0), abs=0.01)

        parameter_plot.clear()
        assert parameter_plot.ax.get_facecolor() == pytest.approx((0.0784, 0.1020, 0.1294, 1.0), abs=0.01)
    finally:
        host.close()
        manager.detach()
        manager.deleteLater()
        qapp.setStyleSheet(previous_stylesheet)
        qapp.setPalette(previous_palette)
        qapp.processEvents()


def test_theme_show_filter_is_reentrancy_safe(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QWidget
    from app.settings.store import SettingsStore
    from app.theme import ThemeManager

    previous_palette = qapp.palette()
    previous_stylesheet = qapp.styleSheet()
    manager = ThemeManager(qapp, SettingsStore(tmp_path / "settings.json"), qapp)
    widget = QWidget()
    calls = []

    def reenter(target):
        calls.append(target)
        manager.eventFilter(target, QEvent(QEvent.Type.Show))

    monkeypatch.setattr(manager, "_apply_widget", reenter)
    manager._themed_widgets.add(widget)
    try:
        widget.show()
        qapp.processEvents()
        calls.clear()
        manager._apply_theme()
        assert calls == [widget]
        assert manager._applying_show_event is False
    finally:
        manager.detach()
        manager.deleteLater()
        widget.close()
        qapp.setStyleSheet(previous_stylesheet)
        qapp.setPalette(previous_palette)
        qapp.processEvents()


def test_theme_styles_visible_windows_without_repolishing_every_app_widget(qapp, tmp_path):
    from PySide6.QtWidgets import QWidget
    from app.settings.store import SettingsStore
    from app.theme import ThemeManager

    previous_palette = qapp.palette()
    previous_stylesheet = qapp.styleSheet()
    store = SettingsStore(tmp_path / "settings.json")
    store.set_appearance("light")
    manager = ThemeManager(qapp, store, qapp)
    window = QWidget()
    try:
        window.show()
        qapp.processEvents()
        assert qapp.styleSheet() == previous_stylesheet
        assert "#f7f4ee" in window.styleSheet().lower()
        manager.set_mode("dark")
        qapp.processEvents()
        assert qapp.styleSheet() == previous_stylesheet
        assert "#0b0f14" in window.styleSheet().lower()
    finally:
        window.close()
        manager.detach()
        manager.deleteLater()
        qapp.setPalette(previous_palette)
        qapp.processEvents()
