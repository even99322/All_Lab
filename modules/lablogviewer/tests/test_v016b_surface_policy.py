from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest


def _grid(x, y, z, *, name="VNA - S21", transform="magnitude_db", unit="dB"):
    return SimpleNamespace(
        x_values=np.asarray(x, dtype=float), y_values=np.asarray(y, dtype=float),
        z_values=np.asarray(z), x_name="Frequency", x_unit="Hz",
        y_name="Average Current", y_unit="A", z_name=name, z_unit=unit,
        transform=transform,
    )


def test_auto_render_policy_keeps_standard_grids_full_and_reduces_large_grids():
    from app.visualization3d.policy import RenderingPolicy, resolve_rendering_policy

    standard = resolve_rendering_policy(855, 501)
    assert standard.source_points == 855 * 501 == 428_355
    assert standard.effective is RenderingPolicy.AUTO
    assert standard.max_vertices is None

    boundary = resolve_rendering_policy(1000, 1000, "Auto")
    assert boundary.max_vertices is None
    large = resolve_rendering_policy(1001, 1000, "Auto")
    assert large.effective is RenderingPolicy.ADAPTIVE_LOD
    assert large.max_vertices == 100_000
    assert large.warning


def test_low_data_can_choose_adaptive_performance_and_full_resolution():
    from app.visualization3d.policy import RenderingPolicy, resolve_rendering_policy

    assert resolve_rendering_policy(20, 30, "Adaptive LOD").max_vertices == 100_000
    assert resolve_rendering_policy(20, 30, "Performance").max_vertices == 50_000
    assert resolve_rendering_policy(2000, 2000, "Full Resolution").max_vertices is None
    assert resolve_rendering_policy(2000, 2000, "Full Resolution").warning


def test_height_and_color_are_separate_and_keep_asymmetric_z_y_x_correspondence():
    from app.visualization3d.data import prepare_surface_grid, prepare_surface_mesh

    height = np.array([[1.0, 3.0, 8.0], [4.0, 7.0, 9.0]])
    color = np.array([[80.0, 50.0, 20.0], [10.0, 40.0, 70.0]])
    height_grid = _grid([5.0, 7.0, 12.0], [0.2, 0.9], height)
    color_grid = _grid([5.0, 7.0, 12.0], [0.2, 0.9], color,
                       name="VNA - S11", transform="phase_deg", unit="deg")

    prepared = prepare_surface_grid(height_grid, color_grid=color_grid)
    assert prepared.shape == (2, 3)
    assert np.array_equal(prepared.z_values, height)
    assert np.array_equal(prepared.color_values, color)
    assert prepared.color_name == "VNA - S11"
    assert prepared.color_transform == "phase_deg"
    mesh = prepare_surface_mesh(prepared)
    assert mesh.coordinates.shape == (2, 3, 3)
    assert np.allclose(mesh.coordinates[1, 0], [0.0, 3.0 / 8.0, 1.0])
    assert np.allclose(mesh.coordinates[0, 2], [1.0, 7.0 / 8.0, 0.0])


def test_z_scale_changes_display_geometry_only_and_preserves_physical_ticks():
    from app.visualization3d.data import prepare_surface_grid, prepare_surface_mesh

    source_z = np.array([[10.0, 20.0], [30.0, 40.0]])
    source_color = np.array([[3.0, 2.0], [1.0, 0.0]])
    grid = prepare_surface_grid(
        _grid([1, 2], [3, 4], source_z),
        color_grid=_grid([1, 2], [3, 4], source_color, name="Color"),
    )
    normal = prepare_surface_mesh(grid)
    exaggerated = prepare_surface_mesh(grid, z_scale=4.0)

    assert not np.allclose(normal.coordinates[:, :, 1], exaggerated.coordinates[:, :, 1])
    assert exaggerated.z_axis.physical_value(exaggerated.coordinates[0, 0, 1]) == pytest.approx(10.0)
    assert exaggerated.z_axis.physical_value(exaggerated.coordinates[1, 1, 1]) == pytest.approx(40.0)
    assert np.array_equal(grid.z_values, source_z)
    assert np.array_equal(grid.color_values, source_color)
    assert np.array_equal(source_z, [[10, 20], [30, 40]])
    with pytest.raises(ValueError, match="between 0.1x and 10x"):
        prepare_surface_mesh(grid, z_scale=10.1)


def test_reversed_nonuniform_axes_preserve_original_row_and_column_order():
    from app.visualization3d.data import prepare_surface_grid, prepare_surface_mesh

    z = np.array([[101.0, 103.0, 107.0], [211.0, 223.0, 227.0]])
    grid = prepare_surface_grid(_grid([9.0, 4.0, 1.0], [3.0, -2.0], z))
    mesh = prepare_surface_mesh(grid)
    assert grid.x_values.tolist() == [9.0, 4.0, 1.0]
    assert grid.y_values.tolist() == [3.0, -2.0]
    assert grid.z_values[0, 2] == 107.0
    assert grid.z_values[1, 0] == 211.0
    assert mesh.coordinates[0, 0, 0] == 1.0
    assert mesh.coordinates[0, 2, 0] == 0.0
    assert mesh.coordinates[0, 0, 2] == 1.0
    assert mesh.coordinates[1, 0, 2] == 0.0


def test_color_source_requires_identical_physical_axes_and_grid_shape():
    from app.visualization3d.data import prepare_surface_grid

    height = _grid([1, 2, 3], [4, 5], np.ones((2, 3)))
    mismatched = _grid([1, 2, 4], [4, 5], np.ones((2, 3)), name="VNA - S11")
    with pytest.raises(ValueError, match="same X/Y coordinates"):
        prepare_surface_grid(height, color_grid=mismatched)


def test_surface_colormap_uses_scientific_scalar_limits_and_transparency():
    from app.gui.plot_2d_widget import DEFAULT_COLORMAP
    from app.visualization3d.renderer import map_surface_colors

    values = np.array([[0.0, 0.5, 1.0], [np.nan, 0.25, 0.75]])
    rgba = map_surface_colors(values, DEFAULT_COLORMAP, (0.0, 1.0))
    assert rgba.shape == (2, 3, 4)
    assert np.array_equal(rgba[0, 1, :3], [255, 255, 255])
    assert not np.array_equal(rgba[0, 0, :3], rgba[0, 2, :3])
    assert rgba[1, 0, 3] == 0
    with pytest.raises(ValueError, match="Minimum < Maximum"):
        map_surface_colors(values, DEFAULT_COLORMAP, (1.0, 0.0))


def test_surface_camera_and_projection_apis_exist_in_installed_qt(qapp):
    from PySide6.QtDataVisualization import Q3DCamera, Q3DSurface

    assert hasattr(Q3DCamera.CameraPreset, "CameraPresetDirectlyAbove")
    assert hasattr(Q3DCamera.CameraPreset, "CameraPresetFront")
    assert hasattr(Q3DCamera.CameraPreset, "CameraPresetRight")
    assert hasattr(Q3DCamera.CameraPreset, "CameraPresetIsometricRight")
    assert callable(Q3DSurface.setOrthoProjection)
    assert callable(Q3DSurface.isOrthoProjection)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
