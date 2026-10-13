"""
app/plotting/complex_transform.py

Transforms for complex-valued log channel data (spec §10). Pure numpy,
no GUI/plot dependency, so it's independently unit-testable and usable
from both data_model.py and the future plot widgets.
"""

from __future__ import annotations

import numpy as np

VALID_TRANSFORMS = (
    "raw",
    "real",
    "imag",
    "magnitude",
    "magnitude_db",
    "phase_deg",
    "phase_rad",
)


def apply_transform(data: np.ndarray, transform: str = "raw") -> np.ndarray:
    """Applies a named transform to a (possibly complex) numpy array.
    For real-valued (non-complex) input, all transforms except
    "imag"/"phase_*" degrade gracefully: "real" and "magnitude" return
    the data unchanged, "imag" returns zeros, phase returns zeros.
    """
    if transform not in VALID_TRANSFORMS:
        raise ValueError(f"Unknown transform '{transform}'. Valid: {VALID_TRANSFORMS}")

    is_complex = np.iscomplexobj(data)

    if transform == "raw":
        return data
    if transform == "real":
        return np.real(data) if is_complex else data
    if transform == "imag":
        return np.imag(data) if is_complex else np.zeros_like(data)
    if transform == "magnitude":
        return np.abs(data)
    if transform == "magnitude_db":
        mag = np.abs(data)
        with np.errstate(divide="ignore"):
            return 20.0 * np.log10(mag)
    if transform == "phase_deg":
        return np.angle(data, deg=True) if is_complex else np.zeros_like(data)
    if transform == "phase_rad":
        return np.angle(data, deg=False) if is_complex else np.zeros_like(data)

    raise AssertionError("unreachable")


def unwrap_phase(values: np.ndarray, unit: str = "deg") -> np.ndarray:
    """Unwraps a 1D phase array along its only axis, removing
    artificial 360deg/2*pi discontinuities (v0.9A: the Advanced
    Viewer's "Unwrap Phase" option).

    Pure numpy, no GUI/HDF5 dependency — lives in this module (not the
    GUI layer) so unwrap is a proper, independently-testable step in
    the Data -> Transform -> dB/Unwrap -> Plot pipeline, the same way
    the other transforms above are, rather than a GUI-only special
    case. Callers apply it AFTER fetching phase_deg/phase_rad data
    from Experiment.get_data() (unwrap only makes sense on an already
    phase-transformed trace, and only along a genuine 1D sweep axis —
    unwrapping a 2D heatmap would require picking an axis to unwrap
    along, which is out of scope here and left to whichever future
    phase adds multi-axis unwrap support explicitly).

    `unit` must be "deg" (matches the "phase_deg" transform) or "rad"
    (matches "phase_rad").
    """
    if unit == "deg":
        return np.degrees(np.unwrap(np.radians(values)))
    if unit == "rad":
        return np.unwrap(values)
    raise ValueError(f"unit must be 'deg' or 'rad', got {unit!r}")
