"""0.0.9：主視窗右上「即時監控 / 量測監控」分頁、電源群組卡片（合併 / 拆開 / 記憶 / 微調）、VNA、獨立視窗。"""
import os
import time

import numpy as np
import pytest

from labcontrol.core import live_groups as lg

pytestmark = pytest.mark.skipif(
    not os.environ.get("QT_QPA_PLATFORM") and os.name != "nt" and not os.environ.get("DISPLAY"),
    reason="需要 Qt 顯示環境（可設 QT_QPA_PLATFORM=offscreen）")


def test_group_rules():
    src = [{"ref": "A.level", "label": "A"}, {"ref": "B.level", "label": "B"}, {"ref": "C.level", "label": "C"}]
    g = lg.reconcile(None, src, [{"name": "magnet", "members": ["A.level", "B.level"]}])
    assert [x["members"] for x in g] == [["A.level", "B.level"], ["C.level"]]
    assert g[0]["memory"] == [None, None, None]
    g = lg.merge(g, "C.level", 0)
    assert [x["members"] for x in g] == [["A.level", "B.level", "C.level"]]
    g = lg.split(g, "A.level", "A")
    assert [x["members"] for x in g] == [["B.level", "C.level"], ["A.level"]]
    assert lg.merge_target({"B.level": 1.0, "C.level": 2.0}, ["B.level", "C.level"], "override") == 1.0
    assert lg.merge_target({"B.level": 1.0, "C.level": 2.0}, ["B.level", "C.level"], "average") == 1.5
    assert lg.differs({"a": 1.0, "b": 1.0000001}, ["a", "b"], 5e-7) is False
    assert [v["members"] for v in lg.visible(g, ["C.level", "A.level"])] == [["C.level"], ["A.level"]]
    # 存過的群組：保留；新電源各自一組
    g2 = lg.reconcile(g, src + [{"ref": "D.level", "label": "D"}], [])
    assert [x["members"] for x in g2] == [["B.level", "C.level"], ["A.level"], ["D.level"]]


@pytest.fixture
def win(station, tmp_path):
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.workbench import LabControlWindow
    from labcontrol.scheme import Scheme

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    station.sim_time_scale = 0.01
    w = LabControlWindow(station, Scheme("t"))
    w.live.dc.store = lg.GroupStore(tmp_path / "live_groups.json")
    w.resize(1500, 950)
    w.show()
    app.processEvents()
    yield w
    w.doc.dirty = False
    w.close()
    app.processEvents()


def pump(cond, s=10.0):
    from PyQt6 import QtWidgets

    t = time.time()
    while time.time() - t < s:
        QtWidgets.QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_live_tab_groups_merge_memory_fine(win, monkeypatch):
    live, dc = win.live, win.live.dc
    assert win.right_tabs.currentWidget() is win.live_holder           # 還沒量測：即時監控
    assert win.right_tabs.tabText(1) == "量測監控"
    # 嵌在主視窗：只有 dB 曲線與量測 / 循環；電源群組在獨立視窗
    assert live.compact and not live.dcw.isVisibleTo(live) and not live.vna.top_scroll.isVisibleTo(live)
    assert live.vna.b_read.isVisibleTo(live.vna.split) and live.vna.cont.isVisibleTo(live.vna.split)
    assert live.vna.plots.views == ["db"]
    win.popout_live()
    assert not live.compact and live.dcw.isVisibleTo(live)
    dc.reload()
    assert pump(lambda: dc.backend is not None and dc.sources == {} and dc.cards == [], 3)   # 沒連線 → 沒有電源
    live.connect_all()
    assert pump(lambda: len(dc.cards) >= 2)
    refs = [c.members for c in dc.cards]
    assert ["DC3.ch1.level", "DC4.ch1.level"] in refs                  # 預設群組 = 虛擬雙電源 pair
    pair = next(c for c in dc.cards if c.members == ["DC3.ch1.level", "DC4.ch1.level"])
    assert len(pair.fine) == 2                                         # 兩台 → 有微調
    single = next(c for c in dc.cards if len(c.members) == 1)
    assert single.fine == []
    # 微調：+ 只動一台一個最小步進
    st = win.station
    before = {r: st.parameter(r).get() for r in pair.members}
    dc.fine_step(pair, +1)
    assert pump(lambda: sorted(st.parameter(r).get() for r in pair.members) != sorted(before.values()))
    after = sorted(st.parameter(r).get() for r in pair.members)
    assert after[1] - after[0] == pytest.approx(1e-6, abs=1e-9)
    # 記憶：M1 記住 master 目前值 → 改變後 recall 會斜坡回去
    assert pump(lambda: dc.levels.get("DC3.ch1.level") is not None)
    pair = next(c for c in dc.cards if c.members[0] == "DC3.ch1.level")
    pair.store(0)
    m1 = dc.groups[pair.index]["memory"][0]
    assert m1 == pytest.approx(dc.levels["DC3.ch1.level"])
    assert lg.GroupStore(dc.store.path).load(dc.key)[pair.index]["memory"][0] == pytest.approx(m1)
    monkeypatch.setattr("labcontrol.apps.qt.live.dc_panel.setting",
                        lambda k, d=None: 50.0 if k == "live.memory_ramp_mA_s" else d)
    dc.ramp(pair, m1 + 2e-5, 5e-3)
    assert pump(lambda: abs(st.parameter("DC3.ch1.level").get() - (m1 + 2e-5)) < 1e-9)
    pair.recall(0)
    assert pump(lambda: abs(st.parameter("DC3.ch1.level").get() - m1) < 1e-9 and
                abs(st.parameter("DC4.ch1.level").get() - m1) < 1e-9)
    # 拆開 DC4 → 自己一組；再合併回來（值不同 → 問：平均）
    dc.on_split("DC4.ch1.level", len(dc.groups))
    assert any(c.members == ["DC4.ch1.level"] for c in dc.cards)
    st.parameter("DC4.ch1.level").set(m1 + 1e-5)
    assert pump(lambda: abs(dc.levels.get("DC4.ch1.level", 0) - (m1 + 1e-5)) < 1e-9)
    asked = []
    monkeypatch.setattr(dc, "ask_merge", lambda names, vals: asked.append(names) or "average")
    target = next(c for c in dc.cards if c.members == ["DC3.ch1.level"]).index
    dc.on_merge("DC4.ch1.level", target)
    assert asked and any(c.members == ["DC3.ch1.level", "DC4.ch1.level"] for c in dc.cards)
    mid = m1 + 0.5e-5
    assert pump(lambda: abs(st.parameter("DC3.ch1.level").get() - mid) < 2e-9 and
                abs(st.parameter("DC4.ch1.level").get() - mid) < 2e-9)
    # 輸出開關
    pair = next(c for c in dc.cards if len(c.members) == 2)
    dc.output(pair, True)
    assert pump(lambda: all(dc.outputs.get(r) for r in pair.members))
    win.live_window.close()
    assert live.compact


def test_live_vna_and_popout_and_measure_tab(win, monkeypatch):
    live = win.live
    win.popout_live()
    win.station.connect(["VNA1"])
    live.vna.reload()
    assert pump(lambda: live.vna.name == "VNA1" and "start_freq" in live.vna.edits)
    assert pump(lambda: live.vna.edits["start_freq"].text() not in ("", "—"))
    live.vna.sbtn["S11"].setChecked(True)
    live.vna.read_trace()
    assert pump(lambda: live.vna.last is not None and live.vna.last[0] == "S11")
    assert len(live.vna.last[1]) > 10
    # 改設定
    e = live.vna.edits["power"]
    e.setText("-25")
    live.vna._mark("power")
    live.vna.apply()
    assert pump(lambda: win.station.parameter("VNA1.power").get() == pytest.approx(-25))
    # 掃描時間：改了就關掉自動
    live.vna.edits["sweep_time"].setText("0.05")
    live.vna._mark("sweep_time")
    assert ("VNA1.sweep_time_auto", False) in live.vna.pending()
    live.vna.apply()
    assert pump(lambda: win.station.parameter("VNA1.sweep_time_auto").get() is False and
                win.station.parameter("VNA1.sweep_time").get() == pytest.approx(0.05))
    # 曲線複選：每種一張圖；IQ 是 Re–Im
    live.vna.set_views(["db", "uphase", "iq"])
    plots = live.vna.plots
    assert list(plots.plots) == ["db", "uphase", "iq"]
    assert pump(lambda: plots.curves["iq"].getData()[0] is not None and len(plots.curves["iq"].getData()[0]) > 10)
    x_iq, y_iq = plots.curves["iq"].getData()
    z = live.vna.last[2]
    assert np.allclose(x_iq, z.real) and np.allclose(y_iq, z.imag)
    # 九宮格：3 張 = 上左右＋下；拖曳交換位置
    grid = plots.grid
    pos = {k: grid.getItemPosition(grid.indexOf(plots.cells[k])) for k in plots.views}
    assert pos == {"db": (0, 0, 1, 1), "uphase": (0, 1, 1, 1), "iq": (1, 0, 1, 2)}
    plots.cells["db"].swap.emit("iq", "db")                          # 把 IQ 拖到 dB 的位置
    assert live.vna.selected_views() == ["iq", "uphase", "db"]
    assert grid.getItemPosition(grid.indexOf(plots.cells["iq"])) == (0, 0, 1, 1)
    # 最多 5 張：第 6 張會被拒絕；5 張 = 四方格＋下
    for k in ("lin", "re", "im"):
        live.vna.vbtn[k].setChecked(True)
        live.vna._toggle_view(k, True)
    assert len(live.vna.selected_views()) == 5 and not live.vna.vbtn["im"].isChecked()
    last = live.vna.selected_views()[-1]
    assert grid.getItemPosition(grid.indexOf(plots.cells[last])) == (2, 0, 1, 2)
    # 頻率軸連動（數值同步，圖寬不同也對齊）
    plots.cells["db"].plot.setXRange(5.021e9, 5.022e9, padding=0)
    lo, hi = plots.cells["uphase"].plot.getViewBox().viewRange()[0]
    assert lo == pytest.approx(5.021e9) and hi == pytest.approx(5.022e9)
    # 數據點
    live.vna.points.setChecked(True)
    assert plots.cells["db"].curve.opts["symbol"] == "o"
    live.vna.points.setChecked(False)
    assert plots.cells["db"].curve.opts["symbol"] is None
    live.vna.set_views([])                                          # 至少保留一種
    assert live.vna.selected_views() == ["db"]
    # 連續、間隔最短：讀完立刻讀下一次
    live.vna.interval.setValue(0)
    n0 = []
    orig = live.vna.plots.update_data
    monkeypatch.setattr(live.vna.plots, "update_data", lambda *a: (n0.append(1), orig(*a)))
    live.vna.cont.setChecked(True)
    assert pump(lambda: len(n0) >= 4, 10) and "次/s" in live.vna.rate.text()
    live.vna.cont.setChecked(False)
    monkeypatch.undo()
    # 曲線獨立視窗
    live.vna.b_plotwin.click()
    assert live.vna.plot_window is not None and live.vna.plot_holder.indexOf(plots) < 0
    live.vna.plot_window.close()
    assert live.vna.plot_window is None and live.vna.plot_holder.currentWidget() is plots
    # 疊圖：dB 左軸、Phase / Unwrap 右軸，同一張畫布；IQ 不能疊
    live.vna.set_views(["db", "phase", "uphase"])
    live.vna.overlay.setChecked(True)
    ov = plots.cells["overlay"].canvas
    assert ov.series == [("db", "L"), ("phase", "R"), ("uphase", "R")] and ov.vb2 is not None
    live.vna._toggle_view("lin", True)                               # 第三種單位 → 拒絕
    assert "lin" not in live.vna.selected_views()
    live.vna.read_trace()
    assert pump(lambda: ov.data.get("phase") is not None and len(ov.data["phase"][0]) > 10)
    # 拖曲線 = 上下移動（只改顯示）；重設偏移
    ov.shift("db", 5.0)
    y_shown = ov.curves["db"].getData()[1]
    assert np.allclose(y_shown, ov.data["db"][1] + 5.0)
    plots.reset_offsets()
    assert np.allclose(ov.curves["db"].getData()[1], ov.data["db"][1])
    # 找得到滑鼠下的曲線（拖曳用）
    QtWidgets = __import__("PyQt6.QtWidgets", fromlist=["QtWidgets"])
    QtWidgets.QApplication.processEvents()
    xx, yy = ov.data["db"]
    i = len(xx) // 2
    sp = ov.vb.mapViewToScene(__import__("PyQt6.QtCore", fromlist=["QPointF"]).QPointF(xx[i], yy[i]))
    assert ov.curve_at(sp) == "db"
    # 軸縮放：在左軸上拖曳只改左軸範圍
    r0 = ov.vb.viewRange()[1]
    ov.vb.scaleBy(y=0.5)
    r1 = ov.vb.viewRange()[1]
    assert (r1[1] - r1[0]) == pytest.approx((r0[1] - r0[0]) * 0.5, rel=1e-3)
    # marker：多個，讀數不含偏移
    f0 = float(xx[i])
    ov.add_marker(f0)
    ov.add_marker(float(xx[i + 10]))
    mv = ov.marker_values()
    assert [m["name"] for m in mv] == ["M1", "M2"] and mv[0]["values"]["db"] == pytest.approx(yy[i], abs=1e-6)
    assert "M2" in ov.readout.text()
    ov.remove_marker(ov.markers[0])
    assert [m["name"] for m in ov.marker_values()] == ["M1"]
    plots.clear_markers()
    assert not ov.markers
    live.vna.overlay.setChecked(False)
    assert plots.mode == "grid"
    # 即時監控放回主視窗 → 只剩 dB 與量測 / 循環
    win.live_window.close()
    assert live.compact and live.vna.plots.views == ["db"]
    # 即時監控獨立視窗：電源與 VNA 都要顯示（0.0.9 獨立視窗是空的）
    win.station.connect(["DC3", "DC4"])
    live.dc.reload()
    live.b_pop.click()
    assert win.live_window is not None and win.live_window.isVisible()
    assert win.live_holder.currentIndex() == 0 and win.live_holder.indexOf(live) < 0
    assert pump(lambda: live.dc.cards and live.dc.isVisible() and live.vna.isVisible() and
                live.vna.split.isVisible() and live.dcw.width() > 100 and live.vw.width() > 100)
    assert live.vna.plots.views == ["db", "phase", "uphase"]          # 獨立視窗恢復使用者選的圖
    win.live_window.close()
    assert win.live_window is None and win.live_holder.currentWidget() is live
    # 開始量測 → 自動切到量測監控，即時控制暫停；結束後恢復
    monkeypatch.setattr(type(win.monitor), "busy", property(lambda self: True))
    assert pump(lambda: win.right_tabs.currentWidget() is win.mon_stack and live.dc.busy, 3)
    assert "量測進行中" in live.banner.text() and not live.vna.b_read.isEnabled()
    monkeypatch.undo()
    assert pump(lambda: not live.dc.busy, 3)
