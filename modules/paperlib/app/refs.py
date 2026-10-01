"""從論文的參考文獻找出「也在論文庫裡的論文」，自動建立「引用」關聯。

物理期刊的參考文獻常常沒有標題（例如 PRL 格式），所以比對四種線索：
1. 期刊＋卷＋頁（例：Phys. Rev. Lett. 120, 057202）——與論文庫的「期刊／出處」欄比對
2. DOI  3. arXiv 編號  4. 標題（Nature／Science 格式會列標題）
被使用者刪掉的自動關聯會記在 ref_ignore，不會再被加回來。
"""
import re

from . import db
from .jobs import handler

REF_HEAD = re.compile(r"\n\s*(?:\d+\s*\.?\s*)?(References(?: and Notes)?|REFERENCES(?: AND NOTES)?|Bibliography|參考文獻)\s*\n")
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s,;\"'<>]+")
ARXIV_RE = re.compile(r"arXiv\s*:?\s*(\d{4}\.\d{4,5})", re.I)
VENUE_RE = re.compile(r"^(?P<j>.*?[A-Za-z.)])\s+(?P<v>\d{1,4})\s*,\s*(?P<p>[A-Za-z]{0,4}\d{2,8}[A-Za-z]?)\b")


def _letters(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def clean_text(t: str) -> str:
    t = t.replace("­", "").replace("‐", "-").replace("‑", "-")
    t = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", t)
    return t


def ref_section(body: str) -> str:
    body = clean_text(body or "")
    heads = list(REF_HEAD.finditer(body))
    if heads and heads[-1].start() > len(body) * 0.25:
        return body[heads[-1].end():]
    return body[int(len(body) * 0.6):]   # 找不到標題：取後段


class Index:
    """論文庫的比對索引（每次分析建一次）。"""

    def __init__(self, con):
        self.papers = [dict(r) for r in con.execute("SELECT id, title, venue, doi, arxiv FROM papers")]
        self.by_doi = {p["doi"].lower(): p["id"] for p in self.papers if p["doi"]}
        self.by_arxiv = {p["arxiv"].split("v")[0]: p["id"] for p in self.papers if p["arxiv"]}
        self.venues = []      # (pid, 期刊字母, 卷, 頁, regex)
        count = {}
        for p in self.papers:
            m = VENUE_RE.match(p["venue"] or "")
            if m:
                key = (m["v"], m["p"].lower())
                count[key] = count.get(key, 0) + 1
                self.venues.append((p["id"], _letters(m["j"]), m["v"], m["p"],
                                    re.compile(rf"(?<!\d){re.escape(m['v'])}\s*(?:\(\d{{4}}\)\s*)?,?\s*{re.escape(m['p'])}(?![\dA-Za-z])")))
        self.vp_count = count
        self.titles = [(p["id"], _alnum(p["title"])) for p in self.papers if len(_alnum(p["title"])) >= 28]

    def find(self, src_id: int, body: str) -> dict:
        """回傳 {被引用論文 id: 依據}。"""
        refs = ref_section(body)
        if not refs.strip():
            return {}
        flat = re.sub(r"\s+", " ", refs)
        hits = {}
        for d in DOI_RE.findall(flat):
            pid = self.by_doi.get(d.rstrip(".").lower())
            if pid:
                hits[pid] = "DOI"
        for a in ARXIV_RE.findall(flat):
            pid = self.by_arxiv.get(a)
            if pid:
                hits.setdefault(pid, "arXiv")
        for pid, jl, vol, page, rx in self.venues:
            if pid in hits:
                continue
            for m in rx.finditer(flat):
                before = _letters(flat[max(0, m.start() - 70): m.start()])
                unique = self.vp_count.get((vol, page.lower()), 0) == 1
                if (jl and before.endswith(jl)) or (unique and len(page) >= 4 and jl[:4] and jl[:4] in before[-40:]):
                    hits[pid] = "期刊卷頁"
                    break
        norm = _alnum(refs)
        for pid, t in self.titles:
            if pid not in hits and t in norm:
                hits[pid] = "標題"
        hits.pop(src_id, None)
        return hits


def rebuild(con, only: int | None = None, progress=lambda m: None) -> dict:
    idx = Index(con)
    ignore = {(r["src_id"], r["dst_id"]) for r in con.execute("SELECT src_id, dst_id FROM ref_ignore")}
    rows = con.execute("SELECT paper_id, body FROM fts" + (" WHERE paper_id=?" if only else ""),
                       (only,) if only else ()).fetchall()
    added = found = 0
    for i, r in enumerate(rows):
        src = int(r["paper_id"])
        hits = idx.find(src, r["body"])
        found += len(hits)
        with db.tx() as c:
            for dst, why in hits.items():
                if (src, dst) in ignore:
                    continue
                cur = c.execute("INSERT OR IGNORE INTO links(src_id, dst_id, rel, note, author_id, created_at, auto) "
                                "VALUES(?,?,?,?,NULL,?,1)", (src, dst, db.AUTO_REL, f"自動（依{why}比對參考文獻）", db.now()))
                added += cur.rowcount
        if i % 20 == 0:
            progress(f"{i + 1}/{len(rows)} 篇")
    return {"papers": len(rows), "found": found, "added": added}


@handler("refs")
def _job(con, arg, progress):
    r = rebuild(con, int(arg) if arg else None, progress)
    return f"分析 {r['papers']} 篇，找到 {r['found']} 條引用，新增 {r['added']} 條"
