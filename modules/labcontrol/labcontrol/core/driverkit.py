"""驅動撰寫規範（driver kit）：用「參數表」宣告儀器，框架自動產生 Parameter、上下限檢查、讀回、
方塊庫資訊與 snapshot。完整說明見 docs/DRIVER_GUIDE.md。

    class MyVNA(Instrument, TraceAcquirer):
        MEASURE_KIND = "vna"
        TRACES = ["S21", "S11"]
        PARAMS = [
            ParamSpec("power", label="功率", unit="dBm", display_unit="dBm",
                      get=":SOUR{ch}:POW?", set=":SOUR{ch}:POW {value}",
                      limits=(-60, 10), default="-10 dBm", measure_setting=True, sweepable=True),
        ]

SCPI 樣板裡的 {…} 由 scpi_context() 提供（例如 {ch} = 通道號），{value} 是要寫入的值。
樣板不夠用時，在 driver 裡定義 _get_<name>(self) / _set_<name>(self, value) 即取代樣板。

每台儀器可在 instruments.yaml 覆寫任何欄位（不必改程式）：
    VNA1:
      driver: rs.zna
      parameters:
        power: {limits: [-40, 0], default: -20 dBm}
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Tuple

from .errors import ConfigError, InstrumentError, LimitError

if TYPE_CHECKING:  # pragma: no cover
    from .instrument import Node

KINDS = ("float", "int", "bool", "str", "enum")


@dataclass
class ParamSpec:
    name: str
    label: str = ""
    unit: str = ""                          # SI 單位（內部值）
    display_unit: str = ""                  # UI 顯示單位（空白 = unit）
    get: Optional[str] = None               # SCPI 查詢樣板
    set: Optional[str] = None               # SCPI 設定樣板，{value} 代入值
    kind: str = "float"                     # float | int | bool | str | enum
    choices: Optional[List[str]] = None     # enum 的選項
    limits: Optional[Tuple[float, float]] = None   # SI；超出 → LimitError
    default: Any = None                     # 量測方塊預設值（可帶單位字串）
    measure_setting: bool = False           # 出現在量測方塊的「儀器設定」
    sweepable: bool = False                 # 出現在方塊庫「儀器參數」（可固定或掃描）
    readback: bool = False                  # 設定後讀回（儀器會自動修正的值，如 IF 頻寬）
    snapshot: bool = True                   # 寫入資料檔的儀器設定
    on_off: Tuple[str, str] = ("ON", "OFF")  # bool 寫入時用的字
    doc: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ConfigError(f"ParamSpec {self.name}: kind 必須是 {KINDS}")
        if self.limits is not None:
            self.limits = (float(self.limits[0]), float(self.limits[1]))

    @property
    def shown_unit(self) -> str:
        return self.display_unit or self.unit

    def with_overrides(self, over: Optional[Dict[str, Any]]) -> "ParamSpec":
        if not over:
            return self
        known = {f.name for f in dataclasses.fields(self)}
        bad = set(over) - known
        if bad:
            raise ConfigError(f"參數 {self.name} 不認得的覆寫欄位：{sorted(bad)}")
        d = {**dataclasses.asdict(self), **over}
        if d.get("limits") is not None:
            d["limits"] = tuple(d["limits"])
        if d.get("on_off") is not None:
            d["on_off"] = tuple(d["on_off"])
        return ParamSpec(**d)

    # ---- 型別轉換 ------------------------------------------------------------
    def encode(self, value: Any) -> str:
        if self.kind == "bool":
            return self.on_off[0] if _truthy(value) else self.on_off[1]
        if self.kind == "int":
            return str(int(round(float(value))))
        if self.kind == "float":
            return f"{float(value):.12g}"
        if self.kind == "enum" and self.choices and str(value) not in self.choices:
            raise ConfigError(f"{self.name} 必須是 {self.choices}")
        return str(value)

    def decode(self, text: str) -> Any:
        t = text.strip().strip("'\"")
        if self.kind == "bool":
            return _truthy(t)
        if self.kind == "int":
            return int(float(t))
        if self.kind == "float":
            return float(t)
        return t

    def check(self, value: Any, who: str) -> None:
        if self.limits is None or self.kind not in ("float", "int"):
            return
        v = float(value)
        lo, hi = self.limits
        if not (lo <= v <= hi):
            raise LimitError(f"{who} = {v:g} 超出允許範圍 [{lo:g}, {hi:g}] {self.unit}")


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().upper() in ("1", "ON", "TRUE", "YES")
    return bool(v)


class _SafeDict(dict):
    def __missing__(self, key):  # 樣板裡用到但 context 沒有的 key → 明確錯誤
        raise ConfigError(f"SCPI 樣板需要 {{{key}}}，但 scpi_context() 沒有提供")


def render(template: str, context: Dict[str, Any], **extra: Any) -> str:
    return template.format_map(_SafeDict({**context, **extra}))


def build_parameters(node: "Node", specs: Sequence[ParamSpec], overrides: Optional[Dict[str, Any]] = None) -> None:
    """依參數表在 node 上建立 Parameter。由 Node.init_params() 呼叫，driver 通常不用直接呼叫。"""
    overrides = dict(overrides or {})
    for spec in specs:
        spec = spec.with_overrides(overrides.pop(spec.name, None))
        getter = _make_getter(node, spec)
        setter = _make_setter(node, spec)
        p = node.add_parameter(spec.name, get=getter, set=setter, unit=spec.unit, doc=spec.doc,
                               snapshot=spec.snapshot and getter is not None)
        p.spec = spec
    if overrides:
        raise ConfigError(f"[{node.full_name}] parameters 覆寫了不存在的參數：{sorted(overrides)}")


def _make_getter(node: "Node", spec: ParamSpec) -> Optional[Callable[[], Any]]:
    custom = getattr(node, f"_get_{spec.name}", None)
    if custom is not None:
        return custom
    if spec.get is None:
        return None

    def getter() -> Any:
        return spec.decode(node.transport.query(render(spec.get, node.scpi_context())))
    return getter


def _make_setter(node: "Node", spec: ParamSpec) -> Optional[Callable[[Any], None]]:
    custom = getattr(node, f"_set_{spec.name}", None)
    if custom is None and spec.set is None:
        return None

    def setter(value: Any) -> None:
        spec.check(value, f"{node.full_name}.{spec.name}")
        if custom is not None:
            custom(value)
        else:
            node.transport.write(render(spec.set, node.scpi_context(), value=spec.encode(value)))
        if spec.readback and spec.get is not None:
            node.parameters[spec.name].cache = spec.decode(
                node.transport.query(render(spec.get, node.scpi_context())))
    return setter


# ---------------------------------------------------------------------------
_NO_ERROR = re.compile(r'^\s*[+]?0\s*(,|$)')


def drain_errors(transport, query: str = ":SYST:ERR?", max_n: int = 20) -> List[str]:
    """讀空儀器錯誤佇列，回傳錯誤訊息（沒有錯誤 → 空 list）。"""
    out = []
    for _ in range(max_n):
        ans = transport.query(query)
        if not ans or _NO_ERROR.match(ans):
            break
        out.append(ans.strip())
    return out


def raise_if_errors(transport, who: str, query: str = ":SYST:ERR?") -> None:
    errs = drain_errors(transport, query)
    if errs:
        raise InstrumentError(f"[{who}] 儀器回報錯誤：{'; '.join(errs)}")


def build_memory_parameters(node: "Node", specs: Sequence[ParamSpec], store: Dict[str, Any],
                            overrides: Optional[Dict[str, Any]] = None,
                            on_change: Optional[Callable[[str, Any], None]] = None,
                            real_cls: Optional[type] = None) -> None:
    """模擬儀器用：依（實機 driver 的）參數表建立存在記憶體的 Parameter，上下限、標籤、方塊庫資訊與實機一致。
    real_cls：實機類別；只有實機可寫的參數（有 set 樣板或 _set_<name>）在模擬中才可寫。"""
    overrides = dict(overrides or {})
    for spec in specs:
        spec = spec.with_overrides(overrides.pop(spec.name, None))
        if spec.name not in store:
            store[spec.name] = _default_si(spec)

        def getter(n=spec.name):
            return store.get(n)

        def setter(v, s=spec):
            s.check(v, f"{node.full_name}.{s.name}")
            store[s.name] = s.decode(s.encode(v)) if s.kind != "str" else v
            if on_change:
                on_change(s.name, store[s.name])
        writable = real_cls is None or spec.set is not None or hasattr(real_cls, f"_set_{spec.name}")
        p = node.add_parameter(spec.name, get=getter, set=setter if writable else None, unit=spec.unit,
                               doc=spec.doc, snapshot=spec.snapshot)
        p.spec = spec


def _default_si(spec: ParamSpec) -> Any:
    from .units import parse_quantity

    if spec.default is None:
        return None
    if spec.kind in ("float", "int"):
        try:
            v = parse_quantity(spec.default)
            return int(v) if spec.kind == "int" else v
        except Exception:  # noqa: BLE001
            return None
    if spec.kind == "bool":
        return _truthy(spec.default)
    return spec.default
