"""matplotlib figures as data (YIG Analysis mirror): not screenshots.

The Host turns what a figure shows into plain values — lines, scatter points,
images, filled spans, text, axis labels / limits / scales, legends — and the
Client redraws them into its own figure with its own theme. Lines are reduced
to at most ``max_points`` (min / max per bucket keeps peaks), images to at
most ``max_image`` pixels per side: enough for the screen, small on the wire.
No code, no pickled objects.
"""

from __future__ import annotations

import numpy as np
from matplotlib import colors as mcolors
from matplotlib.collections import PathCollection, PolyCollection, QuadMesh
from matplotlib.image import AxesImage
from matplotlib.lines import Line2D
from matplotlib.patches import Polygon, Rectangle

MAX_POINTS = 2000
MAX_IMAGE = 480


def _color(value):
    try:
        return mcolors.to_hex(value, keep_alpha=True)
    except (ValueError, TypeError):
        return None


def _decimate(x: np.ndarray, y: np.ndarray, max_points: int):
    n = len(x)
    if n <= max_points:
        return x, y
    buckets = max_points // 2
    edges = np.linspace(0, n, buckets + 1).astype(int)
    keep = []
    finite = np.nan_to_num(y.astype(float), nan=np.nan)
    for start, stop in zip(edges[:-1], edges[1:]):
        if stop <= start:
            continue
        segment = finite[start:stop]
        if np.all(np.isnan(segment)):
            keep.append(start)
            continue
        low, high = start + int(np.nanargmin(segment)), start + int(np.nanargmax(segment))
        keep.extend(sorted({low, high}))
    keep = np.asarray(keep)
    return x[keep], y[keep]


def _reduce_image(array: np.ndarray, max_image: int) -> np.ndarray:
    step_y = max(1, int(np.ceil(array.shape[0] / max_image)))
    step_x = max(1, int(np.ceil(array.shape[1] / max_image)))
    return array[::step_y, ::step_x]


def _axes(ax, max_points: int, max_image: int) -> dict:
    lines = []
    for line in ax.get_lines():
        if not line.get_visible():
            continue
        x = np.asarray(line.get_xdata(orig=False), dtype=float)
        y = np.asarray(line.get_ydata(orig=False), dtype=float)
        if x.size != y.size:
            continue
        x, y = _decimate(x, y, max_points)
        lines.append({
            "x": x.astype(np.float32 if x.size > 64 else np.float64), "y": y.astype(np.float32 if y.size > 64 else np.float64),
            "color": _color(line.get_color()), "lw": float(line.get_linewidth()), "ls": str(line.get_linestyle()),
            "marker": str(line.get_marker()) if line.get_marker() not in (None, "None", "") else "",
            "ms": float(line.get_markersize()), "alpha": line.get_alpha(),
            "label": str(line.get_label()), "z": float(line.get_zorder()),
            "transform": "axes" if line.get_transform() != ax.transData else "data",
        })
    scatters, images, polys, rects = [], [], [], []
    for collection in ax.collections:
        if not collection.get_visible():
            continue
        if isinstance(collection, PathCollection):
            offsets = np.asarray(collection.get_offsets(), dtype=float)
            if offsets.size == 0:
                continue
            faces = collection.get_facecolors()
            scatters.append({
                "xy": offsets[:4 * max_points].astype(np.float32),
                "color": _color(faces[0]) if len(faces) else None,
                "colors": np.asarray(faces[:4 * max_points], dtype=np.float32) if len(faces) > 1 else None,
                "size": float(np.median(collection.get_sizes())) if len(collection.get_sizes()) else 20.0,
                "label": str(collection.get_label()), "z": float(collection.get_zorder()),
            })
        elif isinstance(collection, QuadMesh):
            array = collection.get_array()
            coords = collection.get_coordinates()
            if array is None or coords is None:
                continue
            data = np.ma.filled(np.asarray(array, dtype=float).reshape(coords.shape[0] - 1, coords.shape[1] - 1), np.nan)
            images.append({
                "data": _reduce_image(data, max_image).astype(np.float32),
                "extent": [float(coords[..., 0].min()), float(coords[..., 0].max()),
                           float(coords[..., 1].min()), float(coords[..., 1].max())],
                "cmap": collection.get_cmap().name, "clim": [float(v) for v in collection.get_clim()],
                "origin": "lower", "z": float(collection.get_zorder()),
            })
        elif isinstance(collection, PolyCollection):
            for path in collection.get_paths()[:20]:
                vertices = np.asarray(path.vertices, dtype=float)
                if vertices.size:
                    faces = collection.get_facecolor()
                    polys.append({"xy": vertices.astype(np.float32),
                                  "color": _color(faces[0]) if len(faces) else None,
                                  "alpha": collection.get_alpha(), "z": float(collection.get_zorder())})
    for image in ax.get_images():
        if not isinstance(image, AxesImage) or not image.get_visible():
            continue
        data = np.ma.filled(np.asarray(image.get_array(), dtype=float), np.nan)
        if data.ndim != 2:
            continue
        images.append({
            "data": _reduce_image(data, max_image).astype(np.float32),
            "extent": [float(v) for v in image.get_extent()], "cmap": image.get_cmap().name,
            "clim": [float(v) for v in image.get_clim()], "origin": image.origin, "z": float(image.get_zorder()),
        })
    for patch in ax.patches:
        if not patch.get_visible():
            continue
        if isinstance(patch, Polygon):
            polys.append({"xy": np.asarray(patch.get_xy(), dtype=np.float32), "color": _color(patch.get_facecolor()),
                          "alpha": patch.get_alpha(), "z": float(patch.get_zorder()),
                          "transform": "blended" if patch.get_transform() != ax.transData else "data"})
        elif isinstance(patch, Rectangle):
            rects.append({"xy": [float(v) for v in patch.get_xy()], "w": float(patch.get_width()),
                          "h": float(patch.get_height()), "color": _color(patch.get_facecolor()),
                          "alpha": patch.get_alpha(), "z": float(patch.get_zorder())})
    texts = [{"text": text.get_text(), "xy": [float(v) for v in text.get_position()],
              "color": _color(text.get_color()), "size": float(text.get_fontsize()),
              "ha": text.get_ha(), "va": text.get_va(),
              "coords": "axes" if text.get_transform() == ax.transAxes else "data"}
             for text in ax.texts if text.get_visible() and text.get_text()]
    legend = ax.get_legend()
    return {
        "position": [float(v) for v in ax.get_position().bounds],
        "title": ax.get_title(), "xlabel": ax.get_xlabel(), "ylabel": ax.get_ylabel(),
        "xlim": [float(v) for v in ax.get_xlim()], "ylim": [float(v) for v in ax.get_ylim()],
        "xscale": ax.get_xscale(), "yscale": ax.get_yscale(), "visible": ax.get_visible(),
        "axis_on": ax.axison, "grid": any(line.get_visible() for line in ax.get_xgridlines()),
        "lines": lines, "scatters": scatters, "images": images, "polys": polys, "rects": rects,
        "texts": texts, "legend": legend is not None and legend.get_visible(),
        "legend_loc": getattr(legend, "_loc", "best") if legend is not None else "best",
    }


def serialize_figure(figure, max_points: int = MAX_POINTS, max_image: int = MAX_IMAGE) -> dict:
    return {"axes": [_axes(ax, max_points, max_image) for ax in figure.axes],
            "suptitle": figure._suptitle.get_text() if getattr(figure, "_suptitle", None) else ""}


def render_figure(figure, data: dict) -> None:
    """Redraw serialized content into ``figure`` (the Client's own theme applies)."""
    figure.clear()
    for spec in data.get("axes", []):
        ax = figure.add_axes(spec["position"])
        ax.set_visible(bool(spec.get("visible", True)))
        if not spec.get("axis_on", True):
            ax.set_axis_off()
        for image in spec.get("images", []):
            ax.imshow(image["data"], extent=image["extent"], cmap=image["cmap"], vmin=image["clim"][0],
                      vmax=image["clim"][1], origin=image.get("origin", "lower"), aspect="auto",
                      interpolation="nearest", zorder=image.get("z", 0))
        for poly in spec.get("polys", []):
            transform = ax.get_xaxis_transform() if poly.get("transform") == "blended" else ax.transData
            ax.add_patch(Polygon(poly["xy"], closed=True, facecolor=poly.get("color") or "none",
                                 alpha=poly.get("alpha"), edgecolor="none", zorder=poly.get("z", 1),
                                 transform=transform))
        for rect in spec.get("rects", []):
            ax.add_patch(Rectangle(rect["xy"], rect["w"], rect["h"], facecolor=rect.get("color") or "none",
                                   alpha=rect.get("alpha"), edgecolor="none", zorder=rect.get("z", 1)))
        for line in spec.get("lines", []):
            kwargs = dict(color=line.get("color"), linewidth=line.get("lw", 1.0), linestyle=line.get("ls", "-"),
                          marker=line.get("marker") or None, markersize=line.get("ms", 4.0), alpha=line.get("alpha"),
                          label=line.get("label"), zorder=line.get("z", 2))
            if line.get("transform") == "axes":
                kwargs["transform"] = ax.transAxes
            ax.plot(line["x"], line["y"], **kwargs)
        for scatter in spec.get("scatters", []):
            xy = np.asarray(scatter["xy"])
            color = scatter.get("colors") if scatter.get("colors") is not None else scatter.get("color")
            ax.scatter(xy[:, 0], xy[:, 1], s=scatter.get("size", 20.0), c=color, label=scatter.get("label"),
                       zorder=scatter.get("z", 3))
        for text in spec.get("texts", []):
            ax.text(*text["xy"], text["text"], color=text.get("color"), fontsize=text.get("size", 10),
                    ha=text.get("ha", "left"), va=text.get("va", "baseline"),
                    transform=ax.transAxes if text.get("coords") == "axes" else ax.transData)
        ax.set_title(spec.get("title", ""))
        ax.set_xlabel(spec.get("xlabel", ""))
        ax.set_ylabel(spec.get("ylabel", ""))
        try:
            ax.set_xscale(spec.get("xscale", "linear"))
            ax.set_yscale(spec.get("yscale", "linear"))
        except ValueError:
            pass
        ax.set_xlim(spec["xlim"])
        ax.set_ylim(spec["ylim"])
        if spec.get("grid"):
            ax.grid(True, alpha=0.3)
        if spec.get("legend"):
            handles, labels = ax.get_legend_handles_labels()
            shown = [(h, l) for h, l in zip(handles, labels) if l and not l.startswith("_")]
            if shown:
                ax.legend(*zip(*shown), loc=spec.get("legend_loc", "best"), fontsize="small")
    if data.get("suptitle"):
        figure.suptitle(data["suptitle"])
