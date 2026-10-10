"""Small length-and-SHA-256 framed TCP transport for trusted lab networks."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
from threading import Event, Thread
from typing import Callable
from uuid import uuid4

from app.core.measurement_transfer import MAX_ARCHIVE_BYTES, load_measurement


PROTOCOL_VERSION = 1
CHUNK_SIZE = 256 * 1024


class TransferError(RuntimeError):
    pass


def _read_line(sock: socket.socket, limit: int = 4096) -> bytes:
    buffer = bytearray()
    while len(buffer) < limit:
        char = sock.recv(1)
        if not char:
            raise TransferError("Connection closed before a response was complete")
        if char == b"\n":
            return bytes(buffer)
        buffer.extend(char)
    raise TransferError("Transfer header exceeds the limit")


def _send_line(sock: socket.socket, message: dict) -> None:
    sock.sendall(json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n")


def send_measurement(host: str, port: int, archive_path: str | Path,
                     progress: Callable[[int, int], None] | None = None) -> None:
    source = Path(archive_path)
    length = source.stat().st_size
    if not 0 < length <= MAX_ARCHIVE_BYTES:
        raise TransferError("Measurement size is outside the supported range")
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(block)
    try:
        with socket.create_connection((host, port), timeout=10) as sock:
            sock.settimeout(30)
            _send_line(sock, {"protocol_version": PROTOCOL_VERSION,
                              "length": length, "sha256": digest.hexdigest()})
            sent = 0
            with source.open("rb") as stream:
                for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
                    sock.sendall(block)
                    sent += len(block)
                    if progress is not None:
                        progress(sent, length)
            response = json.loads(_read_line(sock).decode("utf-8"))
            if response.get("status") != "ok":
                raise TransferError(str(response.get("error", "Receiver rejected measurement")))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise TransferError(str(exc)) from exc


class MeasurementReceiver:
    def __init__(self, storage_dir: str | Path,
                 on_received: Callable[[Path], None] | None = None):
        self.storage_dir = Path(storage_dir)
        self.on_received = on_received
        self._stop = Event()
        self._server: socket.socket | None = None
        self._thread: Thread | None = None
        self.port: int | None = None

    def start(self, host: str = "0.0.0.0", port: int = 0) -> int:
        if self._thread is not None and self._thread.is_alive():
            raise TransferError("Receiver is already running")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((host, port))
            server.listen(4)
            server.settimeout(0.5)
        except OSError:
            server.close()
            raise
        self._server = server
        self.port = server.getsockname()[1]
        self._stop.clear()
        self._thread = Thread(target=self._serve, name="LabLogMeasurementReceiver", daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _address = self._server.accept()
            except socket.timeout:
                continue
            except (OSError, AttributeError):
                break
            with connection:
                connection.settimeout(30)
                try:
                    self._receive(connection)
                except Exception as exc:
                    try:
                        _send_line(connection, {"status": "error", "error": str(exc)[:300]})
                    except OSError:
                        pass

    def _receive(self, connection: socket.socket) -> None:
        try:
            header = json.loads(_read_line(connection).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise TransferError("Invalid transfer header") from exc
        if header.get("protocol_version") != PROTOCOL_VERSION:
            raise TransferError("Unsupported transfer protocol version")
        length = header.get("length")
        expected_hash = header.get("sha256")
        if (type(length) is not int or not 0 < length <= MAX_ARCHIVE_BYTES
                or not isinstance(expected_hash, str) or len(expected_hash) != 64
                or any(char not in "0123456789abcdef" for char in expected_hash)):
            raise TransferError("Invalid transfer length or checksum")
        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.storage_dir, prefix=".incoming-",
                                             suffix=".tmp", delete=False) as output:
                temporary_name = output.name
                digest = hashlib.sha256()
                remaining = length
                while remaining:
                    block = connection.recv(min(CHUNK_SIZE, remaining))
                    if not block:
                        raise TransferError("Incomplete measurement transfer")
                    output.write(block)
                    digest.update(block)
                    remaining -= len(block)
            if digest.hexdigest() != expected_hash:
                raise TransferError("Measurement checksum mismatch")
            measurement = load_measurement(temporary_name)
            if not measurement.data:
                raise TransferError("Measurement has no channel data")
            destination = self.storage_dir / f"{uuid4().hex}.llvmeasure"
            os.replace(temporary_name, destination)
            temporary_name = None
            _send_line(connection, {"status": "ok"})
            if self.on_received is not None:
                self.on_received(destination)
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
