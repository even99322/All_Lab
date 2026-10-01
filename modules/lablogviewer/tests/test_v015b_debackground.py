"""Scientific and file-safety tests for the v0.15B De-background service."""

from __future__ import annotations

import ast
import hashlib
import threading
from pathlib import Path

import h5py
import numpy as np
import pytest

from app.core.debackground import (
    DeBackgroundCancelled,
    DeBackgroundCompatibilityError,
    DeBackgroundError,
    DeBackgroundOutputExists,
    divide_complex_trace,
    inspect_debackground,
    process_debackground,
)
from app.core.hdf5_reader import HDF5Reader
from app.core.labber_parser import load_experiment
from tests.real_data import PROJECT_ROOT


def _build_labber_file(
    path: Path,
    traces: dict[str, np.ndarray],
    frequency: np.ndarray,
    *,
    step_values: np.ndarray | None = None,
    unindexed_entries: int = 1,
    frequency_unit: str = "Hz",
) -> Path:
    frequency = np.asarray(frequency, dtype=np.float64)
    n_entries = len(step_values) if step_values is not None else unindexed_entries
    channel_dtype = np.dtype([
        ("name", h5py.special_dtype(vlen=str)),
        ("instrument", h5py.special_dtype(vlen=str)),
        ("quantity", h5py.special_dtype(vlen=str)),
        ("unitPhys", h5py.special_dtype(vlen=str)),
        ("unitInstr", h5py.special_dtype(vlen=str)),
    ])
    name_info_dtype = np.dtype([
        ("name", h5py.special_dtype(vlen=str)),
        ("info", h5py.special_dtype(vlen=str)),
    ])
    list_dtype = np.dtype([("channel_name", h5py.special_dtype(vlen=str))])

    rows = []
    if step_values is not None:
        rows.append(("Current", "Source", "Current", "mA", "mA"))
        rows.append(("Auxiliary", "Source", "Auxiliary", "", ""))
    rows.extend((name, "VNA", name, "", "") for name in traces)

    with h5py.File(path, "w") as file:
        file.attrs.update({"log_name": path.stem, "creation_time": 1.0, "comment": "", "version": "test"})
        file.create_dataset("Channels", data=np.asarray(rows, dtype=channel_dtype))
        steps = ["Current"] if step_values is not None else []
        file.create_dataset("Step list", data=np.asarray([(name,) for name in steps], dtype=list_dtype))
        file.create_dataset("Log list", data=np.asarray([(name,) for name in traces], dtype=list_dtype))

        data_group = file.create_group("Data")
        if step_values is None:
            scalar = np.zeros((1, 0, 1), dtype=np.float64)
            scalar_names = np.asarray([], dtype=name_info_dtype)
            dimensions = np.asarray([], dtype=np.int32)
        else:
            scalar = np.stack((np.asarray(step_values, dtype=np.float64), np.ones(n_entries)), axis=0)
            scalar = scalar.reshape(1, 2, n_entries)
            scalar_names = np.asarray([("Current", ""), ("Auxiliary", "")], dtype=name_info_dtype)
            dimensions = np.asarray([n_entries], dtype=np.int32)
        data_group.create_dataset("Data", data=scalar)
        data_group.create_dataset("Channel names", data=scalar_names)
        data_group.attrs["Step dimensions"] = dimensions
        data_group.attrs["Completed"] = True

        trace_group = file.create_group("Traces")
        dt = float(frequency[1] - frequency[0]) if frequency.size > 1 else 1.0
        for name, values in traces.items():
            values = np.asarray(values, dtype=np.complex128)
            if values.ndim == 1:
                values = values[:, None]
            if values.shape != (frequency.size, n_entries):
                raise ValueError(f"Bad fixture shape for {name}: {values.shape}")
            raw = np.stack((values.real, values.imag), axis=1)
            dataset = trace_group.create_dataset(name, data=raw)
            dataset.attrs["complex"] = True
            dataset.attrs["x, name"] = "Frequency"
            dataset.attrs["x, unit"] = frequency_unit
            trace_group.create_dataset(f"{name}_N", data=np.asarray([frequency.size], dtype=np.int32))
            trace_group.create_dataset(
                f"{name}_t0dt", data=np.asarray([[frequency[0], dt]], dtype=np.float64)
            )
    return path


def _pair(tmp_path: Path, *, frequency=None, bg_frequency=None, bg_steps=None,
          denominator=1.0 + 0.25j, target_sij=("VNA - S21",), background_sij=None,
          target_entries=4, target_bad_value=None, target_active_step=True):
    frequency = np.asarray(frequency if frequency is not None else np.arange(1.0e9, 1.0e9 + 5e6, 1e6))
    bg_frequency = np.asarray(bg_frequency if bg_frequency is not None else frequency)
    if background_sij is None:
        background_sij = target_sij
    entries = np.arange(target_entries, dtype=np.float64)
    target_traces = {}
    for channel_index, name in enumerate(target_sij):
        values = (
            (1 + np.arange(frequency.size)[:, None] * 0.1 + channel_index)
            + 1j * (entries[None, :] * 0.2 + 0.5 + channel_index)
        )
        if target_bad_value is not None:
            values = values.astype(np.complex128)
            values[target_bad_value, 0] = complex(np.nan, 0.0)
        target_traces[name] = values
    background_entries = len(bg_steps) if bg_steps is not None else 1
    background_traces = {
        name: np.full((bg_frequency.size, background_entries), denominator, dtype=np.complex128)
        for name in background_sij
    }
    target_path = _build_labber_file(
        tmp_path / "target.hdf5", target_traces, frequency,
        step_values=np.arange(target_entries, dtype=np.float64) if target_active_step else None,
        unindexed_entries=target_entries,
    )
    background_path = _build_labber_file(
        tmp_path / "background.hdf5", background_traces, bg_frequency,
        step_values=bg_steps,
    )
    return target_path, background_path


def test_complex_division_broadcasts_frequency_trace_over_sweeps():
    target = np.asarray([[2 + 2j, 4 + 0j], [3 + 0j, 0 + 6j]])
    background = np.asarray([1 + 1j, 3 + 0j])
    result = divide_complex_trace(target, background)
    assert np.allclose(result, target / background[:, None])
    assert result.shape == target.shape


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_complex_division_rejects_nonfinite_measurements(bad):
    with pytest.raises(DeBackgroundCompatibilityError, match="NaN or Inf"):
        divide_complex_trace(np.asarray([[complex(bad, 0)]]), np.asarray([1 + 0j]))


def test_complex_division_rejects_zero_denominator():
    with pytest.raises(DeBackgroundCompatibilityError, match="zero complex denominator"):
        divide_complex_trace(np.asarray([[1 + 0j]]), np.asarray([0 + 0j]))


def test_inspection_supports_arbitrary_shared_s_parameters(tmp_path):
    names = ("VNA - S11", "VNA - S31")
    target, background = _pair(tmp_path, target_sij=names)
    report = inspect_debackground(target, background, "VNA - S31")
    assert report.channel_options == names
    assert report.selected_channel == "VNA - S31"
    assert report.can_generate


def test_missing_s_parameter_is_reported_not_substituted(tmp_path):
    target, background = _pair(
        tmp_path,
        target_sij=("VNA - S21", "VNA - S11"),
        background_sij=("VNA - S11",),
    )
    report = inspect_debackground(target, background, "VNA - S21")
    assert not report.can_generate
    assert report.selected_channel == "VNA - S21"
    assert next(check for check in report.checks if check.label == "S Parameter").status == "fail"
    with pytest.raises(DeBackgroundCompatibilityError, match="S Parameter"):
        process_debackground(target, background, "VNA - S21", tmp_path / "bad.hdf5")


def test_frequency_point_count_mismatch_disables_generation(tmp_path):
    target, background = _pair(
        tmp_path, frequency=np.arange(1.0e9, 1.005e9, 1e6),
        bg_frequency=np.arange(1.0e9, 1.006e9, 1e6),
    )
    report = inspect_debackground(target, background)
    assert not report.can_generate
    assert next(check for check in report.checks if check.label == "Frequency points").status == "fail"


def test_frequency_value_mismatch_disables_generation_without_resampling(tmp_path):
    frequency = np.arange(1.0e9, 1.005e9, 1e6)
    target, background = _pair(
        tmp_path, frequency=frequency, bg_frequency=frequency + 1.0,
    )
    report = inspect_debackground(target, background)
    grid = next(check for check in report.checks if check.label == "Frequency grid")
    assert grid.status == "fail"
    assert "Mismatch" in grid.detail
    assert not report.can_generate


def test_reversed_frequency_order_is_rejected(tmp_path):
    frequency = np.arange(1.0e9, 1.005e9, 1e6)
    target, background = _pair(
        tmp_path, frequency=frequency, bg_frequency=frequency[::-1],
    )
    report = inspect_debackground(target, background)
    assert not report.can_generate
    assert "ordering differs" in next(check for check in report.checks if check.label == "Frequency grid").detail


def test_background_with_active_sweep_is_rejected(tmp_path):
    target, background = _pair(tmp_path, bg_steps=np.asarray([1.0, 2.0]))
    report = inspect_debackground(target, background)
    assert not report.can_generate
    shape = next(check for check in report.checks if check.label == "Background shape")
    assert shape.status == "fail"
    assert "without active sweep dimensions" in shape.detail


def test_target_multiple_entries_without_sweep_coordinates_is_rejected(tmp_path):
    target, background = _pair(tmp_path, target_entries=2, target_active_step=False)
    report = inspect_debackground(target, background)
    target_check = next(check for check in report.checks if check.label == "Target")
    assert target_check.status == "fail"
    assert "no active step dimension" in target_check.detail
    assert not report.can_generate


def test_near_zero_background_denominator_is_explicit_warning(tmp_path):
    target, background = _pair(tmp_path, denominator=1e-12 + 0j)
    report = inspect_debackground(target, background)
    denominator = next(check for check in report.checks if check.label == "Denominator")
    assert denominator.status == "warning"
    assert "Near-zero" in denominator.detail
    assert not report.can_generate


def test_processing_creates_labber_copy_and_changes_only_selected_trace(tmp_path):
    frequency = np.arange(1.0e9, 1.005e9, 1e6)
    target, background = _pair(tmp_path, frequency=frequency)
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    bg_before = hashlib.sha256(background.read_bytes()).hexdigest()
    output = process_debackground(target, background, "VNA - S21")
    assert output == target.with_name("target_debg.hdf5")
    assert output.is_file()
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before
    assert hashlib.sha256(background.read_bytes()).hexdigest() == bg_before

    source_reader, output_reader = HDF5Reader(target), HDF5Reader(output)
    try:
        source_nodes = {node.path: node for node in source_reader.tree()}
        output_nodes = {node.path: node for node in output_reader.tree()}
        assert source_nodes.keys() == output_nodes.keys()
        changed = []
        for path, source_node in source_nodes.items():
            output_node = output_nodes[path]
            assert (source_node.kind, source_node.shape, source_node.dtype) == (
                output_node.kind, output_node.shape, output_node.dtype
            )
            if source_node.kind == "dataset" and not np.array_equal(
                source_reader.read(path), output_reader.read(path)
            ):
                changed.append(path)
        assert changed == ["/Traces/VNA - S21"]
        assert source_reader.get_attrs("/Traces/VNA - S21") == output_reader.get_attrs("/Traces/VNA - S21")
    finally:
        source_reader.close()
        output_reader.close()

    generated = load_experiment(output)
    try:
        assert generated.get_data("VNA - S21", "raw").shape == (frequency.size, 4)
    finally:
        generated.close()


def test_output_collision_requires_explicit_overwrite(tmp_path):
    frequency = np.arange(1.0e9, 1.005e9, 1e6)
    target, background = _pair(tmp_path, frequency=frequency)
    output = tmp_path / "result.hdf5"
    output.write_bytes(b"existing output")
    with pytest.raises(DeBackgroundOutputExists):
        process_debackground(target, background, "VNA - S21", output)
    assert output.read_bytes() == b"existing output"
    process_debackground(target, background, "VNA - S21", output, overwrite=True)
    assert output.read_bytes() != b"existing output"


def test_nonfinite_target_fails_without_leaving_partial_output(tmp_path):
    target, background = _pair(tmp_path, target_bad_value=2)
    output = tmp_path / "failed.hdf5"
    with pytest.raises(DeBackgroundCompatibilityError, match="NaN or Inf"):
        process_debackground(target, background, "VNA - S21", output)
    assert not output.exists()
    assert not list(tmp_path.glob(".failed.hdf5.*.tmp"))


def test_missing_source_and_pre_cancelled_work_fail_cleanly(tmp_path):
    missing = tmp_path / "missing.hdf5"
    with pytest.raises(DeBackgroundError, match="Target file is missing"):
        inspect_debackground(missing, tmp_path / "other-missing.hdf5")

    pair_dir = tmp_path / "pair"
    pair_dir.mkdir()
    target, background = _pair(pair_dir)
    cancelled = threading.Event()
    cancelled.set()
    output = pair_dir / "cancelled.hdf5"
    with pytest.raises(DeBackgroundCancelled):
        process_debackground(
            target, background, "VNA - S21", output, cancel_event=cancelled,
        )
    assert not output.exists()
    assert not list(pair_dir.glob(".cancelled.hdf5.*.tmp"))


def test_gui_modules_do_not_import_h5py():
    gui_dir = PROJECT_ROOT / "app" / "gui"
    for filename in ("debackground_dialog.py", "debackground_worker.py", "data_picker_dialog.py"):
        tree = ast.parse((gui_dir / filename).read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        assert "h5py" not in imported


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def test_reference_rsmep_output_matches_legacy_and_preserves_labber_structure(tmp_path):
    reference = PROJECT_ROOT.parent / "development_reference" / "debackground"
    target = reference / "0828 RSMEP_1.hdf5"
    legacy = reference / "0828 RSMEP_1_debg.hdf5"
    background = (
        PROJECT_ROOT.parent / "Data" / "PRL RSMEP best data" / "2026" / "08"
        / "Data_0828" / "0828 5.0197~5.0297GHz BG.hdf5"
    )
    if not all(path.is_file() for path in (target, legacy, background)):
        pytest.skip("Reference RSMEP Target, legacy output, or matching Background is unavailable.")
    source_hashes = {path: _sha256(path) for path in (target, legacy, background)}
    output = process_debackground(target, background, "VNA - S21", tmp_path / "rsmep_debg.hdf5")

    generated = load_experiment(output)
    expected = load_experiment(legacy)
    raw = load_experiment(target)
    bg = load_experiment(background)
    try:
        result = generated.get_data("VNA - S21", "raw")
        expected_data = expected.get_data("VNA - S21", "raw")
        assert result.shape == expected_data.shape == (501, 855)
        assert np.array_equal(result, expected_data)
        assert generated.vector_traces["VNA - S21"].x_name == "Frequency"
        assert np.array_equal(
            generated.vector_traces["VNA - S21"].x_values,
            raw.vector_traces["VNA - S21"].x_values,
        )
        expected_dims = [(axis.channel.name, len(axis.values)) for axis in expected.step_axes]
        result_dims = [(axis.channel.name, len(axis.values)) for axis in generated.step_axes]
        assert result_dims == expected_dims == [("Average Current", 855)]

        direct_division = divide_complex_trace(
            raw.get_data("VNA - S21", "raw"),
            bg.get_data("VNA - S21", "raw"),
        )
        difference = direct_division - expected_data
        magnitude = np.abs(difference)
        assert float(magnitude.max()) == pytest.approx(0.0003446285685799601, rel=1e-10)
        assert float(magnitude.mean()) == pytest.approx(0.0001171463420854955, rel=1e-10)
        assert float(np.sqrt(np.mean(magnitude ** 2))) == pytest.approx(0.00014190745558077304, rel=1e-10)
        assert float(np.max(np.abs(direct_division.real - expected_data.real))) == pytest.approx(0.00024413898236752551)
        assert float(np.max(np.abs(direct_division.imag - expected_data.imag))) == pytest.approx(0.00024413945690415773)
        assert float(np.max(np.abs(np.abs(direct_division) - np.abs(expected_data)))) == pytest.approx(0.00034417441827261364)
        assert float(np.max(np.abs(np.angle(direct_division) - np.angle(expected_data)))) == pytest.approx(0.00043480884374957895)
    finally:
        generated.close()
        expected.close()
        raw.close()
        bg.close()

    target_reader, output_reader, legacy_reader = map(HDF5Reader, (target, output, legacy))
    try:
        target_nodes = {node.path: node for node in target_reader.tree()}
        output_nodes = {node.path: node for node in output_reader.tree()}
        legacy_nodes = {node.path: node for node in legacy_reader.tree()}
        assert target_nodes.keys() == output_nodes.keys() == legacy_nodes.keys()
        assert len(output_nodes) == 79
        assert not [
            path for path, node in target_nodes.items()
            if (node.kind, node.shape, node.dtype) != (
                output_nodes[path].kind, output_nodes[path].shape, output_nodes[path].dtype
            )
        ]
        changed = [
            path for path, node in target_nodes.items()
            if node.kind == "dataset" and not np.array_equal(
                target_reader.read(path), output_reader.read(path)
            )
        ]
        assert changed == ["/Traces/VNA - S21"]
        assert target_reader.get_attrs("/") == output_reader.get_attrs("/")
        assert target_reader.get_attrs("/Views/Current view") == output_reader.get_attrs("/Views/Current view")
        assert target_reader.get_dataset("/Traces/VNA - S21").dtype == output_reader.get_dataset("/Traces/VNA - S21").dtype
        assert target_reader.get_dataset("/Traces/VNA - S21").shape == output_reader.get_dataset("/Traces/VNA - S21").shape
        # The legacy writer changed saved cursor/view attributes; the new writer
        # intentionally preserves the Target's unrelated Labber view state.
        assert target_reader.get_attrs("/Views/Current view") != legacy_reader.get_attrs("/Views/Current view")
    finally:
        target_reader.close()
        output_reader.close()
        legacy_reader.close()
    assert {path: _sha256(path) for path in source_hashes} == source_hashes
