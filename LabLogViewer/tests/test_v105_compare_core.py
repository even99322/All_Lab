import numpy as np
import pytest

from app.core import compare as c


def tr(x, y, unit="Hz"):
    return c.Trace(np.asarray(x, float), np.asarray(y, float), "f", unit, "S21", "dB")


def test_overlap_uses_shared_range_and_interpolates():
    a = tr(np.linspace(0, 10, 11), np.arange(11.0))
    b = tr(np.linspace(5, 15, 21), np.linspace(5, 15, 21))
    al = c.align(a, b)
    assert al.x[0] == 5 and al.x[-1] == 10
    assert np.allclose(al.a, al.b)
    assert np.allclose(c.combine(al, "difference"), 0)


def test_no_overlap_is_refused_but_index_mode_works():
    a, b = tr([0, 1, 2], [0, 1, 0]), tr([10, 11, 12], [0, 1, 0])
    with pytest.raises(c.CompareError):
        c.align(a, b)
    al = c.align(a, b, "index")
    assert al.x_is_index and np.allclose(al.a, al.b)


def test_fixed_step_grid_and_limits():
    a = tr(np.linspace(0, 1, 101), np.zeros(101))
    al = c.align(a, a, "fixed_step", step=0.25)
    assert np.allclose(al.x, [0, .25, .5, .75, 1])
    with pytest.raises(c.CompareError):
        c.align(a, a, "fixed_step", step=0)
    with pytest.raises(c.CompareError):
        c.align(a, a, "fixed_step", step=5)
    with pytest.raises(c.CompareError):
        c.align(a, a, "fixed_step", step=1e-9)


def test_normalise_and_ratio_with_zero():
    assert np.allclose(c.normalise(np.array([2., 4., 6.]), "minmax"), [0, .5, 1])
    assert np.allclose(c.normalise(np.array([1., 2., 3.]), "zscore").mean(), 0)
    assert np.allclose(c.normalise(np.array([-2., 1.]), "max"), [-1, .5])
    assert np.allclose(c.normalise(np.ones(3), "minmax"), 0)
    al = c.Aligned(np.arange(3.), np.array([1., 2., 3.]), np.array([1., 0., 3.]))
    r = c.combine(al, "ratio")
    assert r[0] == 1 and np.isnan(r[1])


def test_shape_only_scaled_copy_matches():
    x = np.linspace(0, 1, 50)
    al = c.align(tr(x, np.sin(6 * x)), tr(x * 100, 5 * np.sin(6 * x) + 3), "index")
    assert np.allclose(c.combine(al, "difference", "minmax"), 0, atol=1e-9)


def test_unit_notes_unsorted_and_nan_input():
    a = tr([3, 1, 2, np.nan], [3, 1, 2, 9])
    b = tr([1, 2, 3], [1, 2, 3], unit="GHz")
    al = c.align(a, b)
    assert any("X units" in n for n in al.notes)
    assert np.allclose(al.a, al.b)
    with pytest.raises(c.CompareError):
        c.align(tr([1], [1]), b)


def test_2d_resample_difference_zero_for_same_map():
    x, y = np.linspace(0, 1, 5), np.linspace(0, 2, 7)
    z = np.outer(y, x)
    nx, ny = c.shared_grid_2d(x, y, x, y)
    r = c.resample_2d(x, y, z, nx, ny)
    assert r.shape == (ny.size, nx.size) and np.allclose(r, z)
    with pytest.raises(c.CompareError):
        c.shared_grid_2d(x, y, x + 5, y)


def test_whats_new_shows_104_and_105_together():
    from app.core.app_update import whats_new_text
    from app.gui.help_content import help_directory

    text = whats_new_text("2.0.0", "en", "1.0.3", folder=help_directory() / "whats_new" / "legacy")
    assert "Compare Traces" in text and "range" in text.lower()
