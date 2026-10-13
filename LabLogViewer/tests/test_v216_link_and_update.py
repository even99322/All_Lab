"""v2.1.6: "Update now" also for a version the GitHub feed announced first, the local link with Lab Control,
dropping data files onto LabLogViewer, the default data folder and the project folder helpers."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def _info(version="9.0.0"):
    from app.core.update_feed import UpdateInfo

    return UpdateInfo(version=version, date="2026-10-13", items=[])


def test_hub_installer_is_added_to_a_notice_the_github_feed_made(monkeypatch):
    from app.core import hub_update, notifications, update_feed

    first = update_feed.notify_info(_info(), "2.1.5")                   # GitHub feed: no installer
    assert first is not None and not (first.get("details") or {}).get("install")
    dl = {"name": "LabLogViewer-Setup.exe", "url": "http://hub/get/x.exe", "size": 3, "sha256": "",
          "platform": "windows", "arch": "x86_64", "hub": "http://hub"}
    assert hub_update.notify(_info(), dl, "2.1.5") is None             # same version: no second notice …
    rec = next(r for r in notifications.items() if r.get("key") == "update:9.0.0")
    assert rec["details"]["install"]["url"] == "http://hub/get/x.exe"  # … but now it can install
    monkeypatch.setattr(hub_update, "FROZEN", True)
    monkeypatch.setattr(hub_update, "this_platform", lambda: "windows")
    assert hub_update.can_install(rec["details"])
    assert len([r for r in notifications.items() if r["kind"] == "update"]) == 1


def test_installer_for_asks_the_hub_when_the_notice_has_none(monkeypatch):
    from app.core import hub_update

    monkeypatch.setattr(hub_update, "this_platform", lambda: "windows")
    dl = {"url": "http://hub/get/y.exe", "platform": "windows"}
    monkeypatch.setattr(hub_update, "read_hub_feed", lambda timeout=6.0: (_info(), dl))
    assert hub_update.installer_for({}) is dl
    own = {"url": "http://hub/own.exe", "platform": "windows"}
    assert hub_update.installer_for({"install": own}) is own
    monkeypatch.setattr(hub_update, "read_hub_feed", lambda timeout=6.0: (_info(), None))
    with pytest.raises(hub_update.HubUpdateError):
        hub_update.installer_for({})


def test_local_link_hands_open_requests_to_the_running_program(qapp, tmp_path, monkeypatch):
    import json

    from app.core import local_link

    user = f"t{os.getpid()}x{time.time_ns() % 100000}"
    monkeypatch.setattr(local_link.getpass, "getuser", lambda: user)
    got = []
    link = local_link.LocalLink(got.append)
    try:
        assert link.start()
        rec = json.loads(local_link.app_file().read_text(encoding="utf-8"))
        assert rec["server"] == local_link.server_name() and rec["exe"]
        # the client is another program (Lab Control, or "LabLogViewer <file>"): a second process here
        import subprocess
        import sys

        root = str(Path(__file__).resolve().parents[1])
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from PySide6.QtCore import QCoreApplication; "
                "a = QCoreApplication([]); from app.core import local_link; "
                "print(local_link.send({'cmd': 'open', 'path': sys.argv[2]}, 4000))")
        env = dict(os.environ, USER=user, LOGNAME=user, USERNAME=user, LNAME=user)
        proc = subprocess.Popen([sys.executable, "-c", code, root, str(tmp_path / "a.nc")], env=env,
                                stdout=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 20
        while (proc.poll() is None or not got) and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert proc.stdout.read().strip() == "True"
        assert got and got[0] == {"cmd": "open", "path": str(tmp_path / "a.nc")}
    finally:
        link.stop()


def test_dropped_paths_take_data_files_and_folders(qapp, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl

    from app.gui.file_drop import dropped_paths

    (tmp_path / "a.nc").write_bytes(b"")
    (tmp_path / "b.h5").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "folder").mkdir()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / n)) for n in ("a.nc", "b.h5", "notes.txt", "folder")]
                 + [QUrl("http://example.com/c.nc")])
    assert [p.name for p in dropped_paths(mime)] == ["a.nc", "b.h5", "folder"]
    assert dropped_paths(QMimeData()) == []


def test_drop_from_another_program_opens_it(qapp, tmp_path):
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent
    from PySide6.QtWidgets import QWidget

    from app.gui.file_drop import FileDropFilter

    f = tmp_path / "scan.nc"
    f.write_bytes(b"")
    opened = []
    flt = FileDropFilter(opened.append)
    w = QWidget()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(f))])
    ev = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier)
    assert flt.eventFilter(w, ev) is True
    assert opened == [str(f)]


def test_lab_data_helpers(tmp_path, monkeypatch):
    from app.core import lab_share

    root = tmp_path / lab_share.FOLDER
    for name in ("Beta", "alpha", "_trash", ".hidden"):
        (root / name).mkdir(parents=True)
    (root / "file.txt").write_text("x")
    monkeypatch.setenv("LABLOGVIEWER_LOCAL_LAB_DATA", str(root))
    assert lab_share.local_lab_data() == root
    assert lab_share.default_folder() == str(root)                     # measurement computer: no NAS lookup
    assert lab_share.lab_root_of(root / "alpha" / "2026" / "x.nc") == root
    assert lab_share.lab_root_of(tmp_path) is None
    assert lab_share.project_folders(root) == ["alpha", "Beta"]
    assert lab_share.project_folders(tmp_path / "missing") == []
    monkeypatch.setenv("LABLOGVIEWER_LOCAL_LAB_DATA", str(tmp_path / "missing"))
    assert lab_share.local_lab_data() is None
