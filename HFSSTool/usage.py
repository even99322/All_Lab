"""HFSS 授權占用回報（v3.4）：每 20 秒告訴平台（Hub）這台電腦有沒有開著 AEDT、工具正在跑什麼。

平台的「授權」頁（/licenses）列出每台電腦，大家可以在那裡標註是誰。這台電腦的名字也可以在工具的「版本檢查」裡填（update.json 的 name）。
AEDT 開著（ansysedt.exe）就算占用一個授權，不管是不是用工具開的。HFSS_NO_UPDATE_CHECK=1 時不回報（測試用）。

v3.5：站長在平台設定了授權伺服器時，Hub 會在回報的回應裡輪流叫一台電腦（約 30 秒一次）用 Ansys 內附的
lmutil 查 `lmutil lmstat -a -c <伺服器>`，把原始輸出送回 /api/hfss/lmstat，由 Hub 解析。
lmutil.exe 找不到時可以在 update.json 填 "lmutil": "C:\\...\\lmutil.exe"（或環境變數 HFSS_LMUTIL）。
"""
from __future__ import annotations

import getpass
import glob
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Callable, Dict, List, Optional

import updater

EVERY_MS = 20_000
AEDT_NAMES = ("ansysedt.exe", "ansysedt")


def aedt_status() -> Dict:
    """這台電腦的 AEDT：{running, count, since}（psutil 找 ansysedt.exe）。"""
    try:
        import psutil
    except ImportError:
        return {"running": False, "count": 0, "since": None}
    procs = []
    for p in psutil.process_iter(["name", "create_time"]):
        try:
            if (p.info.get("name") or "").lower() in AEDT_NAMES:
                procs.append(p.info.get("create_time"))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {"running": bool(procs), "count": len(procs), "since": min((t for t in procs if t), default=None)}


def identity() -> Dict[str, str]:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = os.environ.get("USERNAME", "")
    return {"host": socket.gethostname(), "user": user, "name": updater.load_conf().get("name", ""),
            "version": updater.current_version()}


def _version_key(path: str):
    m = re.search(r"[\\/]v(\d+)[\\/]", path)
    return int(m.group(1)) if m else 0


def find_lmutil() -> Optional[str]:
    """Ansys 內附的 lmutil.exe：update.json 的 lmutil / 環境變數 HFSS_LMUTIL → AWP_ROOTxxx → Program Files 裡最新版。"""
    for p in (updater.load_conf().get("lmutil"), os.environ.get("HFSS_LMUTIL")):
        if p and os.path.isfile(p):
            return p
    cands = []
    for k, v in os.environ.items():
        if re.fullmatch(r"(AWP_ROOT|ANSYSEM_ROOT|ANSYSLIC_DIR)\d*", k.upper()) and v:
            base = v.rstrip("\\/")
            cands += [os.path.join(base, "licensingclient", "winx64", "lmutil.exe"),
                      os.path.join(os.path.dirname(base), "licensingclient", "winx64", "lmutil.exe"),
                      os.path.join(base, "winx64", "lmutil.exe")]
    for root in {os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\Program Files"}:
        cands += glob.glob(os.path.join(root, "ANSYS Inc", "v*", "licensingclient", "winx64", "lmutil.exe"))
        cands += glob.glob(os.path.join(root, "ANSYS Inc", "Shared Files", "Licensing", "winx64", "lmutil.exe"))
        cands += glob.glob(os.path.join(root, "ANSYS Inc", "ANSYS Student", "v*", "licensingclient", "winx64", "lmutil.exe"))
    found = [c for c in dict.fromkeys(cands) if os.path.isfile(c)]
    return max(found, key=_version_key) if found else None


def run_lmstat(server: str, exe: Optional[str] = None, timeout: float = 30.0) -> Dict[str, str]:
    """依序試每個伺服器（; 分開），第一個有回應的就用。回傳 {text, server} 或 {error}。"""
    exe = exe or find_lmutil()
    if not exe:
        return {"error": "這台電腦找不到 lmutil.exe（Ansys 授權工具）"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    last = ""
    for srv in [x.strip() for x in server.split(";") if x.strip()]:
        try:
            r = subprocess.run([exe, "lmstat", "-a", "-c", srv], capture_output=True, timeout=timeout,
                               creationflags=flags, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            last = f"{srv}：lmstat 超過 {timeout:.0f} 秒沒回應"
            continue
        except OSError as e:
            return {"error": f"執行 lmutil 失敗：{e}"}
        text = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
        if "Users of " in text:
            return {"text": text, "server": srv}
        last = text or f"{srv}：沒有輸出"
    return {"text": last, "server": server}


def _open(hub: str, path: str, payload: Dict, timeout: float) -> Dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(f"{hub}{path}", data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "HFSSTool"})
    with opener.open(req, timeout=timeout) as r:
        body = r.read()
    try:
        return json.loads(body or b"{}")
    except ValueError:
        return {}


def post(payload: Dict, hub_list: List[str], timeout: float = 5.0) -> Optional[str]:
    """送到第一個連得到的 Hub；回傳用的 Hub（都連不到 → None）。"""
    return post_ex(payload, hub_list, timeout)[0]


def post_ex(payload: Dict, hub_list: List[str], timeout: float = 5.0):
    """同 post，另外回傳 Hub 的回應（裡面可能有 lmstat: {server, due}）。"""
    for hub in hub_list:
        try:
            return hub, _open(hub, "/api/hfss/usage", payload, timeout)
        except Exception:  # noqa: BLE001 - 換下一個
            continue
    return None, {}


def send_lmstat(hub: str, ident: Dict, server: str, runner=run_lmstat) -> Dict:
    """Hub 叫這台去查：跑 lmstat，把原始輸出送回 Hub 解析。"""
    res = runner(server)
    body = {"host": ident.get("host"), "user": ident.get("user"), **res}
    try:
        return _open(hub, "/api/hfss/lmstat", body, 10.0)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


class UsageReporter:
    """主視窗用：QTimer 每 20 秒在介面執行緒收集狀態，背景執行緒送出。"""

    def __init__(self, win, task_info: Callable[[], Optional[Dict]]) -> None:
        from PyQt6.QtCore import QTimer
        self.win = win
        self.task_info = task_info
        self.hub: Optional[str] = None
        self.lm_busy = False
        self.lm_last: Dict = {}
        self.disabled = bool(os.environ.get("HFSS_NO_UPDATE_CHECK"))
        self.timer = QTimer(win)
        self.timer.setInterval(EVERY_MS)
        self.timer.timeout.connect(self.tick)
        if not self.disabled:
            self.timer.start()
            QTimer.singleShot(3000, self.tick)

    def payload(self, closing: bool = False) -> Dict:
        p = identity()
        p["aedt"] = aedt_status()
        try:
            p["task"] = None if closing else self.task_info()
        except Exception:  # noqa: BLE001
            p["task"] = None
        p["closing"] = closing
        return p

    def _hubs(self) -> List[str]:
        hubs = updater.hubs()
        upd = getattr(self.win, "updater", None)
        first = self.hub or getattr(upd, "hub", "") or ""
        return ([first] if first else []) + [h for h in hubs if h != first]

    def tick(self) -> None:
        payload = self.payload()
        hubs = self._hubs()

        def send():
            hub, resp = post_ex(payload, hubs)
            self.hub = hub or self.hub
            lm = (resp or {}).get("lmstat") or {}
            if hub and lm.get("due") and lm.get("server") and not self.lm_busy:
                self.lm_busy = True
                try:
                    self.lm_last = send_lmstat(hub, payload, str(lm["server"]))
                finally:
                    self.lm_busy = False
        threading.Thread(target=send, name="hfss-usage", daemon=True).start()

    def close(self) -> None:
        """關閉程式：馬上告訴平台（不等 90 秒才變離線）。"""
        if self.disabled:
            return
        self.timer.stop()
        post(self.payload(closing=True), self._hubs()[:1] or updater.hubs()[:1], timeout=2.0)
