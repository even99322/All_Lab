"""Scene of the Scientific Figure Builder: stacked board layers, YIG spheres, coils, sine waves.

World units are millimetres: x / y on the board plane, z up (0 = underside of
the lowest layer). The view is an orthographic camera, so the same numbers
place shapes in the editor and in PowerPoint (where every layer is a flat
shape tilted by PowerPoint's own 3-D rotation and given a 3-D depth).
"""

from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass, field

from shapely import wkt
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

COPPER = 0.032                  # mm (default, editable)
DIELECTRIC = 0.813              # mm (default, editable)

COLORS = {
    "copper": "#E8B04B", "dielectric": "#D5DADF", "ground": "#6F767D",
    "via": "#7B3FBF", "yig": "#2E3238", "spin": "#FFFFFF", "coil": "#A9AEB4", "arrow": "#E53935",
    "wave": "#111111",
}


@dataclass
class Layer:
    key: str                     # "ground", "dielectric", "top_ground", "guide", "via"
    name: str
    geometry: BaseGeometry = field(default_factory=Polygon)
    thickness: float = COPPER
    color: str = COLORS["copper"]
    visible: bool = True
    placement: str = "stack"     # "stack" (on the layer below), "same" (level of the layer below), "through"
    material: str = "metal"      # PowerPoint preset material
    z0: float = 0.0              # computed
    z1: float = 0.0              # computed


@dataclass
class Yig:
    id: int
    x: float
    y: float
    radius: float = 0.5
    color: str = COLORS["yig"]
    spin_color: str = COLORS["spin"]
    spins: int = 7
    lift: float = 0.0            # gap between sphere and the surface below


@dataclass
class Coil:
    id: int
    x: float
    y: float
    radius: float = 1.6
    wire: float = 0.35
    turns: int = 6
    pitch: float = 0.55
    gap: float = 0.6             # distance under the board
    color: str = COLORS["coil"]
    arrow: bool = True
    arrow_color: str = COLORS["arrow"]


@dataclass
class Wave:
    id: int
    x: float                     # box in figure (screen) units; free width / height
    y: float
    w: float
    h: float
    cycles: float = 2.5
    phase: float = 0.0           # degrees
    color: str = COLORS["wave"]
    width: float = 2.5           # line width, pt
    dashed: bool = True


@dataclass
class Camera:
    elevation: float = 28.0      # degrees above the board plane (90 = top view)
    azimuth: float = 0.0         # degrees around the vertical axis


@dataclass
class Scene:
    mode: str = "MS"
    layers: list[Layer] = field(default_factory=list)
    holes: BaseGeometry = field(default_factory=Polygon)
    yigs: list[Yig] = field(default_factory=list)
    coils: list[Coil] = field(default_factory=list)
    waves: list[Wave] = field(default_factory=list)
    camera: Camera = field(default_factory=Camera)
    source: str = ""
    _next_id: int = 1

    # -- construction ----------------------------------------------------------------------
    @classmethod
    def from_drawing(cls, drawing, source: str = "") -> "Scene":
        g = drawing.layers
        empty = Polygon()
        holes = g.get("h", empty)
        if drawing.mode == "CPW":
            vias = g.get("v", empty)
            layers = [
                Layer("ground", "Bottom ground (db)", g.get("db", empty), COPPER, COLORS["copper"]),
                Layer("dielectric", "Dielectric (db)", g.get("db", empty), DIELECTRIC, COLORS["dielectric"],
                      material="plastic"),
                Layer("top_ground", "Top ground (ub)", g.get("ub", empty), COPPER, COLORS["copper"]),
                Layer("guide", "Guide (g)", g.get("g", empty), COPPER, COLORS["copper"], placement="same"),
                Layer("via", "Via (v)", vias, 0.0, COLORS["via"], placement="through"),
            ]
        else:
            layers = [
                Layer("ground", "Ground plane (b)", g.get("b", empty), COPPER, COLORS["copper"]),
                Layer("dielectric", "Dielectric (b)", g.get("b", empty), DIELECTRIC, COLORS["dielectric"],
                      material="plastic"),
                Layer("guide", "Guide (g)", g.get("g", empty), COPPER, COLORS["copper"]),
            ]
        scene = cls(mode=drawing.mode, layers=layers, holes=holes, source=source)
        scene.update_levels()
        return scene

    def new_id(self) -> int:
        self._next_id += 1
        return self._next_id - 1

    # -- geometry ----------------------------------------------------------------------------
    def update_levels(self) -> None:
        """z of every layer from the list order (bottom first) and each layer's placement."""
        z = 0.0
        previous = None
        for layer in self.layers:
            if layer.placement == "through":
                continue
            if layer.placement == "same" and previous is not None:
                layer.z0 = previous.z0
            else:
                layer.z0 = z
            layer.z1 = layer.z0 + layer.thickness
            z = max(z, layer.z1)
            previous = layer
        for layer in self.layers:
            if layer.placement == "through":
                layer.z0, layer.z1 = 0.0, z
                layer.thickness = z

    @property
    def top(self) -> float:
        return max((l.z1 for l in self.layers if l.visible), default=0.0)

    def solid(self, layer: Layer) -> BaseGeometry:
        """The layer as drawn: screw holes go through everything; vias pass through the other layers."""
        shape = layer.geometry
        if shape.is_empty:
            return shape
        if not self.holes.is_empty:
            shape = shape.difference(self.holes)
        if layer.placement != "through":
            vias = [l.geometry for l in self.layers if l.placement == "through" and l.visible
                    and not l.geometry.is_empty]
            if vias:
                shape = shape.difference(unary_union(vias))
        return shape

    def board_outline(self) -> BaseGeometry:
        shapes = [l.geometry for l in self.layers if not l.geometry.is_empty]
        return unary_union(shapes) if shapes else Polygon()

    def surface_height(self, x: float, y: float) -> float:
        """Height of the top of the stack at (x, y) (where a YIG sits)."""
        from shapely.geometry import Point

        point = Point(x, y)
        top = 0.0
        for layer in self.layers:
            if layer.visible and not layer.geometry.is_empty and self.solid(layer).covers(point):
                top = max(top, layer.z1)
        return top

    def bottom(self) -> float:
        return min((l.z0 for l in self.layers if l.visible), default=0.0)

    # -- objects -----------------------------------------------------------------------------
    def add_yig(self, x=None, y=None) -> Yig:
        cx, cy = self._center()
        yig = Yig(self.new_id(), cx if x is None else x, cy if y is None else y, radius=self._size() * 0.035)
        self.yigs.append(yig)
        return yig

    def add_coil(self, x=None, y=None) -> Coil:
        cx, _cy = self._center()
        size = self._size()
        wire = size * 0.012
        if y is None:
            # just inside the front edge: a coil in the middle would be hidden under the board
            _minx, miny, _maxx, _maxy = self.board_outline().bounds if not self.board_outline().is_empty else (0, 0, 0, 0)
            y = miny + size * 0.07
        coil = Coil(self.new_id(), cx if x is None else x, y, radius=size * 0.055,
                    wire=wire, pitch=wire * 2.2, turns=7, gap=size * 0.02)
        self.coils.append(coil)
        return coil

    def add_wave(self, x=0.1, y=0.1, w=0.8, h=0.5) -> Wave:
        wave = Wave(self.new_id(), x, y, w, h)
        self.waves.append(wave)
        return wave

    def remove(self, item) -> None:
        for collection in (self.yigs, self.coils, self.waves):
            if item in collection:
                collection.remove(item)

    def _center(self):
        minx, miny, maxx, maxy = self.board_outline().bounds if not self.board_outline().is_empty else (0, 0, 10, 10)
        return (minx + maxx) / 2, (miny + maxy) / 2

    def _size(self) -> float:
        outline = self.board_outline()
        if outline.is_empty:
            return 10.0
        minx, miny, maxx, maxy = outline.bounds
        return max(maxx - minx, maxy - miny, 1e-6)

    # -- save / load -------------------------------------------------------------------------
    def to_dict(self) -> dict:
        layers = []
        for layer in self.layers:
            row = asdict(layer)
            row["geometry"] = layer.geometry.wkt
            layers.append(row)
        return {"format": "llv-figure-1", "mode": self.mode, "layers": layers, "holes": self.holes.wkt,
                "yigs": [asdict(y) for y in self.yigs], "coils": [asdict(c) for c in self.coils],
                "waves": [asdict(w) for w in self.waves], "camera": asdict(self.camera), "source": self.source,
                "next_id": self._next_id}

    @classmethod
    def from_dict(cls, data: dict) -> "Scene":
        if data.get("format") != "llv-figure-1":
            raise ValueError("Not a Figure Builder file.")
        layers = []
        for row in data["layers"]:
            row = dict(row)
            row["geometry"] = wkt.loads(row["geometry"])
            layers.append(Layer(**row))
        scene = cls(mode=data["mode"], layers=layers, holes=wkt.loads(data["holes"]),
                    yigs=[Yig(**y) for y in data["yigs"]], coils=[Coil(**c) for c in data["coils"]],
                    waves=[Wave(**w) for w in data["waves"]], camera=Camera(**data["camera"]),
                    source=data.get("source", ""), _next_id=int(data.get("next_id", 1)))
        scene.update_levels()
        return scene

    def copy(self) -> "Scene":
        return copy.deepcopy(self)


# -- orthographic projection --------------------------------------------------------------------
class Projection:
    """World (x, y, z) -> figure plane (X right, Y up) and depth (towards the viewer)."""

    def __init__(self, camera: Camera):
        self.phi = math.radians(max(1.0, min(90.0, camera.elevation)))
        self.theta = math.radians(camera.azimuth)
        self.sp, self.cp = math.sin(self.phi), math.cos(self.phi)
        self.st, self.ct = math.sin(self.theta), math.cos(self.theta)

    def rotate(self, x, y):
        """Board plane rotated by the azimuth (what PowerPoint receives as the shape outline)."""
        return x * self.ct - y * self.st, x * self.st + y * self.ct

    def project(self, x, y, z):
        rx, ry = self.rotate(x, y)
        return rx, ry * self.sp + z * self.cp

    def depth(self, x, y, z):
        _rx, ry = self.rotate(x, y)
        return -ry * self.cp + z * self.sp

    def unproject(self, X, Y, z):
        """Figure point -> board point on the plane at height z."""
        ry = (Y - z * self.cp) / self.sp
        rx = X
        return rx * self.ct + ry * self.st, -rx * self.st + ry * self.ct
