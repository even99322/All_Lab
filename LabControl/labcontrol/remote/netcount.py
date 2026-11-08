"""1.0.29：這台電腦的網卡總流量計數（回報給 Hub 的「網路用量」，Hub 累加成每天用量）。

只算實體網卡：拿掉 loopback、虛擬網卡（Hyper-V / VMware / VirtualBox / Docker / WSL）、VPN（Tailscale、WireGuard、
ZeroTier——它們的流量本來就會再經過實體網卡，算進來會重複）。
有 psutil 就用；沒有（安裝檔沒有帶 psutil，免得功能更新包要換套件）→ Windows 直接呼叫 GetIfTable2（64 位元計數），
macOS 讀 netstat -ibn，Linux 讀 /proc/net/dev。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

_SKIP = re.compile(
    r"loopback|^lo\d*$|pseudo|vethernet|hyper-v|vmware|vmnet|virtualbox|vboxnet|docker|^br-|^veth|^virbr|wsl|"
    r"tailscale|wireguard|^wg\d|zerotier|^zt|wintun|tun\d|^tap|^utun|^awdl|^llw|^bridge|^gif|^stf|^anpi|^ap\d|"
    r"bluetooth|isatap|teredo|6to4|npcap|kernel debug|^ham|^ifb|^dummy|^sit\d|^ip6tnl|^xhc|\*$", re.I)


def is_physical(name: str) -> bool:
    return not _SKIP.search(name or "")


def _windows() -> Optional[Dict[str, Any]]:
    """GetIfTable2（64 位元計數；只算 HardwareInterface、不是篩選器介面的網卡）。"""
    import ctypes as C

    class GUID(C.Structure):
        _fields_ = [("a", C.c_uint32), ("b", C.c_uint16), ("c", C.c_uint16), ("d", C.c_ubyte * 8)]

    U64, UL = C.c_uint64, C.c_uint32

    class ROW2(C.Structure):                      # MIB_IF_ROW2（1352 bytes）
        _fields_ = [("Luid", U64), ("Index", UL), ("Guid", GUID), ("Alias", C.c_uint16 * 257),
                    ("Description", C.c_uint16 * 257), ("PhysLen", UL), ("Phys", C.c_ubyte * 32),
                    ("PermPhys", C.c_ubyte * 32), ("Mtu", UL), ("Type", UL), ("TunnelType", UL), ("MediaType", UL),
                    ("PhysicalMediumType", UL), ("AccessType", UL), ("DirectionType", UL), ("Flags", C.c_ubyte),
                    ("OperStatus", UL), ("AdminStatus", UL), ("MediaConnectState", UL), ("NetworkGuid", GUID),
                    ("ConnectionType", UL), ("TxSpeed", U64), ("RxSpeed", U64), ("InOctets", U64),
                    ("InUcastPkts", U64), ("InNUcastPkts", U64), ("InDiscards", U64), ("InErrors", U64),
                    ("InUnknownProtos", U64), ("InUcastOctets", U64), ("InMulticastOctets", U64),
                    ("InBroadcastOctets", U64), ("OutOctets", U64), ("OutUcastPkts", U64), ("OutNUcastPkts", U64),
                    ("OutDiscards", U64), ("OutErrors", U64), ("OutUcastOctets", U64), ("OutMulticastOctets", U64),
                    ("OutBroadcastOctets", U64), ("OutQLen", U64)]

    class TABLE(C.Structure):
        _fields_ = [("NumEntries", UL), ("Table", ROW2 * 1)]

    iphlp = C.WinDLL("iphlpapi")
    ptr = C.POINTER(TABLE)()
    if iphlp.GetIfTable2(C.byref(ptr)) != 0:
        return None
    try:
        n = ptr.contents.NumEntries
        rows = C.cast(C.addressof(ptr.contents.Table), C.POINTER(ROW2 * n)).contents
        rx = tx = 0
        nics = []
        for r in rows:
            hw, filt = bool(r.Flags & 0x01), bool(r.Flags & 0x02)
            name = "".join(chr(c) for c in r.Alias if c) or "".join(chr(c) for c in r.Description if c)
            desc = "".join(chr(c) for c in r.Description if c)
            if not hw or filt or r.Type == 24 or not is_physical(name) or not is_physical(desc):
                continue
            rx += int(r.InOctets)
            tx += int(r.OutOctets)
            nics.append(name)
    finally:
        iphlp.FreeMibTable(ptr)
    return {"rx": rx, "tx": tx, "nics": sorted(nics)[:12]}


def _linux() -> Optional[Dict[str, Any]]:
    rx = tx = 0
    nics = []
    with open("/proc/net/dev", encoding="utf-8") as f:
        for line in f.readlines()[2:]:
            name, _, rest = line.partition(":")
            name = name.strip()
            v = rest.split()
            if len(v) < 9 or not is_physical(name):
                continue
            rx += int(v[0])
            tx += int(v[8])
            nics.append(name)
    return {"rx": rx, "tx": tx, "nics": sorted(nics)[:12]}


def parse_netstat_ibn(text: str) -> Dict[str, Any]:
    """macOS netstat -ibn：每個介面取 <Link#…> 那一行（避免 IPv4 / IPv6 重複）；從右邊數欄位（Address 可能是空的）。"""
    rx = tx = 0
    nics = []
    for line in text.splitlines()[1:]:
        v = line.split()
        if len(v) < 8 or not v[2].startswith("<Link#") or not is_physical(v[0]) or v[0] in nics:
            continue
        try:
            ib, ob = int(v[-5]), int(v[-2])
        except ValueError:
            continue
        rx += ib
        tx += ob
        nics.append(v[0])
    return {"rx": rx, "tx": tx, "nics": sorted(nics)[:12]}


def _macos() -> Optional[Dict[str, Any]]:
    import subprocess

    out = subprocess.run(["netstat", "-ibn"], capture_output=True, text=True, timeout=5).stdout
    return parse_netstat_ibn(out) if out else None


def counters() -> Optional[Dict[str, Any]]:
    """{"rx": 收到的 byte, "tx": 送出的 byte, "nics": [...]}；拿不到 → None。
    （不送開機時間：Hub 看到計數變小就當成重開機，從 0 接著算。）"""
    try:
        import psutil
    except ImportError:
        import os
        import sys

        try:
            if os.name == "nt":
                return _windows()
            if sys.platform == "darwin":
                return _macos()
            if sys.platform.startswith("linux"):
                return _linux()
        except Exception:  # noqa: BLE001
            return None
        return None
    try:
        per = psutil.net_io_counters(pernic=True) or {}
        try:
            stats = psutil.net_if_stats() or {}
        except Exception:  # noqa: BLE001
            stats = {}
        nics = [n for n in per if is_physical(n) and (n not in stats or stats[n].isup or per[n].bytes_recv)]
        rx = sum(int(per[n].bytes_recv) for n in nics)
        tx = sum(int(per[n].bytes_sent) for n in nics)
    except Exception:  # noqa: BLE001
        return None
    return {"rx": rx, "tx": tx, "nics": sorted(nics)[:12]}
