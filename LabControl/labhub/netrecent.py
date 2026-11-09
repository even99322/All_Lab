"""1.0.34：網路用量的「即時」——最近 25 小時、每分鐘一格的流量，給 1 分 / 10 分 / 30 分 / 1 / 3 / 6 小時 / 1 天的總量與速率。

來源（key）：
    router        路由器 Traffic Meter（每 2 分鐘讀一次；路由器以 MB 為單位，小量會延後才出現）
    nas           NAS 網卡（SNMP，每 20 秒）
    hub:remote    Hub 對區網以外（VPN / NAS 本機轉入 / 外網）送收的量（每個請求結束時記）
    hub:lan       Hub 對區網
    pc:<id>       流量監看程式回報的電腦（每分鐘回報一次今天的累計，差值記在回報那一分鐘）
    ts:<id>       Tailscale 裝置跟 NAS 之間（每 20 秒）
每一格存 [up, down]；方向跟網路用量頁其他地方一樣（路由器：上傳 / 下載；NAS、Hub、Tailscale：NAS 送出 / 收到；
電腦：上傳 / 下載）。「目前速率」= 最近一次讀到的差值 ÷ 間隔（電腦用它自己回報的速率）。
存在 Hub 的 data/netusage_recent.json（每分鐘存一次）。只用標準函式庫。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

KEEP_S = 25 * 3600
WINDOWS = (60, 600, 1800, 3600, 3 * 3600, 6 * 3600, 86400)
MAX_POINTS = 1440


class Recent:
    def __init__(self, path: Path, now=time.time) -> None:
        self.path = Path(path)
        self.now = now
        self.lock = threading.Lock()
        self.data: Dict[str, Dict[int, List[int]]] = {}
        self.rate: Dict[str, Tuple[float, float, float]] = {}     # key → (up B/s, down B/s, 讀取時間)
        self._saved = 0.0
        self._dirty = False
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            for k, v in (d.get("series") or {}).items():
                if isinstance(v, dict):
                    self.data[str(k)] = {int(m): [int(x[0]), int(x[1])] for m, x in v.items()
                                         if isinstance(x, list) and len(x) == 2}
        except (OSError, ValueError, TypeError):
            pass

    # ---- 記 ----
    def add(self, key: str, up: int, down: int, t: Optional[float] = None, dt: Optional[float] = None) -> None:
        if up < 0 or down < 0:
            return
        t = self.now() if t is None else t
        m = int(t // 60) * 60
        with self.lock:
            s = self.data.setdefault(key, {})
            cell = s.get(m)
            if cell is None:
                s[m] = [int(up), int(down)]
            else:
                cell[0] += int(up)
                cell[1] += int(down)
            if dt and dt > 0:
                self.rate[key] = (up / dt, down / dt, t)
            self._dirty = True

    def set_rate(self, key: str, up: float, down: float, t: Optional[float] = None) -> None:
        with self.lock:
            self.rate[key] = (float(up), float(down), self.now() if t is None else t)

    def forget(self, key: str) -> None:
        with self.lock:
            self.data.pop(key, None)
            self.rate.pop(key, None)
            self._dirty = True

    # ---- 讀 ----
    def window(self, key: str, seconds: int, series: bool = False, now: Optional[float] = None) -> Dict[str, Any]:
        now = self.now() if now is None else now
        cur = int(now // 60) * 60
        start = cur - (max(60, int(seconds)) // 60 - 1) * 60     # 含目前這一分鐘
        with self.lock:
            s = self.data.get(key) or {}
            up = down = 0
            pts: List[List[int]] = []
            for m in range(start, cur + 60, 60):
                c = s.get(m)
                if c:
                    up += c[0]
                    down += c[1]
                if series:
                    pts.append([m, c[0] if c else 0, c[1] if c else 0])
            r = self.rate.get(key)
        out: Dict[str, Any] = {"up": up, "down": down}
        if r and now - r[2] < 180:
            out["rate"] = {"up": r[0], "down": r[1], "t": r[2]}
        if series:
            step = 60
            if len(pts) > MAX_POINTS:                            # 不會發生（1 天 = 1440 格），保險
                k = -(-len(pts) // MAX_POINTS)
                pts = [[pts[i][0], sum(p[1] for p in pts[i:i + k]), sum(p[2] for p in pts[i:i + k])]
                       for i in range(0, len(pts), k)]
                step *= k
            out["series"] = pts
            out["step"] = step
        return out

    def keys(self) -> List[str]:
        with self.lock:
            return list(self.data)

    # ---- 存 ----
    def save(self, force: bool = False) -> None:
        now = self.now()
        with self.lock:
            if not (self._dirty or force) or (not force and now - self._saved < 60):
                return
            cut = int(now // 60) * 60 - KEEP_S
            for k in list(self.data):
                s = self.data[k]
                for m in [m for m in s if m < cut]:
                    del s[m]
                if not s:
                    del self.data[k]
            text = json.dumps({"series": {k: {str(m): v for m, v in s.items()} for k, s in self.data.items()}},
                              separators=(",", ":"))
            self._dirty = False
            self._saved = now
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass
