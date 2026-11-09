"""1.0.33：儀器改名時，方案、範本、專案範本、電源群組、即時數據設定裡的舊名稱一起改。"""
from __future__ import annotations

import json

import yaml

from labcontrol.core import instedit, rename_refs
from labcontrol.core.station import Station
from labcontrol.scheme import Block, Scheme
from tests.test_instsync import LAB1, _write


def _scheme():
    s = Scheme("FMR", [Block("set", target="magnet_A", mode="sweep", start=0, stop=10, step=1, unit="mA", children=[
        Block("set", target="DC1.ch1.level", mode="fixed", value=1, unit="mA"),
        Block("measure", instrument="VNA1", traces=["S21"], settings={"power": -10})])],
               output={"file_name": "DC1.nc"})
    s.background = {"enabled": True, "sets": [{"target": "DC1", "value": 0, "unit": "mA"}]}
    s.ensure_graph()
    return s


def test_rename_refs_helpers():
    assert rename_refs.ref("DC1", "DC1", "DC9") == "DC9"
    assert rename_refs.ref("DC1.ch1.level", "DC1", "DC9") == "DC9.ch1.level"
    assert rename_refs.ref("DC10", "DC1", "DC9") == "DC10"                     # 只改完整名稱
    d, ch = rename_refs.deep({"DC1|DC2": 0.5, "x": ["DC1.ch1", "DC12"], "DC1": {"a": "DC1"}}, "DC1", "DC9")
    assert ch and d == {"DC9|DC2": 0.5, "x": ["DC9.ch1", "DC12"], "DC9": {"a": "DC9"}}
    s = _scheme()
    assert rename_refs.rename_scheme(s, "DC1", "DC9")
    tg = [b.target for b in s.flat() if b.kind == "set"]
    assert tg == ["magnet_A", "DC9.ch1.level"] and s.background["sets"][0]["target"] == "DC9"
    assert s.output["file_name"] == "DC1.nc"                                      # 檔名不動
    assert not rename_refs.rename_scheme(s, "DC1", "DC9")


def test_rename_instrument_updates_lab_files(tmp_path, monkeypatch):
    lab = tmp_path / "_lab"                     # test_instsync 的 fixture 把 propagate 指到這裡
    (lab / "schemes" / "sub").mkdir(parents=True)
    (lab / "templates").mkdir()
    _scheme().save(lab / "schemes" / "sub" / "fmr.scheme.yaml")
    _scheme().save(lab / "templates" / "my.scheme.yaml")
    (lab / "schemes" / "notes.yaml").write_text("just: text\n", encoding="utf-8")
    (lab / "live_groups.json").write_text(json.dumps({"local:PC": [
        {"name": "magnet_A", "members": ["magnet_A.ch1"], "memory": [0.1, None, None]},
        {"name": "VNA", "members": ["DC1.ch1"], "memory": [None, None, None]}]}), encoding="utf-8")
    (lab / "live_steps.json").write_text(json.dumps({"DC1.ch1|DC2.ch1": 0.001}), encoding="utf-8")
    (lab / "vna_ports.json").write_text(json.dumps({"VNA1": {"wired": ["S21"]}}), encoding="utf-8")
    (lab / "project_templates" / "42").mkdir(parents=True)
    (lab / "project_templates" / "42" / "fmr.json").write_text(json.dumps({"scheme": _scheme().to_dict(), "time": 1}),
                                                               encoding="utf-8")
    from tests.test_instsync import _isolated_rename  # noqa: F401  (fixture 已 autouse 在那個模組；這裡自己設)

    orig = rename_refs.propagate
    monkeypatch.setattr(rename_refs, "propagate",
                        lambda old, new, root=None, push_projects=False: orig(old, new, root=lab, push_projects=False))
    p = _write(tmp_path / "instruments.yaml", LAB1)
    st = Station.from_file(p, simulate=True)
    res = instedit.rename_instrument(st, p, "DC1", "DC9")
    assert set(res["updated"]) >= {"schemes/sub/fmr.scheme.yaml", "templates/my.scheme.yaml", "live_groups.json",
                                   "live_steps.json", "project_templates/42/fmr.json"}
    assert "schemes/notes.yaml" not in res["updated"]
    s = Scheme.load(lab / "schemes" / "sub" / "fmr.scheme.yaml")
    assert "DC9.ch1.level" in [b.target for b in s.flat()] and s.background["sets"][0]["target"] == "DC9"
    assert json.loads((lab / "live_steps.json").read_text())["DC9.ch1|DC2.ch1"] == 0.001
    res = instedit.rename_instrument(st, p, "magnet_A", "magA")
    g = json.loads((lab / "live_groups.json").read_text())["local:PC"]
    assert g[0]["name"] == "magA" and g[0]["members"] == ["magA.ch1"] and g[0]["memory"][0] == 0.1
    assert g[1]["name"] == "VNA" and g[1]["members"] == ["DC9.ch1"]
    instedit.rename_instrument(st, p, "VNA1", "ZNA")
    assert "ZNA" in json.loads((lab / "vna_ports.json").read_text())
    pt = json.loads((lab / "project_templates" / "42" / "fmr.json").read_text())["scheme"]
    s = Scheme.from_dict(pt)
    assert [b.instrument for b in s.flat() if b.kind == "measure"] == ["ZNA"]
    assert [b.target for b in s.flat() if b.kind == "set"][0] == "magA"
    y = yaml.safe_load((lab / "templates" / "my.scheme.yaml").read_text(encoding="utf-8"))
    assert y["name"] == "FMR"
