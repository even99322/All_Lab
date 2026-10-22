"""v3.6：掃頻被 AEDT 拒絕時退回、安裝程式更新。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Sweep:
    name = "Sweep"


class _Setup:
    def __init__(self, reject):
        self.reject, self.tried, self.deleted = reject, [], []

    def delete_sweep(self, name):
        self.deleted.append(name)

    def create_frequency_sweep(self, **kw):
        self.tried.append(kw["sweep_type"])
        if kw["sweep_type"] in self.reject:
            raise RuntimeError("GrpcApiError: Failed to execute gRPC AEDT command: InsertFrequencySweep")
        return _Sweep()


def _worker():
    from hfss_worker import HfssWorker
    w = HfssWorker.__new__(HfssWorker)
    w.params = {}
    w.logs = []
    w.emit_log = w.logs.append
    return w


def test_sweep_falls_back():
    w = _worker()
    st = _Setup({"Fast"})
    sw = w._add_sweep(st, "Sweep", "Fast", 1, 10, 401, False, 0.5)
    assert sw._qel_mode == "Interpolating" and st.tried == ["Fast", "Interpolating"]
    assert any("InsertFrequencySweep" in x for x in w.logs)
    st = _Setup({"Fast", "Interpolating", "Discrete"})
    assert w._add_sweep(st, "Sweep", "Fast", 1, 10, 401, False, 0.5) is None
    assert st.tried == ["Fast", "Interpolating", "Discrete"]


def test_setup_update_paths():
    import updater
    assert updater.is_setup("HFSSTool_v3.6_windows-setup.exe") and not updater.is_setup("HFSSTool_v3.5_windows.exe")
    p = updater.new_exe_path("HFSSTool_v3.6_windows-setup.exe")
    assert p.endswith("HFSSTool_v3.6_windows-setup.exe") and os.path.dirname(p) == updater.SETTINGS_DIR
    assert updater.default_install_exe().endswith(os.path.join("Programs", "HFSSTool", "HFSSTool.exe"))
    assert updater.installed_dir() is None                          # 原始碼版
