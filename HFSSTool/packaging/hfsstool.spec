# -*- mode: python ; coding: utf-8 -*-
# HFSS 自動化工具 → 資料夾版 dist/HFSSTool/HFSSTool.exe，再由 packaging/installer.iss 做成安裝程式
# HFSSTool_v<版本>_windows-setup.exe（v3.6 起，跟量測程式、LabLogViewer 一樣）。在 HFSSTool/ 執行：pyinstaller --noconfirm packaging/hfsstool.spec
# 雲端打包：.github/workflows/hfss.yml（Windows），打包完執行「HFSSTool.exe --self-test」檢查（安裝前、安裝後各一次）。
import os
import re

from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
VERSION = re.search(r'APP_VERSION\s*=\s*"v?([0-9.]+)"', open(os.path.join(ROOT, "config.py"), encoding="utf-8").read()).group(1)

datas = [(os.path.join(ROOT, "icon.ico"), ".")]
binaries = []
hiddenimports = ["selftest", "mode_help", "dxf_editor", "dxf_model", "dxf_geom", "update_ui", "updater", "usage", "yig_view", "yig_help", "mpl_toolkits.mplot3d", "shapely", "ezdxf.path", "pythoncom", "pywintypes", "win32com", "win32com.client",
                 "matplotlib.backends.backend_qtagg", "PIL.Image", "psutil"]
for pkg in ("ansys.aedt.core",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += collect_data_files("ezdxf")
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
# ezdxf 的 C 加速模組（.pyd）不打包：在 exe 裡 import 就會當掉（0xC0000409）；main.py 也設了 EZDXF_DISABLE_C_EXT
a.binaries = [b for b in a.binaries if not ("ezdxf" in b[0].replace("\\", "/").split("/")[0] and "/acc/" in b[0].replace("\\", "/"))]
# 執行環境 DLL 有好幾份時（不同套件各帶一份），印出來看用的是哪一份
for name, src, kind in a.binaries:
    low = os.path.basename(name).lower()
    if low.startswith(("msvcp140", "vcruntime140", "concrt140")):
        print(f"dll {low}: {src}")
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HFSSTool",
    console=False,
    upx=False,
    icon=os.path.join(ROOT, "icon.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="HFSSTool", upx=False)
