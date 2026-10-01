"""
tests/test_labber_parser.py

Validates the full HDF5Reader -> AutoDetector -> Experiment pipeline
against BOTH real sample files, specifically checking things that
differ between them (per project requirement §28: never assume one
file's shape generalizes). Skips gracefully if samples aren't present.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.hdf5_reader import HDF5Reader
from app.core.labber_parser import AutoDetector, LabberParserV2, UnsupportedLabberFormat
from app.core.channel_manager import ChannelManager
from app.core.cache import CachedExperiment, LRUDataCache

from tests.real_data import BIG_FILE, SMALL_FILE

pytestmark = pytest.mark.skipif(
    not (SMALL_FILE.exists() and BIG_FILE.exists()),
    reason="Real Labber sample files not present in this environment.",
)


# ---- can_parse / detection ------------------------------------------------

def test_can_parse_both_files():
    for f in (SMALL_FILE, BIG_FILE):
        with HDF5Reader(f) as reader:
            assert LabberParserV2.can_parse(reader)


def test_autodetector_rejects_non_labber_hdf5(tmp_path):
    import h5py
    fake = tmp_path / "not_labber.hdf5"
    with h5py.File(fake, "w") as f:
        f.create_dataset("random_data", data=np.arange(10))
    with HDF5Reader(fake) as reader:
        with pytest.raises(UnsupportedLabberFormat):
            AutoDetector.detect_and_parse(reader)


# ---- small file: single point, no active sweep --------------------------

def test_small_file_no_active_sweep():
    reader = HDF5Reader(SMALL_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        assert exp.n_active_step_dims == 0
        assert exp.has_vector_log_channel is True
        assert exp.total_dimensions == 1  # just the trace's own frequency axis
        assert "VNA - S21" in exp.vector_traces
        vt = exp.vector_traces["VNA - S21"]
        assert vt.complex is True
        assert vt.n_points == 501
        assert vt.n_entries == 1
    finally:
        exp.close()


def test_small_file_frequency_axis_reconstruction():
    """Directly checks the t0/dt -> x_values reconstruction against
    the known instrument config values (start=5.0197GHz, stop=5.0297GHz,
    501 points) — this is the single most important piece of derived
    logic in the whole parser, so it gets its own explicit test."""
    reader = HDF5Reader(SMALL_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        vt = exp.vector_traces["VNA - S21"]
        assert vt.x_values[0] == pytest.approx(5.0197e9)
        assert vt.x_values[-1] == pytest.approx(5.0297e9)
        assert len(vt.x_values) == 501
        # step should be (stop-start)/(n-1) = 1e7/500 = 20000 Hz
        assert (vt.x_values[1] - vt.x_values[0]) == pytest.approx(20000.0)
    finally:
        exp.close()


# ---- big file: active sweep + vector log channel together ----------------

def test_big_file_active_sweep_detected():
    reader = HDF5Reader(BIG_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        assert exp.n_active_step_dims == 1
        axis_names = [a.channel.name for a in exp.step_axes]
        assert axis_names == ["Average Current"]
        axis = exp.step_axes[0]
        assert len(axis.values) == 855
        assert axis.values[0] == pytest.approx(162.594, abs=1e-3)
        assert axis.values[-1] == pytest.approx(163.021, abs=1e-3)
        assert exp.total_dimensions == 2  # Average Current + trace's own Frequency axis
    finally:
        exp.close()


def test_big_file_vector_trace_entries_match_sweep_length():
    reader = HDF5Reader(BIG_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        vt = exp.vector_traces["VNA - S21"]
        assert vt.n_entries == 855
        assert vt.n_points == 501
    finally:
        exp.close()


def test_format_variant_differs_between_samples():
    """The two files must NOT be classified identically — this is a
    direct check against overfitting to one sample's shape."""
    with HDF5Reader(SMALL_FILE) as r1:
        e1 = AutoDetector.detect_and_parse(r1)
    with HDF5Reader(BIG_FILE) as r2:
        e2 = AutoDetector.detect_and_parse(r2)
    assert e1.format_variant != e2.format_variant
    assert e1.format_variant == "vector_only"
    assert e2.format_variant == "trace_log_channel"


# ---- get_data() lazy loading + transforms ---------------------------------

def test_get_data_magnitude_db_shape():
    reader = HDF5Reader(BIG_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        data = exp.get_data("VNA - S21", transform="magnitude_db", entry_slice=slice(0, 5))
        assert data.shape == (501, 5)
        assert np.isfinite(data).all()
    finally:
        exp.close()


def test_get_data_scalar_channel():
    reader = HDF5Reader(BIG_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        data = exp.get_data("Average Current", transform="raw")
        assert data.shape == (855,)
        assert data[0] == pytest.approx(162.594, abs=1e-3)
    finally:
        exp.close()


def test_get_data_unknown_channel_raises():
    reader = HDF5Reader(SMALL_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        with pytest.raises(Exception):
            exp.get_data("Definitely Not A Real Channel", transform="raw")
    finally:
        exp.close()


# ---- ChannelManager -------------------------------------------------------

def test_channel_manager_lists():
    reader = HDF5Reader(BIG_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        mgr = ChannelManager(exp)
        step_names = {c.name for c in mgr.list_step_channels()}
        assert "Average Current" in step_names
        log_names = {c.name for c in mgr.list_log_channels()}
        assert "VNA - S21" in log_names
        assert mgr.is_complex("VNA - S21") is True
        assert "Average Current" in mgr.list_active_sweep_axes()
    finally:
        exp.close()


# ---- CachedExperiment ------------------------------------------------------

def test_cached_experiment_hits_on_repeat():
    reader = HDF5Reader(BIG_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        cached = CachedExperiment(exp, LRUDataCache())
        d1 = cached.get_data("VNA - S21", transform="magnitude_db", entry_slice=slice(0, 1))
        d2 = cached.get_data("VNA - S21", transform="magnitude_db", entry_slice=slice(0, 1))
        assert np.array_equal(d1, d2)
        assert cached.cache.stats()["hits"] == 1
        assert cached.cache.stats()["misses"] == 1
    finally:
        exp.close()
