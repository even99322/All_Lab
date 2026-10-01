"""用 DOI（Crossref）或 arXiv 編號補齊書目。NAS 需能連外網；連不到時回傳錯誤訊息，不影響其他功能。"""
import html
import json
import re
import urllib.parse
import urllib.request
import os
import xml.etree.ElementTree as ET

from . import config

CROSSREF_API = os.environ.get("PAPERLIB_CROSSREF_API", "https://api.crossref.org")
UA = "PaperLib/1.0 (lab paper archive" + (f"; mailto:{config.CROSSREF_MAILTO}" if config.CROSSREF_MAILTO else "") + ")"


def _get(url: str, timeout: float = 12.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, application/atom+xml"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _strip_jats(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def crossref(doi: str) -> dict:
    data = json.loads(_get(CROSSREF_API + "/works/" + urllib.parse.quote(doi, safe="/")))["message"]
    authors = []
    for a in data.get("author", []):
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "")
        if name:
            authors.append(name)
    year = None
    for k in ("published-print", "published-online", "issued", "created"):
        parts = (data.get(k) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            year = int(parts[0][0])
            break
    journal = (data.get("short-container-title") or data.get("container-title") or [""])[0]
    vol = data.get("volume", "")
    page = data.get("article-number") or data.get("page", "")
    venue = journal
    if vol:
        venue += f" {vol}"
    if page:
        venue += f", {page.split('-')[0]}"
    kind = {"journal-article": "article", "book": "book", "monograph": "book", "posted-content": "preprint",
            "proceedings-article": "article", "book-chapter": "book", "dissertation": "thesis"}.get(data.get("type"), "article")
    return {"title": _strip_jats((data.get("title") or [""])[0]), "authors": authors, "authors_complete": True,
            "year": year, "venue": venue.strip(), "abstract": _strip_jats(data.get("abstract", "")), "kind": kind}


def arxiv(aid: str) -> dict:
    raw = _get("https://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(aid))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = ET.fromstring(raw).find("a:entry", ns)
    if entry is None:
        raise ValueError("arXiv 查無此編號")
    title = re.sub(r"\s+", " ", entry.findtext("a:title", "", ns)).strip()
    authors = [a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)]
    published = entry.findtext("a:published", "", ns)
    return {"title": title, "authors": authors, "authors_complete": True,
            "year": int(published[:4]) if published[:4].isdigit() else None,
            "abstract": re.sub(r"\s+", " ", entry.findtext("a:summary", "", ns)).strip(), "kind": "preprint"}


def lookup(doi: str, arxiv_id: str) -> dict:
    errors = []
    if doi:
        try:
            return crossref(doi)
        except Exception as e:  # noqa: BLE001 - 回報給使用者即可
            errors.append(f"Crossref：{e}")
    if arxiv_id:
        try:
            return arxiv(arxiv_id)
        except Exception as e:  # noqa: BLE001
            errors.append(f"arXiv：{e}")
    if not errors:
        errors.append("這篇沒有 DOI 或 arXiv 編號")
    raise RuntimeError("；".join(errors))
