"""v0.12H integration regressions for identity ownership and lifecycle."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.axis_preset_store import AxisPresetStore
from app.core.mark_store import MarkStore
from app.core.overlay_store import OverlayStore
from app.gui.main_window import MainWindow
from tests.real_data import BIG_FILE

try:
    from PySide6.QtWidgets import QApplication
    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False


pytestmark = pytest.mark.skipif(not PYSIDE_AVAILABLE, reason="PySide6 unavailable")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(tmp_path: Path) -> MainWindow:
    return MainWindow(
        axis_preset_store=AxisPresetStore(tmp_path / "axis.json"),
        overlay_store=OverlayStore(tmp_path / "overlays.json"),
        mark_store=MarkStore(tmp_path / "marks.json"),
    )


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real Labber fixture unavailable")
def test_legacy_raw_path_mark_context_restores_by_resolved_data_identity(qapp, tmp_path):
    """A path alias must not strand marks written before context normalization."""
    canonical = BIG_FILE.resolve()
    alias_dir = tmp_path / "aliases"
    alias_dir.mkdir()
    alias = alias_dir / canonical.name
    try:
        alias.symlink_to(canonical)
    except OSError as error:
        pytest.skip(f"Cannot create path alias on this platform: {error}")

    first = _window(tmp_path)
    first.open_file(str(canonical))
    manager = first._mark_managers[0]
    point = manager.add_at_trace_position(100)
    data_key = first._current_data_key()
    canonical_context = manager.context_key
    first._persist_mark_manager(manager)
    state = first.mark_store.get(data_key, 1, canonical_context)
    first.mark_store.clear(data_key, 1, canonical_context)
    first.close()

    legacy_context = (str(alias), *canonical_context[1:])
    legacy_store = MarkStore(tmp_path / "marks.json")
    legacy_store.save(data_key, 1, legacy_context, state)

    restored = _window(tmp_path)
    restored.open_file(str(alias))
    qapp.processEvents()
    recovered = restored._mark_managers[0].object(point.object_id)
    assert restored._current_data_key() == str(canonical)
    assert recovered is not None
    assert recovered.sample_index == point.sample_index
    assert restored._mark_managers[0].context_key[0] == str(canonical)
    assert restored.mark_store.get(data_key, 1, restored._mark_managers[0].context_key) is not None
    restored.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real 2D fixture unavailable")
def test_repeated_cut_updates_reuse_cached_grid_and_cut_windows(qapp):
    window = MainWindow()
    window.open_file(str(BIG_FILE))
    window.mode_combo.setCurrentIndex(1)
    window._show_cut_window("x")
    window._show_cut_window("y")
    qapp.processEvents()

    source = window.plot_2d_widget
    grid = source._grid
    x_window, y_window = window._cut_windows["x"], window._cut_windows["y"]
    x_curve, y_curve = x_window.panel.plot_widget._curve, y_window.panel.plot_widget._curve
    cache_before = dict(window.cached.cache.stats())

    for ix, iy in zip(
        np.linspace(0, len(grid.x_values) - 1, 80, dtype=int),
        np.linspace(0, len(grid.y_values) - 1, 80, dtype=int),
    ):
        source.set_crosshair_position(grid.x_values[ix], grid.y_values[iy])

    assert window.cached.cache.stats() == cache_before
    assert x_window.grid is grid and y_window.grid is grid
    assert x_window.panel.plot_widget._curve is x_curve
    assert y_window.panel.plot_widget._curve is y_curve

    x_window.close()
    assert y_window.isVisible()
    window._show_cut_window("x")
    assert x_window.panel.plot_widget._curve is x_curve
    window.close()
    qapp.processEvents()
    assert not x_window.isVisible() and not y_window.isVisible()
