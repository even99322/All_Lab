"""Plot-only copy/export coverage for v0.14A."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.real_data import BIG_FILE, FLUX_FILE

try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QTableWidgetItem
    from PySide6.QtWidgets import QApplication
    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


pytestmark = pytest.mark.skipif(
    not PYSIDE_AVAILABLE or not BIG_FILE.exists(),
    reason="Qt or real Labber fixture unavailable",
)


def _viewer(qapp):
    from app.gui.main_window import MainWindow

    viewer = MainWindow()
    viewer.resize(1000, 700)
    viewer.show()
    viewer.open_file(str(BIG_FILE))
    qapp.processEvents()
    return viewer


def test_single_plot_copy_png_and_svg_export_current_render(qapp, tmp_path):
    viewer = _viewer(qapp)
    try:
        assert viewer.copy_plot_action.shortcut().toString()
        viewer._copy_active_pane()
        image = QApplication.clipboard().image()
        assert not image.isNull()
        assert image.width() >= viewer.plot_widget.plot_widget.width() * 2
        assert image.height() >= viewer.plot_widget.plot_widget.height() * 2

        png = tmp_path / "single.png"
        svg = tmp_path / "single.svg"
        assert viewer._save_export(str(png))
        assert viewer._save_export(str(svg))
        assert png.read_bytes().startswith(b"\x89PNG")
        svg_bytes = svg.read_bytes()
        assert b"<svg" in svg_bytes[:500]
        assert b"<polyline" in svg_bytes or b"<path" in svg_bytes
    finally:
        viewer.close()


def test_single_pane_copy_uses_the_same_composite_renderer_as_save(qapp):
    viewer = _viewer(qapp)
    try:
        from app.gui.plot_export import render_composite_image as real_render

        with patch("app.gui.main_window.render_composite_image", wraps=real_render) as render:
            viewer._copy_active_pane()
        assert render.call_count == 1
        surfaces, size = render.call_args.args
        assert len(surfaces) == 1
        assert size.width() == surfaces[0].target.width()
        assert size.height() == surfaces[0].target.height()
    finally:
        viewer.close()


def test_plot_context_menu_augments_native_pyqtgraph_actions(qapp):
    viewer = _viewer(qapp)
    try:
        surface = viewer.plot_widget.plot_widget
        menu = viewer._build_plot_context_menu(surface, surface.getPlotItem(), None)
        labels = [action.text() for action in menu.actions() if not action.isSeparator()]
        assert "Copy Plot" in labels
        assert "Save Plot As..." in labels
        assert {"View All", "X axis", "Y axis", "Mouse Mode"}.issubset(labels)
        assert "Plot Options" in labels
        assert "Export..." not in labels
        assert len(labels) == len(set(labels))
        assert viewer.copy_context_action.shortcut().toString()
        assert viewer.copy_all_panes_action.shortcut().toString()
    finally:
        viewer.close()


def test_multi_pane_composite_uses_all_panes_even_when_maximized(qapp, tmp_path):
    viewer = _viewer(qapp)
    try:
        viewer.pane_layout_combo.setCurrentText("4 Panes \u00b7 2 \u00d7 2")
        qapp.processEvents()
        assert viewer.copy_plot_action.text() == "Copy Active Pane"
        assert viewer.copy_all_panes_action.isEnabled()
        surfaces, size = viewer._pane_render_surfaces(all_panes=True)
        assert len(surfaces) == 4
        assert len({(item.target.x(), item.target.y()) for item in surfaces}) == 4

        viewer.maximize_plot_button.setChecked(True)
        qapp.processEvents()
        maximized_surfaces, maximized_size = viewer._pane_render_surfaces(all_panes=True)
        assert len(maximized_surfaces) == 4
        assert maximized_size.width() > 0 and maximized_size.height() > 0
        assert len({(item.target.x(), item.target.y()) for item in maximized_surfaces}) == 4

        png = tmp_path / "all-panes.png"
        svg = tmp_path / "all-panes.svg"
        assert viewer._save_export(str(png), all_panes=True)
        assert viewer._save_export(str(svg), all_panes=True)
        assert png.read_bytes().startswith(b"\x89PNG")
        assert b"<svg" in svg.read_bytes()[:500]
    finally:
        viewer.close()


def test_pointer_pane_export_remains_distinct_from_active_pane(qapp):
    viewer = _viewer(qapp)
    try:
        viewer.pane_layout_combo.setCurrentText("2 Panes \u00b7 Side by Side")
        viewer._activate_pane(1)
        qapp.processEvents()
        pane_two, _ = viewer._pane_render_surfaces(2)
        active, _ = viewer._pane_render_surfaces(1)
        assert pane_two and active
        assert pane_two[0].widget is viewer._pane_frames[2].plot_1d.plot_widget
        assert active[0].widget is viewer._pane_frames[1].plot_1d.plot_widget
        assert viewer._pane_frames[2].plot_1d.plot_widget.contextMenuPolicy().name == "CustomContextMenu"
    finally:
        viewer.close()


@pytest.mark.skipif(not FLUX_FILE.exists(), reason="Real Flux fixture unavailable")
def test_flux_heatmap_export_uses_the_rendered_2d_surface(qapp, tmp_path):
    from app.gui.main_window import MainWindow
    from app.gui.plot_export import render_composite_image

    viewer = MainWindow()
    try:
        viewer.resize(1000, 700)
        viewer.show()
        viewer.open_file(str(FLUX_FILE))
        viewer.mode_combo.setCurrentIndex(1)
        qapp.processEvents()
        assert viewer.plot_2d_widget._grid is not None
        image = render_composite_image(*viewer._pane_render_surfaces())
        assert not image.isNull() and image.width() > 10 and image.height() > 10
        output = tmp_path / "flux.png"
        assert viewer._save_export(str(output))
        assert output.read_bytes().startswith(b"\x89PNG")
    finally:
        viewer.close()


@pytest.mark.skipif(not FLUX_FILE.exists(), reason="Real Flux fixture unavailable")
def test_interactive_colorbar_range_becomes_manual_persisted_display_state(qapp, tmp_path):
    from app.gui.main_window import MainWindow
    from app.core.quick_preview import build_quick_preview
    from app.core.viewer_display_state_store import ViewerDisplayStateStore

    store = ViewerDisplayStateStore(tmp_path / "viewer_display_states.json")
    viewer = MainWindow(viewer_display_state_store=store)
    try:
        viewer.resize(1000, 700)
        viewer.show()
        viewer.open_file(str(FLUX_FILE))
        viewer.mode_combo.setCurrentIndex(1)
        qapp.processEvents()
        color_bar = viewer.plot_2d_widget.color_bar
        assert color_bar is not None
        low = viewer.plot_2d_widget._z_min + 0.1
        high = viewer.plot_2d_widget._z_max - 0.1
        assert low < high
        color_bar.values = (low, high)
        color_bar.sigLevelsChanged.emit(color_bar)
        qapp.processEvents()
        state = viewer._current_viewer_display_state()
        assert not viewer.auto_range_checkbox.isChecked()
        assert viewer.zmin_spin.value() == pytest.approx(low, abs=1e-4)
        assert viewer.zmax_spin.value() == pytest.approx(high, abs=1e-4)
        assert state and state["two_d"]["auto_color"] is False
        assert state["two_d"]["minimum"] == pytest.approx(low)
        assert state["two_d"]["maximum"] == pytest.approx(high)
        viewer._flush_display_state()
        persisted = store.get(FLUX_FILE)
        assert persisted == state
        preview = build_quick_preview(str(FLUX_FILE), persisted)
        assert preview.kind == "2d" and preview.restored
        assert preview.z_min == pytest.approx(low)
        assert preview.z_max == pytest.approx(high)
    finally:
        viewer.close()


def test_open_file_defers_inactive_2d_and_nd_surface_construction(qapp, monkeypatch):
    """First paint should build only the selected plot mode's scientific data."""
    from app.core.cache import CachedExperiment
    from app.gui.main_window import MainWindow

    calls = {"2d": 0, "nd": 0}
    original_2d = CachedExperiment.get_2d_data
    original_nd = CachedExperiment.get_nd_slice

    def count_2d(self, *args, **kwargs):
        calls["2d"] += 1
        return original_2d(self, *args, **kwargs)

    def count_nd(self, *args, **kwargs):
        calls["nd"] += 1
        return original_nd(self, *args, **kwargs)

    monkeypatch.setattr(CachedExperiment, "get_2d_data", count_2d)
    monkeypatch.setattr(CachedExperiment, "get_nd_slice", count_nd)
    viewer = MainWindow()
    try:
        viewer.open_file(str(BIG_FILE))
        assert viewer.mode_combo.currentIndex() == 0
        assert calls == {"2d": 0, "nd": 0}
        viewer.mode_combo.setCurrentIndex(1)
        assert calls["2d"] == 1
    finally:
        viewer.close()


def test_browser_open_records_a_real_first_frame_timing_breakdown(qapp, tmp_path):
    from app.core.database_scanner import LogEntry
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    stat = BIG_FILE.stat()
    entry = LogEntry(
        absolute_path=str(BIG_FILE), relative_path=BIG_FILE.name, file_name=BIG_FILE.name,
        log_name=BIG_FILE.stem, status="ok", error_message=None,
        size_bytes=stat.st_size, mtime=stat.st_mtime,
    )
    browser = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    try:
        viewer = browser._open_viewer_for(entry)
        assert viewer is not None
        timing = browser.last_viewer_open_timing
        assert timing is not None
        assert set(timing) == {
            "viewer_construction_ms", "data_model_and_initial_plot_ms",
            "session_restore_ms", "first_usable_frame_ms", "total_open_ms",
        }
        assert timing["total_open_ms"] >= timing["viewer_construction_ms"]
    finally:
        for viewer in browser._viewers:
            viewer.close()
        browser.close()


def test_copy_shortcut_is_plot_scoped_and_does_not_steal_table_copy(qapp):
    viewer = _viewer(qapp)
    try:
        clipboard = QApplication.clipboard()
        clipboard.clear()
        surface = viewer.plot_widget.plot_widget
        surface.setFocus()
        QTest.keyClick(surface, Qt.Key_C, Qt.ControlModifier)
        qapp.processEvents()
        assert not clipboard.image().isNull()

        viewer.data_table.setRowCount(1)
        viewer.data_table.setColumnCount(1)
        viewer.data_table.setItem(0, 0, QTableWidgetItem("table copy remains native"))
        viewer.data_table.selectAll()
        viewer._show_data_table_dialog()
        qapp.processEvents()
        clipboard.clear()
        viewer.data_table.setFocus()
        QTest.keyClick(viewer.data_table, Qt.Key_C, Qt.ControlModifier)
        qapp.processEvents()
        assert "table copy remains native" in clipboard.text()
    finally:
        viewer.close()
