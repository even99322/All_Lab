"""1.0.12：即時數據的常用設定（快速切換）、VNA 平均改成「勾選 ＋ 次數」（關閉時次數保留）。"""
from __future__ import annotations

import pytest

from tests.test_live_gui import pump, win  # noqa: F401


def test_vna_average_switch_and_presets(win, monkeypatch):
    from labcontrol.apps.qt.live.vna_panel import AvgEdit

    live = win.live
    win.station.connect(["VNA1"])
    vna = live.vna
    vna.reload()
    assert pump(lambda: vna.name == "VNA1" and "averages" in vna.edits)
    assert pump(lambda: vna.edits["start_freq"].text() not in ("", "—"))
    avg = vna.edits["averages"]
    assert isinstance(avg, AvgEdit)
    # 開：勾選 + 次數 → 寫 n；關：寫 0，次數留著
    avg.check.setChecked(True)
    avg.count.setEnabled(True)
    avg.count.setText("16")
    vna._mark("averages")
    assert ("VNA1.averages", 16) in vna.pending()
    vna.apply()
    assert pump(lambda: win.station.parameter("VNA1.averages").get() == 16)
    assert pump(lambda: not vna.dirty)
    avg.check.click()                                        # 關掉
    assert not avg.count.isEnabled() and avg.count.text() == "16"
    assert ("VNA1.averages", 0) in vna.pending()
    vna.apply()
    assert pump(lambda: win.station.parameter("VNA1.averages").get() == 0)
    vna.read_params()
    assert pump(lambda: not avg.check.isChecked())
    assert avg.count.text() == "16"                          # 讀回 0 → 仍顯示上次的次數
    avg.check.click()
    assert avg.value() == 16
    avg.check.click()

    # 常用設定：存 → 改設定 → 點一下切回
    vna.edits["power"].setText("-30")
    vna._mark("power")
    vna.apply()
    assert pump(lambda: win.station.parameter("VNA1.power").get() == pytest.approx(-30) and not vna.dirty)
    vna.read_params()
    assert pump(lambda: vna.edits["power"].text() == "-30")
    first = vna.save_preset(name="寬掃")
    assert first["values"]["power"] == "-30" and first["values"]["averages"] == [False, 16]
    assert "center_freq" not in first["values"] and "span" not in first["values"]
    assert [b.text() for b in vna.preset_buttons] == ["寬掃"]
    vna.edits["power"].setText("-12")
    vna._mark("power")
    vna.edits["averages"].set_value(4)
    vna._mark("averages")
    vna.apply()
    assert pump(lambda: win.station.parameter("VNA1.power").get() == pytest.approx(-12) and not vna.dirty)
    vna.save_preset(name="窄掃")
    assert [b.text() for b in vna.preset_buttons] == ["寬掃", "窄掃"]
    vna.preset_buttons[0].click()                             # 切回「寬掃」：套用到 VNA
    assert pump(lambda: win.station.parameter("VNA1.power").get() == pytest.approx(-30))
    assert pump(lambda: win.station.parameter("VNA1.averages").get() == 0)
    assert vna.preset_buttons[0].isChecked() and not vna.preset_buttons[1].isChecked()
    # 存在這台電腦（live_ui.json），重開面板還在；刪除
    assert [p["name"] for p in vna.ui.load()["vna_presets"]] == ["寬掃", "窄掃"]
    vna.delete_preset(0)
    assert [b.text() for b in vna.preset_buttons] == ["窄掃"]
    vna.delete_preset(0)


def test_measure_dialog_average_switch(win):
    """量測方塊的 VNA 設定：平均一樣是勾選 ＋ 次數；關閉存 0。"""
    from labcontrol.apps.qt.live.vna_panel import AvgEdit
    from labcontrol.apps.qt.workbench.dialogs import MeasureDialog
    from labcontrol.scheme.model import Block

    b = Block("measure", instrument="VNA1", traces=["S21"], settings={"points": 101, "averages": 0})
    d = MeasureDialog(win, win.doc, b)
    _f, w = d._fields["averages"]
    assert isinstance(w, AvgEdit) and not w.check.isChecked()
    w.check.click()
    w.count.setText("8")
    assert d.result_block().settings["averages"] == 8
    w.check.click()
    assert d.result_block().settings["averages"] == 0 and w.count.text() == "8"
    d.close()


# ---- 手機 / 網頁控制電磁鐵（Hub 的 /magnet → 節點指令）；量測進行中拒絕寫入 ----------------------
from tests.test_remote import _wait, hub, hubsrv, node  # noqa: E402,F401


def test_magnets_from_the_hub_and_lock_during_run(node, hubsrv, tmp_path):
    import json
    import urllib.request

    from labcontrol.core import live_groups as lg
    from labcontrol.paths import lab_path
    from labcontrol.remote.node import CommandError

    hub, state, _rel = hubsrv
    st = node.station
    st.connect(["DC3", "DC4", "pair"])                               # 模擬站：pair = 虛擬雙電源（DC3 + DC4）
    sender = {"user": "phone"}
    d = node.cmd_magnets(sender)
    assert not d["locked"] and d["allow_set"] and d["sources"] and d["groups"]
    pair = next((g for g in d["groups"] if len(g["members"]) == 2), d["groups"][0])
    refs = pair["members"]
    # 記憶寫進這台電腦的 live_groups.json（跟即時數據共用）
    node.cmd_magnet_memory(sender, group=pair["index"], slot=1, value=0.1586)
    saved = lg.GroupStore(lab_path("live_groups.json")).load(node._groups_key())
    assert any(g["members"] == refs and g["memory"][1] == 0.1586 for g in saved)
    # 經過 Hub 送指令（網頁用的路徑）
    url = f"{hub.url}/api/nodes/QEL-PC/commands?wait=10"
    req = urllib.request.Request(url, data=json.dumps({"cmd": "magnets", "args": {"read": True}}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {hub.token}"},
                                 method="POST")
    rep = json.loads(urllib.request.urlopen(req, timeout=15).read())["reply"]
    assert rep["ok"] and [g["members"] for g in rep["result"]["groups"]] == [g["members"] for g in d["groups"]]
    # 量測進行中：寫入被拒（讀取仍可，只回傳最後的值）
    node._last_runner = type("R", (), {"_done": __import__("threading").Event()})()
    node.run = {"name": "測試量測"}
    with pytest.raises(CommandError, match="量測進行中"):
        node.cmd_group_ramp(sender, refs, 0.1587)
    with pytest.raises(CommandError, match="量測進行中"):
        node.cmd_fine_step(sender, refs, 1)
    with pytest.raises(CommandError, match="量測進行中"):
        node.cmd_set(sender, refs[0], 0.1)
    d = node.cmd_magnets(sender)
    assert d["locked"] and d["running"] and d["run"]["name"] == "測試量測"
    node._last_runner._done.set()
    node.run = None
    assert not node.cmd_magnets(sender)["locked"]
    node.cmd_fine_step(sender, refs, 1)                              # 量測結束 → 又可以調


def test_magnet_page_and_nav(hubsrv):
    import urllib.request

    hub, _state, _rel = hubsrv
    req = urllib.request.Request(f"{hub.url}/magnet", headers={"Authorization": f"Bearer {hub.token}"})
    text = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
    assert "電磁鐵" in text and 'href="/magnet"' in text and "magnet_memory" in text and "量測進行中" in text
