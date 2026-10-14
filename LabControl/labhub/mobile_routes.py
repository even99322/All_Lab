"""手機看即時量測（1.0.4）：Lab Control 送來的量測摘要（labcontrol/remote/live_publish.py）與手機網頁 ``/m``。

    POST /api/m/<名稱>/update      ← Lab Control 每秒送一次（狀態、進度、最後一條 trace、熱圖新增的列）
                                     → {ok}；Hub 沒有那一張熱圖（剛重新啟動）→ {need_full: true}，下次送整張
    GET  /api/m                    → 所有量測電腦的概況（手機首頁）
    GET  /api/m/<名稱>?frame=&since= → 一台的完整狀態；frame 相同時熱圖只回傳版本 > since 的列
    GET  /m                        → 手機網頁（Android / iOS 的瀏覽器都可以，「加到主畫面」像 App 一樣開）

權限：跟 /nodes 一樣（裝置 token，或有量測權限的論文庫帳號）。資料只放在記憶體（Hub 重新啟動後由 Lab Control 補送）。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from .server import STATIC, Handler, HubHTTPError, route

FRAME_KEYS = ("id", "key", "label", "col_name", "col_unit", "y0", "y1", "y_label")
ONLINE_S = 45.0                 # 這麼久沒有消息 → 顯示「沒有回報」
KEEP_S = 7 * 24 * 3600          # 這麼久沒有消息 → 從清單拿掉


class MobileStore:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.stations: Dict[str, Dict[str, Any]] = {}
        self.ver = 0

    def update(self, name: str, body: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        with self.lock:
            st = self.stations.setdefault(name, {"name": name, "frame": None})
            run = body.get("run")
            old_run = (st.get("run") or {}).get("id")
            for k in ("host", "version", "run", "chan", "log"):
                if k in body:
                    st[k] = body[k]
            if "trace" in body:
                st["trace"] = body["trace"]
            st["updated"] = now
            if run and run.get("id") != old_run and "frame" not in body:
                st["frame"], st["trace"] = None, None
            f = body.get("frame")
            if isinstance(f, dict):
                cur = st.get("frame")
                if f.get("reset"):
                    self.ver += 1
                    cur = st["frame"] = {k: f.get(k) for k in FRAME_KEYS}
                    cur.update(cols=f.get("cols") or [], rows={}, series={}, reset_ver=self.ver)
                elif cur is None or cur.get("id") != f.get("id"):
                    return {"ok": True, "need_full": True}
                cf = f.get("clear_from")
                if cf is not None:
                    self.ver += 1
                    for kind in ("rows", "series"):
                        cur[kind] = {c: v for c, v in cur[kind].items() if c < int(cf)}
                    cur["reset_ver"] = self.ver
                for kind in ("rows", "series"):
                    for col, values in (f.get(kind) or {}).items():
                        self.ver += 1
                        cur[kind][int(col)] = (self.ver, values)
            return {"ok": True}

    def summary(self) -> Dict[str, Any]:
        now = time.time()
        out = []
        with self.lock:
            for name in sorted(self.stations):
                st = self.stations[name]
                age = now - st.get("updated", 0)
                if age > KEEP_S:
                    continue
                run = st.get("run") or None
                out.append({"name": name, "host": st.get("host"), "version": st.get("version"), "age": round(age, 1),
                            "online": age < ONLINE_S,
                            "run": None if run is None else {k: run.get(k) for k in
                                                             ("name", "state", "index", "total", "eta", "elapsed")}})
        return {"ok": True, "time": now, "stations": out}

    def detail(self, name: str, frame_id: Optional[str], since: int) -> Dict[str, Any]:
        now = time.time()
        with self.lock:
            st = self.stations.get(name)
            if st is None:
                raise HubHTTPError(404, f"沒有 {name} 的量測")
            out = {k: st.get(k) for k in ("name", "host", "version", "run", "chan", "log", "trace")}
            out.update(age=round(now - st.get("updated", 0), 1), online=now - st.get("updated", 0) < ONLINE_S,
                       ver=self.ver, time=now)
            f = st.get("frame")
            if f is not None:
                same = frame_id == f["id"] and since >= f.get("reset_ver", 0)
                frame = {k: f.get(k) for k in FRAME_KEYS + ("cols",)}
                frame["reset"] = not same
                for kind in ("rows", "series"):
                    frame[kind] = {str(c): v for c, (ver, v) in f[kind].items() if not same or ver > since}
                out["frame"] = frame
            else:
                out["frame"] = None
            return out


def store(h: Handler) -> MobileStore:
    hub = h.hub
    st = getattr(hub, "mobile", None)
    if st is None:
        st = hub.mobile = MobileStore()
    return st


@route("POST", r"/api/m/([^/]+)/update")
def _update(h: Handler, name: str) -> None:
    body = h._json_body()
    if not isinstance(body, dict):
        raise HubHTTPError(400, "格式錯誤")
    h._json(store(h).update(name[:80], body))


@route("GET", r"/api/m")
def _list(h: Handler) -> None:
    h._json(store(h).summary())


@route("GET", r"/api/m/([^/]+)")
def _detail(h: Handler, name: str) -> None:
    q = h._query()
    try:
        since = int(q.get("since", -1))
    except ValueError:
        since = -1
    h._json(store(h).detail(name, q.get("frame") or None, since))


@route("GET", r"/(?:m|live)/?")
def _page(h: Handler) -> None:
    from .web_nav import page

    page(h, "mobile.html", "live")


@route("GET", r"/m/manifest\.webmanifest", auth=False)
def _manifest(h: Handler) -> None:
    h._send(200, (STATIC / "mobile.webmanifest").read_bytes(), "application/manifest+json")


@route("GET", r"/m/icon-(180|192|512)\.png", auth=False)
def _icon(h: Handler, size: str) -> None:
    h._send(200, (STATIC / f"m-icon-{size}.png").read_bytes(), "image/png")
