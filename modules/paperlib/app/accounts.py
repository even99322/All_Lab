"""註冊與站長：
- 沒有帳號的人可以在登入頁申請（名稱、帳號、密碼、Email），送給站長審核；通過後才建立帳號。
- 站長：網站的擁有者，擁有管理員的全部權限，另外負責審核註冊、指定或取消其他站長。
  還沒有站長時，管理員可以直接指定；有站長之後，只有站長能變更站長身分，其他管理員也不能修改站長的帳號。
"""
import json
import re
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from . import auth, db, jobs, notify, security, settings

router = APIRouter(prefix="/api")

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_reg_lock = threading.Lock()
_reg_hits: dict[str, list[float]] = {}


def has_owner(con) -> bool:
    return bool(con.execute("SELECT 1 FROM users WHERE owner=1 AND disabled=0").fetchone())


def reviewers(con) -> list[dict]:
    """審核註冊的人：有站長就是站長；還沒有站長時由管理員代為審核。"""
    rows = con.execute("SELECT id, display_name, email FROM users WHERE owner=1 AND disabled=0").fetchall()
    if not rows:
        rows = con.execute("SELECT id, display_name, email FROM users WHERE role='admin' AND disabled=0").fetchall()
    return [dict(r) for r in rows]


def can_review(con, u) -> bool:
    return bool(u.get("owner")) or (u["role"] == "admin" and not has_owner(con))


def mail_later(to: str, subject: str, text: str) -> None:
    """寄信放到背景工作：SMTP 慢或沒設定都不會卡住網頁。"""
    if to:
        jobs.enqueue("mail", {"to": to, "subject": subject, "text": text}, dedupe=False)


@jobs.handler("mail")
def _mail_job(con, arg, progress):
    a = json.loads(arg or "{}")
    if not settings.get(con)["smtp_host"]:
        return "沒有設定 SMTP，略過寄信"
    notify.send_mail(con, a["to"], a["subject"], a["text"])
    return f"已寄給 {a['to']}"


jobs.LABELS["mail"] = "寄送通知信"


# ------------------------------------------------------------------ 註冊申請
def _throttle(ip: str) -> None:
    now = time.time()
    with _reg_lock:
        hits = [t for t in _reg_hits.get(ip, []) if now - t < 3600]
        if len(hits) >= 5:
            raise HTTPException(429, "這個網路一小時內送出太多申請，請稍後再試")
        hits.append(now)
        _reg_hits[ip] = hits


@router.post("/auth/register")
def register(b: dict[str, Any], request: Request):
    con = db.get()
    if settings.get(con).get("register_enabled", "1") != "1":
        raise HTTPException(403, "目前不開放註冊，請洽站長")
    if str(b.get("website") or "").strip():          # 防機器人的隱藏欄位
        return {"ok": True}
    username = str(b.get("username") or "").strip()
    name = str(b.get("display_name") or "").strip()[:40]
    email = str(b.get("email") or "").strip()[:200]
    pw = str(b.get("password") or "")
    note = str(b.get("note") or "").strip()[:500]
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "帳號請用 3–32 個英文字母、數字或 . _ -")
    if not name:
        raise HTTPException(400, "請填寫用戶名稱")
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Email 格式不對")
    if len(pw) < 8:
        raise HTTPException(400, "密碼至少 8 個字元")
    ip = security.client_ip(request)
    _throttle(ip)
    with db.tx() as con:
        if con.execute("SELECT 1 FROM users WHERE lower(username)=lower(?)", (username,)).fetchone() or \
           con.execute("SELECT 1 FROM registrations WHERE lower(username)=lower(?)", (username,)).fetchone():
            raise HTTPException(409, "這個帳號已經有人使用或正在審核，請換一個")
        if con.execute("SELECT COUNT(*) FROM registrations").fetchone()[0] >= 200:
            raise HTTPException(429, "待審核的申請太多，請稍後再試或直接聯絡站長")
        rid = con.execute("INSERT INTO registrations(username, display_name, email, pw_hash, note, ip, created_at) VALUES(?,?,?,?,?,?,?)",
                          (username, name, email, auth.hash_pw(pw), note, ip, db.now())).lastrowid
        revs = reviewers(con)
        text = f"新的註冊申請：{name}（{username}，{email}）{('：' + note[:80]) if note else ''}"
        notify.push(con, [r["id"] for r in revs], "register", None, text)
    link = settings.link("#/admin/users")
    for r in revs:
        mail_later(r["email"], f"[{settings.get()['site_name']}] 新的註冊申請：{name}",
                   f"{r['display_name']} 你好：\n\n{name}（帳號 {username}，Email {email}）申請使用論文庫。\n"
                   f"{('申請說明：' + note + chr(10)) if note else ''}\n請到「管理 → 使用者與權限」審核{('：' + link) if link else '。'}\n")
    return {"ok": True, "id": rid}


def _reviewer(u=Depends(auth.require_user)):
    if not can_review(db.get(), u):
        raise HTTPException(403, "只有站長可以審核註冊" if has_owner(db.get()) else "需要管理員權限")
    return u


@router.get("/registrations")
def list_regs(u=Depends(auth.require_user)):
    con = db.get()
    if not can_review(con, u):
        return {"items": [], "can_review": False}
    rows = [dict(r) for r in con.execute("SELECT id, username, display_name, email, note, ip, created_at FROM registrations ORDER BY id")]
    return {"items": rows, "can_review": True}


@router.post("/registrations/{rid}/approve")
def approve(rid: int, b: dict[str, Any], u=Depends(_reviewer)):
    role = b.get("role") if b.get("role") in ("member", "viewer") else "member"
    with db.tx() as con:
        r = con.execute("SELECT * FROM registrations WHERE id=?", (rid,)).fetchone()
        if r is None:
            raise HTTPException(404, "找不到這筆申請（可能已經處理過）")
        if con.execute("SELECT 1 FROM users WHERE lower(username)=lower(?)", (r["username"],)).fetchone():
            raise HTTPException(409, "已經有同名帳號")
        uid = con.execute("INSERT INTO users(username, display_name, pw_hash, role, perms, email, digest, created_at) VALUES(?,?,?,?,?,?,?,?)",
                          (r["username"], r["display_name"], r["pw_hash"], role, "{}", r["email"], 1, db.now())).lastrowid
        con.execute("DELETE FROM registrations WHERE id=?", (rid,))
        db.log(con, u["id"], "approve", None, f"{r['display_name']}（{r['username']}）→ {auth.ROLE_NAMES[role]}")
    link = settings.link("")
    mail_later(r["email"], f"[{settings.get()['site_name']}] 帳號已開通",
               f"{r['display_name']} 你好：\n\n你申請的帳號「{r['username']}」已經由站長審核通過，角色是「{auth.ROLE_NAMES[role]}」。\n"
               f"{('現在可以登入：' + link) if link else '現在可以登入了。'}\n")
    return {"ok": True, "user_id": uid}


@router.post("/registrations/{rid}/reject")
def reject(rid: int, b: dict[str, Any], u=Depends(_reviewer)):
    reason = str(b.get("reason") or "").strip()[:300]
    with db.tx() as con:
        r = con.execute("SELECT * FROM registrations WHERE id=?", (rid,)).fetchone()
        if r is None:
            raise HTTPException(404, "找不到這筆申請")
        con.execute("DELETE FROM registrations WHERE id=?", (rid,))
        db.log(con, u["id"], "reject", None, f"{r['display_name']}（{r['username']}）")
    if b.get("notify"):
        mail_later(r["email"], f"[{settings.get()['site_name']}] 註冊申請未通過",
                   f"{r['display_name']} 你好：\n\n你申請的帳號「{r['username']}」這次沒有通過審核。{('原因：' + reason) if reason else ''}\n如有疑問請直接聯絡實驗室。\n")
    return {"ok": True}


@router.get("/registrations/settings")
def reg_settings(u=Depends(auth.require_user)):
    con = db.get()
    return {"enabled": settings.get(con).get("register_enabled", "1") == "1", "has_owner": has_owner(con), "can_review": can_review(con, u)}


@router.put("/registrations/settings")
def set_reg_settings(b: dict[str, Any], u=Depends(_reviewer)):
    db.meta_set(db.get(), "register_enabled", "1" if b.get("enabled") else "0")
    return {"ok": True}


# ------------------------------------------------------------------ 站長
def check_owner_change(con, actor: dict, target_id: int, make_owner: bool) -> None:
    """還沒有站長：管理員可以指定。已經有站長：只有站長可以指定或取消。最後一位站長不能取消自己（先指定下一位）。"""
    if has_owner(con):
        if not actor.get("owner"):
            raise HTTPException(403, "已經有站長了，只有站長可以變更站長身分")
    elif actor["role"] != "admin":
        raise HTTPException(403, "需要管理員權限")
    t = con.execute("SELECT id, owner, disabled FROM users WHERE id=?", (target_id,)).fetchone()
    if t is None:
        raise HTTPException(404, "找不到帳號")
    if make_owner and t["disabled"]:
        raise HTTPException(400, "停用中的帳號不能設為站長")
    if not make_owner and t["owner"]:
        n = con.execute("SELECT COUNT(*) FROM users WHERE owner=1 AND disabled=0").fetchone()[0]
        if n <= 1:
            raise HTTPException(400, "這是最後一位站長。請先把另一位成員設為站長，再取消這個身分")


def protect_owner(con, actor: dict, target_id: int) -> None:
    """站長的帳號只有站長能修改（角色、權限、密碼、停用、2FA）。"""
    t = con.execute("SELECT owner FROM users WHERE id=?", (target_id,)).fetchone()
    if t and t["owner"] and not actor.get("owner"):
        raise HTTPException(403, "這是站長的帳號，只有站長可以修改")


# ------------------------------------------------------------------ 提醒填 Email
@router.get("/admin/no-email")
def no_email(u=Depends(auth.require_admin)):
    rows = db.get().execute("SELECT id, username, display_name FROM users WHERE disabled=0 AND trim(email)='' ORDER BY id").fetchall()
    return {"items": [dict(r) for r in rows]}


@router.post("/admin/remind-email")
def remind_email(u=Depends(auth.require_admin)):
    """對還沒填 Email 的帳號送站內通知；已經登入的人下次操作網站時會跳出填寫視窗。"""
    with db.tx() as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM users WHERE disabled=0 AND trim(email)=''")]
        fresh = [i for i in ids if not con.execute("SELECT 1 FROM notifications WHERE user_id=? AND kind='email' AND read=0", (i,)).fetchone()]
        notify.push(con, fresh, "email", u["id"], "請填寫你的 Email，才收得到通知信與每週摘要（點這則通知就能填）")
    return {"ok": True, "count": len(ids), "notified": len(fresh)}
