"""Publication 3D snapshot and independent output-path regression."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from app.visualization3d.publication import (
    render_publication, snapshot_points, snapshot_surface,
)
from app.visualization3d.data import prepare_surface_grid


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


def _settings(**overrides):
    result = dict(
        axis_labels=("Frequency (GHz)", "Current (mA)", "S21 (dB)"),
        title="Scientific measurement", color_label="S21 (dB)",
        colormap="LabLog BWR", color_range=(-30.0, 0.0), opacity=0.5,
        secondary_opacity=0.75, bottom_projection=True,
        projection_opacity=0.7,
        reference_mode="off", reference_value=0.0, z_scale=1.0,
        camera_azimuth=-45.0, camera_elevation=30.0,
        camera_zoom=100.0, camera_target=(0.5, 0.5, 0.5),
        projection="perspective",
    )
    result.update(overrides)
    return result


def test_surface_snapshot_freezes_scientific_arrays_and_exports_png_svg():
    pytest.importorskip("matplotlib")
    x = np.linspace(5.0, 5.02, 16)
    y = np.linspace(10.0, 11.0, 12)
    z = np.add.outer(np.linspace(-24.0, -8.0, 12), np.linspace(-2.0, 2.0, 16))
    grid = Grid(x, y, z)
    snapshot = snapshot_surface(grid, grid, geometry="Surface", **_settings())
    assert snapshot.primary is not None
    assert snapshot.primary.z_values.shape == (12, 16)
    z[0, 0] = 1000.0
    assert snapshot.primary.z_values[0, 0] != 1000.0
    assert not snapshot.primary.z_values.flags.writeable
    png = render_publication(snapshot, "png")
    svg = render_publication(snapshot, "svg")
    assert png.startswith(b"\x89PNG")
    assert b"<svg" in svg
    assert b"Frequency" in svg
    assert b"<image" not in svg


@pytest.mark.parametrize("opacity", [1.0, 0.75, 0.5, 0.25])
def test_publication_surface_svg_contains_real_alpha(opacity):
    pytest.importorskip("matplotlib")
    grid = Grid(np.arange(3), np.arange(3), np.arange(9).reshape(3, 3) - 8)
    snapshot = snapshot_surface(
        grid, grid, geometry="Surface",
        **_settings(opacity=opacity, bottom_projection=False),
    )
    svg = render_publication(snapshot, "svg")
    if opacity < 1:
        assert f"opacity: {opacity}".encode() in svg
    else:
        assert b"<svg" in svg


@pytest.mark.parametrize("geometry", ["Scatter", "Trajectory"])
def test_point_publication_uses_same_snapshot_pipeline(geometry):
    pytest.importorskip("matplotlib")
    t = np.linspace(0, 2 * np.pi, 32)
    coordinates = np.column_stack((np.cos(t), np.sin(t), t))
    colors = np.linspace(-30, 0, len(t))
    snapshot = snapshot_points(coordinates, colors, geometry=geometry, **_settings())
    coordinates[0, 0] = 999.0
    assert snapshot.points[0, 0] != 999.0
    assert render_publication(snapshot, "png").startswith(b"\x89PNG")


def test_invalid_point_snapshot_is_rejected():
    with pytest.raises(ValueError):
        snapshot_points(np.zeros((3, 2)), np.zeros(3), geometry="Scatter", **_settings())


@pytest.mark.parametrize("geometry", ["Dual Surface", "Waterfall"])
def test_other_surface_geometries_render_from_frozen_data(geometry):
    pytest.importorskip("matplotlib")
    x = np.linspace(0, 1, 6)
    y = np.linspace(0, 1, 5)
    z = np.add.outer(y, x) * 15 - 20
    first = Grid(x, y, z)
    second = Grid(x, y, -z)
    snapshot = snapshot_surface(
        first, first, geometry=geometry,
        secondary=prepare_surface_grid(second) if geometry == "Dual Surface" else None,
        **_settings(opacity=0.25, secondary_opacity=0.75,
                    reference_mode="zero", bottom_projection=True),
    )
    svg = render_publication(snapshot, "svg")
    assert b"<svg" in svg
    assert b"opacity: 0.25" in svg if geometry == "Dual Surface" else b"Frequency" in svg


def test_publication_z_scale_is_display_only():
    pytest.importorskip("matplotlib")
    grid = Grid(np.arange(4), np.arange(4), np.arange(16).reshape(4, 4) - 20)
    normal = snapshot_surface(grid, grid, geometry="Surface", **_settings(z_scale=1.0))
    exaggerated = snapshot_surface(grid, grid, geometry="Surface", **_settings(z_scale=2.0))
    assert np.array_equal(normal.primary.z_values, exaggerated.primary.z_values)
    assert np.array_equal(normal.primary.color_values, exaggerated.primary.color_values)
    assert render_publication(normal, "png") != render_publication(exaggerated, "png")


def test_3d_window_copy_save_use_screen_or_publication_style(tmp_path, monkeypatch):
    """v0.18B: 3D copy/save belong to the 3D window; the style follows Settings."""
    from PySide6.QtWidgets import QApplication, QFileDialog
    from PySide6.QtGui import QColor, QImage
    from app.gui.main_window import MainWindow
    from types import SimpleNamespace

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    image = QImage(1800, 1200, QImage.Format.Format_RGB32)
    image.fill(QColor("#2255aa"))
    calls = []

    def render_scene():
        calls.append(True)
        return image

    window.nd_surface_renderer = SimpleNamespace(render_plot_image=render_scene)
    monkeypatch.setattr(window, "_has_3d_scene", lambda: True)
    monkeypatch.setattr(window, "_drag_share_filename", lambda *_args: "scientific-3d")
    try:
        monkeypatch.setattr(window, "three_d_export_style", lambda: "screen")
        window.copy_3d_plot()
        copied = QApplication.clipboard().image()
        assert copied.width() > 1500 and copied.height() > 1000
        target = tmp_path / "surface.png"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "PNG image (*.png)"))
        window.save_3d_plot_dialog()
        assert target.read_bytes().startswith(b"\x89PNG")
        assert calls == [True, True]

        monkeypatch.setattr(window, "three_d_export_style", lambda: "publication")
        published = []
        monkeypatch.setattr(window, "_render_3d_publication",
                            lambda fmt="png": published.append(fmt) or (b"%PDF-1.7" if fmt == "pdf" else None))
        pdf = tmp_path / "surface.pdf"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(pdf), "PDF document (*.pdf)"))
        window.save_3d_plot_dialog()
        assert pdf.read_bytes().startswith(b"%PDF")
        assert published == ["pdf"]
        # Viewer plot actions never export the 3D scene any more.
        assert not hasattr(window, "_is_3d_export")
    finally:
        window.close()
        app.processEvents()


def test_3d_display_state_round_trip_includes_camera_without_native_graph(monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtCore import QSignalBlocker
    from PySide6.QtWidgets import QApplication
    from app.gui.main_window import MainWindow
    from app.visualization3d.state import CameraState3D
    from tests.real_data import BIG_FILE

    if not BIG_FILE.exists():
        pytest.skip("Real Viewer fixture unavailable")
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.open_file(str(BIG_FILE))
    camera = CameraState3D(121.0, 42.0, 175.0, (0.25, 0.4, 0.7))
    try:
        with QSignalBlocker(window.mode_combo):
            window.open_3d_window()
        window.nd_surface_renderer = SimpleNamespace(camera_state=lambda: camera)
        state = window._current_viewer_display_state()
        assert state["three_d"]["camera"] == camera.to_dict()
        window.nd_surface_renderer = None
        monkeypatch.setattr(window, "_rebuild_nd_plot", lambda: None)
        window._restore_display_state(state)
        assert window._pending_3d_camera_state == camera.to_dict()
    finally:
        window.nd_surface_renderer = None
        window.close()
        app.processEvents()
