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
5. 1.0.32 Hub 自己從 GitHub 下載新版的量（控制台「從 GitHub 取得新版」與自動更新）。

路由器密碼只存在 Hub 的資料夾（data/netusage_secret.json），API 不會回傳。
"""
from __future__ import annotations

import datetime as _dt
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
ROUTER_EVERY = 600.0
NAS_EVERY = 60.0
TS_EVERY = 60.0
TS_PATHS = ("lan", "wan", "relay")       # 區網直連 / 外網直連 / DERP 中繼
TS_SOCKETS = ("/tailscale/tailscaled.sock", "/var/run/tailscale/tailscaled.sock",
              "/var/packages/Tailscale/var/tailscaled.sock")
PC_OFFLINE_S = 180.0
GAP_S = 7200.0                       # NAS 超過 2 小時沒讀到 → 那段時間的流量不算（不知道是哪天）
MAX_DAY = 100 * 1024 ** 4            # 一台裝置一天超過 100 TB → 當成錯誤資料
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
            return fields
        return {}

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
        self.status: Dict[str, Dict[str, Any]] = {"router": {}, "nas": {}, "tailscale": {}}
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
        with self.lock:
            if not (self._dirty or force):
                return
            if not force and self.now() - self._last_save < 30:
                return
            try:
                self._write(self.file, {"cfg": self.cfg, "router": self.router, "nas": self.nas, "devices": self.pcs,
                                        "tailscale": self.ts, "github": self.github})
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
                days = self.router["days"]
                days[today] = {"up": int((tm.get("TodayUpload") or 0) * MB), "down": int((tm.get("TodayDownload") or 0) * MB)}
                if tm.get("YesterdayUpload") is not None or tm.get("YesterdayDownload") is not None:
                    days[yday] = {"up": int((tm.get("YesterdayUpload") or 0) * MB),
                                  "down": int((tm.get("YesterdayDownload") or 0) * MB)}
                self.router["month"] = {"up": int((tm.get("MonthUpload") or 0) * MB),
                                        "down": int((tm.get("MonthDownload") or 0) * MB)}
                self.router["last_month"] = {"up": int((tm.get("LastMonthUpload") or 0) * MB),
                                             "down": int((tm.get("LastMonthDownload") or 0) * MB)}
                _trim(days, today)
                self._dirty = True
            st.update(ok=True, info=info, port=cl.port, ssl=cl.ssl)
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
                        if (drx or dtx) and drx <= MAX_DELTA and dtx <= MAX_DELTA:
                            # 用上一次讀到的路徑（這段時間大多走那條）；路徑剛換的話用現在的
                            use = last.get("path") if last.get("path") in TS_PATHS else path
                            day = self.ts["days"].setdefault(today, {})
                            _add(day, use, dtx, drx)
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
        rate = b.get("rate") if isinstance(b.get("rate"), dict) else {}
        now = self.now()
        with self.lock:
            if did not in self.pcs and len(self.pcs) >= MAX_DEVICES:
                raise ValueError("裝置太多")
            dev = self.pcs.setdefault(did, {"days": {}})
            dev["days"].update(clean)                  # 程式自己累計：同一天以最新的數字為準
            ifs = b.get("ifaces") if isinstance(b.get("ifaces"), list) else []
            try:
                r = {"up": max(0.0, float(rate.get("up") or 0)), "down": max(0.0, float(rate.get("down") or 0))}
            except (TypeError, ValueError):
                r = {"up": 0.0, "down": 0.0}
            dev.update(seen=now, src_ip=_s(ip, 60), on_net=bool(b.get("on_net", True)))
            if rate:
                dev["rate"] = r
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
                            "days": pick(pc.get("days") or {}), "month": month_sum(pc.get("days") or {})})
            pcs.sort(key=lambda p: -(p["month"]["up"] + p["month"]["down"]))
            return {"now": now, "today": dates[-1], "dates": dates,
                    "router": {"days": pick(self.router["days"]), "month": self.router.get("month"),
                               "last_month": self.router.get("last_month"), "status": self.status.get("router") or {},
                               "enabled": bool(self.cfg["router"].get("enabled"))},
                    "nas": {"days": pick(self.nas["days"]), "month": month_sum(self.nas["days"]),
                            "status": self.status.get("nas") or {}, "enabled": bool(self.cfg["nas"].get("enabled")),
                            "host": self.cfg["nas"].get("host", "")},
                    "pcs": pcs, "tailscale": self._ts_view(pick, month_sum, now),
                    "github": {"days": pick(self.github["days"]), "month": month_sum(self.github["days"])}}

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

    # ---- 背景執行緒 ----
    def start(self) -> None:
        if self._threads:
            return
        for fn, every, what in ((self.poll_router, ROUTER_EVERY, "router"), (self.poll_nas, NAS_EVERY, "nas"),
                                (self.poll_tailscale, TS_EVERY, "tailscale")):
            th = threading.Thread(target=self._loop, args=(fn, every, what), name=f"netusage-{what}", daemon=True)
            th.start()
            self._threads.append(th)

    def _loop(self, fn, every: float, what: str) -> None:
        self._stop.wait(5.0)
        while not self._stop.is_set():
            if what == "tailscale" or self.cfg[what].get("enabled"):
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
        h._json(store(h.hub).report_device(b, h.ip))
    except ValueError as e:
        raise HubHTTPError(400, str(e)) from None


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


@route("GET", r"/network/?", auth="any")
def _page(h: Handler) -> None:
    from .web_nav import page

    page(h, "network.html", "network")
