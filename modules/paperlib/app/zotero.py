"""Zotero 同步（Zotero Web API v3）。

- 推送：論文庫 → Zotero。書目、標籤；分類對應成子收藏夾；重點欄＋公開標註寫成子筆記；
  有設定網站網址時，另加一個「在論文庫開啟」連結。之後每次同步會更新同一個項目，不會重複建立。
- 匯入（可關閉）：Zotero 裡新增、論文庫還沒有的項目 → 進「未歸檔待讀」，有 PDF 附件會一起下載。
"""
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from html import escape

from . import config, db, library, pdftools, settings
from .jobs import handler

API = os.environ.get("PAPERLIB_ZOTERO_API", "https://api.zotero.org")
PARTICLES = {"van", "von", "de", "der", "den", "da", "di", "del", "la", "le", "du", "dos"}
TYPE = {"article": "journalArticle", "review": "journalArticle", "preprint": "preprint", "book": "book",
        "thesis": "thesis", "web": "webpage", "other": "document"}
KIND = {"journalArticle": "article", "preprint": "preprint", "book": "book", "bookSection": "book", "thesis": "thesis",
        "webpage": "web", "conferencePaper": "article", "report": "other", "document": "other"}


class Z:
    def __init__(self, con):
        s = settings.get(con)
        if not (s["zotero_id"] and s["zotero_key"]):
            raise RuntimeError("請先在「管理 → Zotero」填入 Library ID 與 API 金鑰")
        self.s = s
        self.base = f"{API}/{'groups' if s['zotero_type'] == 'group' else 'users'}/{s['zotero_id']}"
        self.last_version = None

    def req(self, method, path, body=None, headers=None, raw=False):
        h = {"Zotero-API-Key": self.s["zotero_key"], "Zotero-API-Version": "3", "User-Agent": "PaperLib/1.0"}
        if body is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        url = path if path.startswith("http") else self.base + path
        rq = urllib.request.Request(url, method=method, headers=h,
                                    data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(rq, timeout=90) as r:
                self.last_version = r.headers.get("Last-Modified-Version") or self.last_version
                self.total = r.headers.get("Total-Results")
                data = r.read()
                return data if raw else (json.loads(data) if data else None)
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:300]
            if e.code == 403:
                raise RuntimeError("Zotero 拒絕存取：請確認 API 金鑰有這個 library 的讀寫權限") from None
            raise RuntimeError(f"Zotero 回應 {e.code}：{msg}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"連不到 Zotero（{e.reason}）") from None

    def write(self, objs: list[dict], path="/items") -> list[dict]:
        """批次建立／更新，每次最多 50 筆。回傳每筆 {ok, key, version, code, message}。"""
        out = []
        for i in range(0, len(objs), 50):
            chunk = objs[i:i + 50]
            r = self.req("POST", path, chunk) or {}
            succ, fail, unch = r.get("successful", {}), r.get("failed", {}), r.get("unchanged", {})
            for j, o in enumerate(chunk):
                k = str(j)
                if k in succ:
                    out.append({"ok": True, "key": succ[k]["key"], "version": succ[k].get("version")})
                elif k in unch:
                    out.append({"ok": True, "key": unch[k] if isinstance(unch[k], str) else o.get("key"), "version": o.get("version")})
                else:
                    f = fail.get(k, {})
                    out.append({"ok": False, "key": f.get("key") or o.get("key"), "code": f.get("code"), "message": f.get("message", "")})
        return out


def split_name(n: str) -> dict:
    parts = n.strip().split()
    if len(parts) < 2:
        return {"creatorType": "author", "name": n.strip()}
    i = len(parts) - 1
    while i > 1 and parts[i - 1].lower() in PARTICLES:
        i -= 1
    return {"creatorType": "author", "firstName": " ".join(parts[:i]), "lastName": " ".join(parts[i:])}


def join_name(c: dict) -> str:
    return c.get("name") or " ".join(x for x in (c.get("firstName"), c.get("lastName")) if x)


def ensure_collections(z: Z, con) -> dict:
    s = z.s
    cats = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM categories")}
    existing, start = [], 0
    while True:
        page = z.req("GET", f"/collections?limit=100&start={start}") or []
        existing += page
        if len(page) < 100:
            break
        start += 100
    by = {(c["data"].get("parentCollection") or None, c["data"]["name"]): c["key"] for c in existing}
    root = by.get((None, s["zotero_collection"]))
    if not root:
        root = z.write([{"name": s["zotero_collection"]}], "/collections")[0]["key"]
    m = {"root": root}
    need = [(cid, n) for cid, n in cats.items() if (root, n) not in by]
    for cid, n in cats.items():
        if (root, n) in by:
            m[cid] = by[(root, n)]
    if need:
        res = z.write([{"name": n, "parentCollection": root} for _, n in need], "/collections")
        for (cid, _), r in zip(need, res):
            if r["ok"]:
                m[cid] = r["key"]
    return m


def item_json(con, p: dict, colls: dict) -> dict:
    t = TYPE.get(p["kind"], "journalArticle")
    authors = json.loads(p["authors"] or "[]")
    cats = [r["category_id"] for r in con.execute("SELECT category_id FROM paper_categories WHERE paper_id=?", (p["id"],))]
    tags = [r["name"] for r in con.execute("SELECT t.name FROM paper_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.paper_id=?", (p["id"],))]
    extra = [f"Citation Key: {p['citekey']}"]
    it = {"itemType": t, "title": p["title"], "creators": [split_name(a) for a in authors if a.strip()],
          "date": str(p["year"] or ""), "abstractNote": p["abstract"] or "",
          "tags": [{"tag": x} for x in tags] + ([{"tag": "未歸檔待讀"}] if not cats else []),
          "collections": [colls[c] for c in cats if c in colls] + [colls["root"]]}
    if t == "journalArticle":
        it["publicationTitle"] = p["venue"] or ""
        it["DOI"] = p["doi"] or ""
    elif t == "preprint":
        it["DOI"] = p["doi"] or ""
        if p["arxiv"]:
            it["repository"] = "arXiv"
            it["archiveID"] = f"arXiv:{p['arxiv']}"
            it["url"] = f"https://arxiv.org/abs/{p['arxiv']}"
    else:
        if p["venue"]:
            extra.append(f"Venue: {p['venue']}")
        if p["doi"]:
            extra.append(f"DOI: {p['doi']}")
    if p["arxiv"] and t != "preprint":
        extra.append(f"arXiv: {p['arxiv']}")
    it["extra"] = "\n".join(extra)
    return it


def note_html(con, p: dict) -> str:
    ki = json.loads(p["keyinfo"] or "{}")
    h = [f"<h1>{escape(settings.get(con)['site_name'])}：{escape(p['citekey'])}</h1>"]
    if ki:
        h.append("<h2>重點欄</h2>" + "".join(f"<p><b>{escape(k)}</b>：{escape(v)}</p>" for k, v in ki.items()))
    anns = con.execute("SELECT a.page, a.quote, a.body, u.display_name AS who FROM annotations a LEFT JOIN users u ON u.id=a.author_id "
                       "WHERE a.paper_id=? AND a.private=0 ORDER BY a.page IS NULL, a.page, a.id", (p["id"],)).fetchall()
    if anns:
        h.append("<h2>標註</h2>")
        for a in anns:
            h.append(f"<p><i>{'p.' + str(a['page']) if a['page'] else '整篇'} · {escape(a['who'] or '')}</i></p>")
            if a["quote"]:
                h.append(f"<blockquote>{escape(a['quote'])}</blockquote>")
            if a["body"]:
                h.append(f"<p>{escape(a['body'])}</p>")
    return "".join(h)


def push(z: Z, con, progress) -> dict:
    colls = ensure_collections(z, con)
    papers = [dict(r) for r in con.execute("SELECT * FROM papers ORDER BY id")]
    mp = {r["paper_id"]: dict(r) for r in con.execute("SELECT * FROM zotero_map")}
    objs = []
    for p in papers:
        o = item_json(con, p, colls)
        m = mp.get(p["id"])
        if m:
            o["key"], o["version"] = m["item_key"], m["item_version"]
        objs.append(o)
    progress(f"推送 {len(objs)} 篇書目")
    res = z.write(objs)
    created = updated = failed = 0
    retry = []
    for p, o, r in zip(papers, objs, res):
        if r["ok"]:
            if p["id"] in mp:
                updated += 1
                con.execute("UPDATE zotero_map SET item_version=?, synced_at=? WHERE paper_id=?", (r["version"], db.now(), p["id"]))
            else:
                created += 1
                con.execute("INSERT OR REPLACE INTO zotero_map(paper_id, item_key, item_version, synced_at) VALUES(?,?,?,?)",
                            (p["id"], r["key"], r["version"], db.now()))
        elif r.get("code") == 412:
            retry.append((p, o))
        elif r.get("code") == 404 or "not found" in (r.get("message") or "").lower():
            con.execute("DELETE FROM zotero_map WHERE paper_id=?", (p["id"],))   # 在 Zotero 被刪了：下次重新建立
            failed += 1
        else:
            failed += 1
    for p, o in retry:   # 版本衝突：以 Zotero 目前版本為基準，覆寫我們管理的欄位
        try:
            cur = z.req("GET", f"/items/{o['key']}")
            o["version"] = cur["version"]
            r = z.write([o])[0]
            if r["ok"]:
                updated += 1
                con.execute("UPDATE zotero_map SET item_version=?, synced_at=? WHERE paper_id=?", (r["version"], db.now(), p["id"]))
            else:
                failed += 1
        except RuntimeError:
            failed += 1
    # 子筆記與連結
    mp = {r["paper_id"]: dict(r) for r in con.execute("SELECT * FROM zotero_map")}
    notes, owners, links, link_owners = [], [], [], []
    for p in papers:
        m = mp.get(p["id"])
        if not m:
            continue
        n = {"itemType": "note", "parentItem": m["item_key"], "note": note_html(con, p)}
        if m["note_key"]:
            n["key"], n["version"] = m["note_key"], m["note_version"]
        notes.append(n)
        owners.append(p["id"])
        url = settings.link(f"#/p/{p['id']}", con)
        if url and not m["link_key"]:
            links.append({"itemType": "attachment", "parentItem": m["item_key"], "linkMode": "linked_url",
                          "title": f"在 {z.s['site_name']} 開啟", "url": url, "tags": []})
            link_owners.append(p["id"])
    progress(f"推送 {len(notes)} 則筆記")
    for pid, r in zip(owners, z.write(notes) if notes else []):
        if r["ok"]:
            con.execute("UPDATE zotero_map SET note_key=?, note_version=? WHERE paper_id=?", (r["key"], r["version"], pid))
        elif r.get("code") in (404, 412):
            con.execute("UPDATE zotero_map SET note_key=NULL, note_version=NULL WHERE paper_id=?", (pid,))
    for pid, r in zip(link_owners, z.write(links) if links else []):
        if r["ok"]:
            con.execute("UPDATE zotero_map SET link_key=? WHERE paper_id=?", (r["key"], pid))
    return {"created": created, "updated": updated, "failed": failed}


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def pull(z: Z, con, progress) -> dict:
    since = db.meta_get(con, "zotero_since", "0") or "0"
    mapped = {r["item_key"] for r in con.execute("SELECT item_key FROM zotero_map")}
    titles = {_norm(r["title"]): r["id"] for r in con.execute("SELECT id, title FROM papers")}
    items, start = [], 0
    while True:
        page = z.req("GET", f"/items/top?format=json&limit=100&start={start}&since={since}") or []
        items += page
        if len(page) < 100:
            break
        start += 100
    lib_version = z.last_version
    imported = matched = pdfs = 0
    for it in items:
        d = it.get("data", {})
        if it["key"] in mapped or d.get("itemType") in ("note", "attachment", "annotation"):
            continue
        doi = (d.get("DOI") or "").strip()
        m = con.execute("SELECT id FROM papers WHERE lower(doi)=lower(?)", (doi,)).fetchone() if doi else None
        pid = m["id"] if m else titles.get(_norm(d.get("title")))
        if pid:
            if not con.execute("SELECT 1 FROM zotero_map WHERE paper_id=?", (pid,)).fetchone():
                con.execute("INSERT INTO zotero_map(paper_id, item_key, item_version, synced_at) VALUES(?,?,?,?)",
                            (pid, it["key"], it["version"], db.now()))
            matched += 1
            continue
        year = re.search(r"(1[89]|20)\d{2}", d.get("date") or "")
        arx = re.search(r"(\d{4}\.\d{4,5})", (d.get("archiveID") or "") + " " + (d.get("url") or "") + " " + (d.get("extra") or ""))
        with db.tx() as c:
            pid = library.create_paper(c, {
                "title": d.get("title") or "（未命名）", "authors": [join_name(x) for x in d.get("creators", []) if join_name(x)],
                "authors_complete": True, "year": int(year.group(0)) if year else None,
                "venue": d.get("publicationTitle") or d.get("bookTitle") or d.get("proceedingsTitle") or "",
                "doi": doi if not (doi and c.execute("SELECT 1 FROM papers WHERE lower(doi)=lower(?)", (doi,)).fetchone()) else "",
                "arxiv": arx.group(1) if arx else "", "kind": KIND.get(d.get("itemType"), "other"),
                "abstract": d.get("abstractNote") or "", "status": "待讀"}, None)
            db.set_tags(c, pid, [t["tag"] for t in d.get("tags", []) if t.get("tag")] + ["Zotero 匯入"])
            c.execute("INSERT INTO zotero_map(paper_id, item_key, item_version, synced_at) VALUES(?,?,?,?)",
                      (pid, it["key"], it["version"], db.now()))
            db.fts_update(c, pid)
            db.log(c, None, "zotero", pid, d.get("title", "")[:80])
        imported += 1
        titles[_norm(d.get("title"))] = pid
        progress(f"匯入 {imported} 篇")
        # PDF 附件
        try:
            kids = z.req("GET", f"/items/{it['key']}/children") or []
            att = next((k for k in kids if k["data"].get("contentType") == "application/pdf"
                        and k["data"].get("linkMode") in ("imported_file", "imported_url")), None)
            if att:
                tmp = config.STAGING_DIR / f"zot-{secrets.token_hex(8)}.pdf"
                tmp.write_bytes(z.req("GET", f"/items/{att['key']}/file", raw=True))
                if pdftools.is_pdf(tmp):
                    with db.tx() as c:
                        library.add_file(c, pid, tmp, att["data"].get("filename") or "zotero.pdf", "main", "", None, move=True)
                    pdfs += 1
                tmp.unlink(missing_ok=True)
        except (RuntimeError, ValueError):
            pass
    if lib_version:
        db.meta_set(con, "zotero_since", str(lib_version))
    return {"imported": imported, "matched": matched, "pdfs": pdfs}


def sync(con, progress=lambda m: None) -> str:
    z = Z(con)
    s = z.s
    pl = pull(z, con, progress) if s["zotero_import"] == "1" else None
    ps = push(z, con, progress)
    # 推送後 library 版本變新；記下來，避免下次把自己推上去的項目當成新項目
    if z.last_version:
        db.meta_set(con, "zotero_since", str(z.last_version))
    msg = f"推送：新建 {ps['created']}、更新 {ps['updated']}" + (f"、失敗 {ps['failed']}" if ps["failed"] else "")
    if pl:
        msg += f"；匯入：新增 {pl['imported']} 篇（含 PDF {pl['pdfs']}）、對應既有 {pl['matched']} 篇"
    return msg


@handler("zotero")
def _job(con, arg, progress):
    r = sync(con, progress)
    from .jobs import enqueue
    enqueue("refs", "")
    return r


def test(con) -> str:
    z = Z(con)
    info = z.req("GET", f"{API}/keys/current") or {}
    lib = z.req("GET", "/collections/top?limit=1")
    return f"連線成功（金鑰使用者：{info.get('username', '?')}）" if lib is not None else "連線成功"
