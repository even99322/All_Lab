"""控制台的核心：網站 API、NAS 的 SSH、更新打包。沒有畫面，方便測試。"""
import http.cookiejar
import io
import json
import os
import re
import socket
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

try:
    import paramiko  # SSH：停止／重啟容器、看容器日誌、更新網站
except ImportError:  # 沒裝時只能監控網站
    paramiko = None

APP_DIR = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "paperlib-console"
CONFIG = APP_DIR / "config.json"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
EXCLUDE_TOP = {"data", "import", ".venv", "venv", ".git", "__MACOSX"}

DEFAULTS = {
    "site_url": "http://192.168.1.20:8080", "username": "", "remember": False, "cookie": "",
    "nas_host": "", "nas_port": 22, "nas_user": "", "nas_path": "/volume1/docker/paperlib", "container": "paperlib",
    "interval": 30, "alert": True, "last_zip": "", "shortcut_asked": False,
}


def load_config() -> dict:
    try:
        return {**DEFAULTS, **json.loads(CONFIG.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_config(cfg: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in cfg.items() if k in DEFAULTS}
    if not data.get("remember"):
        data["cookie"] = ""
    CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fmt_bytes(n) -> str:
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u in ("B", "KB") else f"{n:.1f} {u}"
        n /= 1024


def fmt_dur(s) -> str:
    s = int(s or 0)
    d, s = divmod(s, 86400); h, s = divmod(s, 3600); m, _ = divmod(s, 60)
    return f"{d} 天 {h} 小時" if d else f"{h} 小時 {m} 分" if h else f"{m} 分鐘"


# ------------------------------------------------------------------ 網站 API
class ApiError(Exception):
    def __init__(self, msg, status=0):
        super().__init__(msg)
        self.status = status


class Site:
    def __init__(self, url: str, cookie: str = ""):
        self.url = url.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), urllib.request.ProxyHandler({}))
        self.cookie = cookie

    def _req(self, method, path, body=None, timeout=15, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.url + path, data=data, method=method, headers={"X-PL": "1", "Content-Type": "application/json"})
        if self.cookie and not any(c.name == "plsession" for c in self.jar):
            r.add_header("Cookie", f"plsession={self.cookie}")
        try:
            with self.op.open(r, timeout=timeout) as resp:
                for c in self.jar:
                    if c.name == "plsession":
                        self.cookie = c.value
                content = resp.read()
                return content if raw else (json.loads(content) if content else {})
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("detail") or str(e)
            except Exception:  # noqa: BLE001
                msg = str(e)
            raise ApiError(msg, e.code) from None
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
            raise ApiError(f"連不到網站：{getattr(e, 'reason', e)}", 0) from None

    def get(self, path, **kw): return self._req("GET", path, **kw)
    def post(self, path, body=None, **kw): return self._req("POST", path, body if body is not None else {}, **kw)

    def ping(self) -> tuple[bool, float, dict | None, str]:
        """網站有沒有回應：(是否正常, 回應毫秒, /api/site 內容, 錯誤訊息)"""
        t0 = time.perf_counter()
        try:
            d = self.get("/api/site", timeout=10)
            return True, (time.perf_counter() - t0) * 1000, d, ""
        except ApiError as e:
            return False, (time.perf_counter() - t0) * 1000, None, str(e)

    def login(self, username, password, code=""):
        self.cookie = ""
        self.jar.clear()
        r = self.post("/api/auth/login", {"username": username, "password": password, "code": code or None})
        if r.get("need_2fa"):
            return "2fa"
        me = self.get("/api/me")
        if me.get("role") != "admin":
            raise ApiError("這個帳號不是管理員，控制台需要管理員或站長帳號")
        return me

    def logged_in(self) -> bool:
        try:
            return self.get("/api/me", timeout=8).get("role") == "admin"
        except ApiError:
            return False

    def status(self): return self.get("/api/admin/status", timeout=30)
    def logs(self, level=""): return self.get(f"/api/admin/logs?level={level}&limit=300")["items"]
    def run_job(self, kind): return self.post("/api/admin/jobs/run", {"kind": kind})
    def backup(self): return self.post("/api/admin/backup", timeout=300)
    def download_backup(self, name, dest: Path):
        dest.write_bytes(self.get(f"/api/admin/backup/{name}", timeout=600, raw=True))
    def reindex(self): return self.post("/api/admin/reindex", timeout=900)

    # --- 帳號
    def meta(self): return self.get("/api/site")
    def me(self): return self.get("/api/me")
    def users(self): return self.get("/api/users")
    def add_user(self, d): return self.post("/api/users", d)
    def edit_user(self, uid, d): return self._req("PATCH", f"/api/users/{uid}", d)
    def delete_user(self, uid, purge=False): return self._req("DELETE", f"/api/users/{uid}?purge={1 if purge else 0}")
    def regs(self): return self.get("/api/registrations")
    def remind_email(self): return self.post("/api/admin/remind-email")
    def approve(self, rid, role): return self.post(f"/api/registrations/{rid}/approve", {"role": role})
    def reject(self, rid, reason="", notify=False): return self.post(f"/api/registrations/{rid}/reject", {"reason": reason, "notify": notify})
    def ocr_all(self): return self.post("/api/admin/ocr/all")


# ------------------------------------------------------------------ 更新用的程式包
def read_version(root: Path) -> str:
    try:
        m = re.search(r'VERSION = "([^"]+)"', (root / "app" / "config.py").read_text(encoding="utf-8"))
        return m.group(1) if m else ""
    except OSError:
        return ""


def find_root(path: Path) -> Path | None:
    """在資料夾或解壓縮後的資料夾裡，找出有 app/main.py 的那一層。"""
    if (path / "app" / "main.py").exists():
        return path
    for p in sorted(path.rglob("main.py")):
        if p.parent.name == "app" and (p.parent.parent / "docker-compose.yml").exists():
            return p.parent.parent
    return None


def open_source(src: str) -> tuple[Path, str, tempfile.TemporaryDirectory | None]:
    """src 是 zip 或資料夾；回傳 (程式根目錄, 版本, 暫存資料夾)。"""
    p = Path(src)
    tmp = None
    if p.is_file() and p.suffix.lower() == ".zip":
        tmp = tempfile.TemporaryDirectory(prefix="plupd-")
        with zipfile.ZipFile(p) as z:
            z.extractall(tmp.name)
        p = Path(tmp.name)
    root = find_root(p)
    if root is None:
        if tmp:
            tmp.cleanup()
        raise ValueError("這裡面找不到論文庫的程式（app/main.py、docker-compose.yml）")
    return root, read_version(root), tmp


def pack(root: Path) -> bytes:
    """打包成 tar.gz（不含 data、import、虛擬環境、快取）。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
        for dirpath, dirnames, filenames in os.walk(root):
            rel = Path(dirpath).relative_to(root)
            if rel == Path("."):
                dirnames[:] = [d for d in dirnames if d not in EXCLUDE_TOP]
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for f in filenames:
                if f.endswith(".pyc") or f in (".DS_Store",) or f.startswith("._"):
                    continue
                full = Path(dirpath) / f
                arc = (rel / f).as_posix()
                info = tar.gettarinfo(str(full), arcname=arc)
                if arc.endswith((".sh", ".command")):
                    info.mode = 0o755
                with open(full, "rb") as fh:
                    tar.addfile(info, fh)
    return buf.getvalue()


# ------------------------------------------------------------------ NAS（SSH）
class NasError(Exception):
    pass


class Nas:
    """用 SSH 在 NAS 上執行指令。docker 指令要管理員權限，用 sudo -S 把密碼從標準輸入給它。"""

    def __init__(self, host, port, user, password, path="/volume1/docker/paperlib", container="paperlib"):
        self.host, self.port, self.user, self.pw = host, int(port or 22), user, password
        self.path, self.container = path.rstrip("/"), container
        self.cli = None
        self.lock = threading.Lock()

    def connect(self):
        if paramiko is None:
            raise NasError("沒有安裝 paramiko，無法連線 NAS（請重新執行啟動檔，它會自動安裝）")
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            c.connect(self.host, port=self.port, username=self.user, password=self.pw, timeout=12, banner_timeout=20,
                      auth_timeout=20, look_for_keys=False, allow_agent=False)
        except paramiko.AuthenticationException:
            raise NasError("NAS 帳號或密碼錯誤") from None
        except (OSError, paramiko.SSHException) as e:
            raise NasError(f"連不到 NAS 的 SSH（{e}）。請確認 DSM 已開啟 SSH、IP 與埠正確") from None
        c.get_transport().set_keepalive(30)
        self.cli = c
        return self

    def ok(self) -> bool:
        return bool(self.cli and self.cli.get_transport() and self.cli.get_transport().is_active())

    def close(self):
        if self.cli:
            self.cli.close()
        self.cli = None

    def _ensure(self):
        if not self.ok():
            self.connect()

    def run(self, cmd: str, sudo=False, on_line=None, stdin: bytes | None = None, timeout=None) -> tuple[int, str]:
        """執行指令；on_line 會收到每一行輸出（已去掉顏色碼）。回傳 (結束代碼, 全部輸出)。"""
        self._ensure()
        env = "export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin:$PATH; "
        full = f"sudo -S -p '' bash -c {shq(env + cmd)}" if sudo else f"bash -c {shq(env + cmd)}"
        chan = self.cli.get_transport().open_session()
        chan.set_combine_stderr(True)
        if timeout:
            chan.settimeout(timeout)
        chan.exec_command(full)
        if sudo:
            chan.sendall((self.pw + "\n").encode())
            if stdin is None:
                chan.shutdown_write()
        if stdin is not None:
            view = memoryview(stdin)
            for i in range(0, len(view), 65536):
                chan.sendall(view[i:i + 65536])
            chan.shutdown_write()
        elif not sudo:
            chan.shutdown_write()
        out, buf = [], b""
        while True:
            data = chan.recv(4096)
            if not data:
                break
            buf += data
            while b"\n" in buf or b"\r" in buf:
                i = min(x for x in (buf.find(b"\n"), buf.find(b"\r")) if x >= 0)
                line, buf = buf[:i], buf[i + 1:]
                s = ANSI.sub("", line.decode("utf-8", "replace"))
                if s.strip():
                    out.append(s)
                    on_line and on_line(s)
        if buf.strip():
            s = ANSI.sub("", buf.decode("utf-8", "replace")); out.append(s); on_line and on_line(s)
        code = chan.recv_exit_status()
        text = "\n".join(out)
        if sudo and code != 0 and ("incorrect password" in text or "Sorry, try again" in text):
            raise NasError("sudo 密碼錯誤，或這個帳號不在 administrators 群組")
        return code, text

    # --- 容器
    def container_state(self) -> dict:
        fmt = "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.RestartCount}}|{{.State.StartedAt}}|{{.Config.Image}}"
        code, out = self.run(f"docker inspect --format '{fmt}' {shq(self.container)} 2>&1; echo '@@'; "
                             f"docker stats --no-stream --format '{{{{.CPUPerc}}}}|{{{{.MemUsage}}}}|{{{{.MemPerc}}}}' {shq(self.container)} 2>/dev/null",
                             sudo=True, timeout=40)
        first, _, stats = out.partition("@@")
        first = first.strip().splitlines()[-1] if first.strip() else ""
        if "No such" in first or "|" not in first:
            return {"exists": False, "status": "不存在", "raw": first}
        st, health, restarts, started, image = (first.split("|") + [""] * 5)[:5]
        cpu = mem = memp = ""
        if stats.strip():
            cpu, mem, memp = (stats.strip().splitlines()[-1].split("|") + ["", "", ""])[:3]
        return {"exists": True, "status": st, "health": health, "restarts": restarts, "started": started, "image": image,
                "cpu": cpu, "mem": mem, "mem_pct": memp}

    def compose(self, args: str, on_line=None):
        return self.run(f"cd {shq(self.path)} && (docker compose version >/dev/null 2>&1 && docker compose -p paperlib {args} || docker-compose -p paperlib {args})",
                        sudo=True, on_line=on_line)

    def restart(self, on_line=None): return self.run(f"docker restart {shq(self.container)}", sudo=True, on_line=on_line)
    def stop(self, on_line=None): return self.run(f"docker stop {shq(self.container)}", sudo=True, on_line=on_line)
    def start(self, on_line=None): return self.compose("up -d", on_line)
    def logs(self, tail=300): return self.run(f"docker logs --tail {int(tail)} {shq(self.container)} 2>&1", sudo=True, timeout=60)[1]
    def disk(self): return self.run(f"df -h {shq(self.path)} | tail -1")[1]

    def update(self, root: Path, clean=False, on_line=None) -> int:
        """上傳程式並執行 update.sh（停止舊容器 → 備份資料庫 → 換程式 → 重建 → 啟動 → 確認版本）。"""
        on_line = on_line or (lambda s: None)
        on_line("▶ 打包程式…")
        data = pack(root)
        on_line(f"  ✓ {len(data) / 1048576:.1f} MB")
        on_line("▶ 上傳到 NAS…")
        code, out = self.run("rm -rf /tmp/plu && mkdir -p /tmp/plu && tar -xzf - -C /tmp/plu && echo UPLOAD_OK", stdin=data)
        if code != 0 or "UPLOAD_OK" not in out:
            raise NasError(f"上傳失敗：{out[-400:]}")
        on_line("  ✓ 已上傳")
        code, _ = self.run(f"bash /tmp/plu/update.sh /tmp/plu {shq(self.path)}{' --clean' if clean else ''}", sudo=True, on_line=on_line)
        return code


def shq(s: str) -> str:
    return "'" + str(s).replace("'", "'\"'\"'") + "'"


def platform_name() -> str:
    return "windows" if sys.platform.startswith("win") else "mac" if sys.platform == "darwin" else "linux"


def install_desktop_shortcut(exe: str) -> str:
    """Windows：把 exe 複製到「本機應用程式」資料夾，並在桌面建立捷徑。回傳捷徑位置。"""
    import shutil
    import subprocess
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Programs" / "PaperlibConsole"
    base.mkdir(parents=True, exist_ok=True)
    target = base / "PaperlibConsole.exe"
    if Path(exe).resolve() != target.resolve():
        shutil.copy2(exe, target)
    ps = ("$d=[Environment]::GetFolderPath('Desktop');$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d '論文庫控制台.lnk'));"
          f"$s.TargetPath='{target}';$s.WorkingDirectory='{base}';$s.IconLocation='{target},0';$s.Description='LAB-QEL論文庫 控制台';$s.Save();"
          "Write-Output (Join-Path $d '論文庫控制台.lnk')")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "建立捷徑失敗")
    return r.stdout.strip()
