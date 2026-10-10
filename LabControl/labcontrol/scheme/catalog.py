"""方塊庫：從 instruments.yaml 與各 driver 的參數表（ParamSpec）產生，程式碼中沒有任何儀器專屬的值。

  * group: magnet 的 Source            → 「DC set · 電磁鐵組」
  * 其他 Source                        → 「DC set · 單台電源」
  * TraceAcquirer（MEASURE_KIND）      → 「量測」；measure_setting=True 的參數成為量測方塊的儀器設定
  * sweepable=True 的參數              → 「儀器參數」（可固定或掃描）

instruments.yaml 可覆寫：label、traces、measure_defaults、parameters.<name>.{label, limits, default, …}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..core.capabilities import Source, TraceAcquirer
from ..core.station import Station
from ..core.units import split_unit
from ..settings import setting


@dataclass
class Target:
    ref: str
    label: str
    short: str
    group: str                      # magnet | source | param
    unit: str                       # 預設顯示單位
    kind_label: str                 # 流程圖方塊標題
    limits: Optional[Tuple[float, float]] = None   # SI
    ramp_rate: Optional[float] = None
    max_jump: Optional[float] = None
    param_label: str = ""
    instrument: str = ""            # 所屬儀器名稱（儀器參數列表分組用）
    common: bool = True             # driver 標記為常用（ParamSpec.sweepable）；其他數值參數也可設定 / 掃描
    interleave: bool = False        # 兩台交錯的電磁鐵組（virtual.interleaved_pair）：可以選「電流異步」掃描


@dataclass
class MeasureField:
    key: str
    label: str
    unit: str                       # 顯示單位（空白 = 無單位整數 / 數字）
    kind: str = "float"             # float | int | bool | str | enum
    choices: Optional[List[str]] = None


@dataclass
class Measurer:
    ref: str
    label: str
    kind: str                       # 來自 driver 的 MEASURE_KIND
    traces: List[str]
    fields: List[MeasureField]
    defaults: Dict[str, Any]
    labber_name: str = ""
    short: str = ""

    head_label: str = ""

    @property
    def head(self) -> str:
        return self.head_label or (self.kind.upper() if self.kind else "量測")

    def field(self, key: str) -> Optional[MeasureField]:
        return next((f for f in self.fields if f.key == key), None)

    def field_label(self, key: str) -> str:
        f = self.field(key)
        return f.label if f else key


@dataclass
class Catalog:
    targets: List[Target] = field(default_factory=list)
    measurers: List[Measurer] = field(default_factory=list)

    def target(self, ref: str) -> Optional[Target]:
        return next((t for t in self.targets if t.ref == ref), None)

    def measurer(self, ref: str) -> Optional[Measurer]:
        return next((m for m in self.measurers if m.ref == ref), None)


def _short(label: str) -> str:
    return str(label).split("（")[0].split(" (")[0]


def build_catalog(station: Station) -> Catalog:
    cat = Catalog()
    cur_unit = setting("editor.new_blocks.dc_set.unit", "mA")
    for name, inst in station.instruments.items():
        label = inst.options.get("label", name)
        short = _short(label)
        src = inst if isinstance(inst, Source) else Station._sole_source(inst)
        sources = [(name, label, short, src)] if src is not None else [
            (f"{name}.{k}", f"{label} {k}", f"{short} {k}", ch) for k, ch in inst.channels.items()
            if isinstance(ch, Source)]
        for ref, lab, sh, s in sources:
            group = "magnet" if inst.options.get("group") == "magnet" else "source"
            lim = (s.limits.lo, s.limits.hi) if s.limits.hi != float("inf") else None
            unit = inst.options.get("display_unit", cur_unit)
            suffix = (setting("editor.axis_name_suffix", {}) or {}).get(split_unit(unit)[0], "")
            cat.targets.append(Target(ref, lab, sh, group, unit,
                                      inst.options.get("block_title", setting("editor.dc_block_title", "DC set")),
                                      lim, s.ramp_policy.rate, s.ramp_policy.max_jump, suffix.strip(),
                                      instrument=name, interleave="interleave_step" in inst.parameters))
        # 其他可設定的數值參數（儀器本身與各通道）→ 儀器參數（固定值或掃描）
        nodes = [("", inst)] + [(k, ch) for k, ch in inst.channels.items()]
        for ck, node in nodes:
            for k, p in node.parameters.items():
                sp = p.spec
                if k.startswith("interleave_") or sp is None or not p.settable or sp.kind not in ("float", "int"):
                    continue
                ref = f"{name}.{ck}.{k}" if ck else f"{name}.{k}"
                where = f"{short} {ck}" if ck and len(inst.channels) > 1 else short
                lab = f"{where} {sp.label or k}"
                cat.targets.append(Target(ref, lab, lab, "param", sp.shown_unit, lab, sp.limits,
                                          param_label=sp.label or k, instrument=name, common=bool(sp.sweepable)))
        if isinstance(inst, TraceAcquirer):
            specs = [(k, p.spec) for k, p in inst.parameters.items() if p.spec is not None]
            fields = [MeasureField(k, s.label or k, s.shown_unit, s.kind, s.choices) for k, s in specs if s.measure_setting]
            defaults = {k: s.default for k, s in specs if s.measure_setting and s.default is not None}
            defaults.update(inst.options.get("measure_defaults") or {})
            cat.measurers.append(Measurer(name, label, getattr(inst, "MEASURE_KIND", "") or "", inst.trace_list(),
                                          fields, defaults, inst.options.get("labber_name", name), short,
                                          inst.options.get("block_title", "")))
    order = {"magnet": 0, "source": 1, "param": 2}
    cat.targets.sort(key=lambda t: order[t.group])
    return cat
