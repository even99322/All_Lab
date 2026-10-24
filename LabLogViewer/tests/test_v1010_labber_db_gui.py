"""v1.0.10: File ▸ Import into Labber Database (dialog + Browser / Viewer menus)."""
from __future__ import annotations

import os
from pathlib import Path

import h5py
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.labber_db import safe_io
from tests.test_v1010_labber_db_convert import nc_file


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(safe_io, "STABILITY_SECONDS", 0)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def test_dialog_imports_into_new_database_and_syncs(qapp, tmp_path):
    from app.gui.labber_db_dialog import LabberDatabaseDialog

    src = tmp_path / "mirror" / "2026" / "10" / "Data_1020"
    src.mkdir(parents=True)
    nc_file(src / "a.nc")
    (src / "native.hdf5").write_bytes(b"not hdf5 at all")             # not a measurement → reported, not imported
    root = tmp_path / "db"
    d = LabberDatabaseDialog(None, [str(tmp_path / "mirror")], str(root))
    d._run("init")
    assert d.wait() and (root / "Database.hdf5").is_file()
    got = []
    d.imported.connect(got.append)
    d.start_import()
    assert d.wait()
    report = d.report
    assert report["ok"] == 1 and got == [report["database_root"]]
    assert (root / "2026/10/Data_1020/a.hdf5").is_file()
    text = d.log.toPlainText()
    assert "a.hdf5" in text and "Database.hdf5" in text
    d.start_sync(rebuild=True)
    assert d.wait() and d.report["operation"] == "index" and d.report["files"] == 1
    with h5py.File(root / "Database.hdf5", "r") as f:
        labels = list(f["Labels"].asstr()[()])
    assert "Project/10/1020" in labels and not any("mirror" in l for l in labels)


def test_conflict_question_answers_worker(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from app.gui import labber_db_dialog as mod

    src = tmp_path / "Data_1020"
    src.mkdir()
    nc_file(src / "a.nc")
    root = tmp_path / "db"
    d = mod.LabberDatabaseDialog(None, [str(src)], str(root))
    d.start_import()
    assert d.wait() and d.report["ok"] == 1
    # second time: the name exists → the question is asked; answer "Keep Both"
    monkeypatch.setattr(QMessageBox, "exec", lambda self: self.setProperty("x", 1) or [
        b for b in self.buttons() if self.buttonRole(b) == QMessageBox.ButtonRole.AcceptRole][0].click())
    d.start_import()
    assert d.wait()
    assert d.report["ok"] == 1 and d.report["files"][0]["conflict_action"] == "keep"
    assert Path(d.report["files"][0]["output"]).name == "a_1.hdf5"


def test_browser_and_viewer_menus(qapp, tmp_path):
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    w = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    assert w.labber_db_action.text()
    (tmp_path / "Database.hdf5").write_bytes(b"")
    w.scanner_root = str(tmp_path)
    dialog = w.import_to_labber_database([])
    assert dialog.root() == str(tmp_path)                              # the open Labber database is the target
    dialog.close()
    w.close()
