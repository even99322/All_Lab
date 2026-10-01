"""Move LabLogViewer history from an old data folder into a new one.

What it solves: records (stars, Tags, comments, Marks, views, 3D views, YIG
sessions ...) are keyed by where a measurement file *was*. When you switch to
another LabLogViewer (for example from a development folder to the released
app) and the measurement files now live somewhere else (for example the lab's
unified database), the records must follow the files.

Steps
1. ``open_folder``  reads a data folder (``LabLogViewerData`` or the pre-v0.18D
                    ``~/.lablogviewer``); nothing is written.
2. ``compare``      finds every file the old records point to inside the
                    folders where the files are now, *by content*:
                      exact      same SHA-256 as the fingerprint LabLogViewer kept
                                 (or, if the old file is still there, computed now)
                      guess      same file name and size, only one candidate
                                 (not ticked by default)
                      unchanged  the old path is already inside a new folder
                      not found  kept under its old path (nothing is lost)
3. ``migrate``      backs up the target folder, then merges: records the target
                    already has for the same file are kept (or replaced, if
                    chosen); lists (stars, trusted formulas ...) are united.
4. ``undo``         puts the backup back.

Measurement files are only read. The source folder is only read.
Used by the Data Transfer window (app/gui/migrate_dialog.py, licence feature "migrate"),
which closes the Viewers first and restarts LabLogViewer afterwards.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.core.record_merge import merge_record

MIGRATOR_FORMAT = 1
STATE, FITTING = "state", "fitting"
MARKER = "lablogviewer_data.json"
BACKUP_FOLDER = "migration_backups"
SUFFIXES = {".hdf5", ".h5"}
SAMPLE = 1024 * 1024

# record file -> key of the section keyed by the file's full path ("" = the whole document)
BY_IDENTITY = {
    "viewer_display_states.json": "by_data", "marks.json": "datasets", "overlays.json": "by_data",
    "named_views.json": "by_data", "axis_presets.json": "by_data", "comments.json": "by_data",
    "three_d_states.json": "", "data_fingerprints.json": "",
}
# record file -> sections keyed by database folder, then by path inside it
BY_DATABASE = {"stars.json": ("by_database",), "comments.json": ("by_database",),
               "tags.json": ("entry_states", "assignments")}
# schema versions this Migrator understands (the document's "schema_version")
KNOWN_SCHEMAS = {
    "stars.json": 1, "comments.json": 2, "marks.json": 1, "named_views.json": 1, "overlays.json": 1,
    "axis_presets.json": 2, "viewer_display_states.json": 1, "tags.json": 4, "transforms.json": 1,
    "settings.json": 1,
}
# rebuilt by LabLogViewer, or belong to one running session: never copied
SKIPPED_STATE = {"session.json", "session_lifecycle.json", "database_index.json"}
SKIPPED_ROOT = {STATE, FITTING, BACKUP_FOLDER, "relink_backups", MARKER, "README.txt", ".DS_Store"}
RECORD_LABELS = {
    "viewer_display_states.json": "view", "marks.json": "marks", "overlays.json": "overlays",
    "named_views.json": "named views", "axis_presets.json": "axis presets", "comments.json": "comment",
    "three_d_states.json": "3D view", "stars.json": "star", "tags.json": "tags", "session": "YIG session",
}


# records that do not depend on the measurement's channels or axes: the only ones that may
# follow a manual match to a clearly different file
SAFE_KINDS = frozenset({"star", "tags", "comment"})


class MigrationError(Exception):
    pass


# -- small helpers -------------------------------------------------------------------------------
def _read(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _write(path: Path, payload) -> None:
    """Atomic JSON write in LabLogViewer's own style."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _section(data, key):
    if not isinstance(data, dict):
        return None
    value = data if key == "" else data.get(key)
    return value if isinstance(value, dict) else None


def identity_of(path) -> str:
    """LabLogViewer's key for a local file (app/core/data_identity.stable_data_identity)."""
    return str(Path(path).expanduser().resolve())


def session_name(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".json"


def sampled_sha256(path, size: int | None = None) -> str:
    size = os.path.getsize(path) if size is None else size
    digest = hashlib.sha256(str(size).encode())
    with open(path, "rb") as stream:
        for offset in sorted({0, max(0, size // 2 - SAMPLE // 2), max(0, size - SAMPLE)}):
            stream.seek(offset)
            digest.update(stream.read(SAMPLE))
    return digest.hexdigest()


def full_sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _under(identity: str, roots: list[str]) -> str | None:
    """The (longest) root that contains ``identity``."""
    path = Path(identity)
    for root in sorted(roots, key=len, reverse=True):
        if path == Path(root) or Path(root) in path.parents:
            return root
    return None


def default_data_folder() -> Path:
    """Where LabLogViewer keeps its data on this computer (follows a moved folder)."""
    home = Path.home()
    if os.name == "nt":
        pointer = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        pointer = home / "Library" / "Application Support"
    else:
        pointer = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    moved = _read(pointer / "LabLogViewer" / "data_location.json")
    if isinstance(moved, dict) and isinstance(moved.get("path"), str) and Path(moved["path"]).is_dir():
        return Path(moved["path"])
    if os.name == "nt":
        return home / "LabLogViewerData"
    documents = home / "Documents" / "LabLogViewerData"
    # LabLogViewer stays out of a cloud-synced Documents folder and uses the home folder instead
    if not documents.is_dir() and (home / "LabLogViewerData").is_dir():
        return home / "LabLogViewerData"
    return documents


# -- data folders ---------------------------------------------------------------------------------
@dataclass
class DataFolder:
    root: Path
    state: Path
    fitting: Path
    kind: str                                   # "data" (v0.18D and later) or "legacy" (~/.lablogviewer)

    @property
    def sessions(self) -> Path:
        return self.fitting / "sessions"


def open_folder(path, create: bool = False) -> DataFolder:
    root = Path(path).expanduser()
    if (root / STATE).is_dir() or (root / MARKER).is_file() or (root / FITTING).is_dir():
        return DataFolder(root, root / STATE, root / FITTING, "data")
    if root.is_dir() and any((root / name).is_file() for name in (*BY_IDENTITY, *BY_DATABASE, "settings.json")):
        return DataFolder(root, root, root / FITTING, "legacy")
    if create and (not root.exists() or (root.is_dir() and not any(root.iterdir()))):
        return DataFolder(root, root / STATE, root / FITTING, "data")
    raise MigrationError(f"Not a LabLogViewer data folder: {root}")


@dataclass
class OldFile:
    identity: str
    records: set[str] = field(default_factory=set)       # labels of what is kept for it
    fingerprint: dict | None = None


def collect(folder: DataFolder) -> dict[str, OldFile]:
    """Every measurement file the folder's records point to (read only)."""
    found: dict[str, OldFile] = {}

    def note(identity, label):
        if isinstance(identity, str) and identity and "://" not in identity:
            found.setdefault(identity, OldFile(identity)).records.add(label)

    for name, key in BY_IDENTITY.items():
        if name == "data_fingerprints.json":
            continue
        for identity in _section(_read(folder.state / name), key) or {}:
            note(identity, RECORD_LABELS[name])
    for name, keys in BY_DATABASE.items():
        data = _read(folder.state / name)
        for key in keys:
            for database, entries in (_section(data, key) or {}).items():
                for relative in entries if isinstance(entries, (list, dict)) else []:
                    if isinstance(relative, str):
                        note(str(Path(database) / relative), RECORD_LABELS[name])
    for identity, path in _sessions(folder).items():
        note(identity, RECORD_LABELS["session"])
    prints = _section(_read(folder.state / "data_fingerprints.json"), "") or {}
    for identity, value in prints.items():
        if identity in found and isinstance(value, dict):
            found[identity].fingerprint = value
    return found


def _sessions(folder: DataFolder) -> dict[str, Path]:
    """YIG session files by the measurement they belong to."""
    out: dict[str, Path] = {}
    if not folder.sessions.is_dir():
        return out
    for path in folder.sessions.glob("*.json"):
        if len(path.stem) != 64:
            continue
        data = _read(path)
        source = data.get("data", {}).get("path") if isinstance(data, dict) and isinstance(data.get("data"), dict) else None
        if isinstance(source, str) and source:
            identity = source if session_name(source) == path.name else identity_of(source)
            out[identity] = path
    return out


def summary(folder: DataFolder) -> dict[str, int]:
    """How many files carry each kind of record (for the window)."""
    counts: dict[str, int] = {}
    for old in collect(folder).values():
        for label in old.records:
            counts[label] = counts.get(label, 0) + 1
    return counts


# -- compare ---------------------------------------------------------------------------------------
@dataclass
class Pair:
    old: OldFile
    status: str                                  # "unchanged" | "exact" | "guess" | "missing" | "manual"
    new_path: str | None = None
    selected: bool = True
    note: str = ""
    kinds: set[str] | None = None                # record kinds that follow (None = all)
    similarity: str = ""                         # manual matches: "same content" | "same layout" | "different"
    automatic: tuple | None = None               # what compare() found, restored by clear_manual()


@dataclass
class Plan:
    source: DataFolder
    target: DataFolder
    new_roots: list[str]
    pairs: list[Pair]

    def mapping(self, kind: str | None = None) -> dict[str, str]:
        """Old key -> new key for the ticked pairs; ``kind`` limits it to pairs that carry
        that record kind ("fingerprint": only files whose content is identical)."""
        out = {}
        for p in self.pairs:
            if not (p.selected and p.new_path and p.status in ("exact", "guess", "manual")):
                continue
            if kind == "fingerprint":
                if p.status != "exact" and p.similarity != "same content":
                    continue                     # a fingerprint describes the old content only
            elif kind is not None and p.kinds is not None and kind not in p.kinds:
                continue
            out[p.old.identity] = identity_of(p.new_path)
        return out

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for pair in self.pairs:
            out[pair.status] = out.get(pair.status, 0) + 1
        return out


def _walk(root: Path, cancel):
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if cancel is not None and cancel():
            return
        for name in names:
            if Path(name).suffix.lower() in SUFFIXES and not name.startswith("."):
                yield Path(folder) / name


def compare(source: DataFolder, target: DataFolder, new_roots, progress=None, cancel=None) -> Plan:
    """Find each old record's file in ``new_roots`` by content (files are only read)."""
    roots = [identity_of(r) for r in new_roots]
    old_files = sorted(collect(source).values(), key=lambda o: o.identity.casefold())
    files: list[Path] = []
    for root in roots:
        files.extend(_walk(Path(root), cancel))
    by_size: dict[int, list[Path]] = {}
    by_name: dict[str, list[Path]] = {}
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        by_size.setdefault(size, []).append(path)
        by_name.setdefault(path.name.casefold(), []).append(path)
    hashes: dict[Path, list[str]] = {}

    def hashed(path: Path, size: int, which: int) -> str:
        entry = hashes.setdefault(path, ["", ""])
        if not entry[which]:
            entry[which] = sampled_sha256(path, size) if which == 0 else full_sha256(path)
        return entry[which]

    pairs: list[Pair] = []
    total = len(old_files)
    for index, old in enumerate(old_files):
        if cancel is not None and cancel():
            break
        if progress is not None:
            progress(index, total)
        if _under(old.identity, roots) and os.path.isfile(old.identity):
            pairs.append(Pair(old, "unchanged", old.identity))
            continue
        known = dict(old.fingerprint or {})
        if not known.get("sha256") and os.path.isfile(old.identity):
            try:                                   # the old file is still here: fingerprint it now
                size = os.path.getsize(old.identity)
                known = {"size": size, "sample_sha256": sampled_sha256(old.identity, size),
                         "sha256": full_sha256(old.identity)}
                old.fingerprint = {**(old.fingerprint or {}), **known}
            except OSError:
                pass
        found: list[Path] = []
        size = known.get("size")
        if known.get("sha256") and isinstance(size, int):
            for path in by_size.get(size, []):
                try:
                    if known.get("sample_sha256") and hashed(path, size, 0) != known["sample_sha256"]:
                        continue
                    if hashed(path, size, 1) == known["sha256"]:
                        found.append(path)
                except OSError:
                    continue
        name = Path(old.identity).name.casefold()
        if found:
            found.sort(key=lambda p: (p.name.casefold() != name, str(p)))
            note = f"{len(found)} identical copies; the first is used" if len(found) > 1 else ""
            pairs.append(Pair(old, "exact", str(found[0]), True, note))
            continue
        candidates = [p for p in by_name.get(name, []) if not isinstance(size, int) or _size(p) == size]
        if len(candidates) == 1:
            pairs.append(Pair(old, "guess", str(candidates[0]), False,
                              "same name and size" if isinstance(size, int) else "same name"))
        else:
            note = f"{len(candidates)} files with this name" if candidates else ""
            pairs.append(Pair(old, "missing", None, False, note))
    if progress is not None:
        progress(total, total)
    return Plan(source, target, roots, pairs)


# -- manual matches ----------------------------------------------------------------------------------
def structure(path) -> dict | None:
    """What a measurement looks like, without its values: every dataset's shape and
    Labber's channel names. None when the file cannot be read."""
    try:
        import h5py
    except ImportError:
        return None
    try:
        shapes: dict[str, tuple] = {}
        with h5py.File(path, "r") as handle:
            handle.visititems(lambda name, obj: shapes.__setitem__(name, tuple(obj.shape))
                              if isinstance(obj, h5py.Dataset) else None)
            channels = []
            if "Data/Channel names" in handle:
                for row in handle["Data/Channel names"][()]:
                    value = row[0] if hasattr(row, "__len__") and not isinstance(row, (bytes, str)) else row
                    channels.append(value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value))
        return {"shapes": shapes, "channels": channels}
    except Exception:                              # not HDF5, damaged, locked ...
        return None


def compare_files(old: OldFile, new_path) -> tuple[str, str]:
    """How alike the old record's file and a file chosen by hand are:
    ("same content" | "same layout" | "different", reason)."""
    new_path = Path(new_path)
    known = old.fingerprint or {}
    old_exists = os.path.isfile(old.identity)
    try:
        new_size = new_path.stat().st_size
    except OSError as error:
        raise MigrationError(f"Cannot read {new_path}: {error}") from error
    sha = known.get("sha256")
    if not sha and old_exists:
        sha = full_sha256(old.identity)
    if sha and known.get("size", new_size) == new_size and full_sha256(new_path) == sha:
        return "same content", "identical content (SHA-256)"
    if not old_exists:
        return "different", ("the old file no longer exists, so only its fingerprint could be compared, "
                             "and the content differs" if sha else
                             "the old file no longer exists and has no fingerprint; nothing could be compared")
    before, after = structure(old.identity), structure(new_path)
    if before is None or after is None:
        return "different", "one of the files cannot be read as HDF5"
    if before == after:
        return "same layout", "same channels and sweep sizes; the measured values differ"
    reasons = []
    if before["channels"] != after["channels"]:
        reasons.append("different channels")
    if set(before["shapes"]) != set(after["shapes"]):
        reasons.append("different data sets")
    elif before["shapes"] != after["shapes"]:
        reasons.append("different sweep sizes")
    return "different", ", ".join(reasons) or "different layout"


def set_manual(pair: Pair, new_path, kinds=None) -> Pair:
    """Point an old record's file at a file chosen by hand. For clearly different files only
    stars, Tags and comments may follow (the others depend on channels and axes)."""
    similarity, reason = compare_files(pair.old, new_path)
    wanted = set(pair.old.records if kinds is None else kinds)
    if similarity == "different":
        if kinds is None:
            wanted &= SAFE_KINDS
        elif wanted - SAFE_KINDS:
            raise MigrationError("Only stars, Tags and comments can follow a file that differs this much.")
    if pair.automatic is None:
        pair.automatic = (pair.status, pair.new_path, pair.selected, pair.note)
    pair.status, pair.new_path, pair.selected = "manual", str(new_path), True
    pair.similarity, pair.note, pair.kinds = similarity, reason, wanted
    return pair


def clear_manual(pair: Pair) -> Pair:
    if pair.automatic is not None:
        pair.status, pair.new_path, pair.selected, pair.note = pair.automatic
        pair.automatic, pair.kinds, pair.similarity = None, None, ""
    return pair


def _size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


# -- merge -----------------------------------------------------------------------------------------
@dataclass
class Options:
    prefer_old: bool = False                      # on a conflict, the old record replaces the target's
    keep_unmatched: bool = True                   # carry records whose file was not found (old path)
    settings: str = "fill"                        # "fill" missing settings, "old" = take the old ones, "skip"


@dataclass
class Report:
    backup: Path
    added: dict[str, int] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    moved_files: int = 0
    created: list[str] = field(default_factory=list)

    def count(self, label: str, amount: int = 1) -> None:
        self.added[label] = self.added.get(label, 0) + amount


def _rekey_identity(identity: str, mapping: dict[str, str], keep: bool) -> str | None:
    if identity in mapping:
        return mapping[identity]
    return identity if keep else None


def _replace_strings(value, mapping: dict[str, str]):
    if isinstance(value, dict):
        return {key: _replace_strings(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_strings(item, mapping) for item in value]
    if isinstance(value, str) and value in mapping:
        return mapping[value]
    return value


def _merge_keyed(target: dict, incoming: dict, prefer_old: bool, report: Report, label: str,
                 record: tuple[str, str] | None = None) -> None:
    for key, value in incoming.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
            report.count(label)
        elif target[key] != value:
            if prefer_old:
                target[key] = copy.deepcopy(value)
                report.count(label)
                report.conflicts.append(f"{label}: {key} (old record used)")
                continue
            if record is not None:
                merged, conflict = merge_record(record[0], record[1], target[key], value)
                if not conflict:
                    if merged != target[key]:
                        target[key] = merged
                        report.count(label)
                    continue
            report.conflicts.append(f"{label}: {key} (kept the one already there)")


def _schema_ok(name: str, source, target, report: Report) -> bool:
    known = KNOWN_SCHEMAS.get(name)
    if known is None:
        return True
    versions = []
    for data in (source, target):
        if isinstance(data, dict):
            versions.append(data.get("schema_version", 1))
        elif isinstance(data, list):
            versions.append(0)                    # list-only documents from before schemas
    if any(not isinstance(v, int) or v > known for v in versions):
        report.skipped.append(f"{name}: written by a newer LabLogViewer than this Migrator knows")
        return False
    if len(versions) == 2 and versions[0] != versions[1]:
        report.skipped.append(f"{name}: the two folders use different formats "
                              "(open each once in the newest LabLogViewer, then migrate again)")
        return False
    return True


def _rekey_database(section: dict, mapping: dict[str, str], roots: list[str], keep: bool) -> dict:
    """{database: {relative: value} or [relative]} with each file moved to its new database."""
    out: dict = {}
    for database, entries in section.items():
        items = entries.items() if isinstance(entries, dict) else ((r, None) for r in entries or [])
        for relative, value in items:
            if not isinstance(relative, str):
                continue
            old = str(Path(database) / relative)
            new = mapping.get(old)
            if new is not None:
                new_root = _under(new, roots)
                if new_root is None:
                    continue
                where, rel = new_root, Path(new).relative_to(new_root).as_posix()
            elif keep:
                where, rel = database, relative
            else:
                continue
            if isinstance(entries, dict):
                out.setdefault(where, {})[rel] = value
            else:
                bucket = out.setdefault(where, [])
                if rel not in bucket:
                    bucket.append(rel)
    return out


def _merge_database(target: dict, incoming: dict, prefer_old: bool, report: Report, label: str,
                    record: tuple[str, str] | None = None) -> None:
    for database, entries in incoming.items():
        have = target.setdefault(database, [] if isinstance(entries, list) else {})
        if isinstance(entries, list) and isinstance(have, list):
            for rel in entries:
                if rel not in have:
                    have.append(rel)
                    report.count(label)
        elif isinstance(entries, dict) and isinstance(have, dict):
            _merge_keyed(have, entries, prefer_old, report, label, record)


def _merge_state_file(name: str, source: DataFolder, target: DataFolder, plan: Plan, options: Options,
                      report: Report) -> None:
    mapping = plan.mapping("fingerprint" if name == "data_fingerprints.json" else RECORD_LABELS.get(name))
    src, dst = _read(source.state / name), _read(target.state / name)
    if src is None:
        return
    if not _schema_ok(name, src, dst, report):
        return
    label = RECORD_LABELS.get(name, name)
    result = copy.deepcopy(dst) if dst is not None else None
    if result is None:                            # a fresh target keeps the source's layout
        result = copy.deepcopy(src)
        for key in (BY_IDENTITY.get(name),):
            if key is not None and _section(result, key) is not None:
                if key == "":
                    result = {}
                else:
                    result[key] = {}
        for key in BY_DATABASE.get(name, ()):
            if isinstance(result, dict) and isinstance(result.get(key), dict):
                result[key] = {}
    if name in BY_IDENTITY:
        key = BY_IDENTITY[name]
        incoming = {}
        for identity, value in (_section(src, key) or {}).items():
            new = _rekey_identity(identity, mapping, options.keep_unmatched)
            if new is not None:
                incoming[new] = _replace_strings(value, mapping)
        section = result if key == "" else result.setdefault(key, {})
        _merge_keyed(section, incoming, options.prefer_old, report, label, (name, key))
    for key in BY_DATABASE.get(name, ()):
        incoming = _rekey_database(_section(src, key) or {}, mapping, plan.new_roots, options.keep_unmatched)
        # "assignments" are derived from "entry_states", and comments keep a legacy copy
        # "by_database" next to "by_data": count each record once
        quiet = (name, key) in (("tags.json", "assignments"), ("comments.json", "by_database"))
        _merge_database(result.setdefault(key, {}), incoming, options.prefer_old,
                        Report(report.backup) if quiet else report, label, (name, key))
    if name == "tags.json":
        _merge_tag_lists(result, src)
    _write(target.state / name, result)


def _merge_tag_lists(result: dict, src: dict) -> None:
    tags = result.setdefault("available_tags", [])
    for tag in src.get("available_tags", []) or []:
        if tag not in tags:
            tags.append(tag)
    categories = result.setdefault("tag_categories", {})
    for tag, category in (src.get("tag_categories") or {}).items():
        if isinstance(category, str):             # {tag: category}, as tag_store writes it
            categories.setdefault(tag, category)
    groups = result.setdefault("group_defaults", {})
    for database, values in (src.get("group_defaults") or {}).items():
        if isinstance(values, dict):
            have = groups.setdefault(database, {})
            for group, value in values.items():
                have.setdefault(group, value)
    recent = result.setdefault("recent_queries", [])
    for query in src.get("recent_queries", []) or []:
        if query not in recent:
            recent.append(query)


def _merge_transforms(source: DataFolder, target: DataFolder, report: Report) -> None:
    src, dst = _read(source.state / "transforms.json"), _read(target.state / "transforms.json")
    if src is None or not _schema_ok("transforms.json", src, dst, report):
        return
    items = src if isinstance(src, list) else src.get("custom", [])
    result = dst if isinstance(dst, dict) else {"schema_version": 1, "custom": dst if isinstance(dst, list) else []}
    names = {item.get("name") for item in result.setdefault("custom", []) if isinstance(item, dict)}
    for item in items:
        if isinstance(item, dict) and item.get("name") not in names:
            result["custom"].append(item)
            names.add(item.get("name"))
            report.count("custom transform")
    _write(target.state / "transforms.json", result)


def _fill_missing(target: dict, source: dict) -> int:
    added = 0
    for key, value in source.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
            added += 1
        elif isinstance(target[key], dict) and isinstance(value, dict):
            added += _fill_missing(target[key], value)
    return added


def _merge_settings(source: DataFolder, target: DataFolder, options: Options, report: Report) -> None:
    src, dst = _read(source.state / "settings.json"), _read(target.state / "settings.json")
    if not isinstance(src, dict) or options.settings == "skip" or not _schema_ok("settings.json", src, dst, report):
        return
    if options.settings == "old" or not isinstance(dst, dict):
        _write(target.state / "settings.json", src)
        report.count("settings (all)")
        return
    added = _fill_missing(dst, src)
    if added:
        _write(target.state / "settings.json", dst)
        report.count("settings (missing values)", added)


def _merge_trusted(source: DataFolder, target: DataFolder, report: Report) -> None:
    src, dst = _read(source.state / "trusted_formulas.json"), _read(target.state / "trusted_formulas.json")
    if not isinstance(src, dict) or not isinstance(src.get("files"), dict):
        return
    result = dst if isinstance(dst, dict) and isinstance(dst.get("files"), dict) else {"version": 1, "files": {}}
    for digest, value in src["files"].items():
        if digest not in result["files"]:
            result["files"][digest] = value
            report.count("trusted formula")
    _write(target.state / "trusted_formulas.json", result)


def _copy_missing(source: Path, target: Path, report: Report, label: str, skip=()) -> None:
    """Copy files that the target does not have; a different file with the same name is kept."""
    if not source.exists():
        return
    if source.is_file():
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            report.created.append(str(target))
            report.count(label)
        elif target.read_bytes() != source.read_bytes():
            report.conflicts.append(f"{label}: {target.name} (kept the one already there)")
        return
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.name not in skip and path.name != ".DS_Store":
            _copy_missing(path, target / path.relative_to(source), report, label)


def _merge_sessions(plan: Plan, options: Options, report: Report) -> None:
    mapping = plan.mapping(RECORD_LABELS["session"])
    target = plan.target.sessions
    for identity, path in _sessions(plan.source).items():
        new = _rekey_identity(identity, mapping, options.keep_unmatched)
        if new is None:
            continue
        data = _read(path)
        if data is None:
            continue
        data = _replace_strings(data, {identity: new, os.path.realpath(identity): new})
        destination = target / session_name(new)
        if destination.exists() and not options.prefer_old:
            report.conflicts.append(f"YIG session: {new} (kept the one already there)")
            continue
        if not destination.exists():
            report.created.append(str(destination))
        _write(destination, data)
        report.count("YIG session")


def _merge_formula_library(plan: Plan, report: Report) -> None:
    src_dir, dst_dir = plan.source.sessions, plan.target.sessions
    src = _read(src_dir / "formula_library.json")
    if not isinstance(src, dict):
        return
    dst = _read(dst_dir / "formula_library.json")
    result = dst if isinstance(dst, dict) else {"version": 1, "options": src.get("options", {}), "formulas": []}
    entries = result.setdefault("formulas", [])
    ids = {e.get("id") for e in entries if isinstance(e, dict)}
    signatures = {(e.get("func"), _formula_bytes(dst_dir, e)) for e in entries if isinstance(e, dict)}
    for entry in src.get("formulas", []):
        if not isinstance(entry, dict):
            continue
        content = _formula_bytes(src_dir, entry)
        if (entry.get("func"), content) in signatures:
            continue                               # the same formula is already there
        entry = dict(entry)
        file = entry.get("file", "")
        if content is not None and file and not os.path.isabs(file):
            destination = dst_dir / file
            if destination.exists() and destination.read_bytes() != content:
                stem, suffix = os.path.splitext(file)
                file = f"{stem}_migrated{suffix}"
                destination = dst_dir / file
                entry["file"] = file
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                report.created.append(str(destination))
        if entry.get("id") in ids:
            entry["id"] = f"{entry.get('id')}-migrated"
        entries.append(entry)
        ids.add(entry.get("id"))
        report.count("library formula")
    _write(dst_dir / "formula_library.json", result)
    # formula files not in the library (for example the Formula Builder's) come along too
    _copy_missing(src_dir / "formulas", dst_dir / "formulas", report, "formula file")


def _formula_bytes(folder: Path, entry: dict) -> bytes | None:
    file = entry.get("file", "")
    path = Path(file) if os.path.isabs(file) else folder / file
    try:
        return path.read_bytes()
    except OSError:
        return None


# -- backup / migrate / undo -------------------------------------------------------------------------
def backups_dir(target: DataFolder) -> Path:
    return target.root / BACKUP_FOLDER


def _backup(target: DataFolder) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = backups_dir(target) / stamp
    number = 1
    while folder.exists():
        number += 1
        folder = backups_dir(target) / f"{stamp}-{number}"
    folder.mkdir(parents=True)
    existed = []
    if target.root.is_dir():
        for path in target.root.iterdir():
            if path.name in (BACKUP_FOLDER, "relink_backups", "received", "drag-share"):
                continue
            existed.append(path.name)
            if path.is_dir():
                shutil.copytree(path, folder / "files" / path.name)
            else:
                (folder / "files").mkdir(exist_ok=True)
                shutil.copy2(path, folder / "files" / path.name)
    _write(folder / "manifest.json", {"format": MIGRATOR_FORMAT, "created": datetime.now().isoformat(timespec="seconds"),
                                      "existed": existed})
    return folder


def migrate(plan: Plan, options: Options | None = None) -> Report:
    """Back up the target, then merge the source's records into it."""
    options = options or Options()
    source, target = plan.source, plan.target
    if source.root.resolve() == target.root.resolve():
        raise MigrationError("The old and the new data folder are the same folder.")
    target.root.mkdir(parents=True, exist_ok=True)
    report = Report(_backup(target))
    target.state.mkdir(parents=True, exist_ok=True)
    for name in sorted({*BY_IDENTITY, *BY_DATABASE}):
        _merge_state_file(name, source, target, plan, options, report)
    _merge_transforms(source, target, report)
    _merge_settings(source, target, options, report)
    _merge_trusted(source, target, report)
    handled = {*BY_IDENTITY, *BY_DATABASE, "transforms.json", "settings.json", "trusted_formulas.json"}
    if source.state.is_dir():
        for path in sorted(source.state.iterdir()):
            if path.name in handled or path.name in SKIPPED_STATE or path.name.startswith("."):
                continue
            if source.kind == "legacy" and path.name == FITTING:
                continue
            if path.name == "drag-share":
                continue                           # temporary files of drag and drop
            # personal colours / icons, experience, received files and anything newer
            _copy_missing(path, target.state / path.name, report, path.name)
    if source.kind == "data":
        for path in sorted(source.root.iterdir()):
            if path.name not in SKIPPED_ROOT and not path.name.startswith("."):
                _copy_missing(path, target.root / path.name, report, path.name)   # licences ...
    _merge_sessions(plan, options, report)
    _merge_formula_library(plan, report)
    if source.fitting.is_dir():
        for path in sorted(source.fitting.iterdir()):
            if path.name != "sessions":
                _copy_missing(path, target.fitting / path.name, report, "fitting " + path.name)
    if not (target.root / MARKER).exists() and target.kind == "data":
        _write(target.root / MARKER, {"created": datetime.now().isoformat(timespec="seconds"), "format": 1})
    report.moved_files = len(plan.mapping())
    _write(target.root / MARKER, {**(_read(target.root / MARKER) or {}), "migrations": [
        *((_read(target.root / MARKER) or {}).get("migrations", [])),
        {"when": datetime.now().isoformat(timespec="seconds"), "from": str(source.root),
         "backup": report.backup.name, "moved_files": report.moved_files}]})
    write_report(plan, options, report)
    return report


def write_report(plan: Plan, options: Options, report: Report) -> Path:
    lines = [f"# LabLogViewer data migration — {datetime.now():%Y-%m-%d %H:%M}", "",
             f"- Old data folder: {plan.source.root}", f"- New data folder: {plan.target.root}",
             f"- Where the files are now: {', '.join(plan.new_roots) or '(none)'}",
             f"- Backup: {report.backup}", f"- Files re-linked: {report.moved_files}",
             f"- Conflicts: {'old record used' if options.prefer_old else 'kept the new folder’s record'}", "",
             "## Added", *(f"- {k}: {v}" for k, v in sorted(report.added.items())), "",
             "## Files", "| Status | Old path | New path | Note |", "|---|---|---|---|"]
    for pair in plan.pairs:
        status = pair.status if pair.selected or pair.status in ("unchanged", "missing") else pair.status + " (not ticked)"
        if pair.status == "manual":
            status = f"manual, {pair.similarity}"
        lines.append(f"| {status} | {pair.old.identity} | {pair.new_path or ''} | {pair.note} |")
    manual = [p for p in plan.pairs if p.status == "manual" and p.selected]
    if manual:
        lines += ["", "## Manual matches (chosen by the user)",
                  "These files were paired by hand. Where they differ, the user accepted that the records "
                  "now belong to a different measurement and are the user's own responsibility.", ""]
        for pair in manual:
            lines.append(f"- {pair.old.identity} -> {pair.new_path}: {pair.similarity} ({pair.note}); "
                         f"moved: {', '.join(sorted(pair.kinds or pair.old.records))}")
    if report.conflicts:
        lines += ["", "## Conflicts", *(f"- {c}" for c in report.conflicts)]
    if report.skipped:
        lines += ["", "## Skipped", *(f"- {s}" for s in report.skipped)]
    path = report.backup / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def list_backups(target: DataFolder) -> list[Path]:
    folder = backups_dir(target)
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if (p / "manifest.json").is_file() and not (p / "undone").exists()),
                  reverse=True)


def undo(target: DataFolder, backup: Path) -> None:
    """Put the target folder back as it was before that migration."""
    manifest = _read(Path(backup) / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("format") != MIGRATOR_FORMAT:
        raise MigrationError("This is not a migration backup.")
    existed = set(manifest.get("existed", []))
    for path in list(target.root.iterdir()):
        if path.name in (BACKUP_FOLDER, "relink_backups", "received", "drag-share"):
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    files = Path(backup) / "files"
    for name in existed:
        source = files / name
        if source.is_dir():
            shutil.copytree(source, target.root / name)
        elif source.is_file():
            shutil.copy2(source, target.root / name)
    (Path(backup) / "undone").write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
