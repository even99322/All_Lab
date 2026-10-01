"""Portal：登入與 session、模塊權限、事件、請求統計。路由在 api.py。"""
from __future__ import annotations

import collections
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

from . import __version__
from .config import PortalConfig
from .httpd import HTTPError, Request, Response
from .modules import ACCESS_MODULES, BUILTIN, BY_ID
from .paperlib import Paperlib, PaperlibError
from .store import Store, loads

log = logging.getLogger("qelportal")
COOKIE = "qel_session"
EVENT_KEEP = 5000


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Portal:
    def __init__(self, cfg: PortalConfig, paperlib: Optional[Paperlib] = None) -> None:
        self.cfg = cfg
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        (cfg.data_dir / "releases").mkdir(exist_ok=True)
        self.store = Store(cfg.data_dir / "portal.db")
        self.paperlib = paperlib or Paperlib(cfg.paperlib_url)
        self.started = time.time()
        self.ev_cond = threading.Condition()
        self._minutes: "collections.OrderedDict[int, Dict[str, Any]]" = collections.OrderedDict()
        self._active: Dict[str, float] = {}
        self._mlock = threading.Lock()
        self.errors: "collections.deque[Dict[str, Any]]" = collections.deque(maxlen=300)

    # ---- 分派 ------------------------------------------------------------------
    def handle(self, req: Request, routes) -> Any:
        for method, rx, fn, opts in routes:
            if method != req.method:
                continue
            m = rx.fullmatch(req.path)
            if m is None:
                continue
            level = opts.get("auth", "user")
            if level != "public":
                self.require(req, level, opts.get("module"))
            elif opts.get("optional_user"):
                req.session = self.session_from(req)
            return fn(self, req, *[g for g in m.groups()])
        if req.path.startswith("/api/"):
            raise HTTPError(404, f"找不到 {req.method} {req.path}")
        return None   # 交給靜態檔案

    def require(self, req: Request, level: str, module: Optional[str] = None) -> Dict[str, Any]:
        s = self.session_from(req)
        if s is None:
            raise HTTPError(401, "請先登入")
        if s["via"] == "cookie" and req.method not in ("GET", "HEAD") and req.headers.get("X-QEL") != "1":
            raise HTTPError(403, "缺少 X-QEL 標頭")       # 防 CSRF：跨站表單無法帶自訂標頭
        req.session = s
        u = s["user"]
        if level == "owner" and not u["manager"]:
            raise HTTPError(403, "需要站長身分")
        if module:
            mods = module if isinstance(module, (list, tuple)) else [module]
            if not any(self.can(u, m) for m in mods):
                names = "、".join(BY_ID.get(m, {}).get("name", m) for m in mods)
                raise HTTPError(403, f"站長還沒有開放「{names}」給你，請洽站長")
        return s

    # ---- session -----------------------------------------------------------------
    def _token_from(self, req: Request):
        a = req.headers.get("Authorization", "")
        if a.lower().startswith("bearer "):
            return a[7:].strip(), "bearer"
        if req.cookies.get(COOKIE):
            return req.cookies[COOKIE], "cookie"
        return None, None

    def session_from(self, req: Request) -> Optional[Dict[str, Any]]:
        token, via = self._token_from(req)
        if not token:
            return None
        th = _hash(token)
        row = self.store.one("SELECT * FROM sessions WHERE token_hash=?", (th,))
        now = time.time()
        if row is None or row["expires"] < now:
            return None
        user = loads(row["user_json"], {})
        if now - row["last_check"] > self.cfg.recheck_s:
            user = self._recheck(th, row["pl_cookie"], user)
            if user is None:
                return None
        if self.blocked(user["username"]):
            return None
        if now - row["last_seen"] > 60:
            with self.store.tx() as c:
                c.execute("UPDATE sessions SET last_seen=?, ip=? WHERE token_hash=?", (now, req.ip, th))
        with self._mlock:
            self._active[user["username"]] = now
        return {"token_hash": th, "user": user, "pl_cookie": row["pl_cookie"], "via": via, "client": row["client"]}

    def _recheck(self, th: str, pl_cookie: str, old: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """論文庫帳號被停用、改密碼登出、session 過期 → 大程式的 session 一起失效。"""
        try:
            me = self.paperlib.me(pl_cookie)
        except PaperlibError as e:
            if e.code in (401, 403):
                with self.store.tx() as c:
                    c.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
                return None
            return old                                      # 論文庫暫時連不到：沿用，下次再確認
        user = self._user_from(me, pl_cookie)
        with self.store.tx() as c:
            c.execute("UPDATE sessions SET user_json=?, last_check=? WHERE token_hash=?",
                      (json.dumps(user, ensure_ascii=False), time.time(), th))
        return user

    def _user_from(self, me: Dict[str, Any], pl_cookie: str) -> Dict[str, Any]:
        owner = bool(me.get("owner"))
        manager = owner or (me.get("role") == "admin" and not self.paperlib.has_owner(pl_cookie))
        return {"username": me["username"], "display_name": me.get("display_name") or me["username"],
                "pl_id": me.get("id"), "role": me.get("role", ""), "owner": owner, "manager": manager,
                "pl_perms": me.get("perms") or {}}

    def login(self, req: Request, username: str, password: str, code: str = "", client: str = "web") -> Dict[str, Any]:
        username = (username or "").strip()
        if not username or not password:
            raise HTTPError(400, "請輸入帳號與密碼")
        try:
            r = self.paperlib.login(username, password, code, client_ip=req.ip)
        except PaperlibError as e:
            raise HTTPError(400 if e.code < 500 else 502, str(e)) from None
        if r.get("need_2fa"):
            return {"ok": False, "need_2fa": True}
        user = self._user_from(r["user"], r["cookie"])
        if self.blocked(user["username"]):
            self.paperlib.logout(r["cookie"])
            raise HTTPError(403, "站長已停用你使用大程式的權限")
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.store.tx() as c:
            c.execute("INSERT INTO sessions(token_hash, username, user_json, pl_cookie, client, ip, created, expires, "
                      "last_seen, last_check) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (_hash(token), user["username"], json.dumps(user, ensure_ascii=False), r["cookie"], client[:80],
                       req.ip, now, now + self.cfg.session_days * 86400, now, now))
            c.execute("DELETE FROM sessions WHERE expires < ?", (now,))
            c.execute("DELETE FROM tickets WHERE expires < ?", (now,))
        self.store.audit(user["username"], "login", client, req.ip)
        return {"ok": True, "token": token, "user": self.user_view(user), "pl_cookie": r["cookie"]}

    def logout(self, s: Dict[str, Any]) -> None:
        with self.store.tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (s["token_hash"],))
        if s.get("pl_cookie"):
            self.paperlib.logout(s["pl_cookie"])

    def new_ticket(self, s: Dict[str, Any]) -> str:
        """一次性登入票（60 秒）：桌面程式開瀏覽器時不把 token 放在網址上。"""
        t = secrets.token_urlsafe(24)
        with self.store.tx() as c:
            c.execute("INSERT INTO tickets(ticket_hash, token_hash, expires) VALUES(?,?,?)",
                      (_hash(t), s["token_hash"], time.time() + 60))
        return t

    def redeem_ticket(self, ticket: str) -> Optional[Dict[str, Any]]:
        th = _hash(ticket or "")
        with self.store.tx() as c:
            row = c.execute("SELECT token_hash, expires FROM tickets WHERE ticket_hash=?", (th,)).fetchone()
            c.execute("DELETE FROM tickets WHERE ticket_hash=?", (th,))
        if row is None or row["expires"] < time.time():
            return None
        # 為瀏覽器另開一個 session（和桌面程式共用論文庫 session）
        src = self.store.one("SELECT * FROM sessions WHERE token_hash=?", (row["token_hash"],))
        if src is None:
            return None
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.store.tx() as c:
            c.execute("INSERT INTO sessions(token_hash, username, user_json, pl_cookie, client, ip, created, expires, "
                      "last_seen, last_check) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (_hash(token), src["username"], src["user_json"], src["pl_cookie"], "web (ticket)", src["ip"],
                       now, src["expires"], now, src["last_check"]))
        return {"token": token, "pl_cookie": src["pl_cookie"], "expires": src["expires"]}

    def session_cookies(self, resp: Response, req: Request, token: str, pl_cookie: str) -> Response:
        age = self.cfg.session_days * 86400
        secure = self.cfg.secure_cookie or req.https
        resp.cookie(COOKIE, token, age, self.cfg.cookie_domain, secure)
        if self.cfg.sso and pl_cookie:
            resp.cookie("plsession", pl_cookie, age, self.cfg.cookie_domain, secure)
        return resp

    def clear_cookies(self, resp: Response) -> Response:
        resp.cookie(COOKIE, "", 0, self.cfg.cookie_domain)
        if self.cfg.sso:
            resp.cookie("plsession", "", 0, self.cfg.cookie_domain)
        return resp

    # ---- 權限 ------------------------------------------------------------------
    def blocked(self, username: str) -> bool:
        r = self.store.one("SELECT blocked FROM user_flags WHERE username=?", (username,))
        return bool(r and r["blocked"])

    def default_access(self) -> Dict[str, bool]:
        stored = self.store.setting("default_access", {}) or {}
        return {m: bool(stored.get(m, BY_ID[m].get("default", False))) for m in ACCESS_MODULES}

    def access_map(self, username: str) -> Dict[str, bool]:
        out = self.default_access()
        for r in self.store.q("SELECT module, enabled FROM access WHERE username=?", (username,)):
            if r["module"] in out:
                out[r["module"]] = bool(r["enabled"])
        return out

    def can(self, user: Dict[str, Any], module: str) -> bool:
        if user.get("manager"):
            return True
        spec = BY_ID.get(module)
        if spec is None:
            return False
        if spec["access"] is False:
            return True
        if spec["access"] == "owner":
            return False
        return self.access_map(user["username"]).get(module, False)

    def user_view(self, user: Dict[str, Any]) -> Dict[str, Any]:
        return {"username": user["username"], "display_name": user["display_name"], "owner": user["owner"],
                "manager": user["manager"], "role": user["role"],
                "modules": [m["id"] for m in BUILTIN if self.can(user, m["id"])]}

    # ---- 事件 ------------------------------------------------------------------
    def publish(self, topic: str, data: Any, by: str = "", target: Optional[str] = None) -> int:
        with self.store.tx() as c:
            seq = c.execute("INSERT INTO events(topic, data, target, by, time) VALUES(?,?,?,?,?)",
                            (topic, json.dumps(data, ensure_ascii=False, default=str), target, by, time.time())).lastrowid
            c.execute("DELETE FROM events WHERE seq <= ?", (seq - EVENT_KEEP,))
        with self.ev_cond:
            self.ev_cond.notify_all()
        return int(seq)

    def last_seq(self) -> int:
        r = self.store.one("SELECT MAX(seq) m FROM events")
        return int(r["m"] or 0)

    def events_after(self, username: str, after: int, wait: float, topics: List[str]) -> Dict[str, Any]:
        """long-poll。after < 0 表示「從現在開始」；after 比最新序號還大（資料庫重建過）時重新對齊。"""
        top = self.last_seq()
        if after < 0 or after > top:
            after = top
        deadline = time.time() + wait
        while True:
            rows = self.store.q("SELECT * FROM events WHERE seq > ? AND (target IS NULL OR target=?) ORDER BY seq LIMIT 200",
                                (after, username))
            if rows:
                after = rows[-1]["seq"]
            evs = [{"seq": r["seq"], "topic": r["topic"], "data": loads(r["data"], None), "by": r["by"],
                    "time": r["time"]} for r in rows
                   if not topics or any(r["topic"] == t or r["topic"].startswith(t + ".") for t in topics)]
            if evs or time.time() >= deadline:
                return {"last": after, "events": evs}
            if rows:                                            # 有新事件但不是要的主題：繼續等
                continue
            with self.ev_cond:
                self.ev_cond.wait(timeout=min(5.0, max(0.05, deadline - time.time())))

    # ---- 統計 ------------------------------------------------------------------
    def record(self, req: Request, status: int) -> None:
        ms = (time.time() - req.started) * 1000
        if req.path.startswith("/static/") or req.path == "/api/v1/events":
            return
        m = int(time.time() // 60)
        with self._mlock:
            b = self._minutes.get(m)
            if b is None:
                b = self._minutes[m] = {"n": 0, "err": 0, "c4": 0, "ms": 0.0, "max": 0.0, "slow": ""}
                while len(self._minutes) > 1440:
                    self._minutes.popitem(last=False)
            b["n"] += 1
            b["ms"] += ms
            if ms > b["max"]:
                b["max"], b["slow"] = ms, req.path
            if status >= 500:
                b["err"] += 1
                self.errors.append({"time": time.time(), "path": req.path, "status": status})
            elif status >= 400 and status not in (401, 404):
                b["c4"] += 1

    def traffic(self, minutes: int = 60) -> Dict[str, Any]:
        now = int(time.time() // 60)
        with self._mlock:
            d = {k: dict(v) for k, v in self._minutes.items() if k > now - minutes}
            online = sorted(u for u, t in self._active.items() if time.time() - t < 900)
        series = [{"t": m * 60, "n": d.get(m, {}).get("n", 0), "err": d.get(m, {}).get("err", 0),
                   "avg": round(d[m]["ms"] / d[m]["n"], 1) if m in d and d[m]["n"] else None}
                  for m in range(now - minutes + 1, now + 1)]
        n = sum(b["n"] for b in d.values())
        return {"minutes": minutes, "requests": n, "errors": sum(b["err"] for b in d.values()),
                "client_errors": sum(b["c4"] for b in d.values()),
                "avg_ms": round(sum(b["ms"] for b in d.values()) / n, 1) if n else None,
                "slowest": max(d.values(), key=lambda b: b["max"], default={"max": 0, "slow": ""}),
                "online": online, "series": series}

    def version(self) -> str:
        return __version__


def constant_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
