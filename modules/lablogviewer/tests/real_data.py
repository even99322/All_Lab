"""Workspace-local, read-only real-data locations used by regression tests."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = PROJECT_ROOT.parent / "Data"
PRL_ROOT = DATA_ROOT / "PRL RSMEP best data" / "2026" / "08" / "Data_0828"

SMALL_FILE = PRL_ROOT / "0828 5.0197~5.0297GHz BG.hdf5"
BIG_FILE = PRL_ROOT / "0828 RSMEP_1.hdf5"
FLUX_FILE = PRL_ROOT / "0828 X1 Flux-dep_debg.hdf5"
S31_FILE = (
    DATA_ROOT
    / "2025 0827 LR CPAEP good data"
    / "2025"
    / "08"
    / "Data_0826"
    / "0826 LRCPAEP @4.782GHz.hdf5"
)
LONG_TRACE_FILE = DATA_ROOT / "CCEP" / "Data_0911" / "0901 CCEP(SETX1)_2.hdf5"
