"""匯出：把劃線與筆記寫進 PDF（任何 PDF 閱讀器都看得到），以及 Markdown 筆記。"""
import io
import json
from html import escape

import pymupdf

from . import config, db

COLOR = {"yellow": (1, 0.84, 0.1), "blue": (0.36, 0.58, 1), "green": (0.3, 0.8, 0.42), "red": (1, 0.42, 0.42)}
COLOR_NAME = {"yellow": "重點", "blue": "方法／公式", "green": "參數／數據", "red": "疑問"}


def _anns(con, pid: int, uid: int, fid: int | None = None):
    rows = [dict(r) for r in con.execute(
        "SELECT a.*, u.display_name AS who FROM annotations a LEFT JOIN users u ON u.id=a.author_id "
        "WHERE a.paper_id=? AND a.nb=0 AND (a.private=0 OR a.author_id=?) ORDER BY a.page IS NULL, a.page, a.id", (pid, uid))]
    for a in rows:
        a["rects"] = json.loads(a["rects"] or "[]")
        a["replies"] = [dict(r) for r in con.execute(
            "SELECT r.body, r.created_at, u.display_name AS who FROM ann_replies r LEFT JOIN users u ON u.id=r.author_id "
            "WHERE r.ann_id=? ORDER BY r.id", (a["id"],))]
    if fid is not None:
        rows = [a for a in rows if a["file_id"] in (None, fid)]
    return rows


def _content(a) -> str:
    t = a["body"] or ""
    for r in a["replies"]:
        t += f"\n— {r['who']}：{r['body']}"
    return t.strip()


def _summary_pdf(p: dict, anns: list, size) -> bytes:
    """重點欄＋所有筆記的摘要頁（HTML 排版，中英混排、自動換頁）。"""
    ki = json.loads(p["keyinfo"] or "{}")
    css = "font-family:sans-serif;"
    h = [f"<h2 style='{css}font-size:15pt;margin:0'>{escape(p['title'])}</h2>",
         f"<p style='{css}font-size:9pt;color:#666'>{escape(p['citekey'])} · {escape(p['venue'] or '')} {p['year'] or ''}</p>"]
    if ki:
        h.append(f"<h3 style='{css}color:#1f5f8b;font-size:12pt'>重點欄</h3>")
        h += [f"<p style='{css}font-size:10.5pt'><b>{escape(k)}</b>：{escape(v)}</p>" for k, v in ki.items()]
    if anns:
        h.append(f"<h3 style='{css}color:#1f5f8b;font-size:12pt'>標註與筆記</h3>")
        for a in anns:
            where = f"p.{a['page']}" if a["page"] else "整篇"
            h.append(f"<p style='{css}font-size:8.5pt;color:#777;margin-bottom:0'>{where} · {COLOR_NAME.get(a['color'], '')} · {escape(a['who'] or '')}</p>")
            if a["quote"]:
                h.append(f"<p style='{css}font-size:9.5pt;color:#444;margin:2px 0 2px 12px'>「{escape(a['quote'])}」</p>")
            if _content(a):
                h.append(f"<p style='{css}font-size:10.5pt;margin-top:2px'>{escape(_content(a)).replace(chr(10), '<br>')}</p>")
    story = pymupdf.Story(html="".join(h))
    buf = io.BytesIO()
    w = pymupdf.DocumentWriter(buf)
    mb = pymupdf.Rect(0, 0, *size)
    more = 1
    while more:
        dev = w.begin_page(mb)
        more, _ = story.place(mb + (48, 48, -48, -48))
        story.draw(dev)
        w.end_page()
    w.close()
    return buf.getvalue()


def annotated_pdf(con, fid: int, uid: int) -> tuple[bytes, str]:
    f = con.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
    p = dict(con.execute("SELECT * FROM papers WHERE id=?", (f["paper_id"],)).fetchone())
    doc = pymupdf.open(config.DATA_DIR / f["path"])
    anns = _anns(con, p["id"], uid, fid)
    n_pages = doc.page_count
    stack = {}
    for a in anns:
        if not a["page"] or a["page"] > n_pages:
            continue
        page = doc[a["page"] - 1]
        W, H = page.rect.width, page.rect.height
        info = {"title": a["who"] or "", "content": _content(a), "subject": COLOR_NAME.get(a["color"], "")}
        if a["kind"] == "ink":
            try:
                strokes = json.loads(a["ink"] or "{}").get("strokes", [])
            except ValueError:
                strokes = []
            for s in strokes:
                pts = [pymupdf.Point(x * W, y * H) * page.derotation_matrix for x, y in s["p"]]
                if len(pts) == 1:
                    pts.append(pts[0] + (0.5, 0.5))
                annot = page.add_ink_annot([[(q.x, q.y) for q in pts]])
                c = s.get("c", "#111111")
                annot.set_colors(stroke=tuple(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)))
                annot.set_border(width=max(0.5, s.get("w", 0.003) * W))
                annot.set_info({"title": a["who"] or "", "subject": "手寫"})
                annot.update(opacity=s.get("o", 1))
            continue
        if a["rects"]:
            rects = [pymupdf.Rect(r[0] * W, r[1] * H, (r[0] + r[2]) * W, (r[1] + r[3]) * H) * page.derotation_matrix
                     for r in a["rects"]]
            annot = page.add_highlight_annot(rects)
            annot.set_colors(stroke=COLOR.get(a["color"], COLOR["yellow"]))
        else:
            k = stack[a["page"]] = stack.get(a["page"], -1) + 1   # 同一頁多則筆記往下排
            annot = page.add_text_annot(pymupdf.Point(W - 28, 24 + 26 * k) * page.derotation_matrix, info["content"] or "（筆記）", icon="Note")
            annot.set_colors(stroke=COLOR.get(a["color"], COLOR["yellow"]))
        annot.set_info(info)
        annot.update(opacity=0.45 if a["rects"] else 1)
    # 最後附上「重點欄＋所有筆記」摘要頁
    size = (doc[0].rect.width, doc[0].rect.height) if n_pages else (595, 842)
    doc.insert_pdf(pymupdf.open("pdf", _summary_pdf(p, anns, size)))
    doc.subset_fonts()
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data, f"{p['citekey']}_annotated.pdf"


def notes_markdown(con, pid: int, uid: int) -> tuple[str, str]:
    p = dict(con.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone())
    authors = json.loads(p["authors"] or "[]")
    L = [f"# {p['title']}", "", f"- citekey: `{p['citekey']}`", f"- 作者：{', '.join(authors)}",
         f"- 出處：{p['venue']} {p['year'] or ''}"]
    if p["doi"]:
        L.append(f"- DOI: https://doi.org/{p['doi']}")
    tags = [r["name"] for r in con.execute("SELECT t.name FROM paper_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.paper_id=?", (pid,))]
    cats = [r["name"] for r in con.execute("SELECT c.name FROM paper_categories pc JOIN categories c ON c.id=pc.category_id WHERE pc.paper_id=?", (pid,))]
    if cats:
        L.append(f"- 分類：{'、'.join(cats)}")
    if tags:
        L.append(f"- 標籤：{' '.join('#' + t.replace(' ', '_') for t in tags)}")
    ki = json.loads(p["keyinfo"] or "{}")
    if ki:
        L += ["", "## 重點欄", ""] + [f"**{k}**：{v}  " for k, v in ki.items()]
    anns = _anns(con, pid, uid)
    if anns:
        L += ["", "## 標註與筆記", ""]
        for a in anns:
            L.append(f"### {'p.' + str(a['page']) if a['page'] else '整篇'} · {COLOR_NAME.get(a['color'], '')} · {a['who'] or ''}")
            if a["quote"]:
                L.append(f"> {a['quote']}")
            if a["body"]:
                L += ["", a["body"]]
            for r in a["replies"]:
                L.append(f"- **{r['who']}**：{r['body']}")
            L.append("")
    return "\n".join(L).strip() + "\n", f"{p['citekey']}_notes.md"
