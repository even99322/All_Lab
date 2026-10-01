"""v1.2 新功能的 API：新論文追蹤、AI、組會與閱讀清單、回覆與通知、OCR、匯出、Zotero、背景工作。"""
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from . import ai, auth, db, export, feeds, jobs, notify, ocr, refs, settings, zotero
from . import main as M

router = APIRouter(prefix="/api")


def _err(e: Exception, code=502):
    raise HTTPException(code, str(e))


# ================================================================== 新論文追蹤
@router.get("/feeds")
def list_feeds(u=Depends(auth.require_user)):
    con = db.get()
    rows = [dict(r) for r in con.execute(
        "SELECT f.*, (SELECT COUNT(*) FROM feed_items i WHERE i.feed_id=f.id AND i.status='new') AS n_new FROM feeds f ORDER BY f.id")]
    for r in rows:
        r["tags"] = json.loads(r["tags"] or "[]")
        r["config"] = json.loads(r.get("config") or "{}")
    return {"feeds": rows, "presets": feeds.RSS_PRESETS,
            "last_job": next(iter(jobs.recent(con, 1, "feeds")), None)}


class FeedIn(BaseModel):
    name: str | None = None
    kind: str | None = None
    keywords: str | None = None
    categories: str | None = None
    url: str | None = None
    tags: list[str] | None = None
    enabled: bool | None = None
    config: dict | None = None


@router.post("/feeds")
def add_feed(b: FeedIn, u=Depends(auth.perm("manage"))):
    if b.kind not in ("arxiv", "rss", "journal"):
        raise HTTPException(400, "類型要是 arxiv、rss 或 journal")
    if b.kind == "rss" and not (b.url or "").startswith("http"):
        raise HTTPException(400, "請填 RSS 網址")
    cur = db.get().execute("INSERT INTO feeds(name, kind, keywords, categories, url, tags, config, created_by, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           ((b.name or "").strip() or {"arxiv": "arXiv", "rss": "RSS", "journal": "期刊搜尋"}[b.kind], b.kind, b.keywords or "",
                            b.categories or "", (b.url or "").strip(), json.dumps(b.tags or [], ensure_ascii=False),
                            json.dumps(b.config or {}, ensure_ascii=False), u["id"], db.now()))
    return {"id": cur.lastrowid}


@router.patch("/feeds/{fid}")
def edit_feed(fid: int, b: FeedIn, u=Depends(auth.perm("manage"))):
    con = db.get()
    for k in ("name", "keywords", "categories", "url"):
        v = getattr(b, k)
        if v is not None:
            con.execute(f"UPDATE feeds SET {k}=? WHERE id=?", (v.strip(), fid))
    if b.tags is not None:
        con.execute("UPDATE feeds SET tags=? WHERE id=?", (json.dumps(b.tags, ensure_ascii=False), fid))
    if b.enabled is not None:
        con.execute("UPDATE feeds SET enabled=? WHERE id=?", (int(b.enabled), fid))
    if b.config is not None:
        con.execute("UPDATE feeds SET config=? WHERE id=?", (json.dumps(b.config, ensure_ascii=False), fid))
    return {"ok": True}


@router.delete("/feeds/{fid}")
def del_feed(fid: int, u=Depends(auth.perm("manage"))):
    db.get().execute("DELETE FROM feeds WHERE id=?", (fid,))
    return {"ok": True}


@router.post("/feeds/check")
def check_feeds(b: dict[str, Any] | None = None, u=Depends(auth.require_user)):
    return {"job": jobs.enqueue("feeds", str((b or {}).get("feed_id") or ""), u["id"])}


@router.get("/feed-items")
def feed_items(status: str = "new", feed: int | None = None, limit: int = 100, offset: int = 0, u=Depends(auth.require_user)):
    con = db.get()
    where, args = ["fi.status=?"], [status]
    if feed:
        where.append("fi.feed_id=?"); args.append(feed)
    total = con.execute(f"SELECT COUNT(*) FROM feed_items fi WHERE {' AND '.join(where)}", args).fetchone()[0]
    rows = [dict(r) | {"authors": json.loads(r["authors"] or "[]")} for r in con.execute(
        f"SELECT fi.*, f.name AS feed_name FROM feed_items fi LEFT JOIN feeds f ON f.id=fi.feed_id WHERE {' AND '.join(where)} "
        f"ORDER BY fi.published DESC, fi.id DESC LIMIT ? OFFSET ?", args + [min(limit, 300), offset])]
    counts = {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) n FROM feed_items GROUP BY status")}
    return {"total": total, "items": rows, "counts": counts}


class ItemsIn(BaseModel):
    ids: list[int]
    tags: list[str] = []
    categories: list[int] = []


@router.post("/feed-items/add")
def feed_add(b: ItemsIn, u=Depends(auth.perm("upload"))):
    con = db.get()
    out = []
    for i in b.ids[:30]:
        try:
            out.append({"id": i, **feeds.add_to_library(con, i, u["id"], b.tags if u["perms"]["tag"] else [],
                                                        b.categories if u["perms"]["categorize"] else [])})
        except Exception as e:  # noqa: BLE001
            out.append({"id": i, "error": str(e)})
    fids = [M.library.main_file_id(con, r["paper_id"]) for r in out if r.get("paper_id")]
    M._after_files([f for f in fids if f], u)
    return out


@router.post("/feed-items/dismiss")
def feed_dismiss(b: ItemsIn, u=Depends(auth.require_user)):
    con = db.get()
    for i in b.ids:
        con.execute("UPDATE feed_items SET status='dismissed' WHERE id=? AND status='new'", (i,))
    return {"ok": True}


@router.post("/feed-items/restore")
def feed_restore(b: ItemsIn, u=Depends(auth.require_user)):
    con = db.get()
    for i in b.ids:
        con.execute("UPDATE feed_items SET status='new' WHERE id=? AND status='dismissed'", (i,))
    return {"ok": True}


# ================================================================== 分類資料夾、置頂
class FolderIn(BaseModel):
    name: str | None = None
    sort: int | None = None


@router.post("/categories/{cid}/folders")
def add_folder(cid: int, b: FolderIn, u=Depends(auth.perm("categorize"))):
    con = db.get()
    name = (b.name or "").strip()[:60]
    if not name:
        raise HTTPException(400, "請填資料夾名稱")
    if not con.execute("SELECT 1 FROM categories WHERE id=?", (cid,)).fetchone():
        raise HTTPException(404, "找不到分類")
    mx = con.execute("SELECT COALESCE(MAX(sort),0)+1 FROM folders WHERE category_id=?", (cid,)).fetchone()[0]
    cur = con.execute("INSERT INTO folders(category_id, name, sort) VALUES(?,?,?)", (cid, name, mx))
    db.log(con, u["id"], "folder", None, name)
    return {"id": cur.lastrowid}


@router.patch("/folders/{fid}")
def edit_folder(fid: int, b: FolderIn, u=Depends(auth.perm("categorize"))):
    con = db.get()
    if b.name is not None and b.name.strip():
        con.execute("UPDATE folders SET name=? WHERE id=?", (b.name.strip()[:60], fid))
    if b.sort is not None:
        con.execute("UPDATE folders SET sort=? WHERE id=?", (b.sort, fid))
    return {"ok": True}


@router.post("/categories/{cid}/folders/order")
def order_folders(cid: int, b: dict[str, Any], u=Depends(auth.perm("categorize"))):
    with db.tx() as con:
        for i, fid in enumerate(b.get("ids") or []):
            con.execute("UPDATE folders SET sort=? WHERE id=? AND category_id=?", (i, int(fid), cid))
    return {"ok": True}


@router.delete("/folders/{fid}")
def del_folder(fid: int, u=Depends(auth.perm("categorize"))):
    with db.tx() as con:
        con.execute("UPDATE paper_categories SET folder_id=NULL WHERE folder_id=?", (fid,))
        con.execute("DELETE FROM folders WHERE id=?", (fid,))
    return {"ok": True}


class MoveIn(BaseModel):
    ids: list[int]
    cat: int
    folder_id: int | None = None


@router.post("/papers/move")
def move_papers(b: MoveIn, u=Depends(auth.require_user)):
    """把論文移到分類裡的某個資料夾（folder_id=None＝移出資料夾）。還不在該分類的論文會一併加入分類。"""
    with db.tx() as con:
        if b.folder_id and not con.execute("SELECT 1 FROM folders WHERE id=? AND category_id=?", (b.folder_id, b.cat)).fetchone():
            raise HTTPException(400, "資料夾不屬於這個分類")
        for pid in b.ids:
            auth.need(u, "categorize", M.paper_or_404(con, pid))
            con.execute("INSERT OR IGNORE INTO paper_categories(paper_id, category_id) VALUES(?,?)", (pid, b.cat))
            con.execute("UPDATE paper_categories SET folder_id=? WHERE paper_id=? AND category_id=?", (b.folder_id, pid, b.cat))
            con.execute("UPDATE papers SET suggested_category_id=NULL WHERE id=?", (pid,))
        db.log(con, u["id"], "move", b.ids[0] if len(b.ids) == 1 else None, f"{len(b.ids)} 篇")
    return {"ok": True}


class PinIn(BaseModel):
    pinned: bool
    cat: int | None = None


@router.post("/papers/{pid}/pin")
def pin_paper(pid: int, b: PinIn, u=Depends(auth.require_user)):
    con = db.get()
    auth.need(u, "categorize", M.paper_or_404(con, pid))
    if b.cat:
        con.execute("UPDATE paper_categories SET pinned=? WHERE paper_id=? AND category_id=?", (int(b.pinned), pid, b.cat))
    else:
        con.execute("UPDATE papers SET pinned=? WHERE id=?", (int(b.pinned), pid))
    return {"ok": True}


# ================================================================== 指派與全站必讀
class AssignIn(BaseModel):
    user_ids: list[int]
    note: str = ""
    paper_ids: list[int] | None = None


def _assign(con, u, pid: int, uids: list[int], note: str) -> int:
    t = con.execute("SELECT title FROM papers WHERE id=?", (pid,)).fetchone()
    if t is None:
        raise HTTPException(404, "找不到論文")
    new = []
    for uid in dict.fromkeys(uids):
        if not con.execute("SELECT 1 FROM users WHERE id=? AND disabled=0", (uid,)).fetchone():
            continue
        cur = con.execute("INSERT OR IGNORE INTO paper_assign(paper_id, user_id, assigned_by, note, created_at) VALUES(?,?,?,?,?)",
                          (pid, uid, u["id"], note[:500], db.now()))
        if cur.rowcount:
            new.append(uid)
            if not db.get_status(con, uid, pid):
                db.set_status(con, uid, pid, "待讀")      # 被指派的人自動列入待讀
        elif note:
            con.execute("UPDATE paper_assign SET note=? WHERE paper_id=? AND user_id=?", (note[:500], pid, uid))
    notify.push(con, new, "assign", u["id"], f"{u['display_name']} 指派你讀：{t['title']}" + (f"（{note[:80]}）" if note else ""), pid)
    if new:
        db.log(con, u["id"], "assign", pid, "、".join(r["display_name"] for r in con.execute(
            f"SELECT display_name FROM users WHERE id IN ({','.join('?' * len(new))})", new)))
    return len(new)


@router.post("/papers/{pid}/assign")
def assign_paper(pid: int, b: AssignIn, u=Depends(auth.perm("meeting"))):
    with db.tx() as con:
        n = sum(_assign(con, u, x, b.user_ids, b.note.strip()) for x in (b.paper_ids or [pid]))
    return {"ok": True, "added": n}


@router.delete("/papers/{pid}/assign/{uid}")
def unassign_paper(pid: int, uid: int, u=Depends(auth.require_user)):
    con = db.get()
    a = con.execute("SELECT assigned_by FROM paper_assign WHERE paper_id=? AND user_id=?", (pid, uid)).fetchone()
    if a is None:
        raise HTTPException(404, "找不到這筆指派")
    if not (u["perms"].get("meeting") or uid == u["id"] or a["assigned_by"] == u["id"]):
        raise HTTPException(403, "只有指派的人、被指派的人或有指派權限的人可以取消")
    con.execute("DELETE FROM paper_assign WHERE paper_id=? AND user_id=?", (pid, uid))
    return {"ok": True}


class RequiredIn(BaseModel):
    required: bool
    note: str | None = None
    paper_ids: list[int] | None = None


@router.post("/papers/{pid}/required")
def set_required(pid: int, b: RequiredIn, u=Depends(auth.perm("manage"))):
    with db.tx() as con:
        for x in (b.paper_ids or [pid]):
            M.paper_or_404(con, x)
            con.execute("UPDATE papers SET required=?, required_note=COALESCE(?, required_note) WHERE id=?", (int(b.required), b.note, x))
        db.log(con, u["id"], "required" if b.required else "unrequired", pid if not b.paper_ids else None, "")
        if b.required:
            others = [r["id"] for r in con.execute("SELECT id FROM users WHERE disabled=0")]
            t = con.execute("SELECT title FROM papers WHERE id=?", (pid,)).fetchone()
            n = len(b.paper_ids or [pid])
            notify.push(con, others, "required", u["id"], f"{u['display_name']} 把「{t['title']}」列為全站必讀" if n == 1 else
                        f"{u['display_name']} 新增了 {n} 篇全站必讀", pid if n == 1 else None)
    return {"ok": True}


@router.get("/required/progress")
def required_progress(u=Depends(auth.require_user)):
    """必讀進度：每個人讀完幾篇。管理員看得到每位成員；一般成員只看自己的。"""
    con = db.get()
    papers = [dict(r) for r in con.execute("SELECT id, citekey, title, year, required_note FROM papers WHERE required=1 ORDER BY year, id")]
    ids = [p["id"] for p in papers]
    show_all = u["role"] == "admin" or u["perms"].get("manage")
    users = [dict(r) for r in con.execute("SELECT id, display_name FROM users WHERE disabled=0" + ("" if show_all else " AND id=?") + " ORDER BY id",
                                          () if show_all else (u["id"],))]
    for x in users:
        done = {r["paper_id"] for r in con.execute(
            f"SELECT paper_id FROM user_paper WHERE user_id=? AND status='已閱讀' AND paper_id IN ({','.join('?' * len(ids)) or 'NULL'})", [x["id"]] + ids)}
        x["done"] = sorted(done)
        x["n_done"] = len(done)
    return {"papers": papers, "users": users, "all": bool(show_all)}


# ================================================================== AI
@router.post("/papers/{pid}/ai/keyinfo")
def ai_keyinfo(pid: int, u=Depends(auth.perm("ai"))):
    M.can_edit(u, pid, "edit_meta")
    try:
        return {"suggest": ai.suggest_keyinfo(db.get(), pid)}
    except (RuntimeError, ValueError, KeyError, IndexError) as e:
        _err(e)


class AskIn(BaseModel):
    question: str
    paper_id: int | None = None
    cat: int | None = None


@router.post("/ai/ask")
def ai_ask(b: AskIn, u=Depends(auth.perm("ai"))):
    try:
        return ai.ask(db.get(), u["id"], b.question, b.paper_id, b.cat)
    except (RuntimeError, ValueError, KeyError, IndexError) as e:
        _err(e)


@router.get("/ai/history")
def ai_history(paper_id: int | None = None, u=Depends(auth.perm("ai"))):
    q, a = "SELECT * FROM ai_log WHERE user_id=?", [u["id"]]
    if paper_id:
        q += " AND scope=?"; a.append(f"paper:{paper_id}")
    return [dict(r) | {"refs": json.loads(r["refs"] or "[]")} for r in db.get().execute(q + " ORDER BY id DESC LIMIT 30", a)]


@router.delete("/ai/history/{hid}")
def ai_history_del(hid: int, u=Depends(auth.perm("ai"))):
    db.get().execute("DELETE FROM ai_log WHERE id=? AND user_id=?", (hid, u["id"]))
    return {"ok": True}


# ---------- 整個論文庫：批次預填、綜述
def _need_batch(u):
    if not (u["role"] == "admin" or (u["perms"].get("manage") and u["perms"].get("ai"))):
        raise HTTPException(403, "整批處理會產生費用，只有管理員（或有管理＋AI 權限的人）可以執行")


@router.get("/ai/estimate")
def ai_estimate(cat: int | None = None, mode: str = "missing", u=Depends(auth.perm("ai"))):
    return ai.estimate(db.get(), cat, mode)


@router.post("/ai/batch")
def ai_batch(b: dict[str, Any], u=Depends(auth.perm("ai"))):
    _need_batch(u)
    ai.config(db.get()) if ai.enabled(db.get()) else _err(RuntimeError("還沒設定 AI 服務"), 400)
    mode = b.get("mode") if b.get("mode") in ("missing", "empty", "all") else "missing"
    return {"job": jobs.enqueue("ai_batch", json.dumps({"cat": b.get("cat"), "mode": mode}), u["id"])}


@router.post("/ai/review")
def ai_review(b: dict[str, Any], u=Depends(auth.perm("ai"))):
    scope = str(b.get("scope") or "all")
    if scope in ("all", "cats"):
        _need_batch(u)
    if not ai.enabled(db.get()):
        _err(RuntimeError("還沒設定 AI 服務"), 400)
    return {"job": jobs.enqueue("ai_review", json.dumps({"scope": scope, "user": u["id"]}), u["id"])}


@router.get("/ai/reports")
def ai_reports(u=Depends(auth.perm("ai"))):
    con = db.get()
    rows = [dict(r) for r in con.execute(
        "SELECT r.id, r.scope, r.title, r.n_papers, r.model, r.created_at, u.display_name AS who FROM ai_reports r "
        "LEFT JOIN users u ON u.id=r.created_by WHERE r.id IN (SELECT MAX(id) FROM ai_reports GROUP BY scope) ORDER BY r.scope='all' DESC, r.id")]
    return rows


@router.get("/ai/reports/{rid}")
def ai_report(rid: int, u=Depends(auth.perm("ai"))):
    r = db.get().execute("SELECT r.*, u.display_name AS who FROM ai_reports r LEFT JOIN users u ON u.id=r.created_by WHERE r.id=?", (rid,)).fetchone()
    if r is None:
        raise HTTPException(404, "找不到")
    return dict(r) | {"refs": json.loads(r["refs"] or "[]")}


@router.get("/ai/reports/{rid}/md", response_class=PlainTextResponse)
def ai_report_md(rid: int, u=Depends(auth.perm("ai"))):
    r = ai_report(rid, u)
    from urllib.parse import quote
    text = f"# {r['title']}\n\n> {r['created_at'][:10]} · {r['n_papers']} 篇 · {r['model']}（AI 產生，請對照原文）\n\n{r['content']}\n"
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(r['title'])}.md"})


@router.post("/admin/ai/test")
def ai_test(u=Depends(auth.require_admin)):
    try:
        return {"text": ai.chat(db.get(), "用一句繁體中文回答。", "什麼是 cavity magnon polariton？", 200)}
    except (RuntimeError, ValueError, KeyError, IndexError) as e:
        _err(e)


# ================================================================== 組會與閱讀清單
def _meeting(con, mid: int) -> dict:
    m = con.execute("SELECT m.*, u.display_name AS creator FROM meetings m LEFT JOIN users u ON u.id=m.created_by WHERE m.id=?", (mid,)).fetchone()
    if m is None:
        raise HTTPException(404, "找不到這場組會")
    d = dict(m)
    d["items"] = [dict(r) for r in con.execute(
        "SELECT mi.*, u.display_name AS presenter, p.title, p.citekey, p.year, p.venue FROM meeting_items mi "
        "LEFT JOIN users u ON u.id=mi.presenter_id LEFT JOIN papers p ON p.id=mi.paper_id WHERE mi.meeting_id=? ORDER BY mi.sort, mi.id", (mid,))]
    return d


@router.get("/meetings")
def meetings(scope: str = "upcoming", u=Depends(auth.require_user)):
    con = db.get()
    today = M.datetime.now().strftime("%Y-%m-%d")
    q = {"upcoming": "WHERE date >= ? ORDER BY date", "past": "WHERE date < ? ORDER BY date DESC LIMIT 60",
         "all": "WHERE ? IS NOT NULL ORDER BY date DESC"}.get(scope, "WHERE date >= ? ORDER BY date")
    return [_meeting(con, r["id"]) for r in con.execute(f"SELECT id FROM meetings {q}", (today,))]


class MeetIn(BaseModel):
    date: str | None = None
    title: str | None = None
    note: str | None = None


@router.post("/meetings")
def add_meeting(b: MeetIn, u=Depends(auth.perm("meeting"))):
    if not b.date or not M.re.match(r"^\d{4}-\d{2}-\d{2}$", b.date):
        raise HTTPException(400, "請選日期")
    cur = db.get().execute("INSERT INTO meetings(date, title, note, created_by, created_at) VALUES(?,?,?,?,?)",
                           (b.date, (b.title or "").strip() or "組會", b.note or "", u["id"], db.now()))
    return _meeting(db.get(), cur.lastrowid)


@router.patch("/meetings/{mid}")
def edit_meeting(mid: int, b: MeetIn, u=Depends(auth.perm("meeting"))):
    con = db.get()
    _meeting(con, mid)
    for k in ("date", "title", "note"):
        v = getattr(b, k)
        if v is not None:
            con.execute(f"UPDATE meetings SET {k}=? WHERE id=?", (v.strip() if k != "note" else v, mid))
    return _meeting(con, mid)


@router.delete("/meetings/{mid}")
def del_meeting(mid: int, u=Depends(auth.perm("meeting"))):
    db.get().execute("DELETE FROM meetings WHERE id=?", (mid,))
    return {"ok": True}


class MItemIn(BaseModel):
    paper_id: int | None = None
    presenter_id: int | None = None
    note: str | None = None


def _notify_presenter(con, u, mid, pid, presenter):
    if not presenter:
        return
    m = con.execute("SELECT date, title FROM meetings WHERE id=?", (mid,)).fetchone()
    t = con.execute("SELECT title FROM papers WHERE id=?", (pid,)).fetchone() if pid else None
    notify.push(con, [presenter], "assign", u["id"], f"{u['display_name']} 排你在 {m['date']}「{m['title']}」報告"
                + (f"：{t['title']}" if t else ""), pid, None, mid)


@router.post("/meetings/{mid}/items")
def add_mitem(mid: int, b: MItemIn, u=Depends(auth.perm("meeting"))):
    con = db.get()
    _meeting(con, mid)
    if b.paper_id:
        M.paper_or_404(con, b.paper_id)
    mx = con.execute("SELECT COALESCE(MAX(sort),0)+1 FROM meeting_items WHERE meeting_id=?", (mid,)).fetchone()[0]
    con.execute("INSERT INTO meeting_items(meeting_id, paper_id, presenter_id, note, sort) VALUES(?,?,?,?,?)",
                (mid, b.paper_id, b.presenter_id, b.note or "", mx))
    _notify_presenter(con, u, mid, b.paper_id, b.presenter_id)
    db.log(con, u["id"], "meeting", b.paper_id, "")
    return _meeting(con, mid)


@router.patch("/meeting-items/{iid}")
def edit_mitem(iid: int, b: dict[str, Any], u=Depends(auth.perm("meeting"))):
    con = db.get()
    it = con.execute("SELECT * FROM meeting_items WHERE id=?", (iid,)).fetchone()
    if it is None:
        raise HTTPException(404, "找不到")
    if "presenter_id" in b:
        con.execute("UPDATE meeting_items SET presenter_id=? WHERE id=?", (b["presenter_id"] or None, iid))
        if b["presenter_id"] and b["presenter_id"] != it["presenter_id"]:
            _notify_presenter(con, u, it["meeting_id"], it["paper_id"], b["presenter_id"])
    if "paper_id" in b:
        con.execute("UPDATE meeting_items SET paper_id=? WHERE id=?", (b["paper_id"] or None, iid))
    if "note" in b:
        con.execute("UPDATE meeting_items SET note=? WHERE id=?", (str(b["note"] or ""), iid))
    if "sort" in b:
        con.execute("UPDATE meeting_items SET sort=? WHERE id=?", (int(b["sort"]), iid))
    return _meeting(con, it["meeting_id"])


@router.delete("/meeting-items/{iid}")
def del_mitem(iid: int, u=Depends(auth.perm("meeting"))):
    db.get().execute("DELETE FROM meeting_items WHERE id=?", (iid,))
    return {"ok": True}


# ================================================================== 回覆與通知
class ReplyIn(BaseModel):
    body: str


@router.post("/annotations/{aid}/replies")
def add_reply(aid: int, b: ReplyIn, u=Depends(auth.perm("annotate"))):
    con = db.get()
    a = con.execute("SELECT * FROM annotations WHERE id=?", (aid,)).fetchone()
    if a is None or (a["private"] and a["author_id"] != u["id"]):
        raise HTTPException(404, "找不到標註")
    body = b.body.strip()[:5000]
    if not body:
        raise HTTPException(400, "請輸入內容")
    con.execute("INSERT INTO ann_replies(ann_id, author_id, body, created_at) VALUES(?,?,?,?)", (aid, u["id"], body, db.now()))
    thread = {a["author_id"]} | {r["author_id"] for r in con.execute("SELECT author_id FROM ann_replies WHERE ann_id=?", (aid,))}
    ment = notify.mentioned(con, body)
    notify.push(con, thread - ment, "reply", u["id"], f"{u['display_name']} 回覆了討論：{body[:120]}", a["paper_id"], aid)
    notify.push(con, ment, "mention", u["id"], f"{u['display_name']} 在討論中提到你：{body[:120]}", a["paper_id"], aid)
    db.log(con, u["id"], "reply", a["paper_id"], body[:80])
    return {"ok": True}


@router.delete("/replies/{rid}")
def del_reply(rid: int, u=Depends(auth.perm("annotate"))):
    con = db.get()
    r = con.execute("SELECT author_id FROM ann_replies WHERE id=?", (rid,)).fetchone()
    if r is None:
        raise HTTPException(404, "找不到")
    if r["author_id"] != u["id"] and u["role"] != "admin":
        raise HTTPException(403, "只能刪除自己的回覆")
    con.execute("DELETE FROM ann_replies WHERE id=?", (rid,))
    return {"ok": True}


@router.get("/notifications")
def notifications(u=Depends(auth.require_user)):
    return notify.listing(db.get(), u["id"])


@router.post("/notifications/read")
def notifications_read(b: dict[str, Any], u=Depends(auth.require_user)):
    con = db.get()
    if b.get("all"):
        con.execute("UPDATE notifications SET read=1 WHERE user_id=?", (u["id"],))
    for i in b.get("ids") or []:
        con.execute("UPDATE notifications SET read=1 WHERE id=? AND user_id=?", (int(i), u["id"]))
    return {"ok": True}


@router.get("/mention-names")
def mention_names(u=Depends(auth.require_user)):
    return [r["display_name"] for r in db.get().execute("SELECT display_name FROM users WHERE disabled=0 ORDER BY id")]


@router.post("/admin/digest/test")
def digest_test(b: dict[str, Any], u=Depends(auth.require_admin)):
    con = db.get()
    try:
        if b.get("kind") == "email":
            r = con.execute("SELECT email FROM users WHERE id=?", (u["id"],)).fetchone()
            if not r["email"]:
                raise RuntimeError("請先在「我的帳號」填你的 Email")
            notify.send_mail(con, r["email"], f"{settings.get(con)['site_name']} 測試信",
                             "這是測試信。收到代表 SMTP 設定正確。\n\n" + notify.render_text(con, notify.weekly(con), dict(u)))
            return {"ok": True, "msg": f"已寄到 {r['email']}"}
        if b.get("kind") == "webhook":
            notify.send_webhook(con, "（測試）" + notify.render_text(con, notify.weekly(con)))
            return {"ok": True, "msg": "已送出"}
        if b.get("kind") == "preview":
            return {"ok": True, "msg": notify.render_text(con, notify.weekly(con), dict(u))}
        return {"ok": True, "job": jobs.enqueue("digest", "", u["id"]), "msg": "已排入寄送"}
    except Exception as e:  # noqa: BLE001
        _err(e, 400)


# ================================================================== OCR、匯出
@router.post("/files/{fid}/ocr")
def file_ocr(fid: int, u=Depends(auth.require_user)):
    con = db.get()
    f = M.file_or_404(con, fid)
    M.can_edit(u, f["paper_id"], "edit_meta")
    if not ocr.available()["ok"]:
        raise HTTPException(400, "伺服器沒有安裝 OCR（Docker 版已內建；本機執行需安裝 tesseract 與 ocrmypdf）")
    con.execute("UPDATE files SET ocr_state='queued' WHERE id=?", (fid,))
    return {"job": jobs.enqueue("ocr", str(fid), u["id"])}


@router.post("/admin/ocr/all")
def ocr_all(u=Depends(auth.require_admin)):
    con = db.get()
    if not ocr.available()["ok"]:
        raise HTTPException(400, "伺服器沒有安裝 OCR")
    rows = con.execute("SELECT id FROM files WHERE has_text=0 AND ocr_state NOT IN ('queued','running','done')").fetchall()
    for r in rows:
        con.execute("UPDATE files SET ocr_state='queued' WHERE id=?", (r["id"],))
        jobs.enqueue("ocr", str(r["id"]), u["id"])
    return {"queued": len(rows)}


@router.get("/files/{fid}/annotated")
def file_annotated(fid: int, u=Depends(auth.require_user)):
    con = db.get()
    M.file_or_404(con, fid)
    data, name = export.annotated_pdf(con, fid, u["id"])
    from urllib.parse import quote
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.get("/papers/{pid}/notes.md", response_class=PlainTextResponse)
def notes_md(pid: int, u=Depends(auth.require_user)):
    M.paper_or_404(db.get(), pid)
    text, name = export.notes_markdown(db.get(), pid, u["id"])
    from urllib.parse import quote
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


# ================================================================== 引用、Zotero、背景工作
@router.post("/admin/refs")
def refs_rebuild(b: dict[str, Any] | None = None, u=Depends(auth.require_admin)):
    con = db.get()
    if (b or {}).get("reset"):
        con.execute("DELETE FROM links WHERE auto=1")
    return {"job": jobs.enqueue("refs", "", u["id"])}


@router.post("/admin/zotero/sync")
def zotero_sync(u=Depends(auth.require_admin)):
    return {"job": jobs.enqueue("zotero", "", u["id"])}


@router.post("/admin/zotero/test")
def zotero_test(u=Depends(auth.require_admin)):
    try:
        return {"msg": zotero.test(db.get())}
    except RuntimeError as e:
        _err(e, 400)


@router.get("/jobs")
def job_list(kind: str | None = None, u=Depends(auth.require_user)):
    return jobs.recent(db.get(), 40, kind)


@router.get("/jobs/{jid}")
def job_get(jid: int, u=Depends(auth.require_user)):
    r = db.get().execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if r is None:
        raise HTTPException(404, "找不到")
    return dict(r) | {"label": jobs.LABELS.get(r["kind"], r["kind"])}


_ = refs  # 確保引用分析的背景工作已註冊
