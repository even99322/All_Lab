"""NAS 上的數據索引（1.0.4）：先把「數據集散地」每個檔的資訊與預覽算好，讀檔程式隔著 VPN 也能馬上打開。

    python -m labhub.indexer            # docker-compose.yml 的 indexer 服務（同一份 labhub 程式）

* 用讀檔程式自己的解析程式（labhub/llvcore，由 tools/sync_llvcore.py 複製）：Log 名稱、建立時間、掃描維度、
  檔案裡的 Tag / Star、預設預覽（labhub/index_codec.py 格式）。結果跟讀檔程式自己掃描的一樣。
* 結果放在 /data/index/：index.sqlite（files、folders、meta）與 previews/*.npz；Hub（labhub/index_routes.py）
  直接讀它們回答讀檔程式。
* 每 LABHUB_INDEX_INTERVAL 秒（預設 300）走一次整個資料夾：只解析新的 / 改過的檔（大小或時間不同），
  最近 60 秒內還在寫的檔下一輪再做；刪掉的檔從索引移除。新的檔先做。
* 一個檔讓程式當掉（壞掉的 HDF5）→ 重新啟動後那個檔記為錯誤、跳過，不會一直當。
* 存取鑰匙：/data/index/share_key，也寫一份到數據集散地的 .qel/index_key（LABHUB_INDEX_KEYDIR）——
  讀得到數據集散地的電腦就讀得到鑰匙，讀檔程式用它向 Hub 證明有權限看這些數據，不必另外登入。
* 需要 numpy、h5py：容器是官方 python 映像，第一次啟動時安裝到 /data/pydeps（之後直接用）。
"""
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import secrets
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

log = logging.getLogger("labhub.indexer")

HERE = Path(__file__).resolve().parent
LLVCORE = HERE / "llvcore"
SCHEMA = 2          # 1.0.23：2D 預覽多存複數原始資料 → 全部重新解析一次
HDF5_EXTENSIONS = (".hdf5", ".h5", ".nc")
SKIP_DIRS = {"@eaDir", "#recycle", "#snapshot", "@tmp", "$RECYCLE.BIN", "System Volume Information"}
WRITING_WINDOW = 60.0                 # 這麼近改過的檔可能還在寫：下一輪再做
DEPS = ("numpy==2.5.3", "h5py==3.16.0")


# ---- 設定 -----------------------------------------------------------------------
def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


def share_root() -> Path:
    return _env_path("LABHUB_SHARE", "/share")


def index_dir(data: Optional[Path] = None) -> Path:
    return (data or _env_path("LABHUB_DATA", "/data")) / "index"


def db_path(data: Optional[Path] = None) -> Path:
    return index_dir(data) / "index.sqlite"


def key_path(data: Optional[Path] = None) -> Path:
    return index_dir(data) / "share_key"


def preview_path(rel: str, data: Optional[Path] = None) -> Path:
    h = hashlib.sha1(rel.encode("utf-8")).hexdigest()
    return index_dir(data) / "previews" / h[:2] / f"{h}.npz"


_STATUS_EXTRA: Dict[str, Any] = {}          # 每次都帶的欄位（例如 key_ok）


def write_status(data: Optional[Path] = None, **kw: Any) -> None:
    """index/status.json：目前在做什麼（啟動、安裝套件、錯誤…）；資料庫還沒建立前 Hub 也看得到。"""
    try:
        d = index_dir(data)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "status.json.part"
        body = dict(_STATUS_EXTRA, **kw, time=time.time(), pid=os.getpid())
        tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / "status.json")
    except OSError:
        pass


def read_status(data: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    try:
        return json.loads((index_dir(data) / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def raw_grid(path: str, pv: Any):
    """1.0.23：2D 預覽的複數原始資料（網頁切換 dB / 相位 / 實部 / 虛部用）；不是複數或讀不到 → None。"""
    if getattr(pv, "kind", "") != "2d":
        return None
    try:
        from app.core.labber_parser import load_experiment

        with load_experiment(path) as ex:
            if not ex.get_channel(pv.channel_name).is_complex:
                return None
            dims = ex.list_dimensions(pv.channel_name)
            if len(dims) != 2:
                return None
            g = ex.get_2d_data(dims[0].name, dims[1].name, pv.channel_name, transform="raw")
            return g.z_values
    except Exception:  # noqa: BLE001 - 沒有就只有 dB
        return None


def parser_id() -> str:
    """解析程式的版本：llvcore 的內容或預覽格式改了 → 全部重新解析。"""
    src = LLVCORE / "SOURCE.json"
    digest = hashlib.sha1(src.read_bytes()).hexdigest()[:12] if src.is_file() else "none"
    return f"{SCHEMA}:{digest}"


def llv_version() -> str:
    try:
        return json.loads((LLVCORE / "SOURCE.json").read_text(encoding="utf-8")).get("llv_version", "?")
    except (OSError, ValueError):
        return "?"


# ---- 套件 -----------------------------------------------------------------------
def ensure_deps(target: Optional[Path] = None) -> None:
    """numpy、h5py：沒有就安裝到 /data/pydeps（只有第一次；NAS 要能連 PyPI）。"""
    target = target or _env_path("LABHUB_PYDEPS", "/data/pydeps")
    if str(target) not in sys.path:
        sys.path.insert(0, str(target))
    try:
        import h5py  # noqa: F401
        import numpy  # noqa: F401
        return
    except ImportError:
        pass
    target.mkdir(parents=True, exist_ok=True)
    log.info("第一次啟動：安裝 numpy、h5py 到 %s（約 1–2 分鐘）", target)
    write_status(state="installing", message="第一次啟動：安裝 numpy、h5py（約 1–2 分鐘，NAS 要能上網）")
    err = ""
    for spec in (list(DEPS), [d.split("==")[0] for d in DEPS]):          # 指定版本不行時用最新版
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--no-cache-dir", "--upgrade",
                            "--target", str(target), *spec], capture_output=True, text=True)
        if r.returncode == 0:
            break
        err = (r.stderr or r.stdout)[-600:]
        log.warning("安裝 %s 失敗：%s", " ".join(spec), err)
    importlib.invalidate_caches()
    try:
        import h5py  # noqa: F401,F811
        import numpy  # noqa: F401,F811
    except ImportError:
        write_status(state="error", message="安裝 numpy、h5py 失敗（NAS 的容器連得到網路嗎？）", detail=err)
        raise


def _llvcore() -> None:
    if str(LLVCORE) not in sys.path:
        sys.path.insert(0, str(LLVCORE))


# ---- 資料庫 ---------------------------------------------------------------------
SQL = """
CREATE TABLE IF NOT EXISTS files (
    rel TEXT PRIMARY KEY, folder TEXT NOT NULL, size INTEGER, mtime REAL,
    status TEXT, error TEXT, log_name TEXT, creation_time REAL, sweep_dimension TEXT,
    file_tags TEXT, file_star INTEGER, preview TEXT, indexed REAL, parser TEXT);
CREATE INDEX IF NOT EXISTS files_folder ON files(folder);
CREATE TABLE IF NOT EXISTS folders (rel TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10, check_same_thread=False)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(path), timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SQL)
    con.row_factory = sqlite3.Row
    return con


def set_meta(con: sqlite3.Connection, **kw: Any) -> None:
    con.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                    [(k, json.dumps(v, ensure_ascii=False)) for k, v in kw.items()])


def get_meta(con: sqlite3.Connection) -> Dict[str, Any]:
    out = {}
    for row in con.execute("SELECT key, value FROM meta"):
        try:
            out[row["key"]] = json.loads(row["value"])
        except ValueError:
            out[row["key"]] = row["value"]
    return out


# ---- 鑰匙 -----------------------------------------------------------------------
def ensure_key(data: Optional[Path] = None, keydir: Optional[Path] = None) -> str:
    kp = key_path(data)
    try:
        key = kp.read_text(encoding="utf-8").strip()
    except OSError:
        key = ""
    if len(key) < 20:
        key = secrets.token_urlsafe(32)
        kp.parent.mkdir(parents=True, exist_ok=True)
        kp.write_text(key, encoding="utf-8")
    keydir = keydir if keydir is not None else _env_path("LABHUB_INDEX_KEYDIR", "/share_key")
    try:
        if keydir.is_dir():
            target = keydir / "index_key"
            if not target.is_file() or target.read_text(encoding="utf-8").strip() != key:
                target.write_text(key, encoding="utf-8")
            readme = keydir / "README.txt"
            if not readme.exists():
                readme.write_text("讀檔程式用這個資料夾的 index_key 向 Hub 取得 NAS 上算好的數據索引（不要刪除）。\n",
                                  encoding="utf-8")
    except OSError as e:
        log.warning("寫不進 %s（讀檔程式改用登入）：%s", keydir, e)
    return key


# ---- 走資料夾 -------------------------------------------------------------------
def walk(root: Path) -> Iterator[Tuple[str, Optional[os.stat_result]]]:
    """(相對路徑, stat)；資料夾也列出來（stat = None，路徑以 / 結尾）。"""
    pending = [root]
    while pending:
        folder = pending.pop()
        try:
            with os.scandir(folder) as it:
                items = list(it)
        except OSError:
            continue
        for item in items:
            name = item.name
            if name.startswith(".") or name in SKIP_DIRS:
                continue
            try:
                if item.is_dir(follow_symlinks=False):
                    pending.append(Path(item.path))
                    yield Path(item.path).relative_to(root).as_posix() + "/", None
                    continue
                if not name.lower().endswith(HDF5_EXTENSIONS) or not item.is_file(follow_symlinks=False):
                    continue
                yield Path(item.path).relative_to(root).as_posix(), item.stat()
            except OSError:
                continue


def folder_of(rel: str) -> str:
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


# ---- 解析一個檔 -----------------------------------------------------------------
def index_file(root: Path, rel: str, stat: os.stat_result, data: Optional[Path] = None,
               preview_max: int = 2 << 30) -> Dict[str, Any]:
    """解析一個檔（讀檔程式的 _scan_one ＋ 預設預覽）→ files 的一列；預覽另外存檔。"""
    _llvcore()
    from app.core.database_scanner import DatabaseScanner

    from .index_codec import encode_preview

    path = root / rel
    entry = DatabaseScanner._scan_one(root, path, stat=stat)
    row: Dict[str, Any] = {
        "rel": rel, "folder": folder_of(rel), "size": int(stat.st_size), "mtime": float(stat.st_mtime),
        "status": entry.status, "error": entry.error_message, "log_name": entry.log_name,
        "creation_time": entry.creation_time, "sweep_dimension": entry.sweep_dimension,
        "file_tags": None if entry.file_tags is None else json.dumps(entry.file_tags, ensure_ascii=False),
        "file_star": None if entry.file_star is None else int(bool(entry.file_star)),
        "preview": "none", "indexed": time.time(), "parser": parser_id(),
    }
    pp = preview_path(rel, data)
    if entry.status == "ok":
        if stat.st_size > preview_max:
            row["preview"] = "none: too big"
        else:
            try:
                from app.core.quick_preview import build_quick_preview

                pv = build_quick_preview(str(path))
                blob = encode_preview(pv, raw_grid(str(path), pv))
                pp.parent.mkdir(parents=True, exist_ok=True)
                tmp = pp.with_suffix(".part")
                tmp.write_bytes(blob)
                os.replace(tmp, pp)
                row["preview"] = "ok"
            except Exception as e:  # noqa: BLE001 — 沒有預覽就讓讀檔程式自己讀
                row["preview"] = f"none: {type(e).__name__}: {e}"[:300]
    if row["preview"] != "ok":
        try:
            pp.unlink()
        except OSError:
            pass
    return row


COLUMNS = ("rel", "folder", "size", "mtime", "status", "error", "log_name", "creation_time", "sweep_dimension",
           "file_tags", "file_star", "preview", "indexed", "parser")


def upsert(con: sqlite3.Connection, row: Dict[str, Any]) -> None:
    con.execute(f"INSERT OR REPLACE INTO files({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                [row.get(c) for c in COLUMNS])


# ---- 一輪 -----------------------------------------------------------------------
class Indexer:
    def __init__(self, root: Optional[Path] = None, data: Optional[Path] = None) -> None:
        self.root = root or share_root()
        self.data = data or _env_path("LABHUB_DATA", "/data")
        self.con = connect(db_path(self.data))
        self.stop = False
        self._recover_crash()

    def _recover_crash(self) -> None:
        """上次解析某個檔時程式當掉（current 還在）→ 那個檔記為錯誤，不再解析。"""
        cur = get_meta(self.con).get("current")
        if isinstance(cur, dict) and cur.get("rel"):
            log.warning("上次解析 %s 時中斷：記為錯誤、跳過", cur["rel"])
            upsert(self.con, {"rel": cur["rel"], "folder": folder_of(cur["rel"]), "size": cur.get("size"),
                              "mtime": cur.get("mtime"), "status": "error",
                              "error": "Indexer stopped while reading this file", "log_name": Path(cur["rel"]).stem,
                              "preview": "none: indexer stopped", "indexed": time.time(), "parser": parser_id()})
        set_meta(self.con, current=None)
        self.con.commit()

    def pass_once(self, budget_s: Optional[float] = None) -> Dict[str, int]:
        """走一次整個資料夾、解析需要的檔；budget_s：最多花這麼多秒解析（測試用），剩下的下一輪。"""
        started = time.time()
        pid = parser_id()
        known = {r["rel"]: (r["size"], r["mtime"], r["parser"]) for r in
                 self.con.execute("SELECT rel, size, mtime, parser FROM files")}
        seen: Dict[str, os.stat_result] = {}
        folders: List[str] = []
        set_meta(self.con, state="listing", pass_started=started)
        self.con.commit()
        for rel, st in walk(self.root):
            if st is None:
                folders.append(rel.rstrip("/"))
            else:
                seen[rel] = st
        now = time.time()
        todo, pending = [], 0
        for rel, st in seen.items():
            k = known.get(rel)
            if k is not None and k[0] == st.st_size and abs((k[1] or 0) - st.st_mtime) < 1e-3 and k[2] == pid:
                continue
            if now - st.st_mtime < WRITING_WINDOW:
                pending += 1
                continue
            todo.append(rel)
        todo.sort(key=lambda r: seen[r].st_mtime, reverse=True)         # 新的先做
        removed = [rel for rel in known if rel not in seen]
        for rel in removed:
            self.con.execute("DELETE FROM files WHERE rel = ?", (rel,))
            try:
                preview_path(rel, self.data).unlink()
            except OSError:
                pass
        self.con.execute("DELETE FROM folders")
        self.con.executemany("INSERT OR IGNORE INTO folders(rel) VALUES (?)", [(f,) for f in folders])
        set_meta(self.con, state="indexing", files=len(seen), folders=len(folders), todo=len(todo), done=0,
                 pending=pending, llv_version=llv_version(), parser=pid, schema=SCHEMA)
        self.con.commit()
        done = 0
        preview_max = int(float(os.environ.get("LABHUB_INDEX_PREVIEW_MAX_MB", "2048")) * (1 << 20))
        for rel in todo:
            if self.stop or (budget_s is not None and time.time() - started > budget_s):
                break
            st = seen[rel]
            set_meta(self.con, current={"rel": rel, "size": st.st_size, "mtime": st.st_mtime})
            self.con.commit()
            try:
                row = index_file(self.root, rel, st, self.data, preview_max)
            except Exception as e:  # noqa: BLE001
                row = {"rel": rel, "folder": folder_of(rel), "size": st.st_size, "mtime": st.st_mtime,
                       "status": "error", "error": f"{type(e).__name__}: {e}"[:300], "log_name": Path(rel).stem,
                       "preview": "none", "indexed": time.time(), "parser": pid}
            upsert(self.con, row)
            done += 1
            set_meta(self.con, current=None, done=done)
            if done % 20 == 0:
                self.con.commit()
        errors = self.con.execute("SELECT COUNT(*) FROM files WHERE status != 'ok'").fetchone()[0]
        indexed = self.con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        set_meta(self.con, state="idle" if done == len(todo) else "indexing", current=None, done=done,
                 indexed=indexed, errors=errors, last_pass=time.time(), pass_seconds=round(time.time() - started, 1),
                 complete=done == len(todo) and pending == 0)
        self.con.commit()
        return {"files": len(seen), "indexed": done, "todo": len(todo), "removed": len(removed), "pending": pending}

    def run_forever(self) -> None:
        interval = float(os.environ.get("LABHUB_INDEX_INTERVAL", "300"))
        while not self.stop:
            if not self.root.is_dir():
                msg = f"找不到 {self.root}（docker-compose.yml 的數據集散地掛載）"
                set_meta(self.con, state="no_share", error=msg)
                self.con.commit()
                write_status(self.data, state="error", message=msg)
                log.warning("找不到 %s，60 秒後再試", self.root)
                self._sleep(60)
                continue
            try:
                write_status(self.data, state="indexing", message="")
                r = self.pass_once()
                write_status(self.data, state="idle" if r["indexed"] >= r["todo"] else "indexing", message="")
                log.info("索引：%s 個檔，這輪解析 %s 個（還有 %s 個在寫入），移除 %s 個",
                         r["files"], r["indexed"], r["pending"], r["removed"])
                if r["indexed"] < r["todo"]:
                    continue                                   # 被中斷：馬上接著做
            except Exception:  # noqa: BLE001
                log.exception("索引失敗")
            self._sleep(interval)

    def _sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while not self.stop and time.time() < end:
            time.sleep(min(1.0, end - time.time()))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        os.nice(10)                     # 讓 Hub、論文庫優先
    except (AttributeError, OSError):
        pass
    data = _env_path("LABHUB_DATA", "/data")
    write_status(data, state="starting", message="啟動中")
    ensure_deps()
    ensure_key(data)
    keydir = _env_path("LABHUB_INDEX_KEYDIR", "/share_key")
    _STATUS_EXTRA["key_ok"] = (keydir / "index_key").is_file()      # False：讀檔程式拿不到鑰匙（.qel 掛載有問題）
    ix = Indexer(share_root(), data)

    def _term(*_a: Any) -> None:
        ix.stop = True
    signal.signal(signal.SIGTERM, _term)
    log.info("數據索引：%s → %s（讀檔程式 %s 的解析程式）", ix.root, db_path(data), llv_version())
    ix.run_forever()


if __name__ == "__main__":
    main()
