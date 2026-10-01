"""LAB-QEL論文庫：FastAPI 後端。所有 API 都在 /api 之下，前端是 static/ 裡的單頁應用。"""
import csv
import difflib
import io
import json
import re
import secrets
import shutil
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import accounts, monitor, ai, auth, config, security, db, enrich, jobs, library, notify, ocr, pdftools, settings, translate

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app):
    print(f"LAB-QEL論文庫 v{config.VERSION} 啟動", flush=True)
    monitor.install_logging()
    db.init()
    cutoff = time.time() - 86400
    for f in config.STAGING_DIR.glob("*"):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
    if config.RUN_JOBS:
        jobs.start()
        jobs.enqueue("similar_index", "")      # 預先建立相似度索引，第一次語意搜尋不用等
    yield
    jobs.stop()


app = FastAPI(title=config.SITE_NAME, docs_url=None, redoc_url=None, lifespan=lifespan)


@app.middleware("http")
async def _monitor(request: Request, call_next):
    # 請求統計（給控制台與狀態頁）；沒被接住的錯誤記到錯誤紀錄
    t0 = time.perf_counter()
    try:
        resp = await call_next(request)
    except Exception as e:
        monitor.log_error(f"{request.method} {request.url.path}", e)
        monitor.record(request.url.path, 500, (time.perf_counter() - t0) * 1000)
        raise
    tok = request.cookies.get(auth.COOKIE)
    monitor.record(request.url.path, resp.status_code, (time.perf_counter() - t0) * 1000, tok[:16] if tok else None)
    return resp


@app.middleware("http")
async def _no_stale_code(request: Request, call_next):
    # 程式碼更新後，瀏覽器要重新驗證 JS／CSS（沒變時只回 304，不會變慢）
    resp = await call_next(request)
    if request.url.path.startswith("/static/") and request.url.path.endswith((".js", ".css", ".mjs")) \
            and "/vendor/" not in request.url.path:
        resp.headers["Cache-Control"] = "no-cache"
    # 對外開放時的基本防護標頭
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if security.is_https(request):
        resp.headers.setdefault("Strict-Transport-Security", "max-age=15552000")
    return resp


@app.exception_handler(sqlite3.IntegrityError)
def _integrity(_req, exc):
    return JSONResponse({"detail": f"資料重複或衝突：{exc}"}, status_code=409)


# ================================================================== 工具
def row(r) -> dict | None:
    return dict(r) if r is not None else None


def paper_or_404(con, pid: int) -> dict:
    p = con.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone()
    if p is None:
        raise HTTPException(404, "找不到這篇論文")
    return dict(p)


def site_name(con=None) -> str:
    return db.meta_get(con or db.get(), "site_name") or config.SITE_NAME


def can_edit(u, pid: int, perm: str = "edit_meta", con=None) -> dict:
    """檢查權限（含「只能改自己上傳的」限制），回傳論文。"""
    p = paper_or_404(con or db.get(), pid)
    auth.need(u, perm, p)
    return p


def data_path(rel: str) -> Path:
    p = (config.DATA_DIR / rel).resolve()
    if config.DATA_DIR not in p.parents:
        raise HTTPException(400, "路徑不合法")
    return p


def parse_authors(v) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in re.split(r"[;\n]", v or "") if x.strip()]


def fts_query(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def search_ids(con, q: str) -> tuple[list[int] | None, dict]:
    """回傳符合搜尋字串的論文 id（依相關度）與摘要片段。短於 3 字的詞改用 LIKE。"""
    terms = [t for t in re.split(r"\s+", q.strip()) if t]
    if not terms:
        return None, {}
    long_terms = [t for t in terms if len(t) >= 3]
    ids = None
    rank = {}
    if long_terms:
        match = " AND ".join(fts_query(t) for t in long_terms)
        rows = con.execute("SELECT paper_id, bm25(fts, 0, 10.0, 5.0, 2.0, 1.0, 4.0) AS r FROM fts WHERE fts MATCH ?",
                           (match,)).fetchall()
        rank = {r["paper_id"]: r["r"] for r in rows}
    for t in terms:
        like = f"%{t}%"
        meta = {r["id"] for r in con.execute("SELECT id FROM papers WHERE citekey LIKE ? OR doi LIKE ? OR arxiv LIKE ?",
                                              (like, like, like))} if len(t) >= 3 else set()
        if len(t) >= 3:
            s = {r["paper_id"] for r in con.execute("SELECT paper_id FROM fts WHERE fts MATCH ?", (fts_query(t),))} | meta
        else:
            rows = con.execute(
                "SELECT paper_id, title, authors, venue, notes, body FROM fts WHERE title LIKE ? OR authors LIKE ? OR venue LIKE ? "
                "OR notes LIKE ? OR body LIKE ?", (like,) * 5).fetchall()
            if t.isascii() and t.isalnum():  # 英文縮寫（如 EP、SM）要整個字相符
                wb = re.compile(rf"\b{re.escape(t)}\b", re.I)
                rows = [r for r in rows if any(wb.search(r[k] or "") for k in ("title", "authors", "venue", "notes", "body"))]
            s = {r["paper_id"] for r in rows} | meta
        ids = s if ids is None else ids & s
    ordered = sorted(ids or [], key=lambda i: rank.get(i, 0))
    return ordered, terms


def make_snippets(con, ids: list[int], terms: list[str], width: int = 70) -> dict:
    """在筆記、重點欄、全文中找第一個命中的詞，擷取前後文並用 ⟦ ⟧ 標記。"""
    if not ids or not terms:
        return {}
    parts = [rf"\b{re.escape(t)}\b" if (len(t) < 3 and t.isascii() and t.isalnum()) else re.escape(t)
             for t in sorted(terms, key=len, reverse=True)]
    pat = re.compile("|".join(parts), re.I)
    out = {}
    qm = ",".join("?" * len(ids))
    for r in con.execute(f"SELECT paper_id, notes, body FROM fts WHERE paper_id IN ({qm})", ids):
        for src in (r["notes"] or "", r["body"] or ""):
            m = pat.search(src)
            if not m:
                continue
            a, b = max(0, m.start() - width), min(len(src), m.end() + width)
            text = re.sub(r"\s+", " ", src[a:b])
            text = pat.sub(lambda x: "⟦" + x.group(0) + "⟧", text)
            out[r["paper_id"]] = ("…" if a else "") + text + ("…" if b < len(src) else "")
            break
    return out


def cards(con, ids: list[int], user_id: int, snippets: dict | None = None, cat: int | None = None) -> list[dict]:
    if not ids:
        return []
    qm = ",".join("?" * len(ids))
    incat = {r["paper_id"]: dict(r) for r in con.execute(
        f"SELECT paper_id, folder_id, pinned FROM paper_categories WHERE category_id=? AND paper_id IN ({qm})", [cat] + ids)} if cat else {}
    base = {r["id"]: dict(r) for r in con.execute(
        f"SELECT id, citekey, title, authors, authors_complete, year, venue, kind, suggested_category_id, "
        f"added_at, updated_at, doi, added_by, pinned, required FROM papers WHERE id IN ({qm})", ids)}
    for b in base.values():
        b["status"] = None
    for r in con.execute(f"SELECT paper_id, status FROM user_paper WHERE user_id=? AND paper_id IN ({qm})", [user_id] + ids):
        base[r["paper_id"]]["status"] = r["status"]
    for r in con.execute(f"SELECT pa.paper_id, pa.note, ub.display_name AS by FROM paper_assign pa LEFT JOIN users ub ON ub.id=pa.assigned_by "
                         f"WHERE pa.user_id=? AND pa.paper_id IN ({qm})", [user_id] + ids):
        base[r["paper_id"]]["my_assign"] = {"by": r["by"], "note": r["note"]}
    for r in con.execute(f"SELECT pa.paper_id, pa.user_id, u.display_name FROM paper_assign pa JOIN users u ON u.id=pa.user_id "
                         f"WHERE pa.paper_id IN ({qm}) ORDER BY pa.created_at", ids):
        b = base[r["paper_id"]]
        b.setdefault("assignees", []).append(r["display_name"])
        if r["user_id"] == user_id:
            b["assigned_to_me"] = True
    for r in con.execute(f"SELECT paper_id, category_id FROM paper_categories WHERE paper_id IN ({qm})", ids):
        base[r["paper_id"]].setdefault("categories", []).append(r["category_id"])
    for r in con.execute(f"SELECT pt.paper_id, t.name FROM paper_tags pt JOIN tags t ON t.id=pt.tag_id "
                         f"WHERE pt.paper_id IN ({qm}) ORDER BY t.name", ids):
        base[r["paper_id"]].setdefault("tags", []).append(r["name"])
    for r in con.execute(f"SELECT paper_id, COUNT(*) c FROM annotations WHERE paper_id IN ({qm}) "
                         f"AND (private=0 OR author_id=?) GROUP BY paper_id", ids + [user_id]):
        base[r["paper_id"]]["n_ann"] = r["c"]
    for r in con.execute(f"SELECT pid, COUNT(*) c FROM (SELECT src_id pid FROM links WHERE src_id IN ({qm}) "
                         f"UNION ALL SELECT dst_id FROM links WHERE dst_id IN ({qm})) GROUP BY pid", ids + ids):
        base[r["pid"]]["n_links"] = r["c"]
    for r in con.execute(f"SELECT paper_id, id, role FROM files WHERE paper_id IN ({qm}) ORDER BY "
                         f"CASE role WHEN 'main' THEN 0 WHEN 'version' THEN 1 WHEN 'sm' THEN 2 ELSE 3 END, id", ids):
        b = base[r["paper_id"]]
        b.setdefault("file_ids", []).append(r["id"])
        b.setdefault("roles", []).append(r["role"])
    out = []
    for i in ids:
        b = base.get(i)
        if not b:
            continue
        authors = json.loads(b["authors"] or "[]")
        b["authors"] = authors[:3]
        b["n_authors"] = len(authors)
        b["thumb"] = b.get("file_ids", [None])[0]
        b.setdefault("categories", []); b.setdefault("tags", []); b.setdefault("assignees", [])
        b.setdefault("n_ann", 0); b.setdefault("n_links", 0)
        b["has_sm"] = "sm" in b.get("roles", [])
        b.pop("file_ids", None); b.pop("roles", None)
        if snippets and i in snippets:
            b["snippet"] = snippets[i]
        if i in incat:
            b["folder_id"] = incat[i]["folder_id"]
            b["cat_pinned"] = incat[i]["pinned"]
        out.append(b)
    return out


# ================================================================== 帳號
class Creds(BaseModel):
    username: str
    password: str
    display_name: str | None = None
    code: str | None = None


@app.get("/api/site")
def site(request: Request):
    con = db.get()
    n = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    tr = translate.settings(con)
    return {"name": site_name(con), "version": config.VERSION, "needs_setup": n == 0, "user": auth.current_user(request),
            "max_upload_mb": config.MAX_UPLOAD_MB, "roles": library.ROLES, "kinds": library.KINDS,
            "statuses": library.STATUSES, "perm_names": auth.PERMS, "role_defaults": auth.ROLE_DEFAULTS,
            "role_names": auth.ROLE_NAMES, "translate": {"enabled": bool(tr["tr_provider"]), "target": tr["tr_target"]},
            "ai": {"enabled": ai.enabled(con)}, "ocr": ocr.available()["ok"], "site_url": settings.get(con)["site_url"],
            "feed_hour": settings.get(con)["feed_hour"], "register": settings.get(con).get("register_enabled", "1") == "1"}


def _set_cookie(resp: Response, token: str, request: Request | None = None):
    # 走 HTTPS（Cloudflare Tunnel、Tailscale Funnel、反向代理）時自動加上 Secure
    resp.set_cookie(auth.COOKIE, token, max_age=config.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                    secure=config.SECURE_COOKIE or (request is not None and security.is_https(request)))


@app.post("/api/auth/setup")
def setup(c: Creds, response: Response, request: Request):
    with db.tx() as con:
        if con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]:
            raise HTTPException(400, "已經設定過管理員")
        if len(c.password) < 6:
            raise HTTPException(400, "密碼至少 6 個字元")
        cur = con.execute("INSERT INTO users(username, display_name, pw_hash, role, created_at) VALUES(?,?,?,?,?)",
                          (c.username.strip(), (c.display_name or c.username).strip(), auth.hash_pw(c.password), "admin", db.now()))
        _set_cookie(response, auth.new_session(con, cur.lastrowid), request)
    return {"ok": True}


@app.post("/api/auth/login")
def login(c: Creds, response: Response, request: Request):
    con = db.get()
    keys = [f"ip:{security.client_ip(request)}", f"user:{c.username.strip().lower()}"]
    security.check_locked(keys)
    u = con.execute("SELECT * FROM users WHERE username=? AND disabled=0", (c.username.strip(),)).fetchone()
    if u is None:
        pend = con.execute("SELECT pw_hash FROM registrations WHERE lower(username)=lower(?)", (c.username.strip(),)).fetchone()
        if pend and auth.check_pw(c.password, pend["pw_hash"]):
            raise HTTPException(400, "你的註冊申請還在等站長審核，通過後會寄信通知你")
    if u is None or not auth.check_pw(c.password, u["pw_hash"]):
        security.record_fail(keys)
        time.sleep(0.6)
        raise HTTPException(400, "帳號或密碼錯誤")
    if u["totp"]:
        if not c.code:
            return {"ok": False, "need_2fa": True}
        if not security.verify(u["totp"], c.code):
            security.record_fail(keys)
            time.sleep(0.6)
            raise HTTPException(400, "驗證碼錯誤")
    security.clear_fail(keys[1:])
    with db.tx() as con:
        _set_cookie(response, auth.new_session(con, u["id"]), request)
        db.log(con, u["id"], "login", None, security.client_ip(request))
    return {"ok": True}


# ------------------------------------------------------------------ 兩步驟驗證、其他裝置登出
@app.post("/api/me/2fa/setup")
def twofa_setup(u=Depends(auth.require_user)):
    secret = security.new_secret()
    uri = security.otpauth_uri(secret, u["username"], site_name())
    return {"secret": secret, "uri": uri, "qr": security.qr_svg(uri)}


@app.post("/api/me/2fa/enable")
def twofa_enable(b: dict[str, Any], u=Depends(auth.require_user)):
    if not security.verify(str(b.get("secret") or ""), str(b.get("code") or "")):
        raise HTTPException(400, "驗證碼不對，請確認手機時間正確後再試")
    db.get().execute("UPDATE users SET totp=? WHERE id=?", (b["secret"], u["id"]))
    return {"ok": True}


@app.post("/api/me/2fa/disable")
def twofa_disable(b: dict[str, Any], u=Depends(auth.require_user)):
    r = db.get().execute("SELECT pw_hash FROM users WHERE id=?", (u["id"],)).fetchone()
    if not auth.check_pw(str(b.get("password") or ""), r["pw_hash"]):
        raise HTTPException(400, "密碼錯誤")
    db.get().execute("UPDATE users SET totp=NULL WHERE id=?", (u["id"],))
    return {"ok": True}


@app.post("/api/me/logout-others")
def logout_others(request: Request, u=Depends(auth.require_user)):
    n = db.get().execute("DELETE FROM sessions WHERE user_id=? AND token!=?", (u["id"], request.cookies.get(auth.COOKIE))).rowcount
    return {"ok": True, "count": n}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(auth.COOKIE)
    if token:
        db.get().execute("DELETE FROM sessions WHERE token=?", (token,))
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


class PwChange(BaseModel):
    old: str
    new: str


@app.post("/api/me/password")
def change_pw(b: PwChange, u=Depends(auth.require_user)):
    con = db.get()
    r = con.execute("SELECT pw_hash FROM users WHERE id=?", (u["id"],)).fetchone()
    if not auth.check_pw(b.old, r["pw_hash"]):
        raise HTTPException(400, "舊密碼錯誤")
    if len(b.new) < 6:
        raise HTTPException(400, "新密碼至少 6 個字元")
    con.execute("UPDATE users SET pw_hash=? WHERE id=?", (auth.hash_pw(b.new), u["id"]))
    return {"ok": True}


@app.get("/api/me")
def me(u=Depends(auth.require_user)):
    r = db.get().execute("SELECT id, username, display_name, email, digest, role, owner, totp IS NOT NULL AS has_2fa FROM users WHERE id=?", (u["id"],)).fetchone()
    return dict(r) | {"perms": u["perms"]}


@app.patch("/api/me")
def edit_me(b: dict[str, Any], u=Depends(auth.require_user)):
    con = db.get()
    if "display_name" in b and str(b["display_name"]).strip():
        con.execute("UPDATE users SET display_name=? WHERE id=?", (str(b["display_name"]).strip()[:40], u["id"]))
    if "email" in b:
        e = str(b["email"] or "").strip()
        if e and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e):
            raise HTTPException(400, "Email 格式不對")
        con.execute("UPDATE users SET email=? WHERE id=?", (e, u["id"]))
    if "digest" in b:
        con.execute("UPDATE users SET digest=? WHERE id=?", (int(bool(b["digest"])), u["id"]))
    return me(u)


@app.get("/api/users")
def users(u=Depends(auth.require_user)):
    if u["role"] != "admin":
        return [dict(r) for r in db.get().execute("SELECT id, display_name FROM users ORDER BY id")]
    out = []
    for r in db.get().execute("SELECT id, username, display_name, role, owner, disabled, created_at, perms, email, digest, totp IS NOT NULL AS has_2fa FROM users ORDER BY owner DESC, id"):
        d = dict(r)
        d["perms"] = auth.perms_for(d["role"], d["perms"])
        out.append(d)
    return out


class UserIn(BaseModel):
    username: str | None = None
    display_name: str | None = None
    password: str | None = None
    role: str | None = None
    disabled: bool | None = None
    perms: dict[str, bool] | None = None
    email: str | None = None
    reset_2fa: bool = False
    owner: bool | None = None


def _perm_diff(role: str, perms: dict | None) -> str:
    """只存與角色預設不同的項目，之後調整角色預設時不會被舊值卡住。"""
    if not perms or role == "admin":
        return "{}"
    base = auth.ROLE_DEFAULTS[role]
    return json.dumps({k: bool(v) for k, v in perms.items() if k in auth.PERMS and bool(v) != base[k]})


@app.post("/api/users")
def add_user(b: UserIn, admin=Depends(auth.require_admin)):
    if not b.username or not b.password or len(b.password) < 6:
        raise HTTPException(400, "需要帳號與至少 6 字元的密碼")
    role = b.role if b.role in auth.ROLE_DEFAULTS else "member"
    db.get().execute("INSERT INTO users(username, display_name, pw_hash, role, perms, created_at) VALUES(?,?,?,?,?,?)",
                     (b.username.strip(), (b.display_name or b.username).strip(), auth.hash_pw(b.password),
                      role, _perm_diff(role, b.perms), db.now()))
    return {"ok": True}


@app.patch("/api/users/{uid}")
def edit_user(uid: int, b: UserIn, admin=Depends(auth.require_user)):
    con = db.get()
    if b.owner is not None:
        # 站長身分：還沒有站長時管理員可以指定；之後只有站長能變更
        with db.tx() as c2:
            accounts.check_owner_change(c2, admin, uid, b.owner)
            if b.owner:
                c2.execute("UPDATE users SET owner=1, role='admin', perms='{}' WHERE id=?", (uid,))
            else:
                c2.execute("UPDATE users SET owner=0 WHERE id=?", (uid,))
            name = c2.execute("SELECT display_name FROM users WHERE id=?", (uid,)).fetchone()["display_name"]
            db.log(c2, admin["id"], "owner", None, f"{'指定' if b.owner else '取消'} {name}")
        if b.model_dump(exclude_none=True, exclude={"owner", "reset_2fa"}) == {} and not b.reset_2fa:
            return {"ok": True}
    if admin["role"] != "admin":
        raise HTTPException(403, "需要管理員權限")
    accounts.protect_owner(con, admin, uid)
    if b.username and b.username.strip():
        nu = b.username.strip()
        if not accounts.USERNAME_RE.match(nu):
            raise HTTPException(400, "帳號請用 3–32 個英文字母、數字或 . _ -")
        if con.execute("SELECT 1 FROM users WHERE lower(username)=lower(?) AND id!=?", (nu, uid)).fetchone():
            raise HTTPException(409, "這個帳號名稱已經有人使用")
        con.execute("UPDATE users SET username=? WHERE id=?", (nu, uid))
    if b.display_name:
        con.execute("UPDATE users SET display_name=? WHERE id=?", (b.display_name.strip()[:40], uid))
    if b.email is not None:
        if b.email.strip() and not accounts.EMAIL_RE.match(b.email.strip()):
            raise HTTPException(400, "Email 格式不對")
        con.execute("UPDATE users SET email=? WHERE id=?", (b.email.strip()[:200], uid))
    if b.reset_2fa:
        con.execute("UPDATE users SET totp=NULL WHERE id=?", (uid,))
    if b.password:
        if len(b.password) < 6:
            raise HTTPException(400, "密碼至少 6 個字元")
        con.execute("UPDATE users SET pw_hash=? WHERE id=?", (auth.hash_pw(b.password), uid))
        con.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    if b.role in auth.ROLE_DEFAULTS or b.perms is not None:
        cur = con.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
        if cur is None:
            raise HTTPException(404, "找不到帳號")
        role = b.role if b.role in auth.ROLE_DEFAULTS else cur["role"]
        if uid == admin["id"] and role != "admin":
            raise HTTPException(400, "不能移除自己的管理員權限")
        if role != "admin" and con.execute("SELECT owner FROM users WHERE id=?", (uid,)).fetchone()["owner"]:
            raise HTTPException(400, "站長一定是管理員；要改角色請先取消站長身分")
        con.execute("UPDATE users SET role=?, perms=? WHERE id=?", (role, _perm_diff(role, b.perms), uid))
    if b.disabled is not None:
        if uid == admin["id"]:
            raise HTTPException(400, "不能停用自己")
        if b.disabled and con.execute("SELECT owner FROM users WHERE id=?", (uid,)).fetchone()["owner"]:
            raise HTTPException(400, "站長的帳號不能停用；請先取消站長身分")
        con.execute("UPDATE users SET disabled=? WHERE id=?", (int(b.disabled), uid))
        con.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    return {"ok": True}


@app.delete("/api/users/{uid}")
def delete_user(uid: int, purge: bool = False, admin=Depends(auth.require_admin)):
    """刪除帳號：登入、閱讀狀態、指派、通知、入門路徑、筆記頁設定一起刪。
    公開的標註與討論預設保留（顯示為「已刪除的帳號」）；purge=1 時連同他的所有標註、手寫、討論回覆一起刪。
    他上傳的論文與檔案一律保留。"""
    with db.tx() as con:
        t = con.execute("SELECT id, username, display_name, owner FROM users WHERE id=?", (uid,)).fetchone()
        if t is None:
            raise HTTPException(404, "找不到帳號")
        if uid == admin["id"]:
            raise HTTPException(400, "不能刪除自己的帳號")
        accounts.protect_owner(con, admin, uid)
        if t["owner"] and con.execute("SELECT COUNT(*) FROM users WHERE owner=1 AND disabled=0").fetchone()[0] <= 1:
            raise HTTPException(400, "這是最後一位站長，不能刪除。請先把另一位設為站長")
        n_priv = con.execute("SELECT COUNT(*) FROM annotations WHERE author_id=? AND (private=1 OR ?)", (uid, int(purge))).fetchone()[0]
        touched = [r[0] for r in con.execute("SELECT DISTINCT paper_id FROM annotations WHERE author_id=? AND (private=1 OR ?)", (uid, int(purge)))]
        con.execute("DELETE FROM ann_replies WHERE ann_id IN (SELECT id FROM annotations WHERE author_id=? AND (private=1 OR ?))", (uid, int(purge)))
        con.execute("DELETE FROM annotations WHERE author_id=? AND (private=1 OR ?)", (uid, int(purge)))
        if purge:
            con.execute("DELETE FROM ann_replies WHERE author_id=?", (uid,))
        for q in ("DELETE FROM notebooks WHERE user_id=?", "DELETE FROM paper_views WHERE user_id=?",
                  "UPDATE meeting_items SET presenter_id=NULL WHERE presenter_id=?"):
            try:
                con.execute(q, (uid,))
            except sqlite3.OperationalError:
                pass
        con.execute("DELETE FROM users WHERE id=?", (uid,))      # 其他（登入、閱讀狀態、指派、通知、路徑成員）隨帳號一起刪除
        db.log(con, admin["id"], "delete_user", None, f"{t['display_name']}（{t['username']}）{'，連同所有標註' if purge else ''}")
        for pid in touched:
            db.fts_update(con, pid)
    return {"ok": True, "removed_annotations": n_priv}


# ================================================================== 分類與標籤
@app.get("/api/categories")
def categories(u=Depends(auth.require_user)):
    con = db.get()
    rows = [dict(r) for r in con.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM paper_categories pc WHERE pc.category_id=c.id) AS count "
        "FROM categories c ORDER BY c.sort, c.id")]
    folders = {}
    for f in con.execute("SELECT f.*, (SELECT COUNT(*) FROM paper_categories pc WHERE pc.folder_id=f.id) AS count "
                         "FROM folders f ORDER BY f.sort, f.id"):
        folders.setdefault(f["category_id"], []).append(dict(f))
    for r in rows:
        r["folders"] = folders.get(r["id"], [])
    return rows


class CatIn(BaseModel):
    name: str | None = None
    grp: str | None = None
    description: str | None = None
    color: str | None = None


@app.post("/api/categories")
def add_category(b: CatIn, u=Depends(auth.perm("manage"))):
    con = db.get()
    mx = con.execute("SELECT COALESCE(MAX(sort),0)+1 m FROM categories").fetchone()["m"]
    con.execute("INSERT INTO categories(name, grp, description, color, sort) VALUES(?,?,?,?,?)",
                ((b.name or "").strip() or "新分類", (b.grp or "架設類型").strip(), b.description or "", b.color or "#5d6d7e", mx))
    return {"ok": True}


@app.patch("/api/categories/{cid}")
def edit_category(cid: int, b: CatIn, u=Depends(auth.perm("manage"))):
    con = db.get()
    for k in ("name", "grp", "description", "color"):
        v = getattr(b, k)
        if v is not None:
            con.execute(f"UPDATE categories SET {k}=? WHERE id=?", (v.strip() if k != "description" else v, cid))
    return {"ok": True}


@app.delete("/api/categories/{cid}")
def del_category(cid: int, u=Depends(auth.perm("manage"))):
    db.get().execute("DELETE FROM categories WHERE id=?", (cid,))
    return {"ok": True}


class Order(BaseModel):
    ids: list[int]


@app.post("/api/categories/order")
def order_categories(b: Order, u=Depends(auth.perm("manage"))):
    with db.tx() as con:
        for i, cid in enumerate(b.ids):
            con.execute("UPDATE categories SET sort=? WHERE id=?", (i, cid))
    return {"ok": True}


@app.get("/api/tags")
def tags(u=Depends(auth.require_user)):
    return [dict(r) for r in db.get().execute(
        "SELECT t.id, t.name, t.color, t.description, t.sort, COUNT(pt.paper_id) AS count FROM tags t "
        "LEFT JOIN paper_tags pt ON pt.tag_id=t.id GROUP BY t.id ORDER BY t.sort, count DESC, t.name")]


class TagIn(BaseModel):
    name: str | None = None
    color: str | None = None
    description: str | None = None


@app.post("/api/tags")
def add_tag(b: TagIn, u=Depends(auth.perm("manage"))):
    name = (b.name or "").strip()[:60]
    if not name:
        raise HTTPException(400, "請填標籤名稱")
    con = db.get()
    mx = con.execute("SELECT COALESCE(MAX(sort),0)+1 m FROM tags").fetchone()["m"]
    con.execute("INSERT INTO tags(name, color, description, sort) VALUES(?,?,?,?)",
                (name, b.color or "#6b7280", b.description or "", mx))
    return {"ok": True}


@app.patch("/api/tags/{tid}")
def edit_tag(tid: int, b: TagIn, u=Depends(auth.perm("manage"))):
    con = db.get()
    if b.color is not None:
        con.execute("UPDATE tags SET color=? WHERE id=?", (b.color[:20], tid))
    if b.description is not None:
        con.execute("UPDATE tags SET description=? WHERE id=?", (b.description[:500], tid))
    if b.name is not None:
        old = con.execute("SELECT name FROM tags WHERE id=?", (tid,)).fetchone()
        if old and b.name.strip() and b.name.strip() != old["name"]:
            rename_tag(TagRename(old=old["name"], new=b.name), u)
    return {"ok": True}


@app.delete("/api/tags/{tid}")
def del_tag(tid: int, u=Depends(auth.perm("manage"))):
    db.get().execute("DELETE FROM tags WHERE id=?", (tid,))
    return {"ok": True}


@app.post("/api/tags/order")
def order_tags(b: Order, u=Depends(auth.perm("manage"))):
    with db.tx() as con:
        for i, tid in enumerate(b.ids):
            con.execute("UPDATE tags SET sort=? WHERE id=?", (i, tid))
    return {"ok": True}


class TagRename(BaseModel):
    old: str
    new: str


@app.post("/api/tags/rename")
def rename_tag(b: TagRename, u=Depends(auth.perm("manage"))):
    with db.tx() as con:
        old = con.execute("SELECT id FROM tags WHERE name=?", (b.old,)).fetchone()
        if not old:
            raise HTTPException(404, "沒有這個標籤")
        new = b.new.strip()
        if not new:
            con.execute("DELETE FROM tags WHERE id=?", (old["id"],))
            return {"ok": True}
        ex = con.execute("SELECT id FROM tags WHERE name=?", (new,)).fetchone()
        if ex:
            con.execute("INSERT OR IGNORE INTO paper_tags(paper_id, tag_id) SELECT paper_id, ? FROM paper_tags WHERE tag_id=?",
                        (ex["id"], old["id"]))
            con.execute("DELETE FROM tags WHERE id=?", (old["id"],))
        else:
            con.execute("UPDATE tags SET name=? WHERE id=?", (new, old["id"]))
    return {"ok": True}


# ================================================================== 總覽與列表
@app.get("/api/overview")
def overview(u=Depends(auth.require_user)):
    con = db.get()
    cats = categories(u)

    def recent_thumbs(where: str, args=()):
        rows = con.execute(
            f"SELECT p.id FROM papers p WHERE {where} ORDER BY p.year DESC, p.id DESC LIMIT 4", args).fetchall()
        return [library.main_file_id(con, r["id"]) for r in rows]

    for c in cats:
        c["thumbs"] = recent_thumbs("EXISTS(SELECT 1 FROM paper_categories pc WHERE pc.paper_id=p.id AND pc.category_id=?)", (c["id"],))
        c["unread"] = con.execute("SELECT COUNT(*) n FROM user_paper up JOIN paper_categories pc ON pc.paper_id=up.paper_id "
                                  "WHERE pc.category_id=? AND up.user_id=? AND up.status='待讀'", (c["id"], u["id"])).fetchone()["n"]
    tag_list = tags(u)
    for t in tag_list:
        t["thumbs"] = recent_thumbs("EXISTS(SELECT 1 FROM paper_tags pt WHERE pt.paper_id=p.id AND pt.tag_id=?)", (t["id"],))
    inbox_where = "NOT EXISTS(SELECT 1 FROM paper_categories pc WHERE pc.paper_id=p.id)"
    inbox = con.execute(f"SELECT COUNT(*) n FROM papers p WHERE {inbox_where}").fetchone()["n"]
    act = [dict(r) for r in con.execute(
        "SELECT a.action, a.detail, a.at, a.paper_id, u.display_name AS who, p.title FROM activity a "
        "LEFT JOIN users u ON u.id=a.user_id LEFT JOIN papers p ON p.id=a.paper_id WHERE a.action NOT IN ('login','settings') ORDER BY a.id DESC LIMIT 12")]
    stat = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    return {"categories": cats, "tags": tag_list, "inbox": {"count": inbox, "thumbs": recent_thumbs(inbox_where)},
            "total": stat("SELECT COUNT(*) FROM papers"), "n_ann": stat("SELECT COUNT(*) FROM annotations WHERE private=0"),
            "n_links": stat("SELECT COUNT(*) FROM links"), "n_reading": con.execute("SELECT COUNT(*) FROM user_paper WHERE user_id=? AND status='閱讀中'", (u["id"],)).fetchone()[0],
            "n_todo": con.execute("SELECT COUNT(*) FROM user_paper WHERE user_id=? AND status='待讀'", (u["id"],)).fetchone()[0],
            "assigned": [dict(r) | {"status": db.get_status(con, u["id"], r["paper_id"])} for r in con.execute(
                "SELECT pa.paper_id, pa.note, pa.created_at, p.title, p.citekey, ub.display_name AS by FROM paper_assign pa JOIN papers p ON p.id=pa.paper_id "
                "LEFT JOIN users ub ON ub.id=pa.assigned_by WHERE pa.user_id=? ORDER BY pa.created_at DESC", (u["id"],))],
            "required": {"total": stat("SELECT COUNT(*) FROM papers WHERE required=1"),
                         "read": con.execute("SELECT COUNT(*) FROM papers p JOIN user_paper up ON up.paper_id=p.id AND up.user_id=? "
                                             "WHERE p.required=1 AND up.status='已閱讀'", (u["id"],)).fetchone()[0]},
            "n_auto_links": stat("SELECT COUNT(*) FROM links WHERE auto=1"),
            "pinned": cards(con, [r["id"] for r in con.execute("SELECT id FROM papers WHERE pinned=1 ORDER BY updated_at DESC LIMIT 12")], u["id"]),
            "feed_new": stat("SELECT COUNT(*) FROM feed_items WHERE status='new'"),
            "n_meetings": con.execute("SELECT COUNT(*) FROM meetings WHERE date >= ?", (datetime.now().strftime("%Y-%m-%d"),)).fetchone()[0],
            "my_talks": [dict(r) for r in con.execute(
                "SELECT m.id, m.date, m.title, p.id AS paper_id, p.title AS paper_title FROM meeting_items mi JOIN meetings m ON m.id=mi.meeting_id "
                "LEFT JOIN papers p ON p.id=mi.paper_id WHERE mi.presenter_id=? AND m.date >= ? ORDER BY m.date LIMIT 5",
                (u["id"], datetime.now().strftime("%Y-%m-%d")))],
            "my_paths": _my_paths(con, u["id"]),
            "recent": cards(con, [r["paper_id"] for r in con.execute(
                "SELECT paper_id, MAX(COALESCE(at, day)) t FROM paper_views WHERE user_id=? GROUP BY paper_id ORDER BY t DESC LIMIT 12", (u["id"],))], u["id"]),
            "n_versions": stat("SELECT COUNT(*) FROM version_suggest WHERE status='new'"),
            "n_figures": stat("SELECT COUNT(*) FROM figures"),
            "n_regs": con.execute("SELECT COUNT(*) FROM registrations").fetchone()[0] if accounts.can_review(con, u) else 0,
            "n_my_notes": con.execute("SELECT (SELECT COUNT(*) FROM annotations WHERE author_id=? AND nb=0) + "
                                      "(SELECT COUNT(DISTINCT paper_id) FROM annotations WHERE author_id=? AND nb=1)", (u["id"], u["id"])).fetchone()[0],
            "n_paths": stat("SELECT COUNT(*) FROM paths"),
            "activity": act}


def _my_paths(con, uid: int) -> list[dict]:
    """我加入、還沒讀完的入門路徑，以及下一篇。"""
    out = []
    for r in con.execute("SELECT p.id, p.title FROM path_members pm JOIN paths p ON p.id=pm.path_id WHERE pm.user_id=? ORDER BY pm.created_at", (uid,)):
        items = con.execute("SELECT pi.paper_id, pi.goal, pp.title, up.status FROM path_items pi JOIN papers pp ON pp.id=pi.paper_id "
                            "LEFT JOIN user_paper up ON up.paper_id=pi.paper_id AND up.user_id=? WHERE pi.path_id=? ORDER BY pi.sort, pi.id",
                            (uid, r["id"])).fetchall()
        done = sum(1 for i in items if i["status"] == "已閱讀")
        nxt = next((i for i in items if i["status"] != "已閱讀"), None)
        if nxt:
            out.append({"id": r["id"], "title": r["title"], "done": done, "total": len(items),
                        "next": {"paper_id": nxt["paper_id"], "title": nxt["title"], "goal": nxt["goal"]}})
    return out


@app.get("/api/papers")
def list_papers(scope: str = "all", cat: int | None = None, tag: str | None = None, status: str | None = None,
                kind: str | None = None, q: str = "", sort: str = "", limit: int = 60, offset: int = 0,
                folder: str | None = None, pinned: int = 0, u=Depends(auth.require_user)):
    con = db.get()
    where, args = ["1=1"], []
    if scope == "inbox":
        where.append("NOT EXISTS(SELECT 1 FROM paper_categories pc WHERE pc.paper_id=p.id)")
    if cat:
        where.append("EXISTS(SELECT 1 FROM paper_categories pc WHERE pc.paper_id=p.id AND pc.category_id=?)"); args.append(cat)
    if tag:
        where.append("EXISTS(SELECT 1 FROM paper_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.paper_id=p.id AND t.name=?)")
        args.append(tag)
    # 閱讀狀態是每個人自己的
    if status == "none":
        where.append("NOT EXISTS(SELECT 1 FROM user_paper up WHERE up.paper_id=p.id AND up.user_id=?)"); args.append(u["id"])
    elif status:
        where.append("EXISTS(SELECT 1 FROM user_paper up WHERE up.paper_id=p.id AND up.user_id=? AND up.status=?)"); args += [u["id"], status]
    if scope == "assigned":
        where.append("EXISTS(SELECT 1 FROM paper_assign pa WHERE pa.paper_id=p.id AND pa.user_id=?)"); args.append(u["id"])
    elif scope == "assigned_by_me":
        where.append("EXISTS(SELECT 1 FROM paper_assign pa WHERE pa.paper_id=p.id AND pa.assigned_by=?)"); args.append(u["id"])
    elif scope == "assigned_any":
        where.append("EXISTS(SELECT 1 FROM paper_assign pa WHERE pa.paper_id=p.id)")
    elif scope == "required":
        where.append("p.required=1")
    if kind:
        where.append("p.kind=?"); args.append(kind)
    if cat and folder:
        if folder == "none":
            where.append("EXISTS(SELECT 1 FROM paper_categories pc WHERE pc.paper_id=p.id AND pc.category_id=? AND pc.folder_id IS NULL)")
            args.append(cat)
        elif folder.isdigit():
            where.append("EXISTS(SELECT 1 FROM paper_categories pc WHERE pc.paper_id=p.id AND pc.folder_id=?)"); args.append(int(folder))
    if pinned:
        where.append("p.pinned=1")
    order = {"year": "p.year IS NULL, p.year DESC, p.id DESC", "year_asc": "p.year IS NULL, p.year ASC, p.id",
             "title": "p.title COLLATE NOCASE", "updated": "p.updated_at DESC", "added": "p.added_at DESC, p.id DESC",
             "citekey": "p.citekey COLLATE NOCASE"}
    # 置頂的排最前面：分類頁看「在此分類置頂」，其他列表看「全站置頂」
    pin = ("(SELECT pc.pinned FROM paper_categories pc WHERE pc.paper_id=p.id AND pc.category_id=?) DESC, " if cat else "p.pinned DESC, ")
    ids = [r["id"] for r in con.execute(
        f"SELECT p.id FROM papers p WHERE {' AND '.join(where)} ORDER BY {pin}{order.get(sort, order['year'])}",
        args + ([cat] if cat else []))]
    terms = []
    if q.strip():
        hits, terms = search_ids(con, q)
        hit_set = set(hits)
        if sort in ("", "relevance"):
            pos = {pid: i for i, pid in enumerate(hits)}
            ids = sorted((i for i in ids if i in hit_set), key=lambda i: pos[i])
        else:
            ids = [i for i in ids if i in hit_set]
    total = len(ids)
    page = ids[offset: offset + max(1, min(limit, 500))]
    return {"total": total, "items": cards(con, page, u["id"], make_snippets(con, page, terms), cat)}


@app.get("/api/lookup")
def lookup(q: str = "", exclude: int | None = None, u=Depends(auth.require_user)):
    con = db.get()
    like = f"%{q.strip()}%"
    rows = con.execute("SELECT id, citekey, title, year, authors FROM papers WHERE (title LIKE ? OR citekey LIKE ? OR "
                       "authors LIKE ? OR doi LIKE ?) AND id != ? ORDER BY year DESC LIMIT 15",
                       (like, like, like, like, exclude or -1)).fetchall()
    return [{**dict(r), "authors": json.loads(r["authors"])[:1]} for r in rows]


# ================================================================== 單篇論文
def paper_detail(con, pid: int, user_id: int) -> dict:
    p = paper_or_404(con, pid)
    p["authors"] = json.loads(p["authors"] or "[]")
    p["keyinfo"] = json.loads(p["keyinfo"] or "{}")
    p["ai_keys"] = json.loads(p.get("ai_keys") or "[]")
    p["categories"] = [r["category_id"] for r in con.execute("SELECT category_id FROM paper_categories WHERE paper_id=?", (pid,))]
    p["tags"] = [r["name"] for r in con.execute(
        "SELECT t.name FROM paper_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.paper_id=? ORDER BY t.name", (pid,))]
    p["files"] = [dict(r) for r in con.execute(
        "SELECT id, role, label, filename, original_name, size, pages, added_at, has_text, ocr_state FROM files WHERE paper_id=? ORDER BY "
        "CASE role WHEN 'main' THEN 0 WHEN 'sm' THEN 1 WHEN 'peer_review' THEN 2 WHEN 'version' THEN 3 ELSE 4 END, id", (pid,))]
    link_sql = ("SELECT l.id, l.rel, l.note, l.src_id, l.dst_id, l.created_at, l.author_id, l.auto, u.display_name AS who, "
                "o.id AS other_id, o.citekey, o.title, o.year FROM links l JOIN papers o ON o.id = l.{o} "
                "LEFT JOIN users u ON u.id=l.author_id WHERE l.{s}=? ORDER BY l.rel, o.year")
    p["links_out"] = [dict(r) for r in con.execute(link_sql.format(o="dst_id", s="src_id"), (pid,))]
    p["links_in"] = [dict(r) for r in con.execute(link_sql.format(o="src_id", s="dst_id"), (pid,))]
    p["n_ann"] = con.execute("SELECT COUNT(*) c FROM annotations WHERE paper_id=? AND (private=0 OR author_id=?)",
                             (pid, user_id)).fetchone()["c"]
    by = con.execute("SELECT display_name FROM users WHERE id=?", (p["added_by"],)).fetchone() if p["added_by"] else None
    p["added_by_name"] = by["display_name"] if by else "匯入"
    p["meetings"] = [dict(r) for r in con.execute(
        "SELECT m.id, m.date, m.title, mi.id AS item_id, u.display_name AS presenter FROM meeting_items mi "
        "JOIN meetings m ON m.id=mi.meeting_id LEFT JOIN users u ON u.id=mi.presenter_id WHERE mi.paper_id=? ORDER BY m.date DESC", (pid,))]
    z = con.execute("SELECT item_key FROM zotero_map WHERE paper_id=?", (pid,)).fetchone()
    p["zotero_key"] = z["item_key"] if z else None
    p["status"] = db.get_status(con, user_id, pid)
    # 指派是公開的；被指派的人是否讀完也公開（其他閱讀狀態仍是私人）
    p["assignments"] = [dict(r) | {"done": r["st"] == "已閱讀"} for r in con.execute(
        "SELECT pa.user_id, pa.note, pa.created_at, pa.assigned_by, u.display_name AS name, ub.display_name AS by, up.status AS st "
        "FROM paper_assign pa JOIN users u ON u.id=pa.user_id LEFT JOIN users ub ON ub.id=pa.assigned_by "
        "LEFT JOIN user_paper up ON up.user_id=pa.user_id AND up.paper_id=pa.paper_id WHERE pa.paper_id=? ORDER BY pa.created_at", (pid,))]
    for a in p["assignments"]:
        a.pop("st", None)
    # 入門路徑：這篇在哪些路徑的第幾步
    p["paths"] = []
    for r in con.execute("SELECT pi.path_id, pi.goal, pa.title FROM path_items pi JOIN paths pa ON pa.id=pi.path_id WHERE pi.paper_id=?", (pid,)):
        steps = [x["paper_id"] for x in con.execute("SELECT paper_id FROM path_items WHERE path_id=? ORDER BY sort, id", (r["path_id"],))]
        p["paths"].append({"id": r["path_id"], "title": r["title"], "goal": r["goal"], "step": steps.index(pid) + 1, "total": len(steps)})
    p["n_figures"] = con.execute("SELECT COUNT(*) FROM figures WHERE paper_id=?", (pid,)).fetchone()[0]
    p["n_params"] = con.execute("SELECT COUNT(*) FROM paper_params WHERE paper_id=?", (pid,)).fetchone()[0]
    p["versions"] = [dict(r) for r in con.execute(
        "SELECT id, kind, other_id, doi, venue, year, source, score, status, resolved_at FROM version_suggest "
        "WHERE (paper_id=? OR other_id=?) AND (status='new' OR (kind='published' AND status='applied')) ORDER BY id DESC", (pid, pid))]
    return p


@app.get("/api/papers/{pid}")
def get_paper(pid: int, u=Depends(auth.require_user)):
    con = db.get()
    p = paper_detail(con, pid, u["id"])
    # 開啟次數（看板用；只統計次數，不公開誰看了什麼）
    con.execute("INSERT INTO paper_views(paper_id, user_id, day, at) VALUES(?,?,?,?) ON CONFLICT(paper_id, user_id, day) DO UPDATE SET at=excluded.at",
                (pid, u["id"], datetime.now().strftime("%Y-%m-%d"), db.now()))
    return p


@app.patch("/api/papers/{pid}")
def edit_paper(pid: int, b: dict[str, Any], u=Depends(auth.require_user)):
    with db.tx() as con:
        p = paper_or_404(con, pid)
        if "status" in b:          # 個人閱讀狀態：任何人都能改自己的
            _set_my_status(con, u, pid, b.pop("status"))
            if not b:
                db.log(con, u["id"], "status", pid, "")
                return paper_detail(con, pid, u["id"])
        keys = set(b)
        if keys & {"categories", "suggested_category_id"}:
            auth.need(u, "categorize", p)
        if "tags" in keys:
            auth.need(u, "tag", p)
        if keys - {"categories", "suggested_category_id", "tags"}:
            auth.need(u, "edit_meta", p)
        sets, args = [], []
        for k in ("title", "venue", "abstract", "arxiv"):
            if k in b:
                sets.append(f"{k}=?"); args.append(str(b[k] or "").strip())
        if "doi" in b:
            doi = str(b["doi"] or "").strip()
            doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
            if doi and con.execute("SELECT id FROM papers WHERE lower(doi)=lower(?) AND id!=?", (doi, pid)).fetchone():
                raise HTTPException(409, "另一篇論文已使用這個 DOI")
            sets.append("doi=?"); args.append(doi)
        if "year" in b:
            y = b["year"]
            sets.append("year=?"); args.append(int(y) if str(y or "").strip().isdigit() else None)
        if "authors" in b:
            sets.append("authors=?"); args.append(json.dumps(parse_authors(b["authors"]), ensure_ascii=False))
        if "authors_complete" in b:
            sets.append("authors_complete=?"); args.append(int(bool(b["authors_complete"])))
        if "kind" in b and b["kind"] in library.KINDS:
            sets.append("kind=?"); args.append(b["kind"])
        if "keyinfo" in b and isinstance(b["keyinfo"], dict):
            clean = {str(k).strip(): str(v) for k, v in b["keyinfo"].items() if str(k).strip() and str(v).strip()}
            sets.append("keyinfo=?"); args.append(json.dumps(clean, ensure_ascii=False))
            # 人改過的欄位就不再標成「AI 產生」
            old = json.loads(p["keyinfo"] or "{}")
            ai_keys = [k for k in json.loads(p.get("ai_keys") or "[]") if k in clean and clean[k] == old.get(k)]
            sets.append("ai_keys=?"); args.append(json.dumps(ai_keys, ensure_ascii=False))
        if "suggested_category_id" in b:
            sets.append("suggested_category_id=?"); args.append(b["suggested_category_id"])
        if "citekey" in b:
            ck = re.sub(r"[^\w\-]", "", str(b["citekey"] or ""))
            if ck and ck != p["citekey"]:
                if con.execute("SELECT 1 FROM papers WHERE citekey=?", (ck,)).fetchone():
                    raise HTTPException(409, "citekey 已被使用")
                sets.append("citekey=?"); args.append(ck)
        if sets:
            sets.append("updated_at=?"); args.append(db.now())
            con.execute(f"UPDATE papers SET {', '.join(sets)} WHERE id=?", args + [pid])
        if "categories" in b:
            db.set_categories(con, pid, b["categories"] or [])
        if "tags" in b:
            db.set_tags(con, pid, b["tags"] or [])
        db.fts_update(con, pid)
        db.log(con, u["id"], "edit", pid, ",".join(sorted(b.keys())))
        return paper_detail(con, pid, u["id"])


def _set_my_status(con, u, pid: int, status):
    old = db.get_status(con, u["id"], pid)
    db.set_status(con, u["id"], pid, status)
    if status == "已閱讀" and old != "已閱讀":
        a = con.execute("SELECT assigned_by FROM paper_assign WHERE paper_id=? AND user_id=?", (pid, u["id"])).fetchone()
        if a and a["assigned_by"]:
            t = con.execute("SELECT title FROM papers WHERE id=?", (pid,)).fetchone()
            notify.push(con, [a["assigned_by"]], "done", u["id"], f"{u['display_name']} 讀完了你指派的：{t['title']}", pid)


class Bulk(BaseModel):
    ids: list[int]
    add_categories: list[int] = []
    remove_categories: list[int] = []
    accept_suggestion: bool = False
    add_tags: list[str] = []
    remove_tags: list[str] = []
    status: str | None = None


@app.post("/api/papers/bulk")
def bulk(b: Bulk, u=Depends(auth.require_user)):
    with db.tx() as con:
        for pid in b.ids:
            p = paper_or_404(con, pid)
            if b.add_categories or b.remove_categories or b.accept_suggestion:
                auth.need(u, "categorize", p)
            if b.add_tags or b.remove_tags:
                auth.need(u, "tag", p)
            cats = {r["category_id"] for r in con.execute("SELECT category_id FROM paper_categories WHERE paper_id=?", (pid,))}
            if b.accept_suggestion:
                s = con.execute("SELECT suggested_category_id s FROM papers WHERE id=?", (pid,)).fetchone()
                if s and s["s"]:
                    cats.add(s["s"])
            cats |= set(b.add_categories)
            cats -= set(b.remove_categories)
            db.set_categories(con, pid, sorted(cats))
            if b.add_tags or b.remove_tags:
                tg = {r["name"] for r in con.execute(
                    "SELECT t.name FROM paper_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.paper_id=?", (pid,))}
                db.set_tags(con, pid, sorted((tg | set(b.add_tags)) - set(b.remove_tags)))
            if b.status is not None:
                _set_my_status(con, u, pid, b.status or None)
            con.execute("UPDATE papers SET updated_at=? WHERE id=?", (db.now(), pid))
        db.log(con, u["id"], "bulk", None, f"{len(b.ids)} 篇")
    return {"ok": True}


@app.delete("/api/papers/{pid}")
def delete_paper(pid: int, u=Depends(auth.require_user)):
    with db.tx() as con:
        can_edit(u, pid, "delete", con)
        p = paper_detail(con, pid, u["id"])
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = config.TRASH_DIR / f"{stamp}-{pid}-{p['citekey']}"
        src = config.FILES_DIR / str(pid)
        dest.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.move(str(src), dest / "files")
        figs = config.DATA_DIR / "figures" / str(pid)
        if figs.exists():
            shutil.move(str(figs), dest / "figures")
        anns = [dict(r) for r in con.execute("SELECT * FROM annotations WHERE paper_id=?", (pid,))]
        (dest / "paper.json").write_text(json.dumps({"paper": p, "annotations": anns}, ensure_ascii=False, indent=1, default=str),
                                         encoding="utf-8")
        for f in p["files"]:
            (config.THUMBS_DIR / f"{f['id']}.jpg").unlink(missing_ok=True)
        con.execute("DELETE FROM papers WHERE id=?", (pid,))
        con.execute("DELETE FROM fts WHERE paper_id=?", (pid,))
        db.log(con, u["id"], "delete", None, p["title"])
    return {"ok": True, "trash": str(dest.relative_to(config.DATA_DIR))}


@app.post("/api/papers/{pid}/enrich")
def enrich_paper(pid: int, b: dict[str, Any] | None = None, u=Depends(auth.require_user)):
    con = db.get()
    p = can_edit(u, pid, "edit_meta", con)
    try:
        info = enrich.lookup(p["doi"], p["arxiv"])
    except RuntimeError as e:
        raise HTTPException(502, f"無法取得書目：{e}")
    overwrite = bool((b or {}).get("overwrite"))
    changed = {}
    for k in ("title", "year", "venue", "abstract"):
        v = info.get(k)
        if v and (overwrite or not p[k] or (k == "title" and p["title"] == "（未命名）")):
            changed[k] = v
    if info.get("authors") and (overwrite or not p["authors_complete"]):
        changed["authors"] = info["authors"]; changed["authors_complete"] = True
    if changed:
        edit_paper(pid, changed, u)
    return {"changed": sorted(changed), "paper": paper_detail(con, pid, u["id"])}


@app.get("/api/papers/{pid}/bibtex", response_class=PlainTextResponse)
def paper_bibtex(pid: int, u=Depends(auth.require_user)):
    return library.bibtex(paper_or_404(db.get(), pid))


# ================================================================== 檔案
def file_or_404(con, fid: int) -> dict:
    f = con.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
    if f is None:
        raise HTTPException(404, "找不到檔案")
    return dict(f)


@app.get("/api/files/{fid}/content")
def file_content(fid: int, download: int = 0, u=Depends(auth.require_user)):
    f = file_or_404(db.get(), fid)
    return FileResponse(data_path(f["path"]), media_type="application/pdf", filename=f["filename"],
                        content_disposition_type="attachment" if download else "inline",
                        headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/files/{fid}/thumb")
def file_thumb(fid: int, u=Depends(auth.require_user)):
    p = config.THUMBS_DIR / f"{fid}.jpg"
    if not p.exists():
        f = file_or_404(db.get(), fid)
        try:
            pdftools.render_thumb(data_path(f["path"]), p)
        except Exception:  # noqa: BLE001
            raise HTTPException(404, "無縮圖")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.patch("/api/files/{fid}")
def edit_file(fid: int, b: dict[str, Any], u=Depends(auth.require_user)):
    with db.tx() as con:
        f = file_or_404(con, fid)
        can_edit(u, f["paper_id"], "edit_meta", con)
        if "role" in b and b["role"] in library.ROLES:
            con.execute("UPDATE files SET role=? WHERE id=?", (b["role"], fid))
        if "label" in b:
            con.execute("UPDATE files SET label=? WHERE id=?", (str(b["label"] or "")[:80], fid))
        if b.get("paper_id") and int(b["paper_id"]) != f["paper_id"]:
            target = can_edit(u, int(b["paper_id"]), "edit_meta", con)
            folder = config.FILES_DIR / str(target["id"])
            folder.mkdir(parents=True, exist_ok=True)
            src = data_path(f["path"])
            name = library._unique_name(folder, f["filename"])
            shutil.move(str(src), folder / name)
            con.execute("UPDATE files SET paper_id=?, filename=?, path=? WHERE id=?",
                        (target["id"], name, str((folder / name).relative_to(config.DATA_DIR)), fid))
        db.log(con, u["id"], "file", f["paper_id"], f["filename"])
    return {"ok": True}


@app.delete("/api/files/{fid}")
def delete_file(fid: int, u=Depends(auth.require_user)):
    with db.tx() as con:
        f = file_or_404(con, fid)
        can_edit(u, f["paper_id"], "delete", con)
        src = data_path(f["path"])
        dest = config.TRASH_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-file-{fid}-{f['filename']}"
        if src.exists():
            shutil.move(str(src), dest)
        (config.THUMBS_DIR / f"{fid}.jpg").unlink(missing_ok=True)
        con.execute("DELETE FROM files WHERE id=?", (fid,))
    return {"ok": True}


# ------------------------------------------------------------------ 上傳（兩段式：先分析、再確認）
async def _save_upload(up: UploadFile, dest: Path) -> int:
    size, limit = 0, config.MAX_UPLOAD_MB * 1024 * 1024
    with open(dest, "wb") as fh:
        while chunk := await up.read(1 << 20):
            size += len(chunk)
            if size > limit:
                fh.close(); dest.unlink(missing_ok=True)
                raise HTTPException(413, f"{up.filename} 超過 {config.MAX_UPLOAD_MB} MB")
            fh.write(chunk)
    return size


def _strip_supp(title: str) -> str:
    return re.sub(r"^(supplementary|supporting)\s+(material|information|materials)\s*(for)?\s*[:'’\"“]*", "", title, flags=re.I).strip(" '’\"”")


def _best_title_match(con, title: str):
    t = _strip_supp(title).lower()
    if len(t) < 8:
        return None
    best, score = None, 0.0
    for r in con.execute("SELECT id, title FROM papers"):
        s = difflib.SequenceMatcher(None, t, r["title"].lower()).ratio()
        if s > score:
            best, score = r, s
    return {"paper_id": best["id"], "title": best["title"], "score": round(score, 2)} if best and score >= 0.72 else None


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...), u=Depends(auth.perm("upload"))):
    con = db.get()
    out = []
    for up in files:
        token = secrets.token_hex(12)
        dest = config.STAGING_DIR / f"{token}.pdf"
        await _save_upload(up, dest)
        item = {"token": token, "filename": up.filename}
        if not pdftools.is_pdf(dest):
            dest.unlink(missing_ok=True)
            out.append({**item, "status": "rejected", "reason": "不是 PDF 檔"})
            continue
        sha = library.sha256_file(dest)
        dup = con.execute("SELECT f.paper_id, p.title FROM files f JOIN papers p ON p.id=f.paper_id WHERE f.sha256=? OR f.orig_sha256=?",
                          (sha, sha)).fetchone()
        if dup:
            dest.unlink(missing_ok=True)
            out.append({**item, "status": "duplicate", "paper_id": dup["paper_id"], "title": dup["title"]})
            continue
        info = pdftools.analyze(dest, up.filename or "")
        item.update(status="ok", sha256=sha, **info)
        if info["doi"]:
            m = con.execute("SELECT id, title FROM papers WHERE lower(doi)=lower(?)", (info["doi"],)).fetchone()
            if m:
                item["doi_match"] = {"paper_id": m["id"], "title": m["title"]}
        if info["arxiv"] and "doi_match" not in item:
            m = con.execute("SELECT id, title FROM papers WHERE arxiv=?", (info["arxiv"],)).fetchone()
            if m:
                item["doi_match"] = {"paper_id": m["id"], "title": m["title"]}
        if info["is_supplement"] or info["is_peer_review"]:
            item["title_match"] = _best_title_match(con, info["title"])
        (config.STAGING_DIR / f"{token}.json").write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
        out.append(item)
    return out


class CommitItem(BaseModel):
    token: str
    action: str                 # new | attach | skip
    paper_id: int | None = None
    role: str = "main"
    label: str = ""
    title: str | None = None
    first_author: str | None = None
    year: int | None = None
    doi: str | None = None
    category_ids: list[int] = []
    tags: list[str] = []


class Commit(BaseModel):
    items: list[CommitItem]


@app.post("/api/upload/commit")
def upload_commit(b: Commit, u=Depends(auth.perm("upload"))):
    results, new_files = [], []
    for it in b.items:
        token = re.sub(r"[^0-9a-f]", "", it.token)
        pdf = config.STAGING_DIR / f"{token}.pdf"
        meta_p = config.STAGING_DIR / f"{token}.json"
        if it.action == "skip" or not pdf.exists():
            pdf.unlink(missing_ok=True); meta_p.unlink(missing_ok=True)
            results.append({"token": token, "status": "skipped"})
            continue
        meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
        try:
            with db.tx() as con:
                if it.action == "attach":
                    can_edit(u, it.paper_id, "upload", con)
                    fid = library.add_file(con, it.paper_id, pdf, meta.get("filename", ""), it.role, it.label, u["id"],
                                           move=True, sha=meta.get("sha256"))
                    db.log(con, u["id"], "attach", it.paper_id, meta.get("filename", ""))
                    results.append({"token": token, "status": "attached", "paper_id": it.paper_id, "file_id": fid})
                    new_files.append(fid)
                else:
                    doi = (it.doi if it.doi is not None else meta.get("doi", "")).strip()
                    if doi and con.execute("SELECT 1 FROM papers WHERE lower(doi)=lower(?)", (doi,)).fetchone():
                        doi = ""  # DOI 已被其他論文使用：保留檔案，但不重複登記 DOI
                    author = it.first_author if it.first_author is not None else meta.get("author", "")
                    pid = library.create_paper(con, {
                        "title": it.title or meta.get("title") or meta.get("filename", ""),
                        "authors": [author] if author else [], "year": it.year or meta.get("year"),
                        "doi": doi, "arxiv": meta.get("arxiv", ""),
                        "status": "待讀" if not it.category_ids else None}, u["id"])
                    library.add_file(con, pid, pdf, meta.get("filename", ""), "main", "", u["id"], move=True,
                                     sha=meta.get("sha256"))
                    db.set_categories(con, pid, it.category_ids if u["perms"]["categorize"] else [])
                    db.set_tags(con, pid, it.tags if u["perms"]["tag"] else [])
                    db.fts_update(con, pid, None)
                    db.log(con, u["id"], "upload", pid, meta.get("filename", ""))
                    results.append({"token": token, "status": "created", "paper_id": pid})
                    new_files.append(library.main_file_id(con, pid))
        except (ValueError, HTTPException) as e:
            results.append({"token": token, "status": "error", "reason": getattr(e, "detail", str(e))})
        meta_p.unlink(missing_ok=True)
    _after_files(new_files, u)
    return results


def _after_files(fids, u):
    """新檔案：沒有文字層就排 OCR；有新論文就重新分析引用。"""
    con = db.get()
    for fid in fids:
        if fid:
            ocr.queue_if_needed(con, fid, u["id"])
    if fids:
        jobs.enqueue("refs", "", u["id"])


@app.post("/api/papers/{pid}/files")
async def attach_file(pid: int, file: UploadFile = File(...), role: str = Form("sm"), label: str = Form(""),
                      u=Depends(auth.perm("upload"))):
    can_edit(u, pid, "upload")
    token = secrets.token_hex(12)
    dest = config.STAGING_DIR / f"{token}.pdf"
    await _save_upload(file, dest)
    if not pdftools.is_pdf(dest):
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "不是 PDF 檔")
    try:
        with db.tx() as con:
            paper_or_404(con, pid)
            fid = library.add_file(con, pid, dest, file.filename or "", role, label, u["id"], move=True)
            db.log(con, u["id"], "attach", pid, file.filename or "")
    except ValueError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(409, str(e))
    _after_files([fid], u)
    return {"ok": True, "file_id": fid}


# ================================================================== 標註
class AnnIn(BaseModel):
    file_id: int | None = None
    page: int | None = None
    kind: str = "note"
    color: str = "yellow"
    quote: str = ""
    body: str = ""
    rects: list[list[float]] = []
    private: bool = False
    ink: dict | None = None


def clean_ink(ink) -> str:
    """手寫筆跡：座標是頁面寬高的比例（0–1），每一筆有顏色、粗細、透明度。"""
    if not isinstance(ink, dict):
        return ""
    out = _clean_strokes(ink.get("strokes"))
    return json.dumps({"strokes": out}, separators=(",", ":")) if out else ""


def _clean_strokes(strokes) -> list:
    out = []
    for s in (strokes or [])[:5000]:
        if not isinstance(s, dict):
            continue
        try:
            pts = [[round(min(1.0, max(0.0, float(x))), 4), round(min(1.0, max(0.0, float(y))), 4)] for x, y in (s.get("p") or [])[:4000]]
        except (TypeError, ValueError):
            continue
        if len(pts) < 1:
            continue
        c = str(s.get("c") or "#111111")
        st = {"c": c if re.match(r"^#[0-9a-fA-F]{6}$", c) else "#111111", "w": round(min(0.05, max(0.0005, float(s.get("w") or 0.003))), 5),
              "o": round(min(1.0, max(0.1, float(s.get("o") or 1))), 2), "p": pts}
        sid = str(s.get("s") or "")
        if re.fullmatch(r"[A-Za-z0-9_-]{1,24}", sid):
            st["s"] = sid                      # 每一筆的編號：重送時不會重複加入
        out.append(st)
    return out


# ---------------------------------------------------------------- 手寫即時儲存
# 瀏覽器每畫一筆就送出；沒網路或伺服器沒回應時先存在瀏覽器（離線佇列），連上後依序補送。
# op：append＝加幾筆（標註不存在就建立）；set＝整則換成這些筆畫（橡皮擦、復原）；delete＝刪除整則
def _ink_row(con, uid: str):
    r = con.execute("SELECT * FROM annotations WHERE client_uid=?", (uid,)).fetchone()
    if r is None and re.fullmatch(r"a\d+", uid):
        r = con.execute("SELECT * FROM annotations WHERE id=?", (int(uid[1:]),)).fetchone()
    return dict(r) if r else None


def _ink_op(con, u, o: dict) -> dict:
    uid = str(o.get("uid") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,40}", uid):
        raise HTTPException(400, "編號不正確")
    op = o.get("op")
    row = _ink_row(con, uid)
    if row is not None and row["author_id"] != u["id"] and u["role"] != "admin":
        raise HTTPException(403, "只能修改自己的手寫")
    if row is not None and row["kind"] != "ink":
        raise HTTPException(400, "這則不是手寫")
    if op == "delete":
        if row is not None:
            con.execute("DELETE FROM annotations WHERE id=?", (row["id"],))
        return {"uid": uid, "ok": True, "deleted": True}
    if op not in ("append", "set"):
        raise HTTPException(400, "不支援的操作")
    strokes = _clean_strokes(o.get("strokes"))
    t = db.now()
    if row is None:
        auth.need(u, "annotate")
        pid = int(o.get("pid") or 0)
        paper_or_404(con, pid)
        if not strokes:
            return {"uid": uid, "ok": True}
        nb = 1 if o.get("nb") else 0
        fid = o.get("file_id")
        if nb or fid is None:
            fid = None
        elif not con.execute("SELECT 1 FROM files WHERE id=? AND paper_id=?", (int(fid), pid)).fetchone():
            raise HTTPException(400, "檔案不屬於這篇論文")
        page = max(1, min(int(o.get("page") or 1), 5000))
        cur = con.execute(
            "INSERT INTO annotations(paper_id, file_id, page, kind, color, quote, body, rects, private, author_id, created_at, updated_at, ink, client_uid, nb) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, fid, page, "ink", "ink", "", "", "[]", int(bool(o.get("private"))), u["id"], t, t,
             json.dumps({"strokes": strokes}, separators=(",", ":")), uid, nb))
        return {"uid": uid, "ok": True, "id": cur.lastrowid, "created": True}
    have = (json.loads(row["ink"]) if row["ink"] else {}).get("strokes", [])
    if op == "append":
        seen = {x.get("s") for x in have if x.get("s")}
        have = have + [x for x in strokes if not x.get("s") or x["s"] not in seen]
    else:
        have = strokes
    if not have:
        con.execute("DELETE FROM annotations WHERE id=?", (row["id"],))
        return {"uid": uid, "ok": True, "id": row["id"], "deleted": True}
    con.execute("UPDATE annotations SET ink=?, updated_at=? WHERE id=?", (json.dumps({"strokes": have}, separators=(",", ":")), t, row["id"]))
    return {"uid": uid, "ok": True, "id": row["id"]}


@app.post("/api/ink/sync")
def ink_sync(b: dict[str, Any], u=Depends(auth.require_user)):
    out = []
    logged = set()
    with db.tx() as con:
        for o in (b.get("ops") or [])[:300]:
            if not isinstance(o, dict):
                continue
            con.execute("SAVEPOINT op")
            try:
                r = _ink_op(con, u, o)
                con.execute("RELEASE op")
            except HTTPException as e:
                con.execute("ROLLBACK TO op"); con.execute("RELEASE op")
                r = {"uid": o.get("uid"), "ok": False, "error": e.detail, "status": e.status_code}
            except (TypeError, ValueError) as e:
                con.execute("ROLLBACK TO op"); con.execute("RELEASE op")
                r = {"uid": o.get("uid"), "ok": False, "error": f"資料格式錯誤：{e}", "status": 400}
            if r.get("created") and o.get("pid") not in logged:
                logged.add(o.get("pid"))
                db.log(con, u["id"], "annotate", int(o.get("pid")), "筆記頁手寫" if o.get("nb") else "手寫")
            out.append(r)
    return {"results": out}


# ---------------------------------------------------------------- 筆記頁（每人每篇一本，空白頁手寫）
NB_BG = ("blank", "lined", "grid", "dots")


def _notebook(con, pid: int, uid: int) -> dict:
    r = con.execute("SELECT pages, bg, updated_at FROM notebooks WHERE paper_id=? AND user_id=?", (pid, uid)).fetchone()
    mx = con.execute("SELECT MAX(page) m FROM annotations WHERE paper_id=? AND author_id=? AND nb=1", (pid, uid)).fetchone()["m"] or 0
    return {"pages": max(r["pages"] if r else 1, mx, 1), "bg": r["bg"] if r else "lined"}


@app.get("/api/papers/{pid}/notebook")
def get_notebook(pid: int, u=Depends(auth.require_user)):
    con = db.get()
    p = paper_or_404(con, pid)
    return {**_notebook(con, pid, u["id"]), "paper": {"id": p["id"], "title": p["title"], "citekey": p["citekey"]}}


@app.put("/api/papers/{pid}/notebook")
def put_notebook(pid: int, b: dict[str, Any], u=Depends(auth.perm("annotate"))):
    with db.tx() as con:
        paper_or_404(con, pid)
        cur = _notebook(con, pid, u["id"])
        pages = max(1, min(int(b.get("pages") or cur["pages"]), 500))
        bg = b.get("bg") if b.get("bg") in NB_BG else cur["bg"]
        con.execute("INSERT INTO notebooks(paper_id, user_id, pages, bg, updated_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(paper_id, user_id) DO UPDATE SET pages=excluded.pages, bg=excluded.bg, updated_at=excluded.updated_at",
                    (pid, u["id"], pages, bg, db.now()))
    return {"pages": pages, "bg": bg}


@app.get("/api/papers/{pid}/notebook.pdf")
def notebook_pdf(pid: int, u=Depends(auth.require_user)):
    import pymupdf
    con = db.get()
    p = paper_or_404(con, pid)
    nb = _notebook(con, pid, u["id"])
    W, H = 595.0, 842.0
    doc = pymupdf.open()
    rows = con.execute("SELECT page, ink FROM annotations WHERE paper_id=? AND author_id=? AND nb=1 ORDER BY page, id", (pid, u["id"])).fetchall()
    by = {}
    for r in rows:
        by.setdefault(r["page"], []).extend((json.loads(r["ink"]) if r["ink"] else {}).get("strokes", []))
    for n in range(1, nb["pages"] + 1):
        page = doc.new_page(width=W, height=H)
        sh = page.new_shape()
        if nb["bg"] == "lined":
            for y in range(24, int(H), 24):
                sh.draw_line((0, y), (W, y))
            sh.finish(color=(0.80, 0.85, 0.9), width=0.5)
        elif nb["bg"] == "grid":
            for y in range(20, int(H), 20):
                sh.draw_line((0, y), (W, y))
            for x in range(20, int(W), 20):
                sh.draw_line((x, 0), (x, H))
            sh.finish(color=(0.86, 0.89, 0.93), width=0.4)
        elif nb["bg"] == "dots":
            for y in range(20, int(H), 20):
                for x in range(20, int(W), 20):
                    sh.draw_circle((x, y), 0.7)
            sh.finish(color=(0.7, 0.75, 0.82), fill=(0.7, 0.75, 0.82), width=0)
        for st in by.get(n, []):
            pts = [pymupdf.Point(x * W, y * H) for x, y in st["p"]]
            c = st.get("c", "#111111")
            rgb = tuple(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))
            if len(pts) == 1:
                sh.draw_circle(pts[0], max(0.3, st.get("w", 0.003) * W / 2))
                sh.finish(color=rgb, fill=rgb, width=0, stroke_opacity=st.get("o", 1), fill_opacity=st.get("o", 1))
                continue
            sh.draw_polyline(pts)
            sh.finish(color=rgb, width=max(0.3, st.get("w", 0.003) * W), lineCap=1, lineJoin=1, closePath=False, stroke_opacity=st.get("o", 1))
        sh.commit()
        page.insert_text((W - 60, H - 16), f"{n} / {nb['pages']}", fontsize=8, color=(0.6, 0.6, 0.6))
    doc.set_metadata({"title": f"{p['citekey']} 筆記頁", "author": u["display_name"]})
    data = doc.tobytes()
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", p["citekey"] or f"paper{pid}") + "_notes.pdf"
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/papers/{pid}/annotations")
def list_ann(pid: int, u=Depends(auth.require_user)):
    con = db.get()
    rows = [dict(r) | {"rects": json.loads(r["rects"] or "[]"), "ink": json.loads(r["ink"]) if r["ink"] else None, "replies": []} for r in con.execute(
        "SELECT a.*, u.display_name AS who FROM annotations a LEFT JOIN users u ON u.id=a.author_id "
        "WHERE a.paper_id=? AND (a.private=0 OR a.author_id=?) ORDER BY a.page IS NULL, a.page, a.id", (pid, u["id"]))]
    by = {a["id"]: a for a in rows}
    if by:
        qm = ",".join("?" * len(by))
        for r in con.execute(f"SELECT r.*, u.display_name AS who FROM ann_replies r LEFT JOIN users u ON u.id=r.author_id "
                             f"WHERE r.ann_id IN ({qm}) ORDER BY r.id", list(by)):
            by[r["ann_id"]]["replies"].append(dict(r))
    return rows


def _mention(con, u, text: str, pid: int, aid: int, private: bool = False):
    if private:
        return
    ids = notify.mentioned(con, text)
    notify.push(con, ids, "mention", u["id"], f"{u['display_name']} 在筆記中提到你：{text[:120]}", pid, aid)


@app.post("/api/papers/{pid}/annotations")
def add_ann(pid: int, a: AnnIn, u=Depends(auth.perm("annotate"))):
    with db.tx() as con:
        paper_or_404(con, pid)
        t = db.now()
        cur = con.execute(
            "INSERT INTO annotations(paper_id, file_id, page, kind, color, quote, body, rects, private, author_id, created_at, updated_at, ink) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, a.file_id, a.page, a.kind if a.kind in ("note", "highlight", "ink") else "note", a.color[:20], a.quote[:4000],
             a.body[:20000], json.dumps([[round(x, 5) for x in r[:4]] for r in a.rects[:200]]), int(a.private), u["id"], t, t,
             clean_ink(a.ink) if a.kind == "ink" else ""))
        db.fts_update(con, pid)
        db.log(con, u["id"], "annotate", pid, (a.quote or a.body)[:80])
        aid = cur.lastrowid
        _mention(con, u, a.body, pid, aid, a.private)
    return next(x for x in list_ann(pid, u) if x["id"] == aid)


def _own_ann(con, aid: int, u) -> dict:
    a = con.execute("SELECT * FROM annotations WHERE id=?", (aid,)).fetchone()
    if a is None:
        raise HTTPException(404, "找不到標註")
    if a["author_id"] != u["id"] and u["role"] != "admin":
        raise HTTPException(403, "只能修改自己的標註")
    return dict(a)


@app.patch("/api/annotations/{aid}")
def edit_ann(aid: int, b: dict[str, Any], u=Depends(auth.perm("annotate"))):
    with db.tx() as con:
        a = _own_ann(con, aid, u)
        for k in ("body", "color", "quote"):
            if k in b:
                con.execute(f"UPDATE annotations SET {k}=? WHERE id=?", (str(b[k] or ""), aid))
        if "body" in b:
            old = notify.mentioned(con, a["body"])
            new = notify.mentioned(con, str(b["body"] or "")) - old
            if new and not b.get("private", a["private"]):
                notify.push(con, new, "mention", u["id"], f"{u['display_name']} 在筆記中提到你：{str(b['body'])[:120]}", a["paper_id"], aid)
        if "private" in b:
            con.execute("UPDATE annotations SET private=? WHERE id=?", (int(bool(b["private"])), aid))
        if "page" in b:
            con.execute("UPDATE annotations SET page=? WHERE id=?", (b["page"], aid))
        if "ink" in b and a["kind"] == "ink":
            con.execute("UPDATE annotations SET ink=? WHERE id=?", (clean_ink(b["ink"]), aid))
        con.execute("UPDATE annotations SET updated_at=? WHERE id=?", (db.now(), aid))
        db.fts_update(con, a["paper_id"])
    return {"ok": True}


@app.delete("/api/annotations/{aid}")
def del_ann(aid: int, u=Depends(auth.perm("annotate"))):
    with db.tx() as con:
        a = _own_ann(con, aid, u)
        con.execute("DELETE FROM annotations WHERE id=?", (aid,))
        db.fts_update(con, a["paper_id"])
    return {"ok": True}


# ================================================================== 關聯
class LinkIn(BaseModel):
    src_id: int
    dst_id: int
    rel: str
    note: str = ""


@app.get("/api/rels")
def rels(u=Depends(auth.require_user)):
    used = [r["rel"] for r in db.get().execute("SELECT rel, COUNT(*) c FROM links GROUP BY rel ORDER BY c DESC")]
    return list(dict.fromkeys(db.DEFAULT_RELS + used))


@app.post("/api/links")
def add_link(b: LinkIn, u=Depends(auth.perm("link"))):
    if b.src_id == b.dst_id:
        raise HTTPException(400, "不能連到自己")
    rel = b.rel.strip()[:40]
    if not rel:
        raise HTTPException(400, "請填關係")
    with db.tx() as con:
        paper_or_404(con, b.src_id); paper_or_404(con, b.dst_id)
        con.execute("INSERT INTO links(src_id, dst_id, rel, note, author_id, created_at) VALUES(?,?,?,?,?,?)",
                    (b.src_id, b.dst_id, rel, b.note[:2000], u["id"], db.now()))
        db.log(con, u["id"], "link", b.src_id, rel)
    return {"ok": True}


def _own_link(con, lid: int, u) -> None:
    l = con.execute("SELECT author_id, auto FROM links WHERE id=?", (lid,)).fetchone()
    if l is None:
        raise HTTPException(404, "找不到關聯")
    if l["auto"]:
        return   # 自動引用關聯：有「關聯」權限的人都可以刪（刪了不會再自動加回）
    if l["author_id"] != u["id"] and u["role"] != "admin":
        raise HTTPException(403, "只能修改自己建立的關聯")


@app.patch("/api/links/{lid}")
def edit_link(lid: int, b: dict[str, Any], u=Depends(auth.perm("link"))):
    con = db.get()
    _own_link(con, lid, u)
    if "rel" in b and str(b["rel"]).strip():
        con.execute("UPDATE links SET rel=? WHERE id=?", (str(b["rel"]).strip()[:40], lid))
    if "note" in b:
        con.execute("UPDATE links SET note=? WHERE id=?", (str(b["note"] or "")[:2000], lid))
    if "rel" in b or "note" in b:   # 手動改過的自動關聯變成一般關聯
        con.execute("UPDATE links SET auto=0, author_id=COALESCE(author_id, ?) WHERE id=?", (u["id"], lid))
    return {"ok": True}


@app.delete("/api/links/{lid}")
def del_link(lid: int, u=Depends(auth.perm("link"))):
    con = db.get()
    _own_link(con, lid, u)
    l = con.execute("SELECT src_id, dst_id, auto FROM links WHERE id=?", (lid,)).fetchone()
    if l["auto"]:
        con.execute("INSERT OR IGNORE INTO ref_ignore(src_id, dst_id) VALUES(?,?)", (l["src_id"], l["dst_id"]))
    con.execute("DELETE FROM links WHERE id=?", (lid,))
    return {"ok": True}


@app.get("/api/graph")
def graph(cat: int | None = None, all_nodes: int = 0, focus: int | None = None, auto: int = 1, u=Depends(auth.require_user)):
    con = db.get()
    edges = [dict(r) for r in con.execute("SELECT id, src_id, dst_id, rel, note, auto FROM links" + ("" if auto else " WHERE auto=0"))]
    node_ids = {e["src_id"] for e in edges} | {e["dst_id"] for e in edges}
    if cat:
        in_cat = {r["paper_id"] for r in con.execute("SELECT paper_id FROM paper_categories WHERE category_id=?", (cat,))}
        if all_nodes:
            node_ids |= in_cat
        edges = [e for e in edges if e["src_id"] in in_cat or e["dst_id"] in in_cat]
        node_ids = ({e["src_id"] for e in edges} | {e["dst_id"] for e in edges}) | (in_cat if all_nodes else set())
    elif all_nodes:
        node_ids = {r["id"] for r in con.execute("SELECT id FROM papers")}
    if focus:
        near = {focus} | {e["dst_id"] for e in edges if e["src_id"] == focus} | {e["src_id"] for e in edges if e["dst_id"] == focus}
        edges = [e for e in edges if e["src_id"] in near and e["dst_id"] in near]
        node_ids = near
    if not node_ids:
        return {"nodes": [], "edges": []}
    qm = ",".join("?" * len(node_ids))
    nodes = [dict(r) for r in con.execute(
        f"SELECT p.id, p.citekey, p.title, p.year, p.venue, (SELECT c.id FROM paper_categories pc JOIN categories c ON c.id=pc.category_id "
        f"WHERE pc.paper_id=p.id ORDER BY c.sort, c.id LIMIT 1) AS cat FROM papers p WHERE p.id IN ({qm})", list(node_ids))]
    colors = {r["id"]: r["color"] for r in con.execute("SELECT id, color FROM categories")}
    for n in nodes:
        if cat and n["id"] in in_cat:
            n["cat"] = cat     # 篩選某分類時，屬於該分類的論文用該分類的顏色
        n["color"] = colors.get(n["cat"])
        n["in_cat"] = (not cat) or n["id"] in in_cat
    return {"nodes": nodes, "edges": edges}


# ================================================================== 動態、匯出、備份
@app.get("/api/activity")
def activity(limit: int = 50, u=Depends(auth.require_user)):
    return [dict(r) for r in db.get().execute(
        "SELECT a.*, u.display_name AS who, p.title FROM activity a LEFT JOIN users u ON u.id=a.user_id "
        "LEFT JOIN papers p ON p.id=a.paper_id ORDER BY a.id DESC LIMIT ?", (min(limit, 500),))]


@app.get("/api/export/bibtex", response_class=PlainTextResponse)
def export_bibtex(scope: str = "all", cat: int | None = None, tag: str | None = None, u=Depends(auth.require_user)):
    res = list_papers(scope=scope, cat=cat, tag=tag, limit=100000, u=u)
    con = db.get()
    ids = [x["id"] for x in res["items"]]
    return "\n\n".join(library.bibtex(paper_or_404(con, i)) for i in ids) + "\n"


@app.get("/api/export/csv")
def export_csv(u=Depends(auth.require_user)):
    con = db.get()
    cats = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM categories")}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["citekey", "title", "authors", "year", "venue", "doi", "arxiv", "kind", "status", "categories", "tags", "files", "keyinfo"])
    for p in con.execute("SELECT * FROM papers ORDER BY citekey"):
        cs = [cats[r["category_id"]] for r in con.execute("SELECT category_id FROM paper_categories WHERE paper_id=?", (p["id"],))]
        ts = [r["name"] for r in con.execute("SELECT t.name FROM paper_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.paper_id=?", (p["id"],))]
        fs = [r["filename"] for r in con.execute("SELECT filename FROM files WHERE paper_id=?", (p["id"],))]
        w.writerow([p["citekey"], p["title"], "; ".join(json.loads(p["authors"])), p["year"] or "", p["venue"], p["doi"], p["arxiv"],
                    p["kind"], db.get_status(con, u["id"], p["id"]) or "", "; ".join(cs), "; ".join(ts), "; ".join(fs), p["keyinfo"]])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=papers.csv"})


@app.post("/api/admin/backup")
def backup(u=Depends(auth.require_admin)):
    dest = config.BACKUP_DIR / f"library-{datetime.now():%Y%m%d-%H%M%S}.db"
    src = db.connect()
    out = sqlite3.connect(dest)
    src.backup(out)
    out.close(); src.close()
    return {"ok": True, "file": dest.name}


@app.get("/api/admin/backup/{name}")
def get_backup(name: str, u=Depends(auth.require_admin)):
    p = (config.BACKUP_DIR / Path(name).name)
    if not p.exists():
        raise HTTPException(404, "找不到備份")
    return FileResponse(p, filename=p.name)


@app.get("/api/admin/status")
def admin_status(u=Depends(auth.require_admin)):
    return monitor.status()


@app.get("/api/admin/traffic")
def admin_traffic(minutes: int = 60, u=Depends(auth.require_admin)):
    return monitor.traffic(max(5, min(minutes, 1440)))


@app.get("/api/admin/logs")
def admin_logs(level: str = "", limit: int = 200, u=Depends(auth.require_admin)):
    rows = [x for x in monitor.LOGS if not level or x["level"] == level.upper()]
    return {"items": rows[-max(1, min(limit, 300)):][::-1]}


# 控制台可以直接啟動的背景工作
RUNNABLE = {"feeds": "檢查新論文", "refs": "重新分析引用", "scan_text": "檢查 PDF 文字層", "versions": "檢查預印本是否已發表",
            "similar_index": "重建相似度索引", "digest": "寄送每週摘要"}


@app.post("/api/admin/jobs/run")
def admin_run_job(b: dict[str, Any], u=Depends(auth.require_admin)):
    kind = str(b.get("kind") or "")
    if kind not in RUNNABLE:
        raise HTTPException(400, f"不能從這裡執行：{kind}")
    return {"ok": True, "job": jobs.enqueue(kind, "", u["id"]), "label": RUNNABLE[kind]}


@app.post("/api/admin/reindex")
def reindex(u=Depends(auth.require_admin)):
    con = db.get()
    n = 0
    for p in con.execute("SELECT id FROM papers").fetchall():
        fid = library.main_file_id(con, p["id"])
        body = ""
        if fid:
            f = file_or_404(con, fid)
            try:
                body = pdftools.full_text(data_path(f["path"]))
            except Exception:  # noqa: BLE001
                body = ""
        with db.tx() as c2:
            db.fts_update(c2, p["id"], body)
        n += 1
    jobs.enqueue("refs", "", u["id"])
    return {"ok": True, "papers": n}


# ================================================================== 網站設定與翻譯
@app.get("/api/admin/settings")
def get_settings(u=Depends(auth.require_admin)):
    con = db.get()
    from . import feeds as feeds_mod
    return {**settings.public(con), "tr_providers": translate.PROVIDERS, "targets": translate.TARGETS,
            "tr_default_model": translate.DEFAULT_MODEL, "ai_providers": ai.PROVIDERS, "ai_default_model": ai.DEFAULT_MODEL,
            "ocr": ocr.available(), "rss_presets": feeds_mod.RSS_PRESETS}


@app.patch("/api/admin/settings")
def set_settings(b: dict[str, Any], u=Depends(auth.require_admin)):
    if "tr_provider" in b and b["tr_provider"] not in translate.PROVIDERS:
        raise HTTPException(400, "不支援的翻譯服務")
    if "ai_provider" in b and b["ai_provider"] not in ai.PROVIDERS:
        raise HTTPException(400, "不支援的 AI 服務")
    if "site_url" in b and b["site_url"] and not re.match(r"^https?://", str(b["site_url"])):
        raise HTTPException(400, "網站網址要以 http:// 或 https:// 開頭")
    with db.tx() as con:
        if "site_name" in b:
            b["site_name"] = str(b["site_name"] or "").strip()[:40] or config.SITE_NAME
        settings.update(con, b)
        db.log(con, u["id"], "settings", None, ",".join(sorted(k for k in b if k not in settings.SECRETS)))
    return get_settings(u)


class TrIn(BaseModel):
    text: str


@app.post("/api/translate")
def do_translate(b: TrIn, u=Depends(auth.perm("translate"))):
    try:
        return translate.translate(db.get(), b.text)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    except (KeyError, IndexError, ValueError) as e:
        raise HTTPException(502, f"翻譯服務回傳格式不符：{e}")


@app.post("/api/admin/settings/test")
def test_translate(u=Depends(auth.require_admin)):
    if not translate.settings(db.get())["tr_provider"]:
        raise HTTPException(400, "請先選擇翻譯服務")
    try:
        return translate.translate(db.get(), "Magnons strongly couple to microwave photons in a cavity, "
                                              "forming cavity magnon polaritons near the exceptional point.",
                                   use_cache=False)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    except (KeyError, IndexError, ValueError) as e:
        raise HTTPException(502, f"翻譯服務回傳格式不符：{e}")


from .api_more import router as _more  # noqa: E402  其餘功能（追蹤、AI、組會、通知、OCR、匯出、Zotero）
app.include_router(_more)
from .api_ext import router as _ext  # noqa: E402  參數、圖表剪貼簿、語意搜尋、預印本、入門路徑、投影片、看板
app.include_router(_ext)
app.include_router(accounts.router)   # 註冊申請與站長

# ================================================================== 前端
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/manifest.webmanifest")
def manifest():
    """安裝成 App（Android、iOS、電腦）用的描述檔。"""
    name = site_name()
    return JSONResponse({
        "id": "/", "name": name, "short_name": name.replace("論文庫", "")[:12] or name[:12], "start_url": "/#/", "scope": "/",
        "display": "standalone", "display_override": ["standalone", "minimal-ui"], "orientation": "any",
        "background_color": "#f6f5f1", "theme_color": "#1f3a4d", "lang": "zh-Hant", "dir": "ltr",
        "description": "實驗室論文歸檔、閱讀、標註與討論",
        "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                  {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                  {"src": "/static/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
        "shortcuts": [{"name": "我的待讀", "url": "/#/todo", "icons": [{"src": "/static/icon-192.png", "sizes": "192x192"}]},
                      {"name": "新論文追蹤", "url": "/#/feeds", "icons": [{"src": "/static/icon-192.png", "sizes": "192x192"}]},
                      {"name": "組會", "url": "/#/meetings", "icons": [{"src": "/static/icon-192.png", "sizes": "192x192"}]}]},
        media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    # Service worker 必須從網站根目錄提供，才能管理整個網站（離線快取）
    return FileResponse(STATIC / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
