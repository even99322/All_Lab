"""打包版（Windows .exe / macOS .app）的程式內版本檢查與更新（0.0.26 起；0.0.27 加上功能更新包與推播）。

    c = check()                      # 問 Hub /get/info：目前版本、Hub 最新版本、這台電腦該怎麼更新
    c["plan"]                        # "code"      功能更新包：只下載 Python zip（約 1 MB），幾秒後重開
                                     # "installer" 整個程式：.exe / .dmg（執行環境不同，例如加了新套件）
                                     # "python"    用 Python 執行（開發 / Lab APP）：只提示
                                     # "wait"      Hub 有新版的功能更新包，但執行環境不同、還沒有對應的安裝檔
                                     # None        已經是最新版
    apply(c, progress)               # 下載 → 換上 → 用同樣的參數重開；呼叫端接著結束自己
    watch(version, wait=60)          # 推播：長輪詢 /get/watch，Hub 一發佈新版就回應

功能更新包解壓到 ~/.qel/labcontrol/code/<版本>/，由 main.py 在下次開啟時放到打包內容前面（見 main.py）。
開啟成功後呼叫 mark_good()；新版開不起來時 main.py 會自動退回上一個可用的版本。
程式自己下載，不經過瀏覽器，所以不會再出現「不常下載」或 SmartScreen 的警告。
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import __version__

log = logging.getLogger(__name__)
APP_ID = "labcontrol"
FROZEN = bool(getattr(sys, "frozen", False))


def parse_version(v: str):
    from .remote.versioning import parse_version as pv

    return pv(str(v or "0"))


def this_platform() -> str:
    return "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"


def this_arch() -> str:
    m = platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "x86_64"


def current_executable() -> Path:
    return Path(sys.executable).resolve()


def current_bundle() -> Optional[Path]:
    """macOS：目前執行的 .app（…/Lab Control.app/Contents/MacOS/LabControl）。"""
    exe = current_executable()
    for p in exe.parents:
        if p.suffix == ".app":
            return p
    return None


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))      # 實驗室內網 / VPN 直連


def pick(downloads: List[Dict[str, Any]], plat: Optional[str] = None, arch: Optional[str] = None) -> Optional[Dict]:
    """從 /get/info 的 downloads 選這台電腦用的（Mac：同晶片 > universal > 另一種晶片；新版本優先）。"""
    plat, arch = plat or this_platform(), arch or this_arch()
    mine = [f for f in downloads if f.get("platform") == plat]
    if not mine:
        return None

    mine.sort(key=lambda f: parse_version(f.get("version", "0")), reverse=True)          # 新版本優先
    mine.sort(key=lambda f: 0 if (f.get("arch") or "") == arch else 1 if (f.get("arch") or "") in ("universal2", "")
              else 2)                                                                     # 再依晶片（穩定排序）
    return mine[0]


def latest(timeout: float = 6.0, bases: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Hub 上這台電腦能用的最新 Lab Control 安裝檔：{version, url（完整網址）, size, name, notes, hub}；沒有 → None。"""
    if bases is None:
        from .remote.hub import hub_urls

        bases = hub_urls()
    last: Optional[Exception] = None
    for base in bases:
        base = base.rstrip("/")
        try:
            with _opener().open(base + "/get/info", timeout=timeout) as r:
                info = json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - 試下一個網址（內網 / VPN）
            last = e
            continue
        app = next((a for a in info.get("apps") or [] if a.get("id") == APP_ID), None)
        f = pick((app or {}).get("downloads") or [])
        if f is None:
            return None
        return dict(f, url=base + f["url"], hub=base)
    if last is not None:
        log.info("檢查新版失敗：%s", last)
    return None


def is_newer(info: Optional[Dict[str, Any]], current: str = __version__) -> bool:
    return bool(info) and parse_version(info.get("version", "0")) > parse_version(current)


# ---- 執行環境與功能更新包 ------------------------------------------------------------------------
def requirements_fingerprint(text: str) -> str:
    """與 Hub（labhub/appupdates.py）相同的算法：去掉註解、空白、大小寫，排序後的 sha256 前 16 碼。"""
    import hashlib

    lines = []
    for line in str(text or "").splitlines():
        line = line.split("#", 1)[0].strip().lower().replace(" ", "")
        if line:
            lines.append(line)
    return hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()[:16]


def runtime() -> Dict[str, str]:
    """這個程式的執行環境：打包時寫進去的 runtime.json；用 Python 執行時從 requirements.txt 算。"""
    if FROZEN:
        try:
            return json.loads((Path(getattr(sys, "_MEIPASS", "")) / "runtime.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    req = Path(__file__).resolve().parents[1] / "requirements.txt"
    text = req.read_text(encoding="utf-8") if req.exists() else ""
    return {"version": __version__, "requirements": requirements_fingerprint(text),
            "python": f"{sys.version_info[0]}.{sys.version_info[1]}"}


def base_version() -> str:
    """.exe 內附的版本（功能更新包之前的版本）。"""
    return os.environ.get("LABCONTROL_BASE_VERSION") or str(runtime().get("version") or __version__)


def code_dir() -> Path:
    return Path(os.environ.get("LABCONTROL_CODE_DIR") or (Path.home() / ".qel" / "labcontrol" / "code"))


def _state() -> Dict[str, Any]:
    try:
        return json.loads((code_dir() / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(st: Dict[str, Any]) -> None:
    p = code_dir() / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st), encoding="utf-8")
    tmp.replace(p)


def mark_good() -> None:
    """開啟成功（主視窗出現）：這個功能更新包可以用，下次開啟繼續用它。"""
    v = os.environ.get("LABCONTROL_CODE_VERSION")
    if not v:
        return
    try:
        st = _state()
        if st.get("good") != v or st.get("trial"):
            st.update(good=v, trial=None)
            _save_state(st)
    except OSError:
        pass


def running() -> Dict[str, Any]:
    """版本檢查視窗用：目前的版本、.exe 內附的版本、功能更新包的位置。"""
    return {"version": __version__, "base": base_version(), "frozen": FROZEN,
            "code_root": os.environ.get("LABCONTROL_CODE_ROOT", ""), "platform": this_platform(),
            "arch": this_arch(), "requirements": runtime().get("requirements", "")}


def check(timeout: float = 6.0, bases: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """問 Hub：{hub, app, version（Hub 最新）, notes, installer, code, plan}；連不到 Hub → None。"""
    if bases is None:
        from .remote.hub import hub_urls

        bases = hub_urls()
    last: Optional[Exception] = None
    for base in bases:
        base = base.rstrip("/")
        try:
            with _opener().open(base + "/get/info", timeout=timeout) as r:
                info = json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - 試下一個網址（內網 / VPN）
            last = e
            continue
        app = next((a for a in info.get("apps") or [] if a.get("id") == APP_ID), None) or {}
        return plan_for(app, base)
    if last is not None:
        log.info("檢查新版失敗：%s", last)
    return None


def plan_for(app: Dict[str, Any], base: str, current: str = __version__) -> Dict[str, Any]:
    inst = pick(app.get("downloads") or [])
    code = app.get("code")
    if inst:
        inst = dict(inst, url=base + inst["url"], hub=base)
    if code:
        code = dict(code, url=base + code["url"], hub=base)
    rt = runtime()
    plan = None
    target = None
    if code and is_newer(code, current) and FROZEN and code.get("requirements") == rt.get("requirements"):
        plan, target = "code", code
    elif inst and is_newer(inst, current) and FROZEN:
        plan, target = "installer", inst
    elif (code and is_newer(code, current)) or (inst and is_newer(inst, current)):
        if not FROZEN:
            plan = "python"
        else:
            plan = "wait"                       # 有新版功能，但執行環境不同、這個作業系統還沒有新的安裝檔
        target = max([x for x in (code, inst) if x and is_newer(x, current)],
                     key=lambda x: parse_version(x["version"]))
    return {"hub": base, "app": app, "version": app.get("version"), "installer": inst, "code": code,
            "plan": plan, "target": target, "notes": (target or {}).get("notes") or app.get("notes") or "",
            "current": current}


def watch(version: str = __version__, wait: float = 60.0, bases: Optional[List[str]] = None) -> Optional[Dict]:
    """推播（長輪詢）：Hub 一發佈比 version 新的 Lab Control 就回傳 check() 的結果；逾時 / 連不到 → None。"""
    if bases is None:
        from .remote.hub import hub_urls

        bases = hub_urls()
    for base in bases:
        base = base.rstrip("/")
        url = f"{base}/get/watch?app={APP_ID}&version={urllib.parse.quote(version)}&wait={int(wait)}"
        try:
            with _opener().open(url, timeout=wait + 15) as r:
                res = json.loads(r.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if res.get("changed"):
            return plan_for(res.get("app") or {}, base, version)
        return None
    raise ConnectionError("連不到 Hub")


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def apply_code(code: Dict[str, Any], progress: Optional[Callable[[int, int], None]] = None) -> Path:
    """下載功能更新包（Python zip），檢查 sha256 與內容，解壓到 code_dir()/<版本>/。回傳那個資料夾。"""
    import zipfile

    ver = str(code["version"])
    tmpdir = Path(tempfile.mkdtemp(prefix="labcontrol_code_"))
    zpath = _fetch(dict(code), tmpdir, progress)
    if code.get("sha256") and _sha256(zpath) != code["sha256"]:
        raise RuntimeError("功能更新包下載後檢查碼不符（請再試一次）")
    dest = code_dir() / ver
    part = code_dir() / f".{ver}.part"
    if part.exists():
        shutil.rmtree(part, ignore_errors=True)
    part.mkdir(parents=True)
    with zipfile.ZipFile(zpath) as z:
        names = [n for n in z.namelist() if not n.startswith("__MACOSX/")]
        tops = {n.split("/", 1)[0] for n in names}
        prefix = (next(iter(tops)) + "/") if len(tops) == 1 and all("/" in n for n in names) else ""   # LabControl/…
        root = part.resolve()
        for info in z.infolist():
            name = info.filename
            if name.startswith("__MACOSX/") or not name.startswith(prefix):
                continue
            rel = name[len(prefix):]
            if not rel:
                continue
            target = (part / rel).resolve()
            if root not in target.parents and target != root:
                raise RuntimeError(f"功能更新包內容不安全：{name}")
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
    init = part / "labcontrol" / "__init__.py"
    if not init.exists():
        shutil.rmtree(part, ignore_errors=True)
        raise RuntimeError("功能更新包裡沒有 labcontrol/")
    import re

    m = re.search(r"__version__\s*=\s*[\"']([^\"']+)", init.read_text(encoding="utf-8"))
    if not m or m.group(1) != ver:
        shutil.rmtree(part, ignore_errors=True)
        raise RuntimeError(f"功能更新包的版本（{m.group(1) if m else '?'}）與 Hub 記錄的 {ver} 不同")
    req = part / "requirements.txt"
    fp = requirements_fingerprint(req.read_text(encoding="utf-8")) if req.exists() else ""
    if FROZEN and fp != runtime().get("requirements"):
        shutil.rmtree(part, ignore_errors=True)
        raise RuntimeError("這個功能更新包需要不同的套件：請改用新的安裝檔更新")
    (part / ".complete").write_text(json.dumps({"version": ver, "requirements": fp, "time": time.time()}),
                                    encoding="utf-8")
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    part.rename(dest)
    shutil.rmtree(tmpdir, ignore_errors=True)
    _prune(keep=3)
    return dest


def _prune(keep: int = 3) -> None:
    """只留最新的幾個功能更新包（正在用的、上一個可用的一定留著）。"""
    st = _state()
    protect = {st.get("good"), os.environ.get("LABCONTROL_CODE_VERSION")}
    dirs = sorted((d for d in code_dir().iterdir() if d.is_dir() and not d.name.startswith(".")),
                  key=lambda d: parse_version(d.name), reverse=True)
    for d in dirs[keep:]:
        if d.name not in protect:
            shutil.rmtree(d, ignore_errors=True)


def apply(c: Dict[str, Any], progress: Optional[Callable[[int, int], None]] = None,
          args: Optional[List[str]] = None) -> str:
    """依 check() 的 plan 更新並重開；回傳做了什麼（"code" / "installer"）。呼叫端接著結束自己。"""
    plan = c.get("plan")
    if plan == "code":
        apply_code(c["code"], progress)
        relaunch(current_executable(), args)
        return "code"
    if plan == "installer":
        update_to(c["installer"], progress, args)
        return "installer"
    if plan == "python":
        raise RuntimeError("用 Python 執行的 Lab Control 不能自己更新（請用打包版，或 Lab APP / git 更新）")
    if plan == "wait":
        raise RuntimeError("Hub 上的新版需要新的執行環境，站長還沒有上傳這個作業系統的安裝檔")
    raise RuntimeError("已經是最新版")


def download(info: Dict[str, Any], progress: Optional[Callable[[int, int], None]] = None) -> Path:
    """下載安裝檔到：Windows → 執行檔旁邊（同一個磁碟才能直接換）；macOS → 暫存資料夾。檢查大小與 sha256。"""
    if this_platform() == "windows" and FROZEN:
        folder = current_executable().parent
    else:
        folder = Path(tempfile.mkdtemp(prefix="labcontrol_update_"))
    path = _fetch(info, folder, progress)
    if info.get("sha256") and _sha256(path) != info["sha256"]:
        path.unlink(missing_ok=True)
        raise RuntimeError("下載後檢查碼不符（請再試一次）")
    return path


def _fetch(info: Dict[str, Any], folder: Path, progress: Optional[Callable[[int, int], None]] = None) -> Path:
    dest = folder / (Path(urllib.parse.unquote(info["name"])).name + ".download")
    expected = int(info.get("size") or 0)
    done = 0
    with _opener().open(info["url"], timeout=60) as r, dest.open("wb") as f:
        total = expected or int(r.headers.get("Content-Length") or 0)
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress is not None:
                progress(done, total)
    if total and dest.stat().st_size != total:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"下載不完整（{dest.stat().st_size if dest.exists() else 0} / {total} bytes）")
    final = dest.with_name(dest.name[: -len(".download")])
    if final.exists():
        final.unlink()
    dest.rename(final)
    return final


def install(path: Path) -> Path:
    """把下載的新版換上去，回傳新版的執行檔（給 relaunch）。失敗丟例外，目前的程式不受影響。"""
    if this_platform() == "windows":
        return _install_windows(Path(path))
    if this_platform() == "macos":
        return _install_macos(Path(path))
    raise RuntimeError("這個作業系統沒有打包版更新")


def _install_windows(new: Path) -> Path:
    """執行中的 .exe 不能覆寫但可以改名：舊檔 → *.old.exe，新檔放到原本的位置（捷徑、開機啟動都不用改）。"""
    cur = current_executable()
    old = cur.with_name(cur.stem + ".old.exe")
    if old.exists():
        try:
            old.unlink()
        except OSError:
            old = cur.with_name(f"{cur.stem}.old{int(time.time())}.exe")
    cur.rename(old)
    try:
        shutil.move(str(new), str(cur))
    except OSError:
        old.rename(cur)
        raise
    return cur


def _install_macos(dmg: Path) -> Path:
    """掛載 .dmg（不顯示在 Finder），把裡面的 .app 換到目前 .app 的位置；舊的先改名，失敗就換回來。"""
    bundle = current_bundle()
    if bundle is None:
        raise RuntimeError("找不到目前的 .app")
    mnt = Path(tempfile.mkdtemp(prefix="labcontrol_dmg_"))
    subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", str(mnt), str(dmg)],
                   check=True, capture_output=True)
    try:
        apps = sorted(mnt.glob("*.app"))
        if not apps:
            raise RuntimeError("安裝檔裡沒有 .app")
        old = bundle.with_name(bundle.name + ".old")
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        bundle.rename(old)
        try:
            subprocess.run(["ditto", str(apps[0]), str(bundle)], check=True, capture_output=True)
        except Exception:
            if bundle.exists():
                shutil.rmtree(bundle, ignore_errors=True)
            old.rename(bundle)
            raise
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(bundle)], capture_output=True)
    finally:
        subprocess.run(["hdiutil", "detach", str(mnt), "-quiet"], capture_output=True)
        try:
            dmg.unlink()
        except OSError:
            pass
    return bundle / "Contents" / "MacOS" / current_executable().name


def relaunch(exe: Path, args: Optional[List[str]] = None) -> None:
    """用同樣的命令列參數開新版（脫離目前的程序）；呼叫端接著結束自己。"""
    args = list(sys.argv[1:] if args is None else args)
    kw: Dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                          "close_fds": True, "cwd": str(Path(exe).parent)}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    env = dict(os.environ)
    env.pop("_MEIPASS2", None)                                  # PyInstaller onefile：讓新版自己解壓
    env.pop("_PYI_APPLICATION_HOME_DIR", None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen([str(exe), *args], env=env, **kw)


def update_to(info: Dict[str, Any], progress: Optional[Callable[[int, int], None]] = None,
              args: Optional[List[str]] = None) -> Path:
    """下載 → 換上 → 開新版。回傳新版執行檔；呼叫端接著結束自己。"""
    if not FROZEN:
        raise RuntimeError("用 Python 執行的 Lab Control 不能自己更新（請用打包版，或 Lab APP / git 更新）")
    path = download(info, progress)
    exe = install(path)
    relaunch(exe, args)
    return exe


def cleanup_after_update() -> None:
    """上次更新留下的舊版（那時還在執行，不能刪）。"""
    if not FROZEN:
        return
    try:
        if this_platform() == "windows":
            cur = current_executable()
            for old in cur.parent.glob(cur.stem + ".old*.exe"):
                try:
                    old.unlink()
                except OSError:
                    pass
        elif this_platform() == "macos":
            b = current_bundle()
            if b is not None and b.with_name(b.name + ".old").exists():
                shutil.rmtree(b.with_name(b.name + ".old"), ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass
