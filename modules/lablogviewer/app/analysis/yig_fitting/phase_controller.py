"""
「相位 / 節點」分頁的邏輯

1. 由連續擬合結果估計相位直線 φ = φ_ref + 2πT(f_m − f_ref)
   （κ_eff、φ 或節點頻率三種來源），並在 2D 圖標出節點 φ = nP
2. 相位連結的連續擬合（硬/軟連結、略過節點附近切片）→ 交給 BatchController.start(link)
3. 全域擬合：多片同時擬合，參數分為 每片獨立 / 全片共用 / 固定 / 相位直線
"""
import os
import copy
import csv
import time

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QMessageBox, QFileDialog

from .core import formula as fm
from .core import phase as ph
from .core.batch import window_mask, default_moving, write_batch_csv
from .core.cleaning import clean_mask, parse_ranges
from .core.paths import safe_stem
from .ui.phase_panel import NONE
from app.core.node_antinode import NodeAntinodeParameters
from app.analysis.yig_mirror.public_api import (
    SIN_SQUARED_COUPLING,
    analyze_physical_positions,
    run_coarse_detector,
)

SLICE_NAMES = {"a", "amp", "amplitude", "phi_0", "phi0", "phase0"}


class _CoarseDetectorWorker(QThread):
    completed = Signal(object, object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, sweep, frequency, values, parameters, identity, parent=None):
        super().__init__(parent)
        self.sweep = sweep
        self.frequency = frequency
        self.values = values
        self.parameters = parameters
        self.identity = identity

    def run(self):
        try:
            result, points = run_coarse_detector(
                self.sweep, self.frequency, self.values, self.parameters,
                trace_identity=self.identity,
                cancel_check=self.isInterruptionRequested,
            )
            if self.isInterruptionRequested():
                self.cancelled.emit()
            else:
                self.completed.emit(result, points)
        except Exception as error:
            if self.isInterruptionRequested():
                self.cancelled.emit()
            else:
                self.failed.emit(str(error))


class PhaseController(QObject):
    def __init__(self, ctrl):
        super().__init__(ctrl)
        self.c = ctrl
        self.w = ctrl.w
        self.p = ctrl.w.phase_panel
        self.pending = None
        self.role_memory = {}          # {公式鍵: {參數: 角色}}
        self.line_info = None          # 最近一次估計的結果（顯示用）
        self.depth = None              # (f, d, nodes)
        self.gres = None               # 全域擬合結果
        self._gctx = None
        self._roles_key = None
        self.coarse_points = []
        self.physical_points = []
        self._coarse_worker = None
        self._pending_saved_points = None

        p, eng = self.p, ctrl.engine
        p.btn_estimate.clicked.connect(self.estimate)
        p.btn_detect.clicked.connect(self.detect_nodes)
        p.btn_from_nodes.clicked.connect(lambda: self.estimate(source="nodes"))
        p.btn_redraw.clicked.connect(self.redraw)
        p.btn_export_points.clicked.connect(self.export_location_points)
        p.btn_link_batch.clicked.connect(self.start_link_batch)
        p.btn_global.clicked.connect(self.start_global)
        p.btn_global_cancel.clicked.connect(eng.cancel)
        p.btn_default_roles.clicked.connect(self.reset_roles)
        p.btn_apply_shared.clicked.connect(self.apply_shared)
        p.btn_to_batch.clicked.connect(self.to_batch)
        p.btn_export.clicked.connect(self.export_global)
        p.chk_coarse_enabled.toggled.connect(self.set_coarse_enabled)
        p.btn_coarse_run.clicked.connect(self.run_coarse_detection)
        p.btn_coarse_cancel.clicked.connect(self.cancel_coarse_detection)
        p.chk_show_nodes.toggled.connect(lambda _: self.update_nodes())
        for sp in (p.spin_T, p.spin_phi_ref, p.spin_fref, p.spin_period, p.spin_guard):
            sp.valueChanged.connect(lambda _: self.update_nodes())
        p.cmb_phase.currentIndexChanged.connect(lambda _: self._sync_link_role())
        p.spin_stride.valueChanged.connect(lambda _: self.update_slice_label())
        p.chk_ok_only.toggled.connect(lambda _: self.update_slice_label())
        p.chk_fix_shared.toggled.connect(lambda _: self.update_fix_label())
        p.spin_kb.valueChanged.connect(lambda _: self.update_fix_label())
        self.w.tabs.currentChanged.connect(lambda _: self.update_fix_label())
        eng.global_progress.connect(self.on_global_progress)
        eng.global_finished.connect(self.on_global_done)
        eng.batch_finished.connect(lambda _: self.on_batch_updated())
        self.set_coarse_enabled(False)

    def shutdown(self):
        worker = self._coarse_worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            worker.wait(5000)

    def set_coarse_enabled(self, enabled):
        self.p.coarse_controls.setVisible(bool(enabled))
        self.p.lbl_coarse_status.setText(
            "Enabled. Results will be labeled as coarse candidates."
            if enabled else "Disabled. Coarse candidates are empirical and are not physical extrema."
        )
        self.p.btn_coarse_run.setEnabled(bool(enabled) and self.c.data is not None)

    def run_coarse_detection(self):
        if not self.p.chk_coarse_enabled.isChecked() or self.c.data is None:
            return
        if self._coarse_worker is not None and self._coarse_worker.isRunning():
            return
        experiment = getattr(self.w, "source_experiment", None)
        if experiment is None or len(experiment.step_axes) != 1:
            self.p.lbl_coarse_status.setText(
                "Coarse detection needs exactly one unambiguous active sweep axis."
            )
            return
        sweep_name = experiment.step_axes[0].channel.name
        if self.w.cmb_axis.currentText() != sweep_name:
            self.p.lbl_coarse_status.setText(
                f"Select the active sweep axis ({sweep_name}) before running the Coarse Detector."
            )
            return
        channel = self.w.cmb_s.currentText()
        values = self.c.data["s_params"].get(channel)
        if values is None or values.ndim != 2:
            self.p.lbl_coarse_status.setText("The selected complex sweep is not available.")
            return
        sweep, _name = self.c.axis_values()
        if values.shape != (self.c.data["frequency"].size, sweep.size):
            self.p.lbl_coarse_status.setText("Complex trace and sweep coordinates do not match.")
            return
        threshold = (
            self.p.spin_coarse_threshold.value()
            if self.p.chk_coarse_manual_threshold.isChecked() else None
        )
        params = NodeAntinodeParameters(
            window_half_width=self.p.spin_coarse_window.value() * 1e9,
            smoothing_points=self.p.spin_coarse_smoothing.value(),
            minimum_distance=self.p.spin_coarse_distance.value(),
            prominence=self.p.spin_coarse_prominence.value(),
            dip_depth_threshold=threshold,
            detection_mode=self.p.cmb_coarse_mode.currentText(),
        )
        self.p.btn_coarse_run.setEnabled(False)
        self.p.btn_coarse_cancel.setEnabled(True)
        self.p.lbl_coarse_status.setText("Running empirical coarse detection…")
        worker = _CoarseDetectorWorker(
            sweep.copy(),
            np.asarray(self.c.data["frequency"], dtype=float),
            values.T,
            params,
            self.c.data.get("identity"),
            self,
        )
        self._coarse_worker = worker
        worker.completed.connect(self.on_coarse_done)
        worker.failed.connect(self.on_coarse_failed)
        worker.cancelled.connect(self.on_coarse_cancelled)
        worker.finished.connect(self.on_coarse_finished)
        worker.start()

    def cancel_coarse_detection(self):
        worker = self._coarse_worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            self.p.btn_coarse_cancel.setEnabled(False)
            self.p.lbl_coarse_status.setText("Cancelling coarse detection…")

    def on_coarse_done(self, _result, points):
        self.coarse_points = list(points)
        self.update_nodes()
        self.p.lbl_coarse_status.setText(
            f"{len(self.coarse_points)} coarse candidates detected. These are empirical, not physical."
        )

    def on_coarse_failed(self, message):
        self.p.lbl_coarse_status.setText(f"Coarse detection failed: {message}")

    def on_coarse_cancelled(self):
        self.p.lbl_coarse_status.setText("Coarse detection cancelled; no partial result applied.")

    def on_coarse_finished(self):
        self.p.btn_coarse_cancel.setEnabled(False)
        self.p.btn_coarse_run.setEnabled(self.p.chk_coarse_enabled.isChecked())
        self._coarse_worker = None

    # ================================================================ 小工具
    @property
    def period(self):
        return self.p.spin_period.value() * np.pi

    def line(self):
        p = self.p
        kb = p.spin_kb.value()
        return dict(T_ns=p.spin_T.value(), phi_ref=p.spin_phi_ref.value(),
                    f_ref=p.spin_fref.value() * 1e9, kappa_b=kb if kb > 0 else None)

    def _combo_name(self, cmb):
        t = cmb.currentText()
        return None if not t or t == NONE else t

    def names_units(self):
        return self.w.table.names(), self.w.table.units()

    def _meta_ok(self, need_data=False):
        b = self.c.batch
        if not b.records or b.meta is None:
            raise ValueError("No Continuous Fit results are available. Run Continuous Fit or load a CSV first.")
        names, _ = self.names_units()
        if b.meta["names"] != names or b.meta["func"] != self.w.cmb_func.currentText():
            raise ValueError("The Continuous Fit model or parameters do not match the currently selected model.")
        if need_data:
            d = self.c.data
            if d is None or b.meta["s_name"] not in d["s_params"]:
                raise ValueError("Load the Data file associated with the Continuous Fit results.")
        return b.meta, b.records

    def _freq_info(self, names, units):
        fname = self._combo_name(self.p.cmb_freq)
        if not fname:
            raise ValueError("Select the resonance-frequency parameter.")
        fac = fm.freq_factor(units[names.index(fname)])
        if fac is None:
            raise ValueError(f"{fname} does not use a frequency unit.")
        return names.index(fname), fac

    # ================================================================ 函式 / 狀態
    def on_func_changed(self, names, units):
        p = self.p
        pend = self.pending or {}
        prev = {k: pend.get(k) or self._combo_name(getattr(p, "cmb_" + k))
                for k in ("phase", "freq", "kappa")}
        freq_names = [n for n, u in zip(names, units) if fm.freq_factor(u) is not None]
        guess_phase = next((n for n, u in zip(names, units)
                            if n.lower() in ("phi", "theta", "phi_m", "theta_m")), None)
        guess_kappa = next((n for n in names if n.lower() in ("kappa_b", "kappa", "kappa_m", "k_b")), None)
        track = self.w.cmb_b_track.currentText()
        for cmb, items, pv, dflt in (
                (p.cmb_phase, [NONE] + list(names), prev["phase"], guess_phase),
                (p.cmb_freq, freq_names, prev["freq"], track if track in freq_names else None),
                (p.cmb_kappa, [NONE] + list(names), prev["kappa"], guess_kappa)):
            cmb.blockSignals(True)
            cmb.clear()
            cmb.addItems(items)
            pick = pv if pv in items else dflt
            if pick in items:
                cmb.setCurrentText(pick)
            cmb.blockSignals(False)
        for k in ("phase", "freq", "kappa"):
            pend.pop(k, None)
        self.remember_roles()
        self._roles_key = self.c.formula_key()
        mem = self.role_memory.get(self._roles_key, {})
        roles = self.default_roles(names, units)
        roles.update({k: v for k, v in mem.items() if k in roles})
        p.role_table.set_rows(names, units, roles)
        self._sync_link_role()
        self.update_slice_label()

    def default_roles(self, names, units):
        phase = self._combo_name(self.p.cmb_phase)
        fr = self.c.data["frequency"] if self.c.data is not None else None
        try:
            _, _, p0, _, _, fixed = self.w.table.values()
        except Exception:
            p0, fixed = np.full(len(names), np.nan), np.zeros(len(names), bool)
        if fr is not None:
            moving = default_moving(units, p0, fr.min(), fr.max())
        else:
            moving = [fm.freq_factor(u) is not None and n.lower().startswith(("w", "f"))
                      for n, u in zip(names, units)]
        out = {}
        for j, n in enumerate(names):
            if n == phase:
                out[n] = "link"
            elif fixed[j]:
                out[n] = "fixed"
            elif moving[j] or n == self._combo_name(self.p.cmb_freq) or n.lower() in SLICE_NAMES:
                out[n] = "slice"
            else:
                out[n] = "shared"
        return out

    def reset_roles(self):
        names, units = self.names_units()
        self.p.role_table.set_rows(names, units, self.default_roles(names, units))
        self.role_memory.pop(self._roles_key, None)

    def _sync_link_role(self):
        phase = self._combo_name(self.p.cmb_phase)
        roles = self.p.role_table.roles()
        for n, r in roles.items():
            if r == "link" and n != phase:
                self.p.role_table.set_role(n, "shared")
        if phase and phase in roles and roles[phase] != "fixed":
            self.p.role_table.set_role(phase, "link")

    def remember_roles(self):
        if self.p.role_table.rowCount() and self._roles_key:
            self.role_memory[self._roles_key] = self.p.role_table.roles()

    def state(self):
        p = self.p
        self.remember_roles()
        module = self.c.module
        identity = (self.c.data or {}).get("identity") or getattr(self.w, "source_identity", None)
        return dict(phase=self._combo_name(p.cmb_phase) or "", freq=self._combo_name(p.cmb_freq) or "",
                    kappa=self._combo_name(p.cmb_kappa) or "", kappa_sin=p.chk_kappa_sin.isChecked(),
                    period_pi=p.spin_period.value(), source=p.cmb_source.currentData(),
                    tmax=p.spin_tmax.value(), nodes=p.txt_nodes.text(), thr=p.spin_thr.value(),
                    T=p.spin_T.value(), phi_ref=p.spin_phi_ref.value(), fref=p.spin_fref.value(),
                    kb=p.spin_kb.value(), show_nodes=p.chk_show_nodes.isChecked(),
                    link_mode=p.cmb_link_mode.currentData(), soft=p.spin_soft.value(),
                    skip=p.chk_skip.isChecked(), guard=p.spin_guard.value(),
                    fix_shared=p.chk_fix_shared.isChecked(),
                    stride=p.spin_stride.value(), ok_only=p.chk_ok_only.isChecked(),
                    fit_T=p.chk_fit_T.isChecked(), fit_phi=p.chk_fit_phi.isChecked(),
                    gnfev=p.spin_gnfev.value(), roles=self.role_memory,
                    data_identity=identity,
                    model_id=getattr(module, "MODEL_ID", None),
                    model_version=getattr(module, "MODEL_VERSION", None),
                    physical_points=[point.to_dict() for point in self.physical_points],
                    coarse=dict(enabled=p.chk_coarse_enabled.isChecked(),
                                mode=p.cmb_coarse_mode.currentText(),
                                window=p.spin_coarse_window.value(),
                                smoothing=p.spin_coarse_smoothing.value(),
                                distance=p.spin_coarse_distance.value(),
                                prominence=p.spin_coarse_prominence.value(),
                                manual_threshold=p.chk_coarse_manual_threshold.isChecked(),
                                threshold=p.spin_coarse_threshold.value(),
                                points=[point.to_dict() for point in self.coarse_points]))

    def apply_state(self, st):
        p = self.p
        self.pending = dict(phase=st.get("phase"), freq=st.get("freq"), kappa=st.get("kappa"))
        self.role_memory = dict(st.get("roles") or {})
        p.chk_kappa_sin.setChecked(bool(st.get("kappa_sin", True)))
        p.spin_period.setValue(float(st.get("period_pi", 1.0)))
        i = p.cmb_source.findData(st.get("source", "kappa"))
        p.cmb_source.setCurrentIndex(max(i, 0))
        p.spin_tmax.setValue(float(st.get("tmax", 20.0)))
        p.txt_nodes.setText(st.get("nodes", ""))
        p.spin_thr.setValue(float(st.get("thr", 0.35)))
        for sp, key in ((p.spin_T, "T"), (p.spin_phi_ref, "phi_ref"), (p.spin_kb, "kb")):
            sp.setValue(float(st.get(key, 0.0)))
        p.spin_fref.setValue(float(st.get("fref", 5.0)))
        p.chk_show_nodes.setChecked(bool(st.get("show_nodes", True)))
        i = p.cmb_link_mode.findData(st.get("link_mode", "hard"))
        p.cmb_link_mode.setCurrentIndex(max(i, 0))
        p.spin_soft.setValue(float(st.get("soft", 0.3)))
        p.chk_skip.setChecked(bool(st.get("skip", True)))
        p.spin_guard.setValue(float(st.get("guard", 0.12)))
        p.chk_fix_shared.setChecked(bool(st.get("fix_shared", True)))
        p.spin_stride.setValue(int(st.get("stride", 1)))
        p.chk_ok_only.setChecked(bool(st.get("ok_only", True)))
        p.chk_fit_T.setChecked(bool(st.get("fit_T", True)))
        p.chk_fit_phi.setChecked(bool(st.get("fit_phi", True)))
        p.spin_gnfev.setValue(int(st.get("gnfev", 200)))
        coarse = st.get("coarse") or {}
        p.chk_coarse_enabled.setChecked(bool(coarse.get("enabled", False)))
        i = p.cmb_coarse_mode.findText(coarse.get("mode", "Both"))
        p.cmb_coarse_mode.setCurrentIndex(max(i, 0))
        p.spin_coarse_window.setValue(float(coarse.get("window", 0.25)))
        p.spin_coarse_smoothing.setValue(int(coarse.get("smoothing", 3)))
        p.spin_coarse_distance.setValue(int(coarse.get("distance", 10)))
        p.spin_coarse_prominence.setValue(float(coarse.get("prominence", 0.0002)))
        p.chk_coarse_manual_threshold.setChecked(bool(coarse.get("manual_threshold", False)))
        p.spin_coarse_threshold.setValue(float(coarse.get("threshold", 0.1)))
        self._pending_saved_points = dict(
            identity=st.get("data_identity"),
            coarse=coarse.get("points") or [],
        )
        self.set_coarse_enabled(p.chk_coarse_enabled.isChecked())

    def restore_data_bound_results(self):
        """Restore only saved empirical points for the same canonical Data."""
        pending = self._pending_saved_points
        self._pending_saved_points = None
        self.coarse_points = []
        if pending and self.c.data is not None and pending.get("identity") == self.c.data.get("identity"):
            from app.analysis.yig_mirror.results.model import AnalysisPoint, COARSE_TYPES

            for value in pending.get("coarse", []):
                try:
                    point = AnalysisPoint.from_dict(value)
                except (TypeError, ValueError):
                    continue
                if point.type in COARSE_TYPES:
                    self.coarse_points.append(point)
        self.update_nodes()

    def update_enabled(self, has_data, is2d, can_fit, busy):
        p = self.p
        running = busy and self.c.engine.kind == "global"
        has_rec = bool(self.c.batch.records)
        for x in (p.btn_estimate, p.btn_from_nodes):
            x.setEnabled(not busy)
        p.btn_detect.setEnabled(has_rec and has_data and not busy)
        p.btn_link_batch.setEnabled(self.w.btn_batch.isEnabled())
        p.btn_global.setEnabled(can_fit and has_rec)
        p.btn_global.setText("Global Fitting..." if running else "Start Global Linked Fit")
        p.btn_global_cancel.setEnabled(running)
        p.role_table.setEnabled(not busy)
        has_g = self.gres is not None and not busy
        for x in (p.btn_apply_shared, p.btn_to_batch, p.btn_export):
            x.setEnabled(has_g)

    def on_batch_updated(self):
        self.depth = None
        self.update_slice_label()
        self.redraw(quiet=True)

    def update_slice_label(self):
        try:
            recs = self._select_records()
            self.p.lbl_gslices.setText(f"{len(recs)} slices will be used")
        except Exception:
            self.p.lbl_gslices.setText("")

    # ================================================================ 直線估計
    def _series(self):
        """回傳 (f_hz, phi, keff, recs) —— 只含有結果的成功切片"""
        meta, recs = self._meta_ok()
        names, units = meta["names"], meta["units"]
        fi, fac = self._freq_info(names, units)
        phase = self._combo_name(self.p.cmb_phase)
        kap = self._combo_name(self.p.cmb_kappa)
        use = [r for r in recs if r["ok"] and r["params"] is not None]
        P = np.array([r["params"] for r in use]) if use else np.zeros((0, len(names)))
        f = P[:, fi] * fac
        phi = P[:, names.index(phase)] if phase else None
        keff = None
        if kap:
            keff = P[:, names.index(kap)].copy()
            if phase and self.p.chk_kappa_sin.isChecked():
                keff = keff * np.sin(np.pi / self.period * phi) ** 2
            keff = np.abs(keff)
        return f, phi, keff, use

    def estimate(self, source=None):
        p = self.p
        source = source or p.cmb_source.currentData()
        try:
            if source == "nodes":
                nodes = [a * 1e9 for a, b in parse_ranges_points(p.txt_nodes.text())]
                sign = -1 if p.spin_T.value() < 0 else 1
                L = ph.line_from_nodes(nodes, period=self.period, sign=sign)
                info = (f"Estimated from {len(nodes)} Node candidates: spacing {L['spacing_hz'] / 1e6:.4g} MHz"
                        f" (residual {L['rms_hz'] / 1e6:.3g} MHz)")
                kb = None
            else:
                f, phi, keff, use = self._series()
                if source == "kappa":
                    if keff is None:
                        raise ValueError("Select the coupling parameter κ_b or choose another estimator source.")
                    L = ph.fit_kappa_line(f, keff, period=self.period, T_max_ns=p.spin_tmax.value())
                    kb = L["kappa_b"]
                    info = (f"κ_eff fit: R² = {L['r2']:.4f}, used {L['n_used']}/{L['n_total']} slices; "
                            f"κ_b = {kb:.5g} ± {L['kappa_b_err']:.2g}")
                else:
                    if phi is None:
                        raise ValueError("Select a phase parameter.")
                    L = ph.fit_phase_line(f, phi, period=self.period, T_max_ns=p.spin_tmax.value())
                    kb = None
                    info = (f"Folded φ fit: residual {L['rms']:.3g} rad, used {L['n_used']}/{L['n_total']} slices"
                            + (" (using the −φ mirror solution)" if L["sign"] < 0 else ""))
                info += (f"\nT = {L['T_ns']:.6g} ± {L['T_err']:.2g} ns，"
                         f"Node spacing {L['node_spacing_hz'] / 1e6:.5g} MHz")
        except Exception as e:
            QMessageBox.warning(self.w, "Cannot Estimate Phase Line", str(e))
            return
        for sp, v in ((p.spin_T, L["T_ns"]), (p.spin_phi_ref, L["phi_ref"]),
                      (p.spin_fref, L["f_ref"] / 1e9)):
            sp.blockSignals(True)
            sp.setValue(v)
            sp.blockSignals(False)
        if kb is not None:
            p.spin_kb.setValue(kb)
        self.line_info = L
        p.lbl_line.setText(info)
        self.update_nodes()
        self.redraw()
        self.report_line()
        self.c.status("Phase line estimated")

    def report_line(self):
        L, p = self.line(), self.p
        lines = ["=== Phase Line ===",
                 "φ(f_m) = φ_ref + 2π·T·(f_m − f_ref)",
                 f"T      = {L['T_ns']:.8g} ns    (x/v_g)",
                 f"φ_ref  = {L['phi_ref']:.6g} rad",
                 f"f_ref  = {L['f_ref'] / 1e9:.9g} GHz",
                 f"Period P = {p.spin_period.value():g} π"]
        if L["T_ns"]:
            lines.append(f"Node spacing = {self.period / (ph.TWO_PI_NS * abs(L['T_ns'])) / 1e6:.6g} MHz")
        if L["kappa_b"]:
            lines.append(f"Estimated κ_b = {L['kappa_b']:.6g}")
        if p.lbl_line.text():
            lines += ["", p.lbl_line.text()]
        nodes = self.nodes()
        if nodes:
            lines += ["", "Physical Nodes (φ = nP):"] + [f"  n = {n:+d}   f = {f / 1e9:.6f} GHz" for n, f in nodes]
        p.txt.setPlainText("\n".join(lines))

    def nodes(self):
        L = self.line()
        d = self.c.data
        if not L["T_ns"]:
            return []
        if d is not None:
            fmin, fmax = float(d["frequency"].min()), float(d["frequency"].max())
        else:
            fmin, fmax = L["f_ref"] - 5e9, L["f_ref"] + 5e9
        return ph.node_frequencies(L["T_ns"], L["phi_ref"], L["f_ref"], fmin, fmax, self.period)

    def update_nodes(self):
        data = self.c.data
        self.physical_points = []
        relation = getattr(self.c.module, "PHYSICAL_COUPLING_RELATION", None)
        line = self.line()
        if (relation == SIN_SQUARED_COUPLING and line["T_ns"] and data is not None):
            try:
                names, units = self.names_units()
                kappa_name = self._combo_name(self.p.cmb_kappa)
                kappa_b_hz = None
                if kappa_name and line["kappa_b"] is not None:
                    factor = fm.freq_factor(units[names.index(kappa_name)])
                    if factor is not None:
                        kappa_b_hz = line["kappa_b"] * factor
                self.physical_points = analyze_physical_positions(
                    model_relation=relation,
                    T_ns=line["T_ns"],
                    phi_ref=line["phi_ref"],
                    f_ref_hz=line["f_ref"],
                    f_min_hz=float(np.min(data["frequency"])),
                    f_max_hz=float(np.max(data["frequency"])),
                    kappa_b_hz=kappa_b_hz,
                    trace_identity=data.get("identity"),
                )
            except (ValueError, IndexError):
                self.physical_points = []
        visible_physical = self.physical_points if self.p.chk_show_nodes.isChecked() else []
        all_points = visible_physical + list(self.coarse_points)
        self.w.data_plot.set_analysis_points(all_points)
        self._update_location_table(self.physical_points + list(self.coarse_points))
        L = self.line()
        if L["T_ns"]:
            g = self.p.spin_guard.value()
            frac = g / self.period * 2
            self.p.lbl_guard.setText(
                f"Skip window ≈ Node ± {g / (ph.TWO_PI_NS * abs(L['T_ns'])) / 1e6:.3g} MHz"
                f" (about {100 * frac:.0f}% of the frequency span); here sin²φ < {np.sin(np.pi / self.period * g) ** 2:.3g}")
        else:
            self.p.lbl_guard.setText("Phase line is not set (T = 0)")
        self.redraw(quiet=True)

    def _phase_source_map(self):
        data = self.c.data
        if data is None:
            return None
        values = data.get("s_params", {}).get(self.w.cmb_s.currentText())
        if values is None or np.asarray(values).ndim != 2:
            return None
        try:
            sweep, sweep_name = self.c.axis_values()
        except Exception:
            return None
        frequency = np.asarray(data.get("frequency"), dtype=float)
        values = np.asarray(values, dtype=complex)
        if values.shape != (frequency.size, np.asarray(sweep).size):
            return None
        return {"frequency_hz": frequency, "sweep_values": np.asarray(sweep, dtype=float),
                "complex_values": values, "sweep_name": sweep_name}

    def _update_location_table(self, points):
        table = self.p.results_table
        table.setRowCount(len(points))
        for row, point in enumerate(points):
            values = (
                point.type,
                point.method,
                "" if point.sweep_value is None else f"{point.sweep_value:.8g}",
                f"{point.frequency_hz / 1e9:.9g}",
                "" if point.phase_rad is None else f"{point.phase_rad:.7g}",
                "" if point.kappa_hz is None else f"{point.kappa_hz:.7g}",
                point.status,
            )
            for column, value in enumerate(values):
                from PySide6.QtWidgets import QTableWidgetItem
                table.setItem(row, column, QTableWidgetItem(value))

    def export_location_points(self):
        points = self.physical_points + list(self.coarse_points)
        if not points:
            QMessageBox.information(self.w, "No location results", "Run physical or coarse analysis first.")
            return
        identity = (self.c.data or {}).get("identity") or ""
        module = self.c.module
        default_name = safe_stem(os.path.basename((self.c.data or {}).get("path", "YIG_analysis")))
        path, _ = QFileDialog.getSaveFileName(
            self.w, "Export location results", f"{default_name}_locations.csv", "CSV (*.csv)")
        if not path:
            return
        fields = ("data_identity", "model_id", "model_version", "type", "method",
                  "trace_identity", "sweep_value", "frequency_hz", "frequency_ghz",
                  "phase_rad", "kappa_hz", "status", "origin")
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for point in points:
                    row = point.to_dict()
                    row.update(
                        data_identity=identity,
                        model_id=getattr(module, "MODEL_ID", ""),
                        model_version=getattr(module, "MODEL_VERSION", ""),
                        frequency_ghz=point.frequency_hz / 1e9,
                    )
                    writer.writerow(row)
        except (OSError, csv.Error) as error:
            QMessageBox.critical(self.w, "Export failed", str(error))
            return
        self.c.status(f"Exported {len(points)} location results to {path}")

    # ================================================================ 節點偵測
    def detect_nodes(self):
        try:
            meta, recs = self._meta_ok(need_data=True)
            names, units = meta["names"], meta["units"]
            fi, fac = self._freq_info(names, units)
            d = self.c.data
            freq = d["frequency"]
            S = d["s_params"][meta["s_name"]]
            cfg = meta.get("cfg") or {}
            off = cfg.get("offset_hz", 0.0) if meta.get("track") == names[fi] else 0.0
            fs, ds = [], []
            for r in recs:
                if r["idx"] >= S.shape[1]:
                    continue
                if r["params"] is not None and r["ok"]:
                    fc = r["params"][fi] * fac
                else:
                    fc = 0.5 * (r["f1"] + r["f2"]) - off
                fs.append(fc)
                ds.append(ph.signal_depth(freq, S[:, r["idx"]], r["f1"], r["f2"]))
            fs, ds = np.array(fs), np.array(ds)
            nodes = ph.detect_nodes(fs, ds, rel_thresh=self.p.spin_thr.value())
        except Exception as e:
            QMessageBox.warning(self.w, "Cannot Detect Coarse Nodes", str(e))
            return
        self.depth = (fs, ds, nodes)
        self.p.txt_nodes.setText(", ".join(f"{x / 1e9:.5f}" for x in nodes))
        self.redraw()
        self.c.status(f"Detected {len(nodes)} Coarse Node Candidates" + (" (at least 2 are needed to estimate a line)" if len(nodes) < 2 else ""))

    # ================================================================ 繪圖
    def redraw(self, quiet=False):
        source_map = self._phase_source_map()
        try:
            f, phi, keff, use = self._series()
        except Exception as e:
            self.p.plot.plot(source_map=source_map,
                             points=self.physical_points + list(self.coarse_points),
                             title="Continuous Fit required" if not quiet else "")
            if not quiet:
                self.p.lbl_line.setText(str(e))
            return
        names, units = self.c.batch.meta["names"], self.c.batch.meta["units"]
        kap = None
        if keff is not None:
            inl = None
            li = self.line_info
            if li is not None and "kappa" in li and len(li["kappa"]) == len(keff) \
                    and np.allclose(li["kappa"], keff, equal_nan=True):
                inl = li["inlier"]
            kname = self._combo_name(self.p.cmb_kappa)
            kap = (f, keff, inl, units[names.index(kname)])
        glob = None
        if self.gres is not None and self._gctx is not None and self.gres.get("link_on"):
            G = self.gres
            fi, fac = self._gctx["fi"], self._gctx["ffac"]
            glob = (G["params"][:, fi] * fac, G["params"][:, self._gctx["pi"]])
        depth = self.depth
        L = self.line()
        xs = [f] + ([depth[0]] if depth is not None else [])
        allf = np.concatenate([x for x in xs if len(x)]) if any(len(x) for x in xs) else None
        frange = None if allf is None else (np.nanmin(allf), np.nanmax(allf))
        nodes = []
        if L["T_ns"] and frange is not None:
            nodes = ph.node_frequencies(L["T_ns"], L["phi_ref"], L["f_ref"], frange[0], frange[1],
                                        self.period)
        trajectory = None
        if source_map is not None:
            sweep = source_map["sweep_values"]
            trajectory_x, trajectory_f = [], []
            for frequency_hz, record in zip(f, use):
                index = int(record.get("idx", -1))
                if 0 <= index < sweep.size:
                    trajectory_x.append(sweep[index])
                    trajectory_f.append(frequency_hz)
            trajectory = (trajectory_x, trajectory_f,
                          np.isfinite(trajectory_x) & np.isfinite(trajectory_f))
        self.p.plot.plot(kap=kap, phi=(f, phi) if phi is not None else None, depth=depth,
                         line=L if L["T_ns"] else None, nodes=nodes, period=self.period,
                         frange=frange, glob=glob,
                         title=f"{self.c.batch.meta['func']} — {len(use)} fitted traces",
                         source_map=source_map,
                         points=self.physical_points + list(self.coarse_points),
                         trajectory=trajectory)

    # ================================================================ 相位連結連續擬合
    def link_cfg(self):
        p = self.p
        names, units = self.names_units()
        phase = self._combo_name(p.cmb_phase)
        if not phase:
            raise ValueError("Select a phase parameter.")
        fi, _ = self._freq_info(names, units)
        L = self.line()
        if not L["T_ns"]:
            raise ValueError("Phase-line T is zero. Estimate or enter T first.")
        return dict(phase=phase, freq=names[fi], T=L["T_ns"], phi_ref=L["phi_ref"],
                    f_ref=L["f_ref"], period=self.period, mode=p.cmb_link_mode.currentData(),
                    soft_half=p.spin_soft.value(), guard=p.spin_guard.value(),
                    skip=p.chk_skip.isChecked(),
                    fix=self.shared_fix_values()[0] if p.chk_fix_shared.isChecked() else {})

    def shared_fix_values(self):
        """連結連續擬合時要固定的共用參數：{名稱: 值}（來源：全域擬合 > κ_b 估計 > 參數表初值）"""
        names, _ = self.names_units()
        roles = self.p.role_table.roles()
        _, _, p0, _, _, _ = self.w.table.values()
        G = self.gres if (self.gres is not None and self._gctx
                          and self._gctx["names"] == names) else None
        kap = self._combo_name(self.p.cmb_kappa)
        out, src = {}, {}
        for j, n in enumerate(names):
            if roles.get(n) != "shared":
                continue
            if G is not None and n in G["shared"]:
                out[n], src[n] = G["shared"][n], "Global fit"
            elif n == kap and self.p.spin_kb.value() > 0:
                out[n], src[n] = self.p.spin_kb.value(), "Estimate"
            else:
                out[n], src[n] = float(p0[j]), "Initial value"
        return out, src

    def update_fix_label(self):
        try:
            vals, src = self.shared_fix_values()
            txt = ", ".join(f"{n}={v:.5g} ({src[n]})" for n, v in vals.items())
            self.p.lbl_fix.setText(("Will fix: " + txt) if txt else "No shared parameters")
        except Exception:
            self.p.lbl_fix.setText("")

    def start_link_batch(self):
        self.update_fix_label()
        try:
            link = self.link_cfg()
        except Exception as e:
            QMessageBox.warning(self.w, "Cannot Start", str(e))
            return
        self.c.batch.start(link=link)

    # ================================================================ 全域擬合
    def _select_records(self):
        meta, recs = self._meta_ok()
        use = [r for r in recs if r["params"] is not None and (r["ok"] or not self.p.chk_ok_only.isChecked())]
        return use[::max(1, self.p.spin_stride.value())]

    def start_global(self):
        c, w, p = self.c, self.w, self.p
        try:
            meta, _ = self._meta_ok(need_data=True)
            names, units, p0, lo, hi, fixed = w.table.values()
            roles_d = p.role_table.roles()
            roles = []
            for j, n in enumerate(names):
                r = "fixed" if fixed[j] else roles_d.get(n, "shared")
                roles.append(r)
            phase = self._combo_name(p.cmb_phase)
            link = None
            fi, ffac = self._freq_info(names, units)
            if "link" in roles:
                bad = [n for n, r in zip(names, roles) if r == "link" and n != phase]
                if bad:
                    raise ValueError(f"Only the phase parameter ({phase}) can use the phase-line role: {', '.join(bad)}")
                L = self.line()
                if not L["T_ns"]:
                    raise ValueError("Phase-line T is zero. Estimate or enter T first.")
                link = dict(phase=phase, freq=names[fi], ffac=ffac, T=L["T_ns"],
                            phi_ref=L["phi_ref"], f_ref=L["f_ref"],
                            fit_T=p.chk_fit_T.isChecked(), fit_phi=p.chk_fit_phi.isChecked())
            if "slice" not in roles and "shared" not in roles and not (
                    link and (link["fit_T"] or link["fit_phi"])):
                raise ValueError("There are no free parameters to fit.")
            recs = self._select_records()
            if len(recs) < 2:
                raise ValueError("Fewer than two usable slices are available.")

            d = c.data
            freq = d["frequency"]
            S = d["s_params"][meta["s_name"]]
            cfg = meta.get("cfg") or {}
            clean = cfg.get("clean")
            fr = freq
            moving = cfg.get("moving") or default_moving(units, p0, fr.min(), fr.max())
            ti = names.index(meta["track"]) if meta.get("track") in names else None
            ti2 = names.index(meta["track2"]) if meta.get("track2") in names else None
            slices = []
            for r in recs:
                s_all = S[:, r["idx"]]
                m = window_mask(freq, r["f1"], r["f2"], r.get("f1b"), r.get("f2b"))
                if clean:
                    m &= clean_mask(freq, s_all, clean)
                lk, hk = lo.copy(), hi.copy()
                has2 = r.get("f1b") is not None and np.isfinite(r.get("f1b", np.nan))
                for j, u in enumerate(units):
                    fac = fm.freq_factor(u)
                    if fac is None or not moving[j] or roles[j] != "slice":
                        continue
                    if j == ti:
                        a, b = r["f1"], r["f2"]
                    elif j == ti2 and has2:
                        a, b = r["f1b"], r["f2b"]
                    else:
                        a = min(r["f1"], r["f1b"]) if has2 else r["f1"]
                        b = max(r["f2"], r["f2b"]) if has2 else r["f2"]
                    lk[j], hk[j] = a / fac, b / fac
                slices.append(dict(freq=freq[m], s=s_all[m], params=np.asarray(r["params"], float),
                                   lo=lk, hi=hk, idx=r["idx"]))

            P = np.array([r["params"] for r in recs], float)
            init = p0.copy()
            kap = self._combo_name(p.cmb_kappa)
            for j, n in enumerate(names):
                if roles[j] == "shared":
                    init[j] = float(np.nanmedian(P[:, j]))
                    if n == kap and p.spin_kb.value() > 0 and meta.get("cfg", {}).get("link") is None:
                        init[j] = p.spin_kb.value()   # 一般連續擬合的 κ_b 與 φ 耦合，用估計值
                    if not np.isfinite(init[j]):
                        init[j] = p0[j]
                    if np.isfinite(lo[j]) and np.isfinite(hi[j]):
                        init[j] = float(np.clip(init[j], lo[j], hi[j]))
            opts = dict(c.fit_options(), max_nfev=p.spin_gnfev.value())
        except Exception as e:
            QMessageBox.warning(w, "Cannot Start Global Linked Fit", str(e))
            return

        self.remember_roles()
        self._gctx = dict(meta=copy.deepcopy(meta), recs=recs, names=names, units=units,
                          roles=roles, fi=fi, ffac=ffac,
                          pi=names.index(phase) if phase in names else None,
                          lo=lo, hi=hi, p0=p0, fixed=fixed, link=link, t0=time.perf_counter())
        npts = sum(len(s["freq"]) for s in slices)
        c.engine.submit_global(c.formula_path, w.cmb_func.currentText(), slices, names, roles,
                               init, lo, hi, link, opts)
        p.lbl_gprog.setText(f"Global Linked Fit running: {len(slices)} slices, {npts} points...")
        c.status(f"Global Linked Fit running: {len(slices)} slices")

    def on_global_progress(self, nfev, cost, elapsed):
        self.p.lbl_gprog.setText(f"nfev = {nfev}   cost = {cost:.5g}   {elapsed:.1f} s")

    def on_global_done(self, res):
        g, p = self._gctx, self.p
        res["link_on"] = g["link"] is not None
        self.gres = res
        if g["link"] is not None:
            for sp, key in ((p.spin_T, "T"), (p.spin_phi_ref, "phi_ref")):
                sp.blockSignals(True)
                sp.setValue(res[key])
                sp.blockSignals(False)
            p.spin_fref.setValue(res["f_ref"] / 1e9)
        kap = self._combo_name(p.cmb_kappa)
        if kap in res["shared"]:
            p.spin_kb.setValue(abs(res["shared"][kap]))
        p.lbl_gprog.setText(f"Complete: {res['message']} ({res['elapsed']:.1f} s)")
        p.txt.setPlainText(self.global_report())
        self.update_fix_label()
        self.update_nodes()
        self.redraw()
        self.c.update_enabled()
        self.c.status(f"Global Linked Fit complete: χ²_red = {res['chi2_red']:.4g}, {res['elapsed']:.1f} s")

    def global_report(self):
        G, g = self.gres, self._gctx
        names, units = g["names"], g["units"]

        def u(n):
            x = units[names.index(n)]
            return f" {x}" if x else ""

        L = [f"=== Global Linked Fit  {g['meta']['func']}  ({g['meta']['file']}) ===",
             f"{len(G['idx'])} slices, {G['n_points']} data points, {G['n_free']} free parameters",
             f"χ²_red = {G['chi2_red']:.6g}   nfev = {G['nfev']}   {G['elapsed']:.1f} s",
             f"Status: {G['message']}",
             f"Per-slice R²: min {np.nanmin(G['r2']):.5f}, median {np.nanmedian(G['r2']):.5f}", ""]
        if G.get("link_on"):
            L += ["[Phase Line]",
                  f"  T      = {G['T']:.8g} ± {G['T_err']:.2g} ns" + ("" if g["link"]["fit_T"] else " (fixed)"),
                  f"  φ_ref  = {G['phi_ref']:.6g} ± {G['phi_ref_err']:.2g} rad"
                  + ("" if g["link"]["fit_phi"] else " (fixed)"),
                  f"  f_ref  = {G['f_ref'] / 1e9:.9g} GHz"]
            if G["T"]:
                L.append(f"  Node spacing = {self.period / (ph.TWO_PI_NS * abs(G['T'])) / 1e6:.6g} MHz")
            L.append("")
        L.append("[Shared]")
        for n, v in G["shared"].items():
            e = G["shared_err"][n]
            L.append(f"  {n:<12s} = {v:.8g} ± {e:.3g}{u(n)}")
        fx = [n for n, r in zip(names, G["roles"]) if r == "fixed"]
        if fx:
            L.append("[Fixed] " + ", ".join(f"{n} = {g['p0'][names.index(n)]:.6g}{u(n)}" for n in fx))
        sl = [j for j, r in enumerate(G["roles"]) if r in ("slice", "link")]
        if sl:
            L += ["", "[Per Slice]"]
            head = f"{'Slice':>6s} " + " ".join(f"{names[j] + u(names[j]):>16s}" for j in sl) + f" {'R²':>9s}"
            L.append(head)
            for k, idx in enumerate(G["idx"]):
                L.append(f"{idx:>6d} " + " ".join(f"{G['params'][k, j]:>16.8g}" for j in sl)
                         + f" {G['r2'][k]:>9.5f}")
        nodes = self.nodes()
        if nodes:
            L += ["", "Physical Nodes (φ = nP):"] + [f"  n = {n:+d}   f = {f / 1e9:.6f} GHz" for n, f in nodes]
        return "\n".join(L)

    def apply_shared(self):
        G = self.gres
        if G is None:
            return
        names = self.w.table.names()
        if names != self._gctx["names"]:
            QMessageBox.warning(self.w, "Parameter Mismatch", "The current model parameters differ from the Global Linked Fit.")
            return
        vals = np.full(len(names), np.nan)
        for n, v in G["shared"].items():
            vals[names.index(n)] = v
        self.w.table.set_p0(vals)
        self.c.status("Copied Global Linked Fit shared values to initial values")

    def global_records(self):
        G, g = self.gres, self._gctx
        out = []
        for k, r in enumerate(g["recs"]):
            rec = dict(r)
            rec.update(k=k, total=len(g["recs"]), ok=True, params=G["params"][k].copy(),
                       errors=G["errors"][k].copy(), r2=float(G["r2"][k]),
                       r2_complex=float(G["r2_complex"][k]), chi2_red=G["chi2_red"],
                       nfev=G["nfev"], elapsed=0.0, msg="Global Linked Fit", skipped=False)
            if g["pi"] is not None:
                rec["phi_pred"] = float(G["params"][k, g["pi"]])
                rec["node_dist"] = float(ph.node_distance(rec["phi_pred"], self.period))
            out.append(rec)
        meta = copy.deepcopy(g["meta"])
        meta["global_fit"] = dict(
            roles=dict(zip(g["names"], G["roles"])), shared=G["shared"], shared_err=G["shared_err"],
            T_ns=G["T"], T_err=G["T_err"], phi_ref=G["phi_ref"], phi_ref_err=G["phi_ref_err"],
            f_ref_hz=G["f_ref"], chi2_red=G["chi2_red"], nfev=G["nfev"], message=G["message"],
            p0=[float(x) for x in g["p0"]], period=self.period,
            created=time.strftime("%Y-%m-%d %H:%M:%S"))
        meta["created"] = meta["global_fit"]["created"]
        return meta, out

    def to_batch(self):
        if self.gres is None:
            return
        meta, recs = self.global_records()
        c = self.c
        path = os.path.join(c.output_dir(), f"{safe_stem(meta['file'])}_{meta['func']}_global_autosave.csv")
        c.batch.set_records(meta, recs, dirty=True, autosave_path=path)
        self.w.tabs.setCurrentWidget(self.w.batch_panel)
        c.status("Global Linked Fit results sent to Continuous Fit for per-slice review")

    def export_global(self):
        if self.gres is None:
            return
        meta, recs = self.global_records()
        path, _ = QFileDialog.getSaveFileName(
            self.w, "Export Global Linked Fit Results",
            os.path.join(self.c.output_dir(), f"{safe_stem(meta['file'])}_{meta['func']}_global.csv"),
            "CSV (*.csv)")
        if not path:
            return
        try:
            self.write_global(path, meta, recs)
        except Exception as e:
            QMessageBox.critical(self.w, "Export Failed", str(e))

    def write_global(self, path, meta=None, recs=None):
        if meta is None:
            meta, recs = self.global_records()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        write_batch_csv(path, meta, recs)
        base = os.path.splitext(path)[0]
        self.p.plot.save_all_to(base + "_phase.png")
        with open(base + "_report.txt", "w", encoding="utf-8") as fh:
            fh.write(self.global_report())
        self.c.status(f"Exported {path} with _phase.png and _report.txt")


def parse_ranges_points(text):
    """「4.26, 4.59 4.93」→ [(4.26, 4.26), ...]；也接受 a-b 取中點"""
    out = []
    for tok in text.replace(";", ",").replace("，", ",").split(","):
        for t in tok.split():
            t = t.strip()
            if not t:
                continue
            if "-" in t[1:]:
                a, b = parse_ranges(t)[0]
                v = 0.5 * (a + b)
            else:
                v = float(fm.parse_num(t))
            out.append((v, v))
    return out
