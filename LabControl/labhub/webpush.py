"""1.0.25：Web Push（手機 / 電腦瀏覽器的系統通知），只用標準函式庫。

Hub 跑在 python:3.12-slim、不裝任何套件，所以這裡自己實作推播需要的密碼學：
* P-256 橢圓曲線：產生金鑰、ECDH（RFC 8291 加密內容）、ECDSA 簽章（RFC 8292 VAPID 的 ES256 JWT）；
* HKDF（HMAC-SHA-256）；
* AES-128-GCM（RFC 8188 的 aes128gcm 內容編碼）。
每則通知只算幾次，純 Python 每則約幾十 ms，夠用。正確性由 tests 用 `cryptography` 套件交叉驗證。

    keys = VapidKeys.load_or_create(path)
    status, text = send(subscription, payload_bytes, keys, subject="https://…", ttl=…)
       subscription = {"endpoint": …, "keys": {"p256dh": base64url, "auth": base64url}}
       status 201 / 200 = 已送到推播服務；404 / 410 = 這個訂閱已失效（刪掉）
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# ---- P-256 ------------------------------------------------------------------
P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
A = P - 3
B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
G = (0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
     0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5)


def _inv(x: int, m: int) -> int:
    return pow(x, -1, m)


def _jdouble(p):
    x, y, z = p
    if y == 0 or z == 0:
        return (0, 1, 0)
    ysq = y * y % P
    s = 4 * x * ysq % P
    zz = z * z % P
    m = 3 * (x - zz) * (x + zz) % P            # a = -3
    nx = (m * m - 2 * s) % P
    ny = (m * (s - nx) - 8 * ysq * ysq) % P
    nz = 2 * y * z % P
    return (nx, ny, nz)


def _jadd(p, q):
    if p[2] == 0:
        return q
    if q[2] == 0:
        return p
    x1, y1, z1 = p
    x2, y2, z2 = q
    z1z1, z2z2 = z1 * z1 % P, z2 * z2 % P
    u1, u2 = x1 * z2z2 % P, x2 * z1z1 % P
    s1, s2 = y1 * z2 * z2z2 % P, y2 * z1 * z1z1 % P
    if u1 == u2:
        return _jdouble(p) if s1 == s2 else (0, 1, 0)
    h = (u2 - u1) % P
    r = (s2 - s1) % P
    hh = h * h % P
    hhh = h * hh % P
    v = u1 * hh % P
    nx = (r * r - hhh - 2 * v) % P
    ny = (r * (v - nx) - s1 * hhh) % P
    nz = h * z1 * z2 % P
    return (nx, ny, nz)


def _affine(p) -> Tuple[int, int]:
    x, y, z = p
    if z == 0:
        raise ValueError("無窮遠點")
    zi = _inv(z, P)
    zi2 = zi * zi % P
    return (x * zi2 % P, y * zi2 * zi % P)


def mul(k: int, pt: Tuple[int, int] = G) -> Tuple[int, int]:
    """純量乘法（Montgomery ladder 形式，固定步數）。"""
    r0, r1 = (0, 1, 0), (pt[0], pt[1], 1)
    for i in reversed(range(256)):
        if (k >> i) & 1:
            r0, r1 = _jadd(r0, r1), _jdouble(r1)
        else:
            r1, r0 = _jadd(r0, r1), _jdouble(r0)
    return _affine(r0)


def on_curve(pt: Tuple[int, int]) -> bool:
    x, y = pt
    return 0 <= x < P and 0 <= y < P and (y * y - (x * x * x + A * x + B)) % P == 0


def point_bytes(pt: Tuple[int, int]) -> bytes:
    return b"\x04" + pt[0].to_bytes(32, "big") + pt[1].to_bytes(32, "big")


def point_from(raw: bytes) -> Tuple[int, int]:
    if len(raw) != 65 or raw[0] != 4:
        raise ValueError("不是未壓縮的 P-256 公鑰")
    pt = (int.from_bytes(raw[1:33], "big"), int.from_bytes(raw[33:], "big"))
    if not on_curve(pt):
        raise ValueError("公鑰不在 P-256 曲線上")
    return pt


def new_private() -> int:
    return secrets.randbelow(N - 1) + 1


def ecdh(priv: int, pub: Tuple[int, int]) -> bytes:
    return mul(priv, pub)[0].to_bytes(32, "big")


def _rfc6979_k(priv: int, h: bytes) -> int:
    """RFC 6979 決定性 k（不靠亂數品質）。"""
    x = priv.to_bytes(32, "big")
    hm = (int.from_bytes(h, "big") % N).to_bytes(32, "big")
    v, k = b"\x01" * 32, b"\x00" * 32
    k = hmac.new(k, v + b"\x00" + x + hm, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b"\x01" + x + hm, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    while True:
        v = hmac.new(k, v, hashlib.sha256).digest()
        cand = int.from_bytes(v, "big")
        if 1 <= cand < N:
            return cand
        k = hmac.new(k, v + b"\x00", hashlib.sha256).digest()
        v = hmac.new(k, v, hashlib.sha256).digest()


def sign_es256(priv: int, msg: bytes) -> bytes:
    """ECDSA P-256 / SHA-256，回傳 JWS 用的 r‖s（64 bytes）。"""
    h = hashlib.sha256(msg).digest()
    e = int.from_bytes(h, "big")
    while True:
        k = _rfc6979_k(priv, h)
        r = mul(k)[0] % N
        s = _inv(k, N) * (e + r * priv) % N
        if r and s:
            return r.to_bytes(32, "big") + s.to_bytes(32, "big")


# ---- AES-128-GCM --------------------------------------------------------------
def _make_sbox() -> bytes:
    """AES S-box：GF(2⁸) 反元素＋仿射變換（用算的，不抄表）。"""
    def gmul(a, b):
        r = 0
        while b:
            if b & 1:
                r ^= a
            a = ((a << 1) ^ 0x11b) if a & 0x80 else a << 1
            b >>= 1
        return r
    inv = [0] * 256
    for a in range(1, 256):
        for b in range(1, 256):
            if gmul(a, b) == 1:
                inv[a] = b
                break
    out = []
    for a in range(256):
        x = inv[a]
        y = x
        for k in range(1, 5):
            y ^= ((x << k) | (x >> (8 - k))) & 0xff
        out.append(y ^ 0x63)
    return bytes(out)


_SBOX = _make_sbox()
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36)


def _xt(a: int) -> int:
    return ((a << 1) ^ 0x1b) & 0xff if a & 0x80 else a << 1


def _expand(key: bytes):
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return [sum((w[4 * r + c] for c in range(4)), []) for r in range(11)]


def _encrypt_block(rk, block: bytes) -> bytes:
    s = [b ^ k for b, k in zip(block, rk[0])]
    for rnd in range(1, 11):
        s = [_SBOX[b] for b in s]
        s = [s[(i + 4 * (i % 4)) % 16] for i in range(16)]          # ShiftRows（column-major）
        if rnd != 10:
            t = []
            for c in range(4):
                a = s[4 * c:4 * c + 4]
                x = a[0] ^ a[1] ^ a[2] ^ a[3]
                t += [a[0] ^ x ^ _xt(a[0] ^ a[1]), a[1] ^ x ^ _xt(a[1] ^ a[2]),
                      a[2] ^ x ^ _xt(a[2] ^ a[3]), a[3] ^ x ^ _xt(a[3] ^ a[0])]
            s = t
        s = [b ^ k for b, k in zip(s, rk[rnd])]
    return bytes(s)


_R = 0xe1 << 120


def _gmul(x: int, y: int) -> int:
    z, v = 0, y
    for i in range(127, -1, -1):
        if (x >> i) & 1:
            z ^= v
        v = (v >> 1) ^ _R if v & 1 else v >> 1
    return z


def _ghash(h: int, aad: bytes, ct: bytes) -> int:
    y = 0

    def blocks(b):
        for i in range(0, len(b), 16):
            yield (b[i:i + 16] + bytes(16))[:16]
    for blk in list(blocks(aad)) + list(blocks(ct)):
        y = _gmul(y ^ int.from_bytes(blk, "big"), h)
    lens = struct.pack(">QQ", len(aad) * 8, len(ct) * 8)
    return _gmul(y ^ int.from_bytes(lens, "big"), h)


def aes_gcm_encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """回傳 密文‖16 bytes 標籤（nonce 12 bytes）。"""
    if len(key) != 16 or len(nonce) != 12:
        raise ValueError("AES-128-GCM：金鑰 16 bytes、nonce 12 bytes")
    rk = _expand(key)
    h = int.from_bytes(_encrypt_block(rk, bytes(16)), "big")
    j0 = nonce + b"\x00\x00\x00\x01"
    out = bytearray()
    ctr = int.from_bytes(j0, "big")
    for i in range(0, len(plaintext), 16):
        ctr = (ctr & ~0xffffffff) | ((ctr + 1) & 0xffffffff)
        ks = _encrypt_block(rk, ctr.to_bytes(16, "big"))
        chunk = plaintext[i:i + 16]
        out += bytes(a ^ b for a, b in zip(chunk, ks))
    s = _ghash(h, aad, bytes(out))
    tag = int.from_bytes(_encrypt_block(rk, j0), "big") ^ s
    return bytes(out) + tag.to_bytes(16, "big")


# ---- RFC 8291 / 8188 ----------------------------------------------------------
def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def unb64u(s: str) -> bytes:
    s = str(s).strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def encrypt(payload: bytes, ua_public: bytes, auth_secret: bytes, *, as_private: Optional[int] = None,
            salt: Optional[bytes] = None, rs: int = 4096) -> bytes:
    """aes128gcm 內容編碼後的 HTTP body（RFC 8291）。"""
    ua_pt = point_from(ua_public)
    as_private = as_private or new_private()
    as_public = point_bytes(mul(as_private))
    salt = salt or os.urandom(16)
    ecdh_secret = ecdh(as_private, ua_pt)
    prk_key = _hmac(auth_secret, ecdh_secret)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    if len(payload) + 1 + 16 > rs:
        raise ValueError("通知內容太長")
    body = aes_gcm_encrypt(cek, nonce, payload + b"\x02")
    return salt + struct.pack(">I", rs) + bytes([len(as_public)]) + as_public + body


# ---- VAPID --------------------------------------------------------------------
class VapidKeys:
    def __init__(self, private: int) -> None:
        self.private = private
        self.public = point_bytes(mul(private))

    @property
    def public_b64(self) -> str:
        return b64u(self.public)

    @classmethod
    def load_or_create(cls, path: Path) -> "VapidKeys":
        path = Path(path)
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            priv = int.from_bytes(unb64u(d["private"]), "big")
            if 1 <= priv < N:
                return cls(priv)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        keys = cls(new_private())
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"private": b64u(keys.private.to_bytes(32, "big")), "public": keys.public_b64,
                                   "created": time.time()}), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)
        return keys

    def jwt(self, endpoint: str, subject: str, ttl: int = 12 * 3600) -> str:
        u = urllib.parse.urlsplit(endpoint)
        aud = f"{u.scheme}://{u.netloc}"
        head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        claims = b64u(json.dumps({"aud": aud, "exp": int(time.time()) + ttl, "sub": subject},
                                 separators=(",", ":")).encode())
        signing = f"{head}.{claims}".encode("ascii")
        return f"{head}.{claims}.{b64u(sign_es256(self.private, signing))}"


def send(sub: Dict[str, Any], payload: bytes, keys: VapidKeys, *, subject: str, ttl: int = 24 * 3600,
         urgency: str = "normal", topic: str = "", timeout: float = 10.0, opener=None) -> Tuple[int, str]:
    endpoint = str(sub.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        return 400, "endpoint 不是 https"
    k = sub.get("keys") or {}
    body = encrypt(payload, unb64u(k.get("p256dh", "")), unb64u(k.get("auth", "")))
    headers = {"Content-Type": "application/octet-stream", "Content-Encoding": "aes128gcm", "TTL": str(int(ttl)),
               "Urgency": urgency, "Authorization": f"vapid t={keys.jwt(endpoint, subject)}, k={keys.public_b64}"}
    if topic:
        headers["Topic"] = topic[:32]
    req = urllib.request.Request(endpoint, data=body, method="POST", headers=headers)
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            return r.status, (r.read(300) or b"").decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            text = e.read(300).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            text = ""
        return e.code, text or str(e.reason)
    except (urllib.error.URLError, OSError) as e:
        return 0, str(getattr(e, "reason", e))
