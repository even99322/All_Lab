"""跟 Hub 溝通：回報用量、查新版（只用標準函式庫）。"""
from __future__ import annotations

import getpass
import json
import platform
import socket
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from . import __version__

_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 區網的 Hub：不走系統 proxy


def _req(url: str, body: Optional[Dict[str, Any]] = None, timeout: float = 8.0) -> Any:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json", "User-Agent": f"QELNetMeter/{__version__}"})
    with _NO_PROXY.open(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw.decode("utf-8") or "{}")


def os_name() -> str:
    s = platform.system()
    if s == "Windows":
        rel = platform.release()
        try:
            if int(platform.version().split(".")[-1]) >= 22000:
                rel = "11"
        except (ValueError, IndexError):
            pass
        return f"Windows {rel}"
    if s == "Darwin":
        return f"macOS {platform.mac_ver()[0]}"
    return f"{s} {platform.release()}"


def device_info() -> Dict[str, str]:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = ""
    return {"host": socket.gethostname(), "user": user, "os": os_name(), "version": __version__}


def report(hubs: List[str], payload: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """依序送到第一個連得到的 Hub → (Hub 位址, 回應)；都失敗丟最後一個錯誤。"""
    err: Optional[Exception] = None
    for h in hubs:
        try:
            return h, _req(h + "/api/netusage/report", payload)
        except Exception as e:  # noqa: BLE001
            err = e
    raise ConnectionError(f"連不到 Hub（{'、'.join(hubs)}）：{err}")


def latest(hub: str, plat: str) -> Optional[Dict[str, Any]]:
    """Hub 上最新的流量監看安裝檔（plat = windows / macos）→ {version, url, name, sha256}；沒有 → None。"""
    info = _req(hub + "/get/info")
    for a in info.get("apps") or []:
        if a.get("id") != "netmeter":
            continue
        best = None
        for f in a.get("downloads") or []:
            if f.get("platform") != plat:
                continue
            if best is None or vkey(f.get("version", "0")) > vkey(best.get("version", "0")):
                best = f
        if best:
            return {"version": best.get("version"), "url": hub + best.get("url", ""), "name": best.get("name", ""),
                    "sha256": best.get("sha256", ""), "arch": best.get("arch", "")}
    return None


def vkey(v: str) -> tuple:
    out = []
    for p in str(v or "0").split("."):
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return tuple(out)
