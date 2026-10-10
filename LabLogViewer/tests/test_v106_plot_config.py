import numpy as np

from app.core.plot_config import (
    PlotConfig, apply_grid_operations, apply_trace_operation, calculate_fixed_rotation,
    calculate_rotation_per_x, derivative, fft_trace, histogram_iq, histogram_trace,
    normalize, operation_unit, principal_angle_deg, rotate_complex, smooth_values, sub_mean,
)


def test_config_round_trip_and_bad_payloads():
    config = PlotConfig(rotation_per_x=1.5, operation="FFT", smooth=5)
    assert PlotConfig.from_dict(config.to_dict()) == config
    bad = PlotConfig.from_dict({"operation": "nope", "smooth": -4, "bins": "x", "rotation_per_x": float("nan")})
    assert bad.operation == "None" and bad.smooth == 1 and bad.bins == 50 and bad.rotation_per_x == 0.0
    assert PlotConfig.from_dict(None) == PlotConfig()


def test_rotation_calculations_recover_known_rotation():
    x = np.linspace(0, 1, 101)
    z = (1 + 0.2 * x) * np.exp(1j * np.deg2rad(30 + 400 * x))
    slope = calculate_rotation_per_x(x, z)
    assert abs(slope - 400) < 1.0
    fixed = calculate_fixed_rotation(x, z, slope)
    config = PlotConfig(rotation_per_x=slope, fixed_rotation=fixed)
    out = rotate_complex(x, z, config)
    assert np.max(np.abs(np.angle(out))) < np.deg2rad(2)


def test_auto_rotate_aligns_principal_axis():
    t = np.linspace(-1, 1, 200)
    z = t * np.exp(1j * np.deg2rad(40)) + 0.001j * np.cos(5 * t)
    assert abs(principal_angle_deg(z) - 40) < 1.0
    out = rotate_complex(t, z, PlotConfig(auto_rotate=True))
    assert np.std(out.imag) < 0.02 * np.std(out.real)
    real = np.arange(5.0)
    assert rotate_complex(real, real, PlotConfig(fixed_rotation=30)) is real or np.array_equal(
        rotate_complex(real, real, PlotConfig(fixed_rotation=30)), real)


def test_smooth_sub_mean_normalize_derivative():
    y = np.array([0.0, 10.0, 0.0, 10.0, 0.0])
    assert np.allclose(smooth_values(y, 3)[1:4], [10 / 3, 20 / 3, 10 / 3])
    assert np.isclose(smooth_values(y, 3)[0], 5.0)
    assert np.allclose(sub_mean(y), y - 4)
    assert np.isclose(np.max(np.abs(normalize(y - 3))), 1.0)
    x = np.linspace(0, 2, 21)
    assert np.allclose(derivative(x ** 2, x)[2:-2], 2 * x[2:-2], atol=1e-9)
    assert np.all(np.isnan(derivative(y, np.zeros(5))))


def test_smooth_keeps_nan_gaps_and_axis():
    grid = np.arange(12.0).reshape(3, 4)
    assert smooth_values(grid, 2, axis=0).shape == grid.shape
    nan = np.array([1.0, np.nan, 3.0])
    assert np.isfinite(smooth_values(nan, 3)).all()


def test_fft_and_histograms():
    x = np.linspace(0, 1, 256, endpoint=False)
    freq, amp = fft_trace(x, np.sin(2 * np.pi * 20 * x))
    assert abs(freq[np.argmax(amp)] - 20) < 1.0
    centres, counts = histogram_trace(np.r_[np.zeros(30), np.ones(10)], 4)
    assert counts.sum() == 40 and centres.size == 4
    assert histogram_trace([5.0, 5.0], 3)[1].sum() == 2
    re, im, counts2 = histogram_iq(np.array([1 + 1j, 1 + 1j, -1 - 1j]), 4)
    assert counts2.shape == (4, 4) and counts2.sum() == 3


def test_trace_operation_dispatch():
    x = np.linspace(0, 1, 11)
    y = x ** 2
    assert np.allclose(apply_trace_operation(x, y, PlotConfig(operation="y-<y>")).y, y - y.mean())
    fft = apply_trace_operation(x, y, PlotConfig(operation="FFT"))
    assert fft.unit_mode == "invx"
    hist = apply_trace_operation(x, y, PlotConfig(operation="Histogram", bins=5))
    assert hist.y.sum() == 11
    plain = apply_trace_operation(x, y, PlotConfig())
    assert np.array_equal(plain.y, y)


def test_grid_operations_along_x_and_y():
    xv, yv = np.linspace(0, 1, 5), np.linspace(0, 10, 4)
    z = yv[:, None] * 2 + xv[None, :] * 0
    out, mode = apply_grid_operations(z, xv, yv, PlotConfig(third_operation="d/dx"))
    assert mode == "per_y" and np.allclose(out, 2.0)
    out, mode = apply_grid_operations(z, xv, yv, PlotConfig(operation="d/dx", third_operation="d/dx"))
    assert mode == "per_xy"
    out, mode = apply_grid_operations(z, xv, yv, PlotConfig(operation="y-<y>"))
    assert np.allclose(out.mean(axis=1), 0)


def test_operation_unit_text():
    assert operation_unit("dB", "keep") == "dB"
    assert operation_unit("dB", "per_y", y_unit="mA") == "dB/mA"
    assert operation_unit(None, "per_x", x_unit="GHz") == "1/GHz"
    assert operation_unit("dB", "none") is None
    assert operation_unit("V", "invx", x_unit="s") == "1/s"


def test_mat_export_is_a_struct_with_complex_values(tmp_path):
    from scipy.io import loadmat

    from app.core.data_export import ExportColumn, ExportDataset, write_mat

    x = np.linspace(0, 1, 5)
    z = x * (1 + 2j)
    dataset = ExportDataset(
        [ExportColumn("x", "x", x)],
        {"trace_1_x": x, "trace_1_values": z, "1 bad name!": x},
        {"channel": "S21", "note": None},
    )
    target = tmp_path / "out.mat"
    write_mat(target, dataset)
    loaded = loadmat(target, struct_as_record=False, squeeze_me=True)
    data = loaded["data"]
    assert np.allclose(data.trace_1_values, z) and np.allclose(data.trace_1_x, x)
    assert hasattr(data, "value_1_bad_name")
    assert '"channel":"S21"' in str(loaded["metadata_json"])
