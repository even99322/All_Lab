"""Driver 測試：SCPI 字串依各儀器手冊（見 driver 檔頭的章節）。"""
import numpy as np
import pytest

from labcontrol import ConfigError, InstrumentBusy, InstrumentError, LimitError, MockTransport, Station
from labcontrol.core.instrument import acting_as
from labcontrol.drivers.rohde_schwarz.vna import RohdeSchwarzZNA
from labcontrol.drivers.yokogawa.gs import GS200, GS610, GS820


def _gs200(state=None, **opts):
    state = state or {"lev": "0.0015", "func": "CURR", "rang": "0.2", "outp": "1"}
    t = MockTransport({"*IDN?": "YOKOGAWA,GS210,1,1", ":SOUR:FUNC?": lambda c: state["func"],
                       ":SOUR:RANG?": lambda c: state["rang"], ":SOUR:LEV?": lambda c: state["lev"],
                       ":OUTP?": lambda c: state["outp"], ":SYST:ERR?": '0,"No error"'})
    inst = GS200("DC1", transport=t, **opts)
    inst.connect()
    return inst, t


# ---- Yokogawa GS200 ---------------------------------------------------------------
def test_gs200_connect_is_read_only():
    inst, t = _gs200()
    assert t.writes == []                      # 連線不改變儀器狀態
    ch = inst.channels["ch1"]
    assert ch.func == "CURR" and ch.unit == "A" and ch.range == 0.2
    assert ch.resolution == pytest.approx(1e-6)    # 200 mA 檔解析度（手冊規格表）
    assert ch.get_level() == pytest.approx(0.0015)


def test_gs200_scpi_limits_and_quantization():
    inst, t = _gs200(source={"limits": [-0.01, 0.01], "resolution": None})
    ch = inst.channels["ch1"]
    ch.set_level(0.0020004)                    # 200 mA 檔解析度 1 µA → 取整
    ch.set_output(False)
    ch.parameters["limiter"].set(5)            # 電流源 → 電壓限制器（§13.2.3）
    assert t.writes == [":SOUR:LEV 0.002", ":OUTP 0", ":SOUR:PROT:VOLT 5"]
    with pytest.raises(LimitError):
        ch.set_level(0.05)


def test_gs200_refuses_range_change_while_output_on():
    inst, t = _gs200()
    ch = inst.channels["ch1"]
    with pytest.raises(InstrumentError):
        ch.parameters["range"].set(0.01)       # 感性負載：輸出開啟時不切量程
    with pytest.raises(InstrumentError):
        ch.parameters["function"].set("VOLT")
    assert not any(w.startswith(":SOUR:RANG") or w.startswith(":SOUR:FUNC") for w in t.writes)


def test_gs200_level_beyond_range_rejected():
    state = {"lev": "0", "func": "CURR", "rang": "0.01", "outp": "1"}
    inst, t = _gs200(state)
    with pytest.raises(InstrumentError):
        inst.channels["ch1"].set_level(0.015)   # 10 mA 檔最大 12 mA


def test_gs200_on_connect_only_writes_differences():
    state = {"lev": "0", "func": "VOLT", "rang": "0.2", "outp": "0"}
    inst, t = _gs200(state, on_connect={"function": "CURR", "range": 0.2, "output": True})
    assert ":SOUR:FUNC CURR" in t.writes and ":OUTP 1" in t.writes
    assert not any(w.startswith(":SOUR:RANG") for w in t.writes)   # range 相同，不重寫


def test_scpi_override_from_yaml():
    inst, t = _gs200(scpi={"level_w": ":SOUR:LEV:FIX {value}"})
    inst.channels["ch1"].set_level(0.0016)
    assert t.writes[-1] == ":SOUR:LEV:FIX 0.0016"


def test_parameter_override_from_yaml():
    inst, _ = _gs200(channels={"ch1": {"parameters": {"limiter": {"limits": [0, 10], "label": "V 限制"}}}})
    p = inst.channels["ch1"].parameters["limiter"]
    assert p.spec.label == "V 限制"
    with pytest.raises(LimitError):
        p.set(20)
    with pytest.raises(ConfigError):
        _gs200(channels={"ch1": {"parameters": {"not_a_param": {}}}})


# ---- GS610 / GS820 -------------------------------------------------------------------
def test_gs610_commands_and_voltage_limiter():
    t = MockTransport({":SOUR:FUNC?": "CURR", ":SOUR:CURR:RANG?": "0.5", ":SOUR:CURR:LEV?": "0", ":OUTP?": "0",
                       ":SOUR:MODE?": "FIX", ":SOUR:SHAP?": "DC", ":SOUR:CURR:RANG:AUTO?": "0"})
    g = GS610("G", transport=t)
    g.connect()
    ch = g.channels["ch1"]
    ch.set_level(0.1)
    assert t.writes[-1] == ":SOUR:CURR:LEV 0.1"
    ch.parameters["limiter"].set(5)            # 手冊：電流源時啟用的是電壓限制器
    assert t.writes[-3:] == [":SOUR:VOLT:PROT:STAT 1", ":SOUR:VOLT:PROT:ULIM 5", ":SOUR:VOLT:PROT:LLIM -5"]


def test_gs610_warns_on_sweep_mode_and_auto_range():
    t = MockTransport({":SOUR:FUNC?": "CURR", ":SOUR:CURR:RANG?": "0.2", ":SOUR:CURR:LEV?": "0", ":OUTP?": "0",
                       ":SOUR:MODE?": "SWE", ":SOUR:SHAP?": "DC", ":SOUR:CURR:RANG:AUTO?": "1"})
    g = GS610("G", transport=t)
    w = g.channels["ch1"].read_state() if g.transport else None
    assert any("源模式" in x for x in w) and any("自動量程" in x for x in w)


def test_gs820_channel_syntax():
    t = MockTransport({":CHAN1:SOUR:FUNC?": "CURR", ":CHAN2:SOUR:FUNC?": "VOLT", ":CHAN1:SOUR:CURR:RANG?": "0.2",
                       ":CHAN2:SOUR:VOLT:RANG?": "7", ":CHAN1:SOUR:CURR:LEV?": "0", ":CHAN2:SOUR:VOLT:LEV?": "1",
                       ":CHAN1:SOUR:MODE?": "FIX", ":CHAN2:SOUR:MODE?": "FIX", ":CHAN1:SOUR:SHAP?": "DC",
                       ":CHAN2:SOUR:SHAP?": "DC", ":CHAN2:MEAS?": "0.99", ":CHAN1:OUTP?": "0", ":CHAN2:OUTP?": "ZERO"})
    g2 = GS820("G2", transport=t)
    g2.connect()
    g2.channels["ch2"].set_level(2.0)
    g2.channels["ch1"].set_output(True)
    assert t.writes[-2:] == [":CHAN2:SOUR:VOLT:LEV 2", ":CHAN1:OUTP 1"]   # 手冊：:CHANnel<n>:SOURce:…
    assert g2.channels["ch2"].unit == "V"
    assert g2.channels["ch2"].get_output() is False       # ZERO 狀態不算開啟
    assert g2.channels["ch2"].parameters["measured"].get() == pytest.approx(0.99)
    g2.channels["ch1"].parameters["limiter"].set(3)
    assert t.writes[-1] == ":CHAN1:SOUR:VOLT:PROT:LEV 3"


def test_auto_ramp_when_jump_too_large():
    inst, t = _gs200(source={"ramp_rate": 0.01, "max_jump": 1e-4, "ramp_dt": 0.005})
    inst.channels["ch1"].set_level(0.0020)  # 由 1.5 mA 跳 0.5 mA > max_jump → 斜坡
    levels = [float(w.split()[1]) for w in t.writes if w.startswith(":SOUR:LEV")]
    assert len(levels) > 1 and levels[-1] == pytest.approx(0.002)
    assert np.all(np.diff(levels) > 0)


# ---- R&S ZNA -------------------------------------------------------------------------
def _zna(**opts):
    t = MockTransport({"CALC1:PAR:CAT?": "'Trc1,S21,Trc2,S11'", "*ESR?": "1",
                       "CALC:DATA:TRAC? 'Trc1', SDAT": "1,2,3,-4", "CALC1:DATA:STIM?": "5e9,6e9",
                       ":SENS1:BAND?": "10000", ":SOUR1:POW?": "-10", "SYST:ERR?": '0,"No error"'})
    v = RohdeSchwarzZNA("V", transport=t, **opts)
    v.connected = True
    return v, t


def test_zna_acquire_sequence():
    v, t = _zna()
    v._averages = 3
    z = v.acquire("S21")
    assert np.allclose(z, [1 + 2j, 3 - 4j])
    w = t.writes
    assert w.index("INIT1:CONT OFF") < w.index(":SENS1:AVER:CLE") < w.index("INIT1:IMM")   # 每點重新平均
    assert w[-1] == "INIT1:CONT ON"
    assert v.x_axis("S21").values.tolist() == [5e9, 6e9]
    assert "CALC1:PAR:SEL 'Trc1'" in t.writes
    # 讀 x 軸前也要送 FORM ASC（儀器格式可能被其他程式改成二進位）
    w = t.writes
    assert w.index("FORM ASC", w.index("CALC1:PAR:SEL 'Trc1'")) > w.index("CALC1:PAR:SEL 'Trc1'")


def test_zna_configure_readback_and_unknown_key():
    v, t = _zna()
    t.responses[":SENS1:BAND?"] = "7000"       # 儀器把 6 kHz 捨入到 7 kHz
    v.configure(if_bw=6000, points=201, averages=2)
    assert v.parameters["if_bw"].cache == 7000
    assert ":SENS1:SWE:POIN 201" in t.writes and ":SENS1:AVER:COUN 2" in t.writes
    assert ":OUTP ON" in t.writes
    with pytest.raises(ConfigError):
        v.configure(bogus=1)
    with pytest.raises(LimitError):
        v.configure(if_bw=1e9)


def test_zna_missing_trace_is_clear_error():
    v, _ = _zna()
    with pytest.raises(InstrumentError):
        v.acquire("S22")


def test_zna_timeout_raises():
    from labcontrol import InstrumentTimeout

    v, t = _zna(opc_timeout_s=0.1)
    t.responses["*ESR?"] = "0"
    with pytest.raises(InstrumentTimeout):
        v.acquire("S21")
    assert t.writes[-1] == "INIT1:CONT ON"


# ---- Station ---------------------------------------------------------------------------
def test_lease_blocks_other_writers(station: Station):
    station.connect()
    src = station.source("DC3")
    with station.lease("run-1", ["pair"]):
        with pytest.raises(InstrumentBusy):
            src.set_level(0.1)              # 網頁面板等其他執行緒
        assert src.get_level() == pytest.approx(0.1586)   # 讀取仍可
        with acting_as("run-1"):
            src.set_level(0.15861)          # 量測本身可以寫
    src.set_level(0.15862)                  # 釋放後恢復


def test_ref_resolution(station: Station):
    station.connect()
    assert station.parameter("DC3.level") is station.get("DC3.ch1.level")
    assert station.get("DC5.ch2").full_name == "DC5.ch2"
    assert station.parameter("pair").name == "level"


def test_sim_mirrors_real_parameter_tables(station: Station):
    vna = station.instruments["VNA1"]
    assert {"start_freq", "if_bw", "power", "averages"} <= set(vna.parameters)
    assert vna.parameters["if_bw"].spec.limits == (1, 30e6)
    assert "limiter" in station.instruments["DC5"].channels["ch2"].parameters
