"""Lab Control Hub 控制代理（NAS 上另一個小容器，port 8766）：停止 / 備份 / 換程式 / 重建 / 啟動 / 確認 Hub。

Hub 自己停止時沒辦法回報進度，所以由這個一直在執行的代理負責，並把每一步即時回報給控制台
（Lab Control Monitor 的「Hub 控制台」或網頁 http://<NAS>:8766/）。

    python -m labhub.agent            # docker-compose.yml 的 agent 服務

需要：
  * /var/run/docker.sock（Docker Engine API；只用標準函式庫，不需要 docker CLI）
  * HUB_DIR（預設 /hubdir）= NAS 上的 LabControlHub 資料夾（裡面有 labhub/、data/、docker-compose.yml）

動作（Hub）：
  update        上傳新版 zip → 檢查 → 停止 → 備份 → 換程式 → 重建 → 啟動 → 確認版本（失敗自動換回舊版）
  full_rebuild  停止 → 重新下載基礎映像 → 重建 → 啟動 → 確認
  rebuild       停止 → 重建 → 啟動 → 確認
  restart / stop / start / rollback（回到某個備份）

動作（論文庫 paperlib，設定 PAPERLIB_DIR 後啟用；0.0.14 起）：
  paperlib_update    上傳新版 zip → 檢查 → 備份程式 → 換程式（data/、import/、docker-compose.yml 不動）
                     → 建置映像 → 重建容器 → 啟動 → 確認版本（/api/site；失敗自動換回舊版）
  paperlib_restart / paperlib_rollback（回到某個備份）
"""
from __future__ import annotations

import argparse
import datetime as _dt
import http.client
import io
import json
import logging
import os
import re
import shutil
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import __version__
from .server import STATIC, Handler, HubHTTPError, HubServer, parse_version, route

log = logging.getLogger("labhub.agent")
AGENT_PORT = 8766
ACTIONS = {"update": "更新網站", "full_rebuild": "完全重建", "rebuild": "重建", "restart": "重新啟動網站",
           "stop": "停止", "start": "啟動", "rollback": "回到備份",
           "paperlib_update": "更新論文庫", "paperlib_restart": "重新啟動論文庫", "paperlib_rollback": "論文庫回到備份"}
#: 更新論文庫時不動的東西（資料、匯入資料夾、使用者改過的 compose）
PAPERLIB_KEEP = {"data", "import", "backups", ".env", "docker-compose.yml", "docker-compose.yml.bak", ".git",
                 "__pycache__", ".update"}
_PL_VER = re.compile(r"""^VERSION\s*=\s*["']([^"']+)["']""", re.M)


# ---- Docker Engine API（unix socket 或 http，用標準函式庫）-----------------------------
class _UnixConn(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 60) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


class DockerError(RuntimeError):
    pass


class Docker:
    def __init__(self, host: str) -> None:
        self.host = host                 # unix:///var/run/docker.sock 或 http://127.0.0.1:2375

    def _conn(self, timeout: float):
        if self.host.startswith("unix://"):
            return _UnixConn(self.host[len("unix://"):], timeout)
        u = urllib.parse.urlsplit(self.host)
        return http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)

    def req(self, method: str, path: str, body: Any = None, timeout: float = 60,
            on_line: Optional[Callable[[str], None]] = None, raw: Optional[bytes] = None,
            ctype: str = "application/json") -> Tuple[int, Any]:
        c = self._conn(timeout)
        try:
            data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
            c.request(method, path, body=data, headers={"Content-Type": ctype} if data is not None else {})
            r = c.getresponse()
            if on_line is not None:                     # 串流（下載映像的進度）
                buf = b""
                while True:
                    chunk = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if line.strip():
                            on_line(line.decode("utf-8", "replace"))
                return r.status, None
            raw = r.read()
            try:
                return r.status, json.loads(raw) if raw else None
            except ValueError:
                return r.status, raw.decode("utf-8", "replace")
        except OSError as e:
            raise DockerError(f"連不到 Docker（{self.host}）：{e}；請確認 docker-compose.yml 有掛載 /var/run/docker.sock") from None
        finally:
            c.close()

    def _ok(self, st: int, data: Any, what: str, allow=(200, 201, 204, 304)) -> Any:
        if st not in allow:
            msg = data.get("message") if isinstance(data, dict) else data
            raise DockerError(f"{what}失敗（{st}）：{msg}")
        return data

    def inspect(self, name: str) -> Optional[Dict[str, Any]]:
        st, data = self.req("GET", f"/containers/{urllib.parse.quote(name)}/json")
        if st == 404:
            return None
        return self._ok(st, data, "讀取容器資訊")

    def stop(self, cid: str, t: int = 15) -> None:
        st, data = self.req("POST", f"/containers/{cid}/stop?t={t}", timeout=t + 30)
        self._ok(st, data, "停止容器")

    def start(self, cid: str) -> None:
        st, data = self.req("POST", f"/containers/{cid}/start")
        self._ok(st, data, "啟動容器")

    def restart(self, cid: str, t: int = 15) -> None:
        st, data = self.req("POST", f"/containers/{cid}/restart?t={t}", timeout=t + 30)
        self._ok(st, data, "重新啟動容器")

    def remove(self, cid: str) -> None:
        st, data = self.req("DELETE", f"/containers/{cid}?force=true")
        self._ok(st, data, "刪除容器", allow=(200, 204, 404))

    def pull(self, image: str, on_line: Callable[[str], None]) -> None:
        name, _, tag = image.rpartition(":") if ":" in image.split("/")[-1] else (image, "", "latest")
        st, _ = self.req("POST", f"/images/create?fromImage={urllib.parse.quote(name)}&tag={urllib.parse.quote(tag)}",
                         timeout=900, on_line=on_line)
        if st != 200:
            raise DockerError(f"下載映像 {image} 失敗（{st}）")

    def build(self, tag: str, context_tar: bytes, on_line: Callable[[str], None]) -> None:
        """POST /build：用 tar 的建置內容（含 Dockerfile）建立映像並標成 tag。"""
        errors: List[str] = []

        def line(l: str) -> None:
            try:
                d = json.loads(l)
            except ValueError:
                return
            if d.get("error"):
                errors.append(str(d["error"]))
            msg = str(d.get("stream") or d.get("status") or "").rstrip()
            if msg:
                on_line(msg)
        st, _ = self.req("POST", f"/build?t={urllib.parse.quote(tag)}&rm=1&forcerm=1", timeout=1800, on_line=line,
                         raw=context_tar, ctype="application/x-tar")
        if st != 200 or errors:
            raise DockerError(f"建置映像 {tag} 失敗（{st}）：{'; '.join(errors)[-500:]}")

    def recreate(self, name: str, info: Dict[str, Any]) -> str:
        """以原本的設定（inspect）刪除並重新建立容器，回傳新 id。compose 標籤一併保留。"""
        cfg = dict(info.get("Config") or {})
        keep = ("Env", "Cmd", "Entrypoint", "WorkingDir", "User", "Labels", "ExposedPorts", "Volumes", "StopSignal",
                "Healthcheck", "Tty", "OpenStdin", "StdinOnce", "AttachStdin", "AttachStdout", "AttachStderr")
        body: Dict[str, Any] = {k: cfg[k] for k in keep if cfg.get(k) is not None}
        body["Image"] = cfg.get("Image") or info.get("Image")
        body["HostConfig"] = info.get("HostConfig") or {}
        nets = (info.get("NetworkSettings") or {}).get("Networks") or {}
        items = list(nets.items())
        if items:
            n0, v0 = items[0]
            body["NetworkingConfig"] = {"EndpointsConfig": {n0: {"Aliases": v0.get("Aliases"),
                                                                 "IPAMConfig": v0.get("IPAMConfig")}}}
        self.remove(info["Id"])
        st, data = self.req("POST", f"/containers/create?name={urllib.parse.quote(name.lstrip('/'))}", body)
        new = self._ok(st, data, "建立容器")["Id"]
        for n, v in items[1:]:
            self.req("POST", f"/networks/{urllib.parse.quote(n)}/connect",
                     {"Container": new, "EndpointConfig": {"Aliases": v.get("Aliases")}})
        return new


# ---- 工作（每一步即時回報）----------------------------------------------------------
class Job:
    def __init__(self, action: str, steps: List[Tuple[str, str]], by: str = "") -> None:
        self.id = uuid.uuid4().hex[:10]
        self.action, self.by = action, by
        self.title = ACTIONS.get(action, action)
        self.steps = [{"key": k, "title": t, "status": "pending", "msg": "", "t0": None, "t1": None} for k, t in steps]
        self.lines: List[Dict[str, Any]] = []
        self.done, self.ok = False, False
        self.started = time.time()
        self.finished: Optional[float] = None
        self.result = ""
        self.cond = threading.Condition()

    def _step(self, key: str) -> Dict[str, Any]:
        for s in self.steps:
            if s["key"] == key:
                return s
        s = {"key": key, "title": key, "status": "pending", "msg": "", "t0": None, "t1": None}
        self.steps.append(s)
        return s

    def begin(self, key: str, msg: str = "") -> None:
        with self.cond:
            s = self._step(key)
            s.update(status="running", t0=time.time(), msg=msg)
            self._line(f"▶ {s['title']}" + (f"：{msg}" if msg else ""))

    def end(self, key: str, msg: str = "", status: str = "ok") -> None:
        with self.cond:
            s = self._step(key)
            s.update(status=status, t1=time.time(), msg=msg or s["msg"])
            icon = {"ok": "✔", "fail": "✖", "skip": "–"}.get(status, "•")
            self._line(f"{icon} {s['title']}" + (f"：{msg}" if msg else ""))

    def say(self, msg: str) -> None:
        with self.cond:
            self._line(msg)

    def _line(self, msg: str) -> None:
        self.lines.append({"i": len(self.lines), "time": time.time(), "msg": msg})
        log.info("[%s] %s", self.id, msg)
        self.cond.notify_all()

    def finish(self, ok: bool, result: str) -> None:
        with self.cond:
            for s in self.steps:
                if s["status"] == "pending":
                    s["status"] = "skip"
            self.done, self.ok, self.result, self.finished = True, ok, result, time.time()
            self._line(("✅ " if ok else "❌ ") + result)

    def view(self, after: int = 0) -> Dict[str, Any]:
        return {"id": self.id, "action": self.action, "title": self.title, "by": self.by, "steps": self.steps,
                "lines": self.lines[after:], "n_lines": len(self.lines), "done": self.done, "ok": self.ok,
                "result": self.result, "started": self.started, "finished": self.finished}


class AgentConfig:
    def __init__(self, hub_dir: Path, container: str, hub_url: str, docker_host: str, token: Optional[str],
                 verify_s: float = 60.0, paperlib_dir: Optional[Path] = None, paperlib_container: str = "paperlib",
                 paperlib_url: str = "", paperlib_verify_s: float = 180.0) -> None:
        self.hub_dir = Path(hub_dir)
        self.container, self.hub_url = container, hub_url.rstrip("/")
        self.docker_host = docker_host
        self.token = token
        self.verify_s = verify_s
        # 論文庫（paperlib）：專案資料夾（裡面有 Dockerfile、app/、data/）、容器名稱、網址
        self.paperlib_dir = Path(paperlib_dir) if paperlib_dir else None
        self.paperlib_container = paperlib_container
        self.paperlib_url = (paperlib_url or "").rstrip("/")
        self.paperlib_verify_s = paperlib_verify_s

    @classmethod
    def from_env(cls) -> "AgentConfig":
        e = os.environ
        tok = e.get("LABHUB_TOKEN", "")
        if not tok:
            f = Path(e.get("HUB_DIR", "/hubdir")) / "data" / "token.txt"
            tok = f.read_text(encoding="utf-8").strip() if f.exists() else ""
        return cls(Path(e.get("HUB_DIR", "/hubdir")), e.get("HUB_CONTAINER", "labcontrol-hub"),
                   e.get("HUB_URL", "http://labcontrol-hub:8765"),
                   e.get("DOCKER_HOST", "unix:///var/run/docker.sock"),
                   None if e.get("LABHUB_AUTH", "on").lower() in ("off", "0", "false") else (tok or None),
                   float(e.get("AGENT_VERIFY_S", 60)),
                   Path(e["PAPERLIB_DIR"]) if e.get("PAPERLIB_DIR") else None,
                   e.get("PAPERLIB_CONTAINER", "paperlib"), e.get("PAPERLIB_URL", "http://paperlib:8080"),
                   float(e.get("PAPERLIB_VERIFY_S", 180)))


def package_version(pkg: Path) -> Optional[str]:
    try:
        m = re.search(r'__version__\s*=\s*["\']([^"\']+)', (pkg / "__init__.py").read_text(encoding="utf-8"))
        return m.group(1) if m else None
    except OSError:
        return None


class Agent:
    def __init__(self, cfg: AgentConfig) -> None:
        self.cfg = cfg
        self.docker = Docker(cfg.docker_host)
        self.jobs: Dict[str, Job] = {}
        self.current: Optional[Job] = None
        self.lock = threading.Lock()
        self.restart_self = False

    # ---- 路徑 ------------------------------------------------------------
    @property
    def code_dir(self) -> Path:
        return self.cfg.hub_dir / "labhub"

    @property
    def backup_dir(self) -> Path:
        return self.cfg.hub_dir / "backups"

    def backups(self) -> List[Dict[str, Any]]:
        if not self.backup_dir.exists():
            return []
        out = []
        for p in sorted(self.backup_dir.glob("labhub_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
            m = re.match(r"labhub_v(.+?)_(\d{8}-\d{6})\.zip$", p.name)
            out.append({"name": p.name, "version": m.group(1) if m else "?", "size": p.stat().st_size,
                        "time": p.stat().st_mtime})
        return out

    # ---- 狀態 ------------------------------------------------------------
    def hub_ping(self, timeout: float = 3.0) -> Optional[Dict[str, Any]]:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(self.cfg.hub_url + "/api/ping", timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def status(self) -> Dict[str, Any]:
        c: Dict[str, Any] = {"name": self.cfg.container}
        try:
            info = self.docker.inspect(self.cfg.container)
            if info is None:
                c["status"] = "missing"
            else:
                stt = info.get("State") or {}
                c.update(status=stt.get("Status"), running=stt.get("Running"), started=stt.get("StartedAt"),
                         image=(info.get("Config") or {}).get("Image"), id=info.get("Id", "")[:12],
                         restarts=info.get("RestartCount"))
        except DockerError as e:
            c.update(status="unknown", error=str(e))
        ping = self.hub_ping()
        cur = self.current
        return {"agent_version": __version__, "container": c, "hub_online": ping is not None,
                "hub_version": (ping or {}).get("version"), "code_version": package_version(self.code_dir),
                "hub_dir": str(self.cfg.hub_dir), "backups": self.backups()[:10],
                "paperlib": self.paperlib_status(),
                "job": cur.view(len(cur.lines)) if cur is not None else None, "time": time.time()}

    # ---- 啟動工作 --------------------------------------------------------
    STEPS = {
        "update": [("check", "檢查 zip"), ("stop", "停止"), ("backup", "備份"), ("replace", "換程式"),
                   ("rebuild", "重建"), ("start", "啟動"), ("verify", "確認版本")],
        "full_rebuild": [("stop", "停止"), ("pull", "下載基礎映像"), ("rebuild", "重建"), ("start", "啟動"),
                         ("verify", "確認版本")],
        "rebuild": [("stop", "停止"), ("rebuild", "重建"), ("start", "啟動"), ("verify", "確認版本")],
        "restart": [("restart", "重新啟動"), ("verify", "確認版本")],
        "stop": [("stop", "停止")],
        "start": [("start", "啟動"), ("verify", "確認版本")],
        "rollback": [("stop", "停止"), ("backup", "備份目前版本"), ("replace", "換回備份"), ("rebuild", "重建"),
                     ("start", "啟動"), ("verify", "確認版本")],
        "paperlib_update": [("check", "檢查 zip"), ("backup", "備份程式"), ("replace", "換程式"), ("build", "建置映像"),
                            ("stop", "停止"), ("dbbackup", "備份資料庫"), ("rebuild", "重建容器"), ("start", "啟動"),
                            ("verify", "確認版本")],
        "paperlib_restart": [("restart", "重新啟動"), ("verify", "確認版本")],
        "paperlib_rollback": [("backup", "備份目前版本"), ("replace", "換回備份"), ("build", "建置映像"),
                              ("rebuild", "重建容器"), ("start", "啟動"), ("verify", "確認版本")],
    }

    def submit(self, action: str, by: str = "", data: Optional[bytes] = None, backup: str = "") -> Job:
        if action not in self.STEPS:
            raise HubHTTPError(400, f"不認得的動作 {action}")
        if action.startswith("paperlib_") and self.cfg.paperlib_dir is None:
            raise HubHTTPError(400, "控制代理沒有設定論文庫（docker-compose.yml 的 PAPERLIB_DIR 與掛載）")
        with self.lock:
            if self.current is not None and not self.current.done:
                raise HubHTTPError(409, f"正在執行「{self.current.title}」，請等它完成")
            job = Job(action, self.STEPS[action], by)
            self.jobs[job.id] = job
            self.current = job
        threading.Thread(target=self._run, args=(job, data, backup), daemon=True, name=f"job-{job.id}").start()
        return job

    def _run(self, job: Job, data: Optional[bytes], backup: str) -> None:
        try:
            fn = getattr(self, f"_do_{job.action}")
            ok, msg = fn(job, data, backup)
            job.finish(ok, msg)
        except Exception as e:  # noqa: BLE001
            log.exception("工作失敗")
            for s in job.steps:
                if s["status"] == "running":
                    s["status"] = "fail"
            job.finish(False, f"{type(e).__name__}: {e}")
        if self.restart_self:
            job.say("控制代理 3 秒後重新啟動以套用新版（畫面會自動重新連線）")
            threading.Timer(3.0, lambda: os._exit(0)).start()

    # ---- 各步驟 ------------------------------------------------------------
    def _info(self) -> Dict[str, Any]:
        info = self.docker.inspect(self.cfg.container)
        if info is None:
            raise DockerError(f"找不到容器 {self.cfg.container}（先在 Container Manager 建立專案）")
        return info

    def _stop(self, job: Job, key: str = "stop") -> None:
        job.begin(key, self.cfg.container)
        info = self._info()
        if (info.get("State") or {}).get("Running"):
            self.docker.stop(info["Id"])
            job.end(key, "已停止")
        else:
            job.end(key, "原本就沒有在執行")

    def _rebuild(self, job: Job, key: str = "rebuild") -> None:
        job.begin(key, "以原本的設定重新建立容器（掛載的新程式會生效）")
        info = self._info()
        if (info.get("State") or {}).get("Running"):
            self.docker.stop(info["Id"])
        new = self.docker.recreate(self.cfg.container, info)
        job.end(key, f"新容器 {new[:12]}")

    def _start(self, job: Job, key: str = "start") -> None:
        job.begin(key)
        info = self._info()
        if not (info.get("State") or {}).get("Running"):
            self.docker.start(info["Id"])
        job.end(key, "已啟動")

    def _verify(self, job: Job, want: Optional[str], key: str = "verify") -> bool:
        job.begin(key, "等待 Hub 回應" + (f"（應為 v{want}）" if want else ""))
        t0 = time.time()
        last = None
        while time.time() - t0 < self.cfg.verify_s:
            p = self.hub_ping()
            if p is not None:
                last = p.get("version")
                if want is None or last == want:
                    job.end(key, f"Hub v{last} 已上線（{time.time() - t0:.0f} s）")
                    return True
            time.sleep(1.0)
        job.end(key, f"{self.cfg.verify_s:.0f} 秒內沒有" + (f"等到 v{want}（目前 {last}）" if last else "回應"), "fail")
        return False

    def _backup(self, job: Job, key: str = "backup") -> Path:
        job.begin(key)
        v = package_version(self.code_dir) or "unknown"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        dst = self.backup_dir / f"labhub_v{v}_{_dt.datetime.now():%Y%m%d-%H%M%S}.zip"
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(self.code_dir.rglob("*")):
                rel = p.relative_to(self.code_dir)
                if p.is_file() and "__pycache__" not in rel.parts:
                    z.write(p, ("labhub" / rel).as_posix())
        for old in self.backups()[20:]:                 # 保留最近 20 份
            try:
                (self.backup_dir / old["name"]).unlink()
            except OSError:
                pass
        job.end(key, f"{dst.name}")
        return dst

    def _replace_from(self, job: Job, pkg: Path, key: str = "replace") -> None:
        """把 pkg（labhub 資料夾）的內容換進 hub_dir/labhub（保留資料夾本身，Docker 掛載不受影響）。"""
        job.begin(key, f"v{package_version(self.code_dir)} → v{package_version(pkg)}")
        dst = self.code_dir
        dst.mkdir(exist_ok=True)
        for p in list(dst.iterdir()):
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        for p in pkg.iterdir():
            if p.name == "__pycache__":
                continue
            if p.is_dir():
                shutil.copytree(p, dst / p.name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                shutil.copy2(p, dst / p.name)
        stale = self.cfg.hub_dir / "data" / "hub_app" / "current.json"      # 0.0.8 的網頁內更新不再使用
        if stale.exists():
            stale.rename(stale.with_name("current.disabled.json"))
        job.end(key, f"已換成 v{package_version(dst)}")

    @staticmethod
    def extract_package(data: bytes, into: Path) -> Tuple[Path, str]:
        """從 zip 找出 labhub/（可在任一層，例如 LabControlHub/labhub/ 或 LabControl/labhub/）並檢查語法。"""
        into.mkdir(parents=True, exist_ok=True)
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise ValueError("不是 zip 檔")
        root = into.resolve()
        with z:
            for m in z.infolist():
                t = (root / m.filename).resolve()
                if root not in t.parents and t != root:
                    raise ValueError(f"zip 內容不安全：{m.filename}")
            z.extractall(root)
        pkgs = sorted((p.parent for p in root.rglob("server.py") if p.parent.name == "labhub"
                       and (p.parent / "__init__.py").exists()), key=lambda p: len(p.parts))
        if not pkgs:
            raise ValueError("zip 裡找不到 labhub/ 資料夾（請選 LabControlHub_v*.zip 或 LabControl_v*.zip）")
        pkg = pkgs[0]
        for f in pkg.rglob("*.py"):
            compile(f.read_text(encoding="utf-8"), str(f), "exec")
        v = package_version(pkg)
        if not v:
            raise ValueError("讀不到新版的版本號")
        return pkg, v

    # ---- 動作 ------------------------------------------------------------
    def _do_update(self, job: Job, data: Optional[bytes], backup: str) -> Tuple[bool, str]:
        work = self.cfg.hub_dir / ".update" / job.id
        try:
            job.begin("check")
            if not data:
                raise ValueError("沒有收到 zip")
            pkg, new = self.extract_package(data, work)
            old = package_version(self.code_dir)
            agent_changed = (pkg / "agent.py").exists() and \
                (pkg / "agent.py").read_bytes() != ((self.code_dir / "agent.py").read_bytes()
                                                   if (self.code_dir / "agent.py").exists() else b"")
            job.end("check", f"新版 v{new}（目前 v{old}）" + ("；舊版本" if old and parse_version(new) < parse_version(old)
                                                        else ""))
            self._stop(job)
            bk = self._backup(job)
            self._replace_from(job, pkg)
            try:
                self._rebuild(job)
                self._start(job)
                if not self._verify(job, new):
                    raise RuntimeError("新版沒有正常啟動")
            except Exception as e:  # noqa: BLE001
                job.say(f"⚠ {e}：自動換回 v{old}…")
                self._restore(job, bk)
                return False, f"更新失敗，已換回 v{old}（{e}）"
            if agent_changed:
                self.restart_self = True
            return True, f"Hub 已更新到 v{new}"
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _restore(self, job: Job, backup_zip: Path) -> None:
        tmp = self.cfg.hub_dir / ".update" / f"restore-{job.id}"
        with job.cond:
            for k, t in (("restore", "換回舊版"), ("restore_rebuild", "重建（舊版）"), ("restore_start", "啟動（舊版）"),
                         ("restore_verify", "確認舊版")):
                job.steps.append({"key": k, "title": t, "status": "pending", "msg": "", "t0": None, "t1": None})
        try:
            pkg, v = self.extract_package(backup_zip.read_bytes(), tmp)
            try:
                info = self._info()
                if (info.get("State") or {}).get("Running"):
                    self.docker.stop(info["Id"])
            except DockerError:
                pass
            self._replace_from(job, pkg, key="restore")
            self._rebuild(job, key="restore_rebuild")
            self._start(job, key="restore_start")
            self._verify(job, v, key="restore_verify")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _do_rollback(self, job: Job, data: Optional[bytes], backup: str) -> Tuple[bool, str]:
        src = self.backup_dir / Path(backup).name
        if not backup or not src.exists():
            raise ValueError(f"沒有這個備份：{backup}")
        tmp = self.cfg.hub_dir / ".update" / job.id
        try:
            pkg, v = self.extract_package(src.read_bytes(), tmp)
            self._stop(job)
            self._backup(job)
            self._replace_from(job, pkg)
            self._rebuild(job)
            self._start(job)
            ok = self._verify(job, v)
            return ok, (f"已回到 v{v}" if ok else "Hub 沒有正常啟動")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _do_full_rebuild(self, job: Job, data, backup) -> Tuple[bool, str]:
        self._stop(job)
        info = self._info()
        image = (info.get("Config") or {}).get("Image", "")
        job.begin("pull", image)
        last = [0.0]

        def line(l: str) -> None:
            try:
                d = json.loads(l)
            except ValueError:
                return
            if d.get("error"):
                raise DockerError(d["error"])
            if time.time() - last[0] > 1.0 or "Status: " in str(d.get("status", "")):
                last[0] = time.time()
                job.say(f"  {d.get('status', '')} {d.get('progress', '') or ''}".rstrip())
        self.docker.pull(image, line)
        job.end("pull", f"{image} 已更新")
        self._rebuild(job)
        self._start(job)
        ok = self._verify(job, None)
        return ok, "完全重建完成" if ok else "Hub 沒有正常啟動"

    def _do_rebuild(self, job: Job, data, backup) -> Tuple[bool, str]:
        self._stop(job)
        self._rebuild(job)
        self._start(job)
        ok = self._verify(job, None)
        return ok, "重建完成" if ok else "Hub 沒有正常啟動"

    def _do_restart(self, job: Job, data, backup) -> Tuple[bool, str]:
        job.begin("restart")
        info = self._info()
        self.docker.restart(info["Id"])
        job.end("restart", "已重新啟動")
        ok = self._verify(job, None)
        return ok, "Hub 已重新啟動" if ok else "Hub 沒有正常啟動"

    def _do_stop(self, job: Job, data, backup) -> Tuple[bool, str]:
        self._stop(job)
        return True, "Hub 已停止"

    def _do_start(self, job: Job, data, backup) -> Tuple[bool, str]:
        self._start(job)
        ok = self._verify(job, None)
        return ok, "Hub 已啟動" if ok else "Hub 沒有正常啟動"

    # ==== 論文庫（paperlib）：Dockerfile 建置的網站 ====================================================
    def paperlib_code_version(self, d: Optional[Path] = None) -> Optional[str]:
        d = d or self.cfg.paperlib_dir
        try:
            m = _PL_VER.search((d / "app" / "config.py").read_text(encoding="utf-8"))
            return m.group(1) if m else None
        except (OSError, TypeError):
            return None

    def paperlib_ping(self, timeout: float = 4.0) -> Optional[Dict[str, Any]]:
        if not self.cfg.paperlib_url:
            return None
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(self.cfg.paperlib_url + "/api/site", timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def paperlib_backups(self) -> List[Dict[str, Any]]:
        if not self.backup_dir.exists():
            return []
        out = []
        for p in sorted(self.backup_dir.glob("paperlib_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
            m = re.match(r"paperlib_v(.+?)_(\d{8}-\d{6})\.zip$", p.name)
            out.append({"name": p.name, "version": m.group(1) if m else "?", "size": p.stat().st_size,
                        "time": p.stat().st_mtime})
        return out

    def paperlib_status(self) -> Dict[str, Any]:
        if self.cfg.paperlib_dir is None:
            return {"configured": False}
        c: Dict[str, Any] = {"name": self.cfg.paperlib_container}
        try:
            info = self.docker.inspect(self.cfg.paperlib_container)
            if info is None:
                c["status"] = "missing"
            else:
                stt = info.get("State") or {}
                c.update(status=stt.get("Status"), running=stt.get("Running"),
                         image=(info.get("Config") or {}).get("Image"))
        except DockerError as e:
            c.update(status="unknown", error=str(e))
        ping = self.paperlib_ping()
        return {"configured": True, "container": c, "online": ping is not None, "version": (ping or {}).get("version"),
                "code_version": self.paperlib_code_version(), "dir": str(self.cfg.paperlib_dir),
                "url": self.cfg.paperlib_url, "backups": self.paperlib_backups()[:10]}

    def _pl_info(self) -> Dict[str, Any]:
        info = self.docker.inspect(self.cfg.paperlib_container)
        if info is None:
            raise DockerError(f"找不到容器 {self.cfg.paperlib_container}（先用 Container Manager 建立論文庫專案）")
        return info

    @staticmethod
    def extract_paperlib(data: bytes, into: Path) -> Tuple[Path, str, bool]:
        """zip → (程式資料夾, 版本, 是否為部分更新包)。

        完整包：有 Dockerfile 與 app/main.py 的那一層（整份程式）。
        部分更新包（例如 paperlib 1.7.0 的更新 zip，只有改過的 app/*.py、app/static/*）：有 app/config.py 的那一層，
        之後只覆蓋這些檔案，其他檔案保留。
        """
        into.mkdir(parents=True, exist_ok=True)
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise ValueError("不是 zip 檔")
        root = into.resolve()
        with z:
            for m in z.infolist():
                t = (root / m.filename).resolve()
                if root not in t.parents and t != root:
                    raise ValueError(f"zip 內容不安全：{m.filename}")
            z.extractall(root)
        full = sorted((p.parent for p in root.rglob("Dockerfile") if (p.parent / "app" / "main.py").exists()),
                      key=lambda p: len(p.parts))
        if full:
            pkg, overlay = full[0], False
        else:
            part = sorted((p.parent.parent for p in root.rglob("config.py") if p.parent.name == "app"
                           and "PAPERLIB" in p.read_text(encoding="utf-8", errors="replace")),
                          key=lambda p: len(p.parts))
            if not part:
                raise ValueError("zip 裡找不到論文庫程式（完整包要有 Dockerfile 與 app/main.py；"
                                 "更新包至少要有 app/config.py）")
            pkg, overlay = part[0], True
        for f in (pkg / "app").rglob("*.py"):
            compile(f.read_text(encoding="utf-8"), str(f), "exec")
        m = _PL_VER.search((pkg / "app" / "config.py").read_text(encoding="utf-8")) \
            if (pkg / "app" / "config.py").exists() else None
        if not m:
            raise ValueError("讀不到論文庫的版本號（app/config.py 的 VERSION）")
        return pkg, m.group(1), overlay

    @staticmethod
    def missing_imports(app_dirs: List[Path]) -> List[str]:
        """合併後的 app/ 裡，`from . import x` / `from .x import …` 用到但不存在的模組（部分更新包需要較新的基底版本時）。"""
        have = set()
        for d in app_dirs:
            if d.is_dir():
                have |= {p.stem for p in d.glob("*.py")} | {p.name for p in d.iterdir() if p.is_dir()}
        need: Dict[str, str] = {}
        for d in app_dirs:
            if not d.is_dir():
                continue
            for f in d.glob("*.py"):
                text = f.read_text(encoding="utf-8", errors="replace")
                groups = re.findall(r"^\s*from \. import \(([^)]*)\)", text, re.M) + \
                    re.findall(r"^\s*from \. import (?!\()([^\n#]+)", text, re.M)
                for mods in groups:
                    for part in mods.replace("\\", " ").split(","):
                        toks = part.split()
                        if toks and re.fullmatch(r"\w+", toks[0]):
                            need.setdefault(toks[0], f.name)
                for nm in re.findall(r"^\s*from \.(\w+)", text, re.M):
                    need.setdefault(nm, f.name)
        return sorted(f"{n}.py（{need[n]} 需要）" for n in need if n not in have)

    def _pl_items(self, base: Path) -> List[Path]:
        return [p for p in sorted(base.iterdir()) if p.name not in PAPERLIB_KEEP and not p.name.startswith(".update")]

    def _pl_backup(self, job: Job, key: str = "backup") -> Path:
        job.begin(key, "只備份程式（data/ 不動）")
        d = self.cfg.paperlib_dir
        v = self.paperlib_code_version() or "unknown"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        dst = self.backup_dir / f"paperlib_v{v}_{_dt.datetime.now():%Y%m%d-%H%M%S}.zip"
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
            for item in self._pl_items(d):
                for p in ([item] if item.is_file() else sorted(item.rglob("*"))):
                    rel = p.relative_to(d)
                    if p.is_file() and "__pycache__" not in rel.parts:
                        z.write(p, ("paperlib" / rel).as_posix())
        for old in self.paperlib_backups()[10:]:
            try:
                (self.backup_dir / old["name"]).unlink()
            except OSError:
                pass
        job.end(key, dst.name)
        return dst

    def _pl_replace(self, job: Job, pkg: Path, key: str = "replace", overlay: bool = False) -> None:
        d = self.cfg.paperlib_dir
        job.begin(key, f"v{self.paperlib_code_version()} → v{self.paperlib_code_version(pkg)}")
        if overlay:                                   # 部分更新包：只覆蓋包裡有的檔案
            n = 0
            for p in sorted(pkg.rglob("*")):
                rel = p.relative_to(pkg)
                if not p.is_file() or rel.parts[0] in PAPERLIB_KEEP or "__pycache__" in rel.parts:
                    continue
                dst = d / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
                n += 1
            job.end(key, f"已覆蓋 {n} 個檔案，現在是 v{self.paperlib_code_version()}（其他檔案、data/ 沒動）")
            return
        new_items = self._pl_items(pkg)
        for item in new_items:
            dst = d / item.name
            if dst.is_dir():
                shutil.rmtree(dst)
            elif dst.exists():
                dst.unlink()
            if item.is_dir():
                shutil.copytree(item, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                shutil.copy2(item, dst)
        job.end(key, f"已換成 v{self.paperlib_code_version()}（{len(new_items)} 項；data/ 與 docker-compose.yml 沒動）")

    def _pl_context(self) -> bytes:
        """建置內容：論文庫資料夾，不含 data/、import/ 等（照 .dockerignore 的簡單規則再排除）。"""
        import fnmatch
        import tarfile

        d = self.cfg.paperlib_dir
        ignore = set(PAPERLIB_KEEP)
        di = d / ".dockerignore"
        pats = [l.strip().rstrip("/") for l in di.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")] if di.exists() else []
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for p in sorted(d.rglob("*")):
                rel = p.relative_to(d)
                if rel.parts[0] in ignore or "__pycache__" in rel.parts or not p.is_file():
                    continue
                if any(fnmatch.fnmatch(rel.as_posix(), pt) or fnmatch.fnmatch(rel.parts[0], pt) for pt in pats):
                    continue
                tar.add(str(p), rel.as_posix(), recursive=False)
        return buf.getvalue()

    def _pl_build(self, job: Job, key: str = "build") -> str:
        info = self._pl_info()
        image = (info.get("Config") or {}).get("Image") or "paperlib:latest"
        job.begin(key, f"{image}（第一次或套件有變時會比較久）")
        ctx = self._pl_context()
        job.say(f"  建置內容 {len(ctx) / 1e6:.1f} MB")
        last = [0.0]

        def line(msg: str) -> None:
            if msg.startswith("Step ") or time.time() - last[0] > 2.0:
                last[0] = time.time()
                job.say("  " + msg[:200])
        self.docker.build(image, ctx, line)
        job.end(key, f"{image} 建置完成")
        return image

    def _pl_recreate(self, job: Job, key: str = "rebuild") -> None:
        job.begin(key, "用新映像重新建立容器（設定、掛載的 data/ 不變）")
        info = self._pl_info()
        if (info.get("State") or {}).get("Running"):
            self.docker.stop(info["Id"])
        new = self.docker.recreate(self.cfg.paperlib_container, info)
        job.end(key, f"新容器 {new[:12]}")

    def _pl_start(self, job: Job, key: str = "start") -> None:
        job.begin(key)
        info = self._pl_info()
        if not (info.get("State") or {}).get("Running"):
            self.docker.start(info["Id"])
        job.end(key, "已啟動")

    def _pl_verify(self, job: Job, want: Optional[str], key: str = "verify") -> bool:
        job.begin(key, "等待論文庫回應" + (f"（應為 v{want}）" if want else ""))
        t0 = time.time()
        last = None
        while time.time() - t0 < self.cfg.paperlib_verify_s:
            p = self.paperlib_ping()
            if p is not None:
                last = p.get("version")
                if want is None or last == want:
                    job.end(key, f"論文庫 v{last} 已上線（{time.time() - t0:.0f} s）")
                    return True
            time.sleep(1.0)
        job.end(key, f"{self.cfg.paperlib_verify_s:.0f} 秒內沒有" + (f"等到 v{want}（目前 {last}）" if last else "回應"),
                "fail")
        return False

    def _pl_stop(self, job: Job, key: str = "stop") -> None:
        job.begin(key, self.cfg.paperlib_container)
        info = self._pl_info()
        if (info.get("State") or {}).get("Running"):
            self.docker.stop(info["Id"], t=30)
            job.end(key, "已停止（新映像已建置好，停機只到新版啟動為止）")
        else:
            job.end(key, "原本就沒有在執行")

    def _pl_db_backup(self, job: Job, key: str = "dbbackup") -> Optional[Path]:
        """停止後備份 data/library.db（新版可能升級資料庫結構；失敗時一併換回）。"""
        import sqlite3

        job.begin(key)
        src = self.cfg.paperlib_dir / "data" / "library.db"
        if not src.exists():
            job.end(key, "沒有 data/library.db（略過）")
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        v = self.paperlib_code_version() or "unknown"
        dst = self.backup_dir / f"paperlib_db_{_dt.datetime.now():%Y%m%d-%H%M%S}.db"
        a = sqlite3.connect(str(src))
        b = sqlite3.connect(str(dst))
        try:
            a.backup(b)
        finally:
            b.close()
            a.close()
        olds = sorted(self.backup_dir.glob("paperlib_db_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in olds[5:]:                              # 保留最近 5 份資料庫備份
            try:
                old.unlink()
            except OSError:
                pass
        job.end(key, f"{dst.name}（{dst.stat().st_size / 1e6:.1f} MB）")
        return dst

    def _pl_db_restore(self, job: Job, db_backup: Optional[Path]) -> None:
        if db_backup is None or not db_backup.exists():
            return
        dst = self.cfg.paperlib_dir / "data" / "library.db"
        for ext in ("-wal", "-shm"):
            w = dst.with_name(dst.name + ext)
            if w.exists():
                w.unlink()
        shutil.copy2(db_backup, dst)
        job.say(f"  資料庫已換回更新前的備份 {db_backup.name}")

    def _pl_restore(self, job: Job, backup_zip: Path) -> None:
        tmp = self.cfg.hub_dir / ".update" / f"pl-restore-{job.id}"
        with job.cond:
            for k, t in (("restore", "換回舊版"), ("restore_build", "建置（舊版）"), ("restore_rebuild", "重建容器（舊版）"),
                         ("restore_start", "啟動（舊版）"), ("restore_verify", "確認舊版")):
                job.steps.append({"key": k, "title": t, "status": "pending", "msg": "", "t0": None, "t1": None})
        try:
            pkg, v, _ = self.extract_paperlib(backup_zip.read_bytes(), tmp)
            self._pl_replace(job, pkg, key="restore")
            self._pl_build(job, key="restore_build")
            self._pl_recreate(job, key="restore_rebuild")
            self._pl_start(job, key="restore_start")
            self._pl_verify(job, v, key="restore_verify")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _do_paperlib_update(self, job: Job, data: Optional[bytes], backup: str) -> Tuple[bool, str]:
        """先換程式、建置好新映像（舊版照常服務）→ 停止 → 備份資料庫 → 重建容器 → 啟動 → 確認；失敗換回程式與資料庫。"""
        work = self.cfg.hub_dir / ".update" / job.id
        try:
            job.begin("check")
            if not data:
                raise ValueError("沒有收到 zip")
            pkg, new, overlay = self.extract_paperlib(data, work)
            old = self.paperlib_code_version()
            self._pl_info()                                 # 容器要存在
            if overlay:
                miss = self.missing_imports([pkg / "app", self.cfg.paperlib_dir / "app"])
                if miss:
                    raise ValueError(f"這是 v{new} 的部分更新包，但 NAS 上的論文庫 v{old} 缺少它需要的檔案："
                                     f"{'、'.join(miss[:5])}。請先更新到它要求的基底版本（完整包），再套用這個更新包")
            job.end("check", f"{'部分更新包' if overlay else '完整包'} v{new}（目前 v{old}）"
                    + ("；舊版本" if old and parse_version(new) < parse_version(old) else ""))
            bk = self._pl_backup(job)
            self._pl_replace(job, pkg, overlay=overlay)
            db_bk: Optional[Path] = None
            try:
                self._pl_build(job)
                self._pl_stop(job)
                db_bk = self._pl_db_backup(job)
                self._pl_recreate(job)
                self._pl_start(job)
                if not self._pl_verify(job, new):
                    raise RuntimeError("新版沒有正常啟動")
            except Exception as e:  # noqa: BLE001
                job.say(f"⚠ {e}：自動換回 v{old}…")
                try:
                    info = self._pl_info()
                    if (info.get("State") or {}).get("Running"):
                        self.docker.stop(info["Id"], t=30)
                except DockerError:
                    pass
                self._pl_db_restore(job, db_bk)
                self._pl_restore(job, bk)
                return False, f"論文庫更新失敗，已換回 v{old}（{e}）"
            return True, f"論文庫已更新到 v{new}"
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _do_paperlib_restart(self, job: Job, data, backup) -> Tuple[bool, str]:
        job.begin("restart")
        info = self._pl_info()
        self.docker.restart(info["Id"])
        job.end("restart", "已重新啟動")
        ok = self._pl_verify(job, None)
        return ok, "論文庫已重新啟動" if ok else "論文庫沒有正常啟動"

    def _do_paperlib_rollback(self, job: Job, data, backup) -> Tuple[bool, str]:
        src = self.backup_dir / Path(backup).name
        if not backup or not src.exists() or not src.name.startswith("paperlib_"):
            raise ValueError(f"沒有這個論文庫備份：{backup}")
        tmp = self.cfg.hub_dir / ".update" / job.id
        try:
            pkg, v, _ = self.extract_paperlib(src.read_bytes(), tmp)
            self._pl_backup(job)
            self._pl_replace(job, pkg)
            self._pl_build(job)
            self._pl_recreate(job)
            self._pl_start(job)
            ok = self._pl_verify(job, v)
            return ok, (f"論文庫已回到 v{v}" if ok else "論文庫沒有正常啟動")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---- HTTP ----------------------------------------------------------------------
class AgentHandler(Handler):
    routes: List = []
    server_version = f"LabControlHubAgent/{__version__}"
    agent: Agent = None  # type: ignore[assignment]


A = AgentHandler


@route("GET", r"/", auth=True, handler=A)
def _a_index(h: AgentHandler) -> None:
    tok = h._query().get("token")
    if tok and h.hub.cfg.token is not None:
        c = f"labhub_token={urllib.parse.quote(tok)}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax"
        h._redirect("/", {"Set-Cookie": c})
        return
    h._html((STATIC / "console.html").read_text(encoding="utf-8"))


@route("GET", r"/login", auth=False, handler=A)
def _a_login_page(h: AgentHandler, error: str = "") -> None:
    page = (STATIC / "login.html").read_text(encoding="utf-8").replace("Lab Control Hub", "Hub 控制台")
    h._html(page.replace("{{error}}", error), 401 if error else 200)


@route("POST", r"/login", auth=False, handler=A)
def _a_login(h: AgentHandler) -> None:
    import hmac

    form = urllib.parse.parse_qs(h._body_bytes(65536).decode("utf-8"))
    tok = (form.get("token") or [""])[-1].strip()
    t = h.hub.cfg.token
    if t is None or hmac.compare_digest(tok.encode(), t.encode()):
        c = f"labhub_token={urllib.parse.quote(tok)}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax"
        h._redirect("/", {"Set-Cookie": c})
    else:
        _a_login_page(h, "token 不正確")


@route("GET", r"/logout", auth=False, handler=A)
def _a_logout(h: AgentHandler) -> None:
    h._redirect("/login", {"Set-Cookie": "labhub_token=; Path=/; Max-Age=0"})


@route("GET", r"/api/ping", auth=False, handler=A)
def _a_ping(h: AgentHandler) -> None:
    h._json({"ok": True, "server": "labhub-agent", "version": __version__, "time": time.time(),
             "auth": h.authorized()})


@route("GET", r"/api/agent", handler=A)
def _a_status(h: AgentHandler) -> None:
    h._json(h.agent.status())


@route("POST", r"/api/agent/jobs", handler=A)
def _a_submit(h: AgentHandler) -> None:
    q = h._query()
    action = q.get("action", "")
    is_update = action in ("update", "paperlib_update")
    data = h._body_bytes(256 * 1024 * 1024) if is_update else None
    if not is_update:
        h._body_bytes(65536)
    job = h.agent.submit(action, by=q.get("by", f"{h.ip}"), data=data, backup=q.get("backup", ""))
    h._json(job.view())


@route("GET", r"/api/agent/jobs/([0-9a-f]+)", handler=A)
def _a_job(h: AgentHandler, jid: str) -> None:
    job = h.agent.jobs.get(jid)
    if job is None:
        raise HubHTTPError(404, "沒有這個工作")
    after = int(float(h._query().get("after", 0)))
    wait = min(h._qf("wait"), 30.0)
    end = time.time() + wait
    with job.cond:
        while not job.done and len(job.lines) <= after and time.time() < end:
            job.cond.wait(end - time.time())
        v = job.view(after)
    h._json(v)


def make_agent_server(cfg: AgentConfig, host: str = "0.0.0.0", port: int = AGENT_PORT) -> Tuple[HubServer, Agent]:
    agent = Agent(cfg)
    state = type("AgentState", (), {"cfg": cfg})()
    handler = type("BoundAgentHandler", (AgentHandler,), {"hub": state, "agent": agent})
    return HubServer((host, port), handler), agent


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="labhub.agent", description="Lab Control Hub 控制代理")
    ap.add_argument("--host", default=os.environ.get("AGENT_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("AGENT_PORT", AGENT_PORT)))
    a = ap.parse_args(argv)
    logging.basicConfig(level=os.environ.get("LABHUB_LOG", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    cfg = AgentConfig.from_env()
    srv, agent = make_agent_server(cfg, a.host, a.port)
    log.info("Hub 控制代理 %s 啟動：http://%s:%d/（Hub 容器 %s，資料夾 %s，Docker %s）", __version__, a.host, a.port,
             cfg.container, cfg.hub_dir, cfg.docker_host)
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
