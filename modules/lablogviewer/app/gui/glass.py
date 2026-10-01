"""In-app liquid glass material for compact tool surfaces.

The optical model is a PySide6 port of the PyGlass slab (MIT, copyright 2026
neomosh8; see docs/third_party_pyglass.txt): a rounded-rectangle bevel refracts
the in-app backdrop through Snell's law with per-channel dispersion, reflects a
procedural environment through a Schlick-Fresnel rim, and frosts the
transmitted light. It is retuned for toolbar-sized capsules.

Buttons that sit together share one capsule. The glass is painted behind
ordinary Qt child widgets, never captures the desktop, owns no scientific
pixels, and falls back to a vector material if the optical path fails.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRegion,
)
from PySide6.QtWidgets import QApplication, QSlider, QStyle, QToolBar, QWidget

from app.palette import (
    GLASS_KNOB, GLASS_KNOB_OUTLINE, GLASS_KNOB_SHADOW_RGB, GLASS_MATERIAL, GLASS_SHADOW_RGB,
    GLASS_SPECULAR_RGB,
)
from app.theme import current_theme_colors, get_theme_manager


_OPTICAL_ENABLED = os.environ.get("LABLOGVIEWER_DISABLE_GLASS", "") != "1"
DEFAULT_THICKNESS = 0.5
DEFAULT_FROST = 0.25
_CAPSULE_GAP = 10          # logical px; wider gaps between controls start a new capsule
_CAPSULE_PAD_X = 6
_DRAG_INTERVAL_MS = 60     # throttle while a dial is being dragged
_SETTLE_MS = 120


def optical_glass_available() -> bool:
    return _OPTICAL_ENABLED


def _pixels(image: QImage) -> np.ndarray:
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    h, w = image.height(), image.width()
    rows = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(h, image.bytesPerLine())
    return rows[:, :w * 4].reshape(h, w, 4).copy()


def _image(pixels: np.ndarray, dpr: float) -> QImage:
    pixels = np.ascontiguousarray(pixels, dtype=np.uint8)
    h, w = pixels.shape[:2]
    result = QImage(pixels.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
    result.setDevicePixelRatio(dpr)
    return result


# ------------------------------------------------------------------ optics
def _rounded_rect_sdf(xs, ys, cx, cy, hx, hy, r):
    qx = np.abs(xs - cx) - (hx - r)
    qy = np.abs(ys - cy) - (hy - r)
    outside = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0))
    inside = np.minimum(np.maximum(qx, qy), 0.0)
    return outside + inside - r


def _normalize3(vec):
    v = np.asarray(vec, np.float32)
    return v / np.linalg.norm(v)


def _hue_to_rgb(h):
    h6 = (h % 1.0) * 6.0
    i = np.floor(h6).astype(np.int32) % 6
    f = (h6 - np.floor(h6)).astype(np.float32)
    q = 1.0 - f
    conds = [i == 0, i == 1, i == 2, i == 3, i == 4, i == 5]
    rgb = np.empty(h.shape + (3,), np.float32)
    rgb[..., 0] = np.select(conds, [1.0, q, 0.0, 0.0, f, 1.0])
    rgb[..., 1] = np.select(conds, [f, 1.0, 1.0, q, 0.0, 0.0])
    rgb[..., 2] = np.select(conds, [0.0, 0.0, f, 1.0, 1.0, q])
    return rgb


def _box_radius_for_sigma(sigma: float, passes: int = 3) -> int:
    if sigma <= 0.0:
        return 0
    var = sigma * sigma * 3.0 / passes
    return max(0, int(round((-1.0 + np.sqrt(1.0 + 4.0 * var)) / 2.0)))


def _box_blur(img: np.ndarray, radius: int, passes: int = 3) -> np.ndarray:
    """Separable cumulative-sum box blur (three passes approximate a Gaussian)."""
    if radius < 1:
        return img
    k = 2 * radius + 1
    inv = np.float32(1.0 / k)
    for _ in range(passes):
        ap = np.pad(img, ((0, 0), (radius + 1, radius)), mode="edge")
        cs = np.cumsum(ap, axis=1, dtype=np.float32)
        img = (cs[:, k:] - cs[:, :-k]) * inv
        ap = np.pad(img, ((radius + 1, radius), (0, 0)), mode="edge")
        cs = np.cumsum(ap, axis=0, dtype=np.float32)
        img = (cs[k:, :] - cs[:-k, :]) * inv
    return img


def _environment(rx, ry, rz):
    """Sky gradient plus a warm top key light and a cool lower-left fill."""
    key_dir = _normalize3((0.0, -0.85, 0.52))
    fill_dir = _normalize3((-0.62, 0.55, 0.56))
    dot_key = np.clip(rx * key_dir[0] + ry * key_dir[1] + rz * key_dir[2], 0, 1)
    dot_fill = np.clip(rx * fill_dir[0] + ry * fill_dir[1] + rz * fill_dir[2], 0, 1)
    spec_key = dot_key ** 55
    spec_fill = dot_fill ** 22
    horizon = np.clip(1.0 - np.clip(rz, 0, 1), 0, 1)
    refl = np.empty(rx.shape + (3,), np.float32)
    refl[..., 0] = horizon * 120 + spec_key * 255 + spec_fill * 150
    refl[..., 1] = horizon * 140 + spec_key * 252 + spec_fill * 195
    refl[..., 2] = horizon * 175 + spec_key * 245 + spec_fill * 255
    return refl


class GlassKernel:
    """Geometry-only glass response for one capsule size; apply() is cheap."""

    def __init__(self, panel_w: int, panel_h: int, pad: int, radius: float, *,
                 bevel: float, strength: float, ior_edge: float, ior_inner: float,
                 chroma: float, reflect: float, f0: float, disp_glow: float,
                 disp_sat: float = 0.55, disp_width: float = 1.5):
        self.w, self.h, self.pad = panel_w, panel_h, pad
        self.pw_pad, self.ph_pad = panel_w + 2 * pad, panel_h + 2 * pad
        ys, xs = np.mgrid[0:panel_h, 0:panel_w].astype(np.float32)
        px, py = xs + pad, ys + pad
        sdf = _rounded_rect_sdf(px, py, pad + panel_w / 2.0, pad + panel_h / 2.0,
                                panel_w / 2.0, panel_h / 2.0, radius)
        d = -sdf
        inside = d > 0
        gy, gx = np.gradient(sdf)
        gnorm = np.hypot(gx, gy) + 1e-6
        nx, ny = gx / gnorm, gy / gnorm

        # Quarter-circle roundover: slope grows toward the rim.
        t = np.clip(d / bevel, 0.0, 1.0)
        xx = np.where(inside, np.clip(1.0 - t, 0.0, 0.985), 0.0)
        slope = xx / np.sqrt(1.0 - xx * xx)
        cos_t = 1.0 / np.sqrt(1.0 + slope * slope)
        sin_t = slope * cos_t

        n_base = ior_edge + (ior_inner - ior_edge) * t
        max_disp = 0.92 * pad
        self._idx = []
        for chroma_factor in (-1.0, 0.0, 1.0):          # R bends least, B most
            eta = 1.0 / (n_base * (1.0 + chroma * chroma_factor))
            cos_r = np.sqrt(np.clip(1.0 - eta * eta * sin_t * sin_t, 0.0, 1.0))
            coeff = eta * cos_t - cos_r
            scale = strength / np.maximum(eta - coeff * cos_t, 1e-3)
            dx = np.clip(coeff * nx * sin_t * scale, -max_disp, max_disp)
            dy = np.clip(coeff * ny * sin_t * scale, -max_disp, max_disp)
            self._idx.append(self._precompute_sample(px + dx, py + dy))

        fres = f0 + (1.0 - f0) * (1.0 - np.clip(cos_t, 0.0, 1.0)) ** 5
        fres = (np.where(inside, fres, 0.0) * reflect)[..., None].astype(np.float32)
        refl = _environment(2.0 * cos_t * nx * sin_t, 2.0 * cos_t * ny * sin_t, 2.0 * cos_t * cos_t - 1.0)
        self._one_minus_f = 1.0 - fres
        self._refl_term = (refl * fres).astype(np.float32)

        hue = np.arctan2(ny, nx) / (2.0 * np.pi) + 0.5
        light = _hue_to_rgb(hue) * disp_sat + (1.0 - disp_sat)
        line = np.where(inside, np.clip(1.0 - d / disp_width, 0.0, 1.0), 0.0)
        self._disp_glow = (light * (line * disp_glow)[..., None]).astype(np.float32)
        # 1 at the rim, 0 in the flat interior: where a light-theme lens darkens.
        self._rim_weight = (np.where(inside, 1.0 - t, 0.0) ** 1.5)[..., None].astype(np.float32)

    def _precompute_sample(self, sx, sy):
        x0 = np.floor(sx).astype(np.int32)
        y0 = np.floor(sy).astype(np.int32)
        fx = (sx - x0).astype(np.float32)
        fy = (sy - y0).astype(np.float32)
        return (
            np.clip(x0, 0, self.pw_pad - 1), np.clip(x0 + 1, 0, self.pw_pad - 1),
            np.clip(y0, 0, self.ph_pad - 1), np.clip(y0 + 1, 0, self.ph_pad - 1),
            fx, 1.0 - fx, fy, 1.0 - fy,
        )

    def apply(self, padded: np.ndarray, *, blur_radius: int = 0, haze: float = 0.0,
              glow_scale: float = 1.0, edge_shade: float = 0.0) -> np.ndarray:
        """Refract and reflect a padded RGBA backdrop slice into an RGBA capsule."""
        out = np.empty((self.h, self.w, 4), np.float32)
        for c, (x0, x1, y0, y1, fx, omfx, fy, omfy) in enumerate(self._idx):
            pc = padded[..., c]
            top = pc[y0, x0] * omfx + pc[y0, x1] * fx
            bottom = pc[y1, x0] * omfx + pc[y1, x1] * fx
            out[..., c] = top * omfy + bottom * fy
        trans = out[..., :3]
        if blur_radius:
            for c in range(3):
                trans[..., c] = _box_blur(trans[..., c], blur_radius)
        if haze:
            milky = 0.5 * trans.mean(axis=2, keepdims=True) + 127.5
            trans *= (1.0 - haze)
            trans += milky * haze
        if edge_shade:
            # On a near-uniform light backdrop refraction alone is invisible;
            # a darker bevel band gives the slab its lens-like thickness.
            trans *= 1.0 - edge_shade * self._rim_weight
        out[..., :3] = trans * self._one_minus_f + self._refl_term + self._disp_glow * glow_scale
        np.clip(out[..., :3], 0.0, 255.0, out[..., :3])
        out[..., 3] = 255.0
        return out.astype(np.uint8)


def _anchored(t: float, lo: float, hi: float) -> float:
    """Dial multiplier: lo at 0, exactly 1 at 0.5, hi at 1."""
    t = min(max(t, 0.0), 1.0)
    if t >= 0.5:
        return 1.0 + (t - 0.5) * 2.0 * (hi - 1.0)
    return 1.0 + (t - 0.5) * 2.0 * (1.0 - lo)


@dataclass(frozen=True)
class GlassMaterial:
    """PyGlass's two perceptual dials, rescaled for toolbar capsules.

    ``thickness`` drives displacement, bevel width, IOR span and dispersion;
    ``frost`` drives transmission blur, milky haze and a softer spectral rim.
    Frost is applied per refresh, so moving it never rebuilds a kernel.
    """

    thickness: float = DEFAULT_THICKNESS
    frost: float = DEFAULT_FROST
    strength: float = 12.0
    bevel: float = 14.0
    pad: float = 26.0
    ior_edge: float = 5.0
    ior_inner: float = 1.5
    chroma: float = 0.11
    disp_glow: float = 110.0
    disp_width: float = 1.5
    reflect: float = 1.0
    f0: float = 0.035
    max_frost_sigma: float = 5.0

    def pad_px(self, dpr: float) -> int:
        t = min(max(self.thickness, 0.0), 1.0)
        return int(self.pad * (1.0 if t <= 0.5 else 1.0 + 1.2 * (t - 0.5)) * dpr)

    def kernel_key(self) -> tuple:
        return (round(self.thickness, 3),)

    def build_kernel(self, panel_w: int, panel_h: int, radius: float, dpr: float) -> GlassKernel:
        t = self.thickness
        ior_inner = self.ior_inner * _anchored(t, 0.83, 1.13)
        # The bevel can never exceed the capsule's half height.
        bevel = min(self.bevel * _anchored(t, 0.6, 1.5) * dpr, 0.48 * min(panel_w, panel_h))
        return GlassKernel(
            panel_w, panel_h, self.pad_px(dpr), radius,
            bevel=max(bevel, 1.0),
            strength=self.strength * _anchored(t, 0.5, 1.6) * dpr,
            ior_edge=ior_inner + (self.ior_edge - self.ior_inner) * _anchored(t, 0.45, 1.55),
            ior_inner=ior_inner,
            chroma=self.chroma * _anchored(t, 0.5, 1.6),
            reflect=self.reflect, f0=self.f0, disp_glow=self.disp_glow,
            disp_width=self.disp_width * _anchored(t, 0.6, 1.5) * dpr,
        )

    def scatter(self, dpr: float) -> tuple[int, float, float]:
        f = min(max(self.frost, 0.0), 1.0)
        sigma = (f ** 1.5) * self.max_frost_sigma * (0.85 + 0.3 * self.thickness) * dpr
        return _box_radius_for_sigma(sigma), f * 0.18, 1.0 - 0.4 * f


class GlassRenderer:
    """Cache kernels per capsule size and refract backdrop slices."""

    def __init__(self):
        self._kernels: dict[tuple, GlassKernel] = {}

    def refract(self, backdrop: np.ndarray, rect: QRect, material: GlassMaterial,
                dpr: float, *, edge_shade: float = 0.0) -> QImage:
        """Refract ``rect`` (device px inside ``backdrop``'s padded frame)."""
        w, h = rect.width(), rect.height()
        if w < 4 or h < 4:
            raise ValueError("Glass capsule is too small")
        pad = material.pad_px(dpr)
        key = (w, h, pad, round(dpr, 3)) + material.kernel_key()
        kernel = self._kernels.get(key)
        if kernel is None:
            if len(self._kernels) > 24:
                self._kernels.clear()
            kernel = self._kernels[key] = material.build_kernel(w, h, h / 2.0, dpr)
        gx, gy = rect.x() - pad, rect.y() - pad
        gw, gh = w + 2 * pad, h + 2 * pad
        bh, bw = backdrop.shape[:2]
        if 0 <= gx <= bw - gw and 0 <= gy <= bh - gh:
            padded = backdrop[gy:gy + gh, gx:gx + gw]
        else:
            xs = np.clip(np.arange(gx, gx + gw), 0, bw - 1)
            ys = np.clip(np.arange(gy, gy + gh), 0, bh - 1)
            padded = backdrop[np.ix_(ys, xs)]
        blur, haze, glow = material.scatter(dpr)
        return _image(kernel.apply(padded, blur_radius=blur, haze=haze, glow_scale=glow,
                                   edge_shade=edge_shade), dpr)


# ------------------------------------------------------------------ compositing
@dataclass(frozen=True)
class GlassStyle:
    shadow_layers: int
    shadow_alpha: int
    tint_top: QColor
    tint_bottom: QColor
    fallback: QColor
    sheen_alpha: int
    rim_top: int
    rim_bottom: int
    hairline: QColor
    shadow_offset: float = 1.6
    inner_shadow: int = 0        # alpha of the cool shade along the lower inner edge
    edge_shade: float = 0.0      # optical bevel darkening (light theme)
    inner_shadow_rgb: tuple = (30, 55, 80)


def _glass_style(values: dict) -> GlassStyle:
    return GlassStyle(
        shadow_layers=values["shadow_layers"], shadow_alpha=values["shadow_alpha"],
        tint_top=QColor(*values["tint_top"]), tint_bottom=QColor(*values["tint_bottom"]),
        fallback=QColor(*values["fallback"]), sheen_alpha=values["sheen_alpha"],
        rim_top=values["rim_top"], rim_bottom=values["rim_bottom"], hairline=QColor(*values["hairline"]),
        shadow_offset=values["shadow_offset"], inner_shadow=values["inner_shadow"],
        edge_shade=values["edge_shade"], inner_shadow_rgb=tuple(values["inner_shadow_rgb"]),
    )


# Light theme: the beige window is nearly the glass color, so the slab is
# separated by a deeper soft shadow, a cool tint, a darker bevel band and a
# lower inner shade, while a bright specular rim keeps it reading as glass.
# Values: app/palette.py GLASS_MATERIAL.
LIGHT_STYLE = _glass_style(GLASS_MATERIAL["light"])
DARK_STYLE = _glass_style(GLASS_MATERIAL["dark"])


def paint_glass(painter: QPainter, rect: QRectF, refracted: QImage | None, style: GlassStyle) -> None:
    """Composite shadow, refracted body, tint, sheen and Fresnel rim."""
    radius = rect.height() / 2.0
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setPen(Qt.PenStyle.NoPen)
    for layer in range(style.shadow_layers, 0, -1):
        spread = layer * 0.9
        painter.setBrush(QColor(*GLASS_SHADOW_RGB, style.shadow_alpha))
        painter.drawRoundedRect(rect.adjusted(-spread, -spread + 1.0, spread, spread + style.shadow_offset),
                                radius + spread, radius + spread)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.setClipPath(path)
    if refracted is not None:
        painter.drawImage(rect, refracted)
    else:
        painter.fillRect(rect, style.fallback)
    tint = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    tint.setColorAt(0.0, style.tint_top)
    tint.setColorAt(1.0, style.tint_bottom)
    painter.fillRect(rect, QBrush(tint))
    sheen = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + rect.height() * 0.5))
    sheen.setColorAt(0.0, QColor(*GLASS_SPECULAR_RGB, style.sheen_alpha))
    sheen.setColorAt(1.0, QColor(*GLASS_SPECULAR_RGB, 0))
    painter.fillRect(rect, QBrush(sheen))
    if style.inner_shadow:
        inner = QLinearGradient(QPointF(rect.left(), rect.top() + rect.height() * 0.55), rect.bottomLeft())
        inner.setColorAt(0.0, QColor(*style.inner_shadow_rgb, 0))
        inner.setColorAt(1.0, QColor(*style.inner_shadow_rgb, style.inner_shadow))
        painter.fillRect(rect, QBrush(inner))
    painter.setClipping(False)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(style.hairline, 1.0))
    painter.drawRoundedRect(rect.adjusted(-0.5, -0.5, 0.5, 0.5), radius + 0.5, radius + 0.5)
    rim = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    rim.setColorAt(0.0, QColor(*GLASS_SPECULAR_RGB, style.rim_top))
    rim.setColorAt(0.5, QColor(*GLASS_SPECULAR_RGB, style.rim_top // 5))
    rim.setColorAt(1.0, QColor(*GLASS_SPECULAR_RGB, style.rim_bottom))
    painter.setPen(QPen(QBrush(rim), 1.2))
    painter.drawRoundedRect(rect.adjusted(0.6, 0.6, -0.6, -0.6), radius - 0.6, radius - 0.6)
    painter.restore()


def current_glass_material() -> GlassMaterial:
    manager = get_theme_manager()
    if manager is None:
        return GlassMaterial()
    thickness, frost = manager.glass_material
    return GlassMaterial(thickness=thickness, frost=frost)


# ------------------------------------------------------------------ hosts
class _GlassPaint:
    """Shared capsule lifecycle for a toolbar or compact layout host."""

    def _init_glass(self) -> None:
        self._glass_renderer = GlassRenderer()
        self._glass_images: dict[tuple[int, int, int, int], QImage] = {}
        self._glass_rects: tuple[QRect, ...] = ()
        self._glass_dark = QColor(current_theme_colors().window).lightness() < 128
        self._glass_enabled = _OPTICAL_ENABLED
        self._glass_manager = None
        self._glass_timer = QTimer(self)
        self._glass_timer.setSingleShot(True)
        self._glass_timer.timeout.connect(self._refresh_glass)
        self.setAutoFillBackground(False)
        self._connect_glass_manager()

    @property
    def _glass_image(self) -> QImage | None:
        """First capsule image; kept for diagnostics and older callers."""
        return next(iter(self._glass_images.values()), None)

    def _connect_glass_manager(self) -> None:
        manager = get_theme_manager()
        if manager is self._glass_manager or manager is None:
            return
        if self._glass_manager is not None:
            try:
                self._glass_manager.glass_material_changed.disconnect(self._on_glass_material_changed)
            except (RuntimeError, TypeError):
                pass
        manager.glass_material_changed.connect(self._on_glass_material_changed)
        self._glass_manager = manager

    def _on_glass_material_changed(self, _thickness: float, _frost: float, final: bool) -> None:
        # While a dial is dragged, refresh at a throttled rate; settle on release.
        self.invalidate_glass(keep_images=True, delay=_SETTLE_MS if final else _DRAG_INTERVAL_MS,
                              restart=final)

    def set_optical_glass_enabled(self, enabled: bool) -> None:
        self._glass_enabled = bool(enabled) and _OPTICAL_ENABLED
        self.invalidate_glass()

    def apply_lablog_theme(self, colors) -> None:
        self._glass_dark = QColor(colors.window).lightness() < 128
        self.invalidate_glass(keep_images=False, delay=0)

    def invalidate_glass(self, *, keep_images: bool = False, delay: int = _SETTLE_MS,
                         restart: bool = True) -> None:
        if not keep_images:
            self._glass_images = {}
        self.update()
        if self.isVisible() and self._glass_enabled and (restart or not self._glass_timer.isActive()):
            self._glass_timer.start(delay)

    # -- geometry ------------------------------------------------------
    def _glass_groups(self) -> list[list[QWidget]]:
        raise NotImplementedError

    def capsule_rects(self) -> tuple[QRect, ...]:
        """Capsules around clustered controls, in this widget's coordinates."""
        bounds = self.rect().adjusted(1, 2, -1, -2)
        rects = []
        for group in self._glass_groups():
            clusters: list[QRect] = []
            for widget in sorted(group, key=lambda w: w.mapTo(self, QPoint(0, 0)).x()):
                geometry = QRect(widget.mapTo(self, QPoint(0, 0)), widget.size())
                if clusters and geometry.left() - clusters[-1].right() <= _CAPSULE_GAP:
                    clusters[-1] = clusters[-1].united(geometry)
                else:
                    clusters.append(geometry)
            for cluster in clusters:
                rect = QRect(cluster.left() - _CAPSULE_PAD_X, bounds.top(),
                             cluster.width() + 2 * _CAPSULE_PAD_X, bounds.height())
                rects.append(rect.intersected(bounds))
        rects.sort(key=QRect.left)
        # Narrow separators leave less room than the capsule padding; split
        # neighbours at the midpoint so capsules never overlap.
        for left, right in zip(rects, rects[1:]):
            if left.right() + 4 > right.left():
                middle = (left.right() + right.left()) // 2
                left.setRight(middle - 2)
                right.setLeft(middle + 2)
        return tuple(rect for rect in rects if rect.width() >= rect.height() and rect.height() >= 8)

    # -- optics --------------------------------------------------------
    def _capture_backdrop(self, dpr: float, pad: int) -> tuple[np.ndarray, QPoint]:
        """Parent background plus siblings around this host; never the host itself."""
        parent = self.parentWidget()
        pad_logical = int(np.ceil(pad / dpr))
        region = self.geometry().adjusted(-pad_logical, -pad_logical, pad_logical, pad_logical)
        backdrop = QImage(max(1, round(region.width() * dpr)), max(1, round(region.height() * dpr)),
                          QImage.Format.Format_RGBA8888)
        backdrop.setDevicePixelRatio(dpr)
        backdrop.fill(parent.palette().color(parent.backgroundRole()))
        visible = region.intersected(parent.rect())
        parent.render(backdrop, visible.topLeft() - region.topLeft(), QRegion(visible),
                      QWidget.RenderFlag.DrawWindowBackground)
        for sibling in parent.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            if sibling is self or sibling.isWindow() or not sibling.isVisible():
                continue
            overlap = sibling.geometry().intersected(visible)
            if overlap.isEmpty():
                continue
            sibling.render(
                backdrop, overlap.topLeft() - region.topLeft(),
                QRegion(overlap.translated(-sibling.pos())),
                QWidget.RenderFlag.DrawWindowBackground | QWidget.RenderFlag.DrawChildren,
            )
        origin = self.pos() - region.topLeft()
        return _pixels(backdrop), QPoint(round(origin.x() * dpr), round(origin.y() * dpr))

    def _refresh_glass(self) -> None:
        self._connect_glass_manager()
        if not self._glass_enabled or not self.isVisible() or not self.parentWidget():
            self._glass_images = {}
            self.update()
            return
        try:
            material = current_glass_material()
            dpr = self.devicePixelRatioF()
            pad = material.pad_px(dpr)
            backdrop, origin = self._capture_backdrop(dpr, pad)
            style = DARK_STYLE if self._glass_dark else LIGHT_STYLE
            images = {}
            for rect in self.capsule_rects():
                device = QRect(origin.x() + round(rect.x() * dpr), origin.y() + round(rect.y() * dpr),
                               round(rect.width() * dpr), round(rect.height() * dpr))
                images[rect.getRect()] = self._glass_renderer.refract(
                    backdrop, device, material, dpr, edge_shade=style.edge_shade,
                )
            self._glass_images = images
        except Exception:
            # Decoration must never prevent the underlying toolbar from working.
            self._glass_images = {}
        self.update()

    def _paint_glass(self) -> None:
        rects = self.capsule_rects()
        if rects != self._glass_rects:
            # Controls moved (splitter drag, relayout): repaint now with the
            # nearest cached material and refract the new positions once idle.
            self._glass_rects = rects
            if self._glass_enabled and self.isVisible():
                self._glass_timer.start(_SETTLE_MS)
        style = DARK_STYLE if self._glass_dark else LIGHT_STYLE
        by_size = {(key[2], key[3]): image for key, image in self._glass_images.items()}
        painter = QPainter(self)
        for rect in rects:
            image = self._glass_images.get(rect.getRect()) or by_size.get((rect.width(), rect.height()))
            paint_glass(painter, QRectF(rect), image, style)
        painter.end()


class GlassToolSurface(_GlassPaint, QWidget):
    """A layout host whose child controls stay sharp and interactive."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._init_glass()

    def _glass_groups(self) -> list[list[QWidget]]:
        groups = []
        layout = self.layout()
        if layout is None:
            return groups
        for index in range(layout.count()):
            host = layout.itemAt(index).widget()
            if host is None or not host.isVisible():
                continue
            inner = host.layout()
            if inner is None:
                groups.append([host])
                continue
            members = [inner.itemAt(i).widget() for i in range(inner.count())]
            groups.append([widget for widget in members if widget is not None and widget.isVisible()])
        return groups

    def paintEvent(self, event) -> None:
        self._paint_glass()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.invalidate_glass(keep_images=True, delay=0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.invalidate_glass(keep_images=True)


class GlassToolBar(_GlassPaint, QToolBar):
    """Existing QAction and QToolButton semantics with glass capsules.

    Separators split capsules, so related actions read as one piece of glass.
    """

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(title, parent)
        self._init_glass()

    def _glass_groups(self) -> list[list[QWidget]]:
        groups: list[list[QWidget]] = [[]]
        for action in self.actions():
            if action.isSeparator():
                groups.append([])
                continue
            widget = self.widgetForAction(action)
            if widget is not None and widget.property("glassSpacer"):
                groups.append([])          # a stretch spacer separates capsules and is never glass
                continue
            if widget is not None and widget.isVisible():
                groups[-1].append(widget)
        return [group for group in groups if group]

    def paintEvent(self, event) -> None:
        self._paint_glass()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.invalidate_glass(keep_images=True, delay=0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.invalidate_glass(keep_images=True)


# ------------------------------------------------------------------ dial
class GlassSlider(QSlider):
    """Compact 0-100 dial: accent fill, white knob that grows while dragged."""

    released = Signal(int)

    KNOB = 16
    KNOB_ACTIVE = 19

    def __init__(self, parent: QWidget | None = None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setRange(0, 100)
        self.setPageStep(10)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.sliderReleased.connect(lambda: self.released.emit(self.value()))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API spelling
        return QSize(76, 24)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(48, 24)

    def _span(self) -> tuple[float, float]:
        margin = self.KNOB_ACTIVE / 2.0 + 1
        return margin, max(1.0, self.width() - 2 * margin)

    def _value_at(self, x: float) -> int:
        left, span = self._span()
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(),
                                              round(min(max(x - left, 0), span)), round(span))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self.setSliderDown(True)
        self.setValue(self._value_at(event.position().x()))
        self.update()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self.isSliderDown():
            self.setValue(self._value_at(event.position().x()))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self.isSliderDown() and event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(False)
            self.update()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        super().keyReleaseEvent(event)
        if not event.isAutoRepeat() and event.key() in (
            Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
            Qt.Key.Key_PageUp, Qt.Key.Key_PageDown, Qt.Key.Key_Home, Qt.Key.Key_End,
        ):
            self.released.emit(self.value())

    def wheelEvent(self, event) -> None:  # noqa: N802
        super().wheelEvent(event)
        self.released.emit(self.value())

    def paintEvent(self, _event) -> None:  # noqa: N802
        colors = current_theme_colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        left, span = self._span()
        cy = self.height() / 2.0
        fraction = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
        knob_x = left + fraction * span
        enabled = self.isEnabled()
        track = QColor(colors.border)
        accent = QColor(colors.accent if enabled else colors.secondary)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(left, cy - 2, span, 4), 2, 2)
        painter.setBrush(accent)
        painter.drawRoundedRect(QRectF(left, cy - 2, max(4.0, knob_x - left), 4), 2, 2)
        diameter = self.KNOB_ACTIVE if self.isSliderDown() else self.KNOB
        knob = QRectF(knob_x - diameter / 2.0, cy - diameter / 2.0, diameter, diameter)
        for spread, alpha in ((2.5, 18), (1.2, 34)):
            painter.setBrush(QColor(*GLASS_KNOB_SHADOW_RGB, alpha))
            painter.drawEllipse(knob.adjusted(-spread, -spread + 1.2, spread, spread + 1.2))
        painter.setBrush(QColor(GLASS_KNOB) if enabled else QColor(colors.control))
        painter.setPen(QPen(QColor(*GLASS_KNOB_OUTLINE), 0.8))
        painter.drawEllipse(knob)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(accent, 1.4))
            painter.drawEllipse(knob.adjusted(-2.5, -2.5, 2.5, 2.5))
        painter.end()
