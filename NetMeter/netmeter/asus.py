"""1.0.6：代讀華碩（ASUS / Asuswrt）路由器 —— 給「另一間實驗室」：Hub 連不到那邊的路由器，由那邊一台電腦的流量監看讀。

    Asus(host, user, password).read() → {"ok", "rx", "tx"（外網累計 bytes）, "clients": [{mac, ip, name, type}], "model"}

登入：POST /login.cgi（login_authorization = base64(帳號:密碼)）→ asus_token；
外網累計：GET /appGet.cgi?hook=netdev(appobj)（INTERNET_rx / INTERNET_tx，十六進位）；
連線裝置：GET /appGet.cgi?hook=get_clientlist()。
帳密只放在記憶體（Hub 每次回報的回應給，只給 Hub 指定的代讀電腦），不寫進檔案。只用標準函式庫。
"""
from __future__ import annotations

import base64
import http.cookiejar
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

UA = "asusrouter-Android-DUTUtil-1.0.0.245"          # 華碩 App 的 User-Agent：路由器才會回 JSON、不擋登入


class AsusError(RuntimeError):
    pass


def _loose_json(text: str) -> Dict[str, Any]:
    """路由器回的 JSON 有時候多了逗號、少了引號。"""
    t = text.strip()
    try:
        return json.loads(t)
    except ValueError:
        t = re.sub(r",\s*([}\]])", r"\1", t)
        try:
            return json.loads(t)
        except ValueError as e:
            raise AsusError(f"路由器回的資料看不懂：{t[:80]!r}") from e


def _num(v: Any) -> int:
    s = str(v or "0").strip()
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(float(s))
    except ValueError:
        return 0


def parse_netdev(d: Dict[str, Any]) -> Dict[str, int]:
    nd = d.get("netdev") or d
    rx, tx = _num(nd.get("INTERNET_rx")), _num(nd.get("INTERNET_tx"))
    if not rx and not tx:                                 # 沒有 INTERNET_*（舊韌體）→ 用 WAN
        rx, tx = _num(nd.get("WAN_rx") or nd.get("wan_rx")), _num(nd.get("WAN_tx") or nd.get("wan_tx"))
    if not rx and not tx and not any(k.startswith(("INTERNET", "WAN", "wan")) for k in nd):
        raise AsusError("路由器沒有外網計數（netdev 裡沒有 INTERNET / WAN）")
    return {"rx": rx, "tx": tx}


def parse_clients(d: Dict[str, Any]) -> List[Dict[str, str]]:
    cl = d.get("get_clientlist") or d
    out = []
    macs = cl.get("maclist") if isinstance(cl.get("maclist"), list) else [k for k in cl if re.match(r"^[0-9A-Fa-f:]{17}$", k)]
    for m in macs:
        x = cl.get(m) if isinstance(cl.get(m), dict) else {}
        if str(x.get("isOnline", "1")) not in ("1", "true", "True"):
            continue
        wl = str(x.get("isWL", "0"))
        out.append({"mac": m.upper(), "ip": str(x.get("ip") or ""), "name": str(x.get("nickName") or x.get("name") or ""),
                    "type": {"0": "wired", "1": "2.4GHz", "2": "5GHz", "3": "5GHz", "4": "6GHz"}.get(wl, wl),
                    "dtype": str(x.get("type") or ""), "vendor": str(x.get("vendor") or "")[:60]})
    return out


def parse_js_array(text: str, var: str = "array_statistics") -> List[Any]:
    """getWanTraffic.asp / getAppTraffic.asp 回的是 JavaScript：「array_statistics = [[…], …];」。"""
    i = text.rfind(var + " =")
    if i < 0:
        i = text.rfind(var + "=")
    if i < 0:
        raise AsusError("Traffic Analyzer 回的資料看不懂（沒有 array_statistics）")
    body = text[text.index("=", i) + 1:].strip()
    end = body.rfind("]")
    body = body[:end + 1] if end >= 0 else body
    if not body or body.startswith("new Array"):
        return []
    body = body.replace("'", '"')
    try:
        v = json.loads(re.sub(r",\s*([}\]])", r"\1", body))
    except ValueError as e:
        raise AsusError(f"Traffic Analyzer 回的資料看不懂：{body[:80]!r}") from e
    return v if isinstance(v, list) else []


def _rows(v: List[Any]) -> List[List[Any]]:
    out = []
    for r in v:
        if isinstance(r, list) and len(r) >= 3:
            try:
                out.append([str(r[0]), int(float(r[1])), int(float(r[2]))])
            except (TypeError, ValueError):
                continue
    return out


TAG = "QEL-unregistered"          # 我們加的家長電腦控制規則的名稱（只動這些）


def plan_block(nv: Dict[str, str], want: List[str]) -> Optional[Dict[str, str]]:
    """家長電腦控制（MULTIFILTER_*，用「>」分隔的平行清單）：讓名稱是 TAG 的規則剛好 = want（封鎖）。
    別人設的規則不動。沒有要改 → None。"""
    def split(k: str) -> List[str]:
        v = str(nv.get(k) or "")
        return v.split(">") if v else []
    en, mac, name, day = (split("MULTIFILTER_ENABLE"), split("MULTIFILTER_MAC"), split("MULTIFILTER_DEVICENAME"),
                          split("MULTIFILTER_MACFILTER_DAYTIME_V2"))
    n = len(mac)
    en += ["0"] * (n - len(en))
    name += [""] * (n - len(name))
    day += [""] * (n - len(day))
    want_set = {m.upper() for m in want}
    rows = []
    have_ours = set()
    for i in range(n):
        m = mac[i].upper()
        if name[i] == TAG:
            if m not in want_set:
                continue                                   # 拿掉：登記了
            have_ours.add(m)
            rows.append(["2", m, TAG, ""])
        else:
            rows.append([en[i], m, name[i], day[i]])
    others = {r[1] for r in rows if r[2] != TAG}
    add = [m for m in sorted(want_set) if m not in have_ours and m not in others]
    for m in add:
        rows.append(["2", m, TAG, ""])
    removed = {mac[i].upper() for i in range(n) if name[i] == TAG} - want_set
    if not add and not removed:
        return None
    out = {"MULTIFILTER_ENABLE": ">".join(r[0] for r in rows), "MULTIFILTER_MAC": ">".join(r[1] for r in rows),
           "MULTIFILTER_DEVICENAME": ">".join(r[2] for r in rows),
           "MULTIFILTER_MACFILTER_DAYTIME_V2": ">".join(r[3] for r in rows)}
    if want_set and str(nv.get("MULTIFILTER_ALL") or "0") != "1":
        out["MULTIFILTER_ALL"] = "1"
    return out


class Asus:
    def __init__(self, host: str, user: str, password: str, port: int = 0, use_ssl: bool = False,
                 timeout: float = 10.0, opener: Any = None) -> None:
        if not re.match(r"^[A-Za-z0-9.\-]+$", host or ""):
            raise AsusError("路由器位址格式不對")
        scheme = "https" if use_ssl else "http"
        self.base = f"{scheme}://{host}" + (f":{port}" if port else "")
        self.user, self.password, self.timeout = user, password, timeout
        self.jar = http.cookiejar.CookieJar()
        if opener is None:
            hs = [urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar)]
            if use_ssl:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE            # 路由器是自簽憑證
                hs.append(urllib.request.HTTPSHandler(context=ctx))
            opener = urllib.request.build_opener(*hs)
        self.opener = opener
        self.token: Optional[str] = None

    def _req(self, path: str, data: Optional[bytes] = None) -> str:
        req = urllib.request.Request(self.base + path, data=data, headers={
            "User-Agent": UA, "Referer": self.base + "/Main_Login.asp",
            **({"Cookie": f"asus_token={self.token}"} if self.token else {}),
            **({"Content-Type": "application/x-www-form-urlencoded"} if data else {})})
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raise AsusError(f"路由器回應 HTTP {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            raise AsusError(f"連不到路由器 {self.base}：{getattr(e, 'reason', e)}") from None

    def login(self) -> None:
        auth = base64.b64encode(f"{self.user}:{self.password}".encode("utf-8")).decode("ascii")
        text = self._req("/login.cgi", urllib.parse.urlencode({"login_authorization": auth}).encode("ascii"))
        try:
            d = _loose_json(text)
        except AsusError:
            d = {}
        tok = d.get("asus_token")
        if not tok:
            err = str(d.get("error_status") or "")
            raise AsusError({"3": "帳號或密碼錯誤", "7": "登入失敗太多次，路由器暫時鎖住", "10": "要驗證碼（請關掉路由器的登入驗證碼）"}
                            .get(err, f"登入失敗（{err or text[:60]}）"))
        self.token = tok

    def hook(self, name: str) -> Dict[str, Any]:
        if not self.token:
            self.login()
        q = urllib.parse.quote(name, safe="();")
        for attempt in (0, 1):
            text = self._req(f"/appGet.cgi?hook={q}")
            if "error_status" in text and attempt == 0:          # token 過期 → 重新登入
                self.login()
                continue
            return _loose_json(text)
        return {}

    def page(self, path: str) -> str:
        if not self.token:
            self.login()
        for attempt in (0, 1):
            text = self._req(path)
            if ("error_status" in text or "Main_Login" in text) and attempt == 0:
                self.login()
                continue
            return text
        return ""

    def traffic_analyzer(self, now: float, top: int = 10) -> Dict[str, Any]:
        """Traffic Analyzer（路由器要開；資料每小時更新）：最近 24 小時每小時、各設備、所有 App、前 top 名設備的 App。"""
        d = int(now)
        hourly = [[int(float(r[0])), int(float(r[1]))] for r in
                  parse_js_array(self.page(f"/getWanTraffic.asp?client=all&mode=hour&dura=24&date={d}"))
                  if isinstance(r, list) and len(r) >= 2]
        clients = _rows(parse_js_array(self.page(f"/getAppTraffic.asp?client=all&mode=detail&dura=24&date={d}")))
        clients.sort(key=lambda r: -(r[1] + r[2]))
        apps_all = _rows(parse_js_array(self.page(f"/getWanTraffic.asp?client=all&mode=detail&dura=24&date={d}")))
        apps_all.sort(key=lambda r: -(r[1] + r[2]))
        apps: Dict[str, List[List[Any]]] = {}
        for mac, _tx, _rx in clients[:top]:
            try:
                rows = _rows(parse_js_array(self.page(
                    f"/getWanTraffic.asp?client={urllib.parse.quote(mac)}&mode=detail&dura=24&date={d}")))
            except AsusError:
                continue
            rows.sort(key=lambda r: -(r[1] + r[2]))
            apps[mac.upper()] = rows[:8]
        return {"t": now, "hourly": hourly[:24], "clients": clients[:200], "apps_all": apps_all[:60], "apps": apps}

    def nvram(self, *keys: str) -> Dict[str, str]:
        d = self.hook(";".join(f"nvram_get({k})" for k in keys))
        out = {}
        for k in keys:                                    # 網頁的 nvram 值把 > < 寫成 &#62 &#60
            v = str(d.get(k) or "")
            for a, b in (("&#62;", ">"), ("&#62", ">"), ("&gt;", ">"), ("&#60;", "<"), ("&#60", "<"), ("&lt;", "<")):
                v = v.replace(a, b)
            out[k] = v
        return out

    def apply(self, data: Dict[str, str], service: str = "restart_firewall") -> Dict[str, Any]:
        """跟華碩網頁的 httpApi.nvramSet 一樣：POST /applyapp.cgi（內容 = encodeURIComponent(JSON)）。"""
        if not self.token:
            self.login()
        body = urllib.parse.quote(json.dumps(dict(data, action_mode="apply", rc_service=service))).encode("ascii")
        text = self._req("/applyapp.cgi", body)
        try:
            return _loose_json(text)
        except AsusError:
            return {}

    def set_blocked(self, macs: List[str]) -> Dict[str, Any]:
        """家長電腦控制：名稱 QEL-unregistered 的封鎖規則剛好 = macs（只擋上網，內網照通）。"""
        keys = ("MULTIFILTER_ALL", "MULTIFILTER_ENABLE", "MULTIFILTER_MAC", "MULTIFILTER_DEVICENAME",
                "MULTIFILTER_MACFILTER_DAYTIME_V2")
        nv = self.nvram(*keys)
        plan = plan_block(nv, macs)
        if plan is not None:
            self.apply(plan)
        return {"ok": True, "macs": sorted(m.upper() for m in macs), "changed": plan is not None}

    def read(self) -> Dict[str, Any]:
        nd = parse_netdev(self.hook("netdev(appobj)"))
        try:
            clients = parse_clients(self.hook("get_clientlist()"))
        except AsusError:
            clients = []
        model = ""
        try:
            model = str((self.hook("nvram_get(productid)") or {}).get("productid") or "")
        except AsusError:
            pass
        return {"ok": True, "rx": nd["rx"], "tx": nd["tx"], "clients": clients[:300], "model": model}
