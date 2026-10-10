"""
「相位 / 節點」分頁的邏輯

1. 由連續擬合結果估計相位直線 φ = φ_ref + 2πT(f_m − f_ref)
   （κ_eff、φ 或節點頻率三種來源），並在 2D 圖標出節點 φ = nP
2. 相位連結的連續擬合（硬/軟連結、略過節點附近切片）→ 交給 BatchController.start(link)
3. 全域擬合：多片同時擬合，參數分為 每片獨立 / 全片共用 / 固定 / 相位直線
"""
import os
import copy
import time

import numpy as np
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QMessageBox, QFileDialog

from .core import formula as fm
from .core import phase as ph
from .core.batch import window_mask, default_moving, write_batch_csv
from .core.cleaning import clean_mask, parse_ranges
from .core.paths import safe_stem
from .ui.phase_panel import NONE

SLICE_NAMES = {"a", "amp", "amplitude", "phi_0", "phi0", "phase0"}


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

        p, eng = self.p, ctrl.engine
        p.btn_estimate.clicked.connect(self.estimate)
        p.btn_detect.clicked.connect(self.detect_nodes)
        p.btn_from_nodes.clicked.connect(lambda: self.estimate(source="nodes"))
        p.btn_redraw.clicked.connect(self.redraw)
        p.btn_link_batch.clicked.connect(self.start_link_batch)
        p.btn_global.clicked.connect(self.start_global)
        p.btn_global_cancel.clicked.connect(eng.cancel)
        p.btn_default_roles.clicked.connect(self.reset_roles)
        p.btn_apply_shared.clicked.connect(self.apply_shared)
        p.btn_to_batch.clicked.connect(self.to_batch)
        p.btn_export.clicked.connect(self.export_global)
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
            raise ValueError("「連續擬合」分頁沒有結果，請先執行連續擬合（或載入 CSV）")
        names, _ = self.names_units()
        if b.meta["names"] != names or b.meta["func"] != self.w.cmb_func.currentText():
            raise ValueError("連續擬合結果的函式 / 參數與目前選擇的函式不同")
        if need_data:
            d = self.c.data
            if d is None or b.meta["s_name"] not in d["s_params"]:
                raise ValueError("需要載入連續擬合結果對應的數據檔")
        return b.meta, b.records

    def _freq_info(self, names, units):
        fname = self._combo_name(self.p.cmb_freq)
        if not fname:
            raise ValueError("請選擇共振頻率參數")
        fac = fm.freq_factor(units[names.index(fname)])
        if fac is None:
            raise ValueError(f"{fname} 的單位不是頻率單位")
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
                    gnfev=p.spin_gnfev.value(), roles=self.role_memory)

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

    def update_enabled(self, has_data, is2d, can_fit, busy):
        p = self.p
        running = busy and self.c.engine.kind == "global"
        has_rec = bool(self.c.batch.records)
        for x in (p.btn_estimate, p.btn_from_nodes):
            x.setEnabled(not busy)
        p.btn_detect.setEnabled(has_rec and has_data and not busy)
        p.btn_link_batch.setEnabled(self.w.btn_batch.isEnabled())
        p.btn_global.setEnabled(can_fit and has_rec)
        p.btn_global.setText("全域擬合中…" if running else "開始全域擬合")
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
            self.p.lbl_gslices.setText(f"將使用 {len(recs)} 片")
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
                info = (f"由 {len(nodes)} 個節點估計：間距 {L['spacing_hz'] / 1e6:.4g} MHz"
                        f"（殘差 {L['rms_hz'] / 1e6:.3g} MHz）")
                kb = None
            else:
                f, phi, keff, use = self._series()
                if source == "kappa":
                    if keff is None:
                        raise ValueError("請選擇耦合參數 κ_b（或改用其他估計來源）")
                    L = ph.fit_kappa_line(f, keff, period=self.period, T_max_ns=p.spin_tmax.value())
                    kb = L["kappa_b"]
                    info = (f"κ_eff 擬合：R² = {L['r2']:.4f}，使用 {L['n_used']}/{L['n_total']} 片；"
                            f"κ_b = {kb:.5g} ± {L['kappa_b_err']:.2g}")
                else:
                    if phi is None:
                        raise ValueError("請選擇相位參數")
                    L = ph.fit_phase_line(f, phi, period=self.period, T_max_ns=p.spin_tmax.value())
                    kb = None
                    info = (f"φ 折疊擬合：殘差 {L['rms']:.3g} rad，使用 {L['n_used']}/{L['n_total']} 片"
                            + ("（採用 −φ 鏡像解）" if L["sign"] < 0 else ""))
                info += (f"\nT = {L['T_ns']:.6g} ± {L['T_err']:.2g} ns，"
                         f"節點間距 {L['node_spacing_hz'] / 1e6:.5g} MHz")
        except Exception as e:
            QMessageBox.warning(self.w, "無法估計相位直線", str(e))
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
        self.c.status("已估計相位直線")

    def report_line(self):
        L, p = self.line(), self.p
        lines = ["=== 相位直線 ===",
                 "φ(f_m) = φ_ref + 2π·T·(f_m − f_ref)",
                 f"T      = {L['T_ns']:.8g} ns    （x/v_g）",
                 f"φ_ref  = {L['phi_ref']:.6g} rad",
                 f"f_ref  = {L['f_ref'] / 1e9:.9g} GHz",
                 f"週期 P = {p.spin_period.value():g} π"]
        if L["T_ns"]:
            lines.append(f"節點間距 = {self.period / (ph.TWO_PI_NS * abs(L['T_ns'])) / 1e6:.6g} MHz")
        if L["kappa_b"]:
            lines.append(f"κ_b（估計） = {L['kappa_b']:.6g}")
        if p.lbl_line.text():
            lines += ["", p.lbl_line.text()]
        nodes = self.nodes()
        if nodes:
            lines += ["", "節點（φ = nP）："] + [f"  n = {n:+d}   f = {f / 1e9:.6f} GHz" for n, f in nodes]
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
        show = self.p.chk_show_nodes.isChecked()
        nodes = self.nodes() if show else []
        self.w.data_plot.set_nodes([f / 1e9 for _, f in nodes])
        L = self.line()
        if L["T_ns"]:
            g = self.p.spin_guard.value()
            frac = g / self.period * 2
            self.p.lbl_guard.setText(
                f"略過範圍 ≈ 節點 ± {g / (ph.TWO_PI_NS * abs(L['T_ns'])) / 1e6:.3g} MHz"
                f"（約 {100 * frac:.0f}% 的頻率）；此處 sin²φ < {np.sin(np.pi / self.period * g) ** 2:.3g}")
        else:
            self.p.lbl_guard.setText("尚未設定相位直線（T = 0）")

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
            QMessageBox.warning(self.w, "無法偵測節點", str(e))
            return
        self.depth = (fs, ds, nodes)
        self.p.txt_nodes.setText(", ".join(f"{x / 1e9:.5f}" for x in nodes))
        self.redraw()
        self.c.status(f"偵測到 {len(nodes)} 個節點" + ("（至少 2 個才能估計直線）" if len(nodes) < 2 else ""))

    # ================================================================ 繪圖
    def redraw(self, quiet=False):
        try:
            f, phi, keff, use = self._series()
        except Exception as e:
            if not quiet:
                self.p.plot.fig.clear()
                self.p.plot.fig.text(0.5, 0.5, str(e), ha="center", va="center")
                self.p.plot.canvas.draw_idle()
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
        self.p.plot.plot(kap=kap, phi=(f, phi) if phi is not None else None, depth=depth,
                         line=L if L["T_ns"] else None, nodes=nodes, period=self.period,
                         frange=frange, glob=glob,
                         title=f"{self.c.batch.meta['func']}   {len(use)} 片成功")

    # ================================================================ 相位連結連續擬合
    def link_cfg(self):
        p = self.p
        names, units = self.names_units()
        phase = self._combo_name(p.cmb_phase)
        if not phase:
            raise ValueError("請選擇相位參數")
        fi, _ = self._freq_info(names, units)
        L = self.line()
        if not L["T_ns"]:
            raise ValueError("相位直線的 T = 0，請先估計或輸入 T")
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
                out[n], src[n] = G["shared"][n], "全域"
            elif n == kap and self.p.spin_kb.value() > 0:
                out[n], src[n] = self.p.spin_kb.value(), "估計"
            else:
                out[n], src[n] = float(p0[j]), "初值"
        return out, src

    def update_fix_label(self):
        try:
            vals, src = self.shared_fix_values()
            txt = "、".join(f"{n}={v:.5g}({src[n]})" for n, v in vals.items())
            self.p.lbl_fix.setText(("將固定：" + txt) if txt else "沒有「全片共用」參數")
        except Exception:
            self.p.lbl_fix.setText("")

    def start_link_batch(self):
        self.update_fix_label()
        try:
            link = self.link_cfg()
        except Exception as e:
            QMessageBox.warning(self.w, "無法開始", str(e))
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
                    raise ValueError(f"只有相位參數（{phase}）可以設為「相位直線」：{', '.join(bad)}")
                L = self.line()
                if not L["T_ns"]:
                    raise ValueError("相位直線的 T = 0，請先估計或輸入 T")
                link = dict(phase=phase, freq=names[fi], ffac=ffac, T=L["T_ns"],
                            phi_ref=L["phi_ref"], f_ref=L["f_ref"],
                            fit_T=p.chk_fit_T.isChecked(), fit_phi=p.chk_fit_phi.isChecked())
            if "slice" not in roles and "shared" not in roles and not (
                    link and (link["fit_T"] or link["fit_phi"])):
                raise ValueError("沒有需要擬合的參數")
            recs = self._select_records()
            if len(recs) < 2:
                raise ValueError("可用的切片少於 2 片")

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
            QMessageBox.warning(w, "無法開始全域擬合", str(e))
            return

        self.remember_roles()
        self._gctx = dict(meta=copy.deepcopy(meta), recs=recs, names=names, units=units,
                          roles=roles, fi=fi, ffac=ffac,
                          pi=names.index(phase) if phase in names else None,
                          lo=lo, hi=hi, p0=p0, fixed=fixed, link=link, t0=time.perf_counter())
        npts = sum(len(s["freq"]) for s in slices)
        c.engine.submit_global(c.formula_path, w.cmb_func.currentText(), slices, names, roles,
                               init, lo, hi, link, opts)
        p.lbl_gprog.setText(f"全域擬合中：{len(slices)} 片、{npts} 點…")
        c.status(f"全域擬合中：{len(slices)} 片")

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
        p.lbl_gprog.setText(f"完成：{res['message']}（{res['elapsed']:.1f} s）")
        p.txt.setPlainText(self.global_report())
        self.update_fix_label()
        self.update_nodes()
        self.redraw()
        self.c.update_enabled()
        self.c.status(f"全域擬合完成：χ²_red = {res['chi2_red']:.4g}，耗時 {res['elapsed']:.1f} s")

    def global_report(self):
        G, g = self.gres, self._gctx
        names, units = g["names"], g["units"]

        def u(n):
            x = units[names.index(n)]
            return f" {x}" if x else ""

        L = [f"=== 全域擬合  {g['meta']['func']}  ({g['meta']['file']}) ===",
             f"切片 {len(G['idx'])} 片，數據點 {G['n_points']}，自由參數 {G['n_free']}",
             f"χ²_red = {G['chi2_red']:.6g}   nfev = {G['nfev']}   {G['elapsed']:.1f} s",
             f"狀態：{G['message']}",
             f"每片 R²：最小 {np.nanmin(G['r2']):.5f}  中位數 {np.nanmedian(G['r2']):.5f}", ""]
        if G.get("link_on"):
            L += ["[相位直線]",
                  f"  T      = {G['T']:.8g} ± {G['T_err']:.2g} ns" + ("" if g["link"]["fit_T"] else "（固定）"),
                  f"  φ_ref  = {G['phi_ref']:.6g} ± {G['phi_ref_err']:.2g} rad"
                  + ("" if g["link"]["fit_phi"] else "（固定）"),
                  f"  f_ref  = {G['f_ref'] / 1e9:.9g} GHz"]
            if G["T"]:
                L.append(f"  節點間距 = {self.period / (ph.TWO_PI_NS * abs(G['T'])) / 1e6:.6g} MHz")
            L.append("")
        L.append("[全片共用]")
        for n, v in G["shared"].items():
            e = G["shared_err"][n]
            L.append(f"  {n:<12s} = {v:.8g} ± {e:.3g}{u(n)}")
        fx = [n for n, r in zip(names, G["roles"]) if r == "fixed"]
        if fx:
            L.append("[固定] " + ", ".join(f"{n} = {g['p0'][names.index(n)]:.6g}{u(n)}" for n in fx))
        sl = [j for j, r in enumerate(G["roles"]) if r in ("slice", "link")]
        if sl:
            L += ["", "[每片]"]
            head = f"{'切片':>6s} " + " ".join(f"{names[j] + u(names[j]):>16s}" for j in sl) + f" {'R²':>9s}"
            L.append(head)
            for k, idx in enumerate(G["idx"]):
                L.append(f"{idx:>6d} " + " ".join(f"{G['params'][k, j]:>16.8g}" for j in sl)
                         + f" {G['r2'][k]:>9.5f}")
        nodes = self.nodes()
        if nodes:
            L += ["", "節點（φ = nP）："] + [f"  n = {n:+d}   f = {f / 1e9:.6f} GHz" for n, f in nodes]
        return "\n".join(L)

    def apply_shared(self):
        G = self.gres
        if G is None:
            return
        names = self.w.table.names()
        if names != self._gctx["names"]:
            QMessageBox.warning(self.w, "參數不符", "目前函式的參數與全域擬合不同")
            return
        vals = np.full(len(names), np.nan)
        for n, v in G["shared"].items():
            vals[names.index(n)] = v
        self.w.table.set_p0(vals)
        self.c.status("已將共用參數的全域擬合值設為初值")

    def global_records(self):
        G, g = self.gres, self._gctx
        out = []
        for k, r in enumerate(g["recs"]):
            rec = dict(r)
            rec.update(k=k, total=len(g["recs"]), ok=True, params=G["params"][k].copy(),
                       errors=G["errors"][k].copy(), r2=float(G["r2"][k]),
                       r2_complex=float(G["r2_complex"][k]), chi2_red=G["chi2_red"],
                       nfev=G["nfev"], elapsed=0.0, msg="全域擬合", skipped=False)
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
        c.status("已將全域擬合結果放入「連續擬合」分頁（可逐片檢視擬合圖）")

    def export_global(self):
        if self.gres is None:
            return
        meta, recs = self.global_records()
        path, _ = QFileDialog.getSaveFileName(
            self.w, "匯出全域擬合結果",
            os.path.join(self.c.output_dir(), f"{safe_stem(meta['file'])}_{meta['func']}_global.csv"),
            "CSV (*.csv)")
        if not path:
            return
        try:
            self.write_global(path, meta, recs)
        except Exception as e:
            QMessageBox.critical(self.w, "匯出失敗", str(e))

    def write_global(self, path, meta=None, recs=None):
        if meta is None:
            meta, recs = self.global_records()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        write_batch_csv(path, meta, recs)
        base = os.path.splitext(path)[0]
        self.p.plot.fig.savefig(base + "_phase.png", dpi=150)
        with open(base + "_report.txt", "w", encoding="utf-8") as fh:
            fh.write(self.global_report())
        self.c.status(f"已匯出 {path}（含 _phase.png、_report.txt）")


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
