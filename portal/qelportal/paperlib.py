"""論文庫（paperlib）的伺服器端連線：用論文庫帳號登入、查使用者、查論文。

大程式不另外存帳號密碼：登入時把帳密轉給論文庫的 ``/api/auth/login``，
拿到論文庫的 session cookie（``plsession``）後存在大程式的 session 裡，之後代表這個人查論文。
論文庫的權限（例如唯讀訪客、只能改自己的論文）因此照樣有效。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import cookies
from typing import Any, Dict, List, Optional, Tuple

PL_COOKIE = "plsession"


class PaperlibError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class Paperlib:
    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    # ---- 底層 ------------------------------------------------------------------
    def call(self, method: str, path: str, body: Any = None, cookie: str = "", client_ip: str = "",
             query: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Tuple[Any, Dict[str, str]]:
        if query:
            q = {k: v for k, v in query.items() if v not in (None, "")}
            if q:
                path += "?" + urllib.parse.urlencode(q)
        headers = {"Accept": "application/json", "X-PL": "1"}
        if cookie:
            headers["Cookie"] = f"{PL_COOKIE}={cookie}"
        if client_ip:
            headers["X-Forwarded-For"] = client_ip      # 論文庫的登入鎖定依真正的來源 IP，不是大程式的 IP
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self._opener.open(req, timeout=timeout or self.timeout) as r:
                raw = r.read()
                set_cookies = r.headers.get_all("Set-Cookie") or []
        except urllib.error.HTTPError as e:
            try:
                d = json.loads(e.read().decode("utf-8") or "{}")
                msg = d.get("detail") or d.get("error") or e.reason
            except Exception:  # noqa: BLE001
                msg = e.reason
            if isinstance(msg, list):                     # FastAPI 驗證錯誤
                msg = "；".join(str(m.get("msg", m)) for m in msg)
            raise PaperlibError(e.code, str(msg)) from None
        except (urllib.error.URLError, OSError) as e:
            raise PaperlibError(502, f"連不到論文庫（{self.base}）：{getattr(e, 'reason', e)}") from None
        jar: Dict[str, str] = {}
        for sc in set_cookies:
            c = cookies.SimpleCookie()
            try:
                c.load(sc)
            except cookies.CookieError:
                continue
            for k, m in c.items():
                jar[k] = m.value
        try:
            out = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            out = raw.decode("utf-8", "replace")
        return out, jar

    def get(self, path: str, cookie: str, **query: Any) -> Any:
        return self.call("GET", path, cookie=cookie, query=query)[0]

    # ---- 帳號 ------------------------------------------------------------------
    def login(self, username: str, password: str, code: str = "", client_ip: str = "") -> Dict[str, Any]:
        """回傳 {"need_2fa": True} 或 {"cookie": 論文庫 session, "user": /api/me}。"""
        r, jar = self.call("POST", "/api/auth/login", {"username": username, "password": password, "code": code or None},
                           client_ip=client_ip)
        if isinstance(r, dict) and r.get("need_2fa"):
            return {"need_2fa": True}
        cookie = jar.get(PL_COOKIE, "")
        if not cookie:
            raise PaperlibError(502, "論文庫沒有回傳登入資訊")
        return {"cookie": cookie, "user": self.me(cookie)}

    def me(self, cookie: str) -> Dict[str, Any]:
        u = self.get("/api/me", cookie)
        if not isinstance(u, dict) or not u.get("username"):
            raise PaperlibError(401, "論文庫登入已失效")
        return u

    def logout(self, cookie: str) -> None:
        try:
            self.call("POST", "/api/auth/logout", {}, cookie=cookie, timeout=5)
        except PaperlibError:
            pass

    def has_owner(self, cookie: str) -> bool:
        try:
            r = self.get("/api/registrations/settings", cookie)
            return bool((r or {}).get("has_owner"))
        except PaperlibError:
            return True

    def register(self, body: Dict[str, Any], client_ip: str) -> Any:
        return self.call("POST", "/api/auth/register", body, client_ip=client_ip)[0]

    def users(self, cookie: str) -> List[Dict[str, Any]]:
        r = self.get("/api/users", cookie)
        return r if isinstance(r, list) else []

    def site(self) -> Dict[str, Any]:
        return self._cached("site", 30, lambda: self.get("/api/site", ""))

    def admin_status(self, cookie: str) -> Dict[str, Any]:
        return self.get("/api/admin/status", cookie)

    # ---- 論文 ------------------------------------------------------------------
    def papers(self, cookie: str, tag: str = "", q: str = "", limit: int = 30) -> Dict[str, Any]:
        r = self.get("/api/papers", cookie, tag=tag, q=q, limit=limit, sort="year")
        return r if isinstance(r, dict) else {"total": 0, "items": []}

    def papers_with_tag(self, cookie: str, tag: str, limit: int = 50) -> List[Dict[str, Any]]:
        """論文庫裡標了同名標籤的論文（快取 60 秒；論文對所有登入者都可見）。"""
        return self._cached(f"tag:{tag.casefold()}:{limit}", 60,
                            lambda: [slim(p) for p in self.papers(cookie, tag=tag, limit=limit).get("items", [])])

    def paper(self, cookie: str, pid: int) -> Optional[Dict[str, Any]]:
        def load() -> Optional[Dict[str, Any]]:
            try:
                return slim(self.get(f"/api/papers/{int(pid)}", cookie))
            except PaperlibError as e:
                if e.code == 404:
                    return None
                raise
        return self._cached(f"paper:{int(pid)}", 120, load)

    def tags(self, cookie: str) -> List[Dict[str, Any]]:
        return self._cached("pltags", 60, lambda: self.get("/api/tags", cookie) or [])

    def _cached(self, key: str, ttl: float, fn):
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        v = fn()
        with self._lock:
            self._cache[key] = (now, v)
            if len(self._cache) > 2000:
                for k in sorted(self._cache, key=lambda k: self._cache[k][0])[:500]:
                    self._cache.pop(k, None)
        return v

    def forget(self, prefix: str = "") -> None:
        with self._lock:
            for k in [k for k in self._cache if k.startswith(prefix)]:
                self._cache.pop(k, None)


def slim(p: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(p, dict) or "id" not in p:
        return None
    authors = p.get("authors")
    if isinstance(authors, str):
        try:
            authors = json.loads(authors)
        except ValueError:
            authors = [authors]
    first = ""
    if isinstance(authors, list) and authors:
        a = authors[0]
        first = a if isinstance(a, str) else (a.get("family") or a.get("name") or "") if isinstance(a, dict) else ""
    return {"id": p["id"], "title": p.get("title", ""), "year": p.get("year"), "venue": p.get("venue", ""),
            "doi": p.get("doi", ""), "arxiv": p.get("arxiv", ""), "citekey": p.get("citekey", ""),
            "first_author": first, "tags": [t.get("name") if isinstance(t, dict) else t for t in (p.get("tags") or [])]}
