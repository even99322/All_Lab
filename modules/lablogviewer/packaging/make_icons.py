"""Step 2: the program file's icon from icons/app/app_icon_4.png (the default icon).

    python packaging/make_icons.py        -> build/LabLogViewer.icns (macOS) and build/LabLogViewer.ico
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "icons" / "app" / "app_icon_4.png"
BUILD = ROOT / "build"


def make() -> None:
    BUILD.mkdir(exist_ok=True)
    image = Image.open(SOURCE).convert("RGBA")
    image.save(BUILD / "LabLogViewer.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    if sys.platform == "darwin":
        with tempfile.TemporaryDirectory() as work:
            iconset = Path(work) / "LabLogViewer.iconset"
            iconset.mkdir()
            for size in (16, 32, 128, 256, 512):
                image.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
                image.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(BUILD / "LabLogViewer.icns")],
                           check=True)
    print("Icons written to", BUILD)


if __name__ == "__main__":
    make()
