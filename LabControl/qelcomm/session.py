"""平台 session：登入、標籤、論文、數據目錄、檔案、模塊訊息、模塊下載。"""
from __future__ import annotations

import json
import os
import platform as _platform
import shutil
import socket
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Union

DEFAULT_GATEWAY_ENV = "QEL_GATEWAY"
DEFAULT_TOKEN_ENV = "QEL_TOKEN"


class QelError(RuntimeError):
    def __init__(self, message: str, code: int = 0) -> None:
        super().__init__(message)
        self.code = code


class QelAuthError(QelError):
    """沒登入 / 登入失效（401）或沒有權限（403）。"""


def qel_home() -> Path:
    base = os.environ.get("QEL_HOME")
    return Path(base).expanduser() if base else Path.home() / ".qel"


def session_file() -> Path:
    return qel_home() / "session.json"


def client_name() -> str:
    try:
        host = socket.gethostname()
    except OSError:
        host = "?"
    return f"{host} ({_platform.system()})"


def normalize_gateway(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    return url


class Session:
    """一位使用者在一台電腦上的登入（Hub 發的使用者 token，30 天）。"""

    def __init__(self, gateway: str, token: str = "", user: Optional[Dict[str, Any]] = None,
                 timeout: float = 15.0, paperlib_url: str = "") -> None:
        self.gateway = normalize_gateway(gateway)
        self.token = token
        self.user = user or {}
        self.timeout = timeout
        self.paperlib_url = paperlib_url
        # 實驗室內網 / VPN 直連：不走系統 proxy
        self._op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __repr__(self) -> str:
        return f"Session({self.gateway!r}, user={self.user.get('username')!r})"

    # ---- HTTP ---------------------------------------------------------------
    def url(self, path: str) -> str:
        return path if path.startswith("http") else self.gateway + path

    def request(self, method: str, path: str, body: Any = None, raw: Optional[bytes] = None,
                timeout: Optional[float] = None, headers: Optional[Dict[str, str]] = None) -> Any:
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode("utf-8"))
        req = urllib.request.Request(self.url(path), data=data, method=method)
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with self._op.open(req, timeout=timeout or self.timeout) as r:
                b = r.read()
                if r.headers.get_content_type() == "application/json":
                    return json.loads(b.decode("utf-8")) if b else None
                return b
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode("utf-8")).get("error") or f"HTTP {e.code}"
            except (ValueError, AttributeError):
                msg = f"HTTP {e.code}"
            cls = QelAuthError if e.code in (401, 403) else QelError
            raise cls(msg, e.code) from None
        except (urllib.error.URLError, OSError) as e:
            raise QelError(f"連不到平台（{self.gateway}）：{getattr(e, 'reason', e)}") from None

    def get(self, path: str, **params: Any) -> Any:
        q = {k: (",".join(v) if isinstance(v, (list, tuple)) else v) for k, v in params.items()
             if v not in (None, "", [], ())}
        return self.request("GET", path + ("?" + urllib.parse.urlencode(q) if q else ""))

    # ---- 登入 ---------------------------------------------------------------
    @classmethod
    def login(cls, gateway: str, username: str, password: str, code: str = "", save: bool = True,
              client: str = "") -> "Session":
        """用論文庫帳號登入。需要兩步驟驗證時丟出 QelAuthError("need_2fa")。"""
        s = cls(gateway)
        r = s.request("POST", "/api/platform/login", {"username": username, "password": password, "code": code,
                                                      "client": client or client_name()})
        s.token, s.user, s.paperlib_url = r["token"], r.get("user") or {}, r.get("paperlib_url") or ""
        if save:
            s.save()
        return s

    def logout(self) -> None:
        try:
            if self.token.startswith("u_"):
                self.request("POST", "/api/platform/logout", {})
        except QelError:
            pass
        self.forget()

    def save(self, path: Optional[Path] = None) -> Path:
        p = path or session_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"gateway": self.gateway, "token": self.token, "user": self.user,
                                   "paperlib_url": self.paperlib_url, "saved": time.time()},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)            # 只有自己能讀
        except OSError:
            pass
        os.replace(tmp, p)
        return p

    def forget(self, path: Optional[Path] = None) -> None:
        p = path or session_file()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("token") == self.token:
                p.unlink()
        except (OSError, ValueError):
            pass

    @classmethod
    def load(cls, path: Optional[Path] = None) -> Optional["Session"]:
        try:
            d = json.loads((path or session_file()).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not d.get("gateway") or not d.get("token"):
            return None
        return cls(d["gateway"], d["token"], d.get("user") or {}, paperlib_url=d.get("paperlib_url") or "")

    @classmethod
    def current(cls) -> Optional["Session"]:
        """目前的登入：環境變數（啟動器開模塊時帶入）優先，其次是 ~/.qel/session.json。"""
        gw, tok = os.environ.get(DEFAULT_GATEWAY_ENV), os.environ.get(DEFAULT_TOKEN_ENV)
        if gw and tok:
            return cls(gw, tok)
        return cls.load()

    def env(self) -> Dict[str, str]:
        """給子程式（模塊）的環境變數。"""
        return {DEFAULT_GATEWAY_ENV: self.gateway, DEFAULT_TOKEN_ENV: self.token}

    def me(self) -> Dict[str, Any]:
        r = self.request("GET", "/api/platform/me")
        self.user = r.get("user") or self.user
        self.paperlib_url = r.get("paperlib_url") or self.paperlib_url
        return r

    def check(self) -> bool:
        try:
            self.me()
            return True
        except QelAuthError:
            return False

    @property
    def is_owner(self) -> bool:
        return bool(self.user.get("owner"))

    @property
    def username(self) -> str:
        return str(self.user.get("username") or "")

    # ---- 平台 ---------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        return self.request("GET", "/api/platform/status")

    def modules(self) -> List[Dict[str, Any]]:
        return self.request("GET", "/api/platform/modules")["modules"]

    def module(self, mid: str) -> Optional[Dict[str, Any]]:
        return next((m for m in self.modules() if m.get("id") == mid), None)

    def download_module(self, mid: str, version: str, filename: str, dest: Union[str, Path],
                        progress: Optional[Callable[[int, int], None]] = None) -> Path:
        url = f"/api/platform/modules/{mid}/{urllib.parse.quote(version)}/{urllib.parse.quote(filename)}"
        return self._download(url, Path(dest).expanduser() / filename, progress)

    def publish_module(self, mid: str, version: str, path: Union[str, Path], notes: str = "") -> Dict[str, Any]:
        p = Path(path)
        return self.request("PUT", f"/api/platform/modules/{mid}/{urllib.parse.quote(version)}/"
                                   f"{urllib.parse.quote(p.name)}?notes={urllib.parse.quote(notes)}",
                            raw=p.read_bytes(), timeout=max(self.timeout, 300))

    # ---- 標籤與論文 -------------------------------------------------------------
    def tags(self) -> List[Dict[str, Any]]:
        return self.request("GET", "/api/platform/tags")["tags"]

    def create_tag(self, name: str, category: str = "", color: str = "", description: str = "") -> Dict[str, Any]:
        return self.request("POST", "/api/platform/tags", {"name": name, "category": category, "color": color,
                                                           "description": description})

    def set_tag_meta(self, name: str, **kw: Any) -> None:
        self.request("PATCH", "/api/platform/tags/" + urllib.parse.quote(name, safe=""), kw)

    def papers(self, tags: Iterable[str], limit: int = 50) -> Dict[str, Any]:
        """{tags: [{name, url, papers, error}], papers: [{id, title, authors, year, url, tags}]}"""
        return self.get("/api/platform/papers", tags=list(tags), limit=limit)

    # ---- 數據目錄 ---------------------------------------------------------------
    def catalog(self, tags: Optional[Iterable[str]] = None, q: str = "", node: str = "", user: str = "",
                mode: str = "and", limit: int = 100, offset: int = 0, project: str = "") -> Dict[str, Any]:
        return self.get("/api/platform/catalog", tags=list(tags or []), q=q, node=node, user=user, mode=mode,
                        limit=limit, offset=offset or None, project=project)

    # ---- 專案（論文庫的專案專區）---------------------------------------------------
    def projects(self, refresh: bool = False) -> List[Dict[str, Any]]:
        """[{id, name, short_name, color, data, tags, folder, url}]"""
        return self.get("/api/platform/projects", refresh="1" if refresh else None)["projects"]

    def project(self, pid: str) -> Dict[str, Any]:
        """專案詳細：數據標籤（tag_info，含論文數）、論文（papers，每篇有 url）、數據數。"""
        return self.request("GET", f"/api/platform/projects/{urllib.parse.quote(str(pid), safe='')}")

    def create_project(self, name: str, short_name: str = "", description: str = "", color: str = "",
                       parent: str = "") -> Dict[str, Any]:
        """在論文庫建立專案（需要論文庫的權限）；parent = 上層專案 id → 子專案（1.0.10）。"""
        body = {"name": name, "short_name": short_name, "description": description, "color": color}
        if parent:
            body["parent"] = str(parent)
        return self.request("POST", "/api/platform/projects", body)

    def add_project_tags(self, pid: str, tags: Iterable[str]) -> List[str]:
        return self.request("PUT", f"/api/platform/projects/{urllib.parse.quote(str(pid), safe='')}/tags",
                            {"tags": list(tags)})["tags"]

    def set_item_project(self, cid: str, project: str) -> Dict[str, Any]:
        return self.request("PUT", f"/api/platform/catalog/{cid}/project", {"project": project})

    def project_folder(self, pid: str, shares: Iterable[str] = ()) -> Optional[Path]:
        """NAS 上的專案資料夾（這台電腦看得到的話，例如 Windows 的網路路徑或 macOS 的 /Volumes/…），沒有就 None。"""
        info = self.request("GET", f"/api/platform/projects/{urllib.parse.quote(str(pid), safe='')}/files")
        for base in shares:
            base = (base or "").strip()
            if base and Path(base).is_dir():
                p = Path(base) / info["folder"]
                if p.is_dir():
                    return p
        return None

    def sync_project(self, pid: str, dest: Union[str, Path],
                     progress: Optional[Callable[[int, int, str], None]] = None) -> Path:
        """把專案的數據檔同步到本機資料夾 dest/<專案>（只下載新的或大小不同的），回傳資料夾。"""
        info = self.request("GET", f"/api/platform/projects/{urllib.parse.quote(str(pid), safe='')}/files")
        root = Path(dest).expanduser() / info["folder"]
        root.mkdir(parents=True, exist_ok=True)
        files = info["files"]
        for i, f in enumerate(files):
            target = root / Path(*f["rel"].split("/"))
            if progress:
                progress(i, len(files), f["name"])
            if target.is_file() and target.stat().st_size == f["size"]:
                continue
            self._download(f["url"], target)
        if progress:
            progress(len(files), len(files), "")
        return root

    def item(self, cid: str) -> Dict[str, Any]:
        return self.request("GET", f"/api/platform/catalog/{cid}")

    def item_papers(self, cid: str) -> Dict[str, Any]:
        return self.request("GET", f"/api/platform/catalog/{cid}/papers")

    def set_item_tags(self, cid: str, tags: Iterable[str]) -> Dict[str, Any]:
        return self.request("PUT", f"/api/platform/catalog/{cid}/tags", {"tags": list(tags)})

    def register(self, node: str, rel: str, size: int = 0, tags: Iterable[str] = (), run_id: str = "",
                 scheme: str = "", meta: Optional[Dict[str, Any]] = None, project: str = "") -> Dict[str, Any]:
        """只登記（檔案留在量測電腦上，例如沒有接 Hub 上傳的本機量測）。"""
        return self.request("POST", "/api/platform/catalog/register",
                            {"node": node, "rel": rel, "size": size, "tags": list(tags), "run_id": run_id,
                             "scheme": scheme, "meta": meta or {}, "project": project})

    def upload(self, node: str, rel: str, src: Union[str, Path, bytes], tags: Iterable[str] = (), run_id: str = "",
               scheme: str = "", meta: Optional[Dict[str, Any]] = None, project: str = "") -> Dict[str, Any]:
        data = src if isinstance(src, (bytes, bytearray)) else Path(src).read_bytes()
        q = {"tags": ",".join(tags), "run": run_id, "scheme": scheme, "project": str(project or ""),
             "meta": json.dumps(meta or {}, ensure_ascii=False, default=str)}
        path = (f"/api/files/{urllib.parse.quote(node, safe='')}/{urllib.parse.quote(rel)}?"
                + urllib.parse.urlencode({k: v for k, v in q.items() if v}))
        return self.request("PUT", path, raw=bytes(data), timeout=max(self.timeout, 600))

    def download(self, item: Union[str, Dict[str, Any]], dest: Union[str, Path],
                 progress: Optional[Callable[[int, int], None]] = None) -> Path:
        """下載數據檔。item = 目錄項目（dict）、目錄 id 或 /api/files/… 網址；dest = 資料夾或檔名。"""
        if isinstance(item, str) and not item.startswith(("/", "http")):
            item = self.item(item)
        url = item["url"] if isinstance(item, dict) else item
        name = item["name"] if isinstance(item, dict) else urllib.parse.unquote(url.rsplit("/", 1)[-1])
        d = Path(dest).expanduser()
        target = d / name if d.is_dir() or str(dest).endswith(("/", "\\")) else d
        return self._download(url, target, progress)

    def _download(self, url: str, target: Path, progress: Optional[Callable[[int, int], None]] = None) -> Path:
        req = urllib.request.Request(self.url(url))
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.part")
        try:
            with self._op.open(req, timeout=max(self.timeout, 60)) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        except urllib.error.HTTPError as e:
            if tmp.exists():
                tmp.unlink()
            cls = QelAuthError if e.code in (401, 403) else QelError
            raise cls(f"下載失敗（HTTP {e.code}）", e.code) from None
        except (urllib.error.URLError, OSError) as e:
            if tmp.exists():
                tmp.unlink()
            raise QelError(f"下載失敗：{getattr(e, 'reason', e)}") from None
        os.replace(tmp, target)
        return target

    # ---- 模塊之間的訊息 ----------------------------------------------------------
    def send(self, module: str, type: str, /, from_module: str = "", **payload: Any) -> int:
        """送訊息給「這位使用者」的某個模塊（他登入中的每台電腦 / 手機都收得到）。"""
        body = dict(payload, type=type, from_module=from_module)
        return int(self.request("POST", f"/api/platform/bus/{module}", body)["id"])

    def poll(self, module: str, after: int = 0, wait: float = 25.0) -> Dict[str, Any]:
        return self.request("GET", f"/api/platform/bus/{module}?after={int(after)}&wait={wait}",
                            timeout=wait + 15)

    def listen(self, module: str, stop: Optional[threading.Event] = None,
               wait: float = 25.0) -> Iterator[Dict[str, Any]]:
        """持續收訊息（只收開始聽之後送來的）。連線斷了會自己重試。"""
        stop = stop or threading.Event()
        after = None
        while not stop.is_set():
            try:
                if after is None:
                    after = int(self.poll(module, 0, 0).get("seq", 0))
                    continue
                r = self.poll(module, after, wait)
                for m in r.get("messages", []):
                    after = max(after, int(m["id"]))
                    yield m
            except QelAuthError:
                raise
            except QelError:
                stop.wait(5)


class Listener(threading.Thread):
    """背景收某模塊的訊息，交給 callback（callback 在背景執行緒被呼叫；GUI 請自己轉回主執行緒）。"""

    def __init__(self, session: Session, module: str, callback: Callable[[Dict[str, Any]], None],
                 on_error: Optional[Callable[[Exception], None]] = None) -> None:
        super().__init__(daemon=True, name=f"qel-bus-{module}")
        self.session, self.module, self.callback, self.on_error = session, module, callback, on_error
        self.stop_event = threading.Event()

    def run(self) -> None:
        try:
            for m in self.session.listen(self.module, self.stop_event):
                try:
                    self.callback(m)
                except Exception as e:  # noqa: BLE001
                    if self.on_error:
                        self.on_error(e)
        except QelError as e:
            if self.on_error:
                self.on_error(e)

    def stop(self) -> None:
        self.stop_event.set()


def copy_file(src: Path, dst: Path) -> Path:      # 小工具：拖放時把檔案複製到暫存
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst
