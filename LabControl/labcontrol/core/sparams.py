"""VNA 的 S 參數名稱（依 port 數）。

    sparam_names(4)          → S11 S12 S13 S14 S21 … S44（Sij：i = 接收 port，j = 激發 port）
    ports_of("S43")          → (4, 3)
    ports_from_idn("Rohde-Schwarz,ZNA26-4Port,…") → 4
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

#: 舊版預設的四個（偵測不到 port 數時）
DEFAULT_ORDER = ["S21", "S11", "S12", "S22"]


def _p(i: int, n: int) -> str:
    return f"{i:02d}" if n >= 10 else str(i)


def sparam_names(n_ports: int) -> List[str]:
    """n port 的全部 S 參數，矩陣順序（一列一個接收 port）。"""
    n = max(1, int(n_ports))
    return [f"S{_p(i, n)}{_p(j, n)}" for i in range(1, n + 1) for j in range(1, n + 1)]


def ports_of(param: str) -> Optional[Tuple[int, int]]:
    m = re.fullmatch(r"S(\d)(\d)|S(\d\d)(\d\d)", str(param or "").strip().upper())
    if not m:
        return None
    a, b = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    return int(a), int(b)


def ports_from_idn(idn: str) -> Optional[int]:
    """*IDN? 型號裡的 port 數（R&S：ZNA26-4Port、ZNB20-2Port）。"""
    m = re.search(r"(\d+)\s*-?\s*Port", str(idn or ""), re.I)
    return int(m.group(1)) if m else None


def ports_from_params(params: Iterable[str]) -> int:
    best = 0
    for p in params:
        pp = ports_of(p)
        if pp:
            best = max(best, *pp)
    return best


def ordered(params: Iterable[str], existing: Iterable[str] = ()) -> List[str]:
    """量測清單的順序：S21 優先（舊方案預設），再來儀器上已經有 trace 的，其餘照矩陣順序。"""
    params = list(params)
    have = {e.upper() for e in existing}
    first = [p for p in params if p == "S21"]
    mid = [p for p in params if p.upper() in have and p not in first]
    rest = [p for p in params if p not in first and p not in mid]
    return first + mid + rest


def detection(n_ports: int, catalog: Dict[str, List[str]], source: str) -> Dict[str, object]:
    """detect_ports() 的回傳格式。"""
    names = sparam_names(n_ports)
    return {"ports": int(n_ports), "sparams": names, "traces": {k: list(v) for k, v in catalog.items()},
            "source": source}
