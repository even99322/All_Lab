from __future__ import annotations

import numpy as np

from app.visualization3d import texture_probe as tp

LOW, HIGH = (125, 0, 0), (0, 35, 130)


def _image(columns: int, width: int = 200, cut: float = 1.0, shift: float = 0.0) -> np.ndarray:
    """Top-view picture of a surface drawn from the marker texture (optionally cut short)."""
    image = np.full((60, width + 40, 3), 230, np.uint8)
    values = tp.marker_values(columns)
    for pixel in range(int(width * cut)):
        v = values[min(columns - 1, int((pixel / width + shift) * (columns - 1)))]
        image[:, 20 + pixel] = LOW if v == 0 else HIGH if v == 1 else (230, 230, 230)
    return image


def test_marker_values_hold_lowest_and_highest_at_known_places():
    values = tp.marker_values(1000)
    assert values[0] == 0 and values[-1] == 1 and values[300] == 1 and values[700] == 0


def test_aligned_texture_passes_and_cut_or_shifted_texture_fails():
    good = _image(4000)
    extent = tp.marker_extent(good, LOW, HIGH)
    assert tp.markers_aligned(good, LOW, HIGH, extent)
    assert not tp.markers_aligned(_image(4000, cut=0.64), LOW, HIGH, extent)
    assert not tp.markers_aligned(_image(4000, shift=0.1), LOW, HIGH, extent)


def test_find_axis_limit_halves_until_the_gpu_draws_correctly():
    tp.reset_probe_state()
    try:
        assert tp.find_axis_limit(1500, lambda n: False) is None
        assert tp.find_axis_limit(10_001, lambda n: n <= 4096) == 4096
        assert tp.find_axis_limit(20_000, lambda n: False) == 4096
        assert tp.find_axis_limit(3000, lambda n: False) is None
    finally:
        tp.reset_probe_state()
