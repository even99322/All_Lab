"""產生發佈檔（在 repo 根目錄執行：python tools/make_release.py [輸出資料夾]）。

    LabControl_v<版本>.zip          Lab Control（Lab APP 程式，整個 repo）
    LabControlHub_v<版本>.zip       NAS 上的 Hub（docker-compose.yml + labhub/ + data/；在 Hub 控制台選這個 zip 更新）
    LabControlMonitor_v<版本>.zip   Lab Control Monitor（Lab APP 程式：main.py + labmonitor/ + 內附的 labhub/）

Monitor 的 Lab APP 資料夾也會放在 <輸出資料夾>/LabControlMonitor/，可以直接推到 Lab APP 的 NAS git。
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"__pycache__", ".pytest_cache", "build", "dist", ".git", ".venv", "venv", ".build-venv", ".mypy_cache"}
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


def manifest(target: str, v: str) -> str:
    """qel-module.json：發佈中心靠它判斷 zip 是哪個模塊、哪一版（說明取自 docs/releases/v<版本>.md 的摘要）。"""
    sys.path.insert(0, str(ROOT))
    from labhub.packages import manifest as _m

    notes = ""
    rel = ROOT / "docs" / "releases" / f"v{v}.md"
    if rel.exists():
        m = re.search(r"##\s*摘要[^\n]*\n(.*?)(?:\n##\s|\Z)", rel.read_text(encoding="utf-8"), re.S)
        notes = m.group(1).strip() if m else ""
    return _m(target, v, notes)


def zip_dir(src: Path, dst: Path, prefix: str, target: str = "") -> Path:
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(prefix + "/", "")
        if target:
            z.writestr(f"{prefix}/qel-module.json", manifest(target, version()))
        for p, rel in _files(src):
            z.write(p, f"{prefix}/{rel.as_posix()}")
    return dst


def hub_zip(out: Path, v: str) -> Path:
    dst = out / f"LabControlHub_v{v}.zip"
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("LabControlHub/", "")
        z.writestr("LabControlHub/qel-module.json", manifest("hub", v))
        for name in ("README.md", "run_hub.bat", "run_hub.sh"):
            z.write(ROOT / "deploy" / "hub" / name, f"LabControlHub/{name}")
        z.writestr("LabControlHub/docker-compose 在 QELPlatform_NAS 安裝包.txt",
                   "0.0.15 起 Hub、控制代理、論文庫在同一個 Docker 專案（docker/QELPlatform/docker-compose.yml），\n"
                   "見 QELPlatform_NAS_v<版本>.zip。更新 Hub 只要在 Monitor「發佈中心」丟這個 zip。\n")
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
- **發佈中心**：把新版 zip 拖進來（Hub、論文庫、量測模塊、讀檔模塊、Monitor、啟動器都可以）→「▶ 全部發佈」。
  網站由控制代理更新（失敗自動換回），模塊發佈到平台後各電腦的啟動器自動更新。
- **平台（站長）**：用站長的論文庫帳號登入；論文庫 / Hub / 節點 / 數據目錄狀態，每位使用者各模塊的開關、
  停用 / 啟用帳號（寫回論文庫），發佈各模塊的新版（啟動器據此單獨更新），操作紀錄。

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


def nas_bundle(out: Path, v: str) -> Path:
    """NAS 安裝包：一個專案（paperlib ＋ Hub ＋ 控制代理）的 docker-compose.yml 與說明。"""
    dst = out / f"QELPlatform_NAS_v{v}.zip"
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("QELPlatform/qel-module.json", json.dumps({"id": "qelplatform-nas", "version": v, "kind": "deploy"}))
        for name in ("docker-compose.yml", "README.md"):
            z.write(ROOT / "deploy" / "platform" / name, f"QELPlatform/{name}")
    return dst


def monitor_app(out: Path, v: str) -> Path:
    app = out / "LabControlMonitor"
    if app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True)
    for pkg in ("labmonitor", "labhub", "qelcomm"):
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
    return zip_dir(app, out / f"LabControlMonitor_v{v}.zip", "LabControlMonitor", "monitor")


def main(argv) -> int:
    out = Path(argv[1]) if len(argv) > 1 else ROOT / "dist"
    out.mkdir(parents=True, exist_ok=True)
    v = version()
    made = [zip_dir(ROOT, out / f"LabControl_v{v}.zip", "LabControl", "labcontrol"), hub_zip(out, v),
            monitor_app(out, v), nas_bundle(out, v)]
    for p in made:
        print(f"{p}  ({p.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
