"""圖表剪貼簿：在 PDF 上框選一張圖、一條公式或一個表格，存成「圖卡」（高解析 PNG＋來源頁碼）。

裁切在伺服器用 PyMuPDF 重新繪製（不是截螢幕），所以放大、印出都清楚。
圖卡檔案放在 data/figures/<paper_id>/<figure_id>.png。
"""
import re
from pathlib import Path

import pymupdf

from . import ai, config, db

FIG_DIR = config.DATA_DIR / "figures"
KINDS = {"figure": "圖", "equation": "公式", "table": "表格"}
_CAP = re.compile(r"^\s*(FIG(?:URE)?|Fig(?:ure)?|TABLE|Table|Eq(?:uation)?)\.?\s*(S?\d+|[IVX]+)", re.I)


def _pdf_path(con, file_id: int) -> Path:
    f = con.execute("SELECT path FROM files WHERE id=?", (file_id,)).fetchone()
    if f is None:
        raise ValueError("找不到檔案")
    return (config.DATA_DIR / f["path"]).resolve()


def _clip(page, rect) -> pymupdf.Rect:
    x, y, w, h = [max(0.0, min(1.0, float(v))) for v in rect]
    r = page.rect
    clip = pymupdf.Rect(r.x0 + x * r.width, r.y0 + y * r.height, r.x0 + (x + w) * r.width, r.y0 + (y + h) * r.height)
    if clip.width < 4 or clip.height < 4:
        raise ValueError("框選範圍太小")
    return clip


def render(pdf: Path, page_no: int, rect, dpi: int = 220) -> tuple[bytes, int, int, str, str]:
    """回傳 (png, 寬, 高, 框內文字, 猜到的圖說)。"""
    with pymupdf.open(pdf) as doc:
        if not 1 <= page_no <= doc.page_count:
            raise ValueError("頁碼超出範圍")
        page = doc[page_no - 1]
        clip = _clip(page, rect)
        zoom = dpi / 72
        # 圖太大時降低解析度，單張控制在約 6 百萬像素
        zoom = min(zoom, (6e6 / max(1.0, clip.width * clip.height)) ** 0.5)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
        text = page.get_text("text", clip=clip).strip()
        caption = guess_caption(page, clip)
        return pix.tobytes("png"), pix.width, pix.height, text[:4000], caption


def guess_caption(page, clip) -> str:
    """圖說通常在圖的正下方（表格在正上方）：找以 FIG./Table 開頭、與框選範圍水平重疊的文字區塊。"""
    best = None
    ph = page.rect.height
    for b in page.get_text("blocks"):
        x0, y0, x1, y1, txt = b[0], b[1], b[2], b[3], b[4]
        if not _CAP.match(txt or ""):
            continue
        overlap = min(x1, clip.x1) - max(x0, clip.x0)
        if overlap < min(clip.width, x1 - x0) * 0.3:
            continue
        if y0 >= clip.y1 - 5:
            d = y0 - clip.y1
        elif y1 <= clip.y0 + 5:
            d = (clip.y0 - y1) * 1.5          # 在上方（表格的標題）稍微不優先
        elif clip.y0 <= y0 <= clip.y1:
            d = 0                             # 框選範圍裡就有圖說
        else:
            continue
        if d > ph * 0.25:
            continue
        if best is None or d < best[0]:
            best = (d, txt)
    if not best:
        return ""
    return re.sub(r"\s+", " ", best[1]).strip()[:1500]


def save(con, pid: int, file_id: int, page_no: int, rect, kind: str, caption: str | None, note: str, user_id) -> int:
    kind = kind if kind in KINDS else "figure"
    png, w, h, text, guessed = render(_pdf_path(con, file_id), page_no, rect)
    cur = con.execute("INSERT INTO figures(paper_id, file_id, page, rect, kind, caption, note, text, path, width, height, author_id, created_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (pid, file_id, page_no, str(list(map(float, rect))), kind, (caption if caption is not None else guessed)[:1500],
                       (note or "")[:2000], text, "", w, h, user_id, db.now()))
    fid = cur.lastrowid
    folder = FIG_DIR / str(pid)
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{fid}.png"
    out.write_bytes(png)
    con.execute("UPDATE figures SET path=? WHERE id=?", (str(out.relative_to(config.DATA_DIR)), fid))
    return fid


def preview(con, file_id: int, page_no: int, rect) -> dict:
    png, w, h, text, caption = render(_pdf_path(con, file_id), page_no, rect, dpi=110)
    return {"caption": caption, "text": text[:600], "width": w, "height": h}


def file_path(fig: dict) -> Path:
    return (config.DATA_DIR / fig["path"]).resolve()


def to_latex(con, fig_id: int) -> str:
    f = con.execute("SELECT * FROM figures WHERE id=?", (fig_id,)).fetchone()
    if f is None:
        raise ValueError("找不到圖卡")
    png = file_path(dict(f)).read_bytes()
    system = "你把論文截圖裡的數學公式轉成 LaTeX。只輸出 LaTeX 本身（不要 $ 或 ``` 包起來、不要解釋）。多行公式用 aligned 環境。看不出來的符號用 \\text{?} 標記。"
    out = ai.vision(con, system, "把這張圖裡的公式轉成 LaTeX。" + (f"（框內文字參考：{f['text'][:500]}）" if f["text"] else ""), png)
    out = re.sub(r"^```(?:latex|tex)?\s*|\s*```$", "", out.strip())
    out = out.strip().strip("$").strip()
    con.execute("UPDATE figures SET latex=? WHERE id=?", (out[:4000], fig_id))
    return out


def delete(con, fig: dict) -> None:
    con.execute("DELETE FROM figures WHERE id=?", (fig["id"],))
    try:
        file_path(fig).unlink(missing_ok=True)
    except OSError:
        pass
