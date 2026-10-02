"""把桌面大程式（launcher）或監控程式（monitor）打包成不需要 Python 的安裝檔。

    pip install pyinstaller PySide6 pillow
    python tools/build_desktop.py launcher      # Windows → dist/QELLab-windows.exe
                                                # macOS   → dist/QELLab-macos.zip（裡面是 QEL Lab.app）
    python tools/build_desktop.py monitor       # QELMonitor-…

PyInstaller 只能打包「執行它的那種電腦」：Windows 的 exe 要在 Windows 上打包、Mac 的 App 要在 Mac 上打包
（NAS 是 Linux，沒辦法直接產生 exe）。GitHub Actions 的「build-desktop」會在 Windows 與 macOS 上自動打包，
下載後到大程式網頁「管理 → 模塊發佈 → 安裝檔」上傳，大家就能在「下載」頁下載。

打包後的程式自己就有 Python 與畫面套件；各模塊（量測、讀檔）需要的 Python 會在第一次安裝模塊時自動下載。
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = {
    "launcher": {"dir": ROOT / "launcher", "pkg": "qellauncher", "name": "QELLab", "title": "QEL Lab"},
    "monitor": {"dir": ROOT / "monitor", "pkg": "qelmonitor", "name": "QELMonitor", "title": "QEL Lab 監控程式"},
}


def platform_tag() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos" if platform.machine() == "arm64" else "macos-intel"
    return "linux"


def build(app: str, out: Path) -> Path:
    spec = APPS[app]
    src = spec["dir"]
    version = json.loads((src / "module.json").read_text(encoding="utf-8"))["version"]
    work = ROOT / "build" / app
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    icon_png = ROOT / "portal" / "qelportal" / "static" / "icon-512.png"
    sep = ";" if sys.platform == "win32" else ":"
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
            "--name", spec["name"], "--distpath", str(work / "dist"), "--workpath", str(work / "build"),
            "--specpath", str(work), "--paths", str(ROOT / "comm"), "--paths", str(src),
            "--collect-submodules", "labcomm", "--collect-submodules", spec["pkg"],
            "--add-data", f"{src / spec['pkg'] / 'icon.svg'}{sep}{spec['pkg']}",
            "--add-data", f"{src / 'module.json'}{sep}.",
            "--icon", str(icon_png)]
    if sys.platform == "win32":
        args.append("--onefile")
    if sys.platform == "darwin":
        args += ["--osx-bundle-identifier", f"tw.qel.{app}"]
    args.append(str(src / "main.py"))
    print(" ".join(args), flush=True)
    subprocess.run(args, check=True, cwd=str(work))
    out.mkdir(parents=True, exist_ok=True)
    tag = platform_tag()
    dist = work / "dist"
    if sys.platform == "win32":
        dst = out / f"{spec['name']}-{tag}.exe"
        shutil.copy2(dist / f"{spec['name']}.exe", dst)
    else:                                                    # macOS：.app；Linux：資料夾（測試用）
        target = dist / f"{spec['name']}.app" if sys.platform == "darwin" else dist / spec["name"]
        dst = out / f"{spec['name']}-{tag}.zip"
        if dst.exists():
            dst.unlink()
        if sys.platform == "darwin":                         # ditto 保留 App 的權限與符號連結
            subprocess.run(["ditto", "-c", "-k", "--keepParent", str(target), str(dst)], check=True)
        else:
            shutil.make_archive(str(dst.with_suffix("")), "zip", root_dir=target.parent, base_dir=target.name)
    (out / f"{spec['name']}-{tag}.version").write_text(version, encoding="utf-8")
    print(f"完成：{dst}（v{version}）")
    return dst


def main() -> int:
    for stream in (sys.stdout, sys.stderr):             # Windows 主控台預設 cp1252／cp950：中文訊息不要讓打包失敗
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("app", choices=list(APPS))
    ap.add_argument("--out", default=str(ROOT / "dist" / "installers"))
    a = ap.parse_args()
    build(a.app, Path(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
