"""
操作設定的保存與還原

- 每 1.5 秒比對一次目前狀態，有變動才寫入 JSON（原子寫入 + .bak 備份）
- 關閉視窗、未捕捉例外時會立即保存
- 啟動時自動還原：視窗大小、公式檔與函式、參數表（每個函式各自記憶）、
  擬合設定、數據檔與切片/遮罩、連續擬合設定與上次結果
"""
import os
import traceback

from PyQt6.QtCore import QObject, QTimer, QByteArray

STATE_VERSION = 1


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

    # ------------------------------------------------------------------ 收集
    def collect(self):
        c, w = self.c, self.c.w
        c.remember_table()

        if c.data is not None:
            data = dict(path=c.data["path"], s_name=w.cmb_s.currentText(),
                        axis=w.cmb_axis.currentText(), scale=w.spin_scale.value(),
                        idx=w.spin_idx.value(), f1=w.spin_f1.value(), f2=w.spin_f2.value())
        else:
            data = dict(c.pending_data or {})

        return dict(
            version=STATE_VERSION,
            window=dict(geometry=bytes(w.saveGeometry().toHex()).decode(),
                        tab=w.tabs.currentIndex()),
            last_dir=c.last_dir,
            data=data,
            zoom=w.chk_zoom.isChecked(),
            output=dict(dir=w.txt_outdir.text().strip(), auto_single=w.chk_auto_single.isChecked(),
                        auto_batch=w.chk_auto_batch.isChecked()),
            clean=dict(c.clean_cfg(), drag_exclude=w.chk_excl_drag.isChecked()),
            formula=dict(path=c.formula_path, func=w.cmb_func.currentText()),
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
