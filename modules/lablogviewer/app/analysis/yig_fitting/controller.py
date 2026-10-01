"""控制器：連接 UI 事件、背景工作與核心邏輯（單次擬合 + 共用狀態）"""
import os
import json
import inspect
import traceback
import importlib.util

import numpy as np
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from .core import formula as fm
from .core.report import format_report, write_csv
from .core.batch import window_mask
from .core.cleaning import clean_mask, is_active, parse_ranges, format_ranges
from .core.settings import SessionStore
from .core.paths import default_output_dir, ensure_dir, session_path, sub_dir, safe_stem
from .core.library import FormulaLibrary, latex_for_spec
from .core import codegen as cg
from .workers.data_worker import DataLoadWorker
from .workers.fit_engine import FitEngine
from .batch_controller import BatchController
from .phase_controller import PhaseController
from .session import SessionManager


class FitController(QObject):
    def __init__(self, window, store=None, source_path=None):
        super().__init__(window)
        self.w = window
        self.source_path = os.path.realpath(source_path) if source_path else ""
        self.scipy_available = importlib.util.find_spec("scipy") is not None
        self.data = None
        self.module = None
        self.funcs = {}
        self.formula_path = ""
        self.last_dir = ""
        self.fit_ctx = None
        self.fit_result = None
        self.loader = None
        self.pending_data = None       # 還原設定時，等數據載入完再套用
        self.param_memory = {}         # {"公式檔::函式": [row, ...]}
        self._table_key = None

        # 切片滑桿去抖動：拖動時最多每 30 ms 重畫一次
        self._slice_timer = QTimer(self)
        self._slice_timer.setSingleShot(True)
        self._slice_timer.setInterval(30)
        self._slice_timer.timeout.connect(self._apply_slice)

        # 雜訊設定變動去抖動
        self._clean_timer = QTimer(self)
        self._clean_timer.setSingleShot(True)
        self._clean_timer.setInterval(150)
        self._clean_timer.timeout.connect(self.on_clean_changed)

        self.engine = FitEngine(self)
        self.engine.ready.connect(self._on_engine_ready)
        self.engine.progress.connect(self.on_fit_progress)
        self.engine.finished.connect(self.on_fit_done)
        self.engine.failed.connect(self.on_fit_failed)
        self.engine.busy_changed.connect(self.on_busy_changed)
        if self.scipy_available:
            self.engine.start()
        else:
            self.w.lbl_engine.setText("SciPy unavailable - fit actions disabled")

        self.store = store or SessionStore(session_path(getattr(window, "source_identity", self.source_path)))
        self.library = FormulaLibrary(os.path.join(self.store.folder, "formula_library.json"))
        self._seed_bundled_models()
        self._library_dlg = None
        self.batch = BatchController(self)
        self.phase = PhaseController(self)
        self.session = SessionManager(self, self.store)

        self._connect()
        self.update_enabled()
        self.session.restore()
        if not self.formula_path:
            bundled = os.path.join(os.path.dirname(__file__), "models", "formulas_example.py")
            self.formula_path = bundled
            self.reload_formula(prefer_func="S11_single")
        self.session.start()

    # ================================================================ 連線
    def _connect(self):
        w = self.w
        w.closing.connect(self.shutdown)
        w.btn_open.clicked.connect(self.open_data)
        w.cmb_s.currentIndexChanged.connect(self.rebuild_map)
        w.cmb_axis.currentIndexChanged.connect(self.on_axis_changed)
        w.spin_scale.valueChanged.connect(self.rebuild_map)
        w.slider_idx.valueChanged.connect(w.spin_idx.setValue)
        w.spin_idx.valueChanged.connect(w.slider_idx.setValue)
        w.spin_idx.valueChanged.connect(lambda _: self._slice_timer.start())
        w.spin_f1.valueChanged.connect(self.on_range_changed)
        w.spin_f2.valueChanged.connect(self.on_range_changed)
        w.btn_full.clicked.connect(self.set_full_range)
        w.chk_zoom.toggled.connect(w.data_plot.set_zoom)
        w.data_plot.rangeSelected.connect(self.on_span_selected)
        w.data_plot.sliceClicked.connect(self.on_map_clicked)

        for x in (w.chk_spike, w.chk_excl_drag):
            x.toggled.connect(lambda _: self._clean_timer.start())
        for x in (w.spin_sp_half, w.spin_sp_k, w.spin_sp_dil):
            x.valueChanged.connect(lambda _: self._clean_timer.start())
        w.cmb_sp_comp.currentIndexChanged.connect(lambda _: self._clean_timer.start())
        w.txt_excl.textChanged.connect(lambda _: self._clean_timer.start())
        w.btn_excl_clear.clicked.connect(lambda: w.txt_excl.setText(""))

        w.btn_formula.clicked.connect(self.open_formula)
        w.btn_reload.clicked.connect(lambda: self.reload_formula())
        w.btn_builder.clicked.connect(self.open_builder)
        w.btn_library.clicked.connect(self.open_library)
        w.btn_add_lib.clicked.connect(self.add_current_to_library)
        w.btn_outdir.clicked.connect(self.browse_output_dir)
        w.btn_outdir_open.clicked.connect(self.open_output_dir)
        w.txt_outdir.textChanged.connect(lambda _: self.update_outdir_label())
        w.cmb_func.currentIndexChanged.connect(lambda _: self.on_func_changed())
        w.table.itemChanged.connect(lambda _: self.batch.on_window2_changed())

        w.btn_guess.clicked.connect(self.auto_guess)
        w.btn_preview.clicked.connect(self.preview_initial)
        w.btn_fit.clicked.connect(self.start_fit)
        w.btn_cancel.clicked.connect(self.engine.cancel)
        w.btn_apply.clicked.connect(self.apply_fit_to_p0)
        w.btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(w.txt_result.toPlainText()))
        w.btn_export.clicked.connect(self.export_result)
        w.btn_save_cfg.clicked.connect(self.save_config)
        w.btn_load_cfg.clicked.connect(self.load_config)

    def shutdown(self):
        self.phase.shutdown()
        self.session.save(force=True)
        self.batch.autosave(force=True)
        self.engine.shutdown()
        if self.loader is not None and self.loader.isRunning():
            self.loader.wait(3000)

    def update_enabled(self):
        w = self.w
        has_data = self.data is not None
        loading = self.loader is not None and self.loader.isRunning()
        busy = self.engine.is_busy
        is2d = has_data and self.data["n_steps"] > 1
        can_prepare = has_data and bool(self.funcs) and not busy
        can_fit = can_prepare and self.scipy_available

        w.btn_open.setEnabled(False)
        for x in (w.cmb_s, w.spin_f1, w.spin_f2, w.btn_full):
            x.setEnabled(has_data and not busy)
        for x in (w.cmb_axis, w.spin_scale):
            x.setEnabled(is2d and not busy)
        for x in (w.slider_idx, w.spin_idx):
            x.setEnabled(is2d)
        w.btn_formula.setEnabled(not busy)
        w.btn_reload.setEnabled(bool(self.formula_path) and not busy)
        w.cmb_func.setEnabled(not busy)
        w.table.setEnabled(not busy)
        for x in (w.btn_guess, w.btn_preview, w.btn_save_cfg, w.btn_load_cfg):
            x.setEnabled(can_prepare)
        w.btn_fit.setEnabled(can_fit)
        if not self.scipy_available:
            w.btn_fit.setToolTip("Install the declared SciPy dependency to run numerical fits.")
        w.btn_cancel.setEnabled(busy and self.engine.kind == "single")
        for x in (w.btn_apply, w.btn_export, w.btn_copy):
            x.setEnabled(self.fit_result is not None and not busy)
        w.progress.setVisible((busy and self.engine.kind == "single") or loading)
        self.batch.update_enabled(has_data, is2d, can_fit, busy)
        self.phase.update_enabled(has_data, is2d, can_fit, busy)

    def _on_engine_ready(self):
        if self.scipy_available:
            self.w.lbl_engine.setText("Fitting engine: ready")
        else:
            self.w.lbl_engine.setText("SciPy unavailable - fit actions disabled")

    def status(self, msg):
        self.w.statusBar().showMessage(msg)

    def formula_key(self, func_name=None):
        func_name = func_name or self.w.cmb_func.currentText()
        return f"{os.path.basename(self.formula_path)}::{func_name}"

    # ================================================================ 數據
    def open_data(self):
        self.status("Data is owned by the source Viewer. Select another Data there to open a new analysis window.")

    def load_data_path(self, path):
        if self.source_path and os.path.realpath(path) != self.source_path:
            self.pending_data = None
            self.status("Ignored saved fitting state for a different Data source.")
            return
        self.last_dir = os.path.dirname(path)
        self.pending_data = self.pending_data or {"path": path}

    def load_experiment(self, experiment):
        wk = DataLoadWorker(experiment, self)
        self.loader = wk
        wk.loaded.connect(lambda d, _wk=wk: self.on_data_loaded(d) if _wk is self.loader else None)
        wk.failed.connect(lambda m, _wk=wk: self.on_data_failed(m) if _wk is self.loader else None)
        self.loader.finished.connect(self.update_enabled)
        self.loader.start()
        self.w.lbl_file.setText(f"Preparing Viewer Data: {os.path.basename(self.source_path)}")
        self.status("Preparing complex traces from the source Viewer…")
        self.update_enabled()
        return wk

    def _seed_bundled_models(self):
        if self.library.entries:
            return
        model_dir = os.path.join(os.path.dirname(__file__), "models")
        for filename in ("formulas_example.py", "formula_yig_node.py", "formula_s21_coupled.py"):
            path = os.path.join(model_dir, filename)
            if not os.path.isfile(path):
                continue
            try:
                module, functions = fm.load_formula_module(path)
                for function_name, function in functions.items():
                    names = fm.param_names(function)
                    self.library.upsert(
                        path, function_name, title=function_name,
                        units=fm.get_units(module, function_name, names),
                        source="LabLogViewer bundled model",
                    )
            except Exception as error:
                self.status(f"Bundled fitting model unavailable ({filename}): {error}")

    def on_data_failed(self, msg):
        self.pending_data = None
        self.w.lbl_file.setText("Load failed")
        QMessageBox.critical(self.w, "Read Failed", msg)

    def on_data_loaded(self, d):
        w = self.w
        self.data = d
        self.fit_result = None
        w.lbl_file.setText(os.path.basename(d["path"]))
        pend = self.pending_data or {}
        self.pending_data = None

        widgets = (w.cmb_s, w.cmb_axis, w.spin_scale, w.spin_idx, w.slider_idx, w.spin_f1, w.spin_f2)
        for x in widgets:
            x.blockSignals(True)

        w.cmb_s.clear()
        keys = list(d["s_params"])
        w.cmb_s.addItems(keys)
        if pend.get("s_name") in keys:
            w.cmb_s.setCurrentText(pend["s_name"])
        elif any("S21" in key.upper() for key in keys):
            w.cmb_s.setCurrentText(next(key for key in keys if "S21" in key.upper()))
        elif keys:
            w.cmb_s.setCurrentText(keys[0])

        w.cmb_axis.clear()
        w.cmb_axis.addItem("(Index)")
        w.cmb_axis.addItems(list(d["step_channels"]))
        first = next(iter(d["step_channels"].values()), None)
        if pend.get("axis") and w.cmb_axis.findText(pend["axis"]) >= 0:
            w.cmb_axis.setCurrentText(pend["axis"])
        elif first is not None and np.ptp(first) != 0:
            w.cmb_axis.setCurrentIndex(1)
        if "scale" in pend:
            w.spin_scale.setValue(float(pend["scale"]))
        else:
            w.spin_scale.setValue(1000.0 if "current" in w.cmb_axis.currentText().lower() else 1.0)

        ns = d["n_steps"]
        idx = int(np.clip(pend.get("idx", ns // 2), 0, max(0, ns - 1)))
        for x in (w.spin_idx, w.slider_idx):
            x.setRange(0, max(0, ns - 1))
            x.setValue(idx)

        fr = d["frequency"] / 1e9
        for sp in (w.spin_f1, w.spin_f2):
            sp.setRange(fr.min(), fr.max())
            sp.setSingleStep(max((fr.max() - fr.min()) / 200, 1e-6))
        w.spin_f1.setValue(float(np.clip(pend.get("f1", fr.min()), fr.min(), fr.max())))
        w.spin_f2.setValue(float(np.clip(pend.get("f2", fr.max()), fr.min(), fr.max())))

        for x in widgets:
            x.blockSignals(False)

        w.data_plot.idx = idx
        w.data_plot.zoom = w.chk_zoom.isChecked()
        w.data_plot.set_range(w.spin_f1.value(), w.spin_f2.value())
        self.rebuild_map()
        self.on_clean_changed()
        self.update_outdir_label()
        if self.funcs:
            self.fill_missing_guess()
        self.batch.on_data_changed()
        self.phase.set_coarse_enabled(w.phase_panel.chk_coarse_enabled.isChecked())
        self.phase.restore_data_bound_results()
        self.update_enabled()
        self.status(f"Loaded {os.path.basename(d['path'])}: {fr[0]:.6f}–{fr[-1]:.6f} GHz, "
                    f"{len(fr)} points, {ns} traces")

    def axis_values(self):
        name = self.w.cmb_axis.currentText()
        if self.data and name in self.data["step_channels"]:
            return self.data["step_channels"][name] * self.w.spin_scale.value(), name
        return np.arange(self.data["n_steps"], dtype=float), "Index"

    def on_axis_changed(self):
        name = self.w.cmb_axis.currentText().lower()
        self.w.spin_scale.blockSignals(True)
        self.w.spin_scale.setValue(1000.0 if "current" in name else 1.0)
        self.w.spin_scale.blockSignals(False)
        self.rebuild_map()

    def rebuild_map(self, *_):
        if self.data is None or not self.w.cmb_s.currentText():
            return
        s_name = self.w.cmb_s.currentText()
        yv, yname = self.axis_values()
        self.w.data_plot.idx = self.w.spin_idx.value()
        self.w.data_plot.set_map(self.data["frequency"] / 1e9, yv, self.data["s_db"][s_name],
                                 yname, s_name)
        self.batch.refresh_track()
        self.refresh_clean_preview()

    def _apply_slice(self):
        self.w.data_plot.set_slice(self.w.spin_idx.value())
        self.refresh_clean_preview()

    # ---------------------------------------------------------------- 雜訊處理
    def clean_cfg(self):
        w = self.w
        return dict(spike=w.chk_spike.isChecked(), half=w.spin_sp_half.value(),
                    k=w.spin_sp_k.value(), dilate=w.spin_sp_dil.value(),
                    components=w.cmb_sp_comp.currentData(),
                    ranges=parse_ranges(w.txt_excl.text()))

    def apply_clean_cfg(self, cfg):
        w = self.w
        w.chk_spike.setChecked(bool(cfg.get("spike", False)))
        w.spin_sp_half.setValue(int(cfg.get("half", 5)))
        w.spin_sp_k.setValue(float(cfg.get("k", 5.0)))
        w.spin_sp_dil.setValue(int(cfg.get("dilate", 1)))
        i = w.cmb_sp_comp.findData(cfg.get("components", "abs"))
        w.cmb_sp_comp.setCurrentIndex(max(i, 0))
        w.txt_excl.setText(format_ranges([tuple(r) for r in cfg.get("ranges", [])]))
        w.chk_excl_drag.setChecked(bool(cfg.get("drag_exclude", False)))

    def current_keep(self):
        """目前切片（整條）要保留的點"""
        s = self.data["s_params"][self.w.cmb_s.currentText()][:, self.w.spin_idx.value()]
        cfg = self.clean_cfg()
        return clean_mask(self.data["frequency"], s, cfg if is_active(cfg) else None)

    def on_clean_changed(self):
        self.w.data_plot.set_exclude_ranges(self.clean_cfg()["ranges"])
        self.refresh_clean_preview()
        if self.data is not None:
            self.update_npts()

    def refresh_clean_preview(self):
        if self.data is None:
            return
        keep = self.current_keep()
        self.w.data_plot.set_removed(~keep if (~keep).any() else None)

    def on_map_clicked(self, y):
        yv, _ = self.axis_values()
        self.w.spin_idx.setValue(int(np.argmin(np.abs(yv - y))))

    def on_range_changed(self, *_):
        self.w.data_plot.set_range(self.w.spin_f1.value(), self.w.spin_f2.value())
        self.update_npts()
        self.batch.update_window_label()

    def window_meta(self):
        a, b = sorted((self.w.spin_f1.value() * 1e9, self.w.spin_f2.value() * 1e9))
        win2 = self.batch.window2()
        return dict(f1=a, f2=b, f1b=win2[0] if win2 else None, f2b=win2[1] if win2 else None)

    def refresh_window2(self):
        """第二視窗變動 → 更新預覽與點數"""
        win2 = self.batch.window2()
        self.w.data_plot.set_range2(None if win2 is None else (win2[0] / 1e9, win2[1] / 1e9))
        if self.data is not None:
            self.update_npts()

    def set_range(self, f1_ghz, f2_ghz):
        self.w.spin_f1.blockSignals(True)
        self.w.spin_f1.setValue(f1_ghz)
        self.w.spin_f1.blockSignals(False)
        self.w.spin_f2.setValue(f2_ghz)
        self.on_range_changed()

    def on_span_selected(self, a, b):
        if self.w.chk_excl_drag.isChecked():   # 拖曳 → 新增排除頻段
            ranges = self.clean_cfg()["ranges"] + [(a, b)]
            self.w.txt_excl.setText(format_ranges(ranges))
            self.status(f"Added excluded range {a:.6f}–{b:.6f} GHz")
            return
        self.set_range(a, b)

    def set_full_range(self):
        fr = self.data["frequency"] / 1e9
        self.set_range(fr.min(), fr.max())

    def update_npts(self):
        f, _, nrem = self.masked_trace(with_removed=True)
        txt = f"{len(f)} points" + (" (includes window 2)" if self.batch.window2() else "")
        self.w.lbl_npts.setText(txt)
        self.w.lbl_clean.setText(f"{nrem} points excluded in this slice's fit range" if is_active(self.clean_cfg())
                                 else "Inactive")

    def masked_trace(self, with_removed=False, return_removed_points=False):
        """
        回傳擬合用的 (f, s)：遮罩（含視窗 2）∩ 去除雜訊
        with_removed           → 另外回傳被去除的點數
        return_removed_points  → 另外回傳被去除的 (f, s)
        """
        f = self.data["frequency"]
        s = self.data["s_params"][self.w.cmb_s.currentText()][:, self.w.spin_idx.value()]
        a, b = sorted((self.w.spin_f1.value() * 1e9, self.w.spin_f2.value() * 1e9))
        win2 = self.batch.window2()   # 啟用追蹤參數 2 時取兩視窗聯集
        m = window_mask(f, a, b, *(win2 if win2 else (None, None)))
        keep = self.current_keep()
        rem = m & ~keep
        m = m & keep
        out = (f[m].copy(), s[m].copy())
        if with_removed:
            out += (int(rem.sum()),)
        if return_removed_points:
            out += ((f[rem].copy(), s[rem].copy()),)
        return out

    # ================================================================ 公式
    def open_formula(self):
        path, _ = QFileDialog.getOpenFileName(self.w, "Select Fit Model .py",
                                              os.path.dirname(self.formula_path), "Python (*.py)")
        if path:
            self.remember_table()
            self.formula_path = path
            self.reload_formula()

    def reload_formula(self, prefer_func=None):
        if not self.formula_path:
            return False
        if not os.path.isfile(self.formula_path):
            # e.g. a session saved by an older version folder that was deleted:
            # use the bundled model of the same name when there is one.
            bundled = os.path.join(os.path.dirname(__file__), "models", os.path.basename(self.formula_path))
            if os.path.isfile(bundled):
                self.status(f"Model file not found ({os.path.basename(self.formula_path)}); "
                            "using the bundled model of the same name.")
                self.formula_path = bundled
            else:
                QMessageBox.warning(self.w, "Model Not Found",
                                    f"The model file no longer exists:\n{self.formula_path}\n\n"
                                    "Choose the model again (Load Model .py or Model Library).")
                return False
        prev = prefer_func or self.w.cmb_func.currentText()
        try:
            self.module, self.funcs = fm.load_formula_module(self.formula_path)
        except Exception as e:
            from .core.trust import UntrustedFormulaError

            detail = str(e) if isinstance(e, UntrustedFormulaError) else f"{e}\n\n{traceback.format_exc()}"
            QMessageBox.critical(self.w, "Model Load Failed", detail)
            return False
        if not self.funcs:
            QMessageBox.warning(self.w, "No Functions", "No usable model functions were found (expected at least two parameters: w, p1, ...).")
        self.w.lbl_formula.setText(os.path.basename(self.formula_path))
        cmb = self.w.cmb_func
        cmb.blockSignals(True)
        cmb.clear()
        cmb.addItems(list(self.funcs))
        if prev in self.funcs:
            cmb.setCurrentText(prev)
        cmb.blockSignals(False)
        self.on_func_changed()
        self.update_enabled()
        self.status(f"Loaded model: {len(self.funcs)} functions")
        return True

    def open_builder(self):
        from .ui.formula_builder import FormulaBuilderDialog
        dlg = getattr(self, "_builder", None)
        if dlg is None:
            start = os.path.dirname(self.formula_path) or self.last_dir
            dlg = FormulaBuilderDialog(self.w, trace_provider=self._builder_trace, start_dir=start)
            dlg.saved.connect(self.on_builder_saved)
            self._builder = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _builder_trace(self):
        if self.data is None:
            return None
        f, s = self.masked_trace()
        return (f, s) if len(f) > 3 else None

    def on_builder_saved(self, path, func_name, load):
        info = self._builder.library_info()
        loaded_path = path
        if info["enabled"]:
            spec = cg.load_spec(path) or {}
            entry = None
            names = [func_name] + ([func_name + "_abs"] if spec.get("make_abs", True) else [])
            for fn in names:
                title = info["title"] or func_name
                if fn != func_name:
                    title += " (|S| version)"
                try:
                    e = self.add_to_library(path, fn, title=title, notes=info["notes"],
                                            latex=latex_for_spec(spec, fn), source="builder")
                    entry = entry or e
                except Exception as ex:
                    QMessageBox.warning(self.w, "Add to Model Library Failed", str(ex))
            if entry is not None:
                loaded_path = self.library.resolve(entry)
        if load:
            self.load_formula_file(loaded_path, func_name)

    def load_formula_file(self, path, func_name):
        self.remember_table()
        self.formula_path = path
        self.reload_formula(prefer_func=func_name)

    # ---------------------------------------------------------------- 公式庫
    def add_to_library(self, path, func, title=None, notes="", latex=None, source="file"):
        mod, funcs = fm.load_formula_module(path)
        if func not in funcs:
            raise KeyError(f"檔案中沒有函式 {func}")
        units = fm.get_units(mod, func, fm.param_names(funcs[func]))
        if latex is None:
            from .ui.library_dialog import auto_latex
            latex, _ = auto_latex(path, func)
        e = self.library.upsert(path, func, title=title, latex=latex, notes=notes,
                                units=units, source=source)
        self.on_library_changed()
        return e

    def open_library(self):
        from .ui.library_dialog import FormulaLibraryDialog
        if self._library_dlg is None:
            dlg = FormulaLibraryDialog(self.library, self.w, current_provider=self._current_formula)
            dlg.load_requested.connect(self.load_formula_file)
            dlg.library_changed.connect(self.on_library_changed)
            dlg.open_in_builder.connect(self._open_spec_in_builder)
            self._library_dlg = dlg
        dlg = self._library_dlg
        dlg.lib.load()
        dlg.refresh_list()
        if self.formula_path and self.current_func() is not None:
            dlg.select_entry(self.formula_path, self.w.cmb_func.currentText())
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _current_formula(self):
        if self.formula_path and self.current_func() is not None:
            return self.formula_path, self.w.cmb_func.currentText()
        return None

    def _open_spec_in_builder(self, spec):
        self.open_builder()
        self._builder.set_spec(spec)

    def add_current_to_library(self):
        cur = self._current_formula()
        if not cur:
            return
        e = self.library.find(*cur)
        if e is None:
            try:
                self.add_to_library(*cur)
            except Exception as ex:
                QMessageBox.critical(self.w, "Add to Model Library Failed", str(ex))
                return
            self.status(f"Added {cur[1]} to the Model Library; its equation and notes can be edited there.")
        self.open_library()

    def on_library_changed(self):
        self.refresh_formula_card()
        if self._library_dlg is not None and self._library_dlg.isVisible():
            self._library_dlg.refresh_list()

    def refresh_formula_card(self):
        """主視窗公式區：若目前函式在公式庫中，顯示公式圖與備註"""
        w = self.w
        cur = self._current_formula()
        e = self.library.find(*cur) if cur else None
        if e is None:
            w.img_formula.setVisible(False)
            w.lbl_lib_notes.setVisible(False)
            w.btn_add_lib.setText("Add to Library")
            w.btn_add_lib.setEnabled(cur is not None)
            return
        w.img_formula.setVisible(bool(e.get("latex")))
        w.img_formula.set_latex(e.get("latex", ""))
        notes = e.get("notes", "").strip()
        w.lbl_lib_notes.setText(f"【{e.get('title', '')}】" + (f" {notes}" if notes else ""))
        w.lbl_lib_notes.setVisible(True)
        w.btn_add_lib.setText("Edit in Library...")
        w.btn_add_lib.setEnabled(True)

    # ---------------------------------------------------------------- 輸出資料夾
    def output_dir(self, create=True):
        d = self.w.txt_outdir.text().strip()
        if not d:
            d = default_output_dir(self.data["path"] if self.data else None)
        return ensure_dir(d) if create else d

    def update_outdir_label(self):
        self.w.lbl_outdir.setText("Output folder: " + self.output_dir(create=False))

    def browse_output_dir(self):
        d = QFileDialog.getExistingDirectory(self.w, "Select Fit Results Folder", self.output_dir(create=False))
        if d:
            self.w.txt_outdir.setText(d)

    def open_output_dir(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.output_dir()))

    def current_func(self):
        return self.funcs.get(self.w.cmb_func.currentText())

    def current_units(self):
        return dict(zip(self.w.table.names(), self.w.table.units()))

    def remember_table(self):
        """把目前參數表存進記憶（換函式 / 換公式檔前呼叫）"""
        if self._table_key and self.w.table.rowCount():
            self.param_memory[self._table_key] = self.w.table.rows()

    def on_func_changed(self):
        self.remember_table()
        fn = self.current_func()
        table = self.w.table
        if fn is None:
            table.setRowCount(0)
            self.w.lbl_sig.setText("")
            self._table_key = None
            return
        names = fm.param_names(fn)
        units = fm.get_units(self.module, fn.__name__, names)
        self.w.lbl_sig.setText(f"{fn.__name__}{inspect.signature(fn)}")
        table.blockSignals(True)
        table.set_params(names, units)
        self._table_key = self.formula_key(fn.__name__)
        mem = {r["name"]: r for r in self.param_memory.get(self._table_key, [])}
        if self.data is not None:
            self.auto_guess(quiet=True)
        if mem:
            table.set_rows({k: {kk: vv for kk, vv in v.items() if vv != ""} for k, v in mem.items()})
        table.blockSignals(False)
        self.fit_result = None
        self.batch.on_func_changed(names, table.units())
        self.phase.on_func_changed(names, table.units())
        self.refresh_formula_card()
        self.update_enabled()

    def fill_missing_guess(self):
        """數據載入後補上空白的初值（保留已有的值）"""
        rows = self.w.table.rows()
        if all(r["p0"].strip() for r in rows):
            return
        self.auto_guess(quiet=True)
        self.w.table.set_rows({r["name"]: {k: v for k, v in r.items() if v != ""}
                               for r in rows if r["p0"].strip()})

    def auto_guess(self, quiet=False):
        fn = self.current_func()
        if self.data is None or fn is None:
            return
        f, s = self.masked_trace()
        if len(f) < 3:
            if not quiet:
                QMessageBox.warning(self.w, "Fit Range Too Small", "Too few frequency points are inside the selected range.")
            return
        guess, src, warn = fm.guess_params(self.module, fn.__name__, fm.param_names(fn), f, s)
        if warn and not quiet:
            QMessageBox.warning(self.w, "Initial-Guess Function Warning", warn)
        self.w.table.set_guess(guess)
        self.status(f"Initial values updated (source: {src})")

    # ================================================================ 單次擬合
    def fit_options(self):
        w = self.w
        return dict(weight=w.chk_weight.isChecked(), eps=w.spin_eps.value(),
                    method=w.cmb_method.currentText(), loss=w.cmb_loss.currentText(),
                    maxfev=w.spin_maxfev.value(), tol=float(w.cmb_tol.currentText()))

    def apply_options(self, o):
        w = self.w
        w.chk_weight.setChecked(bool(o.get("weight", True)))
        w.spin_eps.setValue(float(o.get("eps", 0.05)))
        w.cmb_method.setCurrentText(o.get("method", "trf"))
        w.cmb_loss.setCurrentText(o.get("loss", "linear"))
        w.spin_maxfev.setValue(int(o.get("maxfev", 300000)))
        tol = o.get("tol")
        if tol is not None:
            i = w.cmb_tol.findText(f"{float(tol):.0e}".replace("e-0", "e-"))
            if i >= 0:
                w.cmb_tol.setCurrentIndex(i)

    def preview_initial(self):
        try:
            _, _, p0, *_ = self.w.table.values()
            f, s, removed = self.masked_trace(return_removed_points=True)
            c, mag = fm.model_to_complex(self.current_func()(f, *p0), len(f))
            self.w.fit_plot.plot(f, s, c, mag, label="Initial guess", title="Initial-Guess Preview",
                                 removed=removed)
            self.w.tabs.setCurrentIndex(1)
        except Exception as e:
            QMessageBox.critical(self.w, "Error", str(e))

    def start_fit(self):
        try:
            names, units, p0, lo, hi, fixed = self.w.table.values()
            f, s, removed = self.masked_trace(return_removed_points=True)
            if len(f) <= int((~fixed).sum()):
                raise ValueError("The number of data points is smaller than the number of free parameters.")
        except Exception as e:
            QMessageBox.critical(self.w, "Cannot Start Fit", str(e))
            return
        yv, yname = self.axis_values()
        idx = self.w.spin_idx.value()
        func_name = self.w.cmb_func.currentText()
        model_id = getattr(self.module, "MODEL_ID", func_name)
        model_version = getattr(self.module, "MODEL_VERSION", "unversioned")
        self.fit_ctx = dict(
            names=names, units=units, f=f, s=s, removed=removed,
            meta=dict(func=func_name, formula_path=self.formula_path,
                      model_id=model_id, model_version=model_version,
                      file=os.path.basename(self.data["path"]), file_path=self.data["path"],
                      s_name=self.w.cmb_s.currentText(), idx=idx, axis=yname,
                      axis_val=float(yv[idx]), npts=len(f), nclean=len(removed[0]),
                      **self.window_meta()),
        )
        self.w.table.clear_fit()
        self.engine.submit(self.formula_path, func_name, f, s, p0, lo, hi, fixed, self.fit_options())
        self.status("Fitting in a worker process; the Analysis window remains responsive...")

    def on_busy_changed(self, busy):
        self.w.btn_fit.setText("Fitting..." if busy and self.engine.kind == "single" else "Start Fit")
        if not busy:
            self.w.lbl_fit_progress.setText("")
        self.update_enabled()

    def on_fit_progress(self, nfev, cost, elapsed):
        self.w.lbl_fit_progress.setText(f"nfev = {nfev}   cost = {cost:.4g}   {elapsed:.1f} s")

    def on_fit_failed(self, msg):
        self.status("Fit failed")
        QMessageBox.critical(self.w, "Fit Failed", msg)

    def on_fit_done(self, res):
        ctx = self.fit_ctx
        if self.source_path:
            from app._guard import gate

            gate.note_operation(self.source_path)      # a fit counts as a data operation
        self.fit_result = res
        self.w.table.set_fit(res["params"], res["errors"])
        self.w.txt_result.setPlainText(format_report(ctx["meta"], ctx["names"], ctx["units"], res))
        self.w.fit_plot.plot(ctx["f"], ctx["s"], res["model_complex"], res["model_mag"],
                             label="Fitted", title=f"{ctx['meta']['func']}   R² = {res['r2']:.6f}",
                             removed=ctx.get("removed"))
        self.w.tabs.setCurrentIndex(1)
        self.update_enabled()
        self.status(f"Fit complete: R² = {res['r2']:.6f}, nfev = {res['nfev']}, {res['elapsed']:.2f} s")
        if self.w.chk_auto_single.isChecked():
            try:
                self.write_single_result(self.single_result_path(stamp=True))
            except Exception as ex:
                QMessageBox.warning(self.w, "Automatic Save Failed", str(ex))

    def apply_fit_to_p0(self):
        if self.fit_result:
            self.w.table.set_p0(self.fit_result["params"])
            self.status("Fitted values copied to initial values")

    # ================================================================ 存檔
    def export_result(self):
        if not self.fit_result:
            return
        path, _ = QFileDialog.getSaveFileName(
            self.w, "Export Fit Result", self.single_result_path(), "CSV (*.csv)")
        if path:
            self.write_single_result(path)

    def single_result_path(self, stamp=False):
        meta = self.fit_ctx["meta"]
        name = f"{safe_stem(meta['file'])}_{meta['func']}_slice{meta['idx']}"
        if stamp:
            import time
            name += time.strftime("_%Y%m%d-%H%M%S")
        return os.path.join(self.output_dir(), name + ".csv")

    def write_single_result(self, path):
        meta = self.fit_ctx["meta"]
        ensure_dir(os.path.dirname(os.path.abspath(path)))
        write_csv(path, meta, self.fit_ctx["names"], self.fit_ctx["units"], self.fit_result)
        png = os.path.splitext(path)[0] + ".png"
        if not self.w.fit_plot.save_all_to(png):
            raise OSError("Failed to render the Fit Results plot.")
        self.status(f"Saved {path} and {os.path.basename(png)}")

    def save_config(self):
        path, _ = QFileDialog.getSaveFileName(
            self.w, "Save Fit Settings",
            os.path.join(sub_dir("params"), f"{self.w.cmb_func.currentText()}_params.json"), "JSON (*.json)")
        if not path:
            return
        cfg = {"function": self.w.cmb_func.currentText(),
               "params": self.w.table.rows(),
               "options": self.fit_options(),
               "freq_range_GHz": [self.w.spin_f1.value(), self.w.spin_f2.value()]}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        self.status(f"Saved {path}")

    def load_config(self):
        path, _ = QFileDialog.getOpenFileName(self.w, "Load Fit Settings", sub_dir("params"), "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig") as fh:
                cfg = json.load(fh)
            if cfg.get("function") in self.funcs and cfg["function"] != self.w.cmb_func.currentText():
                self.w.cmb_func.setCurrentText(cfg["function"])
            self.w.table.set_rows({p["name"]: p for p in cfg["params"]})
            self.apply_options(cfg.get("options", {}))
            if "freq_range_GHz" in cfg:
                self.set_range(*cfg["freq_range_GHz"])
            self.status(f"Loaded {path}")
        except Exception as e:
            QMessageBox.critical(self.w, "Load Failed", str(e))
