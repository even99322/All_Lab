"""
操作設定的保存與還原

- 每 1.5 秒比對一次目前狀態，有變動才寫入 JSON（原子寫入 + .bak 備份）
- 關閉視窗、未捕捉例外時會立即保存
- 啟動時自動還原：視窗大小、公式檔與函式、參數表（每個函式各自記憶）、
  擬合設定、數據檔與切片/遮罩、連續擬合設定與上次結果
"""
import os
import traceback

from PySide6.QtCore import QObject, QTimer, QByteArray, Qt

STATE_VERSION = 2


class SessionManager(QObject):
    INTERVAL_MS = 1500

    def __init__(self, ctrl, store):
        super().__init__(ctrl)
        self.c = ctrl
        self.store = store
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self.save)
        self._restoring = False

    @property
    def path(self):
        return self.store.path

    def start(self):
        self._timer.start()

    @staticmethod
    def _splitter_ratio(splitter, fallback=0.5):
        sizes = splitter.sizes()
        total = sum(sizes)
        return sizes[0] / total if len(sizes) == 2 and total else fallback

    @staticmethod
    def _restore_splitter_ratio(splitter, ratio, bounds=(0.15, 0.85)):
        try:
            ratio = float(ratio)
        except (TypeError, ValueError):
            return False
        if not 0.0 < ratio < 1.0:
            return False
        ratio = min(bounds[1], max(bounds[0], ratio))

        def apply():
            total = max(300, splitter.width() if splitter.orientation() == Qt.Orientation.Horizontal
                        else splitter.height())
            first = round(total * ratio)
            splitter.setSizes([first, max(1, total - first)])

        QTimer.singleShot(0, apply)
        return True

    # ------------------------------------------------------------------ 收集
    def collect(self):
        c, w = self.c, self.c.w
        c.remember_table()
        main_sizes = w.main_splitter.sizes()
        main_total = sum(main_sizes)
        main_splitter_ratio = (main_sizes[0] / main_total
                               if len(main_sizes) == 2 and main_total else 0.22)

        if c.data is not None:
            data = dict(path=c.data["path"], s_name=w.cmb_s.currentText(),
                        axis=w.cmb_axis.currentText(), scale=w.spin_scale.value(),
                        idx=w.spin_idx.value(), f1=w.spin_f1.value(), f2=w.spin_f2.value())
        else:
            data = dict(c.pending_data or {})

        return dict(
            version=STATE_VERSION,
            window=dict(geometry=bytes(w.saveGeometry().toHex()).decode(),
                        tab=w.tabs.currentIndex(),
                        main_splitter=bytes(w.main_splitter.saveState().toHex()).decode(),
                        main_splitter_ratio=main_splitter_ratio,
                        right_splitter=bytes(w.right_splitter.saveState().toHex()).decode(),
                        right_splitter_ratio=self._splitter_ratio(w.right_splitter, 0.74),
                        preview_splitter=w.data_plot.layout_state(),
                        fit_plot=w.fit_plot.state(),
                        phase_plot_splitters=w.phase_panel.plot.layout_state(),
                        phase_main_splitter=bytes(w.phase_panel.main_splitter.saveState().toHex()).decode(),
                        phase_main_splitter_ratio=self._splitter_ratio(w.phase_panel.main_splitter, 0.31),
                        phase_plot_splitter=bytes(w.phase_panel.plot_splitter.saveState().toHex()).decode(),
                        phase_plot_splitter_ratio=self._splitter_ratio(w.phase_panel.plot_splitter, 0.72),
                        phase_result_splitter=bytes(w.phase_panel.result_splitter.saveState().toHex()).decode(),
                        phase_result_splitter_ratio=self._splitter_ratio(w.phase_panel.result_splitter, 0.58)),
            last_dir=c.last_dir,
            data=data,
            zoom=w.chk_zoom.isChecked(),
            output=dict(dir=w.txt_outdir.text().strip(), auto_single=w.chk_auto_single.isChecked(),
                        auto_batch=w.chk_auto_batch.isChecked()),
            clean=dict(c.clean_cfg(), drag_exclude=w.chk_excl_drag.isChecked()),
            formula=dict(path=c.formula_path, func=w.cmb_func.currentText(),
                         model_id=getattr(c.module, "MODEL_ID", w.cmb_func.currentText()),
                         model_version=getattr(c.module, "MODEL_VERSION", "unversioned")),
            params=c.param_memory,
            options=c.fit_options(),
            batch=dict(c.batch.state(), restore_autosave=True),
            phase=c.phase.state(),
        )

    def save(self, force=False):
        if self._restoring:
            return
        try:
            self.store.save(self.collect(), force=force)
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------------------------ 還原
    def restore(self):
        st = self.store.load()
        if not st:
            return
        c, w = self.c, self.c.w
        self._restoring = True
        try:
            win = st.get("window", {})
            if win.get("geometry"):
                w.restoreGeometry(QByteArray.fromHex(win["geometry"].encode()))
            restored_main = self._restore_splitter_ratio(
                w.main_splitter, win.get("main_splitter_ratio"), (0.18, 0.65)
            )
            if not restored_main and win.get("main_splitter"):
                w.main_splitter.restoreState(QByteArray.fromHex(win["main_splitter"].encode()))
            for splitter, ratio_key, state_key, bounds in (
                    (w.right_splitter, "right_splitter_ratio", "right_splitter", (0.3, 0.9)),
                    (w.phase_panel.main_splitter, "phase_main_splitter_ratio", "phase_main_splitter", (0.2, 0.65)),
                    (w.phase_panel.plot_splitter, "phase_plot_splitter_ratio", "phase_plot_splitter", (0.35, 0.9)),
                    (w.phase_panel.result_splitter, "phase_result_splitter_ratio", "phase_result_splitter", (0.25, 0.8))):
                restored = self._restore_splitter_ratio(
                    splitter, win.get(ratio_key), bounds
                )
                if not restored and win.get(state_key):
                    splitter.restoreState(QByteArray.fromHex(win[state_key].encode()))
            w.data_plot.apply_layout_state(win.get("preview_splitter", {}))
            w.fit_plot.apply_state(win.get("fit_plot", {}))
            w.phase_panel.plot.apply_layout_state(win.get("phase_plot_splitters", {}))
            c.last_dir = st.get("last_dir", "")
            w.chk_zoom.setChecked(bool(st.get("zoom", True)))
            out = st.get("output") or {}
            w.txt_outdir.setText(out.get("dir", ""))
            w.chk_auto_single.setChecked(bool(out.get("auto_single", False)))
            w.chk_auto_batch.setChecked(bool(out.get("auto_batch", False)))
            c.apply_clean_cfg(st.get("clean") or {})
            c.apply_options(st.get("options", {}))
            c.param_memory = st.get("params", {}) or {}
            c.batch.apply_state(st.get("batch", {}))
            c.phase.apply_state(st.get("phase", {}))

            data = st.get("data", {})
            has_data = bool(data.get("path")) and os.path.exists(data["path"])
            if has_data:
                c.pending_data = data

            fml = st.get("formula", {})
            if fml.get("path") and os.path.exists(fml["path"]):
                c.formula_path = fml["path"]
                c.reload_formula(prefer_func=fml.get("func"))

            if has_data:
                c.load_data_path(data["path"])
            if "tab" in win:
                w.tabs.setCurrentIndex(int(win["tab"]))
            c.status(f"已還原上次的操作設定（{os.path.basename(self.store.path)}）")
        except Exception:
            traceback.print_exc()
        finally:
            self._restoring = False
