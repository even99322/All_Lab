"""v1.0.5: lablogviewer:// links from the Hub's web page open a file of 數據集散地."""
from __future__ import annotations

import os

from app.core import link_open


def test_parse_links():
    assert link_open.parse("lablogviewer://open?share=%E9%99%B3/2026/10/a.nc") == {"share": "陳/2026/10/a.nc"}
    assert link_open.parse("LabLogViewer://open?share=a\\b.nc") == {"share": "a/b.nc"}
    assert link_open.parse("lablogviewer://open?path=C%3A%5Cx.nc") == {"path": "C:\\x.nc"}
    assert link_open.parse("lablogviewer://open?share=../etc/passwd") is None
    assert link_open.parse("lablogviewer://delete?share=a.nc") is None
    assert link_open.parse("https://open?share=a.nc") is None and not link_open.is_link(None)


def test_resolve_against_the_share(tmp_path, monkeypatch):
    share = tmp_path / "數據集散地"
    (share / "陳" / "2026").mkdir(parents=True)
    f = share / "陳" / "2026" / "a.nc"
    f.write_bytes(b"x")
    monkeypatch.setenv("LABLOGVIEWER_LAB_DATA", str(share))
    path, reason = link_open.resolve("lablogviewer://open?share=%E9%99%B3/2026/a.nc", timeout=2)
    assert path == os.path.join(str(share), "陳", "2026", "a.nc") and not reason
    path, reason = link_open.resolve("lablogviewer://open?share=%E9%99%B3/missing.nc", timeout=2)
    assert path is None and "not found" in reason
    monkeypatch.setenv("LABLOGVIEWER_LAB_DATA", str(tmp_path / "nowhere"))
    path, reason = link_open.resolve("lablogviewer://open?share=a.nc", timeout=1)
    assert path is None and "數據集散地" in reason
    assert link_open.register_windows("x.exe") is False or os.name == "nt"


def test_file_open_event_with_a_link(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QFileOpenEvent
    from PySide6.QtWidgets import QApplication

    from app.gui.file_drop import FileDropFilter

    app = QApplication.instance() or QApplication([])
    share = tmp_path / "數據集散地"
    share.mkdir()
    (share / "b.nc").write_bytes(b"x")
    monkeypatch.setenv("LABLOGVIEWER_LAB_DATA", str(share))
    opened = []
    f = FileDropFilter(opened.append)
    assert f.eventFilter(app, QFileOpenEvent(QUrl("lablogviewer://open?share=b.nc")))
    import time

    end = time.time() + 5
    while not opened and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert opened == [os.path.join(str(share), "b.nc")]
