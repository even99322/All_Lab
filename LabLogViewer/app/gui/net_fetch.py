"""Copy a data file from the network drive before a Viewer opens it (v2.1.6), with a progress dialog.

The window stays responsive (the copy runs on a thread) and the Viewer then reads the local copy, so plotting,
trace switching and processing are as fast as for a local file. Nothing happens for a local file or a file
that is already cached.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QProgressDialog

from app.core import net_cache


def fetch(parent, path: str, max_bytes: int = net_cache.VIEWER_MAX_BYTES) -> bool:
    """True: go on and open ``path`` (copied, or read directly). False: the user cancelled."""
    if not net_cache.needs_copy(path, max_bytes):
        return True
    state = {"done": 0, "total": 0, "finished": False, "cancel": False, "error": None}

    def work() -> None:
        try:
            net_cache.ensure_local(path, lambda d, t: state.update(done=d, total=t),
                                   lambda: state["cancel"], max_bytes)
        except net_cache.Cancelled:
            pass
        except Exception as error:                     # read the original instead
            state["error"] = error
        state["finished"] = True

    worker = threading.Thread(target=work, name="lablog-net-fetch", daemon=True)
    worker.start()
    worker.join(0.3)                                   # a small file: no dialog at all
    if state["finished"]:
        return not state["cancel"]
    from pathlib import Path

    dialog = QProgressDialog(f"Copying from the network drive…\n{Path(path).name}", "Cancel", 0, 100, parent)
    dialog.setWindowTitle("Opening data")
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.setValue(0)
    dialog.canceled.connect(lambda: state.update(cancel=True))
    started = time.monotonic()
    while not state["finished"]:
        QApplication.processEvents()
        total = state["total"]
        if total:
            dialog.setValue(int(state["done"] * 100 / total))
            elapsed = max(time.monotonic() - started, 1e-3)
            rate = state["done"] / elapsed / (1 << 20)
            dialog.setLabelText(f"Copying from the network drive…\n{Path(path).name}\n"
                                f"{state['done'] / (1 << 20):.0f} / {total / (1 << 20):.0f} MB  ·  {rate:.1f} MB/s")
        worker.join(0.05)
    dialog.close()
    dialog.deleteLater()
    return not state["cancel"]
