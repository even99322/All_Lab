"""Focused v0.14B scientific-data export and trace splitter coverage."""

from __future__ import annotations

import csv
import json
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.data_export import (
    ACTIVE_TRACE,
    DISPLAYED,
    FULL_DATA,
    FULL_RANGE,
    RAW,
    SELECTED_TRACES,
    ExportColumn,
    ExportDataset,
    long_grid_dataset,
    write_csv,
    write_npz,
)
from tests.real_data import BIG_FILE, FLUX_FILE

try:
    from PySide6.QtWidgets import QApplication
    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_raw_complex_csv_and_npz_round_trip_without_pickle(tmp_path):
    original = np.array([1.25 + 2.5j, -3.75 + 4.5j])
    dataset = ExportDataset(
        [
            ExportColumn("frequency", "Frequency_Hz", np.array([1.0, 2.0])),
            ExportColumn("s21_real", "S21_Real", original.real),
            ExportColumn("s21_imag", "S21_Imag", original.imag),
        ],
        {"x_values": np.array([1.0, 2.0]), "values": original},
        {"representation": RAW, "note": "繁體中文 metadata"},
    )
    csv_path, npz_path = tmp_path / "raw.csv", tmp_path / "raw.npz"
    write_csv(csv_path, dataset)
    write_npz(npz_path, dataset)

    rows = list(csv.reader(line for line in csv_path.read_text(encoding="utf-8").splitlines()
                           if not line.startswith("#")))
    reconstructed = np.asarray([float(row[1]) + 1j * float(row[2]) for row in rows[1:]])
    assert np.allclose(reconstructed, original)
    archive = np.load(npz_path, allow_pickle=False)
    assert np.allclose(archive["values"], original)
    assert json.loads(str(archive["metadata_json"]))["note"] == "繁體中文 metadata"


def test_long_grid_csv_orientation_and_visible_x_filter_are_explicit():
    grid = long_grid_dataset(
        x_values=np.array([10.0, 20.0, 30.0]), y_values=np.array([1.0, 2.0]),
        z_values=np.array([[11.0, 12.0, 13.0], [21.0, 22.0, 23.0]]),
        x_name="Frequency", x_unit="Hz", y_name="Flux", y_unit="Phi0",
        z_name="S21", z_unit="dB", metadata={"representation": DISPLAYED},
    )
    assert grid.metadata["array_orientation"] == "z[y_index, x_index]"
    rows = np.column_stack([column.values for column in grid.columns])
    assert np.allclose(rows[:, :3], [[10, 1, 11], [20, 1, 12], [30, 1, 13],
                                     [10, 2, 21], [20, 2, 22], [30, 2, 23]])
    cropped = grid.filtered_x_range(15.0, 30.0)
    assert np.all((cropped.columns[0].values >= 15.0) & (cropped.columns[0].values <= 30.0))


pytestmark_gui = pytest.mark.skipif(
    not PYSIDE_AVAILABLE or not BIG_FILE.exists(),
    reason="Qt or real Labber fixture unavailable",
)


@pytestmark_gui
def test_viewer_export_uses_current_trace_transform_and_visible_range(qapp):
    from app.gui.data_export_dialog import ExportOptions
    from app.gui.main_window import MainWindow

    viewer = MainWindow()
    try:
        viewer.resize(1000, 700)
        viewer.show()
        viewer.open_file(str(BIG_FILE))
        viewer.transform_combo.setCurrentText("Phase")
        viewer.unwrap_checkbox.setChecked(True)
        viewer.entry_spin.setValue(3)
        qapp.processEvents()
        view_box = viewer.plot_widget.plot_widget.getViewBox()
        view_box.setXRange(5.021e9, 5.024e9, padding=0)
        options = ExportOptions(ACTIVE_TRACE, DISPLAYED, "visible_x", "csv", "unused.csv")
        dataset = viewer._build_export_dataset(1, 0, options)
        x = dataset.columns[0].values
        _, rendered, transform, _ = viewer._plot_arrays_for_entry(
            viewer.x_combo.currentData(), viewer.y_combo.currentData(), 3, viewer._current_transform_spec(),
        )
        mask = (viewer.plot_widget._trace_data[3][0] >= 5.021e9) & (viewer.plot_widget._trace_data[3][0] <= 5.024e9)
        assert dataset.metadata["transform"] == transform
        assert np.all((x >= 5.021e9) & (x <= 5.024e9))
        assert np.allclose(dataset.columns[1].values, np.asarray(rendered)[mask])
    finally:
        viewer.close()


@pytestmark_gui
def test_selected_trace_export_preserves_trace_order_and_reference(qapp):
    from app.gui.data_export_dialog import ExportOptions
    from app.gui.main_window import MainWindow

    viewer = MainWindow()
    try:
        viewer.open_file(str(BIG_FILE))
        viewer.log_entries.restore_selection([5, 2, 7], active=7, visible=[2, 7], reference=2)
        qapp.processEvents()
        dataset = viewer._build_export_dataset(
            1, 0, ExportOptions(SELECTED_TRACES, DISPLAYED, FULL_RANGE, "csv", "unused.csv")
        )
        trace_column = next(column for column in dataset.columns if column.key == "trace")
        reference_column = next(column for column in dataset.columns if column.key == "reference")
        assert list(dict.fromkeys(trace_column.values.tolist())) == [3, 6, 8]
        assert dataset.metadata["traces"][0]["index"] == 2
        assert any(reference_column.values)
    finally:
        viewer.close()


@pytest.mark.skipif(not PYSIDE_AVAILABLE or not FLUX_FILE.exists(), reason="Qt or Flux fixture unavailable")
def test_flux_export_keeps_grid_axis_orientation_and_raw_complex_values(qapp):
    from app.gui.data_export_dialog import ExportOptions
    from app.gui.main_window import MainWindow

    viewer = MainWindow()
    try:
        viewer.open_file(str(FLUX_FILE))
        viewer.mode_combo.setCurrentIndex(1)
        qapp.processEvents()
        dataset = viewer._build_export_dataset(1, 1, ExportOptions(FULL_DATA, RAW, FULL_RANGE, "npz", "unused.npz"))
        raw_grid = viewer.cached.get_2d_data(
            viewer.x_combo_2d.currentText(), viewer.y_combo_2d.currentText(),
            viewer.z_combo_2d.currentText(), transform="raw",
        )
        assert dataset.arrays["z_values"].shape == raw_grid.z_values.shape
        assert np.allclose(dataset.arrays["z_values"], raw_grid.z_values)
        assert dataset.metadata["array_orientation"] == "z[y_index, x_index]"
        viewer.plot_2d_widget.view_box.setXRange(
            raw_grid.x_values[20], raw_grid.x_values[35], padding=0
        )
        cropped = viewer._build_export_dataset(
            1, 1, ExportOptions(FULL_DATA, RAW, "visible_x", "npz", "unused.npz")
        )
        assert cropped.arrays["z_values"].shape[1] == cropped.arrays["x_values"].size
        assert np.all((cropped.arrays["x_values"] >= raw_grid.x_values[20]) &
                      (cropped.arrays["x_values"] <= raw_grid.x_values[35]))
    finally:
        viewer.close()


@pytestmark_gui
def test_trace_splitter_ratio_survives_multi_pane_reparenting_and_session(qapp):
    from app.gui.main_window import MainWindow

    viewer = MainWindow()
    try:
        viewer.show()
        viewer.open_file(str(BIG_FILE))
        qapp.processEvents()
        viewer.plot_1d_splitter.setSizes([700, 300])
        qapp.processEvents()
        viewer._remember_trace_area_ratio()
        expected = viewer._trace_area_ratio
        viewer.pane_layout_combo.setCurrentText("2 Panes · Side by Side")
        qapp.processEvents()
        assert viewer._current_trace_area_ratio() == pytest.approx(expected, abs=0.04)
        state = viewer.session_state()
        assert state["splitters"]["trace_area_ratio"] == pytest.approx(expected, abs=0.04)
        viewer.pane_layout_combo.setCurrentText("1 Pane")
        qapp.processEvents()
        assert viewer._current_trace_area_ratio() == pytest.approx(expected, abs=0.04)
    finally:
        viewer.close()
