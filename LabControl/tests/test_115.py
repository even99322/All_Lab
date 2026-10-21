"""1.0.15：內網 https——Hub 用同一張 Tailscale 憑證在內網開 https（Hub、論文庫），並監測憑證還剩幾天。"""
from __future__ import annotations

import datetime as dt
import http.client
import json
import os
import socket
import socketserver
import ssl
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler

import pytest

DOMAIN = "ccu-qel-nas.tailtest.ts.net"


def _make_cert(days: float, cn: str = DOMAIN, issuer: str = ""):
    """自簽憑證（PEM 憑證, PEM 私鑰, 到期時間）。"""
    crypto = pytest.importorskip("cryptography")
    del crypto
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    na = now + dt.timedelta(days=days)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer or cn)]))
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(na)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False)
            .sign(key, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()),
            na.timestamp())


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _https_get(port: int, path: str, cafile_pem: bytes, host: str = DOMAIN):
    """用指定的憑證當 CA 驗證（網域要對），回傳 (status, body, 伺服器憑證 DER)。"""
    ctx = ssl.create_default_context(cadata=cafile_pem.decode())
    raw = socket.create_connection(("127.0.0.1", port), timeout=10)
    s = ctx.wrap_socket(raw, server_hostname=host)
    der = s.getpeercert(binary_form=True)
    s.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n".encode())
    data = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
    s.close()
    head, _, body = data.partition(b"\r\n\r\n")
    return int(head.split()[1]), body, der


def _write(tls_dir, cert: bytes, key: bytes) -> None:
    tls_dir.mkdir(parents=True, exist_ok=True)
    (tls_dir / "cert.pem").write_bytes(cert)
    (tls_dir / "key.pem").write_bytes(key)


def test_cert_info_reads_validity():
    from labhub.lan_tls import TLSError, cert_info

    cert, _key, na = _make_cert(63, issuer="Test CA E5")
    info = cert_info(cert)
    assert abs(info["not_after"] - na) < 2
    assert info["subject"] == DOMAIN and info["issuer"] == "Test CA E5" and len(info["sha256"]) == 64
    with pytest.raises(TLSError):
        cert_info(b"not a cert")


def test_lan_https_serves_hub_and_paperlib_and_hot_reloads(tmp_path, monkeypatch):
    from labhub.server import Config, make_server
    from tests import fake_paperlib

    monkeypatch.setenv("LABHUB_TAILSCALE_SOCKET", str(tmp_path / "no.sock"))
    monkeypatch.setenv("LABHUB_TLS_DOMAIN", DOMAIN)
    monkeypatch.setenv("LABHUB_TLS_PORT", "0")
    pport = _free_port()
    monkeypatch.setenv("LABHUB_TLS_PAPERLIB_PORT", str(pport))
    psrv, _, purl = fake_paperlib.start()
    cert, key, _na = _make_cert(80)
    _write(tmp_path / "hub" / "tls", cert, key)
    srv, state = make_server(Config(tmp_path / "hub", token="dev", paperlib_url=purl,
                                    paperlib_public="http://192.168.50.2:8080"), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tls = state.tls
    try:
        assert tls.status()["level"] == "ok" and tls.status()["days_left"] > 78     # 還沒開埠也知道剩幾天
        tls.start(srv.RequestHandlerClass, "127.0.0.1")
        for _ in range(100):
            if tls.listeners.get("hub", {}).get("ok") and tls.listeners.get("paperlib", {}).get("ok"):
                break
            time.sleep(0.05)
        st = tls.status()
        assert st["source"] == "files" and st["level"] == "ok", st
        hport = st["listeners"]["hub"]["port"]
        assert st["urls"] == {"hub": f"https://{DOMAIN}:{hport}", "paperlib": f"https://{DOMAIN}:{pport}"}
        # Hub 本身（同一個請求處理）
        code, body, der = _https_get(hport, "/api/ping", cert)
        assert code == 200 and json.loads(body)["server"]
        # 論文庫（Hub 轉過去）
        code, body, _ = _https_get(pport, "/api/site", cert)
        assert code == 200 and b"{" in body
        # 用 https 網域進來 → 論文庫連結也給 https；用內網 IP 進來 → 照舊
        from labhub.platform import REQUEST

        REQUEST.host = DOMAIN
        assert state.platform.paperlib_public == f"https://{DOMAIN}:{pport}"
        REQUEST.host = "192.168.50.2"
        assert state.platform.paperlib_public == "http://192.168.50.2:8080"
        REQUEST.host = ""
        # 換新憑證 → 不用重開，下一條連線就是新的
        cert2, key2, _ = _make_cert(89)
        _write(tmp_path / "hub" / "tls", cert2, key2)
        st = tls.check()
        assert st["level"] == "ok"
        code, _body, der2 = _https_get(hport, "/api/ping", cert2)
        assert code == 200 and der2 != der
        # 握手失敗（不是 TLS）不影響之後的連線
        raw = socket.create_connection(("127.0.0.1", hport), timeout=5)
        raw.sendall(b"GET / HTTP/1.0\r\n\r\n")
        raw.close()
        assert _https_get(hport, "/api/ping", cert2)[0] == 200
    finally:
        tls.stop()
        srv.shutdown()
        psrv.shutdown()


@pytest.mark.parametrize("days,level", [(40, "ok"), (15, "warn"), (5, "bad"), (-1, "bad")])
def test_days_left_levels(tmp_path, monkeypatch, days, level):
    from labhub.lan_tls import LanTLS
    from labhub.server import Config

    monkeypatch.setenv("LABHUB_TAILSCALE_SOCKET", str(tmp_path / "no.sock"))
    cert, key, _ = _make_cert(days if days > 0 else -0.5)
    cfg = Config(tmp_path / "hub", token="dev")
    _write(tmp_path / "hub" / "tls", cert, key)
    st = LanTLS(cfg).check()
    assert st["level"] == level, st
    if days < 0:
        assert "過期" in st["message"]


def test_not_configured_is_off(tmp_path, monkeypatch):
    from labhub.lan_tls import LanTLS
    from labhub.server import Config

    monkeypatch.setenv("LABHUB_TAILSCALE_SOCKET", str(tmp_path / "no.sock"))
    st = LanTLS(Config(tmp_path / "hub", token="dev")).check()
    assert st["level"] == "off" and st["source"] == "none" and st["days_left"] is None
    monkeypatch.setenv("LABHUB_TLS", "off")
    st = LanTLS(Config(tmp_path / "hub2", token="dev")).check()
    assert st["source"] == "off" and st["level"] == "off"


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or sys.platform == "win32", reason="unix socket")
def test_fetches_and_renews_from_tailscale_localapi(tmp_path, monkeypatch):
    """假的 tailscaled LocalAPI：status → CertDomains；cert?type=pair → 私鑰＋憑證。換新時寫檔、記錄、熱更新。"""
    from labhub.lan_tls import LanTLS
    from labhub.server import Config

    cur = {}
    hosts = []
    cert, key, _ = _make_cert(60)
    cur.update(cert=cert, key=key)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def address_string(self):
            return "unix"

        def do_GET(self):  # noqa: N802
            hosts.append(self.headers.get("Host"))
            if self.headers.get("Host") != "local-tailscaled.sock":
                self.send_response(403)
                self.end_headers()
                return
            u = urllib.parse.urlsplit(self.path)
            if u.path == "/localapi/v0/status":
                body = json.dumps({"CertDomains": [DOMAIN], "Self": {"DNSName": DOMAIN + "."}}).encode()
            elif u.path == f"/localapi/v0/cert/{DOMAIN}" and "type=pair" in u.query:
                body = cur["key"] + cur["cert"]
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class S(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    sock = f"/tmp/ts-{os.getpid()}-{time.time_ns() % 100000}.sock"     # unix socket 路徑不能太長
    srv = S(sock, H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("LABHUB_TAILSCALE_SOCKET", sock)
    monkeypatch.delenv("LABHUB_TLS_DOMAIN", raising=False)
    audits = []
    try:
        tls = LanTLS(Config(tmp_path / "hub", token="dev"), audit=lambda *a: audits.append(a))
        st = tls.check()
        assert st["source"] == "tailscale" and st["domain"] == DOMAIN and st["level"] == "ok", st
        assert (tmp_path / "hub" / "tls" / "cert.pem").read_bytes().strip() == cert.strip()
        assert b"PRIVATE KEY" in (tmp_path / "hub" / "tls" / "key.pem").read_bytes()
        assert set(hosts) == {"local-tailscaled.sock"} and not audits and st["renewed_at"] is None
        sha = st["sha256"]
        assert tls.check()["sha256"] == sha and not audits                 # 一樣的憑證：不重寫、不記錄
        # Tailscale 續約了 → 換新、記錄
        cert2, key2, na2 = _make_cert(90)
        cur.update(cert=cert2, key=key2)
        st = tls.check()
        assert st["sha256"] != sha and abs(st["not_after"] - na2) < 2 and st["renewed_at"]
        assert audits and audits[0][1] == "tls-renew"
        assert tls.loaded_sha == st["sha256"]                               # 已經載入新的 TLS 設定
        # 狀態留在 data/tls/state.json：Hub 重開後還知道上次換新的時間
        assert LanTLS(Config(tmp_path / "hub", token="dev")).status()["renewed_at"] == st["renewed_at"]
        # Tailscale 掛了 → 用手上的憑證繼續，狀態顯示錯誤
        srv.shutdown()
        srv.server_close()
        os.unlink(sock)
        open(sock, "w").close()
        st = tls.check()
        assert st["level"] == "warn" and "Tailscale" in st["last_error"] and st["days_left"] > 80
    finally:
        try:
            os.unlink(sock)
        except OSError:
            pass


def test_tls_routes_need_owner(tmp_path, monkeypatch):
    from labhub.server import Config, make_server

    monkeypatch.setenv("LABHUB_TAILSCALE_SOCKET", str(tmp_path / "no.sock"))
    srv, state = make_server(Config(tmp_path / "hub", token="dev"), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        c.request("GET", "/api/platform/console/tls")
        assert c.getresponse().status in (401, 403)
        c.close()
        st = state.console.status()
        assert st["tls"]["level"] == "off"
    finally:
        srv.shutdown()
