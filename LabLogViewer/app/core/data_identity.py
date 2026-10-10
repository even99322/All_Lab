"""Display-name and persistent-identity helpers for Labber logs.

The filename is the operator-controlled name of a log. Labber's embedded
``log_name`` attribute can be older than a renamed file, so it remains useful
metadata but must not silently replace the filename in the user interface.
Persistent state uses the resolved source path instead of either display name.
"""

from __future__ import annotations

from pathlib import Path


def display_name_for_source(source_path: str | Path, metadata_log_name: object = None) -> str:
    """Return the complete human-facing filename stem when it is available."""
    stem = Path(source_path).stem.strip() if source_path else ""
    if stem:
        return stem
    metadata = str(metadata_log_name or "").strip()
    return metadata or "(unnamed)"


def stable_data_identity(source_path: str | Path) -> str:
    """Return a resolved source key, never a mutable display name."""
    return str(Path(source_path).expanduser().resolve())
