"""DXF 編輯器的資料（v3.3）：讀 / 寫 DXF、板材大小、用節點畫的微波線、CPW 自動上地。不用 Qt，可以單獨測試。

DXF 單位是 mm（建模時以 Scale 0.001 匯入）。圖層照 README「DXF 圖層命名規範」：
    MS ：b 板材、g 訊號線、h 螺絲孔
    CPW：db 下板材、ub 上板材（頂層地）、g 訊號線、h 螺絲孔、v 金屬過孔

微波線（path）＝中心線節點＋線寬（CPW 另有間隙）：
    存檔時在 g 產生封閉輪廓（HFSS 匯入的就是它），中心線另外存在圖層 g_path（HFSS 不會匯入），之後打開還能用節點編輯。
    CPW 打開「自動產生上地」時，ub = 板材 −（微波線往外加間隙），也是存檔時產生。
    產生的圖元帶 XDATA「QELHFSS」，重新打開時不當成使用者畫的東西。
其他圖元（文字、標註、不認得的）原樣保留。
"""
from __future__ import annotations

import os

os.environ.setdefault("EZDXF_DISABLE_C_EXT", "1")      # 見 main.py：打包後 C 加速模組會當掉

import copy
import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

APPID = "QELHFSS"
PATH_LAYER = "g_path"
BOARD_LAYERS = {"MS": ("b",), "CPW": ("db",)}
LAYERS = {
    "MS": ("b", "g", "h"),
    "CPW": ("db", "ub", "g", "h", "v"),
}
REQUIRED = {"MS": ("b", "g"), "CPW": ("db", "ub", "g")}
LAYER_INFO = {
    "b": ("板材", 3), "db": ("下板材", 3), "ub": ("上板材（頂層地）", 30), "g": ("訊號線", 1),
    "h": ("螺絲孔", 8), "v": ("金屬過孔", 5), PATH_LAYER: ("微波線中心線（不匯入）", 6),
}
EDGE_TOL = 1e-6

Point = Tuple[float, float]


class DxfError(RuntimeError):
    pass


@dataclass
class Shape:
    """kind：poly（封閉多邊形）、circle（圓）、path（微波線中心線）。"""
    kind: str
    layer: str
    pts: List[Point] = field(default_factory=list)
    r: float = 0.0                     # circle：半徑（圓心 = pts[0]）
    width: float = 1.0                 # path：線寬
    gap: float = 0.2                   # path：CPW 間隙
    handle: str = ""                   # 讀進來的圖元（存檔時刪掉重寫）

    def copy(self) -> "Shape":
        return copy.deepcopy(self)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "layer": self.layer, "pts": [list(p) for p in self.pts], "r": self.r,
                "width": self.width, "gap": self.gap, "handle": self.handle}

    @staticmethod
    def from_dict(d: dict) -> "Shape":
        return Shape(d["kind"], d["layer"], [tuple(p) for p in d["pts"]], d.get("r", 0.0), d.get("width", 1.0),
                     d.get("gap", 0.2), d.get("handle", ""))

    def bounds(self) -> Tuple[float, float, float, float]:
        if self.kind == "circle":
            (x, y), r = self.pts[0], self.r
            return x - r, y - r, x + r, y + r
        pad = self.width / 2 + self.gap if self.kind == "path" else 0.0
        xs = [p[0] for p in self.pts] or [0.0]
        ys = [p[1] for p in self.pts] or [0.0]
        return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad

    def translate(self, dx: float, dy: float) -> None:
        self.pts = [(x + dx, y + dy) for x, y in self.pts]


# ---- 幾何（shapely）-------------------------------------------------------------------------------
def _sg():
    import shapely.geometry as sg
    return sg


def path_outline(pts: List[Point], width: float, extra: float = 0.0):
    """中心線 → 輪廓（平頭、斜接），extra = 每邊再往外加（CPW 間隙）。"""
    sg = _sg()
    clean = [p for i, p in enumerate(pts) if i == 0 or (abs(p[0] - pts[i - 1][0]) > 1e-12 or abs(p[1] - pts[i - 1][1]) > 1e-12)]
    if len(clean) < 2 or width <= 0:
        return sg.Polygon()
    return sg.LineString(clean).buffer(width / 2 + extra, cap_style="flat", join_style="mitre", mitre_limit=10.0)


def _polys(geom) -> list:
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    return [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon" and not g.is_empty]


def _ring(poly) -> List[Point]:
    pts = [(round(x, 9), round(y, 9)) for x, y in poly.exterior.coords]
    return pts[:-1] if len(pts) > 1 and pts[0] == pts[-1] else pts


# ---- 設計 ---------------------------------------------------------------------------------------
class Design:
    def __init__(self, mode: str = "MS") -> None:
        self.mode = mode if mode in LAYERS else "MS"
        self.shapes: List[Shape] = []
        self.auto_ground = False
        self.path: str = ""
        self.doc = None                      # 讀進來的 ezdxf 文件（存檔時保留其他圖元）
        self.foreign: List[dict] = []        # 不能編輯、只顯示的圖元（線段）：{"layer", "pts"}
        self.loaded: set = set()             # 讀進來、由編輯器管理的圖元 handle（存檔時全部重寫）

    # ---- 快照（復原 / 重做）-------------------------------------------------------------------
    def snapshot(self) -> str:
        return json.dumps({"mode": self.mode, "auto": self.auto_ground, "shapes": [s.to_dict() for s in self.shapes]})

    def restore(self, snap: str) -> None:
        d = json.loads(snap)
        self.mode, self.auto_ground = d["mode"], d["auto"]
        self.shapes = [Shape.from_dict(s) for s in d["shapes"]]

    # ---- 範本 --------------------------------------------------------------------------------
    @classmethod
    def template(cls, mode: str, w: float = 30.0, h: float = 20.0, width: float = 1.0, gap: float = 0.2) -> "Design":
        d = cls(mode)
        board = BOARD_LAYERS[d.mode][0]
        d.shapes.append(Shape("poly", board, [(0, 0), (w, 0), (w, h), (0, h)]))
        d.shapes.append(Shape("path", "g", [(0, h / 2), (w, h / 2)], width=width, gap=gap))
        d.auto_ground = d.mode == "CPW"
        return d

    # ---- 查詢 --------------------------------------------------------------------------------
    def boards(self) -> List[Shape]:
        return [s for s in self.shapes if s.layer in BOARD_LAYERS[self.mode] and s.kind == "poly"]

    def board_bounds(self) -> Optional[Tuple[float, float, float, float]]:
        bs = self.boards()
        if not bs:
            return None
        b = [s.bounds() for s in bs]
        return min(x[0] for x in b), min(x[1] for x in b), max(x[2] for x in b), max(x[3] for x in b)

    def bounds(self) -> Tuple[float, float, float, float]:
        items = [s.bounds() for s in self.shapes] + [
            (min(p[0] for p in f["pts"]), min(p[1] for p in f["pts"]), max(p[0] for p in f["pts"]),
             max(p[1] for p in f["pts"])) for f in self.foreign if f["pts"]]
        if not items:
            return 0.0, 0.0, 30.0, 20.0
        return min(b[0] for b in items), min(b[1] for b in items), max(b[2] for b in items), max(b[3] for b in items)

    def board_geom(self):
        from shapely.ops import unary_union
        sg = _sg()
        return unary_union([sg.Polygon(s.pts).buffer(0) for s in self.boards() if len(s.pts) >= 3])

    def generated(self) -> Dict[str, list]:
        """存檔時會產生的輪廓：{"g": [多邊形點…], "ub": [...]}（畫面上也顯示這些）。"""
        from shapely.ops import unary_union
        out: Dict[str, list] = {"g": [], "ub": []}
        paths = [s for s in self.shapes if s.kind == "path"]
        for s in paths:
            out["g"] += [_ring(p) for p in _polys(path_outline(s.pts, s.width))]
        if self.mode == "CPW" and self.auto_ground and paths:
            board = self.board_geom()
            if not board.is_empty:
                cut = unary_union([path_outline(s.pts, s.width, s.gap) for s in paths])
                out["ub"] = [_ring(p) for p in _polys(board.difference(cut))]
                self._ground_holes = sum(len(p.interiors) for p in _polys(board.difference(cut)))
        return out

    def ports(self) -> List[Tuple[Point, str]]:
        """微波線端點落在板邊的地方（HFSS 在那裡建 Wave Port）。"""
        bb = self.board_bounds()
        if not bb:
            return []
        out = []
        for s in self.shapes:
            if s.kind != "path" or len(s.pts) < 2:
                continue
            for p in (s.pts[0], s.pts[-1]):
                side = _edge_side(p, bb)
                if side:
                    out.append((p, side))
        return out

    def check(self) -> List[str]:
        """存檔前的檢查：缺圖層、端點沒碰到板邊、上地有內孔。回傳警告（空 = 沒問題）。"""
        warn = []
        have = {s.layer for s in self.shapes} | ({"g"} if any(s.kind == "path" for s in self.shapes) else set())
        if self.mode == "CPW" and self.auto_ground:
            have.add("ub")
        for layer in REQUIRED[self.mode]:
            if layer not in have:
                warn.append(f"缺少圖層 {layer}（{LAYER_INFO[layer][0]}）")
        paths = [s for s in self.shapes if s.kind == "path"]
        if paths and len(self.ports()) < 1:
            warn.append("沒有微波線的端點碰到板邊：HFSS 找不到可以放 Wave Port 的位置")
        if self.mode == "CPW" and self.auto_ground:
            self._ground_holes = 0
            self.generated()
            if getattr(self, "_ground_holes", 0):
                warn.append("自動產生的上地有內孔（微波線兩端要碰到板邊，地才會被切開）")
        for s in self.shapes:
            if s.kind == "poly" and len(s.pts) < 3:
                warn.append(f"圖層 {s.layer} 有少於 3 個點的多邊形")
            if s.kind == "path" and len(s.pts) < 2:
                warn.append("有少於 2 個節點的微波線")
        return warn

    # ---- 編輯 --------------------------------------------------------------------------------
    def set_board(self, x0: float, y0: float, w: float, h: float, follow: bool = True) -> None:
        """板材改成 x0, y0 起、w × h 的矩形；follow：原本落在板邊的端點 / 頂點跟著移到新的板邊。"""
        if w <= 0 or h <= 0:
            raise DxfError("板材的寬、高要大於 0")
        old = self.board_bounds()
        nb = (x0, y0, x0 + w, y0 + h)
        boards = self.boards()
        rect = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
        if boards:
            boards[0].pts = list(rect)
            for extra in boards[1:]:
                self.shapes.remove(extra)
        else:
            self.shapes.insert(0, Shape("poly", BOARD_LAYERS[self.mode][0], list(rect)))
        if self.mode == "CPW":                    # 手畫的上板材如果是整塊矩形，也跟著
            for s in self.shapes:
                if s.layer == "ub" and s.kind == "poly" and old and _is_rect(s.pts, old):
                    s.pts = list(rect)
        if not (follow and old):
            return
        for s in self.shapes:
            if s.kind == "path" and len(s.pts) >= 2:
                for i in (0, len(s.pts) - 1):
                    s.pts[i] = _move_to_edge(s.pts[i], old, nb, s.pts[1] if i == 0 else s.pts[-2])
            elif s.kind == "poly" and s.layer not in BOARD_LAYERS[self.mode] and not (s.layer == "ub" and _is_rect(s.pts, nb)):
                s.pts = [_move_to_edge(p, old, nb) for p in s.pts]

    # ---- 讀檔 --------------------------------------------------------------------------------
    @classmethod
    def load(cls, path: str, mode: Optional[str] = None) -> "Design":
        import ezdxf
        try:
            doc = ezdxf.readfile(path)
        except (IOError, OSError, ezdxf.DXFStructureError) as e:
            raise DxfError(f"讀不了這個 DXF：{e}") from None
        d = cls(mode or "MS")
        d.path, d.doc = path, doc
        meta = {}
        try:
            if doc.header.custom_vars.has_tag("QEL_HFSS"):
                meta = json.loads(doc.header.custom_vars.get("QEL_HFSS") or "{}")
        except (ValueError, AttributeError):
            meta = {}
        layers = {e.dxf.layer for e in doc.modelspace()}
        if mode is None:
            d.mode = meta.get("mode") or ("CPW" if "db" in layers or "ub" in layers else "MS")
        d.auto_ground = bool(meta.get("auto_ground", False))
        for e in doc.modelspace():
            t, layer = e.dxftype(), e.dxf.layer
            if _xdata(e).get("gen"):
                continue                          # 存檔時產生的：重新產生
            if t == "LWPOLYLINE":
                pts = _lw_points(e)
                if layer == PATH_LAYER:
                    x = _xdata(e)
                    d.shapes.append(Shape("path", x.get("layer", "g"), [(p[0], p[1]) for p in e.get_points("xy")],
                                          width=float(x.get("width") or e.dxf.const_width or 1.0),
                                          gap=float(x.get("gap", 0.2)), handle=e.dxf.handle))
                elif e.closed or (len(pts) > 2 and _same(pts[0], pts[-1])):
                    if len(pts) > 2 and _same(pts[0], pts[-1]):
                        pts = pts[:-1]
                    d.shapes.append(Shape("poly", layer, pts, handle=e.dxf.handle))
                else:
                    d.foreign.append({"layer": layer, "pts": pts})
            elif t == "POLYLINE" and not e.is_3d_polyline and not e.is_poly_face_mesh and not e.is_polygon_mesh:
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                if e.is_closed and len(pts) >= 3:
                    d.shapes.append(Shape("poly", layer, pts, handle=e.dxf.handle))
                else:
                    d.foreign.append({"layer": layer, "pts": pts})
            elif t == "CIRCLE":
                c = e.dxf.center
                d.shapes.append(Shape("circle", layer, [(c.x, c.y)], r=float(e.dxf.radius), handle=e.dxf.handle))
            elif t == "LINE":
                d.foreign.append({"layer": layer, "pts": [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]})
            elif t in ("ARC", "ELLIPSE", "SPLINE"):
                try:
                    pts = [(p.x, p.y) for p in e.flattening(0.01)]
                    d.foreign.append({"layer": layer, "pts": pts})
                except Exception:  # noqa: BLE001 - 只是顯示
                    pass
        d.loaded = {s.handle for s in d.shapes if s.handle}
        return d

    # ---- 存檔 --------------------------------------------------------------------------------
    def save(self, path: str) -> None:
        import ezdxf
        doc = self.doc
        if doc is None:
            doc = ezdxf.new("R2013", setup=False)
            doc.header["$INSUNITS"] = 4          # mm
        msp = doc.modelspace()
        keep = {s.handle for s in self.shapes if s.handle} | self.loaded
        # 刪掉之前讀進來（包括已經刪掉的）/ 產生的、現在要重寫的圖元
        for e in list(msp):
            if e.dxf.handle in keep or _xdata(e).get("gen") or (e.dxftype() == "LWPOLYLINE" and e.dxf.layer == PATH_LAYER):
                msp.delete_entity(e)
        if APPID not in doc.appids:
            doc.appids.new(APPID)
        for name, (desc, color) in LAYER_INFO.items():
            if name not in doc.layers:
                doc.layers.add(name, color=color)
        for s in self.shapes:
            if s.kind == "poly":
                e = msp.add_lwpolyline(s.pts, close=True, dxfattribs={"layer": s.layer})
            elif s.kind == "circle":
                e = msp.add_circle(s.pts[0], s.r, dxfattribs={"layer": s.layer})
            else:
                e = msp.add_lwpolyline(s.pts, close=False, dxfattribs={"layer": PATH_LAYER, "const_width": s.width})
                e.set_xdata(APPID, [(1000, json.dumps({"width": s.width, "gap": s.gap, "layer": s.layer}))])
            s.handle = e.dxf.handle
        for layer, polys in self.generated().items():
            for pts in polys:
                e = msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})
                e.set_xdata(APPID, [(1000, json.dumps({"gen": 1}))])
        meta = json.dumps({"mode": self.mode, "auto_ground": self.auto_ground})
        cv = doc.header.custom_vars
        if cv.has_tag("QEL_HFSS"):
            cv.replace("QEL_HFSS", meta)
        else:
            cv.append("QEL_HFSS", meta)
        if doc.dxfversion < "AC1027":
            doc.dxfversion = "AC1027"            # README：AutoCAD 2013 格式
        try:
            doc.saveas(path)
        except OSError as e:
            raise DxfError(f"存不了：{e}") from None
        self.doc, self.path = doc, path
        self.loaded = {s.handle for s in self.shapes if s.handle}


# ---- 小工具 -----------------------------------------------------------------------------------
def _same(a: Point, b: Point, tol: float = 1e-9) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _xdata(e) -> dict:
    try:
        if e.has_xdata(APPID):
            for code, value in e.get_xdata(APPID):
                if code == 1000:
                    return json.loads(value)
    except Exception:  # noqa: BLE001 - 別人的 XDATA
        pass
    return {}


def _lw_points(e) -> List[Point]:
    """LWPOLYLINE 的點；有弧（bulge）的段切成小直線（誤差 0.005 mm）。"""
    raw = list(e.get_points("xyb"))
    if not any(b for _x, _y, b in raw):
        return [(x, y) for x, y, _b in raw]
    from ezdxf.path import make_path
    pts = [(v.x, v.y) for v in make_path(e).flattening(0.005)]
    if e.closed and len(pts) > 2 and _same(pts[0], pts[-1]):
        pts = pts[:-1]
    return pts


def _edge_side(p: Point, bb, tol: float = 1e-6) -> str:
    x0, y0, x1, y1 = bb
    if abs(p[0] - x0) <= tol and y0 - tol <= p[1] <= y1 + tol:
        return "左"
    if abs(p[0] - x1) <= tol and y0 - tol <= p[1] <= y1 + tol:
        return "右"
    if abs(p[1] - y0) <= tol and x0 - tol <= p[0] <= x1 + tol:
        return "下"
    if abs(p[1] - y1) <= tol and x0 - tol <= p[0] <= x1 + tol:
        return "上"
    return ""


def _move_to_edge(p: Point, old, new, neighbor: Optional[Point] = None) -> Point:
    """原本在舊板邊上的點移到新板邊的同一邊（另一個座標保持，超出新板材時夾回）。"""
    x, y = p
    ox0, oy0, ox1, oy1 = old
    nx0, ny0, nx1, ny1 = new
    side = _edge_side(p, old)
    if side == "左":
        x = nx0
    elif side == "右":
        x = nx1
    elif side == "下":
        y = ny0
    elif side == "上":
        y = ny1
    else:
        return p
    if side in ("左", "右"):
        y = min(max(y, ny0), ny1)
    else:
        x = min(max(x, nx0), nx1)
    return (x, y)


def _is_rect(pts: List[Point], bb) -> bool:
    if len(pts) != 4:
        return False
    x0, y0, x1, y1 = bb
    want = {(round(x0, 6), round(y0, 6)), (round(x1, 6), round(y0, 6)), (round(x1, 6), round(y1, 6)),
            (round(x0, 6), round(y1, 6))}
    return {(round(x, 6), round(y, 6)) for x, y in pts} == want
