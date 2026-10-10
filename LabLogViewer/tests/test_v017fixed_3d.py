"""Reference-plane blending and native scene export regression."""

from dataclasses import dataclass
from pathlib import Path
import os

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
    z_name: str = "S21"
    z_unit: str = "dB"
    transform: str = "magnitude_db"


def test_reference_mesh_is_a_bounded_two_triangle_plane():
    source = Path(__file__).resolve().parents[1] / "app/visualization3d/reference_plane.obj"
    lines = source.read_text().splitlines()
    assert len([line for line in lines if line.startswith("f ")]) == 2
    assert len([line for line in lines if line.startswith("vt ")]) == 4
    assert len([line for line in lines if line.startswith("vn ")]) == 1


def test_viewer_3d_export_no_longer_imports_parallel_publication_renderer():
    import inspect
    from app.gui.main_window import MainWindow

    source = inspect.getsource(MainWindow._render_3d_export_image)
    save_source = inspect.getsource(MainWindow._save_export)
    three_d_save = inspect.getsource(MainWindow.save_3d_plot_dialog)
    publication = inspect.getsource(MainWindow._render_3d_publication)
    assert "render_plot_image" in source
    # v0.18B: Viewer plot export is 1D/2D only; 3D export lives in the 3D window
    # and its publication path is publication_style, not the old snapshot module.
    assert "nd_surface_renderer" not in save_source
    assert "_render_3d_publication" in three_d_save
    assert "render_renderer_publication" in publication
    assert "render_publication(" not in source + save_source + publication


def _rgba(image):
    from PySide6.QtGui import QImage

    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    buffer = np.frombuffer(converted.bits(), dtype=np.uint8)
    return buffer.reshape(converted.height(), converted.bytesPerLine())[
        :, :converted.width() * 4
    ].reshape(converted.height(), converted.width(), 4).copy()


@pytest.mark.skipif(
    os.environ.get("QT_QPA_PLATFORM") == "offscreen",
    reason="Native OpenGL pixel test requires a window server",
)
def test_native_reference_plane_alpha_and_scene_export(tmp_path):
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QApplication
    from app.visualization3d.availability import probe_opengl_context
    from app.visualization3d.renderer import SurfaceRenderer

    app = QApplication.instance() or QApplication([])
    if not probe_opengl_context():
        pytest.skip("No native OpenGL context")
    renderer = SurfaceRenderer()
    renderer.resize(920, 680)
    x = np.linspace(5.0, 5.02, 80)
    y = np.linspace(10.0, 11.0, 60)
    z = -15 + 8 * np.sin((x[None, :] - 5.0) * 220) * np.cos((y[:, None] - 10) * 6)
    try:
        renderer.set_grid(Grid(x, y, z))
        renderer.set_bottom_projection(True)
        renderer.show()
        renderer.graph.scene().activeCamera().setZoomLevel(65)
        app.processEvents()
        renderer.set_reference_plane("off")
        off = _rgba(renderer.render_to_image(QSize(1000, 700)))
        renderer.set_reference_plane("custom", -15)
        app.processEvents()
        on = _rgba(renderer.render_to_image(QSize(1000, 700)))

        red = ((off[..., 0] > 140) & (off[..., 0] > off[..., 2] * 1.5)
               & (off[..., 1] < 150))
        blended = red & (on[..., 0] < off[..., 0] - 20) & (on[..., 0] > 50)
        assert np.count_nonzero(blended) > 500
        assert renderer.reference_plane_item.isVisible()
        assert not renderer.reference_plane_item.isScalingAbsolute()

        renderer.series.setVisible(False)
        with_plane = _rgba(renderer.render_to_image(QSize(1000, 700)))
        renderer.set_reference_plane("off")
        without_plane = _rgba(renderer.render_to_image(QSize(1000, 700)))
        floor_red = ((without_plane[..., 0] > 140)
                     & (without_plane[..., 0] > without_plane[..., 2] * 1.5)
                     & (without_plane[..., 1] < 150))
        floor_blended = (floor_red & (with_plane[..., 0] < without_plane[..., 0] - 20)
                         & (with_plane[..., 0] > 50))
        assert np.count_nonzero(floor_blended) > 300
        renderer.series.setVisible(True)
        renderer.set_reference_plane("custom", -15)

        before = renderer.camera_state()
        mesh_count = renderer._mesh_rebuild_count
        direct = _rgba(renderer.render_to_image(renderer.graph.size()))
        composite = _rgba(renderer.render_plot_image(scale=1))
        scene_part = composite[:, :renderer.graph.width(), :]
        assert np.mean(np.abs(scene_part.astype(int) - direct.astype(int))) < 2.0
        colorbar_part = composite[:, renderer.graph.width():, :3]
        assert np.ptp(colorbar_part[..., 0]) > 100
        assert np.ptp(colorbar_part[..., 2]) > 100
        image = renderer.render_plot_image(scale=2)
        assert image.width() > renderer.graph.width() * 2
        assert image.height() == renderer.graph.height() * 2
        assert renderer.camera_state() == before
        assert renderer._mesh_rebuild_count == mesh_count
        for _ in range(3):
            assert not renderer.render_plot_image(scale=1).isNull()
        renderer.graph.setOrthoProjection(True)
        orthographic = _rgba(renderer.render_plot_image(scale=1))
        assert renderer.graph.isOrthoProjection()
        assert np.mean(np.abs(orthographic.astype(int) - composite.astype(int))) > 1.0
        renderer.graph.setOrthoProjection(False)
        renderer.set_geometry_type("Waterfall")
        assert not renderer.reference_plane_item.isVisible()
        assert not renderer.render_plot_image(scale=1).isNull()
        renderer.set_geometry_type("Surface")
        assert renderer.reference_plane_item.isVisible()
        assert renderer.camera_state() == before
        assert renderer._mesh_rebuild_count > mesh_count
        renderer.series.setVisible(False)
        with pytest.raises(ValueError, match="finish preparing"):
            renderer.render_plot_image(scale=1)
        renderer.series.setVisible(True)
        assert image.save(str(tmp_path / "scene.png"), "PNG")
        assert not image.isNull()
    finally:
        renderer.close()
        app.processEvents()
