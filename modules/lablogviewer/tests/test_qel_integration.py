"""QEL Lab 大程式整合：交給量測模塊、收量測模塊的數據、共用標籤、相關論文。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if (ROOT / "comm" / "labcomm").is_dir():
    sys.path.insert(0, str(ROOT / "comm"))
pytest.importorskip("labcomm")

from labcomm import local  # noqa: E402

from app.interfaces import qel  # noqa: E402
from app.interfaces.base import InterfaceContext, InterfaceStatus  # noqa: E402
from app.interfaces.measurement.interface import MeasurementInterface  # noqa: E402

if qel.labcomm is None:            # 其他測試先 import 了（那時還沒有 labcomm 路徑）
    import importlib
    importlib.reload(qel)
# 其他測試（v0.17A）檢查「沒有大程式時」的行為：預設關閉，這裡的測試各自打開
qel.AVAILABLE = False


@pytest.fixture()
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def on(monkeypatch, tmp_path):
    monkeypatch.setattr(qel, "AVAILABLE", True)
    monkeypatch.setenv("QEL_HOME", str(tmp_path / "qel"))
    monkeypatch.delenv("QEL_TOKEN", raising=False)


def test_measurement_interface_unavailable_without_qel():
    assert MeasurementInterface().launch(InterfaceContext()).status is InterfaceStatus.UNAVAILABLE


def test_measurement_interface_sends_to_labcontrol(on, tmp_path):
    got = []
    ep = local.LocalEndpoint("labcontrol", "0", lambda a, p: got.append((a, p)) or {"accepted": True},
                             ["apply_scheme"]).start()
    try:
        mi = MeasurementInterface()
        assert mi.is_available()
        r = mi.launch(InterfaceContext())
        assert r.status is InterfaceStatus.ERROR and "選一筆數據" in r.message
        f = tmp_path / "a.hdf5"
        f.write_bytes(b"x")
        assert mi.launch(InterfaceContext(selected_log_path=f)).status is InterfaceStatus.LAUNCHED
        assert got == [("apply_scheme", {"path": str(f)})]
        r = mi.launch(InterfaceContext(selected_log_path=tmp_path / "notes.txt"))
        assert r.status is InterfaceStatus.ERROR and "HDF5" in r.message
    finally:
        ep.stop()
    r = MeasurementInterface().launch(InterfaceContext(selected_log_path=tmp_path / "a.hdf5"))
    assert r.status is InterfaceStatus.ERROR and "大程式" in r.message      # 量測模塊與大程式都沒開


def test_bridge_receives_open_file(on, tmp_path, qapp):
    from PySide6.QtWidgets import QWidget
    from PySide6.QtTest import QTest

    opened = []
    host = QWidget()
    b = qel.QelBridge(host, lambda p: opened.append(p)).start()
    try:
        f = tmp_path / "m.hdf5"
        f.write_bytes(b"x")
        assert local.send("lablogviewer", "open_file", {"path": str(f)}, sender="labcontrol") == {"accepted": True}
        for _ in range(50):
            if opened:
                break
            QTest.qWait(20)
        assert opened == [str(f)]
        with pytest.raises(qel.CommError, match="找不到"):
            local.send("lablogviewer", "open_file", {"path": str(tmp_path / "nope.hdf5")})
        with pytest.raises(qel.CommError, match="登入"):
            local.send("lablogviewer", "open_file", {"dataset_id": 3})
    finally:
        b.stop()


def test_merge_shared_tags(tmp_path):
    from app.core.tag_store import TagStore

    store = TagStore(tmp_path / "tags.json", legacy_paths=[])
    tax = {"tags": [{"name": "BIC", "category": "Project"}, {"name": "YIG mirror", "category": "Board Design"},
                    {"name": "S21 sweep", "category": "Measurement"}, {"name": "Cavity", "category": "Paper Topic"}]}
    assert qel.merge_shared_tags(store, tax) == 3
    assert store.category_for("YIG mirror") == "Board Design"
    assert store.category_for("S21 sweep") == "Other" and store.category_for("Cavity") == "Other"
    assert qel.merge_shared_tags(store, tax) == 0


def test_papers_html_and_file_tags(on, tmp_path):
    h = qel.papers_html([{"tag": "BIC", "tag_url": "http://nas:8080/#/t/BIC",
                          "papers": [{"title": "A <b>paper</b>", "url": "http://nas:8080/#/p/1", "year": 2024,
                                      "first_author": "Wang"}]},
                         {"tag": "LA", "no_access": True, "papers": []}])
    assert 'href="http://nas:8080/#/p/1"' in h and "&lt;b&gt;" in h and "站長還沒有開放" in h
    h5py = pytest.importorskip("h5py")
    f = tmp_path / "t.hdf5"
    with h5py.File(f, "w") as g:
        g.create_group("Tags").attrs["Tags"] = [b"BIC", b"Flux"]
    assert qel.tags_of_file(str(f)) == ["BIC", "Flux"]


def test_related_papers_falls_back_without_login(on):
    class B:
        pass
    assert qel.show_related_papers(B()) is False           # 沒有登入大程式 → 原本的論文庫網址
