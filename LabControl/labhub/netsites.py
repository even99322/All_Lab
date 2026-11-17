"""1.0.43：網路用量的延伸——裝置改名、路由器封鎖、另一間實驗室（華碩路由器）、提醒沒裝流量監看的電腦。

    PUT  /api/netusage/alias           {key, name}：key = pc:<裝置 id>（電腦）、mac:<MAC>（路由器上的裝置）、ts:<id>（VPN 裝置）
    POST /api/netusage/block           {mac, block: true/false, enable?: true, self?: true}（站長）
                                       用路由器（Netgear）的「存取控制」封鎖 / 解除；NAS 不能封鎖；封鎖自己正在用的裝置要再確認
    GET  /api/netusage/sites           另一間實驗室的設定（站長看得到帳號，密碼不回傳）
    PUT  /api/netusage/sites           {sites: [{id, name, subnets, router: {kind: asus, host, user, password?, port, ssl},
                                        collector}]}（站長）
    GET  /api/netusage/selfcheck       這台電腦（用電腦開 Hub 的人）有沒有流量監看在回報 → 網頁上方提醒；
                                       沒有 → 同一個帳號的推播裝置每天最多提醒一次（通知類別「流量監看提醒」）

所在實驗室：流量監看 1.0.6 起回報預設閘道（路由器）的 IP 與 MAC（gw）。
    從實驗室內網直接回報的（來源 IP 在本實驗室網段）→ 本實驗室，順便記下本實驗室路由器的 MAC；
    閘道 MAC 認得的 → 那一間；是某間實驗室指定的「代讀電腦」→ 那一間（記下它的閘道 MAC）。
    華碩路由器出廠的內網也是 192.168.50.x，所以只看網段分不出來。
另一間實驗室：Hub 連不到那邊的路由器，由那邊一台裝了流量監看、經 VPN 回報的電腦（代讀電腦）代讀：
    Hub 在回報的回應裡給它路由器位址與帳密（只給這一台），它每分鐘讀路由器的外網累計（rx / tx）與連線裝置，
    下一次回報帶上來（site_router）；Hub 算差值記成那間實驗室的「整個網段」。
只用標準函式庫。
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any, Dict, List, Optional

from .server import Handler, HubHTTPError, route

_MAC = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")
_SID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")
SITE_DEV_KEEP_S = 30 * 86400.0
PUSH_EVERY_S = 86400.0
SEEN_S = 1800.0
MAX_DELTA = 50 * 1024 ** 3


def _s(v: Any, n: int = 120) -> str:
    return str(v if v is not None else "")[:n]


def norm_mac(m: Any) -> str:
    m = re.sub(r"[^0-9A-Fa-f]", "", str(m or "")).upper()
    return ":".join(m[i:i + 2] for i in range(0, 12, 2)) if len(m) == 12 else ""


def _nets(xs: List[str]) -> List[Any]:
    out = []
    for x in xs:
        try:
            out.append(ipaddress.ip_network(str(x).strip(), strict=False))
        except ValueError:
            pass
    return out


def _in(ip: str, nets: List[Any]) -> bool:
    try:
        a = ipaddress.ip_address(str(ip).split("%", 1)[0])
    except ValueError:
        return False
    return any(a.version == n.version and a in n for n in nets)


def _conn(x: Dict[str, Any]) -> str:
    t = str(x.get("type") or x.get("conn") or "").lower()
    if not t:
        return ""
    if "wired" in t or t in ("0", "lan"):
        return "有線"
    if "2.4" in t or t == "1":
        return "無線 2.4G"
    if "6" in t and "g" in t:
        return "無線 6G"
    if "5" in t or t in ("2", "3"):
        return "無線 5G"
    return str(x.get("type") or "")


# ---------------------------------------------------------------- 另一間實驗室
def sites(st: Any) -> Dict[str, Dict[str, Any]]:
    return st.extra["sites"]


def site_list(st: Any) -> List[Dict[str, str]]:
    return [{"id": "main", "name": st.extra.get("main_name") or "本實驗室"}] + \
        [{"id": k, "name": v.get("name") or k} for k, v in sites(st).items()]


def public_sites(st: Any) -> List[Dict[str, Any]]:
    out = []
    for sid, c in sites(st).items():
        r = dict(c.get("router") or {})
        r["has_password"] = bool(st.secret_get(f"site:{sid}:password"))
        out.append({"id": sid, "name": c.get("name") or sid, "subnets": c.get("subnets") or [], "router": r,
                    "collector": c.get("collector") or "", "status": c.get("status") or {},
                    "gateway": next((m for m, s in st.extra["gateways"].items() if s == sid), "")})
    return out


def set_sites(st: Any, body: Dict[str, Any]) -> None:
    items = body.get("sites")
    if not isinstance(items, list) or len(items) > 8:
        raise HubHTTPError(400, "sites 要是清單（最多 8 間）")
    with st.lock:
        new: Dict[str, Dict[str, Any]] = {}
        for x in items:
            if not isinstance(x, dict):
                continue
            sid = _s(x.get("id"), 24).strip().lower()
            if not _SID.match(sid) or sid == "main":
                raise HubHTTPError(400, f"實驗室代號「{sid}」只能用英文小寫、數字、- _（不能是 main）")
            old = sites(st).get(sid) or {}
            r = x.get("router") if isinstance(x.get("router"), dict) else {}
            subnets = [str(n) for n in _nets(x.get("subnets") or [])][:6]
            try:
                port = max(0, min(65535, int(r.get("port") or 0)))
            except (TypeError, ValueError):
                port = 0
            router = {"kind": "asus", "host": _s(r.get("host"), 120).strip(), "user": _s(r.get("user") or "admin", 60),
                      "port": port, "ssl": bool(r.get("ssl"))}
            if router["host"] and not re.match(r"^[A-Za-z0-9.\-]+$", router["host"]):
                raise HubHTTPError(400, "路由器位址格式不對")
            if r.get("password"):
                st.secret_set(f"site:{sid}:password", _s(r["password"], 200))
            new[sid] = dict(old, name=_s(x.get("name") or sid, 40), subnets=subnets, router=router,
                            collector=_s(x.get("collector"), 40))
        for sid in set(sites(st)) - set(new):
            st.secret_set(f"site:{sid}:password", "")
            for m in [m for m, s in st.extra["gateways"].items() if s == sid]:
                del st.extra["gateways"][m]
        st.extra["sites"] = new
        if body.get("main_name"):
            st.extra["main_name"] = _s(body["main_name"], 40)
        st._dirty = True
    st.save(force=True)


def _main_nets(st: Any) -> List[str]:
    try:
        return list(st._lan_list())
    except Exception:  # noqa: BLE001
        return ["192.168.50.0/24"]


def on_report(st: Any, did: str, b: Dict[str, Any], ip: str) -> Dict[str, Any]:
    """流量監看回報：判斷在哪一間實驗室；代讀電腦 → 收路由器的數字、給它路由器帳密。回傳要加進回應的東西。"""
    gw = b.get("gw") if isinstance(b.get("gw"), dict) else {}
    gmac, gip = norm_mac(gw.get("mac")), _s(gw.get("ip"), 60)
    main_nets = _nets(_main_nets(st))
    out: Dict[str, Any] = {}
    with st.lock:
        pc = st.pcs.get(did)
        if pc is None:
            return out
        if gmac or gip:
            pc["gw"] = {"ip": gip, "mac": gmac}
        site = ""
        if _in(ip, main_nets):                                   # 從本實驗室內網直接回報
            site = "main"
            if gmac and st.extra["gateways"].get(gmac) not in ("main",):
                if gmac not in st.extra["gateways"]:
                    st.extra["gateways"][gmac] = "main"
        elif gmac and gmac in st.extra["gateways"]:
            site = st.extra["gateways"][gmac]
        if not site and gip:                                     # 1.0.44：閘道在某間實驗室自己的網段（跟本實驗室不重疊）
            for sid, c in sites(st).items():
                sn = _nets(c.get("subnets") or [])
                if _in(gip, sn) and not any(a.version == m.version and a.overlaps(m) for a in sn for m in main_nets):
                    site = sid
                    if gmac and gmac not in st.extra["gateways"]:
                        st.extra["gateways"][gmac] = sid
                    break
        for sid, c in sites(st).items():
            if c.get("collector") and c.get("collector") == did:
                if not site or site == sid:
                    site = sid
                    if gmac and gmac not in st.extra["gateways"]:
                        st.extra["gateways"][gmac] = sid
                pw = st.secret_get(f"site:{sid}:password")
                r = c.get("router") or {}
                if r.get("host") and pw and site == sid:
                    out["collect"] = {"site": sid, "kind": r.get("kind", "asus"), "host": r["host"],
                                      "user": r.get("user") or "admin", "password": pw, "port": r.get("port") or 0,
                                      "ssl": bool(r.get("ssl"))}
        if site or b.get("on_net"):
            pc["site"] = site if site in ("main",) or site in sites(st) else ""
        sr = b.get("site_router") if isinstance(b.get("site_router"), dict) else None
        if sr and sr.get("site") in sites(st) and sites(st)[sr["site"]].get("collector") == did:
            _site_router(st, sr["site"], sr)
        coll = next((sid for sid, c in sites(st).items() if c.get("collector") == did and sid == site), "")
        subnets: List[str] = []
        for n in _main_nets(st) + [n for c in sites(st).values() for n in c.get("subnets") or []]:
            if n not in subnets:
                subnets.append(n)
        out["subnets"] = subnets
        out["gateways"] = sorted(st.extra["gateways"])
        st._dirty = True
    if coll:                                                      # 1.0.46：設備登記（登記頁、封鎖）
        from . import netreg

        out.update(netreg.on_collector(st, coll, b))
    return out


def _site_router(st: Any, sid: str, sr: Dict[str, Any]) -> None:
    c = sites(st)[sid]
    now = st.now()
    if not sr.get("ok"):
        c["status"] = {"ok": False, "error": _s(sr.get("error"), 300), "time": now}
        return
    try:
        rx, tx, t = int(sr.get("rx") or 0), int(sr.get("tx") or 0), float(sr.get("t") or now)
    except (TypeError, ValueError):
        return
    last = c.get("last")
    c["last"] = {"t": t, "rx": rx, "tx": tx}
    c["status"] = {"ok": True, "time": now, "model": _s(sr.get("model"), 60), "n": len(sr.get("clients") or [])}
    if isinstance(sr.get("ta"), dict):                    # 1.0.46：華碩 Traffic Analyzer（代讀電腦每 10 分鐘讀一次）
        from .netanalysis import clean_ta

        c["ta"] = clean_ta(sr["ta"], now)
    if last and 0 < t - float(last.get("t") or 0) < 3600:
        drx, dtx = rx - int(last["rx"]), tx - int(last["tx"])
        if drx < 0:
            drx = rx                                     # 路由器重開 / 計數歸零
        if dtx < 0:
            dtx = tx
        if 0 <= drx <= MAX_DELTA and 0 <= dtx <= MAX_DELTA:
            day = st.today()
            d = c.setdefault("days", {}).setdefault(day, {"up": 0, "down": 0})
            d["up"] += dtx
            d["down"] += drx
            for k in sorted(c["days"])[:-400]:
                del c["days"][k]
            st.recent.add(f"site:{sid}", dtx, drx, now, t - float(last["t"]))
    devs = c.setdefault("devices", {})
    online = []
    for x in (sr.get("clients") or [])[:300]:
        if not isinstance(x, dict):
            continue
        mac = norm_mac(x.get("mac"))
        if not mac:
            continue
        d = devs.setdefault(mac, {"first": now})
        d.update(mac=mac, ip=_s(x.get("ip"), 60), name=_s(x.get("name"), 80), type=_s(x.get("type"), 20), last=now)
        if x.get("dtype") or x.get("vendor"):                 # 1.0.46：華碩的裝置類型、廠牌（設備登記）
            d.update(dtype=_s(x.get("dtype"), 10), vendor=_s(x.get("vendor"), 60))
        online.append(mac)
    c["online"] = online
    for m in [m for m, d in devs.items() if now - float(d.get("last") or 0) > SITE_DEV_KEEP_S]:
        del devs[m]


def _pc_split(src: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    x = src.get("split")
    if not x:
        return None
    return {"lan": x.get("lan"), "wan": x.get("wan"), "vpn": x.get("tun"), "vpnw": x.get("tunw")}


def _merge_devices(st: Any, rdevs: List[Dict[str, Any]], pcs: List[Dict[str, Any]], nas: Optional[Dict[str, Any]],
                   nas_ip: str) -> List[Dict[str, Any]]:
    """路由器上的裝置 ＋ 流量監看的電腦 ＋ NAS → 一張表（同一台：IP 或名稱對到）。"""
    used = set()
    out = []
    by_ip: Dict[str, Dict[str, Any]] = {}
    for p in pcs:
        for ip in re.split(r"[,\s]+", str(p.get("ip") or "")):
            if ip:
                by_ip[ip] = p
    for r in rdevs:
        p = by_ip.get(r.get("ip") or "")
        if p is None:
            nm = str(r.get("router_name") or r.get("name") or "").lower()
            p = next((x for x in pcs if nm and x["key"] not in used and
                      str(x.get("host") or "").lower().rstrip("$") == nm), None)
        d = {"key": r.get("alias_key") or f"mac:{r.get('mac')}", "alias_key": r.get("alias_key") or "",
             "name": r.get("name") or r.get("router_name") or r.get("ip") or r.get("mac"), "ip": r.get("ip", ""),
             "mac": r.get("mac", ""), "conn": _conn(r), "online": bool(r.get("online")), "allow": r.get("allow", ""),
             "kind": "device", "watched": bool(r.get("watched")), "last": r.get("last"), "first": r.get("first"),
             "dtype": r.get("dtype", ""), "model": r.get("model", ""), "vendor": r.get("vendor", ""),
             "router_name": r.get("router_name", "")}
        if p is not None:
            used.add(p["key"])
            d.update(kind="pc", source=p["key"], alias_key=f"pc:{p.get('id')}", name=p["name"], up=p["up"],
                     down=p["down"], rate=p.get("rate"), split=_pc_split(p), watched=True,
                     online=bool(r.get("online")) or bool(p.get("online")))
        elif nas_ip and r.get("ip") == nas_ip:
            d.update(nas or {"kind": "nas", "watched": True})
        out.append(d)
    for p in pcs:
        if p["key"] in used:
            continue
        out.append({"key": p["key"], "alias_key": f"pc:{p.get('id')}", "name": p["name"], "ip": p.get("ip", ""),
                    "mac": "", "conn": "", "online": bool(p.get("online")), "kind": "pc", "watched": True,
                    "source": p["key"], "up": p["up"], "down": p["down"], "rate": p.get("rate"), "split": _pc_split(p)})
    if nas is not None and not any(d.get("kind") == "nas" for d in out):
        out.append(dict({"key": "nas", "alias_key": "", "ip": nas_ip, "mac": "", "conn": "", "online": True,
                         "watched": True}, **nas))
    out.sort(key=lambda d: (not d["online"], -((d.get("up") or 0) + (d.get("down") or 0)), str(d.get("name"))))
    return out


def lan_devices(st: Any, sources: List[Dict[str, Any]], window: int, now: float) -> List[Dict[str, Any]]:
    rd = st._rdev_view(now).get("devices") or []
    rd = [r for r in rd if r.get("online") or now - float(r.get("last") or 0) < 86400]
    pcs = [s for s in sources if s.get("kind") == "pc"]
    nas = None
    src = {s["key"]: s for s in sources}
    if "nas" in src or "nasdir:lan" in src:
        n = src.get("nas") or {"up": 0, "down": 0, "rate": None}
        nas = {"kind": "nas", "name": "NAS", "source": "nas" if "nas" in src else "", "up": n["up"], "down": n["down"],
               "rate": n.get("rate"), "watched": True}
        if "nasdir:lan" in src:
            nas["split"] = {"lan": src["nasdir:lan"], "wan": src.get("nasdir:wan"), "vpn": src.get("nasdir:vpn"),
                            "vpnw": None}
            nas["split"] = {k: ({"up": v["up"], "down": v["down"]} if v else None) for k, v in nas["split"].items()}
    out = _merge_devices(st, rd, pcs, nas, str(st.cfg["nas"].get("host") or ""))
    from . import netreg

    netreg.annotate(st, "main", out)
    return out


def site_recent(st: Any, sid: str, window: int, chart: str = "") -> Dict[str, Any]:
    from .netrecent import WINDOWS

    if sid not in sites(st):
        raise HubHTTPError(404, "沒有這間實驗室")
    window = min(WINDOWS, key=lambda w: abs(w - int(window or 600)))
    now = st.now()
    rc = st.recent
    c = sites(st)[sid]
    out: List[Dict[str, Any]] = []
    with st.lock:
        w = rc.window(f"site:{sid}", window, now=now)
        out.append({"key": f"site:{sid}", "name": f"整個網段（{c.get('name') or sid} 路由器）", "kind": "router",
                    "up": w["up"], "down": w["down"], "rate": w.get("rate")})
        for did, pc in st.pcs.items():
            if pc.get("site") != sid:
                continue
            pw = rc.window(f"pc:{did}", window, now=now)
            o = {"key": f"pc:{did}", "id": did, "name": pc.get("label") or pc.get("host") or did, "kind": "pc",
                 "up": pw["up"], "down": pw["down"], "rate": pw.get("rate"), "ip": pc.get("ip", ""),
                 "online": now - float(pc.get("seen") or 0) < 180, "host": pc.get("host", ""),
                 "sub": " ".join(x for x in (pc.get("ip", ""), pc.get("user", "")) if x)}
            if any(f"pcx:{did}:{k}" in rc.data for k in ("lan", "wan", "tun", "tunw")):
                o["split"] = {k: {kk: v for kk, v in rc.window(f"pcx:{did}:{k}", window, now=now).items()
                                  if kk in ("up", "down")} for k in ("lan", "wan", "tun", "tunw")}
            out.append(o)
        ku = kd = 0
        for o in out[1:]:
            if o.get("split"):
                ku += o["split"]["wan"]["up"] + o["split"]["tunw"]["up"]
                kd += o["split"]["wan"]["down"] + o["split"]["tunw"]["down"]
            else:
                ku += o["up"]
                kd += o["down"]
        out.append({"key": "unknown", "name": "路由器有、監看不到的", "kind": "unknown", "nochart": True,
                    "up": max(0, w["up"] - ku), "down": max(0, w["down"] - kd), "rate": None,
                    "known": {"up": ku, "down": kd}})
        online = set(c.get("online") or [])
        rd = [{"mac": m, "ip": d.get("ip", ""), "router_name": d.get("name", ""), "type": d.get("type", ""),
               "alias_key": f"mac:{m}", "name": st.alias(f"mac:{m}", d.get("name", "")), "online": m in online,
               "last": d.get("last"), "first": d.get("first"), "watched": False, "dtype": d.get("dtype", ""),
               "vendor": d.get("vendor", "")}
              for m, d in (c.get("devices") or {}).items() if m in online or now - float(d.get("last") or 0) < 86400]
    pcs = [o for o in out if o["kind"] == "pc"]
    for r in rd:
        r["watched"] = any(r["ip"] and r["ip"] in str(p.get("ip") or "") for p in pcs)
    keys = {o["key"] for o in out if not o.get("nochart")}
    if chart not in keys:
        chart = f"site:{sid}"
    ch = rc.window(chart, window, series=True, now=now)
    st_ = c.get("status") or {}
    devices = _merge_devices(st, rd, pcs, None, "")
    from . import netreg

    netreg.annotate(st, sid, devices)
    return {"now": now, "window": window, "windows": list(WINDOWS), "sources": out, "site": sid,
            "sites": site_list(st), "site_status": st_, "devices": devices,
            "chart": {"key": chart, "series": ch.get("series", []), "step": ch.get("step", 60)},
            "every": {"router": 60, "nas": 20, "tailscale": 20}}


# ---------------------------------------------------------------- 改名、封鎖
def set_alias(st: Any, key: str, name: str) -> None:
    name = _s(name, 40).strip()
    if key.startswith("pc:"):
        st.set_label(key[3:], name)
        return
    if not re.match(r"^(mac:[0-9A-F:]{17}|ip:[0-9a-fA-F.:]{3,45}|ts:[A-Za-z0-9_.:-]{1,80})$", key):
        raise HubHTTPError(400, "名稱的對象格式不對")
    with st.lock:
        if name:
            st.extra["aliases"][key] = name
        else:
            st.extra["aliases"].pop(key, None)
        st._dirty = True
    st.save(force=True)


def block(st: Any, mac: str, on: bool, requester_ip: str, enable_ok: bool = False, self_ok: bool = False,
          client: Any = None) -> Dict[str, Any]:
    from .netusage import NetgearClient, NetgearError

    mac = norm_mac(mac)
    if not mac:
        raise HubHTTPError(400, "MAC 格式不對")
    rd = next((r for r in st.rdev.values() if r.get("mac") == mac), None)
    if rd is None:
        raise HubHTTPError(404, "路由器的裝置清單裡沒有這個 MAC（只能封鎖路由器看過的裝置）")
    nas_ip = str(st.cfg["nas"].get("host") or "")
    if on and rd.get("ip") and rd["ip"] in (nas_ip, "192.168.50.2"):
        raise HubHTTPError(400, "NAS 不能封鎖（Hub、論文庫、資料都在上面）")
    if on and rd.get("ip") == requester_ip and not self_ok:
        raise HubHTTPError(409, "這是你現在用來開 Hub 的裝置，封鎖後你會連不上；確定要封鎖請再確認")
    rc = st.cfg["router"]
    if not rc.get("host"):
        raise HubHTTPError(400, "還沒設定路由器")
    cl = client or NetgearClient(rc["host"], rc.get("user") or "admin", st._password(), int(rc.get("port") or 0))
    try:
        if on and not cl.block_enabled():
            if not enable_ok:
                raise HubHTTPError(409, "路由器的「存取控制」還沒開。開了之後，路由器依它的設定處理新裝置"
                                        "（預設「允許新裝置連線」）；確定要開請再確認")
            cl.set_block_enabled(True)
        cl.set_block(mac, on)
    except NetgearError as e:
        raise HubHTTPError(502, f"路由器拒絕：{e}") from None
    with st.lock:
        rd["allow"] = "Block" if on else "Allow"
        st._dirty = True
    st.save(force=True)
    return {"ok": True, "mac": mac, "allow": rd["allow"]}


# ---------------------------------------------------------------- 提醒沒裝流量監看
_DESK = re.compile(r"Windows NT|Macintosh|Mac OS X")
_MOBILE = re.compile(r"Mobile|Android|iPhone|iPad")


def real_ip(h: Handler) -> str:
    ip = h.ip
    try:
        from . import traffic

        return traffic.store(h.hub).client_class(ip, h.headers.get("X-Forwarded-For", ""))[1] or ip
    except Exception:  # noqa: BLE001
        return ip


def selfcheck(st: Any, ip: str, ua: str, user: str, notifier: Any = None) -> Dict[str, Any]:
    if not _DESK.search(ua or "") or _MOBILE.search(ua or ""):
        return {"show": False, "why": "not-desktop"}
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return {"show": False, "why": "ip"}
    lab = _nets(_main_nets(st) + [n for c in sites(st).values() for n in c.get("subnets") or []])
    vpn = ipaddress.ip_network("100.64.0.0/10")
    if not (_in(ip, lab) or (a.version == 4 and a in vpn)):
        return {"show": False, "why": "unknown-ip"}            # 看不到真正的來源（例如 Docker 轉送）→ 不亂提醒
    nas_ip = str(st.cfg["nas"].get("host") or "")
    if ip == nas_ip:
        return {"show": False, "why": "nas"}
    now = st.now()
    with st.lock:
        for pc in st.pcs.values():
            if now - float(pc.get("seen") or 0) > SEEN_S:
                continue
            ips = set(re.split(r"[,\s]+", str(pc.get("ip") or ""))) | {str(pc.get("src_ip") or "")}
            if ip in ips:
                return {"show": False, "why": "ok", "pc": pc.get("label") or pc.get("host")}
        pushed = st.extra.setdefault("pushed", {})
        due = bool(user) and now - float(pushed.get(user) or 0) > PUSH_EVERY_S
        if due:
            pushed[user] = now
            st._dirty = True
    os_ = "mac" if "Mac" in ua else "windows"
    if due and notifier is not None:
        try:
            notifier.notify_user(user, "netmeter", "這台電腦還沒裝流量監看",
                                 f"你從 {ip} 開了 Hub，但那台電腦沒有流量監看在回報。實驗室每天有 100 GB 上限，"
                                 "請在那台電腦安裝（Hub「模塊下載」→ QEL 流量監看）。", url="/get")
        except Exception:  # noqa: BLE001
            pass
    return {"show": True, "os": os_, "ip": ip, "get": "/get"}


# ---------------------------------------------------------------- 路由
def _st(h: Handler) -> Any:
    from .netusage import store

    return store(h.hub)


@route("PUT", r"/api/netusage/alias", auth="any")
def _alias(h: Handler) -> None:
    b = h._json_body() or {}
    try:
        set_alias(_st(h), str(b.get("key") or ""), str(b.get("name") or ""))
    except KeyError:
        raise HubHTTPError(404, "沒有這台電腦") from None
    h._json({"ok": True})


@route("POST", r"/api/netusage/block", auth="owner")
def _block(h: Handler) -> None:
    b = h._json_body() or {}
    st = _st(h)
    res = block(st, str(b.get("mac") or ""), bool(b.get("block")), real_ip(h), bool(b.get("enable")), bool(b.get("self")))
    u = (h.user() or {}).get("username", "")
    plat = getattr(h.hub, "platform", None)
    if plat is not None:
        try:
            plat.audit(u, "net-block" if b.get("block") else "net-allow", res["mac"])
        except Exception:  # noqa: BLE001
            pass
    h._json(res)


@route("GET", r"/api/netusage/sites", auth="owner")
def _sites_get(h: Handler) -> None:
    st = _st(h)
    h._json({"ok": True, "sites": public_sites(st), "main_name": st.extra.get("main_name") or "本實驗室",
             "pcs": [{"id": k, "name": v.get("label") or v.get("host") or k, "site": v.get("site", ""),
                      "gw": v.get("gw") or {}} for k, v in st.pcs.items()]})


@route("PUT", r"/api/netusage/sites", auth="owner")
def _sites_put(h: Handler) -> None:
    st = _st(h)
    set_sites(st, h._json_body() or {})
    h._json({"ok": True, "sites": public_sites(st)})


@route("GET", r"/api/netusage/selfcheck", auth="any")
def _selfcheck(h: Handler) -> None:
    n = None
    try:
        from . import notify

        n = notify.notifier(h)
    except Exception:  # noqa: BLE001
        n = None
    u = (h.user() or {}).get("username", "")
    h._json(selfcheck(_st(h), real_ip(h), h.headers.get("User-Agent", ""), u, n))

