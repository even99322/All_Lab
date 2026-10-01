"""What happens on the first launch after LabLogViewer was updated.

The data folder remembers which version used it last (lablogviewer_data.json:
"last_app_version") and the format of its records ("data_format"). On start:

1. A newer program on older data: every upgrade step from the data's format to
   DATA_FORMAT runs in order, after a backup of state/ and fitting/ in
   <data folder>/update_backups/<time>_<old>_to_<new>/. If a step fails, the backup
   is put back and the program works with the data as it was.
2. The version changed since the last start: the "What's New" window is shown once
   (not on a brand-new installation).
3. An older program on newer data (data_format higher than it knows): a warning asks
   to update the program; nothing is changed.

Program files and the data folder are separate, so installing an update never touches
the data; this module is the only place that adapts the data to a new version.
Add a step to UPGRADES (and raise DATA_FORMAT) whenever a version changes how records
are stored.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

DATA_FORMAT = 1
MARKER = "lablogviewer_data.json"
BACKUPS = "update_backups"

# (from format, to format, what it does, function(data folder)) - applied in order.
UPGRADES: list[tuple[int, int, str, Callable[[Path], None]]] = []


@dataclass
class UpdateResult:
    previous_version: str | None = None       # None: first start of any version on this data
    current_version: str = ""
    fresh: bool = False                       # a brand-new data folder
    show_whats_new: bool = False
    upgraded: list[str] = field(default_factory=list)
    backup: Path | None = None
    error: str | None = None
    newer_data: bool = False                  # the data was written by a newer program


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, value: dict) -> None:
    from app.core.external_state import atomic_write_json

    atomic_write_json(path, value)


def had_records(root: Path) -> bool:
    """Whether the data folder already held a user's records (before this start wrote any)."""
    for name in ("state", "fitting"):
        folder = root / name
        if folder.is_dir() and any(p.is_file() and p.name != ".DS_Store" for p in folder.rglob("*")):
            return True
    return False


def version_key(version: str) -> tuple:
    """"0.19E" < "0.19F" < "0.20A" < "1.01" (= 1.0.1) < "1.0.3" < "1.0.10".

    Numbers compare as numbers, letters as letters. From 1.0.3 on, versions have three
    parts like the lab's release tags; the two-part 1.x names used before ("1.01") mean 1.0.x.
    """
    import re

    text = str(version).strip().lstrip("vV")
    legacy = re.fullmatch(r"([1-9]\d*)\.(\d+)", text)
    if legacy:
        text = f"{legacy.group(1)}.0.{int(legacy.group(2))}"
    return tuple((int(part), "") if part.isdigit() else (0, part)
                 for part in re.findall(r"\d+|[A-Za-z]+", text))


def check(root: str | Path, current_version: str, *, had_data: bool | None = None,
          upgrades=None, formats: int | None = None) -> UpdateResult:
    """Run at start, right after the data folder is ready and before any record is read."""
    root = Path(root)
    upgrades = UPGRADES if upgrades is None else upgrades
    target = DATA_FORMAT if formats is None else formats
    marker_path = root / MARKER
    marker = _read(marker_path)
    result = UpdateResult(previous_version=marker.get("last_app_version"), current_version=current_version)
    had_data = had_records(root) if had_data is None else had_data
    data_format = marker.get("data_format", 1)
    if not isinstance(data_format, int) or data_format < 1:
        data_format = 1
    if data_format > target:
        result.newer_data = True              # an older program: change nothing
        return result
    if result.previous_version is None:
        result.fresh = not had_data
        result.show_whats_new = had_data      # data from a version before 1.01 (which did not record itself)
    elif result.previous_version != current_version:
        result.show_whats_new = version_key(current_version) > version_key(result.previous_version)
    steps = [step for step in upgrades if step[0] >= data_format and step[1] <= target]
    steps.sort(key=lambda step: step[0])
    if steps and had_data:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = root / BACKUPS / f"{stamp}_{result.previous_version or 'old'}_to_{current_version}"
        backup.mkdir(parents=True, exist_ok=True)
        for name in ("state", "fitting"):
            if (root / name).is_dir():
                shutil.copytree(root / name, backup / name)
        result.backup = backup
        try:
            for start, end, description, function in steps:
                function(root)
                result.upgraded.append(f"{start} → {end}: {description}")
                data_format = end
        except Exception as error:            # put everything back, work with the data as it was
            for name in ("state", "fitting"):
                if (root / name).exists():
                    shutil.rmtree(root / name)
                if (backup / name).is_dir():
                    shutil.copytree(backup / name, root / name)
            result.error = f"{type(error).__name__}: {error}"
            result.upgraded.clear()
            return result
    elif steps:
        data_format = max(step[1] for step in steps)       # nothing to convert in a new folder
    marker = _read(marker_path)
    marker.update({"last_app_version": current_version, "data_format": max(data_format, 1)})
    history = marker.get("versions_used")
    history = history if isinstance(history, list) else []
    if not history or history[-1].get("version") != current_version:
        history.append({"version": current_version, "first_start": datetime.now().isoformat(timespec="seconds")})
    marker["versions_used"] = history[-20:]
    _write(marker_path, marker)
    return result


def whats_new_text(version: str, language: str, since: str | None = None) -> str:
    """Markdown for the What's New window (help/whats_new/<version>.<en|zh>.md).

    Every version after ``since`` up to ``version`` is included, newest first, so an update
    that skips a version still shows what that one brought. Without ``since`` all are listed.
    """
    from app.gui.help_content import help_directory

    folder = help_directory() / "whats_new"
    versions = sorted({path.name.split(".en.md")[0] for path in folder.glob("*.en.md")}, key=version_key,
                      reverse=True) if folder.is_dir() else []
    chosen = [v for v in versions if version_key(v) <= version_key(version)
              and (since is None or version_key(v) > version_key(since))]
    parts = []
    for item in chosen:
        for name in (f"{item}.{language}.md", f"{item}.en.md"):
            path = folder / name
            if path.is_file():
                body = path.read_text(encoding="utf-8").strip()
                parts.append(f"## v{item}\n\n{body}" if len(chosen) > 1 else body)
                break
    return "\n\n---\n\n".join(parts)
