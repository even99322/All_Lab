"""One user data folder for everything LabLogViewer keeps outside HDF5.

Layout of the data folder (default name ``LabLogViewerData``)::

    LabLogViewerData/
        state/      stars, tags, comments, marks, transforms, overlays, views,
                    sessions, database index, settings, drag-share, received
        fitting/    YIG / Node-Antinode sessions, formula library, parameters
        lablogviewer_data.json   folder marker + migration record
        README.txt

Default location: macOS ``~/Documents/LabLogViewerData``; Windows
``C:\\Users\\<user>\\LabLogViewerData``. The folder can be moved from
Settings; the chosen path is kept in a small pointer file in the per-user
application-config folder (the only file LabLogViewer writes elsewhere).

Moves happen at the next launch, before any store opens, so nothing written
while closing the app is left behind. On first use, data from earlier
versions (``~/.lablogviewer`` and the fitting AppData folder) is copied in;
the originals are kept untouched as a backup.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


LOGGER = logging.getLogger(__name__)

DATA_FOLDER_NAME = "LabLogViewerData"
STATE_FOLDER = "state"
FITTING_FOLDER = "fitting"
MARKER_NAME = "lablogviewer_data.json"
POINTER_NAME = "data_location.json"
LEGACY_STATE_DIRECTORY = ".lablogviewer"
# Rebuildable caches are not user history and are not migrated.
_SKIPPED_LEGACY_NAMES = {"matplotlib", ".DS_Store"}

_root: Path | None = None
_notice: str | None = None


# -- locations ---------------------------------------------------------------
def _is_windows() -> bool:
    return os.name == "nt"


def default_data_root() -> Path:
    home = Path.home()
    if _is_windows():
        return home / DATA_FOLDER_NAME
    documents = home / "Documents"
    if (sys.platform == "darwin" or documents.is_dir()) and cloud_service(documents / DATA_FOLDER_NAME) is None:
        return documents / DATA_FOLDER_NAME
    return home / DATA_FOLDER_NAME                 # Documents is cloud-synced: stay local


class CloudLocationError(ValueError):
    """The target is synced by a cloud service (history records must stay local)."""

    def __init__(self, service: str):
        super().__init__(service)
        self.service = service


def _cloud_folder(part: str) -> str | None:
    """Service for one path component, by each service's own folder naming."""
    lowered = part.lower()
    if lowered in {"mobile documents", "com~apple~clouddocs", "icloud drive", "iclouddrive"}:
        return "iCloud"
    if lowered == "onedrive" or lowered.startswith("onedrive - ") or lowered.startswith("onedrive-"):
        return "OneDrive"
    if lowered == "dropbox" or lowered.startswith("dropbox (") or lowered.startswith("dropbox-"):
        return "Dropbox"
    if lowered in {"google drive", "googledrive", "my drive"} or lowered.startswith("googledrive-"):
        return "Google Drive"
    return None


def _icloud_syncs(folder: str) -> bool:
    """macOS "Desktop & Documents Folders" sync keeps a copy under iCloud Drive."""
    return (Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / folder).is_dir()


def cloud_service(path: str | Path) -> str | None:
    """Name of the cloud service syncing ``path``, or None if it stays on this computer."""
    target = Path(path).expanduser()
    try:
        target = target.resolve()
    except OSError:
        pass
    for part in target.parts:
        service = _cloud_folder(part)
        if service is not None:
            return service
    if "cloudstorage" in (part.lower() for part in target.parts):   # macOS File Provider folders
        return "a cloud service"
    if sys.platform == "darwin" and not _is_windows():
        home = Path.home()
        for folder in ("Documents", "Desktop"):
            if target.is_relative_to(home / folder) and _icloud_syncs(folder):
                return "iCloud"
    return None


def pointer_path() -> Path:
    """Per-user config file that remembers where the data folder is."""
    home = Path.home()
    if _is_windows():
        base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return base / "LabLogViewer" / POINTER_NAME


def legacy_sources() -> list[tuple[str, Path]]:
    """(destination sub-folder, source folder) used by versions before v0.18D."""
    home = Path.home()
    old_state = home / LEGACY_STATE_DIRECTORY
    fitting: list[Path] = [old_state / FITTING_FOLDER]      # fallback used when AppData was unavailable
    if _is_windows():
        for variable, default in (("APPDATA", home / "AppData" / "Roaming"),
                                  ("LOCALAPPDATA", home / "AppData" / "Local")):
            fitting.append(Path(os.environ.get(variable) or default) / "LabLogViewer" / FITTING_FOLDER)
    elif sys.platform == "darwin":
        fitting.append(home / "Library" / "Application Support" / "LabLogViewer" / FITTING_FOLDER)
    else:
        fitting.append(Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
                       / "LabLogViewer" / FITTING_FOLDER)
    try:
        from PySide6.QtCore import QCoreApplication, QStandardPaths

        if QCoreApplication.instance() is not None:
            location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
            # Qt asks the system for the real home on macOS and ignores $HOME; a folder
            # outside the home in use is never this user's legacy data (tests, sandboxes).
            if location and Path(location).resolve().is_relative_to(home.resolve()):
                fitting.append(Path(location) / FITTING_FOLDER)
    except ImportError:
        pass
    sources = [(STATE_FOLDER, old_state)]
    seen: set[str] = set()
    for folder in fitting:
        key = os.path.normcase(str(folder))
        if key not in seen:
            seen.add(key)
            sources.append((FITTING_FOLDER, folder))
    return sources


# -- public API --------------------------------------------------------------
def data_root() -> Path:
    """The active data folder; prepares, moves and migrates it on first call."""
    global _root
    if _root is None:
        _root = _prepare()
    return _root


def state_path(filename: str) -> Path:
    return data_root() / STATE_FOLDER / filename


def fitting_dir() -> Path:
    folder = data_root() / FITTING_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def startup_notice() -> str | None:
    """A problem found while opening the data folder (shown in Settings)."""
    return _notice


def pending_move() -> Path | None:
    """Folder that becomes active at the next launch, if it differs from now."""
    path = _read_pointer().get("path")
    if path and Path(path).resolve() != data_root().resolve():
        return Path(path)
    return None


def normalize_choice(folder: str | Path) -> Path:
    """A folder picked in the dialog -> the data folder inside it."""
    chosen = Path(folder).expanduser()
    return chosen if chosen.name == DATA_FOLDER_NAME else chosen / DATA_FOLDER_NAME


def folder_has_data(folder: Path) -> bool:
    return (folder / MARKER_NAME).is_file() or any(
        (folder / name).is_dir() and any((folder / name).iterdir()) for name in (STATE_FOLDER, FITTING_FOLDER))


def request_move(target: str | Path, allow_cloud: bool = False) -> str:
    """Schedule moving the current data folder to ``target`` at the next launch.

    Returns "scheduled", "same", "occupied" (target already holds LabLogViewer
    data; use :func:`use_existing` or cancel), or raises ValueError for an
    unusable target. A cloud-synced target raises :class:`CloudLocationError`
    unless ``allow_cloud`` (the user accepted that lost history records are
    their own responsibility; the consent is recorded).
    """
    current = data_root().resolve()
    target = Path(target).expanduser().resolve()
    service = cloud_service(target)
    if service is not None and not allow_cloud:
        raise CloudLocationError(service)
    if target == current:
        _write_pointer({"path": str(current)})
        return "same"
    if target.is_relative_to(current) or current.is_relative_to(target):
        raise ValueError("The new folder cannot be inside the current data folder (or contain it).")
    if target.exists() and folder_has_data(target):
        return "occupied"
    _check_writable(target.parent)
    pointer = {"path": str(target), "pending_move_from": str(current)}
    if service is not None:
        pointer["cloud_consent"] = {"service": service, "path": str(target),
                                    "accepted": datetime.now().isoformat(timespec="seconds")}
    _write_pointer(pointer)
    return "scheduled"


def use_existing(target: str | Path) -> None:
    """Switch to a folder that already holds data; the current folder stays as it is."""
    target = Path(target).expanduser().resolve()
    _check_writable(target)
    _write_pointer({"path": str(target)})


# -- internals ---------------------------------------------------------------
def _prepare() -> Path:
    global _notice
    pointer = _read_pointer()
    default = default_data_root()
    root = Path(pointer["path"]) if pointer.get("path") else default
    source = pointer.get("pending_move_from")
    if source:
        root = _apply_move(Path(source), root)
    if root != default and not root.parent.is_dir():
        # e.g. an external drive that is not connected: do not create a fake
        # folder in its place; use the default until it is available again.
        _notice = f"Data folder is not available: {root}. Using {default} for now."
        LOGGER.warning(_notice)
        root = default
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        _notice = f"Cannot open data folder {root} ({error}). Using {default}."
        LOGGER.warning(_notice)
        root = default
        root.mkdir(parents=True, exist_ok=True)
    marker = _read_marker(root)
    if not marker.get("legacy_migration", {}).get("completed"):
        _migrate_legacy(root, marker)
    _write_readme(root)
    return root


def _apply_move(source: Path, target: Path) -> Path:
    """Copy ``source`` to ``target``, verify, then remove ``source``."""
    global _notice
    try:
        if source.is_dir():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target, dirs_exist_ok=True)
            missing = [path for path in _files(source)
                       if not (target / path).is_file()
                       or (target / path).stat().st_size != (source / path).stat().st_size]
            if missing:
                raise OSError(f"{len(missing)} file(s) did not copy, e.g. {missing[0]}")
            if (source / MARKER_NAME).is_file():            # only ever delete our own folder
                shutil.rmtree(source, ignore_errors=True)
        _write_pointer({"path": str(target)})
        return target
    except OSError as error:
        _notice = f"Moving the data folder to {target} failed ({error}). Still using {source}."
        LOGGER.warning(_notice)
        _write_pointer({"path": str(source)})
        return source


def _migrate_legacy(root: Path, marker: dict) -> None:
    copied: list[str] = []
    kept: list[str] = []
    errors: list[str] = []
    sources: list[str] = []
    for folder_name, source in legacy_sources():
        if not source.is_dir():
            continue
        sources.append(str(source))
        destination = root / folder_name
        for relative in _files(source):
            if relative.parts[0] in _SKIPPED_LEGACY_NAMES or relative.name in _SKIPPED_LEGACY_NAMES:
                continue
            if folder_name == STATE_FOLDER and relative.parts[0] == FITTING_FOLDER:
                continue                                   # handled as a fitting source
            target = destination / relative
            label = f"{folder_name}/{relative.as_posix()}"
            if target.exists():
                kept.append(label)                         # never overwrite newer data
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / relative, target)
                copied.append(label)
            except OSError as error:
                errors.append(f"{source / relative}: {error}")
    record = {"sources": sources, "copied": copied, "kept_existing": kept, "errors": errors}
    if not errors:
        record["completed"] = datetime.now().isoformat(timespec="seconds")
    marker["legacy_migration"] = record
    _write_marker(root, marker)
    if copied:
        LOGGER.info("Copied %d file(s) from earlier LabLogViewer versions into %s", len(copied), root)


def _files(folder: Path) -> list[Path]:
    return sorted(path.relative_to(folder) for path in folder.rglob("*") if path.is_file())


def _check_writable(folder: Path) -> None:
    probe_parent = folder
    while not probe_parent.exists():
        probe_parent = probe_parent.parent
    if not os.access(probe_parent, os.W_OK):
        raise ValueError(f"No permission to write in {probe_parent}.")


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_pointer() -> dict:
    return _read_json(pointer_path())


def _write_pointer(payload: dict) -> None:
    from app.core.external_state import atomic_write_json

    atomic_write_json(pointer_path(), payload)


def _read_marker(root: Path) -> dict:
    marker = _read_json(root / MARKER_NAME)
    marker.setdefault("format", 1)
    marker.setdefault("created", datetime.now().isoformat(timespec="seconds"))
    return marker


def _write_marker(root: Path, marker: dict) -> None:
    from app.core.external_state import atomic_write_json

    atomic_write_json(root / MARKER_NAME, marker)


def _write_readme(root: Path) -> None:
    readme = root / "README.txt"
    if readme.exists():
        return
    try:
        readme.write_text(
            "LabLogViewer data folder\n"
            "========================\n\n"
            "Everything LabLogViewer remembers outside your HDF5 files lives here.\n"
            "HDF5 measurement files are never modified by these records.\n\n"
            "state/    stars, tags, comments, marks, transforms, overlays, named views,\n"
            "          axis presets, sessions, database index, settings\n"
            "fitting/  YIG / Node-Antinode analysis sessions, formula library, parameters\n"
            f"{MARKER_NAME}  folder marker and the record of data copied from older versions\n\n"
            "To move this folder, use Settings > General > Data Folder in LabLogViewer\n"
            "(do not move it by hand while LabLogViewer is running).\n",
            encoding="utf-8")
    except OSError:
        pass


def _reset_for_tests() -> None:
    global _root, _notice
    _root = None
    _notice = None
