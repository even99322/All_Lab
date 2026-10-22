"""YIG 分頁的 3D 座標預覽（v3.4）：上基板表面＋波導（訊號線、CPW 上地）＋ YIG 球，滑鼠移到頂點就顯示小球與空間座標。

座標跟 HFSS 建模一樣（mm）：介電層底面 z = 0，上基板表面 z = Die 厚，走線從 Die 厚到 Die 厚 + Top 厚；
XY 是 DXF 的座標。點一下頂點：座標複製到剪貼簿，可以「把 YIG 球心放到這一點正上方」。
厚度很薄看不清楚時把「Z 放大」調大（只放大畫面，座標照實際）。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

import dxf_geom as G
import yig

HOVER_PX = 12


def scene_from(design, thick_die: float, thick_top: float, yig_params: Optional[Dict] = None) -> Dict:
    """→ {"faces": [(3D 多邊形, 顏色, 透明度)], "verts": N×3, "labels": [說明], "sphere": (中心, 半徑) | None,
         "bounds": (x0, y0, x1, y1)}"""
    z_sub, z_top = float(thick_die), float(thick_die) + float(thick_top)
    faces: List[Tuple[list, str, float]] = []
    verts: List[Tuple[float, float, float]] = []
    labels: List[str] = []
    # 上基板表面
    for s in design.boards():
        ring = s.ring()
        if len(ring) >= 3:
            faces.append(([(x, y, z_sub) for x, y in ring], "#7CB342", 0.30))
            for x, y in _corners(s):
                verts.append((x, y, z_sub))
                labels.append(f"基板表面（{s.layer}）")
    # 波導：訊號線（g）與 CPW 的上地（ub）
    gen = design.generated()
    conductors: List[Tuple[list, str, str]] = [(G.ring_points(v), "#E53935", "訊號線") for v in gen["g"]]
    conductors += [(s.ring(), "#E53935", "訊號線") for s in design.shapes if s.kind == "poly" and s.layer == "g"]
    if design.mode == "CPW":
        conductors += [(G.ring_points(v), "#FB8C00", "上地") for v in gen["ub"]]
        conductors += [(s.ring(), "#FB8C00", "上地") for s in design.shapes if s.kind == "poly" and s.layer == "ub"]
    gx: List[float] = []
    gy: List[float] = []
    for ring, color, name in conductors:
        if len(ring) < 3:
            continue
        faces += _prism(ring, z_sub, z_top, color)
        for x, y in _simplify(ring):
            verts += [(x, y, z_sub), (x, y, z_top)]
            labels += [f"{name} 底面", f"{name} 頂面"]
        if name == "訊號線":
            gx += [p[0] for p in ring]
            gy += [p[1] for p in ring]
    sphere = None
    if yig_params:
        bbox = [min(gx), min(gy), z_sub, max(gx), max(gy), z_top] if gx else None
        p = dict(yig_params, thick_die=float(thick_die), thick_top=float(thick_top))
        center, r = yig.sphere_center(p, bbox)
        sphere = (tuple(center), r)
        verts.append(tuple(center))
        labels.append("YIG 球心")
        verts.append((center[0], center[1], center[2] - r))
        labels.append("YIG 球底")
    bb = design.board_bounds() or design.bounds()
    return {"faces": faces, "verts": np.array(verts, dtype=float).reshape(-1, 3), "labels": labels,
            "sphere": sphere, "bounds": bb, "z": (z_sub, z_top)}


def _corners(s) -> List[Tuple[float, float]]:
    """板材的頂點（有圓弧 / Bézier 時用簡化過的邊界，不要每個細分點都算）。"""
    if not s.bezier and not any(s.bulges or []) and not any(s.fillets or []):
        return list(s.pts)
    return _simplify(s.ring())


def _simplify(ring, tol: float = 0.02) -> List[Tuple[float, float]]:
    """把細分的曲線點去掉一些（只留轉折明顯的點），滑鼠才不會到處都是頂點。"""
    try:
        from shapely.geometry import Polygon
        poly = Polygon(ring)
        if poly.is_valid and not poly.is_empty:
            return [(x, y) for x, y in list(poly.simplify(tol, preserve_topology=True).exterior.coords)[:-1]]
    except Exception:  # noqa: BLE001
        pass
    return list(ring)


def _prism(ring, z0: float, z1: float, color: str):
    out = [([(x, y, z1) for x, y in ring], color, 0.95), ([(x, y, z0) for x, y in ring], color, 0.6)]
    n = len(ring)
    for i in range(n):
        (ax, ay), (bx, by) = ring[i], ring[(i + 1) % n]
        out.append(([(ax, ay, z0), (bx, by, z0), (bx, by, z1), (ax, ay, z1)], color, 0.8))
    return out


# =====================================================================================================
def make_view(parent=None):
    """Qt 元件（延後 import：不開 YIG 分頁就不載入 3D 繪圖）。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (QApplication, QCheckBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton,
                                 QVBoxLayout, QWidget)
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    from mpl_toolkits.mplot3d import proj3d
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    class Yig3DView(QWidget):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self.scene: Optional[Dict] = None
            self.picked: Optional[Tuple[float, float, float]] = None
            self.on_set_yig = None                       # callback(x, y)
            v = QVBoxLayout(self)
            v.setContentsMargins(0, 0, 0, 0)
            row = QHBoxLayout()
            self.btn_refresh = QPushButton("↻ 重新整理")
            self.zscale = QDoubleSpinBox()
            self.zscale.setRange(1, 200)
            self.zscale.setValue(1)
            self.zscale.setPrefix("Z 放大 ×")
            self.zscale.setToolTip("板子很薄：把 Z 方向放大才看得清楚（只影響畫面、YIG 球會變成橢圓；座標照實際）")
            self.chk_focus = QCheckBox("只看 YIG 附近")
            self.chk_focus.setToolTip("畫面只顯示 YIG 球周圍 ±(3 × 直徑, 至少 2 mm)，方便看球跟走線的相對位置")
            self.chk_sphere = QCheckBox("顯示 YIG 球")
            self.chk_sphere.setChecked(True)
            self.btn_set = QPushButton("把 YIG 球心放到選的點正上方")
            self.btn_set.setEnabled(False)
            for wdg in (self.btn_refresh, self.zscale, self.chk_sphere, self.chk_focus, self.btn_set):
                row.addWidget(wdg)
            row.addStretch(1)
            v.addLayout(row)
            self.fig = Figure(figsize=(6, 4.2))
            self.canvas = FigureCanvasQTAgg(self.fig)
            self.canvas.setMinimumHeight(430)
            v.addWidget(self.canvas)
            self.info = QLabel("滑鼠移到頂點顯示座標；左鍵拖曳旋轉、右鍵拖曳縮放；點一下頂點複製座標。")
            self.info.setWordWrap(True)
            self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v.addWidget(self.info)
            self.tip = QLabel(self.canvas)                 # 游標旁的座標
            self.tip.setStyleSheet("background: rgba(20,24,30,220); color: #fff; padding: 3px 7px; border-radius: 6px;"
                                   "font-family: Consolas, monospace;")
            self.tip.hide()
            try:                                        # 照加入的順序畫（不然大片的地常常蓋住 YIG 球）
                self.ax = self.fig.add_subplot(111, projection="3d", computed_zorder=False)
            except (TypeError, AttributeError):
                self.ax = self.fig.add_subplot(111, projection="3d")
            self.fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
            self.marker = None
            self.pin = None
            self._press = None
            self._xy2d = None
            self.zscale.valueChanged.connect(lambda _v: self.draw())
            self.chk_sphere.toggled.connect(lambda _v: self.draw())
            self.chk_focus.toggled.connect(lambda _v: self.draw())
            self.btn_set.clicked.connect(self._set_yig)
            self.canvas.mpl_connect("motion_notify_event", self._motion)
            self.canvas.mpl_connect("button_press_event", self._pressed)
            self.canvas.mpl_connect("button_release_event", self._released)
            self.canvas.mpl_connect("draw_event", lambda _e: setattr(self, "_xy2d", None))

        # ---- 畫 ---------------------------------------------------------------------------------
        def set_scene(self, scene: Dict) -> None:
            self.scene = scene
            self.picked = None
            self.btn_set.setEnabled(False)
            self.draw()

        def draw(self) -> None:
            ax = self.ax
            elev, azim = (ax.elev, ax.azim) if self.scene and getattr(self, "_drawn", False) else (28, -60)
            ax.clear()
            self.marker = self.pin = None
            self._xy2d = None
            sc = self.scene
            if not sc:
                ax.set_axis_off()
                self.canvas.draw_idle()
                return
            k = self.zscale.value()
            box = self._focus_box()
            polys, colors = [], []
            for pts, color, alpha in sc["faces"]:
                for face in (_clip_face(pts, box) if box else [pts]):
                    polys.append([(x, y, z * k) for x, y, z in face])
                    colors.append(_rgba(color, alpha))
            if polys:
                pc = Poly3DCollection(polys, facecolors=colors, edgecolors=(0, 0, 0, 0.15), linewidths=0.3)
                pc.set_zorder(1)
                ax.add_collection3d(pc)
            zmax = sc["z"][1]
            if sc["sphere"] and self.chk_sphere.isChecked():
                (cx, cy, cz), r = sc["sphere"]
                u, w = np.mgrid[0:2 * np.pi:28j, 0:np.pi:16j]
                surf = ax.plot_surface(cx + r * np.cos(u) * np.sin(w), cy + r * np.sin(u) * np.sin(w),
                                       (cz + r * np.cos(w)) * k, color="#8E24AA", alpha=0.55, linewidth=0)
                surf.set_zorder(5)
                zmax = max(zmax, cz + r)
            x0, y0, x1, y1 = box or sc["bounds"]
            dx, dy = max(x1 - x0, 1e-3), max(y1 - y0, 1e-3)
            ax.set_xlim(x0, x1)
            ax.set_ylim(y0, y1)
            ax.set_zlim(0, max(zmax * k, 1e-3))
            ax.set_box_aspect((dx, dy, max(zmax * k, 0.04 * max(dx, dy))))   # 不放大（放大會被裁掉）
            ax.set_xlabel("X (mm)")
            ax.set_ylabel("Y (mm)")
            ax.set_zlabel("Z (mm)")
            zt = ax.get_zticks()
            ax.set_zticks(zt)
            ax.set_zticklabels([f"{t / k:.3g}" for t in zt])
            ax.view_init(elev=elev, azim=azim)
            self._drawn = True
            self.canvas.draw_idle()

        def _focus_box(self):
            """「只看 YIG 附近」：球心 ±(6 × 半徑, 至少 2 mm)。"""
            sc = self.scene
            if not (sc and self.chk_focus.isChecked() and sc["sphere"]):
                return None
            (cx, cy, _cz), r = sc["sphere"]
            half = max(6 * r, 2.0)
            return (cx - half, cy - half, cx + half, cy + half)

        # ---- 滑鼠 ---------------------------------------------------------------------------------
        def _project(self):
            if self._xy2d is None and self.scene is not None and len(self.scene["verts"]):
                v = self.scene["verts"]
                k = self.zscale.value()
                xs, ys, _ = proj3d.proj_transform(v[:, 0], v[:, 1], v[:, 2] * k, self.ax.get_proj())
                xy = self.ax.transData.transform(np.column_stack([xs, ys]))
                box = self._focus_box()
                if box:                                    # 畫面外的頂點不算
                    out = (v[:, 0] < box[0] - 1e-9) | (v[:, 0] > box[2] + 1e-9) | (v[:, 1] < box[1] - 1e-9) | \
                          (v[:, 1] > box[3] + 1e-9)
                    xy[out] = np.nan
                self._xy2d = xy
            return self._xy2d

        def nearest(self, x: float, y: float) -> Optional[int]:
            xy = self._project()
            if xy is None or not len(xy):
                return None
            d = np.hypot(xy[:, 0] - x, xy[:, 1] - y)
            if np.all(np.isnan(d)):
                return None
            i = int(np.nanargmin(d))
            return i if d[i] <= HOVER_PX else None

        def _motion(self, ev) -> None:
            if ev.inaxes is not self.ax or self.scene is None:
                self.tip.hide()
                return
            if ev.button is not None:                   # 正在旋轉：投影會變
                self._xy2d = None
                self.tip.hide()
                return
            i = self.nearest(ev.x, ev.y)
            if i is None:
                self.tip.hide()
                if self.marker is not None:
                    self.marker.set_visible(False)
                    self.canvas.draw_idle()
                return
            x, y, z = self.scene["verts"][i]
            k = self.zscale.value()
            if self.marker is None:
                self.marker, = self.ax.plot([x], [y], [z * k], "o", ms=11, color="#FFD600", mec="#212121", mew=1.2,
                                            zorder=10)
            else:
                self.marker.set_data_3d([x], [y], [z * k])
                self.marker.set_visible(True)
            self.canvas.draw_idle()
            self.tip.setText(f"{self.scene['labels'][i]}\nX {x:.4f}  Y {y:.4f}  Z {z:.4f} mm")
            self.tip.adjustSize()
            h = self.canvas.height()
            dpr = self.canvas.devicePixelRatioF() or 1.0
            px, py = ev.x / dpr, h - ev.y / dpr
            self.tip.move(int(min(px + 14, self.canvas.width() - self.tip.width() - 4)), int(max(py - self.tip.height() - 10, 2)))
            self.tip.show()
            self.tip.raise_()

        def _pressed(self, ev) -> None:
            self._press = (ev.x, ev.y, ev.button)

        def _released(self, ev) -> None:
            p, self._press = self._press, None
            self._xy2d = None
            if p is None or ev.inaxes is not self.ax or self.scene is None or p[2] != 1:
                return
            if math.hypot(ev.x - p[0], ev.y - p[1]) > 4:  # 拖曳 = 旋轉，不是點選
                return
            i = self.nearest(ev.x, ev.y)
            if i is None:
                return
            x, y, z = (float(c) for c in self.scene["verts"][i])
            self.picked = (x, y, z)
            text = f"{x:.4f}, {y:.4f}, {z:.4f}"
            QApplication.clipboard().setText(text)
            self.info.setText(f"已選 <b>{self.scene['labels'][i]}</b>：X {x:.4f}、Y {y:.4f}、Z {z:.4f} mm（已複製到剪貼簿）")
            self.btn_set.setEnabled(self.on_set_yig is not None)
            k = self.zscale.value()
            if self.pin is None:
                self.pin, = self.ax.plot([x], [y], [z * k], "o", ms=9, color="#00E676", mec="#1B5E20", mew=1.2, zorder=11)
            else:
                self.pin.set_data_3d([x], [y], [z * k])
            self.canvas.draw_idle()

        def _set_yig(self) -> None:
            if self.picked and self.on_set_yig:
                self.on_set_yig(self.picked[0], self.picked[1])

    return Yig3DView(parent)


def _clip_face(pts, box):
    """3D 面裁到 XY 方框裡（水平面用多邊形交集，垂直的側面用線段交集）。"""
    from shapely.geometry import LineString, Polygon, box as sbox
    b = sbox(*box)
    zs = {round(p[2], 9) for p in pts}
    if len(zs) == 1:
        z = pts[0][2]
        try:
            g = Polygon([(p[0], p[1]) for p in pts]).buffer(0).intersection(b)
        except Exception:  # noqa: BLE001
            return []
        geoms = [g] if g.geom_type == "Polygon" else [x for x in getattr(g, "geoms", []) if x.geom_type == "Polygon"]
        return [[(x, y, z) for x, y in list(q.exterior.coords)[:-1]] for q in geoms if not q.is_empty]
    if len(pts) == 4:                                    # 側面：(a, z0) (b, z0) (b, z1) (a, z1)
        (ax, ay, z0), (bx, by, _), _c, (_dx, _dy, z1) = pts
        seg = LineString([(ax, ay), (bx, by)]).intersection(b)
        if seg.is_empty or seg.geom_type != "LineString":
            return []
        (px, py), (qx, qy) = list(seg.coords)[0], list(seg.coords)[-1]
        return [[(px, py, z0), (qx, qy, z0), (qx, qy, z1), (px, py, z1)]]
    return [pts]


def _rgba(color: str, alpha: float):
    c = color.lstrip("#")
    return (int(c[0:2], 16) / 255, int(c[2:4], 16) / 255, int(c[4:6], 16) / 255, alpha)
