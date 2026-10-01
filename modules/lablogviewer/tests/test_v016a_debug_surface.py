from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _grid(x, y, z):
    return SimpleNamespace(
        x_values=np.asarray(x), y_values=np.asarray(y), z_values=np.asarray(z),
        x_name="Frequency", x_unit="Hz", y_name="Current", y_unit="A",
        z_name="VNA - S21", z_unit="V", transform="magnitude",
    )


def test_surface_grid_preserves_scientific_z_y_x_orientation():
    from app.visualization3d.data import prepare_surface_grid

    source = np.array([[11.0, 12.0, 13.0], [21.0, 22.0, 23.0]])
    result = prepare_surface_grid(_grid([5.0, 6.0, 7.0], [1.0, 2.0], source))
    assert result.shape == (2, 3)
    assert result.z_values[0, 2] == 13.0
    assert result.z_values[1, 0] == 21.0
    assert result.x_values.tolist() == [5.0, 6.0, 7.0]
    assert result.y_values.tolist() == [1.0, 2.0]
    assert result.x_name == "Frequency"
    assert result.y_name == "Current"


def test_surface_grid_rejects_invalid_shapes_axes_and_complex_fields():
    from app.visualization3d.data import prepare_surface_grid

    with pytest.raises(ValueError, match=r"shape \(len\(Y\), len\(X\)\)"):
        prepare_surface_grid(_grid([1, 2, 3], [1, 2], np.ones((3, 2))))
    with pytest.raises(ValueError, match="at least two"):
        prepare_surface_grid(_grid([1], [1, 2], np.ones((2, 1))))
    with pytest.raises(ValueError, match="real-valued Transform"):
        prepare_surface_grid(_grid([1, 2], [1, 2], np.ones((2, 2), dtype=complex)))
    with pytest.raises(ValueError, match="axis coordinates contain"):
        prepare_surface_grid(_grid([1, np.inf], [1, 2], np.ones((2, 2))))


def test_surface_grid_visual_sampling_is_bounded_and_does_not_mutate_source():
    from app.visualization3d.data import prepare_surface_grid

    source = np.arange(501 * 855, dtype=np.float64).reshape(855, 501)
    result = prepare_surface_grid(
        _grid(np.arange(501), np.arange(855), source), max_vertices=20_000
    )
    assert result.vertex_count <= 20_000
    assert result.was_decimated
    assert result.x_values[0] == 0 and result.x_values[-1] == 500
    assert result.y_values[0] == 0 and result.y_values[-1] == 854
    assert result.z_values[0, 0] == source[0, 0]
    assert result.z_values[-1, -1] == source[-1, -1]
    assert np.array_equal(source, np.arange(501 * 855, dtype=np.float64).reshape(855, 501))


def test_surface_grid_represents_nonfinite_values_as_gaps_without_mutation():
    from app.visualization3d.data import prepare_surface_grid

    source = np.array([[1.0, np.nan], [np.inf, 4.0]])
    result = prepare_surface_grid(_grid([1, 2], [3, 4], source))
    assert result.invalid_value_count == 2
    assert np.isnan(result.z_values[0, 1])
    assert np.isnan(result.z_values[1, 0])
    assert np.isinf(source[1, 0])
    with pytest.raises(ValueError, match="no finite Z values"):
        prepare_surface_grid(_grid([1, 2], [3, 4], np.full((2, 2), np.nan)))


def test_camera_state_round_trip_and_sanitizes_corrupt_values():
    from app.visualization3d.state import CameraState3D

    state = CameraState3D(390.0, -20.0, 800.0, (1.0, 2.0, 3.0), "orthographic")
    restored = CameraState3D.from_dict(state.to_dict())
    assert restored.x_rotation == 30.0
    assert restored.y_rotation == 340.0
    assert restored.zoom_level == 500.0
    assert restored.projection == "orthographic"
    assert CameraState3D.from_dict({"target": [0, float("nan"), 1]}).target == (0.0, 0.0, 0.0)
    assert CameraState3D.from_dict(None) == CameraState3D()


def test_camera_pose_matches_installed_pyside_binding_and_keeps_target_separate(qapp):
    from PySide6.QtDataVisualization import Q3DCamera
    from PySide6.QtGui import QVector3D

    from app.visualization3d.renderer import _apply_camera_pose

    camera = Q3DCamera()
    target = QVector3D(0.2, 0.4, 0.7)
    _apply_camera_pose(camera, 35.0, 25.0, 100.0, target)
    assert camera.xRotation() == pytest.approx(35.0)
    assert camera.yRotation() == pytest.approx(25.0)
    assert camera.zoomLevel() == pytest.approx(100.0)
    assert camera.target() == target

    with pytest.raises(TypeError, match="too many arguments"):
        camera.setCameraPosition(35.0, 25.0, 100.0, target)


def test_scene_coordinate_normalization_retains_physical_tick_values():
    from app.visualization3d.data import normalize_axis_for_scene

    mapping = normalize_axis_for_scene(np.array([5.019e9, 5.020e9, 5.021e9]))
    assert mapping.values.tolist() == [0.0, 0.5, 1.0]
    assert mapping.physical_value(0.5) == 5.020e9
    constant = normalize_axis_for_scene(np.array([0.125, 0.125]))
    assert constant.values.tolist() == [0.0, 0.0]
    assert constant.physical_value(-0.5) == 0.125


def test_surface_mesh_mapping_keeps_y_rows_x_columns_and_z_as_height():
    from app.visualization3d.data import prepare_surface_grid, prepare_surface_mesh

    grid = prepare_surface_grid(
        _grid([5.0, 6.0, 7.0], [1.0, 2.0], [[11.0, 12.0, 13.0], [21.0, 22.0, 23.0]])
    )
    mesh = prepare_surface_mesh(grid)
    assert mesh.coordinates.shape == (2, 3, 3)
    assert np.allclose(mesh.coordinates[0, 2], [1.0, 1.0 / 6.0, 0.0])
    assert np.allclose(mesh.coordinates[1, 0], [0.0, 5.0 / 6.0, 1.0])
    assert mesh.x_axis.physical_value(mesh.coordinates[0, 2, 0]) == 7.0
    assert mesh.y_axis.physical_value(mesh.coordinates[1, 0, 2]) == 2.0
    assert mesh.z_axis.physical_value(mesh.coordinates[1, 0, 1]) == 21.0


def test_surface_renderer_fails_safely_without_context_and_proxy_preserves_rows(qapp):
    from PySide6.QtDataVisualization import QSurfaceDataItem, QSurfaceDataProxy
    from PySide6.QtGui import QVector3D

    proxy = QSurfaceDataProxy()
    proxy.resetArray([
        [QSurfaceDataItem(QVector3D(1, 11, 3)), QSurfaceDataItem(QVector3D(2, 12, 3))],
        [QSurfaceDataItem(QVector3D(1, 21, 4)), QSurfaceDataItem(QVector3D(2, 22, 4))],
    ])
    assert (proxy.rowCount(), proxy.columnCount()) == (2, 2)
    assert proxy.itemAt(1, 0).position() == QVector3D(1, 21, 4)

    from app.visualization3d.availability import probe_opengl_context
    if not probe_opengl_context():
        from app.visualization3d.renderer import SurfaceRenderer

        with pytest.raises(RuntimeError, match="No compatible OpenGL context"):
            SurfaceRenderer()


def test_debug_page_is_localized_and_debug_payload_omits_personal_paths(qapp, tmp_path):
    from PySide6.QtGui import QGuiApplication

    from app.localization import LocalizationManager
    from app.settings.debug_info import DebugPage
    from app.settings.store import SettingsStore

    localizer = LocalizationManager(SettingsStore(tmp_path / "settings.json"))
    page = DebugPage(localizer)
    english = page.output.toPlainText()
    assert "Application Version:" in english
    assert "Graphics Device: Not queried" in english
    assert str(Path.home()) not in english
    localizer.set_language("zh_TW")
    chinese = page.output.toPlainText()
    assert "應用程式版本:" in chinese
    assert "圖形裝置: 尚未查詢" in chinese
    assert str(Path.home()) not in chinese
    page.copy_information()
    assert QGuiApplication.clipboard().text() == page.output.toPlainText()
    page.close()


def test_real_labber_grid_keeps_2d_heatmap_and_surface_grid_aligned(qapp, tmp_path):
    from tests.real_data import BIG_FILE

    if not BIG_FILE.exists():
        pytest.skip("Real 2D Labber fixture is not available.")

    from app.core.axis_preset_store import AxisPresetStore
    from app.core.comment_store import CommentStore
    from app.core.mark_store import MarkStore
    from app.core.named_view_store import NamedViewStore
    from app.core.overlay_store import OverlayStore
    from app.core.transform_store import TransformStore
    from app.core.viewer_display_state_store import ViewerDisplayStateStore
    from app.gui.main_window import MainWindow
    from app.visualization3d.data import prepare_surface_grid, prepare_surface_mesh

    window = MainWindow(
        transform_store=TransformStore(tmp_path / "transforms.json"),
        axis_preset_store=AxisPresetStore(tmp_path / "axis-presets.json"),
        overlay_store=OverlayStore(tmp_path / "overlays.json"),
        mark_store=MarkStore(tmp_path / "marks.json"),
        viewer_display_state_store=ViewerDisplayStateStore(tmp_path / "display.json"),
        comment_store=CommentStore(tmp_path / "comments.json"),
        named_view_store=NamedViewStore(tmp_path / "views.json"),
    )
    window.open_file(str(BIG_FILE))
    window.open_3d_window()
    heatmap = window.plot_2d_widget_nd._grid
    surface = prepare_surface_grid(heatmap)
    assert surface.source_shape == heatmap.z_values.shape
    assert surface.source_shape == (heatmap.y_values.size, heatmap.x_values.size)
    assert surface.x_values[0] == heatmap.x_values[0]
    assert surface.y_values[0] == heatmap.y_values[0]
    assert surface.shape == heatmap.z_values.shape
    assert surface.vertex_count == 855 * 501
    assert surface.sample_step == (1, 1)
    assert np.allclose(surface.z_values, heatmap.z_values, equal_nan=True)

    mesh = prepare_surface_mesh(surface)
    assert mesh.coordinates.shape == (855, 501, 3)
    assert mesh.coordinates[0, 0, 0] == pytest.approx(mesh.x_axis.values[0])
    assert mesh.coordinates[0, 0, 2] == pytest.approx(mesh.y_axis.values[0])
    assert mesh.z_axis.physical_value(mesh.coordinates[-1, -1, 1]) == pytest.approx(
        surface.z_values[-1, -1]
    )

    # The offscreen plugin in this environment has no OpenGL context. Selecting
    # Surface must report that limitation and leave the known-good heatmap live.
    from app.visualization3d.availability import probe_opengl_context
    if not probe_opengl_context():
        assert window.nd_plot_stack.currentIndex() == 2
        assert "OpenGL context" in window.statusBar().currentMessage()
        assert window.plot_2d_widget_nd._grid is heatmap
    window.close()


def test_browser_settings_menu_is_explicit_top_level_and_after_processing(qapp):
    from PySide6.QtGui import QGuiApplication
    from app.gui.browser_window import BrowserWindow

    window = BrowserWindow()
    actions = window.menuBar().actions()
    assert actions == [
        window.file_menu.menuAction(), window.tags_menu.menuAction(),
        window.processing_menu.menuAction(), window.interfaces_menu.menuAction(),
        window.network_menu.menuAction(), window.settings_menu.menuAction(), window.help_menu.menuAction(),
    ]
    assert actions[4].menuRole().name == "NoRole" and actions[5].menuRole().name == "NoRole"
    assert window.settings_action.menuRole().name == "NoRole"
    if QGuiApplication.platformName() != "offscreen":
        assert window.menuBar().isNativeMenuBar()
    window.close()


def test_settings_has_general_and_debug_sections_and_bilingual_surface_controls(qapp, tmp_path):
    from app.localization import LocalizationManager
    from app.settings.dialog import SettingsDialog
    from app.settings.store import SettingsStore

    localizer = LocalizationManager(SettingsStore(tmp_path / "settings.json"))
    dialog = SettingsDialog(localizer)
    assert dialog.sections.count() == 6
    assert [dialog.sections.item(index).text() for index in range(6)] == [
        "General", "Appearance", "Personal", "3D", "Debug", "About",
    ]
    dialog.sections.setCurrentRow(4)
    assert dialog.pages.currentIndex() == 4
    localizer.set_language("zh_TW")
    assert dialog.sections.item(0).text() == "一般"
    assert dialog.sections.item(1).text() == "外觀"
    assert dialog.sections.item(2).text() == "個人化"
    assert dialog.sections.item(3).text() == "3D"
    assert dialog.sections.item(4).text() == "偵錯"
    assert localizer.text("viewer.reset_view") == "重設視角"
    assert localizer.text("viewer.view_all") == "檢視全部"
    assert localizer.text("viewer.surface_3d") == "3D 曲面"          # v0.19D: translated
    assert "3D Surface" not in __import__(
        "app.localization.terminology", fromlist=["SCIENTIFIC_TERMS"]
    ).SCIENTIFIC_TERMS                                                  # v0.19D: no longer kept in English
    dialog.close()
