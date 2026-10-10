"""v1.0.4: 2D Swap X / Y, icon-only Traces buttons that stay visible, 75 % CPU default."""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.real_data import BIG_FILE

needs_data = pytest.mark.skipif(not BIG_FILE.exists(), reason="Real sample file not present.")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@needs_data
def test_swap_xy_transposes_the_2d_plot_and_back(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))
    win.mode_combo.setCurrentIndex(1)
    first = win.plot_2d_widget._grid
    assert (first.x_name, first.y_name) == ("Frequency", "Average Current")
    win.swap_axes_2d_button.click()
    swapped = win.plot_2d_widget._grid
    assert (swapped.x_name, swapped.y_name) == ("Average Current", "Frequency")
    assert (win.x_combo_2d.currentText(), win.y_combo_2d.currentText()) == ("Average Current", "Frequency")
    assert np.allclose(np.asarray(swapped.z_values), np.asarray(first.z_values).T, equal_nan=True)
    win.swap_axes_2d_button.click()
    back = win.plot_2d_widget._grid
    assert (back.x_name, back.y_name) == ("Frequency", "Average Current")
    win.close()


@needs_data
def test_traces_action_buttons_are_icons_with_tooltips_and_visible(qapp):
    from PySide6.QtWidgets import QToolButton

    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.resize(1300, 700)
    win.open_file(str(BIG_FILE))
    win.show()
    qapp.processEvents()
    for button in (win.clear_reference_button, win.remove_trace_button, win.save_overlay_button,
                   win.rename_overlay_button, win.delete_overlay_button, win.load_overlay_button):
        assert isinstance(button, QToolButton)
        assert not button.icon().isNull()
        assert button.toolTip()
        assert button.width() >= 28
    tree_top = win.multi_trace_tree.mapTo(win.multi_trace_content, win.multi_trace_tree.rect().topLeft()).y()
    assert win.save_overlay_button.mapTo(win.multi_trace_content, win.save_overlay_button.rect().topLeft()).y() < tree_top
    win.close()


def test_default_cpu_threads_is_75_percent_of_logical_cpus(tmp_path):
    from app.settings.store import SettingsStore

    expected = max(1, int(SettingsStore.available_cpu_threads() * 0.75))
    assert SettingsStore.default_cpu_threads() == expected
    store = SettingsStore(tmp_path / "settings.json")
    assert store.cpu_threads() == expected
    store.set_cpu_threads(1)
    assert store.cpu_threads() == 1


def test_rectangle_range_label_matches_reference_layout(qapp):
    import pyqtgraph as pg

    from app.core.mark_model import Annotation, RECT_RANGE
    from app.gui.mark_overlay import MarkOverlay

    plot = pg.PlotWidget()
    overlay = MarkOverlay(plot.getPlotItem(), plot)
    rect = Annotation(annotation_type=RECT_RANGE, number=1, x=5.031195e9, x2=5.033002e9, y=-59.27, y2=-43.16,
                      x_unit="Hz", y_unit="dB")
    top = overlay._rect_text(rect)
    assert "y: -43.16 dB, <y>: -51.215 dB, dy: 16.11 dB" in top
    assert "x: 5.033002 GHz, <x>: 5.032099 GHz, dx: 1.807 MHz" in top
    assert overlay._rect_corner_text(rect) == "y: -59.27 dB\nx: 5.031195 GHz"
