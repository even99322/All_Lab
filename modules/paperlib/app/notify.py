"""站內通知（回覆、@提及、被排報告）與每週摘要（Email、Discord／Slack／Teams Webhook）。"""
import json
import smtplib
import ssl
import urllib.request
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from html import escape

from . import db, settings
from .jobs import handler


def users(con) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT id, username, display_name, email, digest FROM users WHERE disabled=0")]


def mentioned(con, text: str) -> set[int]:
    """文字中的 @顯示名稱 或 @帳號。"""
    out = set()
    if "@" not in (text or ""):
        return out
    for u in users(con):
        for n in {u["display_name"], u["username"]}:
            if n and f"@{n}" in text:
                out.add(u["id"])
    return out


def push(con, user_ids, kind: str, actor_id, text: str, paper_id=None, ann_id=None, meeting_id=None) -> None:
    for uid in set(user_ids) - {actor_id, None}:
        con.execute("INSERT INTO notifications(user_id, kind, actor_id, paper_id, ann_id, meeting_id, text, created_at) "
                    "VALUES(?,?,?,?,?,?,?,?)", (uid, kind, actor_id, paper_id, ann_id, meeting_id, text[:300], db.now()))


def listing(con, uid: int, limit: int = 40) -> dict:
    rows = [dict(r) for r in con.execute(
        "SELECT n.*, u.display_name AS actor, p.title, p.citekey FROM notifications n LEFT JOIN users u ON u.id=n.actor_id "
        "LEFT JOIN papers p ON p.id=n.paper_id WHERE n.user_id=? ORDER BY n.id DESC LIMIT ?", (uid, limit))]
    unread = con.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read=0", (uid,)).fetchone()[0]
    return {"items": rows, "unread": unread}


# ------------------------------------------------------------------ 每週摘要
def weekly(con, days: int = 7) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    today = datetime.now().strftime("%Y-%m-%d")
    until = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
    new_papers = [dict(r) for r in con.execute(
        "SELECT p.id, p.title, p.citekey, u.display_name AS who FROM papers p LEFT JOIN users u ON u.id=p.added_by "
        "WHERE p.added_at >= ? ORDER BY p.id DESC", (since,))]
    ann = con.execute("SELECT u.display_name AS who, COUNT(*) n FROM annotations a LEFT JOIN users u ON u.id=a.author_id "
                      "WHERE a.private=0 AND a.created_at >= ? GROUP BY a.author_id ORDER BY n DESC", (since,)).fetchall()
    meets = []
    for m in con.execute("SELECT * FROM meetings WHERE date >= ? AND date <= ? ORDER BY date", (today, until)):
        items = [dict(r) for r in con.execute(
            "SELECT mi.presenter_id, u.display_name AS presenter, p.id AS paper_id, p.title FROM meeting_items mi "
            "LEFT JOIN users u ON u.id=mi.presenter_id LEFT JOIN papers p ON p.id=mi.paper_id WHERE mi.meeting_id=? ORDER BY mi.sort, mi.id",
            (m["id"],))]
        meets.append({**dict(m), "items": items})
    feed_new = con.execute("SELECT COUNT(*) FROM feed_items WHERE status='new' AND found_at >= ?", (since,)).fetchone()[0]
    return {"since": since[:10], "new_papers": new_papers, "annotations": [dict(r) for r in ann], "meetings": meets,
            "feed_new": feed_new}


def render_text(con, w: dict, for_user: dict | None = None) -> str:
    s = settings.get(con)
    L = [f"【{s['site_name']}】每週摘要（{w['since']} 起）", ""]
    if for_user:
        mine = [(m, it) for m in w["meetings"] for it in m["items"] if it["presenter_id"] == for_user["id"]]
        unread = con.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read=0", (for_user["id"],)).fetchone()[0]
        if mine:
            L.append("▶ 你要報告：")
            L += [f"  {m['date']} {m['title']}：{it['title'] or '（未指定論文）'}" for m, it in mine]
        if unread:
            L.append(f"▶ 你有 {unread} 則未讀通知")
        todo = con.execute("SELECT p.title FROM paper_assign pa JOIN papers p ON p.id=pa.paper_id LEFT JOIN user_paper up "
                           "ON up.user_id=pa.user_id AND up.paper_id=pa.paper_id WHERE pa.user_id=? AND COALESCE(up.status,'')!='已閱讀'",
                           (for_user["id"],)).fetchall()
        if todo:
            L.append(f"▶ 指派給你、還沒讀完的論文（{len(todo)}）：")
            L += [f"  - {t['title']}" for t in todo[:10]]
        req = con.execute("SELECT COUNT(*) FROM papers p WHERE p.required=1 AND NOT EXISTS(SELECT 1 FROM user_paper up "
                          "WHERE up.paper_id=p.id AND up.user_id=? AND up.status='已閱讀')", (for_user["id"],)).fetchone()[0]
        if req:
            L.append(f"▶ 全站必讀還有 {req} 篇沒讀")
        if mine or unread or todo or req:
            L.append("")
    if w["meetings"]:
        L.append("▶ 接下來兩週的組會：")
        for m in w["meetings"]:
            L.append(f"  {m['date']} {m['title']}")
            L += [f"    - {it['presenter'] or '未定'}：{it['title'] or '（未指定論文）'}" for it in m["items"]]
        L.append("")
    L.append(f"▶ 本週新增論文 {len(w['new_papers'])} 篇")
    for p in w["new_papers"][:15]:
        url = settings.link(f"#/p/{p['id']}", con)
        L.append(f"  - {p['title']}（{p['who'] or '匯入'}）{(' ' + url) if url else ''}")
    if len(w["new_papers"]) > 15:
        L.append(f"  …等 {len(w['new_papers'])} 篇")
    if w["annotations"]:
        L.append("▶ 本週標註：" + "、".join(f"{a['who']} {a['n']} 則" for a in w["annotations"]))
    if w["feed_new"]:
        L.append(f"▶ 新論文追蹤找到 {w['feed_new']} 篇待看" + (f"：{settings.link('#/feeds', con)}" if s["site_url"] else ""))
    return "\n".join(L).strip()


def send_mail(con, to: str, subject: str, text: str) -> None:
    s = settings.get(con)
    if not s["smtp_host"] or not to:
        raise RuntimeError("沒有設定 SMTP 或收件人")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((s["site_name"], s["smtp_from"] or s["smtp_user"]))
    msg["To"] = to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(f"<pre style='font-family:inherit;white-space:pre-wrap'>{escape(text)}</pre>", "html", "utf-8"))
    port = int(s["smtp_port"] or 587)
    if s["smtp_security"] == "ssl":
        srv = smtplib.SMTP_SSL(s["smtp_host"], port, timeout=30, context=ssl.create_default_context())
    else:
        srv = smtplib.SMTP(s["smtp_host"], port, timeout=30)
        if s["smtp_security"] == "starttls":
            srv.starttls(context=ssl.create_default_context())
    try:
        if s["smtp_user"]:
            srv.login(s["smtp_user"], s["smtp_pass"])
        srv.sendmail(s["smtp_from"] or s["smtp_user"], [to], msg.as_string())
    finally:
        srv.quit()


def send_webhook(con, text: str) -> None:
    url = settings.get(con)["webhook_url"]
    if not url:
        raise RuntimeError("沒有設定 Webhook 網址")
    # Slack／Teams 讀 text，Discord 讀 content（上限 2000 字）
    body = json.dumps({"text": text, "content": text[:1990]}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json", "User-Agent": "PaperLib/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


def send_digest(con, only_user: int | None = None) -> str:
    s = settings.get(con)
    w = weekly(con)
    sent, errs = 0, []
    if s["webhook_url"] and only_user is None:
        try:
            send_webhook(con, render_text(con, w))
            sent += 1
        except Exception as e:  # noqa: BLE001
            errs.append(f"Webhook：{e}")
    if s["smtp_host"]:
        for u in users(con):
            if not u["email"] or (only_user and u["id"] != only_user) or (not only_user and not u["digest"]):
                continue
            try:
                send_mail(con, u["email"], f"{s['site_name']} 每週摘要", render_text(con, w, u))
                sent += 1
            except Exception as e:  # noqa: BLE001
                errs.append(f"{u['display_name']}：{e}")
    if not s["smtp_host"] and not s["webhook_url"]:
        raise RuntimeError("沒有設定 Email（SMTP）或 Webhook")
    return f"寄出 {sent} 份" + (f"；失敗：{'；'.join(errs)}" if errs else "")


@handler("digest")
def _job(con, arg, progress):
    return send_digest(con, int(arg) if arg else None)
