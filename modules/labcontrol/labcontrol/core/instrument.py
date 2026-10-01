"""儀器抽象：Instrument / Channel / Parameter。

命名規則（ref）：
    "DC1"              → Instrument
    "DC5.ch2"          → Channel（多通道儀器，例如 GS820）
    "VNA1.power"       → Parameter
    "DC5.ch2.level"    → Channel 的 Parameter
    "DC1.level"        → 單通道儀器可以省略 ch1（自動 fallback 到唯一的通道）

任何可被掃描的東西都是 Parameter，所以「掃 VNA 功率 × 掃電流」的二維量測
不需要改程式，只要在 YAML 多寫一個 axis。
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from .errors import ConfigError, InstrumentBusy
from .transport import Transport, VisaTransport

if TYPE_CHECKING:  # pragma: no cover
    from .station import Station

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 「誰在寫入」：量測引擎在自己的執行緒宣告 owner，Station 依此判斷 lease
# ---------------------------------------------------------------------------
_owner_ctx = threading.local()


def current_owner() -> Optional[str]:
    return getattr(_owner_ctx, "owner", None)


class acting_as:
    """with acting_as("run-0012"): ...  在此區塊內的寫入視為該 owner 發出。"""

    def __init__(self, owner: Optional[str]) -> None:
        self.owner = owner
        self._prev: Optional[str] = None

    def __enter__(self):
        self._prev = current_owner()
        _owner_ctx.owner = self.owner
        return self

    def __exit__(self, *exc):
        _owner_ctx.owner = self._prev
        return False


# ---------------------------------------------------------------------------
class Parameter:
    """可讀/可寫的一個量。p() 讀取，p(value) 寫入。"""

    def __init__(
        self,
        owner: "Node",
        name: str,
        get: Optional[Callable[[], Any]] = None,
        set: Optional[Callable[[Any], None]] = None,  # noqa: A002
        unit: str = "",
        doc: str = "",
        snapshot: bool = True,
    ) -> None:
        self.owner = owner
        self.name = name
        self._get = get
        self._set = set
        self.unit = unit
        self.doc = doc
        self.in_snapshot = snapshot
        self.cache: Any = None
        self.spec = None          # driverkit.ParamSpec（宣告式參數才有）：標籤、單位、上下限、方塊庫資訊

    @property
    def full_name(self) -> str:
        return f"{self.owner.full_name}.{self.name}"

    @property
    def settable(self) -> bool:
        return self._set is not None

    @property
    def gettable(self) -> bool:
        return self._get is not None

    def get(self) -> Any:
        if self._get is None:
            return self.cache
        self.cache = self._get()
        return self.cache

    def set(self, value: Any) -> None:
        if self._set is None:
            raise ConfigError(f"{self.full_name} 不可寫入")
        self.owner.guard_write()
        self._set(value)
        if not (self.spec is not None and self.spec.readback):
            self.cache = value

    def __call__(self, *args):
        if args:
            return self.set(args[0])
        return self.get()

    def __repr__(self) -> str:
        return f"<Parameter {self.full_name} [{self.unit}]>"


class Node:
    """Instrument 與 Channel 的共同基底：持有 parameters。"""

    name: str

    def __init__(self) -> None:
        self.parameters: Dict[str, Parameter] = {}

    @property
    def full_name(self) -> str:
        return self.name

    def add_parameter(self, name: str, **kw) -> Parameter:
        p = Parameter(self, name, **kw)
        self.parameters[name] = p
        return p

    def guard_write(self) -> None:  # 由子類別實作
        pass

    #: 宣告式參數表（driverkit.ParamSpec），子類別覆寫
    PARAMS: List[Any] = []

    def scpi_context(self) -> Dict[str, Any]:
        """SCPI 樣板可用的變數（例如 {ch}）。子類別覆寫。"""
        return {}

    def init_params(self, overrides: Optional[Dict[str, Any]] = None) -> None:
        from .driverkit import build_parameters

        if self.PARAMS:
            build_parameters(self, self.PARAMS, overrides)

    def snapshot_parameters(self) -> Dict[str, Any]:
        out = {}
        for name, p in self.parameters.items():
            if not (p.in_snapshot and p.gettable):
                continue
            try:
                out[name] = {"value": p.get(), "unit": p.unit}
            except Exception as e:  # noqa: BLE001
                out[name] = {"value": None, "unit": p.unit, "error": str(e)}
        return out


class Channel(Node):
    def __init__(self, parent: "Instrument", key: str) -> None:
        super().__init__()
        self.parent = parent
        self.key = key
        self.name = key
        ch_opts = (parent.options.get("channels") or {}).get(key) or {}
        self.init_params(ch_opts.get("parameters"))

    @property
    def full_name(self) -> str:
        return f"{self.parent.name}.{self.key}"

    @property
    def transport(self) -> Optional[Transport]:
        return self.parent.transport

    def guard_write(self) -> None:
        self.parent.guard_write()

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.full_name}>"


class Instrument(Node):
    """所有 driver 的基底。

    子類別通常只需要：
      - 在 __init__ 建立 channels / add_parameter
      - 覆寫 on_connect()（讀回儀器目前狀態，**不要**在這裡改變輸出）
      - 覆寫 snapshot()（需要時）
    """

    #: 由 @register_driver 自動填入
    driver_name: str = ""
    #: 量測種類（vna / shfqc / …），TraceAcquirer 用；方塊庫與規則依此分類
    MEASURE_KIND: str = ""
    #: 可量測的通道名稱（例如 S21），instruments.yaml 的 traces 可覆寫
    TRACES: List[str] = []
    #: *IDN? 應該符合的樣式（正規表示式）；驅動測試與 VISA 掃描用來確認 / 建議驅動
    IDN_PATTERN: Optional[str] = None
    #: 讀錯誤佇列的指令；None = 儀器不支援
    ERROR_QUERY: Optional[str] = ":SYST:ERR?"
    #: simulate 模式下改用哪個 driver（例如 "sim.current_source"）
    sim_driver: Optional[str] = None
    #: 預設 VISA 設定
    default_timeout_ms: int = 5000
    write_termination: Optional[str] = "\n"
    read_termination: Optional[str] = "\n"

    def __init__(self, name: str, *, transport: Optional[Transport] = None,
                 station: Optional["Station"] = None, **options: Any) -> None:
        super().__init__()
        self.name = name
        self.options = options
        self.transport = transport
        self.station = station
        self.channels: Dict[str, Channel] = {}
        self.connected = False
        self._idn = ""
        self._owns_transport = False
        self.init_params(self.options.get("parameters"))

    def trace_list(self) -> List[str]:
        return list(self.options.get("traces") or self.TRACES)

    def check_errors(self) -> List[str]:
        """讀空儀器錯誤佇列（不丟例外）。"""
        from .driverkit import drain_errors

        if self.ERROR_QUERY is None or self.transport is None:
            return []
        return drain_errors(self.transport, self.ERROR_QUERY)

    # ---- 連線 ------------------------------------------------------------
    def open_transport(self) -> Optional[Transport]:
        """預設用 VISA；不需要通訊的 driver（虛擬、模擬）回傳 None。"""
        address = self.options.get("address")
        if not address:
            raise ConfigError(f"[{self.name}] 缺少 address")
        from ..settings import setting

        return VisaTransport(
            address,
            timeout_ms=int(self.options.get("timeout_ms", self.default_timeout_ms)),
            write_termination=self.options.get("write_termination", self.write_termination),
            read_termination=self.options.get("read_termination", self.read_termination),
            backend=str(self.options.get("visa_backend") or setting("server.visa_backend", "") or ""),
        )

    def connect(self) -> None:
        if self.connected:
            return
        if self.transport is None:
            self.transport = self.open_transport()
            self._owns_transport = self.transport is not None
        self.connected = True
        try:
            self._idn = self.idn()
            self.on_connect()
        except Exception:
            self.connected = False
            raise
        log.info("[%s] connected: %s", self.name, self._idn)

    def on_connect(self) -> None:
        """讀回儀器狀態。請勿在此改變輸出（避免重開程式時電流跳動）。"""

    def idn(self) -> str:
        if self.transport is None:
            return f"{type(self).__name__} (no transport)"
        return self.transport.query("*IDN?")

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
        self.connected = False

    # ---- 結構 ------------------------------------------------------------
    def add_channel(self, ch: Channel) -> Channel:
        self.channels[ch.key] = ch
        return ch

    def sole_channel(self) -> Optional[Channel]:
        return next(iter(self.channels.values())) if len(self.channels) == 1 else None

    def dependencies(self) -> List[str]:
        """此儀器依賴的其他 ref（虛擬儀器用）；Station 據此決定連線順序與 lease 範圍。"""
        return []

    # ---- 權限 ------------------------------------------------------------
    def guard_write(self) -> None:
        if self.station is not None:
            holder = self.station.lease_holder(self.name)
            if holder is not None and holder != current_owner():
                raise InstrumentBusy(f"[{self.name}] 目前由 {holder} 使用中，暫時不能從這裡寫入")

    # ---- 中繼資料 ---------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        snap: Dict[str, Any] = {
            "driver": self.driver_name,
            "idn": self._idn,
            "address": self.options.get("address"),
            "parameters": self.snapshot_parameters(),
        }
        if self.channels:
            snap["channels"] = {k: ch.snapshot_parameters() for k, ch in self.channels.items()}
        return snap

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name} ({self.driver_name})>"
