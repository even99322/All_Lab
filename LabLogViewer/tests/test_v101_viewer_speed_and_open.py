"""1.0.1: a data file from Lab Control opens only its Viewer; faster trace switching (cached axes, reused curve,
coalesced redraws while stepping quickly); large 2D maps rebuild on a worker thread."""

from __future__ import annotations

import os
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.mark_store import MarkStore
from app.core.named_view_store import NamedViewStore
from app.core.viewer_display_state_store import ViewerDisplayStateStore
from tests.test_nd_slice_synthetic import _build_synthetic_labber_file


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def _viewer(tmp_path, n_entries=6, n_points=51):
    from app.gui.main_window import MainWindow

    path = tmp_path / "t.hdf5"
    _build_synthetic_labber_file(path, [("Current", "A", np.linspace(0, 1, n_entries))],
                                 np.linspace(4e9, 6e9, n_points))
    w = MainWindow(named_view_store=NamedViewStore(tmp_path / "v.json"), mark_store=MarkStore(tmp_path / "m.json"),
                   viewer_display_state_store=ViewerDisplayStateStore(tmp_path / "d.json"))
    w.resize(1200, 800)
    w.show()
    w.open_file(str(path))
    return w, path


def test_axes_cached_and_curve_reused(qapp, tmp_path):
    from PySide6.QtWidgets import QGraphicsItem

    w, _ = _viewer(tmp_path)
    w.mode_combo.setCurrentIndex(0)
    qapp.processEvents()
    item = w.plot_widget.plot_widget.getPlotItem()
    assert item.getAxis("left").cacheMode() == QGraphicsItem.CacheMode.DeviceCoordinateCache
    curve = next(iter(w.plot_widget._curves.values()))
    w.log_entries.select_row(3)
    qapp.processEvents()
    assert list(w.plot_widget._curves.values()) == [curve]          # same curve object, new trace
    assert 3 in w.plot_widget._curves
    w.close()


def test_rapid_steps_draw_only_the_last_trace(qapp, tmp_path, monkeypatch):
    w, _ = _viewer(tmp_path)
    w.mode_combo.setCurrentIndex(0)
    qapp.processEvents()
    draws = []
    real = w.update_plot
    monkeypatch.setattr(w, "update_plot", lambda *a, **k: (draws.append(w.log_entries.current_row()),
                                                           time.sleep(0.05), real(*a, **k)))   # a slow redraw
    for row in (1, 2, 3, 4):
        w.log_entries.select_row(row)                                  # no event processing in between
    assert len(draws) == 1                                             # the first one; the rest wait
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and (not draws or draws[-1] != 4):
        qapp.processEvents()
        time.sleep(0.01)
    assert draws[-1] == 4 and len(draws) <= 2
    w.close()


def test_open_path_file_opens_viewer_without_raising_browser(qapp, tmp_path, monkeypatch):
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    path = tmp_path / "x.hdf5"
    _build_synthetic_labber_file(path, [("Current", "A", np.linspace(0, 1, 3))], np.linspace(4e9, 6e9, 11))
    browser = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    browser.showMinimized()
    opened = []
    monkeypatch.setattr(browser, "_open_viewer_for", lambda entry, *a: opened.append(entry) or None)
    browser.open_path(str(path))
    assert opened and opened[0].absolute_path == str(path)
    assert browser.isMinimized()                                       # the Browser stays where it was
    browser.close()


def test_heavy_grids_are_built_on_a_worker(qapp, tmp_path):
    w, _ = _viewer(tmp_path, n_entries=60, n_points=10_001)            # 600 k points
    w.mode_combo.setCurrentIndex(1)
    assert w.wait_for_pending_grid(30)
    assert w._grid_is_heavy(w.z_combo_2d.currentText())
    w.plot_config_2d.smooth_spin.setValue(3)
    w.plot_config_2d.changed.emit()
    assert w._grid_pending                                             # not built on the window's thread
    assert w.wait_for_pending_grid(30)
    assert w.plot_2d_widget._grid is not None
    w.close()
