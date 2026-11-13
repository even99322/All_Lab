"""讀這台電腦每張網卡的流量計數與位址（psutil）。"""
from __future__ import annotations

import re
import time
from typing import Any, Dict

_SKIP = re.compile(r"loopback|^lo\d*$|pseudo", re.I)


def read() -> Dict[str, Any]:
    import socket

    import psutil

    io = psutil.net_io_counters(pernic=True) or {}
    addrs = psutil.net_if_addrs() or {}
    out: Dict[str, Any] = {}
    for name, c in io.items():
        if _SKIP.search(name):
            continue
        # 1.0.4：IPv6 也列（分內網 / 外網時要知道哪些位址是這張網卡的）；on_net 只看同版本的網段
        ips = [a.address for a in addrs.get(name, []) if a.family in (socket.AF_INET, socket.AF_INET6)]
        out[name] = {"rx": int(c.bytes_recv), "tx": int(c.bytes_sent), "ips": ips}
    try:
        boot = str(int(psutil.boot_time() // 120))     # 2 分鐘的格子：避免 Windows 的開機時間有幾秒的抖動
    except Exception:  # noqa: BLE001
        boot = ""
    return {"t": time.time(), "boot": boot, "ifaces": out}
