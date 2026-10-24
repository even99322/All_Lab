"""1.0.25：手機 / 電腦的推播通知（Web Push）——模擬完成、量測完成。每台裝置自己開關、選要收哪些。

    GET    /notify                          → 網頁「🔔 通知」（開關這台裝置的通知、選類別與電腦、送測試）
    GET    /api/notify                      → {key（VAPID 公鑰）, cats, sources（可以只收哪些電腦）, count}
    POST   /api/notify/lookup               {endpoint} → 這台裝置的設定（沒訂閱過 → sub: null）
    POST   /api/notify/subscribe            {subscription, enabled?, cats?, sources?} → 新增 / 更新這台裝置
    PUT    /api/notify/sub/<id>             {enabled?, cats?, sources?}
    DELETE /api/notify/sub/<id>
    POST   /api/notify/sub/<id>/test        → 送一則測試通知（結果直接回傳）
    POST   /api/hfss/event                  ← HFSS 自動化工具 3.8+：模擬做完（不用登入，跟 /api/hfss/usage 一樣）
           {host, user, name, task, label, ok, aborted, elapsed, project, detail}

量測完成：Lab Control 每秒送給 /api/m/<名稱>/update 的摘要裡，量測狀態從進行中變成 finished / aborted / failed
（mobile_routes.MobileStore 記下來，這裡送出）。

推播：瀏覽器的推播服務（Android = Google FCM、iPhone = Apple、Firefox = Mozilla），Hub 要能連外網。
iPhone / iPad 要 iOS 16.4 以上、先「加入主畫面」從 App 打開才能開通知；網站要用 https（tailscale 網址）。
訂閱與金鑰放在 data/push/（subs.json、vapid.json）。密碼學在 webpush.py（只用標準函式庫）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import webpush
from .server import Handler, HubHTTPError, route

log = logging.getLogger(__name__)

CATS = {
    "measure": {"label": "量測結束", "desc": "量測完成、停止或失敗（Lab Control）"},
    "hfss": {"label": "模擬結束", "desc": "HFSS 自動化工具的 S 參數 / 場型 / GIF / 一鍵全流程做完或失敗"},
}
MAX_SUBS = 500
MAX_FAILS = 30                     # 連續失敗這麼多次（例如很久沒開的裝置）→ 自動停用
EVENT_GAP_S = 5.0                  # 同一台電腦同一種事件，這麼短時間內只送一次
_SRC = re.compile(r"^(measure|hfss):[^\s]{1,160}$")


def _s(v: Any, n: int = 120) -> str:
    return str(v if v is not None else "")[:n]


def fmt_dur(sec: Optional[float]) -> str:
    try:
        sec = float(sec or 0)
    except (TypeError, ValueError):
        return ""
    if sec < 60:
        return f"{sec:.0f} 秒"
    m = int(sec // 60)
    if m < 60:
        return f"{m} 分"
    h, m = divmod(m, 60)
    return f"{h} 小時 {m} 分" if m else f"{h} 小時"


class Notifier:
    def __init__(self, data_dir: Path, sender=None) -> None:
        self.dir = Path(data_dir) / "push"
        self.file = self.dir / "subs.json"
        self.lock = threading.Lock()
        self.keys = webpush.VapidKeys.load_or_create(self.dir / "vapid.json")
        saved = self._load()
        self.subs: Dict[str, Dict[str, Any]] = {k: v for k, v in (saved.get("subs") or {}).items() if isinstance(v, dict)}
        self.subject: str = _s(saved.get("subject"), 200)
        self.recent: Dict[str, float] = {}
        self.history: List[Dict[str, Any]] = []
        self._send = sender or webpush.send
        self.q: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.thread = threading.Thread(target=self._loop, name="push", daemon=True)
        self.thread.start()

    # ---- 保存 ----
    def _load(self) -> Dict[str, Any]:
        try:
            return json.loads(self.file.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"subs": self.subs, "subject": self.subject}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.file)
        except OSError as e:
            log.warning("存推播訂閱失敗：%s", e)

    # ---- 訂閱 ----
    @staticmethod
    def sub_id(endpoint: str) -> str:
        return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def public(sub: Dict[str, Any]) -> Dict[str, Any]:
        return {k: sub.get(k) for k in ("id", "enabled", "cats", "sources", "user", "device", "created", "last_ok",
                                        "last_error", "fails", "denied")}

    def subscribe(self, body: Dict[str, Any], user: str, ua: str, allowed: Dict[str, bool]) -> Dict[str, Any]:
        sub = body.get("subscription") if isinstance(body.get("subscription"), dict) else {}
        endpoint = _s(sub.get("endpoint"), 2000)
        keys = sub.get("keys") if isinstance(sub.get("keys"), dict) else {}
        if not endpoint.startswith("https://"):
            raise HubHTTPError(400, "這個瀏覽器給的推播網址不對")
        try:
            webpush.point_from(webpush.unb64u(keys.get("p256dh", "")))
            if len(webpush.unb64u(keys.get("auth", ""))) < 8:
                raise ValueError
        except (ValueError, TypeError):
            raise HubHTTPError(400, "瀏覽器給的推播金鑰格式不對") from None
        sid = self.sub_id(endpoint)
        now = time.time()
        with self.lock:
            old = self.subs.get(sid)
            if old is None and len(self.subs) >= MAX_SUBS:
                raise HubHTTPError(409, f"訂閱的裝置太多（上限 {MAX_SUBS}）")
            cur = dict(old or {"id": sid, "created": now, "enabled": True, "sources": [],
                                "cats": {c: True for c in CATS}, "fails": 0})
            cur.update(endpoint=endpoint, keys={"p256dh": _s(keys.get("p256dh"), 200), "auth": _s(keys.get("auth"), 100)},
                       user=_s(user, 64), device=_s(ua, 120), updated=now, fails=0, last_error="")
            self._apply(cur, body, allowed)
            self.subs[sid] = cur
            self._save()
            return self.public(cur)

    @staticmethod
    def _apply(cur: Dict[str, Any], body: Dict[str, Any], allowed: Dict[str, bool]) -> None:
        if "enabled" in body:
            cur["enabled"] = bool(body["enabled"])
            if cur["enabled"]:
                cur["fails"] = 0
        if isinstance(body.get("cats"), dict):
            cats = dict(cur.get("cats") or {})
            for c in CATS:
                if c in body["cats"]:
                    cats[c] = bool(body["cats"][c])
            cur["cats"] = cats
        if isinstance(body.get("sources"), list):
            cur["sources"] = [s for s in (_s(x, 170) for x in body["sources"][:100]) if _SRC.match(s)]
        cur["denied"] = sorted(c for c, ok in allowed.items() if not ok)     # 沒有權限的類別（例如量測模塊）

    def lookup(self, endpoint: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            sub = self.subs.get(self.sub_id(_s(endpoint, 2000)))
            return self.public(sub) if sub else None

    def get(self, sid: str) -> Dict[str, Any]:
        with self.lock:
            sub = self.subs.get(sid)
            if sub is None:
                raise HubHTTPError(404, "找不到這個裝置的訂閱（可能已失效，請重新開啟通知）")
            return sub

    def update(self, sid: str, body: Dict[str, Any], allowed: Dict[str, bool]) -> Dict[str, Any]:
        with self.lock:
            sub = self.subs.get(sid)
            if sub is None:
                raise HubHTTPError(404, "找不到這個裝置的訂閱（可能已失效，請重新開啟通知）")
            self._apply(sub, body, allowed)
            sub["updated"] = time.time()
            self._save()
            return self.public(sub)

    def remove(self, sid: str) -> bool:
        with self.lock:
            ok = self.subs.pop(sid, None) is not None
            if ok:
                self._save()
            return ok

    def set_subject(self, origin: str) -> None:
        """VAPID 的 sub（聯絡方式）：用開通知的那個 https 網址（Apple 要有效的 https / mailto）。"""
        if origin.startswith("https://") and origin != self.subject:
            with self.lock:
                self.subject = origin[:200]
                self._save()

    # ---- 送出 ----
    def notify(self, cat: str, source: str, title: str, body: str, url: str = "/", tag: str = "",
               dedupe: str = "") -> int:
        """排進佇列；回傳符合條件的裝置數。"""
        now = time.time()
        key = dedupe or f"{cat}|{source}|{title}"
        with self.lock:
            if now - self.recent.get(key, 0) < EVENT_GAP_S:
                return 0
            self.recent[key] = now
            if len(self.recent) > 2000:
                self.recent = {k: t for k, t in self.recent.items() if now - t < 3600}
            targets = [sid for sid, s in self.subs.items() if self._wants(s, cat, source)]
            self.history.append({"time": now, "cat": cat, "source": source, "title": title, "body": body,
                                 "to": len(targets)})
            del self.history[:-50]
        msg = {"title": title, "body": body, "url": url, "tag": tag or key[:60], "cat": cat, "time": now}
        for sid in targets:
            self.q.put({"sid": sid, "msg": msg})
        return len(targets)

    @staticmethod
    def _wants(s: Dict[str, Any], cat: str, source: str) -> bool:
        if not s.get("enabled") or cat in (s.get("denied") or []):
            return False
        if not (s.get("cats") or {}).get(cat, False):
            return False
        srcs = [x for x in (s.get("sources") or []) if x.startswith(cat + ":")]
        return not srcs or source in srcs

    def send_now(self, sid: str, msg: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            sub = dict(self.subs.get(sid) or {})
            subject = self.subject or "mailto:hub@qel.invalid"
        if not sub:
            return {"ok": False, "status": 404, "error": "訂閱已不存在"}
        payload = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        try:
            status, text = self._send(sub, payload, self.keys, subject=subject, ttl=24 * 3600,
                                      urgency="high" if msg.get("cat") != "test" else "normal",
                                      topic=hashlib.sha1(str(msg.get("tag", "")).encode()).hexdigest()[:32])
        except Exception as e:  # noqa: BLE001
            status, text = 0, f"{type(e).__name__}: {e}"
        now = time.time()
        with self.lock:
            cur = self.subs.get(sid)
            if cur is not None:
                if status in (200, 201, 202):
                    cur.update(last_ok=now, fails=0, last_error="")
                elif status in (404, 410):          # 瀏覽器已取消訂閱 / 過期
                    self.subs.pop(sid, None)
                else:
                    cur["fails"] = int(cur.get("fails") or 0) + 1
                    cur["last_error"] = f"{status} {text}"[:200]
                    if cur["fails"] >= MAX_FAILS:
                        cur["enabled"] = False
                self._save()
        ok = status in (200, 201, 202)
        return {"ok": ok, "status": status, "error": "" if ok else (text or "連不到推播服務（Hub 能連外網嗎？）")[:300]}

    def _loop(self) -> None:
        while True:
            job = self.q.get()
            try:
                self.send_now(job["sid"], job["msg"])
            except Exception:  # noqa: BLE001
                log.exception("推播失敗")

    def view(self) -> Dict[str, Any]:
        with self.lock:
            return {"count": len(self.subs), "enabled": sum(1 for s in self.subs.values() if s.get("enabled")),
                    "history": list(self.history[-20:])}

    # ---- 事件 ----
    def hfss_event(self, b: Dict[str, Any], who: str, key: str) -> int:
        task = _s(b.get("label") or b.get("task"), 40) or "模擬"
        ok, aborted = bool(b.get("ok")), bool(b.get("aborted"))
        state = "完成" if ok else ("已中止" if aborted else "失敗")
        icon = "✅" if ok else ("⏹" if aborted else "❌")
        parts = [p for p in (_s(b.get("project"), 120), fmt_dur(b.get("elapsed"))) if p]
        body = f"{who}：" + "，".join(parts) if parts else who
        detail = _s(b.get("detail"), 300)
        if detail:
            body += "\n" + detail
        return self.notify("hfss", f"hfss:{key}", f"{icon} {task}{state}", body, url="/licenses",
                           tag=f"hfss-{key}", dedupe=f"hfss|{key}|{b.get('task')}|{ok}")

    def measure_event(self, station: str, run: Dict[str, Any]) -> int:
        status = _s(run.get("state"), 20)
        text = {"finished": "完成", "aborted": "已停止", "stopped": "已停止", "failed": "失敗"}.get(status, status)
        icon = {"finished": "✅", "failed": "❌"}.get(status, "⏹")
        dur = None
        try:
            dur = float(run.get("finished") or 0) - float(run.get("started") or 0)
        except (TypeError, ValueError):
            pass
        parts = [p for p in (_s(run.get("name"), 120), fmt_dur(dur) if dur and dur > 0 else "") if p]
        body = f"{station}：" + "，".join(parts)
        if run.get("error"):
            body += "\n" + _s(run.get("error"), 200)
        elif run.get("summary"):
            body += "\n" + _s(run.get("summary"), 200)
        return self.notify("measure", f"measure:{station}", f"{icon} 量測{text}", body,
                           url=f"/m#{station}", tag=f"measure-{station}",
                           dedupe=f"measure|{station}|{run.get('id')}|{status}")


def notifier(h: Handler) -> Notifier:
    n = getattr(h.hub, "notifier", None)
    if n is None:
        with _CREATE:
            n = getattr(h.hub, "notifier", None)
            if n is None:
                n = h.hub.notifier = Notifier(h.hub.cfg.data_dir)
    return n


_CREATE = threading.Lock()


def _allowed(h: Handler) -> Dict[str, bool]:
    return {"measure": h.check(True) is None, "hfss": True}


def _user(h: Handler) -> str:
    u = h.user()
    return u.get("username") or ("量測電腦" if h.principal() else "")


def _origin(h: Handler) -> str:
    host = (h.headers.get("Host") or "").strip()
    proto = (h.headers.get("X-Forwarded-Proto") or "").strip().lower()
    return f"https://{host}" if host and proto == "https" else ""


def _own(h: Handler, sub: Dict[str, Any]) -> None:
    u = h.user()
    if u.get("owner"):
        return
    if (sub.get("user") or "") != _user(h):
        raise HubHTTPError(403, "這不是你的裝置")


def sources(h: Handler) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if h.check(True) is None:
        mob = getattr(h.hub, "mobile", None)
        if mob is not None:
            for st in mob.summary().get("stations", []):
                out.append({"id": f"measure:{st['name']}", "cat": "measure", "label": st["name"],
                            "hint": st.get("host") or ""})
    use = getattr(h.hub, "hfss_usage", None)
    if use is not None:
        for it in use.view().get("items", []):
            who = it.get("who") or ""
            out.append({"id": f"hfss:{it['key']}", "cat": "hfss", "label": who or it.get("host") or it["key"],
                        "hint": f"{it.get('host')}／{it.get('user')}" if who else it.get("user") or ""})
    return out


# ---- 路由 ----
@route("GET", r"/notify/?", auth="any")
def _page(h: Handler) -> None:
    from .web_nav import page

    page(h, "notify.html", "notify")


@route("GET", r"/api/notify", auth="any")
def _info(h: Handler) -> None:
    n = notifier(h)
    allowed = _allowed(h)
    out = {"ok": True, "key": n.keys.public_b64, "cats": [dict(v, id=k, allowed=allowed.get(k, True)) for k, v in CATS.items()],
           "sources": sources(h), "user": _user(h), "owner": bool(h.user().get("owner"))}
    try:
        out.update(https_url=h.hub.tls.url("hub") or "", scheme=getattr(h, "scheme", ""))
    except Exception:  # noqa: BLE001
        out.update(https_url="", scheme="")
    out.update(n.view() if h.user().get("owner") else {"count": n.view()["count"]})
    h._json(out)


@route("POST", r"/api/notify/lookup", auth="any")
def _lookup(h: Handler) -> None:
    b = h._json_body() or {}
    h._json({"ok": True, "sub": notifier(h).lookup(_s(b.get("endpoint"), 2000)) if isinstance(b, dict) else None})


@route("POST", r"/api/notify/subscribe", auth="any")
def _subscribe(h: Handler) -> None:
    b = h._json_body()
    if not isinstance(b, dict):
        raise HubHTTPError(400, "格式錯誤")
    n = notifier(h)
    origin = _s(b.get("origin"), 200)
    n.set_subject(origin if origin.startswith("https://") else _origin(h))
    sub = n.subscribe(b, _user(h), _s(b.get("device") or h.headers.get("User-Agent"), 120), _allowed(h))
    h._json({"ok": True, "sub": sub})


@route("PUT", r"/api/notify/sub/([0-9a-f]{20})", auth="any")
def _update(h: Handler, sid: str) -> None:
    b = h._json_body()
    if not isinstance(b, dict):
        raise HubHTTPError(400, "格式錯誤")
    n = notifier(h)
    _own(h, n.get(sid))
    h._json({"ok": True, "sub": n.update(sid, b, _allowed(h))})


@route("DELETE", r"/api/notify/sub/([0-9a-f]{20})", auth="any")
def _delete(h: Handler, sid: str) -> None:
    n = notifier(h)
    try:
        _own(h, n.get(sid))
    except HubHTTPError as e:
        if e.code == 404:
            h._json({"ok": True, "removed": False})
            return
        raise
    h._json({"ok": True, "removed": n.remove(sid)})


@route("POST", r"/api/notify/sub/([0-9a-f]{20})/test", auth="any")
def _test(h: Handler, sid: str) -> None:
    n = notifier(h)
    _own(h, n.get(sid))
    res = n.send_now(sid, {"title": "🔔 量子工程平台", "body": "測試通知：這台裝置收得到通知了。", "url": "/notify",
                           "tag": "test", "cat": "test", "time": time.time()})
    h._json(res)


@route("POST", r"/api/hfss/event", auth=False)
def _hfss_event(h: Handler) -> None:
    try:
        b = json.loads(h._body_bytes(16 * 1024).decode("utf-8") or "{}")
    except ValueError:
        raise HubHTTPError(400, "JSON 格式錯誤") from None
    if not isinstance(b, dict) or not _s(b.get("host"), 64):
        raise HubHTTPError(400, "缺少 host")
    from .hfss_usage import UsageStore, store as usage_store

    host, user = _s(b.get("host"), 64), _s(b.get("user"), 64)
    key = UsageStore.key_of(host, user)
    who = host
    try:
        st = usage_store(h)
        who = st.labels.get(key) or _s(b.get("name"), 40) or host
    except Exception:  # noqa: BLE001
        pass
    h._json({"ok": True, "sent": notifier(h).hfss_event(b, who, key)})


def measure_ended(h: Handler, events: List[Dict[str, Any]]) -> None:
    """mobile_routes 收到「量測結束」時呼叫。"""
    if not events:
        return
    n = notifier(h)
    for ev in events:
        try:
            n.measure_event(ev["station"], ev["run"])
        except Exception:  # noqa: BLE001
            log.exception("量測結束通知失敗")
