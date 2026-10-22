"""1.0.19：HFSS 授權占用——實驗室電腦上的模擬工具（HFSS 自動化工具 3.4+）每 20 秒回報一次，Hub 列出誰在用（1.0.20 起也直接讀授權伺服器）。

    POST /api/hfss/usage                 ← 模擬工具回報（不用登入；只收這些欄位、字串有長度上限）
         {id, host, user, name, version, aedt: {running, count, since}, task: {name, project, started, cores, tasks, gpus} | null,
          closing}
    GET  /api/hfss/usage                 → 每台電腦（線上 / 離線、標註、AEDT 是否開著、正在跑什麼）＋ 授權總數、占用數
    PUT  /api/hfss/usage/label           {key, label} → 標註是誰（登入的人都可以；記在 data/hfss_usage.json）
    PUT  /api/hfss/usage/seats           {seats} → 授權總數（站長；0 = 不顯示剩幾個）
    DELETE /api/hfss/usage/<key>         → 刪掉一台（站長；換電腦 / 不用了）
    GET  /licenses                       → 網頁「授權」

「占用」= 那台電腦開著 AEDT（ansysedt.exe）：開著就占一個 HFSS 授權，不管是不是用工具開的。90 秒沒回報 = 離線。

1.0.20：直接問授權伺服器（跟 lmutil lmstat 一樣準，連沒裝模擬工具的電腦也看得到）
    PUT  /api/hfss/usage/server          {server: "1055@192.168.x.x;1055@100.x.x.x"}（站長）
    回報的回應多一個 lmstat: {server, due}——Hub 一次只叫一台電腦去查（約 30 秒一次），查到的原始輸出送回：
    POST /api/hfss/lmstat                {host, user, server, text | error}（不用登入；要是 Hub 剛叫的那台）
    Hub 用 lmstat.py 解析：每個授權發了幾個、用了幾個、誰在用（電腦、使用者、從幾點開始、幾個）。
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from . import lmstat as lm
from .server import Handler, HubHTTPError, route

OFFLINE_S = 90.0
LM_EVERY = 30.0          # 多久查一次授權伺服器
LM_LEASE = 45.0          # 叫了某台去查，這段時間內不叫別台
LM_STALE = 150.0         # 超過這麼久沒有新資料 → 頁面標「資料過舊」
LM_BAD = 600.0           # 某台查不到（沒有 lmutil 等）→ 10 分鐘內不再叫它
LM_MAX_TEXT = 512 * 1024
MAX_HOSTS = 300
_KEY = re.compile(r"[^A-Za-z0-9_.@-]+")


def _s(v: Any, n: int = 80) -> str:
    return str(v or "")[:n]


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) < 1e12 else None


class UsageStore:
    def __init__(self, data_dir: Path) -> None:
        self.file = Path(data_dir) / "hfss_usage.json"
        self.lock = threading.Lock()
        self.items: Dict[str, Dict[str, Any]] = {}
        saved = self._load()
        self.labels: Dict[str, str] = dict(saved.get("labels") or {})
        self.seats: int = int(saved.get("seats") or 0)
        self.server: str = _s(saved.get("license_server"), 200)
        self.lm: Optional[Dict[str, Any]] = None        # 最近一次查到的（解析後）
        self.lm_from = ""
        self.lm_error: Dict[str, Any] = {}              # 最近一次失敗：{text, from, time}
        self.lm_lease: tuple = ("", 0.0)                # (key, 到期)
        self.lm_bad: Dict[str, float] = {}
        for key, it in (saved.get("hosts") or {}).items():      # 重開 Hub 後還記得有哪些電腦（離線）
            self.items[key] = dict(it, key=key, seen=float(it.get("seen") or 0), aedt={"running": False}, task=None,
                                   closed=True)

    def _load(self) -> Dict[str, Any]:
        try:
            return json.loads(self.file.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        hosts = {k: {f: v.get(f) for f in ("host", "user", "name", "version", "seen", "last_aedt")}
                 for k, v in self.items.items()}
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"labels": self.labels, "seats": self.seats, "license_server": self.server,
                                       "hosts": hosts}, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(self.file)
        except OSError:
            pass

    @staticmethod
    def key_of(host: str, user: str) -> str:
        return _KEY.sub("_", f"{host}@{user}".lower())[:120]

    def report(self, b: Dict[str, Any], ip: str) -> Dict[str, Any]:
        host, user = _s(b.get("host"), 64), _s(b.get("user"), 64)
        if not host:
            raise HubHTTPError(400, "缺少 host")
        key = self.key_of(host, user)
        aedt_in = b.get("aedt") if isinstance(b.get("aedt"), dict) else {}
        aedt = {"running": bool(aedt_in.get("running")), "count": int(_num(aedt_in.get("count")) or 0),
                "since": _num(aedt_in.get("since"))}
        task_in = b.get("task") if isinstance(b.get("task"), dict) else None
        task = None
        if task_in:
            task = {"name": _s(task_in.get("name"), 40), "project": _s(task_in.get("project"), 120),
                    "started": _num(task_in.get("started")), "cores": _num(task_in.get("cores")),
                    "tasks": _num(task_in.get("tasks")), "gpus": _num(task_in.get("gpus"))}
        now = time.time()
        with self.lock:
            if key not in self.items and len(self.items) >= MAX_HOSTS:
                oldest = min(self.items, key=lambda k: self.items[k].get("seen", 0))
                del self.items[oldest]
            prev = self.items.get(key) or {}
            it = {"key": key, "host": host, "user": user, "name": _s(b.get("name"), 40), "version": _s(b.get("version"), 20),
                  "ip": ip, "seen": now, "aedt": aedt, "task": task, "closed": bool(b.get("closing")),
                  "last_aedt": now if aedt["running"] else prev.get("last_aedt")}
            changed = (not prev or prev.get("name") != it["name"] or prev.get("version") != it["version"]
                       or bool((prev.get("aedt") or {}).get("running")) != aedt["running"] or b.get("closing"))
            self.items[key] = it
            if changed:
                self._save()
            due = False if b.get("closing") else self._lm_due(key, now)
        out = {"ok": True, "key": key, "label": self.labels.get(key, "")}
        if self.server:
            out["lmstat"] = {"server": self.server, "due": due, "every": LM_EVERY}
        return out

    # ---- 授權伺服器（lmstat）----
    def _lm_due(self, key: str, now: float) -> bool:
        """這台要不要去查？一次只叫一台；上次查到超過 LM_EVERY 秒才叫。（要拿著 lock 呼叫）"""
        if not self.server or self.lm_bad.get(key, 0) > now:
            return False
        last = max((self.lm or {}).get("time", 0), self.lm_error.get("time", 0))
        if now - last < LM_EVERY - 2:
            return False
        holder, until = self.lm_lease
        if holder and holder != key and until > now:
            return False
        self.lm_lease = (key, now + LM_LEASE)
        return True

    def lmstat(self, b: Dict[str, Any], ip: str) -> Dict[str, Any]:
        host, user = _s(b.get("host"), 64), _s(b.get("user"), 64)
        key = self.key_of(host, user)
        now = time.time()
        with self.lock:
            holder, until = self.lm_lease
            fresh = self.lm and now - self.lm["time"] < LM_EVERY
            if holder != key and (fresh or (holder and until > now)):
                raise HubHTTPError(409, "現在不用查")
            self.lm_lease = ("", 0.0)
            text = str(b.get("text") or "")[:LM_MAX_TEXT]
            err = _s(b.get("error"), 300)
            snap = lm.parse(text, now) if text and not err else None
            if snap and not snap["error"]:
                snap.update(server=_s(b.get("server"), 120), by=key)
                self.lm, self.lm_from, self.lm_error = snap, host, {}
                self.lm_bad.pop(key, None)
                return {"ok": True, "features": len(snap["features"])}
            msg = err or (snap or {}).get("error") or "沒有輸出"
            self.lm_error = {"text": msg, "from": host, "time": now, "tail": text[-600:]}
            if err:                                    # 這台查不了（沒裝 lmutil 等）→ 先換別台
                self.lm_bad[key] = now + LM_BAD
            return {"ok": True, "error": msg}

    def set_server(self, server: str) -> None:
        server = re.sub(r"\s+", "", _s(server, 200))
        if server and not re.fullmatch(r"[A-Za-z0-9_.:@;\[\]-]+", server):
            raise HubHTTPError(400, "格式像 1055@10.0.0.11（多個用 ; 分開）")
        with self.lock:
            if server != self.server:
                self.lm, self.lm_error, self.lm_bad, self.lm_lease = None, {}, {}, ("", 0.0)
            self.server = server
            self._save()

    def _lm_view(self, now: float, tools: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"server": self.server, "ok": False}
        if self.lm_error:
            out["error"] = dict(self.lm_error, age=now - self.lm_error["time"])
        snap = self.lm
        if not snap:
            return out
        age = now - snap["time"]
        devs = []
        for d in lm.devices(snap):
            key = self.key_of(d["host"], d["user"])
            t = tools.get(key) or {}
            devs.append(dict(d, key=key, label=self.labels.get(key, ""), name=t.get("name") or "",
                             who=self.labels.get(key) or t.get("name") or "",
                             tool={"online": t.get("online", False), "version": t.get("version", ""),
                                   "task": t.get("task")} if t else None))
        feats = [{"name": f["name"], "label": f["label"], "issued": f["issued"], "in_use": f["in_use"],
                  "electronics": lm.is_electronics(f["name"]), "note": f["note"]} for f in snap["features"]]
        out.update(ok=True, age=age, stale=age > LM_STALE, time=snap["time"], status=snap["status"],
                   version=snap["version"], from_host=self.lm_from, summary=lm.summary(snap), features=feats,
                   devices=devs)
        return out

    def view(self) -> Dict[str, Any]:
        now = time.time()
        out = []
        with self.lock:
            for key, it in self.items.items():
                online = not it.get("closed") and now - float(it.get("seen") or 0) < OFFLINE_S
                aedt = dict(it.get("aedt") or {})
                using = online and bool(aedt.get("running"))
                out.append(dict(it, online=online, using=using, label=self.labels.get(key, ""),
                                who=self.labels.get(key) or it.get("name") or "",
                                aedt=aedt if online else {"running": False}, task=it.get("task") if online else None))
            lmv = self._lm_view(now, {x["key"]: x for x in out})
        out.sort(key=lambda x: (not x["using"], not x["online"], -(x.get("seen") or 0)))
        used = sum(x["aedt"].get("count") or 1 for x in out if x["using"])
        return {"items": out, "seats": self.seats, "used": used, "online": sum(1 for x in out if x["online"]),
                "time": now, "offline_s": OFFLINE_S, "lm": lmv}

    def set_label(self, key: str, label: str) -> None:
        with self.lock:
            in_lm = any(self.key_of(d["host"], d["user"]) == key for d in lm.devices(self.lm or {}))
            if key not in self.items and key not in self.labels and not in_lm:
                raise HubHTTPError(404, "沒有這台電腦")
            label = _s(label, 40).strip()
            if label:
                self.labels[key] = label
            else:
                self.labels.pop(key, None)
            self._save()

    def set_seats(self, seats: int) -> None:
        with self.lock:
            self.seats = max(0, min(int(seats), 999))
            self._save()

    def remove(self, key: str) -> None:
        with self.lock:
            self.items.pop(key, None)
            self.labels.pop(key, None)
            self._save()


def store(h: Handler) -> UsageStore:
    st = getattr(h.hub, "hfss_usage", None)
    if st is None:
        st = h.hub.hfss_usage = UsageStore(h.hub.cfg.data_dir)
    return st


def _by(h: Handler) -> str:
    try:
        return h.user().get("username", "") or h.ip
    except Exception:  # noqa: BLE001
        return h.ip


@route("POST", r"/api/hfss/usage", auth=False)
def _report(h: Handler) -> None:
    try:
        b = json.loads(h._body_bytes(64 * 1024).decode("utf-8") or "{}")
    except ValueError:
        raise HubHTTPError(400, "JSON 格式錯誤") from None
    if not isinstance(b, dict):
        raise HubHTTPError(400, "格式錯誤")
    h._json(store(h).report(b, h.ip))


@route("GET", r"/api/hfss/usage", auth="any")
def _view(h: Handler) -> None:
    h._json(store(h).view())


@route("PUT", r"/api/hfss/usage/label", auth="any")
def _label(h: Handler) -> None:
    b = h._json_body() or {}
    key = str(b.get("key") or "")
    store(h).set_label(key, str(b.get("label") or ""))
    plat = getattr(h.hub, "platform", None)
    if plat is not None:
        plat.audit(_by(h), "hfss-label", f"{key} = {b.get('label') or '（清除）'}")
    h._json({"ok": True})


@route("PUT", r"/api/hfss/usage/seats", auth="owner")
def _seats(h: Handler) -> None:
    b = h._json_body() or {}
    try:
        store(h).set_seats(int(b.get("seats") or 0))
    except (TypeError, ValueError):
        raise HubHTTPError(400, "授權數要是整數") from None
    h._json({"ok": True})


@route("PUT", r"/api/hfss/usage/server", auth="owner")
def _server(h: Handler) -> None:
    b = h._json_body() or {}
    store(h).set_server(str(b.get("server") or ""))
    plat = getattr(h.hub, "platform", None)
    if plat is not None:
        plat.audit(_by(h), "hfss-license-server", str(b.get("server") or "（清除）"))
    h._json({"ok": True})


@route("POST", r"/api/hfss/lmstat", auth=False)
def _lmstat(h: Handler) -> None:
    try:
        b = json.loads(h._body_bytes(1024 * 1024).decode("utf-8") or "{}")
    except ValueError:
        raise HubHTTPError(400, "JSON 格式錯誤") from None
    if not isinstance(b, dict) or not b.get("host"):
        raise HubHTTPError(400, "格式錯誤")
    h._json(store(h).lmstat(b, h.ip))


@route("DELETE", r"/api/hfss/usage/([A-Za-z0-9_.@-]+)", auth="owner")
def _remove(h: Handler, key: str) -> None:
    store(h).remove(key)
    h._json({"ok": True})


@route("GET", r"/licenses/?", auth="any")
def _page(h: Handler) -> None:
    from .web_nav import page

    page(h, "licenses.html", "licenses")
