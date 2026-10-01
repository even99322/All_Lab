"""資料庫（SQLite, WAL）：登入、模塊權限、共用標籤、數據登錄、事件、發佈版本、操作紀錄。

帳號本身不存在這裡（以論文庫為準）；這裡只存「大程式」自己的東西。
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, user_json TEXT NOT NULL, pl_cookie TEXT NOT NULL DEFAULT '',
  client TEXT NOT NULL DEFAULT '', ip TEXT NOT NULL DEFAULT '', created REAL NOT NULL, expires REAL NOT NULL,
  last_seen REAL NOT NULL, last_check REAL NOT NULL);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(username);
CREATE TABLE IF NOT EXISTS tickets(ticket_hash TEXT PRIMARY KEY, token_hash TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS access(
  username TEXT NOT NULL COLLATE NOCASE, module TEXT NOT NULL, enabled INTEGER NOT NULL,
  updated_by TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL, PRIMARY KEY(username, module));
CREATE TABLE IF NOT EXISTS user_flags(
  username TEXT PRIMARY KEY COLLATE NOCASE, blocked INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
  updated_by TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tags(
  name TEXT PRIMARY KEY COLLATE NOCASE, category TEXT NOT NULL DEFAULT 'Other', color TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '', aliases TEXT NOT NULL DEFAULT '[]', sort INTEGER NOT NULL DEFAULT 0,
  created_by TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tag_papers(
  tag TEXT NOT NULL COLLATE NOCASE REFERENCES tags(name) ON DELETE CASCADE ON UPDATE CASCADE,
  paper_id INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '', added_by TEXT NOT NULL DEFAULT '',
  added_at REAL NOT NULL, PRIMARY KEY(tag, paper_id));
CREATE TABLE IF NOT EXISTS datasets(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL DEFAULT '', fingerprint TEXT NOT NULL DEFAULT '',
  hub_node TEXT NOT NULL DEFAULT '', hub_rel TEXT NOT NULL DEFAULT '', scheme TEXT, source TEXT NOT NULL DEFAULT '{}',
  meta TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
  updated_at REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS datasets_fp ON datasets(fingerprint);
CREATE INDEX IF NOT EXISTS datasets_path ON datasets(path);
CREATE TABLE IF NOT EXISTS dataset_tags(
  dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE, tag TEXT NOT NULL COLLATE NOCASE,
  PRIMARY KEY(dataset_id, tag));
CREATE INDEX IF NOT EXISTS dataset_tags_tag ON dataset_tags(tag);
CREATE TABLE IF NOT EXISTS events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, data TEXT NOT NULL, target TEXT,
  by TEXT NOT NULL DEFAULT '', time REAL NOT NULL);
CREATE TABLE IF NOT EXISTS releases(
  module TEXT NOT NULL, version TEXT NOT NULL, file TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '', manifest TEXT NOT NULL DEFAULT '{}', uploaded_by TEXT NOT NULL DEFAULT '',
  uploaded_at REAL NOT NULL, withdrawn INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(module, version));
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, time REAL NOT NULL, username TEXT NOT NULL DEFAULT '', action TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '', ip TEXT NOT NULL DEFAULT '');
"""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.write_lock = threading.RLock()
        con = self.con()
        con.executescript(SCHEMA)
        con.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema', ?)", (str(SCHEMA_VERSION),))

    def con(self) -> sqlite3.Connection:
        c = getattr(self._local, "con", None)
        if c is None:
            c = sqlite3.connect(str(self.path), timeout=30, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.con = c
        return c

    @contextlib.contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.write_lock:
            c = self.con()
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise

    def q(self, sql: str, args: tuple = ()) -> List[sqlite3.Row]:
        return self.con().execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple = ()) -> Optional[sqlite3.Row]:
        return self.con().execute(sql, args).fetchone()

    # ---- 設定 ------------------------------------------------------------------
    def setting(self, key: str, default: Any = None) -> Any:
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        if r is None:
            return default
        try:
            return json.loads(r["value"])
        except ValueError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(value, ensure_ascii=False)))

    # ---- 操作紀錄 ----------------------------------------------------------------
    def audit(self, username: str, action: str, detail: str = "", ip: str = "") -> None:
        with self.tx() as c:
            c.execute("INSERT INTO audit(time, username, action, detail, ip) VALUES(?,?,?,?,?)",
                      (time.time(), username, action, detail[:2000], ip))
            c.execute("DELETE FROM audit WHERE id <= (SELECT MAX(id) FROM audit) - 20000")


def loads(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default
