"""
tests/test_metadata_and_table.py — Phase 9

Tests for ChannelManager.get_metadata_summary() and
app/core/data_table.build_1d_table().
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.data_table import build_1d_table, DEFAULT_MAX_ROWS

from tests.real_data import BIG_FILE, SMALL_FILE

real_file_pytestmark = pytest.mark.skipif(
    not (BIG_FILE.exists() and SMALL_FILE.exists()),
    reason="Real Labber sample files not present in this environment.",
)


def test_build_1d_table_basic():
    x = np.array([1.0, 2.0, 3.0])
    y = np.array([10.0, 20.0, 30.0])
    table = build_1d_table(x, y, x_name="X", x_unit="m", y_name="Y", y_unit="V", transform="raw")
    assert len(table.rows) == 3
    assert table.truncated is False
    assert table.total_rows == 3
    assert table.rows[0].index == 0
    assert table.rows[0].x == 1.0
    assert table.rows[0].y == 10.0
    assert table.x_name == "X" and table.x_unit == "m"
    assert table.y_name == "Y" and table.y_unit == "V"


def test_build_1d_table_truncation():
    x = np.arange(10000, dtype=float)
    y = np.arange(10000, dtype=float) * 2
    table = build_1d_table(x, y, x_name="X", x_unit=None, y_name="Y", y_unit=None,
                            transform="raw", max_rows=100)
    assert len(table.rows) == 100
    assert table.truncated is True
    assert table.total_rows == 10000
    assert table.rows[-1].index == 99


def test_build_1d_table_no_limit():
    x = np.arange(10000, dtype=float)
    y = np.arange(10000, dtype=float)
    table = build_1d_table(x, y, x_name="X", x_unit=None, y_name="Y", y_unit=None,
                            transform="raw", max_rows=None)
    assert len(table.rows) == 10000
    assert table.truncated is False


def test_build_1d_table_complex_values():
    x = np.array([0.0, 1.0])
    y = np.array([1 + 2j, 3 - 4j])
    table = build_1d_table(x, y, x_name="X", x_unit=None, y_name="Y", y_unit=None, transform="raw")
    assert table.rows[0].y == complex(1, 2)
    assert table.rows[1].y == complex(3, -4)


def test_build_1d_table_default_max_rows_is_reasonable():
    assert DEFAULT_MAX_ROWS >= 1000


def test_build_1d_table_empty_arrays():
    table = build_1d_table(np.array([]), np.array([]), x_name="X", x_unit=None,
                            y_name="Y", y_unit=None, transform="raw")
    assert len(table.rows) == 0
    assert table.total_rows == 0
    assert table.truncated is False


@real_file_pytestmark
def test_metadata_summary_file_level_fields():
    from app.core.labber_parser import load_experiment
    from app.core.channel_manager import ChannelManager

    exp = load_experiment(str(BIG_FILE))
    try:
        mgr = ChannelManager(exp)
        summary = mgr.get_metadata_summary()
        assert summary["file_name"] == BIG_FILE.name
        assert summary["file_path"] == str(BIG_FILE)
        assert summary["experiment_name"]
        assert summary["format_variant"] == "trace_log_channel"
        assert summary["project"] is not None
        assert summary["n_step_channels"] == 11
        assert summary["n_log_channels"] == 1
        assert summary["channel"] is None
        assert summary["dimensions"] == []
    finally:
        exp.close()


@real_file_pytestmark
def test_metadata_summary_channel_level_fields():
    from app.core.labber_parser import load_experiment
    from app.core.channel_manager import ChannelManager

    exp = load_experiment(str(BIG_FILE))
    try:
        mgr = ChannelManager(exp)
        summary = mgr.get_metadata_summary("VNA - S21")
        assert summary["channel"]["name"] == "VNA - S21"
        assert summary["channel"]["is_vector"] is True
        assert summary["channel"]["is_complex"] is True
        assert summary["channel"]["shape"] == (501, 855)

        dims = summary["dimensions"]
        assert len(dims) == 2
        freq_dim = next(d for d in dims if d["name"] == "Frequency")
        assert freq_dim["unit"] == "Hz"
        assert freq_dim["size"] == 501
        assert freq_dim["min"] == pytest.approx(5.0197e9)
        assert freq_dim["max"] == pytest.approx(5.0297e9)
        assert freq_dim["is_uniform"] is True
        assert freq_dim["step"] == pytest.approx(20000.0)

        current_dim = next(d for d in dims if d["name"] == "Average Current")
        assert current_dim["unit"] == "mA"
        assert current_dim["size"] == 855
    finally:
        exp.close()


@real_file_pytestmark
def test_metadata_summary_unknown_channel_degrades_gracefully():
    from app.core.labber_parser import load_experiment
    from app.core.channel_manager import ChannelManager

    exp = load_experiment(str(BIG_FILE))
    try:
        mgr = ChannelManager(exp)
        summary = mgr.get_metadata_summary("Definitely Not A Real Channel")
        assert summary["channel"] is None
        assert summary["dimensions"] == []
        assert summary["file_name"] == BIG_FILE.name
    finally:
        exp.close()


@real_file_pytestmark
def test_metadata_summary_small_file_no_active_sweep():
    from app.core.labber_parser import load_experiment
    from app.core.channel_manager import ChannelManager

    exp = load_experiment(str(SMALL_FILE))
    try:
        mgr = ChannelManager(exp)
        summary = mgr.get_metadata_summary("VNA - S21")
        assert summary["channel"]["shape"] == (501,)
        assert len(summary["dimensions"]) == 1
        assert summary["dimensions"][0]["name"] == "Frequency"
    finally:
        exp.close()


@real_file_pytestmark
def test_metadata_summary_does_not_trigger_extra_hdf5_reads():
    from app.core.labber_parser import load_experiment
    from app.core.channel_manager import ChannelManager
    from app.core.cache import CachedExperiment, LRUDataCache

    exp = load_experiment(str(BIG_FILE))
    try:
        cached = CachedExperiment(exp, LRUDataCache())
        mgr = ChannelManager(exp)
        for _ in range(5):
            mgr.get_metadata_summary("VNA - S21")
        assert cached.cache.stats()["hits"] == 0
        assert cached.cache.stats()["misses"] == 0
    finally:
        exp.close()
