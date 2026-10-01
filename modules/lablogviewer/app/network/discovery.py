"""Find Hosts on the same subnet (UDP).

A Client broadcasts a small query; running Hosts answer with their name,
session, client count and TCP port. Replies are plain JSON (no data). In
sandbox mode (LABLOGVIEWER_NET_SANDBOX=1) everything stays on 127.0.0.1 and
no packet leaves the machine.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Callable

from app.network.transport import sandbox_mode

DISCOVERY_PORT = 47810
QUERY = b"LLVN-DISCOVER-1"


class DiscoveryResponder:
    def __init__(self, info: Callable[[], dict], port: int = DISCOVERY_PORT):
        self._info = info
        self.port = port
        self._stop = threading.Event()
        self._sock: socket.socket | None = None

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        try:
            sock.bind(("127.0.0.1" if sandbox_mode() else "", self.port))
        except OSError:
            sock.close()
            return                                     # discovery unavailable; direct search still works
        sock.settimeout(0.5)
        self._sock = sock
        threading.Thread(target=self._loop, name="llvn-discovery", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, address = self._sock.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                return
            if data != QUERY:
                continue
            try:
                self._sock.sendto(json.dumps(self._info()).encode("utf-8"), address)
            except OSError:
                pass


def discover(timeout: float = 1.5, port: int = DISCOVERY_PORT) -> list[dict]:
    """Hosts that answered within ``timeout`` seconds (address added as 'address')."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    found: dict[tuple, dict] = {}
    try:
        if sandbox_mode():
            targets = ["127.0.0.1"]
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            targets = ["255.255.255.255"]
        sock.settimeout(0.2)
        for target in targets:
            try:
                sock.sendto(QUERY, (target, port))
            except OSError:
                pass
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, address = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                info = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if isinstance(info, dict) and isinstance(info.get("port"), int):
                info["address"] = address[0]
                found[(address[0], info["port"], info.get("session_id"))] = info
    finally:
        sock.close()
    return sorted(found.values(), key=lambda item: (str(item.get("host_name")), str(item.get("session_name"))))


def search(hosts: list[dict], text: str) -> list[dict]:
    """Filter discovered Hosts by host name or session name (case-insensitive)."""
    needle = text.strip().casefold()
    if not needle:
        return hosts
    return [host for host in hosts if needle in str(host.get("host_name", "")).casefold()
            or needle in str(host.get("session_name", "")).casefold()]
