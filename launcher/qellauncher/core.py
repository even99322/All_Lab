"""大程式（桌面）的核心：安裝、更新、啟動各模塊（沒有畫面，方便測試）。

資料夾（``QEL_HOME``，預設 ``~/QELLab``）::

    modules/<模塊>/<版本>/      解壓縮後的模塊（含 module.json）
    modules/<模塊>/current.json {"version": 目前使用的版本}
    envs/<模塊>/                模塊自己的 Python 環境（venv），requirements 改變時才重裝
    downloads/                  下載中的 zip
    logs/<模塊>.log             模塊的輸出
    run/                        開著的模塊（labcomm 本機傳遞）
    session.json                登入狀態（各模塊共用）

每個模塊各自一個版本資料夾與環境：更新量測模塊不會動到讀檔模塊，反之亦然。
保留上一個版本，更新後打不開可以「換回上一版」。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import venv
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from labcomm import PortalClient
from labcomm.config import qel_home, save_session
from labcomm.errors import CommError

KEEP_VERSIONS = 2
Progress = Callable[[str, float], None]          # (訊息, 0–1)


def _child_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """子程序（Python、venv、pip）一律用 UTF-8：中文 Windows 預設 cp950，
    pip 讀到含「—」等字元的 requirements.txt 會 UnicodeDecodeError。"""
    env = dict(os.environ)
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PIP_NO_INPUT="1")
    env.update(extra or {})
    return env


def _run(cmd: List[str], timeout: float, **kw: Any) -> "subprocess.CompletedProcess[str]":
    """執行 Python 子程序並收集輸出（UTF-8、解不開的字元換掉；Windows 不跳出黑色視窗）。"""
    if sys.platform == "win32":
        kw.setdefault("creationflags", 0x08000000)          # CREATE_NO_WINDOW
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=_child_env(), **kw)


def _noop(msg: str, frac: float) -> None:
    pass


def parse_version(v: str):
    import re
    return tuple((0, int(p)) if p.isdigit() else (-1, p) for p in re.findall(r"\d+|[a-z]+", str(v).lower()))


# 打包成 exe／App 時，模塊用的 Python：自動下載可攜版 Python 3.12（python-build-standalone，免安裝、不需要系統管理員）
PORTABLE_TAG = "20241016"
PORTABLE_VERSION = "3.12.7"
PORTABLE_TARGETS = {("win32", "AMD64"): "x86_64-pc-windows-msvc", ("darwin", "arm64"): "aarch64-apple-darwin",
                    ("darwin", "x86_64"): "x86_64-apple-darwin", ("linux", "x86_64"): "x86_64-unknown-linux-gnu"}


def portable_python_url() -> Optional[str]:
    if os.environ.get("QEL_PYTHON_URL"):
        return os.environ["QEL_PYTHON_URL"]            # 沒有外網時：放在 NAS 上的同一個檔案
    import platform
    target = PORTABLE_TARGETS.get((sys.platform, platform.machine()))
    if target is None:
        return None
    return (f"https://github.com/astral-sh/python-build-standalone/releases/download/{PORTABLE_TAG}/"
            f"cpython-{PORTABLE_VERSION}+{PORTABLE_TAG}-{target}-install_only.tar.gz")


def portable_python_exe(home: Path) -> Path:
    base = home / "python"
    return base / "python.exe" if sys.platform == "win32" else base / "bin" / "python3"


OK_MARK = ".qel-ok"                       # 解壓縮並檢查過才寫；沒有它的 python 資料夾視為壞掉（例如裝到一半）
_PY_LOCK = threading.Lock()


class _FileLock:
    """跨程序的簡單鎖（同一台電腦同時開兩個大程式也不會一起裝）。超過 stale 秒的舊鎖視為殘留。"""

    def __init__(self, path: Path, timeout: float = 1800, stale: float = 1200) -> None:
        self.path, self.timeout, self.stale = path, timeout, stale

    def __enter__(self) -> "_FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale:
                        self.path.unlink()
                        continue
                except OSError:
                    continue
                if time.time() - t0 > self.timeout:
                    raise CommError("等太久：另一個大程式正在安裝 Python，請稍後再試") from None
                time.sleep(0.5)

    def __exit__(self, *exc) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


def _python_ok(home: Path) -> bool:
    return portable_python_exe(home).exists() and (home / "python" / OK_MARK).exists()


def ensure_portable_python(home: Path, progress: Progress = _noop) -> Path:
    """QEL_HOME/python 沒有（或壞掉）時下載解壓縮（約 30 MB，只有第一次）。

    同時有好幾個模塊在安裝時只會有一個在下載，其他的等它裝好直接用。
    """
    exe = portable_python_exe(home)
    if _python_ok(home):
        return exe
    with _PY_LOCK, _FileLock(home / "python.lock"):
        if _python_ok(home):                                   # 等鎖的時候別人已經裝好了
            return exe
        url = portable_python_url()
        if url is None:
            raise CommError("這種電腦沒有可攜版 Python，請安裝 Python 3.12（python.org）或設定 QEL_PYTHON")
        import tarfile
        import urllib.request
        tag = f"{os.getpid()}.{threading.get_ident()}"
        dl = home / "downloads" / f"python-portable.{tag}.tar.gz"
        tmp = home / f".python.{tag}.tmp"
        dl.parent.mkdir(parents=True, exist_ok=True)
        try:
            progress("下載 Python（只有第一次）", 0.1)
            try:
                with urllib.request.urlopen(url, timeout=60) as r, open(dl, "wb") as f:
                    total = int(r.headers.get("Content-Length") or 0)
                    done = 0
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        progress("下載 Python（只有第一次）", 0.1 + 0.4 * (done / total if total else 0))
            except OSError as e:
                raise CommError(f"下載 Python 失敗（{e}）。沒有外網時，請站長把 Python 檔放到 NAS 並設定 "
                                f"QEL_PYTHON_URL，或在這台電腦安裝 Python 3.12") from None
            progress("解壓縮 Python", 0.55)
            shutil.rmtree(tmp, ignore_errors=True)
            try:
                with tarfile.open(dl) as t:
                    root = tmp.resolve()
                    for m in t.getmembers():
                        target = (root / m.name).resolve()
                        if root not in target.parents and target != root:
                            raise CommError(f"Python 壓縮檔內容不安全：{m.name}")
                    t.extractall(tmp)
            except (tarfile.TarError, EOFError) as e:
                raise CommError(f"下載的 Python 檔不完整（{e}），請再試一次") from None
            new_exe = portable_python_exe(tmp)
            r = _run([str(new_exe), "-c", "import ensurepip, venv, ssl; print('ok')"], 120)
            if r.returncode != 0 or "ok" not in r.stdout:
                raise CommError(f"下載的 Python 無法執行：{(r.stderr or r.stdout).strip()[-300:]}")
            shutil.rmtree(home / "python", ignore_errors=True)       # 舊的（壞掉或裝到一半）換掉
            shutil.move(str(tmp / "python"), str(home / "python"))
            (home / "python" / OK_MARK).write_text(PORTABLE_VERSION, encoding="utf-8")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            if dl.exists():
                dl.unlink()
    if not exe.exists():
        raise CommError("Python 解壓縮後找不到執行檔")
    return exe


def _system_python(want: str) -> Optional[List[str]]:
    cands: List[List[str]] = []
    if sys.platform == "win32":
        if want:
            cands.append(["py", f"-{want}"])
        cands += [["py", "-3"], ["python"]]
    else:
        if want:
            cands.append([f"python{want}"])
        cands += [["/usr/local/bin/python3"], ["/opt/homebrew/bin/python3"], ["python3"]]
    for c in cands:
        try:
            r = _run(c + ["-c", "import sys; print(sys.version_info[:2] >= (3, 9))"], 20)
            if r.returncode == 0 and r.stdout.strip() == "True":
                return c
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def find_python(want: str = "", home: Optional[Path] = None, progress: Progress = _noop) -> List[str]:
    """模塊環境用的 Python。

    * 環境變數 QEL_PYTHON；
    * 沒有打包（用 .bat／.command 執行）：目前的 Python；
    * 打包成 exe／App：QEL_HOME/python 的可攜版（沒有就自動下載），下載不到才找系統上的 Python。
    """
    env = os.environ.get("QEL_PYTHON")
    if env:
        return [env]
    if not getattr(sys, "frozen", False):
        return [sys.executable]
    home = home or qel_home()
    try:
        return [str(ensure_portable_python(home, progress))]
    except CommError as e:
        sysp = _system_python(want)
        if sysp:
            return sysp
        raise e


class ModuleStore:
    def __init__(self, home: Optional[Path] = None) -> None:
        self.home = Path(home) if home else qel_home()
        for d in ("modules", "envs", "downloads", "logs", "run"):
            (self.home / d).mkdir(parents=True, exist_ok=True)

    # ---- 查詢 ------------------------------------------------------------------
    def mod_dir(self, mid: str) -> Path:
        return self.home / "modules" / mid

    def installed(self, mid: str) -> Optional[str]:
        try:
            v = json.loads((self.mod_dir(mid) / "current.json").read_text(encoding="utf-8"))["version"]
            return v if (self.mod_dir(mid) / v).is_dir() else None
        except (OSError, ValueError, KeyError):
            return None

    def versions(self, mid: str) -> List[str]:
        d = self.mod_dir(mid)
        if not d.exists():
            return []
        vs = [p.name for p in d.iterdir() if p.is_dir() and (p / "module.json").exists()]
        return sorted(vs, key=parse_version, reverse=True)

    def path(self, mid: str, version: Optional[str] = None) -> Optional[Path]:
        v = version or self.installed(mid)
        return self.mod_dir(mid) / v if v else None

    def manifest(self, mid: str, version: Optional[str] = None) -> Dict[str, Any]:
        p = self.path(mid, version)
        if p is None:
            return {}
        try:
            return json.loads((p / "module.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def set_current(self, mid: str, version: str) -> None:
        p = self.mod_dir(mid) / "current.json"
        p.write_text(json.dumps({"version": version, "time": time.time()}), encoding="utf-8")

    # ---- 安裝 ------------------------------------------------------------------
    def install_zip(self, mid: str, version: str, zip_path: Path, progress: Progress = _noop,
                    sha256: str = "") -> Path:
        if sha256:
            h = hashlib.sha256()
            with open(zip_path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != sha256:
                raise CommError("下載的檔案不完整（校驗碼不符），請再試一次")
        progress("解壓縮", 0.6)
        dst = self.mod_dir(mid) / version
        tmp = self.mod_dir(mid) / f".{version}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        root = tmp.resolve()
        with zipfile.ZipFile(zip_path) as z:
            for m in z.infolist():
                t = (root / m.filename).resolve()
                if root not in t.parents and t != root:
                    raise CommError(f"zip 內容不安全：{m.filename}")
            z.extractall(root)
        top = [p for p in root.iterdir() if p.name != "__MACOSX"]
        base = top[0] if len(top) == 1 and top[0].is_dir() and not (root / "module.json").exists() else root
        man_p = base / "module.json"
        if not man_p.exists():
            shutil.rmtree(tmp, ignore_errors=True)
            raise CommError("這個 zip 不是 QEL Lab 模塊（沒有 module.json）")
        man = json.loads(man_p.read_text(encoding="utf-8-sig"))
        if man.get("id") != mid or str(man.get("version")) != str(version):
            shutil.rmtree(tmp, ignore_errors=True)
            raise CommError(f"zip 內容是 {man.get('id')} v{man.get('version')}，不是 {mid} v{version}")
        shutil.rmtree(dst, ignore_errors=True)
        shutil.move(str(base), str(dst))
        shutil.rmtree(tmp, ignore_errors=True)
        return dst

    def prepare_env(self, mid: str, version: str, progress: Progress = _noop) -> Optional[Path]:
        """建立 / 更新模塊的 Python 環境；requirements 沒變就不重裝。函式庫（labcomm）不需要環境。"""
        man = self.manifest(mid, version)
        if man.get("kind") == "library" or not man.get("entry"):
            return None
        mdir = self.mod_dir(mid) / version
        req = mdir / man.get("requirements", "requirements.txt")
        env = self.home / "envs" / mid
        py = env_python(env)
        want = hashlib.sha256((req.read_bytes() if req.exists() else b"") + str(man.get("python", "")).encode()).hexdigest()
        stamp = env / ".qel-requirements"
        if py.exists() and stamp.exists() and stamp.read_text().strip() == want:
            return py
        progress("建立 Python 環境", 0.7)
        if not _env_healthy(py):                      # 沒有環境，或上次建到一半（有 python 沒有 pip）
            base = find_python(str(man.get("python", "")), self.home, progress)
            log = self.home / "logs" / f"{mid}-env.log"
            if base == [sys.executable] and not getattr(sys, "frozen", False):
                venv.EnvBuilder(with_pip=True, clear=True).create(str(env))
            else:
                r = _run(base + ["-m", "venv", "--clear", str(env)], 600)
                log.write_text(f"{base}\n{r.stdout}\n{r.stderr}", encoding="utf-8")
                if r.returncode != 0 or not _env_healthy(py):
                    shutil.rmtree(env, ignore_errors=True)  # 下次從頭建，不留半套環境
                    raise CommError(f"建立 {mid} 的 Python 環境失敗：{_tail(r.stderr or r.stdout)}"
                                    f"（完整訊息：{log}）")
        if req.exists() and req.read_text(encoding="utf-8").strip():
            progress("安裝套件（第一次會比較久）", 0.8)
            r = _run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req)], 3600,
                     cwd=str(mdir))
            (self.home / "logs" / f"{mid}-pip.log").write_text(r.stdout + "\n" + r.stderr, encoding="utf-8")
            if r.returncode != 0:
                raise CommError(f"安裝 {mid} 需要的套件失敗：{_tail(r.stderr or r.stdout)}"
                                f"（完整訊息：{self.home / 'logs' / f'{mid}-pip.log'}）")
        stamp.write_text(want)
        return py

    def install(self, client: PortalClient, mid: str, version: str, progress: Progress = _noop,
                sha256: str = "", with_env: bool = True) -> Path:
        z = self.home / "downloads" / f"{mid}_v{version}.{os.getpid()}.{threading.get_ident()}.zip"
        progress(f"下載 {mid} v{version}", 0.05)
        client.download_release(mid, version, z,
                                lambda done, total: progress("下載中", 0.05 + 0.5 * (done / total if total else 0)))
        try:
            dst = self.install_zip(mid, version, z, progress, sha256)
        finally:
            if z.exists():
                z.unlink()
        if with_env:
            self.prepare_env(mid, version, progress)
        self.set_current(mid, version)
        self.prune(mid)
        progress("完成", 1.0)
        return dst

    def prune(self, mid: str) -> None:
        cur = self.installed(mid)
        for v in self.versions(mid)[KEEP_VERSIONS:]:
            if v != cur:
                shutil.rmtree(self.mod_dir(mid) / v, ignore_errors=True)

    def rollback(self, mid: str) -> Optional[str]:
        cur = self.installed(mid)
        older = [v for v in self.versions(mid) if v != cur and (cur is None or parse_version(v) < parse_version(cur))]
        if not older:
            return None
        self.set_current(mid, older[0])
        return older[0]

    def uninstall(self, mid: str) -> None:
        shutil.rmtree(self.mod_dir(mid), ignore_errors=True)
        shutil.rmtree(self.home / "envs" / mid, ignore_errors=True)


def _env_healthy(py: Path) -> bool:
    if not py.exists():
        return False
    try:
        r = _run([str(py), "-c", "import pip"], 60)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _tail(text: str, n: int = 300) -> str:
    lines = [x for x in (text or "").strip().splitlines() if x.strip()]
    return " / ".join(lines[-3:])[-n:] or "（沒有訊息）"


def env_python(env: Path) -> Path:
    return env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


class Launcher:
    """登入、檢查更新、啟動模塊。畫面（window.py）與測試都用這個。"""

    def __init__(self, client: PortalClient, store: Optional[ModuleStore] = None) -> None:
        self.client = client
        self.store = store or ModuleStore()
        self.user: Optional[Dict[str, Any]] = None
        self.modules: List[Dict[str, Any]] = []
        self.procs: Dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()
        self._install_locks: Dict[str, threading.Lock] = {}

    def _install_lock(self, mid: str) -> threading.Lock:
        """同一個模塊一次只裝一個（背景自動更新和使用者按的不會撞在一起）。"""
        with self._lock:
            return self._install_locks.setdefault(mid, threading.Lock())

    def login(self, username: str, password: str, code: str = "") -> Dict[str, Any]:
        r = self.client.login(username, password, code)
        if r.get("need_2fa"):
            return r
        self.user = r["user"]
        save_session(self.client.urls, self.client.token, self.user)
        return r

    def resume(self) -> bool:
        if not self.client.token:
            return False
        try:
            self.user = self.client.me()
        except CommError:
            return False
        save_session(self.client.urls, self.client.token, self.user)
        return True

    def logout(self) -> None:
        from labcomm.config import clear_session
        try:
            self.client.logout()
        except CommError:
            pass
        clear_session()
        self.user = None

    def refresh(self) -> List[Dict[str, Any]]:
        mods = self.client.modules()
        for m in mods:
            m["installed"] = self.store.installed(m["id"])
            m["update"] = bool(m.get("latest") and m["installed"] and
                               parse_version(m["latest"]) > parse_version(m["installed"]))
            m["running"] = self.running(m["id"])
        self.modules = mods
        return mods

    def release(self, mid: str, version: str) -> Dict[str, Any]:
        for r in self.client.releases(mid):
            if r["version"] == version:
                return r
        raise CommError(f"大程式上沒有 {mid} v{version}")

    def install_latest(self, mid: str, progress: Progress = _noop) -> str:
        m = next((x for x in (self.modules or self.refresh()) if x["id"] == mid), None)
        if m is not None and not m.get("allowed", True):
            raise CommError("站長還沒有開放這個模塊給你")
        if m is None or not m.get("latest"):
            raise CommError(f"{mid} 還沒有發佈版本")
        with self._install_lock(mid):
            if self.store.installed(mid) != m["latest"]:
                rel = self.release(mid, m["latest"])
                self.store.install(self.client, mid, m["latest"], progress, rel.get("sha256", ""))
        return m["latest"]

    def ensure_labcomm(self, progress: Progress = _noop) -> Optional[str]:
        """通信模塊自動安裝與更新（不需要使用者按）。"""
        m = next((x for x in (self.modules or self.refresh()) if x["id"] == "labcomm"), None)
        if not m or not m.get("latest"):
            return self.store.installed("labcomm")
        with self._install_lock("labcomm"):
            cur = self.store.installed("labcomm")           # 等鎖的時候別人可能已經裝好了
            if cur is None or parse_version(m["latest"]) > parse_version(cur):
                self.store.install(self.client, "labcomm", m["latest"], progress, with_env=False)
        return self.store.installed("labcomm")

    # ---- 啟動 ------------------------------------------------------------------
    def running(self, mid: str) -> bool:
        p = self.procs.get(mid)
        return p is not None and p.poll() is None

    def command(self, mid: str, extra: Optional[List[str]] = None) -> Dict[str, Any]:
        """組出啟動指令與環境變數（測試會直接檢查）。"""
        version = self.store.installed(mid)
        if version is None:
            raise CommError(f"{mid} 還沒有安裝")
        man = self.store.manifest(mid, version)
        mdir = self.store.mod_dir(mid) / version
        entry = man.get("entry") or "main.py"
        py = env_python(self.store.home / "envs" / mid)
        if not py.exists():
            py = Path(find_python(home=self.store.home)[0])
        env = dict(os.environ)
        paths = []
        lc = self.store.path("labcomm")
        if lc is not None:
            paths.append(str(lc))                   # 模塊 zip 根目錄就是 labcomm/ 的上一層
        else:                                       # 還沒安裝通信模塊：用大程式自己帶的這份
            import labcomm
            paths.append(str(Path(labcomm.__file__).resolve().parent.parent))
        if env.get("PYTHONPATH"):
            paths.append(env["PYTHONPATH"])
        env.update(QEL_HOME=str(self.store.home), QEL_PORTAL_URL=", ".join(self.client.urls),
                   QEL_TOKEN=self.client.token, QEL_MODULE_ID=mid, QEL_MODULE_VERSION=version,
                   PYTHONPATH=os.pathsep.join(paths), PYTHONIOENCODING="utf-8")
        return {"cmd": [str(py), str(mdir / entry), *(man.get("args") or []), *(extra or [])], "cwd": str(mdir),
                "env": env}

    def launch(self, mid: str, extra: Optional[List[str]] = None) -> subprocess.Popen:
        if self.store.installed("labcomm") is None and self.user is not None:
            try:
                self.ensure_labcomm()               # 第一次：先裝通信模塊（裝不到就用大程式自己帶的）
            except (CommError, OSError):
                pass
        with self._lock:
            if self.running(mid):
                return self.procs[mid]
            c = self.command(mid, extra)
            log = open(self.store.home / "logs" / f"{mid}.log", "ab")
            log.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} 開啟 {mid} ====\n".encode("utf-8"))
            log.flush()
            kw: Dict[str, Any] = {}
            if sys.platform == "win32":
                kw["creationflags"] = 0x08000000           # CREATE_NO_WINDOW（不跳出黑色視窗）
            p = subprocess.Popen(c["cmd"], cwd=c["cwd"], env=c["env"], stdout=log, stderr=subprocess.STDOUT, **kw)
            log.close()
            self.procs[mid] = p
            return p

    def deliver_after_launch(self, mid: str, action: str, payload: Dict[str, Any], timeout: float = 90) -> None:
        """開啟模塊後，等它的本機傳遞埠準備好再把動作送過去。"""
        from labcomm import local
        self.launch(mid)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if local.is_running(mid):
                local.send(mid, action, payload, sender="launcher")
                return
            if not self.running(mid):
                raise CommError(f"{mid} 開啟後馬上結束了，請看 {self.store.home / 'logs' / f'{mid}.log'}")
            time.sleep(0.5)
        raise CommError(f"{mid} 開太久，動作沒有送出")
