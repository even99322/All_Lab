"""背景程式：每 30 秒讀網卡計數、每分鐘把最近幾天的用量送到 Hub；本機控制埠給視窗查狀態。

控制埠（127.0.0.1:47630，一行 JSON 進、一行 JSON 出）：
    {"cmd": "status"}  → 狀態（今天 / 本月用量、是否在實驗室網段、最後回報…）
    {"cmd": "quit"}    → 存檔後結束（更新 / 解除安裝用）
    {"cmd": "reload"}  → 重新讀設定
同一台電腦只會有一個背景程式（控制埠被占用 = 已經有一個在跑）。
1.0.4：分內網 / 外網 / VPN（flows.py）；Windows 在實驗室網段時把 Hub 的 https 網址指到 NAS（hosts.py）。
1.0.6：回報預設閘道（gateway.py）讓 Hub 分辨在哪一間實驗室；不在實驗室網段時每 10 分鐘回報一次（拿 Hub 的網段設定）；
       Hub 指定這台當另一間實驗室的代讀電腦 → 每次回報前讀那邊的華碩路由器（asus.py）。
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
from typing import Any, Callable, Dict, List, Optional

from . import __version__, config, gateway, hosts, hub
from . import install as install_mod
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
        self.update_now = threading.Event()
        self.update_error = ""
        self._tried: Dict[str, float] = {}
        self._last_save = 0.0
        self._was_on = False
        self.lan_https: Dict[str, str] = dict(config.read_extra("lan_https.json"))     # Hub 給的 https 網域（上次的）
        self.hosts_state = ""
        # 1.0.6：Hub 給的網段（各實驗室）與各實驗室路由器的 MAC；代讀路由器
        hc = config.read_extra("hub_cfg.json")
        self.hub_subnets = [str(x) for x in hc.get("subnets") or []]
        self.meter.allowed_gw = set(hc.get("gateways") or [])
        self._apply_nets()
        self.collect: Dict[str, Any] = {}
        self.site_router: Optional[Dict[str, Any]] = None
        from .collector import Collector

        self._kicked = threading.Event()
        self._renewing = False
        self.renew_result: Optional[Dict[str, Any]] = None
        self.col = Collector(kick=self._kicked.set, port=int(self.cfg.get("reg_port", 47680)))        # 1.0.9：代讀（Traffic Analyzer、設備登記頁、停網）
        self.gw: Dict[str, str] = {}

    # ---- 一輪 ----
    def _apply_nets(self) -> None:
        import ipaddress

        nets = config.networks(self.cfg)
        for x in self.hub_subnets:
            try:
                n = ipaddress.ip_network(x, strict=False)
            except ValueError:
                continue
            if n not in nets:
                nets.append(n)
        self.meter.nets = nets

    def tick(self) -> Dict[str, Any]:
        try:
            self.gw = gateway.detect(self.meter.ips)
            self.meter.gw_mac = self.gw.get("mac", "")
        except Exception:  # noqa: BLE001
            pass
        with self.lock:
            r = self.meter.step()
            if self.meter.dirty and time.time() - self._last_save > 120:
                self.meter.save()
                self._last_save = time.time()
        return r

    def payload(self) -> Dict[str, Any]:
        with self.lock:
            m = self.meter
            p = {"id": m.id, **self.info, "ip": ", ".join(m.ips), "subnet": ", ".join(self.cfg["subnets"]),
                 "ifaces": m.on_ifaces, "on_net": bool(m.on_ifaces), "rate": dict(m.rate), "days": m.recent(8)}
        if self.gw:
            p["gw"] = dict(self.gw)
        if self.renew_result is not None:
            p["renew_result"] = self.renew_result
        if self.col.active():
            p.update(self.col.payload())
        elif self.site_router:
            p["site_router"] = self.site_router
        return p

    def _read_site_router(self) -> None:
        """1.0.6：Hub 指定這台代讀另一間實驗室的路由器（1.0.9 起由 collector.Collector 做，含 Traffic Analyzer）。"""
        if not self.collect:
            self.site_router = None
            return
        self.site_router = self.col.read()

    def _apply_hub(self, resp: Any) -> None:
        if not isinstance(resp, dict):
            return
        changed = False
        if isinstance(resp.get("subnets"), list):
            subs = [str(x)[:43] for x in resp["subnets"]][:16]
            if subs != self.hub_subnets:
                self.hub_subnets = subs
                self._apply_nets()
                changed = True
        if isinstance(resp.get("gateways"), list):
            gws = {gateway.norm_mac(str(x)) for x in resp["gateways"]} - {""}
            if gws != self.meter.allowed_gw:
                self.meter.allowed_gw = gws
                changed = True
        if changed:
            config.write_extra("hub_cfg.json", {"subnets": self.hub_subnets, "gateways": sorted(self.meter.allowed_gw)})
        c = resp.get("collect")
        self.collect = dict(c) if isinstance(c, dict) and c.get("host") else {}
        try:
            self.col.configure(self.collect)
            if self.collect:
                self.col.on_hub(resp)
        except Exception:  # noqa: BLE001
            log.exception("代讀")
        rn = resp.get("renew")                               # 1.0.11：Hub 固定了這台的 IP → 重新取得 IP
        if isinstance(rn, dict) and rn.get("to") and not self._renewing:
            self._renewing = True
            threading.Thread(target=self.renew_ip, args=(dict(rn),), daemon=True).start()
        lv = resp.get("latest")                              # 1.0.8：Hub 上有新版 → 馬上查、更新
        if isinstance(lv, str) and hub.vkey(lv) > hub.vkey(__version__) and self.update.get("version") != lv:
            self.update_now.set()

    def _ips_now(self) -> Dict[str, List[str]]:
        try:
            s = self.meter.sampler()
        except Exception:  # noqa: BLE001
            return {}
        return {n: [str(i).split("%", 1)[0] for i in (v.get("ips") or [])] for n, v in (s.get("ifaces") or {}).items()}

    def renew_ip(self, rn: Dict[str, Any], run: Optional[Callable[[List[str]], Any]] = None,
                 wait: float = 4.0) -> Dict[str, Any]:
        """1.0.11：ipconfig /renew 那張網卡（路由器的 DHCP 保留改了 IP，dnsmasq 會 NAK 舊的 → 拿到新的）；
        沒換過去再 /release + /renew。只有 Windows（系統排程的背景程式有權限）。"""
        import subprocess

        frm, to = str(rn.get("from") or ""), str(rn.get("to") or "")
        res: Dict[str, Any] = {"mac": rn.get("mac"), "ok": False, "ip": frm}
        try:
            if sys.platform != "win32" and run is None:
                raise RuntimeError("只有 Windows 會自動換 IP")
            if run is None:
                def run(args: List[str]) -> Any:
                    return subprocess.run(args, capture_output=True, timeout=90, creationflags=0x08000000)
            ifs = self._ips_now()
            name = next((n for n, ips in ifs.items() if frm and frm in ips), "")
            if not name:
                if any(to in ips for ips in ifs.values()):
                    res.update(ok=True, ip=to)
                    return res
                raise RuntimeError(f"找不到有 {frm} 的網卡")
            log.info("固定 IP：%s 從 %s 換到 %s", name, frm, to)
            for steps in ((["ipconfig", "/renew", name],), (["ipconfig", "/release", name], ["ipconfig", "/renew", name])):
                for a in steps:
                    run(a)
                time.sleep(wait)
                now_ips = self._ips_now().get(name) or []
                if to in now_ips:
                    res.update(ok=True, ip=to)
                    break
            else:
                res["error"] = f"重新取得後還是 {', '.join(self._ips_now().get(name) or []) or '沒有 IP'}"
        except Exception as e:  # noqa: BLE001
            res["error"] = str(e)[:200]
        finally:
            self._renewing = False
            self.renew_result = res
            self._kicked.set()                          # 馬上回報（Hub 才知道換好了）
        return res

    def send(self, heartbeat: bool = False) -> bool:
        if not self.meter.on_ifaces and not heartbeat:
            return False                                   # 不在實驗室網段：只有每 5 分鐘的 heartbeat
        try:
            if self.collect:
                self._read_site_router()
            hubs = self._hubs_ordered()                      # 1.0.7：上次連得到的先試（在別間實驗室不用先等內網位址逾時）
            pl = self.payload()
            self._last_payload_keys = tuple(pl)
            used, resp = self.reporter(hubs, pl)
            self.last_report = {"time": time.time(), "hub": used, "ok": True}
            self.good_hub = used
            if "renew_result" in (getattr(self, "_last_payload_keys", ()) or ()):
                self.renew_result = None
            self.last_error = ""
            self._apply_hub(resp)
            self._learn_https(used, resp)
            return True
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            self.last_report = {"time": time.time(), "ok": False}
            log.info("回報失敗：%s", e)
            return False

    # ---- 1.0.4：內網開 Hub 的 https 網址 ----
    def _learn_https(self, used: str, resp: Any) -> None:
        import urllib.parse

        x = resp.get("lan_https") if isinstance(resp, dict) else None
        dom = str((x or {}).get("domain") or "").lower()
        ip = urllib.parse.urlsplit(used or "").hostname or ""
        if not dom or not hosts.valid(ip, dom, self.meter.nets):
            return
        url = str((x or {}).get("url") or "")
        new = {"domain": dom, "ip": ip, "url": url if url.startswith(f"https://{dom}") else ""}
        if new != self.lan_https:
            self.lan_https = new
            config.write_extra("lan_https.json", new)
        self.sync_hosts()

    def sync_hosts(self, force_off: bool = False) -> None:
        """在實驗室網段 → hosts 有「網域 → NAS」；不在 / 結束 → 拿掉。只有 Windows、設定 lan_hosts 沒關掉時。"""
        if sys.platform != "win32" or not self.cfg.get("lan_hosts", True):
            return
        d = self.lan_https
        # 1.0.6：還要「最近一次是用內網位址回報成功的」——另一間實驗室的內網也可能是 192.168.50.x，不能把網址指過去
        import urllib.parse

        lr = self.last_report or {}
        via_lan = (bool(lr.get("ok")) and time.time() - float(lr.get("time") or 0) < 900
                   and (urllib.parse.urlsplit(lr.get("hub") or "").hostname or "") == d.get("ip"))
        on = (not force_off and bool(self.meter.on_ifaces) and bool(d.get("domain")) and via_lan
              and hosts.valid(d.get("ip", ""), d.get("domain", ""), self.meter.nets))
        want = f"{d.get('ip')} {d.get('domain')}" if on else "-"
        if want == self.hosts_state:
            return
        try:
            hosts.apply([(d["ip"], d["domain"])] if on else [])
            self.hosts_state = want
        except OSError as e:
            log.info("沒辦法改 hosts（%s）：要系統排程的背景程式（SYSTEM）才有權限", e)
            self.hosts_state = want                      # 不要每 30 秒重試、洗紀錄

    def status(self) -> Dict[str, Any]:
        with self.lock:
            m = self.meter
            from .meter import today_str

            return {"version": __version__, "id": m.id, "pid": os.getpid(), "on_net": bool(m.on_ifaces),
                    "ifaces": m.on_ifaces, "ips": m.ips, "rate": dict(m.rate),
                    "today": dict(m.days.get(today_str(m.now()), {"up": 0, "down": 0})), "month": m.month(),
                    "days": m.recent(14), "hubs": self.cfg["hubs"],
                    "subnets": [str(n) for n in m.nets],
                    "gw_unknown": bool(m.allowed_gw and m.gw_mac and m.gw_mac not in m.allowed_gw),
                    "gw": dict(self.gw), "collect": self.col.status() if self.collect else None,
                    "split": {"kind": getattr(m.flows, "kind", ""), "ok": bool(getattr(m.flows, "ok", False)),
                              "error": getattr(m.flows, "error", ""), "rate": m.rate_split},
                    "lan_https": dict(self.lan_https, hosts=self.hosts_state not in ("", "-")),
                    "last_report": dict(self.last_report), "error": self.last_error, "update": dict(self.update),
                    "as_system": install_mod.running_as_system() if sys.platform == "win32" else None}

    # ---- 主迴圈 ----
    def run(self) -> None:
        srv = ControlServer(self)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True).start()
        threading.Thread(target=self._update_loop, daemon=True).start()
        log.info("流量監看 %s 啟動（網段 %s；Hub %s）", __version__, self.cfg["subnets"], self.cfg["hubs"])
        if self.meter.flows is None and self.cfg.get("split", True):          # 1.0.4：分內網 / 外網 / VPN
            from . import flows

            f = flows.make()
            if f.start():
                log.info("分內網 / 外網 / VPN：%s", f.kind)
            self.meter.flows = f
        next_report = 0.0
        next_hb = 0.0
        try:
            while not self.stop.is_set():
                try:
                    r = self.tick()
                except Exception:  # noqa: BLE001
                    log.exception("讀網卡計數失敗")
                    r = {"on": False}
                now = time.time()
                kicked = self._kicked.is_set()
                self._kicked.clear()
                if r.get("on") and (now >= next_report or not self._was_on or kicked):
                    self.send()
                    next_report = now + float(self.cfg.get("report_s") or 60)
                elif not r.get("on") and now >= next_hb:           # 1.0.6：不在實驗室網段 → 每 5 分鐘問一次 Hub（1.0.7 起；Hub 可能加了這間）
                    self.send(heartbeat=True)
                    next_hb = now + 300
                self._was_on = bool(r.get("on"))
                try:
                    self.sync_hosts()
                except Exception:  # noqa: BLE001
                    log.exception("hosts")
                end = time.time() + float(self.cfg.get("sample_s") or 30)
                while not self.stop.is_set() and not self._kicked.is_set() and time.time() < end:
                    self.stop.wait(min(1.0, max(0.0, end - time.time())))
        finally:
            self.col.stop_page()
            try:
                self.sync_hosts(force_off=True)
            except Exception:  # noqa: BLE001
                pass
            if self.meter.flows is not None:
                self.meter.flows.stop()
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
        """1.0.8：開始 1 分鐘後查一次；之後每小時查一次，另外 Hub 每次回報的回應帶最新版本（latest），
        比這版新就馬上查（_apply_hub 叫 self.update_now）——Hub 發佈新版後幾分鐘內就自己更新。"""
        self.stop.wait(60)
        while not self.stop.is_set():
            try:
                self.check_update()
            except Exception as e:  # noqa: BLE001
                log.info("檢查更新失敗：%s", e)
                self.update_error = str(e)[:300]
            self.update_now.wait(float(self.cfg.get("update_check_h") or 1) * 3600)
            self.update_now.clear()

    def _hubs_ordered(self) -> List[str]:
        hubs = list(self.cfg["hubs"])
        last = getattr(self, "good_hub", None)
        if last in hubs:
            hubs.remove(last)
            hubs.insert(0, last)
        return hubs

    def check_update(self) -> Optional[Dict[str, Any]]:
        plat = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else ""
        if not plat:
            return None
        for h in self._hubs_ordered():
            try:
                info = hub.latest(h, plat)
            except Exception:  # noqa: BLE001
                continue
            if not info or hub.vkey(info["version"]) <= hub.vkey(__version__):
                self.update = {}
                return None
            self.update = {"version": info["version"], "url": info["url"]}
            if plat == "windows" and getattr(sys, "frozen", False):
                if not install_mod.running_as_system() and install_mod.service_installed():
                    log.info("有新版 %s：交給系統排程的背景程式更新（這個是使用者身分的）", info["version"])
                    return info
                if self._tried.get(info["version"], 0) > time.time() - 1800:
                    return info                              # 同一版 30 分鐘內不重複下載
                self._tried[info["version"]] = time.time()
                self._apply_windows(info)
            return info
        return None

    def _apply_windows(self, info: Dict[str, Any]) -> None:
        """下載新版安裝程式 → 安靜安裝（安裝程式會先結束這個背景程式，裝好再啟動新版）。"""
        import hashlib
        import subprocess

        name = info.get("name") or f"QELNetMeter_v{info['version']}_windows-setup.exe"
        if not name.lower().endswith("setup.exe"):
            log.info("Hub 上的新版不是安裝程式（%s），略過自動更新", name)
            return
        tmp = os.path.join(tempfile.gettempdir(), name)
        req = urllib.request.Request(info["url"], headers={"User-Agent": f"QELNetMeter/{__version__}"})
        with hub._NO_PROXY.open(req, timeout=300) as r, open(tmp, "wb") as f:
            f.write(r.read())
        if info.get("sha256"):
            with open(tmp, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != info["sha256"]:
                    raise RuntimeError("新版檔案的 sha256 不對")
        log.info("更新到 %s", info["version"])
        with self.lock:
            self.meter.save()
        args = [tmp, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
        kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        try:            # 1.0.3：從系統排程的工作裡「脫離」（不然工作結束時安裝程式會被一起關掉）
            subprocess.Popen(args, creationflags=0x00000008 | 0x00000200 | 0x01000000, **kw)
        except OSError:
            subprocess.Popen(args, creationflags=0x00000008 | 0x00000200, **kw)


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
