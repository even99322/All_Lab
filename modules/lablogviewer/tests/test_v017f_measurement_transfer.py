from __future__ import annotations

import hashlib
import json
from pathlib import Path
import socket
import time
from types import SimpleNamespace

import numpy as np
import pytest

from app.core.data_model import ChannelInfo, Dimension
from app.core.labber_parser import load_experiment
from app.core.measurement_transfer import load_measurement, serialize_measurement
from app.core.measurement_transport import MeasurementReceiver, TransferError, send_measurement
from tests.real_data import S31_FILE


REAL_FLUX = (Path(__file__).resolve().parents[2] / "Data" / "CPAEP" /
             "Data_0908" / "0908 X2 Flux-dep.hdf5")


class SyntheticExperiment:
    def __init__(self, names):
        self.log_name = "Sij measurement"
        self.creation_time = None
        self.comment = ""
        self.project = None
        self.tags = []
        self.user = None
        self.version = None
        self.format_variant = "synthetic"
        self.channels = {name: ChannelInfo(name=name, unit="", is_log=True,
                                           is_vector=True, is_complex=True) for name in names}
        self.step_axes = []
        self.vector_traces = {name: True for name in names}
        self.metadata_tree = {"scalar_data_matrix": None}
        self.values = {name: np.array([[1 + 2j], [3 + 4j]], dtype=np.complex128)
                       for name in names}

    def get_data(self, name, transform="raw"):
        assert transform == "raw"
        return self.values[name]

    def list_dimensions(self, name):
        return [Dimension("Frequency", "Hz", 2, np.array([1., 2.]), True, 1.)]

    def close(self):
        pass


def test_generic_sij_and_complex_roundtrip(tmp_path, monkeypatch):
    names = [f"VNA - S{i}{j}" for i in range(1, 5) for j in range(1, 5)]
    experiment = SyntheticExperiment(names)
    monkeypatch.setattr("app.core.measurement_transfer.load_experiment", lambda _: experiment)
    archive = tmp_path / "sij.llvmeasure"
    serialize_measurement("not-used.hdf5", archive)
    received = load_measurement(archive)
    assert set(received.data) == set(names)
    for name in names:
        np.testing.assert_array_equal(received.get_data(name), experiment.values[name])
        assert received.channels[name]["is_complex"] is True


@pytest.mark.skipif(not S31_FILE.is_file(), reason="Real S31 file unavailable")
def test_real_s31_not_hardcoded_to_s21(tmp_path):
    with load_experiment(str(S31_FILE)) as source:
        original = source.get_data("VNA - S31", transform="raw")
        source_dims = [(dim.name, dim.size) for dim in source.list_dimensions("VNA - S31")]
    archive = tmp_path / "s31.llvmeasure"
    serialize_measurement(S31_FILE, archive)
    received = load_measurement(archive)
    assert "VNA - S31" in received.data
    assert "VNA - S21" not in received.data
    assert [(dim["name"], dim["size"]) for dim in received.list_dimensions("VNA - S31")] == source_dims
    np.testing.assert_array_equal(received.data["VNA - S31"], original)


def test_socket_roundtrip_and_corruption(tmp_path, monkeypatch):
    experiment = SyntheticExperiment(["VNA - S31"])
    monkeypatch.setattr("app.core.measurement_transfer.load_experiment", lambda _: experiment)
    archive = tmp_path / "source.llvmeasure"
    serialize_measurement("not-used.hdf5", archive)
    received_paths = []
    receiver = MeasurementReceiver(tmp_path / "received", received_paths.append)
    port = receiver.start("127.0.0.1", 0)
    try:
        time.sleep(0.7)  # The listener must remain alive after its accept timeout.
        send_measurement("127.0.0.1", port, archive)
        assert len(received_paths) == 1
        np.testing.assert_array_equal(load_measurement(received_paths[0]).data["VNA - S31"],
                                      experiment.values["VNA - S31"])
        with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
            payload = archive.read_bytes()
            header = {"protocol_version": 1, "length": len(payload), "sha256": "0" * 64}
            sock.sendall(json.dumps(header).encode() + b"\n" + payload)
            assert b"checksum mismatch" in sock.recv(1024)
        assert len(list((tmp_path / "received").glob("*.llvmeasure"))) == 1
    finally:
        receiver.stop()


def test_offline_receiver_fails_cleanly(tmp_path):
    archive = tmp_path / "source.llvmeasure"
    archive.write_bytes(b"x")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(TransferError):
        send_measurement("127.0.0.1", port, archive)


def test_browser_measurement_drag_keeps_click_separate(monkeypatch):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QTreeWidgetItem
    from app.gui import measurement_transfer_ui as ui

    app = QApplication.instance() or QApplication([])
    tree = ui.MeasurementList()
    tree.setColumnCount(2)
    item = QTreeWidgetItem(["", "Measurement"])
    item.setData(0, Qt.UserRole, object())
    tree.addTopLevelItem(item)
    tree.resize(300, 120)
    tree.show()
    app.processEvents()
    calls = []

    class FakeDrag:
        def __init__(self, source):
            pass

        def setMimeData(self, mime):
            calls.append(bytes(mime.data(ui.MIME_TYPE)))

        def exec(self, action):
            calls.append(action)

    monkeypatch.setattr(ui, "QDrag", FakeDrag)
    point = tree.visualItemRect(item).center()
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, pos=point)
    assert tree.currentItem() is item
    assert calls == []
    QTest.mousePress(tree.viewport(), Qt.LeftButton, pos=point)
    QTest.mouseMove(tree.viewport(), point + QPoint(QApplication.startDragDistance() + 10, 0))
    QTest.mouseRelease(tree.viewport(), Qt.LeftButton, pos=point)
    assert len(calls) == 2
    assert tree.active_drag_token is None
    tree.close()


def test_browser_drop_transfer_and_failure_recovery(tmp_path, monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer, Qt
    from PySide6.QtWidgets import QApplication, QTreeWidgetItem
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow
    from app.gui import browser_window as browser_module
    from tests.real_data import SMALL_FILE

    if not SMALL_FILE.is_file():
        pytest.skip("Real small Labber file unavailable")
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(browser_module, "default_state_path", lambda name: tmp_path / name)
    browser = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    browser.transfer_port.setValue(port)
    browser.transfer_listen.setChecked(True)
    assert browser._measurement_receiver is not None
    browser.transfer_host.setText("127.0.0.1")
    item = QTreeWidgetItem(["", "Measurement"])
    item.setData(0, Qt.UserRole, SimpleNamespace(
        status="ok", absolute_path=str(SMALL_FILE), folder_parts=("sample",),
    ))
    browser.data_list.blockSignals(True)
    browser.data_list.addTopLevelItem(item)
    browser.data_list.setCurrentItem(item)
    browser.data_list.blockSignals(False)
    browser.data_list.active_drag_token = "test-drag"
    loop = QEventLoop()
    QTimer.singleShot(15000, loop.quit)
    browser._send_dropped_measurement("test-drag")
    worker = next(iter(browser._send_workers))
    worker.finished.connect(loop.quit)
    loop.exec()
    app.processEvents()
    assert not worker.isRunning()
    assert "completed" in browser.transfer_status.text().lower() or "Received:" in browser.transfer_status.text()
    assert len(list((tmp_path / "received").glob("*.llvmeasure"))) == 1
    browser.transfer_host.setText("invalid host with spaces")
    browser._send_dropped_measurement("test-drag")
    assert browser.transfer_status.text() == "Invalid receiver host"
    assert browser.data_list.currentItem() is item
    browser.close()


@pytest.mark.skipif(not REAL_FLUX.is_file(), reason="Real 0908 Flux file unavailable")
def test_flux_empty_semantics_real_file_roundtrip(tmp_path):
    before = hashlib.sha256(REAL_FLUX.read_bytes()).hexdigest()
    with load_experiment(str(REAL_FLUX)) as source:
        name = "VNA - S21"
        original_dimensions = [(dim.name, dim.unit, dim.size, dim.values.copy())
                               for dim in source.list_dimensions(name)]
        original = source.get_data(name, transform="raw")
        assert source.channels["DC supply - 4 - Current"].is_relation_based
        assert source.channels["DC supply - 4 - Current"].equation == "x"
        assert len(source.step_axes) == 1
    archive = tmp_path / "flux.llvmeasure"
    serialize_measurement(REAL_FLUX, archive)
    destination = []
    receiver = MeasurementReceiver(tmp_path / "received", destination.append)
    port = receiver.start("127.0.0.1", 0)
    try:
        send_measurement("127.0.0.1", port, archive)
    finally:
        receiver.stop()
    assert len(destination) == 1
    received = load_measurement(destination[0])
    assert received.identity["log_name"] == "0908 X2 Flux-dep"
    assert received.channels["DC supply - 4 - Current"]["is_relation_based"] is True
    assert received.channels["DC supply - 4 - Current"]["equation"] == "x"
    assert len(received.step_axes) == 1
    assert received.data[name].shape == (10001, 399)
    assert np.issubdtype(received.data[name].dtype, np.complexfloating)
    np.testing.assert_array_equal(received.data[name], original)
    for actual, expected in zip(received.list_dimensions(name), original_dimensions, strict=True):
        assert (actual["name"], actual["unit"], actual["size"]) == expected[:3]
        np.testing.assert_array_equal(actual["values"], expected[3])
    assert hashlib.sha256(REAL_FLUX.read_bytes()).hexdigest() == before
