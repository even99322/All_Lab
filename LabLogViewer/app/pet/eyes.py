"""Pupil placement shared by the live window and offline previews (pure Python)."""
import math


def pupil_positions(eyes, gaze, pupil_r, scale=1.0):
    """eyes: one frame of eye ellipses (sprite pixels); gaze: (gx, gy) in [-1, 1].

    Returns [(x, y, r), ...] for right and left eye in display pixels, or [] when the
    pupils are hidden (blink, happy squint).
    """
    ps = eyes.get('pupil_scale', 1.0)
    if ps <= 0.15:
        return []
    r = pupil_r * scale * min(1.0, ps * 1.2)
    gx, gy = gaze
    out = []
    for key in ('right', 'left'):
        e = eyes[key]
        cx, cy, rx, ry = e['cx'] * scale, e['cy'] * scale, e['rx'] * scale, e['ry'] * scale
        ax, ay = max(1.0, rx - r * 1.5), max(1.0, ry - r * 1.5)
        # start from the deadpan rest position, then lean toward the gaze direction
        px = e['rest_x'] * scale + gx * ax * 0.95
        py = e['rest_y'] * scale + gy * ay * 0.95
        nx, ny = (px - cx) / ax, (py - cy) / ay
        n = math.hypot(nx, ny)
        if n > 1.0:
            px, py = cx + nx / n * ax, cy + ny / n * ay
        out.append((px, py, r))
    return out
