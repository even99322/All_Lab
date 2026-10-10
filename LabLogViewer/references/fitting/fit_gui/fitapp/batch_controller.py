"""連續擬合控制：送出工作、接收逐片結果、參數變化圖、單片圖形調閱、自動備份"""
import os
import time
import traceback

import numpy as np
from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from .core import formula as fm
from .core.batch import (batch_indices, write_batch_csv, read_batch_csv, default_moving,
                         window_mask)
from .core.report import format_report
from .core.cleaning import clean_mask, is_active
from .core.paths import safe_stem


NO_TRACK2 = "（不使用）"


class BatchController(QObject):
    LIVE_MS = 600        # 即時更新圖形的最短間隔
    AUTOSAVE_MS = 2000

    def __init__(self, ctrl):
        super().__init__(ctrl)
        self.c = ctrl
        self.w = ctrl.w
        self.p = ctrl.w.batch_panel
        self.meta = None
        self.records = []
        self.pending = None          # 還原設定用
        self.roll_memory = {}        # 參數滾動追蹤設定（依參數名稱記憶）
        self._dirty = False
        self._live_row = None
        self._t_start = 0.0
        self.autosave_path = None    # 開始連續擬合時決定（位於輸出資料夾）

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(self.LIVE_MS)
        self._refresh_timer.timeout.connect(self.refresh_views)

        self._save_timer = QTimer(self)
        self._save_timer.setInterval(self.AUTOSAVE_MS)
        self._save_timer.timeout.connect(self.autosave)
        self._save_timer.start()

        eng = ctrl.engine
        eng.slice_done.connect(self.on_slice)
        eng.batch_finished.connect(self.on_finished)

        w, p = self.w, self.p
        w.btn_batch.clicked.connect(self.start)
        w.btn_batch_cancel.clicked.connect(eng.cancel)
        w.btn_b_start_cur.clicked.connect(lambda: w.spin_b_start.setValue(w.spin_idx.value()))
        w.btn_b_end_cur.clicked.connect(lambda: w.spin_b_end.setValue(w.spin_idx.value()))
        w.cmb_b_track.currentIndexChanged.connect(lambda _: self.update_window_label())
        w.cmb_b_track2.currentIndexChanged.connect(lambda _: self.on_window2_changed())
        w.roll_table.itemChanged.connect(lambda _: self.update_window_label())
        w.spin_b_w2.valueChanged.connect(lambda _: self.on_window2_changed())
        w.spin_b_off2.valueChanged.connect(lambda _: self.on_window2_changed())
        w.btn_b_w2_same.clicked.connect(
            lambda: w.spin_b_w2.setValue(abs(w.spin_f2.value() - w.spin_f1.value()) * 1e3))
        for sp in (w.spin_b_start, w.spin_b_end, w.spin_b_step):
            sp.valueChanged.connect(lambda _: self.update_window_label())
        p.cmb_param.currentIndexChanged.connect(lambda _: self.plot_params())
        p.cmb_x.currentIndexChanged.connect(lambda _: self.plot_params())
        p.chk_ok_only.toggled.connect(lambda _: self.plot_params())
        p.chk_skip_fixed.toggled.connect(lambda _: self.plot_params())
        p.chk_err.toggled.connect(lambda _: self.plot_params())
        p.plot_tabs.currentChanged.connect(lambda _: self.plot_params())
        p.chk_track.toggled.connect(lambda _: self.refresh_track())
        p.rowSelected.connect(self.show_row)
        p.btn_to_p0.clicked.connect(self.selected_to_p0)
        p.btn_goto.clicked.connect(self.goto_selected)
        p.btn_export.clicked.connect(self.export_csv)
        p.btn_import.clicked.connect(self.import_csv)
        p.btn_clear.clicked.connect(self.clear)

    # ================================================================ 狀態
    def update_enabled(self, has_data, is2d, can_fit, busy):
        w = self.w
        running = busy and self.c.engine.kind == "batch"
        ok = can_fit and is2d and w.cmb_b_track.count() > 0
        w.btn_batch.setEnabled(ok)
        w.btn_batch.setText("連續擬合中…" if running else "開始連續擬合")
        w.btn_batch_cancel.setEnabled(running)
        for x in (w.roll_table, w.chk_roll_clip,
                  w.spin_b_start, w.spin_b_end, w.spin_b_step, w.btn_b_start_cur, w.btn_b_end_cur,
                  w.cmb_b_track, w.cmb_b_track2, w.spin_b_w2, w.btn_b_w2_same, w.spin_b_off2,
                  w.cmb_b_init, w.chk_b_bound, w.spin_b_r2, w.chk_b_stop):
            x.setEnabled(is2d and not busy)
        has_rec = bool(self.records)
        for x in (self.p.btn_export, self.p.btn_clear):
            x.setEnabled(has_rec and not running)
        self.p.btn_import.setEnabled(not running)
        self.p.btn_to_p0.setEnabled(has_rec and not busy)
        self.p.btn_goto.setEnabled(has_rec and has_data)

    def on_data_changed(self):
        w = self.w
        if self.c.current_func() is not None:
            self.on_func_changed(w.table.names(), w.table.units())
        ns = self.c.data["n_steps"]
        pend = self.pending or {}
        for sp in (w.spin_b_start, w.spin_b_end):
            sp.blockSignals(True)
            sp.setRange(0, max(0, ns - 1))
            sp.blockSignals(False)
        w.spin_b_start.setValue(int(np.clip(pend.get("start", 0), 0, ns - 1)))
        w.spin_b_end.setValue(int(np.clip(pend.get("end", ns - 1), 0, ns - 1)))
        if self.pending is not None:
            self.pending.pop("start", None)
            self.pending.pop("end", None)
        self.try_restore_autosave()
        self.update_window_label()

    def on_func_changed(self, names, units):
        w = self.w
        prev = w.cmb_b_track.currentText()
        if self.pending and self.pending.get("track"):
            prev = self.pending["track"]
        w.cmb_b_track.blockSignals(True)
        w.cmb_b_track.clear()
        freq_names = self._freq_param_order(names, units)
        w.cmb_b_track.addItems(freq_names)
        if prev in freq_names:
            w.cmb_b_track.setCurrentText(prev)
            if self.pending:
                self.pending.pop("track", None)
        w.cmb_b_track.blockSignals(False)

        prev2 = w.cmb_b_track2.currentText()
        if self.pending and "track2" in self.pending:
            prev2 = self.pending["track2"] or NO_TRACK2
        w.cmb_b_track2.blockSignals(True)
        w.cmb_b_track2.clear()
        w.cmb_b_track2.addItem(NO_TRACK2)
        w.cmb_b_track2.addItems(freq_names)
        if prev2 in freq_names or prev2 == NO_TRACK2:
            w.cmb_b_track2.setCurrentText(prev2)
            if self.pending:
                self.pending.pop("track2", None)
        w.cmb_b_track2.blockSignals(False)

        saved = dict(self.roll_memory)
        saved.update(w.roll_table.state())          # 目前表格優先
        if self.pending and "roll" in self.pending:
            saved.update(self.pending.pop("roll") or {})
        if w.roll_table.rowCount() and \
                [w.roll_table.item(r, 1).text() for r in range(w.roll_table.rowCount())] == list(names):
            w.roll_table.update_units(list(names), list(units))
        else:
            self.roll_memory.update(w.roll_table.state())
            w.roll_table.set_params(list(names), list(units), saved)
        self.on_window2_changed()
        self.try_restore_autosave()

    def _freq_param_order(self, names, units):
        """頻率單位的參數；數值落在數據頻率範圍內（共振位置）的排前面"""
        cand = [(n, u) for n, u in zip(names, units) if fm.freq_factor(u) is not None]
        if self.c.data is None:
            return [n for n, _ in cand]
        fr = self.c.data["frequency"]
        p0s = {r["name"]: r["p0"] for r in self.w.table.rows()}

        def in_range(n, u):
            try:
                v = fm.parse_num(p0s.get(n, "")) * fm.freq_factor(u)
                return fr.min() <= v <= fr.max()
            except Exception:
                return False
        return [n for n, u in sorted(cand, key=lambda nu: not in_range(*nu))]

    # ---------------------------------------------------------------- 第二視窗
    def track2_name(self):
        t = self.w.cmb_b_track2.currentText()
        return None if not t or t == NO_TRACK2 else t

    def window2(self):
        """依追蹤參數 2 的初值、寬度、偏移算出視窗 2 (f1, f2)，單位 Hz；未啟用或無法計算回傳 None"""
        t2 = self.track2_name()
        if t2 is None:
            return None
        try:
            r = {row["name"]: row for row in self.w.table.rows()}[t2]
            fac = fm.freq_factor(r["unit"])
            center = fm.parse_num(r["p0"]) * fac + self.w.spin_b_off2.value() * 1e6
        except Exception:
            return None
        half = self.w.spin_b_w2.value() * 1e6 / 2
        return center - half, center + half

    def on_window2_changed(self):
        self.update_window_label()
        self.c.refresh_window2()

    def moving_names(self):
        if self.c.data is None:
            return []
        fr = self.c.data["frequency"]
        out = []
        for r in self.w.table.rows():
            try:
                fac = fm.freq_factor(r["unit"])
                if fac and fr.min() <= fm.parse_num(r["p0"]) * fac <= fr.max():
                    out.append(r["name"])
            except Exception:
                pass
        for track in (self.w.cmb_b_track.currentText(), self.track2_name()):
            if track and track not in out:
                out.append(track)
        return out

    def update_window_label(self):
        w = self.w
        if self.c.data is None or not w.cmb_b_track.currentText():
            w.lbl_b_window.setText("需要數據與頻率單位的追蹤參數")
            return
        width = abs(w.spin_f2.value() - w.spin_f1.value()) * 1e3
        n = len(batch_indices(w.spin_b_start.value(), w.spin_b_end.value(), w.spin_b_step.value()))
        txt = f"視窗寬度 {width:.4g} MHz，共 {n} 片"
        try:
            rows = {r["name"]: r for r in w.table.rows()}
            r = rows[w.cmb_b_track.currentText()]
            fac = fm.freq_factor(r["unit"])
            p0 = fm.parse_num(r["p0"]) * fac
            center = (w.spin_f1.value() + w.spin_f2.value()) / 2 * 1e9
            txt += f"，偏移 {(center - p0) / 1e6:+.4g} MHz"
        except Exception:
            pass
        t2 = self.track2_name()
        if t2:
            win2 = self.window2()
            if t2 == w.cmb_b_track.currentText():
                txt += "\n⚠ 追蹤參數 2 不能與 1 相同"
            elif win2 is None:
                txt += f"\n⚠ 無法計算視窗 2（檢查 {t2} 的初值/單位）"
            else:
                txt += f"\n視窗 2：{win2[0]/1e9:.6f} ~ {win2[1]/1e9:.6f} GHz"
        mv = self.moving_names()
        if mv:
            txt += "\n隨視窗移動：" + ", ".join(mv)
        try:
            rl = w.roll_table.config()
            if rl:
                txt += "\n滾動追蹤：" + ", ".join(
                    f"{n}(±{v['half']:.4g}{'，外插' if v['extrap'] else ''})" for n, v in rl.items())
        except ValueError as e:
            txt += f"\n⚠ {e}"
        w.lbl_b_window.setText(txt)

    def state(self):
        w = self.w
        return dict(start=w.spin_b_start.value(), end=w.spin_b_end.value(),
                    step=w.spin_b_step.value(), track=w.cmb_b_track.currentText(),
                    track2=self.track2_name() or "", width2_mhz=w.spin_b_w2.value(),
                    offset2_mhz=w.spin_b_off2.value(),
                    roll={**self.roll_memory, **w.roll_table.state()},
                    roll_clip=w.chk_roll_clip.isChecked(),
                    init_mode=w.cmb_b_init.currentData(), bound_freq=w.chk_b_bound.isChecked(),
                    r2_min=w.spin_b_r2.value(), stop_on_fail=w.chk_b_stop.isChecked(),
                    x_mode=self.p.cmb_x.currentData(), y_param=self.p.cmb_param.currentData(),
                    ok_only=self.p.chk_ok_only.isChecked(), show_track=self.p.chk_track.isChecked(),
                    live=self.p.chk_live.isChecked(),
                    skip_fixed=self.p.chk_skip_fixed.isChecked(),
                    show_err=self.p.chk_err.isChecked(),
                    plot_tab=self.p.plot_tabs.currentIndex(),
                    autosave_file=self.autosave_path or "")

    def apply_state(self, st):
        w, p = self.w, self.p
        self.pending = dict(st)
        self.autosave_path = st.get("autosave_file") or None
        w.spin_b_step.setValue(int(st.get("step", 1)))
        w.spin_b_w2.setValue(float(st.get("width2_mhz", 10.0)))
        w.spin_b_off2.setValue(float(st.get("offset2_mhz", 0.0)))
        w.chk_roll_clip.setChecked(bool(st.get("roll_clip", False)))
        self.roll_memory = dict(st.get("roll") or {})
        i = w.cmb_b_init.findData(st.get("init_mode", "prev"))
        w.cmb_b_init.setCurrentIndex(max(i, 0))
        w.chk_b_bound.setChecked(bool(st.get("bound_freq", True)))
        w.spin_b_r2.setValue(float(st.get("r2_min", 0.9)))
        w.chk_b_stop.setChecked(bool(st.get("stop_on_fail", False)))
        i = p.cmb_x.findData(st.get("x_mode", "track"))
        p.cmb_x.setCurrentIndex(max(i, 0))
        p.chk_ok_only.setChecked(bool(st.get("ok_only", False)))
        p.chk_track.setChecked(bool(st.get("show_track", True)))
        p.chk_live.setChecked(bool(st.get("live", True)))
        p.chk_skip_fixed.setChecked(bool(st.get("skip_fixed", True)))
        p.chk_err.setChecked(bool(st.get("show_err", True)))
        p.plot_tabs.setCurrentIndex(int(st.get("plot_tab", 0)))

    # ================================================================ 執行
    def start(self, link=None):
        """link：相位連結設定（見 core/batch.run_batch 的 cfg['link']），None = 一般連續擬合"""
        c, w = self.c, self.w
        try:
            if c.data is None or c.current_func() is None:
                raise ValueError("請先載入數據與公式")
            names, units, p0, lo, hi, fixed = w.table.values()
            track = w.cmb_b_track.currentText()
            if track not in names:
                raise ValueError("請選擇追蹤參數")
            ti = names.index(track)
            tfac = fm.freq_factor(units[ti])
            if tfac is None:
                raise ValueError(f"追蹤參數 {track} 的單位「{units[ti]}」不是頻率單位")
            if fixed[ti]:
                raise ValueError("追蹤參數不能設為固定")
            f1, f2 = sorted((w.spin_f1.value() * 1e9, w.spin_f2.value() * 1e9))
            width = f2 - f1
            if width <= 0:
                raise ValueError("遮罩寬度必須大於 0")
            offset = (f1 + f2) / 2 - p0[ti] * tfac
            fr = c.data["frequency"]
            moving = default_moving(units, p0, fr.min(), fr.max())
            moving[ti] = True
            track2 = self.track2_name()
            width2 = offset2 = 0.0
            if track2:
                if track2 == track:
                    raise ValueError("追蹤參數 2 不能與追蹤參數 1 相同")
                t2i = names.index(track2)
                t2fac = fm.freq_factor(units[t2i])
                if t2fac is None:
                    raise ValueError(f"追蹤參數 2（{track2}）的單位「{units[t2i]}」不是頻率單位")
                if fixed[t2i]:
                    raise ValueError("追蹤參數 2 不能設為固定")
                width2 = w.spin_b_w2.value() * 1e6
                offset2 = w.spin_b_off2.value() * 1e6
                moving[t2i] = True
            idxs = batch_indices(w.spin_b_start.value(), w.spin_b_end.value(), w.spin_b_step.value())
            roll = w.roll_table.config()
            if link:
                for key in ("phase", "freq"):
                    if link[key] not in names:
                        raise ValueError(f"相位連結的參數 {link[key]} 不在目前函式中")
                link = dict(link)
                for n, v in (link.pop("fix", None) or {}).items():
                    j = names.index(n)
                    if j in (ti, names.index(link["phase"])):
                        continue
                    p0[j], fixed[j] = float(v), True
                    lo[j], hi[j] = min(lo[j], p0[j]), max(hi[j], p0[j])
                roll.pop(link["phase"], None)
                if link.get("mode") == "soft" and fixed[names.index(link["phase"])]:
                    raise ValueError("軟連結需要相位參數不是固定")
            for n in roll:
                if fixed[names.index(n)]:
                    raise ValueError(f"參數 {n} 設為固定，不能滾動追蹤")
        except Exception as e:
            QMessageBox.critical(w, "無法開始連續擬合", str(e))
            return

        if self.records and self._dirty:
            self.autosave(force=True)
        self.autosave_path = os.path.join(
            c.output_dir(), f"{safe_stem(c.data['path'])}_{w.cmb_func.currentText()}_batch_autosave.csv")

        s_name = w.cmb_s.currentText()
        S_cols = np.ascontiguousarray(c.data["s_params"][s_name][:, idxs])
        yv, yname = c.axis_values()
        axis_vals = yv[idxs]
        func_name = w.cmb_func.currentText()
        cfg = dict(track=track, width_hz=width, offset_hz=offset, moving=moving,
                   track2=track2, width2_hz=width2, offset2_hz=offset2,
                   roll=roll, roll_clip=w.chk_roll_clip.isChecked(),
                   clean=c.clean_cfg() if is_active(c.clean_cfg()) else None,
                   init_mode=w.cmb_b_init.currentData(), bound_freq=w.chk_b_bound.isChecked(),
                   r2_min=w.spin_b_r2.value(), stop_on_fail=w.chk_b_stop.isChecked(),
                   link=link)
        self.meta = dict(file=os.path.basename(c.data["path"]), file_path=c.data["path"],
                         s_name=s_name, axis=yname, scale=w.spin_scale.value(),
                         formula_path=c.formula_path, func=func_name,
                         names=names, units=units, track=track, track2=track2,
                         p0=p0.tolist(), lo=[float(x) for x in lo], hi=[float(x) for x in hi],
                         fixed=fixed.tolist(), opts=c.fit_options(), cfg=cfg,
                         start=idxs[0], end=idxs[-1], step=w.spin_b_step.value(),
                         created=time.strftime("%Y-%m-%d %H:%M:%S"))
        self.records = []
        self._live_row = None
        self._prepare_panel()
        w.batch_progress.setRange(0, len(idxs))
        w.batch_progress.setValue(0)
        self._t_start = time.perf_counter()

        c.engine.submit_batch(c.formula_path, func_name, c.data["frequency"], S_cols, idxs, axis_vals,
                              names, units, p0, lo, hi, fixed, c.fit_options(), cfg)
        w.tabs.setCurrentWidget(self.p)
        c.status(f"{'相位連結' if link else ''}連續擬合中：{len(idxs)} 片（切片 {idxs[0]} → {idxs[-1]}）")

    def _prepare_panel(self):
        m = self.meta
        self.p.clear()
        self.p.set_params(m["names"], m["units"])
        tu = m["units"][m["names"].index(m["track"])]
        h2 = None
        if m.get("track2"):
            tu2 = m["units"][m["names"].index(m["track2"])]
            h2 = f"{m['track2']} [{tu2}]"
        self.p.set_track_header(f"{m['track']} [{tu}]", h2)
        self.p.set_axis_header(m["axis"])
        pend = self.pending or {}
        if pend.get("y_param"):
            i = self.p.cmb_param.findData(pend.pop("y_param"))
            if i >= 0:
                self.p.cmb_param.setCurrentIndex(i)

    def track_value(self, rec, which=1):
        key = self.meta.get("track") if which == 1 else self.meta.get("track2")
        if rec["params"] is None or not key:
            return np.nan
        return float(rec["params"][self.meta["names"].index(key)])

    def track_unit(self, which=1):
        key = self.meta.get("track") if which == 1 else self.meta.get("track2")
        return self.meta["units"][self.meta["names"].index(key)] if key else ""

    def on_slice(self, rec):
        self.records.append(rec)
        row = self.p.add_row(rec, self.track_value(rec),
                             self.track_value(rec, 2) if self.meta.get("track2") else None)
        self._dirty = True
        self.w.batch_progress.setValue(len(self.records))
        n_ok = sum(r["ok"] for r in self.records)
        el = time.perf_counter() - self._t_start
        self.c.status(f"連續擬合：{len(self.records)}/{rec['total']}（成功 {n_ok}）  {el:.1f} s")
        if self.p.chk_live.isChecked():
            self._live_row = row
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def on_finished(self, completed):
        self._refresh_timer.stop()
        self.refresh_views()
        self.autosave(force=True)
        n_ok = sum(r["ok"] for r in self.records)
        el = time.perf_counter() - self._t_start
        msg = "完成" if completed else "已中止"
        if completed and self.w.chk_auto_batch.isChecked() and self.records:
            try:
                path = os.path.join(self.c.output_dir(),
                                    f"{safe_stem(self.meta['file'])}_{self.meta['func']}_batch"
                                    + time.strftime("_%Y%m%d-%H%M%S") + ".csv")
                self.write_export(path)
            except Exception as ex:
                QMessageBox.warning(self.w, "自動匯出失敗", str(ex))
        self.c.status(f"連續擬合{msg}：{len(self.records)} 片，成功 {n_ok}，耗時 {el:.1f} s")
        self.c.update_enabled()

    # ================================================================ 繪圖 / 調閱
    def refresh_views(self):
        self.plot_params()
        self.refresh_track()
        if self._live_row is not None:
            r, self._live_row = self._live_row, None
            self.p.select_row(r)

    def _x_series(self):
        """回傳 (選到的列號, 記錄, x 值, x 標籤)"""
        m = self.meta
        xmode = self.p.cmb_x.currentData()
        names, units = m["names"], m["units"]
        recs = self.records
        rows = list(range(len(recs)))
        if self.p.chk_ok_only.isChecked():
            rows = [i for i in rows if recs[i]["ok"]]
        sel = [recs[i] for i in rows]
        if xmode == "track2" and not m.get("track2"):
            xmode = "track"
        if xmode in ("track", "track2"):
            which = 1 if xmode == "track" else 2
            x = [self.track_value(r, which) for r in sel]
            xl = f"{m['track' if which == 1 else 'track2']} ({self.track_unit(which)})"
        elif xmode == "axis":
            x = [r["axis_val"] for r in sel]
            xl = m["axis"]
        else:
            x = [r["idx"] for r in sel]
            xl = "切片索引"
        return rows, sel, x, xl

    def _y_series(self, key, sel):
        """回傳 (y, err, 標籤)"""
        names, units = self.meta["names"], self.meta["units"]
        if key in names:
            j = names.index(key)
            y = [np.nan if r["params"] is None else r["params"][j] for r in sel]
            err = None
            if self.p.chk_err.isChecked():
                err = [np.nan if r["errors"] is None else r["errors"][j] for r in sel]
            return y, err, (f"{key} ({units[j]})" if units[j] else key)
        if key == "__r2":
            return [r["r2"] for r in sel], None, "R² (dB)"
        if key == "__r2c":
            return [r["r2_complex"] for r in sel], None, "R² (complex)"
        return [r["chi2_red"] for r in sel], None, "χ²_red"

    def plot_params(self):
        if not self.records or self.meta is None:
            return
        rows, sel, x, xl = self._x_series()
        ok = [r["ok"] for r in sel]
        title = f"{self.meta['func']}   {len(self.records)} 片（成功 {sum(r['ok'] for r in self.records)}）"
        sel_row = self.p.selected_row()
        if self.p.all_mode():
            m = self.meta
            fixed = m.get("fixed") or [False] * len(m["names"])
            series = []
            for j, n in enumerate(m["names"]):
                xm = self.p.cmb_x.currentData()
                if (xm == "track" and n == m["track"]) or \
                        (xm == "track2" and m.get("track2") and n == m["track2"]):
                    continue  # 追蹤頻率對自己沒有意義
                if fixed[j] and self.p.chk_skip_fixed.isChecked():
                    continue
                y, err, lab = self._y_series(n, sel)
                series.append(dict(y=y, err=err, label=lab, fixed=fixed[j]))
            y, _, lab = self._y_series("__r2", sel)
            series.append(dict(y=y, err=None, label=lab))
            self.p.all_plot.plot(x, series, ok, rows, xl, sel_row=sel_row, title=title)
        else:
            key = self.p.cmb_param.currentData()
            if key is None:
                return
            y, err, yl = self._y_series(key, sel)
            self.p.param_plot.plot(x, y, err, ok, rows, xl, yl, sel_row=sel_row, title=title)

    def update_selection(self):
        """選取改變時只更新標記，不重畫整張小圖陣列"""
        if self.p.all_mode():
            self.p.all_plot.set_selected(self.p.selected_row())
        else:
            self.plot_params()

    def refresh_track(self):
        dp = self.w.data_plot
        c = self.c
        if (not self.records or self.meta is None or c.data is None
                or not self.p.chk_track.isChecked()
                or os.path.abspath(self.meta["file_path"]) != os.path.abspath(c.data["path"])):
            dp.set_track(None)
            return
        yv, _ = c.axis_values()
        fac = fm.freq_factor(self.track_unit(1)) or 1.0
        recs = [r for r in self.records if r["params"] is not None and r["idx"] < len(yv)]
        if not recs:
            dp.set_track(None)
            return
        f2 = None
        if self.meta.get("track2"):
            fac2 = fm.freq_factor(self.track_unit(2)) or 1.0
            f2 = [self.track_value(r, 2) * fac2 / 1e9 for r in recs]
        dp.set_track([yv[r["idx"]] for r in recs],
                     [self.track_value(r) * fac / 1e9 for r in recs],
                     [r["ok"] for r in recs], f2)

    def _func_for_meta(self):
        c = self.c
        fn = c.funcs.get(self.meta["func"])
        if fn is None and self.meta.get("formula_path") and os.path.exists(self.meta["formula_path"]):
            try:
                fn = fm.get_function(self.meta["formula_path"], self.meta["func"])
            except Exception:
                fn = None
        if fn is not None and fm.param_names(fn) != self.meta["names"]:
            return None
        return fn

    def show_row(self, r):
        if not (0 <= r < len(self.records)):
            return
        rec, m, c = self.records[r], self.meta, self.c
        self.update_selection()
        fp = self.p.fit_plot
        if rec["params"] is None:
            fp.fig.clear()
            fp.fig.text(0.5, 0.5, f"切片 #{rec['idx']} 沒有擬合結果\n{rec['msg']}",
                        ha="center", va="center", fontsize=12)
            fp.canvas.draw_idle()
            return
        fn = self._func_for_meta()
        if c.data is None or m["s_name"] not in c.data["s_params"] or fn is None:
            fp.fig.clear()
            fp.fig.text(0.5, 0.5, "需要載入對應的數據檔與公式才能顯示擬合圖",
                        ha="center", va="center", fontsize=12)
            fp.canvas.draw_idle()
            return
        try:
            f_all = c.data["frequency"]
            s_all = c.data["s_params"][m["s_name"]][:, rec["idx"]]
            win = window_mask(f_all, rec["f1"], rec["f2"], rec.get("f1b"), rec.get("f2b"))
            keep = clean_mask(f_all, s_all, (m.get("cfg") or {}).get("clean"))
            msk = win & keep
            rem = win & ~keep
            f, s = f_all[msk], s_all[msk]
            removed = (f_all[rem], s_all[rem])
            cm, mag = fm.model_to_complex(fn(f, *rec["params"]), len(f))
            status = "OK" if rec["ok"] else rec["msg"]
            trk = f"{m['track']} = {self.track_value(rec):.7g} {self.track_unit(1)}"
            if m.get("track2"):
                trk += f"   {m['track2']} = {self.track_value(rec, 2):.7g} {self.track_unit(2)}"
            fp.plot(f, s, cm, mag, label="Fitted", removed=removed,
                    title=(f"#{rec['idx']}   {m['axis']} = {rec['axis_val']:.6g}   {trk}   "
                           f"R² = {rec['r2']:.5f}   [{status}]"))
            res = dict(rec, mode="-", params=np.asarray(rec["params"]),
                       errors=np.asarray(rec["errors"]))
            meta = dict(func=m["func"], file=m["file"], s_name=m["s_name"], idx=rec["idx"],
                        axis=m["axis"], axis_val=rec["axis_val"], f1=rec["f1"], f2=rec["f2"],
                        f1b=rec.get("f1b"), f2b=rec.get("f2b"), nclean=int(rem.sum()),
                        npts=rec["npts"])
            self.w.txt_result.setPlainText(f"[連續擬合 第 {r} 列]  狀態：{status}\n"
                                           + format_report(meta, m["names"], m["units"], res))
        except Exception as e:
            fp.fig.clear()
            fp.fig.text(0.5, 0.5, f"繪圖失敗：{e}", ha="center", va="center")
            fp.canvas.draw_idle()

    def _selected_record(self):
        r = self.p.selected_row()
        return None if r is None or r >= len(self.records) else self.records[r]

    def selected_to_p0(self):
        rec = self._selected_record()
        if rec is None or rec["params"] is None:
            return
        if self.w.table.names() != self.meta["names"]:
            QMessageBox.warning(self.w, "參數不符", "目前函式的參數與此連續擬合結果不同")
            return
        self.w.table.set_p0(rec["params"])
        self.c.status(f"已將切片 #{rec['idx']} 的擬合值設為初值")

    def goto_selected(self):
        rec = self._selected_record()
        if rec is None or self.c.data is None:
            return
        self.w.spin_idx.setValue(rec["idx"])
        self.c.set_range(rec["f1"] / 1e9, rec["f2"] / 1e9)
        self.w.tabs.setCurrentIndex(0)

    # ================================================================ 存取
    def clear(self):
        self.records = []
        self.meta = None
        self.p.clear()
        self.w.data_plot.set_track(None)
        self._dirty = False
        try:
            if self.autosave_path and os.path.exists(self.autosave_path):
                os.remove(self.autosave_path)
        except OSError:
            pass
        self.c.update_enabled()

    def autosave(self, force=False):
        if not self.records or self.meta is None or not (self._dirty or force) \
                or not self.autosave_path:
            return
        try:
            write_batch_csv(self.autosave_path, self.meta, self.records)
            self._dirty = False
        except Exception:
            traceback.print_exc()

    def export_csv(self):
        if not self.records:
            return
        base = safe_stem(self.meta["file"])
        path, _ = QFileDialog.getSaveFileName(
            self.w, "匯出連續擬合結果",
            os.path.join(self.c.output_dir(), f"{base}_{self.meta['func']}_batch.csv"), "CSV (*.csv)")
        if path:
            self.write_export(path)

    def write_export(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        write_batch_csv(path, self.meta, self.records)
        base_png = os.path.splitext(path)[0]
        # 兩種參數圖都輸出（切到對應分頁重畫後存檔，再切回原分頁）
        cur = self.p.plot_tabs.currentIndex()
        for i, suffix in ((0, "_all_params.png"), (1, "_param.png")):
            self.p.plot_tabs.setCurrentIndex(i)
            self.plot_params()
            self.p.plot_tabs.widget(i).fig.savefig(base_png + suffix, dpi=150)
        self.p.plot_tabs.setCurrentIndex(cur)
        self.c.status(f"已匯出 {path} 與參數圖 PNG")

    def import_csv(self):
        path, _ = QFileDialog.getOpenFileName(self.w, "載入連續擬合結果", self.c.output_dir(create=False),
                                              "CSV (*.csv)")
        if path:
            self.load_csv(path, quiet=False)

    def load_csv(self, path, quiet=True):
        try:
            meta, recs = read_batch_csv(path)
        except Exception as e:
            if not quiet:
                QMessageBox.critical(self.w, "載入失敗", str(e))
            return False
        self.set_records(meta, recs, dirty=path != self.autosave_path)
        if not quiet and self.c.data is not None and \
                os.path.abspath(meta["file_path"]) != os.path.abspath(self.c.data["path"]):
            QMessageBox.information(self.w, "提示",
                                    f"此結果來自 {meta['file']}，與目前數據檔不同；"
                                    "要檢視擬合圖請先載入該數據檔。")
        self.c.status(f"已載入連續擬合結果：{len(recs)} 片")
        return True

    def set_records(self, meta, recs, dirty=True, autosave_path=None):
        """直接放入一組結果（載入 CSV、全域擬合結果轉入時使用）"""
        if self.records and self._dirty:
            self.autosave(force=True)
        if autosave_path:
            self.autosave_path = autosave_path
        self.meta, self.records = meta, recs
        self._prepare_panel()
        for r in recs:
            self.p.add_row(r, self.track_value(r),
                           self.track_value(r, 2) if meta.get("track2") else None)
        self._dirty = dirty
        self.w.batch_progress.setRange(0, max(1, len(recs)))
        self.w.batch_progress.setValue(len(recs))
        self.refresh_views()
        if recs:
            self.p.select_row(len(recs) - 1)
        self.c.update_enabled()
        ph = getattr(self.c, "phase", None)
        if ph is not None:
            ph.on_batch_updated()

    def try_restore_autosave(self):
        """啟動時：數據與公式都就緒後，還原上次（或閃退前）的連續擬合結果"""
        if self.pending is None or not self.pending.get("restore_autosave"):
            return
        if not self.c.funcs:
            return
        if self.c.data is None and self.c.pending_data:
            return  # 等數據載入完成
        self.pending["restore_autosave"] = False
        if self.autosave_path and os.path.exists(self.autosave_path) and not self.records:
            if self.load_csv(self.autosave_path):
                self._dirty = False
