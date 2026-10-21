"""站長控制台（平台網頁「控制台」，0.0.18）：所有程式的更新只要在網頁上傳檔案。

流程：

1. 上傳（``POST /api/platform/console/stage?filename=``）→ 存到 ``data/staging/<id>``，用 :mod:`labhub.packages`
   判斷是哪個模塊、哪一版，回傳線上目前的版本；
2. 發佈（``POST /api/platform/console/publish/<id>``）：
   - 模塊（量測、讀檔、Monitor、啟動器、.exe / .dmg）→ 直接加到平台的模塊發佈（啟動器自動更新）；
   - Hub、論文庫 → 交給 NAS 控制代理（同一個 Docker 專案的 ``labcontrol-hub-agent``）做更新，回傳工作編號；
3. 工作進度（``GET /api/platform/console/jobs/<工作>``）→ 轉問控制代理。Hub 自己更新時會重新啟動一下，
   網頁會等它回來再繼續問。

Hub 用自己的裝置 token 呼叫控制代理（兩者是同一個 ``LABHUB_TOKEN``），站長不需要知道它。
"""
from __future__ import annotations

import json
import secrets
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .packages import PackageError, identify
from .platform import PlatformError

ORDER = {"paperlib": 0, "hub": 9}            # 其他模塊 = 5；安裝檔（.exe / .dmg）排在同一版的 zip 後面
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
KEEP_S = 24 * 3600                          # 上傳後沒發佈的檔，一天後刪掉


class ConsoleError(PlatformError):
    """控制台錯誤（Hub 會轉成 HTTP 錯誤碼與訊息）。"""


class AgentLink:
    """Hub → 控制代理（標準函式庫）。"""

    def __init__(self, url: str, token: Optional[str]) -> None:
        self.url = (url or "").rstrip("/")
        self.token = token or ""

    def _req(self, method: str, path: str, data: Optional[bytes] = None, timeout: float = 15) -> Any:
        if not self.url:
            raise ConsoleError(503, "沒有設定控制代理網址（LABHUB_AGENT_URL）")
        req = urllib.request.Request(self.url + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/zip")
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with _OPENER.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode("utf-8")).get("error") or str(e)
            except Exception:  # noqa: BLE001
                msg = str(e)
            raise ConsoleError(502, f"控制代理回應 {e.code}：{msg}") from None
        except (urllib.error.URLError, OSError) as e:
            raise ConsoleError(503, f"連不到控制代理 {self.url}：{getattr(e, 'reason', e)}") from None

    def status(self, timeout: float = 10) -> Dict[str, Any]:
        return self._req("GET", "/api/agent", timeout=timeout)

    def submit(self, action: str, data: bytes, by: str) -> Dict[str, Any]:
        q = urllib.parse.urlencode({"action": action, "by": by})
        return self._req("POST", f"/api/agent/jobs?{q}", data=data, timeout=600)

    def job(self, jid: str, after: int = 0, wait: float = 10) -> Dict[str, Any]:
        return self._req("GET", f"/api/agent/jobs/{jid}?after={after}&wait={wait:g}", timeout=wait + 15)


class Console:
    def __init__(self, hub: Any) -> None:
        self.hub = hub
        self.dir = Path(hub.cfg.data_dir) / "staging"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.agent = AgentLink(getattr(hub.cfg, "agent_url", ""), hub.cfg.token)
        try:                                         # 1.0.13：自動從 GitHub 更新 → Hub 啟動時就開始
            if json.loads((self.dir.parent / "github_sync.json").read_text(encoding="utf-8")).get("auto"):
                _ = self.github
        except (OSError, ValueError):
            pass

    # ---- 上傳暫存 ------------------------------------------------------------
    def _meta(self, sid: str) -> Path:
        return self.dir / sid / "item.json"

    def _cleanup(self) -> None:
        now = time.time()
        for d in self.dir.iterdir():
            if d.name.startswith("_"):              # _github：下載中的檔案
                continue
            try:
                if d.is_dir() and now - d.stat().st_mtime > KEEP_S:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass

    def stage(self, filename: str, data: bytes, by: str, version: str = "") -> Dict[str, Any]:
        name = Path(filename or "").name
        if not name or "/" in name or "\\" in name or len(name) > 120:
            raise ConsoleError(400, "檔名不合法")
        try:
            info = identify(data, name, version=(version or "").strip())
        except PackageError as e:
            raise ConsoleError(400, str(e)) from None
        self._cleanup()
        sid = secrets.token_hex(6)
        d = self.dir / sid
        d.mkdir(parents=True)
        (d / name).write_bytes(data)
        import hashlib

        item = dict(info, id=sid, file=name, size=len(data), sha256=hashlib.sha256(data).hexdigest(), by=by,
                    time=time.time(), status="待發佈", done=False)
        self._meta(sid).write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
        return self.decorate([item])[0]

    def stage_path(self, path: Path, filename: str, by: str, version: str = "") -> Dict[str, Any]:
        """1.0.13：已經在 NAS 上的檔案（從 GitHub 下載的）放進暫存——不整個讀進記憶體（安裝檔可能幾百 MB）。"""
        import hashlib

        path = Path(path)
        name = Path(filename or path.name).name
        try:
            info = identify(path, name, version=(version or "").strip())
        except PackageError as e:
            raise ConsoleError(400, str(e)) from None
        self._cleanup()
        sid = secrets.token_hex(6)
        d = self.dir / sid
        d.mkdir(parents=True)
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        size = path.stat().st_size
        shutil.move(str(path), d / name)
        item = dict(info, id=sid, file=name, size=size, sha256=h.hexdigest(), by=by,
                    time=time.time(), status="待發佈", done=False)
        self._meta(sid).write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
        return self.decorate([item])[0]

    @property
    def github(self):
        if getattr(self, "_github", None) is None:
            from .github_sync import GithubSync

            self._github = GithubSync(self)
        return self._github

    def items(self) -> List[Dict[str, Any]]:
        out = []
        for f in self.dir.glob("*/item.json"):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        from .server import parse_version

        # 論文庫 → 模塊（同一個模塊：完整 zip 先、更新包後，版本小的先）→ 安裝檔 → Hub
        for it in out:                                  # 1.0.11：頁面重新整理後也看得到更新進度 / 結果
            if it.get("job") and not it.get("done") and not it.get("failed"):
                self._refresh(it)
        out.sort(key=lambda x: (x.get("done", False), ORDER.get(x["target"], 5), 1 if x.get("platform") else 0,
                                1 if x.get("partial") else 0, parse_version(x.get("version") or "0"),
                                x.get("time", 0)))
        return self.decorate(out)

    def get(self, sid: str) -> Dict[str, Any]:
        try:
            return json.loads(self._meta(sid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ConsoleError(404, "沒有這個上傳的檔案（可能已超過一天被清掉，請重新上傳）") from None

    def _save(self, item: Dict[str, Any]) -> None:
        self._meta(item["id"]).write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")

    def discard(self, sid: str) -> None:
        shutil.rmtree(self.dir / sid, ignore_errors=True)

    def clear_done(self) -> None:
        for it in self.items():
            if it.get("done"):
                self.discard(it["id"])

    # ---- 線上版本 ------------------------------------------------------------
    def current(self) -> Dict[str, Optional[str]]:
        if time.time() - getattr(self, "_cur_at", 0) < 5:
            return dict(self._cur)
        cur: Dict[str, Optional[str]] = {"hub": __version__}
        for mid, m in self.hub.platform.modules().items():
            if m.get("latest"):
                cur[mid] = m["latest"]
        pl = (self.hub.platform.status().get("paperlib") or {})
        cur["paperlib"] = pl.get("version")
        if not cur["paperlib"]:
            try:
                cur["paperlib"] = (self.agent.status(timeout=4).get("paperlib") or {}).get("version")
            except ConsoleError:
                pass
        self._cur, self._cur_at = cur, time.time()
        return dict(cur)

    def decorate(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """每一項加上線上目前的版本，與「已經在線上」的判斷。"""
        cur = self.current() if items else {}
        files = {}
        for it in items:
            c = cur.get(it["target"])
            it["current"] = c
            if it.get("platform"):
                if it["target"] not in files:
                    files[it["target"]] = {f"{v['version']}/{f['name']}" for v in
                                           (self.hub.platform.modules().get(it["target"]) or {}).get("versions", [])
                                           for f in v["files"]}
                it["online"] = f"{it['version']}/{it['file']}" in files[it["target"]]
            else:
                it["online"] = c == it["version"]
            it["older"] = bool(c and not it.get("platform") and _older(it["version"], c))
        return items

    # ---- 發佈 ---------------------------------------------------------------
    def publish(self, sid: str, by: str, notes: Optional[str] = None) -> Dict[str, Any]:
        with self.lock:
            it = self.get(sid)
            if notes is not None:
                it["notes"] = notes
            path = self.dir / sid / it["file"]
            if not path.is_file():
                raise ConsoleError(404, "上傳的檔案不見了，請重新上傳")
            if it["kind"] == "web":
                data = path.read_bytes()
                action = "update" if it["target"] == "hub" else "paperlib_update"
                job = self.agent.submit(action, data, by=f"{by}（控制台）")
                it.update(status="更新中…", job=job.get("id"))
                self._save(it)
                self.hub.platform.audit(by, "console", f"{it['target']} {it['version']} → 控制代理 {job.get('id')}")
                return dict(it, job_view=job)
            if it.get("partial"):
                merged = self._merge_patch(it, path)
                self.hub.platform.add_release(it["target"], it["version"], merged.name, merged, by, it.get("notes", ""))
            else:
                # 1.0.13：大檔（.exe / .dmg）不整個讀進記憶體，直接把暫存的檔案搬過去
                self.hub.platform.add_release(it["target"], it["version"], it["file"], path, by, it.get("notes", ""))
            self._cur_at = 0
            msg = ("已發佈：平台網頁的下載按鈕會給這個檔" if it.get("platform") else
                   "已發佈：各電腦的 App 會自動更新")
            it.update(status="✔ " + msg, done=True)
            self._save(it)
            return it

    def _merge_patch(self, it: Dict[str, Any], patch: Path) -> Path:
        """更新包 + 平台上 base 版的完整 zip → 新版完整 zip（更新包的檔案覆蓋同路徑的檔案）。"""
        import zipfile

        base = self.hub.platform.base_zip(it["target"], it["base"])

        def top(names: List[str]) -> str:
            tops = {n.split("/", 1)[0] for n in names if n and not n.startswith("__MACOSX/")}
            return (tops.pop() + "/") if len(tops) == 1 and all("/" in n for n in names if n) else ""

        out = patch.with_name(f"merged_{it['id']}.zip")
        with zipfile.ZipFile(patch) as zp, zipfile.ZipFile(base) as zb, \
                zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
            pnames = [n for n in zp.namelist() if not n.endswith("/") and not n.startswith("__MACOSX/")]
            ptop, btop = top(pnames), top(zb.namelist())
            new = {}
            for n in pnames:
                rel = n[len(ptop):] if ptop and n.startswith(ptop) else n
                if rel == "qel-module.json":
                    continue
                if ".." in rel.split("/") or rel.startswith("/"):
                    raise ConsoleError(400, f"更新包裡有不安全的路徑：{n}")
                new[rel] = n
            for info in zb.infolist():
                rel = info.filename[len(btop):] if btop and info.filename.startswith(btop) else info.filename
                if rel in new or rel == "qel-module.json":
                    continue
                zo.writestr(info, zb.read(info))
            for rel, n in new.items():
                zo.writestr(btop + rel, zp.read(n))
        stem = Path(it["file"]).stem.replace("_patch", "").replace("-patch", "")
        final = out.with_name(f"{stem}.zip")
        out.replace(final)
        return final

    def _refresh(self, it: Dict[str, Any]) -> None:
        """還沒結束的更新工作：問控制代理現在做到哪一步（不等待）；結束了就記下結果。"""
        try:
            v = self.agent.job(it["job"], 0, 0)
        except ConsoleError as e:
            if "回應 404" in str(e):                      # 控制代理重新啟動過：工作紀錄不見了
                it.update(status="❓ 查不到這次更新的結果（控制代理重新啟動過）；請看「線上」版本", failed=True)
                self._save(it)
            return
        except Exception:  # noqa: BLE001 — 控制代理暫時連不到：照舊
            return
        if v.get("done"):
            ok = bool(v.get("ok"))
            it.update(status=("✔ " if ok else "❌ ") + (v.get("result") or ("完成" if ok else "失敗")), done=ok,
                      failed=not ok)
            self._save(it)
            return
        cur = next((s for s in v.get("steps") or [] if s.get("status") == "running"), None)
        if cur:
            el = time.time() - float(cur.get("t0") or time.time())
            it["status"] = f"更新中：{cur.get('title')}…（{int(el // 60)} 分 {int(el % 60)} 秒）" + \
                (f" {cur.get('msg')}" if cur.get("msg") else "")
        it["running"] = True

    def job(self, sid: str, after: int = 0, wait: float = 10) -> Dict[str, Any]:
        """控制代理的工作進度；結束時把結果記到上傳項目。"""
        it = self.get(sid)
        if not it.get("job"):
            raise ConsoleError(400, "這個檔案沒有更新工作")
        v = self.agent.job(it["job"], after, wait)
        if v.get("done") and not it.get("done"):
            ok = bool(v.get("ok"))
            it.update(status=("✔ " if ok else "❌ ") + (v.get("result") or ("完成" if ok else "失敗")), done=ok,
                      failed=not ok)
            self._save(it)
        return dict(v, item=it)

    # ---- 狀態 ---------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"hub": {"version": __version__}, "time": time.time()}
        try:
            a = self.agent.status()
            out["agent"] = {"online": True, "version": a.get("agent_version"), "hub_container": a.get("container"),
                            "paperlib": a.get("paperlib"), "backups": len(a.get("backups") or []),
                            "job": a.get("job"), "indexer": a.get("indexer")}
        except ConsoleError as e:
            out["agent"] = {"online": False, "error": str(e)}
        try:
            du = shutil.disk_usage(self.hub.cfg.data_dir)
            out["disk"] = {"total": du.total, "used": du.used, "free": du.free}
        except OSError:
            out["disk"] = None
        out["platform"] = self.hub.platform.status()
        try:                                              # 1.0.4 數據索引
            from .index_routes import index_summary

            out["index"] = index_summary(self.hub.cfg.data_dir)
        except Exception as e:  # noqa: BLE001
            out["index"] = {"available": False, "message": f"{type(e).__name__}: {e}"}
        snap = self.hub.snapshot()
        out["nodes"] = [{k: n.get(k) for k in ("name", "online", "state", "user", "host", "version", "run",
                                               "simulate", "last_seen")} for n in snap.get("nodes", [])]
        out["devices"] = [{k: d.get(k) for k in ("id", "online", "version", "role", "node")}
                          for d in snap.get("devices", [])]
        return out


def _older(a: str, b: str) -> bool:
    from .server import parse_version

    return parse_version(a) < parse_version(b)
