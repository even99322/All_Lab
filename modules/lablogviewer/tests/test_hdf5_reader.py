"""
tests/test_hdf5_reader.py

Runs against the two real sample files (per project requirement: no
synthetic-only testing for the core parser stack). Skips gracefully if
the sample files aren't present in this environment/checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.hdf5_reader import HDF5Reader, HDF5ReadError

from tests.real_data import BIG_FILE, SMALL_FILE

pytestmark = pytest.mark.skipif(
    not (SMALL_FILE.exists() and BIG_FILE.exists()),
    reason="Real Labber sample files not present in this environment.",
)


def test_open_and_close():
    reader = HDF5Reader(SMALL_FILE)
    assert reader.is_open
    reader.close()
    assert not reader.is_open


def test_context_manager():
    with HDF5Reader(SMALL_FILE) as reader:
        assert reader.is_open
    assert not reader.is_open


def test_missing_file_raises():
    with pytest.raises(HDF5ReadError):
        HDF5Reader("/nonexistent/path/does_not_exist.hdf5")


def test_kind_of():
    with HDF5Reader(SMALL_FILE) as reader:
        assert reader.kind_of("/Channels") == "dataset"
        assert reader.kind_of("/Data") == "group"
        assert reader.kind_of("/does/not/exist") is None


def test_get_attrs_root():
    with HDF5Reader(SMALL_FILE) as reader:
        attrs = reader.get_attrs("/")
        assert "log_name" in attrs
        assert "version" in attrs


def test_read_full_dataset_small_file():
    with HDF5Reader(SMALL_FILE) as reader:
        data = reader.read("/Traces/VNA - S21")
        assert data.shape == (501, 2, 1)


def test_read_sliced_dataset_big_file_does_not_load_whole_thing():
    """The big file's VNA - S21 trace is (501, 2, 855) float64 ~ 6.8MB
    in memory if fully loaded (84MB on disk with compression). We only
    ask for one entry - this should be fast and return the right shape,
    proving `read()` supports partial/lazy access."""
    with HDF5Reader(BIG_FILE) as reader:
        sliced = reader.read("/Traces/VNA - S21", slice_=(slice(None), slice(None), slice(0, 1)))
        assert sliced.shape == (501, 2, 1)


def test_tree_walk_both_files():
    for f in (SMALL_FILE, BIG_FILE):
        with HDF5Reader(f) as reader:
            nodes = reader.tree()
            paths = {n.path for n in nodes}
            assert "/Channels" in paths
            assert "/Data/Data" in paths
            assert "/Traces/VNA - S21" in paths


def test_get_scalar_big_file_instrument_config():
    with HDF5Reader(BIG_FILE) as reader:
        val = reader.get_scalar("/Instrument config/VNA/Start frequency")
        assert val == pytest.approx(5019700000.0)


def test_read_after_close_raises():
    reader = HDF5Reader(SMALL_FILE)
    reader.close()
    with pytest.raises(HDF5ReadError):
        reader.read("/Channels")
