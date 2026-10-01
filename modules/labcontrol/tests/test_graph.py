"""節點圖（SchemeGraph）與 Labber 式 step 設定。"""
import numpy as np
import pytest

from labcontrol.measure.sweep import Axis, SweepPlan
from labcontrol.scheme import TEMPLATES, Block, Scheme, build_catalog, compile_scheme
from labcontrol.scheme.graph import BODY, NEXT, START, GraphError, SchemeGraph


def _loop(target, a, b, step, **kw):
    return Block("set", target=target, mode="sweep", start=a, stop=b, step=step, unit="mA", **kw)


def test_graph_roundtrip_matches_templates():
    for key, (_, fn) in TEMPLATES.items():
        s = fn()
        g = SchemeGraph.from_blocks(s.blocks)
        assert [b.to_dict() for b in g.to_blocks()] == [b.to_dict() for b in s.blocks], key
        g2 = SchemeGraph.from_dict(g.to_dict())
        assert [b.to_dict() for b in g2.to_blocks()] == [b.to_dict() for b in s.blocks], key


def test_graph_editing_rules():
    g = SchemeGraph()
    a = g.add(_loop("magnet_A", 50, 51, 1))
    m = g.add(Block("measure", instrument="VNA1", traces=["S21"]))
    d = g.add(Block("save"))
    g.connect(START, NEXT, a.id)
    g.connect(a.id, BODY, m.id)
    g.connect(a.id, NEXT, d.id)
    tree = g.to_blocks()
    assert [b.kind for b in tree] == ["set", "save"] and tree[0].children[0].kind == "measure"
    with pytest.raises(GraphError):
        g.connect(m.id, NEXT, a.id)           # 不能形成環
    with pytest.raises(GraphError):
        g.connect(m.id, BODY, d.id)           # 量測節點沒有 body 輸出
    # 插入在連線上
    w = g.add(Block("wait", seconds=1))
    g.insert_on_link(a.id, BODY, w.id)
    assert [c.kind for c in g.to_blocks()[0].children] == ["wait", "measure"]
    # 刪除中間節點自動接回
    g.remove(w.id)
    assert [c.kind for c in g.to_blocks()[0].children] == ["measure"]
    # 迴圈改成固定值：迴圈內容接回主流程
    g.set_mode(a.id, "fixed")
    assert [b.kind for b in g.to_blocks()] == ["set", "measure", "save"]
    # 沒接上的節點
    x = g.add(Block("wait"))
    assert g.loose() == [x.id]


def test_scheme_v2_file_roundtrip(tmp_path):
    s = TEMPLATES["2d_file_per_outer"][1]()
    s.ensure_graph()
    s.graph.pos[s.blocks[0].id] = (123.0, 45.0)
    p = tmp_path / "x.scheme.yaml"
    s.save(p)
    s2 = Scheme.load(p)
    assert s2.graph is not None and s2.graph.pos[s.blocks[0].id] == (123.0, 45.0)
    assert [b.to_dict() for b in s2.blocks] == [b.to_dict() for b in s.blocks]


def test_axis_log_and_alternate():
    ax = Axis.from_config({"target": "x", "start": 1, "stop": 100, "num": 3, "interp": "log"})
    np.testing.assert_allclose(ax.values, [1, 10, 100])
    outer = Axis.from_config({"target": "a", "values": [0, 1, 2]})
    inner = Axis.from_config({"target": "b", "values": [0, 1], "alternate": True})
    plan = SweepPlan([outer, inner])
    assert [plan.multi_index(i) for i in range(6)] == [(0, 0), (0, 1), (1, 1), (1, 0), (2, 0), (2, 1)]


def test_compile_labber_step_options(station):
    cat = build_catalog(station)
    loop = Block("set", target="pair", mode="sweep", start=158.60, stop=158.62, points=3, unit="mA",
                 alternate=True, after="stay", children=[Block("measure", instrument="VNA1", traces=["S21"])])
    s = Scheme("t", [loop], output={"formats": ["hdf5"], "file_name": "a.hdf5"}, run={"point_delay": 0.3})
    r = compile_scheme(s, cat)
    assert r.ok, r.issues
    ax = r.config["sweep"][0]
    assert ax["alternate"] is True and ax["settle"] == pytest.approx(0.3)
    assert r.config["run"]["park"] == {ax["name"]: "none"}
    assert r.n_files == 1 and r.config["output"]["export"] == [{"type": "hdf5"}]
    assert any("沒有 Data 節點" in i.message for i in r.issues)
    loop.interp = "log"
    loop.points = None
    loop.step = 0.01
    assert not compile_scheme(s, cat).ok     # 對數間隔必須用點數


def test_file_settings_from_output(station):
    cat = build_catalog(station)
    s = Scheme("t", [_loop("pair", 158.60, 158.61, 0.01, children=[Block("measure", instrument="VNA1",
                                                                          traces=["S21"])])],
               output={"formats": ["labber"], "file_name": "x.hdf5", "user": "me", "project": "P/Q",
                       "tags": ["A"], "comment": "hello", "collision": "overwrite"})
    r = compile_scheme(s, cat)
    e = r.config["output"]["export"][0]
    assert e == {"type": "labber", "project": "P/Q", "user": "me", "tags": ["A"], "comment": "hello"}
    assert r.config["output"]["collision"] == "overwrite" and r.config["output"]["file_name"] == "x.hdf5"


def test_runner_park_per_axis(station):
    from labcontrol.measure import Experiment
    from tests.conftest import experiment_cfg

    cfg = experiment_cfg(stop=158.602)
    name = cfg["sweep"][0]["name"]
    cfg["run"]["park"] = {name: "none"}
    exp = Experiment(station, cfg)
    exp.create_runner().run()
    assert station.source("pair").get_level() == pytest.approx(158.602e-3, abs=2e-6)   # 停在最後一點
