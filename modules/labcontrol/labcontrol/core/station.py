"""Station：實驗室所有儀器的唯一入口。

- 所有儀器位址、型號、安全限制只寫在 LAB/instruments.yaml 一個地方。
- 每台儀器在同一個程式裡只會被開啟一次（web 面板和量測共用，不再搶 VISA）。
- lease 機制：量測進行中，被使用的儀器會被鎖給該次量測；
  其他前端（web 面板）仍可讀取，但寫入會收到 InstrumentBusy。
- simulate=True 時自動換成模擬 driver，可在沒有硬體的電腦上開發。
"""
from __future__ import annotations

import contextlib
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Union

from .capabilities import Source, TraceAcquirer
from .config import load_config
from .errors import ConfigError, InstrumentBusy
from .events import EventBus
from .instrument import Channel, Instrument, Parameter
from .registry import DRIVERS, ensure_builtins_loaded, load_plugin_paths

log = logging.getLogger(__name__)

Ref = str
Resolved = Union[Instrument, Channel, Parameter]


class Station:
    def __init__(self, config: Optional[Dict[str, Any]] = None, *, simulate: Optional[bool] = None,
                 bus: Optional[EventBus] = None) -> None:
        ensure_builtins_loaded()
        self.config: Dict[str, Any] = dict(config or {})
        self.bus = bus or EventBus()
        from ..settings import setting

        if simulate is None:
            simulate = self.config.get("simulate", setting("app.simulate", False))
        self.simulate = bool(simulate)
        self.sim_time_scale = float(self.config.get("sim_time_scale", setting("app.sim_time_scale", 1.0)))
        self.instruments: Dict[str, Instrument] = {}
        self._leases: Dict[str, str] = {}
        self._lease_lock = threading.Lock()
        #: 連線前檢查（遠端量測：共用的網路儀器歸別台電腦時拒絕）；回傳拒絕原因或 None
        self.connect_guard: Optional[Callable[[str], Optional[str]]] = None

        base = Path(self.config.get("_source_path", ".")).parent
        paths = [Path(p) if Path(p).is_absolute() else base / p for p in self.config.get("plugin_paths", [])]
        if self.config.get("_lab_plugins", True):
            from ..paths import lab_path
            lab_plugins = lab_path("plugins")
            if lab_plugins.is_dir() and lab_plugins not in paths:
                paths.append(lab_plugins)
        if paths:
            load_plugin_paths(paths)

        for name, opts in (self.config.get("instruments") or {}).items():
            opts = dict(opts or {})
            if opts.pop("enabled", True) is False:
                continue
            driver = opts.pop("driver", None)
            if not driver:
                raise ConfigError(f"儀器 {name} 缺少 driver")
            self.add(name, driver, **opts)

    @property
    def config_path(self) -> Optional[Path]:
        """讀進來的儀器清單檔（儀器伺服器新增 / 移除時寫回這個檔）。"""
        p = self.config.get("_source_path")
        return Path(p) if p else None

    @classmethod
    def from_file(cls, path: str | Path, **kw) -> "Station":
        return cls(load_config(path), **kw)

    @classmethod
    def from_lab(cls, path: Optional[str | Path] = None, **kw) -> "Station":
        """讀 LAB/instruments.yaml（檔名見 settings.yaml app.instruments_file）。"""
        from ..paths import ensure_lab_home, lab_path
        from ..settings import setting

        if path is None:
            ensure_lab_home()
            path = lab_path(setting("app.instruments_file", "instruments.yaml"))
        return cls.from_file(path, **kw)

    # ---- 建立 ---------------------------------------------------------------
    def add(self, name: str, driver: str, **options: Any) -> Instrument:
        if name in self.instruments:
            raise ConfigError(f"儀器名稱重複：{name}")
        if "." in name:
            raise ConfigError(f"儀器名稱不可含 '.'：{name}")
        cls = DRIVERS.get(driver)
        if self.simulate and getattr(cls, "sim_driver", None):
            sim_opts = dict(options.pop("sim", None) or {})
            options = {**options, **sim_opts, "real_driver": driver}
            cls = DRIVERS.get(cls.sim_driver)
        else:
            options.pop("sim", None)
        inst = cls(name, station=self, **options)
        inst.config_driver = driver   # instruments.yaml 寫的 driver 名稱（模擬時實際類別可能不同）
        if self.simulate:
            scale = self.sim_time_scale
            for node in [inst, *inst.channels.values()]:
                if isinstance(node, Source):
                    node.time_scale = scale
        self.instruments[name] = inst
        self.bus.publish("station.changed", action="add", name=name)
        return inst

    def remove(self, name: str) -> None:
        """從 Station 移除一台儀器（先關閉連線）。量測使用中或被虛擬儀器使用時拒絕。"""
        if name not in self.instruments:
            raise ConfigError(f"找不到儀器 '{name}'")
        if self.lease_holder(name) is not None:
            raise InstrumentBusy(f"[{name}] 量測使用中，不能移除")
        users = [n for n, i in self.instruments.items() if n != name and
                 name in {self._inst_name(d) for d in i.dependencies()}]
        if users:
            raise ConfigError(f"{name} 被 {', '.join(users)} 使用中，請先移除它們")
        self.disconnect(name)
        del self.instruments[name]
        self.bus.publish("station.changed", action="remove", name=name)

    def disconnect(self, name: str) -> None:
        """只中斷這一台（不影響相依儀器）。"""
        inst = self.instruments[name]
        if self.lease_holder(name) is not None:
            raise InstrumentBusy(f"[{name}] 量測使用中，不能中斷連線")
        if inst.connected:
            try:
                inst.close()
            finally:
                if getattr(inst, "_owns_transport", False):
                    inst.transport = None      # 下次連線重新開啟 VISA
                inst.connected = False
                self.bus.publish("instrument.closed", name=name)

    # ---- 連線 ---------------------------------------------------------------
    def connect(self, names: Optional[Iterable[str]] = None, force: bool = False) -> None:
        for n in self._connect_order(names):
            inst = self.instruments[n]
            if inst.connected:
                continue
            if self.connect_guard is not None and not force:
                reason = self.connect_guard(n)
                if reason:
                    self.bus.publish("instrument.error", name=n, error=reason)
                    raise InstrumentBusy(reason)
            try:
                inst.connect()
                self.bus.publish("instrument.connected", name=n, idn=inst._idn)
            except Exception as e:
                self.bus.publish("instrument.error", name=n, error=str(e))
                raise

    def connect_all(self, names: Optional[Iterable[str]] = None, force: bool = False) -> Dict[str, str]:
        """一次連線（遇到錯誤繼續下一台）。回傳 {名稱: 錯誤訊息}，成功或本來就連著的是空字串。"""
        out: Dict[str, str] = {}
        for n in self._connect_order(names):
            if self.instruments[n].connected:
                out[n] = ""
                continue
            try:
                self.connect([n], force=force)
                out[n] = ""
            except Exception as e:  # noqa: BLE001
                out[n] = str(e) or type(e).__name__
        return out

    def disconnect_all(self, names: Optional[Iterable[str]] = None) -> Dict[str, str]:
        """一次中斷（量測使用中的跳過）。回傳 {名稱: 錯誤訊息}。"""
        out: Dict[str, str] = {}
        for n in reversed(self._connect_order(names)):
            try:
                self.disconnect(n)
                out[n] = ""
            except Exception as e:  # noqa: BLE001
                out[n] = str(e) or type(e).__name__
        return out

    def ensure_connected(self, refs: Iterable[Ref]) -> None:
        self.connect(self.expand(refs))

    def close(self, names: Optional[Iterable[str]] = None) -> None:
        order = self._connect_order(names)
        for n in reversed(order):
            try:
                self.instruments[n].close()
            except Exception:  # noqa: BLE001
                log.exception("關閉 %s 失敗", n)

    def __enter__(self) -> "Station":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _connect_order(self, names: Optional[Iterable[str]]) -> List[str]:
        wanted = self.expand(names) if names is not None else set(self.instruments)
        order: List[str] = []
        visiting: set = set()

        def visit(n: str) -> None:
            if n in order:
                return
            if n in visiting:
                raise ConfigError(f"虛擬儀器相依關係出現循環：{n}")
            visiting.add(n)
            for dep in self.instruments[n].dependencies():
                visit(self._inst_name(dep))
            visiting.discard(n)
            order.append(n)

        for n in sorted(wanted):
            visit(n)
        return order

    # ---- 解析 ref -----------------------------------------------------------
    @staticmethod
    def _inst_name(ref: Ref) -> str:
        return ref.split(".", 1)[0]

    def expand(self, refs: Iterable[Ref]) -> set:
        """ref → 包含相依儀器在內的完整儀器名稱集合。"""
        out: set = set()
        stack = [self._inst_name(r) for r in refs]
        while stack:
            n = stack.pop()
            if n in out:
                continue
            if n not in self.instruments:
                raise ConfigError(f"找不到儀器 '{n}'。可用：{', '.join(self.instruments)}")
            out.add(n)
            stack.extend(self._inst_name(d) for d in self.instruments[n].dependencies())
        return out

    def get(self, ref: Ref) -> Resolved:
        parts = ref.split(".")
        name = parts[0]
        if name not in self.instruments:
            raise ConfigError(f"找不到儀器 '{name}'（ref='{ref}'）。可用：{', '.join(self.instruments)}")
        inst = self.instruments[name]
        node: Union[Instrument, Channel] = inst
        i = 1
        if i < len(parts) and parts[i] in inst.channels:
            node = inst.channels[parts[i]]
            i += 1
        if i == len(parts):
            return node
        if i == len(parts) - 1:
            pname = parts[i]
            if pname in node.parameters:
                return node.parameters[pname]
            sole = inst.sole_channel() if node is inst else None
            if sole is not None and pname in sole.parameters:
                return sole.parameters[pname]
        raise ConfigError(f"無法解析 ref '{ref}'")

    def __getitem__(self, ref: Ref) -> Resolved:
        return self.get(ref)

    def parameter(self, ref: Ref) -> Parameter:
        obj = self.get(ref)
        if isinstance(obj, Parameter):
            return obj
        if isinstance(obj, Source):
            return obj.parameters["level"]  # type: ignore[attr-defined]
        src = self._sole_source(obj)
        if src is not None:
            return src.parameters["level"]  # type: ignore[attr-defined]
        raise ConfigError(f"'{ref}' 不是可設定的參數")

    def source(self, ref: Ref) -> Source:
        obj = self.get(ref)
        if isinstance(obj, Source):
            return obj
        src = self._sole_source(obj)
        if src is None:
            raise ConfigError(f"'{ref}' 不是 Source")
        return src

    def acquirer(self, ref: Ref) -> TraceAcquirer:
        obj = self.get(ref)
        if not isinstance(obj, TraceAcquirer):
            raise ConfigError(f"'{ref}' 不是 TraceAcquirer")
        return obj

    @staticmethod
    def _sole_source(obj: Any) -> Optional[Source]:
        if isinstance(obj, Instrument):
            ch = obj.sole_channel()
            if isinstance(ch, Source):
                return ch
        return None

    # ---- lease --------------------------------------------------------------
    def lease_holder(self, name: str) -> Optional[str]:
        with self._lease_lock:
            return self._leases.get(name)

    @contextlib.contextmanager
    def lease(self, owner: str, refs: Iterable[Ref]) -> Iterator[set]:
        names = self.expand(refs)
        with self._lease_lock:
            busy = {n: self._leases[n] for n in names if n in self._leases and self._leases[n] != owner}
            if busy:
                raise InstrumentBusy(f"儀器使用中：{busy}")
            for n in names:
                self._leases[n] = owner
        self.bus.publish("station.lease", owner=owner, instruments=sorted(names), acquired=True)
        try:
            yield names
        finally:
            with self._lease_lock:
                for n in names:
                    if self._leases.get(n) == owner:
                        del self._leases[n]
            self.bus.publish("station.lease", owner=owner, instruments=sorted(names), acquired=False)

    # ---- 中繼資料 -------------------------------------------------------------
    def snapshot(self, names: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        wanted = self.expand(names) if names is not None else set(self.instruments)
        return {n: self.instruments[n].snapshot() for n in sorted(wanted) if self.instruments[n].connected}

    def describe(self) -> List[Dict[str, Any]]:
        out = []
        for n, inst in self.instruments.items():
            out.append({
                "name": n,
                "driver": inst.driver_name,
                "label": inst.options.get("label", n),
                "connected": inst.connected,
                "channels": list(inst.channels),
                "parameters": list(inst.parameters),
                "leased_by": self.lease_holder(n),
            })
        return out
