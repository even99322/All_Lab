"""Before anything is saved over an existing file: ask Overwrite / Keep Both / Cancel.

Every QFileDialog.getSaveFileName in the program passes through the wrapper in
app/localization/manager.py, which turns the system's own "Replace?" question off
and asks this one instead (so a shared folder on the NAS gets the same question).
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtWidgets import QMessageBox


def numbered(path: str | Path) -> Path:
    """``name (2).ext`` (then 3, 4, …): the first name not taken in the same folder."""
    path = Path(path)
    stem, suffix = path.stem, path.suffix
    match = re.fullmatch(r"(.*) \((\d+)\)", stem)
    base, index = (match.group(1), int(match.group(2)) + 1) if match else (stem, 2)
    while True:
        candidate = path.with_name(f"{base} ({index}){suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def suffix_from_filter(selected_filter: str) -> str:
    found = re.search(r"\*(\.[A-Za-z0-9]+)", selected_filter or "")
    return found.group(1) if found else ""


def _text(key: str) -> str:
    from app.localization import get_localization_manager

    return get_localization_manager().text(key)


def ask_existing(parent, path: str | Path, *, allow_cancel: bool = True) -> Path | None:
    """The path to write to: the same one (overwrite), a numbered one (keep both), or None (cancel)."""
    path = Path(path)
    if not path.exists():
        return path
    if path.is_dir():
        return numbered(path)                           # a folder is never replaced
    box = QMessageBox(QMessageBox.Icon.Warning, _text("save.exists_title"),
                      _text("save.exists_text").format(name=path.name, folder=str(path.parent)), parent=parent)
    keep = numbered(path)
    box.setInformativeText(_text("save.exists_info").format(new_name=keep.name))
    overwrite = box.addButton(_text("save.overwrite"), QMessageBox.ButtonRole.DestructiveRole)
    keep_both = box.addButton(_text("save.keep_both"), QMessageBox.ButtonRole.AcceptRole)
    if allow_cancel:
        box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(keep_both)
    box.exec()
    clicked = box.clickedButton()
    if clicked is overwrite:
        return path
    if clicked is keep_both:
        return keep
    return None


def confirm_save(parent, path: str, selected_filter: str = "") -> str:
    """For a name returned by a save dialog: add the chosen type's extension, then ask if taken."""
    if not path:
        return path
    target = Path(path)
    extension = suffix_from_filter(selected_filter)
    # "0908 4.9~5.1GHz X2" has no real extension although Path sees ".1GHz X2"
    if extension and not re.fullmatch(r"\.[A-Za-z0-9]{1,5}", target.suffix):
        target = target.with_name(target.name + extension)
    chosen = ask_existing(parent, target)
    return "" if chosen is None else str(chosen)


def ask_keep_both(parent, path: str | Path) -> Path | None:
    """A new name is taken where replacing is not offered (a folder, a measurement file):
    use the numbered name instead, or cancel."""
    path = Path(path)
    if not path.exists():
        return path
    keep = numbered(path)
    box = QMessageBox(QMessageBox.Icon.Warning, _text("save.exists_title"),
                      _text("save.exists_text").format(name=path.name, folder=str(path.parent)), parent=parent)
    box.setInformativeText(_text("save.exists_no_replace").format(new_name=keep.name))
    keep_both = box.addButton(_text("save.keep_both"), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(keep_both)
    box.exec()
    return keep if box.clickedButton() is keep_both else None
