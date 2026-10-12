"""Pet pictures as an optional download (v2.1.5).

The sprites of all pets are ~220 MB, more than the rest of LabLogViewer, so the installers no longer carry
them. They live in a per-user folder:

    Windows  %LOCALAPPDATA%\\LabLogViewer\\pets
    macOS    ~/Library/Application Support/LabLogViewer/pets
    Linux    ~/.local/share/LabLogViewer/pets
    (environment variable LLV_PETS_DIR overrides)

and come from the lab hub as one zip (``LabLogViewerPets_v<version>.zip``: ``<pet id>/lvXX/<clip>/frame_XXXX.webp``
and ``pets_manifest.json``), listed in ``/get/setup.txt`` as the line ``pets|version|file|size|sha256|url``.
QELSetup (Windows, optional component) and the Mac install script (asks) unpack it to the same folder; the pet
page in Settings downloads it when it is missing.

Running from the source folder still uses ``app/pet/assets`` when that folder has the pictures.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

MANIFEST = "pets_manifest.json"
PICTURES_VERSION = "1.0.0"                   # raise when the pictures change (LabLogViewerPets_v<this>.zip)
BUNDLED = Path(__file__).resolve().parent / "assets"
ASSET_PREFIX = "../assets/"                 # how the character packs (characters/*.json) name the pictures


def pets_dir() -> Path:
    env = os.environ.get("LLV_PETS_DIR")
    if env:
        return Path(env).expanduser()
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / "LabLogViewer" / "pets"


def _has_pictures(folder: Path) -> bool:
    try:
        return any((d / "lv01" / "idle").is_dir() for d in folder.iterdir() if d.is_dir())
    except OSError:
        return False


def installed_info() -> dict | None:
    """The manifest of the downloaded pictures, or None."""
    try:
        return json.loads((pets_dir() / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def assets_root() -> Path:
    """Where the pictures are: the downloaded folder, else the source folder's app/pet/assets."""
    folder = pets_dir()
    if (folder / MANIFEST).is_file() or _has_pictures(folder):
        return folder
    if _has_pictures(BUNDLED):
        return BUNDLED
    return folder


def available() -> bool:
    return _has_pictures(assets_root())


def resolve(pack_folder: Path, relative: str) -> Path:
    """A path written in a character pack ("../assets/<id>/lv01") -> the real folder."""
    rel = str(relative).replace("\\", "/")
    if rel.startswith(ASSET_PREFIX):
        return assets_root() / rel[len(ASSET_PREFIX):]
    return Path(pack_folder) / relative


# ---- download from the hub ------------------------------------------------------------------------
def find_on_hub(timeout: float = 6.0, bases: list[str] | None = None) -> dict | None:
    """{"version","name","size","sha256","url","hub"} of the pets zip on the hub, or None when the hub has none.
    Raises HubUpdateError when no hub answers."""
    import urllib.parse

    from app.core import hub_update

    last = None
    for base in bases or hub_update.hub_urls():
        query = urllib.parse.urlencode({"platform": hub_update.this_platform(), "arch": hub_update.this_arch(),
                                       "pets": "1"})
        try:
            with hub_update._opener().open(f"{base}/get/setup.txt?{query}", timeout=timeout) as response:
                text = response.read(200_000).decode("utf-8", "replace")
        except Exception as error:
            last = error
            continue
        for line in text.splitlines():
            parts = line.strip().split("|")
            if len(parts) >= 6 and parts[0] == "pets":
                url = parts[5]
                return {"version": parts[1], "name": parts[2], "size": int(parts[3] or 0), "sha256": parts[4],
                        "url": base + url if url.startswith("/") else url, "hub": base}
        return None
    raise hub_update.HubUpdateError(f"The hub cannot be reached ({last}).")


def install_zip(zip_path: Path, target: Path | None = None) -> Path:
    """Unpack a pets zip into the pets folder (replacing what is there). Returns the folder."""
    target = Path(target) if target else pets_dir()
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="pets_", dir=target.parent))
    try:
        with zipfile.ZipFile(zip_path) as archive:
            root = staging.resolve()
            for member in archive.infolist():
                dest = (staging / member.filename).resolve()
                if root not in dest.parents and dest != root:
                    raise ValueError(f"unsafe path in the pets zip: {member.filename}")
            archive.extractall(staging)
        if not _has_pictures(staging):
            raise ValueError("the pets zip has no pet pictures")
        old = None
        if target.exists():
            old = target.with_name(target.name + ".old")
            shutil.rmtree(old, ignore_errors=True)
            target.rename(old)
        staging.rename(target)
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
        return target
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def install_from_hub(progress: Callable[[int, int], None] | None = None, info: dict | None = None) -> Path:
    from app.core import hub_update

    info = info or find_on_hub()
    if info is None:
        raise hub_update.HubUpdateError("The hub has no pet pictures yet.")
    zip_path = hub_update.download(info, progress)
    try:
        return install_zip(zip_path)
    finally:
        shutil.rmtree(zip_path.parent, ignore_errors=True)


def build_zip(source: Path, out: Path, version: str) -> Path:
    """Packaging: app/pet/assets -> LabLogViewerPets_v<version>.zip (pictures are already compressed: stored)."""
    source, out = Path(source), Path(out)
    pets = sorted(d.name for d in source.iterdir() if d.is_dir() and (d / "lv01").is_dir())
    files = [p for p in sorted(source.rglob("*")) if p.is_file() and p.name != ".DS_Store"]
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as archive:
        for path in files:
            archive.write(path, path.relative_to(source).as_posix())
        archive.writestr(MANIFEST, json.dumps({"version": version, "pets": pets, "files": len(files)}, indent=1))
        # the hub's console recognises the zip by this (target "llvpets")
        archive.writestr("qel-module.json", json.dumps({"id": "llvpets", "version": version, "kind": "module",
                                                        "name": "讀檔寵物圖檔", "notes": ", ".join(pets)},
                                                       ensure_ascii=False, indent=1))
    return out


if __name__ == "__main__":          # python -m app.pet.pack [out folder]  ->  <out>/LabLogViewerPets_v<version>.zip
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist")
    print(build_zip(BUNDLED, out_dir / f"LabLogViewerPets_v{PICTURES_VERSION}.zip", PICTURES_VERSION))
