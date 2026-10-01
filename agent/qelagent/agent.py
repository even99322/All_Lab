"""更新代理的核心：服務設定、工作（每一步即時回報）、更新 / 備份 / 回復。

每個服務在 NAS 上一個資料夾（``dir``），程式碼在其中的 ``code`` 子資料夾，用 Docker 掛載進容器。
更新＝換掉 ``code`` 資料夾的內容後重建容器，所以不需要重新 build 映像；資料夾（data/ 等）完全不動。

動作：
  update        上傳新版 zip → 檢查 → 停止 → 備份 → 換程式 → 重建 → 啟動 → 確認版本（失敗自動換回舊版）
  rollback      回到某個備份
  rebuild       停止 → 重建 → 啟動 → 確認
  full_rebuild  停止 → 重新下載基礎映像 → 重建 → 啟動 → 確認（只適用直接用官方映像的服務）
  restart / stop / start
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import __version__
from .docker import Docker, DockerError

log = logging.getLogger("qelagent")
ACTIONS = {"update": "更新", "rollback": "回到備份", "rebuild": "重建", "full_rebuild": "完全重建",
           "restart": "重新啟動", "stop": "停止", "start": "啟動"}
STEPS = {
    "update": [("check", "檢查 zip"), ("stop", "停止"), ("backup", "備份"), ("replace", "換程式"),
               ("rebuild", "重建"), ("start", "啟動"), ("verify", "確認版本")],
    "rollback": [("check", "檢查備份"), ("stop", "停止"), ("backup", "備份目前版本"), ("replace", "換回備份"),
                 ("rebuild", "重建"), ("start", "啟動"), ("verify", "確認版本")],
    "rebuild": [("stop", "停止"), ("rebuild", "重建"), ("start", "啟動"), ("verify", "確認")],
    "full_rebuild": [("stop", "停止"), ("pull", "下載基礎映像"), ("rebuild", "重建"), ("start", "啟動"), ("verify", "確認")],
    "restart": [("restart", "重新啟動"), ("verify", "確認")],
    "stop": [("stop", "停止")],
    "start": [("start", "啟動"), ("verify", "確認")],
}
SELF_STEPS = {"update": [("check", "檢查 zip"), ("backup", "備份"), ("replace", "換程式"), ("restart", "重新啟動代理")],
              "rollback": [("check", "檢查備份"), ("backup", "備份目前版本"), ("replace", "換回備份"),
                           ("restart", "重新啟動代理")]}
VERSION_RE = r"""__version__\s*=\s*["']([^"']+)"""


@dataclass
class Service:
    id: str
    name: str
    container: str
    dir: Path                      # NAS 上的服務資料夾（掛載進代理）
    code: str                      # dir 裡的程式資料夾名稱（例如 qelportal、app、labhub）
    health: str = ""               # 確認用網址（回 JSON，含 version）
    version_file: str = ""         # 相對於 dir；空白＝code/__init__.py
    version_regex: str = VERSION_RE
    marker: str = "__init__.py"    # zip 裡用來認出程式資料夾的檔案
    extra: List[str] = field(default_factory=list)   # 一起更新的檔案（相對於 dir），例如 requirements.txt
    is_self: bool = False
    pull_ok: bool = True           # 容器用官方映像（可以「完全重建」）

    @property
    def code_dir(self) -> Path:
        return self.dir / self.code

    def version_path(self, root: Optional[Path] = None) -> Path:
        base = root if root is not None else self.dir
        return base / (self.version_file or f"{self.code}/__init__.py")

    def view(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "container": self.container, "dir": str(self.dir),
                "code": self.code, "health": self.health, "self": self.is_self}


def default_services(stack: Path) -> List[Service]:
    return [
        Service("portal", "大程式網站", "qel-portal", stack / "portal", "qelportal",
                "http://qel-portal:8090/api/v1/ping"),
        Service("paperlib", "論文庫", "paperlib", stack / "paperlib", "app", "http://paperlib:8080/api/site",
                version_file="app/config.py", version_regex=r"""VERSION\s*=\s*["']([^"']+)""",
                extra=["requirements.txt", "seed/manifest.json"], pull_ok=False),
        Service("labhub", "量測中繼站（Lab Control Hub）", "labcontrol-hub", stack / "labhub", "labhub",
                "http://labcontrol-hub:8765/api/ping", marker="server.py"),
        Service("agent", "更新代理", "qel-agent", stack / "agent", "qelagent", "", is_self=True),
    ]


def load_services(stack: Path, path: Optional[Path]) -> List[Service]:
    """QEL_AGENT_SERVICES（JSON 檔）可覆寫或新增服務；沒有就用預設值。"""
    services = {s.id: s for s in default_services(stack)}
    if path and path.exists():
        for d in json.loads(path.read_text(encoding="utf-8")):
            base = services.get(d["id"])
            kw = dict(base.__dict__) if base else {}
            kw.update(d)
            p = Path(str(kw.get("dir") or d["id"]))
            kw["dir"] = p if p.is_absolute() else stack / p
            services[d["id"]] = Service(**kw)
    return list(services.values())


def read_version(svc: Service, root: Optional[Path] = None) -> Optional[str]:
    base = root if root is not None else svc.dir
    man = base / "module.json"
    try:
        d = json.loads(man.read_text(encoding="utf-8"))
        if d.get("version") and d.get("id") in (svc.id, None):
            return str(d["version"])
    except (OSError, ValueError):
        pass
    try:
        m = re.search(svc.version_regex, svc.version_path(base).read_text(encoding="utf-8"))
        return m.group(1) if m else None
    except OSError:
        return None


def parse_version(v: str) -> Tuple:
    return tuple((0, int(p)) if p.isdigit() else (-1, p) for p in re.findall(r"\d+|[a-z]+", str(v).lower()))


# ---- 工作 ----------------------------------------------------------------------
class Job:
    def __init__(self, service: str, action: str, steps: List[Tuple[str, str]], by: str = "") -> None:
        self.id = uuid.uuid4().hex[:10]
        self.service, self.action, self.by = service, action, by
        self.title = ACTIONS.get(action, action)
        self.steps = [{"key": k, "title": t, "status": "pending", "msg": "", "t0": None, "t1": None} for k, t in steps]
        self.lines: List[Dict[str, Any]] = []
        self.done, self.ok = False, False
        self.started, self.finished = time.time(), None
        self.result = ""
        self.cond = threading.Condition()

    def _step(self, key: str) -> Dict[str, Any]:
        for s in self.steps:
            if s["key"] == key:
                return s
        s = {"key": key, "title": key, "status": "pending", "msg": "", "t0": None, "t1": None}
        self.steps.append(s)
        return s

    def add_steps(self, steps: List[Tuple[str, str]]) -> None:
        with self.cond:
            for k, t in steps:
                self.steps.append({"key": k, "title": t, "status": "pending", "msg": "", "t0": None, "t1": None})

    def begin(self, key: str, msg: str = "") -> None:
        with self.cond:
            s = self._step(key)
            s.update(status="running", t0=time.time(), msg=msg)
            self._line(f"▶ {s['title']}" + (f"：{msg}" if msg else ""))

    def end(self, key: str, msg: str = "", status: str = "ok") -> None:
        with self.cond:
            s = self._step(key)
            s.update(status=status, t1=time.time(), msg=msg or s["msg"])
            icon = {"ok": "✔", "fail": "✖", "skip": "–", "warn": "⚠"}.get(status, "•")
            self._line(f"{icon} {s['title']}" + (f"：{msg}" if msg else ""))

    def say(self, msg: str) -> None:
        with self.cond:
            self._line(msg)

    def _line(self, msg: str) -> None:
        self.lines.append({"i": len(self.lines), "time": time.time(), "msg": msg})
        log.info("[%s %s] %s", self.service, self.id, msg)
        self.cond.notify_all()

    def finish(self, ok: bool, result: str) -> None:
        with self.cond:
            for s in self.steps:
                if s["status"] == "pending":
                    s["status"] = "skip"
                elif s["status"] == "running":
                    s["status"] = "fail" if not ok else "ok"
            self.done, self.ok, self.result, self.finished = True, ok, result, time.time()
            self._line(("✅ " if ok else "❌ ") + result)

    def wait(self, after: int, timeout: float) -> None:
        with self.cond:
            if len(self.lines) <= after and not self.done:
                self.cond.wait(timeout)

    def view(self, after: int = 0) -> Dict[str, Any]:
        with self.cond:
            return {"id": self.id, "service": self.service, "action": self.action, "title": self.title, "by": self.by,
                    "steps": [dict(s) for s in self.steps], "lines": self.lines[after:], "n_lines": len(self.lines),
                    "done": self.done, "ok": self.ok, "result": self.result, "started": self.started,
                    "finished": self.finished}


class AgentError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---- 代理 ----------------------------------------------------------------------
class Agent:
    def __init__(self, services: List[Service], docker: Docker, backups: Path, verify_s: float = 90.0,
                 exit_fn: Callable[[], None] = lambda: os._exit(0)) -> None:
        self.services = {s.id: s for s in services}
        self.docker = docker
        self.backups_root = backups
        self.verify_s = verify_s
        self.jobs: Dict[str, Job] = {}
        self.current: Dict[str, Job] = {}
        self.lock = threading.Lock()
        self.exit_fn = exit_fn

    def svc(self, sid: str) -> Service:
        s = self.services.get(sid)
        if s is None:
            raise AgentError(404, f"沒有服務 {sid}")
        return s

    # ---- 狀態 ------------------------------------------------------------
    def health(self, svc: Service, timeout: float = 3.0) -> Optional[Dict[str, Any]]:
        if not svc.health:
            return None
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(svc.health, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8") or "{}")
                return d if isinstance(d, dict) else {}
        except Exception:  # noqa: BLE001
            return None

    def container(self, svc: Service) -> Dict[str, Any]:
        c: Dict[str, Any] = {"name": svc.container}
        try:
            info = self.docker.inspect(svc.container)
            if info is None:
                c["status"] = "missing"
            else:
                st = info.get("State") or {}
                c.update(status=st.get("Status"), running=st.get("Running"), started=st.get("StartedAt"),
                         image=(info.get("Config") or {}).get("Image"), id=info.get("Id", "")[:12],
                         restarts=info.get("RestartCount"), health=(st.get("Health") or {}).get("Status"))
        except DockerError as e:
            c.update(status="unknown", error=str(e))
        return c

    def backups(self, svc: Service) -> List[Dict[str, Any]]:
        d = self.backups_root / svc.id
        if not d.exists():
            return []
        out = []
        for p in sorted(d.glob(f"{svc.id}_v*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
            m = re.match(rf"{re.escape(svc.id)}_v(.+?)_(\d{{8}}-\d{{6}})\.zip$", p.name)
            out.append({"name": p.name, "version": m.group(1) if m else "?", "size": p.stat().st_size,
                        "time": p.stat().st_mtime})
        return out

    def status(self, sid: Optional[str] = None) -> Dict[str, Any]:
        out = []
        for s in self.services.values():
            if sid and s.id != sid:
                continue
            h = None if s.is_self else self.health(s)
            cur = self.current.get(s.id)
            out.append(dict(s.view(), container=self.container(s), online=True if s.is_self else h is not None,
                            running_version=__version__ if s.is_self else (h or {}).get("version"),
                            code_version=read_version(s), backups=self.backups(s)[:10],
                            job=cur.view(len(cur.lines)) if cur is not None and not cur.done else None))
        return {"agent_version": __version__, "time": time.time(), "services": out}

    def logs(self, sid: str, tail: int = 200) -> str:
        return self.docker.logs(self.svc(sid).container, tail)

    # ---- 啟動工作 --------------------------------------------------------
    def submit(self, sid: str, action: str, by: str = "", data: Optional[bytes] = None, backup: str = "") -> Job:
        svc = self.svc(sid)
        steps = (SELF_STEPS if svc.is_self else STEPS).get(action)
        if steps is None:
            raise AgentError(400, f"{svc.name} 不支援「{ACTIONS.get(action, action)}」")
        if action == "full_rebuild" and not svc.pull_ok:
            raise AgentError(400, f"{svc.name} 是自己 build 的映像，請在 Container Manager 重建")
        with self.lock:
            busy = [j for j in self.current.values() if not j.done]
            if busy:
                raise AgentError(409, f"正在執行「{self.services[busy[0].service].name}：{busy[0].title}」，請等它完成")
            job = Job(sid, action, steps, by)
            self.jobs[job.id] = job
            self.current[sid] = job
            if len(self.jobs) > 200:
                for k in list(self.jobs)[:100]:
                    if self.jobs[k].done:
                        self.jobs.pop(k)
        threading.Thread(target=self._run, args=(svc, job, data, backup), daemon=True, name=f"job-{job.id}").start()
        return job

    def job(self, jid: str) -> Job:
        j = self.jobs.get(jid)
        if j is None:
            raise AgentError(404, "沒有這個工作")
        return j

    def _run(self, svc: Service, job: Job, data: Optional[bytes], backup: str) -> None:
        restart_self = False
        try:
            fn = getattr(self, f"_do_{job.action}")
            ok, msg = fn(svc, job, data, backup)
            restart_self = ok and svc.is_self and job.action in ("update", "rollback")
            job.finish(ok, msg)
        except (DockerError, ValueError, AgentError, OSError) as e:
            job.finish(False, str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("工作失敗")
            job.finish(False, f"{type(e).__name__}: {e}")
        if restart_self:
            threading.Timer(3.0, self.exit_fn).start()

    # ---- 步驟 ------------------------------------------------------------
    def _info(self, svc: Service) -> Dict[str, Any]:
        info = self.docker.inspect(svc.container)
        if info is None:
            raise DockerError(f"找不到容器 {svc.container}（先在 Container Manager 建立專案）")
        return info

    def _stop(self, svc: Service, job: Job, key: str = "stop") -> None:
        job.begin(key, svc.container)
        info = self._info(svc)
        if (info.get("State") or {}).get("Running"):
            self.docker.stop(info["Id"])
            job.end(key, "已停止")
        else:
            job.end(key, "原本就沒有在執行")

    def _rebuild(self, svc: Service, job: Job, key: str = "rebuild") -> None:
        job.begin(key, "以原本的設定重新建立容器（掛載的新程式會生效）")
        info = self._info(svc)
        if (info.get("State") or {}).get("Running"):
            self.docker.stop(info["Id"])
        new = self.docker.recreate(svc.container, info)
        job.end(key, f"新容器 {new[:12]}")

    def _start(self, svc: Service, job: Job, key: str = "start") -> None:
        job.begin(key)
        info = self._info(svc)
        if not (info.get("State") or {}).get("Running"):
            self.docker.start(info["Id"])
        job.end(key, "已啟動")

    def _verify(self, svc: Service, job: Job, want: Optional[str], key: str = "verify") -> bool:
        if not svc.health:
            job.begin(key)
            job.end(key, "沒有設定確認網址，略過", "skip")
            return True
        job.begin(key, f"等待 {svc.name} 回應" + (f"（應為 v{want}）" if want else ""))
        t0 = time.time()
        last = None
        while time.time() - t0 < self.verify_s:
            p = self.health(svc)
            if p is not None:
                last = p.get("version")
                if want is None or last is None or str(last) == str(want):
                    job.end(key, f"v{last} 已上線（{time.time() - t0:.0f} s）" if last else f"已上線（{time.time() - t0:.0f} s）")
                    return True
            time.sleep(1.0)
        job.end(key, f"{self.verify_s:.0f} 秒內沒有" + (f"等到 v{want}（目前 {last}）" if last else "回應"), "fail")
        return False

    def _backup(self, svc: Service, job: Job, key: str = "backup") -> Path:
        job.begin(key)
        v = read_version(svc) or "unknown"
        d = self.backups_root / svc.id
        d.mkdir(parents=True, exist_ok=True)
        dst = d / f"{svc.id}_v{v}_{_dt.datetime.now():%Y%m%d-%H%M%S}.zip"
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(svc.code_dir.rglob("*")):
                rel = p.relative_to(svc.dir)
                if p.is_file() and "__pycache__" not in rel.parts:
                    z.write(p, rel.as_posix())
            for name in ["module.json", *svc.extra]:
                p = svc.dir / name
                if p.is_file():
                    z.write(p, name)
        for old in self.backups(svc)[20:]:               # 保留最近 20 份
            try:
                (d / old["name"]).unlink()
            except OSError:
                pass
        job.end(key, dst.name)
        return dst

    def extract(self, svc: Service, data: bytes, into: Path) -> Tuple[Path, Optional[str]]:
        """從 zip 找出程式資料夾（可在任一層）並檢查語法。回傳（程式資料夾的上一層＝新的服務根目錄, 版本）。"""
        into.mkdir(parents=True, exist_ok=True)
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise ValueError("不是 zip 檔") from None
        root = into.resolve()
        with z:
            for m in z.infolist():
                t = (root / m.filename).resolve()
                if root not in t.parents and t != root:
                    raise ValueError(f"zip 內容不安全：{m.filename}")
            z.extractall(root)
        cands = sorted((p.parent for p in root.rglob(svc.marker) if p.parent.name == svc.code
                        and "__MACOSX" not in p.parts), key=lambda p: len(p.parts))
        if not cands:
            raise ValueError(f"zip 裡找不到 {svc.code}/ 資料夾（{svc.name} 的程式）")
        pkg = cands[0]
        for f in pkg.rglob("*.py"):
            compile(f.read_text(encoding="utf-8"), str(f), "exec")
        base = pkg.parent
        man = base / "module.json"
        if man.exists():
            try:
                mid = json.loads(man.read_text(encoding="utf-8")).get("id")
            except ValueError:
                raise ValueError("module.json 格式錯誤") from None
            if mid and mid != svc.id:
                raise ValueError(f"這個 zip 是 {mid} 的，不是 {svc.id}")
        return base, read_version(svc, base)

    def _replace(self, svc: Service, job: Job, base: Path, key: str = "replace") -> List[str]:
        """換掉程式資料夾（保留資料夾本身，Docker 掛載不受影響）與 module.json、extra 檔案。"""
        job.begin(key, f"v{read_version(svc)} → v{read_version(svc, base)}")
        warnings = []
        dst = svc.code_dir
        dst.mkdir(parents=True, exist_ok=True)
        for p in list(dst.iterdir()):
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        for p in (base / svc.code).iterdir():
            if p.name == "__pycache__":
                continue
            if p.is_dir():
                shutil.copytree(p, dst / p.name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                shutil.copy2(p, dst / p.name)
        for name in ["module.json", *svc.extra]:
            src = base / name
            if src.is_file():
                old = svc.dir / name
                if name == "requirements.txt" and old.exists() and old.read_bytes() != src.read_bytes():
                    warnings.append("requirements.txt 有變：需要在 Container Manager 重新 build 映像才會安裝新套件")
                old.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, old)
        job.end(key, f"已換成 v{read_version(svc)}" + ("；" + "；".join(warnings) if warnings else ""),
                "warn" if warnings else "ok")
        return warnings

    def _restore(self, svc: Service, job: Job, backup_zip: Path) -> None:
        job.add_steps([("restore", "換回舊版"), ("restore_rebuild", "重建（舊版）"), ("restore_start", "啟動（舊版）"),
                       ("restore_verify", "確認舊版")])
        tmp = svc.dir.parent / f".update-{svc.id}" / f"restore-{job.id}"
        try:
            base, v = self.extract(svc, backup_zip.read_bytes(), tmp)
            try:
                info = self._info(svc)
                if (info.get("State") or {}).get("Running"):
                    self.docker.stop(info["Id"])
            except DockerError:
                pass
            self._replace(svc, job, base, key="restore")
            self._rebuild(svc, job, key="restore_rebuild")
            self._start(svc, job, key="restore_start")
            self._verify(svc, job, v, key="restore_verify")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- 動作 ------------------------------------------------------------
    def _swap(self, svc: Service, job: Job, base: Path, new: Optional[str]) -> Tuple[bool, str]:
        old = read_version(svc)
        if svc.is_self:
            self._backup(svc, job)
            self._replace(svc, job, base)
            job.begin("restart", "3 秒後重新啟動（監控程式會自動重新連線）")
            job.end("restart")
            return True, f"更新代理已換成 v{new}"
        self._stop(svc, job)
        bk = self._backup(svc, job)
        warnings = self._replace(svc, job, base)
        try:
            self._rebuild(svc, job)
            self._start(svc, job)
            if not self._verify(svc, job, new):
                raise RuntimeError("新版沒有正常啟動")
        except Exception as e:  # noqa: BLE001
            job.say(f"⚠ {e}：自動換回 v{old}…")
            self._restore(svc, job, bk)
            return False, f"更新失敗，已換回 v{old}（{e}）"
        return True, f"{svc.name} 已更新到 v{new}" + ("（" + "；".join(warnings) + "）" if warnings else "")

    def _do_update(self, svc: Service, job: Job, data: Optional[bytes], backup: str) -> Tuple[bool, str]:
        work = svc.dir.parent / f".update-{svc.id}" / job.id
        try:
            job.begin("check")
            if not data:
                raise ValueError("沒有收到 zip")
            base, new = self.extract(svc, data, work)
            old = read_version(svc)
            older = new and old and parse_version(new) < parse_version(old)
            job.end("check", f"新版 v{new}（目前 v{old}）" + ("；比目前舊" if older else ""), "warn" if older else "ok")
            return self._swap(svc, job, base, new)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _do_rollback(self, svc: Service, job: Job, data: Optional[bytes], backup: str) -> Tuple[bool, str]:
        src = self.backups_root / svc.id / Path(backup).name
        job.begin("check", backup)
        if not backup or not src.exists():
            raise ValueError(f"沒有這個備份：{backup}")
        work = svc.dir.parent / f".update-{svc.id}" / job.id
        try:
            base, v = self.extract(svc, src.read_bytes(), work)
            job.end("check", f"備份是 v{v}")
            ok, msg = self._swap(svc, job, base, v)
            return ok, (f"已回到 v{v}" if ok else msg)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _do_rebuild(self, svc: Service, job: Job, data, backup) -> Tuple[bool, str]:
        self._stop(svc, job)
        self._rebuild(svc, job)
        self._start(svc, job)
        ok = self._verify(svc, job, None)
        return ok, "重建完成" if ok else f"{svc.name} 沒有正常啟動"

    def _do_full_rebuild(self, svc: Service, job: Job, data, backup) -> Tuple[bool, str]:
        self._stop(svc, job)
        image = (self._info(svc).get("Config") or {}).get("Image", "")
        job.begin("pull", image)
        last = [0.0]

        def line(text: str) -> None:
            try:
                d = json.loads(text)
            except ValueError:
                return
            if d.get("error"):
                raise DockerError(d["error"])
            if time.time() - last[0] > 1.0 or "Status: " in str(d.get("status", "")):
                last[0] = time.time()
                job.say(f"  {d.get('status', '')} {d.get('progress', '') or ''}".rstrip())
        self.docker.pull(image, line)
        job.end("pull", f"{image} 已更新")
        self._rebuild(svc, job)
        self._start(svc, job)
        ok = self._verify(svc, job, None)
        return ok, "完全重建完成" if ok else f"{svc.name} 沒有正常啟動"

    def _do_restart(self, svc: Service, job: Job, data, backup) -> Tuple[bool, str]:
        job.begin("restart")
        self.docker.restart(self._info(svc)["Id"])
        job.end("restart", "已重新啟動")
        ok = self._verify(svc, job, None)
        return ok, f"{svc.name} 已重新啟動" if ok else f"{svc.name} 沒有正常啟動"

    def _do_stop(self, svc: Service, job: Job, data, backup) -> Tuple[bool, str]:
        self._stop(svc, job)
        return True, f"{svc.name} 已停止"

    def _do_start(self, svc: Service, job: Job, data, backup) -> Tuple[bool, str]:
        self._start(svc, job)
        ok = self._verify(svc, job, None)
        return ok, f"{svc.name} 已啟動" if ok else f"{svc.name} 沒有正常啟動"
