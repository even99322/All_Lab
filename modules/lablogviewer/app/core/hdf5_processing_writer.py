"""Narrow HDF5 copy-and-patch primitives for scientific processing.

The normal HDF5Reader remains read-only. This module is the isolated writer
used by processing services: it copies a source file to a sibling temporary
file and permits updates only to one already-existing complex trace dataset.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
import shutil
import tempfile
from typing import Callable

import h5py

from app.core.win_paths import long_path
import numpy as np


class HDF5ProcessingWriteError(RuntimeError):
    """The requested narrow HDF5 update cannot be performed safely."""


def copy_to_temporary(
    source: str | Path,
    destination_directory: str | Path,
    output_name: str,
    *,
    progress: Callable[[int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    chunk_bytes: int = 8 * 1024 * 1024,
) -> Path:
    """Copy a closed Labber source to a unique temporary sibling file."""
    source = Path(source)
    directory = Path(destination_directory)
    fd, raw_path = tempfile.mkstemp(
        prefix=f".{output_name}.", suffix=".tmp", dir=directory,
    )
    os.close(fd)
    temporary = Path(raw_path)
    try:
        total = max(source.stat().st_size, 1)
        copied = 0
        with source.open("rb") as source_file, temporary.open("wb") as output_file:
            while True:
                if cancelled is not None and cancelled():
                    raise InterruptedError("De-background was cancelled while copying the Target file.")
                block = source_file.read(chunk_bytes)
                if not block:
                    break
                output_file.write(block)
                copied += len(block)
                if progress is not None:
                    progress(min(25, int(copied * 25 / total)))
            output_file.flush()
            os.fsync(output_file.fileno())
        shutil.copystat(source, temporary)
        if progress is not None:
            progress(25)
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


class HDF5ComplexTraceWriter:
    """Write real/imag components into an existing Labber trace in place."""

    def __init__(self, path: str | Path, dataset_path: str, expected_shape: tuple[int, int, int]):
        self.path = Path(path)
        self.dataset_path = dataset_path.lstrip("/")
        self.expected_shape = expected_shape
        self._file: h5py.File | None = None
        self._dataset: h5py.Dataset | None = None

    def __enter__(self) -> "HDF5ComplexTraceWriter":
        try:
            self._file = h5py.File(long_path(self.path), "r+")
            if self.dataset_path not in self._file:
                raise HDF5ProcessingWriteError(
                    f"Target trace dataset is missing from the copied file: /{self.dataset_path}"
                )
            dataset = self._file[self.dataset_path]
            if not isinstance(dataset, h5py.Dataset):
                raise HDF5ProcessingWriteError("The selected trace path is not a dataset.")
            if dataset.shape != self.expected_shape:
                raise HDF5ProcessingWriteError(
                    f"Trace layout changed during processing: expected {self.expected_shape}, got {dataset.shape}."
                )
            if dataset.dtype.kind != "f" or not bool(dataset.attrs.get("complex", False)):
                raise HDF5ProcessingWriteError(
                    "The selected trace no longer has the confirmed Labber real/imag complex representation."
                )
            self._dataset = dataset
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def write_entries(self, start: int, stop: int, real: np.ndarray, imag: np.ndarray) -> None:
        dataset = self._dataset
        if dataset is None:
            raise HDF5ProcessingWriteError("The trace writer is not open.")
        expected = (self.expected_shape[0], stop - start)
        if real.shape != expected or imag.shape != expected:
            raise HDF5ProcessingWriteError(
                f"Processed trace chunk has the wrong shape; expected {expected}."
            )
        dataset[:, 0, start:stop] = real
        dataset[:, 1, start:stop] = imag

    def flush(self) -> None:
        if self._file is not None:
            self._file.flush()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._dataset = None
        if self._file is not None:
            self._file.close()
            self._file = None


def publish_temporary(temporary: str | Path, output: str | Path, *, overwrite: bool) -> None:
    """Atomically publish a finished file, refusing implicit replacement."""
    temporary = Path(temporary)
    output = Path(output)
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    if overwrite:
        os.replace(temporary, output)
        return
    try:
        # Same-directory hard-link creation is atomic and fails if output
        # appeared after the caller's collision check; it never clobbers.
        os.link(temporary, output)
    except FileExistsError:
        raise
    except OSError:
        _publish_without_hard_link(temporary, output)
        return
    temporary.unlink()


def _publish_without_hard_link(temporary: Path, output: Path) -> None:
    """Network shares (SMB/NAS), FAT and exFAT have no hard links (errno 45 / ENOTSUP)."""
    # Windows os.rename never replaces an existing file; POSIX rename would,
    # so POSIX re-checks immediately before renaming.
    if os.path.lexists(output):
        raise FileExistsError(errno.EEXIST, "A file already exists", str(output))
    try:
        os.rename(temporary, output)
    except FileExistsError:
        raise
    except OSError as error:
        raise HDF5ProcessingWriteError(
            f"Could not safely publish without replacing an existing file: {error}"
        ) from error
