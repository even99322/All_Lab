"""v0.19A Network Workspace GUI acceptance: a real Host process and Client process.

Both run offscreen on this computer, talk only over 127.0.0.1
(LABLOGVIEWER_NET_SANDBOX=1) and use throwaway HOME folders under tmp_path.
The Host follows a script (1D -> 2D -> zoom -> 1D -> pen -> YIG -> stop); the
Client records what its mirror shows.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.real_data import BIG_FILE

HERE = Path(__file__).resolve().parent / "network_acceptance"


def _env(tmp_path, home: str, **extra) -> dict:
    env = dict(os.environ)
    env.update({
        "HOME": str(tmp_path / home), "NET_OUT": str(tmp_path / "out"), "LABLOGVIEWER_NET_SANDBOX": "1",
        "LABLOGVIEWER_NET_PORT": "0", "LABLOGVIEWER_NET_DISCOVERY": "0", "QT_QPA_PLATFORM": "offscreen",
        "TMPDIR": str(tmp_path / "tmp"), "MPLCONFIGDIR": str(tmp_path / "mpl"),
    })
    env.update(extra)
    (tmp_path / home / "Documents").mkdir(parents=True, exist_ok=True)
    return env


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real measurement fixture unavailable")
def test_client_mirrors_host_view_only_and_ends_cleanly(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "tmp").mkdir()
    host_log = (tmp_path / "host.log").open("wb")
    host = subprocess.Popen([sys.executable, str(HERE / "net_host.py")], env=_env(tmp_path, "host"),
                            stdout=host_log, stderr=subprocess.STDOUT)
    try:
        client = subprocess.run([sys.executable, str(HERE / "net_client.py")], env=_env(tmp_path, "client"),
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=240)
        assert client.returncode == 0, client.stderr.decode("utf-8", "replace")[-2000:]
        host.wait(timeout=60)
    finally:
        if host.poll() is None:
            host.kill()
        host_log.close()
    record = json.loads(next((tmp_path / "out").glob("*_client.json")).read_text(encoding="utf-8"))
    shots = {shot["tag"]: shot for shot in record["shots"]}
    assert shots["start"]["state"] == "connected" and shots["start"]["mode"] == 0
    assert shots["start"]["cached_bytes"] < 500_000                 # 1D view: only the shown trace
    assert shots["after_2d"]["mode"] == 1 and shots["after_2d"]["cached_bytes"] > 5_000_000
    assert shots["after_1d_ink"]["mode"] == 0 and shots["after_1d_ink"]["ink_strokes"] == 1
    # Marks follow the Host (v0.19E): placed, then cleared
    host_marks = json.loads((tmp_path / "out" / "host_marks.json").read_text(encoding="utf-8"))
    assert shots["after_1d_ink"]["marks"] == pytest.approx(host_marks["marks"])
    assert shots["after_1d_ink"]["annotations"] == host_marks["annotations"] == 1
    assert shots["after_yig"]["marks"] == [] and shots["after_yig"]["annotations"] == 0
    assert shots["after_yig"].get("yig_items", 0) > 0
    assert all(shot.get("click_blocked", True) for shot in shots.values())   # view only
    assert all(shot["quality"]["level"] in {"excellent", "good", "fair"} for shot in shots.values())
    assert record["final_state"] == "disconnected" and record["final_reason"] == "host_closed"
    assert record["mirror_closed"]


def test_view_only_filter_blocks_input_except_allowed(qtbot=None):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication, QPushButton, QWidget

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    from app.network.workspace import _ViewOnlyFilter

    filt = _ViewOnlyFilter()
    blocked, allowed = QPushButton("x"), QPushButton("leave")
    allowed.setProperty("networkAllowed", True)
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(1, 1), QPointF(1, 1), Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    assert filt.eventFilter(blocked, press) is True
    assert filt.eventFilter(allowed, press) is False
    assert filt.eventFilter(blocked, QEvent(QEvent.Type.Paint)) is False
    del app
