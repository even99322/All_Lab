# -*- mode: python ; coding: utf-8 -*-
# Step 3 of packaging (after prepare_build.py and make_icons.py):
#     pyinstaller --noconfirm packaging/lablogviewer.spec
# Builds from the BUILD COPY (build/src, licence code compiled), never from the development folder:
#     Windows -> dist/LabLogViewer/LabLogViewer.exe (folder; packaging/installer.iss makes the installer)
#     macOS   -> dist/LabLogViewer.app
# Bundled next to the code: help/, icons/ (found through sys._MEIPASS) and every non-code file under app/
# (pet pictures, YIG models, json ...).
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "build" / "src"
if not (SRC / "main.py").is_file():
    raise SystemExit("Run python packaging/prepare_build.py first (build/src is missing).")
if any((SRC / "app" / "_guard").glob("license.py")):
    raise SystemExit("build/src/app/_guard still has readable licence code: run prepare_build.py again.")
sys.path.insert(0, str(SRC))                                # the compiled licence modules, not the development ones
VERSION = re.search(r'__version__ = "(.+)"', (SRC / "app" / "__init__.py").read_text(encoding="utf-8")).group(1)
CODE = {".py", ".pyc", ".pyd", ".so"}
MODELS = SRC / "app" / "analysis" / "yig_fitting" / "models"      # read as files by the YIG fitter

datas = [(str(SRC / "help"), "help"), (str(SRC / "icons"), "icons")]
for path in sorted((SRC / "app").rglob("*")):
    if not path.is_file() or "__pycache__" in path.parts:
        continue
    if path.suffix.lower() in CODE and MODELS not in path.parents:
        continue
    datas.append((str(path), str(path.parent.relative_to(SRC))))

for package in ("imageio", "imageio-ffmpeg", "pyqtgraph", "matplotlib", "h5py", "ezdxf", "python-pptx"):
    try:
        datas += copy_metadata(package)                    # imageio reads its own version at import
    except Exception:
        pass

UNUSED_QT_PY = ["PySide6." + m for m in (
    "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel", "QtWebSockets", "QtWebView",
    "QtPdf", "QtPdfWidgets", "Qt3DCore", "Qt3DRender", "Qt3DInput", "Qt3DLogic", "Qt3DExtras", "Qt3DAnimation",
    "QtCharts", "QtLocation", "QtPositioning", "QtMultimedia", "QtMultimediaWidgets", "QtSpatialAudio",
    "QtRemoteObjects", "QtScxml", "QtStateMachine", "QtSensors", "QtTextToSpeech", "QtSerialPort", "QtSerialBus",
    "QtBluetooth", "QtNfc", "QtHttpServer", "QtDesigner", "QtHelp", "QtUiTools", "QtSql", "QtTest",
    "QtQuickControls2", "QtQuickTest", "QtAxContainer", "QtCanvasPainter")]

hidden = collect_submodules("app") + ["PySide6.QtGraphs", "PySide6.QtGraphsWidgets", "PySide6.QtSvg",
                                      "PySide6.QtNetwork", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
                                      "matplotlib.backends.backend_agg", "matplotlib.backends.backend_qtagg",
                                      "scipy.signal", "scipy.optimize"]

a = Analysis(
    [str(SRC / "main.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "IPython", "notebook", "pytest", "cv2", "pandas", "sklearn", "torch",
              "numba", "jupyter", "sphinx", "docutils"] + UNUSED_QT_PY,
    noarchive=False,
)

# v2.1.3: leave out the Qt parts LabLogViewer never uses (PySide6 brings all of them: Qt WebEngine alone is
# ~200 MB). Used: Widgets, Gui, Core, Svg, Network, OpenGL, Data Visualization (3D scatter / trajectory) and
# Qt Graphs with Qt Quick + Quick 3D (3D surface). Checked by --self-test ("Qt Graphs 3D (QML)").
DROP_QT = re.compile(
    r"webengine|webchannel|websockets|webview|qtpdf|qt6pdf|pdfquick|qt63d|qt3d|charts|location|positioning|"
    r"multimedia|spatialaudio|remoteobjects|scxml|statemachine|sensors|texttospeech|serialport|serialbus|"
    r"bluetooth|nfc|httpserver|quick3dphysics|quick3dparticles|quick3dxr|virtualkeyboard|designer|qthelp|"
    r"uitools|qt6sql|qtsql|sqldrivers|quickcontrols2|quickdialogs2|quicktemplates2|labsstylekit|qt5compat|"
    r"wayland|lottie|quicktimeline|protobuf|grpc|quicktest|qt6test|qttest|qmltooling|qmlls|sceneparsers|"
    r"geometryloaders|renderplugins|canbus|texttospeech|labsplatform|labsanimation|labsfolderlistmodel|"
    r"labssettings|labsqmlmodels|labssharedimage|labswavefrontmesh",
    re.I)
DROP_QML_DIR = re.compile(r"[/\\]qml[/\\](qt[/\\]labs|qtquick[/\\](controls|dialogs|virtualkeyboard|pdf|timeline|"
                          r"particles|templates|nativestyle|effects)|qtquick3d[/\\](physics|particles3d|xr|"
                          r"spatialaudio|effects))", re.I)
KEEP_TRANSLATION = re.compile(r"_(zh_tw|zh_cn|en)\.qm$", re.I)


def _keep(entry) -> bool:
    dest = entry[0].replace("\\", "/")
    if not dest.lower().startswith("pyside6"):
        return True
    if DROP_QT.search(dest) or DROP_QML_DIR.search("/" + dest):
        return False
    if "/translations/" in dest.lower() and dest.lower().endswith(".qm") and not KEEP_TRANSLATION.search(dest):
        return False
    if "qtwebengine_locales" in dest.lower():
        return False
    return True


before = len(a.binaries) + len(a.datas)
a.binaries = [e for e in a.binaries if _keep(e)]
a.datas = [e for e in a.datas if _keep(e)]
print(f"LabLogViewer spec: left out {before - len(a.binaries) - len(a.datas)} unused Qt files")
pyz = PYZ(a.pure)
icon = str(ROOT / "build" / ("LabLogViewer.icns" if sys.platform == "darwin" else "LabLogViewer.ico"))
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LabLogViewer",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="LabLogViewer")
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="LabLogViewer.app",
        icon=icon,
        bundle_identifier="tw.edu.ccu.qel.lablogviewer",
        version=VERSION,
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "CFBundleDisplayName": "LabLogViewer",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
