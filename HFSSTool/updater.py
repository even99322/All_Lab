"""自動更新（v3.3）：跟量測程式一樣向實驗室平台（Hub）問新版。

    開啟 → GET <Hub>/get/info → apps 裡 id = "hfss" 的版本與 Windows exe
         → 比目前新 → 主視窗右下角通知、狀態列「⬆ 更新到 x」
    之後一直掛著 GET <Hub>/get/watch?app=hfss&version=<目前>&wait=60（長輪詢）：站長一發佈，幾秒內就跳通知
    更新 → 下載新的 exe（核對 sha256）→ 關掉程式 → 換掉同一個檔（捷徑、工作列釘選照用）→ 重開
    v3.6 起發佈的是安裝程式（HFSSTool_v<版本>_windows-setup.exe）：下載後用 /SILENT 安裝（只有進度條）→ 重開裝好的程式。
    3.5 以前的單一 exe 更新到 3.6 時，舊版會把安裝程式換到舊 exe 的位置執行（一般安裝畫面），裝好後安裝程式自己刪掉那個檔。

Hub 位址：環境變數 QEL_HUB（逗號分隔）→ %USERPROFILE%\\LAB\\HFSS\\update.json 的 hub_url → 實驗室內網、VPN。
這裡不用 Qt，介面在 update_ui.py。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional

from config import APP_VERSION, SETTINGS_DIR

APP_ID = "hfss"
DEFAULT_HUBS = ("http://192.168.50.2:8765", "http://100.114.33.20:8765")
CONF_FILE = os.path.join(SETTINGS_DIR, "update.json")


class UpdateError(RuntimeError):
    pass


def current_version() -> str:
    return APP_VERSION.lstrip("vV")


def vkey(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or "0"))) or (0,)


# ---- 設定 ---------------------------------------------------------------------------------------
def load_conf() -> Dict:
    try:
        with open(CONF_FILE, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def save_conf(**kw) -> None:
    d = load_conf()
    d.update(kw)
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(CONF_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def hubs() -> List[str]:
    out: List[str] = []
    for src in (os.environ.get("QEL_HUB", ""), load_conf().get("hub_url", "")):
        out += [u.strip().rstrip("/") for u in str(src).split(",") if u.strip()]
    out += list(DEFAULT_HUBS)
    seen, res = set(), []
    for u in out:
        if not re.match(r"^https?://", u):
            u = "http://" + u
        if u not in seen:
            seen.add(u)
            res.append(u)
    return res


# ---- 問 Hub ------------------------------------------------------------------------------------
def _get_json(url: str, timeout: float) -> Dict:
    req = urllib.request.Request(url, headers={"User-Agent": f"HFSSTool/{current_version()}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 實驗室內網：不走系統代理
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def find_app(info: Dict) -> Optional[Dict]:
    return next((a for a in info.get("apps") or [] if a.get("id") == APP_ID), None)


def check(timeout: float = 4.0, hub_list: Optional[List[str]] = None) -> Optional[Dict]:
    """→ {"hub", "app"}（app = Hub 的版本資訊；Hub 上還沒有這個程式時 app = None）；全部連不到 → None。"""
    for hub in hub_list or hubs():
        try:
            info = _get_json(f"{hub}/get/info", timeout)
        except (OSError, ValueError, urllib.error.URLError):
            continue
        return {"hub": hub, "app": find_app(info)}
    return None


def watch(hub: str, version: str, wait: float = 60.0) -> Dict:
    """長輪詢：Hub 一發佈比 version 新的就回來（{changed, app}）。"""
    q = urllib.parse.urlencode({"app": APP_ID, "version": version, "wait": int(wait)})
    return _get_json(f"{hub}/get/watch?{q}", wait + 15)


def is_newer(app: Optional[Dict]) -> bool:
    return bool(app and app.get("version") and vkey(app["version"]) > vkey(current_version()))


def is_setup(name: str) -> bool:
    n = os.path.basename(str(name or "")).lower()
    return n.endswith(".exe") and "setup" in n


def pick_file(app: Dict, hub: str) -> Optional[Dict]:
    """這台電腦要下載的檔：Windows exe（最新的那個；同一版有安裝程式就用安裝程式）。"""
    files = [f for f in app.get("downloads") or [] if f.get("platform") == "windows"]
    if not files:
        return None
    f = max(files, key=lambda x: (vkey(x.get("version")), is_setup(x.get("name"))))
    url = f.get("url", "")
    if url.startswith("/"):
        url = hub + url
    return dict(f, url=url)


# ---- 下載 / 換檔 ---------------------------------------------------------------------------------
def can_self_update() -> bool:
    """打包好的 Windows exe 才能自己換新；Python 原始碼版要自己下載。"""
    return bool(getattr(sys, "frozen", False)) and os.name == "nt"


def target_exe() -> str:
    return os.path.abspath(sys.executable)


def is_onefile() -> bool:
    """PyInstaller 單一 exe：程式解壓在暫存資料夾（不在 exe 旁邊），外面還有一個解壓用的父程序。"""
    mei = getattr(sys, "_MEIPASS", "")
    if not getattr(sys, "frozen", False) or not mei:
        return False
    exe_dir = os.path.normcase(os.path.dirname(target_exe()))
    return not os.path.normcase(os.path.abspath(mei)).startswith(exe_dir)


def installed_dir() -> Optional[str]:
    """用安裝程式裝的（exe 旁邊有 unins000.exe）→ 安裝資料夾；單一 exe / 原始碼版 → None。"""
    if not getattr(sys, "frozen", False):
        return None
    d = os.path.dirname(target_exe())
    return d if os.path.isfile(os.path.join(d, "unins000.exe")) else None


def default_install_exe() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "Programs", "HFSSTool", "HFSSTool.exe")


def setup_script(setup_exe: str, app_exe: str, pids: List[int], old_version: str) -> str:
    """PowerShell：等程式結束 → 安裝程式 /SILENT（只有進度條）→ 重開裝好的程式 → 刪掉下載的安裝程式。"""
    return "\n".join([
        "$ErrorActionPreference = 'SilentlyContinue'",
        "Start-Transcript -Path (Join-Path $env:TEMP 'hfss_update.log') -Force | Out-Null",
        f"foreach ($p in @({', '.join(str(int(p)) for p in pids)})) {{ Wait-Process -Id $p -Timeout 60 }}",
        f"$setup = {_ps_quote(setup_exe)}; $app = {_ps_quote(app_exe)}",
        "Write-Output \"installing $setup\"",
        "$p = Start-Process -FilePath $setup -ArgumentList '/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "
        "'/CLOSEAPPLICATIONS', ('/LOG=\"' + (Join-Path $env:TEMP 'hfss_setup.log') + '\"') -Wait -PassThru",
        "Write-Output \"setup exit $($p.ExitCode)\"",
        f"if (Test-Path -LiteralPath $app) {{ Start-Process -FilePath $app -ArgumentList '--updated-from', {_ps_quote(old_version)} }}",
        "Start-Sleep -Seconds 3",
        "Remove-Item -LiteralPath $setup -Force",
        "Stop-Transcript | Out-Null",
    ])


def download(url: str, dst: str, sha256: str = "", progress: Optional[Callable[[int, int], None]] = None,
             cancelled: Optional[Callable[[], bool]] = None) -> str:
    tmp = dst + ".part"
    h = hashlib.sha256()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url, headers={"User-Agent": "HFSSTool"}), timeout=30) as r, \
                open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                if cancelled and cancelled():
                    raise UpdateError("已取消")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)
        if total and got != total:
            raise UpdateError(f"下載不完整（{got:,} / {total:,} bytes）")
        if sha256 and h.hexdigest().lower() != sha256.lower():
            raise UpdateError("下載的檔案跟平台上的不一樣（sha256 不符），請再試一次")
        os.replace(tmp, dst)
    except (OSError, urllib.error.URLError) as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise UpdateError(f"下載失敗：{getattr(e, 'reason', e)}") from None
    except UpdateError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return dst


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def install_script(new_exe: str, target: str, pids: List[int], old_version: str) -> str:
    """PowerShell：等這個程式（含單一 exe 的外層程序）結束 → 舊檔改名 → 新檔換上 → 重開 → 刪舊檔。"""
    return "\n".join([
        "$ErrorActionPreference = 'SilentlyContinue'",
        f"foreach ($p in @({', '.join(str(int(p)) for p in pids)})) {{ Wait-Process -Id $p -Timeout 120 }}",
        f"$target = {_ps_quote(target)}; $new = {_ps_quote(new_exe)}; $old = $target + '.old'",
        "for ($i = 0; $i -lt 120; $i++) {",
        "  try { if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Force -ErrorAction Stop }",
        "        Move-Item -LiteralPath $target -Destination $old -Force -ErrorAction Stop; break }",
        "  catch { Start-Sleep -Milliseconds 500 } }",
        "try { Move-Item -LiteralPath $new -Destination $target -Force -ErrorAction Stop }",
        "catch { Move-Item -LiteralPath $old -Destination $target -Force }",
        f"Start-Process -FilePath $target -ArgumentList '--updated-from', {_ps_quote(old_version)}",
        "Start-Sleep -Seconds 8",
        "Remove-Item -LiteralPath $old -Force",
    ])


def install(new_exe: str) -> None:
    """在背景啟動換檔腳本；呼叫的人接著要關掉程式。"""
    if not can_self_update():
        raise UpdateError("只有打包好的 Windows 版能自己更新")
    pids = [os.getpid()]
    if is_onefile():
        try:
            pids.append(os.getppid())      # 單一 exe：外層解壓程序
        except OSError:
            pass
    # v3.6.2：安裝版（資料夾版）只有一個程序；3.6 也等了父程序（= 檔案總管，永遠不會結束）→ 要等 2 分鐘逾時才開始安裝
    if is_setup(new_exe):
        # v3.6.4：直接執行安裝程式，不經過 PowerShell（3.6.x 用 DETACHED_PROCESS 開 PowerShell，沒有主控台的 PowerShell
        # 會直接結束 → 程式關了卻沒有安裝）。安裝程式 /CLOSEAPPLICATIONS 會等 / 關掉還沒結束的舊程式，
        # /RELAUNCH 讓它裝好後自己重開（installer.iss 的 [Run]），記錄在 %TEMP%\hfss_setup.log。
        subprocess.Popen(setup_args(new_exe, current_version()), close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        return
    script = install_script(new_exe, target_exe(), pids, current_version())
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    # PowerShell 一定要有主控台（隱藏的）：不能用 DETACHED_PROCESS
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                      "-EncodedCommand", enc], creationflags=flags, close_fds=True)


def aedt_using_tool_files() -> List[int]:
    """v3.7.1：哪些 AEDT（ansysedt.exe）載入了工具資料夾裡的 DLL（3.7 以前的工具啟動的 AEDT 會這樣）→ 更新會失敗，要先關。"""
    folder = installed_dir() or (os.path.dirname(target_exe()) if getattr(sys, "frozen", False) else None)
    if not folder:
        return []
    try:
        import psutil
    except ImportError:
        return []
    folder = os.path.normcase(os.path.abspath(folder))
    hits = []
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info.get("name") or "").lower() not in ("ansysedt.exe", "ansysedt"):
                continue
            for m in p.memory_maps(grouped=True):
                if os.path.normcase(getattr(m, "path", "") or "").startswith(folder):
                    hits.append(p.pid)
                    break
        except Exception:  # noqa: BLE001 - 權限不足等：當作沒有
            continue
    return hits


def setup_args(setup_exe: str, old_version: str) -> List[str]:
    log = os.path.join(os.environ.get("TEMP") or SETTINGS_DIR, "hfss_setup.log")
    return [setup_exe, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/CURRENTUSER",
            "/RELAUNCH", f"/FROM={old_version}", f"/LOG={log}"]


def cleanup_downloads() -> None:
    """刪掉之前更新下載的安裝程式（開啟時呼叫；正在用的刪不掉就算了）。"""
    try:
        for n in os.listdir(SETTINGS_DIR):
            if is_setup(n) and n.lower().startswith("hfsstool_v"):
                try:
                    os.remove(os.path.join(SETTINGS_DIR, n))
                except OSError:
                    pass
    except OSError:
        pass


def new_exe_path(name: str = "") -> str:
    """新 exe 先下載到舊 exe 旁邊（同一個磁碟，換檔只要改名）；那裡不能寫就放 LAB\\HFSS。
    安裝程式（v3.6 起）一律放 LAB\\HFSS（用完就刪）。"""
    if is_setup(name):
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        return os.path.join(SETTINGS_DIR, re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(name)))
    target = target_exe()
    folder = os.path.dirname(target)
    probe = os.path.join(folder, ".hfss_write_test")
    try:
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
        return target + ".new"
    except OSError:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        return os.path.join(SETTINGS_DIR, "HFSSTool.new.exe")
