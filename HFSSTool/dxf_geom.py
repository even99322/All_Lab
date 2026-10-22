"""DXF 編輯器的曲線幾何（v3.4）：圓角、弧線段（DXF bulge）、Bézier 曲線、微波線的精確輪廓。

節點串 = 點 + 每段的 bulge（DXF 的定義：tan(圓心角 / 4)，正 = 逆時針）+ 每個節點的圓角半徑。
    chain()           → 一串直線（"L"）與圓弧（"A"）
    path_outline()    → 微波線（中心線＋線寬）的封閉輪廓 [(x, y, bulge)…]：直線、圓角、弧線段都存成 DXF 真正的圓弧；
                        做不到精確時（例如弧線段與直線在尖角相接）退回細分的多邊形
    boundary()        → 多邊形邊界 [(x, y, bulge)…]
Bézier（smooth = "bezier"）：點 = [錨, 控, 控, 錨, 控, 控, 錨, …]，存檔時切成細小直線段。
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

Point = Tuple[float, float]
Vert = Tuple[float, float, float]          # (x, y, bulge)
EPS = 1e-9
TOL = 0.001                                 # 曲線切成直線段的弦高誤差（mm）


# ---- 向量 -----------------------------------------------------------------------------------------
def _sub(a, b): return (a[0] - b[0], a[1] - b[1])
def _add(a, b): return (a[0] + b[0], a[1] + b[1])
def _mul(a, k): return (a[0] * k, a[1] * k)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1]
def _cross(a, b): return a[0] * b[1] - a[1] * b[0]
def _len(a): return math.hypot(a[0], a[1])
def _left(u): return (-u[1], u[0])


def _unit(a) -> Optional[Point]:
    n = _len(a)
    return None if n < EPS else (a[0] / n, a[1] / n)


def _close(a, b, tol=1e-7) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


# ---- 圓弧 -----------------------------------------------------------------------------------------
def bulge_arc(p: Point, q: Point, b: float):
    """bulge 段 → (圓心, 半徑, 起始角, 掃過角)；掃過角正 = 逆時針。"""
    sweep = 4.0 * math.atan(b)
    c = _len(_sub(q, p))
    if c < EPS or abs(sweep) < EPS:
        return None
    r = c / (2.0 * math.sin(abs(sweep) / 2.0))
    mid = _mul(_add(p, q), 0.5)
    u = _unit(_sub(q, p))
    h = r * math.cos(sweep / 2.0)            # 圓心到弦中點（逆時針弧圓心在弦的左邊）
    center = _add(mid, _mul(_left(u), h if sweep > 0 else -h))
    a0 = math.atan2(p[1] - center[1], p[0] - center[0])
    return center, r, a0, sweep


def bulge_from_sagitta(p: Point, q: Point, m: Point) -> float:
    """拖弦的中點：m 到弦的距離（右邊為正）→ bulge = 2s / c。"""
    c = _len(_sub(q, p))
    if c < EPS:
        return 0.0
    u = _unit(_sub(q, p))
    s = _dot(_sub(m, p), (u[1], -u[0]))      # 右法向量
    b = 2.0 * s / c
    return max(-20.0, min(20.0, b))


def arc_mid(p: Point, q: Point, b: float) -> Point:
    """bulge 段的中點（拖曳把手的位置）。"""
    c = _len(_sub(q, p))
    mid = _mul(_add(p, q), 0.5)
    if c < EPS:
        return mid
    u = _unit(_sub(q, p))
    return _add(mid, _mul((u[1], -u[0]), b * c / 2.0))


# ---- 節點串 → 直線＋圓弧 --------------------------------------------------------------------------------
def _seg_lists(n: int, closed: bool, bulges, fillets):
    segs = n if closed else n - 1
    bul = [float(bulges[i]) if bulges and i < len(bulges) else 0.0 for i in range(max(segs, 0))]
    fil = [float(fillets[i]) if fillets and i < len(fillets) else 0.0 for i in range(n)]
    return segs, bul, fil


def fillet_info(pts: Sequence[Point], bulges, fillets, closed: bool):
    """每個節點實際的切點距離 t 與圓角半徑（太大的圓角縮到放得下）→ (t, r_eff, turn, clamped)。"""
    n = len(pts)
    segs, bul, fil = _seg_lists(n, closed, bulges, fillets)
    t = [0.0] * n
    turn = [0.0] * n
    for i in range(n):
        if fil[i] <= 0 or (not closed and (i == 0 or i == n - 1)) or n < 3:
            continue
        before, after = (i - 1) % segs if closed else i - 1, i % segs if closed else i
        if bul[before] or bul[after]:
            continue                                    # 弧線段的轉角不做圓角
        u1 = _unit(_sub(pts[i], pts[i - 1]))
        u2 = _unit(_sub(pts[(i + 1) % n], pts[i]))
        if u1 is None or u2 is None:
            continue
        phi = math.atan2(_cross(u1, u2), _dot(u1, u2))
        if abs(phi) < 1e-9 or abs(abs(phi) - math.pi) < 1e-6:
            continue
        t[i] = fil[i] * math.tan(abs(phi) / 2.0)
        turn[i] = phi
    clamped = [False] * n
    for s in range(segs):
        i, j = s, (s + 1) % n
        L = _len(_sub(pts[j], pts[i]))
        need = t[i] + t[j]
        if need > L + EPS and need > 0:
            k = L / need
            for x in (i, j):
                if t[x] > 0:
                    t[x] *= k
                    clamped[x] = True
    r_eff = [t[i] / math.tan(abs(turn[i]) / 2.0) if t[i] > 0 else 0.0 for i in range(n)]
    return t, r_eff, turn, clamped


def chain(pts: Sequence[Point], bulges=None, fillets=None, closed: bool = False) -> list:
    """→ [("L", p, q) | ("A", center, r, a0, sweep, p, q)]，依序首尾相接。"""
    pts = [tuple(p) for p in pts]
    n = len(pts)
    if n < 2:
        return []
    segs, bul, _fil = _seg_lists(n, closed, bulges, fillets)
    t, r_eff, turn, _cl = fillet_info(pts, bulges, fillets, closed)
    out = []

    def start_of(i, j):
        if t[i] <= 0:
            return pts[i]
        return _add(pts[i], _mul(_unit(_sub(pts[j], pts[i])), t[i]))

    def end_of(i, j):
        if t[j] <= 0:
            return pts[j]
        return _sub(pts[j], _mul(_unit(_sub(pts[j], pts[i])), t[j]))

    def fillet_arc(j):
        u1 = _unit(_sub(pts[j], pts[j - 1]))
        u2 = _unit(_sub(pts[(j + 1) % n], pts[j]))
        p = _sub(pts[j], _mul(u1, t[j]))
        q = _add(pts[j], _mul(u2, t[j]))
        r = r_eff[j]
        nrm = _left(u1) if turn[j] > 0 else (u1[1], -u1[0])
        c = _add(p, _mul(nrm, r))
        a0 = math.atan2(p[1] - c[1], p[0] - c[0])
        return ("A", c, r, a0, turn[j], p, q)

    for s in range(segs):
        i, j = s, (s + 1) % n
        if bul[s]:
            arc = bulge_arc(pts[i], pts[j], bul[s])
            if arc is not None:
                c, r, a0, sweep = arc
                out.append(("A", c, r, a0, sweep, pts[i], pts[j]))
            else:
                out.append(("L", pts[i], pts[j]))
        else:
            p, q = start_of(i, j), end_of(i, j)
            if not _close(p, q, 1e-12):
                out.append(("L", p, q))
        if t[j] > 0 and (closed or j != n - 1):
            out.append(fillet_arc(j))
    return out


def _arc_steps(r: float, sweep: float, tol: float) -> int:
    if r <= tol:
        return max(2, int(abs(sweep) / (math.pi / 8)) + 1)
    step = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - tol / r)))
    return max(2, min(720, int(math.ceil(abs(sweep) / max(step, 1e-6)))))


def flatten(prims: list, tol: float = TOL) -> List[Point]:
    out: List[Point] = []
    for pr in prims:
        if pr[0] == "L":
            if not out or not _close(out[-1], pr[1]):
                out.append(pr[1])
            out.append(pr[2])
        else:
            _a, c, r, a0, sweep, p, q = pr
            k = _arc_steps(r, sweep, tol)
            if not out or not _close(out[-1], p):
                out.append(p)
            for m in range(1, k):
                a = a0 + sweep * m / k
                out.append((c[0] + r * math.cos(a), c[1] + r * math.sin(a)))
            out.append(q)
    return out


def boundary(pts, bulges=None, fillets=None) -> List[Vert]:
    """多邊形（封閉）→ DXF 頂點 [(x, y, bulge)]（圓角變成真正的圓弧）。"""
    verts: List[Vert] = []
    for pr in chain(pts, bulges, fillets, closed=True):
        if pr[0] == "L":
            verts.append((pr[1][0], pr[1][1], 0.0))
        else:
            verts.append((pr[5][0], pr[5][1], math.tan(pr[4] / 4.0)))
    return _dedupe(verts)


def ring_points(verts: Sequence[Vert], tol: float = TOL) -> List[Point]:
    """DXF 頂點（含 bulge）→ 細分的封閉點列（畫面、布林運算用）。"""
    n = len(verts)
    out: List[Point] = []
    for i in range(n):
        x, y, b = verts[i]
        nx, ny, _ = verts[(i + 1) % n]
        out.append((x, y))
        if b:
            arc = bulge_arc((x, y), (nx, ny), b)
            if arc:
                c, r, a0, sweep = arc
                k = _arc_steps(r, sweep, tol)
                for m in range(1, k):
                    a = a0 + sweep * m / k
                    out.append((c[0] + r * math.cos(a), c[1] + r * math.sin(a)))
    return out


def _dedupe(verts: List[Vert]) -> List[Vert]:
    out: List[Vert] = []
    for v in verts:
        if out and _close(out[-1], v):
            out[-1] = (out[-1][0], out[-1][1], v[2] or out[-1][2])
            continue
        out.append(v)
    if len(out) > 1 and _close(out[0], out[-1]):
        out.pop()
    return out


# ---- 微波線輪廓 ----------------------------------------------------------------------------------
def _offset(pr, d: float):
    if pr[0] == "L":
        u = _unit(_sub(pr[2], pr[1]))
        if u is None:
            return None
        n = _mul(_left(u), d)
        return ["L", _add(pr[1], n), _add(pr[2], n)]
    _a, c, r, a0, sweep, _p, _q = pr
    r2 = r - d * (1 if sweep > 0 else -1)
    if r2 <= 1e-6:
        return None
    p = (c[0] + r2 * math.cos(a0), c[1] + r2 * math.sin(a0))
    q = (c[0] + r2 * math.cos(a0 + sweep), c[1] + r2 * math.sin(a0 + sweep))
    return ["A", c, r2, a0, sweep, p, q]


def _end(pr): return pr[2] if pr[0] == "L" else pr[6]
def _start(pr): return pr[1] if pr[0] == "L" else pr[5]


def _set_end(pr, p):
    if pr[0] == "L":
        pr[2] = p


def _set_start(pr, p):
    if pr[0] == "L":
        pr[1] = p


def _line_x(a, b) -> Optional[Point]:
    d1, d2 = _sub(a[2], a[1]), _sub(b[2], b[1])
    den = _cross(d1, d2)
    if abs(den) < 1e-12:
        return None
    t = _cross(_sub(b[1], a[1]), d2) / den
    return _add(a[1], _mul(d1, t))


def _side(prims, d: float):
    offs = [_offset(p, d) for p in prims]
    if any(o is None for o in offs):
        return None
    for k in range(len(offs) - 1):
        a, b = offs[k], offs[k + 1]
        if _close(_end(a), _start(b), 1e-6):
            continue
        if a[0] == "L" and b[0] == "L":
            x = _line_x(a, b)
            if x is None:
                return None
            _set_end(a, x)
            _set_start(b, x)
        else:
            return None                                 # 弧線與其他段在尖角相接：精確輪廓不好做，退回細分
    return offs


def _bulge_of(pr) -> float:
    return 0.0 if pr[0] == "L" else math.tan(pr[4] / 4.0)


def _reverse(pr):
    if pr[0] == "L":
        return ["L", pr[2], pr[1]]
    _a, c, r, a0, sweep, p, q = pr
    return ["A", c, r, a0 + sweep, -sweep, q, p]


def path_outline(pts, width: float, bulges=None, fillets=None, extra: float = 0.0) -> List[Vert]:
    """微波線（中心線＋線寬，平頭）→ 封閉輪廓 [(x, y, bulge)]；extra = 每邊再往外（CPW 間隙）。"""
    pts = [tuple(p) for p in pts]
    if len(pts) < 2 or width <= 0:
        return []
    d = width / 2.0 + extra
    prims = chain(pts, bulges, fillets, closed=False)
    if not prims:
        return []
    left = _side(prims, d)
    right = _side(prims, -d)
    if left is not None and right is not None:
        verts: List[Vert] = [(_start(p)[0], _start(p)[1], _bulge_of(p)) for p in left]
        e = _end(left[-1])
        verts.append((e[0], e[1], 0.0))                  # 尾端平頭
        rr = [_reverse(p) for p in reversed(right)]
        verts += [(_start(p)[0], _start(p)[1], _bulge_of(p)) for p in rr]
        e = _end(rr[-1])
        verts.append((e[0], e[1], 0.0))                  # 起點平頭
        return _dedupe(verts)
    return _dense_outline(flatten(prims), d)


def _dense_outline(line: List[Point], d: float) -> List[Vert]:
    import shapely.geometry as sg
    clean = [p for i, p in enumerate(line) if i == 0 or not _close(p, line[i - 1], 1e-12)]
    if len(clean) < 2:
        return []
    g = sg.LineString(clean).buffer(d, cap_style="flat", join_style="mitre", mitre_limit=10.0)
    polys = [g] if g.geom_type == "Polygon" else [x for x in getattr(g, "geoms", []) if x.geom_type == "Polygon"]
    if not polys:
        return []
    poly = max(polys, key=lambda p: p.area).simplify(TOL / 2, preserve_topology=True)
    return _dedupe([(x, y, 0.0) for x, y in list(poly.exterior.coords)[:-1]])


# ---- Bézier -------------------------------------------------------------------------------------
def bezier_from_anchors(anchors: Sequence[Point], closed: bool = False) -> List[Point]:
    """經過這些錨點的平滑曲線（Catmull-Rom 換成三次 Bézier）→ [錨, 控, 控, 錨, …]。"""
    a = [tuple(p) for p in anchors]
    n = len(a)
    if n < 2:
        return list(a)
    tang = []
    for i in range(n):
        if closed:
            prev, nxt = a[(i - 1) % n], a[(i + 1) % n]
        else:
            prev, nxt = a[max(i - 1, 0)], a[min(i + 1, n - 1)]
        tang.append(_mul(_sub(nxt, prev), 0.5 if (closed or 0 < i < n - 1) else 1.0))
    out: List[Point] = []
    segs = n if closed else n - 1
    for i in range(segs):
        j = (i + 1) % n
        out += [a[i], _add(a[i], _mul(tang[i], 1 / 3)), _sub(a[j], _mul(tang[j], 1 / 3))]
    if not closed:
        out.append(a[-1])
    return out


def bezier_segments(ctrl: Sequence[Point], closed: bool) -> List[Tuple[Point, Point, Point, Point]]:
    c = [tuple(p) for p in ctrl]
    n = len(c)
    segs = []
    k = n // 3 if closed else (n - 1) // 3
    for i in range(k):
        p0, p1, p2 = c[3 * i], c[3 * i + 1], c[3 * i + 2]
        p3 = c[(3 * i + 3) % n] if closed else c[3 * i + 3]
        segs.append((p0, p1, p2, p3))
    return segs


def bez_point(seg, t: float) -> Point:
    p0, p1, p2, p3 = seg
    u = 1 - t
    return (u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1])


def bezier_points(ctrl: Sequence[Point], closed: bool = False, tol: float = TOL) -> List[Point]:
    out: List[Point] = []
    for seg in bezier_segments(ctrl, closed):
        L = sum(_len(_sub(seg[i + 1], seg[i])) for i in range(3))
        k = max(8, min(400, int(math.ceil(math.sqrt(max(L, EPS) / max(tol, 1e-6)) * 2))))
        for m in range(0 if not out else 1, k + 1):
            out.append(bez_point(seg, m / k))
    if closed and len(out) > 1 and _close(out[0], out[-1]):
        out.pop()
    return out


def split_bezier(seg, t: float):
    """de Casteljau：一段切成兩段 → (左 4 點, 右 4 點)。"""
    p0, p1, p2, p3 = seg
    lerp = lambda a, b: (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)  # noqa: E731
    a, b, c = lerp(p0, p1), lerp(p1, p2), lerp(p2, p3)
    d, e = lerp(a, b), lerp(b, c)
    f = lerp(d, e)
    return (p0, a, d, f), (f, e, c, p3)


def nearest_bezier(ctrl, p: Point, closed: bool) -> Tuple[int, float, float]:
    """最接近 p 的 (段, t, 距離)。"""
    best = (0, 0.5, float("inf"))
    for i, seg in enumerate(bezier_segments(ctrl, closed)):
        for m in range(0, 101):
            t = m / 100
            q = bez_point(seg, t)
            dist = _len(_sub(q, p))
            if dist < best[2]:
                best = (i, t, dist)
    return best


def arc3_bulge(p0: Point, p1: Point, p2: Point) -> float:
    """從 p0 經過 p1 到 p2 的圓弧 → bulge（三點畫弧）；三點共線 → 0（直線）。"""
    ax, ay = p0
    bx, by = p1
    cx, cy = p2
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return 0.0
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    a0 = math.atan2(ay - uy, ax - ux)
    a2 = math.atan2(cy - uy, cx - ux)
    if _cross(_sub(p1, p0), _sub(p2, p1)) > 0:          # 逆時針經過三點
        sweep = (a2 - a0) % (2 * math.pi)
    else:
        sweep = -((a0 - a2) % (2 * math.pi))
    return math.tan(sweep / 4.0)
