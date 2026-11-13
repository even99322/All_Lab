"""1.0.4：每條連線的流量（兩端位址、送 / 收多少），用來把實驗室網卡的流量分成 內網 / 外網 / VPN。

    Flows.start() → bool；Flows.drain() → {(本機位址, 對方位址, 是否 VPN 通道): [送出, 收到]}（上次 drain 之後的）

Windows：ETW「Microsoft-Windows-Kernel-Network」——每次 TCP / UDP 送收都有一筆（PID、大小、兩端位址）。
         要系統管理員（系統排程以 SYSTEM 執行的背景程式才有）；只讀計數，不碰封包。
macOS：  nettop（每條連線累計的 bytes），每次 drain 讀一次算差值（不用管理員）。
「VPN 通道」= Tailscale 程式（tailscaled）自己送收的加密封包：對方在實驗室網段 = VPN 走區網直連，
其他 = VPN 經外網（直連到外面的裝置或 DERP 中繼）。
只用標準函式庫（ctypes）＋ psutil（查 PID 是哪個程式）。
"""
from __future__ import annotations

import ipaddress
import logging
import re
import struct
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("netmeter")

Key = Tuple[str, str, bool]
TUNNEL_NAMES = ("tailscaled", "tailscale-ipn", "ipnextension", "io.tailscale")


def is_tunnel_name(name: str) -> bool:
    n = (name or "").lower()
    return any(t in n for t in TUNNEL_NAMES)


class Flows:
    kind = "none"

    def __init__(self) -> None:
        self.ok = False
        self.error = ""

    def start(self) -> bool:
        return False

    def drain(self) -> Dict[Key, List[int]]:
        return {}

    def stop(self) -> None:
        pass


def make(test: bool = False) -> Flows:
    if sys.platform == "win32":
        return EtwFlows(_SESSION + ("Test" if test else ""))
    if sys.platform == "darwin":
        return NettopFlows()
    return Flows()


# ---------------------------------------------------------------- Windows（ETW）
_GUID_KN = "{7DD42A49-5329-4832-8DFD-43D979153A88}"      # Microsoft-Windows-Kernel-Network
_SESSION = "QELNetMeterFlows"
# 事件 id → (IP 版本, 是送出)
_EVT = {10: (4, True), 11: (4, False), 42: (4, True), 43: (4, False),
        26: (6, True), 27: (6, False), 58: (6, True), 59: (6, False)}


def _parse(eid: int, data: bytes) -> Optional[Tuple[int, int, bytes, bytes, bool]]:
    """(PID, 大小, 位址 a, 位址 b, 是送出)；兩個位址哪個是本機由呼叫的人判斷（不靠欄位名稱的方向）。"""
    e = _EVT.get(eid)
    if not e:
        return None
    ver, send = e
    n = 4 if ver == 4 else 16
    if len(data) < 8 + 2 * n:
        return None
    pid, size = struct.unpack_from("<II", data, 0)
    return pid, size, data[8:8 + n], data[8 + n:8 + 2 * n], send


class EtwFlows(Flows):
    kind = "etw"

    def __init__(self, session: str = _SESSION) -> None:
        super().__init__()
        self.session = session
        self.lock = threading.Lock()
        self.acc: Dict[Tuple[int, bytes, bytes, bool], int] = {}
        self.names: Dict[int, Tuple[bool, float]] = {}
        self.events = 0
        self._handle = None
        self._trace = None
        self._thread: Optional[threading.Thread] = None
        self._cb = None
        self._props = None

    # ---- ETW（ctypes）----
    def start(self) -> bool:
        try:
            self._start()
            self.ok = True
            self.error = ""
        except Exception as e:  # noqa: BLE001
            self.ok = False
            self.error = str(e)
            log.info("沒辦法分內網 / 外網（ETW）：%s", e)
            self.stop()
        return self.ok

    def _start(self) -> None:
        import ctypes
        from ctypes import wintypes as w

        adv = ctypes.WinDLL("advapi32", use_last_error=True)

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", w.DWORD), ("Data2", w.WORD), ("Data3", w.WORD), ("Data4", ctypes.c_ubyte * 8)]

        def guid(s: str) -> GUID:
            import uuid

            u = uuid.UUID(s)
            g = GUID(u.time_low, u.time_mid, u.time_hi_version)
            g.Data4[:] = list(u.bytes[8:])
            return g

        class WNODE_HEADER(ctypes.Structure):
            _fields_ = [("BufferSize", w.ULONG), ("ProviderId", w.ULONG), ("HistoricalContext", ctypes.c_uint64),
                        ("TimeStamp", ctypes.c_int64), ("Guid", GUID), ("ClientContext", w.ULONG), ("Flags", w.ULONG)]

        class EVENT_TRACE_PROPERTIES(ctypes.Structure):
            _fields_ = [("Wnode", WNODE_HEADER), ("BufferSize", w.ULONG), ("MinimumBuffers", w.ULONG),
                        ("MaximumBuffers", w.ULONG), ("MaximumFileSize", w.ULONG), ("LogFileMode", w.ULONG),
                        ("FlushTimer", w.ULONG), ("EnableFlags", w.ULONG), ("AgeLimit", w.LONG),
                        ("NumberOfBuffers", w.ULONG), ("FreeBuffers", w.ULONG), ("EventsLost", w.ULONG),
                        ("BuffersWritten", w.ULONG), ("LogBuffersLost", w.ULONG), ("RealTimeBuffersLost", w.ULONG),
                        ("LoggerThreadId", w.HANDLE), ("LogFileNameOffset", w.ULONG), ("LoggerNameOffset", w.ULONG)]

        class PROPS(ctypes.Structure):
            _fields_ = [("p", EVENT_TRACE_PROPERTIES), ("name", ctypes.c_wchar * 260), ("file", ctypes.c_wchar * 260)]

        class EVENT_DESCRIPTOR(ctypes.Structure):
            _fields_ = [("Id", w.USHORT), ("Version", ctypes.c_ubyte), ("Channel", ctypes.c_ubyte),
                        ("Level", ctypes.c_ubyte), ("Opcode", ctypes.c_ubyte), ("Task", w.USHORT),
                        ("Keyword", ctypes.c_uint64)]

        class EVENT_HEADER(ctypes.Structure):
            _fields_ = [("Size", w.USHORT), ("HeaderType", w.USHORT), ("Flags", w.USHORT), ("EventProperty", w.USHORT),
                        ("ThreadId", w.ULONG), ("ProcessId", w.ULONG), ("TimeStamp", ctypes.c_int64),
                        ("ProviderId", GUID), ("EventDescriptor", EVENT_DESCRIPTOR), ("ProcessorTime", ctypes.c_uint64),
                        ("ActivityId", GUID)]

        class EVENT_RECORD(ctypes.Structure):
            _fields_ = [("EventHeader", EVENT_HEADER), ("BufferContext", w.ULONG), ("ExtendedDataCount", w.USHORT),
                        ("UserDataLength", w.USHORT), ("ExtendedData", ctypes.c_void_p), ("UserData", ctypes.c_void_p),
                        ("UserContext", ctypes.c_void_p)]

        class EVENT_TRACE_HEADER(ctypes.Structure):
            _fields_ = [("Size", w.USHORT), ("FieldTypeFlags", w.USHORT), ("Version", w.ULONG), ("ThreadId", w.ULONG),
                        ("ProcessId", w.ULONG), ("TimeStamp", ctypes.c_int64), ("Guid", GUID),
                        ("ProcessorTime", ctypes.c_uint64)]

        class EVENT_TRACE(ctypes.Structure):
            _fields_ = [("Header", EVENT_TRACE_HEADER), ("InstanceId", w.ULONG), ("ParentInstanceId", w.ULONG),
                        ("ParentGuid", GUID), ("MofData", ctypes.c_void_p), ("MofLength", w.ULONG),
                        ("ClientContext", w.ULONG)]

        class SYSTEMTIME(ctypes.Structure):
            _fields_ = [(n, w.WORD) for n in ("wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute",
                                              "wSecond", "wMilliseconds")]

        class TIME_ZONE_INFORMATION(ctypes.Structure):
            _fields_ = [("Bias", w.LONG), ("StandardName", ctypes.c_wchar * 32), ("StandardDate", SYSTEMTIME),
                        ("StandardBias", w.LONG), ("DaylightName", ctypes.c_wchar * 32), ("DaylightDate", SYSTEMTIME),
                        ("DaylightBias", w.LONG)]

        class TRACE_LOGFILE_HEADER(ctypes.Structure):
            _fields_ = [("BufferSize", w.ULONG), ("Version", w.ULONG), ("ProviderVersion", w.ULONG),
                        ("NumberOfProcessors", w.ULONG), ("EndTime", ctypes.c_int64), ("TimerResolution", w.ULONG),
                        ("MaximumFileSize", w.ULONG), ("LogFileMode", w.ULONG), ("BuffersWritten", w.ULONG),
                        ("LogInstanceGuid", GUID), ("LoggerName", ctypes.c_void_p), ("LogFileName", ctypes.c_void_p),
                        ("TimeZone", TIME_ZONE_INFORMATION), ("BootTime", ctypes.c_int64), ("PerfFreq", ctypes.c_int64),
                        ("StartTime", ctypes.c_int64), ("ReservedFlags", w.ULONG), ("BuffersLost", w.ULONG)]

        CALLBACK = ctypes.WINFUNCTYPE(None, ctypes.POINTER(EVENT_RECORD))

        class EVENT_TRACE_LOGFILEW(ctypes.Structure):
            _fields_ = [("LogFileName", w.LPWSTR), ("LoggerName", w.LPWSTR), ("CurrentTime", ctypes.c_int64),
                        ("BuffersRead", w.ULONG), ("ProcessTraceMode", w.ULONG), ("CurrentEvent", EVENT_TRACE),
                        ("LogfileHeader", TRACE_LOGFILE_HEADER), ("BufferCallback", ctypes.c_void_p),
                        ("BufferSize", w.ULONG), ("Filled", w.ULONG), ("EventsLost", w.ULONG),
                        ("EventRecordCallback", CALLBACK), ("IsKernelTrace", w.ULONG), ("Context", ctypes.c_void_p)]

        TRACEHANDLE = ctypes.c_uint64
        adv.StartTraceW.argtypes = [ctypes.POINTER(TRACEHANDLE), w.LPCWSTR, ctypes.c_void_p]
        adv.StartTraceW.restype = w.ULONG
        adv.ControlTraceW.argtypes = [TRACEHANDLE, w.LPCWSTR, ctypes.c_void_p, w.ULONG]
        adv.ControlTraceW.restype = w.ULONG
        adv.EnableTraceEx2.argtypes = [TRACEHANDLE, ctypes.POINTER(GUID), w.ULONG, ctypes.c_ubyte, ctypes.c_uint64,
                                       ctypes.c_uint64, w.ULONG, ctypes.c_void_p]
        adv.EnableTraceEx2.restype = w.ULONG
        adv.OpenTraceW.argtypes = [ctypes.POINTER(EVENT_TRACE_LOGFILEW)]
        adv.OpenTraceW.restype = TRACEHANDLE
        adv.ProcessTrace.argtypes = [ctypes.POINTER(TRACEHANDLE), w.ULONG, ctypes.c_void_p, ctypes.c_void_p]
        adv.ProcessTrace.restype = w.ULONG
        adv.CloseTrace.argtypes = [TRACEHANDLE]
        adv.CloseTrace.restype = w.ULONG

        def props() -> PROPS:
            p = PROPS()
            p.p.Wnode.BufferSize = ctypes.sizeof(PROPS)
            p.p.Wnode.Flags = 0x00020000                      # WNODE_FLAG_TRACED_GUID
            p.p.Wnode.ClientContext = 1                       # QPC
            p.p.BufferSize = 64                               # KB
            p.p.MinimumBuffers = 8
            p.p.MaximumBuffers = 64
            p.p.LogFileMode = 0x00000100                      # EVENT_TRACE_REAL_TIME_MODE
            p.p.FlushTimer = 1
            p.p.LoggerNameOffset = PROPS.name.offset
            p.p.LogFileNameOffset = PROPS.file.offset
            return p

        # 上次沒關好（被強制結束）留下來的同名工作階段先關掉
        adv.ControlTraceW(0, self.session, ctypes.byref(props()), 1)          # EVENT_TRACE_CONTROL_STOP
        h = TRACEHANDLE()
        self._props = props()
        rc = adv.StartTraceW(ctypes.byref(h), self.session, ctypes.byref(self._props))
        if rc == 5:
            raise PermissionError("需要系統管理員（系統排程的背景程式才有）")
        if rc:
            raise OSError(f"StartTrace 失敗（{rc}）")
        self._handle = h.value
        self._adv = adv
        g = guid(_GUID_KN)
        rc = adv.EnableTraceEx2(h, ctypes.byref(g), 1, 5, 0x30, 0, 0, None)   # 啟用；Level 5；IPv4 | IPv6
        if rc:
            raise OSError(f"EnableTraceEx2 失敗（{rc}）")

        def on_event(rec):
            try:
                r = rec.contents
                eid = r.EventHeader.EventDescriptor.Id
                if eid not in _EVT or not r.UserData:
                    return
                x = _parse(eid, ctypes.string_at(r.UserData, r.UserDataLength))
                if x:
                    pid, size, a, b, send = x
                    k = (pid, a, b, send)
                    with self.lock:
                        self.acc[k] = self.acc.get(k, 0) + size
                        self.events += 1
            except Exception:  # noqa: BLE001
                pass

        self._cb = CALLBACK(on_event)
        lf = EVENT_TRACE_LOGFILEW()
        lf.LoggerName = self.session
        lf.ProcessTraceMode = 0x00000100 | 0x10000000         # REAL_TIME | EVENT_RECORD
        lf.EventRecordCallback = self._cb
        self._lf = lf
        th = adv.OpenTraceW(ctypes.byref(lf))
        if th in (0xFFFFFFFFFFFFFFFF, 0x00000000FFFFFFFF):
            raise OSError(f"OpenTrace 失敗（{ctypes.get_last_error()}）")
        self._trace = th

        def loop() -> None:
            arr = (TRACEHANDLE * 1)(th)
            rc2 = adv.ProcessTrace(arr, 1, None, None)
            if rc2 and self.ok:
                self.error = f"ProcessTrace 結束（{rc2}）"
                self.ok = False

        self._thread = threading.Thread(target=loop, name="etw", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        adv = getattr(self, "_adv", None)
        if adv is None:
            return
        import ctypes

        self.ok = False
        try:
            if self._handle is not None:
                adv.ControlTraceW(self._handle, None, ctypes.byref(self._props), 1)
            if self._trace is not None:
                adv.CloseTrace(self._trace)
        except Exception:  # noqa: BLE001
            pass
        self._handle = self._trace = None
        if self._thread:
            self._thread.join(3)

    # ---- 取出 ----
    def _tunnel(self, pid: int) -> bool:
        now = time.monotonic()
        c = self.names.get(pid)
        if c and now - c[1] < 600:
            return c[0]
        try:
            import psutil

            t = is_tunnel_name(psutil.Process(pid).name())
        except Exception:  # noqa: BLE001
            t = False
        if len(self.names) > 4096:
            self.names.clear()
        self.names[pid] = (t, now)
        return t

    def drain(self) -> Dict[Key, List[int]]:
        with self.lock:
            acc, self.acc = self.acc, {}
        out: Dict[Key, List[int]] = {}
        for (pid, a, b, send), size in acc.items():
            try:
                ka, kb = str(ipaddress.ip_address(a)), str(ipaddress.ip_address(b))
            except ValueError:
                continue
            k = (ka, kb, self._tunnel(pid))       # 哪個是本機由 split() 判斷
            v = out.setdefault(k, [0, 0])
            v[0 if send else 1] += size
        return out


# ---------------------------------------------------------------- macOS（nettop）
def _endpoint(s: str) -> Optional[str]:
    s = s.strip()
    if not s or s.startswith("*"):
        return None
    if "." in s and s.count(":") >= 2:                   # IPv6：位址.埠
        host = s.rsplit(".", 1)[0]
    elif ":" in s:
        host = s.rsplit(":", 1)[0]
    else:
        return None
    host = host.split("%", 1)[0].strip("[]")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


_TIME = re.compile(r"^\d{1,2}:\d{2}:\d{2}")


def parse_nettop(text: str) -> Dict[Tuple[str, str], Tuple[str, str, int, int]]:
    """nettop -L 1 -n -x -J bytes_in,bytes_out 的輸出 → {(程式, 連線): (本機, 對方, 收, 送)}（累計）。
    表頭那一行有 bytes_in、bytes_out；程式那一行後面接著它的連線（名稱欄有「<->」）。"""
    out: Dict[Tuple[str, str], Tuple[str, str, int, int]] = {}
    ii = io = -1
    proc = ""
    for line in text.splitlines():
        f = [x.strip() for x in line.split(",")]
        if "bytes_in" in f and "bytes_out" in f:
            ii, io = f.index("bytes_in"), f.index("bytes_out")
            continue
        if ii < 0 or len(f) <= max(ii, io):
            continue
        try:
            bi, bo = int(f[ii] or 0), int(f[io] or 0)
        except ValueError:
            continue
        name = next((x for x in f if "<->" in x), "")
        if not name:
            proc = next((x for x in f if x and not _TIME.match(x)), "")
            proc = proc.rsplit(".", 1)[0] if re.search(r"\.\d+$", proc) else proc
            continue
        parts = name.split(" ", 1)
        if len(parts) != 2:
            continue
        a, _, b = parts[1].partition("<->")
        la, ra = _endpoint(a), _endpoint(b)
        if la and ra:
            out[(proc, name)] = (la, ra, bi, bo)
    return out


class NettopFlows(Flows):
    kind = "nettop"

    def __init__(self, runner=None) -> None:
        super().__init__()
        self.runner = runner or self._run
        self.prev: Optional[Dict[Tuple[str, str], Tuple[str, str, int, int]]] = None

    @staticmethod
    def _run() -> str:
        return subprocess.run(["/usr/bin/nettop", "-L", "1", "-n", "-x", "-J", "bytes_in,bytes_out"],
                              capture_output=True, text=True, timeout=20).stdout

    def start(self) -> bool:
        try:
            self.prev = parse_nettop(self.runner())
            self.ok = True
            self.error = ""
        except Exception as e:  # noqa: BLE001
            self.ok = False
            self.error = str(e)
            log.info("沒辦法分內網 / 外網（nettop）：%s", e)
        return self.ok

    def drain(self) -> Dict[Key, List[int]]:
        if not self.ok:
            return {}
        try:
            cur = parse_nettop(self.runner())
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            return {}
        prev, self.prev = self.prev or {}, cur
        out: Dict[Key, List[int]] = {}
        for k, (la, ra, bi, bo) in cur.items():
            p = prev.get(k)
            di, do = (bi - p[2], bo - p[3]) if p else (bi, bo)
            if di < 0 or do < 0:
                di, do = bi, bo
            if di or do:
                v = out.setdefault((la, ra, is_tunnel_name(k[0])), [0, 0])
                v[0] += do
                v[1] += di
        return out
