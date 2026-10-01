"""大程式伺服器（portal）的用戶端：所有模塊經這裡和 NAS 溝通。

只用標準函式庫。每個方法都對應 ``docs/PROTOCOL.md`` 的一個 API；回傳解析後的 JSON。
錯誤一律丟 ``CommError`` 的子類別，訊息可直接顯示給使用者。
"""
from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from . import PROTOCOL, __version__
from .config import split_urls
from .errors import CommError, NotLoggedIn, PermissionDenied, Unreachable

API = "/api/v1"


class PortalClient:
    def __init__(self, portal_url: "str | Iterable[str]", token: str = "", timeout: float = 10.0,
                 client_name: str = "") -> None:
        urls = split_urls(portal_url) if isinstance(portal_url, str) else [u.rstrip("/") for u in portal_url]
        if not urls:
            raise ValueError("沒有大程式網址")
        self.urls: List[str] = urls
        self.base: str = urls[0]
        self.token = token or ""
        self.timeout = timeout
        self.client_name = client_name or f"labcomm/{__version__}"
        # 實驗室內網 / VPN：不經系統 proxy（與 Lab Control Hub 用法相同）
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # ---- 底層 ------------------------------------------------------------------
    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h = {"Accept": "application/json", "User-Agent": self.client_name, "X-QEL": "1",
             "X-QEL-Protocol": str(PROTOCOL)}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if extra:
            h.update(extra)
        return h

    def _open(self, method: str, path: str, data: Optional[bytes], headers: Dict[str, str],
              timeout: Optional[float]):
        """依序嘗試每個網址（內網 → VPN），成功的放到最前面。"""
        last: Optional[BaseException] = None
        order = [self.base] + [u for u in self.urls if u != self.base]
        for base in order:
            req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
            try:
                resp = self._opener.open(req, timeout=timeout or self.timeout)
                self.base = base
                return resp
            except urllib.error.HTTPError as e:
                self.base = base
                raise self._http_error(e) from None
            except (urllib.error.URLError, OSError) as e:
                last = e
        raise Unreachable(f"連不到大程式（{', '.join(order)}）：{getattr(last, 'reason', last)}")

    @staticmethod
    def _http_error(e: urllib.error.HTTPError) -> CommError:
        try:
            body = json.loads(e.read().decode("utf-8") or "{}")
            msg = body.get("error") or body.get("detail") or e.reason
        except Exception:  # noqa: BLE001
            msg = e.reason
        if e.code == 401:
            return NotLoggedIn(str(msg or "請先登入"), 401)
        if e.code == 403:
            return PermissionDenied(str(msg or "沒有權限"), 403)
        return CommError(str(msg), e.code)

    def request(self, method: str, path: str, body: Any = None, query: Optional[Dict[str, Any]] = None,
                timeout: Optional[float] = None, raw: Optional[bytes] = None,
                content_type: str = "application/octet-stream") -> Any:
        if query:
            q = {k: v for k, v in query.items() if v is not None and v != ""}
            if q:
                path += ("&" if "?" in path else "?") + urllib.parse.urlencode(q, doseq=True)
        headers = self._headers()
        data = None
        if raw is not None:
            data = raw
            headers["Content-Type"] = content_type
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        with self._open(method, path, data, headers, timeout) as r:
            text = r.read().decode("utf-8")
        return json.loads(text) if text else None

    def api(self, method: str, path: str, body: Any = None, **kw: Any) -> Any:
        return self.request(method, API + path, body, **kw)

    def download(self, path: str, dest: "str | Path", progress: Optional[Callable[[int, int], None]] = None,
                 timeout: float = 600) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        with self._open("GET", API + path if not path.startswith("/api/") else path, None,
                        self._headers({"Accept": "*/*"}), timeout) as r, open(tmp, "wb") as f:
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
        shutil.move(str(tmp), str(dest))
        return dest

    # ---- 基本 ------------------------------------------------------------------
    def ping(self) -> Dict[str, Any]:
        return self.api("GET", "/ping", timeout=5)

    def login(self, username: str, password: str, code: str = "") -> Dict[str, Any]:
        """用論文庫帳號登入。需要兩步驟驗證時回傳 {"need_2fa": True}。"""
        r = self.api("POST", "/auth/login", {"username": username, "password": password, "code": code,
                                             "client": self.client_name})
        if r and r.get("token"):
            self.token = r["token"]
        return r

    def logout(self) -> None:
        try:
            self.api("POST", "/auth/logout", {})
        finally:
            self.token = ""

    def register(self, username: str, password: str, display_name: str, email: str, note: str = "") -> Dict[str, Any]:
        """申請帳號（轉給論文庫，由站長審核）。"""
        return self.api("POST", "/auth/register", {"username": username, "password": password,
                                                   "display_name": display_name, "email": email, "note": note})

    def me(self) -> Dict[str, Any]:
        return self.api("GET", "/me")

    def health(self) -> Dict[str, Any]:
        return self.api("GET", "/health")

    # ---- 模塊與更新 --------------------------------------------------------------
    def modules(self) -> List[Dict[str, Any]]:
        return self.api("GET", "/modules")["modules"]

    def releases(self, module_id: str) -> List[Dict[str, Any]]:
        return self.api("GET", f"/modules/{_q(module_id)}/releases")["releases"]

    def download_release(self, module_id: str, version: str, dest: "str | Path",
                         progress: Optional[Callable[[int, int], None]] = None) -> Path:
        return self.download(f"/modules/{_q(module_id)}/releases/{_q(version)}.zip", dest, progress)

    def publish_release(self, module_id: str, version: str, zip_bytes: bytes, notes: str = "") -> Dict[str, Any]:
        return self.api("PUT", f"/modules/{_q(module_id)}/releases/{_q(version)}", raw=zip_bytes,
                        content_type="application/zip", query={"notes": notes}, timeout=600)

    # ---- 標籤 ------------------------------------------------------------------
    def tags(self) -> Dict[str, Any]:
        """{"categories": [...], "tags": [{"name","category","color","description","aliases","papers":[id…]}]}"""
        return self.api("GET", "/tags")

    def save_tag(self, name: str, category: Optional[str] = None, color: Optional[str] = None,
                 description: Optional[str] = None, aliases: Optional[List[str]] = None) -> Dict[str, Any]:
        body = {k: v for k, v in dict(name=name, category=category, color=color, description=description,
                                      aliases=aliases).items() if v is not None}
        return self.api("POST", "/tags", body)

    def link_papers(self, tag: str, paper_ids: List[int]) -> Dict[str, Any]:
        return self.api("PUT", f"/tags/{_q(tag)}/papers", {"paper_ids": [int(i) for i in paper_ids]})

    def tag_papers(self, tag: str) -> List[Dict[str, Any]]:
        return self.api("GET", f"/tags/{_q(tag)}/papers")["papers"]

    # ---- 數據 ------------------------------------------------------------------
    def register_dataset(self, name: str, path: str = "", tags: Optional[List[str]] = None,
                         scheme: Optional[dict] = None, source: Optional[dict] = None,
                         hub_file: Optional[dict] = None, meta: Optional[dict] = None,
                         fingerprint: str = "") -> Dict[str, Any]:
        """登錄一筆量測數據；同一個 fingerprint 或 path 重複登錄時更新原本那筆。

        tags 為 None 時不改原本的標籤（給 [] 才會清空）。"""
        body: Dict[str, Any] = {"name": name, "path": path, "scheme": scheme, "source": source or {},
                                "hub_file": hub_file, "meta": meta or {}, "fingerprint": fingerprint}
        if tags is not None:
            body["tags"] = list(tags)
        return self.api("POST", "/datasets", body)

    def datasets(self, tag: str = "", q: str = "", limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        return self.api("GET", "/datasets", query={"tag": tag, "q": q, "limit": limit, "offset": offset})

    def dataset(self, dataset_id: int) -> Dict[str, Any]:
        return self.api("GET", f"/datasets/{int(dataset_id)}")

    def find_dataset(self, path: str = "", fingerprint: str = "") -> Optional[Dict[str, Any]]:
        r = self.api("GET", "/datasets/lookup", query={"path": path, "fingerprint": fingerprint})
        return r.get("dataset")

    def set_dataset_tags(self, dataset_id: int, tags: List[str]) -> Dict[str, Any]:
        return self.api("PATCH", f"/datasets/{int(dataset_id)}", {"tags": list(tags)})

    def dataset_scheme(self, dataset_id: int) -> Optional[dict]:
        return self.api("GET", f"/datasets/{int(dataset_id)}/scheme").get("scheme")

    def dataset_papers(self, dataset_id: int) -> List[Dict[str, Any]]:
        """論文依標籤分組：[{"tag", "papers": [{"id","title","year","url"}]}]"""
        return self.api("GET", f"/datasets/{int(dataset_id)}/papers")["groups"]

    def download_dataset(self, dataset_id: int, dest: "str | Path",
                         progress: Optional[Callable[[int, int], None]] = None) -> Path:
        return self.download(f"/datasets/{int(dataset_id)}/file", dest, progress)

    # ---- 論文 ------------------------------------------------------------------
    def papers(self, tag: str = "", q: str = "", limit: int = 30) -> Dict[str, Any]:
        return self.api("GET", "/papers", query={"tag": tag, "q": q, "limit": limit})

    def papers_for_tags(self, tags: List[str]) -> List[Dict[str, Any]]:
        return self.api("POST", "/papers/by-tags", {"tags": list(tags)})["groups"]

    def paper_url(self, paper_id: int) -> str:
        return self.api("GET", f"/papers/{int(paper_id)}/link")["url"]

    # ---- 事件與跨電腦傳遞 ------------------------------------------------------------
    def events(self, after: int = 0, wait: float = 25, topics: Optional[List[str]] = None) -> Dict[str, Any]:
        """long-poll：{"last": 序號, "events": [{"seq","topic","data","time","by"}]}"""
        return self.api("GET", "/events", query={"after": after, "wait": wait,
                                                 "topics": ",".join(topics) if topics else ""},
                        timeout=wait + 15)

    def publish(self, topic: str, data: Any) -> Dict[str, Any]:
        return self.api("POST", "/events", {"topic": topic, "data": data})

    def handoff(self, module: str, action: str, payload: dict) -> Dict[str, Any]:
        """把動作送給「同一個使用者」在其他電腦上開著的模塊。"""
        return self.api("POST", "/handoff", {"module": module, "action": action, "payload": payload})

    # ---- 量測中繼（Lab Control Hub，經大程式轉送，不需要 Hub token） ----------------------
    def relay(self, method: str, path: str, body: Any = None, query: Optional[Dict[str, Any]] = None,
              timeout: Optional[float] = None) -> Any:
        path = path if path.startswith("/") else "/" + path
        return self.api(method, "/relay" + path, body, query=query, timeout=timeout)


def _q(s: str) -> str:
    return urllib.parse.quote(str(s), safe="")
