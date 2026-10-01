"""
擬合引擎：在「獨立子行程」中執行 curve_fit

為什麼不用 QThread？
    curve_fit 大部分時間在跑 Python 寫的公式函式，會一直持有 GIL，
    即使放在 QThread，GUI 執行緒仍搶不到 GIL → 介面卡頓。
    子行程有自己的直譯器與 GIL，GUI 完全不受影響，也能隨時「中止」。

設計：
    - 常駐一個子行程（啟動時預熱 numpy/scipy，之後每次擬合不用重新啟動）
    - 主行程以 QTimer 非阻塞輪詢結果佇列
    - 中止 = terminate 子行程後自動重建
"""
import queue
import traceback
import multiprocessing as mp

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


def _worker_main(in_q, out_q):
    """子行程入口（必須是模組層級函式，spawn 才能 pickle）"""
    try:
        from fitapp.core.formula import get_function, get_module
        from fitapp.core.fitting import run_fit
        from fitapp.core.batch import run_batch
        from fitapp.core.phase import run_global_fit
    except Exception:
        out_q.put(("fatal", None, traceback.format_exc()))
        return
    out_q.put(("ready", None, None))

    while True:
        job = in_q.get()
        if job is None:
            break
        jid = job["id"]
        try:
            func = get_function(job["formula_path"], job["func_name"])

            if job["kind"] == "single":
                def progress(nfev, cost, elapsed, _jid=jid):
                    out_q.put(("progress", _jid, (nfev, cost, elapsed)))

                res = run_fit(func, job["freq"], job["s"], job["p0"], job["lo"], job["hi"],
                              job["fixed"], job["opts"], progress=progress)
                out_q.put(("done", jid, res))
            elif job["kind"] == "global":
                def gprog(nfev, cost, elapsed, _jid=jid):
                    out_q.put(("gprogress", _jid, (nfev, cost, elapsed)))

                res = run_global_fit(func, job["slices"], job["names"], job["roles"], job["init"],
                                     job["lo"], job["hi"], link=job["link"], opts=job["opts"],
                                     progress=gprog)
                out_q.put(("gdone", jid, res))
            else:
                def emit(rec, _jid=jid):
                    out_q.put(("slice", _jid, rec))

                run_batch(func, get_module(job["formula_path"]), job["func_name"],
                          job["freq"], job["S_cols"], job["idxs"], job["axis_vals"],
                          job["names"], job["units"], job["p0"], job["lo"], job["hi"],
                          job["fixed"], job["opts"], job["cfg"], emit)
                out_q.put(("batch_done", jid, None))
        except Exception as e:
            out_q.put(("error", jid, f"{e}\n\n{traceback.format_exc()}"))


class FitEngine(QObject):
    ready = pyqtSignal()
    progress = pyqtSignal(int, float, float)   # nfev, cost, elapsed
    finished = pyqtSignal(dict)
    slice_done = pyqtSignal(dict)              # 連續擬合：每完成一片
    batch_finished = pyqtSignal(bool)          # 連續擬合結束（True=正常完成, False=中止）
    global_progress = pyqtSignal(int, float, float)
    global_finished = pyqtSignal(dict)         # 全域擬合完成
    failed = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)

    POLL_MS = 50

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ctx = mp.get_context("spawn")
        self._proc = None
        self._in_q = None
        self._out_q = None
        self._job_id = 0
        self._current = None
        self._kind = None
        self._is_ready = False
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._poll)

    # ------------------------------------------------------------------ 行程管理
    def start(self):
        if self._proc is not None and self._proc.is_alive():
            return
        self._in_q = self._ctx.Queue()
        self._out_q = self._ctx.Queue()
        self._proc = self._ctx.Process(target=_worker_main, args=(self._in_q, self._out_q),
                                       daemon=True, name="FitWorker")
        self._proc.start()
        self._is_ready = False
        self._timer.start()

    def shutdown(self):
        self._timer.stop()
        if self._proc is None:
            return
        try:
            if self._proc.is_alive():
                self._in_q.put(None)
                self._proc.join(1.0)
            if self._proc.is_alive():
                self._proc.terminate()
                self._proc.join(1.0)
        finally:
            self._proc = None

    @property
    def is_busy(self):
        return self._current is not None

    @property
    def is_ready(self):
        return self._is_ready

    # ------------------------------------------------------------------ 工作
    @property
    def kind(self):
        return self._kind

    def _submit(self, job):
        if self.is_busy:
            raise RuntimeError("已有擬合正在進行")
        if self._proc is None or not self._proc.is_alive():
            self.start()
        self._job_id += 1
        self._current = self._job_id
        self._kind = job["kind"]
        job["id"] = self._job_id
        self._in_q.put(job)
        self.busy_changed.emit(True)

    def submit(self, formula_path, func_name, freq, s, p0, lo, hi, fixed, opts):
        self._submit(dict(kind="single", formula_path=formula_path, func_name=func_name,
                          freq=freq, s=s, p0=p0, lo=lo, hi=hi, fixed=fixed, opts=opts))

    def submit_batch(self, formula_path, func_name, freq, S_cols, idxs, axis_vals,
                     names, units, p0, lo, hi, fixed, opts, cfg):
        self._submit(dict(kind="batch", formula_path=formula_path, func_name=func_name,
                          freq=freq, S_cols=S_cols, idxs=idxs, axis_vals=axis_vals,
                          names=names, units=units, p0=p0, lo=lo, hi=hi, fixed=fixed,
                          opts=opts, cfg=cfg))

    def submit_global(self, formula_path, func_name, slices, names, roles, init, lo, hi, link, opts):
        self._submit(dict(kind="global", formula_path=formula_path, func_name=func_name,
                          slices=slices, names=names, roles=roles, init=init, lo=lo, hi=hi,
                          link=link, opts=opts))

    def _end_job(self):
        self._current = None
        self._kind = None
        self.busy_changed.emit(False)

    def cancel(self):
        """強制中止：結束子行程並重建"""
        if not self.is_busy:
            return
        was_batch = self._kind == "batch"
        self._poll()  # 先收完已完成的切片
        self._timer.stop()
        if self._proc is not None:
            self._proc.terminate()
            self._proc.join(1.0)
            self._proc = None
        if self.is_busy:
            self._end_job()
            if was_batch:
                self.batch_finished.emit(False)
        self.start()

    # ------------------------------------------------------------------ 輪詢
    def _poll(self):
        if self._out_q is None:
            return
        latest_progress = None
        latest_g = None
        for _ in range(200):
            try:
                kind, jid, payload = self._out_q.get_nowait()
            except queue.Empty:
                break
            if kind == "ready":
                self._is_ready = True
                self.ready.emit()
            elif kind == "fatal":
                if self.is_busy:
                    self._end_job()
                self.failed.emit("擬合子行程啟動失敗：\n" + payload)
            elif jid != self._current:
                continue  # 已取消工作的殘留訊息
            elif kind == "progress":
                latest_progress = payload
            elif kind == "gprogress":
                latest_g = payload
            elif kind == "gdone":
                self._end_job()
                self.global_finished.emit(payload)
            elif kind == "slice":
                self.slice_done.emit(payload)
            elif kind == "done":
                self._end_job()
                self.finished.emit(payload)
            elif kind == "batch_done":
                self._end_job()
                self.batch_finished.emit(True)
            elif kind == "error":
                was_batch = self._kind == "batch"
                self._end_job()
                self.failed.emit(payload)
                if was_batch:
                    self.batch_finished.emit(False)
        if latest_progress is not None and self._current is not None:
            self.progress.emit(*latest_progress)
        if latest_g is not None and self._current is not None:
            self.global_progress.emit(*latest_g)

        if self._current is not None and (self._proc is None or not self._proc.is_alive()):
            was_batch = self._kind == "batch"
            self._end_job()
            if was_batch:
                self.batch_finished.emit(False)
            self.failed.emit("擬合子行程意外結束（可能是記憶體不足或公式造成崩潰），已重新啟動。")
            self.start()
