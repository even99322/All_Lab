"""Serializable result vocabulary shared by physical and coarse detectors."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite


ANALYSIS_STATUSES = frozenset({"Good", "Warning", "Rejected", "Failed", "Manual"})
PHYSICAL_TYPES = frozenset({"Physical Node", "Physical Antinode"})
COARSE_TYPES = frozenset({"Coarse Node Candidate", "Coarse Antinode Candidate"})


@dataclass(frozen=True)
class AnalysisPoint:
    """One detected location; unavailable physical values remain ``None``."""

    type: str
    method: str
    frequency_hz: float
    trace_identity: str | None = None
    sweep_value: float | None = None
    phase_rad: float | None = None
    kappa_hz: float | None = None
    status: str = "Good"
    origin: str = "automatic"

    def __post_init__(self):
        if self.type not in PHYSICAL_TYPES | COARSE_TYPES:
            raise ValueError(f"Unsupported analysis point type: {self.type}")
        if self.status not in ANALYSIS_STATUSES:
            raise ValueError(f"Unsupported analysis status: {self.status}")
        if not isfinite(float(self.frequency_hz)):
            raise ValueError("Analysis point frequency must be finite.")
        for name in ("sweep_value", "phase_rad", "kappa_hz"):
            value = getattr(self, name)
            if value is not None and not isfinite(float(value)):
                raise ValueError(f"{name} must be finite when present.")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "AnalysisPoint":
        return cls(**value)
