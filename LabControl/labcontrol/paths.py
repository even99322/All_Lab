"""LAB 設定資料夾：所有設定檔都放在這裡，程式碼裡不放任何實驗室專屬的值。

位置（依序決定）：
    1. 環境變數 LAB_CONTROL_HOME
    2. Windows：C:\\Users\\even9\\LAB（該使用者資料夾存在時）
    3. 其他情況：<使用者家目錄>/LAB

第一次啟動時自動建立，並把 labcontrol/defaults/ 裡的預設檔複製進去；
已存在的檔案永遠不會被覆寫（升級版本不會蓋掉你的設定）。

LAB/
  settings.yaml       App 設定（預設值、單位、存檔規則、Labber、估時…）
  instruments.yaml    儀器清單（位址、型號、安全限制、參數覆寫）
  experiments/        實驗設定（YAML）
  schemes/            量測方案（編輯器存檔的預設位置）
  templates/          範本方案（編輯器「範本」選單列出這裡的檔案）
  plugins/            自訂 driver / hook / procedure（丟 .py 即載入）
  logs/               錯誤記錄
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List, Optional

ENV_HOME = "LAB_CONTROL_HOME"
DEFAULT_WINDOWS_HOME = r"C:\Users\even9\LAB"
DEFAULTS_DIR = Path(__file__).with_name("defaults")

SETTINGS_FILE = "settings.yaml"
INSTRUMENTS_FILE = "instruments.yaml"
SUBDIRS = ("experiments", "schemes", "templates", "plugins", "logs")


def lab_home() -> Path:
    env = os.environ.get(ENV_HOME)
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        p = Path(DEFAULT_WINDOWS_HOME)
        if p.parent.exists():
            return p
    return Path.home() / "LAB"


def lab_path(*parts: str, home: Optional[Path] = None) -> Path:
    return (home or lab_home()).joinpath(*parts)


def ensure_lab_home(home: Optional[Path] = None) -> List[Path]:
    """建立 LAB 資料夾並補上缺少的預設檔。回傳這次新建的檔案。"""
    home = home or lab_home()
    home.mkdir(parents=True, exist_ok=True)
    for d in SUBDIRS:
        (home / d).mkdir(exist_ok=True)
    _migrate_templates(home)
    created: List[Path] = []
    for src in DEFAULTS_DIR.rglob("*"):
        if src.is_dir() or "__pycache__" in src.parts:
            continue
        dst = home / src.relative_to(DEFAULTS_DIR)
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            created.append(dst)
    _migrate_files(home)
    return created


#: 0.0.30 以前內建的範本（0.0.31 起只留 單張 / 2D / 2D電流異步 / 雙層分檔 / 雙層單檔 / flux）
OLD_TEMPLATES = ("01_single_shot", "02_2d", "03_2d_async", "04_n_layer", "2d_file_per_outer", "2d_one_file",
                 "current_power")


def _migrate_templates(home: Path) -> None:
    """LAB/templates 裡舊的內建範本搬到 LAB/templates/舊範本_0.0.30/（不刪；自己做的範本不動），再補上新的。"""
    marker = home / ".migrated_templates_0031"
    if marker.exists():
        return
    try:
        tdir = home / "templates"
        old = [tdir / f"{k}.scheme.yaml" for k in OLD_TEMPLATES]
        old = [f for f in old if f.is_file()]
        if old:
            dst = tdir / "舊範本_0.0.30"
            dst.mkdir(parents=True, exist_ok=True)
            for f in old:
                shutil.move(str(f), str(dst / f.name))
        marker.write_text("templates 0.0.31\n", encoding="utf-8")
    except OSError:
        pass


def _migrate_files(home: Path) -> None:
    """0.0.31：LAB/instruments.yaml 舊預設的電流斜坡 0.5 mA/s 改成 10 mA/s（只改一次，留 .bak 備份）。"""
    marker = home / ".migrated_ramp_0031"
    if marker.exists():
        return
    try:
        from .core.config import migrate_instruments_text

        for name in (INSTRUMENTS_FILE,):
            f = home / name
            if not f.is_file():
                continue
            text = f.read_text(encoding="utf-8")
            new = migrate_instruments_text(text)
            if new != text:
                shutil.copy2(f, f.with_name(f.name + ".bak-0.0.31"))
                f.write_text(new, encoding="utf-8")
        marker.write_text("ramp 0.5 mA/s -> 10 mA/s\n", encoding="utf-8")
    except OSError:
        pass


def open_folder(path: Path) -> None:
    """用系統檔案總管開啟資料夾。"""
    import subprocess
    import sys

    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
