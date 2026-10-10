from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest


def _grid(z):
    rows, columns = z.shape
    return SimpleNamespace(
        x_values=np.linspace(9.0, 1.0, columns),
        y_values=np.linspace(-3.0, 4.0, rows),
        z_values=z,
        x_name="Frequency", x_unit="Hz", y_name="Current", y_unit="A",
        z_name="S21", z_unit="dB", transform="magnitude_db",
    )


def test_qt_surface_is_filled_without_default_black_wireframe():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtDataVisualization import QSurface3DSeries

    from app.visualization3d.renderer import configure_filled_surface

    app = QApplication.instance() or QApplication([])
    series = QSurface3DSeries()
    assert series.drawMode() == QSurface3DSeries.DrawFlag.DrawSurfaceAndWireframe
    configure_filled_surface(series)
    assert series.drawMode() == QSurface3DSeries.DrawFlag.DrawSurface
    assert not (series.drawMode() & QSurface3DSeries.DrawFlag.DrawWireframe)
    assert app is not None


@pytest.mark.parametrize("shape", [(50, 50), (100, 100), (287, 251), (857, 501)])
def test_render_isolation_sizes_retain_shared_bwr_mapping(shape):
    from app.visualization3d.renderer import build_surface_gradient, map_surface_colors

    low, high = -35.0, -0.5
    samples = np.array([[low, (low + high) / 2, high]])
    rgba = map_surface_colors(samples, "LabLog BWR", (low, high))[0]
    gradient = build_surface_gradient("LabLog BWR", (low, high), (low, high))
    stops = gradient.stops()
    assert len(stops) == 257
    assert len({tuple(pixel) for pixel in rgba}) == 3
    for index, stop in zip((0, 1, 2), (stops[0], stops[128], stops[-1])):
        assert rgba[index, :3].tolist() == [stop[1].red(), stop[1].green(), stop[1].blue()]
    assert shape[0] * shape[1] >= 2500


def test_feature_lod_preserves_narrow_peak_dip_extrema_and_original_orientation():
    from app.visualization3d.data import prepare_surface_grid

    original = np.sin(np.linspace(0, 40, 1001))[None, :] + np.cos(
        np.linspace(0, 9, 120)
    )[:, None]
    original = original.copy()
    original[:, 503] -= 50.0
    original[67, 417] += 80.0
    snapshot = original.copy()
    prepared = prepare_surface_grid(_grid(original), max_vertices=4_000)
    assert prepared.vertex_count <= 4_000
    assert 503 in prepared.source_column_indices
    assert 67 in prepared.source_row_indices
    assert 417 in prepared.source_column_indices
    assert np.min(prepared.z_values) == np.min(original)
    assert np.max(prepared.z_values) == np.max(original)
    assert np.array_equal(original, snapshot)
    for row, column in [(0, 0), (5, 7), (-1, -1)]:
        assert prepared.z_values[row, column] == original[
            prepared.source_row_indices[row], prepared.source_column_indices[column]
        ]


def test_lod_tiny_budget_stays_bounded_and_axes_remain_ordered():
    from app.visualization3d.lod import feature_preserving_indices

    field = np.zeros((20, 30))
    field[10, 15] = 10
    rows, columns = feature_preserving_indices(field, 4)
    assert len(rows) * len(columns) <= 4
    assert np.all(np.diff(rows) > 0) and np.all(np.diff(columns) > 0)
    assert rows[0] == columns[0] == 0
    assert rows[-1] == 19 and columns[-1] == 29


def test_bounded_cache_evicts_and_refuses_oversize_entry():
    from app.visualization3d.cache import BoundedSurfaceCache, surface_grid_bytes
    from app.visualization3d.data import prepare_surface_grid

    grid = prepare_surface_grid(_grid(np.arange(100, dtype=float).reshape(10, 10)))
    size = surface_grid_bytes(grid)
    cache = BoundedSurfaceCache(max_bytes=size + 1)
    assert cache.put("first", grid)
    assert cache.put("second", grid)
    assert len(cache) == 1
    assert cache.get("first") is None
    assert cache.get("second") is grid
    assert cache.current_bytes <= cache.max_bytes
    small = BoundedSurfaceCache(max_bytes=size - 1)
    assert not small.put("large", grid)
    assert len(small) == 0


def test_stale_worker_result_is_rejected_without_touching_cache():
    from app.visualization3d.cache import BoundedSurfaceCache
    from app.visualization3d.renderer import SurfaceRenderer

    key = ("data-A", 50_000)
    fake = SimpleNamespace(
        _lod_pending={key}, _lod_generation=3, _source_key="data-B",
        _lod_cache=BoundedSurfaceCache(),
    )
    SurfaceRenderer._on_lod_ready(fake, 2, "data-A", 50_000, object())
    assert not fake._lod_pending
    assert len(fake._lod_cache) == 0


def test_initial_worker_rejects_old_request_and_applies_only_current_grid():
    from app.visualization3d.renderer import SurfaceRenderer, _PreparedResult

    source = _grid(np.ones((3, 3)))
    calls = []
    signal = SimpleNamespace(emit=lambda value: calls.append(value))
    fake = SimpleNamespace(
        _initial_request=(5, ("initial", 9), source, source, "LabLog BWR", None, "Auto"),
        set_grid=lambda *args, **kwargs: calls.append(kwargs["_prepared"]) or kwargs["_prepared"],
        surface_ready=signal, surface_failed=signal,
    )
    SurfaceRenderer._on_initial_ready(fake, 4, ("initial", 9), 50_000, "old")
    assert calls == []
    prepared = _PreparedResult("current", 42.0)
    SurfaceRenderer._on_initial_ready(fake, 5, ("initial", 9), 50_000, prepared)
    assert calls == ["current", "current"]
    assert fake._initial_request is None
    assert fake._last_grid_prepare_ms == 42.0


def test_interaction_lod_switches_prepared_level_and_restores_idle():
    from app.visualization3d.cache import BoundedSurfaceCache
    from app.visualization3d.data import prepare_surface_grid
    from app.visualization3d.policy import interaction_vertex_budget
    from app.visualization3d.renderer import SurfaceRenderer

    source = _grid(np.arange(300 * 300, dtype=float).reshape(300, 300))
    full = prepare_surface_grid(source)
    budget = interaction_vertex_budget(90_000, (1000, 700), 100, 60)
    reduced = prepare_surface_grid(source, max_vertices=budget)
    cache = BoundedSurfaceCache()
    cache.put(("source", None), full)
    cache.put(("source", budget), reduced)
    applied = []
    timer = SimpleNamespace(stop=lambda: None, start=lambda: applied.append("timer"))
    camera = SimpleNamespace(zoomLevel=lambda: 100)
    graph = SimpleNamespace(scene=lambda: SimpleNamespace(activeCamera=lambda: camera), currentFps=lambda: 60)
    fake = SimpleNamespace(
        _grid=full, _source_key="source", _lod_cache=cache, _idle_timer=timer,
        _interaction_active=False, _active_budget=None, _idle_budget=None,
        container=SimpleNamespace(width=lambda: 1000, height=lambda: 700),
        graph=graph, _apply_prepared_grid=lambda grid: applied.append(grid),
        _publish_diagnostics=lambda: None,
    )
    SurfaceRenderer._interaction_start(fake)
    assert fake._interaction_active and fake._active_budget == budget
    assert applied == [reduced]
    SurfaceRenderer._interaction_end(fake)
    assert applied[-1] == "timer"
    SurfaceRenderer._restore_idle_lod(fake)
    assert not fake._interaction_active and fake._active_budget is None
    assert applied[-1] is full
