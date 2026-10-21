"""量子工程平台：Hub 的「平台」功能（只用標準函式庫）。

Hub（NAS 中繼站）是所有模塊共用的通信中樞。這裡加上：

  * 登入：用論文庫（paperlib）的帳號密碼登入。Hub 在伺服器端向 paperlib 驗證，
    發一個 Hub 的使用者 token（與 paperlib 同樣 30 天）；paperlib 的 session 只存在 Hub，不給前端。
  * 權限：沿用 paperlib 的「站長」。站長可以為每位使用者開關各模塊，也可以停用 / 啟用帳號（寫回 paperlib）。
  * 標籤：所有模塊共用同一批標籤 = paperlib 的標籤 ＋ Hub 自己的分類資訊；每個標籤可以查到對應的論文群，
    並產生跳到論文庫的連結（/#/t/<標籤>、/#/p/<論文>）。
  * 數據目錄：上傳到 Hub 的量測檔都登記在目錄裡（誰量的、哪個節點、標籤、量測設定），
    讀檔模塊與網頁可以依標籤搜尋、下載，並看到相關論文。
  * 訊息匯流排：模塊之間（含不同電腦、手機網頁）互傳指令，例如「在讀檔模塊開這個檔」「套用這個檔的設定到量測模塊」。
  * 模塊發佈：每個模塊各自的版本與下載（啟動器用來單獨安裝 / 更新模塊）。

使用者 token 與原本的裝置 token（LABHUB_TOKEN，量測節點用）並存。
"""
from __future__ import annotations

import hashlib
import http.cookiejar
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger("labhub.platform")
REQUEST = threading.local()                  # 目前請求的 Host（server.py 每個請求設定）

SESSION_DAYS = 30
FAIL_MAX, FAIL_WINDOW_S = 5, 600     # 同一個來源 IP 10 分鐘內失敗 5 次 → 暫停登入
RECHECK_S = 600                      # 每 10 分鐘向 paperlib 確認一次帳號仍有效（停用的帳號會被登出）

#: 內建模塊（站長可以在網頁或平台監控修改名稱、網址等）
DEFAULT_MODULES: Dict[str, Dict[str, Any]] = {
    # 0.0.26 起不用啟動器：量測、讀檔各自是獨立程式（Windows .exe / Mac app），從下載頁 /get 安裝，程式自己更新
    "launcher": {"name": "量子工程平台 App（舊）", "kind": "python", "hidden": True,
                 "description": "舊的啟動器，0.0.26 起不再使用", "order": 90},
    "papers": {"name": "論文庫", "kind": "web", "description": "論文、標籤、筆記（paperlib）", "order": 1},
    "labcontrol": {"name": "量測", "kind": "app", "app": "Lab Control",
                   "description": "Lab Control：流程圖量測、即時數據、儀器伺服器、量測電腦", "order": 2},
    "lablogviewer": {"name": "讀檔", "kind": "app", "app": "LabLogViewer",
                     "description": "LabLogViewer：讀取 Labber / Lab Control（.nc）量測檔、擬合、圖", "order": 3},
    "monitor": {"name": "平台監控", "kind": "python", "hidden": True,
                "description": "網站狀態、使用者權限、模塊發佈與更新（站長；已併入網頁控制台）",
                "order": 91, "owner_only": True},
    "catalog": {"name": "數據目錄", "kind": "web", "description": "依標籤搜尋量測數據與相關論文", "order": 5,
                "url": "/portal#/data"},
    "nodes": {"name": "量測節點", "kind": "web", "description": "所有量測電腦的狀態與進度", "order": 6, "url": "/nodes"},
    # 0.0.28：Windows 一鍵安裝（QELSetup_v<版本>_windows.exe）：從 NAS 下載最新的量測、讀檔安裝程式並安裝
    "setup": {"name": "Windows 安裝程式", "kind": "app", "hidden": True, "order": 92,
              "description": "量測＋讀檔一起裝：從 NAS 下載最新的安裝程式"},
    # 0.0.30：讀檔程式的寵物圖檔（LabLogViewerPets_v<版本>.zip）：安裝時選擇要不要，或在讀檔程式裡下載
    "llvpets": {"name": "讀檔寵物圖檔", "kind": "app", "hidden": True, "order": 93,
                "description": "LabLogViewer 桌面寵物的圖檔（約 220 MB，選擇性下載）"},
    # 1.0.16：模擬工具（Python 原始碼 zip：HFSSTool_v<版本>.zip，控制台發佈）
    "hfss": {"name": "模擬", "kind": "app", "app": "HFSS 自動化工具", "order": 4,
             "description": "HFSS 自動化工具：DXF 版圖 → 3D 模型、S 參數與場型模擬、YIG 磁性球（Windows ＋ Ansys AEDT 2026 R1）",
             "note": "Windows：下載 .exe 直接執行（單一檔案，不用裝 Python；第一次開要等十幾秒），之後程式會自己更新。"
                     "需要 Ansys Electronics Desktop 2026 R1（或 2025 R2）。"},
}
#: 下載頁 /get 列出、不用登入就能下載的獨立程式
APPS = ("labcontrol", "lablogviewer", "hfss")     # 1.0.18：模擬工具也有下載頁與程式內更新（/get/info、/get/watch）



def file_arch(name: str) -> str:
    """安裝檔的晶片：arm64（Apple 晶片）、x86_64（Intel）、universal2（兩種都可以）；沒寫 = ""。"""
    n = name.lower()
    for key, pats in (("universal2", ("universal",)), ("arm64", ("arm64", "aarch64", "apple")),
                      ("x86_64", ("x86_64", "x64", "amd64", "intel"))):
        if any(re.search(rf"[_-]{p}", n) for p in pats):
            return key
    return ""


def file_platform(name: str) -> str:
    """發佈檔是給哪個作業系統直接執行的：windows（.exe、*_windows.zip）、macos（.dmg、*_macos.zip）；其他 = ""。"""
    n = name.lower()
    if n.endswith((".exe", ".msi")) or re.search(r"[_-](windows|win64|win)([_.-]|$)", n):
        return "windows"
    if n.endswith((".dmg", ".pkg")) or re.search(r"[_-](macos|mac|darwin|osx)([_.-]|$)", n):
        return "macos"
    return ""

class PlatformError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
class PaperlibClient:
    """伺服器對伺服器呼叫 paperlib（帶使用者的 plsession cookie 與 X-PL 標頭）。"""

    COOKIE = "plsession"

    def __init__(self, base: str, timeout: float = 8.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _req(self, method: str, path: str, body: Any = None, session: Optional[str] = None,
             client_ip: Optional[str] = None) -> Tuple[int, Any, Dict[str, str]]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        req.add_header("X-PL", "1")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if session:
            req.add_header("Cookie", f"{self.COOKIE}={session}")
        if client_ip:
            req.add_header("X-Forwarded-For", client_ip)       # paperlib 依真正的來源 IP 鎖定登入失敗
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                raw = r.read()
                headers = {k.lower(): v for k, v in r.headers.items()}
                set_cookie = r.headers.get_all("Set-Cookie") or []
                headers["set-cookie"] = "\n".join(set_cookie)
                return r.status, (json.loads(raw.decode("utf-8")) if raw else None), headers
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                obj = json.loads(raw.decode("utf-8")) if raw else None
            except ValueError:
                obj = {"detail": raw[:200].decode("utf-8", "replace")}
            return e.code, obj, {}
        except (urllib.error.URLError, OSError) as e:
            raise PlatformError(502, f"連不到論文庫（{self.base}）：{getattr(e, 'reason', e)}") from None

    @staticmethod
    def _detail(obj: Any, default: str) -> str:
        if isinstance(obj, dict):
            d = obj.get("detail") or obj.get("error")
            if isinstance(d, list):
                d = "; ".join(str(x.get("msg", x)) if isinstance(x, dict) else str(x) for x in d)
            if d:
                return str(d)
        return default

    def login(self, username: str, password: str, code: str = "", client_ip: str = "") -> Tuple[str, Dict]:
        body = {"username": username, "password": password}
        if code:
            body["code"] = code
        st, obj, hd = self._req("POST", "/api/auth/login", body, client_ip=client_ip)
        if st == 429:
            raise PlatformError(429, self._detail(obj, "登入失敗次數太多，請稍後再試"))
        if st >= 400:
            raise PlatformError(401, self._detail(obj, "帳號或密碼錯誤"))
        if isinstance(obj, dict) and obj.get("need_2fa"):
            raise PlatformError(401, "need_2fa")
        m = re.search(rf"{self.COOKIE}=([^;\s]+)", hd.get("set-cookie", ""))
        if not m:
            raise PlatformError(502, "論文庫沒有回傳登入 session")
        sess = m.group(1)
        return sess, self.me(sess)

    def me(self, session: str) -> Dict[str, Any]:
        st, obj, _ = self._req("GET", "/api/me", session=session)
        if st == 401 or st == 403:
            raise PlatformError(401, "論文庫的登入已失效，請重新登入")
        if st >= 400 or not isinstance(obj, dict):
            raise PlatformError(502, self._detail(obj, f"論文庫回應 {st}"))
        return obj

    def logout(self, session: str) -> None:
        try:
            self._req("POST", "/api/auth/logout", {}, session=session)
        except PlatformError:
            pass

    def get(self, path: str, session: str) -> Any:
        st, obj, _ = self._req("GET", path, session=session)
        if st >= 400:
            raise PlatformError(st if st in (401, 403, 404) else 502, self._detail(obj, f"論文庫回應 {st}"))
        return obj

    def send(self, method: str, path: str, body: Any, session: str) -> Any:
        st, obj, _ = self._req(method, path, body, session=session)
        if st >= 400:
            raise PlatformError(st if st in (400, 401, 403, 404, 409) else 502, self._detail(obj, f"論文庫回應 {st}"))
        return obj

    def site(self) -> Dict[str, Any]:
        st, obj, _ = self._req("GET", "/api/site")
        if st >= 400 or not isinstance(obj, dict):
            raise PlatformError(502, f"論文庫回應 {st}")
        return obj


# ---------------------------------------------------------------------------
class Bus:
    """模塊之間的訊息（每位使用者、每個模塊一個收件匣；long-poll）。"""

    def __init__(self, keep: int = 200) -> None:
        self.cond = threading.Condition()
        self.boxes: Dict[Tuple[str, str], deque] = {}
        self.seq = 0
        self.keep = keep

    def send(self, user: str, module: str, msg: Dict[str, Any]) -> Dict[str, Any]:
        with self.cond:
            self.seq += 1
            m = dict(msg, id=self.seq, time=time.time(), to=module)
            self.boxes.setdefault((user, module), deque(maxlen=self.keep)).append(m)
            self.cond.notify_all()
            return m

    def take(self, user: str, module: str, after: int = 0, wait: float = 0.0) -> Dict[str, Any]:
        end = time.time() + max(0.0, min(wait, 30.0))
        with self.cond:
            while True:
                box = self.boxes.get((user, module)) or ()
                items = [m for m in box if m["id"] > after]
                if items or time.time() >= end:
                    return {"messages": items, "seq": self.seq}
                self.cond.wait(min(end - time.time(), 5.0))


# ---------------------------------------------------------------------------
class Platform:
    def __init__(self, data_dir: Path, files_dir: Path, paperlib_url: str = "", paperlib_public: str = "",
                 session_days: int = SESSION_DAYS) -> None:
        self.data_dir = Path(data_dir)
        self.files_dir = Path(files_dir)
        self.modules_dir = self.data_dir / "modules"
        self.modules_dir.mkdir(parents=True, exist_ok=True)
        self._archive_legacy_versions()
        self.paperlib = PaperlibClient(paperlib_url) if paperlib_url else None
        # 給瀏覽器 / 桌面程式開論文連結的網址；可以用逗號列出多個（內網、VPN），依使用者連進 Hub 的位址挑同一台
        self.paperlib_publics = [u.strip().rstrip("/") for u in (paperlib_public or paperlib_url or "").split(",")
                                 if u.strip()]
        self.session_days = session_days
        self.bus = Bus()
        self.lock = threading.RLock()
        #: 每發佈一個檔 +1；/get/watch 用它通知各電腦的程式「有新版」（推播）
        self.release_seq = 0
        self.release_cond = threading.Condition()
        self._tag_cache: Tuple[float, List[Dict[str, Any]]] = (0.0, [])
        self._fails: Dict[str, deque] = {}               # 來源 IP → 最近登入失敗時間（Hub 先擋，避免論文庫鎖住 Hub 的 IP）
        self._db = self.data_dir / "platform.db"
        with self._con() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, username TEXT, user_json TEXT,
                pl_session TEXT, created REAL, expires REAL, checked REAL, client TEXT);
            CREATE TABLE IF NOT EXISTS perms (username TEXT, module TEXT, allowed INTEGER,
                PRIMARY KEY (username, module));
            CREATE TABLE IF NOT EXISTS tag_meta (name TEXT PRIMARY KEY, category TEXT, color TEXT,
                description TEXT, local INTEGER DEFAULT 0, created REAL);
            CREATE TABLE IF NOT EXISTS catalog (id TEXT PRIMARY KEY, node TEXT, rel TEXT, name TEXT, size INTEGER,
                created REAL, updated REAL, user TEXT, run_id TEXT, scheme TEXT, tags TEXT, meta TEXT);
            CREATE INDEX IF NOT EXISTS catalog_created ON catalog(created);
            CREATE TABLE IF NOT EXISTS modules (id TEXT PRIMARY KEY, info TEXT);
            CREATE TABLE IF NOT EXISTS project_tags (project TEXT, tag TEXT, PRIMARY KEY (project, tag));
            CREATE TABLE IF NOT EXISTS projects_cache (id TEXT PRIMARY KEY, info TEXT, seen REAL);
            CREATE TABLE IF NOT EXISTS view_files (path TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS audit (time REAL, user TEXT, action TEXT, detail TEXT);
            """)
            cols = {r[1] for r in con.execute("PRAGMA table_info(catalog)")}
            if "project" not in cols:                      # 0.0.16：數據屬於論文庫的哪個專案
                con.execute("ALTER TABLE catalog ADD COLUMN project TEXT DEFAULT ''")
                con.execute("CREATE INDEX IF NOT EXISTS catalog_project ON catalog(project)")
        self.views_dir = self.data_dir / "projects"       # 每個專案一個資料夾（硬連結到 files/），讀檔模塊直接開
        self._views_lock = threading.Lock()
        self._views_timer: Optional[threading.Timer] = None
        self._proj_cache: Tuple[float, List[Dict[str, Any]]] = (0.0, [])
        # 0.0.31：論文庫的服務帳號（量測電腦沒有登入時，Hub 用它查專案）；帳密只在 NAS 的 docker-compose 環境變數
        # 也可以在網頁控制台設定：存在 Hub 資料夾 paperlib_service.json（只有 NAS 上有）
        self.service_user = os.environ.get("LABHUB_PAPERLIB_SERVICE_USER", "").strip()
        self._service_password = os.environ.get("LABHUB_PAPERLIB_SERVICE_PASSWORD", "")
        self._service_session: Optional[str] = None
        self._service_file = self.data_dir / "paperlib_service.json"
        if not self.service_user and self._service_file.is_file():
            try:
                d = json.loads(self._service_file.read_text(encoding="utf-8"))
                self.service_user, self._service_password = str(d.get("user") or ""), str(d.get("password") or "")
            except (OSError, ValueError):
                pass

    @property
    def paperlib_public(self) -> str:
        """這次請求要用的論文庫網址：和使用者連 Hub 的主機相同的那個（內網進來給內網、VPN 進來給 VPN）。"""
        host = getattr(REQUEST, "host", "") or ""
        for u in self.paperlib_publics:
            if urllib.parse.urlsplit(u).hostname == host:
                return u
        fn = getattr(self, "paperlib_for_host", None)    # 1.0.15：用 https 網域進來 → 論文庫也給 https
        u = fn(host) if fn else ""
        if u:
            return u
        return self.paperlib_publics[0] if self.paperlib_publics else ""

    # ---- 共用 ------------------------------------------------------------------
    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def audit(self, user: str, action: str, detail: str = "") -> None:
        with self._con() as con:
            con.execute("INSERT INTO audit VALUES (?,?,?,?)", (time.time(), user, action, detail[:500]))

    def need_paperlib(self) -> PaperlibClient:
        if self.paperlib is None:
            raise PlatformError(503, "Hub 沒有設定論文庫網址（LABHUB_PAPERLIB_URL），無法用論文庫帳號登入")
        return self.paperlib

    def paper_url(self, pid: Any) -> str:
        return f"{self.paperlib_public}/#/p/{pid}" if self.paperlib_public else ""

    def tag_url(self, name: str) -> str:
        return f"{self.paperlib_public}/#/t/{urllib.parse.quote(name, safe='')}" if self.paperlib_public else ""

    # ---- 登入 / session --------------------------------------------------------------
    @staticmethod
    def _public_user(me: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": me.get("id"), "username": me.get("username"),
                "display_name": me.get("display_name") or me.get("username"),
                "owner": bool(me.get("owner")), "role": me.get("role"), "email": me.get("email") or ""}

    def login(self, username: str, password: str, code: str = "", client_ip: str = "",
              client: str = "") -> Dict[str, Any]:
        pl = self.need_paperlib()
        if not username or not password:
            raise PlatformError(400, "請輸入帳號與密碼")
        now = time.time()
        with self.lock:
            fails = self._fails.setdefault(client_ip or "?", deque(maxlen=FAIL_MAX))
            while fails and now - fails[0] > FAIL_WINDOW_S:
                fails.popleft()
            if len(fails) >= FAIL_MAX:
                mins = int((FAIL_WINDOW_S - (now - fails[0])) // 60) + 1
                raise PlatformError(429, f"登入失敗太多次，請 {mins} 分鐘後再試")
        try:
            sess, me = pl.login(username.strip(), password, code, client_ip)
        except PlatformError as e:
            if e.code == 401 and str(e) != "need_2fa":
                with self.lock:
                    fails.append(time.time())
            raise
        with self.lock:
            fails.clear()
        user = self._public_user(me)
        tok = "u_" + secrets.token_urlsafe(32)
        now = time.time()
        with self._con() as con:
            con.execute("DELETE FROM sessions WHERE expires < ?", (now,))
            con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)",
                        (tok, user["username"], json.dumps(user, ensure_ascii=False), sess, now,
                         now + self.session_days * 86400, now, client[:80]))
        self.audit(user["username"], "login", f"{client_ip} {client}")
        return {"token": tok, "user": user, "modules": self.modules_for(user),
                "expires": now + self.session_days * 86400, "paperlib_url": self.paperlib_public}

    def logout(self, token: str) -> None:
        with self._con() as con:
            row = con.execute("SELECT pl_session FROM sessions WHERE token=?", (token,)).fetchone()
            con.execute("DELETE FROM sessions WHERE token=?", (token,))
        if row and self.paperlib:
            self.paperlib.logout(row["pl_session"])

    def session(self, token: str) -> Optional[Dict[str, Any]]:
        """使用者 token → {user, pl_session}；無效 / 過期 → None。定期向 paperlib 確認帳號仍有效。"""
        if not token or not token.startswith("u_"):
            return None
        now = time.time()
        with self._con() as con:
            row = con.execute("SELECT * FROM sessions WHERE token=? AND expires > ?", (token, now)).fetchone()
        if row is None:
            return None
        user = json.loads(row["user_json"])
        if self.paperlib is not None and now - float(row["checked"] or 0) > RECHECK_S:
            try:
                me = self.paperlib.me(row["pl_session"])
                user = self._public_user(me)
                with self._con() as con:
                    con.execute("UPDATE sessions SET checked=?, user_json=? WHERE token=?",
                                (now, json.dumps(user, ensure_ascii=False), token))
            except PlatformError as e:
                if e.code == 401:                         # paperlib 登出 / 帳號停用 → Hub 也登出
                    with self._con() as con:
                        con.execute("DELETE FROM sessions WHERE token=?", (token,))
                    return None
                # paperlib 暫時連不到：沿用目前的登入
        return {"user": user, "pl_session": row["pl_session"], "token": token}

    def drop_user_sessions(self, username: str) -> int:
        with self._con() as con:
            return con.execute("DELETE FROM sessions WHERE username=?", (username,)).rowcount

    def active_sessions(self) -> List[Dict[str, Any]]:
        with self._con() as con:
            rows = con.execute("SELECT username, created, expires, client FROM sessions WHERE expires > ? "
                               "ORDER BY created DESC", (time.time(),)).fetchall()
        return [dict(r) for r in rows]

    #: 1.0.0 正式版：版本號重新開始的模塊（讀檔程式之前是 0.x – 2.1.6）
    LEGACY_LINES = {"lablogviewer": "1.0.0"}

    def _archive_legacy_versions(self) -> None:
        """Hub 1.0.0 第一次啟動：把讀檔程式舊版號（比 1.0.0 大的 2.x）搬到 data/modules_legacy/，
        否則 2.1.6 會一直被當成最新版。只做一次（之後發佈的 1.0.x 不受影響）。"""
        from .server import parse_version

        for mid, start in self.LEGACY_LINES.items():
            marker = self.modules_dir / f".legacy_archived_{mid}"
            base = self.modules_dir / mid
            if marker.exists():
                continue
            try:
                if base.is_dir():
                    dest = self.data_dir / "modules_legacy" / mid
                    for d in list(base.iterdir()):
                        if d.is_dir() and parse_version(d.name) > parse_version(start):
                            dest.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(d), str(dest / d.name))
                marker.write_text(f"versions above {start} moved to modules_legacy/{mid}\n", encoding="utf-8")
            except OSError as e:
                log.warning("搬移 %s 的舊版號失敗：%s", mid, e)

    # ---- 模塊與權限 ------------------------------------------------------------------
    def modules(self) -> Dict[str, Dict[str, Any]]:
        out = {k: dict(v, id=k) for k, v in DEFAULT_MODULES.items()}
        with self._con() as con:
            for r in con.execute("SELECT id, info FROM modules"):
                info = json.loads(r["info"])
                if r["id"] in DEFAULT_MODULES and info == {"name": r["id"], "kind": "python"}:
                    continue                         # 舊版 Hub 發佈時自動建的空白設定：用內建的
                out[r["id"]] = dict(out.get(r["id"], {}), **info, id=r["id"])
        if "papers" in out and not out["papers"].get("url"):
            out["papers"]["url"] = self.paperlib_public + "/" if self.paperlib_public else ""
        for mid, m in out.items():
            vers = self.module_versions(mid)
            m["versions"] = vers
            # 最新版 = 最新的「有 zip」的版本（啟動器安裝 / 更新用）；只放了 .exe / .dmg 的版本不算
            inst = [v for v in vers if any(f["name"].lower().endswith(".zip") for f in v["files"])]
            m["latest"] = (inst or vers)[0]["version"] if vers else None
            # 各作業系統可以直接執行的安裝檔（Windows .exe、macOS .dmg）：每個平台 / 晶片取最新的一個
            dl: Dict[tuple, Any] = {}
            for v in vers:
                for f in v["files"]:
                    if f["platform"] and (f["platform"], f["arch"]) not in dl:
                        dl[(f["platform"], f["arch"])] = dict(f, version=v["version"])
            m["downloads"] = list(dl.values())
        return dict(sorted(out.items(), key=lambda kv: (kv[1].get("order", 99), kv[0])))

    def set_module(self, mid: str, info: Dict[str, Any]) -> Dict[str, Any]:
        if not re.fullmatch(r"[a-z0-9_-]{2,40}", mid):
            raise PlatformError(400, "模塊代號只能用小寫英數、底線、連字號（2–40 字）")
        keep = {k: info[k] for k in ("name", "kind", "description", "url", "order", "owner_only", "entry", "icon")
                if k in info}
        with self._con() as con:
            row = con.execute("SELECT info FROM modules WHERE id=?", (mid,)).fetchone()
            cur = json.loads(row["info"]) if row else {}
            cur.update(keep)
            con.execute("INSERT OR REPLACE INTO modules VALUES (?,?)", (mid, json.dumps(cur, ensure_ascii=False)))
        return self.modules()[mid]

    def allowed(self, user: Dict[str, Any], module: str) -> bool:
        """站長全部可用；其他人預設可用，站長可以個別關閉；owner_only 的模塊只有站長。"""
        if user.get("owner"):
            return True
        m = DEFAULT_MODULES.get(module) or self.modules().get(module) or {}
        if m.get("owner_only"):
            return False
        with self._con() as con:
            row = con.execute("SELECT allowed FROM perms WHERE username=? AND module=?",
                              (user.get("username"), module)).fetchone()
        return True if row is None else bool(row["allowed"])

    def modules_for(self, user: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [dict(m, allowed=self.allowed(user, mid)) for mid, m in self.modules().items()]

    def perms_table(self) -> Dict[str, Dict[str, bool]]:
        out: Dict[str, Dict[str, bool]] = {}
        with self._con() as con:
            for r in con.execute("SELECT username, module, allowed FROM perms"):
                out.setdefault(r["username"], {})[r["module"]] = bool(r["allowed"])
        return out

    def set_perm(self, by: str, username: str, module: str, allowed: bool) -> None:
        with self._con() as con:
            con.execute("INSERT OR REPLACE INTO perms VALUES (?,?,?)", (username, module, 1 if allowed else 0))
        self.audit(by, "perm", f"{username} {module} {'on' if allowed else 'off'}")

    # ---- 模塊發佈 --------------------------------------------------------------------
    def module_versions(self, mid: str) -> List[Dict[str, Any]]:
        base = self.modules_dir / mid
        if not base.is_dir():
            return []
        out = []
        for d in base.iterdir():
            if not d.is_dir():
                continue
            files = sorted(p for p in d.iterdir() if p.is_file() and not p.name.startswith("."))
            info = {}
            if (d / ".release.json").exists():
                try:
                    info = json.loads((d / ".release.json").read_text(encoding="utf-8"))
                except ValueError:
                    info = {}
            hashes = info.get("files") or {}
            out.append({"version": d.name, "files": [{"name": p.name, "size": p.stat().st_size,
                                                      "sha256": hashes.get(p.name, ""),
                                                      "platform": file_platform(p.name),
                                                      "arch": file_arch(p.name),
                                                      "url": f"/api/platform/modules/{mid}/{d.name}/"
                                                             f"{urllib.parse.quote(p.name)}"} for p in files],
                        "notes": info.get("notes", ""), "time": info.get("time"), "by": info.get("by")})
        from .server import parse_version
        out.sort(key=lambda r: parse_version(r["version"]), reverse=True)
        return out

    def add_release(self, mid: str, version: str, filename: str, data: bytes | Path, by: str, notes: str = "") -> Dict:
        """data：檔案內容，或已經在 Hub 上的檔案路徑（大檔，例如合併好的模塊 zip，直接搬過去）。"""
        if not re.fullmatch(r"\d+(\.\d+){0,3}", version or ""):
            raise PlatformError(400, "版本號請用純數字，例如 0.0.13")
        if filename and not re.fullmatch(r"[A-Za-z0-9_.()-]{1,120}", filename) and "/" not in filename \
                and "\\" not in filename and len(filename) <= 120:
            # 1.0.16：中文檔名（例如「HFSS自動化工具v3.2.zip」）→ 只留英數（下載連結、各電腦的檔名都安全）
            stem, dot, ext = filename.rpartition(".")
            stem = re.sub(r"[^A-Za-z0-9_.()-]+", "_", stem if dot else filename).strip("_.") or f"{mid}_v{version}"
            filename = f"{stem}.{re.sub(r'[^A-Za-z0-9]', '', ext)}" if dot else stem
        if not re.fullmatch(r"[A-Za-z0-9_.()-]{1,120}", filename or "") or filename.startswith("."):
            raise PlatformError(400, "檔名不合法")
        if mid not in self.modules():
            self.set_module(mid, {"name": mid, "kind": "python"})
        d = self.modules_dir / mid / version
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{filename}.part"
        if isinstance(data, Path):
            h = hashlib.sha256()
            with open(data, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            digest, size = h.hexdigest(), data.stat().st_size
            shutil.move(str(data), tmp)
        else:
            digest, size = hashlib.sha256(data).hexdigest(), len(data)
            tmp.write_bytes(data)
        os.replace(tmp, d / filename)
        if not notes and (d / ".release.json").exists():   # 同一版補上 .exe / .dmg：保留原本的發佈說明
            try:
                notes = json.loads((d / ".release.json").read_text(encoding="utf-8")).get("notes", "")
            except ValueError:
                pass
        hashes = {}
        if (d / ".release.json").exists():
            try:
                hashes = dict(json.loads((d / ".release.json").read_text(encoding="utf-8")).get("files") or {})
            except ValueError:
                hashes = {}
        hashes[filename] = digest
        (d / ".release.json").write_text(json.dumps({"notes": notes, "time": time.time(), "by": by,
                                                     "sha256": digest, "files": hashes},
                                                    ensure_ascii=False), encoding="utf-8")
        self.audit(by, "release", f"{mid} {version} {filename}")
        with self.release_cond:                          # 推播：等在 /get/watch 的程式馬上知道
            self.release_seq += 1
            self.release_cond.notify_all()
        return {"module": mid, "version": version, "file": filename, "size": size}

    def base_zip(self, mid: str, version: str) -> Path:
        """某模塊某一版的完整 zip（給更新包合併用）。"""
        d = self.modules_dir / mid / version
        zips = sorted(p for p in d.glob("*.zip") if not file_platform(p.name)) if d.is_dir() else []
        if not zips:
            raise PlatformError(409, f"平台上沒有 {mid} {version} 的完整 zip：請先發佈 {version}，再發佈更新包")
        return zips[0]

    def release_file(self, mid: str, version: str, filename: str) -> Path:
        p = self.modules_dir / mid / version / filename
        if not re.fullmatch(r"[a-z0-9_-]+", mid) or not p.is_file() or ".." in filename or "/" in filename:
            raise PlatformError(404, "沒有這個檔案")
        return p

    # ---- 標籤 ------------------------------------------------------------------------
    def tags(self, sess: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """所有模塊共用的標籤：paperlib 的標籤（含論文數）＋ Hub 的分類 / 只用於數據的標籤（含數據數）。"""
        pl_tags = self._paperlib_tags(sess)
        meta = {}
        with self._con() as con:
            for r in con.execute("SELECT * FROM tag_meta"):
                meta[r["name"]] = dict(r)
        counts = self.tag_data_counts()
        out: Dict[str, Dict[str, Any]] = {}
        for t in pl_tags:
            n = t.get("name")
            if not n:
                continue
            out[n] = {"name": n, "color": t.get("color") or "", "description": t.get("description") or "",
                      "papers": int(t.get("count") or 0), "in_paperlib": True}
        for n, m in meta.items():
            out.setdefault(n, {"name": n, "color": "", "description": "", "papers": 0, "in_paperlib": False})
            for k in ("category", "color", "description"):
                if m.get(k):
                    out[n][k] = m[k]
        for n, c in counts.items():
            out.setdefault(n, {"name": n, "color": "", "description": "", "papers": 0, "in_paperlib": False})
        for n, t in out.items():
            t["data"] = counts.get(n, 0)
            t.setdefault("category", "")
            t["url"] = self.tag_url(n) if t.get("in_paperlib") else ""
        return sorted(out.values(), key=lambda t: (t["category"] or "~", t["name"].lower()))

    def _paperlib_tags(self, sess: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        at, cached = self._tag_cache
        if sess is None or self.paperlib is None:
            return cached                                   # 裝置 token（量測節點）：用最近一次的結果
        if time.time() - at < 20 and cached:
            return cached
        try:
            tags = self.paperlib.get("/api/tags", sess["pl_session"]) or []
            self._tag_cache = (time.time(), list(tags))
            return list(tags)
        except PlatformError as e:
            log.warning("讀取論文庫標籤失敗：%s", e)
            return cached

    def create_tag(self, sess: Optional[Dict[str, Any]], name: str, category: str = "", color: str = "",
                   description: str = "") -> Dict[str, Any]:
        name = (name or "").strip()
        if not name or len(name) > 60:
            raise PlatformError(400, "標籤名稱 1–60 字")
        in_pl = False
        if sess is not None and self.paperlib is not None:
            self._tag_cache = (0.0, self._tag_cache[1])
            if any(t.get("name") == name for t in self._paperlib_tags(sess)):
                in_pl = True                                     # 論文庫已經有這個標籤
        if not in_pl and sess is not None and self.paperlib is not None:
            try:
                self.paperlib.send("POST", "/api/tags", {"name": name, "color": color or None,
                                                         "description": description or ""}, sess["pl_session"])
                in_pl = True
                self._tag_cache = (0.0, self._tag_cache[1])
            except PlatformError as e:
                if e.code == 409 or "已" in str(e) or "exist" in str(e).lower():
                    in_pl = True
                else:
                    log.info("標籤 %s 沒有建立在論文庫（%s）：只建立在 Hub", name, e)
        with self._con() as con:
            con.execute("INSERT INTO tag_meta (name, category, color, description, local, created) VALUES "
                        "(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET category=COALESCE(NULLIF(excluded.category,''),"
                        "category), color=COALESCE(NULLIF(excluded.color,''), color), description=COALESCE("
                        "NULLIF(excluded.description,''), description)",
                        (name, category, color, description, 0 if in_pl else 1, time.time()))
        return {"name": name, "in_paperlib": in_pl, "category": category}

    def set_tag_meta(self, name: str, **kw: Any) -> None:
        with self._con() as con:
            con.execute("INSERT OR IGNORE INTO tag_meta (name, created) VALUES (?,?)", (name, time.time()))
            for k in ("category", "color", "description"):
                if k in kw and kw[k] is not None:
                    con.execute(f"UPDATE tag_meta SET {k}=? WHERE name=?", (kw[k], name))

    def papers_for_tags(self, sess: Optional[Dict[str, Any]], names: Iterable[str], limit: int = 50) -> Dict:
        names = [n for n in dict.fromkeys(names) if n]
        out: Dict[str, Any] = {"tags": [], "papers": []}
        seen = set()
        for n in names:
            items: List[Dict[str, Any]] = []
            err = ""
            if sess is not None and self.paperlib is not None:
                try:
                    q = urllib.parse.urlencode({"tag": n, "limit": limit, "sort": "year"})
                    res = self.paperlib.get(f"/api/papers?{q}", sess["pl_session"]) or {}
                    items = list(res.get("items") or [])
                except PlatformError as e:
                    err = str(e)
            elif sess is None:
                err = "需要使用者登入才能查論文"
            papers = []
            for p in items:
                card = {"id": p.get("id"), "title": p.get("title"), "authors": p.get("authors") or [],
                        "year": p.get("year"), "venue": p.get("venue"), "citekey": p.get("citekey"),
                        "doi": p.get("doi"), "url": self.paper_url(p.get("id"))}
                papers.append(card)
                if p.get("id") not in seen:
                    seen.add(p.get("id"))
                    out["papers"].append(dict(card, tags=[n]))
                else:
                    next(x for x in out["papers"] if x["id"] == p.get("id"))["tags"].append(n)
            out["tags"].append({"name": n, "url": self.tag_url(n), "papers": papers, "error": err})
        return out

    # ---- 數據目錄 --------------------------------------------------------------------
    @staticmethod
    def catalog_id(node: str, rel: str) -> str:
        return hashlib.sha1(f"{node}/{rel}".encode("utf-8")).hexdigest()[:16]

    def register(self, node: str, rel: str, size: int = 0, user: str = "", run_id: str = "", scheme: str = "",
                 tags: Optional[List[str]] = None, meta: Optional[Dict[str, Any]] = None,
                 project: str = "") -> Dict[str, Any]:
        cid = self.catalog_id(node, rel)
        now = time.time()
        tags = [t.strip() for t in (tags or []) if t and t.strip()]
        project = str(project or "").strip()
        with self._con() as con:
            row = con.execute("SELECT * FROM catalog WHERE id=?", (cid,)).fetchone()
            if row is None:
                con.execute("INSERT INTO catalog (id, node, rel, name, size, created, updated, user, run_id, scheme, "
                            "tags, meta, project) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (cid, node, rel, Path(rel).name, int(size), now, now, user, run_id, scheme,
                             json.dumps(tags, ensure_ascii=False), json.dumps(meta or {}, ensure_ascii=False), project))
            else:
                old_tags = json.loads(row["tags"] or "[]")
                merged = list(dict.fromkeys(old_tags + tags))
                old_meta = json.loads(row["meta"] or "{}")
                old_meta.update(meta or {})
                con.execute("UPDATE catalog SET size=?, updated=?, user=COALESCE(NULLIF(?,''),user), "
                            "run_id=COALESCE(NULLIF(?,''),run_id), scheme=COALESCE(NULLIF(?,''),scheme), tags=?, "
                            "meta=?, project=COALESCE(NULLIF(?,''),project) WHERE id=?",
                            (int(size), now, user, run_id, scheme, json.dumps(merged, ensure_ascii=False),
                             json.dumps(old_meta, ensure_ascii=False), project, cid))
            if project and tags:                           # 專案的數據標籤：用過的標籤自動加入專案
                con.executemany("INSERT OR IGNORE INTO project_tags VALUES (?,?)", [(project, t) for t in tags])
        if project:
            self.schedule_views()
        return self.catalog_item(cid)

    def _row(self, r: sqlite3.Row) -> Dict[str, Any]:
        d = dict(r)
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["meta"] = json.loads(d.get("meta") or "{}")
        d["url"] = "/api/files/" + urllib.parse.quote(f"{d['node']}/{d['rel']}")
        d["exists"] = (self.files_dir / d["node"] / d["rel"]).is_file()
        d["tag_urls"] = {t: self.tag_url(t) for t in d["tags"]}
        d["project"] = d.get("project") or ""
        d["project_name"] = self.project_name(d["project"]) if d["project"] else ""
        return d

    def catalog_item(self, cid: str) -> Dict[str, Any]:
        with self._con() as con:
            r = con.execute("SELECT * FROM catalog WHERE id=?", (cid,)).fetchone()
        if r is None:
            raise PlatformError(404, "數據目錄沒有這個項目")
        return self._row(r)

    def catalog(self, tags: Optional[List[str]] = None, q: str = "", node: str = "", user: str = "",
                mode: str = "and", limit: int = 100, offset: int = 0, project: str = "") -> Dict[str, Any]:
        sql, args = "SELECT * FROM catalog WHERE 1=1", []
        if project:
            sql += " AND project=?"
            args.append(str(project))
        if node:
            sql += " AND node=?"
            args.append(node)
        if user:
            sql += " AND user=?"
            args.append(user)
        if q:
            sql += " AND (name LIKE ? OR rel LIKE ? OR scheme LIKE ? OR meta LIKE ?)"
            args += [f"%{q}%"] * 4
        sql += " ORDER BY created DESC"
        with self._con() as con:
            rows = [self._row(r) for r in con.execute(sql, args)]
        tags = [t for t in (tags or []) if t]
        if tags:
            if mode == "or":
                rows = [r for r in rows if set(tags) & set(r["tags"])]
            else:
                rows = [r for r in rows if set(tags) <= set(r["tags"])]
        total = len(rows)
        return {"total": total, "items": rows[offset:offset + max(1, min(limit, 1000))]}

    def set_catalog_tags(self, cid: str, tags: List[str], by: str = "") -> Dict[str, Any]:
        tags = list(dict.fromkeys(t.strip() for t in tags if t and t.strip()))
        with self._con() as con:
            n = con.execute("UPDATE catalog SET tags=?, updated=? WHERE id=?",
                            (json.dumps(tags, ensure_ascii=False), time.time(), cid)).rowcount
        if not n:
            raise PlatformError(404, "數據目錄沒有這個項目")
        self.audit(by, "tags", f"{cid} {tags}")
        item = self.catalog_item(cid)
        if item["project"] and tags:
            with self._con() as con:
                con.executemany("INSERT OR IGNORE INTO project_tags VALUES (?,?)",
                                [(item["project"], t) for t in tags])
        return item

    # ---- 專案（論文庫 paperlib 1.6+ 的「專案專區」）------------------------------------------
    def service_info(self) -> Dict[str, Any]:
        return {"user": self.service_user, "configured": bool(self.service_user and self._service_password),
                "from_env": bool(os.environ.get("LABHUB_PAPERLIB_SERVICE_USER"))}

    def set_service_account(self, by: str, user: str, password: str) -> Dict[str, Any]:
        """站長在控制台設定論文庫服務帳號（帳密只存在 NAS 的 Hub 資料夾）；先試登入，成功才存。"""
        user = (user or "").strip()
        if not user:
            self._service_file.unlink(missing_ok=True)
            self.service_user, self._service_password, self._service_session = "", "", None
            self.audit(by, "paperlib-service", "清除")
            return self.service_info()
        pl = self.need_paperlib()
        try:
            sess, _ = pl.login(user, password)
        except PlatformError as e:                          # 401 會讓網頁以為自己被登出：改成 400
            raise PlatformError(400 if e.code == 401 else e.code, f"論文庫登入失敗：{e}") from None
        tmp = self._service_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"user": user, "password": password}), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self._service_file)
        with self.lock:
            self.service_user, self._service_password, self._service_session = user, password, sess
            self._proj_cache = (0.0, self._proj_cache[1])
        self.audit(by, "paperlib-service", user)
        return self.service_info()

    def service_session(self, renew: bool = False) -> Optional[Dict[str, Any]]:
        """論文庫服務帳號的 session（{"pl_session": …}）；沒有設定 / 登入失敗 → None。"""
        if self.paperlib is None or not self.service_user or not self._service_password:
            return None
        with self.lock:
            if self._service_session is None or renew:
                try:
                    self._service_session, _ = self.paperlib.login(self.service_user, self._service_password)
                except PlatformError as e:
                    log.warning("論文庫服務帳號 %s 登入失敗：%s", self.service_user, e)
                    self._service_session = None
            return {"pl_session": self._service_session} if self._service_session else None

    def _paperlib_projects(self, sess: Optional[Dict[str, Any]], force: bool = False) -> List[Dict[str, Any]]:
        at, cached = self._proj_cache
        if sess is None and self.paperlib is not None:
            sess = self.service_session()                   # 0.0.31：沒有使用者登入 → 用服務帳號查
            if sess is not None:
                try:
                    return self._fetch_projects(sess, force)
                except PlatformError as e:
                    if e.code == 501:
                        raise
                    retry = self.service_session(renew=True) if e.code == 401 else None   # session 過期：重登一次
                    if retry is not None:
                        try:
                            return self._fetch_projects(retry, force)
                        except PlatformError:
                            pass
                sess = None
        if sess is None or self.paperlib is None:
            if not cached:                                  # 裝置 token：用最近一次記住的專案
                with self._con() as con:
                    cached = [json.loads(r["info"]) for r in con.execute("SELECT info FROM projects_cache")]
            return cached
        try:
            return self._fetch_projects(sess, force)
        except PlatformError as e:
            if e.code != 401:
                raise
            log.warning("讀取論文庫專案失敗：%s", e)
            return cached

    def _fetch_projects(self, sess: Dict[str, Any], force: bool = False) -> List[Dict[str, Any]]:
        at, cached = self._proj_cache
        if not force and time.time() - at < 20 and cached:
            return cached
        try:
            res = self.paperlib.get("/api/projects", sess["pl_session"]) or []
            items = res if isinstance(res, list) else list(res.get("items") or res.get("projects") or [])
        except PlatformError as e:
            if e.code == 404:
                raise PlatformError(501, "論文庫沒有「專案專區」（需要 paperlib 1.6 以上）") from None
            if e.code == 401:
                raise
            log.warning("讀取論文庫專案失敗：%s", e)
            return cached
        projects = [{"id": str(p.get("id")), "name": p.get("name") or "", "short_name": p.get("short_name") or "",
                     "description": p.get("description") or "", "status": p.get("status") or "active",
                     "color": p.get("color") or "#2563eb", "n_papers": p.get("n_papers"),
                     # 1.0.10：子專案（論文庫 parent_id；可以一層一層嵌套）
                     "parent": str(p.get("parent_id") if p.get("parent_id") is not None else (p.get("parent") or ""))}
                    for p in items if isinstance(p, dict) and p.get("id") is not None]
        ids = {p["id"] for p in projects}
        for p in projects:
            if p["parent"] not in ids or p["parent"] == p["id"]:
                p["parent"] = ""                       # 上層不見了（被刪 / 沒權限）：當成最上層
        self._proj_cache = (time.time(), projects)
        changed = False
        with self._con() as con:
            old = {r["id"]: json.loads(r["info"]) for r in con.execute("SELECT id, info FROM projects_cache")}
            for p in projects:
                if (old.get(p["id"]) or {}).get("name") != p["name"]:
                    changed = True
                con.execute("INSERT OR REPLACE INTO projects_cache VALUES (?,?,?)",
                            (p["id"], json.dumps(p, ensure_ascii=False), time.time()))
        if changed:
            self.schedule_views()
        return projects

    def project_name(self, pid: str) -> str:
        for p in self._proj_cache[1]:
            if p["id"] == str(pid):
                return p["name"]
        with self._con() as con:
            r = con.execute("SELECT info FROM projects_cache WHERE id=?", (str(pid),)).fetchone()
        return json.loads(r["info"]).get("name", "") if r else ""

    @staticmethod
    def folder_name(name: str, pid: str) -> str:
        """專案資料夾名稱（Windows / macOS 都合法）。"""
        n = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name or "").strip(" .")
        return n or f"project_{pid}"

    def project_chain(self, pid: str) -> List[Dict[str, Any]]:
        """1.0.10：[最上層, …, 這個專案]（子專案一層一層往上找）。"""
        items = {p["id"]: p for p in (self._proj_cache[1] or [])}
        if str(pid) not in items:
            with self._con() as con:
                for r in con.execute("SELECT info FROM projects_cache"):
                    p = json.loads(r["info"])
                    items.setdefault(str(p.get("id")), p)
        chain: List[Dict[str, Any]] = []
        cur, seen = str(pid), set()
        while cur and cur in items and cur not in seen:
            seen.add(cur)
            chain.insert(0, items[cur])
            cur = str(items[cur].get("parent") or "")
        return chain

    def folder_path(self, pid: str, name: str = "") -> str:
        """專案資料夾（子專案在上層的資料夾裡：主專案/子專案/孫專案）。"""
        chain = self.project_chain(pid)
        if not chain:
            return self.folder_name(name, pid)
        return "/".join(self.folder_name(p.get("name") or "", str(p.get("id"))) for p in chain)

    def projects(self, sess: Optional[Dict[str, Any]], force: bool = False) -> List[Dict[str, Any]]:
        """論文庫的專案 ＋ 平台的數據數量、數據標籤、資料夾。"""
        items = self._paperlib_projects(sess, force)
        with self._con() as con:
            counts = {r["project"]: r["n"] for r in con.execute(
                "SELECT project, COUNT(*) AS n FROM catalog WHERE project!='' GROUP BY project")}
            ptags: Dict[str, List[str]] = {}
            for r in con.execute("SELECT project, tag FROM project_tags ORDER BY tag"):
                ptags.setdefault(r["project"], []).append(r["tag"])
        out = []
        for p in items:
            chain = [x["name"] for x in self.project_chain(p["id"])] or [p["name"]]
            out.append(dict(p, data=counts.get(p["id"], 0), tags=ptags.get(p["id"], []),
                            folder=self.folder_path(p["id"], p["name"]), path=chain,
                            url=f"{self.paperlib_public}/#/projects/{p['id']}" if self.paperlib_public else ""))
        return out

    def create_project(self, sess: Dict[str, Any], name: str, short_name: str = "", description: str = "",
                       color: str = "", parent: str = "") -> Dict[str, Any]:
        name = (name or "").strip()
        if not name or len(name) > 80:
            raise PlatformError(400, "專案名稱 1–80 字")
        pl = self.need_paperlib()
        body: Dict[str, Any] = {"name": name, "short_name": short_name, "description": description}
        if color:
            body["color"] = color
        if parent:
            body["parent_id"] = int(parent) if str(parent).isdigit() else parent     # 1.0.10：子專案
        try:
            pl.send("POST", "/api/projects", body, sess["pl_session"])
        except PlatformError as e:
            if e.code == 403:
                raise PlatformError(403, f"論文庫不允許你建立專案（{e}）；請站長在論文庫建立或開權限") from None
            raise
        self.audit(sess["user"]["username"], "project", f"建立 {name}" + (f"（{parent} 的子專案）" if parent else ""))
        p = next((x for x in self.projects(sess, force=True)
                  if x["name"] == name and (not parent or x.get("parent") == str(parent))), None)
        if p is None:
            raise PlatformError(502, "論文庫回報成功，但讀不到新專案")
        return p

    def project_detail(self, sess: Optional[Dict[str, Any]], pid: str) -> Dict[str, Any]:
        pid = str(pid)
        p = next((x for x in self.projects(sess) if x["id"] == pid), None)
        if p is None:
            raise PlatformError(404, "沒有這個專案")
        papers: List[Dict[str, Any]] = []
        err = ""
        if sess is not None and self.paperlib is not None:
            try:
                d = self.paperlib.get(f"/api/projects/{pid}", sess["pl_session"]) or {}
                raw = d.get("papers") if isinstance(d, dict) else None
                for x in raw or []:
                    x = x.get("paper", x) if isinstance(x, dict) else {}
                    if x.get("id") is None:
                        continue
                    papers.append({"id": x.get("id"), "title": x.get("title"), "authors": x.get("authors") or [],
                                   "year": x.get("year"), "venue": x.get("venue"), "role": x.get("role"),
                                   "url": self.paper_url(x.get("id"))})
            except PlatformError as e:
                err = str(e)
        tagmap = {t["name"]: t for t in self.tags(sess)}
        tags = [dict(tagmap.get(t) or {"name": t, "papers": 0, "data": 0, "url": ""}) for t in p["tags"]]
        return dict(p, papers=papers, papers_error=err, tag_info=tags)

    def set_project_tags(self, pid: str, tags: List[str], replace: bool = False, by: str = "") -> List[str]:
        pid = str(pid)
        tags = [t.strip() for t in tags if t and t.strip()]
        with self._con() as con:
            if replace:
                con.execute("DELETE FROM project_tags WHERE project=?", (pid,))
            con.executemany("INSERT OR IGNORE INTO project_tags VALUES (?,?)", [(pid, t) for t in tags])
            out = [r["tag"] for r in con.execute("SELECT tag FROM project_tags WHERE project=? ORDER BY tag", (pid,))]
        self.audit(by, "project_tags", f"{pid} {tags}")
        return out

    def set_catalog_project(self, cid: str, project: str, by: str = "") -> Dict[str, Any]:
        with self._con() as con:
            n = con.execute("UPDATE catalog SET project=?, updated=? WHERE id=?",
                            (str(project or ""), time.time(), cid)).rowcount
        if not n:
            raise PlatformError(404, "數據目錄沒有這個項目")
        item = self.catalog_item(cid)
        if project and item["tags"]:
            self.set_project_tags(project, item["tags"], by=by)
        self.audit(by, "project", f"{cid} → {project}")
        self.schedule_views()
        return item

    def project_files(self, pid: str) -> List[Dict[str, Any]]:
        """專案的數據檔（給啟動器同步到本機資料夾）：rel = 在專案資料夾裡的相對路徑。"""
        out = []
        for d in self.catalog(project=str(pid), limit=100000)["items"]:
            if d["exists"]:
                out.append({"id": d["id"], "name": d["name"], "size": d["size"], "url": d["url"], "updated": d["updated"],
                            "rel": f"{d['node']}/{d['rel']}"})
        return out

    # ---- 專案資料夾（data/projects/<專案>/<節點>/<路徑>，硬連結，不多佔空間）------------------------
    def schedule_views(self, delay: float = 2.0) -> None:
        with self._views_lock:
            if self._views_timer is not None:
                self._views_timer.cancel()
            self._views_timer = threading.Timer(delay, self._views_safe)
            self._views_timer.daemon = True
            self._views_timer.start()

    def _views_safe(self) -> None:
        try:
            self.sync_views()
        except Exception:  # noqa: BLE001
            log.exception("更新專案資料夾失敗")

    def sync_views(self) -> Dict[str, int]:
        """讓 data/projects/ 和數據目錄一致：每個專案一個資料夾，裡面是該專案的數據檔（硬連結；不能硬連結時複製）。
        讀檔模塊（LabLogViewer）直接把 NAS 上的 LabControlHub/data/projects/<專案> 當資料庫開啟。
        只會刪除 Hub 自己放進去、已經不屬於這個專案的檔；使用者（例如讀檔模塊的去背景輸出）放的檔不會動。"""
        want: Dict[Path, Path] = {}
        with self._con() as con:
            rows = con.execute("SELECT node, rel, project FROM catalog WHERE project!=''").fetchall()
        for r in rows:
            src = self.files_dir / r["node"] / r["rel"]
            if not src.is_file():
                continue
            folder = self.folder_path(r["project"], self.project_name(r["project"]))
            want[self.views_dir.joinpath(*folder.split("/")) / r["node"] / r["rel"]] = src
        with self._con() as con:
            managed = {r["path"] for r in con.execute("SELECT path FROM view_files")}
        made = linked = copied = removed = 0
        for dst, src in want.items():
            if dst.exists():
                try:
                    if os.path.samefile(dst, src) or dst.stat().st_size == src.stat().st_size:
                        continue
                except OSError:
                    pass
                dst.unlink()
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(src, dst)
                linked += 1
            except OSError:
                import shutil

                shutil.copy2(src, dst)                     # 不同檔案系統：只好複製
                copied += 1
            made += 1
        wanted = {str(d) for d in want}
        for path in managed - wanted:                      # Hub 放的、已經不屬於這個專案的檔
            fp = Path(path)
            try:
                if fp.is_file():
                    fp.unlink()
                    removed += 1
            except OSError:
                continue
            parent = fp.parent
            while parent != self.views_dir and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        with self._con() as con:
            con.execute("DELETE FROM view_files")
            con.executemany("INSERT OR IGNORE INTO view_files VALUES (?)", [(w,) for w in wanted])
        return {"made": made, "linked": linked, "copied": copied, "removed": removed}

    def tag_data_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        with self._con() as con:
            for r in con.execute("SELECT tags FROM catalog"):
                for t in json.loads(r["tags"] or "[]"):
                    counts[t] = counts.get(t, 0) + 1
        return counts

    def rescan(self) -> int:
        """把 files/ 裡還沒登記的檔案加進數據目錄（第一次啟用平台功能時，舊的上傳檔也會出現）。"""
        n = 0
        if not self.files_dir.exists():
            return 0
        with self._con() as con:
            known = {r["id"] for r in con.execute("SELECT id FROM catalog")}
        for p in self.files_dir.rglob("*"):
            if not p.is_file() or p.name.startswith("."):
                continue
            rel = p.relative_to(self.files_dir)
            node, sub = rel.parts[0], Path(*rel.parts[1:]).as_posix()
            if self.catalog_id(node, sub) in known:
                continue
            self.register(node, sub, p.stat().st_size)
            n += 1
        return n

    # ---- 站長：使用者 ----------------------------------------------------------------------
    def users(self, sess: Dict[str, Any]) -> List[Dict[str, Any]]:
        pl = self.need_paperlib()
        users = pl.get("/api/users", sess["pl_session"]) or []
        perms = self.perms_table()
        mods = [m for m in self.modules() if not (DEFAULT_MODULES.get(m) or {}).get("owner_only")]
        out = []
        for u in users:
            name = u.get("username")
            p = perms.get(name, {})
            out.append({"id": u.get("id"), "username": name, "display_name": u.get("display_name"),
                        "role": u.get("role"), "owner": bool(u.get("owner")), "disabled": bool(u.get("disabled")),
                        "email": u.get("email") or "",
                        "modules": {m: (True if u.get("owner") else p.get(m, True)) for m in mods}})
        return out

    def set_user_enabled(self, sess: Dict[str, Any], uid: int, enabled: bool) -> Dict[str, Any]:
        pl = self.need_paperlib()
        users = {u.get("id"): u for u in (pl.get("/api/users", sess["pl_session"]) or [])}
        u = users.get(uid)
        if u is None:
            raise PlatformError(404, "沒有這個使用者")
        pl.send("PATCH", f"/api/users/{uid}", {"disabled": not enabled}, sess["pl_session"])
        if not enabled:
            self.drop_user_sessions(u.get("username"))
        self.audit(sess["user"]["username"], "user", f"{u.get('username')} {'enable' if enabled else 'disable'}")
        return {"ok": True, "username": u.get("username"), "disabled": not enabled}

    def recent_audit(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._con() as con:
            return [dict(r) for r in con.execute("SELECT * FROM audit ORDER BY time DESC LIMIT ?", (limit,))]

    def status(self) -> Dict[str, Any]:
        pl: Dict[str, Any] = {"configured": self.paperlib is not None, "url": self.paperlib_public}
        if self.paperlib is not None:
            try:
                s = self.paperlib.site()
                pl.update(online=True, version=s.get("version"), name=s.get("name"))
            except PlatformError as e:
                pl.update(online=False, error=str(e))
        with self._con() as con:
            n_cat = con.execute("SELECT COUNT(*) FROM catalog").fetchone()[0]
            n_sess = con.execute("SELECT COUNT(DISTINCT username) FROM sessions WHERE expires > ?",
                                 (time.time(),)).fetchone()[0]
        return {"paperlib": pl, "catalog": n_cat, "users_logged_in": n_sess,
                "modules": {k: v.get("latest") for k, v in self.modules().items()}}
