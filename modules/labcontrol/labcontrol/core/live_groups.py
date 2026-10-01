"""即時監控的電源群組設定（合併 / 拆開 / 記憶 M1–M3），存在 LAB/live_groups.json。

群組 = {"name", "members": [ref…], "memory": [M1, M2, M3]}；第一個成員是 master，其他成員跟隨它。
不同電腦（本機、各量測節點）的群組分開存（key：local:<電腦>、node:<節點>）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

N_MEMORY = 3


def _norm(g: Dict[str, Any]) -> Dict[str, Any]:
    mem = list(g.get("memory") or [])[:N_MEMORY]
    mem += [None] * (N_MEMORY - len(mem))
    return {"name": str(g.get("name") or ""), "members": [str(m) for m in g.get("members") or []], "memory": mem}


def reconcile(saved: Optional[List[Dict[str, Any]]], sources: List[Dict[str, Any]],
              defaults: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把存的群組與目前可用的電源對齊：

    * 第一次（沒有存過）→ 預設群組（instruments.yaml 的虛擬雙電源）＋ 其他電源各自一組；
    * 存過 → 保留順序與記憶；新出現的電源各自一組。成員暫時不在（未連線）的群組保留，不顯示而已。
    """
    labels = {s["ref"]: s.get("label") or s["ref"].split(".")[0] for s in sources}
    avail = list(labels)
    if saved is None:
        groups = [_norm({"name": d["name"], "members": [m for m in d["members"]]}) for d in defaults]
    else:
        groups = [_norm(g) for g in saved]
    used = {m for g in groups for m in g["members"]}
    for ref in avail:
        if ref not in used:
            groups.append(_norm({"name": labels[ref], "members": [ref]}))
    return [g for g in groups if g["members"]]


def visible(groups: List[Dict[str, Any]], available: List[str]) -> List[Dict[str, Any]]:
    """只含已連線成員的群組（成員順序不變；master 不在時第一個在的成員當 master）。"""
    av = set(available)
    out = []
    for i, g in enumerate(groups):
        mem = [m for m in g["members"] if m in av]
        if mem:
            out.append(dict(g, members=mem, index=i))
    return out


def find(groups: List[Dict[str, Any]], ref: str) -> Optional[int]:
    for i, g in enumerate(groups):
        if ref in g["members"]:
            return i
    return None


def merge(groups: List[Dict[str, Any]], ref: str, target: int) -> List[Dict[str, Any]]:
    """把 ref 移到 groups[target]（成為跟隨者）；原群組空了就刪掉。"""
    groups = [dict(g, members=list(g["members"]), memory=list(g["memory"])) for g in groups]
    src = find(groups, ref)
    if src == target:
        return groups
    tgt = groups[target]
    if src is not None:
        groups[src]["members"].remove(ref)
    tgt["members"].append(ref)
    return [g for g in groups if g["members"]]


def split(groups: List[Dict[str, Any]], ref: str, name: str = "", at: Optional[int] = None) -> List[Dict[str, Any]]:
    """把 ref 從群組拆出來，自己一組（插在 at 位置，預設放在原群組後面）。"""
    groups = [dict(g, members=list(g["members"]), memory=list(g["memory"])) for g in groups]
    src = find(groups, ref)
    if src is not None:
        if len(groups[src]["members"]) == 1:
            return groups
        groups[src]["members"].remove(ref)
    new = _norm({"name": name or ref.split(".")[0], "members": [ref]})
    pos = at if at is not None else ((src + 1) if src is not None else len(groups))
    groups.insert(max(0, min(pos, len(groups))), new)
    return [g for g in groups if g["members"]]


def merge_target(levels: Dict[str, float], members: List[str], mode: str) -> float:
    """合併後的目標值：override → master（第一個成員）的值；average → 平均。"""
    vals = [float(levels[m]) for m in members if levels.get(m) is not None]
    if not vals:
        raise ValueError("讀不到成員的目前值")
    if mode == "average":
        return sum(vals) / len(vals)
    return float(levels[members[0]])


def differs(levels: Dict[str, float], members: List[str], tol: float) -> bool:
    vals = [float(levels[m]) for m in members if levels.get(m) is not None]
    return bool(vals) and (max(vals) - min(vals)) > tol


class GroupStore:
    """LAB/live_groups.json：{key: [群組…]}。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _all(self) -> Dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return {}

    def load(self, key: str) -> Optional[List[Dict[str, Any]]]:
        v = self._all().get(key)
        return [_norm(g) for g in v] if isinstance(v, list) else None

    def save(self, key: str, groups: List[Dict[str, Any]]) -> None:
        data = self._all()
        data[key] = [_norm(g) for g in groups]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
