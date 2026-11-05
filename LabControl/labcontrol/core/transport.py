"""通訊層：把「怎麼跟儀器講話」和「講什麼」分開。

driver 只呼叫 transport.write / query，不直接碰 pyvisa。
好處：
  1. 每台儀器一把鎖，write+query 不會被其他執行緒插隊（解決 web 面板輪詢與量測搶 VISA 的問題）。
  2. 測試時換成 MockTransport 就能驗證 SCPI 指令，不需要硬體。
  3. 未來要換成 socket、zhinst、或遠端 Instrument Server，只要多寫一個 Transport。
"""
from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Callable, Dict, List, Optional, Union

import numpy as np

log = logging.getLogger(__name__)


class Transport(ABC):
    """所有 transport 共用：每台一把鎖、收發紀錄（taps）、每個指令的次數與耗時統計（stats）。"""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        #: 收發監聽：fn(方向, 文字)；方向 ">>" 寫入、"?>" 查詢、"<<" 回應、"!!" 錯誤（儀器控制視窗的通訊紀錄用）
        self.taps: List[Callable[[str, str], None]] = []
        #: 指令統計：{指令開頭: [次數, 總秒數]}（Labber 的 Timing statistics）
        self.stats: Dict[str, List[float]] = {}

    def _tap(self, kind: str, text: str) -> None:
        for fn in list(self.taps):
            try:
                fn(kind, text)
            except Exception:  # noqa: BLE001
                pass

    def _stat(self, cmd: str, dt: float) -> None:
        key = cmd.strip().split(" ")[0][:60]
        s = self.stats.setdefault(key, [0, 0.0])
        s[0] += 1
        s[1] += dt

    @abstractmethod
    def write(self, cmd: str) -> None: ...

    @abstractmethod
    def query(self, cmd: str) -> str: ...

    def query_float(self, cmd: str) -> float:
        return float(self.query(cmd))

    def query_ascii_array(self, cmd: str) -> np.ndarray:
        text = self.query(cmd)
        return np.array([float(x) for x in text.split(",") if x.strip()], dtype=float)

    def clear(self) -> None:
        """清空通訊緩衝（device clear）。"""

    def set_timeout(self, timeout_ms: int) -> None:
        """調整逾時；預設無作用。"""

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# VISA
# ---------------------------------------------------------------------------
_rm_lock = threading.Lock()
_shared_rms: Dict[str, object] = {}
#: 實際使用的 VISA 函式庫（key = 設定值）→ 說明文字（掃描 / 錯誤訊息顯示用）
rm_info: Dict[str, str] = {}

#: Windows 上 NI-VISA 的固定位置。系統預設的 visa32.dll 有時被 R&S VISA / 其他廠商的 VISA 接手，
#: 結果 USB-TMC 儀器（例如 Yokogawa GS200）列不到或打不開；明確指定 NI-VISA 才穩定。
NI_VISA_DLLS = (r"C:\Windows\System32\nivisa64.dll",)
#: 開啟資源時等待鎖定的時間（pyvisa 預設 0 ms，USB 儀器剛插上或被其他程式短暫占用時會直接失敗）
OPEN_TIMEOUT_MS = 3000


def visa_candidates(backend: str = "") -> List[str]:
    """依序嘗試的 VISA 函式庫。backend 有設就只用它；空白 / "auto" = NI-VISA → 系統預設 → pyvisa-py。"""
    import os
    import sys

    b = (backend or "").strip()
    if b and b.lower() != "auto":
        return [b]
    out: List[str] = []
    if sys.platform.startswith("win"):
        out += [p for p in NI_VISA_DLLS if os.path.exists(p)]
    out += ["", "@py"]
    return out


def shared_resource_manager(backend: str = ""):
    """全程式共用 pyvisa ResourceManager（每種 backend 一個）。

    backend：settings 的 server.visa_backend。空白 / "auto" 時優先用 NI-VISA（nivisa64.dll），
    失敗再用系統預設 VISA，最後 pyvisa-py（"@py"）。
    """
    key = (backend or "").strip()
    with _rm_lock:
        rm = _shared_rms.get(key)
        if rm is not None:
            return rm
        import pyvisa  # 延遲載入：模擬模式不需要安裝 pyvisa

        errors = []
        for cand in visa_candidates(key):
            try:
                rm = pyvisa.ResourceManager(cand) if cand else pyvisa.ResourceManager()
            except Exception as e:  # noqa: BLE001
                errors.append(f"{cand or '系統預設 VISA'}：{e}")
                continue
            _shared_rms[key] = rm
            rm_info[key] = cand or "系統預設 VISA"
            log.info("VISA 函式庫：%s", rm_info[key])
            return rm
        raise RuntimeError("無法載入 VISA 函式庫（請安裝 NI-VISA）：" + "；".join(errors))


def reset_resource_managers() -> None:
    """關閉並丟掉共用的 ResourceManager（改 visa_backend 設定或測試用）。"""
    with _rm_lock:
        for rm in _shared_rms.values():
            try:
                rm.close()
            except Exception:  # noqa: BLE001
                pass
        _shared_rms.clear()
        rm_info.clear()


def open_visa_resource(rm, address: str, timeout_ms: int = 5000, write_termination: Optional[str] = "\n",
                       read_termination: Optional[str] = "\n", open_timeout_ms: int = OPEN_TIMEOUT_MS):
    """開啟 VISA 資源並設定逾時 / 結束字元（掃描、連線測試與 VisaTransport 共用同一套做法）。"""
    try:
        res = rm.open_resource(address, open_timeout=open_timeout_ms)
    except TypeError:  # 舊版 / 假的 ResourceManager 不收 open_timeout
        res = rm.open_resource(address)
    try:
        res.timeout = timeout_ms
        if write_termination is not None:
            res.write_termination = write_termination
        if read_termination is not None:
            res.read_termination = read_termination
    except Exception:
        try:
            res.close()
        except Exception:  # noqa: BLE001
            pass
        raise
    return res


class VisaTransport(Transport):
    def __init__(
        self,
        address: str,
        timeout_ms: int = 5000,
        write_termination: Optional[str] = "\n",
        read_termination: Optional[str] = "\n",
        backend: str = "",
        open_timeout_ms: int = OPEN_TIMEOUT_MS,
    ) -> None:
        super().__init__()
        self.address = address
        rm = shared_resource_manager(backend)
        self._res = open_visa_resource(rm, address, timeout_ms, write_termination, read_termination,
                                       open_timeout_ms)

    @property
    def resource(self):
        """原始 pyvisa resource（特殊需求才用，例如 binary transfer）。"""
        return self._res

    def write(self, cmd: str) -> None:
        with self.lock:
            log.debug("[%s] >> %s", self.address, cmd)
            self._tap(">>", cmd)
            t0 = time.perf_counter()
            try:
                self._res.write(cmd)
            except Exception as e:
                self._tap("!!", f"{cmd} → {e}")
                raise
            self._stat(cmd, time.perf_counter() - t0)

    def query(self, cmd: str) -> str:
        with self.lock:
            log.debug("[%s] ?? %s", self.address, cmd)
            self._tap("?>", cmd)
            t0 = time.perf_counter()
            try:
                ans = self._res.query(cmd).strip()
            except Exception as e:
                self._tap("!!", f"{cmd} → {e}")
                raise
            self._stat(cmd, time.perf_counter() - t0)
            log.debug("[%s] << %s", self.address, ans[:200])
            self._tap("<<", ans[:400] + ("…" if len(ans) > 400 else ""))
            return ans

    def clear(self) -> None:
        with self.lock:
            self._res.clear()

    def set_timeout(self, timeout_ms: int) -> None:
        with self.lock:
            self._res.timeout = timeout_ms

    def close(self) -> None:
        with self.lock:
            try:
                self._res.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# 測試用
# ---------------------------------------------------------------------------
Responder = Union[str, Callable[[str], str]]


class MockTransport(Transport):
    """記錄所有寫入指令；query 依 responses 對照表回應（字串或函式）。

    >>> t = MockTransport({":SOUR:LEV?": "0.001"})
    >>> t.write(":OUTP 1"); t.query(":SOUR:LEV?")
    '0.001'
    >>> t.log
    ['W :OUTP 1', 'Q :SOUR:LEV?']
    """

    def __init__(self, responses: Optional[Dict[str, Responder]] = None, default: str = "0") -> None:
        super().__init__()
        self.responses: Dict[str, Responder] = dict(responses or {})
        self.default = default
        self.log: List[str] = []

    @property
    def writes(self) -> List[str]:
        return [x[2:] for x in self.log if x.startswith("W ")]

    def write(self, cmd: str) -> None:
        self.log.append("W " + cmd)
        self._tap(">>", cmd)
        self._stat(cmd, 0.0)

    def query(self, cmd: str) -> str:
        self.log.append("Q " + cmd)
        self._tap("?>", cmd)
        r = self.responses.get(cmd, self.default)
        ans = r(cmd) if callable(r) else r
        self._stat(cmd, 0.0)
        self._tap("<<", str(ans))
        return ans
