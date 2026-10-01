"""背景工作：一條工作執行緒依序處理（OCR、引用分析、新論文追蹤、Zotero、摘要），另一條每分鐘檢查排程。

工作存在 jobs 表，網站重開也不會遺失；同類工作已在排隊時不會重複加入。
"""
import json
import threading
import time
import traceback
from datetime import datetime, timedelta

from . import db, settings

_wake = threading.Event()
_stop = threading.Event()
_threads: list[threading.Thread] = []
HANDLERS: dict = {}          # kind -> fn(con, arg, progress) -> str（結果說明）
LABELS = {"scan_text": "檢查 PDF 文字層", "refs": "分析參考文獻、建立引用關聯", "ocr": "OCR 文字辨識",
          "feeds": "檢查新論文", "zotero": "Zotero 同步", "digest": "寄送每週摘要", "feed_add": "加入追蹤到的論文",
          "ai_batch": "AI 批次填寫重點欄", "ai_review": "AI 產生綜述", "params": "AI 抽取論文參數", "slides": "產生投影片",
          "embed": "建立語意搜尋向量", "similar_index": "建立相似度索引", "versions": "檢查預印本是否已發表、找重複論文"}


def handler(kind):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def enqueue(kind: str, arg="", user_id=None, dedupe: bool = True) -> int:
    con = db.get()
    arg = arg if isinstance(arg, str) else json.dumps(arg)
    if dedupe:
        r = con.execute("SELECT id FROM jobs WHERE kind=? AND arg=? AND state IN ('queued','running')", (kind, arg)).fetchone()
        if r:
            return r["id"]
    cur = con.execute("INSERT INTO jobs(kind, arg, created_by, created_at) VALUES(?,?,?,?)", (kind, arg, user_id, db.now()))
    _wake.set()
    return cur.lastrowid


def _run_one(con) -> bool:
    j = con.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
    if j is None:
        return False
    con.execute("UPDATE jobs SET state='running', progress='' WHERE id=?", (j["id"],))
    fn = HANDLERS.get(j["kind"])

    def progress(msg: str):
        con.execute("UPDATE jobs SET progress=? WHERE id=?", (str(msg)[:300], j["id"]))
    try:
        if fn is None:
            raise RuntimeError(f"未知的工作類型：{j['kind']}")
        result = fn(con, j["arg"], progress) or ""
        con.execute("UPDATE jobs SET state='done', result=?, finished_at=? WHERE id=?", (str(result)[:2000], db.now(), j["id"]))
    except Exception as e:  # noqa: BLE001 - 單一工作失敗不影響其他工作
        traceback.print_exc()
        con.execute("UPDATE jobs SET state='failed', result=?, finished_at=? WHERE id=?", (str(e)[:2000], db.now(), j["id"]))
    return True


def _worker():
    con = db.get()
    while not _stop.is_set():
        try:
            while not _stop.is_set() and _run_one(con):
                pass
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        _wake.wait(30)
        _wake.clear()


def _due(con, key: str, hour: int, dow: int | None = None) -> bool:
    """今天（或本週指定星期）過了指定時刻、且還沒跑過。"""
    now = datetime.now()
    if dow is not None and now.weekday() != dow:
        return False
    if now.hour < hour:
        return False
    stamp = now.strftime("%Y-%m-%d")
    if db.meta_get(con, key) == stamp:
        return False
    db.meta_set(con, key, stamp)
    return True


def _scheduler():
    con = db.get()
    while not _stop.wait(60):
        try:
            s = settings.get(con)
            if s["feed_enabled"] == "1" and _due(con, "last_feed_day", int(s["feed_hour"] or 7)):
                enqueue("feeds", "")
            if s["zotero_auto"] == "1" and s["zotero_id"] and _due(con, "last_zotero_day", 3):
                enqueue("zotero", "")
            if s["digest_enabled"] == "1" and _due(con, "last_digest_day", int(s["digest_hour"] or 9), int(s["digest_dow"] or 0)):
                enqueue("digest", "")
            if _due(con, "last_versions_day", 4, 6):           # 每週日清晨：預印本是否已發表、重複論文
                enqueue("versions", "")
            if s["emb_provider"] and _due(con, "last_embed_day", 2):   # 每天清晨補齊新論文的向量
                enqueue("embed", "")
            # 清掉一個月前的完成紀錄
            cut = (datetime.now() - timedelta(days=30)).isoformat()
            con.execute("DELETE FROM jobs WHERE state IN ('done','failed') AND created_at < ?", (cut,))
        except Exception:  # noqa: BLE001
            traceback.print_exc()


def start():
    if _threads:
        return
    from . import ai, feeds, notify, ocr, params, refs, semantic, slides, versions, zotero  # noqa: F401  註冊 handler
    _stop.clear()
    for fn in (_worker, _scheduler):
        t = threading.Thread(target=fn, daemon=True, name=f"paperlib-{fn.__name__}")
        t.start()
        _threads.append(t)


def stop():
    _stop.set()
    _wake.set()


def wait_idle(timeout: float = 60) -> bool:
    """測試用：等所有工作跑完。"""
    end = time.time() + timeout
    while time.time() < end:
        if not db.get().execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone():
            return True
        time.sleep(0.3)
    return False


def recent(con, limit=30, kind=None):
    q, args = "SELECT j.*, u.display_name AS who FROM jobs j LEFT JOIN users u ON u.id=j.created_by", []
    if kind:
        q += " WHERE j.kind=?"; args.append(kind)
    rows = [dict(r) for r in con.execute(q + " ORDER BY j.id DESC LIMIT ?", args + [limit])]
    for r in rows:
        r["label"] = LABELS.get(r["kind"], r["kind"])
    return rows
