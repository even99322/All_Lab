"""Hub 的數據索引 API（1.0.4）：讀 NAS 上索引程式（labhub/indexer.py）算好的結果，給讀檔程式用。

    GET /api/index/status                       → 索引狀態（檔案數、進度、上次完成時間、解析程式版本）
    GET /api/index/tree?root=<相對路徑>          → 這個資料夾底下所有檔的資訊與所有子資料夾（gzip）
    GET /api/index/preview?path=&size=&mtime=   → 一個檔的預設預覽（.npz，labhub/index_codec.py）；
                                                   大小或時間對不上（檔案改過、還沒重新索引）→ 404

路徑都是相對於「數據集散地」、用 / 分隔。權限：登入的使用者 / 裝置 token，或 X-QEL-Share-Key 標頭帶
數據集散地 .qel/index_key 的內容（讀得到數據集散地 = 可以看這些數據）。只用標準函式庫。
"""
from __future__ import annotations

import gzip
import hmac
import json
import re
import sqlite3
from typing import Any, Dict, Optional

from .indexer import connect, db_path, get_meta, key_path, preview_path
from .server import Handler, HubHTTPError, route

MTIME_TOLERANCE = 2.0          # 秒：SMB 看到的時間與 NAS 上的時間可能差一點點


def norm_rel(text: str) -> str:
    parts = [p for p in re.split(r"[\\/]+", text or "") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise HubHTTPError(400, "路徑無效")
    return "/".join(parts)


def _share_key(h: Handler) -> str:
    try:
        return key_path(h.hub.cfg.data_dir).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _allowed(h: Handler) -> None:
    given = (h.headers.get("X-QEL-Share-Key") or "").strip()
    if given:
        key = _share_key(h)
        if key and hmac.compare_digest(given.encode(), key.encode()):
            return
    if h.principal() is not None and h.check("any") is None:
        return
    raise HubHTTPError(401, "需要登入，或數據集散地的 .qel/index_key")


def _db(h: Handler) -> sqlite3.Connection:
    path = db_path(h.hub.cfg.data_dir)
    if not path.is_file():
        raise HubHTTPError(404, "NAS 上還沒有數據索引（docker-compose.yml 加上 indexer 服務）")
    try:
        return connect(path, readonly=True)
    except sqlite3.Error as e:
        raise HubHTTPError(503, f"數據索引暫時讀不到：{e}")


def _send_json(h: Handler, obj: Any) -> None:
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if "gzip" in (h.headers.get("Accept-Encoding") or "") and len(body) > 2048:
        h._send(200, gzip.compress(body, 6), "application/json; charset=utf-8", {"Content-Encoding": "gzip"})
    else:
        h._send(200, body, "application/json; charset=utf-8")


def status_dict(con: sqlite3.Connection) -> Dict[str, Any]:
    meta = get_meta(con)
    keys = ("state", "files", "folders", "todo", "done", "pending", "indexed", "errors", "last_pass",
            "pass_seconds", "complete", "llv_version", "parser", "schema", "error")
    return {k: meta.get(k) for k in keys}


@route("GET", r"/api/index/status", auth=False)
def _status(h: Handler) -> None:
    _allowed(h)
    con = _db(h)
    try:
        h._json(dict(status_dict(con), ok=True))
    finally:
        con.close()


def _entry(row: sqlite3.Row, cut: int) -> Dict[str, Any]:
    tags = row["file_tags"]
    return {"rel": row["rel"][cut:], "size": row["size"], "mtime": row["mtime"], "status": row["status"],
            "error": row["error"], "log_name": row["log_name"], "creation_time": row["creation_time"],
            "sweep_dimension": row["sweep_dimension"] or "",
            "file_tags": json.loads(tags) if tags else ([] if tags == "" else None),
            "file_star": None if row["file_star"] is None else bool(row["file_star"]),
            "preview": row["preview"] == "ok"}


@route("GET", r"/api/index/tree", auth=False)
def _tree(h: Handler) -> None:
    _allowed(h)
    root = norm_rel(h._query().get("root", ""))
    con = _db(h)
    try:
        if root:
            lo, hi = root + "/", root + "0"                  # "0" 緊接在 "/" 後面：前綴範圍
            rows = con.execute("SELECT * FROM files WHERE rel >= ? AND rel < ?", (lo, hi)).fetchall()
            folders = [r["rel"][len(lo):] for r in
                       con.execute("SELECT rel FROM folders WHERE rel >= ? AND rel < ?", (lo, hi))]
            cut = len(lo)
            exists = bool(rows or folders) or con.execute("SELECT 1 FROM folders WHERE rel = ?", (root,)).fetchone()
        else:
            rows = con.execute("SELECT * FROM files").fetchall()
            folders = [r["rel"] for r in con.execute("SELECT rel FROM folders")]
            cut, exists = 0, True
        if not exists:
            raise HubHTTPError(404, f"索引裡沒有 {root}")
        _send_json(h, {"ok": True, "root": root, "status": status_dict(con),
                       "folders": folders, "entries": [_entry(r, cut) for r in rows]})
    finally:
        con.close()


@route("GET", r"/api/index/preview", auth=False)
def _preview(h: Handler) -> None:
    _allowed(h)
    q = h._query()
    rel = norm_rel(q.get("path", ""))
    con = _db(h)
    try:
        row: Optional[sqlite3.Row] = con.execute("SELECT size, mtime, preview FROM files WHERE rel = ?",
                                                 (rel,)).fetchone()
    finally:
        con.close()
    if row is None or row["preview"] != "ok":
        raise HubHTTPError(404, "這個檔沒有算好的預覽")
    try:
        size, mtime = int(q.get("size", row["size"])), float(q.get("mtime", row["mtime"]))
    except ValueError:
        raise HubHTTPError(400, "size / mtime 格式錯誤")
    if size != row["size"] or abs(mtime - (row["mtime"] or 0)) > MTIME_TOLERANCE:
        raise HubHTTPError(404, "檔案改過了，預覽還沒更新")
    try:
        blob = preview_path(rel, h.hub.cfg.data_dir).read_bytes()
    except OSError:
        raise HubHTTPError(404, "預覽檔不見了")
    h._send(200, blob, "application/octet-stream")

