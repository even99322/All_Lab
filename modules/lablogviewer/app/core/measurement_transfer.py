"""Portable, non-executable transfer format for interpreted Labber measurements."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any
import zipfile

import numpy as np

from app.core.labber_parser import load_experiment


FORMAT_VERSION = 1
MAX_ARCHIVE_BYTES = 2_000_000_000
MAX_ARRAY_BYTES = 1_000_000_000


class TransferFormatError(ValueError):
    pass


@dataclass
class ReceivedMeasurement:
    identity: dict[str, Any]
    channels: dict[str, dict[str, Any]]
    step_axes: list[dict[str, Any]]
    dimensions: dict[str, list[dict[str, Any]]]
    data: dict[str, np.ndarray]

    def get_data(self, channel_name: str) -> np.ndarray:
        return self.data[channel_name]

    def list_dimensions(self, channel_name: str) -> list[dict[str, Any]]:
        return self.dimensions[channel_name]

    def get_full_nd_array(self, channel_name: str) -> tuple[list[dict[str, Any]], np.ndarray]:
        dims = self.list_dimensions(channel_name)
        shape = tuple(int(dim["size"]) for dim in dims)
        array = self.get_data(channel_name)
        if shape and int(np.prod(shape)) == array.size:
            return dims, array.reshape(shape)
        return dims, array


def _safe_array(value: np.ndarray) -> np.ndarray:
    array = np.asarray(value)
    if array.dtype.kind not in "biufc" or array.dtype.hasobject:
        raise TransferFormatError(f"Unsupported array dtype: {array.dtype}")
    if array.nbytes > MAX_ARRAY_BYTES:
        raise TransferFormatError("A measurement array exceeds the transfer limit")
    return array


def _write_array(archive: zipfile.ZipFile, name: str, value: np.ndarray) -> None:
    with archive.open(name, "w", force_zip64=True) as output:
        np.save(output, _safe_array(value), allow_pickle=False)


def serialize_measurement(source_path: str | Path, destination: str | Path,
                          *, database_name: str | None = None,
                          folder_name: str | None = None) -> dict[str, Any]:
    """Use the existing parser's logical dimensions; never inspect raw HDF5 here."""
    experiment = load_experiment(str(source_path))
    try:
        manifest: dict[str, Any] = {
            "format_version": FORMAT_VERSION,
            "identity": {
                "log_name": experiment.log_name,
                "database_name": database_name,
                "folder_name": folder_name,
                "creation_time": experiment.creation_time,
                "comment": experiment.comment,
                "project": experiment.project,
                "tags": experiment.tags,
                "user": experiment.user,
                "labber_version": experiment.version,
                "format_variant": experiment.format_variant,
            },
            "channels": {}, "step_axes": [], "dimensions": {}, "data": {},
        }
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED,
                             allowZip64=True) as archive:
            for index, (name, channel) in enumerate(experiment.channels.items()):
                metadata = asdict(channel)
                metadata["shape"] = list(channel.shape)
                manifest["channels"][name] = metadata
            for index, axis in enumerate(experiment.step_axes):
                member = f"axes/step_{index}.npy"
                _write_array(archive, member, axis.values)
                manifest["step_axes"].append({
                    "name": axis.channel.name, "unit": axis.channel.unit,
                    "dim_index": axis.dim_index, "values": member,
                })
            scalar_names = experiment.metadata_tree.get("scalar_data_matrix") or {}
            data_names = set(scalar_names.get("channel_names", [])) | set(experiment.vector_traces)
            for index, name in enumerate(experiment.channels):
                if name not in data_names:
                    continue
                data_member = f"data/{index}.npy"
                array = _safe_array(experiment.get_data(name, transform="raw"))
                _write_array(archive, data_member, array)
                manifest["data"][name] = data_member
                dims = []
                for dim_index, dim in enumerate(experiment.list_dimensions(name)):
                    axis_member = f"axes/channel_{index}_{dim_index}.npy"
                    _write_array(archive, axis_member, dim.values)
                    dims.append({"name": dim.name, "unit": dim.unit,
                                 "size": dim.size, "values": axis_member})
                manifest["dimensions"][name] = dims
            archive.writestr("manifest.json", json.dumps(
                manifest, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8"))
        if Path(destination).stat().st_size > MAX_ARCHIVE_BYTES:
            raise TransferFormatError("Measurement archive exceeds the transfer limit")
        return manifest
    finally:
        experiment.close()


def _read_array(archive: zipfile.ZipFile, member: str, expected: set[str]) -> np.ndarray:
    if member not in expected or not member.endswith(".npy"):
        raise TransferFormatError("Invalid array member")
    with archive.open(member) as source:
        return _safe_array(np.load(source, allow_pickle=False))


def load_measurement(path: str | Path) -> ReceivedMeasurement:
    """Validate an archive before exposing its arrays to the receiver."""
    if Path(path).stat().st_size > MAX_ARCHIVE_BYTES:
        raise TransferFormatError("Measurement archive exceeds the transfer limit")
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            names = [item.filename for item in members]
            if len(names) != len(set(names)) or len(names) > 4096 or "manifest.json" not in names:
                raise TransferFormatError("Invalid measurement archive members")
            if any(item.file_size > MAX_ARRAY_BYTES + 4096 or item.file_size < 0
                   for item in members):
                raise TransferFormatError("Oversized measurement archive member")
            if sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES:
                raise TransferFormatError("Expanded measurement exceeds the transfer limit")
            if archive.getinfo("manifest.json").file_size > 2_000_000:
                raise TransferFormatError("Oversized measurement manifest")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if manifest.get("format_version") != FORMAT_VERSION:
                raise TransferFormatError("Unsupported measurement transfer format")
            if not isinstance(manifest.get("identity"), dict) or not isinstance(manifest.get("channels"), dict):
                raise TransferFormatError("Invalid measurement metadata")
            if not isinstance(manifest.get("data"), dict) or not isinstance(manifest.get("dimensions"), dict):
                raise TransferFormatError("Invalid measurement data index")
            expected = set(names) - {"manifest.json"}
            arrays = {name: _read_array(archive, member, expected)
                      for name, member in manifest["data"].items()}
            dimensions = {}
            for name, dims in manifest["dimensions"].items():
                if name not in arrays or not isinstance(dims, list):
                    raise TransferFormatError("Invalid channel dimensions")
                resolved = []
                for dim in dims:
                    values = _read_array(archive, dim["values"], expected)
                    if values.ndim != 1 or values.size != dim["size"]:
                        raise TransferFormatError("Dimension size does not match axis values")
                    resolved.append({"name": dim["name"], "unit": dim["unit"],
                                     "size": dim["size"], "values": values})
                dimensions[name] = resolved
            axes = []
            for axis in manifest["step_axes"]:
                axes.append({"name": axis["name"], "unit": axis["unit"],
                             "dim_index": axis["dim_index"],
                             "values": _read_array(archive, axis["values"], expected)})
            if set(arrays) != set(dimensions) or not set(arrays) <= set(manifest["channels"]):
                raise TransferFormatError("Measurement channel index is inconsistent")
            return ReceivedMeasurement(manifest["identity"], manifest["channels"],
                                       axes, dimensions, arrays)
    except (OSError, zipfile.BadZipFile, KeyError, TypeError, ValueError, UnicodeError) as exc:
        if isinstance(exc, TransferFormatError):
            raise
        raise TransferFormatError(f"Invalid measurement transfer: {exc}") from exc
