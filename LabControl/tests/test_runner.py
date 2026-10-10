import threading
import time

import numpy as np
import pytest

from labcontrol import InstrumentBusy
from labcontrol.data.readers import open_dataset
from labcontrol.measure import Experiment, State

from .conftest import experiment_cfg


def wait_for(pred, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.005)
    raise TimeoutError


def test_auto_run_export_and_park(station, tmp_path):
    exp = Experiment(station, experiment_cfg())
    out = exp.plan_output(root=tmp_path)
    ds = exp.create_runner(out).run()
    assert len(ds) == 41 and ds.planned_points == 41
    sp = ds.setpoints("Average Current")
    np.testing.assert_allclose(sp, np.round(np.arange(41) * 5e-7 + 0.1586, 12))
    assert station.source("pair").get_level() == pytest.approx(0.1586)      # park: start
    assert ds.column("S21").shape == (41, 101)
    assert ds.metadata["snapshot"]["VNA1"]["driver"] == "sim.vna"

    raw = open_dataset(out.raw_path)
    np.testing.assert_allclose(raw.column("S21"), ds.column("S21"))
    paths = exp.export(ds, out)
    assert paths[0].name == "0929 test.h5"
    assert exp.export(ds, out)[0].name == "0929 test_1.h5"                  # 同名自動遞增


def test_pause_rollback_resume(station, tmp_path):
    exp = Experiment(station, experiment_cfg(stop=158.605, step=0.0005))
    exp.plan.axes[0].settle = 0.03
    r = exp.create_runner(exp.plan_output(root=tmp_path))
    r.start()
    wait_for(lambda: r.dataset is not None and len(r.dataset) >= 5)
    r.pause()
    wait_for(lambda: r.state == State.PAUSED)
    n = len(r.dataset)
    r.rollback()
    r.rollback()
    wait_for(lambda: len(r.dataset) == n - 2)
    target = r.plan.setpoints(n - 2)["Average Current"]
    wait_for(lambda: abs(station.source("pair").get_level() - target) < 1e-12)   # 暫停中回溯立即套用
    r.resume()
    assert r.wait(20)
    assert r.state == State.FINISHED
    assert [rec.index for rec in r.dataset.records] == list(range(len(r.plan)))


def test_manual_mode_retain_and_loop(station, tmp_path):
    exp = Experiment(station, experiment_cfg(stop=158.602, manual=True))
    out = exp.plan_output(root=tmp_path)
    r = exp.create_runner(out)
    waiting = []
    shots = []
    station.bus.subscribe("manual.waiting", lambda t, p: waiting.append(p["index"]))
    station.bus.subscribe("manual.shots", lambda t, p: shots.append(p["count"]))
    r.start()
    wait_for(lambda: waiting == [0])
    r.measure_once(); r.measure_once()
    wait_for(lambda: shots and shots[-1] == 2)
    r.accept(retain=True)
    wait_for(lambda: waiting == [0, 1])
    r.loop_start()
    wait_for(lambda: shots[-1] >= 3)
    r.loop_stop()
    r.accept(retain=False)
    wait_for(lambda: len(waiting) == 3)
    r.set_manual(False)
    assert r.wait(20) and r.state == State.FINISHED
    ds = r.dataset
    assert ds.records[0].retained and len(ds.records[0].shots) == 2 and ds.records[0].selected == 1
    assert not ds.records[1].retained and len(ds.records[1].shots) == 1
    assert Experiment.needs_review(ds)
    back = open_dataset(out.raw_path)
    assert len(back.records[0].shots) == 2 and back.records[0].retained


def test_error_retry_then_pause(station, tmp_path, monkeypatch):
    exp = Experiment(station, experiment_cfg(stop=158.601, retries=1, on_error="pause"))
    r = exp.create_runner(exp.plan_output(root=tmp_path))
    station.connect()
    vna = station.instruments["VNA1"]
    real = vna.acquire
    calls = {"n": 0}

    def flaky(name=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("VNA 沒回應")
        return real(name)

    monkeypatch.setattr(vna, "acquire", flaky)
    r.start()
    wait_for(lambda: r.state == State.PAUSED)
    assert calls["n"] == 2 and len(r.dataset) == 0
    r.resume()
    assert r.wait(20) and r.state == State.FINISHED and len(r.dataset) == 3


def test_hook_autopause(station, tmp_path):
    cfg = experiment_cfg(start=158.68, stop=158.77, step=0.001)
    cfg["hooks"][0]["enabled"] = True
    exp = Experiment(station, cfg)
    r = exp.create_runner(exp.plan_output(root=tmp_path))
    reasons = []
    station.bus.subscribe("run.hook", lambda t, p: (reasons.append(p["reason"]), threading.Timer(0.05, r.resume).start()))
    r.run()
    assert r.state == State.FINISHED
    assert len(reasons) == 2 and "1 ⇌ 2" in reasons[0] and "2 ⇌ 1" in reasons[1]


def test_stop_parks_and_lease_blocks_others(station, tmp_path):
    exp = Experiment(station, experiment_cfg(stop=158.65))
    exp.plan.axes[0].settle = 0.02
    r = exp.create_runner(exp.plan_output(root=tmp_path))
    r.start()
    wait_for(lambda: r.dataset is not None and len(r.dataset) >= 3)
    with pytest.raises(InstrumentBusy):
        station.source("DC3").set_level(0.1)      # 其他前端在量測中寫入 → 拒絕
    r.stop()
    assert r.wait(20)
    assert r.state == State.ABORTED
    assert station.source("pair").get_level() == pytest.approx(0.1586)
    station.source("DC3").set_level(0.15861)      # 量測結束後 lease 釋放
