import os
from pathlib import Path
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication, QFileDialog
from PySide6.QtCore import QTimer
import pytest
import h5py
from app import MainWindow, ConflictDialog, LiquidButton
from converter import convert_batch
from test_converter import nc_file


def test_language_small_window_and_real_background_batch(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path/"settings.json")
    window.show()
    window.resize(920,740)
    app.processEvents()
    for widget in [window.output_edit,window.project_edit]:
        assert widget.height()>=40
    assert window.convert_button.text()=="開始轉換"
    window.language_combo.setCurrentIndex(1)
    app.processEvents()
    assert window.convert_button.text()=="Convert data"
    window.theme_combo.setCurrentIndex(1)
    assert window.dark
    source=tmp_path/"one.nc";nc_file(source)
    window.add_inputs([source,source])
    assert len(window.inputs)==1
    window.output_edit.setText(str(tmp_path/"out"))
    assert window.convert_button.isEnabled()
    window.convert_button.click()
    deadline=time.monotonic()+20
    while window.thread is not None and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    assert window.thread is None
    assert window.last_report["ok"]==1
    assert "2 sweep points" in window.log.toPlainText()
    window.language_combo.setCurrentIndex(0)
    assert "2 個電流點" in window.log.toPlainText()
    assert window.output_edit.height()>=40
    assert isinstance(window.convert_button,LiquidButton)
    assert isinstance(window.open_button,LiquidButton)
    window.resume_check.setChecked(True)
    window.convert_button.click()
    deadline=time.monotonic()+20
    while window.thread is not None and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    assert window.thread is None
    assert window.last_report["resumed"]==1 and window.last_report["ok"]==0
    assert "已續轉略過" in window.log.toPlainText()
    window.language_combo.setCurrentIndex(1);app.processEvents()
    assert "resumed without conversion" in window.log.toPlainText()
    window.close();app.processEvents()


def test_create_database_without_inputs_and_show_actual_root(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path/"settings.json")
    root=tmp_path/"New master folder"
    root.mkdir()
    monkeypatch.setattr(QFileDialog,"getExistingDirectory",lambda *args:str(root))
    window.new_database_button.click()
    deadline=time.monotonic()+20
    while window.thread is not None and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    assert window.thread is None
    assert window.last_report["operation"]=="initialize"
    assert window.last_report["database_verified"]
    assert (root/"Database.hdf5").is_file()
    assert str(root/"Database.hdf5") in window.log.toPlainText()
    assert "New master folder/" in window.database_tree.text()
    assert not window.inputs
    window.language_combo.setCurrentIndex(1);app.processEvents()
    assert window.new_database_button.text()=="Create a new database…"
    assert str(root/"Database.hdf5") in window.log.toPlainText()
    window.close();app.processEvents()


@pytest.mark.parametrize("choice",["keep","replace","skip","close"])
def test_real_conflict_dialog_blocks_worker_until_user_choice(tmp_path,choice):
    app=QApplication.instance() or QApplication([])
    source=tmp_path/"one.nc";nc_file(source)
    first=convert_batch([source],tmp_path/"out")
    original=Path(first["files"][0]["output"]);before=original.read_bytes()
    for n in [1,3]:(original.parent/f"one_{n}.hdf5").write_bytes(b"keep existing")
    with h5py.File(source,"r+") as file:file["S21"][0,0]=777+888j
    window=MainWindow(tmp_path/"settings.json")
    if choice=="replace":window.language_combo.setCurrentIndex(1)
    window.add_inputs([source]);window.output_edit.setText(str(tmp_path/"out"));window.show()
    seen=[]
    timer=QTimer()
    def click_dialog():
        for dialog in window.findChildren(ConflictDialog):
            if dialog.isVisible():
                assert dialog.thread()==app.thread()
                seen.append(dialog.windowTitle())
                assert "one_4.hdf5" in " ".join(label.text() for label in dialog.findChildren(type(window.status)))
                if choice=="close":dialog.reject()
                else:dialog.action_buttons[choice].click()
                timer.stop()
    timer.timeout.connect(click_dialog);timer.start(10)
    window.start()
    deadline=time.monotonic()+20
    while window.thread is not None and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    timer.stop()
    assert window.thread is None
    assert len(seen)==1
    report=window.last_report
    assert report and report["errors"]==0
    if choice in {"skip","close"}:
        assert report["skipped"]==1 and original.read_bytes()==before
    elif choice=="keep":
        assert Path(report["files"][0]["output"]).name=="one_4.hdf5"
        assert original.read_bytes()==before
    else:
        assert report["files"][0]["output"]==str(original)
        with h5py.File(original,"r") as file:assert file["Traces/VNA - S21"][0,0,0]==777
    window.close();app.processEvents()
