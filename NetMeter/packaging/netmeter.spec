# PyInstaller：Windows → dist/QELNetMeter/（資料夾版，給 Inno Setup 包成安裝程式；1.0.0 的單一 exe 會被 Defender 誤判）
#              macOS   → dist/QELNetMeter.app（不出現在 Dock）
import re
import sys
from pathlib import Path

root = Path(SPECPATH).parent
ver = re.search(r'__version__ = "([^"]+)"', (root / "netmeter" / "__init__.py").read_text(encoding="utf-8")).group(1)

a = Analysis([str(root / "packaging" / "netmeter_main.py")], pathex=[str(root)], hiddenimports=["psutil"],
             excludes=["numpy", "PyQt5", "PyQt6", "PySide6", "matplotlib", "pandas", "scipy", "PIL", "pytest"])
pyz = PYZ(a.pure)
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)

    nums = tuple(int(x) for x in (ver.split(".") + ["0", "0", "0"])[:4])
    vinfo = VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums),
        kids=[StringFileInfo([StringTable("040404b0", [
            StringStruct("CompanyName", "CCU QEL"), StringStruct("FileDescription", "QEL 流量監看"),
            StringStruct("FileVersion", ver), StringStruct("InternalName", "QELNetMeter"),
            StringStruct("OriginalFilename", "QELNetMeter.exe"), StringStruct("ProductName", "QEL 流量監看"),
            StringStruct("ProductVersion", ver),
            StringStruct("LegalCopyright", "CCU Quantum Engineering Lab")])]),
            VarFileInfo([VarStruct("Translation", [0x0404, 1200])])])
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="QELNetMeter", console=False, upx=False, version=vinfo)
    coll = COLLECT(exe, a.binaries, a.datas, name="QELNetMeter", upx=False)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="QELNetMeter", console=False, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="QELNetMeter", upx=False)
    app = BUNDLE(coll, name="QELNetMeter.app", bundle_identifier="tw.qel.netmeter", version=ver,
                 info_plist={"CFBundleDisplayName": "QEL 流量監看", "CFBundleShortVersionString": ver,
                             "LSUIElement": True, "NSHighResolutionCapable": True})
