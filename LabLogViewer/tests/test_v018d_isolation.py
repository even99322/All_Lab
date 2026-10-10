"""v0.18D: 3D Surface in its own process, helper write guard, unresponsiveness watchdog."""

from __future__ import annotations

import os
import time

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _wait(qapp, predicate, timeout=40.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_helper_process_cannot_overwrite_shared_records(tmp_path):
    from app.core.external_state import atomic_write_json, restrict_writes

    try:
        restrict_writes({"three_d_states.json"})
        atomic_write_json(tmp_path / "stars.json", {"x": 1})
        atomic_write_json(tmp_path / "three_d_states.json", {"x": 1})
    finally:
        restrict_writes(None)
    assert not (tmp_path / "stars.json").exists()
    assert (tmp_path / "three_d_states.json").exists()


def test_process_mode_is_default_on_screen_and_off_in_tests(monkeypatch):
    from app.gui import three_d_process

    assert not three_d_process.process_mode_enabled()            # offscreen / pytest
    monkeypatch.setenv("LABLOGVIEWER_3D_PROCESS", "1")
    assert three_d_process.process_mode_enabled()
    program, arguments = three_d_process.child_command(["--three-d-window"])
    assert arguments[0].endswith("main.py") and arguments[-1] == "--three-d-window"


def test_3d_crash_only_closes_the_3d_process(qapp, tmp_path, monkeypatch):
    from tests.real_data import BIG_FILE
    from app.gui.three_d_process import ThreeDProcessHost

    if not BIG_FILE.exists():
        pytest.skip("Real 3D fixture unavailable")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")          # inherited by the child
    host = ThreeDProcessHost()
    crashes, messages = [], []
    host.crashed.connect(crashes.append)
    host.message.connect(messages.append)
    host.show(str(BIG_FILE), None)
    try:
        assert _wait(qapp, lambda: host.channel is not None), "3D process did not connect"
        shot = tmp_path / "child.png"
        host.send({"cmd": "grab", "path": str(shot)})
        assert _wait(qapp, lambda: any(m.get("cmd") == "grabbed" for m in messages))
        assert shot.stat().st_size > 1000
        os.kill(host.process.processId(), 9)                        # simulate a driver crash
        assert _wait(qapp, lambda: crashes, timeout=10)
        assert not host.is_running()
    finally:
        host.shutdown()


def test_watchdog_writes_a_report_when_the_ui_stalls(qapp, tmp_path, monkeypatch):
    from app.core import data_location, watchdog

    user = tmp_path / "user"
    (user / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr(data_location, "legacy_sources", lambda: [])
    data_location._reset_for_tests()
    dog = watchdog.Watchdog(qapp, "test", threshold=1.0)
    recovered = []
    dog.recovered.connect(lambda seconds, report: recovered.append(report))
    try:
        qapp.processEvents()
        time.sleep(2.6)                                            # block the UI thread
        assert _wait(qapp, lambda: recovered, timeout=3)
        report = recovered[0]
        assert report and "unresponsive" in open(report, encoding="utf-8").read()
    finally:
        dog._stop.set()
        data_location._reset_for_tests()



def test_3d_channel_fits_a_long_temporary_folder(qapp, monkeypatch, tmp_path):
    """v0.19E: a temporary folder with a long path made the 3D channel fail to listen
    (socket paths are limited to about 104 bytes on macOS)."""
    import os

    from PySide6.QtCore import QDir
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    from app.gui.three_d_process import SOCKET_PATH_LIMIT, channel_name

    if os.name == "nt":
        pytest.skip("named pipes have no path limit")
    long_temp = tmp_path / ("a-very-long-temporary-folder-name-" * 3)
    long_temp.mkdir()
    monkeypatch.setenv("TMPDIR", str(long_temp) + "/")
    assert len(os.path.join(QDir.tempPath(), "lablogviewer-3d-1-abcdef12")) > SOCKET_PATH_LIMIT
    name = channel_name("lablogviewer-3d-1-abcdef12")
    assert len(os.fsencode(name)) <= SOCKET_PATH_LIMIT
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    assert server.listen(name), server.errorString()
    client = QLocalSocket()
    client.connectToServer(name)
    assert client.waitForConnected(3000)
    client.disconnectFromServer()
    server.close()
