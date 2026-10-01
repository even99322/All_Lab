"""v0.18A Qt Graphs renderer pieces that do not need a native RHI window."""

from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture(scope="module")
def qapp():
    import os
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_profiles_set_idle_budgets_and_full_resolution_is_capped():
    from app.visualization3d.graphs_renderer import (
        FULL_RESOLUTION_LIMIT, PROFILES, resolve_graphs_policy,
    )

    small = resolve_graphs_policy(501, 855, "Auto", "balanced")
    assert small.max_vertices is None and small.effective.value == "Auto"
    flux = resolve_graphs_policy(399, 10_001, "Auto", "balanced")
    assert flux.max_vertices == PROFILES["balanced"][0] and flux.warning
    assert resolve_graphs_policy(399, 10_001, "Auto", "economy").max_vertices == PROFILES["economy"][0]
    assert resolve_graphs_policy(399, 10_001, "Auto", "quality").max_vertices is None
    assert resolve_graphs_policy(399, 10_001, "Full Resolution").max_vertices is None
    huge = resolve_graphs_policy(2_000, 5_000, "Full Resolution")
    assert huge.max_vertices == FULL_RESOLUTION_LIMIT and huge.warning
    assert resolve_graphs_policy(399, 10_001, "Performance").max_vertices == 50_000
    assert resolve_graphs_policy(399, 10_001, "Auto", "unknown-profile").max_vertices == PROFILES["balanced"][0]


def test_opacity_changes_alpha_only_and_nan_is_transparent(qapp):
    from app.visualization3d.graphs_renderer import colormap_rgba

    values = np.array([[-1.0, 0.0, 1.0, np.nan]])
    opaque = colormap_rgba(values, "LabLog BWR", (-1.0, 1.0), 1.0)
    faded = colormap_rgba(values, "LabLog BWR", (-1.0, 1.0), 0.4)
    assert np.array_equal(opaque[..., :3], faded[..., :3])   # scientific colors unchanged
    assert faded[0, 0, 3] == round(255 * 0.4)
    assert faded[0, 3, 3] == 0 and opaque[0, 3, 3] == 0


def test_height_map_round_trips_with_z_scale():
    from app.visualization3d.graphs_renderer import _HeightMap

    height = _HeightMap(-40.0, -10.0, 2.0)
    assert height.scene_range == (-0.5, 1.5)
    for value in (-40.0, -25.0, -10.0):
        assert height.physical(float(height.to_scene(value))) == pytest.approx(value)
    flat = _HeightMap(3.0, 3.0, 1.0)
    assert flat.physical(float(flat.to_scene(3.0))) == pytest.approx(3.0)


def test_offscreen_uses_previous_renderers(qapp):
    from app.visualization3d.graphs_renderer import graphs_available

    assert graphs_available() is False


def test_uploaded_buffers_stay_referenced(qapp):
    """PySide6 6.11 resetArrayNp does not own the buffer (native crash otherwise)."""
    from PySide6.QtGraphs import QSurface3DSeries, QSurfaceDataProxy
    from app.visualization3d.graphs_renderer import reset_surface

    series = QSurface3DSeries(QSurfaceDataProxy())
    for step in range(4):
        reset_surface(series, 0.0, 1.0, 0.0, 1.0, np.full((3, 4), step, np.float64))
    held = series._lablog_buffers
    assert len(held) == 2 and all(buffer.dtype == np.float32 for buffer in held)
    assert held[-1][0, 0] == 3.0
    assert series.dataProxy().rowCount() == 3 and series.dataProxy().columnCount() == 4
