import os

import numpy as np
import pytest

import labcontrol.data.writers.labber_export_script as les
from labcontrol import Station
from labcontrol.scheme import TEMPLATES, Block, Scheme, build_catalog, compile_scheme

MAG_LAB = {
    "simulate": True,
    "sim_time_scale": 0.01,
    "instruments": {
        **{f"DC{i}": {"driver": "yokogawa.gs200", "address": "X",
                      "source": {"limits": [-0.2, 0.2], "ramp_rate": 5e-4, "max_jump": 5e-5, "resolution": 1e-6},
                      "sim": {"initial_level": 0.1}} for i in range(1, 5)},
        "VNA1": {"driver": "rs.vna", "address": "X", "label": "VNA-1 (192.168.1.11)", "labber_name": "VNA",
                 "sim": {"sweep_time": 0, "coupling": {"magnet_A": {"I0": 0.075, "df_dI": -8e7},
                                                       "magnet_B": {"I0": 0.125, "df_dI": 1e8}}}},
        "SHFQC1": {"driver": "zi.shfqc", "label": "SHFQC", "sim": {"sweep_time": 0}},
        "magnet_A": {"driver": "virtual.interleaved_pair", "group": "magnet", "label": "磁鐵 A（DC3+DC4）",
                     "sources": ["DC3", "DC4"], "source": {"limits": [-0.2, 0.2], "ramp_rate": 5e-4, "max_jump": 5e-5}},
        "magnet_B": {"driver": "virtual.interleaved_pair", "group": "magnet", "label": "磁鐵 B（DC1+DC2）",
                     "sources": ["DC1", "DC2"], "source": {"limits": [-0.2, 0.2], "ramp_rate": 5e-4, "max_jump": 5e-5}},
    },
}


@pytest.fixture
def mst():
    st = Station(MAG_LAB)
    yield st
    from labcontrol.measure import Runner

    Runner.stop_all(wait=10)
    st.close()


@pytest.fixture
def cat(mst):
    return build_catalog(mst)


def errors(res):
    return [i.message for i in res.issues if i.level == "error"]


def test_templates_compile_like_the_sketches(cat):
    r = {k: compile_scheme(fn(), cat) for k, (_, fn) in TEMPLATES.items()}
    assert all(x.ok for x in r.values()), {k: errors(x) for k, x in r.items()}
    assert [lp.axis_name for lp in r["02_2d"].loops] == ["Average Current"]
    # 單張：沒有迴圈，量一次
    assert not r["01_single_shot"].loops and r["01_single_shot"].total_points == 1
    # 2D 電流異步：平均每點走一半（每台步進 0.01 mA → 0.005 mA，150→152 共 401 點），setup 設交錯網格
    a = r["03_2d_async"]
    assert a.loops[0].n == 401 and a.config["sweep"][0]["step"] == pytest.approx(0.005)
    assert a.config["setup"]["magnet_A.interleave_step"] == pytest.approx(1e-5)
    assert a.config["setup"]["magnet_A.interleave_origin"] == pytest.approx(0.150)
    assert r["02_2d"].config["setup"]["magnet_A.interleave_step"] == 0.0           # 一般 2D：兩台同步
    # N 層：3 個軸、兩個 Data（每個 A 值一個檔 ＋ 完整一個檔）
    n = r["04_n_layer"]
    assert [s["target"] for s in n.config["sweep"]] == ["magnet_A.level", "magnet_B.level", "VNA1.power"]
    assert n.config["output"]["split_by"] == ["磁鐵 A 電流"]
    assert n.config["output"]["extra"][0]["file_name"] == "N_layer_all.hdf5"
    assert n.config["output"]["extra"][0]["split_by"] == [] and n.n_files == 11 + 1
    assert r["2d_one_file"].split_depth == 0 and r["2d_one_file"].n_files == 1
    assert r["2d_file_per_outer"].split_depth == 1 and r["2d_file_per_outer"].n_files == 51
    assert r["2d_file_per_outer"].config["output"]["split_by"] == ["磁鐵 A 電流"]
    sw = r["current_power"].config["sweep"]
    assert [s["target"] for s in sw] == ["magnet_A.level", "VNA1.power"]
    assert "power" not in r["current_power"].config["setup"]["VNA1"]      # 功率由掃描控制
    # 2D 內圈每次回起點的斜坡時間有算進估時
    assert r["2d_one_file"].est_breakdown["內圈回到起點（斜坡）"] == pytest.approx(50 * 0.05 / 5e-4)


def test_model_editing_ops():
    s = Scheme("t")
    dc = s.insert(Block("set", target="magnet_A", mode="fixed", value=1, unit="mA"))
    vna = s.insert(Block("measure", instrument="VNA1", traces=["S21"]))
    data = s.insert(Block("save", formats=["hdf5"]))
    s.set_mode(dc.id, "sweep")                       # 自動包住後面的方塊，停在 Data 之前
    assert [c.id for c in dc.children] == [vna.id] and s.blocks[-1] is data
    assert s.indent(data.id) and s.ancestors(data.id) == [dc]
    assert s.outdent(data.id) and s.ancestors(data.id) == []
    inner = s.wrap([vna.id], Block("set", target="magnet_B", start=1, stop=2, step=1, unit="mA"))
    assert s.ancestors(vna.id) == [dc, inner]
    assert s.swap_with_inner(dc.id) and s.blocks[0].target == "magnet_B" and s.blocks[0].children[0].target == "magnet_A"
    s.remove(s.blocks[0].id)                         # 刪除迴圈：裡面的方塊往外提
    assert s.blocks[0].target == "magnet_A" and s.find(vna.id)
    s2 = Scheme.from_dict(s.to_dict())
    assert s2.to_dict() == s.to_dict()


def test_validation_messages(cat):
    s = Scheme("bad", [Block("set", target="magnet_A", mode="sweep", start=0, stop=500, step=1, unit="mA",
                             children=[Block("measure", instrument="VNA1", traces=["S21"]),
                                       Block("measure", instrument="SHFQC1", traces=["QA0"])]),
                       Block("set", target="magnet_A", mode="fixed", value=10, unit="mA"),
                       Block("set", target="magnet_B", mode="sweep", start=1, stop=2, step=1, unit="mA")])
    e = " | ".join(errors(compile_scheme(s, cat)))
    assert "VNA 與 SHFQC 不同時量測" in e
    assert "安全上下限" in e
    assert "同時被固定設定與掃描" in e
    assert "這個迴圈裡沒有量測方塊" in e
    s = Scheme("bad2", [Block("save"), Block("measure", instrument="VNA1", traces=["S21"])])
    assert any("Data 必須放在量測方塊之後" in m for m in errors(compile_scheme(s, cat)))
    assert any("沒有量測方塊" in m for m in errors(compile_scheme(Scheme("x", [Block("save")]), cat)))
    assert not compile_scheme(Scheme("x", [Block("measure", instrument="VNA1", traces=["S21"])]), cat).loops


def small(fn, **kw):
    s = fn()
    for b in s.flat():
        if b.is_loop and b.target == "magnet_A":
            b.start, b.stop, b.step = 50.0, 52.0, 1.0
        elif b.is_loop and b.target == "magnet_B":
            b.start, b.stop, b.step = 100.0, 101.0, 0.5
        elif b.is_loop:
            b.start, b.stop, b.step = 0.0, -10.0, 5.0
        if b.kind == "save":
            b.formats = ["hdf5"]
        if b.kind == "measure":
            b.settings = {**b.settings, "points": 51}
        b.settle = 0
    s.run["approach_rate"] = "50 mA/s"
    s.run["park"] = "none"
    return s


@pytest.mark.parametrize("key,files", [("2d_file_per_outer", 3), ("current_power", 3), ("2d_one_file", 1)])
def test_scheme_runs_end_to_end(mst, cat, tmp_path, key, files):
    res = compile_scheme(small(TEMPLATES[key][1]), cat)
    assert res.ok, errors(res)
    exp = res.experiment(mst)
    out = exp.plan_output(root=tmp_path)
    ds = exp.create_runner(out).run()
    assert len(ds) == ds.planned_points == 9
    paths = exp.export(ds, out)
    assert len(paths) == files and all(p.exists() for p in paths)
    if key == "2d_one_file":                              # 多維 Labber 對應：外層為主排序、內層在前
        spec = les.build_labber_spec(str(out.raw_path))
        assert [c["name"] for c in spec["step_channels"][:2]] == ["磁鐵 B 電流", "磁鐵 A 電流"]
        np.testing.assert_allclose(spec["entry_axes"][0][1], [50, 50, 50, 51, 51, 51, 52, 52, 52])
    else:
        spec = les.build_labber_spec(str(paths[1]))       # 分檔：外圈值成為單一值 step channel
        a = next(c for c in spec["step_channels"] if c["name"] == "磁鐵 A 電流")
        assert a["values"].tolist() == [51.0]


def test_snake_removes_reset_time(cat):
    s = TEMPLATES["2d_one_file"][1]()
    assert compile_scheme(s, cat).est_breakdown["內圈回到起點（斜坡）"] > 0
    s.snake = True
    assert compile_scheme(s, cat).est_breakdown["內圈回到起點（斜坡）"] == 0


def test_async_interleave_runs_like_sweep_main(mst, cat, tmp_path):
    """2D 電流異步：每一點只有一台前進「每台步進」，A 先走（與舊 sweep_main 相同）。"""
    s = TEMPLATES["03_2d_async"][1]()
    b = next(x for x in s.flat() if x.is_loop)
    b.start, b.stop, b.step, b.settle = 100.0, 100.04, 0.01, 0
    for x in s.flat():
        if x.kind == "measure":
            x.settings = {**x.settings, "points": 21}
        if x.kind == "save":
            x.formats = ["hdf5"]
    s.run["approach_rate"] = "50 mA/s"
    s.run["park"] = "none"
    res = compile_scheme(s, cat)
    assert res.ok, errors(res)
    exp = res.experiment(mst)
    ds = exp.create_runner(exp.plan_output(root=tmp_path)).run()
    assert len(ds) == 9                                             # 100 → 100.04，平均每點 0.005 mA
    pairs = [(round(r.setpoints["Average Current"] * 1e3, 6)) for r in ds.records]
    assert pairs == pytest.approx([100 + 0.005 * k for k in range(9)])
    # 結束時（停在最後一點 100.04）兩台都在 0.01 mA 網格上
    assert mst.parameter("DC3.level").get() == pytest.approx(0.10004)
    assert mst.parameter("DC4.level").get() == pytest.approx(0.10004)
    pair = mst.instruments["magnet_A"]
    assert pair.split(0.100005) == (pytest.approx(0.10001), pytest.approx(0.1))   # A 先前進 0.01 mA
    assert pair.split(0.10001) == (pytest.approx(0.10001), pytest.approx(0.10001))
    assert pair.split(0.100015) == (pytest.approx(0.10002), pytest.approx(0.10001))
    # 沒有異步的方案會把網格設回 resolution（兩台同步）
    s2 = small(TEMPLATES["02_2d"][1])
    exp2 = compile_scheme(s2, cat).experiment(mst)
    exp2.create_runner(exp2.plan_output(root=tmp_path)).run()
    assert pair.grid_step == 0.0 and pair.split(0.0515) == (pytest.approx(0.0515), pytest.approx(0.0515))


def test_multiple_data_blocks(mst, cat, tmp_path):
    """多個 Data：外圈每個值一個檔 ＋ 另外一個完整檔（各自檔名 / 格式）。"""
    s = small(TEMPLATES["2d_file_per_outer"][1])
    s.blocks.append(Block("save", file_name="all.hdf5", formats=["hdf5"]))
    s2 = Scheme.from_dict(s.to_dict())
    res = compile_scheme(s2, cat)
    assert res.ok, errors(res)
    assert res.n_files == 3 + 1 and res.extras[0]["file_name"] == "all.hdf5" and res.extras[0]["depth"] == 0
    exp = res.experiment(mst)
    out = exp.plan_output(root=tmp_path)
    assert len(out.extras) == 1 and out.extras[0].split_by == []
    ds = exp.create_runner(out).run()
    paths = exp.export(ds, out)
    assert len(paths) == 4 and all(p.exists() for p in paths)
    assert sum(1 for p in paths if p.stem == "all") == 1 and sum(1 for p in paths if p.stem != "all") == 3
