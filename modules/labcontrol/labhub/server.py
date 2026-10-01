"""Lab Control Hub 伺服器（只用標準函式庫）。

API（JSON；需要 token：``Authorization: Bearer <token>``，網頁用 cookie）::

    GET  /api/ping                          → {server, version, time, auth}（不需要 token）
    GET  /api/state                         → 所有電腦 / 節點的狀態（網頁與 Lab Control 共用）
    POST /api/devices/heartbeat             ← 每台 Lab Control 定期回報（控制端與節點都會）
    POST /api/nodes/<n>/claim               ← 節點上線（同名節點已在別台電腦上線 → 409）
    POST /api/nodes/<n>/status              ← 節點心跳（狀態、量測進度、儀器）
    POST /api/nodes/<n>/offline             ← 節點離線
    GET/PUT /api/nodes/<n>/instruments      ↔ 節點的 instruments.yaml（控制端建立模擬鏡像用）
    GET  /api/nodes/<n>                     → 單一節點狀態
    POST /api/nodes/<n>/commands[?wait=s]   ← 控制端送指令；有 wait → 等節點回覆一起回傳
    GET  /api/nodes/<n>/commands?wait=s     → 節點取指令（long-poll）
    POST /api/replies/<id>                  ← 節點回覆
    GET  /api/replies/<id>?wait=s           → 控制端取回覆
    POST /api/nodes/<n>/live/head           ← 量測開始（軸、通道）
    POST /api/nodes/<n>/live/batch          ← 即時事件批次 → {seq}
    GET  /api/nodes/<n>/live?after=&wait=   → 新批次（long-poll）
    PUT  /api/files/<n>/<路徑>              ← 節點上傳量測檔
    GET  /api/files[?node=&limit=]          → 檔案清單
    GET  /api/files/<n>/<路徑>              → 下載
    GET  /api/releases                      → 可更新的版本（Lab APP 發佈資料夾）
    GET  /api/releases/<版本>.zip           → 下載某版本（節點自我更新用）

網頁：``/``（監控）、``/login``、``/logout``。
"""
from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import sys
import threading
import time
import urllib.parse
import uuid
import zipfile
from collections import deque
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import DEFAULT_PORT, __version__

log = logging.getLogger("labhub")
STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "labhub_token"


class HubHTTPError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_") or "node"


def safe_rel(rel: str) -> Path:
    """上傳檔的相對路徑：去掉 ..、磁碟機代號與不合法字元。"""
    parts = []
    for p in re.split(r"[\\/]+", urllib.parse.unquote(str(rel))):
        p = re.sub(r'[<>:"|?*\x00-\x1f]', "_", p).strip(" .")
        if p and p not in (".", ".."):
            parts.append(p)
    if not parts:
        raise HubHTTPError(400, "檔案路徑無效")
    return Path(*parts)


def parse_version(v: str) -> Tuple:
    m = re.match(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?([a-z]*)", str(v or ""), re.I)
    if not m:
        return (0, 0, 0, "")
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0), (m.group(4) or "").lower())


# ---------------------------------------------------------------------------
class Config:
    def __init__(self, data_dir: Path, releases_dir: Optional[Path] = None, token: Optional[str] = "",
                 offline_after: float = 15.0, keep_batches: int = 600, app_name: str = "LabControl",
                 max_upload_mb: float = 4096) -> None:
        self.data_dir = Path(data_dir)
        self.releases_dir = Path(releases_dir) if releases_dir else None
        self.offline_after = float(offline_after)
        self.keep_batches = int(keep_batches)
        self.app_name = app_name
        self.max_upload = int(max_upload_mb * 1024 * 1024)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # token：None = 不檢查（只建議在測試或完全封閉的網路）；"" = 讀 data/token.txt，沒有就產生
        if token == "":
            f = self.data_dir / "token.txt"
            if f.exists():
                token = f.read_text(encoding="utf-8").strip()
            if not token:
                token = secrets.token_urlsafe(12)
                f.write_text(token + "\n", encoding="utf-8")
                log.warning("已產生新的存取 token（存在 %s）：%s", f, token)
        self.token = token

    @classmethod
    def from_env(cls, **over: Any) -> "Config":
        env = os.environ
        auth = env.get("LABHUB_AUTH", "on").lower() not in ("off", "0", "false", "no")
        kw = dict(data_dir=Path(env.get("LABHUB_DATA", "./labhub-data")),
                  releases_dir=Path(env["LABHUB_RELEASES"]) if env.get("LABHUB_RELEASES") else None,
                  token=env.get("LABHUB_TOKEN", "") if auth else None,
                  offline_after=float(env.get("LABHUB_OFFLINE_S", 15)),
                  keep_batches=int(env.get("LABHUB_KEEP_BATCHES", 600)),
                  app_name=env.get("LABHUB_APP", "LabControl"))
        kw.update({k: v for k, v in over.items() if v is not None})
        return cls(**kw)


class Node:
    def __init__(self, name: str, keep: int) -> None:
        self.name = name
        self.status: Dict[str, Any] = {}
        self.last_seen = 0.0
        self.claim: Optional[Dict[str, Any]] = None
        self.instruments = ""
        self.commands: List[Dict[str, Any]] = []
        self.head: Optional[Dict[str, Any]] = None
        self.batches: deque = deque(maxlen=keep)
        self.seq = 0
        self.log: deque = deque(maxlen=80)
        self.offline = False


class HubState:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.cond = threading.Condition()
        self.nodes: Dict[str, Node] = {}
        self.devices: Dict[str, Dict[str, Any]] = {}
        self.replies: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        # 儀器登錄：key → {電腦: {first, last, name, address, idn, driver, label, detected}}
        self.inst_seen: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # 共用儀器歸屬（「拉取群組」）：key → {host, by, time}
        self.owners: Dict[str, Dict[str, Any]] = {}
        # 各電腦目前的儀器狀態（連線、lease、輸出值）：host → {time, items: {key: …}}
        self.inst_live: Dict[str, Dict[str, Any]] = {}
        self.started = time.time()
        self._dirty = False
        self.files_dir = cfg.data_dir / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    # ---- 保存（重開 Hub 後仍記得有哪些電腦 / 節點）------------------------------
    def _state_file(self) -> Path:
        return self.cfg.data_dir / "state.json"

    def _load(self) -> None:
        try:
            data = json.loads(self._state_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.devices = {k: dict(v) for k, v in (data.get("devices") or {}).items()}
        self.inst_seen = {k: dict(v) for k, v in (data.get("inst_seen") or {}).items()}
        self.owners = {k: dict(v) for k, v in (data.get("owners") or {}).items()}
        for name, d in (data.get("nodes") or {}).items():
            n = Node(name, self.cfg.keep_batches)
            n.status = d.get("status") or {}
            n.last_seen = float(d.get("last_seen", 0))
            n.instruments = d.get("instruments") or ""
            n.claim = d.get("claim")
            n.log.extend(d.get("log") or [])
            n.offline = True                      # Hub 重開：等節點重新回報
            self.nodes[name] = n

    def save(self, force: bool = False) -> None:
        with self.cond:
            if not (self._dirty or force):
                return
            data = {"version": __version__, "saved": time.time(), "devices": self.devices,
                    "inst_seen": self.inst_seen, "owners": self.owners,
                    "nodes": {n.name: {"status": n.status, "last_seen": n.last_seen, "instruments": n.instruments,
                                       "claim": n.claim, "log": list(n.log)[-30:]} for n in self.nodes.values()}}
            self._dirty = False
        tmp = self._state_file().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._state_file())

    def _touch(self) -> None:
        self._dirty = True
        self.cond.notify_all()

    # ---- 共用 ------------------------------------------------------------
    def online(self, last_seen: float, now: Optional[float] = None) -> bool:
        return ((now or time.time()) - float(last_seen or 0)) < self.cfg.offline_after

    def node(self, name: str, create: bool = False) -> Node:
        name = safe_name(name)
        n = self.nodes.get(name)
        if n is None:
            if not create:
                raise HubHTTPError(404, f"沒有節點 {name}")
            n = self.nodes[name] = Node(name, self.cfg.keep_batches)
        return n

    def node_online(self, n: Node, now: Optional[float] = None) -> bool:
        return not n.offline and self.online(n.last_seen, now)

    def node_view(self, n: Node, now: float) -> Dict[str, Any]:
        st = dict(n.status)
        st.setdefault("name", n.name)
        st["name"] = n.name
        st["online"] = self.node_online(n, now)
        if not st["online"]:
            st["state"] = "offline"
        st["last_seen"] = n.last_seen
        st["seen_ago"] = round(now - n.last_seen, 1) if n.last_seen else None
        st["log"] = list(n.log)[-12:]
        st["seq"] = n.seq
        return st

    # ---- 電腦（每一台 Lab Control）--------------------------------------------
    def device_heartbeat(self, info: Dict[str, Any], ip: str) -> Dict[str, Any]:
        did = str(info.get("id") or f"{info.get('user', '?')}@{info.get('host', ip)}")
        with self.cond:
            d = self.devices.setdefault(did, {})
            d.update({k: v for k, v in info.items() if k not in ("id", "instruments", "scan")})
            d.update(id=did, last_seen=time.time(), ip=ip)
            if "instruments" in info:
                d["n_instruments"] = len(info.get("instruments") or [])
                self._absorb(str(info.get("host") or ip), info.get("instruments") or [], info.get("scan"))
            self._touch()
        return {"ok": True, "time": time.time(), "latest": self.latest_version(), "owners": self.owner_map()}

    def forget_device(self, did: str) -> None:
        with self.cond:
            self.devices.pop(did, None)
            self._touch()

    # ---- 節點 ------------------------------------------------------------
    def claim(self, name: str, info: Dict[str, Any], ip: str) -> Dict[str, Any]:
        with self.cond:
            n = self.node(name, create=True)
            old = n.claim or {}
            same = (old.get("host"), old.get("pid")) == (info.get("host"), info.get("pid"))
            if old and not same and self.node_online(n):
                raise HubHTTPError(409, f"節點名稱「{n.name}」已經在 {old.get('host')}（{old.get('user')}）上線；"
                                        "同一個節點只能開一個，或在 settings.yaml remote.node.name 換名稱")
            n.claim = dict(info, ip=ip, time=time.time())
            n.commands.clear()                  # 離線期間的舊指令不執行
            n.offline = False
            n.last_seen = time.time()
            n.status = dict(n.status, state="idle", host=info.get("host"), user=info.get("user"),
                            version=info.get("version"), pid=info.get("pid"))
            self._touch()
            return {"ok": True, "name": n.name, "seq": n.seq}

    def node_status(self, name: str, st: Dict[str, Any], ip: str) -> Dict[str, Any]:
        with self.cond:
            n = self.node(name, create=True)
            self._absorb(str(st.get("host") or ip), st.get("instruments") or [], st.get("scan"))
            st = {k: v for k, v in st.items() if k != "scan"}
            n.status = dict(st, ip=ip)
            n.last_seen = time.time()
            n.offline = st.get("state") == "offline"
            did = f"{st.get('user', '?')}@{st.get('host', ip)}"
            d = self.devices.setdefault(did, {})
            d.update(id=did, host=st.get("host"), user=st.get("user"), version=st.get("version"), role="node",
                     node=n.name, state=st.get("state"), run=st.get("run"), simulate=st.get("simulate"),
                     ip=ip, last_seen=time.time(), n_instruments=len(st.get("instruments") or []))
            self._touch()
            return {"ok": True, "time": time.time(), "owners": self.owner_map(), "shared": self.shared_keys()}

    def node_offline(self, name: str) -> None:
        with self.cond:
            n = self.node(name)
            n.offline = True
            n.status["state"] = "offline"
            self._touch()

    # ---- 儀器登錄 ------------------------------------------------------------
    def _absorb(self, host: str, items: List[Dict[str, Any]], scan: Optional[Dict[str, Any]] = None) -> None:
        """（持有 cond 時呼叫）記錄 host 上的儀器（設定檔中的 + 掃描偵測到的）。"""
        now = time.time()
        live: Dict[str, Any] = {}
        for it in items:
            k = it.get("key")
            if not k or it.get("virtual"):
                continue
            rec = self.inst_seen.setdefault(k, {}).setdefault(host, {"first": now})
            rec.update(last=now, name=it.get("name"), address=it.get("address"), driver=it.get("driver"),
                       label=it.get("label"), configured=True)
            if isinstance(it.get("options"), dict):
                rec["options"] = it["options"]
            if it.get("idn"):
                rec["idn"] = it["idn"]
            live[k] = {kk: it.get(kk) for kk in ("name", "connected", "lease", "level", "unit", "output",
                                                  "simulate", "enabled")}
        for it in (scan or {}).get("items") or []:
            k = it.get("key")
            if not k:
                continue
            rec = self.inst_seen.setdefault(k, {}).setdefault(host, {"first": now})
            rec.update(last=now, address=rec.get("address") or it.get("address"), detected=True,
                       detected_time=(scan or {}).get("time"))
            if it.get("idn"):
                rec["idn"] = it["idn"]
            rec.setdefault("name", it.get("configured_as"))
            rec.setdefault("configured", bool(it.get("configured_as")))
        self.inst_live[host] = {"time": now, "items": live}

    def shared_keys(self) -> List[str]:
        """多台電腦（30 天內）都看過的網路儀器。"""
        now = time.time()
        with self.cond:
            return sorted(k for k, hosts in self.inst_seen.items() if k.startswith(("net:", "zi:"))
                          and sum(1 for r in hosts.values() if now - float(r.get("last", 0)) <= 30 * 86400) > 1)

    def owner_map(self) -> Dict[str, str]:
        return {k: v.get("host") for k, v in self.owners.items() if v.get("host")}

    def _host_node(self, host: str, now: float) -> Optional[Node]:
        for n in self.nodes.values():
            if str(n.status.get("host", "")).lower() == host.lower() and self.node_online(n, now):
                return n
        return None

    def registry(self) -> List[Dict[str, Any]]:
        now = time.time()
        stale = 30 * 86400
        out = []
        with self.cond:
            for key, hosts in self.inst_seen.items():
                entries = []
                idn = ""
                for host, rec in hosts.items():
                    if now - float(rec.get("last", 0)) > stale:
                        continue
                    lv = self.inst_live.get(host) or {}
                    host_online = self.online(lv.get("time", 0), now)
                    cur = (lv.get("items") or {}).get(key, {}) if host_online else {}
                    node = self._host_node(host, now)
                    idn = idn or rec.get("idn", "")
                    entries.append({"host": host, "node": node.name if node else None, "online": host_online,
                                    "name": rec.get("name"), "address": rec.get("address"), "driver": rec.get("driver"),
                                    "label": rec.get("label"), "configured": bool(rec.get("configured")),
                                    "options": rec.get("options"),
                                    "detected": bool(rec.get("detected")), "idn": rec.get("idn", ""),
                                    "connected": bool(cur.get("connected")), "lease": cur.get("lease"),
                                    "level": cur.get("level"), "unit": cur.get("unit"), "output": cur.get("output"),
                                    "simulate": cur.get("simulate"), "first_seen": rec.get("first"),
                                    "last_seen": rec.get("last")})
                if not entries:
                    continue
                entries.sort(key=lambda e: (not e["online"], e["host"]))
                parts = [x.strip() for x in idn.split(",")] if idn else []
                network = key.startswith(("net:", "zi:"))
                own = self.owners.get(key) or {}
                out.append({"key": key, "network": network, "shared": network and len(entries) > 1,
                            "owner": own.get("host"), "owner_by": own.get("by"), "owner_time": own.get("time"),
                            "maker": parts[0] if parts else "", "model": parts[1] if len(parts) > 1 else "",
                            "serial": parts[2] if len(parts) > 2 else "", "idn": idn,
                            "connected_on": [e["host"] for e in entries if e["connected"]],
                            "entries": entries})
        out.sort(key=lambda r: (r["entries"][0]["name"] or "", r["key"]))
        return out

    def claim_instrument(self, key: str, host: str, by: str) -> Dict[str, Any]:
        """把共用儀器拉到 host 的群組：其他電腦上連著的會收到 release（中斷）指令。"""
        now = time.time()
        released = []
        with self.cond:
            hosts = self.inst_seen.get(key)
            if not hosts:
                raise HubHTTPError(404, f"Hub 沒有看過儀器 {key}")
            for h, lv in self.inst_live.items():
                if h.lower() == host.lower() or not self.online(lv.get("time", 0), now):
                    continue
                cur = (lv.get("items") or {}).get(key) or {}
                if cur.get("lease"):
                    raise HubHTTPError(409, f"{cur.get('name') or key} 正在 {h} 的量測中使用（{cur['lease']}），"
                                            "量測結束後再拉取")
            self.owners[key] = {"host": host, "by": by, "time": now}
            for h, lv in self.inst_live.items():
                if h.lower() == host.lower() or not self.online(lv.get("time", 0), now):
                    continue
                cur = (lv.get("items") or {}).get(key) or {}
                node = self._host_node(h, now)
                if cur.get("connected") and node is not None:
                    node.commands.append({"id": uuid.uuid4().hex[:12], "cmd": "release", "ts": now,
                                          "args": {"key": key, "owner": host},
                                          "from": {"user": by or "hub"}})
                    released.append(node.name)
            self._touch()
        log.info("儀器 %s 拉取到 %s（%s）", key, host, by)
        return {"ok": True, "key": key, "owner": host, "released": released}

    def release_instrument(self, key: str) -> Dict[str, Any]:
        with self.cond:
            self.owners.pop(key, None)
            self._touch()
        return {"ok": True, "key": key}

    # ---- 指令 ------------------------------------------------------------
    def submit(self, name: str, msg: Dict[str, Any]) -> str:
        with self.cond:
            n = self.node(name)
            if not self.node_online(n):
                raise HubHTTPError(409, f"節點 {n.name} 目前離線")
            cid = str(msg.get("id") or uuid.uuid4().hex[:12])
            n.commands.append(dict(msg, id=cid, ts=time.time()))
            self.cond.notify_all()
            return cid

    def take_commands(self, name: str, wait: float) -> List[Dict[str, Any]]:
        end = time.time() + max(0.0, min(wait, 60.0))
        with self.cond:
            n = self.node(name, create=True)
            while True:
                if not n.offline:
                    n.last_seen = time.time()      # 節點正在等指令 = 活著
                if n.commands:
                    out, n.commands = n.commands, []
                    return out
                left = end - time.time()
                if left <= 0:
                    return []
                self.cond.wait(min(left, 5.0))

    def put_reply(self, cid: str, reply: Dict[str, Any]) -> None:
        with self.cond:
            now = time.time()
            self.replies[cid] = (now, reply)
            for k in [k for k, (t, _r) in self.replies.items() if now - t > 600]:
                del self.replies[k]
            self.cond.notify_all()

    def wait_reply(self, cid: str, wait: float) -> Optional[Dict[str, Any]]:
        end = time.time() + max(0.0, min(wait, 300.0))
        with self.cond:
            while True:
                if cid in self.replies:
                    return self.replies.pop(cid)[1]
                left = end - time.time()
                if left <= 0:
                    return None
                self.cond.wait(min(left, 5.0))

    # ---- 即時資料 ------------------------------------------------------------
    def live_head(self, name: str, head: Dict[str, Any]) -> Dict[str, Any]:
        with self.cond:
            n = self.node(name, create=True)
            n.head = dict(head, first_seq=n.seq + 1, time=time.time())
            self._touch()
            return {"first_seq": n.seq + 1}

    def live_batch(self, name: str, events: List[Dict[str, Any]]) -> int:
        with self.cond:
            n = self.node(name, create=True)
            n.seq += 1
            n.batches.append((n.seq, events))
            for ev in events:
                t = ev.get("t")
                if t == "log":
                    n.log.append({"time": time.time(), "level": ev.get("level", "info"),
                                  "message": str(ev.get("message", ""))[:500]})
                elif t == "remote.files":
                    for f in ev.get("files") or []:
                        n.log.append({"time": time.time(), "level": "info", "message": f"📁 已上傳 {f.get('rel')}"})
            self.cond.notify_all()
            return n.seq

    def live_get(self, name: str, after: int, run: Optional[str], wait: float) -> Dict[str, Any]:
        end = time.time() + max(0.0, min(wait, 60.0))
        with self.cond:
            n = self.node(name, create=True)
            while True:
                head_run = (n.head or {}).get("run_id")
                changed = run is not None and head_run is not None and head_run != run
                if n.seq > after or after > n.seq or changed or time.time() >= end:
                    return {"head": n.head, "newest": n.seq,
                            "oldest": n.batches[0][0] if n.batches else n.seq + 1,
                            "batches": [[s, e] for s, e in n.batches if s > after]}
                self.cond.wait(min(end - time.time(), 5.0))

    # ---- 檔案 ------------------------------------------------------------
    def file_path(self, node: str, rel: str) -> Path:
        return self.files_dir / safe_name(node) / safe_rel(rel)

    def save_upload(self, node: str, rel: str, stream, length: int) -> Dict[str, Any]:
        if length > self.cfg.max_upload:
            raise HubHTTPError(413, "檔案太大")
        dst = self.file_path(node, rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:6]}.part")
        left = length
        with open(tmp, "wb") as f:
            while left > 0:
                chunk = stream.read(min(1 << 20, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left:
            tmp.unlink()
            raise HubHTTPError(400, "上傳中斷")
        os.replace(tmp, dst)
        return self.file_info(dst)

    def file_info(self, p: Path) -> Dict[str, Any]:
        rel = p.relative_to(self.files_dir)
        node, sub = rel.parts[0], Path(*rel.parts[1:]).as_posix()
        st = p.stat()
        return {"node": node, "rel": sub, "size": st.st_size, "mtime": st.st_mtime,
                "url": "/api/files/" + urllib.parse.quote(f"{node}/{sub}")}

    def list_files(self, node: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        base = self.files_dir / safe_name(node) if node else self.files_dir
        if not base.exists():
            return []
        files = [p for p in base.rglob("*") if p.is_file() and not p.name.startswith(".")]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [self.file_info(p) for p in files[:max(1, min(limit, 1000))]]

    # ---- 發佈版本 ------------------------------------------------------------
    def _release_base(self) -> Optional[Path]:
        r = self.cfg.releases_dir
        if r is None or not r.exists():
            return None
        return r / self.cfg.app_name if (r / self.cfg.app_name).is_dir() else r

    def releases(self) -> List[str]:
        base = self._release_base()
        if base is None:
            return []
        out = [p.name.lstrip("vV") for p in base.iterdir() if p.is_dir() and (p / "main.py").exists()]
        return sorted(out, key=parse_version)

    def latest_version(self) -> Optional[str]:
        vs = self.releases()
        return vs[-1] if vs else None

    def release_zip(self, version: str) -> Path:
        base = self._release_base()
        v = str(version).lstrip("vV")
        src = next((base / n for n in (f"v{v}", v) if base is not None and (base / n / "main.py").exists()), None)
        if src is None:
            raise HubHTTPError(404, f"Hub 上沒有 {v} 的發佈版本")
        cache = self.cfg.data_dir / "cache"
        cache.mkdir(exist_ok=True)
        dst = cache / f"{self.cfg.app_name}-v{v}.zip"
        newest = max((p.stat().st_mtime for p in src.rglob("*") if p.is_file()), default=0)
        if dst.exists() and dst.stat().st_mtime >= newest:
            return dst
        tmp = dst.with_suffix(f".{uuid.uuid4().hex[:6]}.tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(src.rglob("*")):
                rel = p.relative_to(src)
                if any(part in (".git", "__pycache__") for part in rel.parts) or not p.is_file():
                    continue
                z.write(p, rel.as_posix())
        os.replace(tmp, dst)
        return dst

    # ---- Hub 本身（版本、自我更新）----------------------------------------------------
    def hub_info(self) -> Dict[str, Any]:
        from . import selfupdate

        info = selfupdate.running_info()
        latest = None
        for v in reversed(self.releases()):
            if selfupdate.release_pkg(self.cfg.releases_dir, self.cfg.app_name, v) is not None:
                latest = v
                break
        cur = selfupdate.read_current(self.cfg.data_dir)
        info.update(latest_release=latest,
                    update_available=bool(latest and parse_version(latest) > parse_version(__version__)),
                    installed=cur.get("version"), installed_healthy=cur.get("healthy"),
                    releases_dir=str(self.cfg.releases_dir) if self.cfg.releases_dir else None,
                    uptime=time.time() - self.started)
        return info

    def update_from_release(self, version: Optional[str]) -> str:
        from . import selfupdate

        version = version or self.hub_info().get("latest_release")
        if not version:
            raise HubHTTPError(404, "發佈資料夾裡沒有含 labhub 的版本（確認 LABHUB_RELEASES 掛載）")
        pkg = selfupdate.release_pkg(self.cfg.releases_dir, self.cfg.app_name, version)
        if pkg is None:
            raise HubHTTPError(404, f"發佈資料夾裡找不到 v{version}/labhub")
        try:
            return selfupdate.install_dir(self.cfg.data_dir, pkg)
        except (OSError, ValueError, SyntaxError) as e:
            raise HubHTTPError(400, f"安裝失敗：{e}")

    def install_zip(self, data: bytes) -> str:
        from . import selfupdate

        try:
            return selfupdate.install_zip(self.cfg.data_dir, data)
        except (OSError, ValueError, SyntaxError, zipfile.BadZipFile) as e:
            raise HubHTTPError(400, f"安裝失敗：{e}")

    # ---- 網頁 / Lab Control 用的總覽 ----------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        with self.cond:
            nodes = [self.node_view(n, now) for n in self.nodes.values()]
            devs = []
            for d in self.devices.values():
                x = dict(d)
                x["online"] = self.online(d.get("last_seen", 0), now) and d.get("state") != "offline"
                x["seen_ago"] = round(now - float(d.get("last_seen", 0)), 1)
                devs.append(x)
        nodes.sort(key=lambda n: (not n["online"], n["name"]))
        devs.sort(key=lambda d: (not d["online"], str(d.get("host", ""))))
        versions = [d.get("version") for d in devs if d.get("version")] + self.releases()
        latest = max(versions, key=parse_version) if versions else None
        return {"server": "labhub", "version": __version__, "time": now, "uptime": now - self.started,
                "offline_after": self.cfg.offline_after, "nodes": nodes, "devices": devs,
                "releases": self.releases(), "latest": latest, "auth": self.cfg.token is not None,
                "owners": self.owner_map(), "hub": self.hub_info(),
                "shared": [r["key"] for r in self.registry() if r["shared"]]}


# ---------------------------------------------------------------------------
Route = Tuple[str, "re.Pattern[str]", Callable[..., None], bool]


class Handler(BaseHTTPRequestHandler):
    server_version = f"LabControlHub/{__version__}"
    hub: HubState = None  # type: ignore[assignment]   # 由 make_server 設定
    routes: List[Route] = []

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401 - 改用 logging
        log.debug("%s %s", self.address_string(), fmt % args)

    # ---- 基本工具 ------------------------------------------------------------
    @property
    def ip(self) -> str:
        return self.client_address[0]

    def _query(self) -> Dict[str, str]:
        q = urllib.parse.urlsplit(self.path).query
        return {k: v[-1] for k, v in urllib.parse.parse_qs(q).items()}

    def _qf(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self._query().get(key, default))
        except ValueError:
            return default

    def _length(self) -> int:
        try:
            return int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return 0

    def _body_bytes(self, limit: int = 64 * 1024 * 1024) -> bytes:
        n = self._length()
        if n > limit:
            raise HubHTTPError(413, "資料太大")
        return self.rfile.read(n) if n else b""

    def _json_body(self) -> Any:
        raw = self._body_bytes()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as e:
            raise HubHTTPError(400, f"JSON 格式錯誤：{e}")

    def _send(self, code: int, body: bytes, ctype: str, headers: Optional[Dict[str, str]] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _html(self, text: str, code: int = 200, headers: Optional[Dict[str, str]] = None) -> None:
        self._send(code, text.encode("utf-8"), "text/html; charset=utf-8", headers)

    def _redirect(self, to: str, headers: Optional[Dict[str, str]] = None) -> None:
        self.send_response(303)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _send_file(self, path: Path, ctype: str = "application/octet-stream", name: Optional[str] = None) -> None:
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        fn = urllib.parse.quote(name or path.name)
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{fn}")
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile, 1 << 20)

    # ---- 權限 ------------------------------------------------------------
    def _given_token(self) -> str:
        a = self.headers.get("Authorization", "")
        if a.lower().startswith("bearer "):
            return a[7:].strip()
        if self.headers.get("X-LabHub-Token"):
            return self.headers["X-LabHub-Token"].strip()
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        if COOKIE in c:
            return urllib.parse.unquote(c[COOKIE].value)
        return self._query().get("token", "")

    def authorized(self) -> bool:
        tok = self.hub.cfg.token
        return tok is None or hmac.compare_digest(self._given_token().encode(), tok.encode())

    # ---- 分派 ------------------------------------------------------------
    def _dispatch(self, method: str) -> None:
        path = urllib.parse.urlsplit(self.path).path
        try:
            for m, rx, fn, auth in self.routes:
                if m != method:
                    continue
                mt = rx.fullmatch(path)
                if mt is None:
                    continue
                if auth and not self.authorized():
                    if path.startswith("/api/"):
                        raise HubHTTPError(401, "token 錯誤或未提供（settings.yaml remote.token）")
                    self._redirect("/login")
                    return
                fn(self, *[urllib.parse.unquote(g) for g in mt.groups()])
                return
            raise HubHTTPError(404, f"找不到 {method} {path}")
        except HubHTTPError as e:
            self._json({"ok": False, "error": str(e)}, e.code)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            log.exception("處理 %s %s 失敗", method, path)
            try:
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
            except OSError:
                pass

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")


def route(method: str, pattern: str, auth: bool = True, handler: Optional[type] = None):
    """登錄路由（handler 預設 Hub 的 Handler；控制代理用自己的 AgentHandler）。"""
    def deco(fn):
        (handler or Handler).routes.append((method, re.compile(pattern), fn, auth))
        return fn
    return deco


N = r"/api/nodes/([^/]+)"


# ---- 網頁 --------------------------------------------------------------------
@route("GET", r"/", auth=True)
def _index(h: Handler) -> None:
    tok = h._query().get("token")
    if tok and h.hub.cfg.token is not None:        # http://NAS:8765/?token=... → 記住 token 後去掉網址上的 token
        c = f"{COOKIE}={urllib.parse.quote(tok)}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax"
        h._redirect("/", {"Set-Cookie": c})
        return
    h._html((STATIC / "index.html").read_text(encoding="utf-8"))


@route("GET", r"/login", auth=False)
def _login_page(h: Handler, error: str = "") -> None:
    page = (STATIC / "login.html").read_text(encoding="utf-8")
    h._html(page.replace("{{error}}", error), 401 if error else 200)


@route("POST", r"/login", auth=False)
def _login(h: Handler) -> None:
    form = urllib.parse.parse_qs(h._body_bytes(65536).decode("utf-8"))
    tok = (form.get("token") or [""])[-1].strip()
    cfg = h.hub.cfg
    if cfg.token is None or hmac.compare_digest(tok.encode(), cfg.token.encode()):
        c = f"{COOKIE}={urllib.parse.quote(tok)}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax"
        h._redirect("/", {"Set-Cookie": c})
    else:
        _login_page(h, "token 不正確")


@route("GET", r"/logout", auth=False)
def _logout(h: Handler) -> None:
    h._redirect("/login", {"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0"})


# ---- 總覽 --------------------------------------------------------------------
@route("GET", r"/api/ping", auth=False)
def _ping(h: Handler) -> None:
    h._json({"ok": True, "server": "labhub", "version": __version__, "time": time.time(),
             "auth": h.authorized()})


@route("GET", r"/api/state")
def _state(h: Handler) -> None:
    h._json(h.hub.snapshot())


@route("POST", r"/api/devices/heartbeat")
def _heartbeat(h: Handler) -> None:
    h._json(h.hub.device_heartbeat(h._json_body(), h.ip))


@route("DELETE", r"/api/devices/([^/]+)")
def _forget(h: Handler, did: str) -> None:
    h.hub.forget_device(did)
    h._json({"ok": True})


# ---- 節點 --------------------------------------------------------------------
@route("POST", N + r"/claim")
def _claim(h: Handler, name: str) -> None:
    h._json(h.hub.claim(name, h._json_body(), h.ip))


@route("POST", N + r"/status")
def _status(h: Handler, name: str) -> None:
    h._json(h.hub.node_status(name, h._json_body(), h.ip))


@route("POST", N + r"/offline")
def _offline(h: Handler, name: str) -> None:
    h.hub.node_offline(name)
    h._json({"ok": True})


@route("GET", N)
def _node(h: Handler, name: str) -> None:
    hub = h.hub
    with hub.cond:
        v = hub.node_view(hub.node(name), time.time())
    h._json(v)


@route("PUT", N + r"/instruments")
def _put_inst(h: Handler, name: str) -> None:
    text = h._body_bytes(8 * 1024 * 1024).decode("utf-8")
    with h.hub.cond:
        h.hub.node(name, create=True).instruments = text
        h.hub._touch()
    h._json({"ok": True})


@route("GET", N + r"/instruments")
def _get_inst(h: Handler, name: str) -> None:
    with h.hub.cond:
        text = h.hub.node(name).instruments
    h._send(200, text.encode("utf-8"), "text/yaml; charset=utf-8")


@route("POST", N + r"/commands")
def _submit(h: Handler, name: str) -> None:
    msg = h._json_body()
    if not isinstance(msg, dict) or not msg.get("cmd"):
        raise HubHTTPError(400, "缺少 cmd")
    msg.setdefault("from", {})
    if not isinstance(msg["from"], dict):
        msg["from"] = {}
    msg["from"].setdefault("user", f"web@{h.ip}")
    msg["from"]["ip"] = h.ip
    cid = h.hub.submit(name, msg)
    wait = h._qf("wait")
    if wait > 0:
        rep = h.hub.wait_reply(cid, wait)
        h._json({"id": cid, "reply": rep if rep is not None else
                 {"id": cid, "ok": False, "error": f"節點 {name} 沒有回應（{wait:.0f} s）：可能忙碌或剛離線"}})
    else:
        h._json({"id": cid})


@route("GET", N + r"/commands")
def _take(h: Handler, name: str) -> None:
    h._json({"commands": h.hub.take_commands(name, h._qf("wait"))})


@route("POST", r"/api/replies/([^/]+)")
def _reply(h: Handler, cid: str) -> None:
    h.hub.put_reply(cid, h._json_body())
    h._json({"ok": True})


@route("GET", r"/api/replies/([^/]+)")
def _get_reply(h: Handler, cid: str) -> None:
    rep = h.hub.wait_reply(cid, h._qf("wait"))
    h._json({"id": cid, "pending": rep is None, "reply": rep})


# ---- 即時資料 ------------------------------------------------------------------
@route("POST", N + r"/live/head")
def _head(h: Handler, name: str) -> None:
    h._json(h.hub.live_head(name, h._json_body()))


@route("POST", N + r"/live/batch")
def _batch(h: Handler, name: str) -> None:
    body = h._json_body()
    h._json({"seq": h.hub.live_batch(name, list(body.get("events") or []))})


@route("GET", N + r"/live")
def _live(h: Handler, name: str) -> None:
    q = h._query()
    h._json(h.hub.live_get(name, int(float(q.get("after", 0))), q.get("run") or None, h._qf("wait")))


# ---- 檔案 ----------------------------------------------------------------------
@route("PUT", r"/api/files/([^/]+)/(.+)")
def _upload(h: Handler, node: str, rel: str) -> None:
    info = h.hub.save_upload(node, rel, h.rfile, h._length())
    h._json(dict(info, ok=True))


@route("GET", r"/api/files")
def _files(h: Handler) -> None:
    q = h._query()
    h._json({"files": h.hub.list_files(q.get("node"), int(q.get("limit", 50)))})


@route("GET", r"/api/files/([^/]+)/(.+)")
def _download(h: Handler, node: str, rel: str) -> None:
    p = h.hub.file_path(node, rel)
    if not p.is_file():
        raise HubHTTPError(404, "沒有這個檔案")
    h._send_file(p)


# ---- 發佈版本 --------------------------------------------------------------------
@route("GET", r"/api/releases")
def _releases(h: Handler) -> None:
    h._json({"app": h.hub.cfg.app_name, "versions": h.hub.releases(), "latest": h.hub.latest_version()})


@route("GET", r"/api/releases/v?([^/]+)\.zip")
def _release_zip(h: Handler, version: str) -> None:
    p = h.hub.release_zip(version)
    h._send_file(p, "application/zip")


# ---- 儀器登錄 ---------------------------------------------------------------------
@route("GET", r"/api/instruments")
def _instruments(h: Handler) -> None:
    h._json({"instruments": h.hub.registry(), "owners": h.hub.owner_map()})


@route("POST", r"/api/instruments/claim")
def _claim_inst(h: Handler) -> None:
    b = h._json_body()
    if not b.get("key") or not b.get("host"):
        raise HubHTTPError(400, "需要 key 與 host")
    h._json(h.hub.claim_instrument(str(b["key"]), str(b["host"]), str(b.get("by") or f"web@{h.ip}")))


@route("POST", r"/api/instruments/release")
def _release_inst(h: Handler) -> None:
    b = h._json_body()
    h._json(h.hub.release_instrument(str(b.get("key", ""))))


# ---- Hub 本身 ----------------------------------------------------------------------
def _restart(h: Handler) -> None:
    from . import selfupdate

    selfupdate.restart_later(1.0, before=lambda: h.hub.save(force=True))


@route("GET", r"/api/hub")
def _hub_info(h: Handler) -> None:
    h._json(h.hub.hub_info())


@route("POST", r"/api/hub/update")
def _hub_update(h: Handler) -> None:
    v = h.hub.update_from_release(h._json_body().get("version"))
    h._json({"ok": True, "installed": v, "restarting": True})
    _restart(h)


@route("POST", r"/api/hub/install")
def _hub_install(h: Handler) -> None:
    v = h.hub.install_zip(h._body_bytes(64 * 1024 * 1024))
    h._json({"ok": True, "installed": v, "restarting": True})
    _restart(h)


@route("POST", r"/api/hub/restart")
def _hub_restart(h: Handler) -> None:
    h._json({"ok": True, "restarting": True})
    _restart(h)


# ---------------------------------------------------------------------------
class HubServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(cfg: Config, host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> Tuple[HubServer, HubState]:
    state = HubState(cfg)
    handler = type("BoundHandler", (Handler,), {"hub": state})
    srv = HubServer((host, port), handler)
    return srv, state


def serve(cfg: Config, host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> None:
    srv, state = make_server(cfg, host, port)
    stop = threading.Event()

    def saver() -> None:
        while not stop.wait(10):
            try:
                state.save()
            except OSError as e:
                log.warning("儲存狀態失敗：%s", e)
    threading.Thread(target=saver, daemon=True).start()
    log.info("Lab Control Hub %s 啟動：http://%s:%d/（資料 %s；發佈 %s；token %s）", __version__, host, port,
             cfg.data_dir, cfg.releases_dir or "未設定", "關閉" if cfg.token is None else "已啟用")
    try:
        from . import selfupdate

        selfupdate.mark_healthy(cfg.data_dir)
    except OSError as e:
        log.warning("無法記錄啟動狀態：%s", e)
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        state.save(force=True)
        srv.server_close()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="labhub", description="Lab Control Hub（NAS 中繼網站）")
    ap.add_argument("--host", default=os.environ.get("LABHUB_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("LABHUB_PORT", DEFAULT_PORT)))
    ap.add_argument("--data", help="資料夾（狀態、上傳的量測檔、token）；預設 $LABHUB_DATA 或 ./labhub-data")
    ap.add_argument("--releases", help="Lab APP 發佈資料夾（含 LabControl/v<版本>/）；預設 $LABHUB_RELEASES")
    ap.add_argument("--token", help="存取 token（預設 $LABHUB_TOKEN，或 data/token.txt，沒有就自動產生）")
    ap.add_argument("--no-auth", action="store_true", help="不檢查 token（只在完全封閉的網路使用）")
    ap.add_argument("--version", action="version", version=f"Lab Control Hub {__version__}")
    a = ap.parse_args(argv)
    from . import selfupdate

    selfupdate.set_restart_argv(list(argv) if argv is not None else sys.argv[1:])
    logging.basicConfig(level=os.environ.get("LABHUB_LOG", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    over: Dict[str, Any] = {"data_dir": Path(a.data) if a.data else None,
                            "releases_dir": Path(a.releases) if a.releases else None}
    if a.no_auth:
        over["token"] = None
    elif a.token:
        over["token"] = a.token
    cfg = Config.from_env(**over)
    if a.no_auth:
        cfg.token = None
    serve(cfg, a.host, a.port)
    return 0
