"""v3.9：YIG 2D 熱圖（偏置 × 頻率）、執行步驟清單、求解中的進度。"""
import json
import os
import sys
import types

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["HFSS_NO_UPDATE_CHECK"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])


def _fields(vals):
    import yigmap
    return yigmap.fields_for(vals, "external_mT", 0.18, 2.005)


def test_yigmap_points_and_files(tmp_path):
    import yigmap
    vals = yigmap.bias_points(170, 190, 5)
    assert list(vals) == [170, 175, 180, 185, 190]
    fl = _fields(vals)
    assert fl["f_kittel"][2] == pytest.approx(5.0512, abs=1e-3) and np.all(np.diff(fl["h_int"]) > 0)
    f0, f1 = yigmap.auto_freq_range(fl["f_kittel"])
    assert f0 < fl["f_kittel"].min() and f1 > fl["f_kittel"].max()
    args = yigmap.parametric_args("P", "Setup_YIGMap", "YIG_Hint", fl["h_int"][0], fl["h_int"][-1], 5)
    sweep = args[args.index("Sim. Setups:=") + 2]
    assert args[0] == "NAME:P" and sweep[1][2] == "YIG_Hint" and sweep[1][4].startswith("LINC ") and sweep[1][4].endswith(" 5")
    assert args[3][1:] == ["SaveFields:=", False, "CopyMesh:=", True, "SolveWithCopiedMeshOnly:=", True]
    f = np.linspace(4.5, 5.6, 101)
    m = yigmap.MapData(vals, "external_mT", fl, f, {"project": "P"})
    for i, fk in enumerate(fl["f_kittel"]):
        m.set_row("S21", i, 1 - 0.5 / (1 - 2j * (f - fk) / 0.01))
    m.set_row("S11", 0, np.full(101, 0.1 + 0j))
    base = str(tmp_path / "S_para_yigmap")
    path = m.save(base)
    r = yigmap.MapData.load(path)
    assert r.done_rows() == 5 and np.allclose(r.s["S21"], m.s["S21"]) and r.meta["project"] == "P"
    assert np.isnan(r.s["S11"][1]).all() and r.default_param() == "S21"
    assert os.path.exists(m.save_csv(base)) and os.path.exists(m.save_png(base))
    with pytest.raises(ValueError):
        m.set_row("S21", 0, np.zeros(5))


def test_map_viewer(tmp_path):
    import yigmap
    from map_view import MapViewer, transform
    vals = yigmap.bias_points(170, 190, 9)
    fl = _fields(vals)
    f = np.linspace(4.6, 5.5, 201)
    m = yigmap.MapData(vals, "external_mT", fl, f)
    for i, fk in enumerate(fl["f_kittel"]):
        if i < 6:                                        # 逐點：還沒算完
            m.set_row("S21", i, 1 - 0.5 / (1 - 2j * (f - fk) / 0.01))
    path = m.save(str(tmp_path / "a_yigmap"))
    w = MapViewer(path)
    try:
        assert w.combo_s.currentText() == "S21" and "6/9" in w.windowTitle()
        for k in range(w.combo_view.count()):
            w.combo_view.setCurrentIndex(k)
        w.combo_bg.setCurrentIndex(1)
        ev = types.SimpleNamespace(inaxes=w.ax_map, xdata=float(fl["b_ext_mT"][2]), ydata=float(fl["f_kittel"][2]))
        w._click(ev)
        assert w.sel[0] == 2 and abs(f[w.sel[1]] - fl["f_kittel"][2]) < 0.005
        m.set_row("S21", 8, np.ones(201, complex))       # 檔案更新 → 自動重讀
        import time
        time.sleep(0.02)
        m.save(str(tmp_path / "a_yigmap"))
        os.utime(path, (time.time() + 5, time.time() + 5))
        w._check_update()
        assert "7/9" in w.windowTitle() and w.sel[0] == 2
    finally:
        w.close()
    z = np.array([[1 + 1j, -1 + 0j]])
    assert np.allclose(transform(z, "lin"), [[np.sqrt(2), 1]]) and np.allclose(transform(z, "phase"), [[45, 180]])


def test_monitor_parse():
    import aedt_monitor as mon
    text = ("$begin 'MonConvData'\n\tPass Number=3\n\tMax Mag. Delta S=0.0123\n$end 'MonConvData'\n"
            "$begin 'MapMonGraphicalProgMsg'\n\tTask='Adaptive Pass'\n\tProgress=40\n$end\n"
            "$begin 'MonGenericVariations'\n\tVariation 2 of 11\n$end\n")
    info = mon.parse(text)
    assert info["pass"] == 3 and info["delta_s"] == pytest.approx(0.0123) and info["percent"] == 40
    assert info["variation"] == (2, 11) and "Adaptive" in info["task"]
    d = mon.describe(info)
    assert "第 3 個 pass" in d and "2/11" in d and "40%" in d
    assert mon.parse("") == {} and mon.describe({}) == ""


class _Desk:
    def __init__(self, seq, text):
        self.seq, self.text, self.asked = list(seq), text, 0

    def AreThereSimulationsRunning(self):
        return self.seq.pop(0) if self.seq else False

    def GetMonitorData(self, req):
        self.asked += 1
        return self.text

    def GetMessages(self, *a):
        return []


def _worker(params=None):
    from hfss_worker import HfssWorker
    w = HfssWorker.__new__(HfssWorker)
    w.params = dict({"hpc_cores": 4, "hpc_tasks": 1, "hpc_gpus": 0}, **(params or {}))
    w.logs, w.msgs = [], []
    w.emit_log = w.logs.append
    w._emit = lambda *m: w.msgs.append(m)
    w._last_export = None
    return w


def test_solve_polls_monitor(monkeypatch):
    import hfss_worker
    monkeypatch.setattr(hfss_worker, "POLL_S", 0.0)
    monkeypatch.setattr(hfss_worker, "START_GRACE_S", 0.0)
    w = _worker({"solve_monitor": "monitor"})
    calls = []

    class H:
        odesktop = _Desk([True, True, True, False], "Pass Number=2 Delta S=0.05 Progress=30")
        project_name, design_name = "P", "D"

        def analyze_setup(self, name, blocking=True, **kw):
            calls.append((name, blocking, kw))
            return True

        def save_project(self):
            pass
    assert w._solve(H(), "Setup1", "S 參數") is True
    assert calls[0][1] is False and calls[0][2]["cores"] == 4
    prog = [m for m in w.msgs if m[0] == "progress"]
    assert prog and prog[0][1] == pytest.approx(0.3) and "第 2 個 pass" in prog[0][2] and "S 參數" in prog[0][2]

    class Old(H):                                         # 舊版 PyAEDT 沒有 blocking 參數 → 照舊阻塞
        def analyze_setup(self, name, **kw):
            if "blocking" in kw:
                raise TypeError("unexpected keyword")
            calls.append((name, "blocking"))
            return False
    w2 = _worker({"solve_monitor": "monitor"})
    w2._explain_failure = lambda h: None
    assert w2._solve(Old(), "Setup1") is False and calls[-1] == ("Setup1", "blocking")
    # v3.9.1：預設（關）= 跟 3.8 一樣阻塞，不問 AEDT 進度
    w3 = _worker()
    desk = _Desk([True, False], "Pass Number=2")
    h = H()
    h.odesktop = desk
    calls.clear()
    assert w3._solve(h, "Setup1") is True
    assert calls[0][1] is True and desk.asked == 0 and desk.seq == [True, False]
    # 只看還在不在算：不讀 Simulation Monitor
    w4 = _worker({"solve_monitor": "running"})
    desk = _Desk([True, True, False], "Pass Number=2")
    h.odesktop = desk
    assert w4._solve(h, "Setup1") is True and desk.asked == 0 and calls[-1][1] is False


def test_steps_and_begin():
    w = _worker({"yig_enable": True, "yig_fine_sweep": True, "map_points": 7})
    from hfss_worker import subplan
    assert [x[0] for x in subplan("SIM_S", w.params)][-1] == "SIM_S:yig"
    assert subplan("MAP2D", w.params)[1][1] == "AEDT 求解 7 個偏置"
    w.begin("a")
    w.begin("b")
    w.finish_step(False, "x")
    assert [m for m in w.msgs if m[0] == "step"] == [("step", "a", "run", ""), ("step", "a", "done", ""),
                                                    ("step", "b", "run", ""), ("step", "b", "fail", "x")]


def test_step_panel_estimates():
    from progress_panel import StepPanel
    p = StepPanel()
    p.history = {"connect": 10, "SIM_S:solve": 100}
    p.set_plan([["connect", "連線"], ["SIM_S:solve", "求解"], ["SIM_S:export", "匯出"]])
    assert p.overall() == 0 and p.remaining() is None              # 匯出沒有紀錄 → 不估
    p.set_step("connect", "run")
    p.set_step("connect", "done")
    p.set_step("SIM_S:solve", "run")
    p.set_progress(0.5, "Adaptive 第 3 個 pass")
    assert p.current() == "SIM_S:solve" and p.rows["SIM_S:solve"]["item"].text(3) == "Adaptive 第 3 個 pass"
    assert 0.1 < p.overall() < 0.9
    p.history["SIM_S:export"] = 5
    assert p.remaining() is not None and "2/3" not in p.summary() and "1/3" in p.summary()
    p.finish(False)
    assert p.rows["SIM_S:solve"]["status"] == "fail" and p.rows["SIM_S:export"]["status"] == "skip"


class _FakeData:
    """像 PyAEDT 的 SolutionData：variations、set_active_variation、get_expression_data。"""

    def __init__(self, hs, f, fk):
        self.hs, self.f, self.fk = hs, f, fk
        self.variations = [{"YIG_Hint": h} for h in hs]
        self.expressions = ["S(1,1)", "S(2,1)"]
        self.units_sweeps = {"Freq": "GHz"}
        self.i = 0

    def set_active_variation(self, i):
        self.i = i

    def get_expression_data(self, expr, formula="real"):
        s21 = 1 - 0.5 / (1 - 2j * (self.f - self.fk[self.i]) / 0.01)
        v = s21 if expr == "S(2,1)" else 0.1 * np.ones_like(self.f)
        return self.f, (np.real(v) if formula == "real" else np.imag(v))


def _map_hfss(fail_param=False):
    rec = {"bias": [], "opt": [], "vars": {}, "analyze": []}

    class Mod:
        def __init__(self, name):
            self.name = name

        def AssignMagneticBias(self, args):
            rec["bias"].append(args[args.index("Bias:=") + 1])

        def InsertSetup(self, kind, args):
            if fail_param:
                raise RuntimeError("Optimetrics not licensed")
            rec["opt"].append((kind, args))

        def DeleteSetups(self, names):
            pass

    class Design:
        def GetModule(self, name):
            return Mod(name)

    class Post:
        def __init__(self, h):
            self.h = h

        def available_report_quantities(self, **kw):
            return ["S(1,1)", "S(2,1)"]

        def get_solution_data(self, **kw):
            h = self.h
            f = np.linspace(h.f0, h.f1, h.nf)
            if kw.get("variations"):
                return _FakeData(h.hs, f, h.fk)
            k = int(np.argmin(np.abs(np.array(h.hs) - h.cur)))
            d = _FakeData([h.cur], f, [h.fk[k]])
            return d

    class H:
        solution_type = "Modal"
        odesktop = _Desk([], "")
        project_name, design_name = "P", "D"
        boundaries = []
        modeler = types.SimpleNamespace(object_names=["YIG_Sphere"], refresh_all_ids=lambda: None)

        def __init__(self):
            self.odesign = Design()
            self.post = Post(self)

        def __setitem__(self, k, v):
            rec["vars"][k] = v

        def analyze_setup(self, name, blocking=True, **kw):
            rec["analyze"].append(name)
            try:
                self.cur = float(rec["bias"][-1])
            except (ValueError, IndexError):
                pass
            return True

        def save_project(self):
            pass
    return H(), rec


@pytest.mark.parametrize("method", ["param", "loop", "fallback"])
def test_run_bias_map(tmp_path, monkeypatch, method):
    import hfss_worker
    import yig
    import yigmap
    monkeypatch.setattr(hfss_worker, "POLL_S", 0.0)
    monkeypatch.setattr(hfss_worker, "START_GRACE_S", 0.0)
    params = {"yig_enable": True, "yig_bias_mode": "external_mT", "yig_ms_t": 0.18, "yig_g": 2.005,
              "yig_bias_value": 180, "yig_bias_dir": "+Z (垂直板面)", "map_start": 170, "map_stop": 190,
              "map_points": 5, "map_f0": 4.6, "map_f1": 5.5, "map_fpoints": 91,
              "map_method": "loop" if method == "loop" else "param", "csv_path": str(tmp_path / "S_para.csv"),
              "interp_tol": 0.5}
    w = _worker(params)
    hfss, rec = _map_hfss(fail_param=(method == "fallback"))
    vals = yigmap.bias_points(170, 190, 5)
    fl = yigmap.fields_for(vals, "external_mT", 0.18, 2.005)
    # 模型的「答案」：共振比 Kittel 高 0.8%
    hfss.hs, hfss.fk = [float(h) for h in fl["h_int"]], fl["f_kittel"] * 1.008
    hfss.f0, hfss.f1, hfss.nf = 4.6, 5.5, 91
    hfss.cur = hfss.hs[0]
    setups = []
    w._create_adaptive_setup = lambda h, name, mode, freqs: setups.append((name, mode, freqs)) or object()
    w._add_sweep = lambda setup, name, mode, f0, f1, n, sf, tol: types.SimpleNamespace(name=name, _qel_mode=mode)
    w._explain_failure = lambda h: None
    assert w.run_bias_map(hfss) is True
    path = str(tmp_path / "S_para_yigmap.npz")
    m = yigmap.MapData.load(path)
    assert m.done_rows() == 5 and m.s["S21"].shape == (5, 91)
    i = int(np.argmin(np.abs(m.s["S21"][2])))                         # 第 3 個偏置的 dip 在 1.008 × Kittel
    assert abs(m.freq_ghz[i] - fl["f_kittel"][2] * 1.008) < 0.011
    assert setups[0][0] == "Setup_YIGMap" and setups[0][1] == "Multi-Frequencies"
    assert any(x[0] == "map_result" for x in w.msgs)
    assert rec["bias"][-1] == f"{yig.bias_fields(180, 'external_mT', 0.18, 2.005)['H_int']:.6g}"   # 改回原本的偏置
    if method == "param":
        assert rec["opt"] and rec["analyze"] == ["ParametricSetup_YIGMap"] and "YIG_Hint" in rec["bias"]
    else:
        assert rec["analyze"].count("Setup_YIGMap") == 5
        if method == "fallback":
            assert any("改成逐點" in x for x in w.logs)
