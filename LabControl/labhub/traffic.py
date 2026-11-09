"""1.0.33：Hub 各埠的流量監控、分類限流、對外（VPN）串流壓縮與省流模式。

每一個連到 Hub 的請求（8765 網站與 API、8443 內網 https、8444 論文庫 https 轉送；控制台 8766 另一個容器）
都記下：哪個埠、從哪裡來、哪一類資料、收送多少位元組。

來源：
    lan    實驗室區網（設定的網段，預設 192.168.50.0/24）
    vpn    Tailscale（100.64.0.0/10；經 tailscale serve 轉進來的看 X-Forwarded-For）
    local  NAS 本機 / 容器轉進來的（Synology 的 Tailscale 是 userspace 模式：VPN 直接連 100.x:8765 也會算在這裡）
    wan    其他（公開 IP）
資料類型（CATS）：即時量測、數據檔、安裝與更新、狀態與指令、網頁、其他 API、論文庫。

限流（網路用量頁設定，站長）：每一類可以設
    rate_kbps  速度上限（KB/s，同一類所有連線共用；0 = 不限）
    daily_mb   每日上限（MB；超過後這一類回 429，隔天自動恢復；0 = 不限）
    scope      remote = 只限區網以外（VPN / 本機轉入 / 外網）；all = 全部
網路用量頁、登入、/api/netusage、/api/traffic 永遠不擋（站長才改得回來）。

壓縮（對區網以外的連線）：
    * JSON / 網頁 / 文字回應：用戶端說可以（Accept-Encoding: gzip）就 gzip；
    * 即時量測串流（/api/nodes/<n>/live）：用戶端說看得懂（X-Labhub-Enc: zs，Lab Control 1.0.33 起）就把每條曲線
      換成「位元組重排＋zlib」（無損，約小 30%）；
    * 省流模式：今天的用量超過門檻（economy.threshold_gb；依據 basis：hub = Hub 對外、router = 路由器整個網段、
      max = 取大的）→ 曲線只留 10 位尾數（相對誤差約 0.1%，|S21| 約 0.01 dB；檔案裡的數據不受影響）再 zlib 9，
      約只剩原本的 1/3；手機的最後一條 trace 抽成 300 點。
只用標準函式庫。
"""
from __future__ import annotations

import base64
import datetime as _dt
import gzip
import ipaddress
import json
import logging
import os
import re
import threading
import time
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

CATS: List[Tuple[str, str]] = [("live", "即時量測"), ("data", "數據檔"), ("updates", "安裝與更新"),
                               ("poll", "狀態與指令"), ("pages", "網頁"), ("api", "其他 API"), ("paperlib", "論文庫")]
CAT_IDS = [c for c, _ in CATS]
CLASSES: List[Tuple[str, str]] = [("lan", "區網"), ("vpn", "VPN"), ("local", "NAS 本機轉入"), ("wan", "外網")]
REMOTE = ("vpn", "local", "wan")
KEEP_DAYS = 400
SAVE_EVERY = 30.0
NEVER_BLOCK = re.compile(r"^/(network/?|login|logout|api/netusage.*|api/traffic.*|api/platform/(login|logout|me))$")

_RULES: List[Tuple[re.Pattern, str]] = [(re.compile(p), c) for p, c in [
    (r"^/api/nodes/[^/]+/live(/.*)?$", "live"),
    (r"^/api/m(/.*)?$", "live"),
    (r"^/api/(files/.+|data/.*|index/.*)$", "data"),
    (r"^/api/notebook/[^/]+/files(/.*)?$", "data"),
    (r"^/api/platform/catalog(/.*)?$", "data"),
    (r"^/(get(/.*)?|api/releases(/.*)?|api/platform/modules/.+|portal)$", "updates"),
    (r"^/api/(state|instruments(/.*)?|ping|hub|replies/.*|devices/.*|agent(/.*)?|platform/(bus/.*|status|me))$", "poll"),
    (r"^/api/nodes/[^/]+(/(status|commands|instruments|claim|offline))?$", "poll"),
    (r"^/api/.*$", "api"),
    (r"^.*$", "pages"),
]]

DEFAULT_CFG: Dict[str, Any] = {
    "lan_subnets": ["192.168.50.0/24"],
    "compress": True,
    "local_remote": True,          # 「NAS 本機轉入」算對外（Synology 的 Tailscale 轉進來的 VPN 在這裡）；NAS 的 Docker 看不到真正來源時改 False
    "economy": {"enabled": True, "threshold_gb": 60.0, "basis": "max", "force": False},
    "limits": {c: {"rate_kbps": 0, "daily_mb": 0, "scope": "remote"} for c in CAT_IDS},
}


def category(path: str) -> str:
    for rx, c in _RULES:
        if rx.match(path):
            return c
    return "pages"


def _num(v: Any, default: float = 0.0, lo: float = 0.0, hi: float = 1e12) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, f)) if f == f else default


# ---------------------------------------------------------------------------------------------------------
class Bucket:
    """令牌桶：同一類所有連線共用一個速度上限。"""

    def __init__(self, rate: float) -> None:
        self.rate = float(rate)
        self.tokens = self.rate
        self.t = time.monotonic()
        self.lock = threading.Lock()

    def take(self, n: int) -> None:
        """送 n bytes 前呼叫：令牌不夠就睡一下（每次最多 0.5 秒，連線斷了也很快結束）。"""
        while n > 0:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.rate, self.tokens + (now - self.t) * self.rate)
                self.t = now
                if self.tokens >= 1:
                    got = int(min(n, self.tokens))
                    self.tokens -= got
                    n -= got
                    continue
                need = min(n, max(1.0, self.rate * 0.05))
                wait = (need - self.tokens) / self.rate
            time.sleep(max(0.005, min(wait, 0.5)))


class Traffic:
    def __init__(self, data_dir: Path, now=time.time) -> None:
        self.file = Path(data_dir) / "traffic.json"
        self.now = now
        self.lock = threading.RLock()
        d: Dict[str, Any] = {}
        try:
            d = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        self.cfg = self._clean_cfg(d.get("cfg") or {})
        self.days: Dict[str, Any] = d.get("days") if isinstance(d.get("days"), dict) else {}
        self.recent: Dict[int, Dict[str, int]] = {}            # 秒 → {"cat|cls": bytes}（最近 60 秒，算目前速度）
        self.buckets: Dict[str, Bucket] = {}
        self._dirty = False
        self._saved = 0.0
        self._eco: Tuple[float, bool, Dict[str, Any]] = (0.0, False, {})
        self.netusage = None                                     # Hub 啟動時接上（省流模式依路由器用量）
        self._nets = self._parse_nets()
        self.cache: Dict[Tuple[Any, ...], Any] = {}               # 即時串流壓縮的結果（多個 VPN 用戶端共用）

    # ---- 設定 ----
    @staticmethod
    def _clean_cfg(c: Dict[str, Any]) -> Dict[str, Any]:
        out = json.loads(json.dumps(DEFAULT_CFG))
        if isinstance(c.get("lan_subnets"), list):
            nets = []
            for s in c["lan_subnets"][:20]:
                try:
                    nets.append(str(ipaddress.ip_network(str(s).strip(), strict=False)))
                except ValueError:
                    continue
            out["lan_subnets"] = nets or out["lan_subnets"]
        if "compress" in c:
            out["compress"] = bool(c["compress"])
        if "local_remote" in c:
            out["local_remote"] = bool(c["local_remote"])
        e = c.get("economy") if isinstance(c.get("economy"), dict) else {}
        if "enabled" in e:
            out["economy"]["enabled"] = bool(e["enabled"])
        if "threshold_gb" in e:
            out["economy"]["threshold_gb"] = _num(e["threshold_gb"], 60.0, 0.0, 100000.0)
        if e.get("basis") in ("hub", "router", "max"):
            out["economy"]["basis"] = e["basis"]
        if "force" in e:
            out["economy"]["force"] = bool(e["force"])
        lim = c.get("limits") if isinstance(c.get("limits"), dict) else {}
        for cat in CAT_IDS:
            x = lim.get(cat) if isinstance(lim.get(cat), dict) else {}
            o = out["limits"][cat]
            if "rate_kbps" in x:
                o["rate_kbps"] = _num(x["rate_kbps"], 0.0, 0.0, 10_000_000.0)
            if "daily_mb" in x:
                o["daily_mb"] = _num(x["daily_mb"], 0.0, 0.0, 100_000_000.0)
            if x.get("scope") in ("remote", "all"):
                o["scope"] = x["scope"]
        return out

    def _parse_nets(self) -> List[Any]:
        out = []
        for s in self.cfg["lan_subnets"]:
            try:
                out.append(ipaddress.ip_network(s, strict=False))
            except ValueError:
                continue
        return out

    def set_config(self, body: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            merged = json.loads(json.dumps(self.cfg))
            for k in ("lan_subnets", "compress", "local_remote"):
                if k in body:
                    merged[k] = body[k]
            if isinstance(body.get("economy"), dict):
                merged["economy"].update(body["economy"])
            if isinstance(body.get("limits"), dict):
                for cat, v in body["limits"].items():
                    if cat in merged["limits"] and isinstance(v, dict):
                        merged["limits"][cat].update(v)
            self.cfg = self._clean_cfg(merged)
            self._nets = self._parse_nets()
            self.buckets.clear()
            self._eco = (0.0, False, {})
            self._dirty = True
        self.save(force=True)
        return self.cfg

    def remote_classes(self) -> Tuple[str, ...]:
        return REMOTE if self.cfg.get("local_remote", True) else ("vpn", "wan")

    def is_remote(self, cls: str) -> bool:
        return cls in self.remote_classes()

    # ---- 來源 ----
    def client_class(self, ip: str, xff: str = "") -> Tuple[str, str]:
        """(來源類別, 真正的 IP)。"""
        try:
            a = ipaddress.ip_address(ip.split("%")[0])
        except ValueError:
            return "wan", ip
        if getattr(a, "ipv4_mapped", None):
            a = a.ipv4_mapped
        proxied = a.is_loopback or (a.is_private and not any(a in n for n in self._nets)
                                    and not a in ipaddress.ip_network("100.64.0.0/10"))
        if proxied and xff:
            first = xff.split(",")[0].strip()
            try:
                b = ipaddress.ip_address(first)
                return self._cls(b), first
            except ValueError:
                pass
        if proxied:
            return "local", str(a)
        return self._cls(a), str(a)

    def _cls(self, a) -> str:
        if a in ipaddress.ip_network("100.64.0.0/10") or (a.version == 6 and str(a).startswith("fd7a:115c:a1e0")):
            return "vpn"
        if any(a in n for n in self._nets):
            return "lan"
        if a.is_loopback or a.is_private:
            return "local"
        return "wan"

    # ---- 記帳 ----
    def today(self) -> str:
        return _dt.datetime.fromtimestamp(self.now()).strftime("%Y-%m-%d")

    def add(self, port: int, cls: str, cat: str, nin: int, nout: int, nreq: int = 1) -> None:
        if not (nin or nout or nreq):
            return
        with self.lock:
            day = self.days.setdefault(self.today(), {})
            c = day.setdefault(str(port), {}).setdefault(cls, {}).setdefault(cat, [0, 0, 0])
            c[0] += int(nin)
            c[1] += int(nout)
            c[2] += int(nreq)
            sec = int(time.time())
            nu = self.netusage
            if nu is not None:                                   # 1.0.34：即時頁的「Hub ⇄ 區網以外 / 區網」
                try:
                    nu.recent.add("hub:remote" if self.is_remote(cls) else "hub:lan", int(nout), int(nin))
                except Exception:  # noqa: BLE001
                    pass
            r = self.recent.setdefault(sec, {})
            k = f"{cat}|{cls}"
            r[k] = r.get(k, 0) + int(nin) + int(nout)
            for s in [s for s in self.recent if s < sec - 60]:
                del self.recent[s]
            self._dirty = True
        if time.time() - self._saved > SAVE_EVERY:
            self.save()

    def used_today(self, cat: Optional[str] = None, scope: str = "remote") -> int:
        with self.lock:
            day = self.days.get(self.today()) or {}
            tot = 0
            for _port, by_cls in day.items():
                for cls, by_cat in by_cls.items():
                    if scope == "remote" and not self.is_remote(cls):
                        continue
                    for c, v in by_cat.items():
                        if cat is None or c == cat:
                            tot += int(v[0]) + int(v[1])
            return tot

    # ---- 限流 ----
    def blocked(self, cat: str, cls: str, path: str) -> Optional[str]:
        if NEVER_BLOCK.match(path):
            return None
        lim = self.cfg["limits"].get(cat) or {}
        mb = float(lim.get("daily_mb") or 0)
        if mb <= 0 or (lim.get("scope") == "remote" and not self.is_remote(cls)):
            return None
        used = self.used_today(cat, lim.get("scope", "remote"))
        if used >= mb * 1024 * 1024:
            name = dict(CATS).get(cat, cat)
            return (f"今天「{name}」的流量已達上限 {mb:g} MB（已用 {used / 1048576:.0f} MB）；"
                    "明天自動恢復，或請站長到 Hub「網路用量」調整")
        return None

    def bucket(self, cat: str, cls: str) -> Optional[Bucket]:
        lim = self.cfg["limits"].get(cat) or {}
        kbps = float(lim.get("rate_kbps") or 0)
        if kbps <= 0 or (lim.get("scope") == "remote" and not self.is_remote(cls)):
            return None
        key = f"{cat}|{'all' if lim.get('scope') == 'all' else 'remote'}"
        with self.lock:
            b = self.buckets.get(key)
            if b is None or b.rate != kbps * 1024:
                b = self.buckets[key] = Bucket(kbps * 1024)
            return b

    # ---- 省流模式 ----
    def economy(self) -> Tuple[bool, Dict[str, Any]]:
        t, on, info = self._eco
        if time.time() - t < 10:
            return on, info
        e = self.cfg["economy"]
        hub_b = self.used_today(None, "remote")
        router_b = 0
        nu = self.netusage
        if nu is not None:
            try:
                rd = (nu.router.get("days") or {}).get(nu.today()) or {}
                router_b = int(rd.get("up", 0)) + int(rd.get("down", 0))
            except Exception:  # noqa: BLE001
                router_b = 0
        basis = e.get("basis", "max")
        used = hub_b if basis == "hub" else router_b if basis == "router" else max(hub_b, router_b)
        thr = float(e.get("threshold_gb") or 0) * 1024 ** 3
        on = bool(e.get("force")) or (bool(e.get("enabled")) and thr > 0 and used >= thr)
        info = {"on": on, "used": used, "hub": hub_b, "router": router_b, "threshold": thr, "basis": basis,
                "forced": bool(e.get("force"))}
        self._eco = (time.time(), on, info)
        return on, info

    # ---- 存 ----
    def save(self, force: bool = False) -> None:
        with self.lock:
            if not self._dirty and not force:
                return
            today = self.today()
            cut = (_dt.date.fromisoformat(today) - _dt.timedelta(days=KEEP_DAYS)).isoformat()
            for k in [k for k in self.days if k < cut]:
                del self.days[k]
            data = json.dumps({"cfg": self.cfg, "days": self.days}, ensure_ascii=False)
            self._dirty = False
            self._saved = time.time()
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, self.file)
        except OSError as e:
            log.warning("存 traffic.json 失敗：%s", e)

    # ---- 給網頁 ----
    def view(self, days: int = 31) -> Dict[str, Any]:
        days = max(1, min(int(days or 31), KEEP_DAYS))
        today = _dt.date.fromisoformat(self.today())
        dates = [(today - _dt.timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
        with self.lock:
            per_day: Dict[str, Dict[str, Any]] = {}
            ports: Dict[str, Dict[str, int]] = {}
            table: Dict[str, Dict[str, Dict[str, int]]] = {}       # 今天：類型 → 來源 → {in, out, n}
            for d in dates:
                day = self.days.get(d) or {}
                agg = {"remote": {"up": 0, "down": 0}, "lan": {"up": 0, "down": 0}}
                for port, by_cls in day.items():
                    for cls, by_cat in by_cls.items():
                        for cat, v in by_cat.items():
                            k = "remote" if self.is_remote(cls) else "lan"
                            agg[k]["down"] += int(v[0])               # Hub 收到 = 用戶端上傳
                            agg[k]["up"] += int(v[1])                 # Hub 送出
                            if d == dates[-1]:
                                p = ports.setdefault(port, {"in": 0, "out": 0, "n": 0})
                                p["in"] += int(v[0])
                                p["out"] += int(v[1])
                                p["n"] += int(v[2])
                                t = table.setdefault(cat, {}).setdefault(cls, {"in": 0, "out": 0, "n": 0})
                                t["in"] += int(v[0])
                                t["out"] += int(v[1])
                                t["n"] += int(v[2])
                per_day[d] = agg
            month = dates[-1][:7]
            mtab: Dict[str, Dict[str, int]] = {}
            for d, day in self.days.items():
                if not d.startswith(month):
                    continue
                for _port, by_cls in day.items():
                    for cls, by_cat in by_cls.items():
                        for cat, v in by_cat.items():
                            m = mtab.setdefault(cat, {"lan": 0, "remote": 0})
                            m["remote" if self.is_remote(cls) else "lan"] += int(v[0]) + int(v[1])
            sec = int(time.time())
            rate: Dict[str, Dict[str, float]] = {}
            for s, r in self.recent.items():
                if s >= sec - 30:
                    for k, n in r.items():
                        cat, cls = k.split("|")
                        x = rate.setdefault(cat, {"lan": 0.0, "remote": 0.0})
                        x["remote" if self.is_remote(cls) else "lan"] += n / 30.0
        eco_on, eco = self.economy()
        return {"dates": dates, "days": per_day, "today": table, "month": mtab, "ports": ports, "rate": rate,
                "cats": CATS, "classes": CLASSES, "cfg": self.cfg, "economy": eco,
                "used": {c: self.used_today(c, (self.cfg["limits"][c] or {}).get("scope", "remote")) for c in CAT_IDS}}


# ---------------------------------------------------------------------------------------------------------
# 每個連線的計數器（server.Handler.setup 接上）
class Meter:
    def __init__(self, tr: Traffic, port: int, ip: str) -> None:
        self.tr, self.port, self.ip = tr, port, ip
        self.nin = self.nout = 0
        self.cat = "pages"
        self.cls, self.real_ip = tr.client_class(ip)
        self.bucket: Optional[Bucket] = None

    def begin(self, path: str, xff: str = "") -> None:
        self.cat = category(path)
        self.cls, self.real_ip = self.tr.client_class(self.ip, xff)
        self.bucket = self.tr.bucket(self.cat, self.cls)

    @property
    def remote(self) -> bool:
        return self.tr.is_remote(self.cls)

    def flush(self, nreq: int = 1) -> None:
        nin, nout = self.nin, self.nout
        self.nin = self.nout = 0
        try:
            self.tr.add(self.port, self.cls, self.cat, nin, nout, nreq)
        except Exception:  # noqa: BLE001
            log.debug("記流量失敗", exc_info=True)


class CountingReader:
    def __init__(self, f, m: Meter) -> None:
        self._f, self._m = f, m

    def read(self, *a):
        b = self._f.read(*a)
        self._m.nin += len(b)
        return b

    def readline(self, *a):
        b = self._f.readline(*a)
        self._m.nin += len(b)
        return b

    def readinto(self, buf):
        n = self._f.readinto(buf)
        self._m.nin += n or 0
        return n

    def __getattr__(self, k):
        return getattr(self._f, k)


class CountingWriter:
    CHUNK = 16384

    def __init__(self, f, m: Meter) -> None:
        self._f, self._m = f, m

    def write(self, b) -> int:
        mv = memoryview(b)
        n = len(mv)
        bk = self._m.bucket
        if bk is None or n == 0:
            self._f.write(b)
            self._m.nout += n
            return n
        for i in range(0, n, self.CHUNK):
            part = mv[i:i + self.CHUNK]
            bk.take(len(part))
            self._f.write(part)
            self._m.nout += len(part)
        return n

    def __getattr__(self, k):
        return getattr(self._f, k)


# ---------------------------------------------------------------------------------------------------------
# 壓縮
COMPRESSIBLE = ("application/json", "text/", "application/javascript", "image/svg+xml", "application/manifest+json")


def maybe_gzip(h, body: bytes, ctype: str) -> Tuple[bytes, Dict[str, str]]:
    """區網以外、用戶端說可以 → gzip（省流模式用 9 級）。"""
    m: Optional[Meter] = getattr(h, "_meter", None)
    if m is None or not m.remote or len(body) < 1024:
        return body, {}
    tr = m.tr
    if not tr.cfg.get("compress", True):
        return body, {}
    if "gzip" not in (h.headers.get("Accept-Encoding") or "").lower():
        return body, {}
    if not ctype.startswith(COMPRESSIBLE):
        return body, {}
    eco, _ = tr.economy()
    return gzip.compress(body, 9 if eco else 6, mtime=0), {"Content-Encoding": "gzip", "Vary": "Accept-Encoding"}


_MASKS = {k: bytes(b & ((0xFF << k) & 0xFF) for b in range(256)) for k in range(0, 9)}


def _shuffle(b: bytes, w: int = 4) -> bytes:
    return b"".join(b[i::w] for i in range(w))


def pack_floats(b64: str, economy: bool) -> str:
    """float32（base64）→ 位元組重排＋zlib（economy：尾數只留 10 位、zlib 9）。"""
    raw = base64.b64decode(b64)
    if len(raw) % 4:
        return ""
    if economy:
        ba = bytearray(raw)
        n = len(ba) // 4
        ba[0::4] = bytes(n)                             # 尾數最低 8 位
        ba[1::4] = bytes(ba[1::4]).translate(_MASKS[5])  # 再去掉 5 位 → 留 23 - 13 = 10 位
        raw = bytes(ba)
    return base64.b64encode(zlib.compress(_shuffle(raw), 9 if economy else 6)).decode("ascii")


def _pack_arrays(obj: Any, economy: bool) -> Any:
    if isinstance(obj, list):
        return [_pack_arrays(x, economy) for x in obj]
    if isinstance(obj, dict):
        if "n" in obj and isinstance(obj.get("re"), str) and isinstance(obj.get("im"), str):
            re_, im_ = pack_floats(obj["re"], economy), pack_floats(obj["im"], economy)
            if re_ and im_:
                return {"zre": re_, "zim": im_, "n": obj["n"], **({"q": 10} if economy else {})}
        if "n" in obj and isinstance(obj.get("f"), str):
            f = pack_floats(obj["f"], economy)
            if f:
                return {"zf": f, "n": obj["n"], **({"q": 10} if economy else {})}
        return {k: _pack_arrays(v, economy) for k, v in obj.items()}
    return obj


def pack_live(h, out: Dict[str, Any], node: str) -> Dict[str, Any]:
    """即時串流：區網以外、用戶端看得懂（X-Labhub-Enc: zs）→ 曲線用位元組重排＋zlib。"""
    m: Optional[Meter] = getattr(h, "_meter", None)
    if m is None or not m.remote or "zs" not in (h.headers.get("X-Labhub-Enc") or ""):
        return out
    tr = m.tr
    if not tr.cfg.get("compress", True):
        return out
    eco, _ = tr.economy()
    batches = []
    for item in out.get("batches") or []:
        seq, ev = item[0], item[1]
        key = (node, seq, eco)
        packed = tr.cache.get(key)
        if packed is None:
            packed = _pack_arrays(ev, eco)
            tr.cache[key] = packed
            while len(tr.cache) > 400:
                tr.cache.pop(next(iter(tr.cache)))
        batches.append([seq, packed])
    return dict(out, batches=batches, enc="zs", economy=eco)


def thin_trace(trace: Any, limit: int = 300) -> Any:
    """手機的最後一條 trace：縮成最多 limit 點（每一格取離中位數最遠的點，窄的 dip 不會被抹掉）。"""
    if not isinstance(trace, dict):
        return trace
    ys, xs = trace.get("y"), trace.get("x")
    if not isinstance(ys, list) or len(ys) <= limit:
        return trace
    k = -(-len(ys) // limit)
    fin = sorted(v for v in ys if isinstance(v, (int, float)))
    mid = fin[len(fin) // 2] if fin else 0.0
    ny, nx = [], []
    for i in range(0, len(ys), k):
        blk = ys[i:i + k]
        best, dev = None, -1.0
        for j, v in enumerate(blk):
            if isinstance(v, (int, float)) and abs(v - mid) > dev:
                best, dev = j, abs(v - mid)
        ny.append(blk[best] if best is not None else None)
        if isinstance(xs, list):
            xb = [x for x in xs[i:i + k] if isinstance(x, (int, float))]
            nx.append(sum(xb) / len(xb) if xb else None)
    out = dict(trace, y=ny, thinned=len(ys))
    if isinstance(xs, list):
        out["x"] = nx
    return out


# ---------------------------------------------------------------------------------------------------------
def store(hub) -> Traffic:
    tr = getattr(hub, "traffic", None)
    if tr is None:
        tr = hub.traffic = Traffic(hub.cfg.data_dir)
        try:
            from .netusage import store as _nu

            tr.netusage = _nu(hub)
        except Exception:  # noqa: BLE001
            pass
    return tr


from .server import Handler, HubHTTPError, route  # noqa: E402


@route("GET", r"/api/traffic", auth="any")
def _view(h: Handler) -> None:
    try:
        days = int(h._query().get("days") or 31)
    except ValueError:
        days = 31
    h._json({"ok": True, **store(h.hub).view(days)})


@route("PUT", r"/api/traffic/config", auth="owner")
def _config(h: Handler) -> None:
    b = h._json_body() or {}
    if not isinstance(b, dict):
        raise HubHTTPError(400, "格式錯誤")
    cfg = store(h.hub).set_config(b)
    try:
        u = h.user() or {}
        h.hub.platform.audit(u.get("username", ""), "traffic.config", json.dumps(cfg, ensure_ascii=False)[:500])
    except Exception:  # noqa: BLE001
        pass
    h._json({"ok": True, "cfg": cfg})
