"""1.0.4：在實驗室網段時，把 Hub 的 https 網址（<NAS>.<tailnet>.ts.net）指到 NAS 的內網位址（Windows hosts 檔）。

Hub 的 https 網址平常靠 Tailscale（MagicDNS）找到 NAS；沒開 VPN 的內網電腦查不到。背景程式（系統排程，SYSTEM）
回報到 Hub 時拿到網域，在 hosts 檔加一段（只有這一段，其他內容不動）：

    # QEL NetMeter BEGIN (auto: removed when this PC leaves the lab network)
    192.168.50.2 <NAS>.<tailnet>.ts.net
    # QEL NetMeter END

離開實驗室網段、程式結束、解除安裝時拿掉（在家開 VPN 時照常用 MagicDNS）。
只接受 *.ts.net 網域、實驗室網段裡的位址（Hub 不能藉此改掉別的網址）。
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, List, Optional, Tuple

log = logging.getLogger("netmeter")

BEGIN = "# QEL NetMeter BEGIN"
END = "# QEL NetMeter END"
_DOMAIN = re.compile(r"^(?=.{8,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+ts\.net$")


def hosts_path() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / "drivers" / "etc" / "hosts"
    return Path("/etc/hosts")


def valid(ip: str, domain: str, nets: List[Any]) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return bool(_DOMAIN.fullmatch((domain or "").lower())) and any(a.version == n.version and a in n for n in nets)


def render(text: str, entries: List[Tuple[str, str]]) -> str:
    """拿掉舊的那一段，有 entries 就在最後加新的一段；其他行原封不動。"""
    nl = "\r\n" if "\r\n" in text else "\n" if text else ("\r\n" if sys.platform == "win32" else "\n")
    out: List[str] = []
    skip = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(BEGIN):
            skip = True
            continue
        if skip:
            if s.startswith(END):
                skip = False
            continue
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    if entries:
        if out:
            out.append("")
        out.append(f"{BEGIN} (auto: removed when this PC leaves the lab network)")   # hosts 只放 ASCII
        out += [f"{ip} {d}" for ip, d in entries]
        out.append(END)
    return nl.join(out) + nl if out else ""


def apply(entries: List[Tuple[str, str]], path: Optional[Path] = None) -> bool:
    """改 hosts 檔（內容一樣就不寫）；回傳有沒有改。"""
    p = Path(path or hosts_path())
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        raw = b""
    text = raw.decode("utf-8", errors="replace")
    new = render(text, entries)
    if new == text or (not entries and BEGIN not in text):
        return False
    p.write_bytes(new.encode("utf-8"))           # 原地寫（保留檔案權限）
    _flush_dns()
    log.info("hosts：%s", "、".join(f"{d} → {ip}" for ip, d in entries) or "拿掉 Hub 網址的對應")
    return True


def _flush_dns() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.WinDLL("dnsapi").DnsFlushResolverCache()
    except Exception:  # noqa: BLE001
        pass
