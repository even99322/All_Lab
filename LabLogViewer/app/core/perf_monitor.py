"""Live performance monitor: where the program spends its time, and how that changes.

Three things are measured while the monitor runs (it costs very little and is off until
asked for):
* UI lag - how late a 100 ms timer fires on the UI thread. Anything above ~100 ms is felt.
* Process figures - CPU (share of one core), memory (resident MB), thread count, number of
  Qt widgets alive. A widget count or memory that keeps rising while nothing is open is a leak.
* A stack sampler - 25 times a second it looks at what the UI thread is executing and counts
  the program function responsible (waiting in the event loop counts as "idle"). When the UI
  thread stays in one place for longer than 150 ms, that place is recorded as a stall.
"""

from __future__ import annotations

import collections
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

APP_DIR = Path(__file__).resolve().parents[1]
HISTORY = 600                     # points kept per curve (about 2 minutes at 5 Hz)
STALL_SECONDS = 0.15
IDLE = "(idle: waiting in the event loop)"


@dataclass
class Sample:
    when: float
    lag_ms: float
    cpu_percent: float
    rss_mb: float
    threads: int
    widgets: int


@dataclass
class Stall:
    when: float
    seconds: float
    where: str


def current_rss_mb() -> float:
    """Resident memory of this process in MB (0.0 when it cannot be read)."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/self/statm", encoding="ascii") as stream:
                return int(stream.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
            return counters.WorkingSetSize / 1e6
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True,
                             text=True, timeout=3).stdout.strip()
        return int(out) / 1024.0
    except Exception:
        return 0.0


_inside: dict[str, str | None] = {}


def _program_path(filename: str) -> str | None:
    """The path of a file relative to the project folder when it is part of this program."""
    try:
        return _inside[filename]
    except KeyError:
        try:
            resolved = Path(filename).resolve()
            value = resolved.relative_to(APP_DIR.parent).as_posix() if resolved.is_relative_to(APP_DIR) else None
        except (OSError, ValueError):
            value = None
        _inside[filename] = value
        return value


def describe_frame(frame) -> str:
    """The program function that is running: the innermost frame inside this program's own
    code, else the innermost frame (a library call made on behalf of the program)."""
    cursor = frame
    while cursor is not None:
        relative = _program_path(cursor.f_code.co_filename)
        if relative is not None:
            library = ""
            if cursor is not frame:
                leaf = frame.f_code.co_filename.replace("\\", "/").rsplit("/", 1)[-1]
                library = f" -> {leaf}:{frame.f_code.co_name}"
            return f"{relative}:{cursor.f_code.co_name}{library}"
        cursor = cursor.f_back
    return f"{frame.f_code.co_filename.replace(chr(92), '/').rsplit('/', 1)[-1]}:{frame.f_code.co_name}"


def _is_idle(frame) -> bool:
    """The UI thread is blocked inside app.exec() (the Qt event loop) when its innermost Python
    frame is the program's main function; anything Qt calls back into is deeper than that."""
    name = frame.f_code.co_filename.replace("\\", "/")
    return frame.f_code.co_name in ("main", "run_child") and name.endswith(("/main.py", "/three_d_process.py"))


class PerformanceMonitor(QObject):
    updated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.samples: collections.deque[Sample] = collections.deque(maxlen=HISTORY)
        self.stalls: collections.deque[Stall] = collections.deque(maxlen=200)
        self.where: collections.Counter[str] = collections.Counter()
        self.total_samples = 0
        self.running = False
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)
        self._expected = 0.0
        self._last_cpu = (0.0, 0.0)
        self._rss = 0.0
        self._rss_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._main_id = threading.main_thread().ident
        self._lock = threading.Lock()

    # -- control ---------------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._stop.clear()
        self._expected = time.perf_counter() + 0.2
        self._last_cpu = (time.perf_counter(), time.process_time())
        self._timer.start()
        self._thread = threading.Thread(target=self._sample_loop, name="lablogviewer-perf", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        self._timer.stop()
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(0.5)

    def reset(self) -> None:
        with self._lock:
            self.samples.clear()
            self.stalls.clear()
            self.where.clear()
            self.total_samples = 0

    # -- UI thread -------------------------------------------------------------
    def _tick(self) -> None:
        from PySide6.QtWidgets import QApplication

        now = time.perf_counter()
        lag = max(0.0, (now - self._expected) * 1000.0)
        self._expected = now + 0.2
        wall, cpu = self._last_cpu
        used = time.process_time()
        percent = (used - cpu) / (now - wall) * 100.0 if now > wall else 0.0
        self._last_cpu = (now, used)
        sample = Sample(time.time(), lag, percent, self._rss, threading.active_count(),
                        len(QApplication.allWidgets()))
        with self._lock:
            self.samples.append(sample)
        self.updated.emit()

    # -- sampler thread --------------------------------------------------------
    def _sample_loop(self) -> None:
        here: str | None = None
        stall: Stall | None = None
        since = time.perf_counter()
        while not self._stop.wait(0.04):
            now = time.perf_counter()
            frame = sys._current_frames().get(self._main_id)
            if frame is None:
                continue
            place = IDLE if _is_idle(frame) else describe_frame(frame)
            with self._lock:
                self.where[place] += 1
                self.total_samples += 1
                if place != here:
                    here, since, stall = place, now, None
                elif place != IDLE and now - since >= STALL_SECONDS:
                    if stall is None:
                        stall = Stall(time.time(), 0.0, place)
                        self.stalls.append(stall)
                    stall.seconds = now - since
            if time.perf_counter() - self._rss_at > 2.0:
                self._rss, self._rss_at = current_rss_mb(), time.perf_counter()

    # -- reading ---------------------------------------------------------------
    def top_places(self, count: int = 15) -> list[tuple[str, float]]:
        with self._lock:
            total = max(1, self.total_samples)
            return [(place, 100.0 * n / total) for place, n in self.where.most_common(count)]

    def recent_stalls(self) -> list[Stall]:
        with self._lock:
            return list(self.stalls)[::-1]

    def report(self) -> str:
        from app import __version__

        lines = [f"LabLogViewer {__version__} performance report ({time.strftime('%Y-%m-%d %H:%M:%S')})",
                 f"platform: {sys.platform}, python {sys.version.split()[0]}", ""]
        with self._lock:
            samples = list(self.samples)
        if samples:
            first, last = samples[0], samples[-1]
            lines += [f"samples: {len(samples)} over {last.when - first.when:.0f} s",
                      f"UI lag now / max: {last.lag_ms:.0f} / {max(s.lag_ms for s in samples):.0f} ms",
                      f"CPU now / mean: {last.cpu_percent:.0f} / "
                      f"{sum(s.cpu_percent for s in samples) / len(samples):.0f} % of one core",
                      f"memory first -> now: {first.rss_mb:.0f} -> {last.rss_mb:.0f} MB",
                      f"Qt widgets first -> now: {first.widgets} -> {last.widgets}",
                      f"threads now: {last.threads}", ""]
        lines.append("Where the UI thread spends its time:")
        lines += [f"  {share:5.1f} %  {place}" for place, share in self.top_places(25)]
        lines.append("")
        lines.append("Recent stalls (UI thread stayed in one place >= 150 ms):")
        for stall in self.recent_stalls()[:50]:
            lines.append(f"  {time.strftime('%H:%M:%S', time.localtime(stall.when))}  "
                         f"{stall.seconds * 1000:6.0f} ms  {stall.where}")
        return "\n".join(lines)


_monitor: PerformanceMonitor | None = None


def monitor() -> PerformanceMonitor:
    global _monitor
    if _monitor is None:
        _monitor = PerformanceMonitor()
    return _monitor
