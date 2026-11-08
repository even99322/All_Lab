"""計量：每次讀網卡計數，只把「這次和上次都連在實驗室網段」的網卡的差值加到今天。

    sample() → {"t", "boot", "ifaces": {名稱: {"rx", "tx", "ips": [...]}}}（counters.read() 產生；測試可以換掉）

規則：
  * 那張網卡現在有實驗室網段的 IPv4 位址，而且上次讀的時候也有 → 差值算進今天（下載 = rx，上傳 = tx）；
  * 剛連上 / 剛離開的那一段不算（不知道是在哪個網路用的）；
  * 重開機（boot 變了）或計數變小（網卡重設）→ 重新取基準，不算；
  * 日期用這台電腦的當地時間。
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import _read, write_json

MAX_STEP = 10 * 1024 ** 4        # 一次差值超過 10 TB → 當成計數錯亂


def today_str(t: Optional[float] = None) -> str:
    return dt.datetime.fromtimestamp(t if t is not None else time.time()).strftime("%Y-%m-%d")


def on_net(ips: List[str], nets: List[Any]) -> bool:
    for ip in ips:
        try:
            a = ipaddress.ip_address(ip.split("%", 1)[0])
        except ValueError:
            continue
        if any(a.version == n.version and a in n for n in nets):
            return True
    return False


class Meter:
    def __init__(self, state_file: Path, nets: List[Any], sampler: Callable[[], Dict[str, Any]],
                 keep_days: int = 62) -> None:
        self.file = Path(state_file)
        self.nets = nets
        self.sampler = sampler
        self.keep_days = keep_days
        st = _read(self.file)
        self.id: str = str(st.get("id") or f"nm-{uuid.uuid4().hex[:16]}")
        self.days: Dict[str, Dict[str, int]] = {k: {"up": int(v.get("up", 0)), "down": int(v.get("down", 0))}
                                                for k, v in (st.get("days") or {}).items() if isinstance(v, dict)}
        self.last: Optional[Dict[str, Any]] = st.get("last") if isinstance(st.get("last"), dict) else None
        self.rate = {"up": 0.0, "down": 0.0}
        self.on_ifaces: List[str] = []
        self.ips: List[str] = []
        self.dirty = not self.file.exists()

    # ---- 讀一次 ----
    def step(self) -> Dict[str, Any]:
        s = self.sampler()
        t, boot = float(s["t"]), str(s.get("boot") or "")
        cur = {n: {"rx": int(v["rx"]), "tx": int(v["tx"]), "on": on_net(v.get("ips") or [], self.nets)}
               for n, v in (s.get("ifaces") or {}).items()}
        self.on_ifaces = sorted(n for n, v in cur.items() if v["on"])
        self.ips = [ip for n in self.on_ifaces for ip in (s["ifaces"][n].get("ips") or []) if on_net([ip], self.nets)]
        up = down = 0
        last = self.last
        if last and last.get("boot") == boot and t > float(last.get("t") or 0):
            prev = last.get("ifaces") or {}
            for n, v in cur.items():
                p = prev.get(n)
                if not (v["on"] and p and p.get("on")):
                    continue
                drx, dtx = v["rx"] - int(p["rx"]), v["tx"] - int(p["tx"])
                if 0 <= drx <= MAX_STEP and 0 <= dtx <= MAX_STEP:
                    down += drx
                    up += dtx
            el = t - float(last["t"])
            self.rate = {"up": up / el, "down": down / el} if el > 0 else {"up": 0.0, "down": 0.0}
        else:
            self.rate = {"up": 0.0, "down": 0.0}
        if up or down:
            d = self.days.setdefault(today_str(t), {"up": 0, "down": 0})
            d["up"] += up
            d["down"] += down
        self.last = {"t": t, "boot": boot, "ifaces": cur}
        self.dirty = True
        self._trim(t)
        return {"up": up, "down": down, "on": bool(self.on_ifaces)}

    def _trim(self, t: float) -> None:
        cut = (dt.date.fromisoformat(today_str(t)) - dt.timedelta(days=self.keep_days)).isoformat()
        for k in [k for k in self.days if k < cut]:
            del self.days[k]

    # ---- 保存 / 檢視 ----
    def save(self) -> None:
        write_json(self.file, {"id": self.id, "days": self.days, "last": self.last})
        self.dirty = False

    def now(self) -> float:
        """最後一次讀的時間（沒讀過 = 現在）：「今天」以這個為準。"""
        return float((self.last or {}).get("t") or time.time())

    def recent(self, n: int = 8, t: Optional[float] = None) -> Dict[str, Dict[str, int]]:
        today = dt.date.fromisoformat(today_str(t if t is not None else self.now()))
        keys = {(today - dt.timedelta(days=i)).isoformat() for i in range(n)}
        return {k: dict(v) for k, v in self.days.items() if k in keys}

    def month(self, t: Optional[float] = None) -> Dict[str, int]:
        m = today_str(t if t is not None else self.now())[:7]
        return {"up": sum(v["up"] for k, v in self.days.items() if k.startswith(m)),
                "down": sum(v["down"] for k, v in self.days.items() if k.startswith(m))}
