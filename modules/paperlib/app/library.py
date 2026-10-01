"""論文與檔案的共用操作：API 與匯入工具都走這裡，確保檔名、縮圖、索引一致。"""
import hashlib
import json
import shutil
from pathlib import Path

from . import config, db, pdftools

ROLE_SUFFIX = {"main": "", "sm": "_SM", "peer_review": "_review", "version": "_version", "other": "_file"}
ROLES = ["main", "sm", "peer_review", "version", "other"]
KINDS = ["article", "review", "preprint", "book", "thesis", "web", "other"]
STATUSES = db.STATUSES   # 個人閱讀狀態（私人）


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def citekey_exists(con):
    return lambda k: con.execute("SELECT 1 FROM papers WHERE citekey=?", (k,)).fetchone() is not None


def create_paper(con, f: dict, user_id=None) -> int:
    authors = f.get("authors") or []
    key = f.get("citekey") or pdftools.make_citekey(authors[0] if authors else "", f.get("year"), f.get("title", ""),
                                                   citekey_exists(con))
    if citekey_exists(con)(key):
        key = pdftools.make_citekey(authors[0] if authors else key, f.get("year"), f.get("title", ""), citekey_exists(con))
    t = db.now()
    cur = con.execute(
        "INSERT INTO papers(citekey, title, authors, authors_complete, year, venue, doi, arxiv, kind, abstract, status, "
        "suggested_category_id, keyinfo, added_by, added_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (key, f.get("title") or "（未命名）", json.dumps(authors, ensure_ascii=False), int(bool(f.get("authors_complete"))),
         f.get("year"), f.get("venue") or "", (f.get("doi") or "").strip(), f.get("arxiv") or "", f.get("kind") or "article",
         f.get("abstract") or "", None, f.get("suggested_category_id"),
         json.dumps(f.get("keyinfo") or {}, ensure_ascii=False), user_id, t, t))
    if f.get("status") and user_id:          # 上傳者自己的閱讀狀態
        db.set_status(con, user_id, cur.lastrowid, f["status"])
    return cur.lastrowid


def _unique_name(folder: Path, name: str) -> str:
    stem, suf = Path(name).stem, Path(name).suffix
    cand, i = name, 1
    while (folder / cand).exists():
        i += 1
        cand = f"{stem}{i}{suf}"
    return cand


def add_file(con, paper_id: int, src: Path, original_name: str, role: str = "main", label: str = "",
             user_id=None, move: bool = False, sha: str | None = None, index_text: bool = True) -> int:
    role = role if role in ROLES else "other"
    sha = sha or sha256_file(src)
    dup = con.execute("SELECT id, paper_id FROM files WHERE sha256=? OR orig_sha256=?", (sha, sha)).fetchone()
    if dup:
        raise ValueError(f"檔案已存在（論文 #{dup['paper_id']}）")
    p = con.execute("SELECT citekey FROM papers WHERE id=?", (paper_id,)).fetchone()
    folder = config.FILES_DIR / str(paper_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = _unique_name(folder, pdftools.safe_filename(p["citekey"] + ROLE_SUFFIX[role]) + ".pdf")
    dest = folder / name
    if move:
        shutil.move(str(src), dest)
    else:
        shutil.copy2(src, dest)
    try:
        info = pdftools.analyze(dest, original_name)
        pages = info["pages"]
        has_text = int(pdftools.has_text_layer(dest))
    except Exception:  # noqa: BLE001 - 壞檔仍保存，只是沒有頁數
        pages, has_text = None, None
    cur = con.execute(
        "INSERT INTO files(paper_id, role, label, filename, original_name, sha256, size, pages, path, added_by, added_at, has_text) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (paper_id, role, label, name, original_name, sha, dest.stat().st_size, pages,
         str(dest.relative_to(config.DATA_DIR)), user_id, db.now(), has_text))
    fid = cur.lastrowid
    try:
        pdftools.render_thumb(dest, config.THUMBS_DIR / f"{fid}.jpg")
    except Exception:  # noqa: BLE001
        pass
    if role == "main" and index_text:
        try:
            db.fts_update(con, paper_id, pdftools.full_text(dest))
        except Exception:  # noqa: BLE001
            db.fts_update(con, paper_id)
    return fid


def main_file_id(con, paper_id: int):
    r = con.execute("SELECT id FROM files WHERE paper_id=? ORDER BY CASE role WHEN 'main' THEN 0 WHEN 'version' THEN 1 "
                    "WHEN 'sm' THEN 2 ELSE 3 END, id LIMIT 1", (paper_id,)).fetchone()
    return r["id"] if r else None


def bibtex(p: dict) -> str:
    kind = {"book": "book", "thesis": "phdthesis", "preprint": "misc", "web": "misc"}.get(p["kind"], "article")
    authors = json.loads(p["authors"]) if isinstance(p["authors"], str) else p["authors"]
    lines = [f"@{kind}{{{p['citekey']},", f"  title = {{{{{p['title']}}}}},"]
    if authors:
        auth = " and ".join(authors) + ("" if p.get("authors_complete") else " and others")
        lines.append(f"  author = {{{auth}}},")
    if p.get("year"):
        lines.append(f"  year = {{{p['year']}}},")
    if p.get("venue"):
        lines.append(f"  {'publisher' if kind == 'book' else 'journal'} = {{{p['venue']}}},")
    if p.get("doi"):
        lines.append(f"  doi = {{{p['doi']}}},")
    if p.get("arxiv"):
        lines.append(f"  eprint = {{{p['arxiv']}}},\n  archivePrefix = {{arXiv}},")
    lines.append("}")
    return "\n".join(lines)
