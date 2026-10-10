"""Tags stored inside the HDF5 file, in Labber's own place: the ``Tags`` attribute of the ``/Tags`` group.

Only tag NAMES live in the file (categories stay in the local settings). Writing is deliberately
careful because the file may sit on a shared drive and Labber may use it:

* the file is opened for writing only for the few milliseconds of the change, and an HDF5 lock held
  by another program (Labber measuring, someone else saving) makes the write fail cleanly, with
  nothing changed;
* the tags already in the file are read again inside that same open, so tags Labber or another person
  added a moment ago are kept; only the names the user removed here are removed;
* a file that already holds exactly the wanted tags is not touched at all;
* the result is read back from a fresh open and compared before success is reported.

Lab Control xarray/NetCDF files (.nc, schema "labcontrol-nc/…", v2.1.0) keep their tags in the root
attribute ``tags`` (one comma-separated string, read by xarray as ``ds.attrs["tags"]``) instead of a
``/Tags`` group, so the file stays a clean NetCDF dataset. Reading and writing follow the same rules.
"""

from __future__ import annotations

import time
from contextlib import nullcontext
from pathlib import Path
from typing import Iterable

import h5py
import numpy as np

from app.core.file_lock import FileBusyError, file_lock
from app.core.win_paths import long_path

GROUP = "Tags"
ATTRIBUTE = "Tags"


class NativeTagError(RuntimeError):
    """The tags could not be written to (or read from) the file; the file was left unchanged."""

    def __init__(self, message: str, locked: bool = False):
        super().__init__(message)
        self.locked = locked


def _names(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, np.bytes_, np.str_)):
        value = [value]
    result: list[str] = []
    for item in np.asarray(value, dtype=object).ravel().tolist():
        text = item.decode("utf-8", errors="replace") if isinstance(item, (bytes, np.bytes_)) else str(item)
        text = text.strip()
        if text and text not in result:
            result.append(text)
    return result


NC_ATTRIBUTE = "tags"


def is_labcontrol_nc_file(file: h5py.File) -> bool:
    """A Lab Control .nc file (tags / star live in root attributes, see the module docstring)."""
    from app.core.labcontrol_nc import is_labcontrol_nc

    return is_labcontrol_nc(file)


def _nc_names(value) -> list[str]:
    if isinstance(value, np.ndarray):
        value = value.reshape(-1)[0] if value.size else ""
    if isinstance(value, (bytes, np.bytes_)):
        value = value.decode("utf-8", errors="replace")
    return _names([part for part in str(value or "").split(",")])


def _read_tags_checked(path: str | Path, attempts: int = 4) -> list[str] | None:
    """Like read_native_tags, but an unreadable file raises NativeTagError instead of looking like "no tags".
    Opened with locking off, like the app's own readers: another handle of this process may be open on the file."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with h5py.File(long_path(Path(path)), "r", locking=False) as file:
                if is_labcontrol_nc_file(file):
                    if NC_ATTRIBUTE not in file.attrs:
                        return None
                    return _nc_names(file.attrs[NC_ATTRIBUTE])
                group = file.get(GROUP)
                if not isinstance(group, h5py.Group) or ATTRIBUTE not in group.attrs:
                    return None
                return _names(group.attrs[ATTRIBUTE])
        except (OSError, ValueError, TypeError) as error:
            last = error
            if attempt < attempts - 1:
                time.sleep(0.3 * (attempt + 1))
    raise NativeTagError(f"The file cannot be read ({last}).", locked=True) from last


def read_native_tags(path: str | Path) -> list[str] | None:
    """The tag names in the file, or None when the file has no ``/Tags`` group or cannot be read."""
    try:
        return _read_tags_checked(path, attempts=1)
    except NativeTagError:
        return None


def _open_for_write(source: Path, attempts: int = 3) -> h5py.File:
    last: OSError | None = None
    for attempt in range(attempts):
        try:
            return h5py.File(long_path(source), "r+")
        except OSError as error:
            last = error
            if attempt < attempts - 1:
                time.sleep(0.4 * (attempt + 1))
    text = str(last)
    locked = any(word in text.lower() for word in ("lock", "unable to open", "resource temporarily", "busy", "permission"))
    raise NativeTagError(f"The file cannot be opened for writing ({text}). It may be in use by Labber "
                         f"or another program, or on a read-only drive.", locked=locked) from last


def write_native_tags(path: str | Path, wanted: Iterable[str], removed: Iterable[str] = (),
                      attempts: int = 3, use_lock: bool = True) -> list[str]:
    """Make the file's tags contain ``wanted``; names in ``removed`` that are not wanted are dropped.

    Every other tag already in the file is kept. Returns the tags now in the file. Raises
    NativeTagError (file unchanged) when it cannot be done.
    """
    source = Path(path)
    wanted_list = _names(list(wanted))
    drop = set(_names(list(removed))) - set(wanted_list)
    current = _read_tags_checked(source)           # opening for writing alone changes the file: look first
    if current is not None and [n for n in current if n not in drop] + \
            [n for n in wanted_list if n not in current] == current:
        return current
    if current is None and not wanted_list:
        return []
    try:
        with (file_lock(source) if use_lock else nullcontext()):
            result = _write_locked(source, wanted_list, drop, attempts)
    except FileBusyError as error:
        raise NativeTagError(str(error), locked=True) from error
    confirmed = _read_tags_checked(source)
    if confirmed is None or confirmed != result:
        raise NativeTagError("The tags in the file could not be confirmed after writing.")
    return confirmed


def _write_locked(source: Path, wanted_list: list[str], drop: set[str], attempts: int) -> list[str]:
    file = _open_for_write(source, attempts)
    try:
        if is_labcontrol_nc_file(file):
            existing = _nc_names(file.attrs[NC_ATTRIBUTE]) if NC_ATTRIBUTE in file.attrs else []
            result = [name for name in existing if name not in drop] + [n for n in wanted_list if n not in existing]
            if result == existing:
                return existing
            file.attrs[NC_ATTRIBUTE] = ", ".join(result)       # a plain string attribute, as Lab Control writes it
            file.flush()
            return result
        group = file.get(GROUP)
        existing: list[str] = []
        if isinstance(group, h5py.Group) and ATTRIBUTE in group.attrs:
            existing = _names(group.attrs[ATTRIBUTE])
        result = [name for name in existing if name not in drop] + [n for n in wanted_list if n not in existing]
        if result == existing and (isinstance(group, h5py.Group) or not result):
            return existing
        if group is None:
            group = file.require_group(GROUP)
        elif not isinstance(group, h5py.Group):
            raise NativeTagError("The file has a dataset named 'Tags' where a group is expected.")
        group.attrs.create(ATTRIBUTE, np.array(result, dtype=object), dtype=h5py.string_dtype())
        file.flush()
    except NativeTagError:
        raise
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        raise NativeTagError(f"Writing the tags failed ({error}).") from error
    finally:
        try:
            file.close()
        except Exception:
            pass
    return result
