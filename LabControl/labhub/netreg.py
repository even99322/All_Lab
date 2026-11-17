"""1.0.46：設備登記——沒有備註的手機 / 電腦連上實驗室網路後要登記身分，寬限期過了還沒登記就由路由器封鎖上網。

資料（netusage.json 的 extra）：
    reg       {MAC: {name, role（student / teacher / guest）, until（訪客到期日 YYYY-MM-DD）, by, t, site}}
    enforce   {site: {on, since, grace（分鐘）}}         site = "main"（本實驗室）或其他實驗室代號
    regblock  {MAC: {site, t, why（unregistered / expired）}}  這裡封鎖的（解除時只解除這些）
    expired   {MAC: t}                                   訪客到期 → 立刻封鎖，重新登記才解除

「已登記」= 有登記、或在網路用量改過名稱（有備註）、或裝了流量監看、或是 NAS。
只管手機 / 平板與電腦（路由器回報的裝置類型、名稱、隨機 MAC 判斷）；其他（儀器、印表機…）和認不出的不封鎖。
開啟強制登記那一刻已經在網路上出現過的設備不受影響（不會把正在量測的電腦擋掉），之後新出現的才要登記。

封鎖：本實驗室 = Netgear 存取控制（Hub 讀路由器時順便做）；其他實驗室 = 代讀電腦用華碩家長電腦控制
（只擋上網，內網照通 → 被擋的手機還開得了代讀電腦上的登記頁）。
登記頁：本實驗室 http://<NAS>:8765/reg（不用登入，只認實驗室網段，只能登記自己這台）；
       其他實驗室：代讀電腦上的流量監看開 http://<代讀電腦>:47680/（回報時轉給 Hub）。
"""
from __future__ import annotations

import datetime as _dt
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

from .server import Handler, HubHTTPError, route

ROLES = {"student": "學生", "teacher": "老師", "guest": "訪客"}
REG_PORT = 47680
GRACE_DEFAULT = 15
# 華碩 clientlist 的 type（www/client_function.js）
ASUS_PHONE = {"33", "10", "28", "19", "9", "73", "21", "29", "20", "31", "32"}
ASUS_PC = {"34", "14", "1", "72", "37", "35", "6", "38", "15", "30"}
ASUS_OTHER = {"2", "24", "4", "39", "40", "18", "41", "25", "26", "43", "44", "45"}
PHONE_WORDS = ("iphone", "ipad", "android", "galaxy", "smartphone", "phone", "pixel", "redmi", "xiaomi", "oppo",
               "vivo", "huawei", "samsung", "mobile", "tablet", "realme", "oneplus", "sony xperia", "zenfone")
PC_WORDS = ("desktop", "laptop", "notebook", "macbook", "imac", "mac mini", "computer", "windows", "thinkpad",
            "surface", "ultrabook", "workstation")
OTHER_WORDS = ("printer", "camera", "router", "repeater", "extender", "nas", "server", "switch", "tv", "chromecast",
               "speaker", "homepod", "alexa", "echo", "kindle", "scanner", "console", "playstation", "xbox", "nintendo")


def _s(v: Any, n: int = 80) -> str:
    return re.sub(r"[\x00-\x1f<>]", "", str(v or "")).strip()[:n]


def norm_mac(m: Any) -> str:
    parts = re.split(r"[:\-]", str(m or "").strip())
    if len(parts) != 6:
        return ""
    try:
        return ":".join(f"{int(p, 16):02X}" for p in parts)
    except ValueError:
        return ""


def random_mac(mac: str) -> bool:
    """手機的「私人 Wi-Fi 位址」：第一個位元組的 locally administered 位元。"""
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except (ValueError, IndexError):
        return False


def classify(d: Dict[str, Any]) -> str:
    """phone / computer / other / unknown（unknown 不封鎖）。"""
    t = str(d.get("dtype") or "").strip()
    if t in ASUS_PHONE:
        return "phone"
    if t in ASUS_PC:
        return "computer"
    if t in ASUS_OTHER:
        return "other"
    text = " ".join(str(d.get(k) or "") for k in ("dtype", "model", "vendor", "router_name", "name")).lower()
    if any(w in text for w in OTHER_WORDS):
        return "other"
    if any(w in text for w in PHONE_WORDS):
        return "phone"
    if any(w in text for w in PC_WORDS) or re.search(r"\b(desktop|laptop)-[a-z0-9]+", text):
        return "computer"
    mac = str(d.get("mac") or "")
    wireless = bool(re.search(r"ghz|wifi|wi-fi|wireless|5g|2\.4g|6g", str(d.get("conn") or d.get("type") or "").lower()))
    if mac and random_mac(mac) and (wireless or not d.get("conn")):
        return "phone"
    return "unknown"


KIND_TEXT = {"phone": "手機 / 平板", "computer": "電腦", "other": "其他設備", "unknown": "認不出類型"}


# ---------------------------------------------------------------- 資料
def _x(st: Any) -> Dict[str, Any]:
    e = st.extra
    for k in ("reg", "enforce", "regblock", "expired"):
        if not isinstance(e.get(k), dict):
            e[k] = {}
    return e


def enforce_cfg(st: Any, site: str) -> Dict[str, Any]:
    c = _x(st)["enforce"].get(site) or {}
    return {"on": bool(c.get("on")), "since": float(c.get("since") or 0), "grace": int(c.get("grace") or GRACE_DEFAULT)}


def _today(st: Any) -> str:
    try:
        return st.today()
    except Exception:  # noqa: BLE001
        return time.strftime("%Y-%m-%d")


def expire_guests(st: Any) -> List[str]:
    """訪客到期 → 拿掉登記、記成 expired（強制登記開著的實驗室會立刻封鎖）。"""
    x = _x(st)
    today = _today(st)
    gone = [m for m, r in x["reg"].items() if r.get("role") == "guest" and r.get("until") and r["until"] < today]
    for m in gone:
        x["reg"].pop(m, None)
        x["expired"][m] = st.now()
    if gone:
        st._dirty = True
    return gone


def registered(st: Any, mac: str) -> Optional[Dict[str, Any]]:
    mac = norm_mac(mac)
    if not mac:
        return None
    x = _x(st)
    r = x["reg"].get(mac)
    if r:
        return dict(r, how="reg")
    if mac in x["expired"]:
        return None
    if st.alias(f"mac:{mac}", ""):
        return {"how": "alias", "name": st.alias(f"mac:{mac}", "")}
    return None


def state_of(st: Any, site: str, d: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    """一台設備的登記狀態：ok / pending（剩 left 秒）/ blocked / exempt（不用登記）/ none（強制登記沒開、未登記）。"""
    now = st.now() if now is None else now
    mac = norm_mac(d.get("mac"))
    kind = d.get("dkind") or classify(d)
    out: Dict[str, Any] = {"kind": kind, "kind_text": KIND_TEXT.get(kind, "")}
    if d.get("kind") in ("pc", "nas") or d.get("watched"):
        out["state"] = "ok"
        out["how"] = "watched"
        return out
    if not mac:
        out["state"] = "exempt"
        return out
    r = registered(st, mac)
    if r:
        out.update(state="ok", how=r.get("how"), role=r.get("role", ""), role_text=ROLES.get(r.get("role", ""), ""),
                   until=r.get("until", ""))
        return out
    x = _x(st)
    if mac in x["regblock"]:
        out["state"] = "blocked"
        out["why"] = x["regblock"][mac].get("why", "")
        return out
    cfg = enforce_cfg(st, site)
    expired = mac in x["expired"]
    if kind not in ("phone", "computer") and not expired:
        out["state"] = "exempt" if cfg["on"] else "none"
        return out
    if not cfg["on"]:
        out["state"] = "none"
        return out
    first = float(d.get("first") or now)
    if not expired and first <= cfg["since"]:
        out["state"] = "none"            # 開啟強制登記前就在的：不封鎖，只標未登記
        out["old"] = True
        return out
    left = 0.0 if expired else first + cfg["grace"] * 60 - now
    out["state"] = "pending" if left > 0 else "due"
    out["left"] = max(0, int(left))
    if expired:
        out["why"] = "expired"
    return out


def annotate(st: Any, site: str, devices: Iterable[Dict[str, Any]]) -> None:
    now = st.now()
    for d in devices:
        if d.get("mac") or d.get("kind") in ("pc", "nas"):
            d["reg"] = state_of(st, site, d, now)


def want_blocked(st: Any, site: str, devices: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    """這間實驗室現在應該被擋的 MAC → 原因。強制登記沒開 → 空的（之前擋的都解除）。"""
    if not enforce_cfg(st, site)["on"]:
        return {}
    now = st.now()
    out: Dict[str, str] = {}
    x = _x(st)
    for d in devices:
        mac = norm_mac(d.get("mac"))
        if not mac:
            continue
        s = state_of(st, site, d, now)
        if s["state"] in ("due", "blocked"):
            out[mac] = s.get("why") or x["regblock"].get(mac, {}).get("why") or "unregistered"
    # 已經擋了、現在離線的：還沒登記就繼續擋著
    for mac, b in x["regblock"].items():
        if b.get("site") == site and mac not in out and not registered(st, mac):
            out[mac] = b.get("why") or "unregistered"
    return out


def register(st: Any, mac: str, body: Dict[str, Any], by: str, site: str = "") -> Dict[str, Any]:
    mac = norm_mac(mac)
    if not mac:
        raise HubHTTPError(400, "MAC 格式不對")
    name = _s(body.get("name"), 40)
    role = str(body.get("role") or "")
    until = str(body.get("until") or "").strip()
    if not name:
        raise HubHTTPError(400, "請填設備名稱")
    if role not in ROLES:
        raise HubHTTPError(400, "請選身分（學生 / 老師 / 訪客）")
    if role == "guest":
        try:
            u = _dt.date.fromisoformat(until)
        except ValueError:
            raise HubHTTPError(400, "訪客請填到期日") from None
        if u.isoformat() < _today(st):
            raise HubHTTPError(400, "到期日已經過了")
        until = u.isoformat()
    else:
        until = ""
    x = _x(st)
    with st.lock:
        x["reg"][mac] = {"name": name, "role": role, "until": until, "by": _s(by, 60), "t": st.now(), "site": site}
        x["expired"].pop(mac, None)
        st.extra.setdefault("aliases", {})[f"mac:{mac}"] = name
        st._dirty = True
    st.save()
    return dict(x["reg"][mac], mac=mac)


def unregister(st: Any, mac: str) -> None:
    mac = norm_mac(mac)
    x = _x(st)
    with st.lock:
        x["reg"].pop(mac, None)
        st._dirty = True
    st.save()


def set_enforce(st: Any, site: str, on: bool, grace: int) -> Dict[str, Any]:
    x = _x(st)
    grace = max(1, min(int(grace or GRACE_DEFAULT), 24 * 60))
    with st.lock:
        old = x["enforce"].get(site) or {}
        since = float(old.get("since") or 0) if old.get("on") and on else (st.now() if on else 0)
        x["enforce"][site] = {"on": bool(on), "since": since, "grace": grace}
        st._dirty = True
    st.save()
    return enforce_cfg(st, site)


# ---------------------------------------------------------------- 本實驗室：Netgear 存取控制
def enforce_main(st: Any, cl: Any, devices: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Hub 讀路由器時呼叫（背景執行緒）：把該擋的擋掉、登記了的解除。"""
    expire_guests(st)
    x = _x(st)
    want = want_blocked(st, "main", devices)
    have = {m for m, b in x["regblock"].items() if b.get("site") == "main"}
    res: Dict[str, Any] = {"time": st.now(), "blocked": sorted(want), "ok": True}
    if not (set(want) - have) and not (have - set(want)):
        return res
    try:
        if set(want) - have and not cl.block_enabled():
            raise RuntimeError("路由器的「存取控制」沒有開：請在設備表封鎖一次任何設備（會詢問是否開啟），或到路由器管理頁打開")
        for m in sorted(set(want) - have):
            cl.set_block(m, True)
            with st.lock:
                x["regblock"][m] = {"site": "main", "t": st.now(), "why": want[m]}
                st._dirty = True
            _audit(st, "netreg-block", m)
        for m in sorted(have - set(want)):
            cl.set_block(m, False)
            with st.lock:
                x["regblock"].pop(m, None)
                st._dirty = True
            _audit(st, "netreg-allow", m)
    except Exception as e:  # noqa: BLE001
        res.update(ok=False, error=str(e)[:300])
    st.status["netreg_main"] = res
    return res


def _audit(st: Any, what: str, mac: str) -> None:
    fn = getattr(st, "audit", None)
    if fn:
        try:
            fn("", what, mac)
        except Exception:  # noqa: BLE001
            pass


def main_devices(st: Any) -> List[Dict[str, Any]]:
    now = st.now()
    rd = st._rdev_view(now).get("devices") or []
    return [dict(r, conn=r.get("type", "")) for r in rd if r.get("online") and r.get("mac")]


def unblock_soon(st: Any, mac: str) -> None:
    """登記後馬上解除（不等下一次讀路由器）。"""
    x = _x(st)
    b = x["regblock"].get(norm_mac(mac))
    if not b or b.get("site") != "main":
        return

    def run() -> None:
        try:
            from .netusage import NetgearClient

            rc = st.cfg["router"]
            cl = NetgearClient(rc["host"], rc.get("user") or "admin", st._password(), int(rc.get("port") or 0))
            enforce_main(st, cl, main_devices(st))
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------- 其他實驗室：代讀電腦
def site_devices(st: Any, sid: str) -> List[Dict[str, Any]]:
    from .netsites import sites

    c = sites(st).get(sid) or {}
    online = set(c.get("online") or [])
    watched_ips = {ip for pc in st.pcs.values() if pc.get("site") == sid
                   for ip in re.split(r"[,\s]+", str(pc.get("ip") or "")) if ip}
    out = []
    for m, d in (c.get("devices") or {}).items():
        if m in online:
            out.append(dict(d, mac=m, conn=d.get("type", ""), watched=d.get("ip") in watched_ips))
    return out


def on_collector(st: Any, sid: str, b: Dict[str, Any]) -> Dict[str, Any]:
    """代讀電腦回報：收它的登記頁送來的登記、它套用封鎖的結果；回它該擋的 MAC 與登記頁要顯示的狀態。"""
    expire_guests(st)
    x = _x(st)
    acks = []
    devs = {norm_mac(d.get("mac")): d for d in site_devices(st, sid)}
    for r in (b.get("regs") or [])[:50]:
        if not isinstance(r, dict):
            continue
        rid = _s(r.get("id"), 40)
        mac = norm_mac(r.get("mac"))
        try:
            if mac not in devs and mac not in x["regblock"]:
                raise HubHTTPError(400, "那邊的路由器上沒有這台")
            register(st, mac, r, f"登記頁（{ _s(r.get('ip'), 40) }）", sid)
            acks.append({"id": rid, "ok": True})
        except HubHTTPError as e:
            acks.append({"id": rid, "ok": False, "error": str(e)})
    bs = b.get("block_state")
    if isinstance(bs, dict):
        applied = {norm_mac(m) for m in bs.get("macs") or []} - {""}
        with st.lock:
            for m in applied:
                if m not in x["regblock"] or x["regblock"][m].get("site") != sid:
                    x["regblock"][m] = {"site": sid, "t": st.now(), "why": "unregistered"}
            for m in [m for m, v in x["regblock"].items() if v.get("site") == sid and m not in applied]:
                x["regblock"].pop(m, None)
            st._dirty = True
        st.status[f"netreg_{sid}"] = {"time": st.now(), "ok": bool(bs.get("ok", True)),
                                      "error": _s(bs.get("error"), 300), "blocked": sorted(applied)}
    ds = b.get("dhcp_state")
    if isinstance(ds, dict):                                  # 1.0.47：手動指定 IP 的結果與路由器上全部的保留
        with st.lock:
            c = _site(st, sid)
            lst = [[norm_mac(r[0]), _s(r[1], 45)] for r in (ds.get("list") or [])[:200]
                   if isinstance(r, (list, tuple)) and len(r) >= 2 and norm_mac(r[0])]
            c["dhcp_state"] = {"ok": bool(ds.get("ok", True)), "error": _s(ds.get("error"), 300), "t": st.now(),
                               **({"list": lst} if ds.get("ok", True) else {"list": (c.get("dhcp_state") or {}).get("list", [])})}
            if ds.get("ok", True):
                have = {m for m, _ip in lst}
                rm = x.setdefault("dhcp_rm", {}).get(sid) or []
                x["dhcp_rm"][sid] = [m for m in rm if m in have]
            st._dirty = True
    want = want_blocked(st, sid, devs.values())
    page = {}
    now = st.now()
    for m, d in devs.items():
        s = state_of(st, sid, d, now)
        page[m] = {"state": s["state"], "left": s.get("left", 0), "kind": s["kind"],
                   "name": (registered(st, m) or {}).get("name", "")}
    dh = (x.get("dhcp") or {}).get(sid) or {}
    return {"site_block": sorted(want), "regs_ack": acks, "reg_status": page,
            "enforce": enforce_cfg(st, sid)["on"],
            "site_dhcp": {"set": {m: {"ip": v["ip"], "name": v.get("name", "")} for m, v in dh.items()},
                          "remove": list((x.get("dhcp_rm") or {}).get(sid) or [])}}


# ---------------------------------------------------------------- 1.0.47：手動指定 IP（華碩 DHCP 保留）
def _site(st: Any, sid: str) -> Dict[str, Any]:
    from .netsites import sites

    return sites(st)[sid]


def dhcp_view(st: Any, sid: str) -> Dict[str, Any]:
    """{MAC: {ip, managed}}：路由器上的保留（代讀電腦回報）＋ Hub 要設的（還沒套用的也算）。"""
    from .netsites import sites

    if sid not in sites(st):
        return {}
    out = {m: {"ip": ip, "managed": False} for m, ip in ((_site(st, sid).get("dhcp_state") or {}).get("list") or [])}
    for m in (_x(st).get("dhcp_rm") or {}).get(sid) or []:
        out.pop(m, None)
    for m, v in ((_x(st).get("dhcp") or {}).get(sid) or {}).items():
        out[m] = {"ip": v["ip"], "managed": True, "pending": out.get(m, {}).get("ip") != v["ip"]}
    return out


def dhcp_set(st: Any, sid: str, mac: str, ip: str, name: str, by: str) -> Dict[str, Any]:
    import ipaddress

    from .netsites import _nets, sites

    if sid == "main" or not sid:
        raise HubHTTPError(400, "本實驗室的 Netgear 沒有可以從外面設定的介面：請到路由器管理頁「進階 → 設定 → LAN 設定 → 位址保留」")
    if sid not in sites(st):
        raise HubHTTPError(404, "沒有這間實驗室")
    c = sites(st)[sid]
    if not c.get("collector"):
        raise HubHTTPError(409, "這間還沒有代讀電腦：要由代讀電腦設定路由器")
    mac = norm_mac(mac)
    if not mac:
        raise HubHTTPError(400, "MAC 格式不對")
    x = _x(st)
    with st.lock:
        man = x.setdefault("dhcp", {}).setdefault(sid, {})
        rm = x.setdefault("dhcp_rm", {}).setdefault(sid, [])
        ip = str(ip or "").strip()
        if not ip:
            if man.pop(mac, None) is not None or mac in dict((c.get("dhcp_state") or {}).get("list") or []):
                if mac not in rm:
                    rm.append(mac)
            st._dirty = True
            return {"ok": True, "mac": mac, "ip": ""}
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            raise HubHTTPError(400, "IP 格式不對") from None
        nets = [n for n in _nets(c.get("subnets") or []) if n.version == a.version]
        if not any(a in n for n in nets):
            raise HubHTTPError(400, f"{ip} 不在這間實驗室的網段（{'、'.join(c.get('subnets') or []) or '還沒設定網段'}）")
        if any(a == n.network_address or a == n.broadcast_address for n in nets):
            raise HubHTTPError(400, "不能用網段的第一個 / 最後一個位址")
        gw = ((st.pcs.get(c.get("collector") or "") or {}).get("gw") or {}).get("ip")
        for m, v in dhcp_view(st, sid).items():
            if m != mac and v["ip"] == ip:
                raise HubHTTPError(409, f"{ip} 已經指定給 {st.alias('mac:' + m, m)}")
        for m, d in (c.get("devices") or {}).items():
            if m != mac and d.get("ip") == ip and m in set(c.get("online") or []):
                raise HubHTTPError(409, f"{ip} 現在被 {st.alias('mac:' + m, d.get('name') or m)} 使用中：換一個，或等它離線")
        if gw and ip == gw:
            raise HubHTTPError(400, "這是路由器自己的位址")
        man[mac] = {"ip": ip, "name": _s(name, 40), "by": _s(by, 60), "t": st.now()}
        if mac in rm:
            rm.remove(mac)
        st._dirty = True
    st.save()
    return {"ok": True, "mac": mac, "ip": ip}


@route("PUT", r"/api/netusage/dhcp", auth="owner")
def _dhcp_put(h: Handler) -> None:
    b = h._json_body() or {}
    st = _st(h)
    res = dhcp_set(st, str(b.get("site") or ""), str(b.get("mac") or ""), str(b.get("ip") or ""),
                   str(b.get("name") or ""), (h.user() or {}).get("username", ""))
    h._json(res)


# ---------------------------------------------------------------- 網頁
def _st(h: Handler) -> Any:
    from .netusage import store

    return store(h.hub)


def _lan_ok(st: Any, ip: str) -> bool:
    from .netsites import _in, _main_nets, _nets

    return _in(ip, _nets(_main_nets(st)))


def _self_dev(st: Any, ip: str) -> Optional[Dict[str, Any]]:
    for d in main_devices(st):
        if d.get("ip") == ip:
            return d
    now = st.now()
    for d in (st._rdev_view(now).get("devices") or []):
        if d.get("ip") == ip and d.get("mac") and now - float(d.get("last") or 0) < 1800:
            return d
    return None


def self_view(st: Any, ip: str) -> Dict[str, Any]:
    if not _lan_ok(st, ip):
        return {"ok": False, "why": "not-lan", "ip": ip}
    d = _self_dev(st, ip)
    if d is None:
        return {"ok": False, "why": "unknown", "ip": ip}
    s = state_of(st, "main", d)
    r = registered(st, d["mac"]) or {}
    return {"ok": True, "ip": ip, "mac": d["mac"], "name": r.get("name") or d.get("name") or "", "reg": s,
            "roles": ROLES}


@route("GET", r"/reg", auth=False)
def _page(h: Handler) -> None:
    from pathlib import Path

    h._html((Path(__file__).resolve().parent / "static" / "reg.html").read_text(encoding="utf-8"))


@route("GET", r"/reg/poster")
def _poster(h: Handler) -> None:
    from pathlib import Path

    from .web_nav import with_head

    h._html(with_head((Path(__file__).resolve().parent / "static" / "regposter.html").read_text(encoding="utf-8")))


@route("GET", r"/vendor/qrcode\.js", auth=False)
def _qrjs(h: Handler) -> None:
    from pathlib import Path

    h._send(200, (Path(__file__).resolve().parent / "static" / "vendor" / "qrcode.js").read_bytes(),
            "text/javascript; charset=utf-8", {"Cache-Control": "max-age=86400"})


@route("GET", r"/api/netreg/self", auth=False)
def _self_get(h: Handler) -> None:
    from .netsites import real_ip

    h._json(self_view(_st(h), real_ip(h)))


@route("POST", r"/api/netreg/self", auth=False)
def _self_post(h: Handler) -> None:
    from .netsites import real_ip

    st = _st(h)
    ip = real_ip(h)
    v = self_view(st, ip)
    if not v.get("ok"):
        raise HubHTTPError(403, "請用實驗室的網路（Wi-Fi）打開這一頁" if v.get("why") == "not-lan"
                           else "路由器上還看不到這台設備：等 2 分鐘再試")
    r = register(st, v["mac"], h._json_body() or {}, f"登記頁（{ip}）", "main")
    unblock_soon(st, v["mac"])
    h._json({"ok": True, "reg": r})


@route("GET", r"/api/netreg", auth="any")
def _list(h: Handler) -> None:
    from .netsites import public_sites, sites

    st = _st(h)
    expire_guests(st)
    x = _x(st)
    regs = [dict(v, mac=m, role_text=ROLES.get(v.get("role", ""), "")) for m, v in x["reg"].items()]
    regs.sort(key=lambda r: -float(r.get("t") or 0))
    en = {"main": enforce_cfg(st, "main")}
    for sid in sites(st):
        en[sid] = enforce_cfg(st, sid)
    col = {}
    for s in public_sites(st):
        pc = st.pcs.get(s.get("collector") or "") or {}
        ip = next((i for i in re.split(r"[,\s]+", str(pc.get("ip") or "")) if i), "")
        col[s["id"]] = {"name": s.get("name") or s["id"], "url": f"http://{ip}:{REG_PORT}/" if ip else ""}
    st_ = {k: v for k, v in st.status.items() if k.startswith("netreg_")}
    host = str(st.cfg["nas"].get("host") or "") or (h.headers.get("Host") or "").split(":")[0]
    from . import DEFAULT_PORT

    main_url = f"http://{host}:{DEFAULT_PORT}/reg" if host else "/reg"
    h._json({"regs": regs, "enforce": en, "roles": ROLES, "collector_pages": col, "status": st_, "main_url": main_url,
             "main_name": st.extra.get("main_name") or "本實驗室", "port": REG_PORT})


@route("POST", r"/api/netreg", auth="any")
def _reg_other(h: Handler) -> None:
    b = h._json_body() or {}
    st = _st(h)
    user = (h.user() or {}).get("username", "")
    r = register(st, str(b.get("mac") or ""), b, user, _s(b.get("site"), 40))
    unblock_soon(st, r["mac"])
    h._json({"ok": True, "reg": r})


@route("DELETE", r"/api/netreg/([0-9A-Fa-f:\-]{12,17})", auth="owner")
def _unreg(h: Handler, mac: str) -> None:
    unregister(_st(h), mac)
    h._json({"ok": True})


@route("PUT", r"/api/netreg/enforce", auth="owner")
def _enforce(h: Handler) -> None:
    from .netsites import sites

    b = h._json_body() or {}
    st = _st(h)
    site = str(b.get("site") or "main")
    if site != "main" and site not in sites(st):
        raise HubHTTPError(404, "沒有這間實驗室")
    h._json({"ok": True, "enforce": set_enforce(st, site, bool(b.get("on")), int(b.get("grace") or GRACE_DEFAULT))})
