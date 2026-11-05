"""VISA 掃描 / 連線測試：與實機驗證過的 GS210 連線程式相同的做法（假的 pyvisa，不需要硬體）。"""
import sys
import types

import pytest

from labcontrol import diagnostics
from labcontrol.core import transport
import labcontrol.drivers  # noqa: F401  確保 driver 已註冊

GS_A = "USB0::0x0B21::0x0039::91AB00001::INSTR"
GS_B = "USB0::0x0B21::0x0039::91AB00002::0::INSTR"
VNA = "TCPIP0::192.0.2.10::inst0::INSTR"


class FakeRes:
    def __init__(self, rm, addr):
        self.rm, self.addr = rm, addr
        self.timeout = None
        self.write_termination = None
        self.read_termination = None
        self.closed = False

    def query(self, cmd):
        assert cmd == "*IDN?"
        # 有設 \n 結束字元才回得來（模擬 GS210 沒設 read_termination 會逾時的情況）
        if self.read_termination != "\n":
            raise TimeoutError("VI_ERROR_TMO")
        ans = self.rm.idn.get(self.addr)
        if isinstance(ans, Exception):
            raise ans
        return ans + "\n"

    def write(self, cmd):
        pass

    def close(self):
        self.closed = True


class FakeRM:
    def __init__(self, lib=""):
        self.lib = lib
        self.queries = []
        self.opened = []
        self.listed = [GS_A, GS_B, VNA, "USB0::0x0B21::0x0039::91AB00001::RAW", "TCPIP0::192.0.2.10::5025::SOCKET",
                       GS_A]
        self.idn = {GS_A: "YOKOGAWA,GS210,91AB00001,1.05",
                    GS_B: "YOKOGAWA,GS210,91AB00009,1.05",  # 序號不一致
                    VNA: "Rohde-Schwarz,ZNB20-2Port,1311601062100000,3.45"}

    def list_resources(self, query="?*::INSTR"):
        self.queries.append(query)
        if query == "?*::INSTR":  # 模擬預設查詢漏掉 USB-TMC 儀器的 VISA
            return tuple(a for a in self.listed if not a.startswith("USB"))
        return tuple(self.listed)

    def open_resource(self, addr, open_timeout=0):
        self.opened.append((addr, open_timeout))
        return FakeRes(self, addr)

    def close(self):
        pass


@pytest.fixture
def fake_visa(monkeypatch):
    made = []

    def rm_factory(lib=""):
        if lib == "bad.dll":
            raise OSError("cannot load")
        rm = FakeRM(lib)
        made.append(rm)
        return rm

    mod = types.SimpleNamespace(ResourceManager=rm_factory)
    monkeypatch.setitem(sys.modules, "pyvisa", mod)
    transport.reset_resource_managers()
    yield made
    transport.reset_resource_managers()


def test_parse_usb_address():
    assert diagnostics.parse_usb_address(GS_A) == (0x0B21, 0x0039, "91AB00001")
    assert diagnostics.parse_usb_address(GS_B) == (0x0B21, 0x0039, "91AB00002")
    assert diagnostics.parse_usb_address(VNA) is None
    assert diagnostics.parse_usb_address("USB0::0x0B21::0x0039::91AB00001::RAW") is None
    assert diagnostics.parse_usb_address("USB0::zz::0x0039::1::INSTR") is None


def test_visa_candidates_prefers_ni_visa(monkeypatch):
    import os

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os.path, "exists", lambda p: p == transport.NI_VISA_DLLS[0])
    assert transport.visa_candidates("") == [transport.NI_VISA_DLLS[0], "", "@py"]
    assert transport.visa_candidates("auto")[0] == transport.NI_VISA_DLLS[0]
    assert transport.visa_candidates("@py") == ["@py"]
    monkeypatch.setattr(sys, "platform", "linux")
    assert transport.visa_candidates("") == ["", "@py"]


def test_resource_manager_falls_back(fake_visa, monkeypatch):
    monkeypatch.setattr(transport, "visa_candidates", lambda b: ["bad.dll", "", "@py"])
    rm = transport.shared_resource_manager("")
    assert rm.lib == "" and transport.rm_info[""] == "系統預設 VISA"
    assert transport.shared_resource_manager("") is rm  # 共用


def test_scan_lists_usb_and_checks_serial(fake_visa):
    res = diagnostics.scan_resources(identify=True, timeout_ms=1500, backend="")
    rm = fake_visa[0]
    assert rm.queries == ["?*"]
    assert [r.address for r in res] == [GS_A, GS_B, VNA]  # 只留 INSTR、去重
    a, b, v = res
    assert a.idn.startswith("YOKOGAWA,GS210") and a.serial == "91AB00001" and not a.warning
    assert "yokogawa.gs200" in a.suggested
    assert "不一致" in b.warning and "91AB00009" in b.warning
    assert v.serial == "" and v.vid is None
    # 開啟時帶 open_timeout，並設好結束字元（否則 FakeRes 會逾時）
    assert all(t == transport.OPEN_TIMEOUT_MS for _, t in rm.opened)


def test_usb_id_suggests_driver_when_idn_fails(fake_visa):
    rm = transport.shared_resource_manager("")
    rm.idn[GS_A] = TimeoutError("VI_ERROR_TMO")
    res = diagnostics.scan_resources(identify=True, timeout_ms=100, backend="")
    a = res[0]
    assert a.error and a.suggested == ["yokogawa.gs200"]
    assert "91AB00001" in a.describe() and "*IDN? 失敗" in a.describe()


def test_probe_resource(fake_visa):
    r = diagnostics.probe_resource(GS_A, timeout_ms=2000, backend="")
    assert (r.maker, r.model, r.idn_serial) == ("YOKOGAWA", "GS210", "91AB00001")
    rm = fake_visa[0]
    rm.idn["USB0::0x0B21::0x0039::NOPE::INSTR"] = OSError("VI_ERROR_RSRC_NFOUND")
    with pytest.raises(ConnectionError) as e:
        diagnostics.probe_resource("USB0::0x0B21::0x0039::NOPE::INSTR", backend="")
    assert "USB-TMC" in str(e.value) and "NI-VISA" in str(e.value)


def test_visa_transport_uses_open_timeout_and_termination(fake_visa):
    t = transport.VisaTransport(GS_A, timeout_ms=4000, backend="")
    rm = fake_visa[0]
    assert rm.opened[-1] == (GS_A, transport.OPEN_TIMEOUT_MS)
    assert t.resource.timeout == 4000 and t.resource.read_termination == "\n"
    assert t.query("*IDN?").startswith("YOKOGAWA")
    t.close()
