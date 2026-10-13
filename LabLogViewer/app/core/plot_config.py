"""Viewer plot configuration and the numeric processing it drives.

Pure numpy, no GUI.  The processing order for a trace or map is:
phase delay / complex rotation -> component (Re/Im/Mag/Phase) -> dB / unwrap ->
Operation along X -> Smooth along X -> (2D) Third-dimension Operation /
Smooth along Y -> equations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

import numpy as np

OPERATIONS = ("None", "y-<y>", "Normalize", "d/dx", "FFT", "Histogram", "Histogram-2D")
THIRD_OPERATIONS = ("None", "y-<y>", "Normalize", "d/dx")
OP_NONE, OP_SUB_MEAN, OP_NORMALIZE, OP_DDX, OP_FFT, OP_HIST, OP_HIST2D = OPERATIONS


@dataclass
class PlotConfig:
    phase_delay_ns: float = 0.0      # v2.1.6: electrical delay τ removed as exp(+i·2π·f·τ), f from the X axis
    rotation_per_x: float = 0.0      # degrees per x unit
    fixed_rotation: float = 0.0      # degrees
    auto_rotate: bool = False
    unwrap_angle: bool = False       # 2D phase maps: unwrap along X
    operation: str = OP_NONE
    smooth: int = 1                  # running-average points along X
    bins: int = 50
    show_individual: bool = True     # 1D: draw every selected trace
    third_operation: str = OP_NONE
    third_smooth: int = 1
    show_trace_list: bool = True
    ignore_x_data: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload) -> "PlotConfig":
        config = cls()
        if not isinstance(payload, dict):
            return config
        for item in fields(cls):
            if item.name not in payload:
                continue
            value, default = payload[item.name], getattr(config, item.name)
            try:
                if isinstance(default, bool):
                    value = bool(value)
                elif isinstance(default, int):
                    value = int(value)
                elif isinstance(default, float):
                    value = float(value)
                    if not np.isfinite(value):
                        continue
                elif isinstance(default, str):
                    value = str(value)
            except (TypeError, ValueError):
                continue
            setattr(config, item.name, value)
        if config.operation not in OPERATIONS:
            config.operation = OP_NONE
        if config.third_operation not in THIRD_OPERATIONS:
            config.third_operation = OP_NONE
        config.smooth = max(1, min(config.smooth, 10001))
        config.third_smooth = max(1, min(config.third_smooth, 10001))
        config.bins = max(2, min(config.bins, 100000))
        return config

    def rotates(self) -> bool:
        return bool(self.phase_delay_ns or self.rotation_per_x or self.fixed_rotation or self.auto_rotate)


# ---- phase delay (electrical delay) -----------------------------------------------------

_HZ = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9, "thz": 1e12}


def hz_per_x_unit(x_unit: str | None) -> float:
    """Hz per X unit (X in GHz → 1e9). Unknown or missing unit: X is taken as Hz."""
    return _HZ.get(str(x_unit or "").strip().casefold(), 1.0)


def delay_phasor(x, phase_delay_ns: float, x_unit: str | None = None) -> np.ndarray:
    """exp(+i·2π·f·τ): multiplying by it removes the linear phase −2π·f·τ of a cable / line delay τ."""
    f = np.asarray(x, dtype=float) * hz_per_x_unit(x_unit)
    return np.exp(2j * np.pi * f * float(phase_delay_ns) * 1e-9)


def calculate_phase_delay_ns(x, z, x_unit: str | None = None) -> float:
    """τ (ns) from the slope of the unwrapped phase versus frequency.

    With 20 points or more only the outer 15 % at each end are fitted, so a resonance in the middle of the
    sweep does not bend the line. Returns 0 when it cannot be fitted."""
    x = np.asarray(x, dtype=float).reshape(-1)
    z = np.asarray(z).reshape(-1)
    if x.size != z.size or not np.iscomplexobj(z):
        return 0.0
    ok = np.isfinite(x) & np.isfinite(z) & (np.abs(z) > 0)
    x, z = x[ok], z[ok]
    if x.size < 2:
        return 0.0
    order = np.argsort(x)
    x, z = x[order], z[order]
    phase = np.unwrap(np.angle(z))
    f = x * hz_per_x_unit(x_unit)
    if f.size >= 20:
        edge = max(3, int(round(f.size * 0.15)))
        pick = np.r_[0:edge, f.size - edge:f.size]
        f, phase = f[pick], phase[pick]
    if np.ptp(f) == 0:
        return 0.0
    slope = np.polyfit(f, phase, 1)[0]                 # rad / Hz
    return float(-slope / (2 * np.pi) * 1e9)


# ---- complex rotation ---------------------------------------------------------

def rotation_phasor(x, rotation_per_x: float, fixed_rotation: float) -> np.ndarray:
    """Phasor removing ``fixed + rotation_per_x * x`` degrees of rotation."""
    angle = np.deg2rad(fixed_rotation + rotation_per_x * np.asarray(x, dtype=float))
    return np.exp(-1j * angle)


def principal_angle_deg(z) -> float:
    """Angle (deg) of the main axis of a complex cloud, so z*exp(-i a) lies on the real axis."""
    z = np.asarray(z)
    z = z[np.isfinite(z)]
    if z.size < 2:
        return 0.0
    z = z - z.mean()
    return float(np.rad2deg(0.5 * np.arctan2(2.0 * np.sum(z.real * z.imag),
                                              np.sum(z.real ** 2 - z.imag ** 2))))


def principal_angles_deg(rows) -> np.ndarray:
    """Row-wise ``principal_angle_deg`` for a 2D complex array, vectorised."""
    z = np.asarray(rows)
    ok = np.isfinite(z)
    n = ok.sum(axis=1)
    z = np.where(ok, z, 0)
    mean = z.sum(axis=1) / np.maximum(n, 1)
    d = np.where(ok, z - mean[:, None], 0)
    sxy = np.sum(d.real * d.imag, axis=1)
    sxx = np.sum(d.real ** 2 - d.imag ** 2, axis=1)
    out = np.rad2deg(0.5 * np.arctan2(2.0 * sxy, sxx))
    out[n < 2] = 0.0
    return out


def calculate_rotation_per_x(x, z) -> float:
    """Slope (deg per x unit) of the unwrapped phase of ``z`` versus ``x``."""
    x, z = np.asarray(x, dtype=float), np.asarray(z)
    ok = np.isfinite(x) & np.isfinite(z) & (np.abs(z) > 0)
    if np.count_nonzero(ok) < 2 or np.ptp(x[ok]) == 0:
        return 0.0
    phase = np.unwrap(np.angle(z[ok]))
    return float(np.rad2deg(np.polyfit(x[ok], phase, 1)[0]))


def calculate_fixed_rotation(x, z, rotation_per_x: float = 0.0) -> float:
    """Constant angle (deg) that puts the mean of the (x-derotated) data on the real axis."""
    z = np.asarray(z)
    z = z * rotation_phasor(x, rotation_per_x, 0.0)
    z = z[np.isfinite(z)]
    if z.size == 0:
        return 0.0
    mean = z.mean()
    return float(np.rad2deg(np.angle(mean))) if mean != 0 else 0.0


def rotate_complex(x, z, config: PlotConfig, x_unit: str | None = None) -> np.ndarray:
    """Apply Phase delay, Rotation/x, Fixed rotation and Auto-rotate to complex data."""
    z = np.asarray(z)
    if not np.iscomplexobj(z) or not config.rotates():
        return z
    out = z
    if config.phase_delay_ns:
        out = out * delay_phasor(x, config.phase_delay_ns, x_unit)
    if config.rotation_per_x or config.fixed_rotation:
        out = out * rotation_phasor(x, config.rotation_per_x, config.fixed_rotation)
    if config.auto_rotate:
        out = out * np.exp(-1j * np.deg2rad(principal_angle_deg(out)))
    return out


# ---- operations along an axis ------------------------------------------------------

def smooth_values(values, points: int, axis: int = -1) -> np.ndarray:
    """Centred running average over ``points`` samples; edges average what exists."""
    values = np.asarray(values)
    points = int(points)
    if points <= 1 or values.shape[axis] < 2:
        return values
    points = min(points, values.shape[axis])
    moved = np.moveaxis(values, axis, -1).astype(complex if np.iscomplexobj(values) else float, copy=False)
    n = moved.shape[-1]
    left = (points - 1) // 2          # same window as np.convolve(..., "same"): [i - (p-1)//2 ... i + p//2]
    lo = np.clip(np.arange(n) - (points - 1 - left), 0, n)
    hi = np.clip(np.arange(n) + left + 1, 0, n)

    def window_sums(array):
        csum = np.zeros(array.shape[:-1] + (n + 1,), dtype=array.dtype)
        np.cumsum(array, axis=-1, out=csum[..., 1:])
        return csum[..., hi] - csum[..., lo]

    finite = np.isfinite(moved)
    if finite.all():
        offset = moved.mean(axis=-1, keepdims=True)      # keeps the running sums small and exact
        result = window_sums(moved - offset) / (hi - lo) + offset
        return np.moveaxis(result, -1, axis)
    with np.errstate(all="ignore"):
        offset = np.nanmean(np.where(finite, moved, np.nan), axis=-1, keepdims=True)
    offset = np.where(np.isfinite(offset), offset, 0)
    total = window_sums(np.where(finite, moved - offset, 0))
    count = window_sums(finite.astype(float))
    with np.errstate(invalid="ignore", divide="ignore"):
        result = np.where(count > 0, total / np.where(count > 0, count, 1) + offset, np.nan)
    result = np.where(finite | (count > 0), result, np.nan)
    return np.moveaxis(result, -1, axis)


def sub_mean(values, axis: int = -1) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    with np.errstate(all="ignore"):
        mean = np.nanmean(values, axis=axis, keepdims=True)
    return values - np.where(np.isfinite(mean), mean, 0.0)


def normalize(values, axis: int = -1) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    with np.errstate(all="ignore"):
        peak = np.nanmax(np.abs(values), axis=axis, keepdims=True)
    peak = np.where(np.isfinite(peak) & (peak > 0), peak, 1.0)
    return values / peak


def derivative(values, coordinates, axis: int = -1) -> np.ndarray:
    """d(values)/d(coordinates) along ``axis``; duplicate coordinates give NaN."""
    values = np.asarray(values, dtype=float)
    coordinates = np.asarray(coordinates, dtype=float)
    if values.shape[axis] < 2 or coordinates.size != values.shape[axis]:
        return np.full(values.shape, np.nan)
    if np.any(np.diff(coordinates) == 0) or not np.all(np.isfinite(coordinates)):
        return np.full(values.shape, np.nan)
    with np.errstate(all="ignore"):
        return np.gradient(values, coordinates, axis=axis)


@dataclass
class OperationResult:
    x: np.ndarray
    y: np.ndarray
    x_label: str | None = None       # replaces the X axis label when set
    x_unit: str | None = None
    y_label: str | None = None
    y_unit_suffix: str | None = None  # appended unit, e.g. "/x" for d/dx, "" for none
    unit_mode: str = "keep"           # keep | per_x | none | counts | invx


def fft_trace(x, y) -> tuple[np.ndarray, np.ndarray]:
    """Amplitude spectrum of ``y(x)``; complex data gives the two-sided spectrum."""
    x, y = np.asarray(x, dtype=float), np.asarray(y)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if y.size < 2:
        return np.array([]), np.array([])
    step = float(np.mean(np.diff(x)))
    if step == 0:
        return np.array([]), np.array([])
    n = y.size
    if np.iscomplexobj(y):
        freq = np.fft.fftshift(np.fft.fftfreq(n, abs(step)))
        amp = np.abs(np.fft.fftshift(np.fft.fft(y - y.mean()))) / n
    else:
        freq = np.fft.rfftfreq(n, abs(step))
        amp = np.abs(np.fft.rfft(y - y.mean())) / n
    return freq, amp


def histogram_trace(y, bins: int) -> tuple[np.ndarray, np.ndarray]:
    """Distribution of y: (bin centres, counts)."""
    y = np.asarray(y, dtype=float)
    y = y[np.isfinite(y)]
    if y.size == 0:
        return np.array([]), np.array([])
    if y.min() == y.max():
        spread = max(abs(float(y.min())), 1.0) * 1e-9
        counts, edges = np.histogram(y, bins=max(1, bins), range=(y.min() - spread, y.max() + spread))
    else:
        counts, edges = np.histogram(y, bins=max(1, bins))
    return 0.5 * (edges[:-1] + edges[1:]), counts.astype(float)


def histogram_iq(z, bins: int):
    """2D histogram of real versus imaginary part: (re_centres, im_centres, counts[im, re])."""
    z = np.asarray(z)
    z = z[np.isfinite(z)]
    if z.size == 0:
        return np.array([]), np.array([]), np.zeros((0, 0))
    counts, re_edges, im_edges = np.histogram2d(z.real, z.imag, bins=max(2, bins))
    return (0.5 * (re_edges[:-1] + re_edges[1:]), 0.5 * (im_edges[:-1] + im_edges[1:]), counts.T)


def fft_rows(values, x_values) -> tuple[np.ndarray, np.ndarray]:
    """Amplitude spectrum of every row of a real map along X: (freq, amp[ny, nf])."""
    values = np.asarray(values, dtype=float)
    rows = [fft_trace(x_values, row) for row in values]
    if not rows or rows[0][0].size == 0:
        return np.array([]), np.zeros((values.shape[0], 0))
    return rows[0][0], np.stack([amp for _, amp in rows])


def histogram_columns(values, y_bins: int):
    """Per-column distribution of a real map: (bin centres, counts[bin, column])."""
    values = np.asarray(values, dtype=float)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return np.array([]), np.zeros((0, values.shape[1] if values.ndim == 2 else 0))
    low, high = float(finite.min()), float(finite.max())
    if low == high:
        high = low + max(abs(low), 1.0) * 1e-9
    edges = np.linspace(low, high, max(2, y_bins) + 1)
    out = np.zeros((edges.size - 1, values.shape[1]))
    for column in range(values.shape[1]):
        data = values[:, column]
        data = data[np.isfinite(data)]
        out[:, column] = np.histogram(data, bins=edges)[0]
    return 0.5 * (edges[:-1] + edges[1:]), out


def apply_trace_operation(x, y, config: PlotConfig) -> OperationResult:
    """Operation + Smooth along X for a single trace (after component/dB/unwrap)."""
    x, y = np.asarray(x), np.asarray(y)
    if config.smooth > 1:
        y = smooth_values(y, config.smooth)
    op = config.operation
    if op == OP_SUB_MEAN:
        return OperationResult(x, sub_mean(y), y_label="y-<y>")
    if op == OP_NORMALIZE:
        return OperationResult(x, normalize(y), y_label="normalized", unit_mode="none")
    if op == OP_DDX:
        return OperationResult(x, derivative(y, x), y_label="d/dx", unit_mode="per_x")
    if op == OP_FFT:
        freq, amp = fft_trace(x, y)
        return OperationResult(freq, amp, x_label="Frequency (1/x)", y_label="FFT amplitude",
                               unit_mode="invx")
    if op == OP_HIST:
        centres, counts = histogram_trace(np.real(y), config.bins)
        return OperationResult(centres, counts, x_label="Value", y_label="Counts",
                               unit_mode="counts")
    return OperationResult(x, y)


def apply_grid_operations(z, x_values, y_values, config: PlotConfig) -> tuple[np.ndarray, str]:
    """Fixed-group Operation/Smooth along X, then Third-dimension ones along Y.

    ``z`` is real, shape (ny, nx).  Returns ``(z, unit_mode)`` where unit_mode
    is ``keep``, ``none`` or ``per_x`` / ``per_y`` / ``per_xy``.
    """
    z = np.asarray(z, dtype=float)
    unit_mode = "keep"
    if config.smooth > 1:
        z = smooth_values(z, config.smooth, axis=1)
    if config.operation == OP_SUB_MEAN:
        z = sub_mean(z, axis=1)
    elif config.operation == OP_NORMALIZE:
        z = normalize(z, axis=1)
        unit_mode = "none"
    elif config.operation == OP_DDX:
        z = derivative(z, x_values, axis=1)
        unit_mode = "per_x"
    if config.third_smooth > 1:
        z = smooth_values(z, config.third_smooth, axis=0)
    if config.third_operation == OP_SUB_MEAN:
        z = sub_mean(z, axis=0)
    elif config.third_operation == OP_NORMALIZE:
        z = normalize(z, axis=0)
        unit_mode = "none"
    elif config.third_operation == OP_DDX:
        z = derivative(z, y_values, axis=0)
        unit_mode = "per_xy" if unit_mode == "per_x" else "per_y"
    return z, unit_mode


def operation_unit(base_unit: str | None, unit_mode: str,
                   x_unit: str | None = None, y_unit: str | None = None) -> str | None:
    if unit_mode == "keep":
        return base_unit
    if unit_mode in ("none", "counts"):
        return None
    if unit_mode == "invx":
        return f"1/{x_unit}" if x_unit else None
    divisor = []
    if unit_mode in ("per_x", "per_xy") and x_unit:
        divisor.append(x_unit)
    if unit_mode in ("per_y", "per_xy") and y_unit:
        divisor.append(y_unit)
    if not divisor:
        return base_unit
    return f"{base_unit or ''}/{'/'.join(divisor)}".lstrip("/") if base_unit else "1/" + "/".join(divisor)
