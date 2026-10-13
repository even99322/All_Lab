"""手機看即時量測（1.0.4）：把這台電腦正在進行的量測整理成很小的摘要，定期送到 Hub（``/api/m/<名稱>/update``）。

每一次量測（本機或量測節點）都會送：
* 狀態、進度、經過 / 剩餘時間、目前的設定值；
* 最後一條 trace（|S21| dB 對頻率，最多 TRACE_POINTS 點）與最低點；
* 熱圖：目前這一段外圈，每走一步一列（最多 HEAT_ROWS 個頻率點）；外圈換下一步 → 新的一張；
* 只有純量通道時：數值對內圈的曲線；
* 最近幾行紀錄。

不在量測執行緒做任何計算或網路：事件只放進佇列，另一條執行緒每 ``interval`` 秒整理、上傳一次
（量測快的時候中間的 trace 只送最後一條，熱圖每一列都會送）。Hub 不在或還沒找到 → 只保留狀態，連上後補送整張。
"""
from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

TRACE_POINTS = 1000
HEAT_ROWS = 300
SERIES_POINTS = 4000
END_TEXT = {"finished": "完成", "aborted": "已停止", "stopped": "已停止", "failed": "失敗"}
TOPICS = ("run.started", "run.progress", "point.shot", "point.rollback", "run.state", "run.finished", "log",
          "run.hook")


def reduce_extreme(y: np.ndarray, x: Optional[np.ndarray], limit: int) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    """縮成最多 limit 點，每一格取離中位數最遠的值（窄的 dip 不會被抹掉）；x 取那一格的平均。"""
    y = np.asarray(y, dtype=float).reshape(-1)
    n = y.size
    k = int(math.ceil(n / max(1, limit)))
    if k <= 1:
        return y, x
    m = int(math.ceil(n / k))
    pad = m * k - n
    yy = np.concatenate([y, np.full(pad, np.nan)]) if pad else y
    blocks = yy.reshape(m, k)
    finite = y[np.isfinite(y)]
    center = float(np.median(finite)) if finite.size else 0.0
    dev = np.where(np.isfinite(blocks), np.abs(blocks - center), -1.0)
    out = blocks[np.arange(m), np.argmax(dev, axis=1)]
    if x is None:
        return out, None
    xx = np.asarray(x, dtype=float).reshape(-1)
    xx = np.concatenate([xx, np.full(pad, np.nan)]) if pad else xx
    with np.errstate(invalid="ignore"):
        return out, np.nanmean(xx.reshape(m, k), axis=1)


def _db(z: Any) -> np.ndarray:
    a = np.asarray(z)
    if np.iscomplexobj(a):
        with np.errstate(divide="ignore"):
            return 20.0 * np.log10(np.abs(a) + 1e-30)
    return a.astype(float)


def _num(v: Any) -> Any:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 6) if math.isfinite(f) else None


def _list(a: np.ndarray, digits: int = 3) -> List[Any]:
    return [None if not math.isfinite(v) else round(float(v), digits) for v in np.asarray(a, dtype=float)]


class LivePublisher:
    """``hub_fn()`` → Hub 或 None；``name_fn()`` → 這台在手機上顯示的名稱，None = 不送（不是量測電腦也沒在量）。"""

    def __init__(self, station, hub_fn: Callable[[], Any], name_fn: Callable[[], Optional[str]],
                 interval: float = 1.0, idle_every: float = 15.0) -> None:
        self.station = station
        self.hub_fn, self.name_fn = hub_fn, name_fn
        self.interval, self.idle_every = float(interval), float(idle_every)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._tokens: List[int] = []
        self._thread: Optional[threading.Thread] = None
        self.run: Optional[Dict[str, Any]] = None       # 目前 / 上一次的量測
        self.chan: Optional[Dict[str, Any]] = None
        self.plan = None
        self._x: Optional[np.ndarray] = None            # 顯示通道的頻率（縮小前）
        self._pending: List[Tuple[int, Dict[str, Any]]] = []
        self._trace: Optional[Dict[str, Any]] = None
        self.frame: Optional[Dict[str, Any]] = None     # 熱圖這一張：key、label、各列
        self._sent_rows: Dict[int, int] = {}            # 已送出的列 → 版本
        self._row_version = 0
        self.rows: Dict[int, Tuple[int, List[Any]]] = {}   # col → (版本, 值)
        self.series: Dict[int, Tuple[int, Any]] = {}
        self._clear_from: Optional[int] = None
        self._need_full = True
        self._dirty = True
        self._last_post = 0.0
        self.logs: List[Dict[str, Any]] = []
        self.ever_ran = False
        self.last_error = ""

    # ---- 生命週期 -------------------------------------------------------------
    def start(self) -> "LivePublisher":
        bus = getattr(self.station, "bus", None)
        if bus is not None:
            for t in TOPICS:
                self._tokens.append(bus.subscribe(t, self._on_event))
        self._thread = threading.Thread(target=self._loop, name="live-publish", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        bus = getattr(self.station, "bus", None)
        for t in self._tokens:
            try:
                bus.unsubscribe(t)
            except Exception:  # noqa: BLE001
                pass
        self._tokens.clear()

    # ---- 事件（量測執行緒：只記下來） ---------------------------------------------
    def _on_event(self, topic: str, p: Dict[str, Any]) -> None:
        try:
            with self._lock:
                if topic == "run.started":
                    self._started(p)
                elif topic == "point.shot":
                    self._pending.append((int(p.get("index", 0)), {"shot": p.get("shot") or {},
                                                                  "setpoints": dict(p.get("setpoints") or {})}))
                elif topic == "point.rollback":
                    self._pending.append((int(p.get("index", 0)), {"rollback": True}))
                elif topic == "run.progress" and self.run is not None:
                    self.run.update(index=p.get("index"), total=p.get("total"), elapsed=_num(p.get("elapsed")),
                                    eta=_num(p.get("eta")),
                                    setpoints={str(k): str(v) for k, v in (p.get("display") or {}).items()})
                elif topic == "run.state" and self.run is not None:
                    self.run["state"] = p.get("state")
                elif topic == "run.finished" and self.run is not None:
                    ds = p.get("dataset")
                    self.run.update(state=p.get("status"), finished=time.time(), error=str(p.get("error") or ""),
                                    summary=ds.summary() if ds is not None and hasattr(ds, "summary") else "")
                    status = str(p.get("status") or "")
                    self._add_log("info" if status == "finished" else "warn",
                                  f"結束：{END_TEXT.get(status, status)}" + (f"（{p.get('error')}）" if p.get("error") else ""))
                elif topic == "log":
                    self._add_log(str(p.get("level", "info")), str(p.get("message", "")))
                elif topic == "run.hook":
                    self._add_log("warn", f"{p.get('hook')}：{p.get('reason')}")
                self._dirty = True
            if topic in ("run.started", "run.finished", "run.state"):
                self._wake.set()
        except Exception as e:  # noqa: BLE001 — 只是給手機看的，不影響量測
            log.debug("即時摘要：%s", e)

    def _add_log(self, level: str, message: str) -> None:
        self.logs.append({"time": time.time(), "level": level, "message": message[:300]})
        del self.logs[:-30]

    def _started(self, p: Dict[str, Any]) -> None:
        from ..measure.runner import Runner

        ds = p.get("dataset")
        runner = next((r for r in list(Runner._instances) if r.run_id == p.get("run_id")), None)
        self.plan = runner.plan if runner is not None else None
        self.ever_ran = True
        self.run = {"id": p.get("run_id"), "name": getattr(ds, "name", "量測"), "state": "running",
                    "started": time.time(), "index": 0, "total": len(self.plan) if self.plan is not None else None,
                    "elapsed": 0.0, "eta": None, "setpoints": {}, "error": "", "summary": ""}
        chans = list(getattr(ds, "channels", []) or [])
        vec = next((c for c in chans if getattr(c, "vector", False) and getattr(c, "complex", False)), None) or \
            next((c for c in chans if getattr(c, "vector", False)), None)
        self._x = None
        if vec is not None:
            x = getattr(vec, "x_values", None)
            self._x = None if x is None else np.asarray(x, dtype=float).reshape(-1)
            xu = getattr(vec, "x_unit", "") or ""
            scale, unit = (1e-9, "GHz") if xu == "Hz" else (1.0, xu)
            self.chan = {"name": vec.name, "kind": "trace", "unit": "dB" if vec.complex else (vec.unit or ""),
                         "x_name": getattr(vec, "x_name", "") or "", "x_unit": unit, "x_scale": scale}
        elif chans:
            c = chans[0]
            self.chan = {"name": c.name, "kind": "scalar", "unit": getattr(c, "unit", "") or ""}
        else:
            self.chan = None
        self.frame = None
        self.rows, self.series, self._pending, self._trace = {}, {}, [], None
        self._clear_from, self._need_full = None, True

    # ---- 整理（上傳執行緒） ----------------------------------------------------
    def _axes(self) -> List[Any]:
        return list(getattr(self.plan, "axes", []) or [])

    def _frame_for(self, mi: Tuple[int, ...]) -> Dict[str, Any]:
        axes = self._axes()
        key = list(mi[:-1])
        parts = []
        for a, i in zip(axes[:-1], key):
            try:
                v = float(np.asarray(a.values)[i]) * float(getattr(a, "display_scale", 1.0) or 1.0)
                parts.append(f"{a.name} = {v:.6g} {getattr(a, 'display_unit', '') or ''}".strip())
            except (IndexError, TypeError, ValueError):
                parts.append(f"{a.name} #{i + 1}")
        inner = axes[-1] if axes else None
        cols: List[Any] = []
        if inner is not None:
            scale = float(getattr(inner, "display_scale", 1.0) or 1.0)
            cols = _list(np.asarray(inner.values, dtype=float) * scale, 6)
        out = {"key": key, "label": "、".join(parts), "col_name": getattr(inner, "name", "") if inner else "",
               "col_unit": (getattr(inner, "display_unit", "") or "") if inner else "", "cols": cols,
               "y0": None, "y1": None, "y_label": ""}
        chan = self.chan or {}
        if chan.get("kind") == "trace":
            scale = float(chan.get("x_scale", 1.0))
            if self._x is not None and self._x.size:
                out.update(y0=_num(self._x[0] * scale), y1=_num(self._x[-1] * scale))
            out["y_label"] = f"{chan.get('x_name') or 'x'} [{chan.get('x_unit') or '-'}]"
        else:
            out["y_label"] = f"{chan.get('name', '')} [{chan.get('unit') or '-'}]"
        return out

    def _digest(self) -> None:
        """把排隊的點變成熱圖列 / 純量點與最後一條 trace（在上傳執行緒做）。"""
        with self._lock:
            pending, self._pending = self._pending, []
            chan, plan = self.chan, self.plan
        if not pending or chan is None:
            return
        last_trace = None
        for index, item in pending:
            mi = tuple(int(i) for i in plan.multi_index(index)) if plan is not None else (index,)
            key = list(mi[:-1])
            if self.frame is None or self.frame["key"] != key:
                if item.get("rollback") and self.frame is not None:
                    continue
                self.frame = self._frame_for(mi)
                self.rows, self.series, self._need_full = {}, {}, True
            col = mi[-1]
            if item.get("rollback"):
                self.rows = {c: v for c, v in self.rows.items() if c < col}
                self.series = {c: v for c, v in self.series.items() if c < col}
                self._clear_from = col if self._clear_from is None else min(self._clear_from, col)
                continue
            value = item["shot"].get(chan["name"])
            if value is None:
                continue
            self._row_version += 1
            if chan["kind"] == "trace":
                y = _db(value).reshape(-1)
                row, _ = reduce_extreme(y, None, HEAT_ROWS)
                self.rows[col] = (self._row_version, _list(row, 2))
                last_trace = (index, y)
            else:
                a = np.asarray(value)
                v = float(np.abs(a)) if np.iscomplexobj(a) else float(np.asarray(a, dtype=float).reshape(-1)[0])
                self.series[col] = (self._row_version, _num(v))
        if last_trace is not None:
            index, y = last_trace
            x = self._x if self._x is not None and self._x.size == y.size else np.arange(y.size, dtype=float)
            scale = chan.get("x_scale", 1.0) if self._x is not None and self._x.size == y.size else 1.0
            yr, xr = reduce_extreme(y, x * scale, TRACE_POINTS)
            k = int(np.nanargmin(y)) if np.isfinite(y).any() else 0
            self._trace = {"index": index, "x": _list(xr, 9), "y": _list(yr, 3),
                           "min": {"x": _num(x[k] * scale), "y": _num(y[k])}}

    def payload(self, full: bool = False) -> Dict[str, Any]:
        """這一次要送的內容；full = 整張熱圖（Hub 沒有這一張時）。"""
        self._digest()
        from .. import __version__
        from .hub import hostname

        with self._lock:
            run = dict(self.run) if self.run is not None else None
            logs = list(self.logs)
        body: Dict[str, Any] = {"host": hostname(), "version": __version__, "time": time.time(), "run": run,
                                "chan": self.chan, "log": logs}
        if self.frame is not None:
            full = full or self._need_full
            frame = {k: self.frame[k] for k in ("key", "label", "col_name", "col_unit", "y0", "y1", "y_label")}
            frame["id"] = f"{(run or {}).get('id')}:{','.join(map(str, self.frame['key']))}"
            frame["reset"] = bool(full)
            if full:
                frame["cols"] = self.frame["cols"]
            src = self.rows if (self.chan or {}).get("kind") == "trace" else self.series
            frame["rows" if (self.chan or {}).get("kind") == "trace" else "series"] = {
                str(c): v for c, (ver, v) in src.items() if full or self._sent_rows.get(c) != ver}
            if self._clear_from is not None and not full:
                frame["clear_from"] = self._clear_from
            body["frame"] = frame
        if self._trace is not None:
            body["trace"] = self._trace
        return body

    def _mark_sent(self) -> None:
        src = self.rows if (self.chan or {}).get("kind") == "trace" else self.series
        self._sent_rows = {c: ver for c, (ver, _v) in src.items()}
        self._need_full = False
        self._clear_from = None

    def publish_once(self) -> bool:
        """整理並上傳一次；回傳是否送出。"""
        hub = self.hub_fn()
        name = self.name_fn()
        if hub is None or not name:
            return False
        body = self.payload()
        try:
            r = hub.post("/api/m/" + _quote(name) + "/update", body)
        except Exception as e:  # noqa: BLE001
            if str(e) != self.last_error:
                log.info("手機即時量測：送不到 Hub（%s）", e)
                self.last_error = str(e)
            self._need_full = True
            return False
        self.last_error = ""
        if isinstance(r, dict) and r.get("need_full"):
            self._need_full = True
            self._dirty = True
            return True
        self._mark_sent()
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.interval)
            self._wake.clear()
            if self._stop.is_set():
                return
            now = time.time()
            with self._lock:
                dirty = self._dirty or bool(self._pending)
            if not dirty and now - self._last_post < self.idle_every:
                continue
            with self._lock:
                self._dirty = False
            try:
                if self.publish_once():
                    self._last_post = now
            except Exception as e:  # noqa: BLE001
                log.debug("手機即時量測：%s", e)


def _quote(name: str) -> str:
    import urllib.parse

    return urllib.parse.quote(str(name), safe="")
