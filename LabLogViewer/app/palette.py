"""LabLogViewer color palette — the one place that defines every color.

Change colors here; modules import these names instead of writing color
literals. Values are plain hex strings or ``(r, g, b, a)`` tuples (alpha
0-255) so this module has no Qt dependency and can later be overridden by a
user color scheme (e.g. loaded from settings) before the UI is built.

Sections
--------
1. Application theme (Light / Dark)          APP_LIGHT, APP_DARK
2. Scientific plot background (White / Dark) PLOT_WHITE, PLOT_DARK
3. Liquid-glass buttons and capsules         BUTTON_GLASS, CAPSULE_HOVER, GLASS_MATERIAL
4. Small UI glyphs (tree / spin arrows)      ARROW
5. Status text                               STATUS
6. Stars                                     STAR_FILL, STAR_EDGE
7. Annotation (pen / laser / clear)          ANNOTATION
8. Viewer Mark tools                         MARK
9. Viewer panes and traces                   PANE, TRACE_DEFAULT
10. 3D view (interactive)                    SURFACE_3D
11. 3D publication export                    PUBLICATION
12. Analysis markers (YIG, Node/Antinode)    ANALYSIS
"""

from __future__ import annotations

from dataclasses import dataclass


RGBA = tuple[int, int, int, int]


# 1. Application theme ------------------------------------------------------
@dataclass(frozen=True)
class ThemeColors:
    window: str        # window background
    panel: str         # panels, tab pages
    alternate: str     # alternating rows, hover
    text: str          # primary text and icon lines
    secondary: str     # secondary / disabled text
    border: str        # borders, dividers
    accent: str        # focus rings, checked outlines, selected tab underline
    selected: str      # selected rows, pressed surfaces
    plot_background: str
    plot_panel: str
    control: str       # button / combo backgrounds
    input: str         # text inputs


APP_LIGHT = ThemeColors(
    window="#F7F4EE", panel="#FFFFFF", alternate="#F1F3F5", text="#1D1D1F",
    secondary="#6B7075", border="#BDCDE1", accent="#297FA8", selected="#D7E9FA",
    plot_background="#FFFFFF", plot_panel="#FFFFFF", control="#F8FAFC", input="#FFFFFF",
)
APP_DARK = ThemeColors(
    window="#0B0F14", panel="#141A21", alternate="#1B2630", text="#E6E6E6",
    secondary="#A5B0BA", border="#35424E", accent="#21C6E8", selected="#2D6E77",
    plot_background="#0B0F14", plot_panel="#141A21", control="#202B35", input="#10171E",
)


# 2. Scientific plot background ---------------------------------------------
@dataclass(frozen=True)
class ScientificPlotColors:
    background: str
    panel: str
    text: str
    secondary: str
    border: str

    @property
    def plot_background(self) -> str:
        return self.background

    @property
    def plot_panel(self) -> str:
        return self.panel


PLOT_WHITE = ScientificPlotColors(
    background="#FFFFFF", panel="#FFFFFF", text="#1D1D1F", secondary="#606A73", border="#C6CDD4",
)
PLOT_DARK = ScientificPlotColors(
    background="#0B0F14", panel="#141A21", text="#E6E6E6", secondary="#9AA4AE", border="#35424E",
)


# 3. Liquid glass ------------------------------------------------------------
# Push / tool buttons (stylesheet): (hex, alpha 0..1) stops of the glass body.
BUTTON_GLASS = {
    "light": {
        "body": (("#FFFFFF", 0.98), ("#FFFFFF", 0.80), ("#EEF3F7", 0.78), ("#E4EBF1", 0.92)),
        "edge": ("#0F1720", 0.16), "rim": ("#FFFFFF", 1.0), "low_edge": ("#0F1720", 0.26),
        "hover": (("#FFFFFF", 1.0), ("#E9F2F8", 0.95)),
        "pressed_accent_alpha": (0.20, 0.08),
    },
    "dark": {
        "body": (("#FFFFFF", 0.16), ("#FFFFFF", 0.09), ("#FFFFFF", 0.05), ("#FFFFFF", 0.08)),
        "edge": ("#FFFFFF", 0.14), "rim": ("#FFFFFF", 0.30), "low_edge": ("#000000", 0.45),
        "hover": (("#FFFFFF", 0.22), ("#FFFFFF", 0.10)),
        "pressed_accent_alpha": (0.16, 0.30),
    },
}
# Round hover lens on buttons inside toolbar glass capsules.
CAPSULE_HOVER = {
    "light": {"fill": ("#FFFFFF", 0.62), "edge": ("#FFFFFF", 0.95)},
    "dark": {"fill": ("#FFFFFF", 0.13), "edge": ("#FFFFFF", 0.22)},
}
# Toolbar glass capsules (optical material and vector fallback).
GLASS_MATERIAL = {
    "light": {
        "shadow_layers": 6, "shadow_alpha": 8, "shadow_offset": 2.4,
        "tint_top": (255, 255, 255, 150), "tint_bottom": (222, 233, 242, 105),
        "fallback": (246, 250, 253, 225), "sheen_alpha": 110,
        "rim_top": 255, "rim_bottom": 210, "hairline": (15, 25, 35, 52),
        "inner_shadow": 34, "inner_shadow_rgb": (30, 55, 80), "edge_shade": 0.16,
    },
    "dark": {
        "shadow_layers": 4, "shadow_alpha": 22, "shadow_offset": 1.6,
        "tint_top": (255, 255, 255, 22), "tint_bottom": (255, 255, 255, 6),
        "fallback": (32, 43, 53, 215), "sheen_alpha": 26,
        "rim_top": 120, "rim_bottom": 55, "hairline": (0, 0, 0, 90),
        "inner_shadow": 0, "inner_shadow_rgb": (30, 55, 80), "edge_shade": 0.0,
    },
}
GLASS_SHADOW_RGB = (0, 0, 0)        # capsule drop shadow
GLASS_SPECULAR_RGB = (255, 255, 255)  # sheen and Fresnel rim highlights
GLASS_KNOB = "#FFFFFF"             # glass slider knob
GLASS_KNOB_OUTLINE = (0, 0, 0, 38)
GLASS_KNOB_SHADOW_RGB = (0, 0, 0)


# 4. Small UI glyphs ---------------------------------------------------------
ARROW = {"light": "#6B7075", "dark": "#A5B0BA"}   # tree branch and spin-box arrows


# 5. Status text -------------------------------------------------------------
STATUS = {
    "error": "#b91c1c", "ok": "#166534", "muted": "#4b5563", "pane_label": "#475569",
    "tex_error": "#c0392b", "formula_ok": "#2a7a2a", "unavailable_row": "#a33333",
    "batch_skipped": (225, 232, 245, 255), "batch_failed": (255, 225, 225, 255),
}


# 6. Stars -------------------------------------------------------------------
STAR_FILL = "#F5B800"
STAR_EDGE = "#C98F00"


# 7. Annotation ----------------------------------------------------------------
ANNOTATION = {
    "pens": {"red": "#E53935", "blue": "#1E6FE0", "yellow": "#F9C80E", "green": "#2E9E48"},
    "laser": "#FF2B2B",
    "laser_core": (255, 235, 235, 220),
    "halo_on_light_plot": (255, 255, 255, 190),
    "halo_on_dark_plot": (10, 12, 16, 170),
    "clear_tint": (120, 215, 245, 150),       # glass tint of the sheet being drawn in
    "clear_glow": (170, 235, 255, 170),
    "swatch_outline": (0, 0, 0, 60),
}


# 8. Viewer Mark tools ---------------------------------------------------------
MARK = {
    "point_2d": "#39ff14",                   # 2D Point Mark (bright green, dark edge)
    "preview_region": "#4b5563", "preview_region_edge": "#6b7280",
    "half_level": "#0891b2", "half_level_edge": "#164e63", "half_level_fill": "#22d3ee",
    "half_level_border": "#67e8f9",
    "point": "#c62828", "point_selected": "#d97706", "point_edge": "#7f1d1d",
    "point_selected_edge": "#f59e0b", "point_selected_ring": "#ffffff", "outline": "#111827", "outline_alt": "#111111",
    "range": "#4b5563", "range_label": "#374151", "range_label_border": "#9ca3af",
    "hline": "#047857", "vline": "#6d28d9", "crosshair": "#111827", "crosshair_fill": "#facc15",
    "selected": "#d97706", "label_fill": (255, 255, 255, 205),
    "half_level_label_fill": (255, 255, 255, 210),
    "preview_region_fill": (107, 114, 128, 28), "preview_label_fill": (255, 255, 255, 190),
    "range_fill": (107, 114, 128, 38), "point_shadow": (0, 0, 0, 155), "transparent": (255, 255, 255, 0), "range_hover": (107, 114, 128, 58),
}


# 9. Viewer panes and traces ---------------------------------------------------
PANE = {
    "active_border": "#2563eb", "active_background": "#f8fafc", "active_title": "#1d4ed8",
    "inactive_border": "#cbd5e1", "inactive_background": "#ffffff", "inactive_title": "#475569",
}
TRACE_DEFAULT = "#2563eb"
ANIMATION_LABEL = {"text": "#111827", "fill": (255, 255, 255, 210)}


# 10. 3D view (interactive) ----------------------------------------------------
SURFACE_3D = {
    "white": "#ffffff",                      # plain white base colors
    "wall_light": "#f0f0f2",                 # Qt Graphs plot-area walls (light plots)
    "grid_main_light": "#c4c6cc", "grid_sub_light": "#dedfe3",
    "reference_plane_light": (35, 55, 80, 70), "reference_plane_dark": (205, 220, 235, 80),
    "dual_surface_b": (217, 119, 87),        # Surface B tint (alpha = its opacity)
    "legacy_background": "#f3f5f7", "legacy_label": "#26323d", "legacy_grid": "#b7c1ca",
    "legacy_reference_plane": (35, 43, 52, 90), "legacy_point_plane": (35, 55, 80, 80),
    "colorbar_outline": "#64748b", "colorbar_text": "#202124", "colorbar_handle": "#17212b",
    "pick_label": {"text": "#17212b", "background": "#f8fafc", "border": "#94a3b8"},
    "transparent_fallback": {"background": "#f3f5f7", "text": "#26323d", "secondary": "#46535f",
                             "border": "#8795a2", "pane": "#e8edf2"},
}


# 11. 3D publication export ----------------------------------------------------
PUBLICATION = {
    "pane": (0.95, 0.95, 0.95, 1.0), "grid": (0.80, 0.80, 0.82, 1.0),
    "axis_line": (0.25, 0.25, 0.25, 1.0),
    "plane_on_light": (0.20, 0.30, 0.42, 0.22), "plane_on_dark": (0.80, 0.86, 0.92, 0.28),
    "dual_surface_b": (0.85, 0.47, 0.34),
    "trajectory_line": (0.35, 0.35, 0.40, 0.55),
    "legacy_grid": "#cad3de", "legacy_plane": "#66778c",
}


# 12. Analysis markers ---------------------------------------------------------
ANALYSIS = {
    "physical_node": "#c026d3", "physical_antinode": "#059669",
    "coarse_node": "#f59e0b", "coarse_antinode": "#06b6d4",
    "coarse_node_marker": "#dc2626", "coarse_antinode_marker": "#ea7d00",
    "node_line": "#ff66cc", "node_label": "#cc3399",
    "window_primary": "#ff4d4d", "window_secondary": "#ffa31a", "rejected": "#bbbbbb",
    "pane_active_border": "#168aad", "pane_inactive_border": "#aab2b8",
    "latex_text_fallback": "#000000",
    "node_finder": {"fitted": "#ffffff", "fit_traces": "#ee55cc", "nodes": "#e53935",
                    "antinodes": "#008c5a", "average": "#4c78a8", "smoothed": "#f28e2b"},
}


def rgba_css(value: tuple[str, float]) -> str:
    """('#RRGGBB', alpha 0..1) -> 'rgba(r, g, b, a255)' for stylesheets."""
    color, alpha = value
    color = color.lstrip("#")
    red, green, blue = (int(color[index:index + 2], 16) for index in (0, 2, 4))
    return f"rgba({red}, {green}, {blue}, {round(float(alpha) * 255)})"
