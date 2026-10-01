"""對外開放時的安全措施：登入失敗鎖定、兩步驟驗證（TOTP）、HTTPS 判斷。"""
import base64
import hashlib
import hmac
import secrets
import struct
import time

from fastapi import HTTPException, Request

from . import db

MAX_FAIL = 8            # 同一帳號 15 分鐘內失敗 8 次
MAX_FAIL_IP = 20        # 同一 IP（實驗室共用出口 IP 時較寬鬆）
WINDOW = 15 * 60
LOCK = 15 * 60          # 鎖 15 分鐘


def client_ip(request: Request) -> str:
    # 經過 Cloudflare Tunnel 時用 CF-Connecting-IP；uvicorn 的 --proxy-headers 已處理 X-Forwarded-For
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")


def is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    if request.headers.get("x-forwarded-proto", "").lower() == "https":
        return True
    return '"https"' in request.headers.get("cf-visitor", "")


def check_locked(keys: list[str]) -> None:
    now = time.time()
    for k in keys:
        r = db.get().execute("SELECT until FROM login_fail WHERE key=?", (k,)).fetchone()
        if r and r["until"] > now:
            mins = int((r["until"] - now) // 60) + 1
            raise HTTPException(429, f"登入失敗太多次，請 {mins} 分鐘後再試")


def record_fail(keys: list[str]) -> None:
    now = time.time()
    con = db.get()
    for k in keys:
        r = con.execute("SELECT n, first_at FROM login_fail WHERE key=?", (k,)).fetchone()
        if r is None or now - r["first_at"] > WINDOW:
            con.execute("INSERT OR REPLACE INTO login_fail(key, n, first_at, until) VALUES(?,?,?,0)", (k, 1, now))
        else:
            n = r["n"] + 1
            lim = MAX_FAIL_IP if k.startswith("ip:") else MAX_FAIL
            con.execute("UPDATE login_fail SET n=?, until=? WHERE key=?", (n, now + LOCK if n >= lim else 0, k))
    con.execute("DELETE FROM login_fail WHERE first_at < ? AND until < ?", (now - 86400, now))


def clear_fail(keys: list[str]) -> None:
    for k in keys:
        db.get().execute("DELETE FROM login_fail WHERE key=?", (k,))


# ------------------------------------------------------------------ TOTP（RFC 6238，Google Authenticator 等 App 通用）
def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    d = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = d[-1] & 0x0F
    v = (struct.unpack(">I", d[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{v:06d}"


def verify(secret: str, code: str, window: int = 1) -> bool:
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != 6 or not secret:
        return False
    t = int(time.time() // 30)
    return any(hmac.compare_digest(_code(secret, t + i), code) for i in range(-window, window + 1))


def otpauth_uri(secret: str, account: str, issuer: str) -> str:
    from urllib.parse import quote
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"


def qr_svg(text: str) -> str:
    try:
        import segno
    except ImportError:
        return ""
    return segno.make(text, error="m").svg_inline(scale=5, dark="#111111", light="#ffffff", border=2)
