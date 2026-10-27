"""DXF 編輯器的資料（v3.3–3.4）：讀 / 寫 DXF、板材大小、用節點畫的微波線、曲線、CPW 自動上地。不用 Qt，可以單獨測試。

DXF 單位是 mm（建模時以 Scale 0.001 匯入）。圖層照 README「DXF 圖層命名規範」：
    MS ：b 板材、g 訊號線、h 螺絲孔
    CPW：db 下板材、ub 上板材（頂層地）、g 訊號線、h 螺絲孔、v 金屬過孔

微波線（path）＝中心線節點＋線寬（CPW 另有間隙）：
    存檔時在 g 產生封閉輪廓（HFSS 匯入的就是它），中心線另外存在圖層 g_path（HFSS 不會匯入），之後打開還能用節點編輯。
    CPW 打開「自動產生上地」時，ub = 板材 −（微波線往外加間隙），也是存檔時產生。
    產生的圖元帶 XDATA「QELHFSS」，重新打開時不當成使用者畫的東西。
曲線（v3.4，幾何在 dxf_geom.py）：
    每段可以是圓弧（bulges：DXF 的 bulge）、每個節點可以有圓角（fillets：半徑）→ 存成 DXF 真正的圓弧；
    smooth = "bezier"：Bézier 曲線（點 = 錨、控、控、錨…），存檔時切成細小直線段。
其他圖元（文字、標註、不認得的）原樣保留。
標註（v3.9）：圖層 dim 的 DIMENSION（水平 / 垂直 / 對齊），XDATA 存原始的兩點與標註線位置；HFSS 不會匯入這個圖層。
"""
from __future__ import annotations

import os

os.environ.setdefault("EZDXF_DISABLE_C_EXT", "1")      # 見 main.py：打包後 C 加速模組會當掉

import copy
import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import dxf_geom as G

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
DIM_LAYER = "dim"
DIM_INFO = ("標註（不匯入）", 2)
EDGE_TOL = 1e-6
XDATA_CHUNK = 250

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
    bulges: List[float] = field(default_factory=list)    # 每段（i → i+1）的 bulge；0 = 直線
    fillets: List[float] = field(default_factory=list)   # 每個節點的圓角半徑；0 = 尖角
    smooth: str = ""                   # "bezier"：點 = 錨、控、控、錨…

    # ---- 基本 -----------------------------------------------------------------------------------
    def copy(self) -> "Shape":
        return copy.deepcopy(self)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "layer": self.layer, "pts": [list(p) for p in self.pts], "r": self.r,
                "width": self.width, "gap": self.gap, "handle": self.handle, "bulges": list(self.bulges),
                "fillets": list(self.fillets), "smooth": self.smooth}

    @staticmethod
    def from_dict(d: dict) -> "Shape":
        return Shape(d["kind"], d["layer"], [tuple(p) for p in d["pts"]], d.get("r", 0.0), d.get("width", 1.0),
                     d.get("gap", 0.2), d.get("handle", ""), list(d.get("bulges") or []), list(d.get("fillets") or []),
                     d.get("smooth", ""))

    @property
    def closed(self) -> bool:
        return self.kind == "poly"

    @property
    def bezier(self) -> bool:
        return self.smooth == "bezier" and self.kind in ("path", "poly")

    def n_segs(self) -> int:
        n = len(self.pts)
        return n if self.closed else max(n - 1, 0)

    def norm(self) -> None:
        """bulges / fillets 的長度跟節點數對齊。"""
        if self.bezier or self.kind == "circle":
            self.bulges, self.fillets = [], []
            return
        segs = self.n_segs()
        self.bulges = (list(self.bulges) + [0.0] * segs)[:segs]
        self.fillets = (list(self.fillets) + [0.0] * len(self.pts))[:len(self.pts)]

    def is_anchor(self, i: int) -> bool:
        return not self.bezier or i % 3 == 0

    # ---- 幾何 -----------------------------------------------------------------------------------
    def centerline(self) -> List[Point]:
        """path：細分的中心線；poly：細分的邊界。"""
        if self.kind == "circle":
            return list(self.pts)
        if self.bezier:
            return G.bezier_points(self.pts, self.closed)
        self.norm()
        pts = G.flatten(G.chain(self.pts, self.bulges, self.fillets, self.closed))
        if self.closed and len(pts) > 1 and G._close(pts[0], pts[-1]):
            pts.pop()
        return pts

    def verts(self, extra: float = 0.0) -> List[G.Vert]:
        """存檔用的頂點 [(x, y, bulge)]：poly = 邊界；path = 輪廓（extra = 每邊再往外）。"""
        if self.kind == "poly":
            if self.bezier:
                return [(x, y, 0.0) for x, y in G.bezier_points(self.pts, True)]
            self.norm()
            return G.boundary(self.pts, self.bulges, self.fillets)
        if self.kind == "path":
            if self.bezier:
                return G._dense_outline(G.bezier_points(self.pts, False), self.width / 2 + extra)
            self.norm()
            return G.path_outline(self.pts, self.width, self.bulges, self.fillets, extra)
        return []

    def ring(self, extra: float = 0.0) -> List[Point]:
        """畫面 / 布林運算用的細分輪廓。"""
        return G.ring_points(self.verts(extra))

    def bounds(self) -> Tuple[float, float, float, float]:
        if self.kind == "circle":
            (x, y), r = self.pts[0], self.r
            return x - r, y - r, x + r, y + r
        pad = self.width / 2 + self.gap if self.kind == "path" else 0.0
        pts = (self.centerline() if (self.bezier or any(self.bulges) or any(self.fillets)) else list(self.pts)) \
            or list(self.pts) or [(0.0, 0.0)]
        if self.bezier:
            pts += list(self.pts)                            # 控制點也算（才看得到、點得到）
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad

    def translate(self, dx: float, dy: float) -> None:
        self.pts = [(x + dx, y + dy) for x, y in self.pts]

    def center(self) -> Point:
        if self.kind == "circle":
            return self.pts[0]
        x0, y0, x1, y1 = self.bounds()
        return (x0 + x1) / 2, (y0 + y1) / 2

    def rotate(self, deg: float, pivot: Optional[Point] = None) -> None:
        """v3.9：繞 pivot（預設圖形中心）逆時針轉 deg 度；圓弧、圓角、Bézier 都不變形。"""
        cx, cy = pivot if pivot is not None else self.center()
        c, s_ = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        self.pts = [(round(cx + (x - cx) * c - (y - cy) * s_, 9), round(cy + (x - cx) * s_ + (y - cy) * c, 9))
                    for x, y in self.pts]

    def mirror(self, axis: str, pivot: Optional[Point] = None) -> None:
        """axis = "x"：左右翻（對垂直線）；"y"：上下翻。圓弧方向跟著反過來。"""
        cx, cy = pivot if pivot is not None else self.center()
        self.pts = [((2 * cx - x) if axis == "x" else x, (2 * cy - y) if axis == "y" else y) for x, y in self.pts]
        if not self.bezier and self.kind != "circle":
            self.norm()
            self.bulges = [-b for b in self.bulges]

    def snap_points(self) -> List[Tuple[Point, str]]:
        """物件鎖點：end（節點 / 端點）、mid（直線段中點、弧中點）、cen（圓心）、qua（圓的四分點）。"""
        if self.kind == "circle":
            (x, y), r = self.pts[0], self.r
            return [((x, y), "cen")] + [((x + dx * r, y + dy * r), "qua") for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1))]
        out = [(p, "end") for i, p in enumerate(self.pts) if self.is_anchor(i)]
        if not self.bezier:
            self.norm()
            n = len(self.pts)
            for k in range(self.n_segs()):
                a, b = self.pts[k], self.pts[(k + 1) % n]
                out.append((G.arc_mid(a, b, self.bulges[k]), "mid"))
        return out

    def ends(self) -> Tuple[Point, Point]:
        return self.pts[0], self.pts[-1]

    # ---- 節點 -----------------------------------------------------------------------------------
    def insert_after(self, seg: int, p: Optional[Point] = None, t: float = 0.5) -> int:
        """在第 seg 段中間加一個節點（Bézier：在 t 切開）→ 新節點的索引。"""
        if self.bezier:
            segs = G.bezier_segments(self.pts, self.closed)
            a, b = G.split_bezier(segs[seg], t)
            i = 3 * seg
            self.pts = self.pts[:i + 1] + [a[1], a[2], a[3], b[1], b[2]] + self.pts[i + 3:]
            return i + 3
        self.norm()
        n = len(self.pts)
        j = (seg + 1) % n
        b = self.bulges[seg] if seg < len(self.bulges) else 0.0
        a, c = self.pts[seg], self.pts[j]
        if b:
            arc = G.bulge_arc(a, c, b)
            if p is None:
                p = G.arc_mid(a, c, b)
                b1 = b2 = G.math.tan(G.math.atan(b) / 2.0)
            else:
                center, r, a0, sweep = arc
                ang = G.math.atan2(p[1] - center[1], p[0] - center[0])
                d = (ang - a0) % (2 * G.math.pi) if sweep > 0 else -((a0 - ang) % (2 * G.math.pi))
                d = max(min(d, sweep), 0.0) if sweep > 0 else min(max(d, sweep), 0.0)
                p = (center[0] + r * G.math.cos(a0 + d), center[1] + r * G.math.sin(a0 + d))
                b1, b2 = G.math.tan(d / 4.0), G.math.tan((sweep - d) / 4.0)
        else:
            if p is None:
                p = ((a[0] + c[0]) / 2, (a[1] + c[1]) / 2)
            b1 = b2 = 0.0
        self.pts.insert(seg + 1, p)
        self.bulges[seg:seg + 1] = [b1, b2]
        self.fillets.insert(seg + 1, 0.0)
        return seg + 1

    def min_nodes(self) -> int:
        if self.bezier:
            return 6 if self.closed else 4                    # 至少 2 個錨（封閉 3 個錨的話 9 點；2 段就夠畫）
        return 3 if self.closed else 2

    def can_delete(self, i: int) -> bool:
        if self.bezier:
            return self.is_anchor(i) and len(self.pts) - 3 >= self.min_nodes()
        return len(self.pts) > self.min_nodes()

    def delete_node(self, i: int) -> bool:
        if not self.can_delete(i):
            return False
        if self.bezier:
            n = len(self.pts)
            if not self.closed and i == 0:
                del self.pts[0:3]
            elif not self.closed and i == n - 1:
                del self.pts[n - 3:n]
            elif self.closed and i == 0:
                rest = self.pts[2:n - 1]                      # [控, 錨1, …, 錨末, 控] → 錨點要在 0, 3, 6…
                self.pts = rest[1:] + rest[:1]
            else:
                del self.pts[i - 1:i + 2]
            return True
        self.norm()
        segs = self.n_segs()
        del self.pts[i]
        del self.fillets[i]
        if self.closed:
            del self.bulges[i % segs]
        elif i < segs:
            del self.bulges[i]
        else:
            del self.bulges[segs - 1]
        self.norm()
        return True


@dataclass
class Dim:
    """標註：kind = h（水平距離）、v（垂直距離）、a（兩點真實距離）；base = 標註線經過的點。"""
    kind: str
    p1: Point
    p2: Point
    base: Point

    def value(self) -> float:
        (x1, y1), (x2, y2) = self.p1, self.p2
        if self.kind == "h":
            return abs(x2 - x1)
        if self.kind == "v":
            return abs(y2 - y1)
        return math.hypot(x2 - x1, y2 - y1)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "p1": list(self.p1), "p2": list(self.p2), "base": list(self.base)}

    @staticmethod
    def from_dict(d: dict) -> "Dim":
        return Dim(str(d.get("kind", "a")), tuple(d["p1"]), tuple(d["p2"]), tuple(d.get("base") or d["p2"]))

    def geometry(self):
        """畫面用：(延伸線 1 的兩端, 延伸線 2 的兩端, 標註線兩端, 文字位置, 文字角度°)。"""
        (x1, y1), (x2, y2), (bx, by) = self.p1, self.p2, self.base
        if self.kind == "h":
            a, b = (x1, by), (x2, by)
        elif self.kind == "v":
            a, b = (bx, y1), (bx, y2)
        else:
            dx, dy = x2 - x1, y2 - y1
            L = math.hypot(dx, dy) or 1e-12
            nx, ny = -dy / L, dx / L
            off = (bx - x1) * nx + (by - y1) * ny
            a, b = (x1 + nx * off, y1 + ny * off), (x2 + nx * off, y2 + ny * off)
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        if ang > 90.0001 or ang <= -90:
            ang -= 180 if ang > 0 else -180
        return (self.p1, a), (self.p2, b), (a, b), mid, ang


# ---- 設計 ---------------------------------------------------------------------------------------
class Design:
    def __init__(self, mode: str = "MS") -> None:
        self.mode = mode if mode in LAYERS else "MS"
        self.shapes: List[Shape] = []
        self.dims: List[Dim] = []
        self.auto_ground = False
        self.bridge_gaps = True              # v3.9.4：端對端微波線之間的間隙也挖空
        self.path: str = ""
        self.doc = None                      # 讀進來的 ezdxf 文件（存檔時保留其他圖元）
        self.foreign: List[dict] = []        # 不能編輯、只顯示的圖元（線段）：{"layer", "pts"}
        self.loaded: set = set()             # 讀進來、由編輯器管理的圖元 handle（存檔時全部重寫）
        self._ground_holes = 0

    # ---- 快照（復原 / 重做）-------------------------------------------------------------------
    def snapshot(self) -> str:
        return json.dumps({"mode": self.mode, "auto": self.auto_ground, "bridge": self.bridge_gaps,
                           "shapes": [s.to_dict() for s in self.shapes],
                           "dims": [x.to_dict() for x in self.dims]})

    def restore(self, snap: str) -> None:
        d = json.loads(snap)
        self.mode, self.auto_ground = d["mode"], d["auto"]
        self.bridge_gaps = bool(d.get("bridge", True))
        self.shapes = [Shape.from_dict(s) for s in d["shapes"]]
        self.dims = [Dim.from_dict(x) for x in d.get("dims") or []]

    # ---- 範本 --------------------------------------------------------------------------------
    @classmethod
    def template(cls, mode: str, w: float = 30.0, h: float = 20.0, width: float = 1.0, gap: float = 0.2,
                 trace: bool = True) -> "Design":
        d = cls(mode)
        board = BOARD_LAYERS[d.mode][0]
        d.shapes.append(Shape("poly", board, [(0, 0), (w, 0), (w, h), (0, h)]))
        if trace:
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
        items = [s.bounds() for s in self.shapes if s.pts] + [
            (min(p[0] for p in f["pts"]), min(p[1] for p in f["pts"]), max(p[0] for p in f["pts"]),
             max(p[1] for p in f["pts"])) for f in self.foreign if f["pts"]]
        if not items:
            return 0.0, 0.0, 30.0, 20.0
        return min(b[0] for b in items), min(b[1] for b in items), max(b[2] for b in items), max(b[3] for b in items)

    def board_geom(self):
        import shapely.geometry as sg
        from shapely.ops import unary_union
        return unary_union([sg.Polygon(s.ring()).buffer(0) for s in self.boards() if len(s.pts) >= 3])

    def generated(self) -> Dict[str, list]:
        """存檔時會產生的輪廓：{"g": [[(x, y, bulge)…]…], "ub": [...]}（畫面上也顯示這些）。"""
        import shapely.geometry as sg
        from shapely.ops import unary_union
        out: Dict[str, list] = {"g": [], "ub": []}
        paths = [s for s in self.shapes if s.kind == "path" and len(s.pts) >= 2]
        for s in paths:
            v = s.verts()
            if len(v) >= 3:
                out["g"].append(v)
        self._ground_holes = 0
        self._bridges = 0
        if self.mode == "CPW" and self.auto_ground and paths:
            board = self.board_geom()
            if not board.is_empty:
                cuts = [sg.Polygon(s.ring(s.gap)).buffer(0) for s in paths if len(s.ring(s.gap)) >= 3]
                bridges = self.gap_bridges(paths) if self.bridge_gaps else []
                self._bridges = len(bridges)
                cut = unary_union(cuts + bridges + self.end_gaps(paths))
                rest = board.difference(cut)
                polys = [rest] if rest.geom_type == "Polygon" else [g for g in getattr(rest, "geoms", [])
                                                                    if g.geom_type == "Polygon"]
                for p in polys:
                    if p.is_empty:
                        continue
                    p = p.simplify(G.TOL / 2, preserve_topology=True)
                    self._ground_holes += len(p.interiors)
                    # v3.9.4：DXF 的 LWPOLYLINE 只能存外框；有內孔的地切成沒有內孔的幾塊（HFSS 建模時會合併回一塊），
                    #         之前內孔直接被丟掉 → 上地蓋過訊號線（短路）
                    for piece in _split_holes(p):
                        out["ub"].append(G._dedupe([(round(x, 9), round(y, 9), 0.0)
                                                    for x, y in list(piece.exterior.coords)[:-1]]))
        return out

    @staticmethod
    def _path_ends(paths):
        """[(路徑索引, 端點, 往外的單位向量, 線寬, 間隙)]。"""
        ends = []
        for k, s in enumerate(paths):
            cl = s.centerline()
            if len(cl) < 2:
                continue
            for a, b in ((cl[1], cl[0]), (cl[-2], cl[-1])):     # (前一點, 端點)
                dx, dy = b[0] - a[0], b[1] - a[1]
                n = math.hypot(dx, dy)
                if n > 1e-12:
                    ends.append((k, b, (dx / n, dy / n), s.width, s.gap))
        return ends

    def end_gaps(self, paths):
        """v3.9.4：微波線的開路端也要離地一個間隙 G（之前端點直接碰到地 = 短路）。在板邊的端點不受影響。"""
        import shapely.geometry as sg
        out = []
        for _k, p, u, w, g in self._path_ends(paths):
            if g > 0:
                out.append(sg.LineString([p, (p[0] + u[0] * g, p[1] + u[1] * g)]).buffer(w / 2 + g, cap_style=2))
        return out

    def gap_bridges(self, paths=None):
        """v3.9.4：端對端的微波線之間（例如共振腔的耦合間隙）也挖空，槽線才會連續、地才會被切開。
        兩個端點距離 ≤ 3 ×（W + 2G），而且兩端互相朝向對方 → 中間補一段寬 W + 2G 的槽。"""
        import shapely.geometry as sg
        paths = paths if paths is not None else [s for s in self.shapes if s.kind == "path" and len(s.pts) >= 2]
        ends = self._path_ends(paths)
        out = []
        for i in range(len(ends)):
            for j in range(i + 1, len(ends)):
                ki, pi, ui, wi, gi = ends[i]
                kj, pj, uj, wj, gj = ends[j]
                if ki == kj:
                    continue
                vx, vy = pj[0] - pi[0], pj[1] - pi[1]
                d = math.hypot(vx, vy)
                half = max(wi, wj) / 2 + max(gi, gj)
                if d < 1e-9 or d > 6 * half:
                    continue
                if (ui[0] * vx + ui[1] * vy) < 0.9 * d or (uj[0] * -vx + uj[1] * -vy) < 0.9 * d:
                    continue                                   # 沒有面對面
                out.append(sg.LineString([pi, pj]).buffer(half, cap_style=2))
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
            for p in s.ends():
                side = _edge_side(p, bb)
                if side:
                    out.append((p, side))
        return out

    def check(self) -> List[str]:
        """存檔前的檢查：缺圖層、端點沒碰到板邊、上地有內孔、圓角。回傳警告（空 = 沒問題）。"""
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
            self.generated()
            if self._ground_holes:
                warn.append("自動產生的上地有被地包住的槽（例如微波線沒碰到板邊、或間隙很大的兩段微波線之間有地）："
                            "存檔時切成幾塊，HFSS 會合併回去；要確定這是你要的形狀")
        for s in self.shapes:
            if s.kind == "poly" and len(s.pts) < 3:
                warn.append(f"圖層 {s.layer} 有少於 3 個點的多邊形")
            if s.kind == "path" and len(s.pts) < 2:
                warn.append("有少於 2 個節點的微波線")
            if s.kind == "path" and not s.bezier and any(s.fillets):
                s.norm()
                _t, r_eff, _turn, clamped = G.fillet_info(s.pts, s.bulges, s.fillets, False)
                if any(0 < r < s.width / 2 for r in r_eff):
                    warn.append(f"有圓角半徑小於線寬的一半（{s.width / 2:g} mm）：轉角內側會重疊")
                elif any(clamped):
                    warn.append("有圓角太大、放不下，已經自動縮小（看節點表格的圓角）")
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
            boards[0].pts, boards[0].bulges, boards[0].fillets, boards[0].smooth = list(rect), [], [], ""
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
                    new = _move_to_edge(s.pts[i], old, nb)
                    if s.bezier and new != s.pts[i]:            # 錨點旁邊的控制點跟著移
                        j = 1 if i == 0 else len(s.pts) - 2
                        s.pts[j] = (s.pts[j][0] + new[0] - s.pts[i][0], s.pts[j][1] + new[1] - s.pts[i][1])
                    s.pts[i] = new
            elif s.kind == "poly" and s.layer not in BOARD_LAYERS[self.mode] and not (s.layer == "ub" and _is_rect(s.pts, nb)):
                s.pts = [_move_to_edge(p, old, nb) for p in s.pts]

    def convert(self, mode: str, width: Optional[float] = None, gap: Optional[float] = None) -> None:
        """v3.9：MS ⇄ CPW。板材圖層 b ⇄ db；轉成 MS 時拿掉上地 / 過孔；CPW 開自動上地；所有微波線套用新的線寬 / 間隙。"""
        if mode not in LAYERS:
            raise DxfError(f"不認得的建模方式 {mode}")
        old_board, new_board = BOARD_LAYERS[self.mode][0], BOARD_LAYERS[mode][0]
        for s in self.shapes:
            if s.layer == old_board:
                s.layer = new_board
        if mode == "MS":
            self.shapes = [s for s in self.shapes if s.layer not in ("ub", "v")]
            self.auto_ground = False
        else:
            self.shapes = [s for s in self.shapes if s.layer != "ub"]      # 手畫的上地 → 改用自動產生
            self.auto_ground = True
        self.mode = mode
        for s in self.shapes:
            if s.kind == "path":
                if width:
                    s.width = float(width)
                if gap is not None and mode == "CPW":
                    s.gap = float(gap)

    def via_fence(self, pitch: float, dia: float, clearance: float, rows: int = 1) -> List[Shape]:
        """CPW 的過孔牆：沿每條微波線兩側、離間隙邊 clearance（到過孔邊）、每 pitch 一個；只放在上地裡面。"""
        import shapely.geometry as sg
        from shapely.ops import unary_union
        paths = [s for s in self.shapes if s.kind == "path" and len(s.pts) >= 2]
        board = self.board_geom()
        if not paths or board.is_empty or pitch <= 0 or dia <= 0:
            return []
        r = dia / 2
        cuts = unary_union([sg.Polygon(s.ring(s.gap)).buffer(0) for s in paths if len(s.ring(s.gap)) >= 3])
        holes = unary_union([sg.Point(*s.pts[0]).buffer(s.r) for s in self.shapes if s.kind == "circle"] or [sg.Point(0, 0).buffer(0)])
        inner = board.buffer(-(r + pitch / 2))                   # 離板邊（Wave Port）至少半個間距
        out: List[Shape] = []
        placed: List[Point] = []
        for s in paths:
            line = sg.LineString(s.centerline())
            if line.length <= 0:
                continue
            for row in range(max(1, rows)):
                off = s.width / 2 + s.gap + clearance + r + row * pitch
                for side in (1, -1):
                    try:
                        side_line = line.offset_curve(side * off, join_style=2)
                    except Exception:  # noqa: BLE001
                        continue
                    if side_line.is_empty:
                        continue
                    parts = [side_line] if side_line.geom_type == "LineString" else list(getattr(side_line, "geoms", []))
                    for part in parts:
                        n = int(part.length // pitch)
                        start = (part.length - n * pitch) / 2
                        for k in range(n + 1):
                            p = part.interpolate(start + k * pitch)
                            c = sg.Point(p.x, p.y).buffer(r, 16)
                            if not inner.contains(sg.Point(p.x, p.y)):
                                continue
                            if c.distance(cuts) < clearance * 0.999 or c.intersects(holes):
                                continue
                            if any(math.hypot(p.x - q[0], p.y - q[1]) < pitch * 0.7 for q in placed):
                                continue
                            placed.append((round(p.x, 6), round(p.y, 6)))
                            out.append(Shape("circle", "v", [placed[-1]], r=r))
        return out

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
        d.bridge_gaps = bool(meta.get("bridge_gaps", True))
        for e in doc.modelspace():
            t, layer = e.dxftype(), e.dxf.layer
            x = _xdata(e)
            if x.get("gen"):
                continue                          # 存檔時產生的：重新產生
            if t == "DIMENSION" and x.get("dim"):
                try:
                    d.dims.append(Dim(str(x["dim"]), tuple(x["p1"]), tuple(x["p2"]), tuple(x["base"])))
                except (KeyError, TypeError, ValueError):
                    pass
                continue
            if t == "LWPOLYLINE":
                raw = [(float(px), float(py), float(b)) for px, py, b in e.get_points("xyb")]
                if layer == PATH_LAYER:
                    if x.get("pts"):              # v3.4：完整的節點（含圓角、Bézier）在 XDATA
                        s = Shape("path", x.get("layer", "g"), [tuple(p) for p in x["pts"]],
                                  width=float(x.get("width") or 1.0), gap=float(x.get("gap", 0.2)), handle=e.dxf.handle,
                                  bulges=[float(b) for b in x.get("bulges") or []],
                                  fillets=[float(f) for f in x.get("fillets") or []], smooth=x.get("smooth", ""))
                    else:
                        s = Shape("path", x.get("layer", "g"), [(p[0], p[1]) for p in raw],
                                  width=float(x.get("width") or e.dxf.const_width or 1.0), gap=float(x.get("gap", 0.2)),
                                  handle=e.dxf.handle, bulges=[p[2] for p in raw[:-1]])
                    s.norm()
                    d.shapes.append(s)
                elif e.closed or (len(raw) > 2 and _same(raw[0], raw[-1])):
                    if len(raw) > 2 and _same(raw[0], raw[-1]) and not e.closed:
                        raw = raw[:-1]
                    if x.get("pts"):                # v3.4：圓角 / Bézier 多邊形
                        s = Shape("poly", layer, [tuple(p) for p in x["pts"]], handle=e.dxf.handle,
                                  bulges=[float(b) for b in x.get("bulges") or []],
                                  fillets=[float(f) for f in x.get("fillets") or []], smooth=x.get("smooth", ""))
                    else:
                        s = Shape("poly", layer, [(p[0], p[1]) for p in raw], handle=e.dxf.handle,
                                  bulges=[p[2] for p in raw])
                    s.norm()
                    d.shapes.append(s)
                else:
                    d.foreign.append({"layer": layer, "pts": _lw_points(e)})
            elif t == "POLYLINE" and not e.is_3d_polyline and not e.is_poly_face_mesh and not e.is_polygon_mesh:
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                bul = [float(v.dxf.get("bulge", 0.0) or 0.0) for v in e.vertices]
                if e.is_closed and len(pts) >= 3:
                    d.shapes.append(Shape("poly", layer, pts, handle=e.dxf.handle, bulges=bul))
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
            xd = _xdata(e)
            if e.dxftype() == "DIMENSION" and xd.get("dim"):
                block = e.dxf.get("geometry")
                msp.delete_entity(e)
                if block and block in doc.blocks:
                    try:
                        doc.blocks.delete_block(block, safe=False)
                    except Exception:  # noqa: BLE001
                        pass
                continue
            if e.dxf.handle in keep or xd.get("gen") or (e.dxftype() == "LWPOLYLINE" and e.dxf.layer == PATH_LAYER):
                msp.delete_entity(e)
        if APPID not in doc.appids:
            doc.appids.new(APPID)
        for name, (desc, color) in LAYER_INFO.items():
            if name not in doc.layers:
                doc.layers.add(name, color=color)
        for s in self.shapes:
            s.norm()
            curved = s.bezier or any(s.fillets)
            if s.kind == "poly":
                e = msp.add_lwpolyline(s.verts(), format="xyb", close=True, dxfattribs={"layer": s.layer})
                if curved:                       # 圓角 / Bézier：原始節點存起來，之後打開還能編輯
                    _set_xdata(e, {"pts": s.pts, "bulges": s.bulges, "fillets": s.fillets, "smooth": s.smooth})
            elif s.kind == "circle":
                e = msp.add_circle(s.pts[0], s.r, dxfattribs={"layer": s.layer})
            else:
                line = ([(x, y, 0.0) for x, y in G.bezier_points(s.pts)] if s.bezier
                        else [(p[0], p[1], s.bulges[i] if i < len(s.bulges) else 0.0) for i, p in enumerate(s.pts)])
                e = msp.add_lwpolyline(line, format="xyb", close=False,
                                       dxfattribs={"layer": PATH_LAYER, "const_width": s.width})
                _set_xdata(e, {"width": s.width, "gap": s.gap, "layer": s.layer, "pts": s.pts, "bulges": s.bulges,
                               "fillets": s.fillets, "smooth": s.smooth})
            s.handle = e.dxf.handle
        for layer, polys in self.generated().items():
            for verts in polys:
                e = msp.add_lwpolyline(verts, format="xyb", close=True, dxfattribs={"layer": layer})
                _set_xdata(e, {"gen": 1})
        if self.dims:
            self._save_dims(doc, msp)
        meta = json.dumps({"mode": self.mode, "auto_ground": self.auto_ground, "bridge_gaps": self.bridge_gaps})
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


    def _save_dims(self, doc, msp) -> None:
        if DIM_LAYER not in doc.layers:
            doc.layers.add(DIM_LAYER, color=DIM_INFO[1])
        style = "Standard" if "Standard" in doc.dimstyles else next(iter(doc.dimstyles)).dxf.name
        x0, y0, x1, y1 = self.bounds()
        txt = max(min(x1 - x0, y1 - y0) / 40, 0.05)           # 字高跟著板子大小
        ov = {"dimtxt": txt, "dimasz": txt * 0.8, "dimexo": txt * 0.4, "dimexe": txt * 0.6, "dimgap": txt * 0.3,
              "dimdec": 4, "dimzin": 8, "dimtad": 1}
        for dm in self.dims:
            try:
                if dm.kind in ("h", "v"):
                    obj = msp.add_linear_dim(base=dm.base, p1=dm.p1, p2=dm.p2, angle=0 if dm.kind == "h" else 90,
                                             dimstyle=style, override=ov, dxfattribs={"layer": DIM_LAYER})
                else:
                    (ax, ay), (bx, by), (cx, cy) = dm.p1, dm.p2, dm.base
                    L = math.hypot(bx - ax, by - ay) or 1e-12
                    dist = ((cx - ax) * -(by - ay) + (cy - ay) * (bx - ax)) / L
                    obj = msp.add_aligned_dim(p1=dm.p1, p2=dm.p2, distance=dist, dimstyle=style, override=ov,
                                              dxfattribs={"layer": DIM_LAYER})
                obj.render()
                _set_xdata(obj.dimension, {"dim": dm.kind, "p1": list(dm.p1), "p2": list(dm.p2), "base": list(dm.base)})
            except Exception:  # noqa: BLE001 - 標註只是註記，存不了不影響建模
                continue


# ---- 小工具 -----------------------------------------------------------------------------------
def _split_holes(poly, depth: int = 0) -> list:
    """有內孔的多邊形 → 沒有內孔的幾塊（沿著內孔中心切一刀，遞迴）。"""
    from shapely.geometry import LineString
    from shapely.ops import split
    if poly.is_empty or not poly.interiors or depth > 40:
        return [poly] if not poly.is_empty else []
    hole = poly.interiors[0]
    cx = hole.centroid.x
    x0, y0, x1, y1 = poly.bounds
    try:
        parts = split(poly, LineString([(cx, y0 - 1), (cx, y1 + 1)]))
    except Exception:  # noqa: BLE001
        return [poly]
    out = []
    for g in getattr(parts, "geoms", [parts]):
        if g.geom_type == "Polygon" and g.area > 1e-12:
            out += _split_holes(g, depth + 1)
    return out or [poly]


def _same(a, b, tol: float = 1e-9) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _set_xdata(e, data: dict) -> None:
    """XDATA 每個字串最多 255 字：JSON 切成好幾段。"""
    text = json.dumps(data, separators=(",", ":"))
    e.set_xdata(APPID, [(1000, text[i:i + XDATA_CHUNK]) for i in range(0, len(text), XDATA_CHUNK)] or [(1000, "{}")])


def _xdata(e) -> dict:
    try:
        if e.has_xdata(APPID):
            text = "".join(str(v) for code, v in e.get_xdata(APPID) if code == 1000)
            return json.loads(text) if text else {}
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
