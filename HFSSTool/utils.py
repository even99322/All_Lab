"""與 HFSS 無關、可單獨測試的共用工具函式。"""
import os
import re
import shutil
import subprocess

_FREQ_UNITS = {"hz": 1e-9, "khz": 1e-6, "mhz": 1e-3, "ghz": 1.0, "thz": 1e3}


def parse_freq_ghz(text, default_unit="ghz"):
    """把 '9GHz'、'9 GHz'、'9000MHz'、'9' 等寫法統一轉成 GHz 浮點數。"""
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip().lower().replace(" ", "")
    m = re.fullmatch(r"([-+]?\d*\.?\d+(?:e[-+]?\d+)?)([a-z]*)", s)
    if not m:
        raise ValueError(f"無法解析頻率: '{text}'")
    value, unit = float(m.group(1)), (m.group(2) or default_unit)
    if unit not in _FREQ_UNITS:
        raise ValueError(f"未知的頻率單位: '{text}'")
    return value * _FREQ_UNITS[unit]


def fmt_ghz(value_ghz):
    """10.0 -> '10GHz'、2.5 -> '2.5GHz' (AEDT 可接受的字串)。"""
    return f"{float(value_ghz):g}GHz"


def parse_freq_list_ghz(text):
    """'2.5, 5.5GHz, 8500MHz' -> [2.5, 5.5, 8.5]"""
    return [parse_freq_ghz(t) for t in str(text).split(",") if t.strip()]


def get_cpu_info():
    """回傳 (實體核心, 邏輯執行緒)。

    舊版用 `wmic`，但 Windows 11 24H2 起 wmic 已預設移除，
    會讓偵測失敗、安全上限被錯降為 2 核。改用 psutil (PyAEDT 的相依套件)。
    """
    logical = os.cpu_count() or 1
    physical = None
    try:
        import psutil
        physical = psutil.cpu_count(logical=False)
    except Exception:
        pass
    if not physical:
        physical = logical
    return physical, logical


def s_param_short_name(expr):
    """'St(WavePort_1_T1,WavePort_2_T1)' / 'S(1,2)' -> 'S12'。無法解析時回傳 None。"""
    m = re.search(r"\((.*)\)", expr)
    if not m:
        return None
    parts = m.group(1).split(",")
    if len(parts) != 2:
        return None
    nums = [re.search(r"\d+", p) for p in parts]
    if not all(nums):
        return None
    return f"S{nums[0].group()}{nums[1].group()}"


# 找不到材料介電常數時的後備表 (舊版的寫死值)
_ER_FALLBACK = [("4003", 3.55), ("4350", 3.66), ("fr4", 4.4), ("fr-4", 4.4)]


def fallback_er(material_name):
    name = (material_name or "").lower()
    for key, er in _ER_FALLBACK:
        if key in name:
            return er
    return 3.5


# robocopy 排除清單：AEDT 執行中的鎖定 / 暫存檔
_ROBO_EXCLUDE = ["/XF", "*.lock", "*.auto", "*.semaphore", "*.asol_priv*", "*.tmp",
                 "/XD", "_gif_frames"]


def _robocopy_cmd(src_dir, dst_dir):
    return ["robocopy", os.path.normpath(src_dir), os.path.normpath(dst_dir),
            "/E", "/MT:16", "/XO", "/NP", "/NDL", "/NJH", "/NJS", "/R:1", "/W:1"] + _ROBO_EXCLUDE


def backup_folder(src_dir, dst_dir, log=print, timeout=600):
    """把整個本地專案資料夾 (.aedt / .aedtresults / CSV / GIF) 鏡像到 NAS。回傳 True/False。"""
    if not os.path.isdir(src_dir):
        log(f"   ⚠️ 找不到本地專案資料夾: {src_dir}")
        return False
    try:
        os.makedirs(dst_dir, exist_ok=True)
        res = subprocess.run(_robocopy_cmd(src_dir, dst_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=timeout)
        # robocopy: 0~7 代表成功 (含有/無複製檔案)，>=8 才是失敗
        if res.returncode >= 8:
            log(f"   ⚠️ robocopy 回傳錯誤碼 {res.returncode}，可能未完整備份。")
            return False
        return True
    except FileNotFoundError:
        log("   ⚠️ 找不到 robocopy (非 Windows 環境?)，略過備份。")
    except subprocess.TimeoutExpired:
        log(f"   ⚠️ 備份程序超時 (超過 {timeout} 秒)，已強制中斷備份。")
    except Exception as e:
        log(f"   ❌ 備份至遠端/NAS 時發生錯誤: {e}")
    return False


def backup_folder_detached(src_dir, dst_dir):
    """關閉程式時使用：在背景啟動 robocopy 後立即返回，不讓介面卡住。"""
    if not os.path.isdir(src_dir):
        return False
    try:
        os.makedirs(dst_dir, exist_ok=True)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(_robocopy_cmd(src_dir, dst_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags)
        return True
    except Exception:
        return False


# =================================================================
# 專案資料夾結構
#   本地: <local_root>\<name>\<name>.aedt   (CSV / Touchstone / GIF 也放在同一資料夾)
#   NAS : <nas_root>\<name>\...              (整個資料夾鏡像)
# =================================================================
PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def is_valid_project_name(name):
    return bool(name) and bool(PROJECT_NAME_RE.fullmatch(name)) and len(name) <= 80


def project_paths(local_root, nas_root, name, csv_name="S_para.csv"):
    local_dir = os.path.join(local_root, name)
    nas_dir = os.path.join(nas_root, name) if nas_root else ""
    csv_name = os.path.basename(csv_name or "S_para.csv")
    if not csv_name.lower().endswith(".csv"):
        csv_name += ".csv"
    return {
        'project_name': name,
        'local_root': local_root,
        'local_dir': local_dir,
        'storage_dir': nas_dir,
        'project_path_local': os.path.join(local_dir, f"{name}.aedt"),
        'results_local': os.path.join(local_dir, f"{name}.aedtresults"),
        'csv_path': os.path.join(local_dir, csv_name),
        'gif_dir': os.path.join(local_dir, "GIF"),
    }


def find_existing_project(local_root, nas_root, name, check_nas=True):
    """依優先順序尋找同名專案，回傳 {'where', 'aedt', 'results'} 或 None。

    也會找 v3.0 以前的舊位置 (<root>\<name>.aedt，沒有獨立資料夾)。
    """
    candidates = [("本地", os.path.join(local_root, name)),
                  ("本地 (舊版位置)", local_root)]
    if check_nas and nas_root:
        candidates += [("NAS", os.path.join(nas_root, name)),
                       ("NAS (舊版位置)", nas_root)]
    for where, folder in candidates:
        aedt = os.path.join(folder, f"{name}.aedt")
        try:
            if os.path.isfile(aedt):
                return {'where': where, 'aedt': aedt, 'results': os.path.join(folder, f"{name}.aedtresults")}
        except OSError:      # NAS 離線等狀況
            continue
    return None


def pull_project(src_aedt, src_results, dst_aedt, dst_results, log=print):
    """把 NAS 或舊位置的專案複製到新的本地專案資料夾。"""
    os.makedirs(os.path.dirname(dst_aedt), exist_ok=True)
    if os.path.normcase(os.path.abspath(src_aedt)) == os.path.normcase(os.path.abspath(dst_aedt)):
        return True
    log(f"📥 複製專案到本地: {src_aedt} → {dst_aedt}")
    shutil.copy2(src_aedt, dst_aedt)
    if os.path.isdir(src_results):
        try:
            res = subprocess.run(_robocopy_cmd(src_results, dst_results), stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                 timeout=1800)
            if res.returncode >= 8:
                raise RuntimeError(f"robocopy 錯誤碼 {res.returncode}")
        except FileNotFoundError:
            shutil.copytree(src_results, dst_results, dirs_exist_ok=True)
    return True


def pyaedt_version_tuple():
    try:
        from importlib.metadata import version
        for dist in ("pyaedt", "ansys-aedt-core"):
            try:
                v = version(dist)
                return tuple(int(x) for x in re.findall(r"\d+", v)[:3]), v
            except Exception:
                continue
    except Exception:
        pass
    return None, "unknown"
