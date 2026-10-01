"""新論文追蹤：arXiv 關鍵字查詢、期刊 RSS。每天自動檢查一次，新論文先進「新論文追蹤」清單，
使用者按「加入論文庫」才會建立論文（arXiv 會一併下載 PDF）。"""
import json
import os
import re
import secrets
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from . import config, db, enrich, library, pdftools
from .jobs import handler

ARXIV_API = os.environ.get("PAPERLIB_ARXIV_API", "https://export.arxiv.org/api/query")
UA = enrich.UA
NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom",
      "rss": "http://purl.org/rss/1.0/", "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
      "dc": "http://purl.org/dc/elements/1.1/", "prism": "http://prismstandard.org/namespaces/basic/2.0/",
      "content": "http://purl.org/rss/1.0/modules/content/"}
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+")
# 期刊 RSS 範例（若網址失效，請到期刊網站找「RSS」連結）
RSS_PRESETS = {
    "Phys. Rev. Lett.": "https://feeds.aps.org/rss/recent/prl.xml",
    "Phys. Rev. B": "https://feeds.aps.org/rss/recent/prb.xml",
    "Phys. Rev. A": "https://feeds.aps.org/rss/recent/pra.xml",
    "Phys. Rev. Applied": "https://feeds.aps.org/rss/recent/prapplied.xml",
    "PRX Quantum": "https://feeds.aps.org/rss/recent/prxquantum.xml",
    "Nature Physics": "https://www.nature.com/nphys.rss",
    "Nature Communications": "https://www.nature.com/ncomms.rss",
    "npj Quantum Information": "https://www.nature.com/npjqi.rss",
}


def _get(url: str, timeout=40) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _txt(s) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    import html
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def split_list(s: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,，;\n]", s or "") if x.strip()]


def arxiv_query(keywords: str, cats: str) -> str:
    kw = split_list(keywords)
    parts = []
    for k in kw:
        q = f'"{k}"' if " " in k else k
        parts.append(f"ti:{q} OR abs:{q}")
    q = "(" + " OR ".join(parts) + ")" if parts else "all:magnon"
    cs = split_list(cats)
    if cs:
        q += " AND (" + " OR ".join(f"cat:{c}" for c in cs) + ")"
    return q


def fetch_arxiv(feed: dict, max_results=60) -> list[dict]:
    url = ARXIV_API + "?" + urllib.parse.urlencode({
        "search_query": arxiv_query(feed["keywords"], feed["categories"]), "sortBy": "submittedDate",
        "sortOrder": "descending", "max_results": max_results})
    root = ET.fromstring(_get(url))
    out = []
    for e in root.findall("a:entry", NS):
        aid_full = (e.findtext("a:id", "", NS) or "").rsplit("/abs/", 1)[-1]
        aid = re.sub(r"v\d+$", "", aid_full)
        if not aid:
            continue
        pdf = next((l.get("href") for l in e.findall("a:link", NS) if l.get("title") == "pdf"), f"https://arxiv.org/pdf/{aid}")
        out.append({"uid": f"arxiv:{aid}", "arxiv": aid, "title": _txt(e.findtext("a:title", "", NS)),
                    "authors": [a.findtext("a:name", "", NS) for a in e.findall("a:author", NS)],
                    "abstract": _txt(e.findtext("a:summary", "", NS)), "published": (e.findtext("a:published", "", NS) or "")[:10],
                    "url": f"https://arxiv.org/abs/{aid}", "pdf_url": pdf, "doi": e.findtext("arxiv:doi", "", NS) or "",
                    "venue": "arXiv"})
    return out


def fetch_rss(feed: dict) -> list[dict]:
    root = ET.fromstring(_get(feed["url"]))
    items = root.findall(".//item") + root.findall(".//rss:item", NS) + root.findall(".//a:entry", NS)
    ftitle = _txt(root.findtext(".//channel/title") or root.findtext(".//rss:channel/rss:title", "", NS)
                  or root.findtext("a:title", "", NS) or "")
    out = []
    for it in items:
        g = lambda *tags: next((it.findtext(t, None, NS) for t in tags if it.findtext(t, None, NS)), "")  # noqa: E731
        title = _txt(g("title", "rss:title", "a:title", "dc:title"))
        link = g("link", "rss:link") or next((l.get("href") for l in it.findall("a:link", NS)), "")
        desc = _txt(g("description", "rss:description", "a:summary", "content:encoded", "a:content"))
        doi = g("prism:doi") or ""
        if not doi:
            m = DOI_RE.search(" ".join([g("dc:identifier"), link or "", g("guid")]))
            doi = m.group(0).rstrip(".") if m else ""
        authors = [_txt(c.text) for c in it.findall("dc:creator", NS) if c.text] or \
                  [a.findtext("a:name", "", NS) for a in it.findall("a:author", NS)]
        if len(authors) == 1 and "," in authors[0]:
            authors = [x.strip() for x in re.split(r",| and ", authors[0]) if x.strip()]
        if not title:
            continue
        out.append({"uid": f"doi:{doi.lower()}" if doi else f"url:{link}", "arxiv": "", "title": title, "authors": authors,
                    "abstract": desc, "published": (g("dc:date", "pubDate", "prism:publicationDate", "a:updated") or "")[:25],
                    "url": link, "pdf_url": "", "doi": doi, "venue": g("prism:publicationName") or ftitle})
    kws = [k.lower() for k in split_list(feed["keywords"])]
    if kws:
        out = [x for x in out if any(k in (x["title"] + " " + x["abstract"]).lower() for k in kws)]
    return out


def check(con, feed_id: int | None = None, progress=lambda m: None) -> str:
    feeds = [dict(r) for r in con.execute("SELECT * FROM feeds WHERE enabled=1" + (" AND id=?" if feed_id else ""),
                                          (feed_id,) if feed_id else ())]
    msgs, total = [], 0
    for i, f in enumerate(feeds):
        progress(f"{f['name']}（{i + 1}/{len(feeds)}）")
        try:
            if f["kind"] == "journal":
                from . import journals
                items = journals.fetch_feed(con, f)
            else:
                items = fetch_arxiv(f) if f["kind"] == "arxiv" else fetch_rss(f)
            err = ""
        except Exception as e:  # noqa: BLE001
            items, err = [], f"{type(e).__name__}: {e}"
        new = 0
        for it in items:
            if con.execute("SELECT 1 FROM feed_items WHERE uid=?", (it["uid"],)).fetchone():
                continue
            inlib = None
            if it["arxiv"]:
                inlib = con.execute("SELECT id FROM papers WHERE arxiv=? OR arxiv LIKE ?", (it["arxiv"], it["arxiv"] + "v%")).fetchone()
            if not inlib and it["doi"]:
                inlib = con.execute("SELECT id FROM papers WHERE lower(doi)=lower(?)", (it["doi"],)).fetchone()
            con.execute("INSERT INTO feed_items(feed_id, uid, title, authors, abstract, published, url, pdf_url, doi, arxiv, venue, "
                        "status, paper_id, found_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (f["id"], it["uid"], it["title"][:500], json.dumps(it["authors"][:60], ensure_ascii=False),
                         it["abstract"][:5000], it["published"], it["url"], it["pdf_url"], it["doi"], it["arxiv"],
                         it["venue"][:200], "inlib" if inlib else "new", inlib["id"] if inlib else None, db.now()))
            new += 0 if inlib else 1
        total += new
        con.execute("UPDATE feeds SET last_run=?, last_error=? WHERE id=?", (db.now(), err[:500], f["id"]))
        msgs.append(f"{f['name']}：{'失敗 ' + err if err else f'新 {new} 篇'}")
        if f["kind"] == "arxiv" and i < len(feeds) - 1:
            time.sleep(3)   # arXiv API 要求查詢間隔
    return "；".join(msgs) or "沒有啟用中的追蹤"


def add_to_library(con, item_id: int, user_id, extra_tags: list[str] | None = None, cat_ids: list[int] | None = None) -> dict:
    it = con.execute("SELECT fi.*, f.tags AS feed_tags FROM feed_items fi LEFT JOIN feeds f ON f.id=fi.feed_id WHERE fi.id=?",
                     (item_id,)).fetchone()
    if it is None:
        raise ValueError("找不到這筆")
    if it["paper_id"]:
        return {"paper_id": it["paper_id"], "pdf": None}
    pdf_path, pdf_err = None, ""
    if it["pdf_url"]:
        pdf_path = config.STAGING_DIR / f"feed-{secrets.token_hex(8)}.pdf"
        try:
            pdf_path.write_bytes(_get(it["pdf_url"], timeout=90))
            if not pdftools.is_pdf(pdf_path):
                raise ValueError("下載的不是 PDF")
        except Exception as e:  # noqa: BLE001
            pdf_err = str(e)
            pdf_path.unlink(missing_ok=True)
            pdf_path = None
    authors = json.loads(it["authors"] or "[]")
    year = int(it["published"][:4]) if (it["published"] or "")[:4].isdigit() else None
    if not year:
        m = re.search(r"(19|20)\d{2}", it["published"] or "")
        year = int(m.group(0)) if m else None
    tags = list(dict.fromkeys(json.loads(it["feed_tags"] or "[]") + (extra_tags or [])))
    with db.tx() as c:
        pid = library.create_paper(c, {
            "title": it["title"], "authors": authors, "authors_complete": bool(authors), "year": year,
            "venue": "" if it["venue"] == "arXiv" else it["venue"], "doi": it["doi"], "arxiv": it["arxiv"],
            "kind": "preprint" if it["arxiv"] and not it["doi"] else "article", "abstract": it["abstract"],
            "status": "待讀"}, user_id)
        if cat_ids:
            db.set_categories(c, pid, cat_ids)
        db.set_tags(c, pid, tags)
        if pdf_path:
            try:
                library.add_file(c, pid, pdf_path, f"{it['arxiv'] or 'paper'}.pdf", "main", "", user_id, move=True)
            except ValueError as e:
                pdf_err = str(e)
                pdf_path.unlink(missing_ok=True)
        db.fts_update(c, pid)
        c.execute("UPDATE feed_items SET status='added', paper_id=? WHERE id=?", (pid, item_id))
        db.log(c, user_id, "feed", pid, it["title"][:80])
    return {"paper_id": pid, "pdf": bool(pdf_path) and not pdf_err, "pdf_error": pdf_err}


@handler("feeds")
def _job(con, arg, progress):
    return check(con, int(arg) if arg else None, progress)
