from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
import pytest


@dataclass
class Grid:
    x_values: np.ndarray
    y_values: np.ndarray
    z_values: np.ndarray
    x_name: str = "Frequency"
    x_unit: str = "GHz"
    y_name: str = "Current"
    y_unit: str = "mA"
    z_name: str = "VNA - S21"
    z_unit: str = "dB"
    transform: str = "magnitude_db"


def _grid(rows=24, columns=32):
    x = np.linspace(5.019, 5.030, columns)
    y = np.linspace(130.4, 131.1, rows)
    xx, yy = np.meshgrid(x, y)
    z = -15 - 14 * np.exp(-((xx - 5.025) / 0.0012) ** 2) + 2 * np.sin(yy * 4)
    return Grid(x, y, z)


def test_transparent_geometry_has_bounded_depth_sorted_face_mapping():
    from app.visualization3d.mapping import GeometryType
    from app.visualization3d.transparent_renderer import surface_triangles
    from app.gui.plot_2d_widget import get_colormap
    from app.visualization3d.data import prepare_surface_grid

    assert GeometryType.TRANSPARENT_SURFACE.value == "Transparent Surface"
    grid = prepare_surface_grid(_grid(), max_vertices=6000)
    _vertices, faces, face_values, _axes = surface_triangles(grid)
    assert faces.shape[1] == 3
    assert face_values.shape == (len(faces),)
    assert len(faces) > 0

    cmap = get_colormap("LabLog BWR")
    mapped = np.asarray(cmap.map(np.array([0.0, 0.5, 1.0]), mode="byte"))
    assert np.linalg.norm(mapped[0, :3].astype(int) - mapped[1, :3]) > 100
    assert np.linalg.norm(mapped[2, :3].astype(int) - mapped[1, :3]) > 100
    assert np.linalg.norm(mapped[0, :3].astype(int) - mapped[2, :3]) > 100


@pytest.mark.skipif(os.environ.get("QT_QPA_PLATFORM") != "offscreen",
                    reason="QtAgg offscreen render regression")
def test_transparent_surface_renderer_alpha_axes_export_and_scientific_pick():
    from PySide6.QtWidgets import QApplication
    from app.visualization3d.transparent_renderer import TransparentSurfaceRenderer

    app = QApplication.instance() or QApplication([])
    renderer = TransparentSurfaceRenderer()
    try:
        renderer.set_grid(_grid(), rendering_policy="Auto")
        app.processEvents()
        assert renderer.surface_grid.vertex_count <= renderer.MAX_VERTICES
        assert renderer._collection is not None
        assert np.allclose(renderer._collection.get_facecolors()[:, 3], 0.65, atol=0.02)
        aspect = np.asarray(renderer.ax.get_box_aspect())
        assert aspect / aspect[0] == pytest.approx((1.0, 1.0, 0.72), abs=0.03)
        image = renderer.render_plot_image(scale=1)
        assert not image.isNull()
        assert image.width() > 300 and image.height() > 200
        assert renderer.camera_state().target == pytest.approx((0.5, 0.5, 0.5))

        before = renderer.camera_state()
        renderer.set_z_scale(1.5)
        after = renderer.camera_state()
        assert after == before
        assert renderer.diagnostics()["backend"] == "Matplotlib depth-sorted transparency"
    finally:
        renderer.close()


def test_reference_and_native_3d_share_declared_axis_box_proportions():
    import inspect
    from app.visualization3d.renderer import configure_scientific_3d_scene
    from app.visualization3d.transparent_renderer import TransparentSurfaceRenderer

    source = inspect.getsource(configure_scientific_3d_scene)
    assert "setAspectRatio(0.72)" in source
    assert "setHorizontalAspectRatio(1.0)" in source
    assert "set_box_aspect((1, 1, 0.72))" in inspect.getsource(TransparentSurfaceRenderer._draw_camera)
    assert 'SURFACE_3D["legacy_background"]' in source
    from app.palette import SURFACE_3D
    assert SURFACE_3D["legacy_background"].lower() == "#f3f5f7"
