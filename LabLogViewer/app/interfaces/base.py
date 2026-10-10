"""Small, non-GUI contract for future LabLogViewer interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class InterfaceContext:
    """Best-effort Browser context; unavailable source information stays None."""

    database_path: Path | None = None
    database_name: str | None = None
    database_identity: str | None = None
    selected_folder: Path | None = None
    selected_log_name: str | None = None
    selected_log_id: str | None = None
    selected_log_path: Path | None = None
    selected_channel: str | None = None
    selected_dimensions: tuple[str, ...] | None = None
    sweep_information: Mapping[str, object] | None = None
    metadata: Mapping[str, object] | None = None
    instrument_metadata: Mapping[str, object] | None = None


class InterfaceStatus(Enum):
    LAUNCHED = "launched"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True)
class InterfaceResult:
    """Outcome returned to the Browser, which owns user-facing messages."""

    status: InterfaceStatus
    message: str | None = None

    @classmethod
    def unavailable(cls) -> "InterfaceResult":
        return cls(InterfaceStatus.UNAVAILABLE)

    @classmethod
    def failed(cls, message: str | None = None) -> "InterfaceResult":
        return cls(InterfaceStatus.ERROR, message)

    @classmethod
    def launched(cls) -> "InterfaceResult":
        return cls(InterfaceStatus.LAUNCHED)


class BaseInterface:
    """Implement availability and launch without depending on Qt widgets."""

    def is_available(self) -> bool:
        raise NotImplementedError

    def launch(self, context: InterfaceContext) -> InterfaceResult:
        raise NotImplementedError
