"""1.0.15：內網 https——內網跟 VPN 用同一個網址、同一張 Tailscale 憑證；Hub 監測憑證還剩幾天。

    VPN： 瀏覽器 ─ MagicDNS → 100.114.33.20 ─ tailscale serve（:8443 → Hub、:8444 → 論文庫）
    內網：瀏覽器 ─ 實驗室 DNS → 192.168.50.2 ─ 這裡的 TLS（:8443 → Hub 本身、:8444 → 論文庫）
                                               └ 憑證：tailscaled 的 LocalAPI（/localapi/v0/cert/<網域>?type=pair），
                                                 每 12 小時取一次；Tailscale 快到期會自己續約，換了新憑證這裡熱更新，不用重新啟動

實驗室 DNS 要把 ``<NAS>.<tailnet>.ts.net`` 指到 192.168.50.2（Synology「DNS Server」套件或路由器），步驟見 docs/PLATFORM.md 0p。

設定（環境變數）::

    LABHUB_TLS                 auto（預設：有憑證就開）/ off
    LABHUB_TLS_DOMAIN          網域；不寫 = 問 Tailscale
    LABHUB_TAILSCALE_SOCKET    tailscaled.sock（預設 /tailscale/tailscaled.sock，再試 /var/run/tailscale/tailscaled.sock）
    LABHUB_TLS_PORT            Hub 的 https 埠（預設 8443）
    LABHUB_TLS_PAPERLIB_PORT   論文庫的 https 埠（預設 8444；0 = 不開）
    LABHUB_TLS_WARN_DAYS       剩幾天以內要警告（預設 20；Tailscale 剩 30 天左右就會自己續約）

沒有 Tailscale socket 時也可以手動把憑證放在 ``data/tls/cert.pem``、``key.pem``（換檔後 12 小時內自動載入，或按「立即檢查」）。
只用標準函式庫。
"""
from __future__ import annotations

import base64
import calendar
import hashlib
import http.client
import json
import logging
import os
import re
import select
import socket
import socketserver
import ssl
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("labhub.tls")

CHECK_EVERY_S = 12 * 3600.0
RETRY_S = 3600.0
BAD_DAYS = 7
SOCKETS = ("/tailscale/tailscaled.sock", "/var/run/tailscale/tailscaled.sock",
           "/var/packages/Tailscale/var/tailscaled.sock")
_PEM_CERT = re.compile(rb"-----BEGIN CERTIFICATE-----\s+.+?-----END CERTIFICATE-----\s*", re.S)
_PEM_KEY = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----\s+.+?-----END [A-Z ]*PRIVATE KEY-----\s*", re.S)


class TLSError(RuntimeError):
    pass


# ---- 讀憑證（不用第三方套件：自己解 DER 的有效期間）---------------------------------------------------------
def _tlv(b: bytes, i: int) -> Tuple[int, int, int]:
    """(tag, 內容開始位置, 內容長度)"""
    tag = b[i]
    n = b[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(b[i:i + k], "big")
        i += k
    return tag, i, n


def _time(tag: int, raw: bytes) -> float:
    s = raw.decode("ascii").rstrip("Z")
    if tag == 0x17:                                   # UTCTime YYMMDDHHMMSS
        y = int(s[:2])
        s = f"{1900 + y if y >= 50 else 2000 + y}{s[2:]}"
    return float(calendar.timegm(time.strptime(s[:14], "%Y%m%d%H%M%S")))


def _cn(b: bytes, start: int, length: int) -> str:
    seg = b[start:start + length]
    k = seg.find(b"\x06\x03\x55\x04\x03")             # OID 2.5.4.3 commonName
    if k < 0:
        return ""
    _t, s, n = _tlv(seg, k + 5)
    return seg[s:s + n].decode("utf-8", "replace")


def cert_info(pem: bytes) -> Dict[str, Any]:
    """第一張憑證（網站本身）的有效期間、簽發者、主體、SHA-256 指紋。"""
    m = _PEM_CERT.search(pem)
    if not m:
        raise TLSError("檔案裡沒有憑證（-----BEGIN CERTIFICATE-----）")
    body = re.sub(rb"-----[^-]+-----|\s", b"", m.group(0))
    der = base64.b64decode(body)
    try:
        _t, i, _n = _tlv(der, 0)                      # Certificate
        _t, i, _n = _tlv(der, i)                      # tbsCertificate
        t, s, n = _tlv(der, i)
        if t == 0xA0:                                 # [0] version
            i = s + n
            t, s, n = _tlv(der, i)
        i = s + n                                     # serialNumber
        t, s, n = _tlv(der, i)
        i = s + n                                     # signature
        t, s, n = _tlv(der, i)                        # issuer
        issuer = _cn(der, s, n)
        i = s + n
        t, s, n = _tlv(der, i)                        # validity
        t1, s1, n1 = _tlv(der, s)
        t2, s2, n2 = _tlv(der, s1 + n1)
        nb, na = _time(t1, der[s1:s1 + n1]), _time(t2, der[s2:s2 + n2])
        i = s + n
        t, s, n = _tlv(der, i)                        # subject
        subject = _cn(der, s, n)
    except (IndexError, ValueError) as e:
        raise TLSError(f"看不懂這張憑證：{e}") from None
    return {"not_before": nb, "not_after": na, "issuer": issuer, "subject": subject,
            "sha256": hashlib.sha256(der).hexdigest()}


# ---- tailscaled LocalAPI（unix socket 上的 HTTP）----------------------------------------------------------
class _UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("local-tailscaled.sock", timeout=timeout)   # Host 一定要是這個，tailscaled 才肯回答
        self._path = path

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


def local_api(sock: str, path: str, timeout: float = 120.0) -> bytes:
    try:
        c = _UnixHTTP(sock, timeout)
        c.request("GET", path, headers={"Tailscale-Cap": "1"})
        r = c.getresponse()
        body = r.read()
        c.close()
    except (OSError, http.client.HTTPException) as e:
        raise TLSError(f"連不到 Tailscale（{sock}）：{e}") from None
    if r.status != 200:
        raise TLSError(f"Tailscale 回應 {r.status}：{body[:300].decode('utf-8', 'replace').strip()}")
    return body


# ---- TLS 伺服器：握手放在每條連線自己的執行緒（慢的客戶端不會卡住其他人）-------------------------------------
class _TLSMixin:
    tls: "LanTLS"

    def get_request(self):  # type: ignore[override]
        sock, addr = self.socket.accept()  # type: ignore[attr-defined]
        ctx = self.tls.ctx
        if ctx is None:
            sock.close()
            raise OSError("沒有憑證")
        return ctx.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

    def finish_request(self, request, client_address) -> None:  # type: ignore[override]
        try:
            request.settimeout(20)
            request.do_handshake()
            request.settimeout(None)
        except (ssl.SSLError, OSError) as e:
            log.debug("TLS 握手失敗 %s：%s", client_address, e)
            return
        super().finish_request(request, client_address)  # type: ignore[misc]

    def handle_error(self, request, client_address) -> None:  # type: ignore[override]
        log.debug("TLS 連線錯誤 %s", client_address, exc_info=True)


def _pump(a: ssl.SSLSocket, b: socket.socket, idle: float = 900.0) -> None:
    """瀏覽器（TLS）⇄ 論文庫（TCP），同一個執行緒（OpenSSL 的連線不能兩個執行緒同時讀寫）。"""
    while True:
        if a.pending():
            ready = [a]
        else:
            ready, _w, _x = select.select([a, b], [], [], idle)
            if not ready:
                return
        for s in ready:
            other = b if s is a else a
            try:
                data = s.recv(65536)
            except ssl.SSLWantReadError:
                continue
            except OSError:
                return
            if not data:
                return
            other.sendall(data)


class _PaperlibProxy(socketserver.BaseRequestHandler):
    target: Tuple[str, int] = ("paperlib", 8080)

    def handle(self) -> None:
        try:
            up = socket.create_connection(self.target, timeout=10)
        except OSError as e:
            body = f"Hub 連不到論文庫（{self.target[0]}:{self.target[1]}）：{e}".encode("utf-8")
            self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/plain; charset=utf-8\r\n"
                                 b"Connection: close\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            return
        up.settimeout(None)
        try:
            _pump(self.request, up)
        finally:
            up.close()


class _HubTLSServer(_TLSMixin, socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# ---------------------------------------------------------------------------------------------------------
class LanTLS:
    """內網 https：憑證（Tailscale 或手動檔案）、兩個 TLS 埠、狀態（剩幾天）。"""

    def __init__(self, cfg: Any, audit: Optional[Callable[[str, str, str], None]] = None) -> None:
        self.cfg = cfg
        self.audit = audit
        self.dir = Path(cfg.data_dir) / "tls"
        self.mode_env = (os.environ.get("LABHUB_TLS", "auto") or "auto").strip().lower()
        self.domain_env = os.environ.get("LABHUB_TLS_DOMAIN", "").strip().rstrip(".")
        self.socket_env = os.environ.get("LABHUB_TAILSCALE_SOCKET", "").strip()
        self.hub_port = _env_int("LABHUB_TLS_PORT", 8443)
        self.paperlib_port = _env_int("LABHUB_TLS_PAPERLIB_PORT", 8444)
        self.warn_days = _env_int("LABHUB_TLS_WARN_DAYS", 20)
        self.lock = threading.RLock()
        self.ctx: Optional[ssl.SSLContext] = None
        self.loaded_sha = ""
        self.info: Optional[Dict[str, Any]] = None
        self.domain = self.domain_env
        self.listeners: Dict[str, Dict[str, Any]] = {}
        self._servers: List[Any] = []
        self._handler: Any = None
        self._host = "0.0.0.0"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        st = self._state()
        self.last_check: Optional[float] = st.get("last_check")
        self.last_error: str = st.get("last_error") or ""
        self.renewed_at: Optional[float] = st.get("renewed_at")
        self.source: str = st.get("source") or ""
        if not self.domain:
            self.domain = st.get("domain") or ""
        self._read_files()

    # ---- 檔案 ---------------------------------------------------------------------------------------
    @property
    def cert_file(self) -> Path:
        return self.dir / "cert.pem"

    @property
    def key_file(self) -> Path:
        return self.dir / "key.pem"

    def _state(self) -> Dict[str, Any]:
        try:
            return json.loads((self.dir / "state.json").read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "state.json").write_text(json.dumps(
                {"last_check": self.last_check, "last_error": self.last_error, "renewed_at": self.renewed_at,
                 "source": self.source, "domain": self.domain}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _read_files(self) -> Optional[bytes]:
        try:
            pem = self.cert_file.read_bytes()
        except OSError:
            self.info = None
            return None
        try:
            self.info = cert_info(pem)
        except TLSError as e:
            self.info = None
            self.last_error = f"data/tls/cert.pem：{e}"
            return None
        return pem

    @staticmethod
    def _write(path: Path, data: bytes, private: bool = False) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        if private:
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        os.replace(tmp, path)

    # ---- Tailscale ----------------------------------------------------------------------------------
    def socket_path(self) -> str:
        for p in ([self.socket_env] if self.socket_env else list(SOCKETS)):
            if p and os.path.exists(p):
                return p
        return ""

    def _tailscale_domain(self, sock: str) -> str:
        st = json.loads(local_api(sock, "/localapi/v0/status", timeout=15).decode("utf-8"))
        domains = [d for d in (st.get("CertDomains") or []) if d]
        if not domains:
            raise TLSError("Tailscale 沒有可用的憑證網域：到 Tailscale 管理頁 DNS 打開 MagicDNS 與 HTTPS Certificates")
        return str(domains[0]).rstrip(".")

    def _fetch_tailscale(self, sock: str) -> bool:
        """向 tailscaled 取憑證（它會自己續約）；內容跟現在的不同 → 寫檔，回傳 True。"""
        if not self.domain_env:
            self.domain = self._tailscale_domain(sock)
        q = urllib.parse.quote(self.domain, safe="")
        pair = local_api(sock, f"/localapi/v0/cert/{q}?type=pair", timeout=180)
        key = _PEM_KEY.search(pair)
        certs = _PEM_CERT.findall(pair)
        if not key or not certs:
            raise TLSError("Tailscale 回傳的憑證格式不對")
        chain = b"".join(c.strip() + b"\n" for c in certs)
        try:
            old = self.cert_file.read_bytes()
        except OSError:
            old = b""
        if old == chain and self.key_file.exists():
            return False
        self.dir.mkdir(parents=True, exist_ok=True)
        self._write(self.key_file, key.group(0).strip() + b"\n", private=True)
        self._write(self.cert_file, chain)
        return True

    # ---- 檢查 / 續約 --------------------------------------------------------------------------------
    def check(self) -> Dict[str, Any]:
        """取最新憑證（有 Tailscale 的話）→ 讀檔 → 換了就熱更新 TLS → 還沒開的埠打開。"""
        with self.lock:
            errors: List[str] = []
            if self.mode_env == "off":
                self.source = "off"
            else:
                sock = self.socket_path()
                if sock:
                    self.source = "tailscale"
                    try:
                        before = (self.info or {}).get("sha256")
                        if self._fetch_tailscale(sock):
                            self._read_files()
                            if before and (self.info or {}).get("sha256") != before:
                                self.renewed_at = time.time()
                                if self.audit:
                                    na = (self.info or {}).get("not_after")
                                    self.audit("Hub", "tls-renew", f"{self.domain} 到 {time.strftime('%Y-%m-%d', time.localtime(na or 0))}")
                                log.info("內網 https：已換成新的憑證（%s）", self.domain)
                    except (TLSError, ValueError) as e:
                        errors.append(str(e))
                elif self.cert_file.exists():
                    self.source = "files"
                else:
                    self.source = "none"
                pem = self._read_files()
                if pem is not None and self.info:
                    if not self.domain and self.info.get("subject"):
                        self.domain = str(self.info["subject"]).lstrip("*.")
                    if self.info["sha256"] != self.loaded_sha:
                        try:
                            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                            ctx.set_alpn_protocols(["http/1.1"])
                            ctx.load_cert_chain(str(self.cert_file), str(self.key_file))
                            self.ctx = ctx
                            self.loaded_sha = self.info["sha256"]
                        except (ssl.SSLError, OSError) as e:
                            errors.append(f"憑證和私鑰不能用：{e}")
                    if self.ctx is not None and self._handler is not None:
                        self._open_listeners()
            self.last_check = time.time()
            self.last_error = "；".join(errors)
            self._save_state()
            return self.status()

    def _open_listeners(self) -> None:
        want = [("hub", self.hub_port)]
        target = self._paperlib_target()
        if self.paperlib_port and target:
            want.append(("paperlib", self.paperlib_port))
        for name, port in want:
            if self.listeners.get(name, {}).get("ok"):
                continue
            try:
                if name == "hub":
                    srv = _HubTLSServer((self._host, port), self._handler)
                else:
                    handler = type("PaperlibProxy", (_PaperlibProxy,), {"target": target})
                    srv = _HubTLSServer((self._host, port), handler)
                srv.tls = self
                threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5},
                                 name=f"tls-{name}", daemon=True).start()
                self._servers.append(srv)
                self.listeners[name] = {"port": srv.server_address[1], "ok": True, "error": ""}
                log.info("內網 https：%s 在 :%d", name, srv.server_address[1])
            except OSError as e:
                self.listeners[name] = {"port": port, "ok": False, "error": str(e)}
                log.warning("內網 https：%s 的埠 %d 開不起來：%s", name, port, e)

    def _paperlib_target(self) -> Optional[Tuple[str, int]]:
        u = urllib.parse.urlsplit(getattr(self.cfg, "paperlib_url", "") or "")
        if not u.hostname:
            return None
        return u.hostname, u.port or (443 if u.scheme == "https" else 80)

    # ---- 啟動 / 停止 --------------------------------------------------------------------------------
    def start(self, handler: Any, host: str = "0.0.0.0") -> None:
        """Hub 啟動時：記下 Hub 的請求處理類別，背景每 12 小時檢查一次（出錯或快到期時每小時）。"""
        if self.mode_env == "off":
            self.source = "off"
            return
        self._handler, self._host = handler, host
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="lan-tls", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                st = self.check()
            except Exception:  # noqa: BLE001 - 背景工作不能停
                log.exception("內網 https 檢查失敗")
                st = {"level": "bad"}
            wait = CHECK_EVERY_S if st.get("level") == "ok" and not self.last_error else RETRY_S
            self._wake.wait(wait)
            self._wake.clear()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for s in self._servers:
            try:
                s.shutdown()
                s.server_close()
            except OSError:
                pass

    # ---- 狀態 --------------------------------------------------------------------------------------
    def url(self, which: str = "hub") -> str:
        lst = self.listeners.get(which) or {}
        port = lst.get("port")
        if not self.domain or not port or not lst.get("ok"):
            return ""
        return f"https://{self.domain}" + ("" if port == 443 else f":{port}")

    def paperlib_url_for(self, host: str) -> str:
        """使用者用 https 網域連 Hub 時，論文庫也給 https 網址。"""
        return self.url("paperlib") if host and host == self.domain else ""

    def _guess_source(self) -> str:
        if self.mode_env == "off":
            return "off"
        if self.socket_path():
            return "tailscale"
        return "files" if self.cert_file.exists() else "none"

    def status(self) -> Dict[str, Any]:
        if not self.source:
            self.source = self._guess_source()
        info = self.info or {}
        na = info.get("not_after")
        days = (na - time.time()) / 86400 if na else None
        if self.source == "off":
            level, msg = "off", "已關閉（LABHUB_TLS=off）"
        elif days is None:
            level = "off" if self.source == "none" else "bad"
            msg = ("還沒設定：docker-compose 要掛上 Tailscale 的 socket（或把憑證放在 data/tls）"
                   if self.source == "none" else self.last_error or "沒有憑證")
        elif days <= 0:
            level, msg = "bad", "憑證已過期，https 打不開"
        elif days <= BAD_DAYS:
            level, msg = "bad", f"只剩 {days:.0f} 天，Tailscale 沒有續約成功"
        elif days <= self.warn_days:
            level, msg = "warn", f"剩 {days:.0f} 天：Tailscale 一般在剩 30 天左右續約，請確認 NAS 上的 Tailscale 正常"
        else:
            level, msg = "ok", "正常：到期前 Tailscale 會自己續約"
        if level == "ok" and self.last_error:
            level = "warn"
        if level in ("ok", "warn") and any(not v.get("ok") for v in self.listeners.values()):
            level = "warn"
        return {"level": level, "message": msg, "source": self.source, "domain": self.domain,
                "days_left": None if days is None else round(days, 1), "not_after": na,
                "not_before": info.get("not_before"), "issuer": info.get("issuer", ""), "sha256": info.get("sha256", ""),
                "last_check": self.last_check, "last_error": self.last_error, "renewed_at": self.renewed_at,
                "warn_days": self.warn_days, "socket": self.socket_path(),
                "listeners": {k: dict(v) for k, v in self.listeners.items()},
                "urls": {"hub": self.url("hub"), "paperlib": self.url("paperlib")}}
