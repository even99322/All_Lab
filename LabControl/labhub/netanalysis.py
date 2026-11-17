"""1.0.46：網路用量的「流量分析」（像華碩 Traffic Analyzer）：最近 24 小時每小時的用量、各設備用量、各設備前幾名 App。

    GET /api/netusage/analysis?site=main|<實驗室代號>&dev=<設備 key>
      → {site, has_apps, source, hourly: [{t, up, down}]×24（舊→新）, devices: [{key, name, mac, up, down, kind,
          split?, apps?, reg?}], apps: [{name, up, down}], total: {up, down}, dev_hourly?}

本實驗室（Netgear，沒有 App 分析）：每小時 = 路由器 Traffic Meter 的每分鐘紀錄加總；設備 = 流量監看的電腦、NAS、
    「監看不到」（路由器 − 已知）；電腦有內網 / 外網 / VPN。
其他實驗室（華碩）：代讀電腦每 10 分鐘讀 Traffic Analyzer（getWanTraffic / getAppTraffic，24 小時）帶回來；
    讀不到時退回代讀電腦讀的外網累計。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .server import Handler, HubHTTPError, route

HOURS = 24


def hourly(rc: Any, key: str, now: float, hours: int = HOURS) -> List[Dict[str, Any]]:
    """Recent 的每分鐘紀錄 → 最近 hours 小時（含這個小時），舊 → 新。"""
    cur = int(now // 3600) * 3600
    start = cur - (hours - 1) * 3600
    out = [{"t": start + i * 3600, "up": 0, "down": 0} for i in range(hours)]
    with rc.lock:
        s = dict(rc.data.get(key) or {})
    for m, (u, d) in s.items():
        i = (int(m) - start) // 3600
        if 0 <= i < hours:
            out[i]["up"] += int(u)
            out[i]["down"] += int(d)
    return out


def _clean_rows(rows: Any, n: int, name_len: int = 60) -> List[List[Any]]:
    out = []
    for r in (rows or [])[:n]:
        if isinstance(r, (list, tuple)) and len(r) >= 3:
            try:
                out.append([re.sub(r"[\x00-\x1f<>]", "", str(r[0]))[:name_len], max(0, int(r[1])), max(0, int(r[2]))])
            except (TypeError, ValueError):
                continue
    return out


def clean_ta(ta: Any, now: float) -> Optional[Dict[str, Any]]:
    """代讀電腦送來的 Traffic Analyzer → 只留需要的、限制大小。"""
    if not isinstance(ta, dict):
        return None
    hourly_rows = []
    for r in (ta.get("hourly") or [])[:HOURS]:
        try:
            hourly_rows.append([max(0, int(r[0])), max(0, int(r[1]))])
        except (TypeError, ValueError, IndexError):
            hourly_rows.append([0, 0])
    apps: Dict[str, List[List[Any]]] = {}
    for mac, rows in list((ta.get("apps") or {}).items())[:40]:
        m = str(mac).upper()[:17]
        if re.match(r"^[0-9A-F:]{17}$", m):
            apps[m] = _clean_rows(rows, 8)
    try:
        t = float(ta.get("t") or now)
    except (TypeError, ValueError):
        t = now
    return {"t": t, "hourly": hourly_rows, "clients": _clean_rows(ta.get("clients"), 200, 17),
            "apps_all": _clean_rows(ta.get("apps_all"), 60), "apps": apps, "error": str(ta.get("error") or "")[:200]}


def _site_view(st: Any, sid: str, now: float, dev: str) -> Dict[str, Any]:
    from .netsites import sites

    c = sites(st)[sid]
    ta = c.get("ta") or {}
    names = {m: (st.alias(f"mac:{m}", d.get("name", "")) or m) for m, d in (c.get("devices") or {}).items()}
    if ta.get("hourly"):
        # Traffic Analyzer：第 0 格 = 這個小時（讀的那一刻），往前推
        base = int(float(ta.get("t") or now) // 3600) * 3600
        rows = ta["hourly"]
        hourly_out = [{"t": base - i * 3600, "up": rows[i][0], "down": rows[i][1]} for i in range(len(rows))][::-1]
        devices = []
        for mac, tx, rx in ta.get("clients") or []:
            m = mac.upper()
            devices.append({"key": f"mac:{m}", "mac": m, "name": names.get(m) or st.alias(f"mac:{m}", m),
                            "up": tx, "down": rx, "kind": "device",
                            "apps": [{"name": a, "up": u, "down": d} for a, u, d in (ta.get("apps") or {}).get(m, [])[:5]]})
        apps = [{"name": a, "up": u, "down": d} for a, u, d in ta.get("apps_all") or []]
        src = "ta"
    else:
        hourly_out = hourly(st.recent, f"site:{sid}", now)
        devices, apps, src = [], [], "router"
        for did, pc in st.pcs.items():
            if pc.get("site") == sid:
                w = st.recent.window(f"pc:{did}", 86400, now=now)
                devices.append({"key": f"pc:{did}", "name": pc.get("label") or pc.get("host") or did,
                                "up": w["up"], "down": w["down"], "kind": "pc"})
    tot = {"up": sum(h["up"] for h in hourly_out), "down": sum(h["down"] for h in hourly_out)}
    devices.sort(key=lambda d: -(d["up"] + d["down"]))
    apps.sort(key=lambda a: -(a["up"] + a["down"]))
    out = {"site": sid, "name": c.get("name") or sid, "has_apps": src == "ta", "source": src, "ta_time": ta.get("t"),
           "ta_error": ta.get("error", ""), "hourly": hourly_out, "devices": devices, "apps": apps, "total": tot}
    from . import netreg

    netreg.annotate(st, sid, [d for d in devices if d.get("mac")])
    return out


def _main_view(st: Any, now: float, dev: str) -> Dict[str, Any]:
    rv = st.recent_view(86400)
    rk = "router" if any(s["key"] == "router" for s in rv["sources"]) else "nas"
    hourly_out = hourly(st.recent, rk, now)
    tot = {"up": sum(h["up"] for h in hourly_out), "down": sum(h["down"] for h in hourly_out)}
    devices = []
    for d in rv.get("devices") or []:
        if d.get("up") is None or not (d.get("up") or d.get("down")):
            continue
        devices.append({k: d.get(k) for k in ("key", "name", "mac", "ip", "up", "down", "kind", "split", "source", "reg")})
    unk = next((s for s in rv["sources"] if s["key"] == "unknown"), None)
    if unk and (unk["up"] or unk["down"]):
        devices.append({"key": "unknown", "name": "監看不到的設備", "up": unk["up"], "down": unk["down"], "kind": "unknown"})
    devices.sort(key=lambda d: -((d.get("up") or 0) + (d.get("down") or 0)))
    out = {"site": "main", "name": st.extra.get("main_name") or "本實驗室", "has_apps": False, "source": rk,
           "hourly": hourly_out, "devices": devices, "apps": [], "total": tot}
    if dev:
        d = next((x for x in devices if x.get("key") == dev and x.get("source")), None)
        if d:
            out["dev_hourly"] = {"key": dev, "hourly": hourly(st.recent, d["source"], now)}
    return out


def analysis(st: Any, site: str, dev: str = "") -> Dict[str, Any]:
    from .netsites import site_list, sites

    now = st.now()
    if site and site != "main":
        if site not in sites(st):
            raise HubHTTPError(404, "沒有這間實驗室")
        out = _site_view(st, site, now, dev)
    else:
        out = _main_view(st, now, dev)
    out["sites"] = site_list(st)
    out["now"] = now
    return out


@route("GET", r"/api/netusage/analysis", auth="any")
def _analysis(h: Handler) -> None:
    from .netusage import store

    q = h._query()
    h._json(analysis(store(h.hub), str(q.get("site") or "main"), str(q.get("dev") or "")))
