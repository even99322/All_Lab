"""方案裡的儀器依「在哪台量測電腦執行」自動換成那台的儀器（0.0.31）。

instruments.yaml（全實驗室一份）用 ``node:`` 標明儀器接在哪台：例如
    LAB-1：電磁鐵組 magnet_A、magnet_B，VNA1
    LAB-2：電磁鐵組 magnet_C、magnet_D，VNA2
方案用 LAB-1 的範本（magnet_A、magnet_B、VNA1），在 LAB-2 執行時就換成 magnet_C、magnet_D、VNA2：
同一台裡的第幾個電磁鐵組 → 對到另一台的第幾個；VNA 也一樣。
只換目前儀器清單裡沒有的名稱（客戶端電腦看得到全部儀器時不換）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..core.nodes import pins
from .catalog import Catalog
from .model import Scheme

MAGNET, VNA = "magnet", "vna"


def _measure_kind(driver: str) -> str:
    try:
        from ..core.registry import DRIVERS

        cls = DRIVERS.get(driver)
        return str(getattr(cls, "MEASURE_KIND", "") or "")
    except Exception:  # noqa: BLE001
        return "vna" if str(driver).startswith("rs.zn") else ""


def role_lists(instruments: Dict[str, Dict[str, Any]],
               lab_nodes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Dict[str, List[str]]]:
    """{量測電腦名稱（沒寫 node = ""）: {"magnet": [...], "vna": [...]}}，依 instruments.yaml 的順序。

    0.0.32：``lab_nodes``（settings.yaml，量測電腦對應）先決定每台的順序：
    [{node: LAB-1, magnet: [magnet_A, magnet_B], vna: [VNA1]}, {node: LAB-2, magnet: [magnet_C, magnet_D], vna: [VNA2]}]。
    這台的 instruments.yaml 沒有別台的儀器也能對應。"""
    out = _from_instruments(instruments)
    for entry in lab_nodes or []:
        if not isinstance(entry, dict) or not str(entry.get("node") or "").strip():
            continue
        node = str(entry["node"]).strip()
        key = next((k for k in out if k.lower() == node.lower()), node)
        cur = out.setdefault(key, {MAGNET: [], VNA: []})
        for role in (MAGNET, VNA):
            first = [str(x).strip() for x in (entry.get(role) or []) if str(x).strip()]
            if first:
                cur[role] = first + [n for n in cur.get(role, []) if n not in first]
    return out


def _from_instruments(instruments: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, List[str]]]:
    out: Dict[str, Dict[str, List[str]]] = {}
    for name, opts in (instruments or {}).items():
        opts = dict(opts or {})
        if str(name).startswith("_") or opts.get("enabled", True) is False:
            continue
        driver = str(opts.get("driver") or "")
        if driver == "virtual.interleaved_pair" or opts.get("group") == "magnet":
            role = MAGNET
        elif _measure_kind(driver) == "vna":
            role = VNA
        else:
            continue
        nodes = pins(opts)
        if not nodes and opts.get("sources"):           # 電磁鐵組沒寫 node：跟著第一台電源
            first = str(opts["sources"][0]).split(".")[0]
            nodes = pins((instruments or {}).get(first) or {})
        for n in nodes or [""]:
            out.setdefault(n, {MAGNET: [], VNA: []})[role].append(str(name))
    return out


def _slot(name: str, roles: Dict[str, Dict[str, List[str]]]) -> Optional[Tuple[str, int]]:
    for lists in roles.values():
        for role, names in lists.items():
            if name in names:
                return role, names.index(name)
    return None


def available(cat: Catalog) -> Dict[str, List[str]]:
    """目前儀器清單裡的電磁鐵組、VNA（依清單順序）。"""
    return {MAGNET: [t.ref for t in cat.targets if t.group == MAGNET],
            VNA: [m.ref for m in cat.measurers if (m.kind or "").lower() == VNA]}


def mapping(names: List[str], cat: Catalog, roles: Dict[str, Dict[str, List[str]]]) -> Dict[str, str]:
    """方案用到、但目前清單沒有的儀器 → 這台的對應儀器。"""
    have = available(cat)
    present = set(have[MAGNET]) | set(have[VNA]) | {t.ref for t in cat.targets} | {m.ref for m in cat.measurers}
    out: Dict[str, str] = {}
    for n in names:
        if not n or n in present or n in out:
            continue
        s = _slot(n, roles)
        if s is None:
            continue
        role, idx = s
        cand = have.get(role) or []
        if idx < len(cand):
            out[n] = cand[idx]
    return out


def remap_scheme(scheme: Scheme, cat: Catalog, roles: Dict[str, Dict[str, List[str]]]) -> Dict[str, str]:
    """把方案裡的儀器換成這台的（直接改 scheme）；回傳 {舊: 新}。"""
    used: List[str] = []
    for b in scheme.flat():
        if b.kind == "set" and b.target:
            used.append(b.target.split(".")[0] if "." in b.target else b.target)
        elif b.kind == "measure" and b.instrument:
            used.append(b.instrument)
    m = mapping(used, cat, roles)
    if not m:
        return {}
    for b in (scheme.graph.nodes.values() if scheme.graph is not None else scheme.flat()):
        if b.kind == "set" and b.target:
            head, _, rest = b.target.partition(".")
            if head in m:
                b.target = m[head] + ("." + rest if rest else "")
        elif b.kind == "measure" and b.instrument in m:
            b.instrument = m[b.instrument]
    scheme.sync_from_graph()
    return m
