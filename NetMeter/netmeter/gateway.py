"""1.0.6：預設閘道（路由器）的 IP 與 MAC —— Hub 用它分辨電腦在哪一間實驗室（兩間的內網可能都是 192.168.50.x）。

Windows：route print -4 0.0.0.0 ＋ arp -a <閘道>；macOS：route -n get default ＋ arp -n <閘道>；Linux：/proc/net/route、/proc/net/arp。
結果快取 5 分鐘。只用標準函式庫。
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_CACHE: Tuple[float, Dict[str, str]] = (0.0, {})
EVERY_S = 300.0
_MAC = re.compile(r"([0-9A-Fa-f]{1,2}[:-]){5}[0-9A-Fa-f]{1,2}")


def norm_mac(m: str) -> str:
    parts = re.split(r"[:-]", m.strip())
    if len(parts) != 6:
        return ""
    try:
        return ":".join(f"{int(p, 16):02X}" for p in parts)
    except ValueError:
        return ""


def _run(args: List[str]) -> str:
    kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10, errors="replace", **kw).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_route_print(text: str, prefer: List[str]) -> str:
    """Windows `route print -4 0.0.0.0`：0.0.0.0 0.0.0.0 <閘道> <介面 IP> <metric>；優先實驗室網卡那一條，再取 metric 最小。"""
    best: Optional[Tuple[int, int, str]] = None
    for line in text.splitlines():
        f = line.split()
        if len(f) >= 5 and f[0] == "0.0.0.0" and f[1] == "0.0.0.0" and re.match(r"^\d+\.\d+\.\d+\.\d+$", f[2]):
            try:
                metric = int(f[4])
            except ValueError:
                metric = 9999
            key = (0 if f[3] in prefer else 1, metric, f[2])
            if best is None or key < best:
                best = key
    if best and prefer and best[0] == 1:
        return ""                                         # 1.0.12：實驗室那張網卡沒有預設閘道 → 不猜別張的
    return best[2] if best else ""


def parse_arp(text: str, ip: str) -> str:
    for line in text.splitlines():
        if re.search(rf"(^|[\s(]){re.escape(ip)}([\s)]|$)", line):
            m = _MAC.search(line)
            if m:
                return norm_mac(m.group(0))
    return ""


def _linux() -> Dict[str, str]:
    gw = ""
    try:
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            f = line.split()
            if len(f) > 2 and f[1] == "00000000" and f[2] != "00000000":
                b = bytes.fromhex(f[2])[::-1]
                gw = ".".join(str(x) for x in b)
                break
    except OSError:
        return {}
    mac = ""
    try:
        for line in Path("/proc/net/arp").read_text().splitlines()[1:]:
            f = line.split()
            if f and f[0] == gw and len(f) > 3:
                mac = norm_mac(f[3])
    except OSError:
        pass
    return {"ip": gw, "mac": mac} if gw else {}


def detect(prefer: Optional[List[str]] = None) -> Dict[str, str]:
    """{"ip": 閘道 IP, "mac": "AA:BB:…"}；讀不到 → {}。"""
    global _CACHE
    now = time.time()
    prefer = sorted(prefer or [])
    if now - _CACHE[0] < EVERY_S and _CACHE[1] and _CACHE[1].get("_prefer") == ",".join(prefer):
        return {k: v for k, v in _CACHE[1].items() if not k.startswith("_")}
    out: Dict[str, str] = {}
    if sys.platform == "win32":
        gw = parse_route_print(_run(["route", "print", "-4", "0.0.0.0"]), prefer)
        if gw:
            out = {"ip": gw, "mac": parse_arp(_run(["arp", "-a", gw]), gw)}
    elif sys.platform == "darwin":
        m = re.search(r"gateway:\s*(\d+\.\d+\.\d+\.\d+)", _run(["/sbin/route", "-n", "get", "default"]))
        if m:
            gw = m.group(1)
            out = {"ip": gw, "mac": parse_arp(_run(["/usr/sbin/arp", "-n", gw]), gw)}
    else:
        out = _linux()
    _CACHE = (now, dict(out, _prefer=",".join(prefer)))
    return dict(out)
