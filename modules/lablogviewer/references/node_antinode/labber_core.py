# -*- coding: utf-8 -*-
"""
labber_core.py

從原始 labber_toolkit.py 移植過來的純數據處理邏輯（不含 tkinter 對話框）。
檔案路徑改由呼叫端 (PyQt6 GUI) 透過 QFileDialog 取得後傳入，
因此這裡完全不依賴 tkinter，可以安全地在 Qt 事件迴圈中使用。

對外函式：
    load_labber_data(file_path, log=print)
    get_debackgrounded_data(meas_path, bg_path, mode='-', log=print)

回傳的 dict 結構與原本 labber_toolkit.py 完全相同：
    {
        'filename': str,
        'is_2d': bool,
        'frequency': np.ndarray | None,
        'step_channels': {name: 1D np.ndarray},
        's_params': {short_name: complex np.ndarray},  # shape (n_freq,) 或 (n_freq, n_step)
    }
"""

import os
import traceback

import h5py
import numpy as np


# ==========================================
#  內部輔助函數
# ==========================================
def _extract_step_channels(f, n_step_points, log=print):
    """
    共用邏輯：從 HDF5 檔案中提取符合 n_step_points 長度的外迴圈變數 (如 Current, Voltage)
    """
    channels = {}

    # -----------------------------------------------------------
    # 策略 A: 掃描 'Step config' (Labber 標準結構)
    # -----------------------------------------------------------
    if 'Step config' in f:
        try:
            step_grp = f['Step config']
            for step_name in step_grp.keys():
                item_path = f"Step config/{step_name}/Step items"
                if item_path in f:
                    items = f[item_path][:]
                    if len(items) > 0:
                        config = items[0]
                        if config['n_pts'] == n_step_points:
                            start = config['start']
                            stop = config['stop']
                            axis_data = np.linspace(start, stop, n_step_points)
                            channels[step_name] = axis_data
        except Exception:
            pass

    # -----------------------------------------------------------
    # 策略 B: 掃描 'Traces' (適用於 Log 變數)
    # -----------------------------------------------------------
    if 'Traces' in f:
        try:
            traces = f['Traces']
            for key in traces.keys():
                if "VNA -" in key or "Time stamp" in key:
                    continue
                obj = traces[key]
                if isinstance(obj, h5py.Dataset):
                    if obj.size == n_step_points:
                        d = obj[:].flatten()
                        if key not in channels:
                            channels[key] = d
        except Exception:
            pass

    # -----------------------------------------------------------
    # 策略 C: 掃描 'Data/Data' 主矩陣
    # -----------------------------------------------------------
    if 'Data' in f and 'Channel names' in f['Data'] and 'Data' in f['Data']:
        try:
            raw_names = f['Data']['Channel names'][:]
            main_data = f['Data']['Data'][:]

            channel_names = []
            for row in raw_names:
                name_str = row[0].decode('utf-8') if isinstance(row[0], bytes) else row[0]
                channel_names.append(name_str)

            if main_data.shape[0] == n_step_points:
                for idx, name in enumerate(channel_names):
                    if "Time" not in name and "Step" not in name:
                        if name not in channels:
                            channels[name] = main_data[:, idx]
        except Exception:
            pass

    return channels


# ==========================================
#  主要功能函數
# ==========================================
def load_labber_data(file_path, log=print):
    """
    讀取 Labber HDF5，支援從 Traces 或 Step config 重建外部掃描軸 (如電流)。
    file_path: 使用者於 GUI 選擇的檔案路徑。
    """
    if not file_path or not os.path.isfile(file_path):
        log(">>> 找不到檔案，讀取取消。")
        return None

    filename = os.path.basename(file_path)
    log(f">>> 正在讀取: {filename}")

    result = {
        'filename': filename,
        'is_2d': False,
        'frequency': None,
        'step_channels': {},
        's_params': {},
    }

    try:
        with h5py.File(file_path, 'r') as f:
            if 'Traces' not in f:
                log(f"錯誤: {filename} 缺少 Traces 群組。")
                return None

            traces = f['Traces']

            s_keys = [k for k in traces.keys() if "VNA - S" in k and "_N" not in k and "_t0dt" not in k]

            if not s_keys:
                log("警告: 未找到 S 參數。")
                return result

            ref_key = s_keys[0]
            ref_data = traces[ref_key][:]

            n_freq_points = ref_data.shape[0]
            n_step_points = ref_data.shape[2] if len(ref_data.shape) > 2 else 1

            result['is_2d'] = (n_step_points > 1)

            t0dt_key = ref_key + "_t0dt"
            if t0dt_key in traces:
                t0, dt = traces[t0dt_key][0]
                result['frequency'] = t0 + np.arange(n_freq_points) * dt
            else:
                result['frequency'] = np.arange(n_freq_points)

            for key in s_keys:
                short_name = key.split(' - ')[-1]
                raw = traces[key][:]
                complex_data = raw[:, 0, :] + 1j * raw[:, 1, :]

                if n_step_points == 1:
                    complex_data = complex_data.flatten()

                result['s_params'][short_name] = complex_data

            if result['is_2d']:
                log(f">>> 偵測到 2D 掃描 (Step點數: {n_step_points})，正在搜尋對應軸...")
                result['step_channels'] = _extract_step_channels(f, n_step_points, log=log)
                for k in result['step_channels'].keys():
                    log(f"    -> 找到: {k}")

    except Exception as e:
        log(f"讀取過程發生錯誤: {e}")
        log(traceback.format_exc())
        return None

    log(f">>> 讀取完成。S參數: {list(result['s_params'].keys())}")
    if result['is_2d']:
        log(f">>> 可用的 Step 軸: {list(result['step_channels'].keys())}")

    return result


def get_debackgrounded_data(meas_path, bg_path, mode='-', log=print):
    """
    對 [測量檔] 與 [背景檔] 執行去背運算，並回傳完整的數據結構 (包含 is_2d 與 Step 軸)。
    mode: '-' (相減/相減+1) 或 '/' (相除)
    """
    if not meas_path or not os.path.isfile(meas_path):
        log(">>> 找不到量測檔，去背取消。")
        return None
    if not bg_path or not os.path.isfile(bg_path):
        log(">>> 找不到背景檔，去背取消。")
        return None

    filename = os.path.basename(meas_path)
    log(f">>> 正在處理去背: {filename} (Mode: '{mode}')")

    result = {
        'filename': filename,
        'is_2d': False,
        'frequency': None,
        'step_channels': {},
        's_params': {},
    }

    try:
        with h5py.File(meas_path, 'r') as f_meas, h5py.File(bg_path, 'r') as f_bg:
            if 'Traces' not in f_meas or 'Traces' not in f_bg:
                log("錯誤: 文件結構缺少 Traces。")
                return None

            tr_m = f_meas['Traces']
            tr_b = f_bg['Traces']

            s_keys = [k for k in tr_m.keys() if "VNA - S" in k and "_N" not in k and "_t0dt" not in k]
            if not s_keys:
                return result

            ref_key = s_keys[0]
            ref_data = tr_m[ref_key][:]
            n_step_points = ref_data.shape[2] if len(ref_data.shape) > 2 else 1

            result['is_2d'] = (n_step_points > 1)

            if ref_key + "_t0dt" in tr_m:
                t0, dt = tr_m[ref_key + "_t0dt"][0]
                pts = tr_m[ref_key].shape[0]
                result['frequency'] = t0 + np.arange(pts) * dt

            if result['is_2d']:
                log(">>> 偵測到 2D 結構，正在提取掃描軸...")
                result['step_channels'] = _extract_step_channels(f_meas, n_step_points, log=log)

            for key in s_keys:
                s_name = key.split(' - ')[-1]
                if key not in tr_b:
                    continue

                raw_m = tr_m[key][:]
                raw_b = tr_b[key][:]

                c_meas = raw_m[:, 0, :] + 1j * raw_m[:, 1, :]
                c_bg = raw_b[:, 0, :] + 1j * raw_b[:, 1, :]

                if c_meas.ndim == 2 and c_bg.ndim == 1:
                    c_bg = c_bg[:, np.newaxis]

                c_out = None
                if mode == '/':
                    c_out = np.divide(c_meas, c_bg, out=np.zeros_like(c_meas), where=c_bg != 0)
                elif mode == '-':
                    if len(s_name) == 3 and s_name[1] == s_name[2]:  # S33 (反射)
                        c_out = c_meas - c_bg
                    else:  # S43 (穿透)
                        c_out = 1 + c_meas - c_bg

                if c_out.ndim == 2 and c_out.shape[1] == 1:
                    c_out = c_out.flatten()

                result['s_params'][s_name] = c_out

    except Exception as e:
        log(f"去背過程發生錯誤: {e}")
        log(traceback.format_exc())
        return None

    log(f">>> 去背完成。已處理參數: {list(result['s_params'].keys())}")
    return result
