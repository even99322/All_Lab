"""Network Workspace wire protocol (framing + safe value codec).

Frame layout (big-endian)::

    magic "LLVN" | version u16 | flags u16 | header_len u32 | payload_len u32 | crc32 u32
    header  : UTF-8 JSON object ({"type": ..., ...})
    payload : raw bytes (zlib-compressed when flags & 1)

The CRC covers header + payload as sent. Values are JSON primitives plus
numpy arrays of whitelisted numeric dtypes, carried as binary blobs — never
pickle, never code. Anything else is rejected on both encode and decode.
"""

from __future__ import annotations

import json
import struct
import zlib
from dataclasses import dataclass, field

import numpy as np

PROTOCOL_VERSION = 2                  # v2: encrypted (X25519 + AES-GCM)
MAGIC = b"LLVN"
_HEADER = struct.Struct(">4sHHIII")
HEADER_SIZE = _HEADER.size
FLAG_ZLIB = 1
MAX_HEADER = 4 * 1024 * 1024
MAX_PAYLOAD = 48 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024

_ALLOWED_KINDS = {"f", "c", "i", "u", "b"}           # float, complex, int, uint, bool


class ProtocolError(ValueError):
    """Malformed, oversized, corrupted or unsupported data from the peer."""


@dataclass
class Message:
    header: dict
    payload: bytes = b""
    blobs: list = field(default_factory=list)          # decoded arrays (see decode_values)

    @property
    def type(self) -> str:
        return str(self.header.get("type", ""))


# -- framing -------------------------------------------------------------------
def encode_frame(header: dict, payload: bytes = b"", *, compress: bool = False) -> bytes:
    header_bytes = json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    flags = 0
    if compress and payload:
        packed = zlib.compress(payload, 1)
        if len(packed) < len(payload):
            payload, flags = packed, FLAG_ZLIB
    if len(header_bytes) > MAX_HEADER or len(payload) > MAX_PAYLOAD:
        raise ProtocolError("Frame too large.")
    crc = zlib.crc32(header_bytes + payload) & 0xFFFFFFFF
    return _HEADER.pack(MAGIC, PROTOCOL_VERSION, flags, len(header_bytes), len(payload), crc) + header_bytes + payload


class FrameReader:
    """Incremental frame parser for a byte stream."""

    def __init__(self):
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[Message]:
        self._buffer.extend(data)
        messages = []
        while True:
            message = self.pop()
            if message is None:
                return messages
            messages.append(message)

    def append(self, data: bytes) -> None:
        self._buffer.extend(data)

    def take_remaining(self) -> bytes:
        rest, self._buffer = bytes(self._buffer), bytearray()
        return rest

    def pop(self) -> Message | None:
        """The next complete frame in the buffer, or None."""
        if len(self._buffer) < HEADER_SIZE:
            return None
        magic, version, flags, header_len, payload_len, crc = _HEADER.unpack_from(self._buffer, 0)
        if magic != MAGIC:
            raise ProtocolError("Not a LabLogViewer Network Workspace peer.")
        if version != PROTOCOL_VERSION:
            raise ProtocolError(f"Protocol version {version} is not supported (this app uses {PROTOCOL_VERSION}).")
        if header_len > MAX_HEADER or payload_len > MAX_PAYLOAD:
            raise ProtocolError("Frame too large.")
        total = HEADER_SIZE + header_len + payload_len
        if len(self._buffer) < total:
            return None
        body = bytes(self._buffer[HEADER_SIZE:total])
        del self._buffer[:total]
        if zlib.crc32(body) & 0xFFFFFFFF != crc:
            raise ProtocolError("Checksum mismatch (corrupted frame).")
        try:
            header = json.loads(body[:header_len].decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ProtocolError("Invalid frame header.") from None
        if not isinstance(header, dict):
            raise ProtocolError("Invalid frame header.")
        payload = body[header_len:]
        if flags & FLAG_ZLIB:
            try:
                payload = zlib.decompress(payload)
            except zlib.error:
                raise ProtocolError("Invalid compressed payload.") from None
        return Message(header, payload)


# -- values: JSON + numpy arrays -------------------------------------------------------
def _check_dtype(dtype: np.dtype) -> np.dtype:
    dtype = np.dtype(dtype)
    if dtype.kind not in _ALLOWED_KINDS or dtype.hasobject or dtype.fields is not None:
        raise ProtocolError(f"Array dtype {dtype} is not allowed.")
    return dtype


def encode_values(value) -> tuple[object, bytes]:
    """(JSON-able structure with array references, concatenated array bytes)."""
    blobs: list[bytes] = []
    offset = [0]

    def convert(item):
        if item is None or isinstance(item, (bool, str)):
            return item
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            if isinstance(item, float):
                item = float(item)                      # np.float64 is a float subclass
                if not np.isfinite(item):
                    return {"__float__": repr(item)}
                return item
            return int(item)
        if isinstance(item, np.generic):
            return convert(item.item())
        if isinstance(item, complex):
            return {"__complex__": [item.real, item.imag]}
        if isinstance(item, np.ndarray):
            array = np.ascontiguousarray(item)
            dtype = _check_dtype(array.dtype)
            data = array.tobytes()
            reference = {"__nd__": [offset[0], len(data), dtype.str, list(array.shape)]}
            blobs.append(data)
            offset[0] += len(data)
            return reference
        if isinstance(item, dict):
            return {"__dict__": [[convert(key), convert(val)] for key, val in item.items()]} \
                if any(not isinstance(key, str) for key in item) else {key: convert(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            converted = [convert(entry) for entry in item]
            return {"__tuple__": converted} if isinstance(item, tuple) else converted
        raise ProtocolError(f"Type {type(item).__name__} cannot be sent.")

    return convert(value), b"".join(blobs)


def decode_values(structure, payload: bytes):
    view = memoryview(payload)

    def restore(item):
        if isinstance(item, list):
            return [restore(entry) for entry in item]
        if not isinstance(item, dict):
            return item
        if "__nd__" in item and len(item) == 1:
            start, length, dtype, shape = item["__nd__"]
            dtype = _check_dtype(dtype)
            if not (isinstance(start, int) and isinstance(length, int) and 0 <= start
                    and start + length <= len(payload)):
                raise ProtocolError("Array reference out of range.")
            count = int(np.prod(shape)) if shape else 1
            if count * dtype.itemsize != length:
                raise ProtocolError("Array size does not match its shape.")
            return np.frombuffer(view[start:start + length], dtype=dtype).reshape(shape).copy()
        if "__tuple__" in item and len(item) == 1:
            return tuple(restore(entry) for entry in item["__tuple__"])
        if "__complex__" in item and len(item) == 1:
            real, imag = item["__complex__"]
            return complex(float(real), float(imag))
        if "__float__" in item and len(item) == 1:
            text = item["__float__"]
            if text not in {"nan", "inf", "-inf"}:
                raise ProtocolError("Invalid float.")
            return float(text)
        if "__dict__" in item and len(item) == 1:
            restored = {}
            for key, val in item["__dict__"]:
                key = restore(key)
                restored[tuple(key) if isinstance(key, list) else key] = restore(val)
            return restored
        return {key: restore(val) for key, val in item.items()}

    return restore(structure)


def pack(header: dict, value=None, *, compress: bool = True) -> bytes:
    """Frame with ``value`` (JSON + arrays) encoded into the header / payload."""
    if value is None:
        return encode_frame(header)
    structure, payload = encode_values(value)
    return encode_frame(dict(header, value=structure), payload, compress=compress)


def unpack(message: Message):
    if "value" not in message.header:
        return None
    return decode_values(message.header["value"], message.payload)


# -- slices (Experiment._reader.read(path, slice_)) ----------------------------------------
def encode_slice(slice_) -> object:
    def one(item):
        if isinstance(item, slice):
            return {"s": [item.start, item.stop, item.step]}
        if isinstance(item, (int, np.integer)):
            return {"i": int(item)}
        if item is Ellipsis:
            return {"e": 1}
        raise ProtocolError(f"Unsupported slice element {item!r}.")

    if slice_ is None:
        return None
    if isinstance(slice_, tuple):
        return [one(item) for item in slice_]
    return {"one": one(slice_)}


def decode_slice(value):
    def one(item):
        if not isinstance(item, dict) or len(item) != 1:
            raise ProtocolError("Invalid slice.")
        if "s" in item:
            start, stop, step = item["s"]
            for part in (start, stop, step):
                if part is not None and not isinstance(part, int):
                    raise ProtocolError("Invalid slice.")
            return slice(start, stop, step)
        if "i" in item and isinstance(item["i"], int):
            return item["i"]
        if "e" in item:
            return Ellipsis
        raise ProtocolError("Invalid slice.")

    if value is None:
        return None
    if isinstance(value, list):
        return tuple(one(item) for item in value)
    if isinstance(value, dict) and "one" in value:
        return one(value["one"])
    raise ProtocolError("Invalid slice.")


def slice_key(path: str, slice_) -> str:
    return json.dumps([path, encode_slice(slice_)], separators=(",", ":"))
