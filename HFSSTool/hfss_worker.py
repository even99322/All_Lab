"""
HFSS 工作程序 (適用 AEDT 2026 R1 / PyAEDT >= 1.5)

為什麼用「獨立程序」而不是 QThread：
    PyAEDT 呼叫 AEDT 時 (特別是 analyze_setup 求解期間) 會長時間佔住 Python 的 GIL，
    同一個程序裡的 Qt 介面執行緒拿不到執行權 → 介面整個卡死。
    改成 multiprocessing 獨立程序後，介面只透過 Queue 收訊息，永遠不會被卡住，
    也可以直接中止工作程序。

任務 (params['task']):
    OPEN / BUILD / SIM_S / SIM_FIELDS / EXPORT_GIF / CLOSE
    PIPELINE  -> 依 params['steps'] 在「同一次 AEDT 連線」中依序執行多個任務

傳給介面的訊息 (放進 Queue 的 tuple):
    ('log', str) / ('stage', str) / ('scale', float) / ('done', ok, task, payload)
"""
import os
import sys
import re
import time

import numpy as np
import pandas as pd

from config import (DEFAULT_AEDT_VERSION, MIN_PYAEDT_FOR_2026R1, S_SETUP_NAME, S_SWEEP_NAME,
                    FIELD_SWEEP_NAME, FIELD_SETUP_NAME)
import yig
from utils import (parse_freq_ghz, parse_freq_list_ghz, fmt_ghz, s_param_short_name,
                   fallback_er, backup_folder, pull_project, pyaedt_version_tuple)


class StepFailed(Exception):
    """某一步驟失敗，終止後續流程。"""


STEP_LABELS = {'OPEN': '開啟專案', 'BUILD': '建模', 'SIM_S': 'S參數模擬', 'SIM_FIELDS': '場型模擬',
               'EXPORT_GIF': 'GIF 匯出', 'CLOSE': '關閉專案', 'PIPELINE': '一鍵全流程', 'YIG': '建立 / 更新 YIG 球',
               'MAP2D': 'YIG 2D 熱圖（偏置 × 頻率）'}
MAP_SETUP, MAP_SWEEP, MAP_PARAM, MAP_VAR = "Setup_YIGMap", "Sweep_YIGMap", "ParametricSetup_YIGMap", "YIG_Hint"
POLL_S = 4.0
START_GRACE_S = 15.0          # 不阻塞求解送出後，最多等這麼久看到「開始算」；呼叫本身就花很久 = 其實已經阻塞算完了


def subplan(step, params):
    """v3.9：每個步驟裡面的小步驟 [(id, 文字)]（介面「執行步驟」清單用）。"""
    yig_on = bool(params.get('yig_enable'))
    if step == 'SIM_S':
        out = [("SIM_S:setup", "設定求解器與掃頻"), ("SIM_S:solve", "AEDT 求解 S 參數（Adaptive ＋ 掃頻）"),
               ("SIM_S:export", "匯出 S 參數（CSV / Touchstone）")]
        if yig_on and params.get('yig_fine_sweep', True):
            out.append(("SIM_S:yig", "YIG：找實際共振、細掃"))
        return out
    if step == 'SIM_FIELDS':
        return [("SIM_FIELDS:solve", "AEDT 求解場型"), ("SIM_FIELDS:plots", "建立場圖")]
    if step == 'MAP2D':
        n = int(params.get('map_points') or 0)
        return [("MAP2D:setup", "設定偏置掃描"), ("MAP2D:solve", f"AEDT 求解 {n} 個偏置"),
                ("MAP2D:export", "匯出 2D 熱圖")]
    if step == 'CLOSE':
        return [("CLOSE", "存檔、關閉並備份")]
    return [(step, STEP_LABELS.get(step, step))]


class _Signal:
    """模仿 pyqtSignal.emit 介面，讓工作程式碼不需要 Qt。"""
    def __init__(self, fn):
        self.emit = fn


class HfssWorker:
    # 會修改專案、需要存檔/備份的任務
    _MODIFYING = {"BUILD", "SIM_S", "SIM_FIELDS", "EXPORT_GIF", "YIG", "MAP2D"}

    def __init__(self, params, emit=None):
        self.params = params
        self._emit = emit or (lambda *msg: print(*msg))
        self._last_export = None
        self.suggested_scale_signal = _Signal(lambda v: self._emit('scale', v))

    # ------------------------------------------------------------------
    # 共用
    # ------------------------------------------------------------------
    def emit_log(self, msg):
        self._emit('log', msg)

    def emit_stage(self, text):
        self._emit('stage', text)

    # ---- v3.9：步驟清單與進度 ----
    def begin(self, sid, label=None):
        """開始一個小步驟（上一個自動算完成）。"""
        cur = getattr(self, "_cur_step", None)
        if cur == sid:
            return
        if cur:
            self._emit('step', cur, 'done', '')
        self._cur_step = sid
        self._emit('step', sid, 'run', label or '')

    def finish_step(self, ok=True, info=''):
        cur = getattr(self, "_cur_step", None)
        if cur:
            self._emit('step', cur, 'done' if ok else 'fail', info)
        self._cur_step = None

    def progress(self, frac=None, text=''):
        """目前小步驟的進度（0–1；None = 不知道）與說明。"""
        self._emit('progress', frac, text)

    def _solve(self, hfss, setup_name, label="求解", total_hint=None):
        """v3.9：AEDT 求解改成不阻塞，每幾秒看還在不在算、讀 Simulation Monitor 顯示進度。
        舊版 PyAEDT 沒有 blocking 參數 → 照舊阻塞（沒有細的進度）。"""
        import aedt_monitor as mon
        p = self.params
        tasks = max(1, int(p.get('hpc_tasks', 1)))
        gpus = int(p.get('hpc_gpus', 0)) or None
        kw = dict(cores=p['hpc_cores'], tasks=tasks, gpus=gpus, use_auto_settings=(tasks == 1))
        desk = getattr(hfss, "odesktop", None)
        t0 = time.time()
        mode = p.get('solve_monitor', 'off')
        if mode not in ('running', 'monitor'):
            # v3.9.1：預設跟 3.8 一樣阻塞等 AEDT 算完（3.9 的不阻塞＋Simulation Monitor 有 AEDT 當掉的回報）
            self.progress(None, f"{label}：AEDT 求解中（進階設定「求解中進度」可以打開細的進度）")
            try:
                ok = hfss.analyze_setup(setup_name, **kw)
            except Exception as e:  # noqa: BLE001
                self._aedt_gone(e)
                return False
            return self._solve_result(hfss, setup_name, ok)
        try:
            ok = hfss.analyze_setup(setup_name, blocking=False, **kw)
            blocked = time.time() - t0 > 5.0
        except TypeError:
            self.progress(None, f"{label}（AEDT 求解中）")
            ok = hfss.analyze_setup(setup_name, **kw)
            return self._solve_result(hfss, setup_name, ok)
        if ok is False:
            return self._solve_result(hfss, setup_name, ok)
        started = False
        saved = False
        while True:
            run = mon.running(desk)
            if run is None:                       # 問不到（舊版 AEDT）：就當它已經阻塞到算完
                break
            if run:
                started = True
            elif started or blocked or time.time() - t0 > START_GRACE_S:
                break
            text = mon.read(desk) if run and mode == 'monitor' and getattr(self, "_monitor_ok", True) else None
            if run and mode == 'monitor' and text is None and getattr(self, "_monitor_ok", True):
                self._monitor_ok = False            # 例如「can't get IGenericMonitoringMgr」：不再問（不然每 4 秒一條錯誤）
                self.emit_log("   ℹ️ 這個 AEDT 不提供 Simulation Monitor（can't get IGenericMonitoringMgr）→ 只顯示還在不在算。")
            info = mon.parse(text or "")
            if text and not saved and info:
                mon.save_sample(text)
                saved = True
            elapsed = int(time.time() - t0)
            frac = None
            if info.get("variation") and info["variation"][1]:
                k, n = info["variation"]
                frac = max(0.0, min(1.0, (k - 1 + (info.get("percent", 0) / 100.0)) / n))
            elif info.get("percent") is not None:
                frac = info["percent"] / 100.0
            desc = mon.describe(info) or "AEDT 求解中"
            self.progress(frac, f"{label}：{desc}（{elapsed // 60}:{elapsed % 60:02d}）")
            time.sleep(POLL_S)
        try:
            hfss.save_project()
        except Exception:
            pass
        return self._solve_result(hfss, setup_name, ok)

    def _aedt_gone(self, e):
        self.emit_log(f"❌ 求解時 AEDT 沒有回應或當掉了：{type(e).__name__}: {e}")
        self.emit_log("   💡 如果 AEDT 跳出「Please report this problem」：先確認記憶體（YIG 球網格、HPC 核心數），"
                      "進階設定「求解中進度」維持「關」。AEDT 產生的錯誤 zip 可以寄給 Ansys 或傳給我們看。")
        raise

    def _solve_result(self, hfss, setup_name, ok):
        if ok is False:
            self.emit_log(f"❌ 求解 {setup_name} 失敗。AEDT 的訊息：")
            self._explain_failure(hfss)
            return False
        return True

    def safe_save(self, hfss):
        try:
            hfss.save_project()
        except Exception as e:
            self.emit_log(f"   ⚠️ 背景存檔超時或被拒: {e}")

    def backup_to_nas(self):
        p = self.params
        if not p.get('enable_sync', True):
            self.emit_log("⏭️ 遠端同步已關閉，模擬結果將僅保留於本地端。")
            return
        if not p.get('storage_dir'):
            self.emit_log("⚠️ 未設定 NAS 路徑，略過備份。")
            return
        self.emit_stage("備份到 NAS")
        self.emit_log(f"🔄 正在將專案資料夾鏡像至 NAS: {p['storage_dir']}")
        ok = backup_folder(p['local_dir'], p['storage_dir'], log=self.emit_log)
        if ok:
            self.emit_log("   ✅ 專案已安全備份至遠端/NAS！")

    def _check_pyaedt(self):
        ver, ver_str = pyaedt_version_tuple()
        aedt_ver = self.params.get('aedt_version', DEFAULT_AEDT_VERSION)
        self.emit_log(f"🐍 PyAEDT {ver_str} ／ 目標 AEDT {aedt_ver}")
        if ver and aedt_ver >= "2026.1" and ver < MIN_PYAEDT_FOR_2026R1:
            need = ".".join(map(str, MIN_PYAEDT_FOR_2026R1))
            self.emit_log(f"   ⚠️ AEDT 2026 R1 需要 PyAEDT >= {need}，目前版本可能無法正常運作。"
                          f"\n      請執行: pip install -U \"pyaedt>={need}\"")

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def run(self):
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except ImportError:
            pass

        task = self.params['task']
        steps = list(self.params.get('steps', [])) if task == 'PIPELINE' else [task]
        payload = {}
        hfss = None
        success = True
        closed = False

        handlers = {
            'OPEN': self.task_open,
            'BUILD': self.task_build,
            'SIM_S': self.run_s_param_sim,
            'SIM_FIELDS': self.run_fields_sim,
            'EXPORT_GIF': self.export_field_gifs,
            'YIG': self.task_yig,
            'MAP2D': self.run_bias_map,
        }

        plan = [("connect", "連線 AEDT、開啟專案")]
        for st in steps:
            if st != 'OPEN':
                plan += subplan(st, self.params)
        self._emit('plan', [list(x) for x in plan])
        try:
            self.emit_log(f"\n▶️ 開始執行任務: [{task}] {'→'.join(steps) if task == 'PIPELINE' else ''}")
            os.makedirs(self.params['local_dir'], exist_ok=True)
            self.begin("connect")
            pull = self.params.get('pull_from')
            if pull:
                self.emit_stage("複製專案到本地")
                pull_project(pull['aedt'], pull['results'], self.params['project_path_local'],
                             self.params['results_local'], log=self.emit_log)
            self.emit_stage("連線 AEDT")
            self._check_pyaedt()
            from ansys.aedt.core import Hfss

            with plain_dll_search():          # v3.7.1：AEDT 不要繼承工具的 DLL 搜尋路徑
                hfss = Hfss(
                    project=self.params['project_path_local'],
                    design=self.params['design_name'],
                    version=self.params.get('aedt_version', DEFAULT_AEDT_VERSION),
                    non_graphical=False,
                    remove_lock=True,
                )
            payload['has_model'] = len(hfss.modeler.object_names) > 0

            modified = False
            for step in steps:
                if step == 'CLOSE':
                    continue
                t0 = time.time()
                self.emit_stage(STEP_LABELS.get(step, step))
                if step != 'OPEN':
                    self.begin(subplan(step, self.params)[0][0])
                if task == 'PIPELINE':
                    self.emit_log(f"\n━━━━━━━━ 步驟 [{step}] ━━━━━━━━")
                result = handlers[step](hfss)
                if result is False:
                    raise StepFailed(f"步驟 [{step}] 未成功完成")
                self.finish_step(True)
                if step in self._MODIFYING:
                    modified = True
                    self.safe_save(hfss)
                m, s = divmod(int(time.time() - t0), 60)
                self.emit_log(f"   ⏱️ [{step}] 耗時 {m} 分 {s} 秒")

            payload['has_model'] = len(hfss.modeler.object_names) > 0

            if 'CLOSE' in steps:
                self.begin("CLOSE")
                self.safe_save(hfss)
                # 先關閉專案 (解除檔案鎖) 再備份，確保拷到 NAS 的檔案是完整一致的
                hfss.close_project(save=True)
                closed = True
                self.backup_to_nas()
                self.emit_log("✅ 專案已儲存、關閉並備份！")
            elif modified:
                self.backup_to_nas()

            self.finish_step(True)
        except StepFailed as e:
            success = False
            self.finish_step(False)
            self.emit_log(f"❌ {e}，流程已停止。")
        except Exception as e:
            success = False
            self.finish_step(False, f"{type(e).__name__}")
            self.emit_log(f"❌ 執行 [{task}] 時發生錯誤: {type(e).__name__}: {e}")
        finally:
            if hfss:
                try:
                    if 'CLOSE' in steps and not closed:
                        hfss.close_project(save=True)
                    hfss.release_desktop(close_projects=False, close_desktop=False)
                except Exception:
                    pass
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except ImportError:
                pass
            self._emit('done', success, task, payload)

    def task_open(self, hfss):
        self.emit_log(f"✅ 已成功連線至本地專案：{hfss.project_name} (解算類型: {hfss.solution_type})")
        try:
            self.emit_log(f"   AEDT 版本: {hfss.desktop_class.aedt_version_id}")
        except Exception:
            pass
        return True

    # ------------------------------------------------------------------
    # 建模
    # ------------------------------------------------------------------
    def task_build(self, hfss):
        p = self.params
        dxf_file = p['dxf_file']
        if not os.path.exists(dxf_file):
            self.emit_log(f"❌ 找不到 DXF 檔案: {dxf_file}，建模終止。")
            return False

        # 1) 事先用純 Python 讀 DXF 圖層，缺圖層時直接報錯 (舊版會靜默建出不完整的模型)
        required = {'MS': ['b', 'g'], 'CPW': ['db', 'ub', 'g']}[p['model_type']]
        try:
            from ansys.aedt.core.generic.file_utils import get_dxf_layers
            self._dxf_layers = set(get_dxf_layers(dxf_file))
            self.emit_log(f"🔎 DXF 圖層: {sorted(self._dxf_layers)}")
            missing = [l for l in required if l not in self._dxf_layers]
            if missing:
                self.emit_log(f"❌ DXF 缺少必要圖層 {missing} (大小寫敏感)，請參考 readme 的命名規範。")
                return False
        except Exception as e:
            self._dxf_layers = None
            self.emit_log(f"   ⚠️ 無法預先讀取 DXF 圖層 ({e})，將直接嘗試匯入。")

        # 2) 材料覆寫 (自訂 εr / tanδ)
        if p.get('er_override') or p.get('tand_override'):
            if self._make_custom_dielectric(hfss) is False:
                return False

        # 3) 檢查材料是否存在於材料庫 (版本升級後材料名稱可能改變)
        for key in ('mat_copper', 'mat_dielectric'):
            try:
                if not hfss.materials.exists_material(p[key]):
                    self.emit_log(f"❌ 材料庫找不到材料「{p[key]}」，請確認 2026 R1 材料庫中的名稱。")
                    return False
            except Exception:
                pass

        # 4) 重新建模前先清除舊的模型與邊界，避免重複物件 / Port 名稱衝突
        self._clear_model(hfss)

        if p['model_type'] == 'MS':
            ok = self.build_ms(hfss)
        else:
            ok = self.build_cpw(hfss)
        if ok is False:
            return False
        self.emit_log("✅ 建模任務與空氣腔設定完成！")
        return True

    def _make_custom_dielectric(self, hfss):
        """複製介電材料並覆寫 εr / tanδ，用於板材批次差異或量測擬合後的修正值。"""
        p = self.params
        base = p['mat_dielectric']
        new_name = re.sub(r"[^A-Za-z0-9_]+", "_", base).strip("_") + "_custom"
        try:
            mat = hfss.materials.exists_material(new_name)
            if not mat:
                if not hfss.materials.exists_material(base):
                    self.emit_log(f"❌ 材料庫找不到基底材料「{base}」。")
                    return False
                mat = hfss.materials.duplicate_material(base, name=new_name)
            if p.get('er_override'):
                mat.permittivity = float(p['er_override'])
            if p.get('tand_override'):
                mat.dielectric_loss_tangent = float(p['tand_override'])
            p['mat_dielectric'] = new_name
            self.emit_log(f"🧪 使用自訂材料 {new_name} (εr={p.get('er_override') or '同基底'}, "
                          f"tanδ={p.get('tand_override') or '同基底'})")
            return True
        except Exception as e:
            self.emit_log(f"❌ 建立自訂材料失敗: {e}")
            return False

    def _clear_model(self, hfss):
        names = list(hfss.modeler.object_names)
        bounds = list(hfss.boundaries)
        if not names and not bounds:
            return
        self.emit_log(f"🧹 清除舊模型 ({len(names)} 個物件, {len(bounds)} 個邊界/激發)...")
        for b in bounds:
            try:
                b.delete()
            except Exception:
                pass
        try:
            for op in list(hfss.mesh.meshoperations):
                if str(op.name).startswith("YIG_"):
                    op.delete()
        except Exception:
            pass
        if names:
            try:
                hfss.modeler.delete(names)
            except Exception as e:
                self.emit_log(f"   ⚠️ 清除物件時發生問題: {e}")

    def _has_layer(self, layer):
        layers = getattr(self, '_dxf_layers', None)
        return True if layers is None else layer in layers

    def import_specific_layer(self, hfss, dxf_path, target_layer):
        """匯入單一圖層，回傳新增的物件名稱。"""
        if not self._has_layer(target_layer):
            return []
        before = set(hfss.modeler.object_names)
        import_params = [
            "NAME:options", "FileName:=", dxf_path, "Scale:=", 0.001,
            "AutoDetectClosed:=", True, "SelfStitch:=", True, "DefeatureGeometry:=", False,
            "ImportMethod:=", 1, "2DSheetBodies:=", True,
            ["NAME:LayerInfo", ["NAME:" + target_layer, "source:=", target_layer, "display_source:=", target_layer,
                                "import:=", True, "dest:=", target_layer, "dest_selected:=", False,
                                "layer_type:=", "signal"]]
        ]
        try:
            hfss.modeler.oeditor.ImportDXF(import_params)
        except Exception as e:
            self.emit_log(f"   ⚠️ 匯入圖層 '{target_layer}' 失敗: {e}")
        hfss.modeler.refresh_all_ids()
        new_objs = sorted(set(hfss.modeler.object_names) - before)
        self.emit_log(f"   • 圖層 '{target_layer}': {len(new_objs)} 個物件")
        return new_objs

    def _extrude_board(self, hfss, boards, bot_prefix):
        """把板材輪廓複製一份：原件往下拉成底銅，複本往上拉成介電層。"""
        p = self.params
        die_objs, bot_objs = [], []
        for i, b_name in enumerate(boards):
            if not hfss.modeler.get_object_from_name(b_name):
                continue
            hfss.modeler.copy(b_name)
            die_name = hfss.modeler.paste()[0]
            hfss.modeler.sweep_along_vector(b_name, [0, 0, -p['thick_bot']])
            bot_obj = hfss.modeler.get_object_from_name(b_name)
            if bot_obj:
                bot_obj.name = f"{bot_prefix}_{i}"
                bot_obj.material_name, bot_obj.color, bot_obj.solve_inside = p['mat_copper'], (255, 255, 0), False
                bot_objs.append(bot_obj.name)
            hfss.modeler.sweep_along_vector(die_name, [0, 0, p['thick_die']])
            die_obj = hfss.modeler.get_object_from_name(die_name)
            if die_obj:
                die_obj.name = f"DIE_Substrate_{i}"
                die_obj.material_name, die_obj.color = p['mat_dielectric'], (0, 255, 0)
                die_obj.transparency, die_obj.solve_inside = 0.75, True
                die_objs.append(die_obj.name)
        return die_objs, bot_objs

    def build_ms(self, hfss):
        self.emit_log("🛠️ 開始執行 MS 建模流程...")
        p = self.params
        dxf_file = p['dxf_file']

        # 優化：每個圖層只匯入一次 (舊版 b、h 各匯入兩次，頂層的 b 匯入後又直接刪掉)
        self.emit_log("📥 [1/3] 匯入 DXF 圖層 (b, h, g)...")
        boards = self.import_specific_layer(hfss, dxf_file, "b")
        holes = self.import_specific_layer(hfss, dxf_file, "h")
        guides = self.import_specific_layer(hfss, dxf_file, "g")
        if not boards:
            self.emit_log("❌ 圖層 b 沒有產生任何封閉輪廓，建模終止。")
            return False
        if holes:
            if guides:
                hfss.modeler.subtract(guides, holes, keep_originals=True)
            hfss.modeler.subtract(boards, holes, keep_originals=False)

        self.emit_log("🧱 [2/3] 生成底層地平面、介電層與頂層訊號線...")
        die_objs, _ = self._extrude_board(hfss, boards, "BOT_Copper")
        if len(die_objs) > 1:
            hfss.modeler.unite(die_objs, keep_originals=False)
        final_die_name = die_objs[0] if die_objs else None

        top_guides_names = []
        valid_guides = [g for g in guides if hfss.modeler.get_object_from_name(g)]
        if valid_guides:
            hfss.modeler.move(valid_guides, [0, 0, p['thick_die']])
            for i, name in enumerate(valid_guides):
                try:
                    hfss.modeler.sweep_along_vector(name, [0, 0, p['thick_top']])
                    obj = hfss.modeler.get_object_from_name(name)
                    if obj:
                        obj.name = f"TOP_Copper_Guide_{i}"
                        obj.material_name, obj.color, obj.solve_inside = p['mat_copper'], (255, 0, 0), False
                        top_guides_names.append(obj.name)
                except Exception as e:
                    self.emit_log(f"   ⚠️ 訊號線 {name} 拉伸失敗: {e}")

        self.emit_log("👁️ [3/3] 已完成 MS 3D 實體建模，準備處理 Wave Ports...")
        final_die_obj = hfss.modeler.get_object_from_name(final_die_name) if final_die_name else None
        return self._assign_ports_and_airbox(hfss, final_die_obj, top_guides_names, None, p)

    def build_cpw(self, hfss):
        self.emit_log("🛠️ 開始執行 CPW 建模流程...")
        p = self.params
        dxf_file = p['dxf_file']

        self.emit_log("📥 [1/4] 匯入 db (下板材圖層) 生成底層地與介電層...")
        d_boards = self.import_specific_layer(hfss, dxf_file, "db")
        if not d_boards:
            self.emit_log("❌ 圖層 db 沒有產生任何封閉輪廓，建模終止。")
            return False
        die_objs, bot_objs = self._extrude_board(hfss, d_boards, "BOT_Copper_GND")
        if len(die_objs) > 1:
            hfss.modeler.unite(die_objs, keep_originals=False)
        if len(bot_objs) > 1:
            hfss.modeler.unite(bot_objs, keep_originals=False)
            bot_objs = [bot_objs[0]]
        final_die_name = die_objs[0] if die_objs else None

        self.emit_log("📥 [2/4] 匯入 ub (上板材圖層) 與 g (微波線圖層)...")
        u_boards = self.import_specific_layer(hfss, dxf_file, "ub")
        guides = self.import_specific_layer(hfss, dxf_file, "g")
        top_gnd_objs, top_signal_objs, top_guides_names = [], [], []
        for layer_list, prefix in [(u_boards, "TOP_Copper_GND"), (guides, "TOP_Copper_Guide")]:
            valid_objs = [obj for obj in layer_list if hfss.modeler.get_object_from_name(obj)]
            if not valid_objs:
                continue
            hfss.modeler.move(valid_objs, [0, 0, p['thick_die']])
            for i, name in enumerate(valid_objs):
                try:
                    hfss.modeler.sweep_along_vector(name, [0, 0, p['thick_top']])
                    obj = hfss.modeler.get_object_from_name(name)
                    if obj:
                        obj.name = f"{prefix}_{i}"
                        obj.material_name, obj.solve_inside = p['mat_copper'], False
                        if "GND" in prefix:
                            obj.color = (255, 165, 0)
                            top_gnd_objs.append(obj.name)
                        else:
                            obj.color = (255, 0, 0)
                            top_signal_objs.append(obj.name)
                            top_guides_names.append(obj.name)
                except Exception as e:
                    self.emit_log(f"   ⚠️ {name} 拉伸失敗: {e}")
        if len(top_gnd_objs) > 1:
            hfss.modeler.unite(top_gnd_objs, keep_originals=False)
            top_gnd_objs = [top_gnd_objs[0]]

        self.emit_log("📥 [3/4] 處理 v (金屬過孔) 與 h (螺絲孔) (批量引擎)...")
        holes = self.import_specific_layer(hfss, dxf_file, "h")
        ass_holes = self.import_specific_layer(hfss, dxf_file, "v")
        total_thick = p['thick_bot'] + p['thick_die'] + p['thick_top']

        def smart_process_vias(raw_list, group_name, color):
            if not raw_list:
                return []
            clean_list = []
            for name in raw_list:
                obj = hfss.modeler.get_object_from_name(name)
                if obj:
                    try:
                        if len(obj.faces) > 0:
                            clean_list.append(name)
                    except Exception:
                        pass
            if not clean_list:
                return []

            try:
                hfss.modeler.move(clean_list, [0, 0, -p['thick_bot']])
            except Exception:
                pass

            bulk_success = False
            try:
                hfss.modeler.oeditor.SweepAlongVector(
                    ["NAME:Selections", "Selections:=", ",".join(clean_list), "NewPartsModelFlag:=", "Model"],
                    ["NAME:VectorPolygonParameters", "DraftAngle:=", "0deg", "DraftType:=", "Round",
                     "CheckFaceFaceIntersection:=", False,
                     "SweepVectorX:=", "0mm", "SweepVectorY:=", "0mm", "SweepVectorZ:=", f"{total_thick}mm"]
                )
                bulk_success = True
            except Exception:
                self.emit_log(f"   ⚠️ {group_name} 批量拉伸遇到 DXF 瑕疵，自動降級為逐一處理...")

            if not bulk_success:
                for name in clean_list:
                    try:
                        hfss.modeler.sweep_along_vector(name, [0, 0, total_thick])
                    except Exception:
                        pass
            hfss.modeler.refresh_all_ids()

            survived = [n for n in clean_list if hfss.modeler.get_object_from_name(n)]
            if not survived:
                return []
            if len(survived) > 1:
                try:
                    hfss.modeler.unite(survived, keep_originals=False)
                    survived = [survived[0]]
                except Exception:
                    pass

            final_objs = []
            for i, name in enumerate(survived):
                obj = hfss.modeler.get_object_from_name(name)
                if obj:
                    new_name = f"{group_name}_{i}" if len(survived) > 1 else group_name
                    obj.name = new_name
                    if color:
                        obj.color = color
                        obj.material_name = p['mat_copper']
                        obj.solve_inside = False
                    final_objs.append(new_name)
            return final_objs

        final_holes = smart_process_vias(holes, "HOLE_Group", None)
        if final_holes:
            targets = ([final_die_name] if final_die_name else []) + bot_objs + top_gnd_objs + top_signal_objs
            if targets:
                try:
                    hfss.modeler.subtract(targets, final_holes, keep_originals=False)
                except Exception as e:
                    self.emit_log(f"   ⚠️ 螺絲孔挖空失敗: {e}")

        final_vias = smart_process_vias(ass_holes, "VIA_Copper_Group", (128, 0, 128))
        if final_vias:
            self.emit_log("🔧 正在鑲嵌過孔群組...")
            targets = ([final_die_name] if final_die_name else []) + bot_objs + top_gnd_objs
            if targets:
                try:
                    hfss.modeler.subtract(targets, final_vias, keep_originals=True)
                    for v_name in final_vias:
                        v_obj = hfss.modeler.get_object_from_name(v_name)
                        if v_obj:
                            v_obj.color = (128, 0, 128)
                    self.emit_log("   ✅ 過孔群組鑲嵌完成！")
                except Exception as e:
                    self.emit_log(f"   ⚠️ 過孔鑲嵌失敗: {e}")

        self.emit_log("👁️ [4/4] 已完成 CPW 3D 實體建模，準備處理 Wave Ports...")
        final_die_obj = hfss.modeler.get_object_from_name(final_die_name) if final_die_name else None
        return self._assign_ports_and_airbox(hfss, final_die_obj, top_guides_names, top_gnd_objs, p)

    def _dielectric_er(self, hfss, mat_name):
        try:
            mat = hfss.materials.exists_material(mat_name)
            if mat:
                er = float(mat.permittivity.value)
                if er > 1.0:        # 介電層 εr ≤ 1 不合理 (例如材料以變數/表達式定義時)，改用後備值
                    return er
        except Exception:
            pass
        return fallback_er(mat_name)

    def _assign_ports_and_airbox(self, hfss, die_obj, top_guides_names, top_gnd_names, p):
        if die_obj is None:
            self.emit_log("❌ 找不到介電層物件，無法自動建立 Wave Port。")
            return False
        hfss.modeler.fit_all()
        port_count, tol = 1, 0.05
        port_directions = set()

        scale = p.get('port_scale', 1.0)
        model_type = p['model_type']
        h = p['thick_die']

        try:
            f_max = parse_freq_ghz(p['freq_stop']) * 1e9
            er = self._dielectric_er(hfss, p['mat_dielectric'])
            lambda_eff = 299792458 / (f_max * (er ** 0.5))
            l_limit = (lambda_eff / 2) * 1000 * 0.95
            self.emit_log(f"   εr = {er:g}，有效半波長上限 ≈ {l_limit:.1f} mm")
        except Exception:
            l_limit = 9999

        # 優化：頂點座標只向 AEDT 查詢一次 (舊版在「每個面 × 每條訊號線」都重新查詢，物件多時非常慢)
        def vertex_positions(names):
            pts = []
            for n in names or []:
                obj = hfss.modeler.get_object_from_name(n)
                if obj:
                    pts.append([list(v.position) for v in obj.vertices])
            return pts

        guide_pts = vertex_positions(top_guides_names)
        gnd_pts = [pt for obj_pts in vertex_positions(top_gnd_names) for pt in obj_pts]
        suggested_scale_emitted = False

        for face in die_obj.faces:
            nx, ny, nz = face.normal
            if abs(nz) > 0.1:
                continue
            face_center = face.center

            orientation_code = "YZ" if abs(nx) > 0.8 else "XZ" if abs(ny) > 0.8 else ""
            if not orientation_code:
                continue
            current_direction = ("+X" if nx > 0 else "-X") if orientation_code == "YZ" else ("+Y" if ny > 0 else "-Y")
            axis = 0 if orientation_code == "YZ" else 1      # 面的法線軸
            span_axis = 1 - axis                              # 面上沿著走線寬度的軸

            for pts in guide_pts:
                trace_coords = [v[span_axis] for v in pts if abs(v[axis] - face_center[axis]) < tol]
                if not trace_coords:
                    continue

                w = 1.0
                if max(trace_coords) - min(trace_coords) > 0.001:
                    w = max(trace_coords) - min(trace_coords)
                trace_center = (max(trace_coords) + min(trace_coords)) / 2
                trace_center_x = face_center[0] if axis == 0 else trace_center
                trace_center_y = trace_center if axis == 0 else face_center[1]

                if model_type == 'MS':
                    base_w = max(5 * w, 3 * h) * 2 + w
                    base_h = 6 * h + p['thick_bot']
                    g_info = "N/A"
                else:
                    g = w
                    gnd_coords = [v[span_axis] for v in gnd_pts if abs(v[axis] - face_center[axis]) < tol]
                    if gnd_coords:
                        sig_min, sig_max = trace_center - w / 2, trace_center + w / 2
                        rights = [c for c in gnd_coords if c > sig_max + 0.001]
                        lefts = [c for c in gnd_coords if c < sig_min - 0.001]
                        g_right = (min(rights) - sig_max) if rights else None
                        g_left = (sig_min - max(lefts)) if lefts else None
                        if g_right and g_left:
                            g = (g_right + g_left) / 2
                        elif g_right:
                            g = g_right
                        elif g_left:
                            g = g_left
                    base_w = 3 * (2 * g + w)
                    base_h = 5 * h + p['thick_bot']
                    g_info = f"{g:.3f}mm"

                current_max = max(base_w, base_h)
                if not suggested_scale_emitted:
                    if current_max > l_limit:
                        self.suggested_scale_signal.emit(round(l_limit / current_max, 2))
                        self.emit_log(f"   ⚠️ [高階模態警告] 理論 Port 尺寸 ({current_max:.1f}mm) 大於有效半波長 ({l_limit:.1f}mm)！")
                    else:
                        self.suggested_scale_signal.emit(1.0)
                    suggested_scale_emitted = True

                target_w = base_w * scale
                h_port = base_h * scale

                if orientation_code == "YZ":
                    origin_pos, cs_plane, rect_dims = [face_center[0], trace_center_y - target_w / 2, -p['thick_bot']], "YZ", [target_w, h_port]
                    int_start, int_end = [float(face_center[0]), float(trace_center_y), 0.0], [float(face_center[0]), float(trace_center_y), float(h)]
                else:
                    origin_pos, cs_plane, rect_dims = [trace_center_x - target_w / 2, face_center[1], -p['thick_bot']], "ZX", [h_port, target_w]
                    int_start, int_end = [float(trace_center_x), float(face_center[1]), 0.0], [float(trace_center_x), float(face_center[1]), float(h)]

                rect = hfss.modeler.create_rectangle(cs_plane, origin_pos, rect_dims, name=f"Port_Rect_{port_count}")
                try:
                    port = hfss.wave_port(assignment=rect.name, integration_line=[int_start, int_end],
                                          name=f"WavePort_{port_count}",
                                          modes=int(p.get('port_modes', 1)),
                                          impedance=float(p.get('port_impedance', 50)),
                                          renormalize=bool(p.get('port_renormalize', True)),
                                          deembed=float(p.get('port_deembed', 0)),
                                          characteristic_impedance=p.get('port_zdef', 'Zpi'))
                    if not port:
                        raise RuntimeError("wave_port 回傳 False")
                    self.emit_log(f"   ✅ Wave Port {port_count} 建立成功 [面: {current_direction}] "
                                  f"(寬:{target_w:.2f}, 高:{h_port:.2f}, Gap:{g_info})")
                except Exception as e:
                    self.emit_log(f"   ❌ Wave Port {port_count} 建立失敗: {e}")

                port_directions.add(current_direction)
                port_count += 1

        if port_count == 1:
            self.emit_log("   ⚠️ 沒有偵測到任何碰到板邊的訊號線，未建立 Wave Port。")

        yig_failed = False
        if p.get('yig_enable'):
            # v3.5：YIG 沒建成也把空氣腔做完（模型能用），最後再回報失敗，修好參數後按「建立 / 更新 YIG 球」即可
            if self._build_yig(hfss, top_guides_names) is False or not self._yig_has_sphere(hfss):
                yig_failed = True
                self.emit_log("   ❌ YIG 球沒有建成（原因見上面）。其他部分照常完成；修正後到 YIG 分頁按「🧲 建立 / 更新 YIG 球」。")
        else:
            self.emit_log("   （YIG 分頁沒有勾「在訊號線上方放置 YIG 球」→ 這次不建 YIG）")

        self.emit_log(f"⚙️ 處理空氣腔設定 (貼齊方向: {sorted(port_directions)})...")
        try:
            boundary = p.get('open_boundary', 'Radiation')
            infinite_gp = bool(p.get('infinite_ground', False))
            hfss.create_open_region(frequency=p['freq_open'], boundary=boundary,
                                    apply_infinite_ground=infinite_gp, gp_axis="-z")
            self.emit_log(f"   邊界: {boundary}{' + 無限大地平面 (-Z)' if infinite_gp else ''}")
            hfss.modeler.refresh_all_ids()
            matched_names = hfss.modeler.get_matched_object_name("Radiating*") or \
                hfss.modeler.get_matched_object_name("Region*")
            if matched_names:
                region_obj_name = matched_names[0]
                hfss.modeler[region_obj_name].material_name = "air"
                hfss.modeler[region_obj_name].solve_inside = True
                padding_props = [[f"NAME:{d} Padding Data", "Value:=", "0mm"]
                                 for d in ["-X", "+X", "-Y", "+Y"] if d in port_directions]
                if padding_props:
                    try:
                        hfss.modeler.oeditor.ChangeProperty([
                            "NAME:AllTabs", ["NAME:Geometry3DCmdTab",
                                             ["NAME:PropServers", f"{region_obj_name}:CreateRegion:1"],
                                             ["NAME:ChangedProps"] + padding_props]
                        ])
                        self.emit_log("   ✅ 空氣腔 Padding 已變更為 0mm (Port 面貼齊邊界)。")
                    except Exception as e:
                        self.emit_log(f"   ⚠️ 空氣腔 Padding 修改失敗: {e}")
        except Exception as e:
            self.emit_log(f"   ❌ 空氣腔建立失敗: {e}")
        return not yig_failed

    # ------------------------------------------------------------------
    # YIG 鐵磁共振球
    # ------------------------------------------------------------------
    def task_yig(self, hfss):
        """v3.4：在已經建好的模型上建立（或換新）YIG 球：刪掉舊的球、偏置與網格設定，用 YIG 分頁目前的參數重建。"""
        if not self.params.get('yig_enable'):
            self.emit_log("❌ 請先勾選「在訊號線上方放置 YIG 球」。")
            return False
        names = list(hfss.modeler.object_names)
        guides = [n for n in names if n.startswith("TOP_Copper_Guide")]
        if not guides:
            self.emit_log("❌ 專案裡找不到訊號線（TOP_Copper_Guide_*）：請先「建立 3D 模型」。")
            return False
        if yig.YIG_OBJECT in names:
            self.emit_log("   移除舊的 YIG 球與設定…")
            try:
                for bnd in list(hfss.boundaries):
                    if bnd.name == yig.YIG_BIAS:
                        bnd.delete()
            except Exception:
                pass
            try:
                for op in list(hfss.mesh.meshoperations):
                    if str(op.name).startswith("YIG_"):
                        op.delete()
            except Exception:
                pass
            try:
                hfss.modeler.delete([yig.YIG_OBJECT])
            except Exception as e:
                self.emit_log(f"❌ 刪不掉舊的 YIG 球: {e}")
                return False
        ok = self._build_yig(hfss, guides)
        if ok is not False and not self._yig_has_sphere(hfss):
            self.emit_log(f"❌ AEDT 回報成功，但專案裡找不到 {yig.YIG_OBJECT}。")
            ok = False
        if ok is not False:
            self.emit_log("✅ YIG 球已建立 / 更新。要算共振請再跑「S 參數模擬」（Kittel 頻點與細掃會自動加上）。")
        return ok

    def _yig_fields(self):
        p = self.params
        return yig.bias_fields(p['yig_bias_value'], p['yig_bias_mode'], p['yig_ms_t'], p.get('yig_g', 2.0))

    def _build_yig(self, hfss, guide_names):
        p = self.params
        self.emit_log("🧲 建立 YIG 球...")
        # 1) 位置：預設放在訊號線中央正上方
        bbox = None
        for n in guide_names or []:
            obj = hfss.modeler.get_object_from_name(n)
            if not obj:
                continue
            try:
                bb = [float(v) for v in obj.bounding_box][:6]
            except Exception:
                continue
            bbox = bb if bbox is None else [min(bbox[0], bb[0]), min(bbox[1], bb[1]), min(bbox[2], bb[2]),
                                            max(bbox[3], bb[3]), max(bbox[4], bb[4]), max(bbox[5], bb[5])]
        if p.get('yig_pos_mode', 'auto') == 'auto' and not bbox:
            self.emit_log("   ⚠️ 找不到訊號線範圍，改用手動座標。")
        center, r = yig.sphere_center(p, bbox)

        # 2) 材料 (語法取自 AEDT 2026.1 錄製腳本)
        try:
            dm = hfss.oproject.GetDefinitionManager()
            g = float(p.get('yig_g', 2.0))
            args = yig.material_args(yig.YIG_MATERIAL, p['yig_er'], p['yig_tand'], p['yig_ms_t'],
                                     p['yig_dh_oe'], p.get('yig_dh_freq'), g=g)
            # v3.6.1：AddMaterial 遇到同名不會報錯，而是另外建 YIG_auto0、YIG_auto1…（球還是用舊參數的那個）→ 先問有沒有
            exists = False
            try:
                exists = bool(dm.DoesMaterialExist(yig.YIG_MATERIAL))
            except Exception:
                exists = yig.YIG_MATERIAL.lower() in [m.lower() for m in getattr(hfss.materials, "material_keys", {})]
            def put(a):
                if exists:
                    dm.EditMaterial(yig.YIG_MATERIAL, a)     # 已經有 (重新建模 / 更新 YIG) → 改成目前參數
                else:
                    dm.AddMaterial(a)
            try:
                put(args)
                self.emit_log(f"   材料：4πMs {p['yig_ms_t']:g} T、g {g:g}、ΔH {p['yig_dh_oe']:g} Oe"
                              + (f" @ {p['yig_dh_freq']:g} GHz" if p.get('yig_dh_freq') else "")
                              + f"、εr {p['yig_er']:g}、tanδ {p['yig_tand']:g}")
            except Exception as e:
                # v3.7：這版 AEDT 不認得 lande_G_factor → 不帶 g（HFSS 用 2.0）；Kittel 試算也要用 2.0 才對得起來
                put(yig.material_args(yig.YIG_MATERIAL, p['yig_er'], p['yig_tand'], p['yig_ms_t'],
                                      p['yig_dh_oe'], p.get('yig_dh_freq')))
                if abs(g - 2.0) > 1e-9:
                    self.emit_log(f"   ⚠️ AEDT 沒接受 Landé g（{e}）→ HFSS 用 g = 2.0。請把 YIG 分頁的 g 改成 2.0，"
                                  f"Kittel 頻點與細掃才會對準 HFSS 的共振（差約 {abs(g / 2 - 1) * 100:.2f}%）。")
        except Exception as e:
            self.emit_log(f"❌ 建立 YIG 材料失敗: {e}")
            return False

        # 3) 球體
        try:
            sph = hfss.modeler.create_sphere(origin=center, radius=r, name=yig.YIG_OBJECT)
            if not sph:
                raise RuntimeError("create_sphere 回傳 False（請看 AEDT Message Manager）")
            name = sph.name
            hfss.modeler.oeditor.ChangeProperty(yig.set_material_args(name, yig.YIG_MATERIAL))
            sph.color = (90, 60, 160)
            sph.transparency = 0.2
        except Exception as e:
            self.emit_log(f"❌ 建立 YIG 球失敗: {e}")
            return False

        # 4) 偏置磁場
        fields = self._yig_fields()
        if fields['H_int'] <= 0:
            self.emit_log(f"❌ 偏置場不足以飽和 YIG (內部場 {fields['H_int']:.0f} A/m ≤ 0)，請提高偏置場。")
            return False
        try:
            hfss.odesign.GetModule("BoundarySetup").AssignMagneticBias(
                yig.bias_args(name, fields['H_int'], p['yig_bias_dir']))
        except Exception as e:
            self.emit_log(f"❌ 設定偏置磁場失敗: {e}")
            return False

        # 5) 網格：球體曲面元素 + 邊長上限（v3.6.1：空白 = 直徑/8；估計四面體數，太多就警告，並限制最多加幾個元素）
        edge = p.get('yig_mesh_mm') or yig.auto_mesh_mm(p['yig_diameter'])
        est = yig.mesh_estimate(p['yig_diameter'], edge)
        self.emit_log(f"   球體網格邊長 {edge:g} mm{'（自動）' if not p.get('yig_mesh_mm') else ''}，估計約 {est:,} 個四面體")
        if est > yig.MESH_WARN_TETS:
            self.emit_log(f"   ⚠️ 球體網格太細：估計 {est:,} 個四面體，很可能記憶體不夠（Out of memory）。"
                          f"建議邊長 ≥ {yig.mesh_for_tets(p['yig_diameter'], 15000):g} mm；這次先限制最多加 "
                          f"{yig.MESH_MAX_ELEMENTS:,} 個元素。")
        try:
            hfss.mesh.assign_length_mesh([name], inside_selection=True,
                                         maximum_length=f"{edge:g}mm", maximum_elements=yig.MESH_MAX_ELEMENTS,
                                         name=yig.YIG_MESH)
            hfss.mesh.assign_curvilinear_elements([name], enable=True, name=yig.YIG_CURV)
        except Exception as e:
            self.emit_log(f"   ⚠️ YIG 網格設定失敗 (不影響建模): {e}")

        self.emit_log(f"   ✅ YIG 球 ⌀{2 * r:g} mm，球心 ({center[0]:.3f}, {center[1]:.3f}, {center[2]:.3f}) mm，"
                      f"離走線 {p['yig_gap']:g} mm")
        self.emit_log(f"   偏置 {p['yig_bias_dir']}：外加 {fields['B_ext_mT']:.1f} mT → 內部場 {fields['H_int']:.0f} A/m；"
                      f"Kittel ≈ {fields['f_kittel']:.4f} GHz，線寬 ≈ {yig.linewidth_mhz(p['yig_dh_oe']):.2f} MHz")
        return True

    def _yig_has_sphere(self, hfss):
        try:
            hfss.modeler.refresh_all_ids()
        except Exception:
            pass
        return yig.YIG_OBJECT in hfss.modeler.object_names

    # ------------------------------------------------------------------
    # 模擬設定
    # ------------------------------------------------------------------
    def _create_adaptive_setup(self, hfss, name, mode, freqs_text):
        """用 PyAEDT 官方方法建立 Setup。

        舊版用 EditSetup 塞 'OrderOfBasis' / 'MaxRefinePerPass' / 'Broadband' / 'MultiFreqs'，
        這些 key 與 AEDT 實際使用的 'BasisOrder' / 'PercentRefinement' / 'BroadBand' /
        'MultipleAdaptiveFreqsSetup' 不符，很可能被 AEDT 忽略。
        """
        p = self.params
        if name in hfss.setup_names:
            hfss.delete_setup(name)
        setup = hfss.create_setup(name)

        if mode == 'Single':
            ok = setup.enable_adaptive_setup_single(freq=fmt_ghz(parse_freq_ghz(freqs_text)),
                                                   max_passes=p['max_passes'], max_delta_s=p['max_deltas'])
        elif mode == 'Multi-Frequencies':
            freqs = [fmt_ghz(f) for f in parse_freq_list_ghz(freqs_text)]
            ok = setup.enable_adaptive_setup_multifrequency(frequencies=freqs, max_delta_s=p['max_deltas'])
        else:  # Broadband
            ok = setup.enable_adaptive_setup_broadband(
                low_frequency=fmt_ghz(parse_freq_ghz(p['freq_start'])),
                high_frequency=fmt_ghz(parse_freq_ghz(p['freq_stop'])),
                max_passes=p['max_passes'], max_delta_s=p['max_deltas'])
        if ok is False:
            self.emit_log(f"   ⚠️ Adaptive 模式 {mode} 設定失敗，將使用預設值。")

        setup.auto_update = False
        setup.props["MaximumPasses"] = p['max_passes']
        setup.props["MaxDeltaS"] = p['max_deltas']
        setup.props["MinimumConvergedPasses"] = p['min_converged']
        setup.props["PercentRefinement"] = p['max_refine']
        setup.props["BasisOrder"] = int(p.get('basis_order', -1))   # -1 Mixed / 0 Zero / 1 First / 2 Second
        setup.props["MinimumPasses"] = int(p.get('min_passes', 1))
        setup.props["DoLambdaRefine"] = bool(p.get('lambda_refine', True))
        if p.get('lambda_refine', True):
            setup.props["SetLambdaTarget"] = True
            setup.props["Target"] = float(p.get('lambda_target', 0.3333))
        setup.props["UseIterativeSolver"] = bool(p.get('iterative_solver', False))
        setup.auto_update = True
        setup.update()
        return setup

    def _analyze(self, hfss, setup_name):
        p = self.params
        tasks = max(1, int(p.get('hpc_tasks', 1)))
        gpus = int(p.get('hpc_gpus', 0)) or None
        # tasks=1 時交給 AEDT 自動分配；使用者指定多任務時則關掉自動設定，確保設定真的生效
        ok = hfss.analyze_setup(setup_name, cores=p['hpc_cores'], tasks=tasks, gpus=gpus,
                                use_auto_settings=(tasks == 1))
        if ok is False:
            self.emit_log(f"❌ 求解 {setup_name} 失敗。AEDT 的訊息：")
            self._explain_failure(hfss)
        return ok

    def _explain_failure(self, hfss):
        """v3.6.1：把 AEDT Message Manager 最後的錯誤 / 警告抓到工具記錄裡，常見原因給建議。"""
        msgs = []
        try:
            msgs = list(hfss.odesktop.GetMessages(hfss.project_name, hfss.design_name, 1) or [])
        except Exception:
            pass
        important = [m for m in msgs if "[error]" in m.lower() or "memory" in m.lower() or "seed refinement" in m.lower()]
        for m in important[-8:]:
            self.emit_log("   " + re.sub(r"\s+", " ", str(m)).strip()[:300])
        text = " ".join(msgs).lower()
        if "memory" in text:
            self.emit_log("   💡 記憶體不夠：")
            if "yig" in text and "seed refinement" in text:
                self.emit_log("      • YIG 球網格太細（上面的 Seed Refinement 估計）→ YIG 分頁「球體網格邊長」改大或留空（自動 = 直徑/8），"
                              "再按「🧲 建立 / 更新 YIG 球」")
            self.emit_log("      • Adaptive 用 Multi-Frequencies（Kittel 頻率自動加入）比 Broadband 省很多")
            self.emit_log("      • 降低 Max Passes / 放寬 Max ΔS；Basis Order 用 First 或 Mixed；必要時減少 HPC 核心數")
        elif not important:
            self.emit_log("   （抓不到 AEDT 訊息，請看 AEDT Message Manager）")

    def run_s_param_sim(self, hfss):
        p = self.params
        adaptive_mode, adaptive_freqs = p['adaptive_mode'], p['adaptive_freqs']
        use_yig = p.get('yig_enable') and self._yig_has_sphere(hfss)
        f_k = self._yig_fields()['f_kittel'] if use_yig else None
        f_kittel = f_k
        if use_yig and p.get('yig_cal'):
            # v3.8：同樣的球 / 位置 / 網格上次模擬出來的共振比 Kittel 公式偏多少 → 這次的頻點與細掃先對準那裡
            f_k = yig.calibrated(f_kittel, p['yig_cal'])
            if abs(f_k - f_kittel) > 1e-9:
                self.emit_log(f"🧲 Kittel 公式 {f_kittel:.4f} GHz；用上次模擬的偏移 "
                              f"({(f_k / f_kittel - 1) * 100:+.2f}%) 修正 → 預估共振 {f_k:.4f} GHz")
        if use_yig:
            f0, f1 = parse_freq_ghz(p['freq_start']), parse_freq_ghz(p['freq_stop'])
            if not (f0 <= f_k <= f1):
                self.emit_log(f"   ⚠️ Kittel 頻率 {f_k:.4f} GHz 不在掃頻範圍 {f0:g}–{f1:g} GHz 內。")
            if p.get('yig_add_adaptive', True):
                if adaptive_mode == 'Single':
                    adaptive_mode = 'Multi-Frequencies'
                    adaptive_freqs = f"{adaptive_freqs}, {fmt_ghz(round(f_k, 6))}"
                    self.emit_log(f"🧲 Adaptive 改為 Multi-Frequencies，加入 Kittel 頻率: {adaptive_freqs}")
                elif adaptive_mode == 'Multi-Frequencies':
                    adaptive_freqs = f"{adaptive_freqs}, {fmt_ghz(round(f_k, 6))}"
                    self.emit_log(f"🧲 Adaptive 頻點加入 Kittel 頻率: {adaptive_freqs}")
                else:
                    self.emit_log("🧲 Broadband 無法指定頻點；高 Q 共振建議改用 Multi-Frequencies。")

        self.begin("SIM_S:setup")
        self.emit_log(f"⚙️ 套用求解器 ({adaptive_mode}) 與掃頻 ({p['sweep_mode']}) 設定...")
        setup = self._create_adaptive_setup(hfss, S_SETUP_NAME, adaptive_mode, adaptive_freqs)

        sweep_mode = p['sweep_mode']
        if use_yig and sweep_mode == 'Fast':
            # v3.5.2：模型有偏置的 ferrite（YIG）時 AEDT 不接受 Fast 掃頻（InsertFrequencySweep 失敗）→ 改 Interpolating
            sweep_mode = 'Interpolating'
            self.emit_log("🧲 模型有 YIG（偏置 ferrite），AEDT 不能用 Fast 掃頻 → 主掃頻改用 Interpolating。")
        sweep = self._add_sweep(setup, S_SWEEP_NAME, sweep_mode, parse_freq_ghz(p['freq_start']),
                                parse_freq_ghz(p['freq_stop']), p['sweep_points'],
                                bool(p.get('sweep_save_fields', False)),   # 預設不存場，避免 Discrete 掃頻存下大量場資料
                                float(p.get('interp_tol', 0.5)))
        if not sweep:
            self.emit_log("❌ 建立掃頻失敗（請看 AEDT Message Manager）。")
            return False
        sweep_mode = sweep._qel_mode
        if sweep_mode == 'Interpolating':
            try:
                sweep.props["EnforcePassivity"] = bool(p.get('enforce_passivity', True))
                sweep.props["EnforceCausality"] = bool(p.get('enforce_causality', False))
                sweep.update()
            except Exception as e:
                self.emit_log(f"   ⚠️ 被動性/因果性設定失敗: {e}")

        kittel_sweep = None
        if use_yig and p.get('yig_fine_sweep', True):
            span = p.get('yig_span_mhz', 50) / 1e3
            fine_type = p.get('yig_fine_type', 'Interpolating')
            if fine_type == 'Fast':
                fine_type = 'Interpolating'
            kittel_sweep = self._add_sweep(setup, yig.KITTEL_SWEEP, fine_type, max(0.001, f_k - span), f_k + span,
                                           int(p.get('yig_fine_points', 1001)), False,
                                           float(p.get('yig_fine_tol', 0.1)))
            if not kittel_sweep:
                self.emit_log("   ⚠️ Kittel 細掃建立失敗，只算主掃頻。")
            else:
                step = 2 * span * 1e3 / max(1, int(p.get('yig_fine_points', 1001)) - 1)
                self.emit_log(f"🧲 Kittel 細掃：{f_k - span:.4f}–{f_k + span:.4f} GHz，"
                              f"{p.get('yig_fine_points')} 點 (間距 {step:.3f} MHz，{kittel_sweep._qel_mode})")

        self.emit_log(f"🚀 開始計算 S 參數 ({p['hpc_cores']} 核心 / {p['hpc_tasks']} 任務"
                      f"{' / ' + str(p['hpc_gpus']) + ' GPU' if p.get('hpc_gpus') else ''})...")
        self.begin("SIM_S:solve")
        if self._solve(hfss, S_SETUP_NAME, "S 參數") is False:
            return False
        self.begin("SIM_S:export")
        ok = self._export_s_params(hfss, S_SETUP_NAME, sweep.name)
        if use_yig:
            if kittel_sweep:
                self.begin("SIM_S:yig")
            self._yig_track(hfss, setup, kittel_sweep, f_kittel, f_k, ok)
        return ok

    def _yig_track(self, hfss, setup, kittel_sweep, f_kittel, f_est, exported):
        """v3.8：從寬頻結果找實際共振；細掃沒蓋到 → 以實際共振為中心重掃一次（沿用同一個網格，不重跑 Adaptive）。"""
        p = self.params
        base, ext = os.path.splitext(p['csv_path'])
        fine_csv = f"{base}_kittel{ext}"
        res = None
        if exported and self._last_export is not None:
            res = yig.find_resonance(self._last_export[0], self._last_export[1], f_est)
        span = p.get('yig_span_mhz', 50) / 1e3
        if res is None:
            self.emit_log(f"🧲 寬頻結果在 {f_est:.4f} GHz ±{yig.SEARCH_FRAC * 100:g}% 內看不出共振"
                          "（太窄或耦合太弱）→ 細掃維持在預估頻率。")
        else:
            d = res['f'] - f_kittel
            self.emit_log(f"🧲 寬頻結果的實際共振 ≈ {res['f']:.4f} GHz（{res['by']}，深 {res['depth']:g} dB）；"
                          f"Kittel 公式 {f_kittel:.4f} GHz，差 {d * 1e3:+.1f} MHz（{d / f_kittel * 100:+.2f}%）")
        if kittel_sweep:
            center = f_est
            if res is not None and p.get('yig_track', True) and abs(res['f'] - f_est) > 0.4 * span:
                center = res['f']
                self.emit_log(f"🧲 細掃沒有蓋到實際共振 → 以 {center:.4f} GHz 為中心重新細掃"
                              f"（{center - span:.4f}–{center + span:.4f} GHz，沿用已收斂的網格）...")
                sw = self._add_sweep(setup, yig.KITTEL_SWEEP, kittel_sweep._qel_mode, max(0.001, center - span),
                                     center + span, int(p.get('yig_fine_points', 1001)), False,
                                     float(p.get('yig_fine_tol', 0.1)))
                if sw:
                    kittel_sweep = sw
                    try:
                        self.progress(None, "重新細掃（沿用網格）")
                        hfss.odesign.Analyze(f"{S_SETUP_NAME} : {sw.name}")
                    except Exception as e:  # noqa: BLE001
                        self.emit_log(f"   ⚠️ 重新細掃失敗：{e}")
                        self._explain_failure(hfss)
            if self._export_s_params(hfss, S_SETUP_NAME, kittel_sweep.name, csv_path=fine_csv) \
                    and res is not None and self._last_export is not None:
                fine = yig.find_resonance(self._last_export[0], self._last_export[1], res['f'],
                                          frac=max(span / max(res['f'], 1e-9), 1e-4))
                if fine is not None:
                    res = fine
                    self.emit_log(f"🧲 細掃的共振 = {res['f']:.5f} GHz（{res['by']}，深 {res['depth']:g} dB）")
        if res is not None:
            self._emit('yig_result', {"f_kittel": f_kittel, "f_res": res['f'], "by": res['by'], "depth": res['depth'],
                                      "ratio": res['f'] / f_kittel, "key": yig.cal_key(p), "time": time.time()})

    # ------------------------------------------------------------------
    # v3.9：YIG 2D 熱圖（偏置 × 頻率）
    # ------------------------------------------------------------------
    def _s_category(self, hfss):
        return "Terminal Solution Data" if "Terminal" in str(hfss.solution_type) else "Modal Solution Data"

    def _s_solution(self, hfss, sweep_full, variations=None):
        """S 參數的 SolutionData 與表達式（全部 S(i,j)）。"""
        category = self._s_category(hfss)
        exprs = []
        try:
            exprs = hfss.post.available_report_quantities(report_category=category, quantities_category="S Parameter",
                                                          solution=sweep_full) or []
        except Exception:
            pass
        kw = dict(expressions=exprs or None, setup_sweep_name=sweep_full, report_category=category)
        if variations:
            kw.update(variations=variations, primary_sweep_variable="Freq")
        data = hfss.post.get_solution_data(**kw)
        if not data:
            return None, []
        return data, [e for e in data.expressions if e.startswith(("S(", "St("))]

    @staticmethod
    def _s_rows(data, exprs):
        """目前這個變數組合：頻率 (GHz) 與 {S21: 複數陣列}。"""
        out, freqs = {}, None
        for expr in exprs:
            x, re_ = data.get_expression_data(expr, formula="real")
            _, im_ = data.get_expression_data(expr, formula="imag")
            if freqs is None:
                freqs = np.asarray(x, dtype=float)
            out[s_param_short_name(expr) or expr] = np.asarray(re_) + 1j * np.asarray(im_)
        if freqs is not None:
            unit = str(getattr(data, "units_sweeps", {}).get("Freq", "GHz")).lower()
            scale = {"hz": 1e-9, "khz": 1e-6, "mhz": 1e-3, "ghz": 1.0}.get(unit, 1e-9 if freqs.max() > 1e6 else 1.0)
            freqs = freqs * scale
        return freqs, out

    def _set_bias(self, hfss, value):
        """偏置換成 value（A/m 數字，或設計變數名稱）：刪掉舊的 YIG_Bias 再建一個。"""
        try:
            for bnd in list(hfss.boundaries):
                if bnd.name == yig.YIG_BIAS:
                    bnd.delete()
        except Exception:
            pass
        hfss.odesign.GetModule("BoundarySetup").AssignMagneticBias(
            yig.bias_args(yig.YIG_OBJECT, value, self.params['yig_bias_dir']))

    def run_bias_map(self, hfss):
        import yigmap
        p = self.params
        if not p.get('yig_enable'):
            self.emit_log("❌ 請先勾選「在訊號線上方放置 YIG 球」。")
            return False
        if not self._yig_has_sphere(hfss):
            self.emit_log("🧲 專案裡還沒有 YIG 球 → 先建立。")
            if self.task_yig(hfss) is False:
                return False
        mode, g = p['yig_bias_mode'], float(p.get('yig_g', 2.0))
        vals = yigmap.bias_points(p['map_start'], p['map_stop'], p['map_points'])
        fl = yigmap.fields_for(vals, mode, p['yig_ms_t'], g)
        if np.any(fl["h_int"] <= 0):
            self.emit_log("❌ 有偏置點的內部場 ≤ 0（YIG 沒飽和）：請提高偏置的起點。")
            return False
        f0, f1 = float(p['map_f0']), float(p['map_f1'])
        nf, n = int(p['map_fpoints']), len(vals)
        unit = "mT" if mode == "external_mT" else "A/m"
        self.emit_log(f"🗺 2D 熱圖：偏置 {vals[0]:g} → {vals[-1]:g} {unit}（{n} 點）、頻率 {f0:g} → {f1:g} GHz（{nf} 點）；"
                      f"Kittel {fl['f_kittel'].min():.4f} → {fl['f_kittel'].max():.4f} GHz")
        # 求解設定：Adaptive 在頭、中、尾三個 Kittel 頻率（範圍內的）
        ks = sorted({round(float(fl['f_kittel'][i]), 6) for i in (0, n // 2, n - 1) if f0 <= fl['f_kittel'][i] <= f1})
        ks = ks or [round((f0 + f1) / 2, 6)]
        setup = self._create_adaptive_setup(hfss, MAP_SETUP, 'Multi-Frequencies', ", ".join(fmt_ghz(f) for f in ks))
        sweep = self._add_sweep(setup, MAP_SWEEP, p.get('map_ftype', 'Interpolating'), f0, f1, nf, False,
                                float(p.get('interp_tol', 0.5)))
        if not sweep:
            self.emit_log("❌ 建立熱圖掃頻失敗。")
            return False
        base = os.path.splitext(p['csv_path'])[0] + "_yigmap"
        mp = yigmap.MapData(vals, mode, fl, np.linspace(f0, f1, nf),
                            {"project": p.get('project_name', ''), "method": p.get('map_method', 'loop'),
                             "g": g, "ms_t": p['yig_ms_t'], "diameter": p.get('yig_diameter'), "gap": p.get('yig_gap'),
                             "direction": p.get('yig_bias_dir')})
        method = p.get('map_method', 'loop')
        ok = False
        if method == 'param':
            ok = self._map_parametric(hfss, mp, base)
            if ok is None:
                self.emit_log("   ⚠️ 參數掃描做不起來 → 改成逐點（每點自己求解）。")
                method = 'loop'
        if method == 'loop':
            ok = self._map_loop(hfss, mp, base)
        # 偏置回到 YIG 分頁目前的值（之後的 S 參數模擬照常）
        try:
            self._set_bias(hfss, self._yig_fields()['H_int'])
        except Exception as e:  # noqa: BLE001
            self.emit_log(f"   ⚠️ 偏置改回原本的值失敗：{e}")
        if not mp.s:
            return False
        self.begin("MAP2D:export")
        path = mp.save(base)
        try:
            self.emit_log(f"✨ 2D 熱圖 CSV：{mp.save_csv(base)}")
            png = mp.save_png(base)
            if png:
                self.emit_log(f"✨ 2D 熱圖圖片：{png}")
        except Exception as e:  # noqa: BLE001
            self.emit_log(f"   ⚠️ CSV / 圖片存檔失敗（npz 不受影響）：{e}")
        self.emit_log(f"✨ 2D 熱圖數據：{path}（{mp.done_rows()}/{n} 個偏置）")
        self._emit('map_result', path)
        return ok is not False

    def _map_parametric(self, hfss, mp, base):
        """Optimetrics 參數掃描（複製網格）。回傳 True / False；None = 建不起來（改逐點）。"""
        import yigmap
        n = len(mp.bias)
        self.begin("MAP2D:setup")
        # v3.9.2：用 PyAEDT 的 parametrics API 建（3.9 自己組的 InsertSetup 參數，AEDT 找不到那個 setup 還會當掉），
        #         建好先確認真的在專案裡，不在就改逐點，不去求解一個不存在的名字。
        try:
            hfss[MAP_VAR] = f"{mp.h_int[0]:.10g}A_per_meter"
            self._set_bias(hfss, MAP_VAR)
            par = getattr(hfss, "parametrics", None)
            if par is None:
                raise RuntimeError("這版 PyAEDT 沒有 parametrics")
            for st in list(getattr(par, "setups", []) or []):
                if st.name == MAP_PARAM:
                    try:
                        st.delete()
                    except Exception:
                        pass
            ps = par.add(MAP_VAR, float(mp.h_int[0]), float(mp.h_int[-1]), n, variation_type="LinearCount",
                         solution=MAP_SETUP, name=MAP_PARAM)
            if not ps:
                raise RuntimeError("parametrics.add 回傳 False")
            data_opt = ps.props.get("ProdOptiSetupDataV2") or {}
            data_opt.update({"SaveFields": False, "CopyMesh": True, "SolveWithCopiedMeshOnly": True})
            ps.props["ProdOptiSetupDataV2"] = data_opt
            ps.update()
            names = []
            try:
                names = list(hfss.odesign.GetModule("Optimetrics").GetSetupNames() or [])
            except Exception:
                names = [x.name for x in getattr(par, "setups", [])]
            if ps.name not in names:
                raise RuntimeError(f"專案裡找不到剛建的 {ps.name}（有：{names}）")
        except Exception as e:  # noqa: BLE001
            self.emit_log(f"   ⚠️ 建立參數掃描失敗：{type(e).__name__}: {e}")
            return None
        self.emit_log(f"🚀 參數掃描 {n} 個偏置（第一個跑 Adaptive，其他沿用同一個網格）...")
        self.begin("MAP2D:solve")
        self.progress(None, f"{n} 個偏置：AEDT 參數掃描中")
        p = self.params
        tasks = max(1, int(p.get('hpc_tasks', 1)))
        try:
            ok = ps.analyze(cores=p['hpc_cores'], tasks=tasks, gpus=int(p.get('hpc_gpus', 0)),
                            use_auto_settings=(tasks == 1))
        except Exception as e:  # noqa: BLE001
            self._aedt_gone(e)
        if ok is False:
            self.emit_log("❌ 參數掃描求解失敗。AEDT 的訊息：")
            self._explain_failure(hfss)
            return False
        self.progress(None, "讀取結果")
        try:
            data, exprs = self._s_solution(hfss, f"{MAP_SETUP} : {MAP_SWEEP}", variations={MAP_VAR: ["All"]})
        except Exception as e:  # noqa: BLE001
            self.emit_log(f"❌ 讀不到參數掃描的結果：{type(e).__name__}: {e}")
            return False
        if data is None or not exprs:
            self.emit_log("❌ 參數掃描沒有 S 參數結果。")
            self._explain_failure(hfss)
            return False
        got = 0
        for i, var in enumerate(data.variations):
            try:
                data.set_active_variation(i)
                h = float(var.get(MAP_VAR))
                freqs, rows = self._s_rows(data, exprs)
                k = mp.nearest(h)
                if len(freqs) != len(mp.freq_ghz):
                    mp.freq_ghz = freqs
                    mp.s = {}
                for name, v in rows.items():
                    mp.set_row(name, k, v)
                got += 1
            except Exception as e:  # noqa: BLE001
                self.emit_log(f"   ⚠️ 第 {i + 1} 個偏置讀取失敗：{e}")
        self.emit_log(f"   讀到 {got}/{n} 個偏置")
        return got > 0

    def _map_loop(self, hfss, mp, base):
        if self.params.get('map_method', 'loop') == 'loop':
            self.begin("MAP2D:setup")
        """逐點：每個偏置改數值、求解、讀結果；每算完一點就存檔（中途停下也有已經算好的部分）。"""
        n = len(mp.bias)
        self.begin("MAP2D:solve")
        t_start = time.time()
        for k in range(n):
            label = f"偏置 {k + 1}/{n}（{mp.b_ext_mT[k]:.2f} mT）"
            self.progress(k / n, label)
            try:
                self._set_bias(hfss, float(mp.h_int[k]))
            except Exception as e:  # noqa: BLE001
                self.emit_log(f"❌ 設定偏置失敗：{e}")
                return False
            if self._solve(hfss, MAP_SETUP, label) is False:
                return k > 0
            try:
                data, exprs = self._s_solution(hfss, f"{MAP_SETUP} : {MAP_SWEEP}")
                freqs, rows = self._s_rows(data, exprs) if data else (None, {})
            except Exception as e:  # noqa: BLE001
                self.emit_log(f"   ⚠️ {label} 讀取失敗：{e}")
                continue
            if not rows:
                self.emit_log(f"   ⚠️ {label} 沒有結果")
                continue
            if len(freqs) != len(mp.freq_ghz) and k == 0:
                mp.freq_ghz = freqs
            for name, v in rows.items():
                mp.set_row(name, k, v)
            mp.save(base)
            self._emit('map_result', base + ".npz")
            per = (time.time() - t_start) / (k + 1)
            self.emit_log(f"   ✅ {label}，平均每點 {per / 60:.1f} 分，預計還要 {per * (n - k - 1) / 60:.0f} 分")
        return True

    def _add_sweep(self, setup, name, mode, f0, f1, points, save_fields, tol):
        """建立掃頻；AEDT 拒絕（InsertFrequencySweep 失敗）時依序退回 Interpolating → Discrete，不讓整個任務中斷。"""
        order = [mode] + [m for m in ("Interpolating", "Discrete") if m != mode]
        for i, m in enumerate(order):
            try:
                try:
                    setup.delete_sweep(name)            # 上一次失敗留下的半成品
                except Exception:
                    pass
                sw = setup.create_frequency_sweep(
                    unit="GHz", start_frequency=f0, stop_frequency=f1, num_of_freq_points=int(points), name=name,
                    sweep_type=m, save_fields=save_fields if m != "Discrete" else False,
                    interpolation_tol=tol, interpolation_max_solutions=int(self.params.get('interp_max_solns', 250)))
                if sw:
                    if i:
                        self.emit_log(f"   ⚠️ AEDT 不接受 {order[0]} 掃頻，{name} 改用 {m}。")
                    sw._qel_mode = m
                    return sw
                err = "回傳 False"
            except Exception as e:  # noqa: BLE001 - GrpcApiError 等
                err = f"{type(e).__name__}: {e}"
            self.emit_log(f"   ⚠️ 建立 {m} 掃頻 {name} 失敗：{err}")
        return None

    def _export_s_params(self, hfss, setup_name, sweep_name, csv_path=None):
        p = self.params
        sweep_full = f"{setup_name} : {sweep_name}"
        category = "Terminal Solution Data" if "Terminal" in str(hfss.solution_type) else "Modal Solution Data"
        self.emit_log("📊 正在向 HFSS 請求 S 參數複數數據...")

        exprs = []
        try:
            exprs = hfss.post.available_report_quantities(report_category=category, quantities_category="S Parameter",
                                                          solution=sweep_full) or []
        except Exception:
            pass
        data = hfss.post.get_solution_data(expressions=exprs or None, setup_sweep_name=sweep_full,
                                           report_category=category)
        if not data:
            self.emit_log("❌ 找不到模擬數據。可能 Sweep 名稱錯誤或計算失敗。AEDT 的訊息：")
            self._explain_failure(hfss)
            return False
        exprs = [e for e in data.expressions if e.startswith(("S(", "St("))]
        if not exprs:
            self.emit_log(f"❌ 解算結果中沒有 S 參數 (取得的表達式: {data.expressions})")
            return False

        clean_data = {}
        freqs = None
        for expr in exprs:
            s_name = s_param_short_name(expr) or expr
            try:
                # 優化：一次查詢同時取得實部與虛部 (舊版要再組 re()/im() 表達式向 AEDT 查第二次)
                x, re_vals = data.get_expression_data(expr, formula="real")
                _, im_vals = data.get_expression_data(expr, formula="imag")
                if freqs is None:
                    freqs = np.asarray(x, dtype=float)
                c = np.asarray(re_vals) + 1j * np.asarray(im_vals)
                clean_data[f"{s_name}_real"] = c.real
                clean_data[f"{s_name}_imag"] = c.imag
                clean_data[f"{s_name}_dB"] = 20 * np.log10(np.abs(c) + 1e-12)
                clean_data[f"{s_name}_phase"] = np.angle(c, deg=True)
            except Exception as e:
                self.emit_log(f"   ⚠️ 提取 {s_name} 失敗: {e}")

        if freqs is None or len(freqs) == 0:
            self.emit_log("❌ 無法提取頻率陣列，請確認 HFSS 求解是否成功。")
            return False
        unit = str(getattr(data, "units_sweeps", {}).get("Freq", "GHz")).lower()
        scale = {"hz": 1e-9, "khz": 1e-6, "mhz": 1e-3, "ghz": 1.0}.get(unit)
        if scale is None:
            scale = 1e-9 if freqs.max() > 1e6 else 1.0
        clean_data = {"Frequency_GHz": freqs * scale, **clean_data}

        csv_path = csv_path or p['csv_path']
        if not csv_path.lower().endswith('.csv'):
            csv_path += '.csv'
        csv_dir = os.path.dirname(csv_path)
        if csv_dir:
            os.makedirs(csv_dir, exist_ok=True)
        pd.DataFrame(clean_data).to_csv(csv_path, index=False)
        self._last_export = (clean_data["Frequency_GHz"], clean_data)
        self.emit_log(f"✨ S 參數 CSV 已導出至: {csv_path}")

        # 同時匯出 Touchstone，可直接給 ADS / scikit-rf / VNA 軟體使用
        try:
            n_ports = len({e[e.find("(") + 1:].split(",")[0].strip() for e in exprs}) or 1
            ts_path = os.path.splitext(csv_path)[0] + f".s{n_ports}p"
            out = hfss.export_touchstone(setup=setup_name, sweep=sweep_name, output_file=ts_path)
            if out:
                self.emit_log(f"✨ Touchstone 已導出至: {out}")
        except Exception as e:
            self.emit_log(f"   ⚠️ Touchstone 匯出失敗 (CSV 不受影響): {e}")
        return True

    # ------------------------------------------------------------------
    # 場型
    # ------------------------------------------------------------------
    def _field_solution(self, hfss):
        """回傳 (setup_name, sweep_name)。優先沿用已求解的 S 參數 Setup 網格。"""
        if self.params.get('field_reuse_mesh', True) and S_SETUP_NAME in hfss.setup_names:
            return S_SETUP_NAME, FIELD_SWEEP_NAME
        return FIELD_SETUP_NAME, FIELD_SWEEP_NAME

    def run_fields_sim(self, hfss):
        p = self.params
        freqs = parse_freq_list_ghz(p['field_freqs'])
        if not freqs:
            self.emit_log("❌ 未指定場型頻率。")
            return False
        setup_name, sweep_name = self._field_solution(hfss)

        if setup_name == S_SETUP_NAME:
            setup = hfss.get_setup(S_SETUP_NAME)
            solved = bool(getattr(setup, "is_solved", False))
            self.emit_log(f"♻️ 沿用 {S_SETUP_NAME} 的自適應網格"
                          f"{' (已收斂，免重新 Adaptive)' if solved else ' (尚未求解，將一併計算)'}")
        else:
            self.emit_log(f"⚙️ 建立場型專用 Setup (Single @ {p['freq_open']})...")
            setup = self._create_adaptive_setup(hfss, FIELD_SETUP_NAME, 'Single', p['freq_open'])

        # 刪掉舊的場型 sweep (舊版用的是每個頻率一個 Sweep_xpxGHz，一併清掉)
        for sw in list(hfss.get_sweeps(setup_name) or []):
            if sw == sweep_name or (sw.startswith("Sweep_") and sw.endswith("GHz")):
                try:
                    setup.delete_sweep(sw)
                except Exception:
                    pass

        sweep = setup.create_single_point_sweep(unit="GHz", freq=freqs, name=sweep_name,
                                                save_single_field=True, save_fields=False)
        if not sweep:
            self.emit_log("❌ 建立場型單點掃頻失敗。")
            return False

        self.emit_log(f"🚀 計算場型 @ {', '.join(fmt_ghz(f) for f in freqs)} ({p['hpc_cores']} 核心)...")
        # AEDT 只會計算尚未求解的部分：已收斂的 Adaptive 與 S 參數掃頻會直接沿用
        self.begin("SIM_FIELDS:solve")
        if self._solve(hfss, setup_name, "場型") is False:
            return False
        self.begin("SIM_FIELDS:plots")
        self.emit_log("✅ 場型數據計算完成！")
        self._create_field_plots(hfss, setup_name, sweep_name, freqs)
        return True

    def _field_target(self, hfss):
        die = [n for n in hfss.modeler.object_names if n.startswith("DIE_Substrate")]
        return die[0] if die else None

    def _create_field_plots(self, hfss, setup_name, sweep_name, freqs):
        target = self._field_target(hfss)
        if not target:
            self.emit_log("   ⚠️ 找不到 DIE_Substrate 物件，略過場圖建立。")
            return []
        solution = f"{setup_name} : {sweep_name}"
        self.emit_log("🎨 建立 E 場與 H 場表面圖...")
        plots = []
        for f in freqs:
            tag = f"{f:g}G"
            for qty, prefix in (("Mag_E", "E"), ("Mag_H", "H")):
                name = f"{prefix}_{tag}"
                try:
                    if name in hfss.post.field_plots:
                        hfss.post.field_plots[name].delete()
                    plot = hfss.post.create_fieldplot_surface(
                        assignment=[target], quantity=qty, setup=solution,
                        intrinsics={"Freq": fmt_ghz(f), "Phase": "0deg"}, plot_name=name)
                    if plot:
                        plots.append((plot, qty, f))
                except Exception as e:
                    self.emit_log(f"      ❌ 建立 {name} 失敗: {e}")
        self.emit_log(f"✅ 已建立 {len(plots)} 張場圖，可在 HFSS 左側 Field Overlays 查看。")
        return plots

    def export_field_gifs(self, hfss):
        """用 AEDT 原生匯圖 + Pillow 組成 GIF，取代舊版以螢幕截圖比對的 RPA。

        舊的 RPA 依賴按鈕截圖，2026 R1 換了新版 GUI 後必然失效；
        新做法完全走 API，不需移動滑鼠，也不怕解析度 / 主題 / 語系改變。
        """
        try:
            from PIL import Image
        except ImportError:
            self.emit_log("❌ 缺少 Pillow，請執行: pip install pillow")
            return False

        p = self.params
        setup_name, sweep_name = self._field_solution(hfss)
        if sweep_name not in (hfss.get_sweeps(setup_name) or []):
            self.emit_log("❌ 找不到場型解，請先執行「場型模擬」。")
            return False
        freqs = parse_freq_list_ghz(p['field_freqs'])
        target = self._field_target(hfss)
        if not target:
            self.emit_log("❌ 找不到 DIE_Substrate 物件。")
            return False

        out_dir = p.get('gif_dir') or p['local_dir']
        os.makedirs(out_dir, exist_ok=True)
        n_frames = max(2, int(p.get('gif_frames', 18)))
        phases = [round(i * 360 / n_frames, 3) for i in range(n_frames)]
        view = p.get('gif_view', 'top')
        solution = f"{setup_name} : {sweep_name}"
        frame_dir = os.path.join(p['local_dir'], "_gif_frames")
        os.makedirs(frame_dir, exist_ok=True)

        for f in freqs:
            for qty, prefix in (("Mag_E", "E"), ("Mag_H", "H")):
                name = f"{prefix}_{f:g}G_anim"
                gif_path = os.path.join(out_dir, f"{prefix}_Field_{f:g}GHz.gif")
                self.emit_log(f"🎞️ 產生 {os.path.basename(gif_path)} ({n_frames} 幀, 視角 {view})...")
                try:
                    if name in hfss.post.field_plots:
                        hfss.post.field_plots[name].delete()
                    plot = hfss.post.create_fieldplot_surface(
                        assignment=[target], quantity=qty, setup=solution,
                        intrinsics={"Freq": fmt_ghz(f), "Phase": "0deg"}, plot_name=name)
                    if not plot:
                        raise RuntimeError("建立場圖失敗")

                    # 固定色階，避免每一幀自動縮放造成動畫閃爍
                    vmax = self._field_max(hfss, prefix, solution, f, target)
                    if vmax:
                        try:
                            self.safe_save(hfss)   # folder_settings 需要從已存檔的專案讀取
                            fs = plot.folder_settings
                            fs.scale_settings.scale_type = "MinMax"
                            mm = fs.scale_settings.scale_settings   # PyAEDT 會回傳內部的 MinMaxScale，需就地修改
                            mm.n_levels, mm.min_value, mm.max_value = 15, 0, vmax
                            fs.update()
                        except Exception as e:
                            self.emit_log(f"   ⚠️ 無法固定色階 ({e})，將使用自動色階。")

                    frames = []
                    for i, ph in enumerate(phases):
                        plot.intrinsics["Phase"] = f"{ph:g}deg"
                        plot.update()
                        fp = os.path.join(frame_dir, f"{name}_{i:03d}.png")
                        if plot.export_image(fp, width=int(p.get('gif_width', 1280)), height=int(p.get('gif_height', 720)),
                                             orientation=view, display_wireframe=False, show_region=False,
                                             show_grid=False, show_ruler=True, show_axis=True):
                            frames.append(fp)
                    if not frames:
                        raise RuntimeError("沒有成功匯出任何影格 (需以 GUI 模式執行 AEDT)")

                    imgs = [Image.open(fp).convert("RGB") for fp in frames]
                    imgs[0].save(gif_path, save_all=True, append_images=imgs[1:],
                                 duration=int(p.get('gif_ms', 80)), loop=0, optimize=True)
                    for fp in frames:
                        try:
                            os.remove(fp)
                        except OSError:
                            pass
                    self.emit_log(f"   ✅ {gif_path}")
                    plot.delete()
                except Exception as e:
                    self.emit_log(f"   ❌ {name} GIF 匯出失敗: {e}")
        return True

    def _field_max(self, hfss, prefix, solution, f, target):
        """場在一個週期內的最大值 = 複數振幅 |E|；取介電層體積內的最大值做為固定色階上限。"""
        intr = {"Freq": fmt_ghz(f), "Phase": "0deg"}
        try:
            v = hfss.post.get_scalar_field_value(f"ComplexMag_{prefix}", "Maximum", solution=solution,
                                                 intrinsics=intr, object_name=target, object_type="volume")
            if v:
                return float(v)
        except Exception:
            pass
        try:   # 後備：取 0° 與 90° 相位的最大值
            vals = []
            for ph in ("0deg", "90deg"):
                intr["Phase"] = ph
                vals.append(float(hfss.post.get_scalar_field_value(f"Mag_{prefix}", "Maximum", solution=solution,
                                                                   intrinsics=dict(intr), object_name=target,
                                                                   object_type="volume")))
            return max(vals)
        except Exception:
            return None


# ======================================================================
# 程序進入點 (multiprocessing, spawn)
# ======================================================================
class plain_dll_search:
    """v3.7.1：打包好的 exe 把自己的 _internal 設成 DLL 搜尋路徑，而且「子程序會繼承」。
    PyAEDT 從這裡啟動的 AEDT（ansysedt.exe）因此可能載入工具資料夾裡的 DLL → AEDT 開著時檔案被占用，
    更新 / 重新安裝會失敗（「HFSS 沒關就裝不了」）。啟動 / 連線 AEDT 的那一段改回系統預設，之後再設回來
    （工具自己之後載入的套件還需要它）。"""

    def __enter__(self):
        self.on = sys.platform == "win32" and getattr(sys, "frozen", False)
        if self.on:
            try:
                import ctypes
                ctypes.windll.kernel32.SetDllDirectoryW(None)
            except Exception:
                self.on = False
        return self

    def __exit__(self, *exc):
        if self.on:
            try:
                import ctypes
                ctypes.windll.kernel32.SetDllDirectoryW(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
            except Exception:
                pass
        return False


def process_entry(params, queue):
    """在獨立程序中執行 HFSS 任務，所有訊息透過 queue 傳回介面。"""
    def emit(*msg):
        try:
            queue.put(msg)
        except Exception:
            pass
    try:
        HfssWorker(params, emit).run()
    except BaseException as e:        # 保證介面一定收得到結束訊號
        emit('log', f"❌ 工作程序異常終止: {type(e).__name__}: {e}")
        emit('done', False, params.get('task', '?'), {})
    # v3.7.1：AEDT 還開著時，gRPC 的背景執行緒會讓這個程序一直不結束（占著工具的檔案 → 不能更新）。
    # 訊息送完就直接結束。
    try:
        queue.close()
        queue.join_thread()
    except Exception:
        pass
    os._exit(0)


def stop_entry(version, queue):
    """中止用：另外連線到 AEDT，要求停止所有正在執行的模擬 (best effort)。"""
    try:
        from ansys.aedt.core import Desktop
        with plain_dll_search():
            d = Desktop(version=version, new_desktop=False, non_graphical=False)
        d.stop_simulations(clean_stop=True)
        d.release_desktop(close_projects=False, close_on_exit=False)
        queue.put(('log', "🛑 已要求 AEDT 停止模擬 (會保留已完成的 pass)。"))
    except Exception as e:
        queue.put(('log', f"⚠️ 無法自動停止 AEDT 模擬 ({e})，請在 AEDT 中手動按 Abort。"))
