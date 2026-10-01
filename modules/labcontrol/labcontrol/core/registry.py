"""外掛註冊表：driver / procedure / hook / writer / reader / viewer。

三種擴充方式（由簡到繁）：
  1. 把 .py 丟進專案根目錄的 plugins/ 資料夾，檔案裡用 @register_driver 等裝飾器註冊。
     （LAB/plugins 自動載入；instruments.yaml 的 plugin_paths 可以加更多資料夾）
  2. 寫在自己的套件裡，import 時註冊。
  3. 發佈成 pip 套件，在 pyproject.toml 宣告 entry point：
        [project.entry-points."labcontrol.plugins"]
        my_lab = "my_lab_plugins"
框架啟動時會自動載入。核心程式碼完全不用改。
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Callable, Dict, Generic, Iterable, List, Type, TypeVar

from .errors import ConfigError

log = logging.getLogger(__name__)
T = TypeVar("T")


class Registry(Generic[T]):
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: Dict[str, T] = {}

    def register(self, name: str) -> Callable[[T], T]:
        def deco(obj: T) -> T:
            if name in self._items and self._items[name] is not obj:
                log.warning("%s '%s' 被重新註冊（%s 取代 %s）", self.kind, name, obj, self._items[name])
            self._items[name] = obj
            try:
                setattr(obj, "registered_name", name)
                if self.kind == "driver":
                    setattr(obj, "driver_name", name)
            except (AttributeError, TypeError):
                pass
            return obj

        return deco

    def get(self, name: str) -> T:
        ensure_builtins_loaded()
        try:
            return self._items[name]
        except KeyError:
            raise ConfigError(
                f"找不到 {self.kind} '{name}'。已註冊：{', '.join(sorted(self._items)) or '(無)'}"
            ) from None

    def names(self) -> List[str]:
        ensure_builtins_loaded()
        return sorted(self._items)

    def __contains__(self, name: str) -> bool:
        ensure_builtins_loaded()
        return name in self._items


DRIVERS: Registry[Type] = Registry("driver")
PROCEDURES: Registry[Type] = Registry("procedure")
HOOKS: Registry[Type] = Registry("hook")
WRITERS: Registry[Type] = Registry("writer")
READERS: Registry[Type] = Registry("reader")

register_driver = DRIVERS.register
register_procedure = PROCEDURES.register
register_hook = HOOKS.register
register_writer = WRITERS.register
register_reader = READERS.register

# ---------------------------------------------------------------------------
_builtins_loaded = False
_loaded_paths: set = set()


def ensure_builtins_loaded() -> None:
    global _builtins_loaded
    if _builtins_loaded:
        return
    _builtins_loaded = True
    for mod in (
        "labcontrol.drivers",
        "labcontrol.measure.procedures",
        "labcontrol.measure.hooks",
        "labcontrol.data.writers",
        "labcontrol.data.readers",
    ):
        importlib.import_module(mod)
    load_entry_points()


def load_entry_points(group: str = "labcontrol.plugins") -> None:
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover
        return
    try:
        eps = entry_points()
        selected = eps.select(group=group) if hasattr(eps, "select") else eps.get(group, [])  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return
    for ep in selected:
        try:
            ep.load()
            log.info("已載入外掛套件 %s", ep.name)
        except Exception:  # noqa: BLE001
            log.exception("外掛 %s 載入失敗", ep.name)


def load_plugin_paths(paths: Iterable[str | Path]) -> List[str]:
    """匯入資料夾中所有 .py（底線開頭的略過）。回傳載入的模組名稱。"""
    ensure_builtins_loaded()
    loaded = []
    for p in paths:
        folder = Path(p).expanduser().resolve()
        if not folder.is_dir():
            log.warning("plugin 路徑不存在：%s", folder)
            continue
        for f in sorted(folder.glob("*.py")):
            if f.name.startswith("_") or str(f) in _loaded_paths:
                continue
            mod_name = f"labcontrol_userplugin_{f.stem}"
            spec = importlib.util.spec_from_file_location(mod_name, f)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            try:
                spec.loader.exec_module(module)
                _loaded_paths.add(str(f))
                loaded.append(mod_name)
                log.info("已載入 plugin %s", f.name)
            except Exception:  # noqa: BLE001
                log.exception("plugin %s 載入失敗", f)
    return loaded
