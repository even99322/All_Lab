"""LabLogViewer platform self-check: run it on each computer you want to verify
(macOS Intel, macOS Apple silicon, Windows) and send back the report it writes.

    python tools/platform_check.py            everything (about 15-20 minutes)
    python tools/platform_check.py --quick    environment, dependencies and windows only (1 minute)

What it does, all on this computer only:
1. Environment: system, processor, whether Python runs natively or translated
   (Rosetta on Apple silicon), Python and Qt versions.
2. Dependencies: every library LabLogViewer needs imports, with its version.
3. Windows: Browser, Viewer, Help, Settings open and close; the 3D and Figure Builder
   helper processes start and stop.
4. Tests: the full test suite (unless --quick).

Isolation: a temporary home folder and temporary files inside
platform_check_output/ (in _sandbox on the developer's computer), so your real LabLogViewer data is never
read or changed. Network: LABLOGVIEWER_NET_SANDBOX=1 keeps every connection on this
computer (127.0.0.1); nothing connects to other computers or to shared storage.
Report: platform_check_output/report_<system>_<processor>_<time>.txt
Sample data: the tests use the "Data" folder next to the LabLogViewer folder when it
exists (copy it along for the full check); without it those tests are skipped.
"""

from __future__ import annotations

import importlib
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# the developer's scratch folder when there is one (never packaged), else next to LabLogViewer
OUTPUT = (ROOT.parent / "_sandbox" / "platform_check_output" if (ROOT.parent / "_sandbox").is_dir()
          else ROOT / "platform_check_output")
DEPENDENCIES = ["PySide6", "numpy", "scipy", "h5py", "pyqtgraph", "matplotlib", "imageio", "imageio_ffmpeg",
                "cryptography", "ezdxf", "shapely", "pptx", "pytest"]


def translated() -> str:
    """'native', 'Rosetta (translated)' or 'unknown'."""
    if sys.platform == "darwin":
        try:
            value = subprocess.run(["/usr/sbin/sysctl", "-n", "sysctl.proc_translated"], capture_output=True,
                                   text=True, encoding="utf-8", timeout=5).stdout.strip()
            return "Rosetta (translated)" if value == "1" else "native"
        except Exception:
            return "unknown"
    if sys.platform == "win32":
        machine = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE", "")
        return f"native ({machine})" if machine else "unknown"
    return "native"


def isolate(work: Path) -> dict[str, str]:
    home = work / "home"
    (home / "Documents").mkdir(parents=True, exist_ok=True)
    temp = work / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home / "AppData" / "Roaming"),
                "LOCALAPPDATA": str(home / "AppData" / "Local"), "TMPDIR": str(temp), "TEMP": str(temp),
                "TMP": str(temp), "LABLOGVIEWER_NET_SANDBOX": "1", "PYTHONIOENCODING": "utf-8"})
    return env


def check_dependencies(lines: list[str]) -> bool:
    ok = True
    for name in DEPENDENCIES:
        try:
            module = importlib.import_module(name)
            lines.append(f"  OK    {name} {getattr(module, '__version__', '')}")
        except Exception as error:
            ok = False
            lines.append(f"  FAIL  {name}: {error}")
    try:
        from PySide6 import QtCore
        from PySide6.QtGraphsWidgets import Q3DSurfaceWidgetItem  # noqa: F401

        lines.append(f"  OK    Qt {QtCore.qVersion()} with Qt Graphs 3D")
    except Exception as error:
        lines.append(f"  NOTE  Qt Graphs 3D unavailable ({error}); LabLogViewer falls back to Qt Data Visualization")
    return ok


WINDOW_SCRIPT = r'''
import os, sys, time
sys.path.insert(0, sys.argv[1]); os.chdir(sys.argv[1])
def main():
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv[:1]); app.setApplicationName("LabLogViewer")
    def pump(t):
        end = time.time() + t
        while time.time() < end:
            app.processEvents(); time.sleep(0.01)
    from app.core.data_location import data_root
    print("data folder:", data_root())
    from app.localization import initialize_localization
    from app.theme import initialize_theme
    loc = initialize_localization(app); theme = initialize_theme(app, loc.store)
    from app.gui.effects import install_effects
    install_effects(app)
    from app.gui.browser_window import BrowserWindow
    from app.gui.main_window import MainWindow
    from app.gui.help_window import HelpWindow
    from app.settings.dialog import SettingsDialog
    browser = BrowserWindow(); browser.show()
    viewer = MainWindow(); viewer.show()
    help_window = HelpWindow(loc); help_window.show()
    settings = SettingsDialog(loc, browser, theme)
    settings.show()
    for mode in ("dark", "light"):
        theme.set_mode(mode); pump(0.6)
    print("windows: OK")
    from app.figure_builder.process import FigureBuilderLauncher
    launcher = FigureBuilderLauncher()
    process = launcher.open()
    started = process.waitForStarted(20000)
    pump(6)
    running = launcher.running()
    launcher.shutdown()
    print("figure builder process:", "OK" if started and running else f"FAILED (started={started}, running={running})")
    for w in (settings, help_window, viewer, browser):
        w.close()
    pump(0.5)
if __name__ == "__main__":
    import multiprocessing; multiprocessing.freeze_support(); main(); sys.stdout.flush(); os._exit(0)
'''


def run(command: list[str], env: dict, timeout: int) -> tuple[int, str]:
    try:
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout)
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired as error:
        return -1, f"timed out after {timeout} s\n{error.stdout or ''}"


def main() -> int:
    quick = "--quick" in sys.argv
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    work = OUTPUT / f"run_{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    env = isolate(work)
    lines = [f"LabLogViewer platform check — {datetime.now():%Y-%m-%d %H:%M}", f"Folder: {ROOT.name}", "",
             "Environment",
             f"  System:    {platform.system()} {platform.release()} ({platform.version()})",
             f"  Processor: {platform.machine()} — {translated()}",
             f"  Python:    {platform.python_version()} ({sys.executable})", ""]
    print("\n".join(lines))
    lines.append("Dependencies")
    deps_ok = check_dependencies(lines)
    print("\n".join(lines[-len(DEPENDENCIES) - 1:]))
    script = work / "windows_check.py"
    script.write_text(WINDOW_SCRIPT, encoding="utf-8")
    print("Opening windows and helper processes…")
    code, output = run([sys.executable, str(script), str(ROOT)], env, 300)
    lines += ["", "Windows and helper processes", *("  " + l for l in output.strip().splitlines()[-40:]),
              f"  exit code {code}"]
    windows_ok = code == 0 and "windows: OK" in output and "figure builder process: OK" in output
    tests_ok = None
    if not quick:
        print("Running the test suite (15-20 minutes)…")
        started = time.time()
        code, output = run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            f"--basetemp={work / 'pytest'}", "tests"], env, 3600)
        tail = output.strip().splitlines()
        failures = [l for l in tail if l.startswith(("FAILED", "ERROR"))]
        lines += ["", f"Test suite ({time.time() - started:.0f} s)", *("  " + l for l in failures[:60]),
                  "  " + (tail[-1] if tail else "no output")]
        tests_ok = code == 0
        (work / "pytest_output.txt").write_text(output, encoding="utf-8")
    verdict = deps_ok and windows_ok and tests_ok is not False
    lines += ["", "RESULT: " + ("PASS" if verdict else "PROBLEMS FOUND — send this report back"),
              f"Details: {work}"]
    report = OUTPUT / f"report_{platform.system()}_{platform.machine()}_{stamp}.txt"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    shutil.rmtree(work / "home", ignore_errors=True)
    print("\n".join(lines[-3:]))
    print(f"Report: {report}")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
