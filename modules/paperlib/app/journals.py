"""期刊搜尋：在指定期刊（Physical Review 系列、Nature 系列、Science 系列…）裡，依關鍵字與時間範圍找論文。

兩種搜尋引擎：
- OpenAlex（建議）：搜尋標題＋摘要，支援 AND／OR／NOT。需要免費的 API 金鑰（openalex.org 註冊後在設定頁取得）。
- Crossref（免金鑰）：用書目比對找候選，再在本機依標題（有摘要時含摘要）做交集／聯集篩選；Nature、Science 的摘要常常沒有，找得到的會比較少。

關鍵字清單取自論文庫本身：從每篇的標題與摘要抽出單字與兩字片語，依「出現在幾篇論文」排序。
"""
import json
import os
import re
import urllib.parse
from collections import Counter
from datetime import date, timedelta

from . import db, enrich, settings
from .semantic import tokens

OPENALEX_API = os.environ.get("PAPERLIB_OPENALEX_API", "https://api.openalex.org")
CROSSREF_API = enrich.CROSSREF_API

# 期刊（ISSN 印刷版與電子版都列，任一個對到就算）
JOURNALS = [
    ("prl", "Physical Review Letters", "Physical Review 系列", ["0031-9007", "1079-7114"]),
    ("prx", "Physical Review X", "Physical Review 系列", ["2160-3308"]),
    ("prxq", "PRX Quantum", "Physical Review 系列", ["2691-3399"]),
    ("pra", "Physical Review A", "Physical Review 系列", ["2469-9926", "2469-9934"]),
    ("prb", "Physical Review B", "Physical Review 系列", ["2469-9950", "2469-9969"]),
    ("pre", "Physical Review E", "Physical Review 系列", ["2470-0045", "2470-0053"]),
    ("prapplied", "Physical Review Applied", "Physical Review 系列", ["2331-7019"]),
    ("prresearch", "Physical Review Research", "Physical Review 系列", ["2643-1564"]),
    ("rmp", "Reviews of Modern Physics", "Physical Review 系列", ["0034-6861", "1539-0756"]),
    ("nature", "Nature", "Nature 系列", ["0028-0836", "1476-4687"]),
    ("nphys", "Nature Physics", "Nature 系列", ["1745-2473", "1745-2481"]),
    ("ncomms", "Nature Communications", "Nature 系列", ["2041-1723"]),
    ("nphoton", "Nature Photonics", "Nature 系列", ["1749-4885", "1749-4893"]),
    ("nmat", "Nature Materials", "Nature 系列", ["1476-1122", "1476-4660"]),
    ("nnano", "Nature Nanotechnology", "Nature 系列", ["1748-3387", "1748-3395"]),
    ("natrevphys", "Nature Reviews Physics", "Nature 系列", ["2522-5820"]),
    ("npjqi", "npj Quantum Information", "Nature 系列", ["2056-6387"]),
    ("commsphys", "Communications Physics", "Nature 系列", ["2399-3650"]),
    ("science", "Science", "Science 系列", ["0036-8075", "1095-9203"]),
    ("sciadv", "Science Advances", "Science 系列", ["2375-2548"]),
    ("apl", "Applied Physics Letters", "其他", ["0003-6951", "1077-3118"]),
    ("pla", "Physics Letters A", "其他", ["0375-9601", "1873-2429"]),
    ("njp", "New Journal of Physics", "其他", ["1367-2630"]),
    ("optica", "Optica", "其他", ["2334-2536"]),
    ("jap", "Journal of Applied Physics", "其他", ["0021-8979", "1089-7550"]),
]
DEFAULT = ["prl", "prx", "prxq", "pra", "prb", "prapplied", "prresearch", "nature", "nphys", "ncomms", "science", "sciadv"]
# 太一般、拿來搜尋沒有鑑別力的單字（片語不受影響）
GENERIC = set("""system effect based approach method new study analysis observation demonstration experimental experiment theory
theoretical model high large low small state result show single two one three first control mode modes using realization realizing
application general simple direct toward towards between different various novel enhanced enhancement dynamic dynamics physic physical
strong weak role order case time frequency field process property structure measurement probe probing tunable controllable efficient
spectrum spectra response regime scheme platform device devices point coupled interaction induced observe observed demonstrate
enable enabled arbitrary possible potential beyond others near""".split())
# 片語裡出現這些字就不列（通常是句子片段，不是術語）
PHRASE_BAD = set("""show shown using based new novel toward towards first two one three here present report demonstrate observe observed
enable enabled allow allows can may also well even very""".split())


def journals(con) -> list[dict]:
    out = [{"key": k, "name": n, "group": g, "issn": i, "custom": False} for k, n, g, i in JOURNALS]
    try:
        for j in json.loads(db.meta_get(con, "custom_journals", "[]") or "[]"):
            out.append({"key": j["key"], "name": j["name"], "group": "自訂", "issn": j["issn"], "custom": True})
    except ValueError:
        pass
    return out


def add_custom(con, name: str, issn: str) -> dict:
    issns = [x.strip().upper() for x in re.split(r"[,，;\s]+", issn or "") if re.match(r"^\d{4}-\d{3}[\dX]$", x.strip(), re.I)]
    if not name.strip() or not issns:
        raise ValueError("請填期刊名稱與 ISSN（格式 1234-567X，可填多個）")
    cur = json.loads(db.meta_get(con, "custom_journals", "[]") or "[]")
    key = "c" + re.sub(r"[^0-9x]", "", issns[0].lower())
    cur = [j for j in cur if j["key"] != key] + [{"key": key, "name": name.strip()[:80], "issn": issns}]
    db.meta_set(con, "custom_journals", json.dumps(cur, ensure_ascii=False))
    return {"key": key}


def del_custom(con, key: str) -> None:
    cur = [j for j in json.loads(db.meta_get(con, "custom_journals", "[]") or "[]") if j["key"] != key]
    db.meta_set(con, "custom_journals", json.dumps(cur, ensure_ascii=False))


# ------------------------------------------------------------------ 論文庫的關鍵字（依出現篇數排序）
def library_keywords(con, limit: int = 400) -> list[dict]:
    df, show = Counter(), {}
    for r in con.execute("SELECT title, abstract FROM papers"):
        text = f"{r['title']} {r['abstract'] or ''}"
        seen = set()
        for t in tokens(text):
            if not t.isascii() or len(t) < 3 or t in seen:
                continue
            if " " not in t and (t in GENERIC or t.isdigit()):
                continue
            if " " in t and (all(w in GENERIC for w in t.split()) or any(w in PHRASE_BAD or w.isdigit() for w in t.split())):
                continue
            seen.add(t)
        df.update(seen)
    # 論文庫的英文標籤也列進來
    for r in con.execute("SELECT t.name, COUNT(pt.paper_id) n FROM tags t JOIN paper_tags pt ON pt.tag_id=t.id GROUP BY t.id"):
        if r["name"].isascii() and len(r["name"]) >= 3:
            k = r["name"].lower()
            df[k] = max(df.get(k, 0), r["n"])
    rows = [(k, n) for k, n in df.items() if n >= 2]
    rows.sort(key=lambda x: (-x[1], " " not in x[0], x[0]))
    return [{"kw": k, "n": n} for k, n in rows[:limit]]


# ------------------------------------------------------------------ 查詢字串與本機比對
def _q(kw: str) -> str:
    kw = kw.strip().replace('"', "")
    return f'"{kw}"' if re.search(r"[\s\-]", kw) else kw


def openalex_query(keywords: list[str], mode: str, exclude: list[str]) -> str:
    joiner = " AND " if mode == "and" else " OR "
    q = "(" + joiner.join(_q(k) for k in keywords) + ")"
    if exclude:
        q += " NOT (" + " OR ".join(_q(k) for k in exclude) + ")"
    return q


def _kw_tokens(kw: str) -> list[str]:
    t = tokens(kw)
    phrases = [x for x in t if " " in x]
    return phrases or t


def matched(text: str, keywords: list[str]) -> list[str]:
    toks = set(tokens(text))
    low = text.lower()
    out = []
    for k in keywords:
        kt = _kw_tokens(k)
        if (kt and all(x in toks for x in kt)) or k.lower() in low:
            out.append(k)
    return out


def _abstract_from_index(inv) -> str:
    if not inv:
        return ""
    pos = []
    for word, idx in inv.items():
        pos += [(i, word) for i in idx]
    return " ".join(w for _, w in sorted(pos))


# ------------------------------------------------------------------ 搜尋引擎
def _issns(con, keys: list[str]) -> list[str]:
    by = {j["key"]: j for j in journals(con)}
    out = []
    for k in keys:
        out += by.get(k, {}).get("issn", [])
    return list(dict.fromkeys(out))


def search_openalex(con, key: str, issns, keywords, mode, exclude, d_from, d_to, limit=200) -> tuple[list[dict], int]:
    flt = [f"primary_location.source.issn:{'|'.join(issns)}", f"from_publication_date:{d_from}", f"to_publication_date:{d_to}",
           f"title_and_abstract.search:{openalex_query(keywords, mode, exclude)}", "type:article|review|letter"]
    items, total, page = [], 0, 1
    while len(items) < limit:
        params = {"filter": ",".join(flt), "per_page": 100, "page": page, "sort": "publication_date:desc", "api_key": key,
                  "select": "id,doi,title,publication_date,primary_location,authorships,abstract_inverted_index,best_oa_location,cited_by_count,type"}
        mail = enrich.config.CROSSREF_MAILTO
        if mail:
            params["mailto"] = mail
        data = json.loads(enrich._get(f"{OPENALEX_API}/works?" + urllib.parse.urlencode(params), timeout=40))
        total = data.get("meta", {}).get("count", 0)
        rows = data.get("results", [])
        for w in rows:
            doi = (w.get("doi") or "").replace("https://doi.org/", "")
            src = ((w.get("primary_location") or {}).get("source") or {})
            items.append({"doi": doi, "title": re.sub(r"<[^>]+>", "", w.get("title") or ""),
                          "authors": [a.get("author", {}).get("display_name", "") for a in w.get("authorships", [])][:60],
                          "abstract": _abstract_from_index(w.get("abstract_inverted_index")), "published": w.get("publication_date") or "",
                          "venue": src.get("display_name") or "", "url": f"https://doi.org/{doi}" if doi else w.get("id", ""),
                          "pdf_url": ((w.get("best_oa_location") or {}).get("pdf_url") or ""), "cited": w.get("cited_by_count") or 0})
        if len(rows) < 100:
            break
        page += 1
    return items[:limit], total


def _crossref_rows(issns, d_from, d_to, query, rows=100, offset=0) -> list[dict]:
    flt = [f"issn:{i}" for i in issns] + [f"from-pub-date:{d_from}", f"until-pub-date:{d_to}", "type:journal-article"]
    params = {"filter": ",".join(flt), "query.bibliographic": query, "rows": rows, "offset": offset,
              "select": "DOI,title,author,published,issued,container-title,abstract,URL,is-referenced-by-count,link"}
    if enrich.config.CROSSREF_MAILTO:
        params["mailto"] = enrich.config.CROSSREF_MAILTO
    data = json.loads(enrich._get(f"{CROSSREF_API}/works?" + urllib.parse.urlencode(params), timeout=40))
    return data.get("message", {}).get("items", [])


def search_crossref(issns, keywords, mode, exclude, d_from, d_to, limit=200) -> tuple[list[dict], int]:
    raw = []
    if mode == "and":
        q = " ".join(keywords)
        raw += _crossref_rows(issns, d_from, d_to, q, 100, 0)
        if len(raw) == 100:
            raw += _crossref_rows(issns, d_from, d_to, q, 100, 100)
    else:
        for k in keywords[:15]:
            raw += _crossref_rows(issns, d_from, d_to, k, 60, 0)
    seen, items = set(), []
    for it in raw:
        doi = it.get("DOI", "")
        if not doi or doi.lower() in seen:
            continue
        seen.add(doi.lower())
        parts = ((it.get("published") or it.get("issued") or {}).get("date-parts") or [[None]])[0]
        pub = "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts) if p) if parts and parts[0] else ""
        pdf = next((l.get("URL") for l in it.get("link", []) if "pdf" in (l.get("content-type") or "")), "")
        items.append({"doi": doi, "title": re.sub(r"<[^>]+>", "", (it.get("title") or [""])[0]),
                      "authors": [" ".join(x for x in (a.get("given"), a.get("family")) if x) for a in it.get("author", [])][:60],
                      "abstract": enrich._strip_jats(it.get("abstract", "")), "published": pub,
                      "venue": (it.get("container-title") or [""])[0], "url": it.get("URL") or f"https://doi.org/{doi}",
                      "pdf_url": "", "cited": it.get("is-referenced-by-count") or 0, "_pdf": pdf})
    # 本機依交集／聯集篩選（Crossref 的查詢只是相關度排序）
    out = []
    for x in items:
        text = f"{x['title']} {x['abstract']}"
        m = matched(text, keywords)
        if (mode == "and" and len(m) == len(keywords)) or (mode != "and" and m):
            if not exclude or not matched(text, exclude):
                out.append(x)
    out.sort(key=lambda x: x["published"], reverse=True)
    return out[:limit], len(out)


def date_range(days: int | None, d_from: str | None, d_to: str | None) -> tuple[str, str]:
    today = date.today()
    if d_from and re.match(r"^\d{4}-\d{2}-\d{2}$", d_from):
        f = d_from
    else:
        f = (today - timedelta(days=int(days or 365))).isoformat()
    t = d_to if d_to and re.match(r"^\d{4}-\d{2}-\d{2}$", d_to) else today.isoformat()
    return f, t


def search(con, journal_keys: list[str], keywords: list[str], mode: str = "or", exclude: list[str] | None = None,
           days: int | None = 365, d_from: str | None = None, d_to: str | None = None, limit: int = 200) -> dict:
    keywords = [k.strip() for k in keywords if k.strip()][:30]
    exclude = [k.strip() for k in (exclude or []) if k.strip()][:15]
    if not keywords:
        raise ValueError("請至少選一個關鍵字")
    issns = _issns(con, journal_keys)
    if not issns:
        raise ValueError("請至少選一本期刊")
    f, t = date_range(days, d_from, d_to)
    key = settings.get(con)["openalex_key"]
    if key:
        items, total = search_openalex(con, key, issns, keywords, mode, exclude, f, t, limit)
        engine = "openalex"
    else:
        items, total = search_crossref(issns, keywords, mode, exclude, f, t, limit)
        engine = "crossref"
    for x in items:
        x["matched"] = matched(f"{x['title']} {x['abstract']}", keywords)
        x["uid"] = f"doi:{x['doi'].lower()}" if x["doi"] else f"url:{x['url']}"
        r = con.execute("SELECT id FROM papers WHERE lower(doi)=lower(?)", (x["doi"],)).fetchone() if x["doi"] else None
        x["paper_id"] = r["id"] if r else None
        x.pop("_pdf", None)
    return {"items": items, "total": total, "engine": engine, "from": f, "to": t,
            "query": openalex_query(keywords, mode, exclude) if engine == "openalex" else
            (" 且 " if mode == "and" else " 或 ").join(keywords) + (f"（排除：{'、'.join(exclude)}）" if exclude else "")}


def store_items(con, items: list[dict], feed_id=None) -> list[int]:
    """把搜尋結果存進 feed_items（已存在就沿用），回傳 id；之後用「加入論文庫」的既有流程建立論文。"""
    ids = []
    for it in items:
        uid = it.get("uid") or (f"doi:{it['doi'].lower()}" if it.get("doi") else f"url:{it.get('url', '')}")
        r = con.execute("SELECT id FROM feed_items WHERE uid=?", (uid,)).fetchone()
        if r:
            ids.append(r["id"])
            continue
        inlib = con.execute("SELECT id FROM papers WHERE lower(doi)=lower(?)", (it.get("doi") or "-",)).fetchone()
        cur = con.execute("INSERT INTO feed_items(feed_id, uid, title, authors, abstract, published, url, pdf_url, doi, arxiv, venue, status, paper_id, found_at) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (feed_id, uid, str(it.get("title", ""))[:500], json.dumps((it.get("authors") or [])[:60], ensure_ascii=False),
                           str(it.get("abstract", ""))[:5000], it.get("published", ""), it.get("url", ""), it.get("pdf_url", ""),
                           it.get("doi", ""), "", str(it.get("venue", ""))[:200], "inlib" if inlib else "new",
                           inlib["id"] if inlib else None, db.now()))
        ids.append(cur.lastrowid)
    return ids


def fetch_feed(con, feed: dict) -> list[dict]:
    """「期刊搜尋」存成的自動追蹤：每天找最近 N 天發表的論文。"""
    c = json.loads(feed.get("config") or "{}")
    r = search(con, c.get("journals") or DEFAULT, c.get("keywords") or [], c.get("mode", "or"), c.get("exclude") or [],
               int(c.get("days") or 30), None, None, 200)
    return [{"uid": x["uid"], "arxiv": "", "title": x["title"], "authors": x["authors"], "abstract": x["abstract"], "published": x["published"],
             "url": x["url"], "pdf_url": x["pdf_url"], "doi": x["doi"], "venue": x["venue"]} for x in r["items"]]
