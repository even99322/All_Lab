"""v2.1.3: Metadata pane next to the Browser Comment; De-background publishes on Windows."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

NC = Path(__file__).parent / "data" / "labcontrol_sim_sweep.nc"   # a real Lab Control 0.0.27 .nc (simulated VNA)


def _axis(name, unit, instrument, values):
    return SimpleNamespace(channel=SimpleNamespace(name=name, unit=unit, instrument=instrument), values=np.asarray(values))


def test_labber_style_settings_are_summarized():
    from app.core.quick_metadata import summarize, to_text

    trace = SimpleNamespace(channel=SimpleNamespace(instrument="ZNB"), n_entries=41, n_points=2001,
                            x_values=np.linspace(4e9, 6e9, 2001), x_unit="Hz")
    exp = SimpleNamespace(
        step_axes=[_axis("Yoko - Current", "A", "Yoko", np.linspace(-1e-3, 1e-3, 41))],
        vector_traces={"ZNB - S21": trace},
        instrument_config={
            "ZNB": {"Start frequency": 4e9, "Stop frequency": 6e9, "# of points": 2001.0, "IF bandwidth": 100.0,
                    "Average": 1.0, "# of averages": 8.0, "Output power": -20.0},
            "Yoko": {"Current": 0.0, "Output": True},                 # swept: shown under Sweep, not Sources
            "GS200 coil": {"Current": 0.0025, "Output": 1.0},
        },
        creation_time=None)
    text = to_text(summarize(exp))
    assert "Yoko - Current: -0.001 → 0.001 A · 41 pts · step 5e-05" in text
    assert "ZNB (ZNB - S21)" in text
    assert "frequency: 4 GHz – 6 GHz" in text and "center_span: 5 GHz / 2 GHz" in text
    assert "points: 2001" in text and "ifbw: 100 Hz" in text and "averages: 8" in text and "power: -20 dBm" in text
    assert "GS200 coil: 2.5 mA · on" in text and "Yoko:" not in text


def test_averaging_off_and_no_settings_uses_the_trace_axis():
    from app.core.quick_metadata import summarize, to_text

    trace = SimpleNamespace(channel=SimpleNamespace(instrument="VNA"), n_entries=1, n_points=11,
                            x_values=np.linspace(5e9, 5.01e9, 11), x_unit="Hz")
    text = to_text(summarize(SimpleNamespace(step_axes=[], vector_traces={"S21": trace}, instrument_config={},
                                             creation_time=None)))
    assert "single" in text and "frequency: 5 GHz – 5.01 GHz" in text and "points: 11" in text
    exp = SimpleNamespace(step_axes=[], vector_traces={}, creation_time=None,
                          instrument_config={"V": {"Start frequency": 1e9, "Stop frequency": 2e9, "Average": 0.0,
                                                   "# of averages": 10.0}})
    assert "averages: off" in to_text(summarize(exp))


def test_lab_control_nc_settings():
    from app.core.quick_metadata import summarize, to_text
    from app.core.quick_preview import load_experiment

    with load_experiment(str(NC)) as exp:
        text = to_text(summarize(exp))
    assert "磁鐵 A 電流: 150 → 150.004 mA · 5 pts · step 0.001" in text
    assert "VNA1 (VNA - S21)" in text and "frequency: 5.0198 GHz – 5.0298 GHz" in text
    assert "points: 101" in text and "ifbw: 1 kHz" in text and "averages: 4" in text and "power: -10 dBm" in text


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def test_browser_shows_metadata_next_to_the_comment(qapp, tmp_path):
    import time

    from PySide6.QtWidgets import QApplication

    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    folder = tmp_path / "db"
    folder.mkdir()
    shutil.copy(NC, folder / "sweep.nc")
    browser = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    browser.resize(1400, 900)
    browser.show()
    assert browser.metadata_title.text() == "Metadata"
    assert "Select a data entry" in browser.metadata_view.toPlainText()
    browser.open_database(str(folder))
    end = time.time() + 30
    while time.time() < end and browser.data_list.topLevelItemCount() == 0:
        QApplication.processEvents()
        time.sleep(0.02)
    browser.data_list.setCurrentItem(browser.data_list.topLevelItem(0))
    while time.time() < end and "IF bandwidth" not in browser.metadata_view.toPlainText():
        QApplication.processEvents()
        time.sleep(0.02)
    shown = browser.metadata_view.toPlainText()
    assert "Sweep" in shown and "150 → 150.004 mA" in shown and "Frequency" in shown and "IF bandwidth" in shown
    sizes = browser.preview_comment_splitter.sizes()
    assert sizes[1] >= 0.2 * sum(sizes)                          # room for the settings, not a 5% strip
    left, right = browser.comment_metadata_splitter.sizes()
    assert left > 0 and right > 0
    browser.localizer.set_language("zh_TW")
    assert browser.metadata_title.text() == "量測參數" and "IF 頻寬" in browser.metadata_view.toPlainText()
    browser.localizer.set_language("en")
    browser.close()


@pytest.mark.skipif(os.name == "nt", reason="uses fcntl to imitate Windows")
def test_publish_syncs_through_a_writable_handle(tmp_path, monkeypatch):
    """Windows: os.fsync on a read-only handle is [Errno 9] Bad file descriptor (every De-background failed)."""
    from app.core import hdf5_processing_writer as w

    modes = []
    real_open = Path.open

    def spy(self, mode="r", *a, **k):
        modes.append(mode)
        return real_open(self, mode, *a, **k)

    def strict_fsync(fd):                                     # behaves like Windows' _commit()
        import fcntl

        if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY:
            raise OSError(9, "Bad file descriptor")

    monkeypatch.setattr(Path, "open", spy)
    monkeypatch.setattr(w.os, "fsync", strict_fsync)
    temp = tmp_path / ".x.tmp"
    temp.write_bytes(b"data")
    modes.clear()
    w.publish_temporary(temp, tmp_path / "out.nc", overwrite=False)
    assert modes == ["r+b"]
    monkeypatch.undo()
    assert (tmp_path / "out.nc").read_bytes() == b"data"


# ---- Interfaces > Measurement opens Lab Control; Data drags carry the file -----------------------
_FAKE_LAB_CONTROL = r"""
import sys
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtNetwork import QLocalServer
app = QCoreApplication([])
name, out = sys.argv[1], sys.argv[2]
QLocalServer.removeServer(name)
server = QLocalServer()
assert server.listen(name)
def accept():
    sock = server.nextPendingConnection()
    def read():
        open(out, "a", encoding="utf-8").write(bytes(sock.readAll()).decode("utf-8"))
        sock.write(b'{"ok": true}\n'); sock.flush()
    sock.readyRead.connect(read)
server.newConnection.connect(accept)
print("ready", flush=True)
QTimer.singleShot(20000, app.quit)
app.exec()
"""


def test_measurement_interface_hands_the_file_to_an_open_lab_control(tmp_path, monkeypatch):
    import subprocess
    import sys

    from app.interfaces.base import InterfaceContext, InterfaceStatus
    from app.interfaces.measurement import interface as measurement

    name = f"qel-labcontrol-test-{os.getpid()}"
    monkeypatch.setattr(measurement, "server_name", lambda: name)
    script, out = tmp_path / "fake_lc.py", tmp_path / "got.txt"
    script.write_text(_FAKE_LAB_CONTROL, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script), name, str(out)], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "ready"
        data = tmp_path / "run.nc"
        data.write_bytes(b"x")
        result = measurement.MeasurementInterface().launch(InterfaceContext(selected_log_path=data))
        assert result.status is InterfaceStatus.LAUNCHED
        import json

        message = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
        assert message == {"cmd": "apply", "path": str(data)}
    finally:
        proc.kill()


def test_measurement_interface_starts_lab_control_with_the_file(tmp_path, monkeypatch):
    import json

    from app.interfaces.base import InterfaceContext, InterfaceStatus
    from app.interfaces.measurement import interface as measurement

    monkeypatch.setattr(measurement, "send", lambda message, timeout_ms=1500: False)
    exe = tmp_path / "LabControl.exe"
    exe.write_text("")
    record = tmp_path / ".qel" / "labcontrol" / "app.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"exe": str(exe), "args": []}))
    monkeypatch.setattr(measurement.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("LABCONTROL_EXE", raising=False)
    started = []
    monkeypatch.setattr(measurement.subprocess, "Popen", lambda args, **kw: started.append(args))
    data = tmp_path / "run.hdf5"
    data.write_bytes(b"x")
    assert measurement.MeasurementInterface().launch(InterfaceContext(selected_log_path=data)).status \
        is InterfaceStatus.LAUNCHED
    assert started == [[str(exe), "--apply", str(data)]]
    assert measurement.MeasurementInterface().launch(InterfaceContext()).status is InterfaceStatus.LAUNCHED
    assert started[-1] == [str(exe)]
    record.unlink()
    monkeypatch.setattr(measurement.sys, "platform", "linux")
    assert measurement.MeasurementInterface().launch(InterfaceContext()).status is InterfaceStatus.ERROR


def test_data_drag_carries_the_file(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTreeWidgetItem

    from app.gui import measurement_transfer_ui as ui

    data = tmp_path / "run.nc"
    data.write_bytes(b"x")
    lst = ui.MeasurementList()
    item = QTreeWidgetItem(["run"])
    item.setData(0, Qt.UserRole, SimpleNamespace(absolute_path=str(data)))
    lst.addTopLevelItem(item)
    lst.setCurrentItem(item)
    seen = {}

    class FakeDrag:
        def __init__(self, source):
            pass

        def setMimeData(self, mime):
            seen["urls"] = [u.toLocalFile() for u in mime.urls()]
            seen["token"] = mime.hasFormat(ui.MIME_TYPE)

        def exec(self, action):
            return action
    monkeypatch.setattr(ui, "QDrag", FakeDrag)
    lst.startDrag(Qt.CopyAction)
    assert [Path(u) for u in seen["urls"]] == [data] and seen["token"]


# ---- Lab Control raw records (.lm.h5) open read-only ------------------------------------------------
RAW = Path(__file__).parent / "data" / "labcontrol_raw_sweep.lm.h5"   # Lab Control 0.0.27 _raw record


def test_lab_control_raw_record_opens_like_a_point_layout_nc():
    import h5py

    from app.core.quick_metadata import summarize, to_text
    from app.core.quick_preview import build_quick_preview, load_experiment

    with load_experiment(str(RAW)) as exp:
        assert list(exp.vector_traces) == ["VNA - S21"]
        values = np.asarray(exp.get_data("VNA - S21", "raw"))
        text = to_text(summarize(exp))
        assert not exp._reader.is_labcontrol_nc                  # De-background stays Labber / .nc only
    with h5py.File(RAW) as h5:
        assert np.allclose(values.T, h5["channels/S21/data"][()])
    assert "磁鐵 A 電流: 150 → 150.004 mA · 5 pts" in text and "points: 101" in text
    preview = build_quick_preview(str(RAW))
    assert preview.kind == "2d" and preview.metadata


def test_scanner_lists_raw_records(tmp_path):
    from app.core.database_scanner import DatabaseScanner

    shutil.copy(RAW, tmp_path / "run.lm.h5")
    result = DatabaseScanner.scan(tmp_path)
    entries = [e for e in result.entries if e.file_name == "run.lm.h5"]
    assert entries and entries[0].status == "ok" and entries[0].metadata_complete
