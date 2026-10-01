"""
app/core/database_scanner.py — v0.8

Recursive HDF5 database/folder scanner. Completely separate from the
GUI (no Qt imports here at all - the GUI's QThread wrapper lives in
app/gui/database_scan_worker.py and just calls DatabaseScanner.scan()
with a plain progress callback).

Purpose: build a lightweight Browser tree (file path, log name,
validity) WITHOUT loading any measurement data. Each candidate file is
opened just long enough to run it through the existing
HDF5Reader -> AutoDetector -> Experiment pipeline (reused as-is, not
reimplemented) to read its log_name, then immediately closed — this
never touches a vector/trace dataset (those are only read lazily via
Experiment.get_data()/get_full_nd_array(), which the scanner never
calls), so scanning even a folder of 80MB+ files stays fast and
memory-light.

A single corrupted/unsupported file must never abort the whole scan —
every file is wrapped in its own try/except and recorded as a
LogEntry with status="error" instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import h5py

from app.core.data_identity import stable_data_identity
from app.core.database_index_store import DatabaseIndexStore
from app.core.hdf5_reader import HDF5ReadError, HDF5Reader
from app.core.labber_parser import AutoDetector, UnsupportedLabberFormat

HDF5_EXTENSIONS = (".hdf5", ".h5")

ProgressCallback = Callable[[int, int], None]


@dataclass
class LogEntry:
    """One discovered HDF5 file - either successfully identified as a
    Labber log (status='ok') or not (status='error', with a reason)."""
    absolute_path: str
    relative_path: str       # POSIX-style, relative to the scanned root
    file_name: str
    log_name: str            # from the parsed Experiment, or the
                               # filename stem as a fallback when
                               # parsing fails
    status: str               # "ok" | "error"
    error_message: str | None
    size_bytes: int
    mtime: float
    creation_time: float | None = None
    sweep_dimension: str = ""
    metadata_complete: bool = True

    @property
    def folder_parts(self) -> tuple[str, ...]:
        """The relative_path's directory components, for building a
        nested folder/project tree in the GUI - e.g.
        'Experiment_A/sweep_01.hdf5' -> ('Experiment_A',)."""
        parent = Path(self.relative_path).parent
        return () if str(parent) in ("", ".") else parent.parts

    @property
    def display_name(self) -> str:
        """Human-facing name, deliberately distinct from ``data_identity``."""
        return self.log_name

    @property
    def data_identity(self) -> str:
        """Resolved source identity for non-database-scoped consumers."""
        return stable_data_identity(self.absolute_path)


@dataclass
class DatabaseScanResult:
    root_path: str
    database_id: str
    entries: list[LogEntry] = field(default_factory=list)

    @property
    def n_total(self) -> int:
        return len(self.entries)

    @property
    def n_ok(self) -> int:
        return sum(1 for e in self.entries if e.status == "ok")

    @property
    def n_error(self) -> int:
        return sum(1 for e in self.entries if e.status == "error")


class DatabaseScanner:
    """Stateless: every method is a pure function of its arguments, so
    it's trivially unit-testable and safe to call from a background
    QThread without any shared mutable state."""

    @staticmethod
    def compute_database_id(root_path: str | Path) -> str:
        """A stable identity for a scanned folder, used (together with
        each entry's relative_path) as the Star key — spec explicitly
        requires more than just a filename, since
        'Experiment_A/test.hdf5' and 'Experiment_B/test.hdf5' must not
        collide. Deliberately just the resolved absolute path (not a
        content hash) — spec explicitly says not to hash potentially
        huge files during a scan."""
        return str(Path(root_path).resolve())

    @staticmethod
    def find_hdf5_files(root_path: str | Path) -> list[Path]:
        """Recursive scan for *.hdf5 / *.h5, tolerant of permission
        errors on individual subdirectories (skips them rather than
        raising) and of the root not existing at all (returns [])."""
        root = Path(root_path)
        if not root.exists() or not root.is_dir():
            return []

        found: list[Path] = []

        def _on_error(exc: OSError) -> None:
            # a subdirectory couldn't be listed (permission denied,
            # race with deletion, etc.) - skip it, keep walking the rest
            pass

        for dirpath, dirnames, filenames in os.walk(root, onerror=_on_error):
            for name in filenames:
                if name.lower().endswith(HDF5_EXTENSIONS):
                    found.append(Path(dirpath) / name)

        return sorted(found, key=lambda p: str(p).lower())

    @staticmethod
    def scan(root_path: str | Path, progress_callback: ProgressCallback | None = None,
             index_store: DatabaseIndexStore | None = None, *, fast_listing: bool = False
              ) -> DatabaseScanResult:
        """Full scan: find every HDF5 file under root_path, then parse
        just enough of each to get its log name. `progress_callback`,
        if given, is called as (files_done, files_total) after each
        file - files_total is known up front since find_hdf5_files()
        completes before any parsing starts."""
        root = Path(root_path)
        database_id = DatabaseScanner.compute_database_id(root)
        files = DatabaseScanner.find_hdf5_files(root)

        entries: list[LogEntry] = []
        indexed_entries: dict[str, dict] = {}
        total = len(files)
        for i, file_path in enumerate(files):
            try:
                stat = file_path.stat()
                rel = file_path.relative_to(root).as_posix()
            except (OSError, ValueError):
                stat, rel = None, file_path.name
            cached = (index_store.get(database_id, rel, stat.st_size, stat.st_mtime)
                      if index_store is not None and stat is not None else None)
            if cached is not None:
                entry = LogEntry(**cached)
            elif fast_listing and stat is not None and h5py.is_hdf5(file_path):
                entry = LogEntry(
                    absolute_path=str(file_path), relative_path=rel, file_name=file_path.name,
                    log_name=file_path.stem, status="ok", error_message=None,
                    size_bytes=stat.st_size, mtime=stat.st_mtime, metadata_complete=False,
                )
            else:
                entry = DatabaseScanner._scan_one(root, file_path, stat=stat)
            entries.append(entry)
            if entry.metadata_complete:
                indexed_entries[entry.relative_path] = {
                "absolute_path": entry.absolute_path, "relative_path": entry.relative_path,
                "file_name": entry.file_name, "log_name": entry.log_name, "status": entry.status,
                "error_message": entry.error_message, "size_bytes": entry.size_bytes, "mtime": entry.mtime,
                "creation_time": entry.creation_time, "sweep_dimension": entry.sweep_dimension,
                "metadata_complete": True,
                }
            if progress_callback is not None:
                progress_callback(i + 1, total)
        if index_store is not None and not fast_listing:
            index_store.replace_root(database_id, indexed_entries)
        return DatabaseScanResult(root_path=str(root), database_id=database_id, entries=entries)

    @staticmethod
    def _scan_one(root: Path, file_path: Path, stat=None) -> LogEntry:
        try:
            rel = file_path.relative_to(root).as_posix()
        except ValueError:
            rel = file_path.name  # defensive: shouldn't happen given how files were found

        try:
            stat = stat or file_path.stat()
            size_bytes, mtime = stat.st_size, stat.st_mtime
        except OSError as e:
            # file vanished between find_hdf5_files() and here, or a
            # permission problem - report it, don't crash the scan
            return LogEntry(
                absolute_path=str(file_path), relative_path=rel, file_name=file_path.name,
                log_name=file_path.stem, status="error",
                error_message=f"Cannot access file: {e}", size_bytes=0, mtime=0.0,
            )

        reader: HDF5Reader | None = None
        try:
            reader = HDF5Reader(file_path)
            experiment = AutoDetector.detect_and_parse(reader)
            try:
                log_name = experiment.log_name or file_path.stem
                creation_time = experiment.creation_time
                dimensions = experiment.total_dimensions
                detail = experiment.sweep_dimension_summary()
                sizes = " × ".join(str(size) for size in detail.values())
                sweep_dimension = f"{dimensions}D" + (f" · {sizes}" if sizes else "")
            finally:
                experiment.close()  # closes `reader` too
            return LogEntry(
                absolute_path=str(file_path), relative_path=rel, file_name=file_path.name,
                log_name=log_name, status="ok", error_message=None,
                size_bytes=size_bytes, mtime=mtime,
                creation_time=creation_time, sweep_dimension=sweep_dimension,
            )
        except (HDF5ReadError, UnsupportedLabberFormat) as e:
            if reader is not None:
                reader.close()
            return LogEntry(
                absolute_path=str(file_path), relative_path=rel, file_name=file_path.name,
                log_name=file_path.stem, status="error", error_message=str(e),
                size_bytes=size_bytes, mtime=mtime,
            )
        except Exception as e:  # defensive: never let one bad file abort the whole scan
            if reader is not None:
                reader.close()
            return LogEntry(
                absolute_path=str(file_path), relative_path=rel, file_name=file_path.name,
                log_name=file_path.stem, status="error",
                error_message=f"Unexpected error: {e}", size_bytes=size_bytes, mtime=mtime,
            )
