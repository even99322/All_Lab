"""DXF (AutoCAD 2013) -> layer polygons for the Scientific Figure Builder.

Layer rules (layer names are case-insensitive):
    Microstrip (MS):   b = board, g = guide, h = screw hole
    Coplanar (CPW):    db = down board, ub = up board, g = guide, h = screw hole, v = via
The mode is CPW when any of db / ub / v is present, otherwise MS.

Drawings are often made of loose LINE / open LWPOLYLINE pieces. Endpoints that
are closer than the snap tolerance are joined, the linework is split at every
crossing, and each enclosed face is solid when the even-odd rule says so
(a ring drawn as two outlines becomes a ring, not a disc).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union

REQUIRED_VERSION = "AC1027"                  # AutoCAD 2013 DXF
MS_LAYERS = ("b", "g", "h")
CPW_LAYERS = ("db", "ub", "g", "h", "v")
KNOWN = set(MS_LAYERS) | set(CPW_LAYERS)
ARC_SEGMENTS = 64


class DxfImportError(ValueError):
    pass


@dataclass
class ImportedDrawing:
    mode: str                                         # "MS" or "CPW"
    layers: dict[str, BaseGeometry]                   # layer -> (Multi)Polygon, holes already applied per layer
    version: str
    warnings: list[str] = field(default_factory=list)
    ignored_layers: list[str] = field(default_factory=list)
    tolerance: float = 0.0

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        shapes = [g for g in self.layers.values() if not g.is_empty]
        return unary_union(shapes).bounds if shapes else (0.0, 0.0, 1.0, 1.0)


# -- entities -> pieces ------------------------------------------------------------------------
def _arc_points(cx, cy, r, start_deg, end_deg, segments=ARC_SEGMENTS):
    if end_deg <= start_deg:
        end_deg += 360.0
    count = max(4, int(segments * (end_deg - start_deg) / 360.0))
    angles = np.radians(np.linspace(start_deg, end_deg, count + 1))
    return [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in angles]


def _bulge_points(p0, p1, bulge):
    """Points of a polyline segment with a bulge (arc), excluding p0."""
    if abs(bulge) < 1e-12:
        return [p1]
    from ezdxf.math import bulge_to_arc

    center, start, end, radius = bulge_to_arc(p0, p1, bulge)
    points = _arc_points(center.x, center.y, radius, math.degrees(start), math.degrees(end), 32)
    if bulge < 0:
        points = points[::-1]
    return points[1:]


def _entity_pieces(entity):
    """(closed polygons, open polylines) as point lists."""
    kind = entity.dxftype()
    closed, opened = [], []
    if kind == "LINE":
        opened.append([(entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y)])
    elif kind == "LWPOLYLINE":
        raw = list(entity.get_points("xyb"))
        if not raw:
            return closed, opened
        points = [(raw[0][0], raw[0][1])]
        segments = list(zip(raw, raw[1:] + ([raw[0]] if entity.closed else [])))
        for (x0, y0, b0), (x1, y1, _b1) in segments:
            points += _bulge_points((x0, y0), (x1, y1), b0)
        (closed if entity.closed else opened).append(points)
    elif kind == "POLYLINE" and not entity.is_3d_polyline and not entity.is_poly_face_mesh:
        points = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
        (closed if entity.is_closed else opened).append(points)
    elif kind == "CIRCLE":
        closed.append(_arc_points(entity.dxf.center.x, entity.dxf.center.y, entity.dxf.radius, 0, 360)[:-1])
    elif kind == "ARC":
        opened.append(_arc_points(entity.dxf.center.x, entity.dxf.center.y, entity.dxf.radius,
                                  entity.dxf.start_angle, entity.dxf.end_angle))
    elif kind in ("ELLIPSE", "SPLINE"):
        points = [(p.x, p.y) for p in entity.flattening(0.01)]
        if len(points) > 2 and math.dist(points[0], points[-1]) < 1e-9:
            closed.append(points[:-1])
        else:
            opened.append(points)
    else:
        return None
    return closed, opened


# -- open linework -> faces --------------------------------------------------------------------
def _snap_endpoints(polylines: list[list[tuple]], tolerance: float) -> list[list[tuple]]:
    from scipy.spatial import cKDTree

    ends = [p for line in polylines for p in (line[0], line[-1])]
    if not ends:
        return polylines
    array = np.asarray(ends, dtype=float)
    parent = list(range(len(array)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in cKDTree(array).query_pairs(tolerance):
        parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for index in range(len(array)):
        groups.setdefault(find(index), []).append(index)
    snapped = array.copy()
    for members in groups.values():
        snapped[members] = array[members].mean(axis=0)
    result = []
    for index, line in enumerate(polylines):
        new = [tuple(snapped[2 * index])] + [tuple(p) for p in line[1:-1]] + [tuple(snapped[2 * index + 1])]
        result.append(new)
    return result


def _even_odd_faces(polylines: list[list[tuple]], tolerance: float) -> list[Polygon]:
    lines = [LineString(p) for p in polylines if len(p) >= 2 and LineString(p).length > tolerance]
    if not lines:
        return []
    network = unary_union(lines)                       # splits at every crossing
    faces = [f for f in polygonize(network) if f.area > tolerance * tolerance]
    if not faces:
        return []
    minx, miny, maxx, maxy = network.bounds
    solid = []
    for face in faces:
        point = face.representative_point()
        # even-odd: count crossings of a ray to the right (slightly skewed to avoid vertices)
        ray = LineString([(point.x, point.y), (maxx + (maxx - minx) + 1.0, point.y + 1.234567e-4 * (maxy - miny + 1))])
        hits = ray.intersection(network)
        count = 0 if hits.is_empty else len(getattr(hits, "geoms", [hits]))
        if count % 2 == 1:
            solid.append(face)
    return solid


def _clean(geometry: BaseGeometry) -> BaseGeometry:
    geometry = geometry.buffer(0)
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    polygons = [g for g in getattr(geometry, "geoms", []) if isinstance(g, (Polygon, MultiPolygon))]
    return unary_union(polygons) if polygons else Polygon()


def auto_tolerance(extent: float) -> float:
    return max(0.002, extent * 2e-5)


def import_dxf(path: str | Path, tolerance: float | None = None) -> ImportedDrawing:
    import ezdxf

    try:
        doc = ezdxf.readfile(str(path))
    except (OSError, ezdxf.DXFStructureError) as error:
        raise DxfImportError(f"Cannot read the DXF file: {error}") from error
    warnings: list[str] = []
    if doc.dxfversion != REQUIRED_VERSION:
        warnings.append(f"The file is saved as {doc.dxfversion}, not AutoCAD 2013 ({REQUIRED_VERSION}); "
                        "results may differ. Save as \"AutoCAD 2013 DXF\".")
    closed: dict[str, list] = {}
    opened: dict[str, list] = {}
    ignored: set[str] = set()
    skipped_types: set[str] = set()
    for entity in doc.modelspace():
        layer = str(entity.dxf.layer).strip().lower()
        if layer not in KNOWN:
            ignored.add(str(entity.dxf.layer))
            continue
        pieces = _entity_pieces(entity)
        if pieces is None:
            skipped_types.add(entity.dxftype())
            continue
        closed.setdefault(layer, []).extend(pieces[0])
        opened.setdefault(layer, []).extend(pieces[1])
    if skipped_types:
        warnings.append("Ignored entity types: " + ", ".join(sorted(skipped_types)))
    present = {name for name in set(closed) | set(opened) if closed.get(name) or opened.get(name)}
    if not present:
        raise DxfImportError("No known layers (b, g, h or db, ub, g, h, v) were found.")
    mode = "CPW" if present & {"db", "ub", "v"} else "MS"
    all_points = [p for name in present for line in closed.get(name, []) + opened.get(name, []) for p in line]
    xs, ys = zip(*all_points)
    extent = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)
    tolerance = auto_tolerance(extent) if tolerance is None else float(tolerance)
    layers: dict[str, BaseGeometry] = {}
    for name in sorted(present):
        shapes = [Polygon(p) for p in closed.get(name, []) if len(p) >= 3]
        shapes = [s if s.is_valid else s.buffer(0) for s in shapes]
        lines = _snap_endpoints(opened.get(name, []), tolerance)
        faces = _even_odd_faces(lines, tolerance)
        # closed shapes combine even-odd with each other (a circle inside a closed outline is a hole)
        geometry = Polygon()
        for shape in shapes:
            geometry = geometry.symmetric_difference(shape)
        if faces:
            geometry = geometry.union(unary_union(faces)) if not geometry.is_empty else unary_union(faces)
        layers[name] = _clean(geometry)
        if layers[name].is_empty:
            warnings.append(f"Layer {name}: no closed outline was found.")
    required = {"b", "g"} if mode == "MS" else {"db", "ub", "g"}
    for name in sorted(required - present):
        warnings.append(f"Layer {name} is missing ({mode} mode).")
    return ImportedDrawing(mode=mode, layers=layers, version=doc.dxfversion, warnings=warnings,
                           ignored_layers=sorted(ignored), tolerance=tolerance)


def holes_of(drawing: ImportedDrawing) -> BaseGeometry:
    return drawing.layers.get("h", Polygon())


def polygons(geometry: BaseGeometry) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    return [g for g in getattr(geometry, "geoms", []) if isinstance(g, Polygon) and not g.is_empty]


def contains_point(geometry: BaseGeometry, x: float, y: float) -> bool:
    return geometry.contains(Point(x, y))
