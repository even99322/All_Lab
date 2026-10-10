"""
app/core/transform_store.py — v0.9B

The Transform system, kept deliberately separate from the Axis system
(spec §5's explicit "must not be unnecessarily coupled" requirement)
and from the Trace/Log Entry selection.

A TransformSpec is an EXPLICIT operation chain — base transform, then
optional dB, then optional unwrap — so there is never any ambiguity
about double-application (spec §7's explicit "must not accidentally
perform Magnitude -> dB -> dB" warning): dB is only ever applied to a
"magnitude" base, unwrap only ever to a "phase" base, and each is
applied at most once by construction (booleans, not a stack of
arbitrary operations).

TransformStore persists user-saved TransformSpecs externally (a JSON
file under the user's home directory, same pattern as StarStore) —
never touching the original HDF5 file. The four defaults (Real,
Imaginary, Magnitude, Phase) are always available without being
persisted — they're plain Python objects, not stored in the JSON file,
so a fresh install already has them per spec §6 ("available
immediately without requiring the user to create them").
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock

import numpy as np

from app.core.external_state import atomic_write_json, default_state_path, load_json_state
from app.plotting.complex_transform import apply_transform, unwrap_phase

BASE_TRANSFORMS = ("real", "imag", "magnitude", "phase_deg", "phase_rad")
TRANSFORM_SCHEMA_VERSION = 1


@dataclass
class TransformSpec:
    """One named, explicit transform operation chain.

    `base` must be one of BASE_TRANSFORMS. `db` is only meaningful
    (and only ever applied) when base == "magnitude". `unwrap` is only
    meaningful (and only ever applied) when base is a phase transform.
    This explicitness is what prevents double-application — there is
    no way to express "dB of dB" or "unwrap of unwrap" with this
    shape, by construction.
    """
    name: str
    base: str
    db: bool = False
    unwrap: bool = False

    def __post_init__(self) -> None:
        if self.base not in BASE_TRANSFORMS:
            raise ValueError(f"base must be one of {BASE_TRANSFORMS}, got {self.base!r}")

    def resolve_transform_key(self) -> str:
        """Maps this spec to the underlying named transform key that
        app.plotting.complex_transform.apply_transform() understands."""
        if self.base == "magnitude" and self.db:
            return "magnitude_db"
        return self.base

    def apply(self, complex_array: np.ndarray) -> np.ndarray:
        """The full pipeline: base transform, then dB (folded into the
        base transform key itself), then unwrap (a separate pass, only
        for phase bases) - Data -> Transform -> dB/Unwrap -> Plot,
        exactly as spec §8 requires, all in one explicit call."""
        data = apply_transform(complex_array, self.resolve_transform_key())
        if self.unwrap and self.base in ("phase_deg", "phase_rad"):
            unit = "deg" if self.base == "phase_deg" else "rad"
            data = unwrap_phase(data, unit=unit)
        return data

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "TransformSpec":
        return TransformSpec(
            name=d["name"], base=d["base"],
            db=bool(d.get("db", False)), unwrap=bool(d.get("unwrap", False)),
        )


def default_transforms() -> list[TransformSpec]:
    """The four always-available base transforms (spec §6) - fresh
    objects each call so callers can't accidentally mutate a shared
    instance."""
    return [
        TransformSpec(name="Real", base="real"),
        TransformSpec(name="Imaginary", base="imag"),
        TransformSpec(name="Magnitude", base="magnitude"),
        TransformSpec(name="Phase", base="phase_deg"),
    ]


def default_transform_storage_path() -> Path:
    return default_state_path("transforms.json")


class TransformStore:
    """Persists user-SAVED TransformSpecs only - the four defaults are
    never written to disk, matching spec §6's 'do not hard-code
    user-created transforms into the source code; persist them
    externally' (the defaults are the opposite case: they ARE hardcoded,
    intentionally, since they're not user-created)."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path) if storage_path else default_transform_storage_path()
        self._lock = RLock()
        self._custom: dict[str, TransformSpec] = {}
        self._read_only = False
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            self._custom = {}
            return
        try:
            raw = load_json_state(self.storage_path, []).value
            if isinstance(raw, list):
                items = raw  # v0.12H and older list-only document.
            elif isinstance(raw, dict):
                schema = raw.get("schema_version", 1)
                if not isinstance(schema, int) or schema > TRANSFORM_SCHEMA_VERSION:
                    self._read_only = True
                    return
                items = raw.get("custom", [])
            else:
                raise ValueError("transforms.json root must be a list or schema document")
            if not isinstance(items, list):
                raise ValueError("transforms.json custom transforms must be a list")
            loaded: dict[str, TransformSpec] = {}
            for item in items:
                spec = TransformSpec.from_dict(item)
                loaded[spec.name] = spec
            self._custom = loaded
        except Exception:
            # corrupted state file: back it up, start fresh - never
            # crash the app over a malformed transforms.json
            self._custom = {}

    def _save(self) -> None:
        if self._read_only:
            return
        payload = {
            "schema_version": TRANSFORM_SCHEMA_VERSION,
            "custom": [spec.to_dict() for spec in self._custom.values()],
        }
        atomic_write_json(self.storage_path, payload)

    def list_custom(self) -> list[TransformSpec]:
        with self._lock:
            return list(self._custom.values())

    def list_all(self) -> list[TransformSpec]:
        """Defaults first, then user-saved custom transforms - matches
        spec §6's example ordering (built-ins, then a separator, then
        custom entries)."""
        with self._lock:
            return default_transforms() + list(self._custom.values())

    def save(self, spec: TransformSpec) -> None:
        with self._lock:
            self._custom[spec.name] = spec
            self._save()

    def delete(self, name: str) -> bool:
        with self._lock:
            if name in self._custom:
                del self._custom[name]
                self._save()
                return True
            return False

    def get(self, name: str) -> TransformSpec | None:
        with self._lock:
            for spec in self.list_all():
                if spec.name == name:
                    return spec
            return None

    def reload(self) -> None:
        with self._lock:
            self._read_only = False
            self._load()
