"""App 設定（LAB/settings.yaml）。

程式碼中的「可調的值」一律從這裡取，例如：
    from labcontrol.settings import setting
    setting("labber.user")                      → "QEL"
    setting("editor.new_blocks.dc_set.start")   → 100

讀取方式：labcontrol/defaults/settings.yaml（完整預設）⟵ 深度合併 ⟵ LAB/settings.yaml（使用者）。
新版本新增的設定項目會自動出現預設值，使用者已改的值不受影響。
"""
from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .paths import DEFAULTS_DIR, SETTINGS_FILE, lab_path

_lock = threading.Lock()
_current: Optional["Settings"] = None
_MISSING = object()


def _load_yaml(path: Path) -> Dict[str, Any]:
    import yaml

    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} 最外層必須是 mapping")
    return data


def deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Settings:
    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None) -> None:
        self.data = data
        self.path = path

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Settings":
        path = path or lab_path(SETTINGS_FILE)
        defaults = _load_yaml(DEFAULTS_DIR / SETTINGS_FILE)
        return cls(deep_merge(defaults, _load_yaml(path)), path)

    def get(self, key: str, default: Any = _MISSING) -> Any:
        node: Any = self.data
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                if default is _MISSING:
                    raise KeyError(f"settings.yaml 缺少設定：{key}")
                return default
        return copy.deepcopy(node)

    def set(self, key: str, value: Any) -> None:
        parts = key.split(".")
        node = self.data
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value

    def save(self) -> None:
        import yaml

        if self.path is None:
            raise ValueError("沒有設定檔路徑")
        self.path.write_text(yaml.safe_dump(self.data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def settings() -> Settings:
    global _current
    with _lock:
        if _current is None:
            _current = Settings.load()
        return _current


def reload(path: Optional[Path] = None) -> Settings:
    global _current
    with _lock:
        _current = Settings.load(path)
        return _current


def setting(key: str, default: Any = _MISSING) -> Any:
    return settings().get(key, default)
