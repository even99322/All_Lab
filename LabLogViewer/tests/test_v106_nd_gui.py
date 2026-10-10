from __future__ import annotations

import os

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


def test_three_sweep_dimensions_show_in_2d_with_third_dimension_operation(qapp, tmp_path):
    from app.gui.main_window import MainWindow

    path = tmp_path / "nd.hdf5"
    axes = [("Field", "T", np.linspace(0, 1, 3)), ("Current", "A", np.linspace(0, 2, 4))]
    _build_synthetic_labber_file(path, axes, np.linspace(1e9, 2e9, 5))
    window = MainWindow(named_view_store=NamedViewStore(tmp_path / "v.json"), mark_store=MarkStore(tmp_path / "m.json"),
                        viewer_display_state_store=ViewerDisplayStateStore(tmp_path / "d.json"))
    window.resize(1200, 800)
    window.show()
    window.open_file(str(path))
    window.mode_combo.setCurrentIndex(1)
    qapp.processEvents()
    panel = window.plot_config_2d
    assert panel.third_channel_combo.count() >= 2          # every sweep channel is offered
    grid = window.plot_2d_widget._grid
    assert grid is not None and grid.z_values.ndim == 2
    panel.third_operation_combo.setCurrentText("y-<y>")
    panel.changed.emit()
    qapp.processEvents()
    shifted = window.plot_2d_widget._grid.z_values
    assert np.allclose(np.nanmean(shifted, axis=0), 0.0, atol=1e-9)
    window.close()
