"""``LabLogViewer --self-test``: check a (packaged) build on this computer and exit.

Used after packaging on every platform (macOS Intel, Apple silicon, Windows) and by
the build machines. It checks that every library was bundled, that the windows
open in both themes, that a helper process (Figure Builder) starts and stops, that
the licence code can check licences (public key present) and that the bundled
files (icons, Help, models) are there. Nothing is written outside the data folder
of the home in use; no network connection is made.

Prints one line per check and "SELF-TEST PASS" / "SELF-TEST FAIL"; exit code 0 / 1.
LABLOGVIEWER_SELF_TEST_REPORT=<file> also writes the lines there (a windowed Windows
program has no console to print to).
"""

from __future__ import annotations

import importlib
import os
import platform
import sys
import time
import traceback

MODULES = ["numpy", "scipy", "scipy.signal", "scipy.optimize", "h5py", "pyqtgraph", "matplotlib",
           "matplotlib.backends.backend_agg", "imageio", "imageio_ffmpeg", "cryptography", "ezdxf", "shapely",
           "pptx", "PySide6.QtGraphs", "PySide6.QtGraphsWidgets", "PySide6.QtSvg", "PySide6.QtNetwork",
           "PySide6.QtDataVisualization", "PySide6.QtQuickWidgets", "PySide6.QtOpenGLWidgets"]


def _say(line: str) -> None:
    print(line, flush=True)
    target = os.environ.get("LABLOGVIEWER_SELF_TEST_REPORT")
    if target:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(line + "\n")


def run() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, function) -> None:
        try:
            detail = function()
            results.append((name, True, str(detail or "")))
        except Exception as error:                       # every failure is reported, none stops the test
            results.append((name, False, f"{type(error).__name__}: {error}"))
            traceback.print_exc()
        ok, text = results[-1][1], results[-1][2]
        _say(f"{'OK  ' if ok else 'FAIL'}  {name}{'  - ' + text if text else ''}")

    from app import __version__

    _say(f"LabLogViewer {__version__} self-test — {platform.system()} {platform.machine()}, "
         f"Python {platform.python_version()}, frozen={bool(getattr(sys, 'frozen', False))}")
    for name in MODULES:
        check(f"import {name}", lambda name=name: getattr(importlib.import_module(name), "__version__", ""))
    def matplotlib_fonts():
        started = time.perf_counter()
        from matplotlib import font_manager

        font_manager.findfont("DejaVu Sans")
        return f"font cache ready in {(time.perf_counter() - started) * 1000:.0f} ms ({os.environ.get('MPLCONFIGDIR', 'default')})"

    check("matplotlib fonts", matplotlib_fonts)
    check("ffmpeg binary", lambda: os.path.basename(importlib.import_module("imageio_ffmpeg").get_ffmpeg_exe()))

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("LabLogViewer")

    def pump(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)

    def bundled_files():
        from app.gui.app_icon import icon_path
        from app.gui.help_content import help_directory, load_index
        from app.icons import missing_icons

        missing = missing_icons()
        if missing:
            raise RuntimeError(f"missing icons: {', '.join(missing)}")
        if not icon_path(5).is_file():
            raise RuntimeError("app icons missing")
        articles = sum(len(group["articles"]) for group in load_index()["groups"])
        images = len(list((help_directory() / "images").glob("*.png")))
        from app.analysis.yig_fitting.core.trust import BUNDLED_DIR

        models = len(list(BUNDLED_DIR.glob("*.py")))
        if not models:
            raise RuntimeError(f"no bundled YIG models in {BUNDLED_DIR}")
        return f"{articles} Help articles, {images} pictures, {models} YIG models"

    check("bundled files", bundled_files)

    def graphs_qml():
        """v2.1.3: the build leaves out unused Qt parts; the 3D surface (Qt Graphs on Qt Quick 3D) must still load."""
        from PySide6.QtCore import QUrl
        from PySide6.QtQml import QQmlComponent, QQmlEngine

        engine = QQmlEngine()
        component = QQmlComponent(engine)
        component.setData(b"import QtQuick\nimport QtQuick3D\nimport QtGraphs\n"
                          b"Item { Surface3D { anchors.fill: parent } }", QUrl())
        made = component.create()
        if made is None:
            raise RuntimeError(component.errorString().strip() or "Qt Graphs QML could not be created")
        made.deleteLater()
        return "QtQuick, QtQuick3D, QtGraphs Surface3D"

    check("Qt Graphs 3D (QML)", graphs_qml)

    def pet_pictures():
        """v2.1.5: pet pictures are an optional download (not in the program). When they are installed every
        pet must load; LLV_SELF_TEST_REQUIRE_PETS=1 (the install tests) makes "not installed" a failure."""
        import os

        from app.pet import pack, registry
        from app.pet.character import Character

        if not pack.available():
            if os.environ.get("LLV_SELF_TEST_REQUIRE_PETS") == "1":
                raise RuntimeError(f"not installed in {pack.pets_dir()}")
            return f"not installed (optional download; {pack.pets_dir()})"
        loaded = [Character(info.pack, 1).id for info in registry.PETS if not info.hidden]
        version = (pack.installed_info() or {}).get("version", "source folder")
        return f"{', '.join(loaded)} from {pack.assets_root()} ({version})"

    check("pet pictures", pet_pictures)

    def licences():
        from app._guard import license as licenses
        from app._guard.machine import machine_code

        installed, short = licenses.key_status()
        if not installed:
            raise RuntimeError("no developer public key: licences cannot be checked")
        return f"public key {short}, machine code {machine_code()}"

    check("licence check", licences)

    state = {}

    def windows():
        from app.core.data_location import data_root
        from app.localization import initialize_localization
        from app.theme import initialize_theme

        state["data"] = str(data_root())
        localizer = initialize_localization(app)
        theme = initialize_theme(app, localizer.store)
        from app.gui.browser_window import BrowserWindow
        from app.gui.help_window import HelpWindow
        from app.gui.main_window import MainWindow
        from app.settings.dialog import SettingsDialog

        started = time.perf_counter()
        viewer = MainWindow()
        viewer_ms = (time.perf_counter() - started) * 1000
        opened = [BrowserWindow(), viewer, HelpWindow(localizer)]
        opened.append(SettingsDialog(localizer, opened[0], theme))
        for window in opened:
            window.show()
        for mode in ("dark", "light"):
            theme.set_mode(mode)
            pump(0.4)
        state["windows"] = opened
        return f"{len(opened)} windows (a Viewer is built in {viewer_ms:.0f} ms), data folder {state['data']}"

    check("windows and themes", windows)

    def labber_database():
        # v1.0.10: File ▸ Import into Labber Database — convert one tiny synthetic Lab Control file in the package
        import tempfile

        import h5py
        import numpy as np

        from app.core import labber_db
        from app.gui.labber_db_dialog import LabberDatabaseDialog

        with tempfile.TemporaryDirectory(prefix="llv-labberdb-") as folder:
            src = os.path.join(folder, "Data_1022")
            os.makedirs(src)
            with h5py.File(os.path.join(src, "t.nc"), "w") as file:
                file.attrs.update(schema="labcontrol-nc/1", layout="grid", created="2026-10-22T18:00:00", points=2,
                                  planned_points=2, complete=1, project="", user="test", tags="", labcontrol="{}")
                axis = file.create_dataset("Current", data=[1., 2.])
                axis.make_scale("Current")
                freq = file.create_dataset("Frequency", data=[1e9, 1e9 + 1e3, 1e9 + 2e3])
                freq.make_scale("Frequency")
                trace = file.create_dataset("S21", data=np.ones((2, 3), dtype=complex))
                trace.dims[0].attach_scale(axis)
                trace.dims[1].attach_scale(freq)
            report = labber_db.convert_batch([src], os.path.join(folder, "db"))
            if report["ok"] != 1 or not report.get("database_verified"):
                raise RuntimeError(f"conversion failed: {report['files']}")
            dialog = LabberDatabaseDialog(None, [src], os.path.join(folder, "db"))
            dialog.show()
            pump(0.2)
            dialog.close()
        return f"Labber database import {labber_db.VERSION} ok"

    check("Labber database import", labber_database)

    def helper_process():
        from app.figure_builder.process import FigureBuilderLauncher

        launcher = FigureBuilderLauncher()
        process = launcher.open()
        if not process.waitForStarted(30000):
            raise RuntimeError("the Figure Builder process did not start")
        pump(8)
        running = launcher.running()
        launcher.shutdown()
        if not running:
            raise RuntimeError("the Figure Builder process ended by itself")
        return "started and stopped"

    check("helper process", helper_process)

    def close_windows():
        _say("closing windows…")                   # a crash while closing shows up right here
        for window in state.get("windows", []):
            window.close()
            window.deleteLater()
        pump(0.5)
        return f"{len(state.get('windows', []))} closed"

    check("close windows", close_windows)
    failed = [name for name, ok, _ in results if not ok]
    _say("SELF-TEST " + ("PASS" if not failed else "FAIL: " + ", ".join(failed)))
    return 0 if not failed else 1
