"""v3.8：從寬頻結果找實際 YIG 共振、細掃自動對準、記下偏移修正下次的預估。"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _side_coupled(f, f0, kappa_e=0.004, kappa_i=0.0005, bg=-0.3):
    """旁接共振腔：S21 在 f0 有 dip、S11 在 f0 有峰（GHz）。"""
    s21 = 1 - kappa_e / (kappa_e + kappa_i - 2j * (f - f0))
    s11 = -kappa_e / (kappa_e + kappa_i - 2j * (f - f0)) + 0.02
    db = lambda c: 20 * np.log10(np.abs(c) + 1e-12)    # noqa: E731
    return {"S21_dB": db(s21) + bg, "S11_dB": db(s11), "S12_dB": db(s21) + bg, "S22_dB": db(s11)}


def test_find_resonance_off_kittel():
    import yig
    f = np.linspace(4.66, 5.66, 1001)
    res = yig.find_resonance(f, _side_coupled(f, 5.0903), 5.0512)
    assert res is not None and res["by"] == "S21" and abs(res["f"] - 5.0903) < 3e-4 and res["depth"] > 3
    flat = {"S21_dB": np.full_like(f, -0.3), "S11_dB": np.full_like(f, -30.0)}
    assert yig.find_resonance(f, flat, 5.0512) is None
    # 只有反射
    only11 = {"S11_dB": _side_coupled(f, 5.2)["S11_dB"]}
    r = yig.find_resonance(f, only11, 5.15)
    assert r is not None and r["by"] == "S11" and abs(r["f"] - 5.2) < 1e-3
    # 範圍外（> ±6%）不算
    assert yig.find_resonance(f, _side_coupled(f, 5.6), 5.05) is None


def test_calibration_and_key():
    import yig
    assert abs(yig.calibrated(5.0512, {"ratio": 5.0903 / 5.0512}) - 5.0903) < 1e-9
    assert yig.calibrated(5.0, None) == 5.0 and yig.calibrated(5.0, {"ratio": 3}) == 5.0
    a = {"dxf_file": r"C:\x\CPW.dxf", "yig_diameter": "1.20", "yig_gap": "0.5", "yig_pos_mode": "auto",
         "yig_dx": "0", "yig_dy": "0", "yig_mesh_mm": "", "yig_bias_dir": "+Z (垂直板面)", "thick_die": "0.813",
         "thick_top": "0.035"}
    b = dict(a, dxf_file="/other/CPW.dxf", yig_diameter=1.2, yig_gap=0.5, yig_dx=0.0, yig_dy=0.0, yig_mesh_mm=None,
             thick_die=0.813, thick_top=0.035)
    assert yig.cal_key(a) == yig.cal_key(b)
    assert yig.cal_key(a) != yig.cal_key(dict(a, yig_gap="0.4"))


class _Sw:
    def __init__(self, name, f0, f1, mode="Interpolating"):
        self.name, self.f0, self.f1, self._qel_mode = name, f0, f1, mode


class _Setup:
    def __init__(self):
        self.made = []

    def delete_sweep(self, name):
        pass

    def create_frequency_sweep(self, **kw):
        sw = _Sw(kw["name"], kw["start_frequency"], kw["stop_frequency"])
        self.made.append(sw)
        return sw


def test_worker_recenters_fine_sweep():
    from hfss_worker import HfssWorker
    import yig
    w = HfssWorker.__new__(HfssWorker)
    w.params = {"csv_path": os.path.join("C:", "t", "S_para.csv"), "yig_span_mhz": 10, "yig_fine_points": 31,
                "yig_fine_tol": 0.1, "yig_track": True, "dxf_file": "CPW.dxf", "yig_diameter": 1.2, "yig_gap": 0.5}
    logs, emitted, analyzed, exports = [], [], [], []
    w.emit_log = logs.append
    w._emit = lambda *m: emitted.append(m)

    class Design:
        def Analyze(self, name):
            analyzed.append(name)

    class H:
        odesign = Design()

    f_true = 5.0903
    cur = [None]

    def fake_export(hfss, setup_name, sweep_name, csv_path=None):
        exports.append((sweep_name, csv_path))
        sw = setup.made[-1] if setup.made else cur[0]
        f = np.linspace(sw.f0, sw.f1, 31) if csv_path else np.linspace(4.66, 5.66, 1001)
        w._last_export = (f, _side_coupled(f, f_true))
        return True

    w._export_s_params = fake_export
    setup = _Setup()
    fake_export(None, "S", "Sweep")                               # 寬頻的結果
    first = _Sw(yig.KITTEL_SWEEP, 5.0412, 5.0612)
    cur[0] = first
    w._yig_track(H(), setup, first, 5.0512, 5.0512, True)
    assert len(analyzed) == 1 and analyzed[0].endswith(f" : {yig.KITTEL_SWEEP}")
    assert setup.made and abs((setup.made[-1].f0 + setup.made[-1].f1) / 2 - f_true) < 1e-3
    assert exports[-1][1].endswith("S_para_kittel.csv")
    kinds = [m for m in emitted if m[0] == "yig_result"]
    assert kinds and abs(kinds[0][1]["f_res"] - f_true) < 2e-4 and abs(kinds[0][1]["ratio"] - f_true / 5.0512) < 1e-4
    text = "\n".join(logs)
    assert "實際共振" in text and "重新細掃" in text and "+0.7" in text

    # 預估已經準（上次校正過）→ 不重掃
    analyzed.clear(); setup.made.clear(); emitted.clear()
    cur[0] = _Sw(yig.KITTEL_SWEEP, 5.0803, 5.1003)
    w._yig_track(H(), setup, cur[0], 5.0512, 5.0903, True)
    assert not analyzed and [m for m in emitted if m[0] == "yig_result"]


def test_ui_remembers_offset(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["HFSS_NO_UPDATE_CHECK"] = "1"
    from PyQt6.QtWidgets import QApplication
    global _APP
    _APP = QApplication.instance() or QApplication([])
    import ui_main
    import yig
    monkeypatch.setattr(ui_main.ConfigManager, "save_settings", staticmethod(lambda s: None))
    w = ui_main.MainWindow()
    try:
        w.edit_yig_ms.setText("0.18"); w.edit_yig_g.setText("2.005")
        w.combo_yig_bmode.setCurrentIndex(0); w.edit_yig_bias.setText("180"); w.edit_yig_gap.setText("0.5")
        key = yig.cal_key(w._yig_key_params())
        w._yig_result({"f_kittel": 5.0512, "f_res": 5.0903, "ratio": 5.0903 / 5.0512, "key": key, "by": "S21"})
        assert "+0.77%" in w.lbl_yig_calc.text() and "5.090" in w.lbl_yig_calc.text()
        w.edit_yig_gap.setText("0.3")
        assert "不同" in w.lbl_yig_calc.text()
        w.edit_yig_gap.setText("0.5")
        assert "這次預估 5.090" in w.lbl_yig_calc.text()
    finally:
        w.close()


def test_task_done_event_to_hub(monkeypatch):
    import usage
    sent = []
    monkeypatch.setattr(usage, "post_event", lambda body, hubs, timeout=5.0: sent.append((body, hubs)) or hubs[0])

    class SyncThread:
        def __init__(self, target, **kw):
            self.target = target

        def start(self):
            self.target()
    monkeypatch.setattr(usage.threading, "Thread", SyncThread)
    r = usage.UsageReporter.__new__(usage.UsageReporter)
    r.disabled, r.hub, r.win = False, None, object()
    r._hubs = lambda: ["http://hub.example:8765"]
    assert r.task_done("SIM_S", True, 4866.6, "MS_YIG_test", "YIG 共振 5.0903 GHz") is True
    body, hubs = sent[0]
    assert body["task"] == "SIM_S" and body["label"] == "S 參數模擬" and body["ok"] is True
    assert body["elapsed"] == 4866.6 and body["project"] == "MS_YIG_test" and "5.0903" in body["detail"]
    assert body["host"] and r.hub == "http://hub.example:8765"
    assert r.task_done("BUILD", True, 3) is False and len(sent) == 1
