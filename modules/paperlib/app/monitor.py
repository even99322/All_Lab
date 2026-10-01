"""網站狀態監控（給控制台程式與「管理」頁用）：
- 請求統計：每分鐘的請求數、平均／最慢回應時間、錯誤數（保留 24 小時，只存在記憶體）
- 錯誤紀錄：程式的警告與錯誤（最近 300 筆）
- 狀態總覽：版本、啟動時間、資料庫與磁碟、帳號與論文數、背景工作、設定是否完整
"""
import collections
import logging
import os
import platform
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

from . import config, db, jobs, ocr, settings

STARTED = time.time()
_lock = threading.Lock()
_minutes: "collections.OrderedDict[int, dict]" = collections.OrderedDict()
_active: dict[str, float] = {}          # user_id -> 最後一次請求時間（線上人數）
LOGS: "collections.deque[dict]" = collections.deque(maxlen=300)


def record(path: str, status: int, ms: float, user_id=None) -> None:
    if path.startswith("/static/vendor/"):
        return
    m = int(time.time() // 60)
    with _lock:
        b = _minutes.get(m)
        if b is None:
            b = _minutes[m] = {"n": 0, "err": 0, "c4": 0, "ms": 0.0, "max": 0.0, "slow": ""}
            while len(_minutes) > 1440:
                _minutes.popitem(last=False)
        b["n"] += 1
        b["ms"] += ms
        if ms > b["max"]:
            b["max"], b["slow"] = ms, path
        if status >= 500:
            b["err"] += 1
        elif status >= 400 and status not in (401, 404):
            b["c4"] += 1
        if user_id:
            _active[str(user_id)] = time.time()


def traffic(minutes: int = 60) -> dict:
    now = int(time.time() // 60)
    with _lock:
        rows = [(m, dict(b)) for m, b in _minutes.items() if m > now - minutes]
        online = sum(1 for t in _active.values() if time.time() - t < 900)
    series = []
    d = dict(rows)
    for m in range(now - minutes + 1, now + 1):
        b = d.get(m)
        series.append({"t": m * 60, "n": b["n"] if b else 0, "err": b["err"] if b else 0,
                       "avg": round(b["ms"] / b["n"], 1) if b and b["n"] else None, "max": round(b["max"], 1) if b else None})
    n = sum(b["n"] for _, b in rows)
    slowest = max(rows, key=lambda x: x[1]["max"], default=(0, {"max": 0, "slow": ""}))[1]
    return {"minutes": minutes, "requests": n, "errors": sum(b["err"] for _, b in rows), "client_errors": sum(b["c4"] for _, b in rows),
            "avg_ms": round(sum(b["ms"] for _, b in rows) / n, 1) if n else None,
            "slowest": {"ms": round(slowest["max"], 1), "path": slowest["slow"]}, "online": online, "series": series}


class _Ring(logging.Handler):
    def emit(self, rec: logging.LogRecord) -> None:
        try:
            msg = rec.getMessage()
            if rec.exc_info:
                msg += "\n" + "".join(traceback.format_exception(*rec.exc_info))[-3000:]
            LOGS.append({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "level": rec.levelname,
                         "src": rec.name, "msg": msg[:4000]})
        except Exception:  # noqa: BLE001
            pass


def install_logging() -> None:
    h = _Ring(level=logging.WARNING)
    for name in ("", "uvicorn.error", "app"):
        lg = logging.getLogger(name)
        if not any(isinstance(x, _Ring) for x in lg.handlers):
            lg.addHandler(h)


def log_error(where: str, exc: BaseException) -> None:
    LOGS.append({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "level": "ERROR", "src": where,
                 "msg": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:]})


def _dir_size(p) -> int:
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


_size_cache = {"t": 0.0, "v": {}}


def status() -> dict:
    con = db.get()
    s = settings.get(con)
    one = lambda q, a=(): con.execute(q, a).fetchone()[0]  # noqa: E731
    data = config.DATA_DIR
    du = shutil.disk_usage(data)
    if time.time() - _size_cache["t"] > 300:            # 資料夾大小每 5 分鐘才重算
        _size_cache["v"] = {"files": _dir_size(data / "files"), "total": _dir_size(data)}
        _size_cache["t"] = time.time()
    dbf = data / "library.db"
    backups = sorted(config.BACKUP_DIR.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    job_rows = [dict(r) for r in con.execute("SELECT id, kind, state, progress, result, created_at, finished_at FROM jobs ORDER BY id DESC LIMIT 25")]
    for j in job_rows:
        j["label"] = jobs.LABELS.get(j["kind"], j["kind"])
    if time.time() - _size_cache.get("qc_t", 0) > 600:        # 資料庫檢查每 10 分鐘一次
        try:
            _size_cache["qc"] = con.execute("PRAGMA quick_check").fetchone()[0]
        except Exception as e:  # noqa: BLE001
            _size_cache["qc"] = str(e)
        _size_cache["qc_t"] = time.time()
    integrity = _size_cache["qc"]
    return {
        "version": config.VERSION, "started_at": datetime.fromtimestamp(STARTED, timezone.utc).isoformat(timespec="seconds"),
        "uptime_s": int(time.time() - STARTED), "python": sys.version.split()[0], "platform": platform.platform(terse=True),
        "pid": os.getpid(), "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "db": {"size": dbf.stat().st_size if dbf.exists() else 0,
               "wal": (data / "library.db-wal").stat().st_size if (data / "library.db-wal").exists() else 0,
               "schema": db.meta_get(con, "schema_version"), "integrity": integrity},
        "disk": {"total": du.total, "used": du.used, "free": du.free, "data_bytes": _size_cache["v"].get("total", 0),
                 "pdf_bytes": _size_cache["v"].get("files", 0)},
        "counts": {"papers": one("SELECT COUNT(*) FROM papers"), "files": one("SELECT COUNT(*) FROM files"),
                   "annotations": one("SELECT COUNT(*) FROM annotations"), "users": one("SELECT COUNT(*) FROM users WHERE disabled=0"),
                   "registrations": one("SELECT COUNT(*) FROM registrations"), "figures": one("SELECT COUNT(*) FROM figures"),
                   "sessions": one("SELECT COUNT(*) FROM sessions WHERE expires_at > ?", (db.now(),)),
                   "feed_new": one("SELECT COUNT(*) FROM feed_items WHERE status='new'"),
                   "papers_7d": one("SELECT COUNT(*) FROM papers WHERE added_at >= ?", (_ago(7 * 86400),))},
        "jobs": {"queued": one("SELECT COUNT(*) FROM jobs WHERE state='queued'"), "running": one("SELECT COUNT(*) FROM jobs WHERE state='running'"),
                 "failed_24h": one("SELECT COUNT(*) FROM jobs WHERE state='failed' AND created_at >= ?", (_ago(86400),)),
                 "worker": config.RUN_JOBS, "recent": job_rows},
        "backup": {"last": backups[0].name if backups else None,
                   "last_at": datetime.fromtimestamp(backups[0].stat().st_mtime, timezone.utc).isoformat(timespec="seconds") if backups else None,
                   "count": len(backups)},
        "config": {"site_url": s["site_url"], "smtp": bool(s["smtp_host"]), "ai": bool(s["ai_provider"]), "translate": bool(s["tr_provider"]),
                   "ocr": ocr.available()["ok"], "openalex": bool(s["openalex_key"]), "register": s.get("register_enabled", "1") == "1",
                   "zotero": bool(s["zotero_id"]), "owner": bool(one("SELECT COUNT(*) FROM users WHERE owner=1"))},
        "traffic": traffic(60),
        "logs": {"errors_total": sum(1 for x in LOGS if x["level"] in ("ERROR", "CRITICAL")), "warnings_total": sum(1 for x in LOGS if x["level"] == "WARNING")},
    }


def _ago(sec: int) -> str:
    return datetime.fromtimestamp(time.time() - sec, timezone.utc).isoformat(timespec="seconds")
