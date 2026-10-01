"""產生 NAS 第一次安裝用的資料夾（或 zip）：docker-compose.yml + 各服務的程式。

    python tools/make_nas_bundle.py               # → dist/qel-nas/ 與 dist/qel-nas.zip

已經有論文庫或 Lab Control Hub 的 NAS：把原本的 paperlib/data、paperlib/import、LabControlHub/data
複製（或搬）到新資料夾的 paperlib/data、paperlib/import、labhub/data，帳號、論文、量測檔全部沿用。
之後的更新不需要再用這個工具：用監控程式「服務更新」選各服務的 zip（tools/package.py 產生）。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", ".pytest_cache")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist" / "qel-nas"))
    ap.add_argument("--no-zip", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copy2(ROOT / "deploy" / "docker-compose.yml", out / "docker-compose.yml")
    shutil.copy2(ROOT / "docs" / "DEPLOY.md", out / "README-安裝.md")
    # 大程式網站
    shutil.copytree(ROOT / "portal" / "qelportal", out / "portal" / "qelportal", ignore=SKIP)
    shutil.copy2(ROOT / "portal" / "module.json", out / "portal" / "module.json")
    # 論文庫（不含資料）
    pl = ROOT / "modules" / "paperlib"
    for name in ("app", "seed"):
        shutil.copytree(pl / name, out / "paperlib" / name, ignore=SKIP)
    for name in ("Dockerfile", "requirements.txt", "module.json", "README.md"):
        shutil.copy2(pl / name, out / "paperlib" / name)
    # 量測中繼站
    shutil.copytree(ROOT / "modules" / "labcontrol" / "labhub", out / "labhub" / "labhub", ignore=SKIP)
    shutil.copy2(ROOT / "modules" / "labcontrol" / "labhub.module.json", out / "labhub" / "module.json")
    # 更新代理
    shutil.copytree(ROOT / "agent" / "qelagent", out / "agent" / "qelagent", ignore=SKIP)
    shutil.copy2(ROOT / "agent" / "module.json", out / "agent" / "module.json")
    # Synology 不會自動建立掛載的資料夾：先建好
    for d in ("portal/data", "paperlib/data", "paperlib/import", "labhub/data", "backups"):
        (out / d).mkdir(parents=True, exist_ok=True)
        (out / d / ".keep").write_text("", encoding="utf-8")
    print(f"NAS 資料夾：{out}")
    if not a.no_zip:
        z = shutil.make_archive(str(out), "zip", root_dir=out.parent, base_dir=out.name)
        print(f"zip：{z}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
