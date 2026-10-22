"""HFSS 授權占用回報（v3.4）：每 20 秒告訴平台（Hub）這台電腦有沒有開著 AEDT、工具正在跑什麼。

平台的「授權」頁（/licenses）列出每台電腦，大家可以在那裡標註是誰。這台電腦的名字也可以在工具的「版本檢查」裡填（update.json 的 name）。
AEDT 開著（ansysedt.exe）就算占用一個授權，不管是不是用工具開的。HFSS_NO_UPDATE_CHECK=1 時不回報（測試用）。
"""
from __future__ import annotations

import getpass
import json
import os
import socket
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


def post(payload: Dict, hub_list: List[str], timeout: float = 5.0) -> Optional[str]:
    """送到第一個連得到的 Hub；回傳用的 Hub（都連不到 → None）。"""
    data = json.dumps(payload).encode("utf-8")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for hub in hub_list:
        req = urllib.request.Request(f"{hub}/api/hfss/usage", data=data, method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "HFSSTool"})
        try:
            with opener.open(req, timeout=timeout) as r:
                r.read()
            return hub
        except Exception:  # noqa: BLE001 - 換下一個
            continue
    return None


class UsageReporter:
    """主視窗用：QTimer 每 20 秒在介面執行緒收集狀態，背景執行緒送出。"""

    def __init__(self, win, task_info: Callable[[], Optional[Dict]]) -> None:
        from PyQt6.QtCore import QTimer
        self.win = win
        self.task_info = task_info
        self.hub: Optional[str] = None
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
            self.hub = post(payload, hubs) or self.hub
        threading.Thread(target=send, name="hfss-usage", daemon=True).start()

    def close(self) -> None:
        """關閉程式：馬上告訴平台（不等 90 秒才變離線）。"""
        if self.disabled:
            return
        self.timer.stop()
        post(self.payload(closing=True), self._hubs()[:1] or updater.hubs()[:1], timeout=2.0)
