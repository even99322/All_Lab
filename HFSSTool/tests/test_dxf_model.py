"""DXF 編輯器的資料：範本、存檔 / 讀回、板材大小、微波線輪廓、CPW 自動上地、保留其他圖元。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ezdxf  # noqa: E402

from dxf_model import Design, DxfError, Shape  # noqa: E402


def _layer_polys(path, layer):
    doc = ezdxf.readfile(path)
    return [list(e.get_points("xy")) for e in doc.modelspace().query(f'LWPOLYLINE[layer=="{layer}"]') if e.closed]


def test_ms_template_roundtrip(tmp_path):
    d = Design.template("MS", 30, 20, width=1.0)
    assert d.check() == []
    assert [side for _p, side in d.ports()] == ["左", "右"]
    f = str(tmp_path / "ms.dxf")
    d.save(f)
    doc = ezdxf.readfile(f)
    assert doc.dxfversion == "AC1027"
    g = _layer_polys(f, "g")
    assert len(g) == 1
    xs, ys = [p[0] for p in g[0]], [p[1] for p in g[0]]
    assert min(xs) == pytest.approx(0) and max(xs) == pytest.approx(30)
    assert min(ys) == pytest.approx(9.5) and max(ys) == pytest.approx(10.5)       # 線寬 1 mm
    assert len(_layer_polys(f, "b")) == 1
    # 讀回來：中心線還是可以用節點編輯，產生的輪廓不會變成另一個形狀
    r = Design.load(f)
    assert r.mode == "MS"
    kinds = sorted((s.kind, s.layer) for s in r.shapes)
    assert kinds == [("path", "g"), ("poly", "b")]
    path = next(s for s in r.shapes if s.kind == "path")
    assert path.pts == [(0, 10), (30, 10)] and path.width == pytest.approx(1.0)
    # 再存一次：不會多出重複的圖元
    r.save(f)
    assert len(_layer_polys(f, "g")) == 1 and len(_layer_polys(f, "b")) == 1


def test_bent_trace_with_nodes(tmp_path):
    d = Design.template("MS", 30, 20)
    path = next(s for s in d.shapes if s.kind == "path")
    path.pts = [(0, 5), (15, 5), (15, 15), (30, 15)]
    out = d.generated()["g"]
    assert len(out) == 1
    from shapely.geometry import Polygon
    area = Polygon(out[0]).area
    assert area == pytest.approx(1.0 * (15 + 10 + 15), rel=0.05)                    # 線長 × 線寬（轉角斜接）


def test_set_board_moves_trace_ends(tmp_path):
    d = Design.template("MS", 30, 20)
    path = next(s for s in d.shapes if s.kind == "path")
    path.pts = [(0, 10), (10, 10), (20, 10), (30, 10)]
    d.set_board(-5, 0, 50, 30)
    assert d.board_bounds() == pytest.approx((-5, 0, 45, 30))
    assert path.pts[0] == (-5, 10) and path.pts[-1] == (45, 10)                   # 端點跟著新的板邊
    assert path.pts[1:3] == [(10, 10), (20, 10)]                                   # 中間的節點不動
    d.set_board(0, 0, 10, 10, follow=False)
    assert path.pts[0] == (-5, 10)
    with pytest.raises(DxfError):
        d.set_board(0, 0, 0, 10)


def test_cpw_auto_ground(tmp_path):
    d = Design.template("CPW", 30, 20, width=1.0, gap=0.2)
    gen = d.generated()
    assert len(gen["ub"]) == 2                                                     # 地被微波線切成上下兩塊
    from shapely.geometry import Polygon
    total = sum(Polygon(p).area for p in gen["ub"])
    assert total == pytest.approx(30 * 20 - 30 * 1.4)
    assert d.check() == []
    f = str(tmp_path / "cpw.dxf")
    d.save(f)
    assert len(_layer_polys(f, "ub")) == 2 and len(_layer_polys(f, "db")) == 1
    r = Design.load(f)
    assert r.mode == "CPW" and r.auto_ground
    assert not [s for s in r.shapes if s.layer == "ub"]                           # 產生的上地不會讀成手畫的
    # 微波線沒碰到右邊：地會有內孔 → 警告
    path = next(s for s in r.shapes if s.kind == "path")
    path.pts = [(5, 10), (20, 10)]
    assert any("內孔" in w for w in r.check())


def test_keeps_foreign_entities_and_deleted_shapes_go(tmp_path):
    f = str(tmp_path / "old.dxf")
    doc = ezdxf.new("R2013")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 10), (0, 10)], close=True, dxfattribs={"layer": "b"})
    msp.add_lwpolyline([(0, 4.5), (20, 4.5), (20, 5.5), (0, 5.5)], close=True, dxfattribs={"layer": "g"})
    msp.add_circle((3, 3), 0.8, dxfattribs={"layer": "h"})
    msp.add_text("note", dxfattribs={"layer": "0"})
    msp.add_lwpolyline([(0, 0), (1, 0), (1, 1)], format="xyb", dxfattribs={"layer": "b"})   # 開放的線：只顯示
    doc.saveas(f)
    d = Design.load(f)
    assert d.mode == "MS"
    assert sorted(s.kind for s in d.shapes) == ["circle", "poly", "poly"]
    assert len(d.foreign) == 1
    hole = next(s for s in d.shapes if s.kind == "circle")
    assert hole.r == pytest.approx(0.8)
    d.shapes.remove(hole)                                                          # 刪掉螺絲孔
    g = next(s for s in d.shapes if s.layer == "g")
    g.pts[0] = (0, 4.0)                                                            # 用節點改訊號線
    d.save(f)
    doc = ezdxf.readfile(f)
    assert len(doc.modelspace().query("TEXT")) == 1                               # 文字還在
    assert len(doc.modelspace().query("CIRCLE")) == 0
    assert len(_layer_polys(f, "g")) == 1 and _layer_polys(f, "g")[0][0] == pytest.approx((0, 4.0))


def test_bulge_polyline_is_flattened(tmp_path):
    f = str(tmp_path / "arc.dxf")
    doc = ezdxf.new("R2013")
    doc.modelspace().add_lwpolyline([(0, 0, 0), (10, 0, 1), (10, 10, 0), (0, 10, 0)], format="xyb", close=True,
                                    dxfattribs={"layer": "b"})
    doc.saveas(f)
    d = Design.load(f)
    assert len(d.shapes) == 1 and len(d.shapes[0].pts) > 6                         # 弧切成小段


def test_snapshot_restore():
    d = Design.template("MS")
    snap = d.snapshot()
    d.shapes.append(Shape("circle", "h", [(1, 1)], r=0.5))
    d.restore(snap)
    assert len(d.shapes) == 2
