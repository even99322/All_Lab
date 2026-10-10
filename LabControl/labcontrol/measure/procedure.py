"""量測程序（Procedure）：定義「每一個點要量什麼」。

掃描、暫停、回溯、手動模式、錯誤重試、斜坡、lease、存檔……全部由 Runner 負責，
所以一個新的量測邏輯通常只要實作 setup() 與 measure() 兩個方法。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..core.capabilities import TraceAcquirer
from ..core.errors import ConfigError
from ..core.instrument import Parameter
from ..core.units import to_si_dict
from ..data.dataset import ChannelSpec

if TYPE_CHECKING:  # pragma: no cover
    from ..core.station import Station
    from ..data.dataset import Dataset


@dataclass
class RunContext:
    station: "Station"
    run_id: str
    stop_event: threading.Event
    index: int = 0
    setpoints: Dict[str, float] = field(default_factory=dict)
    dataset: Optional["Dataset"] = None
    shared: Dict[str, Any] = field(default_factory=dict)   # 程序與 hooks 之間共享的暫存

    def log(self, msg: str, level: str = "info") -> None:
        self.station.bus.log(msg, level)


class Procedure:
    """子類別用 @register_procedure("名稱") 註冊，實驗 YAML 以 procedure.type 指定。"""

    def __init__(self, station: "Station", config: Dict[str, Any], setup: Optional[Dict[str, Any]] = None) -> None:
        self.station = station
        self.config = dict(config or {})
        self.setup_block: Dict[str, Any] = dict(setup or {})

    # 用到哪些儀器（Runner 用來連線與 lease）
    def refs(self) -> List[str]:
        return list(self.setup_block)

    def apply_setup(self, ctx: RunContext) -> None:
        """套用實驗 YAML 的 setup 區塊：
            VNA1: {start_freq: 5.0198 GHz, ...}   → VNA1.configure(**settings)
            DC1.range: 0.2                        → 參數直接設定
        """
        for ref, value in self.setup_block.items():
            obj = self.station.get(ref)
            if isinstance(obj, Parameter):
                obj.set(to_si_dict({"v": value})["v"])
            elif isinstance(value, dict) and hasattr(obj, "configure"):
                obj.configure(**to_si_dict(value))
            else:
                raise ConfigError(f"setup 無法套用到 {ref}")

    def setup(self, ctx: RunContext) -> List[ChannelSpec]:
        """回傳這個程序會產生的資料通道。"""
        self.apply_setup(ctx)
        return []

    def measure(self, ctx: RunContext) -> Dict[str, Any]:
        """量一次（一個 shot），回傳 {channel_name: value}。"""
        raise NotImplementedError

    def on_error(self, ctx: RunContext, error: Exception) -> None:
        """measure 出錯、重試之前呼叫；可在此做儀器恢復（例如 VNA recover）。"""

    def teardown(self, ctx: RunContext) -> None:
        pass

    def metadata(self) -> Dict[str, Any]:
        return {"type": getattr(self, "registered_name", type(self).__name__),
                "config": self.config, "setup": self.setup_block}


# ---------------------------------------------------------------------------
@dataclass
class Readout:
    ref: str
    name: str
    trace: Optional[str] = None
    export_name: Optional[str] = None


def parse_readouts(items: List[Dict[str, Any]]) -> List[Readout]:
    out = []
    for d in items or []:
        if "ref" not in d:
            raise ConfigError(f"readout 缺少 ref：{d}")
        name = d.get("name") or d.get("trace") or d["ref"].split(".")[-1]
        out.append(Readout(d["ref"], name, d.get("trace"), d.get("export_name")))
    names = [r.name for r in out]
    if len(set(names)) != len(names):
        raise ConfigError(f"readout 名稱重複：{names}")
    return out


def readout_is_trace(station: "Station", r: Readout) -> bool:
    return r.trace is not None or isinstance(station.get(r.ref), TraceAcquirer)
