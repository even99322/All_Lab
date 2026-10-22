"""1.0.20：解析 FlexNet `lmutil lmstat -a -c <伺服器>` 的輸出（Ansys 授權伺服器）。

模擬工具在實驗室電腦上執行 lmstat，把原始輸出送到 Hub，由這裡解析——之後要改解析方式只要更新 Hub。

    Users of hfss_solve:  (Total of 5 licenses issued;  Total of 1 license in use)
        user LAB-PC-A LAB-PC-A (v2025.0630) (10.0.0.11/1055 3601), start Thu 10/22 15:45
        user DESKTOP-A DESKTOP-A (v2025.0630) (10.0.0.11/1055 3602), start Wed 10/21 18:02, 4 licenses
"""
from __future__ import annotations

import datetime as dt
import re
import time
from typing import Any, Dict, List, Optional

# 常見的 Ansys Electronics 授權名稱 → 看得懂的名字（不認得的直接顯示原名）
LABELS = {
    "electronics_desktop": "Electronics Desktop 主程式",
    "hfss_solve": "HFSS 求解器",
    "hfss_desktop": "HFSS Desktop",
    "hfss_gui": "HFSS 介面",
    "elec_solve_level1": "Electronics 求解 Level 1",
    "elec_solve_level2": "Electronics 求解 Level 2",
    "elec_solve_level3": "Electronics 求解 Level 3",
    "elec_solve_hfss": "HFSS 求解（Electronics）",
    "electronics3d_gui": "3D 介面 (HFSS / Maxwell 3D)",
    "electronics2d_gui": "2D 介面 (Maxwell 2D / Q2D)",
    "electronicsckt_gui": "電路介面 (Circuit)",
    "electronics_ckt_gui": "電路介面 (Circuit)",
    "anshpc": "HPC 核心",
    "anshpc_pack": "HPC Pack",
    "ansys": "Mechanical",
}
ELEC_PREFIX = ("elec", "electronics", "hfss", "anshpc", "maxwell", "q3d", "siwave", "icepak", "nexxim", "designer",
               "ansoft", "aedt", "rf_", "ensemble")
SOLVE = ("hfss_solve", "elec_solve_hfss")
HPC = ("anshpc",)
DESKTOP = ("electronics_desktop", "hfss_desktop")

_USERS = re.compile(r"^Users of ([^:]+):\s*\((.*)\)\s*$")
_ISSUED = re.compile(r"Total of (\d+) licenses? issued")
_INUSE = re.compile(r"Total of (\d+) licenses? in use")
_USER = re.compile(r"^\s+(\S+)\s+(\S+)\s+(\S+)\s.*?start\s+\w+\s+(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})"
                   r"(?:,\s*(\d+)\s+licenses?)?", re.I)
_SERVER = re.compile(r"license server (UP|DOWN)(?:\s*\(([^)]*)\))?\s*(v[\d.]+)?", re.I)


def label(name: str) -> str:
    return LABELS.get(name, name)


def is_electronics(name: str) -> bool:
    return name.lower().startswith(ELEC_PREFIX)


def _start(mon: int, day: int, hh: int, mm: int, now: float) -> Optional[float]:
    year = dt.datetime.fromtimestamp(now).year
    try:
        t = dt.datetime(year, mon, day, hh, mm).timestamp()
    except ValueError:
        return None
    if t > now + 86400:                            # 跨年：12/31 開的、1/1 看
        t = dt.datetime(year - 1, mon, day, hh, mm).timestamp()
    return t


def parse(text: str, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    features: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    status, version, role = "", "", ""
    for line in (text or "").splitlines():
        m = _SERVER.search(line)
        if m and not status:
            status, role, version = m.group(1).upper(), (m.group(2) or ""), (m.group(3) or "")
            continue
        m = _USERS.match(line.strip())
        if m:
            info = m.group(2)
            iss, use = _ISSUED.search(info), _INUSE.search(info)
            cur = {"name": m.group(1).strip(), "issued": int(iss.group(1)) if iss else 0,
                   "in_use": int(use.group(1)) if use else 0, "users": [], "note": "" if iss else info.strip()}
            cur["label"] = label(cur["name"])
            features.append(cur)
            continue
        if cur is None:
            continue
        m = _USER.match(line)
        if m and line.startswith((" ", "\t")):
            user, host, display = m.group(1), m.group(2), m.group(3)
            if user.startswith('"') or user.lower() in ("floating", "vendor_string:"):
                continue
            cur["users"].append({"user": user, "host": host, "display": display,
                                 "start": _start(int(m.group(4)), int(m.group(5)), int(m.group(6)), int(m.group(7)), now),
                                 "count": int(m.group(8) or 1)})
    error = ""
    if not features:
        low = (text or "").lower()
        if "cannot connect" in low or "cannot find license" in low or "down" in status.lower():
            error = "連不到授權伺服器"
        elif not text:
            error = "lmstat 沒有輸出"
        else:
            error = "看不懂 lmstat 的輸出"
    return {"status": status or ("DOWN" if error else ""), "role": role, "version": version, "features": features,
            "error": error, "time": now}


def devices(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    """依（電腦, 使用者）整理：用了哪些授權、HFSS 求解幾個、HPC 幾核、最早從什麼時候開始。"""
    out: Dict[tuple, Dict[str, Any]] = {}
    for f in snapshot.get("features") or []:
        for u in f["users"]:
            k = (u["host"].lower(), u["user"].lower())
            d = out.setdefault(k, {"host": u["host"], "user": u["user"], "features": [], "solve": 0, "hpc": 0,
                                   "desktop": 0, "since": None, "electronics": False})
            d["features"].append({"name": f["name"], "label": f["label"], "count": u["count"]})
            if f["name"] in SOLVE:
                d["solve"] += u["count"]
            if f["name"] in HPC:
                d["hpc"] += u["count"]
            if f["name"] in DESKTOP:
                d["desktop"] += u["count"]
            d["electronics"] = d["electronics"] or is_electronics(f["name"])
            if u["start"] and (d["since"] is None or u["start"] < d["since"]):
                d["since"] = u["start"]
    return sorted(out.values(), key=lambda d: (d["since"] or 0))


def summary(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    """上方的總覽：Electronics Desktop、HFSS 求解、HPC（有發的才列），再加其他有人在用的 Electronics 授權。"""
    feats = {f["name"]: f for f in snapshot.get("features") or []}
    out = []
    for names, title, unit in ((DESKTOP, "Electronics Desktop", ""), (SOLVE, "HFSS 求解", ""), (HPC, "HPC", " 核")):
        fs = [feats[n] for n in names if n in feats]
        if fs:
            out.append({"title": title, "in_use": sum(f["in_use"] for f in fs), "issued": sum(f["issued"] for f in fs),
                        "unit": unit})
    return out
