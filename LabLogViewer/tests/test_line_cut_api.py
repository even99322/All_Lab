"""
tests/test_line_cut_api.py — Phase 8

Core data-layer tests for extract_line_cut() (X Cut / Y Cut). Uses
both the real big sample file and the same synthetic Labber-file
builder introduced in Phase 7 (tests/test_nd_slice_synthetic.py) to
validate 3D/4D + N-D slice integration, since real sample files only
ever have 0-1 active sweep dimensions.

Covers the Phase 8 test checklist items 1-13 at the data-layer level
(GUI-level checks are in tests/test_gui_linecut_smoke.py):
  1/2. 3D synthetic X Cut / Y Cut
  3/4. 4D synthetic X Cut / Y Cut
  5. Physical coordinate correctness
  6. Non-uniform coordinate correctness
  7. Nearest-coordinate selection
  8/9/10. Complex data / magnitude dB / phase line cuts
  11. Crosshair Z value (covered via Plot2DWidget tests in
      test_gui_linecut_smoke.py, which exercises the real widget)
  12. N-D slice + line cut integration
  13. X/Y swapped dimensions
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.hdf5_reader import HDF5Reader
from app.core.labber_parser import AutoDetector
from app.core.data_model import extract_line_cut

# reuse the Phase 7 synthetic Labber-file builder rather than
# duplicating it - it already produces a real, parseable HDF5 file
# with deterministic Z values for exact verification.
from tests.test_nd_slice_synthetic import _build_synthetic_labber_file

from tests.real_data import BIG_FILE

real_file_pytestmark = pytest.mark.skipif(
    not BIG_FILE.exists(), reason="Real Labber sample file not present in this environment."
)


# --------------------------------------------------------------------------
# Fixtures (mirrors test_nd_slice_synthetic.py's fixtures)
# --------------------------------------------------------------------------

@pytest.fixture()
def synthetic_3d_experiment(tmp_path):
    """Frequency (6 pts) x Current (5 pts) x Field (4 pts, non-uniform)."""
    freq_values = np.linspace(1e9, 1.5e9, 6)
    current_values = np.linspace(0.0, 4.0, 5)
    field_values = np.array([0.0, 1.0, 2.0, 4.0])  # non-uniform

    path = tmp_path / "linecut_3d.hdf5"
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
    """Frequency (4 pts) x Current (3 pts) x Field (3 pts) x Power (2 pts)."""
    freq_values = np.linspace(2e9, 2.3e9, 4)
    current_values = np.array([0.0, 5.0, 10.0])
    field_values = np.array([100.0, 200.0, 300.0])
    power_values = np.array([-10.0, 0.0])

    path = tmp_path / "linecut_4d.hdf5"
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
# 1/2. 3D synthetic X Cut / Y Cut
# --------------------------------------------------------------------------

def test_3d_x_cut_exact_values(synthetic_3d_experiment):
    exp = synthetic_3d_experiment
    grid = exp.get_nd_slice("Z", x_dim="Frequency", y_dim="Current", fixed={"Field": 2}, transform="raw")

    # X Cut fixes Y (Current) near 2.0 -> Current index 2 (values [0,1,2,3,4])
    xcut = extract_line_cut(grid, cut_axis="x", requested_value=2.0)
    assert xcut.x_values.shape == (6,)
    assert np.array_equal(xcut.x_values, grid.x_values)  # Frequency axis unchanged
    assert xcut.nearest_value == pytest.approx(2.0)
    assert xcut.cut_axis == "x"

    expected = np.array([(i + 1) + 3 * 10 + 3 * 100 for i in range(6)])  # i_current=2, i_field=2
    assert np.allclose(xcut.y_values.real, expected)


def test_3d_y_cut_exact_values(synthetic_3d_experiment):
    exp = synthetic_3d_experiment
    grid = exp.get_nd_slice("Z", x_dim="Frequency", y_dim="Current", fixed={"Field": 1}, transform="raw")

    # Y Cut fixes X (Frequency) near freq index 3
    freq_val = grid.x_values[3]
    ycut = extract_line_cut(grid, cut_axis="y", requested_value=freq_val)
    assert ycut.x_values.shape == (5,)
    assert np.array_equal(ycut.x_values, grid.y_values)  # Current axis
    assert ycut.nearest_value == pytest.approx(freq_val)
    assert ycut.cut_axis == "y"

    expected = np.array([(3 + 1) + (i + 1) * 10 + 2 * 100 for i in range(5)])  # i_field=1
    assert np.allclose(ycut.y_values.real, expected)


# --------------------------------------------------------------------------
# 3/4. 4D synthetic X Cut / Y Cut (with N-D slice fixed dims preserved)
# --------------------------------------------------------------------------

def test_4d_x_cut_preserves_other_fixed_dims(synthetic_4d_experiment):
    """12. N-D slice + line cut integration: the grid already has
    Power fixed via the N-D slice API - the line cut must inherit
    that, not silently drop it."""
    exp = synthetic_4d_experiment
    grid = exp.get_nd_slice(
        "Z", x_dim="Frequency", y_dim="Field", fixed={"Current": 1, "Power": 1}, transform="raw"
    )
    xcut = extract_line_cut(grid, cut_axis="x", requested_value=200.0)  # Field index 1
    assert "Current" in xcut.fixed_dims
    assert "Power" in xcut.fixed_dims
    assert "Field" in xcut.fixed_dims  # newly fixed by the line cut itself
    assert xcut.fixed_dims["Current"]["index"] == 1
    assert xcut.fixed_dims["Power"]["index"] == 1
    assert xcut.fixed_dims["Field"]["value"] == pytest.approx(200.0)


def test_4d_y_cut_preserves_other_fixed_dims(synthetic_4d_experiment):
    exp = synthetic_4d_experiment
    grid = exp.get_nd_slice(
        "Z", x_dim="Current", y_dim="Power", fixed={"Frequency": 2, "Field": 0}, transform="raw"
    )
    ycut = extract_line_cut(grid, cut_axis="y", requested_value=5.0)  # Current index 1
    assert ycut.fixed_dims["Frequency"]["index"] == 2
    assert ycut.fixed_dims["Field"]["index"] == 0
    assert ycut.fixed_dims["Current"]["value"] == pytest.approx(5.0)
    assert ycut.x_values.shape == (2,)  # Power has 2 points


# --------------------------------------------------------------------------
# 5. Physical coordinate correctness
# --------------------------------------------------------------------------

def test_physical_coordinates_not_indices(synthetic_3d_experiment):
    exp = synthetic_3d_experiment
    grid = exp.get_nd_slice("Z", x_dim="Frequency", y_dim="Current", fixed={"Field": 0}, transform="raw")
    xcut = extract_line_cut(grid, cut_axis="x", requested_value=3.0)
    # x_values must be the REAL frequency values, not [0,1,2,3,4,5]
    assert xcut.x_values[0] == pytest.approx(1e9)
    assert xcut.x_values[-1] == pytest.approx(1.5e9)
    assert not np.array_equal(xcut.x_values, np.arange(6))


# --------------------------------------------------------------------------
# 6/7. Non-uniform coordinates + nearest-sample selection
# --------------------------------------------------------------------------

def test_nearest_coordinate_snapping_non_uniform(synthetic_3d_experiment):
    """Field values are [0, 1, 2, 4] (non-uniform). Requesting 3.9
    must snap to 4.0, NOT to some linearly-interpolated index."""
    exp = synthetic_3d_experiment
    grid = exp.get_nd_slice("Z", x_dim="Frequency", y_dim="Field", fixed={"Current": 0}, transform="raw")
    # X Cut fixes Y = Field; request 3.9, should snap to 4.0 (index 3)
    xcut = extract_line_cut(grid, cut_axis="x", requested_value=3.9)
    assert xcut.nearest_value == pytest.approx(4.0)
    assert xcut.requested_value == pytest.approx(3.9)
    assert xcut.fixed_dims["Field"]["index"] == 3

    # request 1.4 - closer to 1.0 than to 2.0 (not a linear-formula index)
    xcut2 = extract_line_cut(grid, cut_axis="x", requested_value=1.4)
    assert xcut2.nearest_value == pytest.approx(1.0)


def test_requested_vs_nearest_both_reported():
    """The line cut result must always carry BOTH the requested value
    and the nearest real sample - never silently interpolate."""
    from app.core.data_model import Grid2DData

    grid = Grid2DData(
        x_values=np.array([0.0, 1.0, 2.0]),
        y_values=np.array([0.0, 5.0, 20.0]),  # non-uniform
        z_values=np.arange(9, dtype=float).reshape(3, 3),
        x_name="X", x_unit="u", y_name="Y", y_unit="v",
        z_name="Z", z_unit=None, transform="raw",
    )
    cut = extract_line_cut(grid, cut_axis="x", requested_value=12.0)  # between 5 and 20
    assert cut.requested_value == 12.0
    assert cut.nearest_value == 5.0  # closer to 5 than 20
    assert cut.nearest_index == 1


# --------------------------------------------------------------------------
# 8/9/10. Complex data / magnitude dB / phase line cuts
# --------------------------------------------------------------------------

@real_file_pytestmark
def test_complex_line_cut_magnitude_db_matches_heatmap():
    from app.core.labber_parser import load_experiment

    exp = load_experiment(str(BIG_FILE))
    try:
        grid = exp.get_2d_data("Frequency", "Average Current", "VNA - S21", transform="magnitude_db")
        xcut = extract_line_cut(grid, cut_axis="x", requested_value=162.8)
        iy = int(np.argmin(np.abs(grid.y_values - 162.8)))
        assert np.allclose(xcut.y_values, grid.z_values[iy, :])
        assert xcut.transform == "magnitude_db"
    finally:
        exp.close()


@real_file_pytestmark
def test_phase_line_cut():
    from app.core.labber_parser import load_experiment

    exp = load_experiment(str(BIG_FILE))
    try:
        grid = exp.get_2d_data("Frequency", "Average Current", "VNA - S21", transform="phase_deg")
        ycut = extract_line_cut(grid, cut_axis="y", requested_value=5.025e9)
        assert ycut.transform == "phase_deg"
        assert ycut.y_values.min() >= -180.01
        assert ycut.y_values.max() <= 180.01
    finally:
        exp.close()


@real_file_pytestmark
def test_line_cut_transform_consistency_across_calls():
    """9/10: the SAME grid, cut at the SAME point, with different
    transforms applied at the grid level, must produce DIFFERENT line
    cut values - proving the transform is honored, not silently
    dropped or fixed to 'raw'."""
    from app.core.labber_parser import load_experiment

    exp = load_experiment(str(BIG_FILE))
    try:
        grid_db = exp.get_2d_data("Frequency", "Average Current", "VNA - S21", transform="magnitude_db")
        grid_phase = exp.get_2d_data("Frequency", "Average Current", "VNA - S21", transform="phase_deg")

        cut_db = extract_line_cut(grid_db, cut_axis="x", requested_value=162.8)
        cut_phase = extract_line_cut(grid_phase, cut_axis="x", requested_value=162.8)

        assert not np.allclose(cut_db.y_values, cut_phase.y_values)
        assert cut_db.nearest_value == cut_phase.nearest_value  # same fixed Y regardless of transform
    finally:
        exp.close()


# --------------------------------------------------------------------------
# 13. X/Y swapped dimensions
# --------------------------------------------------------------------------

def test_line_cut_after_x_y_swap(synthetic_3d_experiment):
    """A line cut on a swapped-axes grid must still correctly identify
    which physical dimension it's fixing."""
    exp = synthetic_3d_experiment
    grid_normal = exp.get_nd_slice("Z", x_dim="Frequency", y_dim="Current", fixed={"Field": 0}, transform="raw")
    grid_swapped = exp.get_nd_slice("Z", x_dim="Current", y_dim="Frequency", fixed={"Field": 0}, transform="raw")

    # X Cut on normal grid fixes Current; Y Cut on swapped grid also fixes Current
    xcut = extract_line_cut(grid_normal, cut_axis="x", requested_value=2.0)
    ycut_swapped = extract_line_cut(grid_swapped, cut_axis="y", requested_value=2.0)

    assert xcut.fixed_dims["Current"]["index"] == ycut_swapped.fixed_dims["Current"]["index"]
    assert np.allclose(xcut.y_values.real, ycut_swapped.y_values.real)


# --------------------------------------------------------------------------
# Error handling
# --------------------------------------------------------------------------

def test_invalid_cut_axis_raises():
    from app.core.data_model import Grid2DData

    grid = Grid2DData(
        x_values=np.array([0.0, 1.0]), y_values=np.array([0.0, 1.0]),
        z_values=np.zeros((2, 2)), x_name="X", x_unit=None,
        y_name="Y", y_unit=None, z_name="Z", z_unit=None, transform="raw",
    )
    with pytest.raises(ValueError):
        extract_line_cut(grid, cut_axis="z", requested_value=0.0)
