"""打包 Lab Control 成獨立程式（PyInstaller）。在要打包的作業系統上執行（PyInstaller 不能跨平台編譯）：

    Windows：雙擊 packaging/build_windows.bat     → dist/LabControl_v<版本>_windows.exe（單一執行檔）
    macOS：  雙擊 packaging/build_macos.command   → dist/LabControl_v<版本>_macos-<晶片>.dmg（裡面是 Lab Control.app）

或手動：

    python -m pip install -r requirements.txt pyinstaller pillow
    python packaging/build_app.py

產生的檔案拖進平台網頁的「控制台」→「全部發佈」：
  * 下載頁 /get 的「量測」卡片會出現 Windows / Mac 的下載按鈕；
  * 已經在用的 Lab Control 會自己提示「有新版」並更新（量測電腦的量測節點也會）。

macOS 的晶片跟著打包用的 Python：Apple 晶片的 Mac 打出 arm64，Intel Mac 打出 x86_64。
（Linux 只用來檢查打包內容：python packaging/build_app.py --check → dist/LabControl，執行 --self-test。）
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "LabControl"
APP_TITLE = "Lab Control"
BUNDLE_ID = "tw.edu.ccu.qel.labcontrol"


def version() -> str:
    text = (ROOT / "labcontrol" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def run(args, **kw) -> None:
    print("$", " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], check=True, cwd=ROOT, **kw)


def version_file(v: str, folder: Path) -> Path:
    """Windows 執行檔的版本資源（檔案內容 → 內容 → 產品版本；控制台也靠它認版本）。"""
    nums = [int(x) for x in re.findall(r"\d+", v)[:4]] + [0, 0, 0, 0]
    t = tuple(nums[:4])
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
                    date=(0, 0)),
  kids=[StringFileInfo([StringTable('040404b0', [
      StringStruct('CompanyName', 'CCU QEL'),
      StringStruct('FileDescription', 'Lab Control（量測）'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('InternalName', '{NAME}'),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('ProductName', 'Lab Control'),
      StringStruct('ProductVersion', '{v}')])]),
    VarFileInfo([VarStruct('Translation', [1028, 1200])])])
"""
    p = folder / "version_info.txt"
    p.write_text(text, encoding="utf-8")
    return p


def runtime_file(work: Path) -> Path:
    """runtime.json：這個 .exe 的版本與 requirements 指紋。功能更新包的 requirements 指紋相同才會套用（main.py）。"""
    import json

    sys.path.insert(0, str(ROOT))
    from labcontrol.selfupdate import requirements_fingerprint

    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    p = work / "runtime.json"
    p.write_text(json.dumps({"version": version(), "requirements": requirements_fingerprint(req),
                             "python": f"{sys.version_info[0]}.{sys.version_info[1]}",
                             "platform": sys.platform, "machine": platform.machine()}), encoding="utf-8")
    return p


#: 標準函式庫裡不需要的
_STDLIB_SKIP = {"tkinter", "turtle", "turtledemo", "idlelib", "test", "lib2to3", "ensurepip", "venv", "distutils",
                "msilib", "antigravity", "this", "pydoc_data", "xxlimited", "xxsubtype"}
#: 常用套件裡現在沒用到、以後可能用到的子模組
_EXTRA = ["scipy.optimize", "scipy.signal", "scipy.interpolate", "scipy.ndimage", "scipy.stats", "scipy.fft",
          "scipy.linalg", "scipy.special", "scipy.integrate", "numpy.fft", "numpy.linalg", "numpy.polynomial",
          "PyQt6.QtSvg", "PyQt6.QtSvgWidgets", "PyQt6.QtNetwork", "PyQt6.QtPrintSupport", "PyQt6.QtOpenGL",
          "PyQt6.QtOpenGLWidgets", "pyqtgraph.exporters", "h5py", "xarray", "yaml", "ruamel.yaml"]


def future_imports():
    out = []
    for m in sorted(getattr(sys, "stdlib_module_names", ())):
        if m.startswith("_") or m in _STDLIB_SKIP:
            continue
        try:
            __import__(m)
        except Exception:  # noqa: BLE001 - 這個作業系統沒有
            continue
        out.append(m)
    return out + _optional(_EXTRA)


def _optional(mods):
    out = []
    for m in mods:
        try:
            __import__(m)
            out.append(m)
        except Exception:  # noqa: BLE001
            pass
    return out


def pyinstaller(extra, work: Path) -> None:
    sep = ";" if sys.platform == "win32" else ":"
    icon = ROOT / "assets" / ("icon.ico" if sys.platform == "win32" else "icon.png")
    data = [
        (runtime_file(work), "."),                                           # 執行環境指紋（功能更新包要相同）
        (ROOT / "labcontrol" / "defaults", "labcontrol/defaults"),          # 第一次啟動複製到 LAB 資料夾
        (ROOT / "labcontrol" / "apps" / "web" / "templates", "labcontrol/apps/web/templates"),
        (ROOT / "labcontrol" / "data" / "writers" / "labber_export_script.py", "labcontrol/data/writers"),
        (ROOT / "assets", "assets"),
        (ROOT / "labhub" / "static", "labhub/static"),                      # 「在這台電腦執行 Hub」
    ]
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", NAME,
            "--icon", icon, "--workpath", work / "build", "--specpath", work, "--distpath", ROOT / "dist",
            "--paths", ROOT]
    for src, dst in data:
        if src.exists():
            args += ["--add-data", f"{src}{sep}{dst}"]
    for pkg in ("labcontrol", "labhub", "labmonitor", "qelcomm"):
        args += ["--collect-submodules", pkg]
    for pkg in _optional(["pyvisa_py", "pyvisa", "xarray", "h5netcdf", "h5py", "pyqtgraph", "ruamel.yaml"]):
        args += ["--copy-metadata", pkg.replace("_", "-") if pkg == "pyvisa_py" else pkg]
    for pkg in _optional(["pyvisa_py"]):
        args += ["--collect-submodules", pkg]
    # 功能更新包（只換 Lab Control 的程式碼）以後可能用到現在還沒用的模組：把標準函式庫與常用套件的子模組都放進去
    for mod in future_imports():
        args += ["--hidden-import", mod]
    for mod in ("tkinter", "matplotlib", "IPython", "PySide6", "PyQt5", "notebook", "pytest"):
        args += ["--exclude-module", mod]
    run(args + list(extra) + [ROOT / "main.py"])


def build_windows(v: str, work: Path) -> Path:
    pyinstaller(["--onefile", "--windowed", "--version-file", version_file(v, work)], work)
    out = ROOT / "dist" / f"{NAME}_v{v}_windows.exe"
    if out.exists():
        out.unlink()
    (ROOT / "dist" / f"{NAME}.exe").rename(out)
    return out


def build_macos(v: str, work: Path) -> Path:
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    pyinstaller(["--windowed", "--osx-bundle-identifier", BUNDLE_ID], work)
    app = ROOT / "dist" / f"{NAME}.app"
    nice = ROOT / "dist" / f"{APP_TITLE}.app"
    if nice.exists():
        shutil.rmtree(nice)
    app.rename(nice)
    plist = nice / "Contents" / "Info.plist"
    if plist.exists():                                   # 版本、顯示名稱（Finder「取得資訊」）
        import plistlib

        info = plistlib.loads(plist.read_bytes())
        info.update(CFBundleShortVersionString=v, CFBundleVersion=v, CFBundleDisplayName=APP_TITLE,
                    CFBundleName=APP_TITLE, NSHighResolutionCapable=True)
        plist.write_bytes(plistlib.dumps(info))
    out = ROOT / "dist" / f"{NAME}_v{v}_macos-{arch}.dmg"
    if out.exists():
        out.unlink()
    with tempfile.TemporaryDirectory() as tmp:          # .dmg：App ＋「應用程式」捷徑，打開後拖過去就安裝好
        stage = Path(tmp) / "stage"
        stage.mkdir()
        run(["ditto", nice, stage / nice.name])
        (stage / "Applications").symlink_to("/Applications")
        run(["hdiutil", "create", "-volname", APP_TITLE, "-srcfolder", stage, "-ov", "-format", "UDZO", out])
    return out


def build_check(v: str, work: Path) -> Path:
    """Linux：只檢查打包內容（單一執行檔），之後執行 dist/LabControl --self-test。"""
    pyinstaller(["--onefile"], work)
    return ROOT / "dist" / NAME


def self_test(out: Path) -> bool:
    """打包好的程式跑一次 --self-test（Windows 視窗程式沒有主控台：結果寫到檔案再讀）。"""
    if sys.platform == "darwin" and "--check" not in sys.argv:
        exe = ROOT / "dist" / f"{APP_TITLE}.app" / "Contents" / "MacOS" / NAME
    else:
        exe = out
    report = Path(tempfile.mkdtemp()) / "self_test.txt"
    env = dict(os.environ, QT_QPA_PLATFORM=os.environ.get("QT_QPA_PLATFORM", "offscreen"),
               LABCONTROL_SELF_TEST_REPORT=str(report))
    print("自我檢查（打包好的程式）…", flush=True)
    try:
        r = subprocess.run([str(exe), "--self-test"], env=env, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        print("自我檢查超過 15 分鐘")
        return False
    text = report.read_text(encoding="utf-8") if report.exists() else (r.stdout or "") + (r.stderr or "")[-2000:]
    print(text, flush=True)
    return r.returncode == 0


def main() -> int:
    v = version()
    work = ROOT / "build" / "pyinstaller"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    if "--check" in sys.argv:
        out = build_check(v, work)
    elif sys.platform == "win32":
        out = build_windows(v, work)
    elif sys.platform == "darwin":
        out = build_macos(v, work)
    else:
        print("請在 Windows 或 macOS 上執行（PyInstaller 不能跨平台編譯）；Linux 只能 --check")
        return 1
    print(f"\n✔ 完成：{out}（{out.stat().st_size / 1e6:.0f} MB）", flush=True)
    if not self_test(out):
        print("✖ 自我檢查失敗：請把上面的訊息傳給開發者")
        return 1
    if "--check" not in sys.argv:
        print("  把這個檔拖進平台網頁的「控制台」→「全部發佈」。")
        print("  只改了程式碼（requirements.txt 沒變）時，不用重新打包：發佈 tools/make_release.py 做的")
        print("  LabControl_v<版本>.zip 就好，大家的程式會收到推播、幾秒內更新。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
