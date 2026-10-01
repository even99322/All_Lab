"""
tests/test_complex_transform.py

Mostly synthetic (this module is pure math, no HDF5 dependency), plus
one sanity check against real S21 data to make sure the transform
pipeline behaves reasonably on real values, not just contrived inputs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.plotting.complex_transform import apply_transform, VALID_TRANSFORMS


def test_raw_passthrough():
    data = np.array([1 + 2j, 3 - 4j])
    result = apply_transform(data, "raw")
    assert np.array_equal(result, data)


def test_real():
    data = np.array([1 + 2j, 3 - 4j])
    assert np.array_equal(apply_transform(data, "real"), np.array([1.0, 3.0]))


def test_imag():
    data = np.array([1 + 2j, 3 - 4j])
    assert np.array_equal(apply_transform(data, "imag"), np.array([2.0, -4.0]))


def test_magnitude():
    data = np.array([3 + 4j])  # |3+4j| == 5
    assert apply_transform(data, "magnitude")[0] == pytest.approx(5.0)


def test_magnitude_db():
    data = np.array([1 + 0j])  # |1| = 1 -> 20*log10(1) = 0 dB
    assert apply_transform(data, "magnitude_db")[0] == pytest.approx(0.0)
    data2 = np.array([10 + 0j])  # 20*log10(10) = 20 dB
    assert apply_transform(data2, "magnitude_db")[0] == pytest.approx(20.0)


def test_phase_deg_and_rad():
    data = np.array([1j])  # angle = 90 degrees = pi/2 rad
    assert apply_transform(data, "phase_deg")[0] == pytest.approx(90.0)
    assert apply_transform(data, "phase_rad")[0] == pytest.approx(np.pi / 2)


def test_zero_magnitude_db_does_not_raise():
    """log10(0) is -inf, not an exception - must not crash on a zero
    S21 point (e.g. deep notch/null in a resonance)."""
    data = np.array([0 + 0j])
    result = apply_transform(data, "magnitude_db")
    assert np.isneginf(result[0])


def test_real_valued_input_degrades_gracefully():
    data = np.array([1.0, 2.0, 3.0])  # not complex
    assert np.array_equal(apply_transform(data, "real"), data)
    assert np.array_equal(apply_transform(data, "imag"), np.zeros_like(data))
    assert np.array_equal(apply_transform(data, "magnitude"), data)
    assert np.array_equal(apply_transform(data, "phase_deg"), np.zeros_like(data))


def test_invalid_transform_raises():
    with pytest.raises(ValueError):
        apply_transform(np.array([1 + 1j]), "not_a_real_transform")


def test_all_valid_transforms_run_without_error():
    data = np.array([1 + 1j, -1 - 1j, 0 + 0j])
    for t in VALID_TRANSFORMS:
        apply_transform(data, t)  # should not raise


# ---- sanity check against real S21 data, if available ---------------------

from tests.real_data import SMALL_FILE


@pytest.mark.skipif(not SMALL_FILE.exists(), reason="Sample file not present.")
def test_transform_on_real_s21_data():
    from app.core.hdf5_reader import HDF5Reader
    from app.core.labber_parser import AutoDetector

    reader = HDF5Reader(SMALL_FILE)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        mag_db = exp.get_data("VNA - S21", transform="magnitude_db")
        mag = exp.get_data("VNA - S21", transform="magnitude")
        # 20*log10(mag) should equal mag_db within floating point tolerance
        assert np.allclose(20 * np.log10(mag), mag_db, equal_nan=True)
    finally:
        exp.close()
