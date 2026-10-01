"""PDF 處理：抽書目線索（DOI、arXiv、標題）、全文、首頁縮圖。使用 PyMuPDF。"""
import re
import unicodedata
from pathlib import Path

import pymupdf

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s,;)\"\]>}|]+")
ARXIV_RE = re.compile(r"arXiv:\s?(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
SUPP_RE = re.compile(r"(^|[\s_,(-])(sm|si|supp|supplement(ary|al)?|supporting)([\s_,.)-]|$)", re.I)
REVIEW_RE = re.compile(r"peer[\s_-]?review", re.I)
BAD_META_TITLES = re.compile(r"^(untitled|microsoft word|doi:|https?://|[\w-]+\.(docx?|tex|dvi|pdf))", re.I)
HEADER_WORDS = {"letters", "letter", "article", "articles", "review", "review articles", "research article", "paper",
                "physical review letters", "physical review a", "physical review b", "physical review x",
                "nature physics", "nature communications", "science advances"}


def is_pdf(path: Path) -> bool:
    with open(path, "rb") as fh:
        return fh.read(1024).lstrip().startswith(b"%PDF")


def _clean(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip()


def _title_from_fonts(page) -> str:
    """首頁上半部字級最大的幾行，通常就是標題。"""
    spans = []
    h = page.rect.height
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            txt = "".join(s["text"] for s in line["spans"]).strip()
            if len(txt) < 3 or line["bbox"][1] > h * 0.6:
                continue
            size = max(s["size"] for s in line["spans"])
            spans.append((size, line["bbox"][1], txt))
    if not spans:
        return ""
    spans.sort(key=lambda x: -x[0])
    for size, _, txt in spans:
        if _clean(txt).lower() in HEADER_WORDS:
            continue
        top = size
        break
    else:
        return ""
    lines = sorted((y, t) for s, y, t in spans if abs(s - top) < 0.6 and _clean(t).lower() not in HEADER_WORDS)
    title = _clean(" ".join(t for _, t in lines))
    return title if 10 <= len(title) <= 300 else ""


def analyze(path: Path, filename: str = "") -> dict:
    doc = pymupdf.open(path)
    meta = doc.metadata or {}
    first = doc[0].get_text() if doc.page_count else ""
    second = doc[1].get_text() if doc.page_count > 1 else ""
    head = first + "\n" + second
    dois = [d.rstrip(".") for d in DOI_RE.findall(head)]
    arx = ARXIV_RE.findall(first)
    mt = _clean(meta.get("title") or "")
    title = mt if len(mt) >= 12 and not BAD_META_TITLES.match(mt) else ""
    if not title and doc.page_count:
        title = _title_from_fonts(doc[0])
    stem = Path(filename).stem if filename else ""
    if not title and stem:
        title = _clean(re.sub(r"[_]+", " ", stem))
    years = [int(y) for y in YEAR_RE.findall(first[:1500])]
    author = _clean(meta.get("author") or "")
    if len(author) > 80 or "@" in author:
        author = ""
    info = {
        "pages": doc.page_count,
        "doi": dois[0] if dois else "",
        "dois": list(dict.fromkeys(dois))[:5],
        "arxiv": arx[0] if arx else "",
        "title": title,
        "author": author.split(",")[0].split(";")[0].strip() if author else "",
        "year": max(years) if years else None,
        "is_supplement": bool(SUPP_RE.search(stem)) or first.lstrip()[:200].lower().find("supplement") >= 0,
        "is_peer_review": bool(REVIEW_RE.search(stem)) or first.lstrip()[:100].lower().startswith("peer review"),
        "has_text": len(first.strip()) > 50,
    }
    doc.close()
    return info


def has_text_layer(path: Path, pages: int = 4) -> bool:
    """前幾頁是否有可選取的文字（掃描檔沒有）。"""
    with pymupdf.open(path) as doc:
        n = min(pages, doc.page_count)
        if not n:
            return False
        chars = sum(len(doc[i].get_text().strip()) for i in range(n))
        return chars > 80 * n


def full_text(path: Path, max_chars: int = 2_000_000) -> str:
    out, n = [], 0
    with pymupdf.open(path) as doc:
        for page in doc:
            t = page.get_text()
            out.append(t)
            n += len(t)
            if n > max_chars:
                break
    return "\n".join(out)[:max_chars]


def render_thumb(path: Path, out: Path, width: int = 360) -> None:
    with pymupdf.open(path) as doc:
        if not doc.page_count:
            return
        page = doc[0]
        zoom = width / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        out.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(out), jpg_quality=80) if out.suffix == ".jpg" else pix.save(str(out))


def surname(name: str) -> str:
    name = _clean(name)
    if not name:
        return ""
    if "," in name:
        last = name.split(",")[0]
    else:
        parts = [p for p in re.split(r"\s+", name) if p]
        last = parts[-1] if parts else ""
    last = unicodedata.normalize("NFKD", last)
    last = "".join(c for c in last if c.isalnum() and ord(c) < 128)
    return last[:1].upper() + last[1:]


STOP = {"a", "an", "the", "on", "of", "in", "for", "and", "to", "via", "with", "from", "by", "at", "is", "what"}


def make_citekey(first_author: str, year, title: str, exists) -> str:
    last = surname(first_author) or "Paper"
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9]*", title or "") if w.lower() not in STOP]
    word = (words[0][:1].upper() + words[0][1:].lower()) if words else "Untitled"
    base = f"{last}{year or ''}{word}"
    key, i = base, 0
    while exists(key):
        i += 1
        key = f"{base}{chr(ord('a') + i - 1)}" if i <= 26 else f"{base}{i}"
    return key


def safe_filename(s: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", s, flags=re.UNICODE).strip("._")
    return s[:120] or "file"
