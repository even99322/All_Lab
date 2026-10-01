"""掃描檔 OCR：用 OCRmyPDF（Tesseract）在原頁面上加一層看不見的文字，之後就能搜尋、選字、翻譯。

原始檔會移到 data/trash/，並記住原本的 SHA-256，再次上傳同一個掃描檔仍會被判定為重複。
"""
import shutil
import subprocess
import sys
from datetime import datetime

from . import config, db, library, pdftools, settings
from .jobs import enqueue, handler


def available() -> dict:
    try:
        import ocrmypdf  # noqa: F401
        mod = True
    except ImportError:
        mod = False
    tess = shutil.which("tesseract")
    langs = []
    if tess:
        try:
            out = subprocess.run([tess, "--list-langs"], capture_output=True, text=True, timeout=20).stdout
            langs = [x.strip() for x in out.splitlines()[1:] if x.strip() and x.strip() != "osd"]
        except Exception:  # noqa: BLE001
            pass
    return {"ok": mod and bool(tess), "ocrmypdf": mod, "tesseract": bool(tess), "langs": langs}


def queue_if_needed(con, fid: int, user_id=None) -> bool:
    f = con.execute("SELECT has_text, ocr_state FROM files WHERE id=?", (fid,)).fetchone()
    if f and f["has_text"] == 0 and f["ocr_state"] in ("", "failed") and settings.get(con)["ocr_auto"] == "1" and available()["ok"]:
        con.execute("UPDATE files SET ocr_state='queued' WHERE id=?", (fid,))
        enqueue("ocr", str(fid), user_id)
        return True
    return False


def run(con, fid: int, progress=lambda m: None) -> str:
    f = con.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
    if f is None:
        return "檔案已不存在"
    src = (config.DATA_DIR / f["path"]).resolve()
    tmp = src.with_suffix(".ocr.pdf")
    have = available()["langs"]
    want = [x.strip() for x in (settings.get(con)["ocr_langs"] or "eng").split("+") if x.strip()]
    langs = [x for x in want if x in have] or (["eng"] if "eng" in have else have[:1])
    con.execute("UPDATE files SET ocr_state='running' WHERE id=?", (fid,))
    progress(f"{f['filename']}（{'+'.join(langs)}）")
    try:
        # 用獨立行程跑（OCRmyPDF 不建議在執行緒裡呼叫），掃描頁才辨識，已有文字的頁略過
        r = subprocess.run([sys.executable, "-m", "ocrmypdf", "-l", "+".join(langs), "--output-type", "pdf",
                            "--skip-text", "--optimize", "0", "--jobs", "2", "--quiet", str(src), str(tmp)],
                           capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or f"結束代碼 {r.returncode}").strip()[-400:])
    except Exception as e:
        tmp.unlink(missing_ok=True)
        con.execute("UPDATE files SET ocr_state='failed' WHERE id=?", (fid,))
        raise RuntimeError(f"OCR 失敗：{e}") from None
    backup = config.TRASH_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-ocr-original-{fid}-{f['filename']}"
    shutil.move(str(src), backup)
    shutil.move(str(tmp), src)
    sha = library.sha256_file(src)
    ok = pdftools.has_text_layer(src)
    with db.tx() as c:
        c.execute("UPDATE files SET sha256=?, orig_sha256=COALESCE(orig_sha256, ?), size=?, has_text=?, ocr_state='done' WHERE id=?",
                  (sha, f["sha256"], src.stat().st_size, int(ok), fid))
        if library.main_file_id(c, f["paper_id"]) == fid:
            db.fts_update(c, f["paper_id"], pdftools.full_text(src))
        db.log(c, None, "ocr", f["paper_id"], f["filename"])
    (config.THUMBS_DIR / f"{fid}.jpg").unlink(missing_ok=True)
    enqueue("refs", str(f["paper_id"]))
    return f"{f['filename']}：完成" + ("" if ok else "（辨識到的文字很少）")


@handler("ocr")
def _job(con, arg, progress):
    return run(con, int(arg), progress)


@handler("scan_text")
def _scan(con, arg, progress):
    """檢查既有檔案有沒有文字層（升級後跑一次）。"""
    rows = con.execute("SELECT id, path FROM files WHERE has_text IS NULL").fetchall()
    n = 0
    for i, r in enumerate(rows):
        try:
            v = int(pdftools.has_text_layer(config.DATA_DIR / r["path"]))
        except Exception:  # noqa: BLE001
            v = None
        con.execute("UPDATE files SET has_text=? WHERE id=?", (v, r["id"]))
        n += v == 0
        if i % 20 == 0:
            progress(f"{i + 1}/{len(rows)}")
    q = 0
    for r in con.execute("SELECT id FROM files WHERE has_text=0 AND ocr_state=''").fetchall():
        q += queue_if_needed(con, r["id"])
    return f"檢查 {len(rows)} 個檔案，{n} 個沒有文字層" + (f"，已排入 OCR {q} 個" if q else "")
