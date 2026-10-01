"""量測引擎（Runner）：把 SweepPlan + Procedure + Hooks + Writers 串起來執行。

保留舊 sweep_main.py MeasurementThread 的所有行為，但與 UI 完全解耦：
    暫停 / 繼續 / 中斷          pause() resume() stop()
    退回上一點                  rollback()
    手動步進模式                set_manual(True) → measure_once() / loop_start() / loop_stop() / accept(retain)
    斜坡到起點、結束後停靠      RunOptions.approach_rate / park / park_rate
    吸收峰型態變化自動暫停      hooks（dip_shape_pause）
新增：
    錯誤重試與自動暫停          RunOptions.retries / on_error
    儀器 lease                  量測期間其他前端無法寫入同一台儀器
    逐點寫檔                    writers（當機不丟資料）

所有控制方法都是執行緒安全的（只是把指令丟進 queue），任何前端都可以呼叫。
Runner 本身不 import 任何 GUI 套件。
"""
from __future__ import annotations

import datetime as _dt
import logging
import queue
import threading
import time
import traceback
import weakref
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core.capabilities import Source
from ..core.instrument import Parameter, acting_as
from ..core.station import Station
from ..core.units import parse_quantity
from ..data.dataset import Dataset, PointRecord
from .hooks import Hook
from .procedure import Procedure, RunContext
from .sweep import SweepPlan

log = logging.getLogger(__name__)


class State(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"
    ABORTED = "aborted"
    FAILED = "failed"


class Cmd(str, Enum):
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    ROLLBACK = "rollback"
    MANUAL_ON = "manual_on"
    MANUAL_OFF = "manual_off"
    MEASURE_ONCE = "measure_once"
    LOOP_START = "loop_start"
    LOOP_STOP = "loop_stop"
    ACCEPT = "accept"


_MANUAL_CMDS = {Cmd.MEASURE_ONCE, Cmd.LOOP_START, Cmd.LOOP_STOP, Cmd.ACCEPT}


@dataclass
class RunOptions:
    manual: bool = False
    approach_rate: Optional[float] = None   # 移動到第一點的斜坡速率（SI/s）；None=用儀器預設
    park: Any = "start"                     # 結束後：start | none | 數值 | {軸名稱或 target: start | none | 數值}
    park_rate: Optional[float] = None
    settle_first: float = 0.0               # 移動到第一點後額外等待（s）
    retries: int = 1                        # 量測失敗自動重試次數
    on_error: str = "pause"                 # 重試仍失敗：pause（等人處理）| stop
    max_shots: int = 400                    # 手動模式每點最多暫存幾筆
    loop_interval: float = 0.01

    @classmethod
    def from_config(cls, d: Optional[Dict[str, Any]]) -> "RunOptions":
        """實驗 / 方案的 run: 區塊；沒寫的欄位用 settings.yaml 的 run_defaults。"""
        from ..settings import setting

        d = {**(setting("run_defaults", {}) or {}), **dict(d or {})}
        if d.get("loop_interval") is not None:
            d["loop_interval"] = parse_quantity(d["loop_interval"], "s")
        if d.get("settle_first") is not None:
            d["settle_first"] = parse_quantity(d["settle_first"], "s")
        for k in ("approach_rate", "park_rate"):
            if d.get(k) is not None:
                d[k] = parse_quantity(d[k])
        if isinstance(d.get("park"), str) and d["park"] not in ("start", "none", "stay"):
            d["park"] = parse_quantity(d["park"])
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


class Runner:
    _instances: "weakref.WeakSet[Runner]" = weakref.WeakSet()

    @classmethod
    def active_runners(cls) -> List["Runner"]:
        return [r for r in list(cls._instances) if r.is_active]

    @classmethod
    def stop_all(cls, wait: Optional[float] = 30.0) -> None:
        """關閉程式前呼叫：安全中斷所有量測（包含斜坡停靠）。"""
        rs = cls.active_runners()
        for r in rs:
            r.stop()
        for r in rs:
            r.wait(wait)

    def __init__(self, station: Station, procedure: Procedure, plan: SweepPlan, *,
                 options: Optional[RunOptions] = None, hooks: Sequence[Hook] = (),
                 writers: Sequence[Any] = (), name: str = "run",
                 metadata: Optional[Dict[str, Any]] = None) -> None:
        self.station = station
        self.bus = station.bus
        self.procedure = procedure
        self.plan = plan
        self.options = options or RunOptions()
        self.hooks: List[Hook] = list(hooks)
        self.writers = list(writers)
        self.name = name
        self.run_id = f"{name}@{_dt.datetime.now():%H%M%S}-{id(self) % 10000:04d}"
        self.metadata = dict(metadata or {})

        self.state = State.IDLE
        self.dataset: Optional[Dataset] = None
        self.error: Optional[BaseException] = None
        self.manual = self.options.manual
        self.index = 0

        self._cmds: "queue.Queue[Tuple[Cmd, Any]]" = queue.Queue()
        self._stop = threading.Event()
        self._rollback = 0          # 待處理的回溯次數（每按一次退一點）
        self._moved = False
        self._last_applied: Dict[str, float] = {}
        self._thread: Optional[threading.Thread] = None
        self._done = threading.Event()
        self._ctx: Optional[RunContext] = None
        Runner._instances.add(self)

    # ======================================================================
    # 公開控制 API（任何執行緒皆可呼叫）
    # ======================================================================
    def pause(self) -> None: self._send(Cmd.PAUSE)
    def resume(self) -> None: self._send(Cmd.RESUME)
    def rollback(self) -> None: self._send(Cmd.ROLLBACK)
    def measure_once(self) -> None: self._send(Cmd.MEASURE_ONCE)
    def loop_start(self) -> None: self._send(Cmd.LOOP_START)
    def loop_stop(self) -> None: self._send(Cmd.LOOP_STOP)
    def accept(self, retain: bool = False) -> None: self._send(Cmd.ACCEPT, retain)

    def set_manual(self, enabled: bool) -> None:
        self._send(Cmd.MANUAL_ON if enabled else Cmd.MANUAL_OFF)

    def toggle_pause(self) -> None:
        self.resume() if self.state == State.PAUSED else self.pause()

    def stop(self) -> None:
        self._stop.set()
        self._send(Cmd.STOP)

    def hook(self, name: str) -> Optional[Hook]:
        return next((h for h in self.hooks if h.name == name), None)

    @property
    def is_active(self) -> bool:
        return self.state in (State.PREPARING, State.RUNNING, State.PAUSED)

    def start(self) -> threading.Thread:
        """在背景執行緒開始量測（GUI / web 用）。非 daemon：確保程式關閉前完成安全停靠。"""
        if self._thread is not None:
            raise RuntimeError("Runner 只能啟動一次；請建立新的 Runner")
        self._thread = threading.Thread(target=self.run, name=self.run_id, daemon=False)
        self._thread.start()
        return self._thread

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._done.wait(timeout)

    # ======================================================================
    # 主流程
    # ======================================================================
    def run(self) -> Optional[Dataset]:
        """阻塞式執行（腳本 / Jupyter 用）。回傳 Dataset。"""
        status = State.FAILED
        ctx = RunContext(self.station, self.run_id, self._stop)
        self._ctx = ctx
        from .watchdog import StallWatchdog

        dog = StallWatchdog(self).start()          # 太久沒有進度 → 存執行緒狀態到 LAB/logs
        try:
            with acting_as(self.run_id):
                self._set_state(State.PREPARING)
                refs = sorted(set(self.procedure.refs() + [a.target for a in self.plan.axes if a.target]))
                self.station.ensure_connected(refs)
                with self.station.lease(self.run_id, refs):
                    status = self._run_leased(ctx, refs)
        except Exception as e:  # noqa: BLE001
            self.error = e
            status = State.FAILED
            self.bus.log(f"❌ 量測失敗：{e}", "error")
            log.debug(traceback.format_exc())
        finally:
            dog.stop()
            for w in self.writers:
                try:
                    w.close(self.dataset, status.value)
                except Exception:  # noqa: BLE001
                    log.exception("writer 關閉失敗")
            self._set_state(status)
            self.bus.publish("run.finished", run_id=self.run_id, dataset=self.dataset,
                             status=status.value, error=str(self.error) if self.error else None)
            self._done.set()
        return self.dataset

    def _run_leased(self, ctx: RunContext, refs: List[str]) -> State:
        channels = self.procedure.setup(ctx)
        meta = {
            "run_id": self.run_id,
            "created": _dt.datetime.now().isoformat(timespec="seconds"),
            "procedure": self.procedure.metadata(),
            "sweep": [{"target": a.target, "name": a.name, "unit": a.unit, "display_unit": a.display_unit,
                       "n": int(len(a.values)), "first": float(a.values[0]), "last": float(a.values[-1]),
                       "settle": a.settle} for a in self.plan.axes],
            "run_options": asdict(self.options),
            "snapshot": self.station.snapshot(refs),
            **self.metadata,
        }
        self.dataset = Dataset(self.name, [a.spec() for a in self.plan.axes], channels, meta)
        ctx.dataset = self.dataset
        for w in self.writers:
            w.open(self.dataset)
        for h in self.hooks:
            h.on_run_start(ctx)
        self.bus.publish("run.started", run_id=self.run_id, dataset=self.dataset)
        self._set_state(State.RUNNING)

        n_total = len(self.plan)
        try:
            self.bus.log(f"開始掃描（共 {n_total} 點）")
            self._apply(0, approach=True)
            if self.options.settle_first:
                self._sleep(self.options.settle_first)
            t0 = time.monotonic()
            i = 0
            while i < n_total:
                self.index = i
                self._drain()
                if self._stop.is_set():
                    break
                if self._rollback:
                    i = self._do_rollback(i)
                    continue
                if self.state == State.PAUSED:
                    self._wait_while_paused()
                    continue

                ctx.index, ctx.setpoints = i, self.plan.setpoints(i)
                settle = self._apply(i)
                self._progress(i, n_total, t0)
                if not self._sleep(settle):
                    continue
                rec = self._acquire_manual(i, ctx) if self.manual else self._acquire_auto(i, ctx)
                if rec is None:
                    continue
                self._commit(rec)
                action = self._run_hooks(ctx, rec)
                i += 1
                if action == "stop":
                    self._stop.set()
                elif action == "pause" and self.state == State.RUNNING:
                    self._set_state(State.PAUSED)
            self._progress(i, n_total, t0)
            status = State.ABORTED if self._stop.is_set() and i < n_total else State.FINISHED
            self.bus.log("✅ 掃描結束" if status == State.FINISHED else "⚠️ 掃描已中斷")
            return status
        finally:
            for fn in (lambda: self.procedure.teardown(ctx), *[lambda h=h: h.on_run_end(ctx) for h in self.hooks]):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    log.exception("teardown 失敗")
            self._park()

    # ======================================================================
    # 指令處理
    # ======================================================================
    def _send(self, cmd: Cmd, arg: Any = None) -> None:
        self._cmds.put((cmd, arg))

    def _handle_global(self, cmd: Cmd, arg: Any) -> bool:
        if cmd == Cmd.PAUSE:
            if self.state == State.RUNNING:
                self._set_state(State.PAUSED)
                self.bus.log("⏸ 量測已暫停")
        elif cmd == Cmd.RESUME:
            if self.state == State.PAUSED:
                self._set_state(State.RUNNING)
                self.bus.log("▶ 繼續量測")
        elif cmd == Cmd.STOP:
            self._stop.set()
        elif cmd == Cmd.ROLLBACK:
            self._rollback += 1
        elif cmd in (Cmd.MANUAL_ON, Cmd.MANUAL_OFF):
            self.manual = cmd == Cmd.MANUAL_ON
            self.bus.publish("run.manual", run_id=self.run_id, enabled=self.manual)
        else:
            return False
        return True

    def _drain(self) -> None:
        while True:
            try:
                cmd, arg = self._cmds.get_nowait()
            except queue.Empty:
                return
            if not self._handle_global(cmd, arg):
                log.debug("忽略指令 %s（目前非手動模式）", cmd)

    def _wait_while_paused(self) -> None:
        while self.state == State.PAUSED and not self._stop.is_set() and not self._rollback:
            try:
                cmd, arg = self._cmds.get(timeout=0.1)
            except queue.Empty:
                continue
            self._handle_global(cmd, arg)

    def _sleep(self, seconds: float) -> bool:
        """可被中斷的等待；被 stop / rollback 打斷回傳 False。"""
        deadline = time.monotonic() + seconds
        while True:
            self._drain()
            if self._stop.is_set() or self._rollback:
                return False
            remain = deadline - time.monotonic()
            if remain <= 0:
                return True
            time.sleep(min(0.05, remain))

    def _do_rollback(self, i: int) -> int:
        steps, self._rollback = self._rollback, 0
        if i <= 0 or self.dataset is None:
            self.bus.log("⚠️ 已經是第一點，無法退回", "warning")
            return i
        new_i = max(0, i - steps)
        self.dataset.truncate(new_i)
        for w in self.writers:
            w.truncate(self.dataset, new_i)
        for h in self.hooks:
            h.on_rollback(self._ctx, new_i)
        sp = self.plan.setpoints(new_i)
        self.bus.log(f"⏪ 已回溯至第 {new_i + 1} 點 {self._fmt_sp(sp)}")
        self.bus.publish("point.rollback", run_id=self.run_id, index=new_i, setpoints=sp)
        if self.state == State.PAUSED:
            self._apply(new_i)   # 暫停中回溯：立刻把儀器設回該點（與舊版相同）
        return new_i

    # ======================================================================
    # 量測
    # ======================================================================
    def _shot(self, i: int, ctx: RunContext) -> Optional[Dict[str, Any]]:
        attempt = 0
        while True:
            try:
                data = self.procedure.measure(ctx)
                self.bus.publish("point.shot", run_id=self.run_id, index=i, setpoints=dict(ctx.setpoints), shot=data)
                return data
            except Exception as e:  # noqa: BLE001
                attempt += 1
                self.bus.log(f"❌ 第 {i + 1} 點量測失敗（第 {attempt} 次）：{e}", "error")
                self.bus.publish("instrument.error", run_id=self.run_id, index=i, error=str(e))
                self.procedure.on_error(ctx, e)
                if attempt <= self.options.retries:
                    continue
                if self.options.on_error == "stop":
                    raise
                self._set_state(State.PAUSED)
                self.bus.log("⏸ 已自動暫停，排除問題後按「繼續」會重試這一點", "warning")
                self._wait_while_paused()
                if self._stop.is_set() or self._rollback:
                    return None
                attempt = 0

    def _acquire_auto(self, i: int, ctx: RunContext) -> Optional[PointRecord]:
        shot = self._shot(i, ctx)
        if shot is None:
            return None
        return PointRecord(index=i, setpoints=dict(ctx.setpoints), shots=[shot], selected=0)

    def _acquire_manual(self, i: int, ctx: RunContext) -> Optional[PointRecord]:
        shots: List[Dict[str, Any]] = []
        looping = False
        self.bus.publish("manual.waiting", run_id=self.run_id, index=i, setpoints=dict(ctx.setpoints))
        self.bus.log(f"🖐️ [手動模式] 第 {i + 1} 點等待指令…")

        def take() -> bool:
            s = self._shot(i, ctx)
            if s is None:
                return False
            shots.append(s)
            if len(shots) > self.options.max_shots:
                shots.pop(0)
            self.bus.publish("manual.shots", run_id=self.run_id, index=i, count=len(shots))
            return True

        while True:
            if self._stop.is_set() or self._rollback:
                return None
            fast = looping and self.state == State.RUNNING
            try:
                cmd, arg = self._cmds.get_nowait() if fast else self._cmds.get(timeout=0.05)
            except queue.Empty:
                cmd, arg = None, None

            if cmd is None:
                if fast:
                    if not take():
                        return None
                    time.sleep(self.options.loop_interval)
                continue

            if cmd in _MANUAL_CMDS:
                if cmd == Cmd.LOOP_START:
                    looping = True
                    self.bus.log("🔁 開始循環量測")
                elif cmd == Cmd.LOOP_STOP:
                    looping = False
                    self.bus.log("⏹ 循環量測已停止")
                elif cmd == Cmd.MEASURE_ONCE and self.state == State.RUNNING:
                    if not take():
                        return None
                elif cmd == Cmd.ACCEPT:
                    if not shots:
                        self.bus.log("⚠️ 尚未量測任何數據，請先量測再確認", "warning")
                        continue
                    retain = bool(arg)
                    kept = list(shots) if retain else [shots[-1]]
                    if retain:
                        self.bus.log(f"📌 已保留 {len(kept)} 筆備選數據，進入下一點")
                    return PointRecord(index=i, setpoints=dict(ctx.setpoints), shots=kept,
                                       selected=len(kept) - 1, retained=retain)
                continue

            self._handle_global(cmd, arg)
            if cmd == Cmd.PAUSE:
                looping = False
            if not self.manual:   # 中途切回自動：用最後一筆，沒有就量一次
                if not shots and not take():
                    return None
                return PointRecord(index=i, setpoints=dict(ctx.setpoints), shots=[shots[-1]], selected=0)

    def _commit(self, rec: PointRecord) -> None:
        assert self.dataset is not None
        self.dataset.add(rec)
        for w in self.writers:
            w.write_point(self.dataset, rec)
        self.bus.publish("point.committed", run_id=self.run_id, record=rec, n=len(self.dataset))

    def _run_hooks(self, ctx: RunContext, rec: PointRecord) -> Optional[str]:
        worst: Optional[str] = None
        for h in self.hooks:
            if not h.enabled:
                continue
            try:
                res = h.after_point(ctx, rec)
            except Exception:  # noqa: BLE001
                log.exception("hook %s 失敗", h.name)
                continue
            if res is None:
                continue
            self.bus.log(f"⚠️ [{h.name}] {res.reason}", "warning")
            self.bus.publish("run.hook", run_id=self.run_id, hook=h.name, action=res.action, reason=res.reason)
            if res.action == "stop" or worst is None:
                worst = res.action
        return worst

    # ======================================================================
    # 設定儀器
    # ======================================================================
    def _source_of(self, target: str) -> Optional[Source]:
        if not target:
            return None
        obj = self.station.get(target)
        if isinstance(obj, Parameter):
            return obj.owner if (obj.name == "level" and isinstance(obj.owner, Source)) else None
        if isinstance(obj, Source):
            return obj
        return Station._sole_source(obj)

    def _apply(self, i: int, approach: bool = False) -> float:
        sp = self.plan.setpoints(i)
        settle = 0.0
        for axis in self.plan.axes:
            v = sp[axis.name]
            if self._last_applied.get(axis.name) == v or not axis.target:
                continue
            src = self._source_of(axis.target)
            if approach and src is not None:
                rate = self.options.approach_rate or src.ramp_policy.rate
                if rate is None:
                    self.bus.log(f"⚠️ {axis.target} 沒有設定斜坡速率，將直接跳到起點", "warning")
                else:
                    try:
                        cur = float(src.parameters["level"].get())  # type: ignore[attr-defined]
                        eta = abs(v - cur) / rate if rate else 0.0
                        tail = f"（目前 {cur:g} {src.unit}，約 {eta / 60:.1f} 分鐘）" if eta >= 1 else ""
                    except Exception:  # noqa: BLE001
                        tail = ""
                    self.bus.log(f"以 {rate:g} {src.unit}/s 斜坡移動 {axis.target} → {v:g} {src.unit}{tail}")
                src.ramp_to(v, rate=rate, stop_event=self._stop,
                            on_step=lambda x, t=axis.target, to=v, r=rate: self.bus.publish(
                                "run.ramp", run_id=self.run_id, target=t, value=x, to=to, rate=r))
            else:
                self.station.parameter(axis.target).set(v)
            self._moved = True
            self._last_applied[axis.name] = v
            settle = max(settle, axis.settle)
        return settle

    def _park(self) -> None:
        park = self.options.park
        if not self._moved or park in (None, "none", "stay"):
            return
        for axis in self.plan.axes:
            src = self._source_of(axis.target)
            if src is None:
                continue
            if park == "start":
                target = float(axis.values[0])
            elif isinstance(park, dict):
                if axis.target not in park and axis.name not in park:
                    continue
                p = park.get(axis.name, park.get(axis.target))
                if p in (None, "none", "stay"):
                    continue
                target = float(axis.values[0]) if p == "start" else parse_quantity(p)
            else:
                target = float(park)
            rate = self.options.park_rate or src.ramp_policy.rate
            try:
                self.bus.log(f"收尾：{axis.target} 斜坡回到 {target:g} {src.unit}")
                src.ramp_to(target, rate=rate)  # 不可被 stop 中斷
            except Exception as e:  # noqa: BLE001
                self.bus.log(f"❌ 收尾停靠失敗 {axis.target}：{e}", "error")

    # ======================================================================
    def _set_state(self, s: State) -> None:
        prev, self.state = self.state, s
        if prev != s:
            self.bus.publish("run.state", run_id=self.run_id, state=s.value, previous=prev.value)

    def _progress(self, i: int, n: int, t0: float) -> None:
        elapsed = time.monotonic() - t0
        eta = (elapsed / i) * (n - i) if i > 0 else None
        sp = self.plan.setpoints(min(i, n - 1))
        disp = {a.name: f"{sp[a.name] * a.display_scale:.6g} {a.display_unit}" for a in self.plan.axes}
        self.bus.publish("run.progress", run_id=self.run_id, index=i, total=n,
                         elapsed=elapsed, eta=eta, setpoints=sp, display=disp)

    def _fmt_sp(self, sp: Dict[str, float]) -> str:
        parts = []
        for a in self.plan.axes:
            parts.append(f"{a.name}={sp[a.name] * a.display_scale:.6g} {a.display_unit}")
        return "(" + ", ".join(parts) + ")"
