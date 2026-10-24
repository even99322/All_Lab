"""Lossless labcontrol-nc/1 grid -> Labber data-log converter and database merge.

No Labber installation is required. Native Labber UI compatibility still needs
to be checked on the target Labber version; numerical read-back is automatic.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
import io
import errno
import hashlib
import json
import os
import re
import socket
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile
import uuid

import h5py
import numpy as np

from .labber_format import TEXT, CHANNEL_NAME, rows, write_configuration, write_view
from .safe_io import (Cancelled, SourceBusy, RetryPolicy, retry_io, identity, stable_source,
                     require_unchanged, sha256, verified_copy, explicitly_active, atomic_json, pause)

VERSION = "0.4.2"
LOCAL_TZ = timezone(timedelta(hours=8))
EXTENSIONS = {".nc", ".hdf5", ".h5"}


class ConversionError(ValueError):
    pass


class DatabaseBusy(ConversionError):
    pass


@dataclass(frozen=True)
class Source:
    path: Path
    member: str | None = None
    folder_parts: tuple[str, ...] = ()

    @property
    def name(self):
        return PurePosixPath(self.member.replace("\\", "/")).name if self.member else self.path.name

    @property
    def label(self):
        return f"{self.path} :: {self.member}" if self.member else str(self.path)

    @property
    def folders(self):
        if self.folder_parts:
            return self.folder_parts
        if self.member:
            parts = PurePosixPath(self.member.replace("\\", "/")).parent.parts
            return parts or (self.path.stem,)
        return (self.path.parent.name,) if self.path.parent.name else ()


def native_folder_context(parts):
    for index in range(len(parts)-1):
        if re.fullmatch(r"\d{4}",parts[index]) and re.fullmatch(r"0[1-9]|1[0-2]",parts[index+1]):
            return tuple(parts[index:])
    return ()


def discover(paths):
    """Keep the native date tree, or the selected source folder and subfolders."""
    found = []
    seen = set()
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            raise ConversionError(f"Input does not exist: {path}")
        candidates = sorted(path.rglob("*")) if path.is_dir() else [path]
        for item in candidates:
            if not item.is_file() or item.name.startswith(".") or item.name.lower() == "database.hdf5":
                continue
            if item.suffix.lower() == ".zip":
                with zipfile.ZipFile(item) as archive:
                    for entry in archive.infolist():
                        normalized = PurePosixPath(entry.filename.replace("\\", "/"))
                        parts = normalized.parts
                        if normalized.is_absolute() or entry.is_dir() or any(p.startswith(".") or p == "__MACOSX" or re.fullmatch(r"[A-Za-z]:", p) for p in parts):
                            continue
                        if normalized.suffix.lower() not in EXTENSIONS or normalized.name.lower() == "database.hdf5":
                            continue
                        key = (str(item.resolve()), entry.filename)
                        if key not in seen:
                            seen.add(key)
                            folders = native_folder_context(normalized.parent.parts) or normalized.parent.parts or (item.stem,)
                            found.append(Source(item.resolve(), entry.filename, folders))
            elif item.suffix.lower() in EXTENSIONS:
                key = (str(item.resolve()), None)
                if key not in seen:
                    seen.add(key)
                    folders = native_folder_context(item.parent.parts)
                    if not folders:
                        folders = item.relative_to(path.parent).parent.parts if path.is_dir() else (item.parent.name,)
                    found.append(Source(item.resolve(), folder_parts=folders))
    if not found:
        raise ConversionError("No .nc, .hdf5 or .h5 data files were found.")
    return found


LOCK_UNSUPPORTED = ("not supported", "not implemented", "errno = 38", "errno = 45", "errno = 95", "errno = 78",
                    "no locks available", "errno = 37")


def lock_unsupported(error):
    text_ = str(error).lower()
    return "lock" in text_ and any(term in text_ for term in LOCK_UNSUPPORTED)


def h5_open(path, mode="r", **kwargs):
    """0.4.2: NAS shares (SMB/NFS) often cannot take HDF5 file locks → "unable to lock file … not supported".
    That is not a busy writer: fall back to an unlocked open (stability checks still apply)."""
    try:
        return h5py.File(path, mode, locking=True, **kwargs)
    except OSError as error:
        if lock_unsupported(error):
            return h5py.File(path, mode, locking=False, **kwargs)
        raise


@contextmanager
def open_source(source):
    if source.member is None:
        with h5_open(source.path, "r") as file:
            if explicitly_active(file):
                raise SourceBusy(f"Source reports an active measurement; retry after it stops: {source.label}")
            yield file
    else:
        # Never extract archive paths into the output or source folder.
        with zipfile.ZipFile(source.path) as archive, tempfile.TemporaryFile() as temp:
            with archive.open(source.member) as stream:
                shutil.copyfileobj(stream, temp)
            temp.seek(0)
            with h5py.File(temp, "r") as file:
                if explicitly_active(file):
                    raise SourceBusy(f"Source reports an active measurement; retry after it stops: {source.label}")
                yield file


@contextmanager
def checked_hdf5(path):
    try:
        file = h5_open(path,"r")
    except OSError as error:
        if "lock" in str(error).lower() or "already open for write" in str(error).lower():
            raise SourceBusy(f"Source is locked by a writer; refused to copy. Retry after measurement stops: {path}") from error
        raise
    with file:
        if explicitly_active(file):
            raise SourceBusy(f"Source reports an active measurement; refused to copy. Retry after it stops: {path}")
        yield file


def prepare_source(source, workdir, local_cache, policy, cancel, notify, archive_cache):
    before = stable_source(source.path,policy,cancel,notify)
    is_archive = source.member is not None
    cached = archive_cache.get(str(source.path)) if is_archive else None
    if cached and cached[0] == before:
        return Source(cached[1],source.member,source.folders), {**before,"sha256":cached[2]}
    fd, name = tempfile.mkstemp(prefix="source-",suffix=source.path.suffix,dir=workdir)
    os.close(fd)
    local = Path(name)
    def prepare():
        require_unchanged(source.path,before)
        if notify:
            notify({"stage":"caching" if local_cache else "checking"})
        if is_archive:
            digest = verified_copy(source.path,local,"source cache",RetryPolicy(1,0),cancel,notify) if local_cache else sha256(source.path,cancel)
        else:
            # Keep the HDF5 read lock while copying; cooperative writers cannot start.
            with checked_hdf5(source.path):
                digest = verified_copy(source.path,local,"source cache",RetryPolicy(1,0),cancel,notify) if local_cache else sha256(source.path,cancel)
        require_unchanged(source.path,before)
        return digest
    try:
        digest = retry_io(prepare,"source read/close",policy,cancel,notify)
    except BaseException:
        local.unlink(missing_ok=True)
        raise
    if not local_cache:
        local.unlink(missing_ok=True)
        return source, {**before,"sha256":digest}
    if is_archive:
        archive_cache[str(source.path)] = (before,local,digest)
    return Source(local,source.member,source.folders), {**before,"sha256":digest}


def confirm_source(source, signature, policy, notify):
    before = {key:value for key,value in signature.items() if key != "sha256"}
    def confirm():
        require_unchanged(source.path,before)
        if source.member is None:
            with checked_hdf5(source.path):
                require_unchanged(source.path,before)
            require_unchanged(source.path,before)
    retry_io(confirm,"source final check",policy,notify=notify)


def plain(value):
    if isinstance(value, (bytes, np.bytes_)):
        return value.decode("utf-8")
    if isinstance(value, np.ndarray):
        return [plain(v) for v in value.tolist()] if value.ndim else plain(value.item())
    if isinstance(value, np.generic):
        return plain(value.item())
    if isinstance(value, complex):
        return {"real": value.real, "imag": value.imag}
    return value


def text(value):
    return str(plain(value))


def scalar(value):
    a = np.asarray(value)
    if a.size != 1:
        raise ConversionError("Expected a scalar metadata value.")
    return plain(a.reshape(-1)[0])


def names(value):
    return [text(v) for v in np.asarray(value).reshape(-1)]


def is_labber(file):
    return all(name in file for name in ["Channels", "Data", "Step list", "Log list"])


class NotConvertible(ConversionError):
    """0.4.2: a file that is simply not a measurement this tool converts (reported as skipped, not as a failure)."""


def _scale(obj, dim):
    scales = list(obj.dims[dim].values())
    return scales[0] if len(scales) == 1 else None


def _rows_reader(obj, single):
    if single:
        return lambda first, stop: np.asarray(obj[()])[None, :][first:stop]
    return lambda first, stop: obj[first:stop]


def inspect_nc(file, source, project_override=""):
    schema = text(file.attrs.get("schema", ""))
    layout = text(file.attrs.get("layout", "grid")) or "grid"
    if schema != "labcontrol-nc/1":
        raise NotConvertible(f"Not a Lab Control .nc (schema={schema or 'none'}); "
                             "supported input: labcontrol-nc/1 or a valid Labber log.")
    if layout not in {"grid", "point"}:
        raise ConversionError(f"Unsupported labcontrol-nc layout={layout!r} (supported: grid, point).")
    try:
        created = datetime.fromisoformat(text(file.attrs["created"]))
        meta = json.loads(text(file.attrs.get("labcontrol", "{}")))
    except (KeyError, ValueError, TypeError) as error:
        raise ConversionError(f"Invalid creation date or labcontrol metadata: {error}") from error
    if created.tzinfo is None:
        created = created.replace(tzinfo=LOCAL_TZ)
    datasets = {k: v for k, v in file.items() if isinstance(v, h5py.Dataset)}
    two_d = {k: v for k, v in datasets.items() if v.ndim == 2 and v.dtype.kind in "fc"}
    single = False
    if two_d:
        candidates = two_d
    else:
        # 0.4.2: a single-shot trace ("單張"): no sweep axis, traces are (frequency,) only → one entry.
        candidates = {k: v for k, v in datasets.items() if v.ndim == 1 and v.dtype.kind in "fc"
                      and len(v) > 1 and _scale(v, 0) is not None and _scale(v, 0).name.lstrip("/") != k}
        single = bool(candidates)
    trace_specs = []
    sweep_scale = None
    for key, obj in candidates.items():
        fdim = 0 if single else 1
        frequency = _scale(obj, fdim)
        if frequency is None or (not single and _scale(obj, 0) is None):
            raise ConversionError(f"{key}: expected explicit sweep/frequency coordinate scales.")
        if not single:
            sweep = _scale(obj, 0)
            if sweep_scale is not None and sweep.name != sweep_scale.name:
                raise ConversionError("Multiple independent sweep axes are not supported yet.")
            sweep_scale = sweep
        x = np.asarray(frequency[()], dtype=float)
        if len(x)<2 or not np.all(np.isfinite(x)) or not np.allclose(np.diff(x), x[1]-x[0], rtol=1e-8, atol=1e-9) or x[1] == x[0]:
            raise ConversionError(f"{key}: non-uniform frequency axes cannot be encoded as Labber t0/dt without changing the data.")
        name = text(obj.attrs.get("long_name", key))
        if not name or "/" in name:
            raise ConversionError(f"Invalid Labber channel name: {name!r}")
        trace_specs.append(dict(key=key, name=name, unit=text(obj.attrs.get("units", "")),
                                complex=obj.dtype.kind == "c", x=x, freq_key=frequency.name.lstrip("/"),
                                x_name=text(frequency.attrs.get("long_name", "Frequency")),
                                x_unit=text(frequency.attrs.get("units", "Hz"))))
    if not trace_specs:
        raise NotConvertible("No trace channels were found (this version converts traces vs. one sweep axis, "
                             "or a single trace).")
    if len({t["name"] for t in trace_specs}) != len(trace_specs):
        raise ConversionError("Trace display names must be unique.")
    trace_keys = {t["key"] for t in trace_specs}
    freq_keys = {t["freq_key"] for t in trace_specs}
    extra_ok = {"timestamp", "retained"}
    # ---- the sweep axis ----
    if single:
        axis_key, axis_all, axis_name, axis_unit = None, np.array([1.]), "Shot", ""
        n_rows = 1
    elif layout == "point":
        # 0.4.2: points not on a regular grid (e.g. asynchronous sweeps): rows are measurement order,
        # the real setpoint of the (single) swept quantity is stored along "point".
        point_key = sweep_scale.name.lstrip("/")
        n_rows = len(sweep_scale)
        along = [k for k, v in datasets.items() if v.ndim == 1 and len(v) == n_rows
                 and k not in trace_keys | freq_keys | extra_ok | {point_key} and v.dtype.kind in "fiu"]
        if len(along) > 1:
            raise ConversionError("Point layout with several swept quantities (" + ", ".join(along) +
                                  ") is not supported yet; this version converts one sweep axis.")
        extra_ok |= {point_key}
        if along:
            axis_key = along[0]
            axis_dataset = file[axis_key]
        else:
            axis_key, axis_dataset = point_key, sweep_scale
        axis_all = np.asarray(axis_dataset[()], dtype=float)
        axis_name = text(axis_dataset.attrs.get("long_name", axis_key)) if along else "Point"
        axis_unit = text(axis_dataset.attrs.get("units", "")) if along else ""
    else:
        axis_key = sweep_scale.name.lstrip("/")
        axis_dataset = file[axis_key]
        axis_all = np.asarray(axis_dataset[()], dtype=float)
        axis_name = text(axis_dataset.attrs.get("long_name", axis_dataset.name.rsplit("/", 1)[-1]))
        axis_unit = text(axis_dataset.attrs.get("units", ""))
        n_rows = len(axis_all)
    allowed = trace_keys | freq_keys | extra_ok | ({axis_key} if axis_key else set())
    unsupported = [k for k in datasets if k not in allowed]
    if unsupported:
        raise ConversionError(f"Unsupported additional data variables: {', '.join(unsupported)}")
    if not axis_name or "/" in axis_name or axis_name == "Step index API" or axis_name in {t["name"] for t in trace_specs}:
        raise ConversionError(f"Invalid or conflicting sweep channel name: {axis_name!r}")
    if layout == "grid" and not single and not np.all(np.isfinite(axis_all)):
        raise ConversionError("Sweep coordinates contain invalid values.")
    readers = {}
    acquired = np.zeros(n_rows, dtype=bool)
    for spec in trace_specs:
        ds = file[spec["key"]]
        shape = (1, len(spec["x"])) if single else ds.shape
        if shape != (n_rows, len(spec["x"])):
            raise ConversionError(f"{spec['key']}: trace and coordinate dimensions do not match.")
        readers[spec["key"]] = _rows_reader(ds, single)
        for first in range(0, n_rows, 32):
            block = readers[spec["key"]](first, first+32)
            # NetCDF complex fill can be NaN+0j. Its finite imaginary zero is
            # allocation, not a measured point; np.isfinite(complex) checks both.
            acquired[first:first+len(block)] |= np.isfinite(block).any(axis=1)
    timestamps = None
    if "timestamp" in file:
        timestamps = np.atleast_1d(np.asarray(file["timestamp"][()], dtype=float))
        if timestamps.shape != (n_rows,):
            raise ConversionError("Timestamp dimensions do not match sweep coordinates.")
        acquired |= np.isfinite(timestamps)
    if not single and layout == "point":
        acquired &= np.isfinite(axis_all)
    if not acquired.any():
        raise ConversionError("This file contains no acquired points.")
    retained = None
    if "retained" in file:
        retained = np.atleast_1d(np.asarray(file["retained"][()]))
        if retained.shape != (n_rows,):
            raise ConversionError("Retained flags have unexpected dimensions.")
    # Keep interior missing points in place; only unacquired trailing allocation is omitted.
    count = int(np.flatnonzero(acquired)[-1]) + 1
    declared = int(scalar(file.attrs.get("points", count)))
    planned = int(scalar(file.attrs.get("planned_points", n_rows)))
    warnings = []
    if declared != int(acquired.sum()):
        warnings.append(f"Declared points={declared}; observed acquired rows={int(acquired.sum())}; exported prefix={count}.")
    complete = bool(scalar(file.attrs.get("complete", 0)))
    if not complete:
        warnings.append(f"Source run is incomplete: exported {count} of {planned} planned points.")
    if layout == "point" and not single:
        warnings.append("Point layout: entries are in measurement order with their actual setpoints.")
    tags_value = plain(file.attrs.get("tags", ""))
    tags = [v.strip() for v in tags_value.split(",") if v.strip()] if isinstance(tags_value, str) else names(tags_value)
    return dict(created=created, meta=meta, trace_specs=trace_specs, axis_key=axis_key,
                axis_name=axis_name, axis_unit=axis_unit, axis_all=axis_all, layout="single" if single else layout,
                axis=axis_all[:count], count=count, planned=planned, declared=declared, readers=readers,
                complete=complete, warnings=warnings, timestamps=timestamps, retained=retained,
                project=project_override or text(file.attrs.get("project", "")),
                user=text(file.attrs.get("user", "")), tags=tags, source=source.label)


def dataset(group, name, values, **kwargs):
    return group.create_dataset(name, data=values, compression="gzip", compression_opts=4, **kwargs)


def write_nc(file, out, source, info):
    count, axis, specs = info["count"], info["axis"], info["trace_specs"]
    dimensions = np.array([1, count], dtype=np.int32)
    with h5py.File(out, "w", libver="earliest") as target:
        target.attrs.update({"version": "1.8.6", "log_name": Path(source.name).stem,
                             "creation_time": info["created"].timestamp(),
                             "comment": text(file.attrs.get("comment", "")),
                             "Step dimensions": dimensions, "arm_trig_mode": False,
                             "hardware_loop": False, "log_parallel": True, "logger_mode": False,
                             "time_per_point": 0., "trig_channel": "", "wait_between": 0.})
        write_configuration(target, info["axis_name"], info["axis_unit"], axis, specs)
        data = target.create_group("Data")
        data.attrs.update({"Completed": info["complete"], "Entries, last trace": np.int32(1),
                           "Step dimensions": dimensions, "Step index": np.array([0, 1], dtype=np.int32),
                           "Fixed step index": np.array([], dtype=float),
                           "Fixed step values": np.array([], dtype=float)})
        data.create_dataset("Channel names", data=rows(CHANNEL_NAME, [dict(name="Step index API"), dict(name=info["axis_name"])]))
        dataset(data, "Data", np.stack([np.ones(count), axis])[None, :, :])
        traces = target.create_group("Traces")
        for spec in specs:
            name = spec["name"]
            components = 2 if spec["complex"] else 1
            nfreq = len(spec["x"])
            ds = traces.create_dataset(name, shape=(nfreq, components, count), dtype="<f8",
                                       maxshape=(None, components, count),
                                       chunks=(min(nfreq, 512), components, min(count, 16)),
                                       compression="gzip", compression_opts=4)
            ds.attrs.update({"complex": spec["complex"], "x, name": spec["x_name"], "x, unit": spec["x_unit"]})
            for first in range(0, count, 16):
                block = info["readers"][spec["key"]](first, min(count, first+16))
                ds[:, 0, first:first+len(block)] = block.real.T
                if components == 2:
                    ds[:, 1, first:first+len(block)] = block.imag.T
            dataset(traces, name+"_N", np.array([nfreq], dtype=np.int32), maxshape=(count,))
            dataset(traces, name+"_t0dt", np.array([[spec["x"][0], spec["x"][1]-spec["x"][0]]]), maxshape=(count, 2))
        if info["timestamps"] is not None:
            times = info["timestamps"][:count]
            dataset(data, "Time stamp", times)
            dataset(traces, "Time stamp", times)
            finite = times[np.isfinite(times)]
            if len(finite)>1:
                target.attrs["time_per_point"] = float(np.median(np.diff(finite)))
        tags = target.create_group("Tags")
        for name, values in [("Project", [info["project"]]), ("User", [info["user"]]), ("Tags", info["tags"])]:
            tags.attrs.create(name, np.array(values, dtype=object), dtype=TEXT)
        write_view(target, info["axis_name"], axis, specs[0])
        provenance = target.create_group("Conversion")
        provenance.attrs.update({"converter": "Labber Converter", "converter_version": VERSION,
                                 "source": source.label, "source_schema": "labcontrol-nc/1",
                                 "source_complete": info["complete"], "planned_points": info["planned"],
                                 "declared_points": info["declared"], "exported_points": count,
                                 "naive_time_zone": "+08:00", "native_labber_ui_verified": False})
        provenance.create_dataset("source_attributes_json", data=json.dumps({k:plain(v) for k,v in file.attrs.items()}, ensure_ascii=False), dtype=TEXT)
        provenance.create_dataset("variable_attributes_json", data=json.dumps({k:{a:plain(v) for a,v in obj.attrs.items() if a not in {"DIMENSION_LIST", "REFERENCE_LIST"}}
                                                                             for k,obj in file.items() if isinstance(obj,h5py.Dataset)}, ensure_ascii=False), dtype=TEXT)
        provenance.create_dataset("warnings_json", data=json.dumps(info["warnings"], ensure_ascii=False), dtype=TEXT)
        provenance.attrs["source_layout"] = info["layout"]
        if info["retained"] is not None:
            dataset(provenance, "retained", info["retained"][:count])
        # Preserve the full planned sweep coordinates, including its unmeasured tail.
        dataset(provenance, "planned_axis", info["axis_all"])


def validate_nc(file, out, info):
    """Independent read-back of every exported real/imaginary value and both axes."""
    with h5py.File(out, "r") as target:
        if not is_labber(target):
            raise ConversionError("Output does not have the required Labber nodes.")
        np.testing.assert_array_equal(target["Data/Data"][0, 1, :], info["axis"])
        if text(target["Channels"][1]["unitPhys"]) != info["axis_unit"]:
            raise ConversionError("Sweep unit was not preserved.")
        for spec in info["trace_specs"]:
            stored = target["Traces"][spec["name"]]
            t0, dt = target["Traces"][spec["name"]+"_t0dt"][0]
            reconstructed = t0 + np.arange(stored.shape[0])*dt
            np.testing.assert_allclose(reconstructed, spec["x"], rtol=1e-12, atol=1e-6)
            for first in range(0, info["count"], 16):
                original = info["readers"][spec["key"]](first, min(info["count"],first+16))
                np.testing.assert_array_equal(stored[:, 0, first:first+len(original)].T, original.real)
                if spec["complex"]:
                    np.testing.assert_array_equal(stored[:, 1, first:first+len(original)].T, original.imag)
        if info["timestamps"] is not None:
            np.testing.assert_array_equal(target["Data/Time stamp"][()], info["timestamps"][:info["count"]])
        if info["retained"] is not None:
            np.testing.assert_array_equal(target["Conversion/retained"][()], info["retained"][:info["count"]])


def labels_for(file):
    if "Tags" not in file or not isinstance(file["Tags"], h5py.Group):
        return []
    result = []
    for category in ["Project", "User", "Tags"]:
        for value in names(file["Tags"].attrs.get(category, [])):
            if not value:
                continue
            label = f"{category}/{value}"
            if label not in result:
                result.append(label)
    return result


def output_folder(root, source, created, preserve_folders=True):
    """Follow the reference database's year/month/Data_MMDD structure."""
    year, month, day = created.strftime("%Y"), created.strftime("%m"), created.strftime("Data_%m%d")
    if not preserve_folders:
        return root/year/month/day
    parts = list(source.folders)
    if any(part in {"", ".", ".."} or "/" in part or "\\" in part for part in parts):
        raise ConversionError("Invalid source folder hierarchy.")
    native = native_folder_context(parts)
    if native:
        # 0.4.0 could append the selected month a second time below Data_MMDD.
        if len(native)==4 and re.fullmatch(r"Data_"+re.escape(native[1])+r"\d{2}",native[2],re.IGNORECASE) and native[3]==native[1]:
            native=native[:3]
        return root/Path(*native)
    day_index=None
    for index,part in enumerate(parts):
        match = re.fullmatch(r"Data_(\d{2})(\d{2})",part,re.IGNORECASE)
        if match:
            try:
                datetime(int(year),int(match[1]),int(match[2]))
            except ValueError:
                continue
            month,day=match[1],part
            day_index=index
    # Selecting "10/" or a ZIP containing "10/Data_1013" is a month tree,
    # not a source project named "10" to append below every date folder.
    month_index=day_index-1 if day_index is not None and day_index>0 and parts[day_index-1]==month else None
    groups = [part for index,part in enumerate(parts) if part != day and index!=month_index]
    return root/year/month/day/Path(*groups)


def project_with_folders(project, folders):
    base = [p for p in project.split("/") if p]
    extra = [p for p in folders if p]
    # A source project may already contain some or all of its folder hierarchy.
    if extra and any(base[i:i+len(extra)] == extra for i in range(len(base)-len(extra)+1)):
        return "/".join(base)
    overlap = 0
    for count in range(1,min(len(base),len(extra))+1):
        if base[-count:] == extra[:count]:
            overlap = count
    return "/".join(base+extra[overlap:])


def register_source_project(temp, source, relative_folder, created, project_override="", native_copy=False):
    """Map Data_0828 to Project 08/0828, exactly as the reference database."""
    parts = relative_folder.parts
    month = parts[1]
    tail = list(parts[2:])
    match = re.fullmatch(r"Data_(\d{4})",tail[0],re.IGNORECASE) if tail else None
    date = match[1] if match else created.strftime("%m%d")
    groups = tail[1:] if match else tail
    mapped = "/".join([month,date,*groups])
    with h5_open(temp,"r+") as file:
        group=file.require_group("Tags")
        original=names(group.attrs.get("Project",[]))
        project = project_with_folders(project_override,[month,date,*groups]) if project_override else mapped
        projects = [project]
        if native_copy and not project_override:
            projects = []
            for old_project in original:
                if not old_project:
                    continue
                old_parts = [p for p in old_project.split("/") if p]
                has_date = any(old_parts[i:i+2] == [month,date] for i in range(len(old_parts)-1))
                native_source=native_folder_context(source.folders)
                if has_date and len(native_source)==4 and native_source[1]==month and native_source[3]==month and old_parts[-3:]==[month,date,month]:
                    old_project="/".join(old_parts[:-1])
                value = project_with_folders(old_project,groups if has_date else [month,date,*groups])
                if value not in projects:
                    projects.append(value)
            projects = projects or [project]
            project = projects[0]
        if original != projects:
            if "Project" in group.attrs:
                del group.attrs["Project"]
            group.attrs.create("Project",np.array(projects,dtype=object),dtype=TEXT)
        provenance=file.require_group("Conversion")
        provenance.attrs["source_folders_json"]=json.dumps(source.folders,ensure_ascii=False)
        if "original_project_json" not in provenance.attrs:
            provenance.attrs["original_project_json"]=json.dumps(original,ensure_ascii=False)
    return project


def database_revision(path):
    try:
        before = identity(path)
    except FileNotFoundError:
        return None
    digest = sha256(path)
    if identity(path) != before:
        raise DatabaseBusy("Database changed while reading its revision; retry synchronization.")
    return {**before,"sha256":digest}


def merge_database(root, new_labels, replace=False):
    """Re-read concurrent changes and preserve extra nodes; bound contention retries.
    replace=True (0.4.2 "rebuild labels"): Labels become exactly new_labels (stale categories are dropped)."""
    root = Path(root)
    path = root / "Database.hdf5"
    for attempt in range(3):
        try:
            result = retry_io(lambda:_merge_database_snapshot(root,path,new_labels,replace),"database merge")
        except DatabaseBusy:
            if attempt<2:pause(.1*(attempt+1))
            continue
        if result is not None:
            return result
        if attempt<2:pause(.1*(attempt+1))
    raise DatabaseBusy(f"Database kept changing; output is retained for later label sync: {path}")


@contextmanager
def open_database(path, mode="r"):
    try:
        file = h5_open(path,mode)
    except OSError as error:
        if "lock" in str(error).lower() or "already open for write" in str(error).lower():
            raise DatabaseBusy(f"Database is locked by another writer: {path}") from error
        raise
    with file:
        yield file


def _merge_database_snapshot(root, path, new_labels, replace=False):
    revision = database_revision(path)
    existing = []
    if revision is not None:
        with open_database(path) as file:
            if "Labels" not in file or not isinstance(file["Labels"], h5py.Dataset) or file["Labels"].ndim != 1 or not h5py.check_string_dtype(file["Labels"].dtype):
                raise ConversionError(f"Invalid existing database; left unchanged: {path}")
            existing = list(file["Labels"].asstr()[()])
    if database_revision(path) != revision:
        return None
    merged = [] if replace else list(existing)
    for label in new_labels:
        if label not in merged:
            merged.append(label)
    if not merged:
        merged = [""]
    if revision is not None and merged == existing:
        return len(merged)
    fd, name = tempfile.mkstemp(prefix=".database-", suffix=".tmp", dir=root)
    os.close(fd)
    temp = Path(name)
    backup = None
    try:
        if revision is not None:
            shutil.copy2(path, temp)
            if sha256(temp) != revision["sha256"]:
                return None
        with h5_open(temp, "r+" if revision is not None else "w", libver="earliest") as file:
            if "Labels" in file:
                del file["Labels"]
            file.create_dataset("Labels", data=np.array(merged,dtype=object), dtype=TEXT)
        if database_revision(path) != revision:
            return None
        if revision is not None:
            stamp = datetime.now(LOCAL_TZ).strftime("%Y%m%d_%H%M%S_%f")
            folder = backup_folder(root)
            backup = folder/f"Database.hdf5.{stamp}.bak"
            shutil.copy2(path, backup)
            if sha256(backup) != revision["sha256"] or database_revision(path) != revision:
                backup.unlink(missing_ok=True)
                return None
            prune_backups(root)
        elif database_revision(path) is not None:
            return None
        os.replace(temp, path)
        with open_database(path) as file:
            if not set(merged) <= set(file["Labels"].asstr()[()]):
                return None
    finally:
        temp.unlink(missing_ok=True)
    return len(merged)


BACKUP_DIR = ".labber-converter-backups"
KEEP_BACKUPS = 5


def backup_folder(root):
    """0.4.2: Database backups go to a hidden folder, not next to Database.hdf5 (one .bak per new label was clutter)."""
    folder = Path(root)/BACKUP_DIR
    folder.mkdir(exist_ok=True)
    return folder


def prune_backups(root, keep=KEEP_BACKUPS):
    """Move old root-level Database.hdf5.*.bak into the backup folder and keep only the newest `keep`."""
    root = Path(root)
    folder = Path(root)/BACKUP_DIR
    try:
        loose = sorted(root.glob("Database.hdf5.*.bak"))
        if loose:
            folder.mkdir(exist_ok=True)
            for item in loose:
                target = folder/item.name
                if not target.exists():
                    os.replace(item, target)
        if not folder.is_dir():
            return 0
        backups = sorted(folder.glob("Database.hdf5.*.bak"), key=lambda p: p.name, reverse=True)
        for old in backups[keep:]:
            old.unlink(missing_ok=True)
        return len(backups[keep:])
    except OSError:
        return 0          # cleanup never blocks a conversion


def verify_database(root):
    """Confirm the selected master folder itself contains the database file."""
    root = Path(root).expanduser().resolve()
    path = root/"Database.hdf5"
    if not path.is_file():
        raise ConversionError(f"Database.hdf5 is missing from the output master folder: {root}")
    with open_database(path) as file:
        labels = file.get("Labels")
        if not isinstance(labels, h5py.Dataset) or labels.ndim != 1 or not h5py.check_string_dtype(labels.dtype):
            raise ConversionError(f"Invalid database Labels: {path}")
        values = labels.asstr()[()].tolist()
    return {"database_root": str(root), "database_path": str(path),
            "database_verified": True, "labels": len(values)}


def initialize_database(root):
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    created = not (root/"Database.hdf5").exists()
    with database_lock(root):
        prune_backups(root)
        merge_database(root, [])
        result = verify_database(root)
    return {**result, "operation": "initialize", "database_created": created}


def pid_alive(pid):
    """Is this process still running? 0.4.2: on Windows os.kill(pid, 0) does not test — it TERMINATES the
    process (and raises for a dead pid), so stale locks could never be reclaimed there."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return kernel32.GetLastError() == 5                     # access denied → exists
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259                                # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def database_lock(root):
    path = root/".labber-converter.lock"
    owner = {"pid":os.getpid(),"host":socket.gethostname(),"token":uuid.uuid4().hex}
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        previous = identity(path)
        try:
            lock = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(lock,dict) or lock.get("host") != owner["host"] or int(lock.get("pid",0)) <= 0:
                raise ValueError("Unverified lock owner")
            if pid_alive(int(lock["pid"])):
                raise ValueError("Owner is running")
            if identity(path) != previous:
                raise ValueError("Lock changed")
            path.unlink()
            fd = os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        except (OSError,ValueError,TypeError):
            raise ConversionError(f"Another conversion is using this folder or the lock owner cannot be verified: {path}") from error
    try:
        with os.fdopen(fd,"w") as stream:
            json.dump(owner,stream)
        yield
    finally:
        try:
            if json.loads(path.read_text(encoding="utf-8")) == owner:
                path.unlink()
        except FileNotFoundError:
            pass


def occupied(path):
    return path.exists() or path.is_symlink()


def available_path(path, reserved=()):
    """Use original-name_N, starting above every existing matching suffix.

    The entire source stem is the base. E.g. a measurement named CPAEP_1
    keeps its meaningful run number; duplicates become CPAEP_1_1, etc.
    """
    if not occupied(path) and path not in reserved:
        return path
    pattern = re.compile(re.escape(path.stem) + r"_(\d+)" + re.escape(path.suffix) + r"$", re.IGNORECASE)
    highest = 0
    for entry in [*path.parent.iterdir(), *reserved]:
        if entry.parent != path.parent:
            continue
        match = pattern.fullmatch(entry.name)
        if match:
            highest = max(highest, int(match.group(1)))
    candidate = path.with_name(f"{path.stem}_{highest+1}{path.suffix}")
    while occupied(candidate) or candidate in reserved:
        highest += 1
        candidate = path.with_name(f"{path.stem}_{highest+1}{path.suffix}")
    return candidate


def choose_destination(source, original, on_conflict):
    if not occupied(original):
        return original, "new"
    suggestion = available_path(original)
    action = on_conflict(source, original, suggestion) if callable(on_conflict) else on_conflict
    if action not in {"replace", "skip", "keep", "cancel"}:
        raise ConversionError(f"Unknown filename conflict action: {action!r}")
    return (available_path(original) if action == "keep" else original), action


def publish_output(temp, destination, root, labels, replace, policy=RetryPolicy(), notify=None, before_publish=None):
    """Replace only after preparation/validation; restore the old output on DB failure."""
    backup = None
    preserve_backup = False
    fd, name = tempfile.mkstemp(prefix=".upload-",suffix=".tmp",dir=destination.parent)
    os.close(fd)
    upload = Path(name)
    try:
        if notify:
            notify({"stage":"transferring"})
        digest = verified_copy(temp,upload,"output transfer/read-back",policy,notify=notify)
        if before_publish:
            before_publish()
        if occupied(destination):
            if not replace:
                raise FileExistsError(f"Output appeared during conversion; left unchanged: {destination}")
            if destination.is_symlink() or not destination.is_file():
                raise ConversionError(f"Replacement requires a regular file: {destination}")
            fd, name = tempfile.mkstemp(prefix=".previous-output-", suffix=".tmp", dir=destination.parent)
            os.close(fd)
            backup = Path(name)
            verified_copy(destination,backup,"previous output backup",policy,notify=notify)
        if before_publish:
            before_publish()
        try:
            def commit():
                if upload.exists():
                    os.replace(upload,destination)
                if sha256(destination) != digest:
                    raise OSError(errno.EIO,"Published output checksum mismatch")
            retry_io(commit,"output publish/read-back",policy,notify=notify)
            merge_database(root, labels)
            return False
        except DatabaseBusy:
            return True
        except Exception:
            if backup is not None:
                preserve_backup = True
                try:
                    os.replace(backup, destination)
                except OSError as error:
                    raise ConversionError(f"Database update and output recovery failed; previous output is saved at {backup}: {error}") from error
                backup = None
            else:
                if destination.exists() and sha256(destination) == digest:
                    destination.unlink(missing_ok=True)
            raise
    finally:
        upload.unlink(missing_ok=True)
        if backup is not None and not preserve_backup:
            backup.unlink(missing_ok=True)


def convert_one(source, root, project, on_conflict, preserve_folders=True, *, read_source=None,
                workdir=None, before_publish=None, prepared=None, policy=RetryPolicy(), notify=None):
    record = {"source": source.label}
    temp = None
    try:
        read_source = read_source or source
        with open_source(read_source) as file:
            if is_labber(file):
                created = datetime.fromtimestamp(float(file.attrs["creation_time"]), LOCAL_TZ)
                info = None
            else:
                info = inspect_nc(file, source, "" if preserve_folders else project)
                created = info["created"]
            folder = output_folder(root,source,created,preserve_folders)
            retry_io(lambda:folder.mkdir(parents=True,exist_ok=True),"output folder creation",policy,notify=notify)
            original = folder/(Path(source.name).stem+".hdf5")
            destination, action = choose_destination(source, original, on_conflict)
            if action == "cancel":
                raise Cancelled()
            if action == "skip":
                return {**record, "status": "skipped", "output": str(original), "conflict_action": action}
            if action == "replace" and source.member is None and source.path.samefile(destination):
                return {**record, "status": "skipped", "output": str(destination),
                        "conflict_action": "skip", "reason": "source_is_destination"}
            fd, name = tempfile.mkstemp(prefix=".converting-",suffix=".tmp",dir=workdir or folder)
            os.close(fd)
            temp = Path(name)
            if info is not None:
                write_nc(file, temp, source, info)
                validate_nc(file, temp, info)
                record.update(points=info["count"], planned_points=info["planned"],
                              trace_points=[len(t["x"]) for t in info["trace_specs"]],
                              sweep_unit=info["axis_unit"], warnings=info["warnings"],
                              source_complete=info["complete"], verified_values=True)
            else:
                if read_source.member is None:
                    shutil.copyfile(read_source.path, temp)
                else:
                    with zipfile.ZipFile(read_source.path) as archive, archive.open(read_source.member) as stream, temp.open("wb") as dest:
                        shutil.copyfileobj(stream, dest)
                record.update(warnings=[], copied_existing_labber=True)
        # Wait until the source has closed successfully before replacing anything.
        relative_parts=folder.relative_to(root).parts
        record["native_scan_path_warning"] = len(relative_parts)>3 and bool(re.fullmatch(r"Data_\d{4}",relative_parts[2],re.IGNORECASE))
        if preserve_folders:
            record["project"]=register_source_project(temp,source,folder.relative_to(root),created,project,native_copy=info is None)
            record["source_folders"]=list(source.folders)
        with h5_open(temp, "r") as target:
            labels=labels_for(target)
        # If another output appeared while this file was being prepared, ask again.
        while action != "replace" and occupied(destination):
            destination, action = choose_destination(source, original, on_conflict)
            if action == "cancel":
                raise Cancelled()
            if action == "skip":
                return {"source": source.label, "status": "skipped", "output": str(original), "conflict_action": action}
        if destination.name != original.name:
            with h5_open(temp, "r+") as target:
                target.attrs["log_name"] = destination.stem
        if before_publish:
            before_publish()
        record.update(output=str(destination),conflict_action=action,labels=labels,output_sha256=sha256(temp))
        if prepared:
            prepared({**record,"status":"prepared"})
        pending = publish_output(temp, destination, root, labels, replace=action == "replace",
                                 policy=policy,notify=notify,before_publish=before_publish)
        return {**record, "status": "pending_sync" if pending else "ok"}
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


class ProgressLedger:
    def __init__(self, root, policy):
        self.root = root
        self.path = root/".labber-converter-progress.json"
        self.policy = policy
        self.data = {"version":1,"entries":{}}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                if self.data.get("version") != 1 or not isinstance(self.data["entries"],dict):
                    raise ValueError("Unsupported progress file")
            except (ValueError,KeyError,TypeError) as error:
                raise ConversionError(f"Invalid progress file; left unchanged: {self.path}") from error

    def key(self, source, project, preserve_folders):
        value = [source.label,list(source.folders),project,preserve_folders,VERSION]
        return hashlib.sha256(json.dumps(value,ensure_ascii=False).encode()).hexdigest()

    def save(self, key, record, signature):
        self.data["entries"][key] = {"source_signature":signature,"record":record}
        atomic_json(self.path,self.data,self.policy)

    def completed(self, key, signature, policy, notify, before_sync=None):
        entry = self.data["entries"].get(key,{})
        record = entry.get("record",{})
        if entry.get("source_signature") != signature or record.get("status") not in {"ok","resumed","prepared","pending_sync"}:
            return None
        path = Path(record.get("output",""))
        if not path.resolve().is_relative_to(self.root) or not path.is_file():
            return None
        if retry_io(lambda:sha256(path),"resume output verification",policy,notify=notify) != record.get("output_sha256"):
            return None
        if before_sync:
            before_sync()
        try:
            merge_database(self.root,record["labels"])
            return {**record,"status":"resumed"}
        except DatabaseBusy:
            return {**record,"status":"pending_sync"}


def convert_batch(inputs, output, *, project="", progress=None, cancel=None, on_conflict="keep", preserve_folders=True,
                  local_cache=True, resume=False, activity=None, retry_policy=RetryPolicy()):
    root = Path(output).expanduser().resolve()
    sources = discover(inputs)
    # NC/ZIP inputs can live in the chosen database. Avoid re-importing HDF5
    # output discovered via a surrounding folder, while respecting explicit files.
    explicit_files = {Path(p).expanduser().resolve() for p in inputs if Path(p).expanduser().is_file()}
    sources = [s for s in sources if not (s.path.is_relative_to(root)
               and s.path.suffix.lower() in {".hdf5", ".h5"} and s.path not in explicit_files)]
    if not sources:
        raise ConversionError("Only existing HDF5 files in this output database were found. Use Sync database labels, or choose a different output folder to copy them.")
    root.mkdir(parents=True, exist_ok=True)
    report = {"converter_version": VERSION, "database_root": str(root),
              "database_created": not (root/"Database.hdf5").exists(),
              "preserve_source_folders": preserve_folders,
              "local_cache":local_cache,"resume_enabled":resume,"events":[],
              "native_labber_ui_verified": False, "files": [], "cancelled": False}
    with database_lock(root), tempfile.TemporaryDirectory(prefix="labber-converter-") as work:
        # Check/init the DB before writing any measurements, including an existing DB.
        prune_backups(root)
        merge_database(root, [])
        report.update(verify_database(root))
        ledger = ProgressLedger(root,retry_policy)
        report["progress_path"] = str(ledger.path)
        archive_cache = {}
        for index, source in enumerate(sources):
            if cancel and cancel():
                report["cancelled"] = True
                break
            if progress:
                progress(index, len(sources), source.name)
            signature = None
            key = ledger.key(source,project,preserve_folders)
            def notify(event):
                event = {**event,"source":source.label,"name":source.name}
                if event["stage"] == "retry":
                    report["events"].append(event)
                if activity:
                    activity(event)
            try:
                candidate = resume and key in ledger.data["entries"]
                read_source, signature = prepare_source(source,Path(work),local_cache and not candidate,
                                                        retry_policy,cancel,notify,archive_cache)
                record = ledger.completed(key,signature,retry_policy,notify,
                    before_sync=lambda:confirm_source(source,signature,retry_policy,notify)) if candidate else None
                if record is None:
                    if candidate and local_cache:
                        read_source, signature = prepare_source(source,Path(work),True,retry_policy,cancel,notify,archive_cache)
                    ledger.save(key,{"source":source.label,"status":"processing"},signature)
                    notify({"stage":"converting"})
                    record = convert_one(source, root, project, on_conflict,preserve_folders,
                        read_source=read_source,workdir=Path(work),policy=retry_policy,notify=notify,
                        before_publish=lambda:confirm_source(source,signature,retry_policy,notify),
                        prepared=lambda record:ledger.save(key,record,signature))
                ledger.save(key,record,signature)
            except Cancelled:
                report["cancelled"] = True
                break
            except NotConvertible as error:
                # 0.4.2: e.g. Lab Control's own HDF5 next to the .nc, or a non-trace file → skipped, not failed.
                record = {"source": source.label, "status": "skipped", "reason": "not_convertible",
                          "error": f"{error}"}
            except Exception as error:
                record = {"source": source.label, "status": "error", "error": f"{type(error).__name__}: {error}"}
                if isinstance(error,SourceBusy):
                    record["reason"] = "source_active"
            report["files"].append(record)
            if progress:
                progress(index+1, len(sources), source.name)
        try:
            report.update(retry_io(lambda:verify_database(root),"database verification",retry_policy))
        except (OSError,DatabaseBusy) as error:
            report.update(database_verified=False,database_error=str(error))
            for record in report["files"]:
                if record["status"] in {"ok","resumed"}:
                    record["status"]="pending_sync"
        report["ok"] = sum(r["status"] == "ok" for r in report["files"])
        report["errors"] = sum(r["status"] == "error" for r in report["files"])
        report["skipped"] = sum(r["status"] == "skipped" for r in report["files"])
        report["resumed"] = sum(r["status"] == "resumed" for r in report["files"])
        report["pending_sync"] = sum(r["status"] == "pending_sync" for r in report["files"])
        stamp = datetime.now(LOCAL_TZ).strftime("%Y%m%d_%H%M%S_%f")
        report_path = root/f"conversion_report_{stamp}.json"
        report["report_path"] = str(report_path)
        atomic_json(report_path,report,retry_policy)
    return report


def reindex(root, rebuild=False):
    """Scan the database root's Labber files and add their Project/User/Tags to Labels.
    rebuild=True (0.4.2): drop labels that no file uses any more (e.g. a stale project from an earlier import)."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ConversionError("Choose an existing database root folder.")
    labels = []
    count = 0
    errors = []
    with database_lock(root):
        prune_backups(root)
        before = []
        if rebuild and (root/"Database.hdf5").is_file():
            with open_database(root/"Database.hdf5") as file:
                if isinstance(file.get("Labels"), h5py.Dataset):
                    before = list(file["Labels"].asstr()[()])
        for path in sorted(root.rglob("*")):
            if any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            if not path.is_file() or path.suffix.lower() not in {".h5", ".hdf5"} or path.name.lower() == "database.hdf5":
                continue
            try:
                with h5_open(path,"r") as file:
                    if is_labber(file):
                        labels.extend(labels_for(file))
                        count += 1
            except (OSError, ValueError) as error:
                errors.append({"path":str(path),"error":str(error)})
        if rebuild and errors:
            raise ConversionError("Some files could not be read, so labels were not rebuilt (nothing removed): "
                                  + "; ".join(e["path"] for e in errors[:5]))
        total = merge_database(root, labels, replace=rebuild)
        result = verify_database(root)
    removed = [l for l in before if l and l not in labels] if rebuild else []
    return {**result,"operation":"index","files":count,"labels":total,"errors":errors,"rebuild":rebuild,
            "removed_labels":removed}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    conversion=sub.add_parser("convert")
    conversion.add_argument("inputs",nargs="+")
    conversion.add_argument("-o","--output",required=True)
    conversion.add_argument("--project",default="")
    conversion.add_argument("--on-conflict", choices=["keep", "skip", "replace"], default="keep")
    conversion.add_argument("--flatten-folders",action="store_true",help="Use only the date layout; do not add source folders to Project.")
    conversion.add_argument("--resume",action="store_true",help="Verify source/output checksums and resume completed work.")
    conversion.add_argument("--no-local-cache",action="store_true",help="Read the source directly; conversion still uses local temporary storage.")
    indexing=sub.add_parser("index")
    indexing.add_argument("root")
    indexing.add_argument("--rebuild",action="store_true",help="Drop labels that no Labber file in the root uses.")
    args=parser.parse_args()
    try:
        result=convert_batch(args.inputs,args.output,project=args.project,on_conflict=args.on_conflict,
            preserve_folders=not args.flatten_folders,local_cache=not args.no_local_cache,resume=args.resume) if args.command=="convert" else reindex(args.root,args.rebuild)
    except Exception as error:
        parser.exit(1,f"{error}\n")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return int(bool(result.get("errors") or result.get("pending_sync")))


if __name__ == "__main__":
    raise SystemExit(main())
