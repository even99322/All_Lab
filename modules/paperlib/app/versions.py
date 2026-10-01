"""預印本與正式版：
1. 定期檢查只有 arXiv 編號的論文是否已正式發表（arXiv 記錄的 DOI，或 Crossref 用標題＋作者比對），
   找到就更新 DOI、期刊、年份（可在設定關掉自動更新，改成只提示）。
2. 找出論文庫裡重複的論文（同一篇的預印本與正式版、同一 DOI／arXiv、標題幾乎相同），
   合併時保留一篇為主，另一篇的 PDF 變成「其他版本」，標註、圖卡、參數、關聯、分類、標籤、閱讀狀態、指派都搬過來。
"""
import difflib
import json
import os
import re
import shutil
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime

from . import config, db, enrich, settings
from .jobs import handler

ARXIV_API = os.environ.get("PAPERLIB_ARXIV_API", "https://export.arxiv.org/api/query")
CROSSREF_API = enrich.CROSSREF_API
NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
STATUS_RANK = {None: 0, "待讀": 1, "閱讀中": 2, "已閱讀": 3}


def norm_title(t: str) -> str:
    t = re.sub(r"<[^>]+>|\$[^$]*\$", " ", (t or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def title_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm_title(a), norm_title(b)).ratio()


def _surname(authors) -> str:
    a = json.loads(authors) if isinstance(authors, str) else (authors or [])
    if not a:
        return ""
    s = a[0].strip()
    s = s.split(",")[0] if "," in s else s.split()[-1]
    return re.sub(r"[^a-z]", "", s.lower())


def _bare_arxiv(aid: str) -> str:
    return re.sub(r"v\d+$", "", (aid or "").strip().lower().replace("arxiv:", ""))


# ------------------------------------------------------------------ 查詢外部服務
def arxiv_meta(ids: list[str]) -> dict:
    """{arxiv_id: {doi, journal_ref}}"""
    url = ARXIV_API + "?" + urllib.parse.urlencode({"id_list": ",".join(ids), "max_results": len(ids)})
    root = ET.fromstring(enrich._get(url, timeout=30))
    out = {}
    for e in root.findall("a:entry", NS):
        aid = _bare_arxiv((e.findtext("a:id", "", NS) or "").rsplit("/abs/", 1)[-1])
        out[aid] = {"doi": (e.findtext("arxiv:doi", "", NS) or "").strip(), "journal_ref": (e.findtext("arxiv:journal_ref", "", NS) or "").strip()}
    return out


def crossref_find(title: str, surname: str) -> dict | None:
    q = {"query.bibliographic": title[:300], "rows": 5, "filter": "type:journal-article"}
    if surname:
        q["query.author"] = surname
    data = json.loads(enrich._get(f"{CROSSREF_API}/works?" + urllib.parse.urlencode(q), timeout=20))
    best = None
    for it in data.get("message", {}).get("items", []):
        t = (it.get("title") or [""])[0]
        r = title_ratio(title, t)
        fam = [re.sub(r"[^a-z]", "", (a.get("family") or "").lower()) for a in it.get("author", [])]
        if surname and surname not in fam:
            continue
        if best is None or r > best["score"]:
            best = {"doi": it.get("DOI", ""), "title": t, "score": r}
    return best if best and best["score"] >= 0.9 else None


def _pub_info(doi: str, journal_ref: str = "") -> dict:
    try:
        c = enrich.crossref(doi)
        return {"venue": c.get("venue") or journal_ref, "year": c.get("year"), "title": c.get("title", "")}
    except Exception:  # noqa: BLE001 - Crossref 連不到時用 arXiv 的 journal_ref
        m = re.search(r"(19|20)\d{2}", journal_ref or "")
        return {"venue": journal_ref, "year": int(m.group(0)) if m else None, "title": ""}


# ------------------------------------------------------------------ 已發表？
def apply_published(con, pid: int, doi: str, venue: str, year, source: str, user_id=None) -> None:
    p = con.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone()
    sets = {"doi": doi, "updated_at": db.now()}
    if venue:
        sets["venue"] = venue
    if year:
        sets["year"] = int(year)
    if p["kind"] == "preprint":
        sets["kind"] = "article"
    con.execute(f"UPDATE papers SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), pid))
    db.fts_update(con, pid)
    db.log(con, user_id, "published", pid, f"{venue}（{source}）")


def _dup_exists(con, a: int, b: int) -> bool:
    lo, hi = min(a, b), max(a, b)
    return con.execute("SELECT 1 FROM version_suggest WHERE kind='duplicate' AND ((paper_id=? AND other_id=?) OR (paper_id=? AND other_id=?))",
                       (lo, hi, hi, lo)).fetchone() is not None


def _add_dup(con, a: int, b: int, source: str, score: float) -> bool:
    if a == b or _dup_exists(con, a, b):
        return False
    con.execute("INSERT INTO version_suggest(kind, paper_id, other_id, source, score, created_at) VALUES('duplicate',?,?,?,?,?)",
                (min(a, b), max(a, b), source, score, db.now()))
    return True


def check_published(con, progress=lambda m: None, only: int | None = None) -> str:
    s = settings.get(con)
    auto = s["pub_auto"] == "1"
    q = "SELECT id, title, authors, arxiv, doi, kind FROM papers WHERE arxiv!='' AND (doi='' OR kind='preprint')"
    rows = [dict(r) for r in con.execute(q + (" AND id=?" if only else ""), (only,) if only else ())]
    applied = suggested = dups = 0
    errs = []
    meta = {}
    ids = [_bare_arxiv(r["arxiv"]) for r in rows]
    for i in range(0, len(ids), 20):
        progress(f"查 arXiv {i + 1}–{min(i + 20, len(ids))}／{len(ids)}")
        try:
            meta.update(arxiv_meta(ids[i:i + 20]))
        except Exception as e:  # noqa: BLE001
            errs.append(f"arXiv：{e}")
            break
        if i + 20 < len(ids):
            time.sleep(3)          # arXiv 要求每次查詢間隔 3 秒
    for n, r in enumerate(rows):
        progress(f"比對 {n + 1}／{len(rows)}")
        m = meta.get(_bare_arxiv(r["arxiv"]), {})
        doi, source, score, journal_ref = m.get("doi", ""), "arXiv 記錄", 1.0, m.get("journal_ref", "")
        if not doi and not r["doi"]:
            try:
                found = crossref_find(r["title"], _surname(r["authors"]))
                time.sleep(0.5)
            except Exception as e:  # noqa: BLE001
                found = None
                if len(errs) < 3:
                    errs.append(f"Crossref：{e}")
            if found:
                doi, source, score = found["doi"], "Crossref 標題比對", found["score"]
        if not doi and r["doi"]:
            doi = r["doi"]
        if not doi:
            continue
        other = con.execute("SELECT id FROM papers WHERE lower(doi)=lower(?) AND id!=?", (doi, r["id"])).fetchone()
        if other:              # 正式版已經在論文庫裡：是重複
            dups += _add_dup(con, r["id"], other["id"], f"arXiv 版與正式版（{doi}）", 1.0)
            continue
        if r["doi"] and r["kind"] != "preprint":
            continue
        if con.execute("SELECT 1 FROM version_suggest WHERE kind='published' AND paper_id=? AND doi=?", (r["id"], doi)).fetchone():
            continue
        info = _pub_info(doi, journal_ref)
        if info["venue"] and re.search(r"arxiv", info["venue"], re.I):
            continue
        confident = source == "arXiv 記錄" or score >= 0.97
        status = "applied" if auto and confident else "new"
        con.execute("INSERT INTO version_suggest(kind, paper_id, doi, venue, year, title, source, score, status, created_at, resolved_at) "
                    "VALUES('published',?,?,?,?,?,?,?,?,?,?)", (r["id"], doi, info["venue"] or "", info["year"], info["title"] or "",
                                                               source, score, status, db.now(), db.now() if status == "applied" else None))
        if status == "applied":
            apply_published(con, r["id"], doi, info["venue"], info["year"], source)
            applied += 1
        else:
            suggested += 1
    msg = f"檢查 {len(rows)} 篇預印本：自動更新 {applied} 篇、待確認 {suggested} 篇、發現重複 {dups} 組"
    return msg + (f"（{'；'.join(errs[:2])}）" if errs else "")


# ------------------------------------------------------------------ 重複論文
def find_duplicates(con) -> int:
    papers = [dict(r) for r in con.execute("SELECT id, title, authors, year, doi, arxiv FROM papers")]
    n = 0
    by = {}
    for p in papers:
        for key in ([f"doi:{p['doi'].lower()}"] if p["doi"] else []) + ([f"arx:{_bare_arxiv(p['arxiv'])}"] if p["arxiv"] else []):
            by.setdefault(key, []).append(p["id"])
    for key, ids in by.items():
        for other in ids[1:]:
            n += _add_dup(con, ids[0], other, "相同 DOI" if key.startswith("doi") else "相同 arXiv 編號", 1.0)
    buckets = {}
    for p in papers:
        buckets.setdefault(_surname(p["authors"]) or "?", []).append(p)
    for group in buckets.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a["year"] and b["year"] and abs(a["year"] - b["year"]) > 3:
                    continue
                if a["doi"] and b["doi"] and a["doi"].lower() != b["doi"].lower():
                    continue          # 兩篇都有不同 DOI：通常是不同論文（例如 PRL 與後續 PRB）
                r = title_ratio(a["title"], b["title"])
                if r >= 0.9:
                    n += _add_dup(con, a["id"], b["id"], "標題幾乎相同", round(r, 3))
    return n


def pending(con) -> dict:
    def card(pid):
        p = con.execute("SELECT id, citekey, title, year, venue, doi, arxiv, kind, added_at FROM papers WHERE id=?", (pid,)).fetchone()
        if not p:
            return None
        d = dict(p)
        d["n_files"] = con.execute("SELECT COUNT(*) FROM files WHERE paper_id=?", (pid,)).fetchone()[0]
        d["n_ann"] = con.execute("SELECT COUNT(*) FROM annotations WHERE paper_id=?", (pid,)).fetchone()[0]
        return d
    dup = []
    for r in con.execute("SELECT * FROM version_suggest WHERE kind='duplicate' AND status='new' ORDER BY id DESC"):
        a, b = card(r["paper_id"]), card(r["other_id"])
        if a and b:
            dup.append(dict(r) | {"a": a, "b": b, "keep": suggest_keep(a, b)})
    pub = [dict(r) | {"paper": card(r["paper_id"])} for r in con.execute(
        "SELECT * FROM version_suggest WHERE kind='published' AND status='new' ORDER BY id DESC")]
    recent = [dict(r) | {"paper": card(r["paper_id"])} for r in con.execute(
        "SELECT * FROM version_suggest WHERE kind='published' AND status='applied' ORDER BY resolved_at DESC LIMIT 20")]
    return {"duplicates": dup, "published": [x for x in pub if x["paper"]], "applied": [x for x in recent if x["paper"]]}


def suggest_keep(a: dict, b: dict) -> int:
    """優先保留正式版（有 DOI、不是預印本），其次標註多的。"""
    def score(p):
        return (bool(p["doi"]) and p["kind"] != "preprint", p["n_ann"], p["n_files"], -p["id"])
    return a["id"] if score(a) >= score(b) else b["id"]


def merge(con, keep: int, drop: int, user) -> dict:
    if keep == drop:
        raise ValueError("不能和自己合併")
    k = con.execute("SELECT * FROM papers WHERE id=?", (keep,)).fetchone()
    d = con.execute("SELECT * FROM papers WHERE id=?", (drop,)).fetchone()
    if not k or not d:
        raise ValueError("找不到論文")
    k, d = dict(k), dict(d)
    # 備份被合併那篇的資料
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = config.TRASH_DIR / f"{stamp}-merged-{drop}-into-{keep}"
    bak.mkdir(parents=True, exist_ok=True)
    (bak / "paper.json").write_text(json.dumps({"paper": d, "merged_into": keep}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    # 檔案：搬到保留那篇的資料夾；被合併那篇的正文變成「其他版本」
    has_main = con.execute("SELECT 1 FROM files WHERE paper_id=? AND role='main'", (keep,)).fetchone() is not None
    folder = config.FILES_DIR / str(keep)
    folder.mkdir(parents=True, exist_ok=True)
    moved = 0
    for f in con.execute("SELECT * FROM files WHERE paper_id=?", (drop,)).fetchall():
        src = (config.DATA_DIR / f["path"]).resolve()
        name = f["filename"]
        i = 1
        while (folder / name).exists():
            i += 1
            name = f"{os.path.splitext(f['filename'])[0]}_{i}.pdf"
        if src.exists():
            shutil.move(str(src), folder / name)
        role, label = f["role"], f["label"]
        if role == "main" and has_main:
            role = "version"
            label = label or ("arXiv 版" if d["kind"] == "preprint" or d["arxiv"] else "合併的版本")
        elif role == "main":
            has_main = True
        con.execute("UPDATE files SET paper_id=?, filename=?, path=?, role=?, label=? WHERE id=?",
                    (keep, name, str((folder / name).relative_to(config.DATA_DIR)), role, label, f["id"]))
        moved += 1
    old = config.FILES_DIR / str(drop)
    if old.exists() and not any(old.iterdir()):
        old.rmdir()
    # 一對多的資料直接改指向
    for t in ("annotations", "figures", "meeting_items", "path_items", "feed_items", "notifications", "activity"):
        con.execute(f"UPDATE {t} SET paper_id=? WHERE paper_id=?", (keep, drop))
    con.execute("UPDATE ai_log SET scope=? WHERE scope=?", (f"paper:{keep}", f"paper:{drop}"))
    # 多對多：沒有的才加
    con.execute("INSERT OR IGNORE INTO paper_categories(paper_id, category_id, folder_id, pinned) "
                "SELECT ?, category_id, folder_id, pinned FROM paper_categories WHERE paper_id=?", (keep, drop))
    con.execute("INSERT OR IGNORE INTO paper_tags(paper_id, tag_id) SELECT ?, tag_id FROM paper_tags WHERE paper_id=?", (keep, drop))
    con.execute("INSERT OR IGNORE INTO paper_assign(paper_id, user_id, assigned_by, note, created_at) "
                "SELECT ?, user_id, assigned_by, note, created_at FROM paper_assign WHERE paper_id=?", (keep, drop))
    con.execute("INSERT OR IGNORE INTO paper_params(paper_id, key, value, num, raw, note, page, source, author_id, updated_at) "
                "SELECT ?, key, value, num, raw, note, page, source, author_id, updated_at FROM paper_params WHERE paper_id=?", (keep, drop))
    con.execute("INSERT OR IGNORE INTO paper_views(paper_id, user_id, day) SELECT ?, user_id, day FROM paper_views WHERE paper_id=?", (keep, drop))
    for r in con.execute("SELECT user_id, status FROM user_paper WHERE paper_id=?", (drop,)).fetchall():
        cur = db.get_status(con, r["user_id"], keep)
        if STATUS_RANK.get(r["status"], 0) > STATUS_RANK.get(cur, 0):
            db.set_status(con, r["user_id"], keep, r["status"])
    for l in con.execute("SELECT * FROM links WHERE src_id=? OR dst_id=?", (drop, drop)).fetchall():
        s = keep if l["src_id"] == drop else l["src_id"]
        t = keep if l["dst_id"] == drop else l["dst_id"]
        if s != t:
            con.execute("INSERT OR IGNORE INTO links(src_id, dst_id, rel, note, author_id, created_at, auto) VALUES(?,?,?,?,?,?,?)",
                        (s, t, l["rel"], l["note"], l["author_id"], l["created_at"], l["auto"]))
    if not con.execute("SELECT 1 FROM zotero_map WHERE paper_id=?", (keep,)).fetchone():
        con.execute("UPDATE zotero_map SET paper_id=? WHERE paper_id=?", (keep, drop))
    # 書目與重點欄：保留那篇為主，空的欄位用另一篇補
    ki, kd = json.loads(k["keyinfo"] or "{}"), json.loads(d["keyinfo"] or "{}")
    ai_k, ai_d = set(json.loads(k.get("ai_keys") or "[]")), set(json.loads(d.get("ai_keys") or "[]"))
    for key, v in kd.items():
        if str(v).strip() and not str(ki.get(key, "")).strip():
            ki[key] = v
            if key in ai_d:
                ai_k.add(key)
    body_k = (con.execute("SELECT body FROM fts WHERE paper_id=?", (keep,)).fetchone() or {"body": ""})["body"] or ""
    body_d = (con.execute("SELECT body FROM fts WHERE paper_id=?", (drop,)).fetchone() or {"body": ""})["body"] or ""
    con.execute("UPDATE papers SET doi='', arxiv='' WHERE id=?", (drop,))
    con.execute("UPDATE papers SET keyinfo=?, ai_keys=?, doi=?, arxiv=?, abstract=?, required=?, pinned=?, required_note=?, updated_at=? WHERE id=?",
                (json.dumps(ki, ensure_ascii=False), json.dumps(sorted(ai_k), ensure_ascii=False), k["doi"] or d["doi"], k["arxiv"] or d["arxiv"],
                 k["abstract"] or d["abstract"], max(k["required"], d["required"]), max(k["pinned"], d["pinned"]),
                 k["required_note"] or d["required_note"], db.now(), keep))
    con.execute("DELETE FROM papers WHERE id=?", (drop,))
    con.execute("DELETE FROM fts WHERE paper_id=?", (drop,))
    db.fts_update(con, keep, body_k or body_d)
    con.execute("UPDATE version_suggest SET status='applied', resolved_at=?, resolved_by=? WHERE kind='duplicate' AND "
                "((paper_id=? AND other_id=?) OR (paper_id=? AND other_id=?))", (db.now(), user["id"], min(keep, drop), max(keep, drop),
                                                                              max(keep, drop), min(keep, drop)))
    db.log(con, user["id"], "merge", keep, f"合併了「{d['title'][:80]}」（{d['citekey']}）")
    return {"kept": keep, "files_moved": moved, "backup": str(bak.relative_to(config.DATA_DIR))}


@handler("versions")
def _job(con, arg, progress):
    s = settings.get(con)
    msgs = []
    if s["pub_check"] == "1" or arg == "force":
        msgs.append(check_published(con, progress))
    progress("找重複的論文")
    msgs.append(f"新發現可能重複 {find_duplicates(con)} 組")
    return "；".join(msgs)
