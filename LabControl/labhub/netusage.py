"""1.0.29：網路用量——每天用了多少流量（只用標準函式庫）。1.0.30：各台裝置改由獨立的「流量監看」程式回報。

三個來源，都存成「每天上傳 / 下載幾個 byte」（日期以 Hub 設定的時區切，預設台北）：

1. 路由器（Netgear，例如 RAX200）的 Traffic Meter：整個網段對外的總量。
   用路由器的 SOAP 介面（跟 Netgear 手機 App 同一套；HTTP 5000 / 80 或 HTTPS 443 / 5555 的 /soap/server_sa/）
   登入後呼叫 DeviceConfig:1#GetTrafficMeterStatistics，每 10 分鐘一次：
   今天的值 → 今天；昨天的值 → 昨天（換日後以路由器的最終數字為準）。路由器要先打開 Traffic Meter。
   路由器回的是 MB（以 1 MB = 1024² byte 換算）。
2. NAS 自己：SNMP v2c 讀網路介面的 64 位元計數（IF-MIB ifHCInOctets / ifHCOutOctets），每分鐘累加差值。
   DSM「控制台 → 終端機與 SNMP → SNMP」打開 SNMPv1/v2c 並設定社群名稱。
3. 各台裝置：獨立的「流量監看」程式（QELNetMeter，開機自動在背景執行）只在裝置連到實驗室網段（預設
   192.168.50.0/24）時，累計那張網卡的流量，自己存每天的總量，每分鐘把最近幾天的總量送到 Hub
   （POST /api/netusage/report，不用登入）；Hub 以程式送來的數字為準（同一天直接覆蓋，重送不會重複算）。

    GET    /api/netusage?days=31          → 每天的用量（路由器 / NAS / 各台電腦）與各來源狀態（登入的人）
    PUT    /api/netusage/config           {router: {...}, nas: {...}, tz}（站長；密碼留空 = 不改）
    POST   /api/netusage/test             {what: "router" | "nas"} → 立刻試一次（站長）
    POST   /api/netusage/report           ← 流量監看程式回報 {id, host, user, os, version, ip, subnet, ifaces,
                                             rate: {up, down}, days: {日期: {up, down}}}
    PUT    /api/netusage/label            {host: 裝置 id, label}（登入的人）
    DELETE /api/netusage/pc/<裝置 id>      （站長）
    GET    /network                       → 網頁「網路用量」

4. 1.0.32 Tailscale（VPN）：每分鐘讀 NAS 上 tailscaled 的 LocalAPI（/localapi/v0/status，跟內網 https 憑證同一個 socket），
   每個 VPN 裝置跟 NAS 之間的位元組數，依連線路徑分成：區網直連（不經外網）、外網直連、DERP 中繼（經外網）。
   也檢查 NAS 有沒有開「出口節點 / 子網路由」（別人經 NAS 上網會吃掉實驗室的對外流量）。
6. 1.0.37 NAS 分流：記帳容器（labhub/netacct.py，控制代理建立）在 NAS 主機的 iptables 計數，
   分成 NAS ⇄ 區網、NAS ⇄ VPN（Tailscale 的 WireGuard，含 DERP 中繼）、NAS ⇄ 外網（直接）；
   也記 NAS 跟每台裝了流量監看的電腦之間傳了多少（電腦的流量因此可以分成「跟 NAS」與「其餘，多半是外網」）。
5. 1.0.32 Hub 自己從 GitHub 下載新版的量（控制台「從 GitHub 取得新版」與自動更新）。

路由器密碼只存在 Hub 的資料夾（data/netusage_secret.json），API 不會回傳。
"""
from __future__ import annotations

import datetime as _dt
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

KEEP_DAYS = 400
ROUTER_EVERY = 120.0                     # 1.0.34：即時頁（原本 10 分鐘）
NAS_EVERY = 20.0
TS_EVERY = 20.0
TS_PATHS = ("lan", "wan", "relay")       # 區網直連 / 外網直連 / DERP 中繼
TS_SOCKETS = ("/tailscale/tailscaled.sock", "/var/run/tailscale/tailscaled.sock",
              "/var/packages/Tailscale/var/tailscaled.sock")
PC_OFFLINE_S = 180.0
RDEV_KEEP_S = 30 * 86400.0               # 1.0.40：路由器上的裝置留 30 天
GAP_S = 7200.0                       # NAS 超過 2 小時沒讀到 → 那段時間的流量不算（不知道是哪天）
MAX_DAY = 100 * 1024 ** 4            # 一台裝置一天超過 100 TB → 當成錯誤資料
SPLIT_CATS = ("lan", "wan", "tun", "tunw")
SPLIT_KEYS = tuple(f"{c}_{d}" for c in SPLIT_CATS for d in ("up", "down"))
MAX_DEVICES = 500
_ID = re.compile(r"^[A-Za-z0-9_.-]{6,64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_DELTA = 50 * 1024 ** 4           # 一次差值超過 50 TB → 當成計數錯亂，不算
MB = 1024 * 1024
_HOST = re.compile(r"[^A-Za-z0-9_.@-]+")


def _s(v: Any, n: int = 120) -> str:
    return str(v if v is not None else "")[:n]


# ---------------------------------------------------------------------------
# 日期（時區）
# ---------------------------------------------------------------------------
def _tz(name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name or "Asia/Taipei")
    except Exception:  # noqa: BLE001  容器沒有 tzdata → 台北固定 +8
        return _dt.timezone(_dt.timedelta(hours=8))


def day_of(t: float, tz) -> str:
    return _dt.datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Netgear SOAP
# ---------------------------------------------------------------------------
SOAP = """<?xml version="1.0" encoding="utf-8" standalone="no"?>
<SOAP-ENV:Envelope xmlns:SOAPSDK1="http://www.w3.org/2001/XMLSchema"
  xmlns:SOAPSDK2="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:SOAPSDK3="http://schemas.xmlsoap.org/soap/encoding/"
  xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">
<SOAP-ENV:Header>
<SessionID>A7D88AE69687E58D9A00</SessionID>
</SOAP-ENV:Header>
<SOAP-ENV:Body>
<M1:{method} xmlns:M1="urn:NETGEAR-ROUTER:service:{service}">
{params}</M1:{method}>
</SOAP-ENV:Body>
</SOAP-ENV:Envelope>
"""
NETGEAR_PORTS = [(5000, False), (80, False), (443, True), (5555, True)]


def _xml_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def parse_soap(text: str) -> Tuple[str, Dict[str, str]]:
    """→ (ResponseCode, {欄位: 文字})；去掉 namespace。"""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text or "")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return "", {}
    code, out = "", {}
    for el in root.iter():
        tag = el.tag.split("}", 1)[-1]
        if tag == "ResponseCode":
            code = (el.text or "").strip()
        elif len(el) == 0 and el.text is not None:
            out[tag] = el.text.strip()
    return code, out


def _dev(ip: str, name: str, mac: str, ctype: str = "", link: str = "", signal: str = "", ssid: str = "") -> Dict[str, Any]:
    name = (name or "").strip()
    if name.lower() in ("<unknown>", "unknown", "&lt;unknown&gt;", "--"):
        name = ""
    return {"ip": _s(ip, 64), "name": _s(name, 80), "mac": _s((mac or "").upper(), 32), "type": _s(ctype, 32),
            "link": _s(link, 16), "signal": _s(signal, 8), "ssid": _s(ssid, 64)}


def parse_attached2(text: str) -> Optional[List[Dict[str, Any]]]:
    """GetAttachDevice2 → [{ip, name, mac, type, link, signal, ssid}]；格式不對 → None（改用 GetAttachDevice）。"""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text or "")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    holder = next((e for e in root.iter() if e.tag.split("}", 1)[-1] == "NewAttachDevice"), None)
    if holder is None:
        return None
    out = []
    for d in holder:
        if d.tag.split("}", 1)[-1] != "Device":
            continue
        f = {c.tag.split("}", 1)[-1]: (c.text or "").strip() for c in d}
        out.append(_dev(f.get("IP", ""), f.get("Name", ""), f.get("MAC", ""), f.get("ConnectionType", ""),
                        f.get("Linkspeed", ""), f.get("SignalStrength", ""), f.get("SSID", "")))
    return out


def parse_attached1(s: str) -> List[Dict[str, Any]]:
    """GetAttachDevice：「數量@1;IP;名稱;MAC;wired;速度;訊號;Allow@2;…」。"""
    s = (s or "").replace("&lt;unknown&gt;", "<unknown>").strip()
    if not s or s == "0":
        return []
    parts = s.split("@")
    if len(parts) > 1:
        parts = parts[1:]
    out = []
    for e in parts:
        f = e.split(";")
        if len(f) < 4:
            continue
        out.append(_dev(f[1], f[2], f[3], f[4] if len(f) > 4 else "", f[5] if len(f) > 5 else "",
                        f[6] if len(f) > 6 else ""))
    return out


def meter_value(text: str) -> Optional[float]:
    """Traffic Meter 的值：'25,350.10' → 25350.1；'6.19/0.88'（總計 / 平均）→ 6.19；'--' → None。"""
    t = str(text or "").replace(",", "").strip()
    if not t or "--" in t or ":" in t:
        return None
    if "/" in t:
        t = t.split("/", 1)[0]
    try:
        return float(t)
    except ValueError:
        return None


class NetgearError(RuntimeError):
    pass


class NetgearClient:
    def __init__(self, host: str, user: str, password: str, port: int = 0, use_ssl: Optional[bool] = None,
                 timeout: float = 15.0, opener=None) -> None:
        self.host = host.strip()
        self.user = user or "admin"
        self.password = password or ""
        self.port = int(port or 0)
        self.ssl = use_ssl
        self.timeout = timeout
        self.cookie: Optional[str] = None
        self._open = opener or self._urlopen

    def _ctx(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False          # 路由器是自簽憑證，只連設定的區網位址
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _urlopen(self, url: str, data: bytes, headers: Dict[str, str]) -> Tuple[int, Dict[str, str], str]:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout,
                                        context=self._ctx() if url.startswith("https") else None) as r:
                return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read().decode("utf-8", "replace")

    def _url(self, port: int, use_ssl: bool) -> str:
        return f"{'https' if use_ssl else 'http'}://{self.host}:{port}/soap/server_sa/"

    def _post(self, port: int, use_ssl: bool, service: str, method: str, params: Dict[str, str],
              auth: bool = True) -> Tuple[int, Dict[str, str], str]:
        body = "".join(f"<{k}>{_xml_escape(v)}</{k}>\n" for k, v in params.items())
        msg = SOAP.format(method=method, service=service, params=body).encode("utf-8")
        headers = {"SOAPAction": f"urn:NETGEAR-ROUTER:service:{service}#{method}", "Cache-Control": "no-cache",
                   "User-Agent": "QEL-Hub", "Content-Type": "multipart/form-data"}
        if auth and self.cookie:
            headers["Cookie"] = self.cookie
        return self._open(self._url(port, use_ssl), msg, headers)

    def _candidates(self) -> List[Tuple[int, bool]]:
        if self.port:
            return [(self.port, bool(self.ssl) if self.ssl is not None else self.port in (443, 5555))]
        return list(NETGEAR_PORTS)

    def login(self) -> Tuple[int, bool]:
        errors = []
        for port, use_ssl in self._candidates():
            try:
                st, hd, text = self._post(port, use_ssl, "DeviceConfig:1", "SOAPLogin",
                                          {"Username": self.user, "Password": self.password}, auth=False)
            except (OSError, ValueError) as e:
                errors.append(f"{port}：{e}")
                continue
            code, _ = parse_soap(text)
            if st == 200 and code in ("0", "000", "0000") and hd.get("set-cookie"):
                self.cookie = hd["set-cookie"].split(";", 1)[0]
                self.port, self.ssl = port, use_ssl
                return port, use_ssl
            if st == 401 or code == "401":
                raise NetgearError("路由器拒絕登入：帳號或密碼不對")
            errors.append(f"{port}：HTTP {st} 代碼 {code or '—'}")
        raise NetgearError("連不到路由器的管理介面（" + "；".join(errors) + "）")

    def call(self, service: str, method: str, params: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        return self._call(service, method, params)[0]

    def _call(self, service: str, method: str, params: Optional[Dict[str, str]] = None) -> Tuple[Dict[str, str], str]:
        if not self.cookie:
            self.login()
        for attempt in (0, 1):
            st, _hd, text = self._post(self.port, bool(self.ssl), service, method, params or {})
            code, fields = parse_soap(text)
            if st == 401 or code == "401":
                if attempt == 0:
                    self.cookie = None
                    self.login()
                    continue
                raise NetgearError("路由器登入逾期，重新登入也失敗")
            if st != 200 or code not in ("0", "000", "0000", "1", "001"):
                raise NetgearError(f"{method} 失敗（HTTP {st}，代碼 {code or '—'}）")
            return fields, text
        return {}, ""

    def attached_devices(self) -> List[Dict[str, Any]]:
        """1.0.40：路由器上連著的裝置（有線 / 無線 / 訪客）——跟路由器管理頁「附加裝置」同一份。"""
        try:
            _f, text = self._call("DeviceInfo:1", "GetAttachDevice2")
            devs = parse_attached2(text)
            if devs is not None:
                return devs
        except NetgearError:
            pass
        f, _text = self._call("DeviceInfo:1", "GetAttachDevice")
        return parse_attached1(f.get("NewAttachDevice", ""))

    def traffic_meter(self) -> Dict[str, Optional[float]]:
        f = self.call("DeviceConfig:1", "GetTrafficMeterStatistics")
        return {k[3:]: meter_value(v) for k, v in f.items() if k.startswith("New") and "ConnectionTime" not in k}

    def info(self) -> Dict[str, str]:
        try:
            f = self.call("DeviceInfo:1", "GetInfo")
        except NetgearError:
            return {}
        return {k: f.get(k, "") for k in ("ModelName", "Firmwareversion", "DeviceName") if f.get(k)}


# ---------------------------------------------------------------------------
# SNMP v2c（只做 GET / GETNEXT）
# ---------------------------------------------------------------------------
def _ber_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag: int, val: bytes) -> bytes:
    return bytes([tag]) + _ber_len(len(val)) + val


def _int(v: int) -> bytes:
    n = max(1, (v.bit_length() + 8) // 8)
    return _tlv(0x02, v.to_bytes(n, "big", signed=True))


def _oid(oid: str) -> bytes:
    parts = [int(x) for x in oid.strip(".").split(".")]
    out = bytes([parts[0] * 40 + parts[1]])
    for p in parts[2:]:
        enc = [p & 0x7F]
        p >>= 7
        while p:
            enc.append(0x80 | (p & 0x7F))
            p >>= 7
        out += bytes(reversed(enc))
    return _tlv(0x06, out)


def _read(buf: bytes, i: int) -> Tuple[int, bytes, int]:
    tag = buf[i]
    ln = buf[i + 1]
    i += 2
    if ln & 0x80:
        k = ln & 0x7F
        ln = int.from_bytes(buf[i:i + k], "big")
        i += k
    return tag, buf[i:i + ln], i + ln


def _dec_oid(b: bytes) -> str:
    parts = [b[0] // 40, b[0] % 40]
    v = 0
    for c in b[1:]:
        v = (v << 7) | (c & 0x7F)
        if not c & 0x80:
            parts.append(v)
            v = 0
    return ".".join(str(p) for p in parts)


def snmp_request(oids: List[str], pdu: int = 0xA0, req_id: int = 1, community: str = "public") -> bytes:
    vbs = b"".join(_tlv(0x30, _oid(o) + b"\x05\x00") for o in oids)
    body = _int(req_id) + _int(0) + _int(0) + _tlv(0x30, vbs)
    return _tlv(0x30, _int(1) + _tlv(0x04, community.encode()) + _tlv(pdu, body))


def snmp_parse(buf: bytes) -> Tuple[int, int, List[Tuple[str, Any]]]:
    """→ (request id, error status, [(oid, 值)])；值：int / str / None（noSuchObject、endOfMibView）。"""
    _t, msg, _ = _read(buf, 0)
    i = 0
    _t, _ver, i = _read(msg, i)
    _t, _comm, i = _read(msg, i)
    _t, pdu, i = _read(msg, i)
    j = 0
    _t, rid, j = _read(pdu, j)
    _t, err, j = _read(pdu, j)
    _t, _eidx, j = _read(pdu, j)
    _t, vbl, j = _read(pdu, j)
    out = []
    k = 0
    while k < len(vbl):
        _t, vb, k = _read(vbl, k)
        _to, ob, m = _read(vb, 0)
        tag, val, _ = _read(vb, m)
        if tag in (0x02, 0x41, 0x42, 0x43, 0x46):
            v: Any = int.from_bytes(val, "big", signed=(tag == 0x02))
        elif tag == 0x04:
            v = val.decode("utf-8", "replace")
        elif tag == 0x06:
            v = _dec_oid(val)
        else:
            v = None
        out.append((_dec_oid(ob), v))
    return int.from_bytes(rid, "big", signed=True), int.from_bytes(err, "big"), out


class SnmpClient:
    IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
    IF_HC_IN = "1.3.6.1.2.1.31.1.1.1.6"
    IF_HC_OUT = "1.3.6.1.2.1.31.1.1.1.10"

    def __init__(self, host: str, community: str = "public", port: int = 161, timeout: float = 3.0,
                 transport=None) -> None:
        self.host, self.community, self.port, self.timeout = host.strip(), community or "public", int(port or 161), timeout
        self._send = transport or self._udp
        self._rid = int(time.time()) & 0x3FFFFFFF

    def _udp(self, data: bytes) -> bytes:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(self.timeout)
            s.sendto(data, (self.host, self.port))
            return s.recvfrom(65535)[0]

    def _req(self, oids: List[str], pdu: int) -> List[Tuple[str, Any]]:
        self._rid += 1
        last: Optional[Exception] = None
        for _ in range(2):
            try:
                rid, err, vbs = snmp_parse(self._send(snmp_request(oids, pdu, self._rid, self.community)))
            except socket.timeout as e:
                last = e
                continue
            if err:
                raise RuntimeError(f"SNMP 錯誤狀態 {err}")
            return vbs
        raise RuntimeError(f"SNMP 沒有回應（{self.host}:{self.port}；NAS 有打開 SNMP、社群名稱對嗎？）") from last

    def get(self, oids: List[str]) -> Dict[str, Any]:
        return dict(self._req(oids, 0xA0))

    def walk(self, base: str, limit: int = 200) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        cur = base
        for _ in range(limit):
            (oid, val), = self._req([cur], 0xA1)
            if val is None or not oid.startswith(base + "."):
                break
            out[oid] = val
            cur = oid
        return out

    def interfaces(self) -> Dict[int, str]:
        return {int(o.rsplit(".", 1)[1]): str(v) for o, v in self.walk(self.IF_NAME).items()}

    def counters(self, idx: List[int]) -> Dict[int, Tuple[int, int]]:
        oids = [f"{self.IF_HC_IN}.{i}" for i in idx] + [f"{self.IF_HC_OUT}.{i}" for i in idx]
        got = self.get(oids)
        out = {}
        for i in idx:
            rx, tx = got.get(f"{self.IF_HC_IN}.{i}"), got.get(f"{self.IF_HC_OUT}.{i}")
            if isinstance(rx, int) and isinstance(tx, int):
                out[i] = (rx, tx)
        return out


_FAMILIES = (r"ovs_bond\d+", r"bond\d+", r"ovs_eth\d+", r"eth\d+")


def pick_interfaces(names: Dict[int, str], wanted: Optional[List[str]] = None) -> Dict[int, str]:
    """要加總的介面：有指定就照指定；否則自動挑實體網路（bond > ovs_eth > eth，同一層全部加總，避免重複計算）。"""
    if wanted:
        w = {x.strip() for x in wanted if x.strip()}
        return {i: n for i, n in names.items() if n in w}
    for fam in _FAMILIES:
        got = {i: n for i, n in names.items() if re.fullmatch(fam, n)}
        if got:
            return got
    return {}


# ---------------------------------------------------------------------------
# 用量資料
# ---------------------------------------------------------------------------
def _add(days: Dict[str, Dict[str, int]], day: str, up: int, down: int) -> None:
    d = days.setdefault(day, {"up": 0, "down": 0})
    d["up"] = int(d.get("up", 0)) + int(up)
    d["down"] = int(d.get("down", 0)) + int(down)


def _trim(days: Dict[str, Any], today: str) -> None:
    try:
        cut = (_dt.date.fromisoformat(today) - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    except ValueError:
        return
    for k in [k for k in days if k < cut]:
        days.pop(k, None)


def _private_ip(host: str) -> bool:
    import ipaddress

    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip.is_private and not str(ip).startswith("100.")      # 100.64/10（CGNAT / Tailscale）不算區網


def ts_path(peer: Dict[str, Any]) -> str:
    """這個 VPN 裝置目前跟 NAS 的連線路徑：lan（區網直連）、wan（經外網直連）、relay（DERP 中繼，經外網）。"""
    cur = str(peer.get("CurAddr") or "")
    if cur:
        host = cur.rsplit(":", 1)[0] if cur.count(":") == 1 or cur.startswith("[") else cur
        return "lan" if _private_ip(host) else "wan"
    return "relay"


def ts_socket() -> str:
    env = os.environ.get("LABHUB_TAILSCALE_SOCKET", "")
    for p in ([env] if env else list(TS_SOCKETS)):
        if p and os.path.exists(p):
            return p
    return ""


def ts_status(sock: str) -> Dict[str, Any]:
    from .lan_tls import local_api

    return json.loads(local_api(sock, "/localapi/v0/status", timeout=15).decode("utf-8"))


class NetUsage:
    def __init__(self, data_dir: Path, now=time.time) -> None:
        self.file = Path(data_dir) / "netusage.json"
        self.secret_file = Path(data_dir) / "netusage_secret.json"
        self.lock = threading.RLock()
        self.now = now
        d = self._load(self.file)
        self.cfg: Dict[str, Any] = {"tz": "Asia/Taipei",
                                    "router": {"enabled": False, "host": "", "user": "admin", "port": 0},
                                    "nas": {"enabled": False, "host": "", "community": "public", "port": 161,
                                            "interfaces": []}}
        for k, v in (d.get("cfg") or {}).items():
            if isinstance(v, dict) and isinstance(self.cfg.get(k), dict):
                self.cfg[k].update(v)
            else:
                self.cfg[k] = v
        self.router: Dict[str, Any] = dict(d.get("router") or {})
        self.router.setdefault("days", {})
        self.nas: Dict[str, Any] = dict(d.get("nas") or {})
        self.nas.setdefault("days", {})
        # 1.0.30：流量監看程式回報的裝置（key = 程式產生的裝置 id）；1.0.29 由 Lab Control 回報的舊資料（pcs）不再使用
        self.pcs: Dict[str, Dict[str, Any]] = {k: v for k, v in (d.get("devices") or {}).items() if isinstance(v, dict)}
        # 1.0.32：Tailscale（days[日期][路徑] = {up, down}；peers[節點 id] = {name, os, ip, path, days, last}）
        self.ts: Dict[str, Any] = dict(d.get("tailscale") or {})
        self.ts.setdefault("days", {})
        self.ts.setdefault("peers", {})
        # 1.0.32：Hub 自己從 GitHub 下載的量（days[日期] = {down, files}）
        self.github: Dict[str, Any] = dict(d.get("github") or {})
        self.github.setdefault("days", {})
        # 1.0.37：NAS 分流（days[日期] = {"lan": {up, down}, "vpn": {...}, "wan": {...}}；up = NAS 送出）
        self.nasdir: Dict[str, Any] = dict(d.get("nasdir") or {})
        self.nasdir.setdefault("days", {})
        # 1.0.40：路由器上連過的裝置（key = MAC 或 IP）：{ip, name, mac, type, link, signal, ssid, first, last}
        self.rdev: Dict[str, Dict[str, Any]] = {k: v for k, v in (d.get("router_devices") or {}).items()
                                                if isinstance(v, dict)}
        self._rdev_online: set = set()
        self._acct_last: Optional[Dict[str, Any]] = None
        # 1.0.41：NAS 上誰在用網路（記憶體裡留 1 小時）
        import collections

        self._top_last: Optional[Dict[str, Any]] = None
        self._top_status: Dict[str, Any] = {}
        self._top_rates: Dict[str, Any] = {}
        self._ns_info: Dict[str, Dict[str, Any]] = {}
        self._flows: Any = collections.deque(maxlen=60000)
        self._rdns_cache: Dict[str, str] = {}
        self._hosts_written: Optional[List[str]] = None
        self.status: Dict[str, Dict[str, Any]] = {"router": {}, "nas": {}, "tailscale": {}, "netacct": {}}
        from .netrecent import Recent                       # 1.0.34：每分鐘一格（即時）

        self.recent = Recent(Path(data_dir) / "netusage_recent.json", now=now)
        self._router_last: Optional[Tuple[str, int, int, float]] = None
        self._dirty = False
        self._last_save = 0.0
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []

    # ---- 保存 ----
    @staticmethod
    def _load(p: Path) -> Dict[str, Any]:
        try:
            return json.loads(p.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return {}

    def _write(self, p: Path, obj: Dict[str, Any], private: bool = False) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        if private:
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        tmp.replace(p)

    def save(self, force: bool = False) -> None:
        self.recent.save(force)
        with self.lock:
            if not (self._dirty or force):
                return
            if not force and self.now() - self._last_save < 30:
                return
            try:
                self._write(self.file, {"cfg": self.cfg, "router": self.router, "nas": self.nas, "devices": self.pcs,
                                        "tailscale": self.ts, "github": self.github, "nasdir": self.nasdir,
                                        "router_devices": self.rdev})
                self._dirty = False
                self._last_save = self.now()
            except OSError as e:
                log.warning("存網路用量失敗：%s", e)

    def _password(self) -> str:
        return str(self._load(self.secret_file).get("router_password") or "")

    @property
    def tz(self):
        return _tz(str(self.cfg.get("tz") or "Asia/Taipei"))

    def today(self) -> str:
        return day_of(self.now(), self.tz)

    # ---- 設定 ----
    def set_config(self, body: Dict[str, Any]) -> None:
        with self.lock:
            r = body.get("router") if isinstance(body.get("router"), dict) else None
            if r is not None:
                rc = self.cfg["router"]
                rc["enabled"] = bool(r.get("enabled", rc.get("enabled")))
                rc["host"] = _s(r.get("host", rc.get("host")), 120).strip()
                rc["user"] = _s(r.get("user", rc.get("user")) or "admin", 60).strip()
                try:
                    rc["port"] = max(0, min(65535, int(r.get("port", rc.get("port")) or 0)))
                except (TypeError, ValueError):
                    rc["port"] = 0
                if r.get("password"):
                    self._write(self.secret_file, {"router_password": _s(r["password"], 200)}, private=True)
                self.status["router"] = {}
            n = body.get("nas") if isinstance(body.get("nas"), dict) else None
            if n is not None:
                nc = self.cfg["nas"]
                nc["enabled"] = bool(n.get("enabled", nc.get("enabled")))
                nc["host"] = _s(n.get("host", nc.get("host")), 120).strip()
                nc["community"] = _s(n.get("community", nc.get("community")) or "public", 60)
                try:
                    nc["port"] = max(1, min(65535, int(n.get("port", nc.get("port")) or 161)))
                except (TypeError, ValueError):
                    nc["port"] = 161
                ifs = n.get("interfaces", nc.get("interfaces"))
                if isinstance(ifs, str):
                    ifs = [x for x in re.split(r"[,\s]+", ifs) if x]
                nc["interfaces"] = [_s(x, 40) for x in (ifs or [])][:16]
                self.nas.pop("last", None)             # 介面改了 → 重新取基準
                self.status["nas"] = {}
            if body.get("tz"):
                self.cfg["tz"] = _s(body["tz"], 60)
            self._dirty = True
        self.save(force=True)

    def public_config(self) -> Dict[str, Any]:
        c = json.loads(json.dumps(self.cfg))
        c["router"]["has_password"] = bool(self._password())
        return c

    # ---- 路由器 ----
    def _poll_router_devices(self, cl: Any) -> None:
        """1.0.40：順便讀路由器的「附加裝置」（讀不到不影響流量）。"""
        fn = getattr(cl, "attached_devices", None)
        if fn is None:
            return
        try:
            devs = fn()
        except Exception as e:  # noqa: BLE001
            self.status["router_devices"] = {"ok": False, "error": str(e), "time": self.now()}
            return
        now = self.now()
        with self.lock:
            online = set()
            for x in devs:
                key = x.get("mac") or x.get("ip")
                if not key:
                    continue
                r = self.rdev.setdefault(key, {"first": now})
                r.update({k: v for k, v in x.items() if v or k not in r}, last=now)
                online.add(key)
            self._rdev_online = online
            for k in [k for k, r in self.rdev.items() if now - float(r.get("last") or 0) > RDEV_KEEP_S]:
                del self.rdev[k]
            if len(self.rdev) > 500:
                for k in sorted(self.rdev, key=lambda k: float(self.rdev[k].get("last") or 0))[:len(self.rdev) - 500]:
                    del self.rdev[k]
            self._dirty = True
        self.status["router_devices"] = {"ok": True, "time": now, "n": len(online)}

    def _rdev_view(self, now: float) -> Dict[str, Any]:
        """路由器上的裝置＋哪些有被監看（NAS、流量監看程式）。"""
        nas_ip = str(self.cfg["nas"].get("host") or "")
        pc_ip: Dict[str, str] = {}
        pc_name: Dict[str, str] = {}
        for pc in self.pcs.values():
            label = pc.get("label") or pc.get("host") or ""
            for ip in re.split(r"[,\s]+", str(pc.get("ip") or "")):
                if ip:
                    pc_ip[ip] = label
            if pc.get("host"):
                pc_name[str(pc["host"]).lower().rstrip("$")] = label
        out = []
        for key, r in self.rdev.items():
            watched = ""
            if r.get("ip") and r["ip"] == nas_ip:
                watched = "NAS"
            elif r.get("ip") in pc_ip:
                watched = f"流量監看：{pc_ip[r['ip']]}"
            elif (r.get("name") or "").lower() in pc_name:
                watched = f"流量監看：{pc_name[r['name'].lower()]}"
            out.append(dict(r, key=key, online=key in self._rdev_online, watched=watched))
        out.sort(key=lambda r: (not r["online"], bool(r["watched"]), -(float(r.get("last") or 0))))
        return {"devices": out, "status": self.status.get("router_devices") or {}}

    def poll_router(self, client: Optional[NetgearClient] = None) -> Dict[str, Any]:
        rc = self.cfg["router"]
        st: Dict[str, Any] = {"time": self.now()}
        try:
            if not rc.get("host"):
                raise NetgearError("還沒設定路由器位址")
            cl = client or NetgearClient(rc["host"], rc.get("user") or "admin", self._password(), int(rc.get("port") or 0))
            tm = cl.traffic_meter()
            if tm.get("TodayUpload") is None and tm.get("TodayDownload") is None:
                raise NetgearError("路由器的 Traffic Meter 沒有資料：請在路由器管理頁「進階 → 進階設定 → 流量計量器」打開")
            info = getattr(self, "_router_info", None)
            if info is None:
                info = self._router_info = cl.info()
            today = self.today()
            yday = (_dt.date.fromisoformat(today) - _dt.timedelta(days=1)).isoformat()
            with self.lock:
                # 1.0.35：每日量改成自己累加差值（台北時間的日期）。路由器的「Today」在它自己的時鐘換日才歸零，
                # 時區 / 時間不對時，以前會在我們換日那一刻把它前一天的累計整筆算進來（一根假的尖峰）。
                days = self.router["days"]
                cu = int((tm.get("TodayUpload") or 0) * MB)
                cd = int((tm.get("TodayDownload") or 0) * MB)
                now = self.now()
                last = self._router_last
                if last and now - last[3] <= GAP_S:
                    du = cu - last[1] if cu >= last[1] else cu         # 變小 = 路由器歸零了（它自己的換日）
                    dd = cd - last[2] if cd >= last[2] else cd
                    if du <= MAX_DELTA and dd <= MAX_DELTA:
                        _add(days, today, du, dd)
                        self.recent.add("router", du, dd, now, now - last[3])
                elif today not in days:                               # Hub 剛啟動：先用路由器的今天當起點
                    days[today] = {"up": cu, "down": cd}
                self._router_last = (today, cu, cd, now)
                self.router["raw_today"] = {"up": cu, "down": cd, "time": now}
                if (tm.get("YesterdayUpload") is not None or tm.get("YesterdayDownload") is not None) and yday not in days:
                    days[yday] = {"up": int((tm.get("YesterdayUpload") or 0) * MB),
                                  "down": int((tm.get("YesterdayDownload") or 0) * MB)}
                self.router["month"] = {"up": int((tm.get("MonthUpload") or 0) * MB),
                                        "down": int((tm.get("MonthDownload") or 0) * MB)}
                self.router["last_month"] = {"up": int((tm.get("LastMonthUpload") or 0) * MB),
                                             "down": int((tm.get("LastMonthDownload") or 0) * MB)}
                _trim(days, today)
                self._dirty = True
            st.update(ok=True, info=info, port=cl.port, ssl=cl.ssl)
            self._poll_router_devices(cl)
        except Exception as e:  # noqa: BLE001
            st.update(ok=False, error=str(e))
        self.status["router"] = st
        self.save()
        return st

    # ---- NAS ----
    def poll_nas(self, client: Optional[SnmpClient] = None) -> Dict[str, Any]:
        nc = self.cfg["nas"]
        st: Dict[str, Any] = {"time": self.now()}
        try:
            if not nc.get("host"):
                raise RuntimeError("還沒設定 NAS 位址")
            cl = client or SnmpClient(nc["host"], nc.get("community") or "public", int(nc.get("port") or 161))
            names = self.nas.get("ifnames")
            if not names or not self.nas.get("last"):
                names = {str(k): v for k, v in cl.interfaces().items()}
                self.nas["ifnames"] = names
            pick = pick_interfaces({int(k): v for k, v in names.items()}, nc.get("interfaces") or None)
            if not pick:
                raise RuntimeError("找不到要統計的網路介面（NAS 回報：" + "、".join(sorted(names.values())) + "）")
            cnt = cl.counters(sorted(pick))
            rx = sum(v[0] for v in cnt.values())
            tx = sum(v[1] for v in cnt.values())
            with self.lock:
                last = self.nas.get("last")
                if last and last.get("ifs") == sorted(pick) and self.now() - float(last.get("t") or 0) <= GAP_S:
                    drx, dtx = rx - int(last["rx"]), tx - int(last["tx"])
                    if drx < 0:                        # NAS 重開機 / 計數歸零 → 從 0 接著算
                        drx = rx
                    if dtx < 0:
                        dtx = tx
                    if drx <= MAX_DELTA and dtx <= MAX_DELTA:
                        _add(self.nas["days"], self.today(), dtx, drx)
                        self.recent.add("nas", dtx, drx, self.now(), self.now() - float(last.get("t") or 0))
                self.nas["last"] = {"rx": rx, "tx": tx, "t": self.now(), "ifs": sorted(pick)}
                _trim(self.nas["days"], self.today())
                self._dirty = True
            st.update(ok=True, interfaces=[pick[i] for i in sorted(pick)], all=sorted(names.values()))
        except Exception as e:  # noqa: BLE001
            st.update(ok=False, error=str(e))
        self.status["nas"] = st
        self.save()
        return st

    # ---- Tailscale（VPN）----
    def poll_tailscale(self, status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        st: Dict[str, Any] = {"time": self.now()}
        try:
            if status is None:
                sock = ts_socket()
                if not sock:
                    raise RuntimeError("找不到 tailscaled 的 socket（docker-compose.yml 要掛載 /var/packages/Tailscale/var:/tailscale:ro）")
                status = ts_status(sock)
            me = status.get("Self") or {}
            allowed = [str(x) for x in (me.get("AllowedIPs") or [])]
            exit_node = bool(me.get("ExitNodeOption")) or any(x in ("0.0.0.0/0", "::/0") for x in allowed)
            routes = [str(x) for x in (me.get("PrimaryRoutes") or []) if str(x) not in ("0.0.0.0/0", "::/0")]
            today = self.today()
            now = self.now()
            n_on = {"lan": 0, "wan": 0, "relay": 0}
            with self.lock:
                peers = self.ts["peers"]
                for key, p in (status.get("Peer") or {}).items():
                    pid = str(p.get("ID") or key)[:80]
                    rx, tx = int(p.get("RxBytes") or 0), int(p.get("TxBytes") or 0)
                    path = ts_path(p)
                    rec = peers.setdefault(pid, {"days": {}})
                    last = rec.get("last")
                    if last and now - float(last.get("t") or 0) <= GAP_S:
                        drx, dtx = rx - int(last["rx"]), tx - int(last["tx"])
                        if drx < 0:                                # tailscaled 重新啟動 / 重新連線 → 計數歸零
                            drx = rx
                        if dtx < 0:
                            dtx = tx
                        if drx <= MAX_DELTA and dtx <= MAX_DELTA:
                            self.recent.add(f"ts:{pid}", dtx, drx, now, now - float(last.get("t") or 0))
                        if (drx or dtx) and drx <= MAX_DELTA and dtx <= MAX_DELTA:
                            # 用上一次讀到的路徑（這段時間大多走那條）；路徑剛換的話用現在的
                            use = last.get("path") if last.get("path") in TS_PATHS else path
                            day = self.ts["days"].setdefault(today, {})
                            _add(day, use, dtx, drx)
                            self.recent.add(f"tspath:{use}", dtx, drx, now)          # 1.0.37：NAS 分流扣掉中繼
                            _add(rec["days"], today, dtx, drx)
                            pd = rec.setdefault("paths", {}).setdefault(today, {})
                            pd[use] = int(pd.get(use, 0)) + drx + dtx
                    rec["last"] = {"rx": rx, "tx": tx, "t": now, "path": path}
                    ips = p.get("TailscaleIPs") or []
                    rec.update(name=_s(p.get("HostName") or (p.get("DNSName") or "").split(".")[0] or pid, 80),
                               os=_s(p.get("OS"), 30), ip=_s(ips[0] if ips else "", 60), path=path,
                               relay=_s(p.get("Relay"), 20), cur=_s(p.get("CurAddr"), 60),
                               online=bool(p.get("Online")), exit_node_user=bool(p.get("ExitNode")))
                    if p.get("Online"):
                        rec["seen"] = now
                        n_on[path] += 1
                    _trim(rec["days"], today)
                    for k in [k for k in rec.get("paths", {}) if k < min(rec["days"] or {today: 0})]:
                        rec["paths"].pop(k, None)
                _trim(self.ts["days"], today)
                self._dirty = True
            st.update(ok=True, version=_s(status.get("Version"), 40), exit_node=exit_node, routes=routes,
                      name=_s(me.get("HostName"), 80), online=n_on,
                      using_exit=bool((status.get("ExitNodeStatus") or {}).get("Online")))
        except Exception as e:  # noqa: BLE001
            st.update(ok=False, error=str(e))
        self.status["tailscale"] = st
        self.save()
        return st

    # ---- Hub 從 GitHub 下載 ----
    def add_github(self, nbytes: int, files: int = 0) -> None:
        with self.lock:
            day = self.github["days"].setdefault(self.today(), {"up": 0, "down": 0, "files": 0})
            day["down"] = int(day.get("down", 0)) + max(0, int(nbytes))
            day["files"] = int(day.get("files", 0)) + int(files)
            _trim(self.github["days"], self.today())
            self._dirty = True
        self.save()

    # ---- 1.0.37：NAS 分流（記帳容器寫的 data/netacct.json）----
    def _pc_hosts(self) -> List[str]:
        ips: List[str] = []
        with self.lock:
            for pc in self.pcs.values():
                for ip in str(pc.get("ip") or "").replace(";", ",").split(","):
                    ip = ip.strip()
                    if ip and ip not in ips:
                        ips.append(ip)
        return sorted(ips)[:64]

    def poll_netacct(self, st_file: Optional[Path] = None) -> Dict[str, Any]:
        data_dir = self.file.parent
        hosts = self._pc_hosts()
        if hosts != self._hosts_written:                     # 告訴記帳容器要分開記哪些電腦
            try:
                self._write(data_dir / "netacct_hosts.json", {"hosts": hosts})
                self._hosts_written = hosts
            except OSError:
                pass
        p = st_file or (data_dir / "netacct.json")
        now = self.now()
        try:
            a = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st = {"time": now, "ok": False, "error": "還沒有記帳資料：Hub 控制代理（1.0.37 起）會自動建立 labcontrol-netacct 容器"}
            self.status["netacct"] = st
            return st
        t = float(a.get("t") or 0)
        if not a.get("ok"):
            st = {"time": now, "ok": False, "error": str(a.get("error") or "記帳容器回報錯誤")}
        elif now - t > 120:
            st = {"time": now, "ok": False, "error": f"記帳容器 {int(now - t)} 秒沒有更新（labcontrol-netacct 有在跑嗎？）"}
        else:
            st = {"time": now, "ok": True, "mode": a.get("mode"), "ifaces": a.get("ifaces"), "tun": a.get("tun"),
                  "lan": a.get("lan"), "hosts": a.get("hosts")}
            self._acct_step(a, t)
        self.status["netacct"] = st
        try:
            self._top_step(data_dir / "netacct_top.json")
        except Exception:  # noqa: BLE001
            log.exception("NAS 連線")
        self.save()
        return st

    # ---- 1.0.41：NAS 上誰在用網路（記帳容器寫的 data/netacct_top.json）----
    def _top_step(self, p: Path) -> None:
        try:
            a = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        t = float(a.get("t") or 0)
        last = self._top_last
        if last and t <= last["t"]:
            return
        now = self.now()
        self._top_last = {"t": t, "ifaces": a.get("ifaces") or {},
                          "netns": {self._ns_key(x): x for x in a.get("netns") or []}}
        self._top_status = {"time": t, "conntrack": a.get("conntrack") or {}}
        if not last or t - last["t"] > 300:
            return
        dt_ = t - last["t"]
        rates: Dict[str, Any] = {}
        for name, v in (a.get("ifaces") or {}).items():
            o = last["ifaces"].get(name)
            if not o:
                continue
            drx, dtx = int(v[0]) - int(o[0]), int(v[1]) - int(o[1])
            if drx < 0 or dtx < 0 or drx > MAX_DELTA or dtx > MAX_DELTA:
                continue
            rates[name] = {"rx": drx / dt_, "tx": dtx / dt_}
            if drx or dtx:
                self.recent.add(f"nif:{name}", dtx, drx, now, dt_)
        self._top_rates = rates
        for k, x in self._top_last["netns"].items():
            o = last["netns"].get(k)
            if not o:
                continue
            drx, dtx = int(x.get("rx", 0)) - int(o.get("rx", 0)), int(x.get("tx", 0)) - int(o.get("tx", 0))
            if 0 <= drx <= MAX_DELTA and 0 <= dtx <= MAX_DELTA and (drx or dtx):
                self.recent.add(f"nns:{k}", dtx, drx, now, dt_)          # 容器的「送出」= 它網卡的 tx
            self._ns_info[k] = {"comm": x.get("comm", ""), "cmd": x.get("cmd", ""),
                                "container": x.get("container", ""), "ips": x.get("ips") or []}
        with self.lock:
            for f in a.get("flows") or []:
                self._flows.append((now, str(f.get("proc") or ""), str(f.get("container") or ""),
                                    str(f.get("cmd") or "")[:120], str(f.get("proto") or ""),
                                    str(f.get("remote") or ""), int(f.get("rport") or 0),
                                    str(f.get("local") or ""), int(f.get("lport") or 0),
                                    int(f.get("rx") or 0), int(f.get("tx") or 0), bool(f.get("out"))))
            cut = now - 3600
            while self._flows and self._flows[0][0] < cut:
                self._flows.popleft()

    @staticmethod
    def _ns_key(x: Dict[str, Any]) -> str:
        return str(x.get("container") or f"{x.get('comm', '')}-{x.get('pid', '')}")

    def _rdns(self, ip: str) -> str:
        """外網位址的反查名稱（背景查、快取）。"""
        c = self._rdns_cache.get(ip)
        if c is not None:
            return c
        self._rdns_cache[ip] = ""
        if len(self._rdns_cache) > 2000:
            self._rdns_cache.clear()

        def look() -> None:
            import socket as _sk

            try:
                self._rdns_cache[ip] = _sk.gethostbyaddr(ip)[0][:80]
            except (OSError, UnicodeError):
                pass
        threading.Thread(target=look, daemon=True).start()
        return ""

    def nastop_view(self, window: int = 600) -> Dict[str, Any]:
        """NAS 的網卡、容器、程式、對方（window 秒內）。"""
        from .netrecent import WINDOWS

        window = min(WINDOWS, key=lambda w: abs(w - int(window or 600)))
        window = min(window, 3600)
        now = self.now()
        lan = [ipaddress.ip_network(x, strict=False) for x in self._lan_list()]
        vpn = ipaddress.ip_network("100.64.0.0/10")

        def where(ip: str) -> str:
            try:
                a = ipaddress.ip_address(ip)
            except ValueError:
                return "?"
            if a.version == 4 and a in vpn:
                return "vpn"
            if a.is_loopback or a.is_link_local or any(a.version == n.version and a in n for n in lan):
                return "lan"
            if a.is_private:
                return "local"
            return "wan"
        with self.lock:
            fl = [f for f in self._flows if f[0] >= now - window]
        procs: Dict[str, Dict[str, Any]] = {}
        rem: Dict[Tuple[str, str, int, str], Dict[str, Any]] = {}
        for (_t, proc, cont, cmd, proto, remote, rport, local, lport, rx, tx, out_) in fl:
            pk = f"容器 {cont}" if cont else (proc or "（對不到程式）")
            p = procs.setdefault(pk, {"name": pk, "proc": proc, "container": cont, "cmd": cmd, "rx": 0, "tx": 0,
                                      "wan_rx": 0, "wan_tx": 0, "n": set()})
            p["rx"] += rx
            p["tx"] += tx
            w = where(remote)
            if w == "wan":
                p["wan_rx"] += rx
                p["wan_tx"] += tx
            p["n"].add((remote, rport))
            port = rport if out_ else lport
            r = rem.setdefault((remote, proto, port, pk), {"remote": remote, "proto": proto, "port": port,
                                                           "dir": "NAS 連出" if out_ else "連進 NAS",
                                                           "proc": pk, "where": w, "rx": 0, "tx": 0})
            r["rx"] += rx
            r["tx"] += tx
        plist = sorted(procs.values(), key=lambda x: -(x["rx"] + x["tx"]))[:30]
        for p in plist:
            p["conns"] = len(p.pop("n"))
        rlist = sorted(rem.values(), key=lambda x: -(x["rx"] + x["tx"]))[:40]
        for r in rlist:
            if r["where"] == "wan":
                r["host"] = self._rdns(r["remote"])
        ifs = []
        for k in self.recent.keys():
            if k.startswith("nif:"):
                w = self.recent.window(k, window, now=now)
                name = k[4:]
                ifs.append({"name": name, "rx": w["down"], "tx": w["up"],
                            "rate": (self._top_rates or {}).get(name)})
        ifs.sort(key=lambda x: -(x["rx"] + x["tx"]))
        cons = []
        for k in self.recent.keys():
            if k.startswith("nns:"):
                w = self.recent.window(k, window, now=now)
                if w["up"] or w["down"]:
                    cons.append(dict(self._ns_info.get(k[4:]) or {}, key=k[4:], rx=w["down"], tx=w["up"]))
        cons.sort(key=lambda x: -(x["rx"] + x["tx"]))
        st = dict(self._top_status or {})
        if not st:
            st = {"error": "還沒有資料：Hub 1.0.41 更新後，記帳容器（labcontrol-netacct）會重新啟動並開始讀"}
        elif now - float(st.get("time") or 0) > 120:
            st["error"] = f"記帳容器 {int(now - float(st.get('time') or 0))} 秒沒有更新"
        return {"window": window, "ifaces": ifs, "containers": cons[:20], "procs": plist, "remotes": rlist,
                "status": st}

    def _lan_list(self) -> List[str]:
        try:
            a = json.loads((self.file.parent / "netacct.json").read_text(encoding="utf-8"))
            if a.get("lan"):
                return list(a["lan"])
        except (OSError, ValueError):
            pass
        return ["192.168.50.0/24"]

    def _acct_step(self, a: Dict[str, Any], t: float) -> None:
        cur: Dict[str, int] = {}
        for v in ("v4", "v6"):
            for io in ("in", "out"):
                for k, n in ((a.get(v) or {}).get(io) or {}).items():
                    key = f"{io}:{k}"
                    cur[key] = cur.get(key, 0) + int(n or 0)
        last = self._acct_last
        self._acct_last = {"t": t, "c": cur}
        if not last or t <= float(last["t"]) or t - float(last["t"]) > GAP_S:
            return
        prev = last["c"]

        def d(key: str) -> int:
            x, y = cur.get(key, 0), prev.get(key)
            if y is None:
                return 0
            v = x - y if x >= y else x                     # 規則重建（計數歸零）→ 從 0 算
            return v if 0 <= v <= MAX_DELTA else 0
        now = self.now()
        dt_ = t - float(last["t"])
        today = self.today()
        out: Dict[str, Tuple[int, int]] = {}
        for io, idx in (("out", 0), ("in", 1)):
            lan, wgl, wg, al = d(f"{io}:lan"), d(f"{io}:wg_lan"), d(f"{io}:wg"), d(f"{io}:all")
            vals = {"lan": max(0, lan - wgl), "vpn": wg, "wan": max(0, al - lan - (wg - wgl))}
            for k, v in vals.items():
                o = out.get(k, (0, 0))
                out[k] = (o[0] + v, o[1]) if idx == 0 else (o[0], o[1] + v)
        with self.lock:
            day = self.nasdir["days"].setdefault(today, {})
            for k, (up, down) in out.items():
                _add(day, k, up, down)
                self.recent.add(f"nasdir:{k}", up, down, now, dt_)
            ip2pc = {}
            for did, pc in self.pcs.items():
                for ip in str(pc.get("ip") or "").replace(";", ",").split(","):
                    if ip.strip():
                        ip2pc[ip.strip()] = did
            for key in cur:
                if not key.startswith("out:h:"):
                    continue
                ip = key[6:]
                up, down = d(f"out:h:{ip}"), d(f"in:h:{ip}")    # NAS 送給電腦 = 電腦下載
                did = ip2pc.get(ip)
                if did and did in self.pcs and (up or down):
                    pc = self.pcs[did]
                    _add(pc.setdefault("nas_days", {}), today, down, up)     # 電腦的角度：up = 電腦上傳到 NAS
                    self.recent.add(f"pcnas:{did}", down, up, now, dt_)
                    _trim(pc["nas_days"], today)
            _trim(self.nasdir["days"], today)
            self._dirty = True

    # ---- 各台裝置（流量監看程式回報）----
    def report_device(self, b: Dict[str, Any], ip: str = "") -> Dict[str, Any]:
        did = str(b.get("id") or "")
        if not _ID.match(did):
            raise ValueError("裝置 id 格式錯誤")
        days_in = b.get("days") if isinstance(b.get("days"), dict) else {}
        today = _dt.date.fromisoformat(self.today())
        lo, hi = (today - _dt.timedelta(days=KEEP_DAYS)).isoformat(), (today + _dt.timedelta(days=1)).isoformat()
        clean: Dict[str, Dict[str, int]] = {}
        for k, v in list(days_in.items())[:62]:
            if not isinstance(k, str) or not _DATE.match(k) or not (lo <= k <= hi) or not isinstance(v, dict):
                continue
            try:
                up, down = int(v.get("up") or 0), int(v.get("down") or 0)
            except (TypeError, ValueError):
                continue
            if 0 <= up <= MAX_DAY and 0 <= down <= MAX_DAY:
                clean[k] = {"up": up, "down": down}
                try:                                     # 1.0.37（流量監看 1.0.2）：其中經 Tailscale 的
                    vu, vd = int(v.get("vpn_up") or 0), int(v.get("vpn_down") or 0)
                except (TypeError, ValueError):
                    vu = vd = 0
                if 0 < vu + vd and vu <= MAX_DAY and vd <= MAX_DAY:
                    clean[k].update(vpn_up=vu, vpn_down=vd)
                for kk in SPLIT_KEYS:                    # 1.0.38（流量監看 1.0.4）：內網 / 外網 / VPN 通道（其中經外網）
                    try:
                        n = int(v.get(kk) or 0)
                    except (TypeError, ValueError):
                        continue
                    if 0 < n <= MAX_DAY:
                        clean[k][kk] = n
        rate = b.get("rate") if isinstance(b.get("rate"), dict) else {}
        now = self.now()
        with self.lock:
            if did not in self.pcs and len(self.pcs) >= MAX_DEVICES:
                raise ValueError("裝置太多")
            dev = self.pcs.setdefault(did, {"days": {}})
            tk = today.isoformat()
            old, new = dev["days"].get(tk), clean.get(tk)
            if old and new and now - float(dev.get("seen") or 0) <= GAP_S:      # 1.0.34：這一分鐘傳了多少（即時頁）
                du, dd = int(new["up"]) - int(old["up"]), int(new["down"]) - int(old["down"])
                if 0 <= du <= MAX_DELTA and 0 <= dd <= MAX_DELTA:
                    self.recent.add(f"pc:{did}", du, dd, now)
                    for c in SPLIT_CATS:
                        su = int(new.get(f"{c}_up", 0)) - int(old.get(f"{c}_up", 0))
                        sd = int(new.get(f"{c}_down", 0)) - int(old.get(f"{c}_down", 0))
                        if (su or sd) and 0 <= su <= MAX_DELTA and 0 <= sd <= MAX_DELTA:
                            self.recent.add(f"pcx:{did}:{c}", su, sd, now)
            elif new and not old and dev.get("seen"):                           # 換日後第一次回報
                self.recent.add(f"pc:{did}", min(int(new["up"]), MAX_DELTA), min(int(new["down"]), MAX_DELTA), now)
            dev["days"].update(clean)                  # 程式自己累計：同一天以最新的數字為準
            ifs = b.get("ifaces") if isinstance(b.get("ifaces"), list) else []
            try:
                r = {"up": max(0.0, float(rate.get("up") or 0)), "down": max(0.0, float(rate.get("down") or 0))}
            except (TypeError, ValueError):
                r = {"up": 0.0, "down": 0.0}
            dev.update(seen=now, src_ip=_s(ip, 60), on_net=bool(b.get("on_net", True)))
            if rate:
                dev["rate"] = r
                self.recent.set_rate(f"pc:{did}", r["up"], r["down"], now)
            for k, n in (("ip", 60), ("host", 80), ("user", 60), ("os", 60), ("version", 20), ("subnet", 40)):
                if b.get(k):
                    dev[k] = _s(b[k], n)
            if ifs:
                dev["ifaces"] = [_s(x, 40) for x in ifs][:8]
            _trim(dev["days"], today.isoformat())
            self._dirty = True
        self.save()
        return {"ok": True, "time": now, "today": today.isoformat()}

    def set_label(self, host: str, label: str) -> None:
        with self.lock:
            pc = self.pcs.get(host)
            if pc is None:
                raise KeyError(host)
            pc["label"] = _s(label, 40).strip()
            self._dirty = True
        self.save(force=True)

    def remove_pc(self, host: str) -> None:
        with self.lock:
            self.pcs.pop(host, None)
            self.recent.forget(f"pc:{host}")
            for c in SPLIT_CATS:
                self.recent.forget(f"pcx:{host}:{c}")
            self._dirty = True
        self.save(force=True)

    # ---- 檢視 ----
    def view(self, days: int = 31) -> Dict[str, Any]:
        days = max(1, min(int(days or 31), KEEP_DAYS))
        today = _dt.date.fromisoformat(self.today())
        dates = [(today - _dt.timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
        month = today.strftime("%Y-%m")
        now = self.now()

        def pick(d: Dict[str, Any]) -> Dict[str, Any]:
            return {k: d[k] for k in dates if k in d}

        def month_sum(d: Dict[str, Any]) -> Dict[str, int]:
            up = sum(int(v.get("up", 0)) for k, v in d.items() if k.startswith(month))
            down = sum(int(v.get("down", 0)) for k, v in d.items() if k.startswith(month))
            return {"up": up, "down": down}

        with self.lock:
            pcs = []
            for key, pc in self.pcs.items():
                online = bool(pc.get("seen") and now - float(pc["seen"]) < PC_OFFLINE_S)
                pcs.append({"key": key, "host": pc.get("host") or key, "label": pc.get("label", ""),
                            "ip": pc.get("ip", ""), "user": pc.get("user", ""), "seen": pc.get("seen"),
                            "os": pc.get("os", ""), "version": pc.get("version", ""), "ifaces": pc.get("ifaces") or [],
                            "online": online, "rate": pc.get("rate") if online else None,
                            "days": pick(pc.get("days") or {}), "month": month_sum(pc.get("days") or {}),
                            "nas_days": pick(pc.get("nas_days") or {}), "nas_month": month_sum(pc.get("nas_days") or {}),
                            "split_today": _split(pc.get("days") or {}, lambda k: k == dates[-1]),
                            "split_month": _split(pc.get("days") or {}, lambda k: k.startswith(month)),
                            "vpn_month": {"up": sum(int(v.get("vpn_up", 0)) for k, v in (pc.get("days") or {}).items()
                                                    if k.startswith(month)),
                                          "down": sum(int(v.get("vpn_down", 0)) for k, v in (pc.get("days") or {}).items()
                                                      if k.startswith(month))}})
            pcs.sort(key=lambda p: -(p["month"]["up"] + p["month"]["down"]))
            return {"now": now, "today": dates[-1], "dates": dates,
                    "router": {"days": pick(self.router["days"]), "month": self.router.get("month"),
                               "last_month": self.router.get("last_month"), "status": self.status.get("router") or {},
                               "enabled": bool(self.cfg["router"].get("enabled"))},
                    "nas": {"days": pick(self.nas["days"]), "month": month_sum(self.nas["days"]),
                            "status": self.status.get("nas") or {}, "enabled": bool(self.cfg["nas"].get("enabled")),
                            "host": self.cfg["nas"].get("host", "")},
                    "pcs": pcs, "tailscale": self._ts_view(pick, month_sum, now),
                    "github": {"days": pick(self.github["days"]), "month": month_sum(self.github["days"])},
                    "nasdir": self._nasdir_view(pick, month), "router_devices": self._rdev_view(now)}

    def _nasdir_view(self, pick, month: str) -> Dict[str, Any]:
        """NAS 分流每日：DERP 中繼（TCP 443，記帳時算在外網）從外網移到 VPN。"""
        days: Dict[str, Dict[str, Dict[str, int]]] = {}
        for k, v in self.nasdir["days"].items():
            relay = (self.ts["days"].get(k) or {}).get("relay") or {}
            ru, rd = int(relay.get("up", 0)), int(relay.get("down", 0))
            w = v.get("wan") or {"up": 0, "down": 0}
            vp = v.get("vpn") or {"up": 0, "down": 0}
            wu, wd = int(w.get("up", 0)), int(w.get("down", 0))
            mu, md = min(ru, wu), min(rd, wd)
            days[k] = {"lan": dict(v.get("lan") or {"up": 0, "down": 0}),
                       "vpn": {"up": int(vp.get("up", 0)) + mu, "down": int(vp.get("down", 0)) + md},
                       "wan": {"up": wu - mu, "down": wd - md}}
        mon = {c: {"up": sum(int(x[c]["up"]) for k, x in days.items() if k.startswith(month)),
                   "down": sum(int(x[c]["down"]) for k, x in days.items() if k.startswith(month))}
               for c in ("lan", "vpn", "wan")}
        return {"days": pick(days), "month": mon, "status": self.status.get("netacct") or {}}

    def _ts_view(self, pick, month_sum, now: float) -> Dict[str, Any]:
        days = self.ts["days"]
        flat = {k: {"up": sum(int(x.get("up", 0)) for x in v.values()), "down": sum(int(x.get("down", 0)) for x in v.values())}
                for k, v in days.items()}
        ext = {k: {"up": sum(int(v.get(p, {}).get("up", 0)) for p in ("wan", "relay")),
                   "down": sum(int(v.get(p, {}).get("down", 0)) for p in ("wan", "relay"))} for k, v in days.items()}
        peers = []
        for pid, r in self.ts["peers"].items():
            m = month_sum(r.get("days") or {})
            if not (r.get("online") or m["up"] or m["down"]):
                continue
            pext = {k: int(v.get("wan", 0)) + int(v.get("relay", 0)) for k, v in (r.get("paths") or {}).items()}
            peers.append({"id": pid, "name": r.get("name", pid), "os": r.get("os", ""), "ip": r.get("ip", ""),
                          "path": r.get("path", ""), "relay": r.get("relay", ""), "cur": r.get("cur", ""),
                          "online": bool(r.get("online")), "seen": r.get("seen"), "exit_node_user": bool(r.get("exit_node_user")),
                          "days": pick(r.get("days") or {}), "month": m, "ext": pick(pext)})
        peers.sort(key=lambda p: -(p["month"]["up"] + p["month"]["down"]))
        return {"days": pick(flat), "ext": pick(ext), "by_path": pick(days), "month": month_sum(flat),
                "month_ext": month_sum(ext), "peers": peers, "status": self.status.get("tailscale") or {}}

    # ---- 1.0.34：即時（每分鐘一格）----
    def recent_view(self, window: int, chart: str = "") -> Dict[str, Any]:
        from .netrecent import WINDOWS

        window = min(WINDOWS, key=lambda w: abs(w - int(window or 600)))
        now = self.now()
        rc = self.recent
        out: List[Dict[str, Any]] = []

        def src(key: str, name: str, kind: str, **extra: Any) -> None:
            w = rc.window(key, window, now=now)
            out.append({"key": key, "name": name, "kind": kind, "up": w["up"], "down": w["down"],
                        "rate": w.get("rate"), **extra})

        with self.lock:
            if self.cfg["router"].get("enabled"):
                src("router", "整個網段（路由器）", "router")
            if self.cfg["nas"].get("enabled"):
                src("nas", "NAS", "nas")
            if "nasdir:lan" in rc.data:                             # 1.0.37：NAS 分流；DERP 中繼從外網移到 VPN
                w = {k: rc.window(f"nasdir:{k}", window, now=now) for k in ("lan", "vpn", "wan")}
                rl = rc.window("tspath:relay", window, now=now)
                mu, md = min(rl["up"], w["wan"]["up"]), min(rl["down"], w["wan"]["down"])
                adj = {"lan": (0, 0), "vpn": (mu, md), "wan": (-mu, -md)}
                for k, name in (("lan", "NAS ⇄ 區網"), ("vpn", "NAS ⇄ VPN"), ("wan", "NAS ⇄ 外網（直接）")):
                    r = w[k].get("rate")
                    out.append({"key": f"nasdir:{k}", "name": name, "kind": "nasdir", "up": w[k]["up"] + adj[k][0],
                                "down": w[k]["down"] + adj[k][1], "rate": r})
            for did, pc in self.pcs.items():
                online = now - float(pc.get("seen") or 0) < PC_OFFLINE_S
                src(f"pc:{did}", pc.get("label") or pc.get("host") or did, "pc", online=online,
                    sub=" ".join(x for x in (pc.get("ip", ""), pc.get("user", "")) if x))
                if any(f"pcx:{did}:{c}" in rc.data for c in SPLIT_CATS):     # 1.0.38：內網 / 外網 / VPN 通道
                    out[-1]["split"] = {c: {k: v for k, v in rc.window(f"pcx:{did}:{c}", window, now=now).items()
                                            if k in ("up", "down")} for c in SPLIT_CATS}
                if f"pcnas:{did}" in rc.data:                        # 1.0.37：其中跟 NAS 之間的、其餘（多半外網）
                    o = out[-1]
                    nw = rc.window(f"pcnas:{did}", window, now=now)
                    o["nas"] = {"up": nw["up"], "down": nw["down"]}
                    o["other"] = {"up": max(0, o["up"] - nw["up"]), "down": max(0, o["down"] - nw["down"])}
            for pid, r in self.ts["peers"].items():
                if not r.get("online") and f"ts:{pid}" not in rc.data:
                    continue
                src(f"ts:{pid}", r.get("name", pid), "vpn", online=bool(r.get("online")), path=r.get("path", ""),
                    sub=" ".join(x for x in (r.get("os", ""), r.get("ip", "")) if x))
            # 1.0.40：路由器算到、但各裝置（電腦的外網部分、NAS 直接外網）加起來沒有的 → 沒被監看的裝置 / 路由器自己 / 外網口那一側
            r = next((o for o in out if o["key"] == "router"), None)
            if r is not None:
                ku = kd = 0
                for o in out:
                    if o["kind"] == "pc":
                        if o.get("split"):
                            x = o["split"]
                            ku += x["wan"]["up"] + x["tunw"]["up"]
                            kd += x["wan"]["down"] + x["tunw"]["down"]
                        elif o.get("other"):
                            ku += o["other"]["up"]
                            kd += o["other"]["down"]
                        else:
                            ku += o["up"]
                            kd += o["down"]
                    elif o["key"] == "nasdir:wan":
                        ku += o["up"]
                        kd += o["down"]
                    elif o["key"] == "nasdir:vpn":                    # 經 DERP 中繼的 VPN（已從外網移過來）
                        rl = rc.window("tspath:relay", window, now=now)
                        ku += min(rl["up"], o["up"])
                        kd += min(rl["down"], o["down"])
                if "nasdir:wan" not in {o["key"] for o in out} and self.cfg["nas"].get("enabled"):
                    n = next((o for o in out if o["key"] == "nas"), None)     # 沒有 NAS 分流：整個 NAS 都當可能外網
                    if n:
                        ku += n["up"]
                        kd += n["down"]
                out.append({"key": "unknown", "name": "路由器有、監看不到的", "kind": "unknown", "nochart": True,
                            "up": max(0, r["up"] - ku), "down": max(0, r["down"] - kd), "rate": None,
                            "known": {"up": ku, "down": kd}})
        for k in sorted(rc.keys()):                                      # 1.0.41：NAS 主機每張網卡
            if k.startswith("nif:"):
                src(k, f"NAS 網卡 {k[4:]}", "nif")
        src("hub:remote", "Hub ⇄ 區網以外", "hub")
        src("hub:lan", "Hub ⇄ 區網", "hub")
        out = [o for o in out if o["up"] or o["down"] or o.get("online")
               or (o.get("rate") and (o["rate"]["up"] or o["rate"]["down"]))
               or o["kind"] in ("router", "nas", "unknown")]
        keys = {o["key"] for o in out if not o.get("nochart")}
        if chart not in keys:
            chart = "router" if "router" in keys else "nas" if "nas" in keys else (out[0]["key"] if out else "")
        ch = rc.window(chart, window, series=True, now=now) if chart else {"series": [], "step": 60}
        return {"now": now, "window": window, "windows": list(WINDOWS), "sources": out,
                "chart": {"key": chart, "series": ch.get("series", []), "step": ch.get("step", 60)},
                "every": {"router": ROUTER_EVERY, "nas": NAS_EVERY, "tailscale": TS_EVERY}}

    # ---- 背景執行緒 ----
    def start(self) -> None:
        if self._threads:
            return
        for fn, every, what in ((self.poll_router, ROUTER_EVERY, "router"), (self.poll_nas, NAS_EVERY, "nas"),
                                (self.poll_tailscale, TS_EVERY, "tailscale"), (self.poll_netacct, NAS_EVERY, "netacct")):
            th = threading.Thread(target=self._loop, args=(fn, every, what), name=f"netusage-{what}", daemon=True)
            th.start()
            self._threads.append(th)

    def _loop(self, fn, every: float, what: str) -> None:
        self._stop.wait(5.0)
        while not self._stop.is_set():
            if what in ("tailscale", "netacct") or self.cfg[what].get("enabled"):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    log.exception("網路用量（%s）輪詢失敗", what)
            self._stop.wait(every)

    def stop(self) -> None:
        self._stop.set()
        self.save(force=True)


# ---------------------------------------------------------------------------
# Hub 路由
# ---------------------------------------------------------------------------
from .server import Handler, HubHTTPError, route  # noqa: E402


def store(hub) -> NetUsage:
    st = getattr(hub, "netusage", None)
    if st is None:
        st = hub.netusage = NetUsage(hub.cfg.data_dir)
    return st


def start(hub) -> NetUsage:
    st = store(hub)
    st.start()
    return st


def _split(days: Dict[str, Any], want) -> Optional[Dict[str, Any]]:
    """流量監看 1.0.4 起：內網 / 外網 / VPN 通道（其中經外網）＋ 分不出來的；沒有資料 → None。"""
    tot = {c: {"up": 0, "down": 0} for c in SPLIT_CATS + ("all",)}
    seen = False
    for k, v in days.items():
        if not want(k):
            continue
        has = any(kk in v for kk in SPLIT_KEYS)
        seen = seen or has
        if not has:
            continue
        for d in ("up", "down"):
            tot["all"][d] += int(v.get(d, 0))
            for c in SPLIT_CATS:
                tot[c][d] += int(v.get(f"{c}_{d}", 0))
    if not seen:
        return None
    tot["other"] = {d: max(0, tot["all"][d] - tot["lan"][d] - tot["wan"][d] - tot["tun"][d]) for d in ("up", "down")}
    return tot


@route("POST", r"/api/netusage/report", auth=False)
def _report(h: Handler) -> None:
    """流量監看程式回報（不用登入；只收固定欄位、有長度上限）。"""
    try:
        b = json.loads(h._body_bytes(64 * 1024).decode("utf-8") or "{}")
    except ValueError:
        raise HubHTTPError(400, "JSON 格式錯誤") from None
    if not isinstance(b, dict):
        raise HubHTTPError(400, "格式錯誤")
    try:
        out = store(h.hub).report_device(b, h.ip)
        tls = getattr(h.hub, "tls", None)          # 1.0.38：流量監看在內網把 https 網址指到 NAS（hosts）
        if tls is not None and getattr(tls, "domain", "") and tls.url("hub"):
            out["lan_https"] = {"domain": tls.domain, "url": tls.url("hub")}
        h._json(out)
    except ValueError as e:
        raise HubHTTPError(400, str(e)) from None


@route("GET", r"/api/netusage/recent", auth="any")
def _recent(h: Handler) -> None:
    """1.0.34：即時——window 秒（60 / 600 / 1800 / 3600 / 10800 / 21600 / 86400）內各來源的總量與目前速率，
    chart = 要畫每分鐘曲線的來源（router / nas / pc:<id> / ts:<id> / hub:remote / hub:lan）。"""
    q = h._query()
    try:
        window = int(q.get("window") or 600)
    except (TypeError, ValueError):
        window = 600
    h._json({"ok": True, **store(h.hub).recent_view(window, str(q.get("chart") or ""))})


@route("GET", r"/api/netusage", auth="any")
def _view(h: Handler) -> None:
    try:
        days = int(h._query().get("days") or 31)
    except (TypeError, ValueError):
        days = 31
    st = store(h.hub)
    out = st.view(days)
    try:
        owner = bool(h.user().get("owner"))
    except Exception:  # noqa: BLE001
        owner = False
    if owner:
        out["config"] = st.public_config()
    h._json(out)


@route("PUT", r"/api/netusage/config", auth="owner")
def _config(h: Handler) -> None:
    b = h._json_body() or {}
    if not isinstance(b, dict):
        raise HubHTTPError(400, "格式錯誤")
    st = store(h.hub)
    st.set_config(b)
    st.start()
    plat = getattr(h.hub, "platform", None)
    if plat is not None:
        try:
            who = h.user().get("username", "") or h.ip
            plat.audit(who, "netusage-config", json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "password"}
                                                         if isinstance(v, dict) else v for k, v in b.items()},
                                                        ensure_ascii=False)[:300])
        except Exception:  # noqa: BLE001
            pass
    h._json({"ok": True, "config": st.public_config()})


@route("POST", r"/api/netusage/test", auth="owner")
def _test(h: Handler) -> None:
    b = h._json_body() or {}
    st = store(h.hub)
    what = str(b.get("what") or "")
    if what == "router":
        h._json({"ok": True, "status": st.poll_router()})
    elif what == "nas":
        h._json({"ok": True, "status": st.poll_nas()})
    else:
        raise HubHTTPError(400, "what 要是 router 或 nas")


@route("PUT", r"/api/netusage/label", auth="any")
def _label(h: Handler) -> None:
    b = h._json_body() or {}
    try:
        store(h.hub).set_label(str(b.get("host") or ""), str(b.get("label") or ""))
    except KeyError:
        raise HubHTTPError(404, "沒有這台電腦") from None
    h._json({"ok": True})


@route("DELETE", r"/api/netusage/pc/([A-Za-z0-9_.@-]+)", auth="owner")
def _remove(h: Handler, host: str) -> None:
    store(h.hub).remove_pc(host)
    h._json({"ok": True})


@route("GET", r"/api/netusage/nastop", auth="any")
def _nastop(h: Handler) -> None:
    q = h._query()
    try:
        w = int(q.get("window") or 600)
    except ValueError:
        w = 600
    h._json(store(h.hub).nastop_view(w))


@route("GET", r"/network/?", auth="any")
def _page(h: Handler) -> None:
    from .web_nav import page

    page(h, "network.html", "network")
