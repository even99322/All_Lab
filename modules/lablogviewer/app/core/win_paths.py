"""Windows long paths (> 260 characters) for native libraries such as HDF5."""

from __future__ import annotations

import os

_LIMIT = 248          # Windows' limit for directories; files fail at 260


def _is_windows() -> bool:
    return os.name == "nt"


def long_path(path) -> str:
    """``path`` as a string HDF5 can open on Windows even when it is very long.

    On Windows, a long absolute path gets the ``\\\\?\\`` (or ``\\\\?\\UNC\\``) prefix,
    which lifts the 260-character MAX_PATH limit without the registry setting.
    Short paths and every other platform are returned unchanged.
    """
    text = os.fspath(path)
    if not _is_windows():
        return text
    absolute = os.path.abspath(text)
    if len(absolute) < _LIMIT or absolute.startswith("\\\\?\\"):
        return text
    if absolute.startswith("\\\\"):                      # \\server\share\...
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute
