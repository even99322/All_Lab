"""1.0.27：儀器（含電磁鐵組）改名、隱藏——儀器伺服器與量測節點的遠端指令共用。

改名會一起改：
  * instruments.yaml 的名稱（保留順序與註解，原檔先備份）；
  * 其他儀器 sources 裡的參照（電磁鐵組 magnet_A: sources [DC1, DC2] → [DC9, DC2]）；
  * settings.yaml 的「量測電腦對應」（lab_nodes 的 magnet / vna 清單）；
  * Station：用到的儀器先移出，寫檔後重新載入（量測使用中、或已連線時拒絕）；
  * 1.0.33：量測方案、範本、專案範本、電源群組 / 記憶、即時數據的排法、VNA 接線（core/rename_refs.py），
    目前開著的方案（工作台）。

隱藏（hidden: true）只影響儀器伺服器的顯示與「全部連線」，儀器照常載入、可以連線。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import labfile
from .errors import ConfigError

NAME_RE = re.compile(r"^[^\W\d][\w\-]*$", re.UNICODE)

log = logging.getLogger(__name__)


def check_name(new: str, existing) -> str:
    new = str(new or "").strip()
    if not new:
        raise ConfigError("名稱不可空白")
    if not NAME_RE.match(new):
        raise ConfigError(f"名稱「{new}」不合法：只能用字母、數字、底線、減號，且不能以數字開頭（不可含 . 或空白）")
    if any(str(x).lower() == new.lower() for x in existing):
        raise ConfigError(f"已經有叫 {new} 的儀器")
    return new


def _users(conf: Dict[str, Dict[str, Any]], name: str) -> List[str]:
    """用到 name 的虛擬儀器（電磁鐵組）。"""
    out = []
    for n, o in conf.items():
        srcs = [str(x).split(".")[0] for x in ((o or {}).get("sources") or [])]
        if name in srcs:
            out.append(n)
    return out


def rename_lab_nodes(old: str, new: str) -> bool:
    """settings.yaml lab_nodes 裡的 old → new（電磁鐵組 / VNA 順序）。回傳是否有改。"""
    try:
        from ..settings import setting, settings
    except Exception:  # noqa: BLE001
        return False
    nodes = setting("lab_nodes", None)
    if not isinstance(nodes, list):
        return False
    changed = False
    out = []
    for e in nodes:
        if not isinstance(e, dict):
            out.append(e)
            continue
        e = dict(e)
        for k in ("magnet", "vna"):
            lst = [new if str(x) == old else x for x in (e.get(k) or [])]
            if lst != list(e.get(k) or []):
                changed = True
            if k in e:
                e[k] = lst
        out.append(e)
    if changed:
        st = settings()
        st.set("lab_nodes", out)
        st.save()
    return changed


def rename_instrument(station, path: Optional[Path], old: str, new: str) -> Dict[str, Any]:
    """在這台改名（設定檔 + Station + 量測電腦對應）。回傳 {"touched": [...], "reloaded": [...]}。"""
    conf = dict(labfile.configured(path)) if path is not None and Path(path).exists() else {}
    in_file = old in conf
    loaded = old in station.instruments
    if not in_file and not loaded:
        raise ConfigError(f"找不到儀器 {old}")
    existing = set(conf) | set(station.instruments) | set(getattr(station, "foreign", {}) or {})
    existing.discard(old)
    new = check_name(new, existing)
    if new == old:
        return {"touched": [], "reloaded": [], "name": new}
    users = _users(conf, old) if in_file else [n for n, i in station.instruments.items()
                                               if old in {str(x).split(".")[0] for x in (i.options.get("sources") or [])}]
    affected = [n for n in [*users, old] if n in station.instruments]
    for n in affected:
        if station.lease_holder(n):
            raise ConfigError(f"{n} 量測使用中，結束後再改名")
    for n in affected:
        if station.instruments[n].connected:
            raise ConfigError(f"{n} 已連線：請先中斷連線再改名")
    saved: Dict[str, Dict[str, Any]] = {}
    for n in affected:                                    # 虛擬儀器先移出，再移出電源
        inst = station.instruments[n]
        saved[n] = {"driver": getattr(inst, "config_driver", inst.driver_name), **dict(inst.options)}
    for n in sorted(affected, key=lambda x: x == old):
        station.remove(n)
    touched: List[str] = []
    try:
        if in_file:
            touched = labfile.rename_instrument(path, old, new)
    except Exception:
        _readd(station, saved, {})
        raise
    rename_lab_nodes(old, new)
    # 重新載入：用設定檔（有的話），否則用原本的選項
    if in_file:
        conf2 = dict(labfile.configured(path))
        opts = {n2: conf2[n2] for n2 in [new, *users] if n2 in conf2}
    else:
        o = dict(saved.get(old) or {})
        opts = {new: o}
        for u in users:
            uo = dict(saved[u])
            uo["sources"] = [labfile._rename_ref(x, old, new) for x in (uo.get("sources") or [])]
            opts[u] = uo
    reloaded = _readd(station, opts, getattr(station, "foreign", {}) or {})
    updated: List[str] = []
    try:                                                  # 1.0.33：方案、範本、電源群組…用到舊名稱的地方一起改
        from .rename_refs import propagate

        updated = propagate(old, new)
    except Exception as e:  # noqa: BLE001
        log.warning("儀器改名後更新其他引用失敗：%s", e)
    try:
        station.bus.publish("station.changed", action="rename", name=new, old=old)
    except Exception:  # noqa: BLE001
        pass
    return {"touched": touched, "reloaded": reloaded, "name": new, "updated": updated}


def _readd(station, opts: Dict[str, Dict[str, Any]], foreign: Dict[str, str]) -> List[str]:
    out = []
    order = sorted(opts, key=lambda n: bool((opts[n] or {}).get("sources")))   # 電源先、電磁鐵組後
    for n in order:
        o = dict(opts[n] or {})
        if n in station.instruments or n in foreign or o.get("enabled", True) is False:
            continue
        o.pop("enabled", None)
        d = o.pop("driver", None)
        if not d:
            continue
        station.add(n, d, **o)
        out.append(n)
    return out


def set_hidden(station, path: Optional[Path], name: str, hidden: bool) -> bool:
    """設定 hidden（寫 hidden: true / false，讓跨電腦同步知道是刻意改的）。回傳是否在設定檔裡。"""
    in_file = path is not None and Path(path).exists() and any(n == name for n, _ in labfile.configured(path))
    if in_file:
        labfile.update_instrument(path, name, {"hidden": bool(hidden)})
    inst = station.instruments.get(name)
    if inst is not None:
        inst.options["hidden"] = bool(hidden)
    if not in_file and inst is None:
        raise ConfigError(f"找不到儀器 {name}")
    try:
        station.bus.publish("station.changed", action="hidden", name=name)
    except Exception:  # noqa: BLE001
        pass
    return in_file
