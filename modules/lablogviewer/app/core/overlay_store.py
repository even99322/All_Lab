"""External persistence for user-saved multi-trace visualization states."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import RLock

from app.core.external_state import atomic_write_json, default_state_path


MAX_SAVED_OVERLAYS = 5


def default_overlay_storage_path() -> Path:
    return default_state_path("overlays.json")


@dataclass
class SavedOverlay:
    name: str
    selected_traces: list[int]
    visible_traces: list[int]
    active_trace: int
    reference_trace: int | None = None
    color_mode: str = "Sequential"
    x_axis: dict | None = None
    y_axis: dict | None = None
    transform_name: str | None = None
    db: bool = False
    unwrap: bool = False
    plot_mode: int = 0
    two_d: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "SavedOverlay":
        return cls(
            name=str(value["name"]),
            selected_traces=[int(item) for item in value.get("selected_traces", [])],
            visible_traces=[int(item) for item in value.get("visible_traces", [])],
            active_trace=int(value.get("active_trace", 0)),
            reference_trace=(None if value.get("reference_trace") is None
                             else int(value["reference_trace"])),
            color_mode=str(value.get("color_mode", "Sequential")),
            x_axis=value.get("x_axis"),
            y_axis=value.get("y_axis"),
            transform_name=value.get("transform_name"),
            db=bool(value.get("db", False)),
            unwrap=bool(value.get("unwrap", False)),
            plot_mode=int(value.get("plot_mode", 0)),
            two_d=dict(value.get("two_d", {})),
        )


class OverlayStore:
    """Atomic, per-data Saved Overlay storage with a strict five-item limit."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path) if storage_path else default_overlay_storage_path()
        self._lock = RLock()
        self._by_data: dict[str, dict[str, SavedOverlay]] = {}
        self._read_only = False
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            self._by_data = {}
            return
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict):
                raise ValueError("overlays.json root must be an object")
            schema = raw.get("schema_version", 1)
            if not isinstance(schema, int) or schema > 1:
                self._read_only = True
                return
            self._by_data = {
                key: {item.name: item for item in map(SavedOverlay.from_dict, values)}
                for key, values in raw.get("by_data", {}).items()
            }
        except Exception:
            try:
                shutil.copy2(self.storage_path, self.storage_path.with_suffix(".json.bak"))
            except OSError:
                pass
            self._by_data = {}

    def _save(self) -> None:
        if self._read_only:
            return
        payload = {
            "schema_version": 1,
            "by_data": {
                key: [item.to_dict() for item in overlays.values()]
                for key, overlays in self._by_data.items()
            },
        }
        atomic_write_json(self.storage_path, payload)

    def list_all(self, data_key: str) -> list[SavedOverlay]:
        with self._lock:
            return list(self._by_data.get(data_key, {}).values())

    def get(self, data_key: str, name: str) -> SavedOverlay | None:
        with self._lock:
            return self._by_data.get(data_key, {}).get(name)

    def save(self, data_key: str, overlay: SavedOverlay) -> None:
        with self._lock:
            target = self._by_data.setdefault(data_key, {})
            if overlay.name not in target and len(target) >= MAX_SAVED_OVERLAYS:
                raise ValueError(f"Maximum of {MAX_SAVED_OVERLAYS} Saved Overlays reached.")
            target[overlay.name] = overlay
            self._save()

    def rename(self, data_key: str, old_name: str, new_name: str) -> bool:
        with self._lock:
            target = self._by_data.get(data_key, {})
            if old_name not in target or not new_name or (new_name != old_name and new_name in target):
                return False
            overlay = target.pop(old_name)
            overlay.name = new_name
            target[new_name] = overlay
            self._save()
            return True

    def delete(self, data_key: str, name: str) -> bool:
        with self._lock:
            target = self._by_data.get(data_key, {})
            if name not in target:
                return False
            del target[name]
            self._save()
            return True

    def move_data_identity(self, old_identity: str, new_identity: str, **_context) -> bool:
        with self._lock:
            if self._read_only:
                raise RuntimeError("Saved Overlays are from a newer schema and cannot be migrated.")
            if new_identity in self._by_data:
                raise ValueError("Saved Overlays already exist for the destination Data identity.")
            if old_identity not in self._by_data:
                return False
            value = self._by_data.pop(old_identity)
            self._by_data[new_identity] = value
            try:
                self._save()
            except Exception:
                self._by_data.pop(new_identity, None)
                self._by_data[old_identity] = value
                raise
            return True

    def reload(self) -> None:
        with self._lock:
            self._by_data = {}
            self._read_only = False
            self._load()
