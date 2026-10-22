"""曲線幾何：圓角、弧線段、Bézier、微波線精確輪廓。"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shapely.geometry import LineString, Polygon  # noqa: E402

import dxf_geom as G  # noqa: E402


def _area(verts):
    return Polygon(G.ring_points(verts, 1e-4)).area


def test_bulge_arc_semicircle():
    c, r, a0, sweep = G.bulge_arc((0, 0), (2, 0), 1.0)
    assert c == pytest.approx((1, 0)) and r == pytest.approx(1) and sweep == pytest.approx(math.pi)
    assert G.arc_mid((0, 0), (2, 0), 1.0) == pytest.approx((1, -1))                 # 正 bulge 往右邊凸
    assert G.bulge_from_sagitta((0, 0), (2, 0), (1, -1)) == pytest.approx(1.0)
    assert G.bulge_from_sagitta((0, 0), (2, 0), (1, 0.5)) == pytest.approx(-0.5)


def test_fillet_path_outline_is_exact_arcs():
    pts = [(0, 0), (10, 0), (10, 10)]
    verts = G.path_outline(pts, 1.0, fillets=[0, 3, 0])
    assert any(abs(b) > 1e-6 for _x, _y, b in verts)                                  # 轉角是真的圓弧
    # 面積 = 中心線長 × 線寬（圓角中心線長 = 直線 7 + 7 + 弧 3π/2）
    length = 7 + 7 + 3 * math.pi / 2
    assert _area(verts) == pytest.approx(length * 1.0, rel=1e-3)
    # 跟 shapely 細分的結果一致
    dense = LineString(G.flatten(G.chain(pts, None, [0, 3, 0]), 1e-4)).buffer(0.5, cap_style="flat")
    assert _area(verts) == pytest.approx(dense.area, rel=1e-3)


def test_fillet_too_big_is_clamped_and_small_radius_falls_back():
    t, r_eff, _turn, clamped = G.fillet_info([(0, 0), (4, 0), (4, 4)], None, [0, 10, 0], False)
    assert clamped[1] and r_eff[1] == pytest.approx(4.0)
    v = G.path_outline([(0, 0), (10, 0), (10, 10)], 2.0, fillets=[0, 0.5, 0])        # 半徑 < 線寬/2 → 細分
    assert v and all(b == 0 for _x, _y, b in v)
    assert _area(v) > 0


def test_arc_segment_outline():
    verts = G.path_outline([(0, 0), (10, 0)], 1.0, bulges=[math.tan(math.pi / 8)])   # 90° 圓弧
    c, r, a0, sweep = G.bulge_arc((0, 0), (10, 0), math.tan(math.pi / 8))
    assert _area(verts) == pytest.approx(r * abs(sweep) * 1.0, rel=1e-3)
    assert sum(1 for _x, _y, b in verts if b) == 2                                  # 內外兩條弧


def test_polygon_boundary_with_fillets():
    verts = G.boundary([(0, 0), (10, 0), (10, 10), (0, 10)], fillets=[1, 1, 1, 1])
    assert len(verts) == 8 and sum(1 for v in verts if v[2]) == 4
    assert _area(verts) == pytest.approx(100 - (4 - math.pi), rel=1e-4)


def test_bezier():
    ctrl = G.bezier_from_anchors([(0, 0), (5, 5), (10, 0)])
    assert len(ctrl) == 7 and ctrl[0] == (0, 0) and ctrl[3] == (5, 5) and ctrl[6] == (10, 0)
    pts = G.bezier_points(ctrl)
    assert pts[0] == pytest.approx((0, 0)) and pts[-1] == pytest.approx((10, 0))
    assert max(y for _x, y in pts) == pytest.approx(5, abs=0.05)
    seg = G.bezier_segments(ctrl, False)[0]
    a, b = G.split_bezier(seg, 0.5)
    assert a[3] == pytest.approx(G.bez_point(seg, 0.5)) and b[0] == a[3]
    closed = G.bezier_from_anchors([(0, 0), (4, 0), (4, 4), (0, 4)], closed=True)
    assert len(closed) == 12 and len(G.bezier_segments(closed, True)) == 4
