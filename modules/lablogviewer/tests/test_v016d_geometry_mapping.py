from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.data_model import Grid2DData
from app.visualization3d.mapping import (
    GeometryType,
    mapping_values,
    prepare_point_cloud,
    prepare_trajectory_trace,
    prepare_waterfall_grid,
    transform_grid_3d,
    unwrap_phase_grid,
)


def _grid(values):
    values = np.asarray(values)
    return Grid2DData(
        x_values=np.linspace(5.0, 6.0, values.shape[1]),
        y_values=np.linspace(-0.2, 0.2, values.shape[0]),
        z_values=values,
        x_name="Frequency", x_unit="GHz",
        y_name="Current", y_unit="mA",
        z_name="S21", z_unit=None, transform="raw",
    )


def test_geometry_types_are_separate_from_scientific_mappings():
    assert [item.value for item in GeometryType] == [
        "Surface", "Transparent Surface", "Dual Surface", "Waterfall", "Trajectory", "Scatter"
    ]
    assert "real" not in {item.value for item in GeometryType}


def test_phase_unwrap_is_per_selected_axis_and_does_not_cross_nan_gaps():
    phase = np.array([[170.0, -170.0, -160.0], [-170.0, 170.0, 160.0]])
    along_frequency = unwrap_phase_grid(phase, axis=1, unit="deg")
    assert np.allclose(along_frequency[0], [170.0, 190.0, 200.0])
    assert np.allclose(along_frequency[1], [-170.0, -190.0, -200.0])
    along_sweep = unwrap_phase_grid(phase, axis=0, unit="deg")
    assert np.allclose(along_sweep[:, 0], [170.0, 190.0])
    assert np.allclose(along_sweep[:, 1], [-170.0, -190.0])
    with_gap = unwrap_phase_grid(np.array([[170.0, np.nan, -170.0]]), axis=1)
    assert np.isnan(with_gap[0, 1])
    assert with_gap[0, 2] == -170.0


def test_unwrapped_phase_grid_preserves_shape_and_source_data():
    values = np.exp(1j * np.deg2rad(np.array([[170.0, -170.0, -160.0], [0, 10, 20]])))
    source = _grid(values)
    result = transform_grid_3d(source, "phase_unwrapped_deg", unwrap_axis=1)
    assert result.transform == "phase_unwrapped_deg"
    assert result.z_values.shape == values.shape
    assert np.allclose(result.z_values[0], [170.0, 190.0, 200.0])
    assert np.iscomplexobj(source.z_values)
    assert np.allclose(source.z_values, values)


def test_mapping_uses_actual_grid_dimensions_and_complex_transforms():
    source = _grid(np.array([[1 + 2j, 3 + 4j], [5 + 6j, 7 + 8j]]))
    assert mapping_values(source, "Frequency").shape == (2, 2)
    assert np.allclose(mapping_values(source, "Current")[:, 0], source.y_values)
    assert np.array_equal(mapping_values(source, "point_index"), [[0, 1], [2, 3]])
    assert np.allclose(mapping_values(source, "real"), [[1, 3], [5, 7]])
    assert np.allclose(mapping_values(source, "imaginary"), [[2, 4], [6, 8]])
    assert np.allclose(mapping_values(source, "magnitude"), np.abs(source.z_values))
    with pytest.raises(ValueError):
        mapping_values(source, "not-a-channel")


def test_waterfall_separates_rows_and_preserves_extrema_under_budget():
    values = np.zeros((12, 400), dtype=float)
    values[:, 217] = np.where(np.arange(12) % 2, -15.0, 20.0)
    height = _grid(values)
    color = _grid(np.abs(values))
    prepared = prepare_waterfall_grid(height, color, max_vertices=2_000)
    assert prepared.grid.z_values.shape == (2 * len(prepared.source_rows), len(prepared.source_columns))
    assert prepared.grid.z_values.size <= 2_000
    assert np.isnan(prepared.grid.z_values[1::2]).all()
    assert np.allclose(prepared.grid.z_values[0::2], values[np.ix_(prepared.source_rows, prepared.source_columns)])
    assert 217 in prepared.source_columns
    assert np.allclose(prepared.color_grid.z_values[0::2], np.abs(values[np.ix_(prepared.source_rows, prepared.source_columns)]))
    assert np.isnan(prepared.color_grid.z_values[1::2]).all()
    assert np.array_equal(height.z_values, values)


def test_point_cloud_preserves_coordinates_color_and_original_index_mapping():
    x, y, z = np.meshgrid(np.arange(6), np.arange(4), np.arange(2), indexing="xy")
    color = x + 2 * y - z
    cloud = prepare_point_cloud(x, y, z, color, max_points=12)
    assert cloud.coordinates.shape == (12, 3)
    assert cloud.color_values.shape == (12,)
    assert cloud.source_indices.shape == (12,)
    assert cloud.source_shape == x.shape
    assert np.isfinite(cloud.coordinates).all()
    assert np.array_equal(cloud.coordinates[:, 0], x.reshape(-1)[cloud.source_indices])
    assert np.array_equal(cloud.color_values, color.reshape(-1)[cloud.source_indices])


def test_trajectory_uses_selected_trace_and_original_indices():
    x = np.broadcast_to(np.arange(4)[None, :], (3, 4))
    y = np.broadcast_to(np.arange(3)[:, None], (3, 4))
    z = x + y
    cloud = prepare_trajectory_trace(x, y, z, z, trace_index=2)
    assert cloud.source_shape == (3, 4)
    assert np.array_equal(cloud.source_indices, [8, 9, 10, 11])
    assert np.array_equal(cloud.coordinates[:, 1], [2, 2, 2, 2])
    with pytest.raises(ValueError, match="unavailable"):
        prepare_trajectory_trace(x, y, z, z, trace_index=3)
    sparse_color = np.full_like(z, np.nan, dtype=float)
    sparse_color[1, 0] = 1.0
    with pytest.raises(ValueError, match="at least two"):
        prepare_trajectory_trace(x, y, z, sparse_color, trace_index=1)


def test_point_cloud_rejects_complex_or_object_coordinates():
    with pytest.raises(ValueError, match="real-valued"):
        prepare_point_cloud(np.array([1 + 2j]), np.array([0]), np.array([0]))
    with pytest.raises(ValueError, match="real-valued"):
        prepare_point_cloud(np.array([object()]), np.array([0]), np.array([0]))


def test_point_cloud_filters_nonfinite_and_rejects_empty_or_tiny_budget():
    cloud = prepare_point_cloud(
        np.array([0.0, np.nan, 2.0]), np.array([0.0, 1.0, 2.0]),
        np.array([1.0, 1.0, 3.0]), max_points=10,
    )
    assert cloud.coordinates.shape == (2, 3)
    with pytest.raises(ValueError, match="no finite"):
        prepare_point_cloud(np.array([np.nan]), np.array([0]), np.array([0]))
    with pytest.raises(ValueError, match="at least two"):
        prepare_point_cloud(np.arange(4), np.arange(4), np.arange(4), max_points=1)


def test_bounded_point_cloud_keeps_narrow_extremum_and_axis_extents():
    count = 5_000
    x = np.arange(count, dtype=np.float64)
    y = np.zeros(count, dtype=np.float64)
    z = np.zeros(count, dtype=np.float64)
    z[2_731] = -20.0
    cloud = prepare_point_cloud(x, y, z, max_points=64)
    assert len(cloud.coordinates) == 64
    assert 0 in cloud.source_indices
    assert count - 1 in cloud.source_indices
    assert 2_731 in cloud.source_indices
    assert cloud.coordinates[:, 0].min() == 0
    assert cloud.coordinates[:, 0].max() == count - 1


def test_surface_opacity_changes_alpha_without_changing_scientific_rgb():
    from app.visualization3d.renderer import map_surface_colors

    values = np.array([[-2.0, 0.0, 2.0]])
    opaque = map_surface_colors(values, "LabLog BWR", (-2.0, 2.0))
    translucent = map_surface_colors(values, "LabLog BWR", (-2.0, 2.0), opacity=0.3)
    assert np.array_equal(opaque[..., :3], translucent[..., :3])
    assert np.array_equal(translucent[..., 3], np.rint(opaque[..., 3] * 0.3).astype(np.uint8))
    assert len({tuple(color) for color in opaque[0]}) == 3


def test_common_height_limits_keep_dual_surfaces_in_one_scientific_scene():
    from app.visualization3d.data import normalize_axis_for_scene, scene_height_for_value

    height_axis = normalize_axis_for_scene(np.array([[-2.0, 2.0]]), limits=(-4.0, 4.0))
    assert scene_height_for_value(-4.0, height_axis) == pytest.approx(0.0)
    assert scene_height_for_value(0.0, height_axis) == pytest.approx(0.5)
    assert scene_height_for_value(4.0, height_axis) == pytest.approx(1.0)
    assert scene_height_for_value(4.0, height_axis, z_scale=2.0) == pytest.approx(1.5)


def test_shared_height_gradient_uses_dual_surface_scene_range():
    from app.visualization3d.renderer import build_surface_gradient

    gradient = build_surface_gradient(
        "LabLog BWR", (-2.0, 2.0), (-4.0, 4.0)
    )
    stops = gradient.stops()
    assert stops[0][1].red() > stops[0][1].blue()
    assert np.allclose(stops[len(stops) // 2][1].getRgb()[:3], [255, 255, 255], atol=2)
    assert stops[-1][1].blue() > stops[-1][1].red()


def test_horizontal_colorbar_drag_emits_the_same_shared_min_max_pair():
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from app.visualization3d.renderer import InteractiveColorbar

    app = QApplication.instance() or QApplication([])
    bar = InteractiveColorbar()
    bar.resize(500, 42)
    bar.set_mapping(np.tile(np.array([[0, 80, 200, 255]], dtype=np.uint8), (256, 1)),
                    (0.0, 10.0), (2.0, 8.0), "Magnitude (dB)")
    bar.show()
    app.processEvents()
    changed = []
    bar.levels_changed.connect(lambda low, high: changed.append((low, high)))
    left, right = bar._x_for_value(2.0, bar.rect().adjusted(36, 13, -36, -11)), bar.rect().adjusted(36, 13, -36, -11)
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=QPoint(round(left), 23))
    QTest.mouseMove(bar, QPoint(round(bar._x_for_value(3.0, right)), 23), 10)
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=QPoint(round(bar._x_for_value(3.0, right)), 23))
    assert changed
    assert changed[-1][0] == pytest.approx(3.0, abs=0.1)
    assert changed[-1][1] == pytest.approx(8.0)
    bar.close()


def test_viewer_colorbar_range_routes_through_shared_color_update_path(monkeypatch):
    from PySide6.QtWidgets import QApplication

    from app.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    heatmap_ranges = []
    renderer_ranges = []
    monkeypatch.setattr(
        window.plot_2d_widget_nd, "set_color_range",
        lambda low, high: heatmap_ranges.append((low, high)),
    )
    monkeypatch.setattr(
        window, "_update_surface_color_display",
        lambda *, limits=None: renderer_ranges.append(limits),
    )

    window._on_3d_colorbar_range_changed(-3.25, 2.75)

    assert not window.auto_range_checkbox_nd.isChecked()
    assert window.zmin_spin_nd.value() == pytest.approx(-3.25)
    assert window.zmax_spin_nd.value() == pytest.approx(2.75)
    assert heatmap_ranges == [(-3.25, 2.75)]
    assert renderer_ranges == [(-3.25, 2.75)]
    window.close()
    app.processEvents()


def test_geometry_controls_are_contextual_and_mapping_includes_point_index():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    rows = window._three_d_control_rows

    assert rows["opacity"][1].isHidden() is False
    assert rows["projection"][1].isHidden() is False
    assert rows["point_x"][1].isHidden() is True

    for geometry in ("Dual Surface", "Waterfall", "Trajectory", "Scatter"):
        window.geometry_combo.setCurrentIndex(window.geometry_combo.findData(geometry))
        app.processEvents()
        if geometry == "Dual Surface":
            assert not rows["opacity_b"][1].isHidden()
            assert rows["surface_b"][1].isHidden() is False
        elif geometry == "Waterfall":
            assert rows["opacity"][1].isHidden()
            # v0.18A: every geometry offers Bottom Projection and Reference Plane.
            assert not rows["projection"][1].isHidden()
            assert not rows["reference"][1].isHidden()
        else:
            assert not rows["point_x"][1].isHidden()
            assert not rows["projection"][1].isHidden()
            assert not rows["reference"][1].isHidden()
            assert window.point_x_mapping_combo.findData("point_index") >= 0

    window.close()
    app.processEvents()


def test_3d_mapping_and_geometry_round_trip_through_external_viewer_state(tmp_path):
    import os
    from types import SimpleNamespace

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app.gui.main_window import MainWindow
    from app.core.viewer_display_state_store import ViewerDisplayStateStore

    app = QApplication.instance() or QApplication([])

    def prepared_window(index):
        window = MainWindow(viewer_display_state_store=ViewerDisplayStateStore(
            tmp_path / f"display-{index}.json"
        ))
        for combo in (window.x_combo_nd, window.y_combo_nd, window.z_combo_nd):
            combo.blockSignals(True)
        window.experiment = SimpleNamespace(source_path="/tmp/measurement.hdf5")
        window.x_combo_nd.addItem("Frequency")
        window.y_combo_nd.addItem("Current", userData="Current")
        window.z_combo_nd.addItem("VNA - S21")
        for combo in (window.x_combo_nd, window.y_combo_nd, window.z_combo_nd):
            combo.blockSignals(False)
        window.mode_combo.blockSignals(True)
        window.open_3d_window()
        window.mode_combo.blockSignals(False)
        return window

    source = prepared_window(1)
    source.geometry_combo.setCurrentIndex(source.geometry_combo.findData("Dual Surface"))
    source.transform_combo_nd.setCurrentIndex(source.transform_combo_nd.findData("real"))
    source.surface_b_transform_combo.setCurrentIndex(
        source.surface_b_transform_combo.findData("imag")
    )
    source.surface_opacity_slider.setValue(42)
    source.surface_b_opacity_slider.setValue(68)
    source.surface_reference_combo.setCurrentIndex(
        source.surface_reference_combo.findData("zero")
    )
    state = source._current_viewer_display_state()
    assert state["mode"] == "3d"
    assert state["three_d"]["geometry"] == "Dual Surface"

    restored = prepared_window(2)
    restored._restore_display_state(state)
    assert restored.geometry_combo.currentData() == "Dual Surface"
    assert restored.transform_combo_nd.currentData() == "real"
    assert restored.surface_b_transform_combo.currentData() == "imag"
    assert restored.surface_opacity_slider.value() == 42
    assert restored.surface_b_opacity_slider.value() == 68
    assert restored.surface_reference_combo.currentData() == "zero"

    source.experiment = None
    restored.experiment = None
    source.close()
    restored.close()
    app.processEvents()
