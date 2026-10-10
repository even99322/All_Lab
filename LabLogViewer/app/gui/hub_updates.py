"""Push notification of new versions from the lab hub, and "Update now" / "Check for updates" (v2.1.2).

``start(version)`` (called once by the Browser window) runs one background thread that keeps a long poll
open on the hub (app/core/hub_update.py). When the hub publishes a newer LabLogViewer, the "update" notice
appears in the bell and the pet announces it, within seconds. Unreachable hub: retried with a growing pause.
The GitHub feed check (update_feed.py) is unchanged and still runs.
"""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from app.core import hub_update

_WATCHER: "HubWatcher | None" = None


class HubWatcher(QObject):
    found = Signal(object, object)         # UpdateInfo, installer dict (or None)

    def __init__(self, version: str, parent=None):
        super().__init__(parent)
        self.version = version
        self.seen = version
        self._stop = threading.Event()
        self.found.connect(self._notify)

    def start(self) -> None:
        threading.Thread(target=self._run, name="hub-update-watch", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        pause = 10.0
        try:
            info, dl = hub_update.read_hub_feed()
            if hub_update.newer(info, self.version):
                self.found.emit(info, dl)
                self.seen = info.version
        except Exception:
            pass
        while not self._stop.is_set():
            try:
                result = hub_update.watch(self.seen, wait=60)
                pause = 10.0
            except Exception:
                self._stop.wait(pause)
                pause = min(pause * 2, 600.0)
                continue
            if result is not None and not self._stop.is_set():
                info, dl = result
                if hub_update.newer(info, self.seen):
                    self.seen = info.version
                    try:
                        self.found.emit(info, dl)
                    except RuntimeError:
                        return

    def _notify(self, info, download) -> None:
        hub_update.notify(info, download, self.version)


def start(version: str) -> HubWatcher | None:
    """Once per process. Off with LABLOGVIEWER_NO_UPDATE_CHECK=1 (tests) or LABLOGVIEWER_NO_HUB=1."""
    global _WATCHER
    if _WATCHER is not None or os.environ.get("LABLOGVIEWER_NO_UPDATE_CHECK") or os.environ.get("LABLOGVIEWER_NO_HUB"):
        return _WATCHER
    _WATCHER = HubWatcher(version, QApplication.instance())
    _WATCHER.start()
    return _WATCHER


def install_now(parent, localizer, details: dict) -> bool:
    """Download and install the hub's installer for this computer, then quit LabLogViewer. True when started."""
    install = (details or {}).get("install") or {}
    text = localizer.text
    version = (details or {}).get("version", "")
    progress = QProgressDialog(text("update.downloading").format(version=version), text("notice.close"), 0, 100,
                               parent)
    progress.setWindowTitle(text("update.now"))
    progress.setCancelButton(None)
    progress.setMinimumDuration(0)
    progress.setValue(0)
    state = {"done": 0, "total": 0, "error": None, "finished": False}

    def work():
        try:
            hub_update.install(install, lambda d, t: state.update(done=d, total=t))
        except Exception as error:
            state["error"] = error
        state["finished"] = True

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    while not state["finished"]:
        QApplication.processEvents()
        if state["total"]:
            progress.setValue(int(state["done"] * 100 / state["total"]))
        worker.join(0.05)
    progress.close()
    if state["error"] is not None:
        QMessageBox.warning(parent, text("update.now"), text("update.failed").format(error=state["error"]))
        return False
    QMessageBox.information(parent, text("update.now"), text("update.restarting").format(version=version))
    QApplication.quit()
    return True


def check_now(parent, localizer, current_version: str) -> None:
    """Settings > About > "Check for updates": ask the hub now; show the update or say it is up to date."""
    text = localizer.text
    try:
        info, download = hub_update.read_hub_feed()
    except Exception as error:
        QMessageBox.information(parent, text("update.check"), text("update.unreachable").format(error=error))
        return
    if not hub_update.newer(info, current_version):
        QMessageBox.information(parent, text("update.check"), text("update.latest").format(version=current_version))
        return
    from app.core import notifications

    record = hub_update.notify(info, download, current_version)
    if record is None:                         # already announced (or that version was skipped): show it again
        record = next((r for r in notifications.items() if r.get("key") == f"update:{info.version}"), None)
    if record is None:
        details = info.details()
        if download is not None:
            details["install"] = download
        record = {"id": -1, "kind": "update", "params": {"version": info.version}, "details": details}
    from app.gui.notification_bell import UpdatePreviewDialog

    UpdatePreviewDialog(localizer, record, parent).exec()
