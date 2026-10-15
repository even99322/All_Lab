"""v1.0.6: sub-projects in 數據集散地 — one more combo box per level, wrapping when there is no room."""
from __future__ import annotations

import pytest


def test_sub_project_helpers(tmp_path):
    from app.core import lab_share

    root = tmp_path / lab_share.FOLDER
    for p in ("YIG/2026/10/Data_1015", "YIG/mirror/2026/10", "YIG/mirror/fall/2026", "YIG/single", "YIG/_raw",
              "YIG/.hidden", "Magnon/2026"):
        (root / p).mkdir(parents=True)
    assert lab_share.sub_projects(root / "YIG") == ["mirror", "single"]
    assert lab_share.sub_projects(root / "Magnon") == []
    assert lab_share.project_chain(root, root / "YIG" / "mirror" / "2026" / "10") == ["YIG", "mirror"]
    assert lab_share.project_chain(root, root) == []
    assert lab_share.project_chain(root, tmp_path) == []


@pytest.fixture()
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_browser_cascades_sub_projects(qapp, tmp_path, monkeypatch):
    from app.core import lab_share
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    root = tmp_path / lab_share.FOLDER
    for p in ("YIG/mirror/fall/2026", "YIG/single/2026", "Magnon/2026"):
        (root / p).mkdir(parents=True)
    monkeypatch.setenv("LABLOGVIEWER_LOCAL_LAB_DATA", str(root))
    win = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    opened = []
    monkeypatch.setattr(win, "_start_scan", lambda path: opened.append(path))
    win.open_database(str(root / "YIG"))
    combo = win.project_combo
    assert combo.currentText() == "YIG"
    assert len(win.sub_project_combos) == 1
    sub = win.sub_project_combos[0]
    assert [sub.itemText(i) for i in range(sub.count())][1:] == ["mirror", "single"]
    assert "YIG" in sub.itemText(0)
    sub.setCurrentIndex(1)
    win._sub_project_chosen(sub)
    assert opened[-1].endswith("mirror")
    assert len(win.sub_project_combos) == 2 and win.sub_project_combos[0].currentText() == "mirror"
    assert [win.sub_project_combos[1].itemText(i) for i in range(win.sub_project_combos[1].count())][1:] == ["fall"]
    win.open_database(str(root / "YIG" / "mirror" / "fall" / "2026"))       # inside a date folder: same chain
    assert [c.currentText() for c in win.sub_project_combos] == ["mirror", "fall"]
    win.open_database(str(root / "Magnon"))
    assert win.sub_project_combos == []
    rows = win.project_flow.rows
    win.open_database(str(root / "YIG" / "mirror"))
    assert len(rows(10_000)) == 1 and len(rows(60)) >= 2
    win.close()
