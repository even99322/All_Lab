# PyInstaller：Windows → dist/QELNetMeter.exe（單一檔、沒有主控台視窗）；macOS → dist/QELNetMeter.app（不出現在 Dock）
import re
import sys
from pathlib import Path

root = Path(SPECPATH).parent
ver = re.search(r'__version__ = "([^"]+)"', (root / "netmeter" / "__init__.py").read_text(encoding="utf-8")).group(1)

a = Analysis([str(root / "packaging" / "netmeter_main.py")], pathex=[str(root)], hiddenimports=["psutil"],
             excludes=["numpy", "PyQt5", "PyQt6", "PySide6", "matplotlib", "pandas", "scipy", "PIL", "pytest"])
pyz = PYZ(a.pure)
if sys.platform == "win32":
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="QELNetMeter", console=False, upx=False)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="QELNetMeter", console=False, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="QELNetMeter", upx=False)
    app = BUNDLE(coll, name="QELNetMeter.app", bundle_identifier="tw.qel.netmeter", version=ver,
                 info_plist={"CFBundleDisplayName": "QEL 流量監看", "CFBundleShortVersionString": ver,
                             "LSUIElement": True, "NSHighResolutionCapable": True})
