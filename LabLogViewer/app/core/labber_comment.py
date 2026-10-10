"""Conservative access to Labber's confirmed root ``comment`` attribute.

The normal reader remains read-only.  This module is the single, narrowly
scoped exception used by the Browser's Comment editor after inspecting the
actual Labber representation: a string attribute named ``comment`` on the
file root.  It never creates attributes or groups and never touches payload
datasets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

import h5py
import numpy as np

from app.core.file_lock import FileBusyError, file_lock
from app.core.win_paths import long_path


ROOT_COMMENT_ATTRIBUTE = "comment"


class NativeCommentError(RuntimeError):
    """A native comment cannot safely be read or written."""


@dataclass(frozen=True)
class NativeComment:
    text: str
    supported: bool
    encoding: str | None = None
    variable_length: bool = False
    reason: str | None = None


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, np.bytes_):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, (str, np.str_)):
        return str(value)
    raise NativeCommentError("The root comment attribute is not a string.")


def read_native_comment(path: str | Path) -> NativeComment:
    """Read the confirmed Labber root attribute without changing the file."""
    source = Path(path)
    for attempt in range(4):
        try:
            return _read_native_comment_once(source)
        except OSError as error:        # another handle in this process may hold the file for a moment
            if attempt == 3:
                return NativeComment("", False, reason=str(error))
            time.sleep(0.1 * 2 ** attempt)
        except (ValueError, NativeCommentError) as error:
            return NativeComment("", False, reason=str(error))
    return NativeComment("", False, reason="unreadable")


def _read_native_comment_once(source: Path) -> NativeComment:
    with h5py.File(long_path(source), "r", locking=False) as file:
        if ROOT_COMMENT_ATTRIBUTE not in file.attrs:
            return NativeComment("", False, reason="No root 'comment' attribute.")
        attribute_id = file.attrs.get_id(ROOT_COMMENT_ATTRIBUTE)
        string_info = h5py.check_string_dtype(attribute_id.dtype)
        if string_info is None:
            return NativeComment("", False, reason="Root 'comment' is not a string attribute.")
        value = _as_text(file.attrs[ROOT_COMMENT_ATTRIBUTE])
        return NativeComment(
            value,
            True,
            encoding=string_info.encoding,
            variable_length=string_info.length is None,
        )


def _write_comment_once(source: Path, text: str) -> None:
    with h5py.File(long_path(source), "r+") as file:
        if ROOT_COMMENT_ATTRIBUTE not in file.attrs:
            raise NativeCommentError("No confirmed root 'comment' attribute.")
        attribute_id = file.attrs.get_id(ROOT_COMMENT_ATTRIBUTE)
        string_info = h5py.check_string_dtype(attribute_id.dtype)
        if string_info is None:
            raise NativeCommentError("Root 'comment' is not a writable string attribute.")
        encoding = string_info.encoding or "utf-8"
        if string_info.length is not None:
            try:
                encoded = text.encode(encoding)
            except UnicodeEncodeError as error:
                raise NativeCommentError(f"Comment cannot be encoded as {encoding}.") from error
            if len(encoded) > string_info.length:
                raise NativeCommentError("Comment does not fit the fixed-length native attribute.")
        # modify preserves the existing HDF5 attribute datatype and shape.
        file.attrs.modify(ROOT_COMMENT_ATTRIBUTE, text)
        file.flush()


def write_native_comment(path: str | Path, text: str) -> NativeComment:
    """Replace only an existing, verified native Labber Comment attribute.

    Fixed-length encodings are accepted only when the text fits exactly.  No
    attribute is created for an unknown representation, which keeps legacy
    variants safely on external Comment storage.
    """
    source = Path(path)
    if not isinstance(text, str):
        raise NativeCommentError("Comment text must be a string.")
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with file_lock(source, wait=3.0):
                _write_comment_once(source, text)
            last_error = None
            break
        except FileBusyError as error:
            raise NativeCommentError("The file is in use by another device; try again later.") from error
        except NativeCommentError:
            raise
        except (ValueError, TypeError) as error:
            raise NativeCommentError(str(error)) from error
        except OSError as error:        # a background reader may hold the file for a moment
            last_error = error
            time.sleep(0.15 * 2 ** attempt)
    if last_error is not None:
        raise NativeCommentError(str(last_error)) from last_error
    confirmed = read_native_comment(source)
    if not confirmed.supported or confirmed.text != text:
        raise NativeCommentError("Native Comment verification after reopen failed.")
    return confirmed
