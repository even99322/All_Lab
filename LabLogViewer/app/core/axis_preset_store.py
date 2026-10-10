"""Persistent plot-axis presets, separate from measurement transforms."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock

from app.core.external_state import atomic_write_json, default_state_path


def default_axis_preset_storage_path() -> Path:
    return default_state_path("axis_presets.json")


@dataclass(frozen=True)
class AxisRef:
    name: str
    source: str
    base_channel: str
    transform_key: str | None = None

    @classmethod
    def from_candidate(cls, candidate) -> "AxisRef":
        return cls(
            name=candidate.name,
            source=candidate.source,
            base_channel=candidate.base_channel,
            transform_key=candidate.transform_key,
        )


@dataclass
class AxisPreset:
    name: str
    x_axis: AxisRef
    y_axis: AxisRef
    transform_name: str | None = None
    db: bool = False
    unwrap: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "AxisPreset":
        return cls(
            name=str(value["name"]),
            x_axis=AxisRef(**value["x_axis"]),
            y_axis=AxisRef(**value["y_axis"]),
            transform_name=value.get("transform_name"),
            db=bool(value.get("db", False)),
            unwrap=bool(value.get("unwrap", False)),
        )


class AxisPresetStore:
    """Atomic external persistence for per-data plotting configurations.

    The old list-only file remains readable as a preserved legacy namespace.
    It is deliberately not attached to every data file because its identity is
    ambiguous.
    """

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path) if storage_path else default_axis_preset_storage_path()
        self._lock = RLock()
        self._presets: dict[str, dict[str, AxisPreset]] = {}
        self._legacy: dict[str, AxisPreset] = {}
        self._read_only = False
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            self._presets = {}
            self._legacy = {}
            return
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8-sig"))
            if isinstance(raw, list):
                loaded = [AxisPreset.from_dict(item) for item in raw]
                self._legacy = {preset.name: preset for preset in loaded}
                self._presets = {}
            elif isinstance(raw, dict):
                schema = raw.get("schema_version", 1)
                if not isinstance(schema, int) or schema > 2:
                    self._read_only = True
                    return
                self._legacy = {
                    p.name: p for p in (AxisPreset.from_dict(item) for item in raw.get("legacy", []))
                }
                self._presets = {
                    key: {p.name: p for p in (AxisPreset.from_dict(item) for item in items)}
                    for key, items in raw.get("by_data", {}).items()
                }
            else:
                raise ValueError("axis_presets.json has an unsupported root")
        except Exception:
            try:
                shutil.copy2(self.storage_path, self.storage_path.with_suffix(".json.bak"))
            except OSError:
                pass
            self._presets = {}
            self._legacy = {}

    def _save(self) -> None:
        if self._read_only:
            return
        payload = {
            "schema_version": 2,
            "legacy": [p.to_dict() for p in self._legacy.values()],
            "by_data": {
                key: [p.to_dict() for p in presets.values()]
                for key, presets in self._presets.items()
            },
        }
        atomic_write_json(self.storage_path, payload)

    def list_all(self, data_key: str | None = None) -> list[AxisPreset]:
        with self._lock:
            return list(self._legacy.values()) if data_key is None else list(self._presets.get(data_key, {}).values())

    def save(self, preset: AxisPreset, data_key: str | None = None) -> None:
        with self._lock:
            target = self._legacy if data_key is None else self._presets.setdefault(data_key, {})
            previous = target.get(preset.name)
            target[preset.name] = preset
            try:
                self._save()
            except Exception:
                if previous is None:
                    target.pop(preset.name, None)
                else:
                    target[preset.name] = previous
                raise

    def update(self, name: str, preset: AxisPreset, data_key: str) -> bool:
        """Replace an existing data-scoped preset without changing its name."""
        with self._lock:
            target = self._presets.get(data_key, {})
            if name not in target:
                return False
            if preset.name != name:
                raise ValueError("An Axis Preset update must retain its existing name.")
            previous = target[name]
            target[name] = preset
            try:
                self._save()
            except Exception:
                target[name] = previous
                raise
            return True

    def rename(self, old_name: str, new_name: str, data_key: str) -> bool:
        """Rename one preset within its existing Data identity namespace."""
        new_name = str(new_name).strip()
        if not new_name:
            raise ValueError("Axis Preset name cannot be empty.")
        with self._lock:
            target = self._presets.get(data_key, {})
            if old_name not in target:
                return False
            if new_name != old_name and new_name in target:
                raise ValueError(f"An Axis Preset named '{new_name}' already exists.")
            if new_name == old_name:
                return True
            preset = target.pop(old_name)
            renamed = AxisPreset(
                name=new_name, x_axis=preset.x_axis, y_axis=preset.y_axis,
                transform_name=preset.transform_name, db=preset.db, unwrap=preset.unwrap,
            )
            target[new_name] = renamed
            try:
                self._save()
            except Exception:
                del target[new_name]
                target[old_name] = preset
                raise
            return True

    def get(self, name: str, data_key: str | None = None) -> AxisPreset | None:
        with self._lock:
            target = self._legacy if data_key is None else self._presets.get(data_key, {})
            return target.get(name)

    def contains(self, name: str, data_key: str) -> bool:
        return self.get(name, data_key) is not None

    def delete(self, name: str, data_key: str | None = None) -> bool:
        with self._lock:
            target = self._legacy if data_key is None else self._presets.get(data_key, {})
            if name not in target:
                return False
            previous = target.pop(name)
            try:
                self._save()
            except Exception:
                target[name] = previous
                raise
            return True

    def reload(self) -> None:
        with self._lock:
            self._load()

    def move_data_identity(self, old_identity: str, new_identity: str, **_context) -> bool:
        with self._lock:
            if self._read_only:
                raise RuntimeError("Axis Presets are from a newer schema and cannot be migrated.")
            if new_identity in self._presets:
                raise ValueError("Axis Presets already exist for the destination Data identity.")
            if old_identity not in self._presets:
                return False
            value = self._presets.pop(old_identity)
            self._presets[new_identity] = value
            try:
                self._save()
            except Exception:
                self._presets.pop(new_identity, None)
                self._presets[old_identity] = value
                raise
            return True
