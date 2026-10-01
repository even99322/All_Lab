"""儀器伺服器：instruments.yaml 編輯、Station 新增 / 移除、驅動測試、伺服器視窗。"""
import os
import shutil
import time

import pytest

from labcontrol.core import labfile
from labcontrol.diagnostics import FAIL, PASS, WARN, run_driver_test, suggest_drivers
from labcontrol.paths import DEFAULTS_DIR
from labcontrol.testing.fake_scpi import FakeGS200, fake_station

qt = pytest.mark.skipif(not os.environ.get("QT_QPA_PLATFORM") and os.name != "nt" and not os.environ.get("DISPLAY"),
                        reason="需要 Qt 顯示環境")


@pytest.fixture
def inst_file(tmp_path):
    p = tmp_path / "instruments.yaml"
    shutil.copy(DEFAULTS_DIR / "instruments.yaml", p)
    return p


def test_labfile_keeps_comments_and_anchors(inst_file):
    labfile.update_instrument(inst_file, "DC1", {"enabled": False})
    labfile.add_instrument(inst_file, "K1", {"driver": "keithley.k2400", "address": "GPIB0::24::INSTR",
                                             "source": {"limits": [-0.01, 0.01]}})
    text = inst_file.read_text(encoding="utf-8")
    assert "<<: *yoko" in text and "# Lab Control 儀器清單" in text
    conf = dict(labfile.configured(inst_file))
    assert conf["DC1"]["enabled"] is False and conf["DC1"]["source"]["limits"] == [-0.2, 0.2]   # anchor 仍展開
    assert conf["K1"]["source"]["limits"] == [-0.01, 0.01]
    labfile.remove_instrument(inst_file, "K1")
    labfile.update_instrument(inst_file, "DC1", {}, remove=("enabled",))
    assert "K1" not in dict(labfile.configured(inst_file))
    assert "enabled" not in dict(labfile.configured(inst_file))["DC1"]
    assert len(list((inst_file.parent / "logs" / "backup").iterdir())) == 4     # 每次寫入前都有備份
    with pytest.raises(Exception):
        labfile.add_instrument(inst_file, "DC2", {"driver": "x"})


def test_station_add_remove_disconnect():
    st = fake_station()
    events = []
    st.bus.subscribe("station.changed", lambda t, p: events.append((p["action"], p["name"])))
    st.bus.subscribe("instrument.closed", lambda t, p: events.append(("closed", p["name"])))
    st.connect(["DC1"])
    with pytest.raises(Exception):
        st.remove("DC1")                      # magnet_A 正在用 DC1
    st.disconnect("DC1")
    assert not st.instruments["DC1"].connected
    st.connect(["DC1"])                       # 可以重新連線（MockTransport 保留）
    st.remove("magnet_A")
    st.remove("DC1")
    assert "DC1" not in st.instruments
    assert ("closed", "DC1") in events and ("remove", "DC1") in events
    with st.lease("run", ["VNA1"]):
        with pytest.raises(Exception):
            st.remove("VNA1")


def test_driver_test_passes_on_fake_instruments(tmp_path):
    st = fake_station()
    for n in ("DC1", "VNA1", "magnet_A"):
        rep = run_driver_test(st, n, write_same=True, acquire=True, output_step=2e-6)
        assert rep.ok, [(i.name, i.detail) for i in rep.items if i.status == FAIL]
        md = rep.to_markdown()
        assert "驅動測試報告" in md
    rep = run_driver_test(st, "DC1", output_step=2e-6)
    step = next(i for i in rep.items if i.section == "小幅度輸出")
    assert step.status == PASS and "→" in step.value
    assert st.instruments["DC1"].channels["ch1"].get_level() == pytest.approx(0.1586)   # 測試後回到原值
    path = rep.save(tmp_path)
    assert path.exists() and "SCPI 統計" in path.read_text(encoding="utf-8")
    vna = run_driver_test(st, "VNA1")
    assert any("GHz" in i.value for i in vna.items if i.name.startswith("start_freq"))


def test_zna_binary_format_left_by_other_program():
    """0.0.5 實機報告：ZNA 的 FORM 停在 REAL（其他程式設的）→ STIM? 回二進位 → 'ascii' codec 錯誤。
    驅動每次讀陣列前都送 FORM；儀器上沒建立的 trace 標為略過而不是警告。"""
    from labcontrol.diagnostics import SKIP

    st = fake_station()
    st.instruments["VNA1"].transport.s["form"] = "REAL"
    rep = run_driver_test(st, "VNA1", acquire=True)
    x = {i.name: i for i in rep.items if i.name.endswith("x 軸")}
    assert x["S21 x 軸"].status == PASS and "501 點" in x["S21 x 軸"].value
    assert x["S11 x 軸"].status == PASS
    assert x["S12 x 軸"].status == SKIP and x["S22 x 軸"].status == SKIP
    assert rep.ok and not any(i.status == WARN for i in rep.items), [(i.name, i.detail) for i in rep.items]


def test_driver_test_catches_wrong_scpi():
    """驅動的 SCPI 寫錯（這裡故意覆寫成錯的指令）→ 錯誤佇列有 -113 → 該項標 ✖。"""
    from labcontrol.drivers.yokogawa.gs import GS200
    from labcontrol import Station

    st = Station({"instruments": {}}, simulate=False)
    t = FakeGS200()
    st.instruments["BAD"] = GS200("BAD", transport=t, station=st, address="X",
                                  scpi={"limit_curr_src_q": ":SOUR:PROT:VOLTAGE:LIMIT?"})
    rep = run_driver_test(st, "BAD", write_same=True)
    bad = [i for i in rep.items if i.status == FAIL]
    assert bad and any("-113" in i.detail for i in bad)
    assert any(i.status == WARN for i in rep.items if i.name == "IDN 與驅動相符") is False


def test_idn_suggestions():
    assert suggest_drivers("YOKOGAWA,GS210,91W000001,2.02") == ["yokogawa.gs200"]
    assert suggest_drivers("Rohde-Schwarz,ZNA26-4Port,1332450064100001,2.20") == ["rs.zna"]
    assert "yokogawa.gs820" in suggest_drivers("YOKOGAWA,GS820,12345,1.0")


def test_cli_driver_test(tmp_path, monkeypatch):
    from labcontrol.apps.cli import main
    from labcontrol.settings import reload

    monkeypatch.setenv("LAB_CONTROL_HOME", str(tmp_path / "LAB"))
    reload()
    try:
        rc = main(["--sim", "test", "DC1", "--write-same", "--step", "2uA"])
        assert rc == 0
        assert list((tmp_path / "LAB" / "logs").glob("driver_test_DC1_*.md"))
    finally:
        monkeypatch.undo()
        reload()


@qt
def test_server_window(tmp_path, monkeypatch):
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.server import InstrumentServerWindow
    from labcontrol.apps.qt.server.add_dialog import AddInstrumentDialog

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    st = fake_station()
    p = tmp_path / "instruments.yaml"
    p.write_text("instruments: {}\n", encoding="utf-8")
    st.config["_source_path"] = str(p)
    w = InstrumentServerWindow(st)
    w.show()

    def pump(s=0.3):
        t = time.time()
        while time.time() - t < s:
            app.processEvents()
            time.sleep(0.01)
    assert w.tree.topLevelItemCount() == 1                   # 沒有 Hub：只有「這台電腦」群組
    g = w.tree.topLevelItem(0)
    assert g.text(0).startswith("這台電腦")
    names = [g.child(i).text(0) for i in range(g.childCount())]
    assert {"DC1", "DC2", "VNA1", "magnet_A"} <= set(names)
    # 新增儀器（對話框的選項 → Station + instruments.yaml）
    dlg = AddInstrumentDialog(st, w)
    dlg.select_driver("yokogawa.gs610")
    dlg.name.setText("DC9")
    dlg.address.setEditText("GPIB0::9::INSTR")
    dlg._accept()
    assert dlg.result() == QtWidgets.QDialog.DialogCode.Accepted
    monkeypatch.setattr(AddInstrumentDialog, "exec", lambda self: QtWidgets.QDialog.DialogCode.Accepted)
    monkeypatch.setattr(AddInstrumentDialog, "result_name", "DC9", raising=False)
    monkeypatch.setattr(AddInstrumentDialog, "result_options", dlg.result_options, raising=False)
    w.add_instrument()
    assert "DC9" in st.instruments and dict(labfile.configured(p))["DC9"]["address"] == "GPIB0::9::INSTR"
    assert dict(labfile.configured(p))["DC9"]["source"]["limits"] == [-0.2, 0.2]
    # 控制視窗：連線、讀取、寫入、設定輸出
    c = w.open_config("DC1")
    c.connect_inst()
    pump(0.6)
    assert st.instruments["DC1"].connected
    limiter = next(r for r in c.rows if r.p.name == "limiter")
    assert limiter.w.text() == "5"
    limiter.w.setText("7")
    limiter._changed()
    c.set_dirty()
    pump(0.4)
    assert st.instruments["DC1"].transport.s["prot_v"] == 7.0
    d = c.src_boxes[0]
    d["target"].setText("158.62")
    c._set_level(d)
    pump(0.8)
    assert st.instruments["DC1"].channels["ch1"].get_level() == pytest.approx(0.15862)
    assert "SOUR:LEV" in c.traffic.toPlainText()
    # 量測中：控制視窗唯讀
    with st.lease("run-x", ["DC1"]):
        c.refresh_state()
        assert not c.b_set.isEnabled() and not d["set"].isEnabled()
    c.refresh_state()
    # VNA trace
    v = w.open_config("VNA1")
    v.connect_inst()
    pump(0.5)
    v.get_trace()
    pump(0.6)
    assert v._tr_data is not None and len(v._tr_data[0]) == 501
    for x in (c, v):
        x.close()
    w.close()
    pump(0.1)
