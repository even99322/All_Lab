"""Lab Control Hub 控制代理（NAS 上另一個小容器，port 8766）：停止 / 備份 / 換程式 / 重建 / 啟動 / 確認 Hub。

Hub 自己停止時沒辦法回報進度，所以由這個一直在執行的代理負責，並把每一步即時回報給控制台
（Lab Control Monitor 的「Hub 控制台」或網頁 http://<NAS>:8766/）。

    python -m labhub.agent            # docker-compose.yml 的 agent 服務

需要：
  * /var/run/docker.sock（Docker Engine API；只用標準函式庫，不需要 docker CLI）
  * HUB_DIR（預設 /hubdir）= NAS 上的 LabControlHub 資料夾（裡面有 labhub/、data/、docker-compose.yml）

動作：
  update        上傳新版 zip → 檢查 → 停止 → 備份 → 換程式 → 重建 → 啟動 → 確認版本（失敗自動換回舊版）
  full_rebuild  停止 → 重新下載基礎映像 → 重建 → 啟動 → 確認
  rebuild       停止 → 重建 → 啟動 → 確認
  restart / stop / start / rollback（回到某個備份）
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
           "stop": "停止", "start": "啟動", "rollback": "回到備份"}


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
            on_line: Optional[Callable[[str], None]] = None) -> Tuple[int, Any]:
        c = self._conn(timeout)
        try:
            data = json.dumps(body).encode() if body is not None else None
            c.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
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
                 verify_s: float = 60.0) -> None:
        self.hub_dir = Path(hub_dir)
        self.container, self.hub_url = container, hub_url.rstrip("/")
        self.docker_host = docker_host
        self.token = token
        self.verify_s = verify_s

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
                   float(e.get("AGENT_VERIFY_S", 60)))


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
    }

    def submit(self, action: str, by: str = "", data: Optional[bytes] = None, backup: str = "") -> Job:
        if action not in self.STEPS:
            raise HubHTTPError(400, f"不認得的動作 {action}")
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
    data = h._body_bytes(128 * 1024 * 1024) if action == "update" else None
    if action != "update":
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
