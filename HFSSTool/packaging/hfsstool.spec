# -*- mode: python ; coding: utf-8 -*-
# HFSS 自動化工具 → 單一 exe（HFSSTool_v<版本>_windows.exe）。在 HFSSTool/ 執行：pyinstaller --noconfirm packaging/hfsstool.spec
# 雲端打包：.github/workflows/hfss.yml（Windows），打包完執行「HFSSTool.exe --self-test」檢查。
import os
import re

from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
VERSION = re.search(r'APP_VERSION\s*=\s*"v?([0-9.]+)"', open(os.path.join(ROOT, "config.py"), encoding="utf-8").read()).group(1)

datas = [(os.path.join(ROOT, "icon.ico"), ".")]
binaries = []
hiddenimports = ["selftest", "mode_help", "pythoncom", "pywintypes", "win32com", "win32com.client",
                 "matplotlib.backends.backend_qtagg", "PIL.Image", "psutil"]
for pkg in ("ansys.aedt.core",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
for dist in ("pyaedt",):
    datas += copy_metadata(dist)

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "PyQt5", "PySide2", "PySide6", "IPython", "jupyter", "notebook", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=f"HFSSTool_v{VERSION}_windows",
    console=False,
    upx=False,
    icon=os.path.join(ROOT, "icon.ico"),
)
