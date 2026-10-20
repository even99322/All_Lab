from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.data_model import Grid2DData
from app.gui.cross_section_window import CrossSectionWindow, cut_arrays, intensity_histogram
from tests.real_data import FLUX_FILE


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def _grid():
    x = np.linspace(0.0, 9.0, 10)
    y = np.linspace(100.0, 104.0, 5)
    z = y[:, None] * 10 + x[None, :]
    return Grid2DData(x_name="X", x_unit="Hz", x_values=x, y_name="Y", y_unit="A", y_values=y,
                      z_name="S21", z_unit=None, z_values=z, transform="magnitude", fixed_dims={})


def test_cut_arrays_slice_rows_and_columns():
    grid = _grid()
    xs, x_cut, ys, y_cut, row, column = cut_arrays(grid, 3.2, 102.1)
    assert (row, column) == (2, 3)
    assert np.allclose(x_cut, grid.z_values[2]) and np.allclose(y_cut, grid.z_values[:, 3])
    edges, counts = intensity_histogram(grid.z_values)
    assert counts.sum() == grid.z_values.size and edges.size == counts.size + 1


def test_window_lines_move_only_when_dragged_and_cuts_follow(qapp):
    window = CrossSectionWindow()
    window.set_grid(_grid())
    start = window.line_position()
    assert start == (4.5, 102.0)
    window.v_line.setValue(7.0)
    window.h_line.setValue(101.0)
    assert np.allclose(window.x_curve.getData()[1], _grid().z_values[1])
    values, ys = window.y_curve.getData()
    assert np.allclose(values, _grid().z_values[:, 7]) and np.allclose(ys, _grid().y_values)
    window.set_grid(_grid())
    assert window.line_position() == (7.0, 101.0)
    window.full_range()
    assert window.contrast() == (float(_grid().z_values.min()), float(_grid().z_values.max()))
    window.set_contrast(1010.0, 1030.0)
    assert window.contrast() == (1010.0, 1030.0)
    window.close()


@pytest.mark.skipif(not FLUX_FILE.exists(), reason="Real sample file is unavailable.")
def test_viewer_opens_cross_section_for_2d_map(qapp, tmp_path):
    from app.core.mark_store import MarkStore
    from app.core.named_view_store import NamedViewStore
    from app.core.viewer_display_state_store import ViewerDisplayStateStore
    from app.gui.main_window import MainWindow

    window = MainWindow(
        named_view_store=NamedViewStore(tmp_path / "v.json"), mark_store=MarkStore(tmp_path / "m.json"),
        viewer_display_state_store=ViewerDisplayStateStore(tmp_path / "d.json"),
    )
    window.open_file(str(FLUX_FILE))
    window.mode_combo.setCurrentIndex(1)
    window._show_cross_section_window()
    cross = window._cross_section_window
    assert cross.grid is not None and cross.grid.z_values.shape == window.plot_2d_widget._grid.z_values.shape
    window.close()


def test_embedded_cross_section_in_plot2d_widget(qapp):
    from app.gui.plot_2d_widget import Plot2DWidget

    widget = Plot2DWidget()
    widget.resize(900, 700)
    widget.show()
    widget.plot(_grid())
    widget.set_cross_section_enabled(True)
    cross = widget._cross
    assert widget.cross_section_enabled and cross.line_position() == (4.5, 102.0)
    cross.set_line_position(7.0, 101.0)
    values, ys = cross.y_curve.getData()
    assert np.allclose(values, _grid().z_values[:, 7]) and not cross.y_cut_plot.getViewBox().xInverted()  # v1.0.9: Labber
    widget.plot(_grid())
    assert cross.line_position() == (7.0, 101.0)
    seen = []
    widget.color_range_changed.connect(lambda low, high: seen.append((low, high)))
    cross.set_contrast(1010.0, 1030.0)
    assert seen[-1] == (1010.0, 1030.0) and (widget._z_min, widget._z_max) == (1010.0, 1030.0)
    widget.set_cross_section_enabled(False)
    assert not widget.cross_section_enabled
    widget.close()


def test_histogram_view_follows_contrast_pans_and_resets(qapp):
    """v1.0.9: the histogram is vertical (value axis up, like the map), left of the map."""
    window = CrossSectionWindow()
    window.set_grid(_grid())
    window.set_contrast(1010.0, 1012.0)
    vb = window.cross.hist_plot.getViewBox()
    low, high = vb.viewRange()[1]
    assert low < 1010.0 < 1012.0 < high and high - low < 20.0
    assert vb.state["mouseEnabled"][0] is False and vb.state["mouseEnabled"][1] is True
    vb.setYRange(5000.0, 5010.0, padding=0)
    assert vb.viewRange()[1][0] > 4999.0
    vb.mouseDoubleClickEvent(_Ev())
    low, high = vb.viewRange()[1]
    assert low < 1010.0 < 1012.0 < high
    # dragging the region must not rescale the view under the cursor
    window.cross._fit_hist_view()
    before = vb.viewRange()[1]
    window.cross.hist_region.setRegion((1010.2, 1011.2))
    window.cross._on_region_changed()
    assert vb.viewRange()[1] == before
    assert window.cross.contrast() == (1010.2, 1011.2)
    window.close()


class _Ev:
    def accept(self):
        pass
