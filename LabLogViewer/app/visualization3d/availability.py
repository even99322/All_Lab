"""Low-cost OpenGL context probe used before creating a Q3D native window."""

from __future__ import annotations


def probe_opengl_context() -> bool:
    """Return whether Qt can make a temporary OpenGL context current.

    This probe prevents Qt Data Visualization from being constructed on
    platforms such as Qt's offscreen plugin where native graph construction
    can terminate the process instead of raising a Python exception.
    """
    from PySide6.QtGui import QOffscreenSurface, QOpenGLContext

    context = QOpenGLContext()
    surface = QOffscreenSurface()
    try:
        if not context.create():
            return False
        surface.setFormat(context.format())
        surface.create()
        if not surface.isValid() or not context.makeCurrent(surface):
            return False
        context.doneCurrent()
        return True
    finally:
        if context.isValid():
            context.doneCurrent()
        surface.destroy()
