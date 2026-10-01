"""Cross-platform application-data paths for the fitting workspace."""
import os


_CONFIG = None


def config_dir():
    """<data folder>/fitting (per-user, movable from Settings)."""
    global _CONFIG
    if _CONFIG is None:
        from app.core.data_location import fitting_dir

        _CONFIG = str(fitting_dir())
    return _CONFIG


def sub_dir(name):
    d = os.path.join(config_dir(), name)
    os.makedirs(d, exist_ok=True)
    return d


def session_path(identity):
    import hashlib
    key = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()
    folder = sub_dir("sessions")
    return os.path.join(folder, f"{key}.json")


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
