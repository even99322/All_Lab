"""帳號、登入與權限：PBKDF2 密碼雜湊 + HttpOnly session cookie + 逐項權限。"""
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request

from . import config, db

COOKIE = "plsession"
ITER = 240_000

# 權限項目（順序即管理頁顯示順序）
PERMS = {
    "upload": "上傳論文與附件",
    "edit_meta": "編輯書目、重點欄、閱讀狀態",
    "categorize": "變更分類（含歸檔）",
    "tag": "變更標籤",
    "annotate": "劃線與筆記",
    "link": "建立與刪除關聯",
    "translate": "使用翻譯",
    "ai": "使用 AI（預填重點欄、問論文庫）",
    "meeting": "指派論文、安排組會與閱讀清單",
    "delete": "刪除論文與檔案",
    "manage": "管理分類與標籤清單",
    "own_only": "只能修改自己上傳的論文（限制）",
}
ROLE_DEFAULTS = {
    "admin": {k: k != "own_only" for k in PERMS},
    "member": {"upload": True, "edit_meta": True, "categorize": True, "tag": True, "annotate": True, "link": True,
               "translate": True, "ai": True, "meeting": True, "delete": False, "manage": False, "own_only": False},
    "viewer": {"upload": False, "edit_meta": False, "categorize": False, "tag": False, "annotate": False, "link": False,
               "translate": True, "ai": False, "meeting": False, "delete": False, "manage": False, "own_only": False},
}
ROLE_NAMES = {"admin": "管理員", "member": "成員", "viewer": "唯讀訪客"}


def perms_for(role: str, stored: str | None) -> dict:
    base = dict(ROLE_DEFAULTS.get(role, ROLE_DEFAULTS["viewer"]))
    if role == "admin":
        return base  # 管理員永遠擁有全部權限
    try:
        base.update({k: bool(v) for k, v in json.loads(stored or "{}").items() if k in PERMS})
    except ValueError:
        pass
    return base


def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITER)
    return f"pbkdf2${ITER}${salt.hex()}${h.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, it, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(calc.hex(), h)
    except ValueError:
        return False


def new_session(con, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    exp = datetime.now(timezone.utc) + timedelta(days=config.SESSION_DAYS)
    con.execute("INSERT INTO sessions(token, user_id, created_at, expires_at) VALUES(?,?,?,?)",
                (token, user_id, db.now(), exp.isoformat(timespec="seconds")))
    con.execute("DELETE FROM sessions WHERE expires_at < ?", (db.now(),))
    return token


def current_user(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    row = db.get().execute(
        "SELECT u.id, u.username, u.display_name, u.role, u.perms, u.owner, u.email FROM sessions s JOIN users u ON u.id=s.user_id "
        "WHERE s.token=? AND s.expires_at > ? AND u.disabled=0", (token, db.now())).fetchone()
    if not row:
        return None
    u = dict(row)
    u["owner"] = bool(u.get("owner"))
    u["has_email"] = bool(u.pop("email", ""))
    u["perms"] = perms_for(u["role"], u.pop("perms"))
    return u


def require_user(request: Request):
    u = current_user(request)
    if u is None:
        raise HTTPException(401, "請先登入")
    # 簡單的 CSRF 防護：會改資料的請求必須帶自訂標頭，跨站表單無法偽造
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-pl") != "1":
        raise HTTPException(403, "缺少 X-PL 標頭")
    return u


def require_admin(u=Depends(require_user)):
    if u["role"] != "admin":
        raise HTTPException(403, "需要管理員權限")
    return u


def need(u: dict, perm: str, paper: dict | None = None) -> None:
    """檢查權限；paper 有給時，套用「只能修改自己上傳的論文」限制。"""
    if not u["perms"].get(perm):
        raise HTTPException(403, f"你的帳號沒有「{PERMS[perm]}」權限，請洽管理員")
    if paper is not None and u["perms"].get("own_only") and paper.get("added_by") != u["id"]:
        raise HTTPException(403, "你的帳號只能修改自己上傳的論文")


def perm(name: str):
    def dep(u=Depends(require_user)):
        need(u, name)
        return u
    return dep
