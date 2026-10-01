"""產生發佈檔（在 repo 根目錄執行：python tools/make_release.py [輸出資料夾]）。

    LabControl_v<版本>.zip          Lab Control（Lab APP 程式，整個 repo）
    LabControlHub_v<版本>.zip       NAS 上的 Hub（docker-compose.yml + labhub/ + data/；在 Hub 控制台選這個 zip 更新）
    LabControlMonitor_v<版本>.zip   Lab Control Monitor（Lab APP 程式：main.py + labmonitor/ + 內附的 labhub/）

Monitor 的 Lab APP 資料夾也會放在 <輸出資料夾>/LabControlMonitor/，可以直接推到 Lab APP 的 NAS git。
"""
from __future__ import annotations

import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"__pycache__", ".pytest_cache", "build", "dist", ".git", ".venv", "venv", ".mypy_cache"}
SKIP_FILES = {"build_log.txt", "_design_tmp.md"}


def version() -> str:
    text = (ROOT / "labcontrol" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def _files(base: Path):
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(base)
        if any(part in SKIP_DIRS for part in rel.parts) or p.suffix in (".pyc", ".spec") or p.name in SKIP_FILES:
            continue
        if p.is_file():
            yield p, rel


def zip_dir(src: Path, dst: Path, prefix: str) -> Path:
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(prefix + "/", "")
        for p, rel in _files(src):
            z.write(p, f"{prefix}/{rel.as_posix()}")
    return dst


def hub_zip(out: Path, v: str) -> Path:
    dst = out / f"LabControlHub_v{v}.zip"
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("LabControlHub/", "")
        for name in ("README.md", "docker-compose.yml", "run_hub.bat", "run_hub.sh"):
            z.write(ROOT / "deploy" / "hub" / name, f"LabControlHub/{name}")
        for p, rel in _files(ROOT / "labhub"):
            z.write(p, f"LabControlHub/labhub/{rel.as_posix()}")
        z.writestr("LabControlHub/data/README.txt",
                   "Hub 的資料（節點狀態、量測檔、備份）。這個資料夾要存在，docker-compose 才能掛載。\n")
        z.writestr("LabControlHub/backups/README.txt", "Hub 控制台每次更新前的自動備份。\n")
    return dst


MONITOR_README = """# Lab Control Monitor v{v}

CCU QEL 的 Lab Control 監控程式（Lab APP 程式）：

- **量測節點**：所有裝 Lab Control 的電腦狀態、目前量測；直接命令節點全部連線 / 斷線。
- **儀器**：每台儀器掛在哪台電腦、共用儀器歸誰、連線狀態；遠端讀寫儀器參數。
- **所有電腦**：每台 Lab Control 的版本與上線狀態。
- **Hub 控制台**：更新 / 重建 / 重新啟動 NAS 上的 Hub 網站，每一步即時顯示。

## Hub 更新流程

1. 下載新版 `LabControlHub_v*.zip`（不用解壓縮）
2. 打開 Monitor →「Hub 控制台」分頁
3. 選 zip（或拖進來）→ 按「更新網站」
4. 看著它 停止 → 備份 → 換程式 → 重建 → 啟動 → 確認版本；失敗會自動換回舊版

也有「完全重建」「重新啟動網站」「停止」「啟動」與「回到這版」（備份）。
Monitor 內附同版本的 Hub，也可以按「用這個程式內附的 Hub」直接更新。

## 設定

「⚙ 連線設定」：Hub 網址（依序嘗試，例如 192.168.50.2、100.114.33.20）、token（Hub 的存取碼，
docker-compose.yml 的 LABHUB_TOKEN，不是 NAS 帳號密碼）、控制代理網址（空白 = Hub 同一台主機的 8766）。
設定存在 `%APPDATA%\\LabControlMonitor\\config.json`。
"""


def monitor_app(out: Path, v: str) -> Path:
    app = out / "LabControlMonitor"
    if app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True)
    for pkg in ("labmonitor", "labhub"):
        for p, rel in _files(ROOT / pkg):
            d = app / pkg / rel
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, d)
    (app / "assets").mkdir()
    for n in ("icon.ico", "icon.png"):
        if (ROOT / "assets" / n).exists():
            shutil.copy2(ROOT / "assets" / n, app / "assets" / n)
    (app / "main.py").write_text('"""Lab Control Monitor 進入點（Lab APP 的 .entry）。"""\nimport sys\n\n'
                                 "from labmonitor.app import main\n\nif __name__ == \"__main__\":\n"
                                 "    sys.exit(main())\n", encoding="utf-8")
    (app / "requirements.txt").write_text("# Lab Control Monitor 執行所需套件（Lab APP 以此建立虛擬環境）\n"
                                          "PyQt6>=6.5\npyyaml>=6.0\n", encoding="utf-8")
    (app / ".entry").write_text("main.py\n", encoding="utf-8")
    (app / ".icon").write_text("assets/icon.ico\n", encoding="utf-8")
    (app / ".readme").write_text("README.md\n", encoding="utf-8")
    shutil.copy2(ROOT / ".python-version", app / ".python-version")
    shutil.copy2(ROOT / ".gitattributes", app / ".gitattributes")
    (app / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    (app / "README.md").write_text(MONITOR_README.format(v=v), encoding="utf-8")
    shutil.copy2(ROOT / "CHANGELOG.md", app / "CHANGELOG.md")
    return zip_dir(app, out / f"LabControlMonitor_v{v}.zip", "LabControlMonitor")


def main(argv) -> int:
    out = Path(argv[1]) if len(argv) > 1 else ROOT / "dist"
    out.mkdir(parents=True, exist_ok=True)
    v = version()
    made = [zip_dir(ROOT, out / f"LabControl_v{v}.zip", "LabControl"), hub_zip(out, v), monitor_app(out, v)]
    for p in made:
        print(f"{p}  ({p.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
