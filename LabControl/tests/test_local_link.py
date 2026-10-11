"""0.0.28：讀檔程式「介面 → 量測」、`LabControl --apply 檔案` 找到開著的 Lab Control。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_CLIENT = r"""
import sys, json
sys.path.insert(0, sys.argv[1])
from PyQt6 import QtCore
app = QtCore.QCoreApplication([])
from labcontrol.apps.qt import local_link
local_link.server_name = lambda: sys.argv[2]
print(json.dumps(local_link.send(json.loads(sys.argv[3]), 3000)), flush=True)
"""


def _client(tmp_path, name, msg):
    script = tmp_path / "client.py"
    script.write_text(_CLIENT, encoding="utf-8")
    return subprocess.Popen([sys.executable, str(script), str(ROOT), name, json.dumps(msg)],
                            stdout=subprocess.PIPE, text=True, env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))


def _wait(proc, timeout=15):
    from PyQt6 import QtWidgets

    end = time.time() + timeout
    while proc.poll() is None and time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)
    return proc.stdout.read().strip()


def test_local_link_receives_apply_and_records_the_program(tmp_path, monkeypatch):
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt import local_link

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    name = f"qel-labcontrol-test-{os.getpid()}"
    monkeypatch.setattr(local_link, "server_name", lambda: name)
    monkeypatch.setattr(local_link, "app_file", lambda: tmp_path / "app.json")
    got = []
    link = local_link.LocalLink(got.append)
    assert link.start()
    rec = json.loads((tmp_path / "app.json").read_text(encoding="utf-8"))
    assert rec["server"] == name and Path(rec["exe"]).exists()
    assert _wait(_client(tmp_path, name, {"cmd": "apply", "path": "C:/data/run.nc"})) == "true"
    end = time.time() + 2
    while not got and time.time() < end:
        QtWidgets.QApplication.processEvents()
    assert got == [{"cmd": "apply", "path": "C:/data/run.nc"}]
    script = tmp_path / "second.py"                        # 第二個 Lab Control：已經有一個在服務 → 不搶
    script.write_text("import sys\nsys.path.insert(0, sys.argv[1])\nfrom PyQt6 import QtCore\n"
                      "app = QtCore.QCoreApplication([])\nfrom labcontrol.apps.qt import local_link\n"
                      "local_link.server_name = lambda: sys.argv[2]\n"
                      "local_link.app_file = lambda: __import__('pathlib').Path(sys.argv[3])\n"
                      "print(local_link.LocalLink(print).start(), flush=True)\n", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script), str(ROOT), name, str(tmp_path / "b.json")],
                            stdout=subprocess.PIPE, text=True, env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    assert _wait(proc) == "False"
    link.stop()
    assert _wait(_client(tmp_path, name, {"cmd": "show"})) == "false"


def test_window_applies_the_file_from_a_local_message(tmp_path, monkeypatch):
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.workbench import LabControlWindow
    from labcontrol.core.station import Station

    st = Station.from_lab(simulate=True)
    w = LabControlWindow(st, None)
    applied = []
    monkeypatch.setattr(w.platform, "apply_file", lambda p: applied.append(p))
    warnings = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a: warnings.append(a))
    data = tmp_path / "run.nc"
    data.write_bytes(b"x")
    w.handle_local_message({"cmd": "apply", "path": str(data)})
    assert applied == [data] and w.isVisible()
    w.handle_local_message({"cmd": "apply", "path": str(tmp_path / "missing.nc")})
    assert len(applied) == 1 and "找不到" in warnings[-1][2]
    w.doc.dirty = False
    w.close()
    st.close()


def test_data_file_argument_means_apply(monkeypatch):
    """`LabControl.exe 檔案.nc`（「用 Lab Control 開啟」）＝ --apply；已經開著就交給它。"""
    from labcontrol import app as lc_app
    from labcontrol.apps.qt import local_link

    sent = []
    monkeypatch.setattr(local_link, "forward", lambda p: sent.append(p) or True)
    assert lc_app.main(["C:/data/run.nc"]) == 0
    assert lc_app.main(["--apply", "D:/x.hdf5"]) == 0
    assert sent == ["C:/data/run.nc", "D:/x.hdf5"]
