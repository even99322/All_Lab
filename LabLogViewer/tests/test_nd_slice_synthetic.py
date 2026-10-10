"""
tests/test_nd_slice_synthetic.py — Phase 7

Real Labber sample files only exercise 0 or 1 active sweep dimensions
(see docs/hdf5_structure_report.md §7's open question about 2D+
sweeps). To genuinely validate the N-D Slice Explorer for 3D and 4D
data, this module builds small SYNTHETIC Labber-format HDF5 files from
scratch (matching the exact dtypes/groups LabberParserV2 expects,
verified against the real files' field names) and runs them through
the REAL parser pipeline - AutoDetector -> Experiment -> get_nd_slice
- rather than mocking the data model. Z values are a simple
deterministic function of (freq_idx, dim1_idx, dim2_idx, ...) so every
extracted slice can be checked exactly, not just "shape looks right".

Covers the Phase 7 test checklist:
  A. 3D synthetic dataset slicing
  B. 4D synthetic dataset slicing
  C. arbitrary X/Y dimension selection (including swapping)
  D. remaining dimensions correctly become slice dimensions
  E. physical coordinate value retrieval
  F. non-uniform coordinate handling
  G. complex data slicing (magnitude/phase transforms)
  H. 1D slice
  I. 2D heatmap slice
"""

from __future__ import annotations

import sys
from pathlib import Path

import h5py
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.hdf5_reader import HDF5Reader
from app.core.labber_parser import AutoDetector
from app.core.channel_manager import ChannelManager
from app.core.cache import CachedExperiment, LRUDataCache
from app.core.data_model import SliceError


# --------------------------------------------------------------------------
# Synthetic Labber file builder
# --------------------------------------------------------------------------

def _build_synthetic_labber_file(path: Path, step_axes: list[tuple[str, str, np.ndarray]],
                                  freq_values: np.ndarray, freq_unit: str = "Hz") -> None:
    """Builds a minimal but structurally faithful Labber HDF5 log with
    a single complex vector log channel ('Z') swept over an arbitrary
    number of active step axes, given as
    [(name, unit, coordinate_values), ...] in Step-list order (first =
    outermost/slowest-varying, matching LabberParserV2's documented
    reshape assumption).

    Z is defined deterministically as:
        Z[i_freq, i_0, i_1, ...] = (i_freq + 1) + 10*(i_0+1) + 100*(i_1+1) + ...
    (imaginary part left at 0) so every extracted slice can be
    verified by exact arithmetic, not just shape.
    """
    n_freq = len(freq_values)
    active_sizes = [len(vals) for _, _, vals in step_axes]
    n_entries = int(np.prod(active_sizes)) if active_sizes else 1

    # Build the flat entries -> Z value mapping using the SAME
    # nesting convention documented in get_full_nd_array: first-listed
    # active axis is outermost/slowest (reshape via row-major C order).
    channel_names_dtype = np.dtype([("name", h5py.special_dtype(vlen=str)),
                                     ("info", h5py.special_dtype(vlen=str))])
    channels_dtype = np.dtype([
        ("name", h5py.special_dtype(vlen=str)), ("instrument", h5py.special_dtype(vlen=str)),
        ("quantity", h5py.special_dtype(vlen=str)),
        ("unitPhys", h5py.special_dtype(vlen=str)), ("unitInstr", h5py.special_dtype(vlen=str)),
    ])
    step_list_dtype = np.dtype([("channel_name", h5py.special_dtype(vlen=str))])
    log_list_dtype = np.dtype([("channel_name", h5py.special_dtype(vlen=str))])

    with h5py.File(path, "w") as f:
        f.attrs["log_name"] = "synthetic_nd_test"
        f.attrs["creation_time"] = 0.0
        f.attrs["comment"] = ""
        f.attrs["version"] = "synthetic"

        # /Channels
        rows = []
        for name, unit, _ in step_axes:
            rows.append((name, "SyntheticInstrument", name, unit, unit))
        rows.append(("Z", "SyntheticInstrument", "Z", "", ""))
        f.create_dataset("Channels", data=np.array(rows, dtype=channels_dtype))

        # /Step list
        step_rows = [(name,) for name, _, _ in step_axes]
        f.create_dataset("Step list", data=np.array(step_rows, dtype=step_list_dtype))

        # /Log list
        f.create_dataset("Log list", data=np.array([("Z",)], dtype=log_list_dtype))

        # /Data group: scalar step-channel value matrix, one row per
        # active axis, one column per flattened sweep entry.
        data_grp = f.create_group("Data")
        if active_sizes:
            grids = np.meshgrid(*[vals for _, _, vals in step_axes], indexing="ij")
            scalar_matrix = np.stack([g.reshape(-1) for g in grids], axis=0)  # (n_axes, n_entries)
        else:
            scalar_matrix = np.zeros((0, 1))
        data_grp.create_dataset("Data", data=scalar_matrix.reshape(1, len(step_axes), n_entries))
        data_grp.create_dataset(
            "Channel names",
            data=np.array([(name, "") for name, _, _ in step_axes], dtype=channel_names_dtype),
        )
        data_grp.attrs["Step dimensions"] = np.array(active_sizes, dtype=np.int32)
        data_grp.attrs["Completed"] = True

        # /Traces: the complex vector channel
        traces_grp = f.create_group("Traces")
        real = np.zeros((n_freq, n_entries))
        for flat_idx in range(n_entries):
            idx_tuple = np.unravel_index(flat_idx, active_sizes) if active_sizes else ()
            base = sum((idx_tuple[k] + 1) * (10 ** (k + 1)) for k in range(len(idx_tuple)))
            for i_freq in range(n_freq):
                real[i_freq, flat_idx] = (i_freq + 1) + base
        imag = np.zeros_like(real)
        trace_data = np.stack([real, imag], axis=1)  # (n_freq, 2, n_entries)

        ds = traces_grp.create_dataset("Z", data=trace_data)
        ds.attrs["complex"] = True
        ds.attrs["x, name"] = "Frequency"
        ds.attrs["x, unit"] = freq_unit
        traces_grp.create_dataset("Z_N", data=np.array([n_freq]))
        t0 = float(freq_values[0])
        dt = float(freq_values[1] - freq_values[0]) if n_freq > 1 else 1.0
        traces_grp.create_dataset("Z_t0dt", data=np.array([[t0, dt]]))


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture()
def synthetic_3d_experiment(tmp_path):
    """Frequency (6 pts, uniform) x Current (5 pts, uniform) x
    Field (4 pts, NON-uniform) - 3 total dimensions."""
    freq_values = np.linspace(1e9, 1.5e9, 6)
    current_values = np.linspace(0.0, 4.0, 5)
    field_values = np.array([0.0, 1.0, 2.0, 4.0])  # non-uniform (diffs: 1,1,2)

    path = tmp_path / "synthetic_3d.hdf5"
    _build_synthetic_labber_file(
        path,
        step_axes=[("Current", "mA", current_values), ("Field", "mT", field_values)],
        freq_values=freq_values,
    )
    reader = HDF5Reader(path)
    exp = AutoDetector.detect_and_parse(reader)
    yield exp
    exp.close()


@pytest.fixture()
def synthetic_4d_experiment(tmp_path):
    """Frequency (4 pts) x Current (3 pts) x Field (3 pts) x Power (2
    pts) - 4 total dimensions."""
    freq_values = np.linspace(2e9, 2.3e9, 4)
    current_values = np.array([0.0, 5.0, 10.0])
    field_values = np.array([100.0, 200.0, 300.0])
    power_values = np.array([-10.0, 0.0])

    path = tmp_path / "synthetic_4d.hdf5"
    _build_synthetic_labber_file(
        path,
        step_axes=[
            ("Current", "mA", current_values),
            ("Field", "mT", field_values),
            ("Power", "dBm", power_values),
        ],
        freq_values=freq_values,
    )
    reader = HDF5Reader(path)
    exp = AutoDetector.detect_and_parse(reader)
    yield exp
    exp.close()


# --------------------------------------------------------------------------
# A. 3D synthetic dataset slicing
# --------------------------------------------------------------------------

def test_3d_list_dimensions(synthetic_3d_experiment):
    exp = synthetic_3d_experiment
    dims = exp.list_dimensions("Z")
    assert [d.name for d in dims] == ["Frequency", "Current", "Field"]
    assert [d.size for d in dims] == [6, 5, 4]
    assert dims[0].unit == "Hz"
    assert dims[1].unit == "mA"
    assert dims[2].unit == "mT"


def test_3d_full_array_shape(synthetic_3d_experiment):
    exp = synthetic_3d_experiment
    dims, arr = exp.get_full_nd_array("Z", transform="raw")
    assert arr.shape == (6, 5, 4)


def test_3d_1d_slice_exact_values(synthetic_3d_experiment):
    """H. 1D slice: X=Frequency, fix Current=idx2, Field=idx1 -> exact
    values checked against the deterministic Z formula."""
    exp = synthetic_3d_experiment
    result = exp.get_nd_slice("Z", x_dim="Frequency", fixed={"Current": 2, "Field": 1}, transform="raw")
    assert result.y_values.shape == (6,)
    expected = np.array([
        (i_freq + 1) + (2 + 1) * 10 + (1 + 1) * 100 for i_freq in range(6)
    ])
    assert np.allclose(result.y_values.real, expected)
    assert result.fixed_dims["Current"]["index"] == 2
    assert result.fixed_dims["Field"]["index"] == 1


def test_3d_2d_slice_exact_values(synthetic_3d_experiment):
    """I. 2D heatmap slice: X=Frequency, Y=Current, fix Field=idx3."""
    exp = synthetic_3d_experiment
    grid = exp.get_nd_slice("Z", x_dim="Frequency", y_dim="Current", fixed={"Field": 3}, transform="raw")
    assert grid.z_values.shape == (5, 6)  # (ny, nx)
    for i_current in range(5):
        for i_freq in range(6):
            expected = (i_freq + 1) + (i_current + 1) * 10 + (3 + 1) * 100
            assert grid.z_values[i_current, i_freq].real == pytest.approx(expected)
    assert grid.fixed_dims["Field"]["index"] == 3


def test_3d_remaining_dims_become_fixed(synthetic_3d_experiment):
    """D. Selecting X=Frequency, Y=Field leaves Current as the ONLY
    remaining/fixed dimension."""
    exp = synthetic_3d_experiment
    dims = exp.list_dimensions("Z")
    varying = {"Frequency", "Field"}
    remaining = [d.name for d in dims if d.name not in varying]
    assert remaining == ["Current"]


# --------------------------------------------------------------------------
# C. Arbitrary X/Y selection, including swapping / non-trace pairs
# --------------------------------------------------------------------------

def test_3d_swap_x_and_y(synthetic_3d_experiment):
    exp = synthetic_3d_experiment
    grid_a = exp.get_nd_slice("Z", x_dim="Frequency", y_dim="Current", fixed={"Field": 0}, transform="raw")
    grid_b = exp.get_nd_slice("Z", x_dim="Current", y_dim="Frequency", fixed={"Field": 0}, transform="raw")
    assert np.allclose(grid_a.z_values.T.real, grid_b.z_values.real)


def test_3d_x_y_both_non_trace_axes(synthetic_3d_experiment):
    """X and Y can both be active step axes (excluding the trace's own
    axis entirely) - Frequency becomes the fixed dimension instead."""
    exp = synthetic_3d_experiment
    grid = exp.get_nd_slice("Z", x_dim="Current", y_dim="Field", fixed={"Frequency": 2}, transform="raw")
    assert grid.z_values.shape == (4, 5)  # (n_field, n_current)
    for i_field in range(4):
        for i_current in range(5):
            expected = (2 + 1) + (i_current + 1) * 10 + (i_field + 1) * 100
            assert grid.z_values[i_field, i_current].real == pytest.approx(expected)
    assert grid.fixed_dims["Frequency"]["index"] == 2


# --------------------------------------------------------------------------
# E. Physical coordinate value retrieval
# --------------------------------------------------------------------------

def test_3d_physical_coordinate_retrieval(synthetic_3d_experiment):
    exp = synthetic_3d_experiment
    dims = exp.list_dimensions("Z")
    current_dim = next(d for d in dims if d.name == "Current")
    assert current_dim.value_at(2) == pytest.approx(2.0)
    assert current_dim.min == pytest.approx(0.0)
    assert current_dim.max == pytest.approx(4.0)
    assert current_dim.nearest_index(2.1) == 2


# --------------------------------------------------------------------------
# F. Non-uniform coordinate handling
# --------------------------------------------------------------------------

def test_3d_non_uniform_field_dimension(synthetic_3d_experiment):
    exp = synthetic_3d_experiment
    dims = exp.list_dimensions("Z")
    field_dim = next(d for d in dims if d.name == "Field")
    assert list(field_dim.values) == [0.0, 1.0, 2.0, 4.0]
    assert field_dim.is_uniform is False
    assert field_dim.step is None
    # nearest_index must use the REAL array, not a linear formula
    assert field_dim.nearest_index(3.9) == 3  # closest to 4.0
    assert field_dim.nearest_index(1.4) == 1  # closest to 1.0 (not 1.4/step)


def test_3d_uniform_dimension_reports_step(synthetic_3d_experiment):
    exp = synthetic_3d_experiment
    dims = exp.list_dimensions("Z")
    current_dim = next(d for d in dims if d.name == "Current")
    assert current_dim.is_uniform is True
    assert current_dim.step == pytest.approx(1.0)


# --------------------------------------------------------------------------
# G. Complex data slicing with transforms
# --------------------------------------------------------------------------

def test_3d_transform_magnitude_and_phase(synthetic_3d_experiment):
    exp = synthetic_3d_experiment
    grid_mag = exp.get_nd_slice("Z", x_dim="Frequency", y_dim="Current", fixed={"Field": 0}, transform="magnitude")
    grid_raw = exp.get_nd_slice("Z", x_dim="Frequency", y_dim="Current", fixed={"Field": 0}, transform="raw")
    # imaginary part is always 0 in the synthetic data, so magnitude == real part
    assert np.allclose(grid_mag.z_values, np.abs(grid_raw.z_values))

    grid_phase = exp.get_nd_slice("Z", x_dim="Frequency", y_dim="Current", fixed={"Field": 0}, transform="phase_deg")
    assert np.allclose(grid_phase.z_values, 0.0)  # purely real, positive -> phase 0


# --------------------------------------------------------------------------
# Validation / error handling
# --------------------------------------------------------------------------

def test_3d_same_x_and_y_raises(synthetic_3d_experiment):
    exp = synthetic_3d_experiment
    with pytest.raises(SliceError):
        exp.get_nd_slice("Z", x_dim="Frequency", y_dim="Frequency")


def test_3d_unknown_dimension_raises(synthetic_3d_experiment):
    exp = synthetic_3d_experiment
    with pytest.raises(SliceError):
        exp.get_nd_slice("Z", x_dim="NotADimension")


def test_3d_out_of_range_fixed_index_raises(synthetic_3d_experiment):
    exp = synthetic_3d_experiment
    with pytest.raises(SliceError):
        exp.get_nd_slice("Z", x_dim="Frequency", fixed={"Current": 999, "Field": 0})


# --------------------------------------------------------------------------
# B. 4D synthetic dataset slicing
# --------------------------------------------------------------------------

def test_4d_list_dimensions(synthetic_4d_experiment):
    exp = synthetic_4d_experiment
    dims = exp.list_dimensions("Z")
    assert [d.name for d in dims] == ["Frequency", "Current", "Field", "Power"]
    assert [d.size for d in dims] == [4, 3, 3, 2]


def test_4d_full_array_shape(synthetic_4d_experiment):
    exp = synthetic_4d_experiment
    dims, arr = exp.get_full_nd_array("Z", transform="raw")
    assert arr.shape == (4, 3, 3, 2)


def test_4d_two_remaining_dims_both_get_slice_controls(synthetic_4d_experiment):
    """D. With 4 total dims and X/Y chosen, exactly 2 dims remain and
    must both become independent slice controls (not merged/dropped)."""
    exp = synthetic_4d_experiment
    dims = exp.list_dimensions("Z")
    varying = {"Frequency", "Current"}
    remaining = [d.name for d in dims if d.name not in varying]
    assert remaining == ["Field", "Power"]


def test_4d_1d_slice_with_three_fixed_dims(synthetic_4d_experiment):
    exp = synthetic_4d_experiment
    result = exp.get_nd_slice(
        "Z", x_dim="Frequency", fixed={"Current": 1, "Field": 2, "Power": 1}, transform="raw"
    )
    assert result.y_values.shape == (4,)
    expected = np.array([
        (i_freq + 1) + 2 * 10 + 3 * 100 + 2 * 1000 for i_freq in range(4)
    ])
    assert np.allclose(result.y_values.real, expected)
    assert result.fixed_dims == {
        "Current": {"index": 1, "value": 5.0, "unit": "mA"},
        "Field": {"index": 2, "value": 300.0, "unit": "mT"},
        "Power": {"index": 1, "value": 0.0, "unit": "dBm"},
    }


def test_4d_2d_slice_with_two_fixed_dims(synthetic_4d_experiment):
    exp = synthetic_4d_experiment
    grid = exp.get_nd_slice(
        "Z", x_dim="Field", y_dim="Power", fixed={"Frequency": 0, "Current": 2}, transform="raw"
    )
    assert grid.z_values.shape == (2, 3)  # (n_power, n_field)
    for i_power in range(2):
        for i_field in range(3):
            expected = 1 + 3 * 10 + (i_field + 1) * 100 + (i_power + 1) * 1000
            assert grid.z_values[i_power, i_field].real == pytest.approx(expected)


def test_4d_mismatched_entries_raises_slice_error(tmp_path):
    """A corrupted/inconsistent file (the vector trace's entries count
    doesn't match the product of active step dimensions) must raise
    SliceError, not silently produce a garbled reshape."""
    freq_values = np.linspace(1e9, 1.1e9, 3)
    current_values = np.array([0.0, 1.0, 2.0])
    path = tmp_path / "broken.hdf5"
    _build_synthetic_labber_file(
        path, step_axes=[("Current", "mA", current_values)], freq_values=freq_values
    )
    # Corrupt the TRACE's entries count (not Data/Data) so the
    # mismatch is only visible once get_full_nd_array reads the vector
    # channel itself - Data/Data (3 entries) stays internally
    # consistent with 'Step dimensions', so parsing succeeds normally
    # and the mismatch is only caught where it matters: reshaping the
    # actual trace data.
    with h5py.File(path, "r+") as f:
        del f["Traces/Z"]
        bad_trace = np.zeros((3, 2, 4))  # 4 entries instead of the correct 3
        ds = f["Traces"].create_dataset("Z", data=bad_trace)
        ds.attrs["complex"] = True
        ds.attrs["x, name"] = "Frequency"
        ds.attrs["x, unit"] = "Hz"

    reader = HDF5Reader(path)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        with pytest.raises(SliceError):
            exp.get_full_nd_array("Z", transform="raw")
    finally:
        exp.close()


# --------------------------------------------------------------------------
# CachedExperiment / ChannelManager integration
# --------------------------------------------------------------------------

def test_cached_experiment_nd_slice_caches_full_array(synthetic_3d_experiment):
    exp = synthetic_3d_experiment
    cached = CachedExperiment(exp, LRUDataCache())

    r1 = cached.get_nd_slice("Z", x_dim="Frequency", fixed={"Current": 0, "Field": 0}, transform="raw")
    r2 = cached.get_nd_slice("Z", x_dim="Frequency", fixed={"Current": 3, "Field": 2}, transform="raw")
    r3 = cached.get_nd_slice("Z", x_dim="Frequency", fixed={"Current": 4, "Field": 3}, transform="raw")

    assert cached.cache.stats()["misses"] == 1  # full array read from HDF5 exactly once
    assert not np.allclose(r1.y_values.real, r2.y_values.real)  # different slices really differ
    assert not np.allclose(r2.y_values.real, r3.y_values.real)


def test_channel_manager_nd_api(synthetic_3d_experiment):
    exp = synthetic_3d_experiment
    mgr = ChannelManager(exp)
    dims = mgr.list_dimensions("Z")
    assert [d.name for d in dims] == ["Frequency", "Current", "Field"]

    result = mgr.get_nd_slice("Z", x_dim="Frequency", y_dim="Current", fixed={"Field": 1})
    assert result.z_values.shape == (5, 6)
