"""
路徑管理

    <程式資料夾>/config/                      設定與紀錄（JSON 一律放這裡）
        fit_gui_session.json (+ .bak)         操作設定（自動保存）
        formula_library.json                  公式庫索引
        formulas/                             公式庫收錄的公式檔
        params/                               「儲存參數設定」的預設位置
        logs/fit_gui_crash.log                閃退紀錄

程式資料夾不可寫入時，改用 ~/.fit_gui/config/。
擬合結果不放在程式資料夾，而是使用者指定的「輸出資料夾」（預設：數據檔旁的 fit_results/）。
"""
import os
import shutil

APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _writable(d):
    try:
        os.makedirs(d, exist_ok=True)
        test = os.path.join(d, ".write_test")
        with open(test, "w") as fh:
            fh.write("")
        os.remove(test)
        return True
    except OSError:
        return False


_CONFIG = None


def config_dir():
    global _CONFIG
    if _CONFIG is None:
        d = os.path.join(APP_DIR, "config")
        if not _writable(d):
            d = os.path.join(os.path.expanduser("~"), ".fit_gui", "config")
            os.makedirs(d, exist_ok=True)
        _CONFIG = d
        _migrate_old_files(d)
    return _CONFIG


def sub_dir(name):
    d = os.path.join(config_dir(), name)
    os.makedirs(d, exist_ok=True)
    return d


def _migrate_old_files(cfg):
    """舊版把 JSON / 紀錄放在程式資料夾，搬到 config/"""
    moves = [("fit_gui_session.json", cfg), ("fit_gui_session.json.bak", cfg),
             ("fit_gui_crash.log", os.path.join(cfg, "logs"))]
    for fn, dst in moves:
        src = os.path.join(APP_DIR, fn)
        if os.path.isfile(src):
            try:
                os.makedirs(dst, exist_ok=True)
                target = os.path.join(dst, fn)
                if not os.path.exists(target):
                    shutil.move(src, target)
            except OSError:
                pass


def default_output_dir(data_path=None):
    """擬合結果預設資料夾：數據檔旁的 fit_results/；沒有數據時用家目錄下的 fit_results/"""
    base = os.path.dirname(os.path.abspath(data_path)) if data_path else os.path.expanduser("~")
    return os.path.join(base, "fit_results")


def ensure_dir(d):
    os.makedirs(d, exist_ok=True)
    return d


def safe_stem(path_or_name):
    stem = os.path.splitext(os.path.basename(str(path_or_name)))[0]
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in stem) or "data"
