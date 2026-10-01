"""QEL Lab 大程式整合：存檔 → 寫入方案與標籤 → 登錄；數據檔 → 套用設置（本機傳遞、拖到主視窗）。"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if (ROOT / "comm" / "labcomm").is_dir():
    sys.path.insert(0, str(ROOT / "comm"))
labcomm = pytest.importorskip("labcomm")

from labcomm import handoff, local  # noqa: E402

from labcontrol.integrations import qel  # noqa: E402
from labcontrol.measure import Experiment  # noqa: E402
from tests.conftest import experiment_cfg  # noqa: E402

SCHEME = {"scheme": 2, "name": "YIG 2D", "graph": {"start": [0, 0], "nodes": [
    {"kind": "set", "id": "n1", "target": "pair.level", "mode": "sweep", "start": 158.6, "stop": 158.62,
     "step": 0.0005, "unit": "mA"}], "links": []}, "run": {}}


class FakeClient:
    def __init__(self):
        self.calls = []

    def register_dataset(self, name, **kw):
        self.calls.append(dict(kw, name=name))
        return {"id": 42, "created": True}

    def dataset_scheme(self, dsid):
        return SCHEME if dsid == 42 else None

    def tags(self):
        return {"categories": [], "tags": [{"name": "BIC", "category": "Project", "aliases": ["bic-old"]}]}


@pytest.fixture(autouse=True)
def qel_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QEL_HOME", str(tmp_path / "qel"))


def test_export_embeds_and_registers(station, tmp_path):
    client = FakeClient()
    b = qel.QelBridge(station, "9.9.9", client=client).start(endpoint=False)
    try:
        cfg = experiment_cfg(stop=158.601)
        cfg["scheme"] = SCHEME
        exp = Experiment(station, cfg)
        out = exp.plan_output(root=tmp_path)
        ds = exp.create_runner(out).run()
        paths = exp.export(ds, out)
        b.flush()
    finally:
        b.close()
    p = paths[0]
    assert handoff.extract_scheme(p) == SCHEME                      # 方案寫進數據檔
    meta = handoff.read_meta(p)
    assert meta["source"]["module"] == "labcontrol" and meta["dataset_id"] == 42
    c = client.calls[0]
    assert c["name"] == p.name and c["scheme"] == SCHEME and c["fingerprint"].startswith("qfp1:")
    assert c["source"]["version"] == "9.9.9" and c["meta"]["experiment"] == "test"


def test_tags_cleaned_and_scheme_name_dropped(station):
    b = qel.QelBridge(station, "1", client=FakeClient())
    b.tag_names()                                                    # 讀共用標籤（含別名）
    assert b._clean_tags({"tags": ["bic-old", "best_data", "YIG 2D", "Flux"], "name": "YIG 2D"}) == \
        ["BIC", "Best Data", "Flux"]


def test_uploaded_updates_hub_file(station, tmp_path):
    client = FakeClient()
    f = tmp_path / "x.hdf5"
    f.write_bytes(b"\x89HDF" + os.urandom(100))
    b = qel.QelBridge(station, "1", client=client).start(endpoint=False)
    station.bus.publish("data.uploaded", path=str(f), node="PC1", rel="2026/x.hdf5")
    b.flush()
    b.close()
    assert client.calls[-1]["hub_file"] == {"node": "PC1", "rel": "2026/x.hdf5"}


def test_apply_scheme_over_local_ipc(station, tmp_path):
    f = tmp_path / "d.hdf5"
    import h5py
    with h5py.File(f, "w") as h:
        h.create_dataset("x", data=[1])
    handoff.embed(f, SCHEME, {"tags": ["BIC"]})
    got = []
    b = qel.QelBridge(station, "1", client=FakeClient())
    b.on_apply = lambda s, label: got.append((s["name"], label))
    b.start()
    try:
        assert local.send("labcontrol", "apply_scheme", {"path": str(f)}) == {"accepted": True, "name": "YIG 2D"}
        assert local.send("labcontrol", "apply_scheme", {"dataset_id": 42})["name"] == "YIG 2D"
        empty = tmp_path / "empty.hdf5"
        with h5py.File(empty, "w") as h:
            h.create_dataset("x", data=[1])
        with pytest.raises(labcomm.CommError, match="沒有量測設置"):
            local.send("labcontrol", "apply_scheme", {"path": str(empty)})
        with pytest.raises(labcomm.CommError, match="不支援"):
            local.send("labcontrol", "open_file", {"path": str(f)})
    finally:
        b.close()
    assert got == [("YIG 2D", "d.hdf5"), ("YIG 2D", "數據 #42")]


@pytest.mark.skipif(not os.environ.get("QT_QPA_PLATFORM") and os.name != "nt" and not os.environ.get("DISPLAY"),
                    reason="需要 Qt 顯示環境")
def test_drop_data_file_on_window(station, tmp_path, monkeypatch):
    from PyQt6 import QtCore, QtWidgets
    from PyQt6.QtTest import QTest

    from labcontrol.apps.qt.workbench import LabControlWindow
    from labcontrol.scheme import Scheme

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = LabControlWindow(station, Scheme("舊方案"))
    w.show()
    bridge = qel.attach_window(w, station, "1", client=FakeClient())
    try:
        f = tmp_path / "drop.hdf5"
        import h5py
        with h5py.File(f, "w") as h:
            h.create_dataset("x", data=[1])
        handoff.embed(f, SCHEME, {})
        md = QtCore.QMimeData()
        md.setUrls([QtCore.QUrl.fromLocalFile(str(f))])
        pos = QtCore.QPointF(50, 50)
        enter = QtGui_drag(QtCore, md, pos, "enter")
        assert w._qel_relay.eventFilter(w, enter) and enter.isAccepted()
        drop = QtGui_drag(QtCore, md, pos, "drop")
        assert w._qel_relay.eventFilter(w, drop)
        t0 = time.time()
        while w.doc.scheme.name != "YIG 2D" and time.time() - t0 < 5:
            QTest.qWait(50)
        assert w.doc.scheme.name == "YIG 2D"
        # 另一個程序送來的（本機傳遞）→ 主執行緒載入
        w.doc.dirty = False
        bridge.on_apply(dict(SCHEME, name="從讀檔模塊"), "x.hdf5")
        t0 = time.time()
        while w.doc.scheme.name != "從讀檔模塊" and time.time() - t0 < 5:
            QTest.qWait(50)
        assert w.doc.scheme.name == "從讀檔模塊"
        # Tags 欄位有共用標籤提示
        comp = w.files.tags.completer()
        t0 = time.time()
        while comp.model().rowCount() == 0 and time.time() - t0 < 5:
            QTest.qWait(50)
        assert "BIC" in comp.model().stringList()
    finally:
        w.doc.dirty = False
        w.close()
        app.processEvents()


def QtGui_drag(QtCore, md, pos, kind):
    from PyQt6 import QtGui
    acts = QtCore.Qt.DropAction.CopyAction
    if kind == "enter":
        return QtGui.QDragEnterEvent(pos.toPoint(), acts, md, QtCore.Qt.MouseButton.LeftButton,
                                     QtCore.Qt.KeyboardModifier.NoModifier)
    return QtGui.QDropEvent(pos, acts, md, QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier)


def test_scheme_from_file_error_without_scheme(tmp_path):
    import h5py
    f = tmp_path / "plain.hdf5"
    with h5py.File(f, "w") as h:
        h.create_dataset("x", data=[1])
    with pytest.raises(qel.CommError, match="沒有量測設置"):
        qel.scheme_from_file(str(f))
    assert qel.is_data_file("A.HDF5") and not qel.is_data_file("a.yaml")
