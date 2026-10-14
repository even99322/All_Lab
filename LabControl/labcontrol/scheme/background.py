"""1.0.6：「背景＋去背」開關。

流程圖只放一個精簡的「背景＋去背」方塊（不用再排 設定 → 量測 → Data → 去背 四個節點）：

    scheme.background = {
        "enabled": True,
        "sets": [{"target": "magnet_A", "value": 50, "unit": "mA"}, {"target": "DC5", "value": 0, "unit": "mA"}],
        "settle": 0.5,          # 設完背景電流後等待（s）
        "file_name": "",        # 背景檔名（空白 = 主檔名_bg.nc）
        "dest": "",             # 去背檔存到哪（空白 = 與數據相同）
        "suffix": "_debg",
    }

編譯時展開成原本的背景階段：勾選的電源設到背景電流 → 用與主量測**相同的儀器與設定**量一次 → 存背景檔 → 去背。
這個模組不 import Qt。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from .model import Block, Scheme

BG_ID = "__background__"
DEFAULTS: Dict[str, Any] = {"enabled": False, "sets": [], "settle": 0.5, "file_name": "", "dest": "",
                            "suffix": "_debg"}
SOURCE_GROUPS = ("magnet", "source")


def config(scheme: Scheme) -> Dict[str, Any]:
    c = {**DEFAULTS, **(scheme.background or {})}
    c["sets"] = [dict(s) for s in (c.get("sets") or []) if isinstance(s, dict) and s.get("target")]
    return c


def enabled(scheme: Scheme) -> bool:
    return bool((scheme.background or {}).get("enabled"))


def is_background_id(bid: Optional[str]) -> bool:
    return bool(bid) and str(bid).startswith(BG_ID)


def candidates(cat) -> list:
    """可以設背景電流的電源：電磁鐵組與單台電源（instruments.yaml 裡所有 Source）。"""
    return [t for t in cat.targets if t.group in SOURCE_GROUPS]


def _measures(blocks: List[Block]) -> List[Block]:
    out: List[Block] = []

    def rec(items: List[Block]) -> None:
        for b in items:
            if b.kind == "measure":
                out.append(b)
            rec(b.children)
    rec(blocks)
    return out


def _value(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def stage_blocks(scheme: Scheme, main: List[Block]) -> Tuple[List[Block], Block]:
    """→ (背景階段的方塊, 去背方塊)：設定（每個勾選的電源）→ 量測（複製主量測）→ Data。"""
    c = config(scheme)
    sets: List[Block] = []
    for i, s in enumerate(c["sets"]):
        sets.append(Block("set", id=f"{BG_ID}.set{i}", title="背景電流", target=str(s["target"]), mode="fixed",
                          value=_value(s.get("value")), unit=str(s.get("unit") or "")))
    if sets:
        sets[-1].settle = max(0.0, float(_value(c.get("settle")) or 0.0))
    measures = [Block("measure", id=f"{BG_ID}.meas{i}", instrument=m.instrument, traces=list(m.traces),
                      settings=copy.deepcopy(m.settings)) for i, m in enumerate(_measures(main))]
    formats = list(scheme.output.get("formats") or ["netcdf"])
    save = Block("save", id=f"{BG_ID}.save", title="背景", file_name=str(c.get("file_name") or ""), formats=formats)
    debg = Block("debg", id=BG_ID, dest=str(c.get("dest") or ""), suffix=str(c.get("suffix") or "_debg"))
    return sets + measures + [save], debg


def default_sets(scheme: Scheme, cat) -> List[Dict[str, Any]]:
    """第一次打開開關：預先勾選主流程掃描的電源（值 0，請改成遠離共振的電流）。"""
    names = {t.ref: t for t in candidates(cat)}
    out: List[Dict[str, Any]] = []
    for b in scheme.flat():
        if b.kind == "set" and b.target in names and all(s["target"] != b.target for s in out):
            out.append({"target": b.target, "value": 0.0, "unit": b.unit or names[b.target].unit})
    return out


def adopt_explicit(scheme: Scheme) -> bool:
    """把流程圖裡原本的「設定 → 量測 → Data（背景）→ 去背」節點收成精簡的開關設定（並刪掉那些節點）。"""
    from .compile import split_stages

    _main, stages, debg = split_stages(scheme)
    if not stages and debg is None:
        return False
    c = config(scheme)
    first = stages[0] if stages else []
    sets = [b for b in first if b.kind == "set" and b.mode == "fixed" and b.target]
    if sets:
        c["sets"] = [{"target": b.target, "value": b.value if b.value is not None else 0.0, "unit": b.unit}
                     for b in sets]
        c["settle"] = max([b.settle for b in sets] + [0.0])
    save = next((b for b in first if b.kind == "save"), None)
    if save is not None and save.file_name:
        c["file_name"] = save.file_name
    if debg is not None:
        c["dest"], c["suffix"] = debg.dest or "", debg.suffix or "_debg"
    c["enabled"] = True
    scheme.background = c
    ids = [b.id for blocks in stages for b in blocks] + ([debg.id] if debg is not None else [])
    if scheme.graph is not None:
        for nid in ids:
            scheme.graph.remove(nid, heal=True)
        scheme.sync_from_graph()
    else:
        scheme.blocks = [b for b in scheme.blocks if b.id not in ids]
    return True


def set_enabled(scheme: Scheme, on: bool, cat=None) -> None:
    c = config(scheme)
    if on and not c["sets"] and cat is not None:
        c["sets"] = default_sets(scheme, cat)
    c["enabled"] = bool(on)
    scheme.background = c


def remap(scheme: Scheme, mapping: Dict[str, str]) -> None:
    """量測電腦對應（magnet_A → magnet_C…）也換背景電流的電源。"""
    if not scheme.background or not mapping:
        return
    for s in scheme.background.get("sets") or []:
        head, _, rest = str(s.get("target", "")).partition(".")
        if head in mapping:
            s["target"] = mapping[head] + ("." + rest if rest else "")


def used_instruments(scheme: Scheme) -> List[str]:
    if not enabled(scheme):
        return []
    return [str(s["target"]).split(".")[0] for s in config(scheme)["sets"]]


def _fmt(v: Any) -> str:
    f = _value(v)
    if f is None:
        return "?"
    return f"{f:g}"


def summary(scheme: Scheme, cat=None) -> str:
    """一行說明：magnet_A 50 mA · DC5 0 mA。"""
    c = config(scheme)
    if not c["sets"]:
        return "尚未勾選電源"
    parts = []
    for s in c["sets"]:
        t = cat.target(s["target"]) if cat is not None else None
        name = t.short if t is not None else s["target"]
        parts.append(f"{name} {_fmt(s.get('value'))} {s.get('unit') or ''}".strip())
    return " · ".join(parts)
