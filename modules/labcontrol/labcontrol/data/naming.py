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


def day_folder(root: str | Path, pattern: str = "{yyyy}/{mm}/Data_{mmdd}",
               now: Optional[_dt.datetime] = None) -> Path:
    """舊版規則：<root>/YYYY/MM/Data_MMDD"""
    now = now or _dt.datetime.now()
    return Path(root) / pattern.format(**_fmt_tokens(now))


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
