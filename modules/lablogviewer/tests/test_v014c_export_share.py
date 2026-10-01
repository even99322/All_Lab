"""Focused v0.14C coverage for export integration and drag-to-share seams."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.real_data import BIG_FILE

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication, QScrollArea
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


def test_trace_manager_is_scrollable_without_constraining_workspace_splitter(qapp):
    viewer = _viewer(qapp)
    try:
        viewer._show_trace_manager()
        assert isinstance(viewer.multi_trace_panel, QScrollArea)
        assert viewer.multi_trace_panel.widget() is viewer.multi_trace_content
        assert viewer.trace_management_splitter.minimumHeight() <= 1
        assert viewer.multi_trace_content.minimumHeight() > viewer.trace_management_splitter.minimumHeight()
        viewer.plot_1d_splitter.setSizes([690, 10])
        qapp.processEvents()
        assert viewer.multi_trace_tree.parent() is viewer.multi_trace_content
        assert viewer.multi_trace_tree.minimumHeight() == 96
    finally:
        viewer.close()


def test_top_export_button_uses_same_menu_actions_and_context_hides_generic_export(qapp):
    viewer = _viewer(qapp)
    try:
        assert viewer.export_tool_button.menu() is viewer.export_menu
        assert viewer.export_menu.actions()[0] is viewer.copy_plot_action
        assert viewer.export_data_action in viewer.export_menu.actions()
        assert viewer.export_menu.actions()[-1] is viewer.export_animation_action
        surface = viewer.plot_widget.plot_widget
        menu = viewer._build_plot_context_menu(surface, surface.getPlotItem(), None)
        labels = [action.text().replace("&", "") for action in menu.actions() if not action.isSeparator()]
        assert "Export..." not in labels
        assert {"View All", "X axis", "Y axis", "Mouse Mode", "Plot Options"}.issubset(labels)
        assert "Export Data..." in labels
    finally:
        viewer.close()


def test_drag_share_renders_lazily_with_pointer_pane_routing(qapp, tmp_path):
    from app.gui.drag_share import DragShareController, DragShareManager

    viewer = _viewer(qapp)
    try:
        assert viewer._drag_share_controllers
        assert DragShareController.use_pointer_pane(Qt.AltModifier)
        assert not DragShareController.use_pointer_pane(Qt.NoModifier)
        image, filename = viewer._render_drag_share_payload(None, False)
        assert isinstance(image, QImage) and not image.isNull()
        assert BIG_FILE.stem.split()[0] in filename
        manager = DragShareManager(tmp_path / "drag-share")
        path = manager.create_png(image, filename)
        assert path.name.startswith("lablogviewer-")
        assert path.suffix == ".png"
        assert path.read_bytes().startswith(b"\x89PNG")
        assert viewer._drag_share_is_available()
        viewer._pending_mark_tool = "point"
        assert not viewer._drag_share_is_available()
    finally:
        viewer.close()


def test_multi_pane_drag_routing_locks_the_pointer_pane_without_changing_active(qapp):
    viewer = _viewer(qapp)
    try:
        viewer.pane_layout_combo.setCurrentText("2 Panes · Side by Side")
        viewer._activate_pane(1)
        qapp.processEvents()
        active_before = viewer._active_pane_id
        pointer_image, pointer_name = viewer._render_drag_share_payload(2, False)
        all_image, all_name = viewer._render_drag_share_payload(2, True)
        assert not pointer_image.isNull() and not all_image.isNull()
        assert "Pane2" in pointer_name
        assert "AllPanes" in all_name
        assert all_image.width() >= pointer_image.width()
        assert viewer._active_pane_id == active_before
    finally:
        viewer.close()
