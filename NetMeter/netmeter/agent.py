"""背景程式：每 30 秒讀網卡計數、每分鐘把最近幾天的用量送到 Hub；本機控制埠給視窗查狀態。

控制埠（127.0.0.1:47630，一行 JSON 進、一行 JSON 出）：
    {"cmd": "status"}  → 狀態（今天 / 本月用量、是否在實驗室網段、最後回報…）
    {"cmd": "quit"}    → 存檔後結束（更新 / 解除安裝用）
    {"cmd": "reload"}  → 重新讀設定
同一台電腦只會有一個背景程式（控制埠被占用 = 已經有一個在跑）。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import sys
import tempfile
import threading
import time
import urllib.request
from typing import Any, Callable, Dict, Optional

from . import __version__, config, hub
from .meter import Meter

log = logging.getLogger("netmeter")


def setup_logging() -> None:
    from logging.handlers import RotatingFileHandler

    h = RotatingFileHandler(config.data_dir() / "netmeter.log", maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(h)
    root.setLevel(logging.INFO)


class Agent:
    def __init__(self, cfg: Optional[Dict[str, Any]] = None, sampler: Optional[Callable[[], Dict[str, Any]]] = None,
                 reporter=None) -> None:
        from .counters import read

        self.cfg = cfg or config.load()
        self.meter = Meter(config.data_dir() / "state.json", config.networks(self.cfg), sampler or read,
                           int(self.cfg.get("keep_days") or 62))
        self.reporter = reporter or hub.report
        self.info = hub.device_info()
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.last_report: Dict[str, Any] = {}
        self.last_error = ""
        self.update: Dict[str, Any] = {}
        self._last_save = 0.0
        self._was_on = False

    # ---- 一輪 ----
    def tick(self) -> Dict[str, Any]:
        with self.lock:
            r = self.meter.step()
            if self.meter.dirty and time.time() - self._last_save > 120:
                self.meter.save()
                self._last_save = time.time()
        return r

    def payload(self) -> Dict[str, Any]:
        with self.lock:
            m = self.meter
            return {"id": m.id, **self.info, "ip": ", ".join(m.ips), "subnet": ", ".join(self.cfg["subnets"]),
                    "ifaces": m.on_ifaces, "on_net": bool(m.on_ifaces), "rate": dict(m.rate), "days": m.recent(8)}

    def send(self) -> bool:
        if not self.meter.on_ifaces:
            return False                                   # 不在實驗室網段：不送（也送不到）
        try:
            used, resp = self.reporter(self.cfg["hubs"], self.payload())
            self.last_report = {"time": time.time(), "hub": used, "ok": True}
            self.last_error = ""
            return True
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            self.last_report = {"time": time.time(), "ok": False}
            log.info("回報失敗：%s", e)
            return False

    def status(self) -> Dict[str, Any]:
        with self.lock:
            m = self.meter
            from .meter import today_str

            return {"version": __version__, "id": m.id, "pid": os.getpid(), "on_net": bool(m.on_ifaces),
                    "ifaces": m.on_ifaces, "ips": m.ips, "rate": dict(m.rate),
                    "today": dict(m.days.get(today_str(m.now()), {"up": 0, "down": 0})), "month": m.month(),
                    "days": m.recent(14), "hubs": self.cfg["hubs"], "subnets": self.cfg["subnets"],
                    "last_report": dict(self.last_report), "error": self.last_error, "update": dict(self.update)}

    # ---- 主迴圈 ----
    def run(self) -> None:
        srv = ControlServer(self)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True).start()
        threading.Thread(target=self._update_loop, daemon=True).start()
        log.info("流量監看 %s 啟動（網段 %s；Hub %s）", __version__, self.cfg["subnets"], self.cfg["hubs"])
        next_report = 0.0
        try:
            while not self.stop.is_set():
                try:
                    r = self.tick()
                except Exception:  # noqa: BLE001
                    log.exception("讀網卡計數失敗")
                    r = {"on": False}
                now = time.time()
                if r.get("on") and (now >= next_report or not self._was_on):
                    self.send()
                    next_report = now + float(self.cfg.get("report_s") or 60)
                self._was_on = bool(r.get("on"))
                self.stop.wait(float(self.cfg.get("sample_s") or 30))
        finally:
            with self.lock:
                self.meter.save()
            srv.shutdown()
            srv.server_close()
            log.info("流量監看結束")

    def reload(self) -> None:
        cfg = config.load()
        with self.lock:
            self.cfg = cfg
            self.meter.nets = config.networks(cfg)

    # ---- 自動更新（Windows 打包版）----
    def _update_loop(self) -> None:
        self.stop.wait(60)
        while not self.stop.is_set():
            try:
                self.check_update()
            except Exception as e:  # noqa: BLE001
                log.info("檢查更新失敗：%s", e)
            self.stop.wait(float(self.cfg.get("update_check_h") or 6) * 3600)

    def check_update(self) -> Optional[Dict[str, Any]]:
        if not self.meter.on_ifaces:
            return None
        plat = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else ""
        if not plat:
            return None
        for h in self.cfg["hubs"]:
            try:
                info = hub.latest(h, plat)
            except Exception:  # noqa: BLE001
                continue
            if not info or hub.vkey(info["version"]) <= hub.vkey(__version__):
                self.update = {}
                return None
            self.update = {"version": info["version"], "url": info["url"]}
            if plat == "windows" and getattr(sys, "frozen", False):
                self._apply_windows(info)
            return info
        return None

    def _apply_windows(self, info: Dict[str, Any]) -> None:
        """下載新版 exe → 執行它的 --update（它會等這個程式結束、換掉安裝的 exe、再啟動）→ 這個程式結束。"""
        import hashlib
        import subprocess

        tmp = os.path.join(tempfile.gettempdir(), f"QELNetMeter_v{info['version']}.exe")
        req = urllib.request.Request(info["url"], headers={"User-Agent": f"QELNetMeter/{__version__}"})
        with hub._NO_PROXY.open(req, timeout=120) as r, open(tmp, "wb") as f:
            f.write(r.read())
        if info.get("sha256"):
            with open(tmp, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != info["sha256"]:
                    raise RuntimeError("新版檔案的 sha256 不對")
        log.info("更新到 %s", info["version"])
        subprocess.Popen([tmp, "--update", str(os.getpid())], creationflags=0x00000008 | 0x00000200,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        self.stop.set()


# ---------------------------------------------------------------------------
class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            req = json.loads(self.rfile.readline(65536).decode("utf-8") or "{}")
        except ValueError:
            req = {}
        ag: Agent = self.server.agent           # type: ignore[attr-defined]
        cmd = req.get("cmd")
        if cmd == "status":
            out = ag.status()
        elif cmd == "quit":
            ag.stop.set()
            out = {"ok": True}
        elif cmd == "reload":
            ag.reload()
            out = {"ok": True}
        elif cmd == "report":
            out = {"ok": ag.send()}
        else:
            out = {"error": "unknown command"}
        self.wfile.write((json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8"))


class ControlServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        port = agent.cfg.get("control_port")
        super().__init__(("127.0.0.1", 47630 if port is None else int(port)), _Handler)

    def server_bind(self) -> None:
        # 單一執行個體：Windows 用 SO_EXCLUSIVEADDRUSE（SO_REUSEADDR 在 Windows 會讓兩個程式綁同一埠）；
        # 其他系統用 SO_REUSEADDR（只是讓剛關掉的連線 TIME_WAIT 不擋住重新啟動，還在聽的埠照樣綁不上）
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        super().server_bind()


def control(cmd: str, port: Optional[int] = None, timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    """跟背景程式說話；沒有在跑 → None。"""
    port = port or int(config.load().get("control_port") or 47630)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
            s.sendall((json.dumps({"cmd": cmd}) + "\n").encode("utf-8"))
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf.decode("utf-8") or "{}")
    except (OSError, ValueError):
        return None


def wait_gone(pid: int, timeout: float = 15.0) -> None:
    if not pid or pid <= 0 or pid == os.getpid():
        return
    import psutil

    try:
        psutil.Process(pid).wait(timeout)
    except Exception:  # noqa: BLE001
        pass
