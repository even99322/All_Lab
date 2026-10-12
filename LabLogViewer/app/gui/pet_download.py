"""Download the pet pictures from the lab hub with a progress dialog (Settings > Pet, and the one-time offer
when a pet is chosen but its pictures are not on this computer). See app/pet/pack.py."""

from __future__ import annotations

import threading

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from app.pet import pack

DECLINED_KEY = "pet/pictures_declined"


def _settings() -> QSettings:
    return QSettings("CCU QEL", "LabLogViewer")


def _run(work):
    """Run ``work(state)`` in a thread while the GUI keeps painting; returns state."""
    state = {"done": 0, "total": 0, "error": None, "finished": False, "result": None}

    def body():
        try:
            state["result"] = work(state)
        except Exception as error:
            state["error"] = error
        state["finished"] = True

    worker = threading.Thread(target=body, daemon=True, name="pet-pictures")
    worker.start()
    return worker, state


def lookup(parent=None) -> tuple[dict | None, Exception | None]:
    """What the hub offers (pets zip info), without blocking the GUI."""
    worker, state = _run(lambda _s: pack.find_on_hub())
    while not state["finished"]:
        QApplication.processEvents()
        worker.join(0.05)
    return state["result"], state["error"]


def download(parent, text, info: dict | None = None) -> bool:
    """Download and unpack; True when the pictures are now on this computer."""
    title = text("pet.pics.title")
    if info is None:
        info, error = lookup(parent)
        if error is not None or info is None:
            QMessageBox.warning(parent, title, text("pet.pics.failed").format(
                error=error if error is not None else text("pet.pics.none_on_hub")))
            return False
    mb = max(1, round(int(info.get("size") or 0) / 1e6))
    progress = QProgressDialog(text("pet.pics.downloading").format(mb=mb), "", 0, 100, parent)
    progress.setWindowTitle(title)
    progress.setCancelButton(None)
    progress.setMinimumDuration(0)
    progress.setValue(0)
    worker, state = _run(lambda s: pack.install_from_hub(lambda d, t: s.update(done=d, total=t), info=info))
    while not state["finished"]:
        QApplication.processEvents()
        if state["total"]:
            progress.setValue(min(99, int(state["done"] * 100 / state["total"])))
        worker.join(0.05)
    progress.close()
    if state["error"] is not None:
        QMessageBox.warning(parent, title, text("pet.pics.failed").format(error=state["error"]))
        return False
    _settings().remove(DECLINED_KEY)
    QMessageBox.information(parent, title, text("pet.pics.done"))
    return True


def offer(parent, text) -> bool:
    """A pet is chosen and shown but its pictures are missing (e.g. after updating to 2.1.5, whose installer
    no longer carries them): ask once whether to download them now."""
    if _settings().value(DECLINED_KEY, False, type=bool):
        return False
    info, error = lookup(parent)
    if error is not None or info is None:
        return False                          # hub not reachable now: ask again next time
    mb = max(1, round(int(info.get("size") or 0) / 1e6))
    box = QMessageBox(QMessageBox.Icon.Question, text("pet.pics.title"), text("pet.pics.offer").format(mb=mb),
                      parent=parent)
    yes = box.addButton(text("pet.pics.download_now"), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(text("pet.pics.later"), QMessageBox.ButtonRole.RejectRole)
    never = box.addButton(text("pet.pics.never"), QMessageBox.ButtonRole.DestructiveRole)
    box.exec()
    if box.clickedButton() is never:
        _settings().setValue(DECLINED_KEY, True)
        return False
    if box.clickedButton() is not yes:
        return False
    return download(parent, text, info)
