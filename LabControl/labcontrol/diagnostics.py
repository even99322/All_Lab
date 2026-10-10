"""儀器驅動測試與 VISA 掃描（儀器伺服器與命令列共用，不依賴 Qt）。

驅動測試（run_driver_test）的目的：在實機上逐項確認 driver 的 SCPI 是否被儀器接受。
每一個步驟之後都讀一次儀器錯誤佇列（:SYST:ERR?），指令寫錯（例如 -113 Undefined header）會直接標在那一項。

    等級                     會做什麼                                          會不會改變儀器
    ─────────────────────────────────────────────────────────────────────────────────────────
    讀取（預設）              連線、*IDN? 比對、讀取所有參數、trace 清單 / 頻率點     不會
    寫回相同值（write_same）   把每個可寫參數「設成它現在的值」→ 驗證設定指令語法      狀態不變
    量測一次（acquire）        觸發一次掃描並讀回資料（VNA）                        會掃描一次，結束恢復連續掃描
    小幅度輸出（output_step）   電源輸出開啟時 +step 再回到原值，確認讀回一致           會短暫改變輸出（step 很小）

報告存成 Markdown（LAB/logs/driver_test_<名稱>_<時間>.md），包含每一項結果、SCPI 次數 / 耗時統計與收發紀錄。
"""
from __future__ import annotations

import datetime as _dt
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from .core.capabilities import Source, TraceAcquirer
from .core.instrument import Channel, Instrument, Parameter, acting_as
from .core.registry import DRIVERS

PASS, WARN, FAIL, SKIP = "pass", "warn", "fail", "skip"
ICON = {PASS: "✔", WARN: "⚠", FAIL: "✖", SKIP: "–"}


@dataclass
class TestItem:
    section: str
    name: str
    status: str
    value: str = ""
    detail: str = ""
    seconds: float = 0.0


@dataclass
class TestReport:
    instrument: str
    driver: str
    address: str
    simulate: bool
    options: Dict[str, Any]
    started: str = field(default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds"))
    idn: str = ""
    items: List[TestItem] = field(default_factory=list)
    stats: Dict[str, List[float]] = field(default_factory=dict)
    traffic: List[str] = field(default_factory=list)
    aborted: bool = False

    def add(self, *a, **k) -> TestItem:
        it = TestItem(*a, **k)
        self.items.append(it)
        return it

    def counts(self) -> Dict[str, int]:
        c = {PASS: 0, WARN: 0, FAIL: 0, SKIP: 0}
        for it in self.items:
            c[it.status] += 1
        return c

    @property
    def ok(self) -> bool:
        return self.counts()[FAIL] == 0 and not self.aborted

    def summary(self) -> str:
        c = self.counts()
        return (f"{self.instrument}（{self.driver}）：✔ {c[PASS]}　⚠ {c[WARN]}　✖ {c[FAIL]}　– {c[SKIP]}"
                + ("　（已中止）" if self.aborted else ""))

    def to_markdown(self) -> str:
        from . import APP_NAME, __version__

        lines = [f"# 驅動測試報告：{self.instrument}", "",
                 "| 項目 | 內容 |", "|---|---|",
                 f"| 儀器 | {self.instrument} |", f"| 驅動 | `{self.driver}` |", f"| 位址 | `{self.address}` |",
                 f"| *IDN? | {self.idn or '—'} |", f"| 模式 | {'模擬' if self.simulate else '實機'} |",
                 f"| 測試項目 | {', '.join(k for k, v in self.options.items() if v) or '只讀取'} |",
                 f"| 時間 | {self.started} |", f"| 程式 | {APP_NAME} {__version__} |", "",
                 f"**結果**：{self.summary()}", ""]
        section = None
        for it in self.items:
            if it.section != section:
                section = it.section
                lines += ["", f"## {section}", "", "| | 項目 | 值 | 耗時 | 說明 |", "|---|---|---|---|---|"]
            lines.append(f"| {ICON[it.status]} | {it.name} | {_md(it.value)} | "
                         f"{it.seconds * 1e3:.0f} ms | {_md(it.detail)} |")
        if self.stats:
            lines += ["", "## SCPI 統計", "", "| 指令 | 次數 | 平均耗時 |", "|---|---|---|"]
            for k, (n, t) in sorted(self.stats.items(), key=lambda kv: -kv[1][1]):
                lines.append(f"| `{_md(k)}` | {int(n)} | {t / max(n, 1) * 1e3:.1f} ms |")
        if self.traffic:
            lines += ["", "## 收發紀錄（最後 400 行）", "", "```"] + self.traffic[-400:] + ["```"]
        return "\n".join(lines) + "\n"

    def save(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"driver_test_{self.instrument}_{_dt.datetime.now():%Y%m%d_%H%M%S}.md"
        path.write_text(self.to_markdown(), encoding="utf-8")
        return path


def _md(s: Any) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def _fmtp(p: Parameter, v: Any) -> str:
    """依參數的顯示單位格式化（例如 5.0198 GHz、10 kHz）。"""
    from .core.units import split_unit

    unit = param_unit(p)
    if isinstance(v, (float, int, np.floating)) and not isinstance(v, bool) and unit:
        scale = split_unit(unit)[1] or 1.0
        return f"{float(v) / scale:.9g} {unit}"
    return _fmt(v, unit)


def param_unit(p: Parameter) -> str:
    """顯示單位：driver 可用 param_unit(name) 提供隨狀態變化的單位（例如 GS 的量程 A / V）。"""
    dyn = getattr(p.owner, "param_unit", None)
    if callable(dyn):
        u = dyn(p.name)
        if u:
            return u
    if p.spec is not None and p.spec.shown_unit:
        return p.spec.shown_unit
    return p.unit or ""


def _fmt(v: Any, unit: str = "") -> str:
    if isinstance(v, (float, np.floating)):
        return f"{v:.9g} {unit}".strip()
    if isinstance(v, np.ndarray):
        return f"array[{v.size}]"
    return f"{v} {unit}".strip() if v is not None else "None"


# ---------------------------------------------------------------------------
def run_driver_test(station, name: str, *, write_same: bool = False, acquire: bool = False,
                    output_step: Optional[float] = None,
                    progress: Optional[Callable[[TestItem], None]] = None,
                    stop_event: Optional[threading.Event] = None) -> TestReport:
    """測試一台儀器的驅動。output_step：小幅度輸出測試的步進（SI，例如 1e-6 A）；None = 不做。"""
    inst: Instrument = station.instruments[name]
    rep = TestReport(name, getattr(inst, "config_driver", inst.driver_name), str(inst.options.get("address", "")),
                     station.simulate, {"讀取": True, "寫回相同值": write_same, "量測一次": acquire,
                                        "小幅度輸出": output_step is not None})
    stop_event = stop_event or threading.Event()
    owner = f"driver-test:{name}"

    def add(section: str, item: str, status: str, value: str = "", detail: str = "", seconds: float = 0.0):
        it = rep.add(section, item, status, value, detail, seconds)
        if progress:
            progress(it)
        return it

    # ---- 連線 ----
    t0 = time.perf_counter()
    try:
        station.ensure_connected([name])
    except Exception as e:  # noqa: BLE001
        add("連線", "連線", FAIL, detail=str(e), seconds=time.perf_counter() - t0)
        return rep
    rep.idn = inst._idn
    add("連線", "連線 / *IDN?", PASS, inst._idn, seconds=time.perf_counter() - t0)
    real_cls = DRIVERS.get(rep.driver) if rep.driver in DRIVERS else type(inst)
    pat = getattr(real_cls, "IDN_PATTERN", None)
    if pat and not station.simulate:
        ok = re.search(pat, inst._idn or "", re.I) is not None
        add("連線", "IDN 與驅動相符", PASS if ok else WARN, pat,
            "" if ok else "IDN 與這個驅動預期的型號不同：確認 instruments.yaml 的 driver 是否選對")
    tr = inst.transport
    taps: List[str] = []
    if tr is not None:
        tr.stats.clear()
        tap = lambda k, s: taps.append(f"{_dt.datetime.now():%H:%M:%S.%f}"[:-3] + f" {k} {s}")  # noqa: E731
        tr.taps.append(tap)

    def errors() -> List[str]:
        try:
            return inst.check_errors()
        except Exception as e:  # noqa: BLE001
            return [f"(讀錯誤佇列失敗：{e})"]

    try:
        with station.lease(owner, [name]), acting_as(owner):
            pre = errors()
            add("連線", "開始前錯誤佇列", WARN if pre else PASS, "; ".join(pre) or "空",
                "開始前就有錯誤（可能是之前的程式留下的），已清除" if pre else "")
            nodes: List[Tuple[str, Any]] = [("", inst)] + [(k, ch) for k, ch in inst.channels.items()]

            # ---- 讀取 ----
            values: Dict[Tuple[str, str], Any] = {}
            for ck, node in nodes:
                sec = f"讀取參數{f'（{ck}）' if ck else ''}"
                for pname, p in node.parameters.items():
                    if stop_event.is_set():
                        rep.aborted = True
                        return rep
                    if not p.gettable:
                        continue
                    t0 = time.perf_counter()
                    try:
                        v = p.get()
                        dt = time.perf_counter() - t0
                        errs = errors()
                        values[(ck, pname)] = v
                        label = (p.spec.label if p.spec is not None and p.spec.label else "") or pname
                        add(sec, f"{pname}（{label}）" if label != pname else pname, FAIL if errs else PASS,
                            _fmtp(p, v), "; ".join(errs), dt)
                    except Exception as e:  # noqa: BLE001
                        add(sec, pname, FAIL, detail=f"{type(e).__name__}: {e}; {'; '.join(errors())}",
                            seconds=time.perf_counter() - t0)
                if isinstance(node, Source) and "output" not in node.parameters:
                    try:
                        on = node.get_output()
                        add(sec, "輸出狀態", PASS, "ON" if on else "OFF", "; ".join(errors()))
                    except Exception as e:  # noqa: BLE001
                        add(sec, "輸出狀態", FAIL, detail=str(e))
                if hasattr(node, "read_state") and not station.simulate:
                    try:
                        warns = node.read_state()
                        for w in warns:
                            add(sec, "狀態檢查", WARN, detail=w)
                    except Exception as e:  # noqa: BLE001
                        add(sec, "狀態檢查", FAIL, detail=str(e))

            # ---- 量測通道 ----
            if isinstance(inst, TraceAcquirer):
                sec = "量測通道"
                cat = None
                if hasattr(inst, "trace_catalog") and not station.simulate:
                    try:
                        cat = inst.trace_catalog()
                        add(sec, "trace 清單", PASS, ", ".join(f"{k}:{'/'.join(v)}" for k, v in cat.items()) or "（空）",
                            "; ".join(errors()))
                    except Exception as e:  # noqa: BLE001
                        add(sec, "trace 清單", FAIL, detail=str(e))
                for trn in inst.trace_list():
                    if stop_event.is_set():
                        rep.aborted = True
                        return rep
                    if cat is not None and trn.upper() not in cat and trn not in (inst.options.get("trace_map") or {}) \
                            and not inst.options.get("auto_create_trace", False):
                        add(sec, f"{trn} x 軸", SKIP, detail="儀器上沒有建立這個 trace，略過（要量測時請在儀器上建立，"
                            "或在 instruments.yaml 設 traces / auto_create_trace: true）")
                        continue
                    t0 = time.perf_counter()
                    try:
                        x = inst.x_axis(trn)
                        errs = errors()
                        add(sec, f"{trn} x 軸", FAIL if errs else PASS,
                            f"{len(x.values)} 點 {x.values[0]:.6g} ~ {x.values[-1]:.6g} {x.unit}" if len(x.values) else "0 點",
                            "; ".join(errs), time.perf_counter() - t0)
                    except Exception as e:  # noqa: BLE001
                        add(sec, f"{trn} x 軸", FAIL, detail=f"{type(e).__name__}: {e}",
                            seconds=time.perf_counter() - t0)
                        continue
                    if acquire:
                        t0 = time.perf_counter()
                        try:
                            z = np.asarray(inst.acquire(trn))
                            dt = time.perf_counter() - t0
                            errs = errors()
                            db = 20 * np.log10(np.abs(z) + 1e-30)
                            ok = z.size == len(x.values) and np.all(np.isfinite(z))
                            add(sec, f"{trn} 量測一次", FAIL if errs or not ok else PASS,
                                f"{z.size} 點，最低 {db.min():.2f} dB，平均 {db.mean():.2f} dB",
                                "; ".join(errs) or ("" if ok else "點數與 x 軸不符或含 NaN"), dt)
                        except Exception as e:  # noqa: BLE001
                            add(sec, f"{trn} 量測一次", FAIL, detail=str(e), seconds=time.perf_counter() - t0)
            elif acquire:
                add("量測通道", "量測一次", SKIP, detail="這台不是量測儀器")

            # ---- 寫回相同值 ----
            if write_same:
                for ck, node in nodes:
                    sec = f"寫回相同值{f'（{ck}）' if ck else ''}"
                    out_on = isinstance(node, Source) and _safe(node.get_output)
                    for pname, p in node.parameters.items():
                        if stop_event.is_set():
                            rep.aborted = True
                            return rep
                        if not p.settable or (ck, pname) not in values:
                            continue
                        v = values[(ck, pname)]
                        if v is None:
                            add(sec, pname, SKIP, detail="讀值為 None")
                            continue
                        if out_on and pname in ("range", "function", "auto_range"):
                            add(sec, pname, SKIP, _fmtp(p, v), "輸出開啟中：驅動會拒絕切換量程 / 功能（安全保護），略過")
                            continue
                        t0 = time.perf_counter()
                        try:
                            if isinstance(node, Source) and pname == "level":
                                node.set_level(float(v), ramp_if_needed=False)
                            else:
                                p.set(v)
                            dt = time.perf_counter() - t0
                            errs = errors()
                            after = p.get()
                            same = _same(v, after)
                            add(sec, pname, FAIL if errs else (PASS if same else WARN), f"{_fmtp(p, v)} → {_fmtp(p, after)}",
                                "; ".join(errs) or ("" if same else "讀回值與原值不同（儀器可能捨入）"), dt)
                        except Exception as e:  # noqa: BLE001
                            add(sec, pname, FAIL, _fmtp(p, v), f"{type(e).__name__}: {e}; {'; '.join(errors())}",
                                time.perf_counter() - t0)

            # ---- 小幅度輸出 ----
            if output_step is not None:
                srcs = [(ck, n) for ck, n in nodes if isinstance(n, Source)]
                if not srcs:
                    add("小幅度輸出", "輸出測試", SKIP, detail="這台沒有輸出通道")
                for ck, node in srcs:
                    sec = "小幅度輸出"
                    label = ck or name
                    try:
                        if not node.get_output():
                            add(sec, label, SKIP, detail="輸出關閉中，不改變輸出狀態，略過")
                            continue
                        v0 = node.get_level()
                        step = max(abs(float(output_step)), 2 * (node.resolution or 0.0))   # 至少 2 個解析度才看得出來
                        if node.ramp_policy.max_jump is not None and step > node.ramp_policy.max_jump:
                            add(sec, label, SKIP, detail=f"步進 {step:g} 超過單次跳動上限 {node.ramp_policy.max_jump:g}")
                            continue
                        target = v0 + step if node.limits.contains(v0 + step) else v0 - step
                        if not node.limits.contains(target):
                            add(sec, label, SKIP, detail="超出安全上下限")
                            continue
                        t0 = time.perf_counter()
                        node.set_level(target, ramp_if_needed=False)
                        got = node.get_level()
                        node.set_level(v0, ramp_if_needed=False)
                        back = node.get_level()
                        dt = time.perf_counter() - t0
                        errs = errors()
                        tol = max((node.resolution or 0.0) * 0.51, step * 0.05)
                        ok = abs(got - target) <= tol and abs(back - v0) <= tol
                        add(sec, label, FAIL if errs or not ok else PASS,
                            f"{v0:.9g} → {got:.9g} → {back:.9g} {node.unit}",
                            "; ".join(errs) or ("" if ok else f"讀回與設定差距超過 {tol:g}"), dt)
                    except Exception as e:  # noqa: BLE001
                        add(sec, label, FAIL, detail=str(e))

            post = errors()
            add("結束", "結束時錯誤佇列", FAIL if post else PASS, "; ".join(post) or "空")
    except Exception as e:  # noqa: BLE001
        add("測試", "測試中斷", FAIL, detail=f"{type(e).__name__}: {e}")
    finally:
        if tr is not None:
            try:
                tr.taps.remove(tap)
            except ValueError:
                pass
            rep.stats = {k: list(v) for k, v in tr.stats.items()}
        rep.traffic = taps
    return rep


def _safe(fn) -> Any:
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return None


def _same(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))
    except (TypeError, ValueError):
        return str(a).strip().upper() == str(b).strip().upper()


# ---------------------------------------------------------------------------
@dataclass
class Resource:
    address: str
    idn: str = ""
    error: str = ""
    suggested: List[str] = field(default_factory=list)


def suggest_drivers(idn: str) -> List[str]:
    out = []
    for name in DRIVERS.names():
        cls = DRIVERS.get(name)
        pat = getattr(cls, "IDN_PATTERN", None)
        if pat and idn and re.search(pat, idn, re.I) and cls.__name__ not in ("RohdeSchwarzVNA",):
            out.append(name)
    return out


def scan_resources(identify: bool = True, include_serial: Optional[bool] = None,
                   timeout_ms: Optional[int] = None, backend: Optional[str] = None,
                   progress: Optional[Callable[[Resource], None]] = None) -> List[Resource]:
    """列出 VISA 資源；identify=True 時對每個資源送 *IDN?（唯讀）並建議驅動。"""
    from .core.transport import shared_resource_manager
    from .settings import setting

    include_serial = setting("server.scan_include_serial", False) if include_serial is None else include_serial
    timeout_ms = int(setting("server.scan_timeout_ms", 1500) if timeout_ms is None else timeout_ms)
    rm = shared_resource_manager(setting("server.visa_backend", "") if backend is None else backend)
    out = []
    for addr in rm.list_resources():
        r = Resource(str(addr))
        if identify and (include_serial or not r.address.upper().startswith("ASRL")):
            try:
                res = rm.open_resource(r.address)
                res.timeout = timeout_ms
                try:
                    r.idn = res.query("*IDN?").strip()
                finally:
                    res.close()
                r.suggested = suggest_drivers(r.idn)
            except Exception as e:  # noqa: BLE001
                r.error = str(e)
        out.append(r)
        if progress:
            progress(r)
    return out
