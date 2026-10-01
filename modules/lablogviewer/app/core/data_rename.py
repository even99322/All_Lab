"""No-overwrite HDF5 filename rename plus external-state identity migration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from app.core.data_identity import stable_data_identity


class DataRenameError(ValueError):
    """A requested Data rename was unsafe or its external state could not move."""


def _rename_without_hard_link(source: Path, destination: Path) -> None:
    """Fallback for filesystems without hard links (FAT/exFAT, some network shares)."""
    # Windows os.rename never replaces an existing file; POSIX rename would,
    # so POSIX re-checks immediately before renaming.
    if os.name != "nt" and os.path.lexists(destination):
        raise DataRenameError(f"A file already exists at {destination}.")
    try:
        os.rename(source, destination)
    except FileExistsError as error:
        raise DataRenameError(f"A file already exists at {destination}.") from error
    except OSError as error:
        raise DataRenameError(f"Could not rename the file: {error}") from error


def _safe_move_without_overwrite(source: Path, destination: Path) -> None:
    """Move a same-directory file without a check/rename overwrite race."""
    try:
        os.link(source, destination)
    except FileExistsError as error:
        raise DataRenameError(f"A file already exists at {destination}.") from error
    except OSError:
        _rename_without_hard_link(source, destination)
        return
    try:
        source.unlink()
    except OSError:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _restore_original_name(source: Path, destination: Path) -> None:
    _safe_move_without_overwrite(destination, source)


def rename_hdf5_data(
    source_path: str | Path,
    new_stem: str,
    *,
    database_id: str,
    old_relative_path: str,
    state_stores: Iterable[object],
) -> Path:
    """Rename an HDF5 directory entry, then migrate its external state.

    Only a same-directory filename change is allowed. The HDF5 bytes are never
    opened or rewritten. Stores expose ``move_data_identity`` and receive both
    canonical identities and Browser-relative keys, allowing this transaction
    to preserve all existing ownership domains without a parallel state file.
    """
    source = Path(source_path).expanduser()
    if not source.exists() or not source.is_file():
        raise DataRenameError("The selected Data file is no longer available.")
    if source.is_symlink():
        raise DataRenameError("Renaming a symbolic link is not supported.")
    suffix = source.suffix
    if suffix.lower() not in {".hdf5", ".h5"}:
        raise DataRenameError("Only .hdf5 and .h5 Data files can be renamed.")
    stem = str(new_stem).strip()
    if stem.lower().endswith(suffix.lower()):
        stem = stem[:-len(suffix)].rstrip()
    if (not stem or stem in {".", ".."} or Path(stem).name != stem
            or "/" in stem or "\\" in stem
            or any(character in stem for character in '<>:"|?*')
            or any(ord(character) < 32 for character in stem)
            or stem.endswith((".", " "))):
        raise DataRenameError("Enter a valid filename without a folder path or extension.")
    windows_device_names = {
        "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    if stem.split(".", 1)[0].upper() in windows_device_names:
        raise DataRenameError("That filename is reserved by Windows.")
    if stem == source.stem:
        return source

    destination = source.with_name(f"{stem}{suffix}")
    if destination.exists():
        raise DataRenameError(f"A file already exists with that name:\n{destination.name}")
    try:
        new_relative_path = destination.relative_to(Path(database_id)).as_posix()
    except ValueError as error:
        raise DataRenameError("The selected file is not inside the active Database.") from error

    old_identity = stable_data_identity(source)
    new_identity = stable_data_identity(destination)
    _safe_move_without_overwrite(source, destination)
    migrated: list[object] = []
    try:
        for store in state_stores:
            move = getattr(store, "move_data_identity", None)
            if move is None:
                continue
            changed = move(
                old_identity,
                new_identity,
                database_id=database_id,
                old_relative_path=old_relative_path,
                new_relative_path=new_relative_path,
            )
            if changed:
                migrated.append(store)
    except Exception as error:
        rollback_errors: list[str] = []
        for store in reversed(migrated):
            try:
                store.move_data_identity(
                    new_identity,
                    old_identity,
                    database_id=database_id,
                    old_relative_path=new_relative_path,
                    new_relative_path=old_relative_path,
                )
            except Exception as rollback_error:
                rollback_errors.append(str(rollback_error))
        try:
            _restore_original_name(source, destination)
        except Exception as rollback_error:
            rollback_errors.append(str(rollback_error))
        detail = f"Rename was rolled back after state migration failed: {error}"
        if rollback_errors:
            detail += "\nRecovery needs attention: " + "; ".join(rollback_errors)
        raise DataRenameError(detail) from error
    return destination
