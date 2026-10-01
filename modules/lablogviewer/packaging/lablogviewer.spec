# PyInstaller spec for LabLogViewer (step 3). Run from the LabLogViewer folder:
#     pyinstaller --noconfirm packaging/lablogviewer.spec
# after packaging/prepare_build.py and packaging/make_icons.py.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "build" / "src"
sys.path.insert(0, str(SRC))
from app import __version__  # noqa: E402

datas = [
    (str(SRC / "icons"), "icons"),
    (str(SRC / "help"), "help"),
    (str(SRC / "app" / "analysis" / "yig_fitting" / "models"), "app/analysis/yig_fitting/models"),
    (str(SRC / "app" / "visualization3d" / "reference_plane.obj"), "app/visualization3d"),
]
binaries = []
hiddenimports = collect_submodules("app")
for package in ("imageio", "imageio_ffmpeg", "pyqtgraph", "ezdxf", "pptx", "shapely", "h5py"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden
# packages that read their own installed-package information at import time
for package in ("imageio", "imageio-ffmpeg", "matplotlib", "numpy", "scipy", "h5py", "pyqtgraph", "ezdxf",
                "python-pptx", "shapely", "cryptography", "PySide6"):
    datas += copy_metadata(package)
hiddenimports += ["PySide6.QtGraphs", "PySide6.QtGraphsWidgets", "PySide6.QtSvg", "PySide6.QtNetwork",
                  "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtQuick", "PySide6.QtQuick3D",
                  "PySide6.QtQuickWidgets", "scipy.signal", "scipy.optimize", "matplotlib.backends.backend_agg"]

a = Analysis([str(SRC / "main.py")], pathex=[str(SRC)], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, excludes=["tkinter", "pytest", "IPython", "notebook", "PyQt5", "PyQt6", "Cython", "pyximport",
                       "PyInstaller", "setuptools._distutils.compilers"],
             noarchive=False)
pyz = PYZ(a.pure)
icon = str(ROOT / "build" / ("LabLogViewer.icns" if sys.platform == "darwin" else "LabLogViewer.ico"))
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LabLogViewer", console=False, icon=icon,
          target_arch=None, codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, name="LabLogViewer")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="LabLogViewer.app", icon=icon, bundle_identifier="tw.edu.ccu.qel.lablogviewer",
                 version=__version__,
                 info_plist={"CFBundleShortVersionString": __version__, "CFBundleVersion": __version__,
                             "NSHighResolutionCapable": True, "LSMinimumSystemVersion": "12.0",
                             "NSRequiresAquaSystemAppearance": False})
