"""研究工具的 API：參數（對照表、預估值、比較表）、圖表剪貼簿、語意搜尋與相似論文、預印本與重複論文、
入門路徑、組會投影片、實驗室動態看板。"""
import io
import json
import zipfile
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel

from . import ai, auth, db, figures, jobs, notify, params, semantic, settings, slides, versions
from . import main as M

router = APIRouter(prefix="/api")


def _ids(s: str) -> list[int]:
    return [int(x) for x in (s or "").replace(" ", "").split(",") if x.isdigit()][:60]


def _need_manage(u):
    if not (u["role"] == "admin" or u["perms"].get("manage")):
        raise HTTPException(403, "需要「管理」權限")


def _need_batch(u):
    if not (u["role"] == "admin" or (u["perms"].get("manage") and u["perms"].get("ai"))):
        raise HTTPException(403, "整批處理會產生費用，只有管理員（或有管理＋AI 權限的人）可以執行")


def _ai_err(e):
    raise HTTPException(502, str(e))


# ================================================================== 參數：三張參考表
TABLES = {
    "defs": ("param_defs", ["key", "name", "symbol", "aliases", "unit", "grp", "definition", "convention", "cqed", "in_compare", "sort"]),
    "typical": ("param_typical", ["platform", "key", "range_text", "lo", "hi", "note", "source", "sort"]),
    "map": ("param_map", ["grp", "magnon", "cqed", "cavity", "formula", "note", "sort"]),
}


@router.get("/params/reference")
def params_reference(u=Depends(auth.require_user)):
    con = db.get()
    return {"defs": params.defs(con),
            "typical": [dict(r) for r in con.execute("SELECT * FROM param_typical ORDER BY sort, id")],
            "map": [dict(r) for r in con.execute("SELECT * FROM param_map ORDER BY sort, id")],
            "ranges": params.library_ranges(con),
            "n_papers": con.execute("SELECT COUNT(DISTINCT paper_id) FROM paper_params").fetchone()[0]}


def _clean_row(table: str, b: dict) -> dict:
    cols = TABLES[table][1]
    out = {}
    for k in cols:
        if k in b:
            v = b[k]
            if k in ("lo", "hi"):
                v = params.parse_num(v) if v not in (None, "") else None
            elif k in ("in_compare", "sort"):
                v = int(v or 0)
            else:
                v = str(v or "").strip()[:3000]
            out[k] = v
    return out


@router.post("/params/ref/{table}")
def params_add(table: str, b: dict[str, Any], u=Depends(auth.require_user)):
    _need_manage(u)
    if table not in TABLES:
        raise HTTPException(404, "沒有這張表")
    name, _ = TABLES[table]
    row = _clean_row(table, b)
    con = db.get()
    if table == "defs":
        row["key"] = (row.get("key") or "").strip().lower().replace(" ", "_")
        if not row["key"] or not row.get("name"):
            raise HTTPException(400, "請填代號（英文）與名稱")
    row.setdefault("sort", con.execute(f"SELECT COALESCE(MAX(sort),0)+1 FROM {name}").fetchone()[0])
    cur = con.execute(f"INSERT INTO {name}({', '.join(row)}) VALUES({', '.join('?' * len(row))})", list(row.values()))
    db.log(con, u["id"], "params", None, f"{table} 新增")
    return {"id": cur.lastrowid}


@router.patch("/params/ref/{table}/{rid}")
def params_edit(table: str, rid: int, b: dict[str, Any], u=Depends(auth.require_user)):
    _need_manage(u)
    if table not in TABLES:
        raise HTTPException(404, "沒有這張表")
    name, _ = TABLES[table]
    row = _clean_row(table, b)
    row.pop("key", None) if table == "defs" else None      # 代號不能改（論文參數用它對應）
    if row:
        db.get().execute(f"UPDATE {name} SET {', '.join(f'{k}=?' for k in row)} WHERE id=?", [*row.values(), rid])
    return {"ok": True}


@router.delete("/params/ref/{table}/{rid}")
def params_del(table: str, rid: int, u=Depends(auth.require_user)):
    _need_manage(u)
    if table not in TABLES:
        raise HTTPException(404, "沒有這張表")
    db.get().execute(f"DELETE FROM {TABLES[table][0]} WHERE id=?", (rid,))
    return {"ok": True}


@router.post("/params/reference/reset")
def params_reset(b: dict[str, Any], u=Depends(auth.require_admin)):
    """把某張參考表恢復成預設內容（例如改壞了）。"""
    t = b.get("table")
    if t not in TABLES:
        raise HTTPException(400, "請指定 defs、typical 或 map")
    with db.tx() as con:
        con.execute(f"DELETE FROM {TABLES[t][0]}")
        params.seed(con)
    return {"ok": True}


# ---------- 論文參數
@router.get("/papers/{pid}/params")
def paper_params(pid: int, u=Depends(auth.require_user)):
    con = db.get()
    M.paper_or_404(con, pid)
    return {"params": params.paper_params(con, pid), "defs": params.defs(con)}


class ParamsIn(BaseModel):
    values: dict[str, Any]
    source: str = "human"
    meta: dict[str, dict] = {}


@router.put("/papers/{pid}/params")
def paper_params_set(pid: int, b: ParamsIn, u=Depends(auth.require_user)):
    with db.tx() as con:
        M.can_edit(u, pid, "edit_meta", con)
        keys = {d["key"] for d in params.defs(con)}
        for k, v in b.values.items():
            if k not in keys:
                continue
            m = b.meta.get(k) or {}
            params.set_param(con, pid, k, "" if v is None else str(v), u["id"], "ai" if b.source == "ai" else "human",
                             m.get("raw", ""), m.get("note", ""), m.get("page"))
        con.execute("UPDATE papers SET updated_at=? WHERE id=?", (db.now(), pid))
        db.log(con, u["id"], "params", pid, f"{len(b.values)} 項")
    return {"params": params.paper_params(db.get(), pid)}


@router.post("/papers/{pid}/params/ai")
def paper_params_ai(pid: int, u=Depends(auth.perm("ai"))):
    M.can_edit(u, pid, "edit_meta")
    try:
        return {"found": params.extract(db.get(), pid)}
    except (RuntimeError, ValueError, KeyError, IndexError) as e:
        _ai_err(e)


@router.post("/params/batch")
def params_batch(b: dict[str, Any], u=Depends(auth.perm("ai"))):
    if not ai.enabled(db.get()):
        raise HTTPException(400, "還沒設定 AI 服務")
    ids = [int(x) for x in b.get("ids") or []]
    if b.get("cat") or b.get("all") or len(ids) > 10:
        _need_batch(u)
    arg = {"ids": ids, "cat": b.get("cat"), "all": bool(b.get("all")), "redo": bool(b.get("redo"))}
    return {"job": jobs.enqueue("params", json.dumps(arg), u["id"])}


@router.get("/params/compare")
def params_compare(ids: str, u=Depends(auth.require_user)):
    return params.compare(db.get(), _ids(ids))


@router.get("/params/compare.csv")
def params_compare_csv(ids: str, u=Depends(auth.require_user)):
    return Response(params.compare_csv(db.get(), _ids(ids)), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=params-compare.csv"})


@router.post("/params/interpret")
def params_interpret(b: dict[str, Any], u=Depends(auth.perm("ai"))):
    try:
        return {"text": params.interpret(db.get(), [int(x) for x in b.get("ids") or []][:30])}
    except (RuntimeError, ValueError, KeyError, IndexError) as e:
        _ai_err(e)


# ================================================================== 圖表剪貼簿
def _fig_or_404(con, fid: int) -> dict:
    f = con.execute("SELECT * FROM figures WHERE id=?", (fid,)).fetchone()
    if f is None:
        raise HTTPException(404, "找不到圖卡")
    return dict(f)


def _fig_out(r) -> dict:
    d = dict(r)
    d["rect"] = json.loads(d["rect"] or "[]") if isinstance(d.get("rect"), str) else d.get("rect")
    d.pop("path", None)
    return d


class FigIn(BaseModel):
    file_id: int
    page: int
    rect: list[float]
    kind: str = "figure"
    caption: str | None = None
    note: str = ""


@router.post("/figures/preview")
def fig_preview(b: FigIn, u=Depends(auth.require_user)):
    try:
        return figures.preview(db.get(), b.file_id, b.page, b.rect)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/papers/{pid}/figures")
def fig_add(pid: int, b: FigIn, u=Depends(auth.perm("annotate"))):
    with db.tx() as con:
        M.paper_or_404(con, pid)
        f = M.file_or_404(con, b.file_id)
        if f["paper_id"] != pid:
            raise HTTPException(400, "檔案不屬於這篇論文")
        try:
            fid = figures.save(con, pid, b.file_id, b.page, b.rect, b.kind, b.caption, b.note, u["id"])
        except ValueError as e:
            raise HTTPException(400, str(e))
        db.log(con, u["id"], "figure", pid, f"p.{b.page}")
    return _fig_get(fid)


def _fig_get(fid: int) -> dict:
    r = db.get().execute("SELECT f.*, p.citekey, p.title AS paper_title, u.display_name AS who FROM figures f JOIN papers p ON p.id=f.paper_id "
                         "LEFT JOIN users u ON u.id=f.author_id WHERE f.id=?", (fid,)).fetchone()
    return _fig_out(r)


@router.get("/figures")
def fig_list(paper: int | None = None, kind: str = "", mine: int = 0, q: str = "", limit: int = 60, offset: int = 0,
             u=Depends(auth.require_user)):
    where, args = ["1=1"], []
    if paper:
        where.append("f.paper_id=?"); args.append(paper)
    if kind in figures.KINDS:
        where.append("f.kind=?"); args.append(kind)
    if mine:
        where.append("f.author_id=?"); args.append(u["id"])
    if q.strip():
        like = f"%{q.strip()}%"
        where.append("(f.caption LIKE ? OR f.note LIKE ? OR f.text LIKE ? OR p.title LIKE ? OR p.citekey LIKE ?)"); args += [like] * 5
    con = db.get()
    base = f"FROM figures f JOIN papers p ON p.id=f.paper_id LEFT JOIN users u ON u.id=f.author_id WHERE {' AND '.join(where)}"
    total = con.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
    rows = con.execute(f"SELECT f.*, p.citekey, p.title AS paper_title, u.display_name AS who {base} ORDER BY "
                       + ("f.page, f.id" if paper else "f.id DESC") + " LIMIT ? OFFSET ?", args + [min(limit, 300), offset]).fetchall()
    return {"total": total, "items": [_fig_out(r) for r in rows], "kinds": figures.KINDS}


@router.get("/figures/{fid}/image")
def fig_image(fid: int, download: int = 0, u=Depends(auth.require_user)):
    f = _fig_or_404(db.get(), fid)
    p = db.get().execute("SELECT citekey FROM papers WHERE id=?", (f["paper_id"],)).fetchone()
    return FileResponse(figures.file_path(f), media_type="image/png", filename=f"{p['citekey']}-p{f['page']}-{fid}.png",
                        content_disposition_type="attachment" if download else "inline", headers={"Cache-Control": "private, max-age=86400"})


@router.patch("/figures/{fid}")
def fig_edit(fid: int, b: dict[str, Any], u=Depends(auth.perm("annotate"))):
    con = db.get()
    _fig_or_404(con, fid)
    for k in ("caption", "note", "latex"):
        if k in b:
            con.execute(f"UPDATE figures SET {k}=? WHERE id=?", (str(b[k] or "")[:4000], fid))
    if b.get("kind") in figures.KINDS:
        con.execute("UPDATE figures SET kind=? WHERE id=?", (b["kind"], fid))
    return _fig_get(fid)


@router.delete("/figures/{fid}")
def fig_del(fid: int, u=Depends(auth.perm("annotate"))):
    con = db.get()
    f = _fig_or_404(con, fid)
    if f["author_id"] != u["id"] and u["role"] != "admin":
        raise HTTPException(403, "只能刪除自己做的圖卡")
    figures.delete(con, f)
    return {"ok": True}


@router.post("/figures/{fid}/latex")
def fig_latex(fid: int, u=Depends(auth.perm("ai"))):
    try:
        return {"latex": figures.to_latex(db.get(), fid)}
    except (RuntimeError, ValueError, KeyError, IndexError) as e:
        _ai_err(e)


@router.get("/figures-zip")
def fig_zip(ids: str, u=Depends(auth.require_user)):
    con = db.get()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        caps = []
        for fid in _ids(ids):
            f = con.execute("SELECT f.*, p.citekey FROM figures f JOIN papers p ON p.id=f.paper_id WHERE f.id=?", (fid,)).fetchone()
            if not f:
                continue
            name = f"{f['citekey']}-p{f['page']}-{fid}.png"
            path = figures.file_path(dict(f))
            if path.exists():
                z.write(path, name)
                caps.append(f"{name}\n  {f['caption']}\n" + (f"  筆記：{f['note']}\n" if f["note"] else "") + (f"  LaTeX：{f['latex']}\n" if f["latex"] else ""))
        z.writestr("說明.txt", "\n".join(caps))
    return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": "attachment; filename=figures.zip"})


# ================================================================== 投影片
@router.get("/meetings/{mid}/slides/options")
def slide_options(mid: int, u=Depends(auth.require_user)):
    con = db.get()
    items = [dict(r) for r in con.execute(
        "SELECT mi.id, mi.paper_id, p.citekey, p.title, us.display_name AS presenter FROM meeting_items mi LEFT JOIN papers p ON p.id=mi.paper_id "
        "LEFT JOIN users us ON us.id=mi.presenter_id WHERE mi.meeting_id=? ORDER BY mi.sort, mi.id", (mid,))]
    for it in items:
        it["figures"] = [_fig_out(r) for r in con.execute("SELECT * FROM figures WHERE paper_id=? ORDER BY page, id", (it["paper_id"] or -1,))]
        it["has_keyinfo"] = bool(it["paper_id"]) and con.execute("SELECT keyinfo FROM papers WHERE id=?", (it["paper_id"],)).fetchone()["keyinfo"] not in ("{}", "")
        it["n_params"] = con.execute("SELECT COUNT(*) FROM paper_params WHERE paper_id=?", (it["paper_id"] or -1,)).fetchone()[0]
    return {"items": items, "ai": ai.enabled(con)}


@router.post("/meetings/{mid}/slides")
def slide_meeting(mid: int, b: dict[str, Any], u=Depends(auth.require_user)):
    use_ai = bool(b.get("ai"))
    if use_ai:
        auth.need(u, "ai")
    arg = {"meeting": mid, "user": u["id"], "ai": use_ai, "figs": b.get("figs")}
    return {"job": jobs.enqueue("slides", json.dumps(arg), u["id"], dedupe=False)}


@router.post("/papers/{pid}/slides")
def slide_paper(pid: int, b: dict[str, Any], u=Depends(auth.require_user)):
    use_ai = bool(b.get("ai"))
    if use_ai:
        auth.need(u, "ai")
    M.paper_or_404(db.get(), pid)
    arg = {"paper": pid, "user": u["id"], "ai": use_ai, "fig_ids": b.get("fig_ids"), "presenter": u["display_name"]}
    return {"job": jobs.enqueue("slides", json.dumps(arg), u["id"], dedupe=False)}


@router.post("/figures/slides")
def slide_figures(b: dict[str, Any], u=Depends(auth.require_user)):
    ids = [int(x) for x in b.get("ids") or []][:80]
    if not ids:
        raise HTTPException(400, "請先勾選圖卡")
    return {"job": jobs.enqueue("slides", json.dumps({"figures": ids, "title": b.get("title") or "圖表剪貼簿"}), u["id"], dedupe=False)}


@router.get("/exports/{name}")
def get_export(name: str, u=Depends(auth.require_user)):
    try:
        p = slides.export_path(name)
    except FileNotFoundError:
        raise HTTPException(404, "檔案不存在（可能已被清掉，請重新產生）")
    return FileResponse(p, filename=p.name, media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")


# ================================================================== 語意搜尋、相似論文
@router.get("/semantic")
def semantic_search(q: str = "", cat: int | None = None, u=Depends(auth.require_user)):
    con = db.get()
    r = semantic.search(con, q, 40, cat)
    ids = [x["id"] for x in r["items"]]
    meta = {x["id"]: x for x in r["items"]}
    items = M.cards(con, ids, u["id"])
    for c in items:
        c["score"] = meta[c["id"]]["score"]
        c["why"] = meta[c["id"]]["why"]
    return {"items": items, "total": len(items), "mode": r["mode"], "keywords": r["keywords"]}


@router.get("/papers/{pid}/similar")
def paper_similar(pid: int, u=Depends(auth.require_user)):
    con = db.get()
    M.paper_or_404(con, pid)
    sims = semantic.similar(con, pid)
    linked = {r["o"] for r in con.execute("SELECT dst_id o FROM links WHERE src_id=? UNION SELECT src_id FROM links WHERE dst_id=?", (pid, pid))}
    out = []
    for s in sims:
        p = con.execute("SELECT id, citekey, title, year FROM papers WHERE id=?", (s["id"],)).fetchone()
        if p:
            out.append(dict(p) | s | {"linked": p["id"] in linked})
    return out


@router.get("/admin/embed")
def embed_status(u=Depends(auth.require_admin)):
    return semantic.emb_status(db.get())


@router.post("/admin/embed/run")
def embed_run(u=Depends(auth.require_admin)):
    if not semantic.emb_config(db.get()):
        raise HTTPException(400, "請先設定並儲存向量模型")
    return {"job": jobs.enqueue("embed", "", u["id"])}


@router.post("/admin/embed/test")
def embed_test(u=Depends(auth.require_admin)):
    try:
        v = semantic.embed(db.get(), ["cavity magnon polariton"])[0]
        return {"msg": f"連線成功，向量維度 {len(v)}"}
    except (RuntimeError, KeyError, IndexError, ValueError) as e:
        raise HTTPException(502, str(e))


# ================================================================== 預印本與正式版、重複論文
@router.get("/versions")
def versions_list(u=Depends(auth.require_user)):
    return versions.pending(db.get())


@router.post("/versions/check")
def versions_check(u=Depends(auth.require_user)):
    _need_manage(u)
    return {"job": jobs.enqueue("versions", "force", u["id"])}


@router.post("/versions/{sid}/apply")
def versions_apply(sid: int, u=Depends(auth.require_user)):
    with db.tx() as con:
        s = con.execute("SELECT * FROM version_suggest WHERE id=? AND kind='published'", (sid,)).fetchone()
        if s is None:
            raise HTTPException(404, "找不到")
        M.can_edit(u, s["paper_id"], "edit_meta", con)
        if con.execute("SELECT 1 FROM papers WHERE lower(doi)=lower(?) AND id!=?", (s["doi"], s["paper_id"])).fetchone():
            raise HTTPException(409, "另一篇論文已使用這個 DOI，請改用「合併重複論文」")
        versions.apply_published(con, s["paper_id"], s["doi"], s["venue"], s["year"], s["source"], u["id"])
        con.execute("UPDATE version_suggest SET status='applied', resolved_at=?, resolved_by=? WHERE id=?", (db.now(), u["id"], sid))
    return {"ok": True}


@router.post("/versions/{sid}/dismiss")
def versions_dismiss(sid: int, u=Depends(auth.require_user)):
    db.get().execute("UPDATE version_suggest SET status='dismissed', resolved_at=?, resolved_by=? WHERE id=?", (db.now(), u["id"], sid))
    return {"ok": True}


class MergeIn(BaseModel):
    keep: int
    drop: int


@router.post("/papers/merge")
def papers_merge(b: MergeIn, u=Depends(auth.require_user)):
    _need_manage(u)
    with db.tx() as con:
        try:
            r = versions.merge(con, b.keep, b.drop, u)
        except ValueError as e:
            raise HTTPException(400, str(e))
    jobs.enqueue("similar_index", "")
    return r


@router.post("/papers/{pid}/check-published")
def paper_check_published(pid: int, u=Depends(auth.require_user)):
    con = db.get()
    M.can_edit(u, pid, "edit_meta", con)
    try:
        msg = versions.check_published(con, only=pid)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"查詢失敗：{e}")
    return {"msg": msg, "paper": M.paper_detail(con, pid, u["id"])}


# ================================================================== 入門路徑
def _path_or_404(con, pid: int) -> dict:
    r = con.execute("SELECT p.*, u.display_name AS creator FROM paths p LEFT JOIN users u ON u.id=p.created_by WHERE p.id=?", (pid,)).fetchone()
    if r is None:
        raise HTTPException(404, "找不到這條路徑")
    return dict(r)


def _path_detail(con, path_id: int, uid: int) -> dict:
    p = _path_or_404(con, path_id)
    p["items"] = [dict(r) for r in con.execute(
        "SELECT pi.id, pi.paper_id, pi.goal, pi.sort, pp.title, pp.citekey, pp.year, pp.venue, "
        "(SELECT id FROM files f WHERE f.paper_id=pp.id ORDER BY CASE role WHEN 'main' THEN 0 ELSE 1 END, id LIMIT 1) AS thumb "
        "FROM path_items pi JOIN papers pp ON pp.id=pi.paper_id WHERE pi.path_id=? ORDER BY pi.sort, pi.id", (path_id,))]
    ids = [i["paper_id"] for i in p["items"]]
    qm = ",".join("?" * len(ids)) or "NULL"
    members = [dict(r) for r in con.execute(
        "SELECT pm.user_id, pm.created_at, u.display_name FROM path_members pm JOIN users u ON u.id=pm.user_id WHERE pm.path_id=? ORDER BY pm.created_at",
        (path_id,))]
    for m in members:
        m["done"] = sorted(r["paper_id"] for r in con.execute(
            f"SELECT paper_id FROM user_paper WHERE user_id=? AND status='已閱讀' AND paper_id IN ({qm})", [m["user_id"]] + ids))
        m["reading"] = sorted(r["paper_id"] for r in con.execute(
            f"SELECT paper_id FROM user_paper WHERE user_id=? AND status='閱讀中' AND paper_id IN ({qm})", [m["user_id"]] + ids))
        nxt = next((i for i in p["items"] if i["paper_id"] not in m["done"]), None)
        m["next"] = nxt["paper_id"] if nxt else None
    p["members"] = members
    mine = {r["paper_id"]: r["status"] for r in con.execute(f"SELECT paper_id, status FROM user_paper WHERE user_id=? AND paper_id IN ({qm})", [uid] + ids)}
    for i in p["items"]:
        i["my_status"] = mine.get(i["paper_id"])
    p["joined"] = any(m["user_id"] == uid for m in members)
    return p


@router.get("/paths")
def paths_list(u=Depends(auth.require_user)):
    con = db.get()
    out = []
    for r in con.execute("SELECT id FROM paths ORDER BY sort, id"):
        d = _path_detail(con, r["id"], u["id"])
        n = len(d["items"])
        me = next((m for m in d["members"] if m["user_id"] == u["id"]), None)
        out.append({k: d[k] for k in ("id", "title", "description", "creator", "created_at", "joined")} | {
            "n_items": n, "n_members": len(d["members"]), "my_done": len(me["done"]) if me else sum(1 for i in d["items"] if i["my_status"] == "已閱讀"),
            "next": next((i for i in d["items"] if i["my_status"] != "已閱讀"), None),
            "members": [{"name": m["display_name"], "done": len(m["done"])} for m in d["members"]]})
    return out


class PathIn(BaseModel):
    title: str | None = None
    description: str | None = None
    items: list[dict] | None = None


@router.post("/paths")
def path_add(b: PathIn, u=Depends(auth.perm("meeting"))):
    t = (b.title or "").strip()
    if not t:
        raise HTTPException(400, "請填路徑名稱")
    with db.tx() as con:
        cur = con.execute("INSERT INTO paths(title, description, created_by, created_at, sort) VALUES(?,?,?,?,?)",
                          (t[:100], (b.description or "")[:3000], u["id"], db.now(),
                           con.execute("SELECT COALESCE(MAX(sort),0)+1 FROM paths").fetchone()[0]))
        pid = cur.lastrowid
        for i, it in enumerate(b.items or []):
            if con.execute("SELECT 1 FROM papers WHERE id=?", (int(it.get("paper_id") or 0),)).fetchone():
                con.execute("INSERT INTO path_items(path_id, paper_id, goal, sort) VALUES(?,?,?,?)", (pid, int(it["paper_id"]), str(it.get("goal") or "")[:1000], i))
        db.log(con, u["id"], "path", None, t)
    return _path_detail(db.get(), pid, u["id"])


@router.get("/paths/{path_id}")
def path_get(path_id: int, u=Depends(auth.require_user)):
    return _path_detail(db.get(), path_id, u["id"])


@router.patch("/paths/{path_id}")
def path_edit(path_id: int, b: PathIn, u=Depends(auth.perm("meeting"))):
    con = db.get()
    _path_or_404(con, path_id)
    if b.title is not None and b.title.strip():
        con.execute("UPDATE paths SET title=? WHERE id=?", (b.title.strip()[:100], path_id))
    if b.description is not None:
        con.execute("UPDATE paths SET description=? WHERE id=?", (b.description[:3000], path_id))
    return _path_detail(con, path_id, u["id"])


@router.delete("/paths/{path_id}")
def path_del(path_id: int, u=Depends(auth.perm("meeting"))):
    db.get().execute("DELETE FROM paths WHERE id=?", (path_id,))
    return {"ok": True}


@router.post("/paths/{path_id}/items")
def path_item_add(path_id: int, b: dict[str, Any], u=Depends(auth.perm("meeting"))):
    con = db.get()
    _path_or_404(con, path_id)
    M.paper_or_404(con, int(b.get("paper_id") or 0))
    if con.execute("SELECT 1 FROM path_items WHERE path_id=? AND paper_id=?", (path_id, int(b["paper_id"]))).fetchone():
        raise HTTPException(409, "這篇已經在路徑裡了")
    mx = con.execute("SELECT COALESCE(MAX(sort),0)+1 FROM path_items WHERE path_id=?", (path_id,)).fetchone()[0]
    con.execute("INSERT INTO path_items(path_id, paper_id, goal, sort) VALUES(?,?,?,?)", (path_id, int(b["paper_id"]), str(b.get("goal") or "")[:1000], mx))
    for m in con.execute("SELECT user_id FROM path_members WHERE path_id=?", (path_id,)).fetchall():
        if not db.get_status(con, m["user_id"], int(b["paper_id"])):
            db.set_status(con, m["user_id"], int(b["paper_id"]), "待讀")
    return _path_detail(con, path_id, u["id"])


@router.patch("/path-items/{iid}")
def path_item_edit(iid: int, b: dict[str, Any], u=Depends(auth.perm("meeting"))):
    con = db.get()
    it = con.execute("SELECT path_id FROM path_items WHERE id=?", (iid,)).fetchone()
    if it is None:
        raise HTTPException(404, "找不到")
    if "goal" in b:
        con.execute("UPDATE path_items SET goal=? WHERE id=?", (str(b["goal"] or "")[:1000], iid))
    return {"ok": True}


@router.delete("/path-items/{iid}")
def path_item_del(iid: int, u=Depends(auth.perm("meeting"))):
    db.get().execute("DELETE FROM path_items WHERE id=?", (iid,))
    return {"ok": True}


@router.post("/paths/{path_id}/order")
def path_order(path_id: int, b: dict[str, Any], u=Depends(auth.perm("meeting"))):
    with db.tx() as con:
        for i, iid in enumerate(b.get("ids") or []):
            con.execute("UPDATE path_items SET sort=? WHERE id=? AND path_id=?", (i, int(iid), path_id))
    return {"ok": True}


def _join(con, path_id: int, uids: list[int], actor) -> int:
    p = _path_or_404(con, path_id)
    items = [r["paper_id"] for r in con.execute("SELECT paper_id FROM path_items WHERE path_id=? ORDER BY sort, id", (path_id,))]
    new = []
    for uid in dict.fromkeys(uids):
        if not con.execute("SELECT 1 FROM users WHERE id=? AND disabled=0", (uid,)).fetchone():
            continue
        cur = con.execute("INSERT OR IGNORE INTO path_members(path_id, user_id, added_by, created_at) VALUES(?,?,?,?)", (path_id, uid, actor["id"], db.now()))
        if cur.rowcount:
            new.append(uid)
            for pid in items:           # 路徑上的論文列入他的待讀（已經有狀態的不動）
                if not db.get_status(con, uid, pid):
                    db.set_status(con, uid, pid, "待讀")
    notify.push(con, [x for x in new if x != actor["id"]], "path", actor["id"], f"{actor['display_name']} 幫你安排了入門路徑：{p['title']}（{len(items)} 篇）")
    return len(new)


@router.post("/paths/{path_id}/members")
def path_members_add(path_id: int, b: dict[str, Any], u=Depends(auth.perm("meeting"))):
    with db.tx() as con:
        n = _join(con, path_id, [int(x) for x in b.get("user_ids") or []], u)
        db.log(con, u["id"], "path_assign", None, f"{n} 人")
    return {"added": n}


@router.post("/paths/{path_id}/join")
def path_join(path_id: int, u=Depends(auth.require_user)):
    with db.tx() as con:
        _join(con, path_id, [u["id"]], u)
    return {"ok": True}


@router.delete("/paths/{path_id}/members/{uid}")
def path_member_del(path_id: int, uid: int, u=Depends(auth.require_user)):
    if uid != u["id"] and not u["perms"].get("meeting"):
        raise HTTPException(403, "只能退出自己，或需要「指派」權限")
    db.get().execute("DELETE FROM path_members WHERE path_id=? AND user_id=?", (path_id, uid))
    return {"ok": True}


@router.post("/paths/draft")
def path_draft(b: dict[str, Any], u=Depends(auth.perm("ai"))):
    """AI 起草：從某個分類（或整個論文庫）挑 4–8 篇、排順序、寫每篇的閱讀目標。"""
    con = db.get()
    try:
        c = ai.config(con)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    cat = b.get("cat")
    q = "SELECT p.* FROM papers p" + (" JOIN paper_categories pc ON pc.paper_id=p.id AND pc.category_id=?" if cat else "") + " ORDER BY p.year, p.id"
    rows = con.execute(q, (cat,) if cat else ()).fetchall()
    if not rows:
        raise HTTPException(400, "沒有論文可以挑")
    blocks = []
    for p in rows[:150]:
        ki = json.loads(p["keyinfo"] or "{}")
        blocks.append(f"[{p['citekey']}] {p['title']}（{p['year'] or '?'}，{p['kind']}）重點：{(ki.get('重點') or p['abstract'] or '')[:300]}")
    topic = str(b.get("topic") or "").strip()
    system = ("你幫凝態物理實驗室設計新成員的論文閱讀路徑。只輸出 JSON 陣列：[{\"citekey\": ..., \"goal\": \"讀這篇要看懂什麼（40 字內，具體）\"}]，"
              "4–8 篇，由淺入深：先回顧或經典入門，再核心實驗，最後和實驗室方向最接近的。只能用資料裡的 citekey。" + ai._lab(c))
    user = (f"主題：{topic or '這個分類的入門'}\n\n候選論文：\n" + "\n".join(blocks))[: c["max_chars"]]
    try:
        out = ai._json_from(ai.chat(con, system, user, 1500))
    except Exception as e:  # noqa: BLE001
        _ai_err(e)
    by = {r["citekey"]: r for r in rows}
    res = []
    for it in out if isinstance(out, list) else []:
        p = by.get(str(it.get("citekey", "")).strip("[] "))
        if p and all(x["paper_id"] != p["id"] for x in res):
            res.append({"paper_id": p["id"], "citekey": p["citekey"], "title": p["title"], "goal": str(it.get("goal") or "")[:200]})
    return res


# ================================================================== 實驗室動態看板
@router.get("/dashboard")
def dashboard(days: int = 30, u=Depends(auth.require_user)):
    con = db.get()
    days = max(7, min(days, 365))
    now = datetime.now()
    since = (now - timedelta(days=days)).isoformat()
    prev = (now - timedelta(days=2 * days)).isoformat()
    since_day = (now - timedelta(days=days)).strftime("%Y-%m-%d")

    def cnt(sql, *a):
        return con.execute(sql, a).fetchone()[0]

    def pair(table, col, extra=""):
        return {"now": cnt(f"SELECT COUNT(*) FROM {table} WHERE {col} >= ? {extra}", since),
                "prev": cnt(f"SELECT COUNT(*) FROM {table} WHERE {col} >= ? AND {col} < ? {extra}", prev, since)}
    totals = {"papers": pair("papers", "added_at"), "annotations": pair("annotations", "created_at", "AND private=0"),
              "replies": pair("ann_replies", "created_at"), "figures": pair("figures", "created_at"),
              "links": pair("links", "created_at", "AND auto=0")}
    # 每位成員（只算公開的貢獻；閱讀狀態是私人的，不列）
    members = []
    for r in con.execute("SELECT id, display_name FROM users WHERE disabled=0 ORDER BY id"):
        uid = r["id"]
        m = {"name": r["display_name"],
             "uploads": cnt("SELECT COUNT(*) FROM papers WHERE added_by=? AND added_at >= ?", uid, since),
             "annotations": cnt("SELECT COUNT(*) FROM annotations WHERE author_id=? AND private=0 AND created_at >= ?", uid, since),
             "replies": cnt("SELECT COUNT(*) FROM ann_replies WHERE author_id=? AND created_at >= ?", uid, since),
             "figures": cnt("SELECT COUNT(*) FROM figures WHERE author_id=? AND created_at >= ?", uid, since),
             "links": cnt("SELECT COUNT(*) FROM links WHERE author_id=? AND auto=0 AND created_at >= ?", uid, since)}
        m["total"] = sum(v for k, v in m.items() if k != "name")
        members.append(m)
    members.sort(key=lambda x: -x["total"])
    top_opened = [dict(r) for r in con.execute(
        "SELECT p.id, p.citekey, p.title, COUNT(*) n, COUNT(DISTINCT v.user_id) people FROM paper_views v JOIN papers p ON p.id=v.paper_id "
        "WHERE v.day >= ? GROUP BY v.paper_id ORDER BY n DESC, people DESC LIMIT 10", (since_day,))]
    hot = [dict(r) for r in con.execute(
        "SELECT a.id AS ann_id, a.paper_id, p.title, p.citekey, a.quote, a.body, COUNT(r.id) n FROM ann_replies r JOIN annotations a ON a.id=r.ann_id "
        "JOIN papers p ON p.id=a.paper_id WHERE r.created_at >= ? AND a.private=0 GROUP BY a.id ORDER BY n DESC LIMIT 6", (since,))]
    # 每月新增論文與標註（近 12 個月）
    months = []
    y, mth = now.year, now.month
    for _ in range(12):
        months.append(f"{y:04d}-{mth:02d}")
        mth -= 1
        if mth == 0:
            y, mth = y - 1, 12
    months.reverse()
    per_month = {k: {"papers": 0, "annotations": 0} for k in months}
    for r in con.execute("SELECT substr(added_at,1,7) m, COUNT(*) n FROM papers GROUP BY m"):
        if r["m"] in per_month:
            per_month[r["m"]]["papers"] = r["n"]
    for r in con.execute("SELECT substr(created_at,1,7) m, COUNT(*) n FROM annotations WHERE private=0 GROUP BY m"):
        if r["m"] in per_month:
            per_month[r["m"]]["annotations"] = r["n"]
    cats = [dict(r) for r in con.execute(
        "SELECT c.id, c.name, c.color, COUNT(pc.paper_id) n FROM categories c LEFT JOIN paper_categories pc ON pc.category_id=c.id GROUP BY c.id ORDER BY n DESC")]
    years = [dict(r) for r in con.execute("SELECT year, COUNT(*) n FROM papers WHERE year IS NOT NULL GROUP BY year ORDER BY year")]
    # 必讀與入門路徑：整體進度（個人明細只有管理者看得到）
    req_ids = [r["id"] for r in con.execute("SELECT id FROM papers WHERE required=1")]
    users = [r["id"] for r in con.execute("SELECT id FROM users WHERE disabled=0")]
    req_done = cnt(f"SELECT COUNT(*) FROM user_paper WHERE status='已閱讀' AND paper_id IN ({','.join('?' * len(req_ids)) or 'NULL'}) "
                   f"AND user_id IN (SELECT id FROM users WHERE disabled=0)", *req_ids) if req_ids else 0
    paths = []
    for r in con.execute("SELECT id, title FROM paths ORDER BY sort, id"):
        d = _path_detail(con, r["id"], u["id"])
        n = len(d["items"])
        paths.append({"id": d["id"], "title": d["title"], "n_items": n,
                      "members": [{"name": m["display_name"], "done": len(m["done"])} for m in d["members"]]})
    assign_total = cnt("SELECT COUNT(*) FROM paper_assign")
    assign_done = cnt("SELECT COUNT(*) FROM paper_assign pa JOIN user_paper up ON up.user_id=pa.user_id AND up.paper_id=pa.paper_id WHERE up.status='已閱讀'")
    return {"days": days, "totals": totals, "members": members, "top_opened": top_opened, "hot": hot,
            "months": [{"month": k, **v} for k, v in per_month.items()], "categories": cats, "years": years,
            "required": {"papers": len(req_ids), "users": len(users), "done": req_done},
            "assign": {"total": assign_total, "done": assign_done}, "paths": paths,
            "library": {"papers": cnt("SELECT COUNT(*) FROM papers"), "annotations": cnt("SELECT COUNT(*) FROM annotations WHERE private=0"),
                        "figures": cnt("SELECT COUNT(*) FROM figures"), "links": cnt("SELECT COUNT(*) FROM links"),
                        "with_params": cnt("SELECT COUNT(DISTINCT paper_id) FROM paper_params")}}


_ = (PlainTextResponse, quote)



# ================================================================== 期刊搜尋
@router.get("/journal-search/meta")
def jsearch_meta(u=Depends(auth.require_user)):
    from . import journals
    con = db.get()
    return {"journals": journals.journals(con), "default": journals.DEFAULT, "keywords": journals.library_keywords(con),
            "engine": "openalex" if settings.get(con)["openalex_key"] else "crossref"}


class JSearchIn(BaseModel):
    journals: list[str]
    keywords: list[str]
    mode: str = "or"
    exclude: list[str] = []
    days: int | None = 365
    date_from: str | None = None
    date_to: str | None = None
    limit: int = 200


@router.post("/journal-search")
def jsearch(b: JSearchIn, u=Depends(auth.require_user)):
    from . import journals
    try:
        return journals.search(db.get(), b.journals, b.keywords, "and" if b.mode == "and" else "or", b.exclude, b.days,
                               b.date_from, b.date_to, max(10, min(b.limit, 500)))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001 - 外部服務錯誤
        msg = str(e)
        if "409" in msg or "401" in msg or "403" in msg:
            msg += "（OpenAlex 金鑰可能無效或額度用完，請到「管理 → 維護與備份」檢查）"
        raise HTTPException(502, f"搜尋服務連線失敗：{msg}")


@router.post("/journal-search/add")
def jsearch_add(b: dict[str, Any], u=Depends(auth.perm("upload"))):
    """把勾選的搜尋結果加入論文庫（沿用「新論文追蹤」的加入流程：有開放取用 PDF 會一起下載）。"""
    from . import feeds, journals
    con = db.get()
    with db.tx() as c:
        ids = journals.store_items(c, (b.get("items") or [])[:50])
    out = []
    for i in ids:
        try:
            out.append({"id": i, **feeds.add_to_library(con, i, u["id"], b.get("tags") or [], [])})
        except Exception as e:  # noqa: BLE001
            out.append({"id": i, "error": str(e)})
    M._after_files([f for f in (M.library.main_file_id(con, r["paper_id"]) for r in out if r.get("paper_id")) if f], u)
    return out


@router.post("/journal-search/journals")
def jsearch_add_journal(b: dict[str, Any], u=Depends(auth.require_user)):
    from . import journals
    _need_manage(u)
    try:
        return journals.add_custom(db.get(), str(b.get("name") or ""), str(b.get("issn") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/journal-search/journals/{key}")
def jsearch_del_journal(key: str, u=Depends(auth.require_user)):
    from . import journals
    _need_manage(u)
    journals.del_custom(db.get(), key)
    return {"ok": True}


# ================================================================== 我的筆記（所有標註、手寫、筆記頁集中看）
def _thin_ink(ink_json: str, max_strokes: int = 150, max_pts: int = 60) -> dict | None:
    """清單預覽用：筆畫與點數減量，避免一次傳太多資料。"""
    try:
        strokes = (json.loads(ink_json) if ink_json else {}).get("strokes", [])
    except ValueError:
        return None
    out = []
    for s in strokes[:max_strokes]:
        pts = s.get("p") or []
        step = max(1, len(pts) // max_pts)
        thin = pts[::step]
        if pts and thin[-1] != pts[-1]:
            thin.append(pts[-1])
        out.append({"c": s.get("c"), "w": s.get("w"), "o": s.get("o"), "p": thin})
    return {"strokes": out, "total": len(strokes)}


NOTE_KINDS = {
    "ink": "a.kind='ink' AND a.nb=0",
    "highlight": "a.kind='highlight'",
    "note": "a.kind='note'",
    "notebook": "a.nb=1",
}


@router.get("/notes")
def all_notes(scope: str = "mine", kind: str = "all", q: str = "", paper: int = 0, limit: int = 120, offset: int = 0,
              u=Depends(auth.require_user)):
    con = db.get()
    me = u["id"]
    base = ["(a.private=0 OR a.author_id=?)"]
    args: list[Any] = [me]
    if scope != "all":
        base.append("a.author_id=?"); args.append(me)
    if paper:
        base.append("a.paper_id=?"); args.append(paper)
    if q.strip():
        like = f"%{q.strip()}%"
        base.append("(a.quote LIKE ? OR a.body LIKE ? OR p.title LIKE ? OR p.citekey LIKE ?)"); args += [like] * 4
    where = " AND ".join(base)
    counts = {k: con.execute(f"SELECT COUNT(*) FROM annotations a JOIN papers p ON p.id=a.paper_id WHERE {where} AND {cond}", args).fetchone()[0]
              for k, cond in NOTE_KINDS.items()}
    cond = NOTE_KINDS.get(kind, "a.nb=0")        # 「全部」不含筆記頁（筆記頁另外列成整本）
    limit = max(1, min(limit, 300))
    rows = con.execute(
        f"SELECT a.id, a.paper_id, a.file_id, a.page, a.kind, a.color, a.quote, a.body, a.private, a.author_id, a.nb, a.ink, "
        f"a.created_at, a.updated_at, u.display_name AS who, p.title, p.citekey, p.year, "
        f"(SELECT COUNT(*) FROM ann_replies r WHERE r.ann_id=a.id) AS n_replies "
        f"FROM annotations a JOIN papers p ON p.id=a.paper_id LEFT JOIN users u ON u.id=a.author_id "
        f"WHERE {where} AND {cond} ORDER BY a.updated_at DESC, a.id DESC LIMIT ? OFFSET ?", args + [limit + 1, offset]).fetchall()
    items = []
    for r in rows[:limit]:
        d = dict(r)
        d["ink"] = _thin_ink(d["ink"]) if d["kind"] == "ink" else None
        items.append(d)
    notebooks = []
    if offset == 0 and kind in ("all", "notebook"):
        nb_where = "a.author_id=? AND a.nb=1" + (" AND a.paper_id=?" if paper else "")
        nb_args: list[Any] = [me] + ([paper] if paper else [])
        pids = {r["paper_id"]: r for r in con.execute(
            f"SELECT a.paper_id, MAX(a.updated_at) t, COUNT(*) n, MAX(a.page) mx FROM annotations a WHERE {nb_where} GROUP BY a.paper_id", nb_args)}
        for r in con.execute("SELECT paper_id, pages, updated_at FROM notebooks WHERE user_id=?" + (" AND paper_id=?" if paper else ""), nb_args):
            if r["paper_id"] not in pids:
                pids[r["paper_id"]] = {"paper_id": r["paper_id"], "t": r["updated_at"], "n": 0, "mx": r["pages"]}
        for pid, r in pids.items():
            p = con.execute("SELECT title, citekey, year FROM papers WHERE id=?", (pid,)).fetchone()
            if p is None:
                continue
            if q.strip() and q.strip().lower() not in f"{p['title']} {p['citekey']}".lower():
                continue
            meta = con.execute("SELECT pages, bg FROM notebooks WHERE paper_id=? AND user_id=?", (pid, me)).fetchone()
            first_page = con.execute("SELECT MIN(page) m FROM annotations WHERE paper_id=? AND author_id=? AND nb=1", (pid, me)).fetchone()["m"] or 1
            prev = {"strokes": []}
            for f in con.execute("SELECT ink FROM annotations WHERE paper_id=? AND author_id=? AND nb=1 AND page=? ORDER BY id LIMIT 5", (pid, me, first_page)):
                t = _thin_ink(f["ink"], 120, 40)
                if t:
                    prev["strokes"] += t["strokes"]
            notebooks.append({"paper_id": pid, "title": p["title"], "citekey": p["citekey"], "year": p["year"],
                              "pages": max(meta["pages"] if meta else 1, r["mx"] or 1), "bg": meta["bg"] if meta else "lined",
                              "updated_at": r["t"], "n_blocks": r["n"], "preview": prev, "preview_page": first_page})
        notebooks.sort(key=lambda x: x["updated_at"] or "", reverse=True)
    return {"items": items, "more": len(rows) > limit, "counts": counts, "notebooks": notebooks}
