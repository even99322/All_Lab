"""數據檔 → 量測設定：Lab Control 原生 / 匯出的 Labber（含方案）、一般 Labber（套用 VNA 與掃描範圍）。"""
from __future__ import annotations

import json

import h5py
import numpy as np

from labcontrol.integrations.file_settings import apply_to_scheme, read_file_settings, scheme_from_file
from labcontrol.scheme.model import Scheme
from labcontrol.testing.fake_labber import write_labber_like
from labcontrol.paths import DEFAULTS_DIR


def template(name="02_2d"):
    return Scheme.load(DEFAULTS_DIR / "templates" / f"{name}.scheme.yaml")


def test_labber_settings_applied(tmp_path):
    p = write_labber_like(tmp_path / "old.hdf5",
                          [("Average Current", "A", np.linspace(0.12, 0.13, 21))],
                          {"VNA - ZNB": {"Start frequency": 6.1e9, "Stop frequency": 6.2e9, "# of points": 801,
                                         "Output power": -25.0, "IF bandwidth": 1000.0, "# of averages": 4}},
                          tags=["transmon", "sample-A"])
    fs = read_file_settings(p)
    assert fs.source == "labber" and fs.scheme is None and fs.tags == ["transmon", "sample-A"]
    st = fs.steps[0]
    assert st.channel == "Average Current" and st.points == 21 and abs(st.start - 0.12) < 1e-12
    assert "Average Current" in fs.summary()
    sch = template()
    changes = apply_to_scheme(sch, fs)
    loop = sch.blocks[0]
    meas = loop.children[0]
    assert meas.settings["start_freq"] == "6.1 GHz" and meas.settings["points"] == 801
    assert meas.settings["power"] == "-25 dBm" and meas.settings["if_bw"] == "1 kHz" and meas.settings["averages"] == 4
    assert (loop.start, loop.stop, loop.points) == (120.0, 130.0, 21) and loop.step is None
    assert any("Average Current" in c for c in changes)


def test_labber_without_step_config_uses_data(tmp_path):
    p = write_labber_like(tmp_path / "d.hdf5", [("Average Current", "A", [0.1, 0.2, 0.3])], {}, step_config=False)
    st = read_file_settings(p).steps[0]
    assert (st.start, st.stop, st.points) == (0.1, 0.3, 3)


def test_graph_scheme_updates_nodes(tmp_path):
    from labcontrol.scheme.graph import SchemeGraph

    sch = template()
    sch.graph = SchemeGraph.from_blocks(sch.blocks)
    p = write_labber_like(tmp_path / "g.hdf5", [("Average Current", "A", np.linspace(0.0, 0.01, 11))],
                          {"VNA": {"# of points": 101}})
    apply_to_scheme(sch, read_file_settings(p))
    node = next(b for b in sch.graph.nodes.values() if b.kind == "measure")
    assert node.settings["points"] == 101 and sch.blocks[0].children[0].settings["points"] == 101
    assert sch.blocks[0].stop == 10.0


def test_labcontrol_embedded_scheme(tmp_path):
    sch = template("03_2d_async")
    meta = {"experiment": {"scheme": sch.to_dict(), "output": {"export": [{"type": "labber", "tags": ["JPA"]}]}}}
    p = write_labber_like(tmp_path / "lc.hdf5", [("Average Current", "A", [0.1, 0.2])], {})
    from labcontrol.data.writers.labber import embed_metadata

    assert embed_metadata(p, meta)
    fs = read_file_settings(p)
    assert fs.source == "labcontrol" and fs.tags == ["JPA"]
    again = scheme_from_file(fs)
    assert again.to_dict()["blocks"] == sch.to_dict()["blocks"]
    native = tmp_path / "n.lm.h5"
    with h5py.File(native, "w") as f:
        f.attrs["metadata"] = json.dumps(meta)
    assert read_file_settings(native).scheme["name"] == sch.name
