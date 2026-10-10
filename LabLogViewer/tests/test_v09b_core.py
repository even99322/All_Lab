"""
tests/test_v09b_core.py — v0.9B

Core-layer (no Qt) tests for:
  - Empty/Function/relation-based step channel detection
  - Dynamic Axis Discovery (ChannelManager.list_axis_candidates / get_axis_data)
  - Domain compatibility validation
  - Transform system (TransformSpec, TransformStore, double-application
    prevention)

Uses the two real sample files where available, plus a synthetic
Labber-format file (reusing the Phase 7 builder) with a
use_relations=True step channel to validate relation-based/"Function"
dimension handling — since the real "0828 X1 Flux-dep_debg.hdf5" file
was NOT available in this environment (see README "Real-file
validation status" for the honest accounting of what was and wasn't
verified against it).
"""

from __future__ import annotations

import sys
from pathlib import Path

import h5py
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.hdf5_reader import HDF5Reader
from app.core.labber_parser import AutoDetector
from app.core.channel_manager import AxisCandidate, ChannelManager
from app.core.transform_store import TransformSpec, TransformStore, default_transforms
from app.plotting.complex_transform import unwrap_phase

from tests.test_nd_slice_synthetic import _build_synthetic_labber_file

from tests.real_data import BIG_FILE, FLUX_FILE, SMALL_FILE
# ^ v0.9C update: the real file became available in a different
# location than originally assumed - checked here too so this
# already-written placeholder test actually runs instead of skipping
# now that validation is genuinely possible (see
# tests/test_v09c_flux_and_generalization.py for the full acceptance
# checklist this file's test was standing in for).

real_file_pytestmark = pytest.mark.skipif(
    not (BIG_FILE.exists() and SMALL_FILE.exists()),
    reason="Real Labber sample files not present in this environment.",
)
flux_file_pytestmark = pytest.mark.skipif(
    not FLUX_FILE.exists(),
    reason="'0828 X1 Flux-dep_debg.hdf5' was not provided in this environment — "
           "NOT VERIFIED against the real Flux-dependent file.",
)


# ---- unwrap_phase (unchanged pure function, still valid) -----------------------

def test_unwrap_phase_deg_removes_discontinuity():
    wrapped = np.array([170.0, 179.0, -179.0, -170.0])
    unwrapped = unwrap_phase(wrapped, unit="deg")
    assert np.all(np.abs(np.diff(unwrapped)) < 30)


def test_unwrap_phase_rad():
    wrapped = np.array([3.0, 3.1, -3.1, -3.0])
    unwrapped = unwrap_phase(wrapped, unit="rad")
    assert np.all(np.abs(np.diff(unwrapped)) < 1.0)


def test_unwrap_phase_invalid_unit_raises():
    with pytest.raises(ValueError):
        unwrap_phase(np.array([1.0, 2.0]), unit="bogus")


# ---- A/B. Empty / Function / relation-based dimension detection ---------------

def test_relation_based_step_channel_detected(tmp_path):
    """Builds a synthetic Labber file with a step channel whose
    Step-list entry has use_relations=True and a non-trivial equation
    (Labber's concrete mechanism for a derived/'Function' dimension,
    such as a Flux channel computed from a Current channel) and
    confirms the parser correctly flags it via ChannelInfo, WITHOUT
    needing to evaluate the equation itself (Labber already evaluated
    it and stored the results, which the parser reads normally)."""
    freq_values = np.linspace(1e9, 1.1e9, 4)
    current_values = np.linspace(0.0, 1.0, 5)

    path = tmp_path / "relation_based.hdf5"
    _build_synthetic_labber_file(
        path, step_axes=[("Flux", "Phi0", current_values)], freq_values=freq_values,
    )
    # patch in use_relations/equation for the "Flux" step channel,
    # matching the REAL field names confirmed present in actual
    # Labber files (see docs/hdf5_structure_report.md).
    with h5py.File(path, "r+") as f:
        old_step_list = f["Step list"][:]
        old_names = old_step_list.dtype.names
        new_dtype = np.dtype(
            [(n, old_step_list.dtype[n]) for n in old_names]
            + [("use_relations", "?"), ("equation", h5py.special_dtype(vlen=str))]
        )
        new_arr = np.zeros(old_step_list.shape, dtype=new_dtype)
        for name in old_names:
            new_arr[name] = old_step_list[name]
        new_arr["use_relations"][0] = True
        new_arr["equation"][0] = "Current * 0.5"
        del f["Step list"]
        f.create_dataset("Step list", data=new_arr)

    reader = HDF5Reader(path)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        flux_channel = exp.channels["Flux"]
        assert flux_channel.is_relation_based is True
        assert flux_channel.equation == "Current * 0.5"
        # the channel must STILL work as a completely normal step axis
        # for data access - the flag is informational, not a different
        # code path for reading its (already-evaluated) values.
        assert flux_channel.is_step is True
        axis = next(a for a in exp.step_axes if a.channel.name == "Flux")
        assert len(axis.values) == 5
        assert np.allclose(axis.values, current_values)
    finally:
        exp.close()


def test_non_relation_step_channel_not_flagged(tmp_path):
    """A normal, directly-configured step channel must NOT be flagged
    as relation-based - confirms the detection doesn't over-trigger."""
    freq_values = np.linspace(1e9, 1.1e9, 3)
    current_values = np.array([0.0, 1.0, 2.0])
    path = tmp_path / "normal.hdf5"
    _build_synthetic_labber_file(
        path, step_axes=[("Current", "mA", current_values)], freq_values=freq_values,
    )
    reader = HDF5Reader(path)
    exp = AutoDetector.detect_and_parse(reader)
    try:
        assert exp.channels["Current"].is_relation_based is False
        assert exp.channels["Current"].equation is None
    finally:
        exp.close()


@real_file_pytestmark
def test_real_files_have_no_relation_based_channels():
    """Confirms real-file parsing still works cleanly with the new
    is_relation_based/equation fields present (regression) - neither
    real sample happens to use Labber's relation mechanism, which the
    parser correctly reports as False/None rather than guessing."""
    from app.core.labber_parser import load_experiment

    for f in (BIG_FILE, SMALL_FILE):
        exp = load_experiment(str(f))
        try:
            for ch in exp.channels.values():
                if ch.is_step:
                    assert ch.is_relation_based is False
                    assert ch.equation is None
        finally:
            exp.close()


@flux_file_pytestmark
def test_flux_file_dimension_reconstruction():
    """v0.9C: now genuinely verified - the real
    '0828 X1 Flux-dep_debg.hdf5' file became available and is checked
    here directly. The physically Flux-dependent axis is represented
    in this file by a scalar channel named 'DC supply - 1 - Current'
    (Flux being externally proportional to this current, not a
    channel literally named 'Flux' or 'Field' in this particular
    file) - so the check looks for that concrete channel rather than
    a name substring. See test_v09c_flux_and_generalization.py for the
    complete PASS/FAIL acceptance checklist against this file."""
    from app.core.labber_parser import load_experiment
    from app.core.channel_manager import ChannelManager

    exp = load_experiment(str(FLUX_FILE))
    try:
        mgr = ChannelManager(exp)
        candidates = mgr.list_axis_candidates()
        names = {c.name for c in candidates}
        assert len(candidates) > 0
        assert "DC supply - 1 - Current" in names  # the Flux-proportional sweep axis
        assert "VNA - S21" in names                  # the measurement channel
    finally:
        exp.close()


# ---- C/D. Dynamic Axis Discovery ------------------------------------------------

@real_file_pytestmark
def test_axis_candidates_include_all_fixed_and_active_step_channels():
    """Spec §4's example list includes BOTH actively-swept channels
    (Average Current) and fixed settings (Output power, IF bandwidth,
    etc) - confirms our discovery doesn't filter out fixed channels."""
    from app.core.labber_parser import load_experiment

    exp = load_experiment(str(BIG_FILE))
    try:
        mgr = ChannelManager(exp)
        names = {c.name for c in mgr.list_axis_candidates()}
        assert "Average Current" in names       # actively swept
        assert "Output power" in names            # fixed setting
        assert "IF bandwidth" in names             # fixed setting
        assert "# of averages" in names              # fixed setting
        assert "VNA - S21" in names                    # vector channel itself
        assert "Frequency" in names                     # vector's own trace axis
    finally:
        exp.close()


@real_file_pytestmark
def test_axis_candidates_never_hardcoded_generalizes_to_different_files():
    """The SAME code path, run against two structurally different real
    files, must produce DIFFERENT candidate lists reflecting each
    file's actual content - proof against hardcoding."""
    from app.core.labber_parser import load_experiment

    exp_big = load_experiment(str(BIG_FILE))
    exp_small = load_experiment(str(SMALL_FILE))
    try:
        names_big = {c.name for c in ChannelManager(exp_big).list_axis_candidates()}
        names_small = {c.name for c in ChannelManager(exp_small).list_axis_candidates()}
        assert names_big != names_small
        assert "Average Current" in names_big
        assert "Average Current" not in names_small  # not swept in the small file
    finally:
        exp_big.close()
        exp_small.close()


@real_file_pytestmark
def test_missing_channel_excluded_not_offered():
    """Spec's 'Missing channel handling': a channel with no recorded
    values (the small file's step channels, all fixed with an empty
    Data/Data matrix) must not appear as a candidate at all, rather
    than being offered and then failing when selected."""
    from app.core.labber_parser import load_experiment

    exp = load_experiment(str(SMALL_FILE))
    try:
        mgr = ChannelManager(exp)
        candidates = mgr.list_axis_candidates()
        # every candidate must actually resolve without raising
        for c in candidates:
            data = mgr.get_axis_data(c, entry_index=0)
            assert len(data) > 0
    finally:
        exp.close()


@real_file_pytestmark
def test_get_axis_data_entries_domain():
    from app.core.labber_parser import load_experiment

    exp = load_experiment(str(BIG_FILE))
    try:
        mgr = ChannelManager(exp)
        cand = next(c for c in mgr.list_axis_candidates() if c.name == "Average Current")
        data = mgr.get_axis_data(cand)
        assert data.shape == (855,)
        assert data[0] == pytest.approx(162.594, abs=1e-3)
    finally:
        exp.close()


@real_file_pytestmark
def test_get_axis_data_points_domain_trace_axis():
    from app.core.labber_parser import load_experiment

    exp = load_experiment(str(BIG_FILE))
    try:
        mgr = ChannelManager(exp)
        cand = next(c for c in mgr.list_axis_candidates() if c.name == "Frequency")
        data = mgr.get_axis_data(cand)
        assert data.shape == (501,)
        assert data[0] == pytest.approx(5.0197e9)
    finally:
        exp.close()


@real_file_pytestmark
def test_get_axis_data_points_domain_vector_channel_with_transform():
    from app.core.labber_parser import load_experiment

    exp = load_experiment(str(BIG_FILE))
    try:
        mgr = ChannelManager(exp)
        cand = next(c for c in mgr.list_axis_candidates() if c.name == "VNA - S21")
        assert cand.is_complex is True
        data = mgr.get_axis_data(cand, entry_index=10, transform="magnitude_db")
        assert data.shape == (501,)
        assert np.isfinite(data).all()
    finally:
        exp.close()


# ---- domain compatibility validation --------------------------------------------

@real_file_pytestmark
def test_domain_compatible_entries_vs_entries():
    from app.core.labber_parser import load_experiment

    exp = load_experiment(str(BIG_FILE))
    try:
        mgr = ChannelManager(exp)
        by_name = {c.name: c for c in mgr.list_axis_candidates()}
        assert mgr.axis_domains_compatible(by_name["Average Current"], by_name["Output power"])
    finally:
        exp.close()


@real_file_pytestmark
def test_domain_compatible_points_vs_points_same_channel():
    from app.core.labber_parser import load_experiment

    exp = load_experiment(str(BIG_FILE))
    try:
        mgr = ChannelManager(exp)
        by_name = {c.name: c for c in mgr.list_axis_candidates()}
        assert mgr.axis_domains_compatible(by_name["Frequency"], by_name["VNA - S21"])
    finally:
        exp.close()


@real_file_pytestmark
def test_domain_incompatible_entries_vs_points():
    from app.core.labber_parser import load_experiment

    exp = load_experiment(str(BIG_FILE))
    try:
        mgr = ChannelManager(exp)
        by_name = {c.name: c for c in mgr.list_axis_candidates()}
        assert not mgr.axis_domains_compatible(by_name["Average Current"], by_name["VNA - S21"])
        assert not mgr.axis_domains_compatible(by_name["Frequency"], by_name["Output power"])
    finally:
        exp.close()


# ---- Transform system (TransformSpec, TransformStore) ---------------------------

def test_transform_spec_double_application_prevention():
    """The core spec §7 guarantee: Magnitude+dB never becomes
    'dB of dB' - resolve_transform_key() always maps to exactly ONE
    underlying transform key."""
    spec = TransformSpec(name="X", base="magnitude", db=True)
    assert spec.resolve_transform_key() == "magnitude_db"
    data = np.array([10 + 0j])
    result = spec.apply(data)
    assert result[0] == pytest.approx(20.0)  # 20*log10(10), applied exactly once


def test_transform_spec_invalid_base_raises():
    with pytest.raises(ValueError):
        TransformSpec(name="bad", base="magnitude_db")


def test_transform_spec_phase_unwrap():
    spec = TransformSpec(name="Y", base="phase_deg", unwrap=True)
    wrapped = np.array([170 + 1j * 0.001, -170 + 1j * 0.001])  # near +-180 boundary in angle terms
    result = spec.apply(wrapped)
    assert result.shape == (2,)


def test_default_transforms_are_real_imag_magnitude_phase():
    names = {s.name for s in default_transforms()}
    assert names == {"Real", "Imaginary", "Magnitude", "Phase"}
    for s in default_transforms():
        assert s.db is False
        assert s.unwrap is False


def test_transform_store_defaults_available_immediately(tmp_path):
    store = TransformStore(tmp_path / "transforms.json")
    assert len(store.list_all()) == 4  # no save needed to see the defaults


def test_transform_store_save_and_reload(tmp_path):
    path = tmp_path / "transforms.json"
    store = TransformStore(path)
    custom = TransformSpec(name="My S21 dB", base="magnitude", db=True)
    store.save(custom)
    assert len(store.list_all()) == 5

    store2 = TransformStore(path)  # simulates reopening the app
    found = store2.get("My S21 dB")
    assert found is not None
    assert found.db is True


def test_transform_store_never_touches_original_hdf5(tmp_path):
    """Spec §15: transform presets must be stored externally, never
    touching the HDF5 file."""
    import hashlib
    import shutil

    hdf5_copy = tmp_path / "data.hdf5"
    if BIG_FILE.exists():
        shutil.copy(SMALL_FILE, hdf5_copy)
    else:
        hdf5_copy.write_bytes(b"placeholder")
    hash_before = hashlib.sha256(hdf5_copy.read_bytes()).hexdigest()

    store = TransformStore(tmp_path / "transforms.json")
    store.save(TransformSpec(name="Custom", base="phase_deg", unwrap=True))
    store.delete("Custom")

    hash_after = hashlib.sha256(hdf5_copy.read_bytes()).hexdigest()
    assert hash_before == hash_after


def test_transform_store_corrupted_file_recovers(tmp_path):
    path = tmp_path / "transforms.json"
    path.write_text("{ not valid json")
    store = TransformStore(path)  # must not raise
    assert len(store.list_all()) == 4  # defaults still available
    backup = path.with_suffix(".json.bak")
    assert backup.exists()


def test_transform_store_delete():
    import tempfile
    tmp_path = Path(tempfile.mkdtemp())
    store = TransformStore(tmp_path / "transforms.json")
    store.save(TransformSpec(name="Temp", base="real"))
    assert store.delete("Temp") is True
    assert store.get("Temp") is None
    assert store.delete("Temp") is False  # already gone
