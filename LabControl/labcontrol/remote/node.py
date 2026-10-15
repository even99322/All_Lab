"""量測節點：量測電腦上執行，透過 Lab Control Hub（NAS 網站）接受其他電腦的指令並上傳即時資料與量測檔。

    node = NodeService(station, find_hub(), name="QEL-PC").start()
    ...
    node.stop()

主視窗「🛰 量測節點」或 `python -m labcontrol node` 開啟。

安全：
  * 同一時間只能有一個量測（本機或遠端都一樣）；儀器 lease 照舊，量測中的儀器不能被別人寫入。
  * 停止、暫停任何人都可以按（安全優先）；開始量測 / 設定儀器可在 settings.yaml 關閉或限定使用者。
  * 更新：量測進行中不更新，等量測結束才執行；更新前狀態會回報 Hub，讓控制端與網頁看得到。
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .. import __version__
from ..core.station import Station
from ..core.units import parse_quantity
from ..paths import lab_path
from ..settings import setting
from .codec import FORWARD, enc_array, encode_event
from .hub import Hub, HubConflict, HubError, default_node_name, hostname, read_json, safe_name, user_name, write_json
from .instruments import describe_instrument, instrument_key, instrument_report, last_scan, record_scan
from .versioning import is_newer

log = logging.getLogger(__name__)

#: 不排隊、立即處理的指令（停止一定要馬上執行）
QUICK = {"ping", "status", "stop", "pause", "update", "release", "group_stop", "ramp_status"}
#: 只讀，不檢查 allowed_users
READ_ONLY = {"ping", "status", "get", "get_all", "describe", "sources", "ramp_status", "vnas", "vna_catalog",
             "catalog", "templates", "check_scheme", "instruments_file"}
#: 即時監控的讀取 / 控制：另一條佇列處理，不會排在「全部連線」「開始量測」這類慢指令後面而逾時
LIVE = {"get", "describe", "get_all", "sources", "vnas", "vna_catalog", "vna_ports", "vna_trace", "vna_traces",
        "vna_scan", "vna_set_visible", "fine_step", "output", "group_ramp"}
#: 等待上傳的即時資料上限（Hub 斷線太久時丟掉最舊的曲線，不影響量測）
MAX_PENDING = 4000


def owner_guard(owners_fn: Callable[[], Dict[str, str]], station: Station, host: Callable[[], str],
                shared_fn: Optional[Callable[[], Any]] = None):
    """Station.connect_guard：共用儀器（多台電腦都偵測到的網路儀器）要先歸到某台的群組才能連線。

    * 歸別台電腦 → 拒絕；
    * 還沒歸屬（Hub 的 shared 清單裡、沒有 owner）→ 拒絕，請先在儀器伺服器把它移到群組。
    """
    def guard(name: str) -> Optional[str]:
        inst = station.instruments.get(name)
        if inst is None:
            return None
        key = instrument_key(str(inst.options.get("address") or ""), inst.options, host())
        if not key:
            return None
        owner = (owners_fn() or {}).get(key)
        if owner and owner.lower() != host().lower():
            return (f"{name} 目前歸 {owner} 使用（共用儀器 {key}）；"
                    "請在儀器伺服器把它移到這台的群組（或 Lab Control Monitor「拉取到這台」）後再連線")
        if not owner and shared_fn is not None and key in set(shared_fn() or ()):
            return (f"{name} 是共用儀器（{key}，多台電腦都偵測得到），尚未歸屬；"
                    "請先在儀器伺服器把它移到某個量測節點的群組")
        return None
    return guard


STATE_FILE = "node_state.json"


def load_node_state() -> Dict[str, Any]:
    return read_json(lab_path(STATE_FILE), {}) or {}


def save_node_state(**kw: Any) -> None:
    st = load_node_state()
    st.update(kw)
    try:
        write_json(lab_path(STATE_FILE), st)
    except OSError:
        pass


class CommandError(RuntimeError):
    pass


class NodeService:
    def __init__(self, station: Station, hub: Hub, name: Optional[str] = None,
                 on_restart: Optional[Callable[[], None]] = None) -> None:
        self.station, self.hub = station, hub
        self.name = safe_name(name or default_node_name())
        self.on_restart = on_restart
        self.run: Dict[str, Any] = {}
        self.run_by: Dict[str, str] = {}              # run_id → 誰啟動的
        self.update_pending: Optional[str] = None
        self.update_error = ""
        self.update_message = ""
        self.last_error = ""
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._events: List[Dict[str, Any]] = []
        self._work: "queue.Queue" = queue.Queue()
        self._live: "queue.Queue" = queue.Queue()
        self._tokens: List[int] = []
        self._threads: List[threading.Thread] = []
        self._restarting = False
        self._err_logged = ""
        self._status_now = threading.Event()          # 量測開始 / 結束：立即回報，不等下一次心跳
        self._out: List[Tuple[str, Any]] = []         # 待上傳（("head", …) / ("event", …)）；只在這裡排隊，不做網路
        self._olock = threading.Lock()
        self._out_ev = threading.Event()
        self.owners: Dict[str, str] = {}              # Hub 的共用儀器歸屬（key → 電腦名稱）
        self.shared: List[str] = []                   # 多台電腦都偵測到的網路儀器（未歸屬時不可連線）

    # ---- 生命週期 -----------------------------------------------------------
    def start(self) -> "NodeService":
        import os

        try:
            self.hub.claim(self.name, {"host": hostname(), "user": user_name(), "pid": os.getpid(),
                                       "version": __version__})
        except HubConflict as e:
            raise HubError(str(e)) from None
        from ..core.nodes import register_running

        register_running(self.name)
        try:                        # 這台現在是量測節點：instruments.yaml 指定給別台的儀器移出（中斷、不回報）
            away = self.station.apply_ownership()
            if away:
                log.info("量測節點 %s：%s 接在別台，不載入", self.name, ", ".join(sorted(away)))
        except Exception:  # noqa: BLE001
            log.exception("套用儀器歸屬（node:）失敗")
        self.write_instruments()
        for t in FORWARD:
            self._tokens.append(self.station.bus.subscribe(t, self._on_event))
        if self.station.connect_guard is None:
            self.station.connect_guard = owner_guard(lambda: self.owners, self.station, hostname, lambda: self.shared)
        for fn, nm in ((self._loop, "node-loop"), (self._cmd_loop, "node-commands"), (self._worker, "node-worker"),
                       (self._live_worker, "node-live"),
                       (self._uploader, "node-upload")):
            th = threading.Thread(target=fn, daemon=True, name=nm)
            th.start()
            self._threads.append(th)
        self._post_status()
        log.info("量測節點 %s 上線（%s）", self.name, self.hub.url)
        return self

    def stop(self) -> None:
        from ..core.nodes import register_running

        register_running(self.name, False)
        self._stop.set()
        self._work.put(None)
        self._live.put(None)
        for t in self._tokens:
            self.station.bus.unsubscribe(t)
        self._tokens.clear()
        self._out_ev.set()
        try:
            self._send_pending()
            self.hub.offline(self.name)
        except HubError as e:
            log.warning("節點離線通知失敗：%s", e)

    def write_instruments(self) -> None:
        """節點的儀器清單放到 Hub（控制端用它建立「模擬鏡像」來編輯 / 檢查方案）。"""
        text = ""
        p = self.station.config_path
        if p is not None and p.exists():
            text = p.read_text(encoding="utf-8")
        else:
            import yaml

            inst = {k: v for k, v in (self.station.config.get("instruments") or {}).items()}
            text = yaml.safe_dump({"instruments": inst}, allow_unicode=True, sort_keys=False)
        self.hub.put_instruments(self.name, text)

    # ---- 狀態 ------------------------------------------------------------
    def _active_runner(self):
        from ..measure.runner import Runner

        rs = Runner.active_runners()
        if rs:
            return rs[0]
        r = getattr(self, "_last_runner", None)       # 剛 start()、執行緒還沒進入 PREPARING 的量測也算
        if r is not None and not r._done.is_set():
            return r
        return None

    def data_info(self) -> Dict[str, Any]:
        """0.0.31：這台量測電腦存檔的地方（給遠端的檔案設置顯示、選專案資料夾）；資料夾清單 30 秒更新一次。"""
        from ..data.naming import data_root, folder_pattern, list_project_folders

        now = time.time()
        cache = getattr(self, "_data_info", None)
        if cache is None or now - cache[0] > 30:
            root = data_root()
            cache = (now, {"root": str(root), "pattern": folder_pattern(), "sep": os.sep,
                           "folders": list_project_folders(root)[:300]})
            self._data_info = cache
        return dict(cache[1])

    def status(self) -> Dict[str, Any]:
        r = self._active_runner()
        state = "updating" if self._restarting else (r.state.value if r is not None else "idle")
        import os

        with self._olock:
            pending = len(self._out)
        return {"name": self.name, "host": hostname(), "user": user_name(), "version": __version__, "pid": os.getpid(),
                "time": time.time(), "state": state, "simulate": self.station.simulate,
                "run": dict(self.run) if self.run else None,
                "update": {"pending": self.update_pending, "error": self.update_error,
                           "message": self.update_message},
                "allow": {"run": bool(setting("remote.node.allow_remote_run", True)),
                          "set": bool(setting("remote.node.allow_remote_set", True)),
                          "update": bool(setting("remote.node.allow_update", True))},
                "instruments": instrument_report(self.station), "scan": dict(last_scan),
                "upload_pending": pending, "error": self.last_error, "data": self.data_info()}

    def _post_status(self) -> None:
        r = self.hub.put_status(self.name, self.status())
        if isinstance(r, dict) and isinstance(r.get("owners"), dict):
            self.owners = dict(r["owners"])
            if isinstance(r.get("shared"), list):
                self.shared = list(r["shared"])

    # ---- 主迴圈 ------------------------------------------------------------
    def _note_error(self, where: str, e: Exception) -> None:
        msg = f"{where}：{e}"
        self.last_error = str(e)
        if msg != self._err_logged:            # 同樣的錯誤只記一次（Hub 斷線時不洗版）
            log.warning("節點 %s %s", self.name, msg)
            self._err_logged = msg

    def _loop(self) -> None:
        """心跳、檢查更新（即時資料由 _uploader 上傳）。"""
        hb = float(setting("remote.heartbeat_s", 2))
        live = float(setting("remote.live_interval_s", 0.5))
        last_hb = 0.0
        while not self._stop.is_set():
            try:
                now = time.time()
                if now - last_hb >= hb or self._status_now.is_set():
                    self._status_now.clear()
                    self._post_status()
                    last_hb = now
                self._maybe_update()
                if self.last_error:
                    log.info("節點 %s 與 Hub 的連線恢復", self.name)
                self.last_error, self._err_logged = "", ""
            except Exception as e:  # noqa: BLE001 — Hub 暫時連不到（NAS 重開、斷線）：繼續重試
                self._note_error("回報 Hub 失敗", e)
                self._stop.wait(2.0)
            self._stop.wait(min(live, hb) / 2)

    def _uploader(self) -> None:
        """即時資料的上傳執行緒：量測執行緒只把事件放進佇列，網路慢或斷線都不會拖住量測。"""
        live = float(setting("remote.live_interval_s", 0.5))
        while not self._stop.is_set():
            self._out_ev.wait(live)
            self._out_ev.clear()
            if not self._send_pending():
                self._stop.wait(1.0)

    def _send_pending(self) -> bool:
        with self._olock:
            items, self._out = self._out, []
        i = 0
        try:
            while i < len(items):
                kind, payload = items[i]
                if kind == "head":
                    self.hub.write_head(self.name, payload)
                    i += 1
                    continue
                j = i
                batch = []
                while j < len(items) and items[j][0] == "event" and len(batch) < 400:
                    batch.append(items[j][1])
                    j += 1
                self.hub.write_batch(self.name, batch)
                i = j
            return True
        except Exception as e:  # noqa: BLE001
            with self._olock:
                self._out[:0] = items[i:]
                self._trim()
            self._note_error("上傳即時資料失敗", e)
            return False

    def _trim(self) -> None:
        """佇列太長（Hub 長時間連不到）：丟掉最舊的曲線資料，保留量測開頭、狀態、結束與訊息。"""
        extra = len(self._out) - MAX_PENDING
        if extra <= 0:
            return
        keep = []
        for kind, payload in self._out:
            if extra > 0 and kind == "event" and payload.get("t") in ("point.shot", "run.progress", "run.ramp"):
                extra -= 1
                continue
            keep.append((kind, payload))
        self._out = keep

    def _enqueue(self, kind: str, payload: Any, urgent: bool = False) -> None:
        with self._olock:
            self._out.append((kind, payload))
            self._trim()
        if urgent:
            self._out_ev.set()

    def _cmd_loop(self) -> None:
        """long-poll 取指令：有指令時 Hub 立即回傳（不必固定間隔輪詢）。"""
        wait = float(setting("remote.command_wait_s", 20))
        while not self._stop.is_set():
            try:
                msgs = self.hub.take_commands(self.name, wait)
            except Exception as e:  # noqa: BLE001
                self._note_error("取指令失敗", e)
                self._stop.wait(2.0)
                continue
            for msg in msgs:
                if self._stop.is_set():
                    return
                if msg.get("cmd") in QUICK:
                    self._handle(msg)
                elif msg.get("cmd") in LIVE:
                    self._live.put(msg)
                else:
                    self._work.put(msg)

    def _worker(self) -> None:
        while not self._stop.is_set():
            msg = self._work.get()
            if msg is None:
                return
            self._handle(msg)

    def _live_worker(self) -> None:
        while not self._stop.is_set():
            msg = self._live.get()
            if msg is None:
                return
            self._handle(msg)

    def _handle(self, msg: Dict[str, Any]) -> None:
        cid = str(msg.get("id", ""))
        cmd = str(msg.get("cmd", ""))
        sender = msg.get("from") or {}
        try:
            fn = getattr(self, f"cmd_{cmd}", None)
            if fn is None:
                raise CommandError(f"不認得的指令 {cmd}（節點版本 {__version__}）")
            allowed = setting("remote.node.allowed_users", []) or []
            if allowed and cmd not in READ_ONLY and sender.get("user") not in allowed and cmd not in ("stop", "pause"):
                raise CommandError(f"{sender.get('user')} 不在這個節點的允許名單")
            result = fn(sender, **(msg.get("args") or {}))
            reply = {"id": cid, "ok": True, "result": result, "ts": time.time()}
        except Exception as e:  # noqa: BLE001
            log.debug(traceback.format_exc())
            reply = {"id": cid, "ok": False, "error": str(e) or type(e).__name__, "ts": time.time()}
        try:
            self.hub.reply(cid, reply)
        except HubError as e:
            log.warning("回覆失敗：%s", e)

    # ---- 即時資料 ------------------------------------------------------------
    def _on_event(self, topic: str, p: Dict[str, Any]) -> None:
        """在量測執行緒被呼叫：只做編碼與排隊，不做任何網路傳輸。"""
        try:
            if topic == "run.started":
                from ..measure.runner import Runner

                runner = next((r for r in list(Runner._instances) if r.run_id == p.get("run_id")), None)
                ev = encode_event(topic, p, runner)
                self.run = {"id": p.get("run_id"), "name": p["dataset"].name,
                            "by": self.run_by.get(p.get("run_id"), f"{user_name()}@{hostname()}（本機）"),
                            "index": 0, "total": len(runner.plan) if runner else None, "status": "running",
                            "started": time.time()}
                self._status_now.set()
                self._enqueue("head", {"run_id": p.get("run_id"), "started": ev, "by": self.run["by"]}, urgent=True)
                return
            ev = encode_event(topic, p)
            if ev is None:
                return
            if topic == "run.progress":
                self.run.update(index=p.get("index"), total=p.get("total"), eta=p.get("eta"))
            elif topic == "run.finished":
                self.run.update(status=p.get("status"), finished=time.time())
                self._status_now.set()
            self._enqueue("event", ev, urgent=topic in ("run.finished", "run.state"))
        except Exception as e:  # noqa: BLE001
            log.debug("事件上傳失敗：%s", e)

    def _flush(self) -> None:
        self._send_pending()

    def push_event(self, ev: Dict[str, Any]) -> None:
        self._enqueue("event", ev, urgent=True)

    # ---- 指令 ------------------------------------------------------------
    def cmd_ping(self, sender) -> Dict[str, Any]:
        return {"version": __version__, "name": self.name}

    def cmd_status(self, sender) -> Dict[str, Any]:
        return self.status()

    def cmd_run_scheme(self, sender, scheme: Dict[str, Any], root: Optional[str] = None) -> Dict[str, Any]:
        from ..scheme import Scheme, build_catalog, compile_scheme

        if not setting("remote.node.allow_remote_run", True):
            raise CommandError("這個節點不允許遠端開始量測（settings.yaml remote.node.allow_remote_run）")
        if self._active_runner() is not None:
            raise CommandError("節點上已經有量測在進行")
        if self.update_pending or self._restarting:
            raise CommandError("節點即將更新，請稍候再開始")
        scheme = dict(scheme)
        output = dict(scheme.get("output") or {})
        # 0.0.31：在量測電腦上執行 → 一律存到這台的資料根目錄（settings.yaml data.root）；
        # 送來的 root 是遠端那台電腦自己的路徑（例如 C:\Users\even9\Desktop\…），不能用
        if output.pop("root", None) is not None:
            scheme["output"] = output
        sch = Scheme.from_dict(scheme)
        res = compile_scheme(sch, build_catalog(self.station))
        if not res.ok:
            raise CommandError("方案在節點上檢查失敗：" + "；".join(i.message for i in res.issues if i.level == "error"))
        exp = res.experiment(self.station)
        out = exp.plan_output(root=root)
        from ..data.naming import data_root

        root_dir = data_root(root or exp.output_cfg.get("root"))
        runner = exp.create_runner(out)
        by = str(sender.get("user", "?"))
        self.run_by[runner.run_id] = by

        def finished(topic, p):
            if p.get("run_id") != runner.run_id:
                return
            self.station.bus.unsubscribe(tok)
            ds = p.get("dataset")
            if ds is not None and len(ds):
                threading.Thread(target=self._export, args=(exp, ds, out, runner.run_id, root_dir),
                                 daemon=True).start()
        tok = self.station.bus.subscribe("run.finished", finished)
        self.station.bus.log(f"🛰 {by} 從遠端開始量測「{sch.name}」")
        self._last_runner = runner
        runner.start()
        return {"run_id": runner.run_id, "folder": str(out.folder), "file_name": out.file_name,
                "points": res.total_points, "est_seconds": res.est_seconds}

    def _export(self, exp, ds, out, run_id: Optional[str] = None, root_dir: Optional[Path] = None) -> None:
        written: List[Path] = []                      # 其中一種格式失敗時，已寫好的檔仍要上傳
        tok = self.station.bus.subscribe("data.exported", lambda t, p: written.append(Path(p["path"])))
        from ..integrations.platform import suspend_uploads

        try:
            with suspend_uploads():               # 節點自己上傳到 Hub（這台電腦的平台上傳不重複做）
                paths = list(exp.export(ds, out))
        except Exception as e:  # noqa: BLE001
            self.station.bus.log(f"❌ 匯出失敗：{e}", "error")
            paths = []
        finally:
            self.station.bus.unsubscribe(tok)
        paths = list(dict.fromkeys([*paths, *written]))
        if out.raw_path is not None and Path(out.raw_path).exists():
            paths.append(Path(out.raw_path))
        if paths and setting("remote.upload_results", True):
            tags = exp.output_tags() if hasattr(exp, "output_tags") else []
            project = exp.output_project().get("id", "") if hasattr(exp, "output_project") else ""
            self.upload_results(paths, root_dir, run_id, tags, getattr(exp, "name", ""), project)

    def upload_results(self, paths: Sequence[Path], root_dir: Optional[Path], run_id: Optional[str],
                       tags: Optional[List[str]] = None, scheme: str = "", project: str = "") -> List[Dict]:
        """量測檔上傳到 Hub（數據中轉），並通知控制端（remote.files 事件）。"""
        done: List[Dict[str, Any]] = []
        for p in paths:
            p = Path(p)
            try:
                rel = p.resolve().relative_to(Path(root_dir).resolve()).as_posix() if root_dir else p.name
            except ValueError:
                rel = p.name
            try:
                info = self.hub.upload(self.name, rel, p, tags=tags, run_id=run_id or "", scheme=scheme,
                                       project=project)
                done.append({"rel": info.get("rel", rel), "url": info.get("url"), "size": info.get("size")})
            except HubError as e:
                self.station.bus.log(f"⚠ 上傳 {p.name} 到 Hub 失敗：{e}（檔案仍在節點上）", "warning")
        if done:
            self.station.bus.log(f"📤 已上傳 {len(done)} 個檔到 Hub")
            self.push_event({"t": "remote.files", "run_id": run_id, "files": done})
        return done

    def _runner(self):
        r = self._active_runner()
        if r is None:
            raise CommandError("節點上沒有進行中的量測")
        return r

    def cmd_stop(self, sender) -> Dict[str, Any]:
        self._runner().stop()
        self.station.bus.log(f"🛰 {sender.get('user')} 從遠端停止量測")
        return {"stopping": True}

    def cmd_pause(self, sender) -> Dict[str, Any]:
        self._runner().pause()
        return {"paused": True}

    def cmd_resume(self, sender) -> Dict[str, Any]:
        self._runner().resume()
        return {"resumed": True}

    def cmd_rollback(self, sender) -> Dict[str, Any]:
        self._runner().rollback()
        return {}

    def cmd_manual(self, sender, on: bool = True) -> Dict[str, Any]:
        self._runner().set_manual(bool(on))
        return {}

    def cmd_measure_once(self, sender) -> Dict[str, Any]:
        self._runner().measure_once()
        return {}

    def cmd_loop(self, sender, on: bool = True) -> Dict[str, Any]:
        r = self._runner()
        r.loop_start() if on else r.loop_stop()
        return {}

    def cmd_accept(self, sender, retain: bool = False) -> Dict[str, Any]:
        self._runner().accept(bool(retain))
        return {}

    def cmd_get(self, sender, refs: List[str], connect: bool = False) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for r in refs:
            try:
                if connect:
                    self.station.ensure_connected([r])
                v = self.station.parameter(r).get()
                if hasattr(v, "__len__") and not isinstance(v, str):
                    v = enc_array(v)
                elif hasattr(v, "item"):
                    v = v.item()
                out[r] = {"value": v}
            except Exception as e:  # noqa: BLE001
                out[r] = {"value": None, "error": str(e)}
        return out

    def cmd_set(self, sender, ref: str, value: Any) -> Any:
        if not setting("remote.node.allow_remote_set", True):
            raise CommandError("這個節點不允許遠端設定儀器（settings.yaml remote.node.allow_remote_set）")
        self.station.ensure_connected([ref])
        p = self.station.parameter(ref)
        if isinstance(value, str):
            try:
                value = parse_quantity(value, p.unit or "")
            except Exception:  # noqa: BLE001
                pass
        p.set(value)
        self.station.bus.log(f"🛰 {sender.get('user')} 設定 {ref} = {value}")
        v = p.get() if p.gettable else value
        return v.item() if hasattr(v, "item") else v

    def cmd_connect(self, sender, names: Optional[List[str]] = None, force: bool = False) -> Dict[str, str]:
        """連線（唯讀，不改變輸出）。回傳 {名稱: 錯誤}（空字串 = 成功）。共用儀器歸別台時會被拒絕。"""
        res = self.station.connect_all(names or None, force=bool(force))
        self._status_now.set()
        return {k: v for k, v in res.items() if v or names is None or k in (names or [])}

    def cmd_connect_all(self, sender) -> Dict[str, str]:
        """這台節點「群組」內的儀器全部連線：共用且歸別台電腦的會略過（需先拉取）。"""
        res = self.station.connect_all()
        ok = sum(1 for v in res.values() if not v)
        self.station.bus.log(f"🛰 {sender.get('user')} 遠端全部連線：{ok}/{len(res)} 台成功")
        self._status_now.set()
        return res

    def cmd_disconnect(self, sender, names: List[str]) -> Dict[str, str]:
        res = self.station.disconnect_all(names)
        self._status_now.set()
        return res

    def cmd_disconnect_all(self, sender) -> Dict[str, str]:
        res = self.station.disconnect_all()
        self.station.bus.log(f"🛰 {sender.get('user')} 遠端全部中斷連線")
        self._status_now.set()
        return res

    def cmd_release(self, sender, key: str, owner: str = "") -> Dict[str, Any]:
        """共用儀器被拉到別台電腦：中斷這台上對應的儀器（量測中的不中斷）。"""
        if owner:
            self.owners[key] = owner
        done, busy = [], []
        for n, inst in self.station.instruments.items():
            k = instrument_key(str(inst.options.get("address") or ""), inst.options)
            if k != key or not inst.connected:
                continue
            if self.station.lease_holder(n):
                busy.append(n)
                continue
            self.station.disconnect(n)
            done.append(n)
        if done:
            self.station.bus.log(f"🛰 {', '.join(done)} 已被拉取到 {owner or '別台電腦'}，這台已中斷連線")
        self._status_now.set()
        return {"disconnected": done, "busy": busy}

    def cmd_scan(self, sender) -> List[Dict[str, Any]]:
        """掃描這台電腦上的 VISA 資源並以 *IDN? 辨識（唯讀）。結果回報到 Hub 的儀器登錄。"""
        from ..diagnostics import scan_resources

        items = record_scan(scan_resources(identify=True), self.station)
        self._status_now.set()
        return items

    def cmd_describe(self, sender, name: str) -> Dict[str, Any]:
        if name not in self.station.instruments:
            raise CommandError(f"這個節點沒有 {name}")
        return describe_instrument(self.station, name)

    def cmd_get_all(self, sender, name: str) -> Dict[str, Any]:
        """讀取一台儀器所有可讀參數（需已連線）。"""
        if name not in self.station.instruments:
            raise CommandError(f"這個節點沒有 {name}")
        if not self.station.instruments[name].connected:
            raise CommandError(f"{name} 尚未連線")
        d = describe_instrument(self.station, name)
        refs = [p["ref"] for g in d["groups"] for p in g["params"] if p["gettable"]]
        return self.cmd_get(sender, refs)

    # ---- 即時監控（別台電腦控制這台的電源群組 / VNA）------------------------------
    def _backend(self):
        from .backend import LocalBackend

        return LocalBackend(self.station)

    def _check_set(self) -> None:
        if not setting("remote.node.allow_remote_set", True):
            raise CommandError("這個節點不允許遠端設定儀器（settings.yaml remote.node.allow_remote_set）")

    def cmd_sources(self, sender) -> Dict[str, Any]:
        return self._backend().sources()

    def cmd_group_ramp(self, sender, refs: List[str], target: float, rate: Optional[float] = None) -> str:
        self._check_set()
        self.station.ensure_connected(list(refs))
        jid = self._backend().group_ramp(list(refs), float(target), rate)
        self.station.bus.log(f"🛰 {sender.get('user')} 群組斜坡 {', '.join(refs)} → {target}")
        return jid

    def cmd_group_stop(self, sender, job: Optional[str] = None) -> Dict[str, Any]:
        self._backend().group_stop(job)
        return {}

    def cmd_ramp_status(self, sender, job: str) -> Dict[str, Any]:
        return self._backend().ramp_status(job)

    def cmd_fine_step(self, sender, refs: List[str], direction: int, step: Optional[float] = None) -> Dict[str, float]:
        self._check_set()
        self.station.ensure_connected(list(refs))
        return self._backend().fine_step(list(refs), int(direction), step)

    def cmd_output(self, sender, refs: List[str], on: bool) -> Dict[str, Any]:
        self._check_set()
        self.station.ensure_connected(list(refs))
        res = self._backend().output(list(refs), bool(on))
        self.station.bus.log(f"🛰 {sender.get('user')} 輸出 {'ON' if on else 'OFF'}：{', '.join(refs)}")
        self._status_now.set()
        return {k: bool(v) for k, v in res.items()}

    def cmd_vnas(self, sender) -> List[Dict[str, Any]]:
        return self._backend().vnas()

    def cmd_vna_catalog(self, sender, name: str) -> Dict[str, List[str]]:
        return self._backend().vna_catalog(name)

    def cmd_vna_ports(self, sender, name: str) -> Dict[str, Any]:
        if self._active_runner() is not None:
            raise CommandError("量測進行中，稍後再偵測 port")
        return self._backend().vna_ports(name)

    def cmd_vna_trace(self, sender, name: str, trace: str, create: bool = False) -> Dict[str, Any]:
        if self._active_runner() is not None:
            raise CommandError("量測進行中，即時監控暫停讀取 VNA")
        x, z, unit = self._backend().vna_trace(name, trace, bool(create))
        return {"x": enc_array(x), "z": enc_array(z), "unit": unit}

    def cmd_vna_traces(self, sender, name: str, traces: List[str], create: bool = False) -> Dict[str, Any]:
        if self._active_runner() is not None:
            raise CommandError("量測進行中，即時監控暫停讀取 VNA")
        x, zs, unit = self._backend().vna_traces(name, list(traces or []), bool(create))
        return {"x": enc_array(x), "z": {k: enc_array(v) for k, v in zs.items()}, "unit": unit}

    def cmd_vna_scan(self, sender, name: str) -> Dict[str, Any]:
        if self._active_runner() is not None:
            raise CommandError("量測進行中，稍後再掃描 port")
        return self._backend().vna_scan(name)

    def cmd_vna_set_visible(self, sender, name: str, params: Optional[List[str]] = None) -> Dict[str, Any]:
        return self._backend().vna_set_visible(name, params)

    def cmd_add_instrument(self, sender, name: str, options: Dict[str, Any], save: bool = True) -> Dict[str, Any]:
        """把儀器設定加到這台（儀器伺服器把儀器移到這個節點、這台還沒有設定時）。"""
        from ..core import labfile

        self._check_set()
        opts = dict(options or {})
        if name in self.station.instruments:
            cur = self.station.instruments[name].options
            if instrument_key(str(cur.get("address") or ""), cur) != instrument_key(str(opts.get("address") or ""), opts):
                raise CommandError(f"這個節點已經有另一台叫 {name} 的儀器（位址不同）；請先改名")
            return {"added": False, "name": name}
        driver = opts.pop("driver")
        self.station.add(name, driver, **dict(opts))
        getattr(self.station, "foreign", {}).pop(name, None)
        if save:
            path = self.station.config_path or lab_path(setting("app.instruments_file", "instruments.yaml"))
            if any(n == name for n, _o in labfile.configured(path)):
                # 已經在設定檔裡（node: 指定給別台而沒載入）→ 改成指定給這台
                labfile.update_instrument(path, name, {"node": opts.get("node") or self.name})
            else:
                labfile.add_instrument(path, name, {"driver": driver, **opts})
        self.station.bus.log(f"🛰 {sender.get('user')} 新增儀器 {name}（{driver}）")
        self._status_now.set()
        return {"added": True, "name": name}

    # ---- 1.0.7：Hub 網頁「量測設置」----------------------------------------------
    def cmd_catalog(self, sender) -> Dict[str, Any]:
        """這台可以設定 / 掃描的目標與量測儀器（網頁的方案編輯器用）。"""
        from ..scheme import build_catalog

        cat = build_catalog(self.station)
        return {
            "targets": [{"ref": t.ref, "label": t.label, "short": t.short, "group": t.group, "unit": t.unit,
                         "common": t.common, "instrument": t.instrument} for t in cat.targets],
            "measurers": [{"ref": m.ref, "label": m.label, "kind": m.kind, "traces": list(m.traces),
                           "fields": [{"key": f.key, "label": f.label, "unit": f.unit, "kind": f.kind,
                                       "choices": list(f.choices or [])} for f in m.fields],
                           "defaults": {k: (v if isinstance(v, (int, float, str, bool)) or v is None else str(v))
                                        for k, v in (m.defaults or {}).items()}} for m in cat.measurers],
        }

    @staticmethod
    def _outline(sch) -> List[Dict[str, Any]]:
        sch.ensure_graph()
        sch.sync_from_graph()
        return [{"id": b.id, "kind": b.kind, "depth": depth} for b, depth, _p in sch.walk()]

    def cmd_templates(self, sender) -> List[Dict[str, Any]]:
        """這台的量測範本（LAB/templates 與內建），連同流程順序（網頁照順序列出方塊）。"""
        from ..scheme.templates import load_template, template_files

        out = []
        for key in template_files():
            try:
                sch = load_template(key)
                outline = self._outline(sch)
                out.append({"key": key, "name": sch.name, "scheme": sch.to_dict(), "outline": outline})
            except Exception as e:  # noqa: BLE001 — 壞掉的範本不影響其他
                out.append({"key": key, "name": key, "error": str(e)})
        return out

    def cmd_check_scheme(self, sender, scheme: Dict[str, Any]) -> Dict[str, Any]:
        """在這台編譯方案（不執行）：問題、點數、預估時間、檔案數。"""
        from ..scheme import Scheme, build_catalog, compile_scheme

        sch = Scheme.from_dict(dict(scheme))
        res = compile_scheme(sch, build_catalog(self.station))
        return {"ok": res.ok, "issues": [{"level": i.level, "message": i.message, "block": i.block_id}
                                         for i in res.issues if i.level in ("error", "warning")],
                "points": res.total_points, "est_seconds": res.est_seconds, "files": res.n_files,
                "outline": self._outline(sch)}

    def _instruments_path(self) -> Path:
        return Path(self.station.config_path or lab_path(setting("app.instruments_file", "instruments.yaml")))

    def cmd_instruments_file(self, sender) -> Dict[str, Any]:
        """這台的 instruments.yaml（文字）與各量測電腦的電磁鐵組 / VNA 對應。"""
        import yaml

        from ..scheme.remap import role_lists

        path = self._instruments_path()
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        try:
            data = yaml.safe_load(text) or {}
            roles = role_lists((data.get("instruments") or {}) if isinstance(data, dict) else {},
                               setting("lab_nodes", None))
        except Exception:  # noqa: BLE001
            roles = {}
        return {"path": str(path), "text": text, "roles": roles, "node": self.name,
                "loaded": sorted(self.station.instruments), "lab_nodes": list(setting("lab_nodes", []) or [])}

    def cmd_save_lab_nodes(self, sender, lab_nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """站長從網頁改「量測電腦對應」（settings.yaml lab_nodes：每台的電磁鐵組與 VNA，依序）。"""
        from ..settings import settings

        if not sender.get("owner"):
            raise CommandError("只有站長可以從網頁修改量測電腦對應")
        if not setting("remote.node.allow_remote_config", True):
            raise CommandError("這個節點不允許遠端修改設定（settings.yaml remote.node.allow_remote_config）")
        clean = []
        for e in lab_nodes or []:
            if not isinstance(e, dict) or not str(e.get("node") or "").strip():
                continue
            clean.append({"node": str(e["node"]).strip(),
                          "magnet": [str(x) for x in (e.get("magnet") or []) if str(x).strip()],
                          "vna": [str(x) for x in (e.get("vna") or []) if str(x).strip()]})
        st = settings()
        st.set("lab_nodes", clean)
        st.save()
        self.station.bus.log(f"🛰 {sender.get('user')} 從網頁修改了量測電腦對應")
        return {"saved": True, "lab_nodes": clean}

    def cmd_save_instruments(self, sender, text: str) -> Dict[str, Any]:
        """站長從網頁改 instruments.yaml：檢查格式、備份成 .bak 再寫入；在這台重新載入設定後生效。"""
        import yaml

        if not sender.get("owner"):
            raise CommandError("只有站長可以從網頁修改量測電腦的設定檔")
        if not setting("remote.node.allow_remote_config", True):
            raise CommandError("這個節點不允許遠端修改設定檔（settings.yaml remote.node.allow_remote_config）")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise CommandError(f"YAML 格式錯誤：{e}")
        if not isinstance(data, dict) or not isinstance(data.get("instruments"), dict):
            raise CommandError("格式不對：最上層要有 instruments:")
        for name, opts in data["instruments"].items():
            if not isinstance(opts, dict) or not opts.get("driver"):
                raise CommandError(f"儀器 {name} 缺少 driver")
        path = self._instruments_path()
        if path.is_file():
            path.with_suffix(path.suffix + ".bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        self.station.bus.log(f"🛰 {sender.get('user')} 從網頁修改了 {path.name}（重新載入設定後生效）")
        return {"saved": True, "path": str(path), "instruments": sorted(data["instruments"])}

    def cmd_update(self, sender, version: str) -> Dict[str, Any]:
        if not setting("remote.node.allow_update", True):
            return {"accepted": False, "reason": "這個節點關閉了遠端更新（remote.node.allow_update）"}
        if not is_newer(version, __version__):
            return {"accepted": False, "reason": f"節點已是 {__version__}"}
        from .. import selfupdate

        if selfupdate.FROZEN:                     # 打包版：Hub 下載頁要有這個作業系統的新版安裝檔
            c = selfupdate.check()
            if c is None or c["plan"] not in ("code", "installer"):
                return {"accepted": False, "reason": f"Hub 上沒有這台電腦可以直接更新、比 {__version__} 新的 Lab Control"}
            version = c["target"]["version"]
        else:
            try:
                available = self.hub.releases()
            except HubError as e:
                return {"accepted": False, "reason": f"無法讀取 Hub 的發佈版本：{e}"}
            if str(version).lstrip("vV") not in available:
                return {"accepted": False, "reason": f"Hub 上找不到 {version} 的發佈版本（Hub 的 LABHUB_RELEASES）"}
        if self.update_pending and not is_newer(version, self.update_pending):
            return {"accepted": True, "already": True, "deferred": self._active_runner() is not None}
        self.update_pending = version
        self.update_error = ""
        busy = self._active_runner() is not None
        self.update_message = f"{sender.get('user')} 要求更新到 {version}" + ("（量測結束後更新）" if busy else "")
        self.station.bus.log("⬆ " + self.update_message)
        return {"accepted": True, "deferred": busy}

    # ---- 更新 ------------------------------------------------------------
    def _maybe_update(self) -> None:
        if not self.update_pending or self._restarting or self._active_runner() is not None:
            return
        version = self.update_pending
        from .updater import perform_update

        self._restarting = True
        self._safe_status()
        try:
            how = perform_update(version, self.hub, log=lambda m: self.station.bus.log(m))
        except Exception as e:  # noqa: BLE001
            self._restarting = False
            self.update_pending = None
            self.update_error = str(e)
            self.station.bus.log(f"❌ 更新到 {version} 失敗：{e}", "error")
            return
        self.update_message = f"已啟動 {version}（{how}），這個程式即將關閉"
        self.station.bus.log("⬆ " + self.update_message)
        self._safe_status()
        if self.on_restart is not None:
            self.on_restart()

    def _safe_status(self) -> None:
        try:
            self.hub.put_status(self.name, self.status())
        except HubError as e:
            self._note_error("回報 Hub 失敗", e)
