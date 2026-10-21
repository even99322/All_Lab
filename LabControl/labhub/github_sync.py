"""1.0.13：控制台「從 GitHub 取得新版」——Hub（NAS）自己到 GitHub Releases 下載雲端打包好的檔案，不用站長先下載再上傳。

    GET  /api/platform/console/github           → 最新的 labcontrol-v* 與 lablogviewer-v* 發佈，每個檔案：判斷為哪個模塊、
                                                   線上是不是已經有了；以及下載工作的進度、自動模式
    POST /api/platform/console/github/fetch      {publish: bool} → 下載線上還沒有的檔案到暫存（跟上傳的一樣列在「發佈更新」），
                                                   publish = true 時下載完直接發佈（Hub 最後才更新）
    PUT  /api/platform/console/github/auto       {auto: bool} → 每 30 分鐘檢查一次，有新版自動下載並發佈

GitHub repo 是公開的，不需要 token（每小時 60 次 API 查詢；結果快取 60 秒）。``LABHUB_GITHUB_TOKEN`` 可以給 token，
``LABHUB_GITHUB_REPO`` / ``LABHUB_GITHUB_API`` 改 repo / API 位址（測試用）。只用標準函式庫。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .packages import PackageError, identify_binary

PREFIXES = ("labcontrol-v", "lablogviewer-v")
CACHE_S = 60.0
AUTO_EVERY_S = 30 * 60.0
_ZIP = re.compile(r"^(?P<name>LabControlHub|LabControlMonitor|LabControl|LabLogViewerPets|LabLogViewer|paperlib)"
                  r"[_-]v?(?P<ver>\d+(?:\.\d+){0,3})(?P<upd>[_-]update)?\.zip$", re.I)
_ZIP_TARGET = {"labcontrolhub": "hub", "labcontrolmonitor": "monitor", "labcontrol": "labcontrol",
               "lablogviewerpets": "llvpets", "lablogviewer": "lablogviewer",
               "paperlib": "paperlib"}


class GithubError(RuntimeError):
    pass


def _vkey(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "0"))


def guess(name: str) -> Optional[Dict[str, Any]]:
    """從檔名猜是哪個模塊、哪一版（下載前用來判斷線上有沒有）；認不出來 → None。"""
    m = _ZIP.match(name)
    if m:
        tid = _ZIP_TARGET.get(m.group("name").lower())
        if tid:
            return {"target": tid, "version": m.group("ver"), "file": name, "partial": bool(m.group("upd"))}
    try:
        b = identify_binary(name)
    except PackageError:
        return None
    return {"target": b["target"], "version": b["version"], "file": name, "platform": b["platform"]} if b else None


class GithubSync:
    def __init__(self, console: Any) -> None:
        self.console = console
        self.repo = os.environ.get("LABHUB_GITHUB_REPO", "even99322/All_Lab")
        self.api = os.environ.get("LABHUB_GITHUB_API", "https://api.github.com").rstrip("/")
        self.token = os.environ.get("LABHUB_GITHUB_TOKEN", "")
        self.lock = threading.Lock()
        self._cache: Optional[tuple] = None
        self.job: Optional[Dict[str, Any]] = None
        self.cfg_path = Path(console.dir).parent / "github_sync.json"
        self._auto_thread: Optional[threading.Thread] = None
        if self.config().get("auto"):
            self._start_auto()

    # ---- 設定 ---------------------------------------------------------------------------------
    def config(self) -> Dict[str, Any]:
        try:
            return json.loads(self.cfg_path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return {}

    def _save_cfg(self, **kw: Any) -> None:
        d = self.config()
        d.update(kw)
        self.cfg_path.parent.mkdir(parents=True, exist_ok=True)
        self.cfg_path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def set_auto(self, on: bool, by: str = "") -> Dict[str, Any]:
        self._save_cfg(auto=bool(on), auto_by=by)
        if on:
            self._start_auto()
        return self.config()

    # ---- GitHub ------------------------------------------------------------------------------
    def _open(self, url: str, timeout: float = 20.0, accept: str = "application/vnd.github+json"):
        req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "QEL-Hub"})
        if self.token and url.startswith(self.api):
            req.add_header("Authorization", f"Bearer {self.token}")
        return urllib.request.urlopen(req, timeout=timeout)

    def _releases_raw(self, force: bool = False) -> List[Dict[str, Any]]:
        if not force and self._cache and time.time() - self._cache[0] < CACHE_S:
            return self._cache[1]
        try:
            with self._open(f"{self.api}/repos/{self.repo}/releases?per_page=30") as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise GithubError(f"GitHub 回應 {e.code}（{e.reason}）") from None
        except (urllib.error.URLError, OSError) as e:
            raise GithubError(f"連不到 GitHub：{getattr(e, 'reason', e)}（NAS 要能上網）") from None
        if not isinstance(data, list):
            raise GithubError("GitHub 回傳的格式不對")
        self._cache = (time.time(), data)
        return data

    def latest(self, force: bool = False) -> List[Dict[str, Any]]:
        """每個程式最新的一版（labcontrol-v*、lablogviewer-v*），每個檔案標出線上有沒有。"""
        out = []
        raw = [r for r in self._releases_raw(force) if not r.get("draft") and not r.get("prerelease")]
        staged = {(it.get("file"), it.get("version")) for it in self.console.items() if not it.get("done")}
        for prefix in PREFIXES:
            rels = [r for r in raw if str(r.get("tag_name", "")).startswith(prefix)]
            if not rels:
                continue
            rel = max(rels, key=lambda r: _vkey(r["tag_name"][len(prefix):]))
            files = []
            for a in rel.get("assets") or []:
                g = guess(a.get("name", ""))
                if g is None:
                    continue
                item = self.console.decorate([dict(g)])[0]
                files.append({"name": a["name"], "size": a.get("size", 0), "url": a.get("browser_download_url", ""),
                              "target": g["target"], "version": g["version"], "platform": g.get("platform"),
                              "online": bool(item.get("online")), "older": bool(item.get("older")),
                              "current": item.get("current"), "staged": (a["name"], g["version"]) in staged})
            out.append({"tag": rel["tag_name"], "name": rel.get("name") or rel["tag_name"],
                        "date": rel.get("published_at"), "url": rel.get("html_url"), "files": files})
        return out

    def wanted(self, releases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """要下載的：線上還沒有、不是比線上舊、還沒在暫存裡的。"""
        return [f for r in releases for f in r["files"] if not f["online"] and not f["older"] and not f["staged"]]

    def view(self, force: bool = False) -> Dict[str, Any]:
        cfg = self.config()
        res: Dict[str, Any] = {"repo": self.repo, "auto": bool(cfg.get("auto")), "last_auto": cfg.get("last_auto"),
                               "last_auto_result": cfg.get("last_auto_result"), "job": self.job_view()}
        try:
            rel = self.latest(force)
            res.update(ok=True, releases=rel, wanted=[f["name"] for f in self.wanted(rel)])
        except GithubError as e:
            res.update(ok=True, error=str(e), releases=[], wanted=[])
        return res

    # ---- 下載 --------------------------------------------------------------------------------
    def job_view(self) -> Optional[Dict[str, Any]]:
        with self.lock:
            return json.loads(json.dumps(self.job)) if self.job else None

    def fetch(self, by: str, publish: bool = False, names: Optional[List[str]] = None,
              wait: bool = False) -> Dict[str, Any]:
        with self.lock:
            if self.job and not self.job.get("done"):
                return json.loads(json.dumps(self.job))
        files = self.wanted(self.latest(force=True))
        if names:
            files = [f for f in files if f["name"] in names]
        job = {"id": secrets.token_hex(4), "by": by, "publish": bool(publish), "started": time.time(),
               "done": not files, "files": [{"name": f["name"], "size": f["size"], "got": 0, "status": "等待",
                                            "item": None} for f in files], "message": "" if files else "線上都是最新版"}
        with self.lock:
            self.job = job
        if files:
            th = threading.Thread(target=self._run, args=(job, files), name="github-fetch", daemon=True)
            th.start()
            if wait:
                th.join()
        return self.job_view() or job

    def _set(self, job: Dict[str, Any], i: int, **kw: Any) -> None:
        with self.lock:
            job["files"][i].update(kw)

    def _download(self, url: str, dst: Path, progress: Callable[[int], None]) -> None:
        tmp = dst.with_name(dst.name + ".part")
        with self._open(url, timeout=60, accept="application/octet-stream") as r, open(tmp, "wb") as f:
            got = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                progress(got)
        os.replace(tmp, dst)

    def _run(self, job: Dict[str, Any], files: List[Dict[str, Any]]) -> None:
        tmpdir = Path(self.console.dir) / "_github"
        tmpdir.mkdir(parents=True, exist_ok=True)
        staged: List[Dict[str, Any]] = []
        for i, f in enumerate(files):
            self._set(job, i, status="下載中")
            dst = tmpdir / f["name"]
            try:
                self._download(f["url"], dst, lambda got, i=i: self._set(job, i, got=got))
                if f["size"] and dst.stat().st_size != f["size"]:
                    raise GithubError(f"下載不完整（{dst.stat().st_size:,} / {f['size']:,} bytes）")
                it = self.console.stage_path(dst, f["name"], f"{job['by']}（GitHub）")
                staged.append(it)
                self._set(job, i, status="已下載", item=it["id"], got=f["size"])
            except Exception as e:  # noqa: BLE001 - 一個檔失敗不影響其他的
                self._set(job, i, status=f"❌ {e}")
                try:
                    dst.unlink()
                except OSError:
                    pass
        if job["publish"]:
            # Hub 最後才更新（它會重新啟動）
            for it in sorted(staged, key=lambda x: x.get("target") == "hub"):
                idx = next(k for k, x in enumerate(job["files"]) if x.get("item") == it["id"])
                self._set(job, idx, status="發佈中")
                try:
                    r = self.console.publish(it["id"], f"{job['by']}（GitHub）")
                    self._set(job, idx, status="更新中（控制代理）" if r.get("job") else "✔ 已發佈")
                except Exception as e:  # noqa: BLE001
                    self._set(job, idx, status=f"❌ 發佈失敗：{e}")
        with self.lock:
            job["done"] = True
            job["finished"] = time.time()
            ok = sum(1 for x in job["files"] if not str(x["status"]).startswith("❌"))
            job["message"] = f"{ok} / {len(job['files'])} 個檔案完成"

    # ---- 自動 --------------------------------------------------------------------------------
    def _start_auto(self) -> None:
        if self._auto_thread is not None and self._auto_thread.is_alive():
            return
        self._auto_thread = threading.Thread(target=self._auto_loop, name="github-auto", daemon=True)
        self._auto_thread.start()

    def auto_once(self) -> Dict[str, Any]:
        try:
            job = self.fetch(self.config().get("auto_by") or "自動", publish=True, wait=True)
            result = job.get("message") or ""
        except GithubError as e:
            result = f"❌ {e}"
        self._save_cfg(last_auto=time.time(), last_auto_result=result)
        return {"result": result}

    def _auto_loop(self) -> None:
        time.sleep(60)
        while self.config().get("auto"):
            self.auto_once()
            for _ in range(int(AUTO_EVERY_S / 5)):
                if not self.config().get("auto"):
                    return
                time.sleep(5)
