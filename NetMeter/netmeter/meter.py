"""計量：每次讀網卡計數，只把「這次和上次都連在實驗室網段」的網卡的差值加到今天。

    sample() → {"t", "boot", "ifaces": {名稱: {"rx", "tx", "ips": [...]}}}（counters.read() 產生；測試可以換掉）

規則：
  * 那張網卡現在有實驗室網段的 IPv4 位址，而且上次讀的時候也有 → 差值算進今天（下載 = rx，上傳 = tx）；
  * 剛連上 / 剛離開的那一段不算（不知道是在哪個網路用的）；
  * 重開機（boot 變了）或計數變小（網卡重設）→ 重新取基準，不算；
  * 日期用這台電腦的當地時間。
  * 1.0.2：Tailscale 的網卡（100.64.0.0/10 的位址）另外記成 vpn_up / vpn_down（在實驗室網段時才記）——
    這部分也已經算在實驗網卡的流量裡（VPN 封包包起來後經實驗網卡送出），Hub 用它看「其中經 VPN 的有多少」。
  * 1.0.4：有每條連線的資料（flows.py：Windows ETW / macOS nettop）時，把實驗網卡的量依比例分成
      lan  內網：對方在實驗室網段（NAS、別台電腦；不經路由器的外網口）
      wan  外網：其他（經路由器出去，算進對外的流量上限）
      tun  VPN 通道：Tailscale 程式自己送收的加密封包；其中 tunw = 經外網（直連外面的裝置 / DERP 中繼）
    連線資料只算「內容」大小，所以用它的比例去分網卡計數（含封包標頭）；分不出來的（ICMP 等）留在總量裡不分。
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from .config import _read, write_json

MAX_STEP = 10 * 1024 ** 4        # 一次差值超過 10 TB → 當成計數錯亂
VPN_NETS = [ipaddress.ip_network("100.64.0.0/10")]
CATS = ("lan", "wan", "tun", "tunw")
DAY_KEYS = ("up", "down", "vpn_up", "vpn_down") + tuple(f"{c}_{d}" for c in CATS for d in ("up", "down"))


def _near(ip: str, nets: List[Any]) -> bool:
    """不經路由器外網口：實驗室網段、本機連結、多點傳送、廣播。"""
    try:
        a = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return True
    if a.is_link_local or a.is_multicast or a.is_loopback or a.is_unspecified or str(a) == "255.255.255.255":
        return True
    return any(a.version == n.version and (a in n or (a.version == 4 and a == n.broadcast_address)) for n in nets)


def split_flows(flows: Dict[Any, List[int]], local_ips: Set[str], nets: List[Any]) -> Dict[str, List[int]]:
    """{(位址 a, 位址 b, 是否 VPN 通道): [送出, 收到]} → {lan / wan / tun / tunw: [送出, 收到]}（只算本機位址在實驗網卡的）。"""
    out = {c: [0, 0] for c in CATS}
    for (a, b, tun), (snd, rcv) in flows.items():
        a, b = a.split("%", 1)[0], b.split("%", 1)[0]
        if a in local_ips and b not in local_ips:
            rem = b
        elif b in local_ips and a not in local_ips:
            rem = a
        else:
            continue
        near = _near(rem, nets)
        if tun:
            out["tun"][0] += snd
            out["tun"][1] += rcv
            if not near:
                out["tunw"][0] += snd
                out["tunw"][1] += rcv
        else:
            c = out["lan" if near else "wan"]
            c[0] += snd
            c[1] += rcv
    return out


def scale_split(total: int, parts: Dict[str, List[int]], i: int) -> Optional[Dict[str, int]]:
    """網卡計數 total 依連線資料的比例分到 lan / wan / tun（加起來 = total）；tunw 是 tun 的一部分。"""
    s = parts["lan"][i] + parts["wan"][i] + parts["tun"][i]
    if s <= 0 or total <= 0:
        return None
    f = total / s
    lan, wan = int(parts["lan"][i] * f), int(parts["wan"][i] * f)
    tun = total - lan - wan
    return {"lan": lan, "wan": wan, "tun": tun, "tunw": min(tun, int(parts["tunw"][i] * f))}


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
                 keep_days: int = 62, flows: Any = None) -> None:
        self.file = Path(state_file)
        self.nets = nets
        self.sampler = sampler
        self.keep_days = keep_days
        st = _read(self.file)
        self.id: str = str(st.get("id") or f"nm-{uuid.uuid4().hex[:16]}")
        self.flows = flows
        self.days: Dict[str, Dict[str, int]] = {k: {kk: int(v.get(kk, 0)) for kk in DAY_KEYS
                                                    if kk in ("up", "down") or v.get(kk)}
                                                for k, v in (st.get("days") or {}).items() if isinstance(v, dict)}
        self.last: Optional[Dict[str, Any]] = st.get("last") if isinstance(st.get("last"), dict) else None
        self.rate = {"up": 0.0, "down": 0.0}
        self.rate_split: Dict[str, Dict[str, float]] = {}
        self.on_ifaces: List[str] = []
        self.ips: List[str] = []
        self.dirty = not self.file.exists()

    # ---- 讀一次 ----
    def step(self) -> Dict[str, Any]:
        s = self.sampler()
        t, boot = float(s["t"]), str(s.get("boot") or "")
        cur = {n: {"rx": int(v["rx"]), "tx": int(v["tx"]), "on": on_net(v.get("ips") or [], self.nets),
                   "vpn": on_net(v.get("ips") or [], VPN_NETS)}
               for n, v in (s.get("ifaces") or {}).items()}
        self.on_ifaces = sorted(n for n, v in cur.items() if v["on"])
        self.ips = [ip for n in self.on_ifaces for ip in (s["ifaces"][n].get("ips") or []) if on_net([ip], self.nets)]
        up = down = vup = vdown = 0
        fl = None
        if self.flows is not None and getattr(self.flows, "ok", False):
            try:
                fl = self.flows.drain()
            except Exception:  # noqa: BLE001
                fl = None
        last = self.last
        if last and last.get("boot") == boot and t > float(last.get("t") or 0):
            prev = last.get("ifaces") or {}
            lab_on = any(v["on"] and (prev.get(n) or {}).get("on") for n, v in cur.items())
            for n, v in cur.items():
                p = prev.get(n)
                if v.get("vpn") and p and p.get("vpn") and lab_on:
                    drx, dtx = v["rx"] - int(p["rx"]), v["tx"] - int(p["tx"])
                    if 0 <= drx <= MAX_STEP and 0 <= dtx <= MAX_STEP:
                        vdown += drx
                        vup += dtx
                if not (v["on"] and p and p.get("on")):
                    continue
                drx, dtx = v["rx"] - int(p["rx"]), v["tx"] - int(p["tx"])
                if 0 <= drx <= MAX_STEP and 0 <= dtx <= MAX_STEP:
                    down += drx
                    up += dtx
            el = t - float(last["t"])
            self.rate = {"up": up / el, "down": down / el} if el > 0 else {"up": 0.0, "down": 0.0}
        else:
            el = 0.0
            self.rate = {"up": 0.0, "down": 0.0}
        sp: Dict[str, Dict[str, int]] = {}
        if fl is not None and (up or down):
            local = {ip.split("%", 1)[0] for n in self.on_ifaces for ip in (s["ifaces"][n].get("ips") or [])}
            parts = split_flows(fl, local, self.nets)
            for i, dname in ((0, "up"), (1, "down")):
                x = scale_split(up if i == 0 else down, parts, i)
                if x:
                    sp[dname] = x
        self.rate_split = ({c: {dn: (sp.get(dn, {}).get(c, 0) / el) for dn in ("up", "down")} for c in CATS}
                           if sp and el > 0 else {})
        if up or down or vup or vdown:
            d = self.days.setdefault(today_str(t), {"up": 0, "down": 0})
            d["up"] += up
            d["down"] += down
            if vup or vdown:
                d["vpn_up"] = int(d.get("vpn_up", 0)) + vup
                d["vpn_down"] = int(d.get("vpn_down", 0)) + vdown
            for dn, x in sp.items():
                for c, n in x.items():
                    if n:
                        d[f"{c}_{dn}"] = int(d.get(f"{c}_{dn}", 0)) + n
        self.last = {"t": t, "boot": boot, "ifaces": cur}
        self.dirty = True
        self._trim(t)
        return {"up": up, "down": down, "vpn_up": vup, "vpn_down": vdown, "on": bool(self.on_ifaces), "split": sp}

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
        out = {"up": 0, "down": 0}
        for k, v in self.days.items():
            if k.startswith(m):
                for kk, n in v.items():
                    out[kk] = out.get(kk, 0) + int(n)
        return out
