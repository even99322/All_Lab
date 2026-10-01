"""SQLite 資料庫：結構、連線與常用查詢。單一檔案 library.db，放在 NAS 的資料夾。"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
  pw_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member', disabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS categories(
  id INTEGER PRIMARY KEY, key TEXT UNIQUE, name TEXT UNIQUE NOT NULL, grp TEXT NOT NULL DEFAULT '架設類型',
  description TEXT NOT NULL DEFAULT '', color TEXT NOT NULL DEFAULT '#5d6d7e', sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS papers(
  id INTEGER PRIMARY KEY, citekey TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
  authors TEXT NOT NULL DEFAULT '[]', authors_complete INTEGER NOT NULL DEFAULT 0,
  year INTEGER, venue TEXT NOT NULL DEFAULT '', doi TEXT NOT NULL DEFAULT '', arxiv TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'article', abstract TEXT NOT NULL DEFAULT '', status TEXT,
  suggested_category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
  keyinfo TEXT NOT NULL DEFAULT '{}', added_by INTEGER, added_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS papers_doi ON papers(lower(doi));
CREATE TABLE IF NOT EXISTS paper_categories(
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  PRIMARY KEY(paper_id, category_id));
CREATE TABLE IF NOT EXISTS tags(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS paper_tags(
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY(paper_id, tag_id));
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY, paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'main', label TEXT NOT NULL DEFAULT '', filename TEXT NOT NULL,
  original_name TEXT NOT NULL DEFAULT '', sha256 TEXT UNIQUE NOT NULL, size INTEGER, pages INTEGER,
  path TEXT NOT NULL, added_by INTEGER, added_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS annotations(
  id INTEGER PRIMARY KEY, paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  file_id INTEGER REFERENCES files(id) ON DELETE SET NULL, page INTEGER,
  kind TEXT NOT NULL DEFAULT 'note', color TEXT NOT NULL DEFAULT 'yellow',
  quote TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '', rects TEXT NOT NULL DEFAULT '[]',
  private INTEGER NOT NULL DEFAULT 0, author_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ann_paper ON annotations(paper_id);
CREATE TABLE IF NOT EXISTS links(
  id INTEGER PRIMARY KEY, src_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  dst_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE, rel TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '', author_id INTEGER, created_at TEXT NOT NULL,
  UNIQUE(src_id, dst_id, rel));
CREATE TABLE IF NOT EXISTS activity(
  id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT NOT NULL, paper_id INTEGER,
  detail TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS translations(
  hash TEXT PRIMARY KEY, provider TEXT NOT NULL, target TEXT NOT NULL, src TEXT NOT NULL, out TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS folders(
  id INTEGER PRIMARY KEY, category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  name TEXT NOT NULL, sort INTEGER NOT NULL DEFAULT 0, UNIQUE(category_id, name));
CREATE TABLE IF NOT EXISTS login_fail(key TEXT PRIMARY KEY, n INTEGER NOT NULL, first_at REAL NOT NULL, until REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS user_paper(
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  status TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(user_id, paper_id));
CREATE TABLE IF NOT EXISTS paper_assign(
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  assigned_by INTEGER, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, PRIMARY KEY(paper_id, user_id));
CREATE INDEX IF NOT EXISTS paper_assign_user ON paper_assign(user_id);
CREATE TABLE IF NOT EXISTS ai_reports(
  id INTEGER PRIMARY KEY, scope TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, refs TEXT NOT NULL DEFAULT '[]',
  n_papers INTEGER NOT NULL DEFAULT 0, model TEXT NOT NULL DEFAULT '', created_by INTEGER, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ai_reports_scope ON ai_reports(scope, id);
CREATE TABLE IF NOT EXISTS ref_ignore(src_id INTEGER NOT NULL, dst_id INTEGER NOT NULL, PRIMARY KEY(src_id, dst_id));
CREATE TABLE IF NOT EXISTS feeds(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'arxiv', keywords TEXT NOT NULL DEFAULT '',
  categories TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1, last_run TEXT, last_error TEXT NOT NULL DEFAULT '', created_by INTEGER,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS feed_items(
  id INTEGER PRIMARY KEY, feed_id INTEGER REFERENCES feeds(id) ON DELETE CASCADE, uid TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL, authors TEXT NOT NULL DEFAULT '[]', abstract TEXT NOT NULL DEFAULT '', published TEXT,
  url TEXT NOT NULL DEFAULT '', pdf_url TEXT NOT NULL DEFAULT '', doi TEXT NOT NULL DEFAULT '', arxiv TEXT NOT NULL DEFAULT '',
  venue TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'new', paper_id INTEGER, found_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS feed_items_status ON feed_items(status);
CREATE TABLE IF NOT EXISTS meetings(
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
  created_by INTEGER, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meeting_items(
  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  paper_id INTEGER REFERENCES papers(id) ON DELETE CASCADE, presenter_id INTEGER, note TEXT NOT NULL DEFAULT '',
  sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS ann_replies(
  id INTEGER PRIMARY KEY, ann_id INTEGER NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
  author_id INTEGER, body TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, kind TEXT NOT NULL,
  actor_id INTEGER, paper_id INTEGER, ann_id INTEGER, meeting_id INTEGER, text TEXT NOT NULL DEFAULT '',
  read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS notif_user ON notifications(user_id, read);
CREATE TABLE IF NOT EXISTS ai_log(
  id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL,
  refs TEXT NOT NULL DEFAULT '[]', scope TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS zotero_map(
  paper_id INTEGER PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE, item_key TEXT NOT NULL, item_version INTEGER,
  note_key TEXT, note_version INTEGER, link_key TEXT, synced_at TEXT);
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, arg TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'queued',
  progress TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '', created_by INTEGER, created_at TEXT NOT NULL,
  finished_at TEXT);
CREATE TABLE IF NOT EXISTS param_defs(
  id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, name TEXT NOT NULL, symbol TEXT NOT NULL DEFAULT '',
  aliases TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT '', grp TEXT NOT NULL DEFAULT '', definition TEXT NOT NULL DEFAULT '',
  convention TEXT NOT NULL DEFAULT '', cqed TEXT NOT NULL DEFAULT '', in_compare INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS param_typical(
  id INTEGER PRIMARY KEY, platform TEXT NOT NULL, key TEXT NOT NULL, range_text TEXT NOT NULL DEFAULT '', lo REAL, hi REAL,
  note TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS param_map(
  id INTEGER PRIMARY KEY, grp TEXT NOT NULL DEFAULT '', magnon TEXT NOT NULL, cqed TEXT NOT NULL DEFAULT '', cavity TEXT NOT NULL DEFAULT '',
  formula TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS paper_params(
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE, key TEXT NOT NULL, value TEXT NOT NULL DEFAULT '',
  num REAL, raw TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', page INTEGER, source TEXT NOT NULL DEFAULT 'human',
  author_id INTEGER, updated_at TEXT NOT NULL, PRIMARY KEY(paper_id, key));
CREATE TABLE IF NOT EXISTS figures(
  id INTEGER PRIMARY KEY, paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  file_id INTEGER REFERENCES files(id) ON DELETE SET NULL, page INTEGER, rect TEXT NOT NULL DEFAULT '[]',
  kind TEXT NOT NULL DEFAULT 'figure', caption TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', latex TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL DEFAULT '', path TEXT NOT NULL, width INTEGER, height INTEGER, author_id INTEGER, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS figures_paper ON figures(paper_id);
CREATE TABLE IF NOT EXISTS paths(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_by INTEGER, created_at TEXT NOT NULL,
  sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS path_items(
  id INTEGER PRIMARY KEY, path_id INTEGER NOT NULL REFERENCES paths(id) ON DELETE CASCADE,
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE, goal TEXT NOT NULL DEFAULT '', sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS path_members(
  path_id INTEGER NOT NULL REFERENCES paths(id) ON DELETE CASCADE, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  added_by INTEGER, created_at TEXT NOT NULL, PRIMARY KEY(path_id, user_id));
CREATE TABLE IF NOT EXISTS paper_views(
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE, user_id INTEGER NOT NULL, day TEXT NOT NULL,
  PRIMARY KEY(paper_id, user_id, day));
CREATE TABLE IF NOT EXISTS embeddings(
  paper_id INTEGER PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE, model TEXT NOT NULL, dim INTEGER NOT NULL,
  vec BLOB NOT NULL, sig TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS version_suggest(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  other_id INTEGER REFERENCES papers(id) ON DELETE CASCADE, doi TEXT NOT NULL DEFAULT '', venue TEXT NOT NULL DEFAULT '',
  year INTEGER, title TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', score REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'new', created_at TEXT NOT NULL, resolved_at TEXT, resolved_by INTEGER);
CREATE INDEX IF NOT EXISTS version_suggest_status ON version_suggest(status);
CREATE TABLE IF NOT EXISTS registrations(
  id INTEGER PRIMARY KEY, username TEXT NOT NULL, display_name TEXT NOT NULL, email TEXT NOT NULL, pw_hash TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '', ip TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notebooks(
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE, user_id INTEGER NOT NULL,
  pages INTEGER NOT NULL DEFAULT 1, bg TEXT NOT NULL DEFAULT 'lined', updated_at TEXT NOT NULL, PRIMARY KEY(paper_id, user_id));
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  paper_id UNINDEXED, title, authors, venue, body, notes, tokenize='trigram');
"""

AUTO_REL = "引用"
DEFAULT_RELS = ["引用", "延伸自", "使用其方法／公式", "實驗驗證其理論", "理論解釋其實驗", "對照／比較",
                "同一系列", "背景知識", "反駁／修正"]

_local = threading.local()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def get() -> sqlite3.Connection:
    con = getattr(_local, "con", None)
    if con is None:
        con = connect()
        _local.con = con
    return con


@contextmanager
def tx():
    con = get()
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def init() -> None:
    config.ensure_dirs()
    con = connect()
    con.executescript(SCHEMA)
    con.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', '1')")
    migrate(con)
    con.close()


def migrate(con) -> None:
    """舊資料庫升級：只加欄位／資料表，不動既有資料。"""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(users)")}
    if "perms" not in cols:
        con.execute("ALTER TABLE users ADD COLUMN perms TEXT NOT NULL DEFAULT '{}'")
    tcols = {r["name"] for r in con.execute("PRAGMA table_info(tags)")}
    for col, ddl in (("color", "TEXT NOT NULL DEFAULT '#6b7280'"), ("description", "TEXT NOT NULL DEFAULT ''"),
                     ("sort", "INTEGER NOT NULL DEFAULT 0")):
        if col not in tcols:
            con.execute(f"ALTER TABLE tags ADD COLUMN {col} {ddl}")
    def add_cols(table, spec):
        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        for col, ddl in spec:
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    add_cols("links", [("auto", "INTEGER NOT NULL DEFAULT 0")])
    add_cols("feeds", [("config", "TEXT NOT NULL DEFAULT '{}'")])
    add_cols("annotations", [("ink", "TEXT NOT NULL DEFAULT ''")])      # 手寫：{"strokes": [{c, w, o, p: [[x, y], ...]}]}
    # 手寫即時儲存：client_uid＝瀏覽器產生的編號（重送不會重複建立）；nb＝寫在「筆記頁」而不是 PDF 上
    add_cols("annotations", [("client_uid", "TEXT"), ("nb", "INTEGER NOT NULL DEFAULT 0")])
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS annotations_uid ON annotations(client_uid) WHERE client_uid IS NOT NULL")
    add_cols("paper_views", [("at", "TEXT")])                             # 最近開啟的時間（首頁「最近開啟」）
    add_cols("files", [("has_text", "INTEGER"), ("ocr_state", "TEXT NOT NULL DEFAULT ''"), ("orig_sha256", "TEXT")])
    add_cols("users", [("email", "TEXT NOT NULL DEFAULT ''"), ("digest", "INTEGER NOT NULL DEFAULT 1"), ("totp", "TEXT"),
                       ("owner", "INTEGER NOT NULL DEFAULT 0")])        # owner＝站長
    add_cols("paper_categories", [("folder_id", "INTEGER"), ("pinned", "INTEGER NOT NULL DEFAULT 0")])
    add_cols("papers", [("pinned", "INTEGER NOT NULL DEFAULT 0"), ("ai_keys", "TEXT NOT NULL DEFAULT '[]'"),
                        ("required", "INTEGER NOT NULL DEFAULT 0"), ("required_note", "TEXT NOT NULL DEFAULT ''")])
    con.execute("UPDATE jobs SET state='queued' WHERE state='running'")   # 重開機時中斷的工作重新排隊
    ver = int(con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()["value"])
    if ver < 2:
        # v2：網站改名為 LAB-QEL論文庫（寫進設定，舊 compose 的環境變數不會再蓋掉）
        con.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('site_name', 'LAB-QEL論文庫')")
        # 既有標籤配上不同顏色，跟分類區分
        palette = ["#d97706", "#7c3aed", "#0891b2", "#be185d", "#4d7c0f", "#b45309", "#1d4ed8", "#9f1239"]
        for i, t in enumerate(con.execute("SELECT id FROM tags ORDER BY id").fetchall()):
            con.execute("UPDATE tags SET color=?, sort=? WHERE id=?", (palette[i % len(palette)], i, t["id"]))
        con.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
        ver = 2
    if ver < 3:
        # v3：預設一個 arXiv 追蹤、掃描既有檔案的文字層、建立自動引用關聯
        if not con.execute("SELECT 1 FROM feeds").fetchone():
            con.execute("INSERT INTO feeds(name, kind, keywords, categories, tags, created_at) VALUES(?,?,?,?,?,?)",
                        ("arXiv：magnon", "arxiv", "magnon, magnonic, cavity magnonics, yttrium iron garnet",
                         "cond-mat.mes-hall, quant-ph, physics.optics, physics.app-ph", "[]", now()))
        for kind in ("scan_text", "refs"):
            con.execute("INSERT INTO jobs(kind, created_at) VALUES(?,?)", (kind, now()))
        con.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
        ver = 3
    if ver < 4:
        # v4：閱讀狀態改成每個人自己的（私人）。舊的共用狀態複製給每位現有成員，之後各自調整
        t = now()
        users = [r["id"] for r in con.execute("SELECT id FROM users")]
        for r in con.execute("SELECT id, status FROM papers WHERE status IS NOT NULL").fetchall():
            st = "已閱讀" if r["status"] == "已讀" else r["status"]
            for uid in users:
                con.execute("INSERT OR IGNORE INTO user_paper(user_id, paper_id, status, updated_at) VALUES(?,?,?,?)", (uid, r["id"], st, t))
        con.execute("UPDATE papers SET status=NULL")
        con.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
        ver = 4
    if ver < 5:
        # v5：參數對照表、預估值表、magnon↔qubit／cavity 對照（預設內容，之後可在網站上改）；檢查預印本是否已發表
        from . import params
        params.seed(con)
        con.execute("INSERT INTO jobs(kind, created_at) VALUES('versions', ?)", (now(),))
        con.execute("UPDATE meta SET value='5' WHERE key='schema_version'")


def meta_get(con, key: str, default=None):
    r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def meta_set(con, key: str, value) -> None:
    con.execute("INSERT INTO meta(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))


def log(con, user_id, action, paper_id=None, detail=""):
    con.execute("INSERT INTO activity(user_id, action, paper_id, detail, at) VALUES(?,?,?,?,?)",
                (user_id, action, paper_id, detail[:500], now()))


# ------------------------------------------------------------------ 全文索引
def fts_update(con, paper_id: int, body: str | None = None) -> None:
    """重建一篇論文的索引列。body=None 時沿用舊的全文（只更新書目與筆記）。"""
    p = con.execute("SELECT title, authors, venue, abstract, keyinfo FROM papers WHERE id=?", (paper_id,)).fetchone()
    if p is None:
        con.execute("DELETE FROM fts WHERE paper_id=?", (paper_id,))
        return
    if body is None:
        old = con.execute("SELECT body FROM fts WHERE paper_id=?", (paper_id,)).fetchone()
        body = old["body"] if old else ""
    notes = [p["abstract"] or ""]
    try:
        notes += [f"{k} {v}" for k, v in json.loads(p["keyinfo"] or "{}").items()]
    except ValueError:
        pass
    for a in con.execute("SELECT quote, body FROM annotations WHERE paper_id=? AND private=0", (paper_id,)):
        notes.append(a["quote"]); notes.append(a["body"])
    authors = " ".join(json.loads(p["authors"] or "[]"))
    con.execute("DELETE FROM fts WHERE paper_id=?", (paper_id,))
    con.execute("INSERT INTO fts(paper_id, title, authors, venue, body, notes) VALUES(?,?,?,?,?,?)",
                (paper_id, p["title"], authors, p["venue"], body or "", "\n".join(x for x in notes if x)))


# ------------------------------------------------------------------ 標籤、分類
def set_tags(con, paper_id: int, names: list[str]) -> None:
    con.execute("DELETE FROM paper_tags WHERE paper_id=?", (paper_id,))
    for n in dict.fromkeys(x.strip() for x in names if x and x.strip()):
        con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (n[:60],))
        tid = con.execute("SELECT id FROM tags WHERE name=?", (n[:60],)).fetchone()["id"]
        con.execute("INSERT OR IGNORE INTO paper_tags(paper_id, tag_id) VALUES(?,?)", (paper_id, tid))
    # 標籤是獨立清單：沒有論文的標籤也保留（由「管理 → 標籤」刪除）


def set_categories(con, paper_id: int, cat_ids: list[int]) -> None:
    """只增刪有變動的分類，保留原本在各分類裡的資料夾與置頂。"""
    want = list(dict.fromkeys(int(x) for x in cat_ids))
    have = {r["category_id"] for r in con.execute("SELECT category_id FROM paper_categories WHERE paper_id=?", (paper_id,))}
    for c in have - set(want):
        con.execute("DELETE FROM paper_categories WHERE paper_id=? AND category_id=?", (paper_id, c))
    for c in want:
        if c not in have:
            con.execute("INSERT OR IGNORE INTO paper_categories(paper_id, category_id) VALUES(?,?)", (paper_id, c))
    if cat_ids:  # 已歸檔就清掉建議
        con.execute("UPDATE papers SET suggested_category_id=NULL WHERE id=?", (paper_id,))


# ------------------------------------------------------------------ 個人閱讀狀態
STATUSES = ["待讀", "閱讀中", "已閱讀"]


def set_status(con, user_id: int, paper_id: int, status: str | None) -> None:
    status = status if status in STATUSES else None
    if status is None:
        con.execute("DELETE FROM user_paper WHERE user_id=? AND paper_id=?", (user_id, paper_id))
    else:
        con.execute("INSERT INTO user_paper(user_id, paper_id, status, updated_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(user_id, paper_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at",
                    (user_id, paper_id, status, now()))


def get_status(con, user_id: int, paper_id: int):
    r = con.execute("SELECT status FROM user_paper WHERE user_id=? AND paper_id=?", (user_id, paper_id)).fetchone()
    return r["status"] if r else None
