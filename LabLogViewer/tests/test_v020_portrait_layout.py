"""Portrait screens put the plot on top and the tool panel underneath."""
import pytest
from PySide6.QtCore import Qt


@pytest.fixture(scope="module")
def qapp():
    import os
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _window(qapp):
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    qapp.processEvents()
    return window


def test_portrait_screen_moves_tool_panel_below_plot_and_back(qapp):
    window = _window(qapp)
    try:
        splitter = window.main_splitter
        assert splitter.orientation() == Qt.Horizontal
        assert splitter.widget(0) is window.controls_panel

        window._portrait_override = True
        window._update_orientation()
        qapp.processEvents()
        assert splitter.orientation() == Qt.Vertical
        assert splitter.widget(0) is window.right_splitter and splitter.widget(1) is window.controls_panel
        controls, plot = window._main_sizes()
        assert controls > 0 and plot > controls
        assert window.controls_panel.minimumWidth() == 0

        window._set_surface_layout_mode(True)          # 3D page keeps the portrait layout
        assert splitter.orientation() == Qt.Vertical and window.controls_panel.maximumWidth() > 1000
        window._set_surface_layout_mode(False)

        window._on_toggle_channel_browser(True)
        assert window.controls_panel.isHidden() and window._main_sizes()[0] == 0
        window._on_toggle_channel_browser(False)
        assert window._main_sizes()[0] >= 200

        window._portrait_override = False
        window._update_orientation()
        qapp.processEvents()
        assert splitter.orientation() == Qt.Horizontal and splitter.widget(0) is window.controls_panel
        assert window.controls_panel.minimumWidth() >= 260
    finally:
        window.close()


def test_saved_main_sizes_only_apply_in_the_same_orientation(qapp):
    window = _window(qapp)
    try:
        window._restore_main_sizes({"main_sizes": [300, 800], "main_portrait": True})
        window._portrait_override = True
        window._update_orientation()
        before = window._main_sizes()
        window._restore_main_sizes({"main_sizes": [300, 800], "main_portrait": False})
        assert window._main_sizes() == before
    finally:
        window.close()


def test_cross_section_off_and_on_again_keeps_the_heatmap_full_size(qapp):
    import numpy as np
    from app.core.data_model import Grid2DData
    from app.gui.plot_2d_widget import Plot2DWidget

    x = np.linspace(0, 9, 60)
    y = np.linspace(100, 104, 40)
    z = np.outer(y - 100, np.exp(-((x - 4.5) / 1.2) ** 2))
    grid = Grid2DData(x_name="X", x_unit="Hz", x_values=x, y_name="Y", y_unit="A", y_values=y,
                      z_name="S21", z_unit=None, z_values=z, transform="magnitude", fixed_dims={})
    widget = Plot2DWidget()
    widget.resize(1000, 700)
    widget.show()
    widget.plot(grid)
    qapp.processEvents()
    try:
        full = widget.plot_item.sceneBoundingRect().width()
        for enabled in (True, False, True, False):
            widget.set_cross_section_enabled(enabled)
            qapp.processEvents()
            width = widget.plot_item.sceneBoundingRect().width()
            assert width > 400, (enabled, width)
        assert abs(width - full) < 5
    finally:
        widget.close()
