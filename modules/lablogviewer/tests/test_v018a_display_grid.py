"""v0.18A evenly spaced, peak-preserving display grids for Qt Graphs upload."""

from __future__ import annotations

import numpy as np
import pytest

from app.visualization3d.display_grid import (
    build_display_grid, display_shape, select_waterfall_traces,
)


def test_uniform_grid_within_budget_is_identity():
    x = np.linspace(5.0e9, 5.01e9, 301)
    y = np.linspace(0.1, 0.2, 41)
    z = np.random.default_rng(1).normal(size=(41, 301))
    grid = build_display_grid(x, y, z, max_vertices=None)
    assert grid.exact
    assert np.array_equal(grid.heights, z)
    assert np.array_equal(grid.x_centers, x)
    assert np.array_equal(grid.source_rows[:, 0], np.arange(41))
    assert np.array_equal(grid.source_columns[0], np.arange(301))


def test_decimation_keeps_narrow_dip_depth_and_real_source_samples():
    x = np.linspace(0.0, 1.0, 10_001)
    y = np.linspace(0.0, 1.0, 399)
    z = np.zeros((399, 10_001))
    z[:, 5_000] = -30.0                         # one-sample-wide resonance dip
    grid = build_display_grid(x, y, z, max_vertices=150_000)
    assert grid.vertex_count <= 150_000
    assert grid.heights.min() == -30.0            # depth survives decimation
    rows, columns = np.nonzero(grid.heights == -30.0)
    assert np.all(grid.source_columns[rows, columns] == 5_000)
    # Every display value is a real source sample at its recorded index.
    assert np.array_equal(grid.heights, z[grid.source_rows, grid.source_columns])


def test_uneven_axis_is_resampled_onto_even_centers():
    x = np.concatenate([np.linspace(0, 1, 50), np.linspace(1.01, 10, 20)])
    y = np.array([0.0, 1.0, 3.0])
    z = np.outer(np.arange(3), np.ones(x.size)) + x
    grid = build_display_grid(x, y, z, max_vertices=None)
    assert not grid.exact
    assert np.allclose(np.diff(grid.x_centers), np.diff(grid.x_centers)[0])
    assert np.allclose(np.diff(grid.y_centers), np.diff(grid.y_centers)[0])
    assert np.array_equal(grid.heights, z[grid.source_rows, grid.source_columns])


def test_descending_axis_and_colors_follow_the_same_samples():
    x = np.linspace(10, 0, 20)
    y = np.linspace(0, 1, 5)
    z = np.tile(x, (5, 1))
    color = -z
    grid = build_display_grid(x, y, z, color, max_vertices=None)
    assert np.all(np.diff(grid.x_centers) > 0)
    assert np.array_equal(grid.colors, -grid.heights)


def test_display_shape_keeps_short_sweep_axis_when_possible():
    assert display_shape(399, 10_001, 1_000_000) == (399, 2506)
    rows, columns = display_shape(399, 10_001, 150_000)
    assert rows * columns <= 150_000 and rows < 399
    assert display_shape(100, 100, None) == (100, 100)
    with pytest.raises(ValueError):
        display_shape(100, 100, 3)


def test_waterfall_trace_selection_spans_first_and_last():
    picked = select_waterfall_traces(855, 64)
    assert picked[0] == 0 and picked[-1] == 854 and len(picked) <= 64
    assert np.array_equal(select_waterfall_traces(10, 64), np.arange(10))
