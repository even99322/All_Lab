"""檔名與資料夾規則（由 sweep_main.py 移植，改成不依賴 UI 的純函式）。"""
from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path
from typing import Optional, Tuple

COLLISION_MODES = ("paren", "underscore", "overwrite")
_LEGACY = {"自動遞增 (括號)": "paren", "自動遞增 (底線)": "underscore", "直接覆寫": "overwrite"}


def _fmt_tokens(now: _dt.datetime) -> dict:
    return dict(yyyy=now.strftime("%Y"), mm=now.strftime("%m"), dd=now.strftime("%d"),
                mmdd=now.strftime("%m%d"), yyyymmdd=now.strftime("%Y%m%d"))


def project_folder_name(name: str) -> str:
    """論文庫專案名稱 → 資料夾名稱（Windows / macOS 都合法）；沒有專案 → 「未分專案」。"""
    n = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name or "").strip(" .")
    return n or "未分專案"


#: 0.0.30：預設存到桌面的「數據集散地」，每個專案一個資料夾（C:\Users\user\Desktop\數據集散地\<專案>\2026\10\Data_1012）
DEFAULT_ROOT = "~/Desktop/數據集散地"
DEFAULT_PATTERN = "{project}/{yyyy}/{mm}/Data_{mmdd}"
#: 舊版的預設值：設定檔裡還是這些值時，換成新的預設（見 settings.migrate）
OLD_DEFAULTS = {"data.root": ("C:/Users/QEL/Desktop/VNA_data",), "data.folder_pattern": ("{yyyy}/{mm}/Data_{mmdd}",),
                # 0.0.31：電流斜坡預設 10 mA/s
                "run_defaults.approach_rate": ("0.5 mA/s",), "run_defaults.park_rate": ("1 mA/s",),
                # 1.0.2：分檔檔名 data_100350mA、data_p1（不再有 _001_）
                "data.split_pattern": ("{stem}_{index:03d}_{label}",)}


def data_root(root: str | Path | None = None) -> Path:
    """資料根目錄（~、%USERPROFILE% 會展開）：參數 > settings.yaml data.root > 預設的桌面「數據集散地」。"""
    if not root:
        from ..settings import setting

        root = setting("data.root", "") or DEFAULT_ROOT
    return Path(os.path.expandvars(os.path.expanduser(str(root))))


def folder_pattern(pattern: str | None = None) -> str:
    if pattern:
        return pattern
    from ..settings import setting

    return str(setting("data.folder_pattern", "") or DEFAULT_PATTERN)


def project_folder(output: dict) -> str:
    """方案的 output → {project} 用的資料夾名稱：選了既有資料夾（output.project_folder）就用它，
    否則用論文庫專案名稱（沒有專案 → 「未分專案」）。"""
    output = output or {}
    chosen = str(output.get("project_folder") or "").strip()
    if chosen:
        return project_folder_name(chosen)
    p = output.get("lab_project") or {}
    return project_folder_name(str(p.get("name") or "") if isinstance(p, dict) else "")


def list_project_folders(root: str | Path | None = None) -> list:
    """資料根目錄裡已有的專案資料夾（不含 . 或 _ 開頭的）。"""
    base = data_root(root)
    try:
        return sorted((d.name for d in base.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))),
                      key=str.casefold)
    except OSError:
        return []


def _folders_file() -> Path:
    from ..paths import lab_path

    return lab_path("project_folders.json")


def remembered_folder(project_id: str) -> str:
    """這台電腦上次替這個論文庫專案選的資料夾（LAB/project_folders.json）；沒有 → ""。"""
    import json

    try:
        return str(json.loads(_folders_file().read_text(encoding="utf-8")).get(str(project_id), ""))
    except (OSError, ValueError, AttributeError):
        return ""


def remember_folder(project_id: str, folder: str) -> None:
    import json

    if not project_id:
        return
    path = _folders_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    if folder:
        data[str(project_id)] = folder
    else:
        data.pop(str(project_id), None)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def day_folder(root: str | Path, pattern: str = DEFAULT_PATTERN,
               now: Optional[_dt.datetime] = None, project: str = "") -> Path:
    """<root>/<pattern>：預設 <root>/<專案>/YYYY/MM/Data_MMDD。{project} = 專案資料夾名稱。"""
    now = now or _dt.datetime.now()
    return data_root(root) / pattern.format(**_fmt_tokens(now), project=project_folder_name(project))


def stale_date_in_filename(name: str, now: Optional[_dt.datetime] = None) -> Optional[Tuple[str, str, str]]:
    """檔名裡的 MMDD 與今天不同 → 回傳 (舊日期, 今天, 建議檔名)；否則 None。UI 決定要不要採用。"""
    now = now or _dt.datetime.now()
    today = now.strftime("%m%d")
    m = re.search(r"(?<!\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)", name)
    if m and m.group(0) != today:
        return m.group(0), today, name.replace(m.group(0), today)
    return None


def unique_filename(folder: str | Path, original: str, mode: str = "underscore") -> str:
    """同名檔案處理：paren → 'x (2).hdf5'、underscore → 'x_2.hdf5'、overwrite → 原名。"""
    mode = _LEGACY.get(mode, mode)
    if mode not in COLLISION_MODES:
        raise ValueError(f"未知的 collision 模式 {mode}")
    if mode == "overwrite":
        return original
    base, ext = os.path.splitext(original)
    while True:
        new_base = re.sub(r"\s*\(\d+\)$", "", base)
        new_base = re.sub(r"_\d+$", "", new_base)
        if new_base == base:
            break
        base = new_base
    fmt = " ({})" if mode == "paren" else "_{}"
    pattern = re.escape(base) + r"(?: \((\d+)\)|_(\d+))" + re.escape(ext) + "$"
    folder = Path(folder)
    max_num = 0
    if folder.is_dir():
        for fname in os.listdir(folder):
            m = re.match(pattern, fname)
            if m:
                max_num = max(max_num, int(m.group(1) or m.group(2)))
    if max_num == 0:
        return f"{base}{fmt.format(1)}{ext}" if (folder / original).exists() else original
    return f"{base}{fmt.format(max_num + 1)}{ext}"
