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


def test_yig_mesh_estimate_and_hint():
    import yig
    assert 250_000 < yig.mesh_estimate(1.2, 0.05) < 290_000          # AEDT 估 270,567
    assert yig.auto_mesh_mm(1.2) == 0.15 and yig.mesh_estimate(1.2, 0.15) < yig.MESH_WARN_TETS
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["HFSS_NO_UPDATE_CHECK"] = "1"
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from ui_main import MainWindow
    w = MainWindow()
    try:
        w.edit_yig_d.setText("1.2")
        w.edit_yig_mesh.setText("0.05")
        assert "⚠️" in w.lbl_yig_mesh_est.text()
        w.edit_yig_mesh.setText("")
        assert "自動 0.15" in w.lbl_yig_mesh_est.text()
    finally:
        w.close()


def test_explain_failure_reads_aedt_messages():
    w = _worker()

    class D:
        def GetMessages(self, *a):
            return ["[warning] Solving adaptive frequency ..., process hf3d: Out of memory",
                    "[error] Insufficient memory to solve with the current analysis configuration.",
                    "[info] Seed Refinement Estimates for body YIG_Sphere might reach 270,567 tets from 526 tets."]

    class H:
        odesktop, project_name, design_name = D(), "P", "D"
    w._explain_failure(H())
    text = "\n".join(w.logs)
    assert "Insufficient memory" in text and "球體網格邊長" in text and "Multi-Frequencies" in text
