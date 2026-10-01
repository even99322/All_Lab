"""Network quality: round-trip time and throughput, graded for the UI.

Minimum to host / join / keep syncing: RTT <= 200 ms and >= 1 MB/s.
Updated every 2 s from heartbeats (RTT) and real transfers / probes
(throughput).
"""

from __future__ import annotations

import statistics
import time
from collections import deque
from dataclasses import dataclass

HEARTBEAT_SECONDS = 2.0
MAX_RTT_MS = 200.0
MIN_THROUGHPUT = 1.0e6            # bytes / s
PROBE_BYTES = 1024 * 1024

LEVELS = (
    # name, max RTT (ms), min throughput (bytes/s)
    ("excellent", 20.0, 20.0e6),
    ("good", 80.0, 5.0e6),
    ("fair", MAX_RTT_MS, MIN_THROUGHPUT),
)


@dataclass(frozen=True)
class Quality:
    rtt_ms: float | None
    throughput: float | None      # bytes / s
    level: str                    # excellent / good / fair / unusable / unknown

    @property
    def usable(self) -> bool:
        return self.level in {"excellent", "good", "fair"}

    def text(self) -> str:
        rtt = "–" if self.rtt_ms is None else f"{self.rtt_ms:.0f} ms"
        speed = "–" if self.throughput is None else f"{self.throughput / 1e6:.1f} MB/s"
        return f"{rtt} · {speed}"

    def as_dict(self) -> dict:
        return {"rtt_ms": self.rtt_ms, "throughput": self.throughput, "level": self.level}

    @staticmethod
    def from_dict(value) -> "Quality":
        value = value if isinstance(value, dict) else {}
        rtt, speed = value.get("rtt_ms"), value.get("throughput")
        return Quality(float(rtt) if isinstance(rtt, (int, float)) else None,
                       float(speed) if isinstance(speed, (int, float)) else None,
                       str(value.get("level", "unknown")))


def grade(rtt_ms: float | None, throughput: float | None) -> str:
    if rtt_ms is None or throughput is None:
        return "unknown"
    for name, max_rtt, min_speed in LEVELS:
        if rtt_ms <= max_rtt and throughput >= min_speed:
            return name
    return "unusable"


class QualityMeter:
    """Rolling RTT (last 5 heartbeats) and throughput (last transfers)."""

    def __init__(self):
        self._rtts: deque[float] = deque(maxlen=5)
        self._speeds: deque[float] = deque(maxlen=5)
        self._last_speed_at = 0.0

    def add_rtt(self, seconds: float) -> None:
        self._rtts.append(seconds * 1000.0)

    def add_transfer(self, size: int, seconds: float) -> None:
        # Tiny transfers say nothing about bandwidth.
        if size >= 64 * 1024 and seconds > 0:
            self._speeds.append(size / seconds)
            self._last_speed_at = time.monotonic()

    def quality(self) -> Quality:
        rtt = statistics.median(self._rtts) if self._rtts else None
        speed = statistics.median(self._speeds) if self._speeds else None
        return Quality(rtt, speed, grade(rtt, speed))

    def speed_age(self) -> float:
        return time.monotonic() - self._last_speed_at if self._last_speed_at else float("inf")
