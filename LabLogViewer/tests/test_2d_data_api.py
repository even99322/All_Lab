"""
tests/test_2d_data_api.py — Phase 6

Tests for the core-layer 2D surface API: Experiment.get_2d_data(),
ChannelManager.get_2d_axis_candidates(), and CachedExperiment.get_2d_data().
No GUI/Qt dependency - these validate the data layer independently of
any widget, per the "HDF5 -> Reader -> Parser -> Experiment ->
ChannelManager -> GUI" layering.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.hdf5_reader import HDF5Reader
from app.core.labber_parser import AutoDetector
from app.core.channel_manager import ChannelManager
from app.core.cache import CachedExperiment, LRUDataCache
from app.core.data_model import Data2DError

from tests.real_data import BIG_FILE, SMALL_FILE

pytestmark = pytest.mark.skipif(
    not (SMALL_FILE.exists() and BIG_FILE.exists()),
    reason="Real Labber sample files not present in this environment.",
)


# ---- ChannelManager.get_2d_axis_candidates() -------------------------------

def test_2d_axis_candidates_big_file():
    reader = HDF5Reader(BIG_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        mgr = ChannelManager(exp)
        x_candidates, y_candidates = mgr.get_2d_axis_candidates("VNA - S21")
        assert x_candidates == ["Frequency", "Average Current"]
        assert y_candidates == ["Frequency", "Average Current"]
    finally:
        exp.close()


def test_2d_axis_candidates_small_file_no_active_sweep():
    """The single-point file has no active step axes, so Y candidates
    must be empty - this is the structural signal the GUI uses to
    disable/empty the Y dropdown, not a hardcoded check."""
    reader = HDF5Reader(SMALL_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        mgr = ChannelManager(exp)
        x_candidates, y_candidates = mgr.get_2d_axis_candidates("VNA - S21")
        assert x_candidates == []
        assert y_candidates == []
    finally:
        exp.close()


# ---- Experiment.get_2d_data() - the main verification ----------------------

def test_get_2d_data_full_surface_shape_and_orientation():
    """The most important Phase 6 check: Frequency x Average Current x
    S21 must be a full 855x501 surface, not a single sweep entry, with
    X truly mapping to Frequency and Y truly mapping to Average Current."""
    reader = HDF5Reader(BIG_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        grid = exp.get_2d_data("Frequency", "Average Current", "VNA - S21", transform="magnitude_db")

        assert grid.x_values.shape == (501,)
        assert grid.y_values.shape == (855,)
        assert grid.z_values.shape == (855, 501)  # (ny, nx)

        assert grid.x_name == "Frequency"
        assert grid.x_unit == "Hz"
        assert grid.y_name == "Average Current"
        assert grid.y_unit == "mA"
        assert grid.z_name == "VNA - S21"

        # X axis really is frequency: known start/stop from Phase 1 findings
        assert grid.x_values[0] == pytest.approx(5.0197e9)
        assert grid.x_values[-1] == pytest.approx(5.0297e9)

        # Y axis really is Average Current: known range from Phase 3 findings
        assert grid.y_values[0] == pytest.approx(162.594, abs=1e-3)
        assert grid.y_values[-1] == pytest.approx(163.021, abs=1e-3)

        assert np.isfinite(grid.z_values).all()
    finally:
        exp.close()


def test_get_2d_data_z_value_matches_1d_get_data():
    """Cross-check: a specific (x_idx, y_idx) point in the 2D grid must
    equal what get_data() returns for that same sweep entry / point -
    i.e. the 2D API isn't reshuffling data, just reorganizing the same
    underlying values."""
    reader = HDF5Reader(BIG_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        grid = exp.get_2d_data("Frequency", "Average Current", "VNA - S21", transform="magnitude_db")

        entry_idx = 300
        point_idx = 100
        expected = exp.get_data(
            "VNA - S21", transform="magnitude_db", entry_slice=slice(entry_idx, entry_idx + 1)
        ).reshape(-1)[point_idx]

        assert grid.z_values[entry_idx, point_idx] == pytest.approx(expected)
    finally:
        exp.close()


def test_get_2d_data_different_transforms():
    reader = HDF5Reader(BIG_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        grid_db = exp.get_2d_data("Frequency", "Average Current", "VNA - S21", transform="magnitude_db")
        grid_mag = exp.get_2d_data("Frequency", "Average Current", "VNA - S21", transform="magnitude")
        grid_phase = exp.get_2d_data("Frequency", "Average Current", "VNA - S21", transform="phase_deg")

        assert np.allclose(20 * np.log10(grid_mag.z_values), grid_db.z_values, equal_nan=True)
        assert grid_phase.z_values.min() >= -180.01
        assert grid_phase.z_values.max() <= 180.01
    finally:
        exp.close()


# ---- Data2DError validation (spec §12) --------------------------------------

def test_get_2d_data_wrong_x_raises_data2derror():
    reader = HDF5Reader(BIG_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        with pytest.raises(Data2DError):
            exp.get_2d_data("Average Current", "Average Current", "VNA - S21")
    finally:
        exp.close()


def test_get_2d_data_unknown_y_raises_data2derror():
    reader = HDF5Reader(BIG_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        with pytest.raises(Data2DError):
            exp.get_2d_data("Frequency", "Not A Real Channel", "VNA - S21")
    finally:
        exp.close()


def test_get_2d_data_small_file_no_active_sweep_raises():
    """The single-point file has zero active step axes, so no Y
    channel can ever be a valid active sweep axis - must raise
    cleanly, not crash or silently return a degenerate 1x501 grid."""
    reader = HDF5Reader(SMALL_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        with pytest.raises(Data2DError):
            exp.get_2d_data("Frequency", "Average Current", "VNA - S21")
    finally:
        exp.close()


# ---- CachedExperiment.get_2d_data() caching --------------------------------

def test_cached_experiment_2d_caches_and_is_fast():
    reader = HDF5Reader(BIG_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        cached = CachedExperiment(exp, LRUDataCache())
        grid1 = cached.get_2d_data("Frequency", "Average Current", "VNA - S21", transform="magnitude_db")
        grid2 = cached.get_2d_data("Frequency", "Average Current", "VNA - S21", transform="magnitude_db")
        assert grid1 is grid2  # same cached object, not recomputed
        assert cached.cache.stats()["hits"] == 1
        assert cached.cache.stats()["misses"] == 1
    finally:
        exp.close()


def test_cached_experiment_2d_different_transform_is_separate_cache_entry():
    reader = HDF5Reader(BIG_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        cached = CachedExperiment(exp, LRUDataCache())
        grid_db = cached.get_2d_data("Frequency", "Average Current", "VNA - S21", transform="magnitude_db")
        grid_phase = cached.get_2d_data("Frequency", "Average Current", "VNA - S21", transform="phase_deg")
        assert grid_db is not grid_phase
        assert not np.allclose(grid_db.z_values, grid_phase.z_values)
    finally:
        exp.close()
