"""The star stored inside the HDF5 file, in Labber's own place: the root attribute ``star`` (an HDF5 enum
FALSE=0 / TRUE=1, exactly as Labber writes it).

Rules that protect the data:
* a file without a ``star`` attribute is never touched to say "not starred";
* the file is looked at read-only first and is not opened for writing when it already holds the wanted value;
* the change is written under the file's lock (app/core/file_lock.py), flushed and read back from a fresh open.

Lab Control .nc files (v2.1.0) get ``star`` as a plain integer 0/1 instead of the HDF5 enum, so the file stays
valid NetCDF (xarray reads it as ``ds.attrs["star"]``).
"""

from __future__ import annotations

from pathlib import Path

import time

import h5py
import numpy as np

from app.core.file_lock import FileBusyError, file_lock
from app.core.labber_tags import NativeTagError, _open_for_write
from app.core.win_paths import long_path

ATTRIBUTE = "star"


def _read_star_checked(path: str | Path, attempts: int = 4) -> bool | None:
    """Like read_native_star, but an unreadable file raises NativeTagError instead of looking like "no star".
    Opened with locking off, like the app's own readers: another handle of this process may be open on the file."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with h5py.File(long_path(Path(path)), "r", locking=False) as file:
                if ATTRIBUTE not in file.attrs:
                    return None
                return _as_bool(file.attrs[ATTRIBUTE])
        except (OSError, ValueError, TypeError) as error:
            last = error
            if attempt < attempts - 1:
                time.sleep(0.3 * (attempt + 1))
    raise NativeTagError(f"The file cannot be read ({last}).", locked=True) from last


def _as_bool(value) -> bool:
    value = np.asarray(value)
    return bool(value.reshape(-1)[0]) if value.size else False


def read_native_star(path: str | Path) -> bool | None:
    """True/False as stored in the file, or None when there is no ``star`` attribute or the file cannot be read."""
    try:
        return _read_star_checked(path, attempts=1)
    except NativeTagError:
        return None


def write_native_star(path: str | Path, starred: bool, attempts: int = 3, use_lock: bool = True) -> bool | None:
    """Make the file's ``star`` equal ``starred``. Returns the value now in the file (None: no attribute, and
    "not starred" was asked, so nothing was written). Raises NativeTagError, file unchanged, when it cannot be done."""
    source = Path(path)
    wanted = bool(starred)
    current = _read_star_checked(source)
    if current == wanted or (current is None and not wanted):
        return current
    try:
        if use_lock:
            with file_lock(source):
                _write(source, wanted, attempts)
        else:
            _write(source, wanted, attempts)
    except FileBusyError as error:
        raise NativeTagError(str(error), locked=True) from error
    confirmed = _read_star_checked(source)
    if confirmed != wanted:
        raise NativeTagError("The star in the file could not be confirmed after writing.")
    return confirmed


def _write(source: Path, wanted: bool, attempts: int) -> None:
    file = _open_for_write(source, attempts)
    try:
        if ATTRIBUTE in file.attrs and _as_bool(file.attrs[ATTRIBUTE]) == wanted:
            return                                           # another device wrote the same value meanwhile
        if ATTRIBUTE in file.attrs:
            del file.attrs[ATTRIBUTE]
        from app.core.labber_tags import is_labcontrol_nc_file

        if is_labcontrol_nc_file(file):
            file.attrs.create(ATTRIBUTE, np.int8(1 if wanted else 0), dtype=np.int8)
        else:
            file.attrs.create(ATTRIBUTE, np.bool_(wanted), dtype=np.bool_)
        file.flush()
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        raise NativeTagError(f"Writing the star failed ({error}).") from error
    finally:
        try:
            file.close()
        except Exception:
            pass
