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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import h5py

from app.core.data_identity import stable_data_identity
from app.core.database_index_store import DatabaseIndexStore
from app.core.hdf5_reader import HDF5ReadError, HDF5Reader
from app.core.labber_parser import AutoDetector, UnsupportedLabberFormat

HDF5_EXTENSIONS = (".hdf5", ".h5", ".nc")   # .nc = Lab Control xarray/NetCDF (v2.1.0)
LISTING_THREADS = 8                         # folders listed at once on a network drive (v1.0.2)

ProgressCallback = Callable[[int, int], None]


def _tag_names(values) -> list[str]:
    result: list[str] = []
    for item in values if isinstance(values, (list, tuple)) else [values]:
        text = item.decode("utf-8", errors="replace") if isinstance(item, (bytes, bytearray)) else str(item)
        text = text.strip()
        if text and text not in result:
            result.append(text)
    return result


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
    file_tags: list[str] | None = None     # tag names stored in the file's /Tags group; None = not read
    file_star: bool | None = None          # the root attribute 'star' (missing = False); None = not read

    @property
    def folder_parts(self) -> tuple[str, ...]:
        """The relative_path's directory components, for building a
        nested folder/project tree in the GUI - e.g.
        'Experiment_A/sweep_01.hdf5' -> ('Experiment_A',).

        v1.0.2: remembered per relative_path (the Browser asks for every file at each folder change)."""
        cached = self.__dict__.get("_folder_parts")
        if cached is not None and cached[0] == self.relative_path:
            return cached[1]
        parent = Path(self.relative_path).parent
        parts = () if str(parent) in ("", ".") else parent.parts
        self.__dict__["_folder_parts"] = (self.relative_path, parts)
        return parts

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
    # v1.0.2: every folder under the root (relative parts, empty ones too) seen while listing, so the
    # Browser need not walk the whole share again on the GUI thread (seconds to minutes over VPN).
    folders: set[tuple[str, ...]] | None = None

    @property
    def n_total(self) -> int:
        return len(self.entries)

    @property
    def n_ok(self) -> int:
        return sum(1 for e in self.entries if e.status == "ok")

    @property
    def n_error(self) -> int:
        return sum(1 for e in self.entries if e.status == "error")


class ScanCancelled(Exception):
    """Raised inside a scan when the user cancelled it."""


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
    def find_hdf5_files(root_path: str | Path, should_cancel: Callable[[], bool] | None = None) -> list[Path]:
        """Recursive scan for *.hdf5 / *.h5, tolerant of permission
        errors on individual subdirectories (skips them rather than
        raising) and of the root not existing at all (returns [])."""
        return [path for path, _stat in DatabaseScanner.find_hdf5_files_with_stats(root_path, should_cancel)]

    @staticmethod
    def find_hdf5_files_with_stats(root_path: str | Path, should_cancel: Callable[[], bool] | None = None,
                                   folders_out: set[tuple[str, ...]] | None = None
                                   ) -> list[tuple[Path, os.stat_result | None]]:
        """Like ``find_hdf5_files`` with each file's size / time from the directory listing (v2.1.6).

        On Windows the listing already carries them, so a NAS folder of thousands of files needs no extra
        network request per file (one request per folder instead). ``folders_out`` collects every folder
        (relative parts; hidden ``.folders`` are not shown in the tree, as in ``change_watch``)."""
        root = Path(root_path)
        if not root.exists() or not root.is_dir():
            return []
        from app.core import net_cache

        found: list[tuple[Path, os.stat_result | None]] = []

        def take(folder: str, listed: tuple[list[str], list[tuple[Path, os.stat_result | None]]]) -> list[str]:
            dirs, files = listed
            found.extend(files)
            if folders_out is not None:
                for path in dirs:
                    try:
                        parts = Path(path).relative_to(root).parts
                    except ValueError:
                        parts = ()
                    if parts and not any(part.startswith(".") for part in parts):
                        folders_out.add(parts)
            return dirs

        if net_cache.is_network_path(root):
            # v1.0.2: over VPN every folder listing waits for the server — list several folders at once
            from concurrent.futures import ThreadPoolExecutor

            frontier = [str(root)]
            with ThreadPoolExecutor(max_workers=LISTING_THREADS, thread_name_prefix="llv-list") as pool:
                while frontier:
                    if should_cancel is not None and should_cancel():
                        raise ScanCancelled()
                    following: list[str] = []
                    for folder, listed in zip(frontier, pool.map(DatabaseScanner._list_folder, frontier)):
                        following.extend(take(folder, listed))
                    frontier = following
        else:
            pending = [str(root)]
            while pending:
                if should_cancel is not None and should_cancel():
                    raise ScanCancelled()
                folder = pending.pop()
                pending.extend(take(folder, DatabaseScanner._list_folder(folder)))
        return sorted(found, key=lambda pair: str(pair[0]).lower())

    @staticmethod
    def _list_folder(folder: str) -> tuple[list[str], list[tuple[Path, os.stat_result | None]]]:
        """(sub-folders, measurement files with their size / time) of one folder; ([], []) when it
        cannot be listed (skipped, the walk goes on)."""
        try:
            with os.scandir(folder) as listing:
                items = list(listing)
        except OSError:
            return [], []
        dirs: list[str] = []
        files: list[tuple[Path, os.stat_result | None]] = []
        for item in items:
            try:
                if item.is_dir(follow_symlinks=False) or (item.is_symlink() and item.is_dir()):
                    dirs.append(item.path)
                    continue
            except OSError:
                continue
            if not item.name.lower().endswith(HDF5_EXTENSIONS):
                continue
            try:
                stat = item.stat()
            except OSError:
                stat = None
            files.append((Path(item.path), stat))
        return dirs, files

    @staticmethod
    def _index_record(entry: LogEntry) -> dict:
        return {
            "absolute_path": entry.absolute_path, "relative_path": entry.relative_path,
            "file_name": entry.file_name, "log_name": entry.log_name, "status": entry.status,
            "error_message": entry.error_message, "size_bytes": entry.size_bytes, "mtime": entry.mtime,
            "creation_time": entry.creation_time, "sweep_dimension": entry.sweep_dimension,
            "metadata_complete": True, "file_tags": entry.file_tags, "file_star": entry.file_star,
        }

    @staticmethod
    def scan(root_path: str | Path, progress_callback: ProgressCallback | None = None,
             index_store: DatabaseIndexStore | None = None, *, fast_listing: bool = False,
             should_cancel: Callable[[], bool] | None = None,
             priority: Callable[[], tuple[str, ...] | None] | None = None,
             batch_callback: Callable[[list[LogEntry]], None] | None = None,
             remote_index: Callable[[], dict[str, dict] | None] | None = None) -> DatabaseScanResult:
        """Full scan: find every HDF5 file under root_path, then parse
        just enough of each to get its log name. `progress_callback`,
        if given, is called as (files_done, files_total) after each
        file - files_total is known up front since find_hdf5_files()
        completes before any parsing starts.

        v2.1.6 (large folders, NAS over VPN):
        * fast_listing only lists (no file is opened on a network drive);
        * ``priority()`` → the folder the user is looking at: its files are parsed first (asked again as
          parsing goes on, so switching folders re-prioritises);
        * ``batch_callback(entries)``: newly parsed entries in small batches, so the Browser fills its rows
          as they come instead of waiting for the whole folder tree;
        * parsing waits while a preview / Viewer is reading from the network (net_cache.busy).

        v1.0.3: ``remote_index()`` → the NAS index of this folder ({relative path: record}, see hub_index); it is
        asked while the folders are listed, and a file whose size / time match is taken from it, not opened."""
        from app.core import net_cache

        root = Path(root_path)
        database_id = DatabaseScanner.compute_database_id(root)
        folders: set[tuple[str, ...]] = set()
        remote_box: dict[str, dict | None] = {}
        remote_thread = None
        if remote_index is not None:
            import threading

            def ask_remote():
                try:
                    remote_box["records"] = remote_index()
                except Exception:
                    remote_box["records"] = None

            remote_thread = threading.Thread(target=ask_remote, name="llv-hub-index", daemon=True)
            remote_thread.start()
        listed = DatabaseScanner.find_hdf5_files_with_stats(root, should_cancel, folders)
        remote: dict[str, dict] | None = None
        if remote_thread is not None:
            remote_thread.join(30.0)
            remote = remote_box.get("records")
        network = net_cache.is_network_path(root)

        slots: list[LogEntry | None] = [None] * len(listed)
        todo: list[tuple[int, Path, os.stat_result | None, str]] = []
        indexed_entries: dict[str, dict] = {}
        total = len(listed)
        done = 0
        for i, (file_path, stat) in enumerate(listed):
            if should_cancel is not None and should_cancel():
                raise ScanCancelled()
            try:
                rel = file_path.relative_to(root).as_posix()
            except ValueError:
                rel = file_path.name
            cached = (index_store.get(database_id, rel, stat.st_size, stat.st_mtime)
                      if index_store is not None and stat is not None else None)
            if cached is not None and ("file_tags" not in cached or "file_star" not in cached):
                cached = None                       # indexed before tags were read: scan this file again
            if cached is None and remote and stat is not None:
                record = remote.get(rel)
                if record is not None and record.get("status") == "ok":
                    from app.core import hub_index

                    if hub_index.matches(record, stat.st_size, stat.st_mtime):
                        cached = hub_index.entry_fields(record, root, rel, stat)
            if cached is not None:
                cached["absolute_path"] = str(file_path)
                slots[i] = LogEntry(**cached)
            elif fast_listing and stat is not None:
                probe = True if network else DatabaseScanner._probe_hdf5(file_path)
                if probe is False:
                    slots[i] = DatabaseScanner._scan_one(root, file_path, stat=stat)
                elif isinstance(probe, OSError):
                    slots[i] = LogEntry(
                        absolute_path=str(file_path), relative_path=rel, file_name=file_path.name,
                        log_name=file_path.stem, status="error",
                        error_message=f"Cannot access file: {probe}",
                        size_bytes=stat.st_size, mtime=stat.st_mtime,
                    )
                else:
                    slots[i] = LogEntry(
                        absolute_path=str(file_path), relative_path=rel, file_name=file_path.name,
                        log_name=file_path.stem, status="ok", error_message=None,
                        size_bytes=stat.st_size, mtime=stat.st_mtime, metadata_complete=False,
                    )
            else:
                todo.append((i, file_path, stat, rel))
                continue
            done += 1
            if progress_callback is not None:
                progress_callback(done, total)

        batch: list[LogEntry] = []
        last_flush = time.monotonic()
        last_store = time.monotonic()
        from collections import OrderedDict, deque

        groups: "OrderedDict[tuple[str, ...], deque]" = OrderedDict()
        for item in todo:
            parent = Path(item[3]).parent
            groups.setdefault(() if str(parent) in ("", ".") else parent.parts, deque()).append(item)
        while groups:
            if should_cancel is not None and should_cancel():
                raise ScanCancelled()
            wanted = priority() if priority is not None else None
            folder = tuple(wanted) if wanted is not None and tuple(wanted) in groups else next(iter(groups))
            queue = groups[folder]
            i, file_path, stat, _rel = queue.popleft()
            if not queue:
                del groups[folder]
            if network:
                net_cache.wait_idle(should_cancel)
            entry = DatabaseScanner._scan_one(root, file_path, stat=stat)
            slots[i] = entry
            batch.append(entry)
            done += 1
            if progress_callback is not None:
                progress_callback(done, total)
            now = time.monotonic()
            if batch_callback is not None and (len(batch) >= 40 or now - last_flush > 0.7):
                batch_callback(batch)
                batch, last_flush = [], now
            if index_store is not None and now - last_store > 15 and hasattr(index_store, "merge_root"):
                index_store.merge_root(database_id, {e.relative_path: DatabaseScanner._index_record(e)
                                                     for e in slots if e is not None and e.metadata_complete})
                last_store = now
        if batch_callback is not None and batch:
            batch_callback(batch)

        entries = [e for e in slots if e is not None]
        for entry in entries:
            if entry.metadata_complete:
                indexed_entries[entry.relative_path] = DatabaseScanner._index_record(entry)
        if index_store is not None and not fast_listing:
            index_store.replace_root(database_id, indexed_entries)
        return DatabaseScanResult(root_path=str(root), database_id=database_id, entries=entries, folders=folders)

    @staticmethod
    def _probe_hdf5(file_path: Path, attempts: int = 3):
        """True/False from ``h5py.is_hdf5``; the OSError itself when the file stays unreadable.

        A network drive can fail one file with an I/O error (errno 5); that must mark only
        this file, never abort the scan of the whole folder, so transient errors are retried."""
        error: OSError | None = None
        for attempt in range(attempts):
            try:
                return bool(h5py.is_hdf5(file_path))
            except OSError as exc:
                error = exc
                time.sleep(0.2 * (attempt + 1))
        return error

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
                raw_tags = getattr(experiment, "tags", None)
                file_tags = None if raw_tags is None else _tag_names(raw_tags)
                try:
                    file_star = bool(reader.get_attrs("/").get("star", False))
                except Exception:
                    file_star = None
            finally:
                experiment.close()  # closes `reader` too
            return LogEntry(
                absolute_path=str(file_path), relative_path=rel, file_name=file_path.name,
                log_name=log_name, status="ok", error_message=None,
                size_bytes=size_bytes, mtime=mtime,
                creation_time=creation_time, sweep_dimension=sweep_dimension, file_tags=file_tags,
                file_star=file_star,
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
