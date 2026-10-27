"""v3.9：DXF 編輯器（參考 AutoCAD）——以游標縮放、點節點打座標、標註 / 量距離、鎖點 / 正交、指令列、
移動 / 旋轉 / 鏡像、MS ⇄ CPW 自動轉換。"""
import math
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QWheelEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])


@pytest.fixture
def ed(monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    from dxf_editor import DxfEditor
    w = DxfEditor(mode="MS", substrate=lambda: {"er": None, "h": 0.813, "material": "Rogers RO4003 (tm)"})
    w.resize(1300, 850)
    w.show()
    QTest.qWait(50)
    w.canvas.fit()
    yield w
    w.dirty = False
    w.close()


def _vp(ed, x, y) -> QPoint:
    return ed.canvas.mapFromScene(QPointF(x, y))


def _click(ed, x, y):
    p = _vp(ed, x, y)
    QTest.mouseMove(ed.canvas.viewport(), p)
    QTest.mouseClick(ed.canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)


def test_zoom_keeps_point_under_cursor(ed):
    c = ed.canvas
    pos = QPointF(220, 160)
    under = c.mapToScene(pos.toPoint())
    tol = 4 * c.px()                                      # 捲軸是整數像素：每次縮放最多差半個像素
    for _ in range(25):
        ev = QWheelEvent(pos, c.viewport().mapToGlobal(pos), QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        c.wheelEvent(ev)
    after = c.mapToScene(pos.toPoint())
    assert abs(after.x() - under.x()) < tol and abs(after.y() - under.y()) < tol
    for _ in range(80):                                   # 放到最大也不會跳回中間
        c.zoom(1.5, pos)
    assert abs(c.transform().m11()) <= 5000.01
    after = c.mapToScene(pos.toPoint())
    assert abs(after.x() - under.x()) < tol and abs(after.y() - under.y()) < tol


def test_click_node_and_type_coordinates(ed):
    d = ed.design
    path = next(s for s in d.shapes if s.kind == "path")            # (0,10) → (30,10)
    ed.set_tool("select")
    ed.canvas.select(d.shapes.index(path))
    _click(ed, 30, 10)                                             # 點節點
    assert ed.canvas.active_node == 1 and ed.node_box.isVisible()
    assert ed.sp_nx.value() == pytest.approx(30) and ed.sp_nlen.value() == pytest.approx(30)
    ed.sp_ny.setValue(14.5)
    assert path.pts[1] == (30, 14.5)
    ed.sp_nang.setValue(0.0)                                       # 改角度：繞上一點轉回水平，長度不變
    assert path.pts[1][1] == pytest.approx(10) and path.pts[1][0] == pytest.approx(math.hypot(30, 4.5))
    ed.undo()
    ed.undo()
    path = next(s for s in d.shapes if s.kind == "path")            # 復原會換成新的物件
    assert path.pts[1] == (30, 10)
    # 指令列也能打節點座標
    ed.canvas.select(d.shapes.index(path))
    ed.canvas.active_node = 0
    assert ed.run_command("@0,2")
    assert path.pts[0] == (0, 12)
    ed.step_node(1)
    assert ed.canvas.active_node == 1
    ed.nudge(0.1, 0)
    assert path.pts[1][0] == pytest.approx(30.1)


def test_command_line_draws_path_with_relative_and_polar(ed):
    n0 = len(ed.design.shapes)
    assert ed.run_command("L")
    for t in ("2,3", "@10,0", "@5<90"):
        assert ed.run_command(t)
    assert ed.run_command("")                                      # Enter 結束
    s = ed.design.shapes[n0]
    assert s.kind == "path" and s.pts[0] == (2, 3) and s.pts[1] == (12, 3)
    assert s.pts[2][0] == pytest.approx(12) and s.pts[2][1] == pytest.approx(8)
    assert ed.run_command("REC") and ed.run_command("1,1") and ed.run_command("@4,2")
    r = ed.design.shapes[-1]
    assert r.kind == "poly" and r.bounds() == pytest.approx((1, 1, 5, 3))
    assert ed.run_command("C") and ed.run_command("10,10") and ed.run_command("0.5")
    assert ed.design.shapes[-1].kind == "circle" and ed.design.shapes[-1].r == pytest.approx(0.5)
    assert not ed.run_command("XYZ")


def test_modify_move_copy_rotate_mirror(ed):
    d = ed.design
    path = next(s for s in d.shapes if s.kind == "path")
    ed.canvas.select(d.shapes.index(path))
    assert ed.run_command("M 1,2")
    assert path.pts == [(1, 12), (31, 12)]
    ed.run_command("RO")
    ed.run_command("90")
    assert path.pts[0] == pytest.approx((16, -3)) and path.pts[1] == pytest.approx((16, 27))
    n = len(d.shapes)
    ed.copy_selected(5, 0)
    assert len(d.shapes) == n + 1 and d.shapes[-1].pts[0] == pytest.approx((21, -3))
    s = d.shapes[-1]
    s.bulges = [0.3]
    ed.mirror_selected("x")
    assert s.bulges == [-0.3]


def test_osnap_and_ortho(ed):
    c = ed.canvas
    tol = c.px() * 5
    p = c.snap(30 - tol, 10 + tol)                                # 靠近微波線端點 → 鎖上
    assert p == (30, 10) and c.osnap_hit[1] == "end"
    p = c.snap(15 + tol, 10 - tol)
    assert p == (15, 10) and c.osnap_hit[1] == "mid"
    ed.chk_osnap.setChecked(False)
    c.snap(30 - tol, 10 + tol)
    assert c.osnap_hit is None
    ed.chk_ortho.setChecked(True)
    ed.set_tool("path")
    c.add_point((1, 1))
    assert c._constrain(5, 2, None) == (5, 1) and c._constrain(2, 7, None) == (1, 7)


def test_dimensions_and_measure_saved_as_dxf(ed, tmp_path):
    c = ed.canvas
    ed.set_tool("dim")
    for p in ((0, 0), (30, 0), (15, -3)):                          # 自動：往下拉 → 水平距離
        c.add_point(p)
    for p in ((30, 0), (30, 20), (34, 10)):                        # 往右拉 → 垂直距離
        c.add_point(p)
    ed.combo_dim.setCurrentIndex(ed.combo_dim.findData("a"))
    for p in ((0, 0), (30, 20), (10, 10)):
        c.add_point(p)
    kinds = [(dm.kind, round(dm.value(), 4)) for dm in ed.design.dims]
    assert kinds == [("h", 30), ("v", 20), ("a", round(math.hypot(30, 20), 4))]
    ed.set_tool("dist")
    c.add_point((0, 0))
    c.add_point((3, 4))
    assert c.measure == ((0, 0), (3, 4)) and "距離 5 mm" in ed.lbl_cmd_msg.text()
    f = str(tmp_path / "dims.dxf")
    assert ed._save_to(f)
    from dxf_model import Design
    r = Design.load(f)
    assert [(dm.kind, round(dm.value(), 4)) for dm in r.dims] == kinds
    assert ed._save_to(f)                                          # 再存一次不會重複
    import ezdxf
    doc = ezdxf.readfile(f)
    dims = [e for e in doc.modelspace() if e.dxftype() == "DIMENSION"]
    assert len(dims) == 3 and {e.dxf.layer for e in dims} == {"dim"}
    assert abs(dims[0].get_measurement() - 30) < 1e-6
    ed.delete_dim(0)
    assert len(ed.design.dims) == 2
    ed.undo()
    assert len(ed.design.dims) == 3


def test_convert_ms_to_cpw_and_back(ed):
    import tline
    n = ed.convert_design("CPW", 1.0, tline.gcpw_gap(50, 1.0, 0.813, 3.55), vias=(1.0, 0.3, 0.2))
    d = ed.design
    assert d.mode == "CPW" and d.auto_ground and n > 20
    assert all(s.layer == "v" and s.r == pytest.approx(0.15) for s in d.shapes if s.kind == "circle")
    assert any(s.layer == "db" for s in d.shapes)
    path = next(s for s in d.shapes if s.kind == "path")
    assert path.width == 1.0 and abs(tline.gcpw_z0(path.width, path.gap, 0.813, 3.55) - 50) < 0.1
    assert not d.check()                                           # 可以建模（有 Port、上地沒有內孔）
    ed.convert_design("MS", tline.ms_width(50, 0.813, 3.55), 0)
    assert d.mode == "MS" and not any(s.layer in ("v", "ub") for s in d.shapes)
    assert any(s.layer == "b" for s in d.shapes) and path.width == pytest.approx(1.833, abs=0.01)


def test_convert_dialog_computes_sizes(ed, monkeypatch):
    from dxf_editor import ConvertDialog
    dlg = ConvertDialog(ed)
    assert dlg.target() == "CPW" and dlg.sp_er.value() == pytest.approx(3.55)
    assert "50.0 Ω" in dlg.lbl_z.text()
    dlg.rb_ms.setChecked(True)
    assert dlg.sp_w.value() == pytest.approx(1.833, abs=0.01) and "MS" in dlg.lbl_z.text()
    dlg.close()


def test_tline_formulas():
    import tline
    assert tline.ms_width(50, 1.6, 4.4) == pytest.approx(3.06, abs=0.1)        # FR4 1.6 mm 約 3 mm
    assert tline.ms_z0(tline.ms_width(75, 0.5, 9.8), 0.5, 9.8) == pytest.approx(75, abs=0.5)
    g = tline.gcpw_gap(50, 0.5, 0.5, 9.8)
    assert g and tline.gcpw_z0(0.5, g, 0.5, 9.8) == pytest.approx(50, abs=0.01)
    assert tline.material_er("Rogers RO4003 (tm)") == 3.55 and tline.material_er("unknown") is None
    assert tline.ellip_k(0) == pytest.approx(math.pi / 2)


def test_cpw_coupling_gaps_and_open_ends():
    """v3.9.4：共振腔（三段微波線、中間兩個耦合間隙）：槽要連續、地切成上下兩塊；開路端離地一個間隙；
    有內孔的地切成幾塊存檔（之前內孔被丟掉 → 上地蓋住訊號線）。"""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    import dxf_geom as G
    from dxf_model import Design, Shape

    def ground(d):
        return unary_union([Polygon(G.ring_points(v)) for v in d.generated()["ub"]])
    d = Design.template("CPW", w=60, h=14, trace=False)
    for a, b in (((0, 7), (19.5, 7)), ((20, 7), (40, 7)), ((40.5, 7), (60, 7))):
        d.shapes.append(Shape("path", "g", [a, b], width=1.0, gap=0.2))
    assert d.check() == [] and len(d.generated()["ub"]) == 2 and d._bridges == 2
    assert ground(d).area == pytest.approx(60 * 14 - 60 * 1.4)
    d.bridge_gaps = False                                 # 不挖空：地伸進間隙，但不會蓋住訊號線
    g = ground(d)
    assert any("被地包住" in w for w in d.check())
    for s in d.shapes[1:]:
        assert g.intersection(Polygon(s.ring())).area < 1e-9
    o = Design.template("CPW", w=60, h=14, trace=False)
    o.shapes.append(Shape("path", "g", [(0, 7), (30, 7)], width=1.0, gap=0.2))
    assert ground(o).distance(Polygon(o.shapes[-1].ring())) == pytest.approx(0.2)
