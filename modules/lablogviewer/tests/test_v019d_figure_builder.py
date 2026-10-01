"""v0.19D Scientific Figure Builder: DXF layers, 3-D scene, editing, PowerPoint / image export."""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

SAMPLES = Path(__file__).resolve().parents[2] / "autocad測試檔"
SINGLE = SAMPLES / "singlenonHwithgap.dxf"
HEX = SAMPLES / "hex0531gap.dxf"
needs_samples = pytest.mark.skipif(not SINGLE.exists(), reason="AutoCAD sample drawings unavailable")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _cpw_dxf(path: Path) -> Path:
    """A small coplanar-waveguide drawing made of loose lines (like real drawings)."""
    import ezdxf

    doc = ezdxf.new("R2013")
    msp = doc.modelspace()
    for name in ("db", "ub", "g", "h", "v"):
        doc.layers.add(name.upper() if name == "ub" else name)       # layer names are case-insensitive

    def rect(layer, x0, y0, x1, y1, lines=True):
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        if lines:
            for a, b in zip(pts, pts[1:] + pts[:1]):
                msp.add_line(a, b, dxfattribs={"layer": layer})
        else:
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})

    rect("db", -10, -15, 10, 15)
    rect("UB", -10, -15, -1.5, 15, lines=False)
    rect("UB", 1.5, -15, 10, 15, lines=False)
    rect("g", -0.8, -15, 0.8, 15)
    for x, y in ((-7, 12), (7, 12), (-7, -12), (7, -12)):
        msp.add_circle((x, y), 1.0, dxfattribs={"layer": "h"})
    for y in range(-10, 11, 4):
        msp.add_circle((-3, y), 0.3, dxfattribs={"layer": "v"})
        msp.add_circle((3, y), 0.3, dxfattribs={"layer": "v"})
    doc.saveas(path)
    return path


@needs_samples
def test_microstrip_drawings_are_recognised():
    from app.figure_builder.dxf_import import import_dxf, polygons

    single = import_dxf(SINGLE)
    assert single.mode == "MS" and single.version == "AC1027" and single.warnings == []
    assert [len(polygons(single.layers[k])) for k in "bgh"] == [1, 6, 4]
    hexagons = import_dxf(HEX)                                   # tiny gaps (0.00125 mm) are joined
    assert len(polygons(hexagons.layers["g"])) >= 30 and len(polygons(hexagons.layers["h"])) == 4
    from shapely.geometry import Point

    assert not single.layers["g"].contains(Point(0, 0))           # the hexagon is a ring of strips, not a disc
    assert single.layers["g"].contains(Point(0, 10))              # the feed line is solid


def test_cpw_mode_layers_and_through_holes(tmp_path):
    from shapely.geometry import Point

    from app.figure_builder.dxf_import import import_dxf
    from app.figure_builder.scene import COPPER, DIELECTRIC, Scene

    drawing = import_dxf(_cpw_dxf(tmp_path / "cpw.dxf"))
    assert drawing.mode == "CPW" and set(drawing.layers) == {"db", "ub", "g", "h", "v"}
    scene = Scene.from_drawing(drawing)
    by_key = {layer.key: layer for layer in scene.layers}
    assert by_key["ground"].thickness == COPPER and by_key["dielectric"].thickness == DIELECTRIC
    assert by_key["guide"].z0 == by_key["top_ground"].z0 == pytest.approx(COPPER + DIELECTRIC)
    via = by_key["via"]
    assert via.color.upper() == "#7B3FBF" and via.z0 == 0 and via.z1 == pytest.approx(3 * COPPER + DIELECTRIC - COPPER)
    hole, via_point = Point(-7, 12), Point(-3, 2)
    for layer in scene.layers:
        assert not scene.solid(layer).contains(hole)               # screw holes go through every layer
    assert scene.solid(via).contains(via_point)
    assert not scene.solid(by_key["dielectric"]).contains(via_point)


def test_scene_round_trip_and_layer_order(tmp_path):
    from app.figure_builder.dxf_import import import_dxf
    from app.figure_builder.scene import Scene

    scene = Scene.from_drawing(import_dxf(_cpw_dxf(tmp_path / "cpw.dxf")))
    scene.add_yig(0, 5)
    scene.add_coil()
    scene.add_wave(0.1, 0.2, 0.7, 0.3)
    scene.layers[1].thickness = 1.5
    scene.update_levels()
    copy = Scene.from_dict(json.loads(json.dumps(scene.to_dict())))
    assert copy.to_dict() == scene.to_dict()
    assert copy.top == pytest.approx(0.032 + 1.5 + 0.032)
    yig = copy.yigs[0]
    assert copy.surface_height(yig.x, yig.y) == pytest.approx(copy.top)   # on the guide


def test_projection_round_trip():
    from app.figure_builder.scene import Camera, Projection

    proj = Projection(Camera(elevation=33, azimuth=40))
    X, Y = proj.project(3.0, -2.0, 0.8)
    assert proj.unproject(X, Y, 0.8) == pytest.approx((3.0, -2.0))


@needs_samples
def test_editor_drag_resize_undo(qapp):
    from PySide6.QtCore import QPointF

    from app.figure_builder import render
    from app.figure_builder.scene import Projection
    from app.figure_builder.window import FigureBuilderWindow

    window = FigureBuilderWindow(None)
    window.resize(1200, 800)
    window.show()
    assert window.load_dxf(str(SINGLE))
    yig = window.add_yig()
    wave = window.add_wave()
    canvas = window.canvas
    canvas.repaint()
    qapp.processEvents()
    frame = canvas._frame
    proj = Projection(window.scene.camera)
    center, _r, _g = render.yig_screen(window.scene, yig, proj, frame)
    assert canvas.item_at(center) is yig
    canvas.select(yig)
    canvas.move_item_to(yig, QPointF(center.x() + 60, center.y()))
    assert yig.x > 0.5                                          # followed the pointer on the board
    window._commit()
    # the sine wave box resizes freely: only its width changes when the right edge is dragged
    canvas.select(wave)
    old = (wave.x, wave.y, wave.w, wave.h)
    right = canvas.item_rect(wave, frame).right()
    canvas.resize_wave(wave, "e", QPointF(right - 120, 10), old)
    assert wave.w < old[3 - 1] and wave.h == old[3] and wave.y == old[1]
    window._commit()
    window.undo()
    restored = window.scene.waves[0]
    assert (restored.w, restored.h) == pytest.approx((old[2], old[3]))
    window.redo()
    assert window.scene.waves[0].w < old[2]
    window.close()


@needs_samples
def test_pptx_parts_are_separate_3d_shapes(qapp, tmp_path):
    from app.figure_builder.dxf_import import import_dxf
    from app.figure_builder.pptx_export import export_pptx, slide_shape_summary
    from app.figure_builder.scene import Scene

    scene = Scene.from_drawing(import_dxf(SINGLE))
    scene.add_yig(0, 9)
    scene.add_coil()
    scene.add_wave()
    path = export_pptx(scene, tmp_path / "figure.pptx")
    names = [name for name, _kind in slide_shape_summary(path)]
    for expected in ("Ground plane (b)", "Dielectric (b)", "Guide (g) 1", f"YIG {scene.yigs[0].id} sphere",
                     f"Coil {scene.coils[0].id} turn 1", f"Sine wave {scene.waves[0].id}"):
        assert expected in names
    xml = zipfile.ZipFile(path).read("ppt/slides/slide1.xml").decode()
    assert "<pic:" not in xml and "<p:pic" not in xml                   # no pictures, only shapes
    assert xml.count("<a:sp3d") >= 8 and 'extrusionH="' in xml and 'prst="orthographicFront"' in xml
    assert 'prst="circle"' in xml                                          # sphere / tube bevels
    from pptx import Presentation

    Presentation(str(path))                                                 # re-opens cleanly


@needs_samples
def test_image_exports(qapp, tmp_path):
    from app.figure_builder.window import FigureBuilderWindow

    window = FigureBuilderWindow(None)
    window.load_dxf(str(HEX))
    window.add_yig()
    for suffix in ("png", "svg", "pdf"):
        path = window.export_image(str(tmp_path / f"f.{suffix}"), width=800)
        assert path.stat().st_size > 1000
    assert "<svg" in (tmp_path / "f.svg").read_text(encoding="utf-8")[:400]
    window.close()


def test_bad_dxf_is_reported(tmp_path):
    from app.figure_builder.dxf_import import DxfImportError, import_dxf

    (tmp_path / "x.dxf").write_text("not a dxf", encoding="utf-8")
    with pytest.raises(DxfImportError):
        import_dxf(tmp_path / "x.dxf")


def test_processing_menu_opens_builder_in_its_own_process(qapp, monkeypatch):
    from app.figure_builder import process as fp
    from app.gui.browser_window import BrowserWindow

    started = []
    monkeypatch.setattr(fp.QProcess, "start", lambda self, program, args: started.append(args))
    browser = BrowserWindow()
    assert browser.figure_builder_action in browser.processing_menu.actions()
    browser.figure_builder_action.trigger()
    assert started and "--figure-builder" in started[0]
    browser.close()

