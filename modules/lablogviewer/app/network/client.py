"""Network Workspace Client connection (background threads, never the GUI thread).

States: connecting -> connected -> (reconnecting -> connected)* -> disconnected.
Events are delivered through the ``events`` callback as plain dicts; the Qt
side forwards them to the GUI thread.
"""

from __future__ import annotations

import hashlib
import itertools
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from app.network import protocol as P
from app.network.secure import KeyPair, SecureError, join_proof
from app.network.quality import HEARTBEAT_SECONDS, MAX_RTT_MS, MIN_THROUGHPUT, PROBE_BYTES, QualityMeter, grade
from app.network.transport import Connection, connect

RECONNECT_DELAYS = (1.0, 2.0, 4.0)
REQUEST_TIMEOUT = 20.0              # without progress


class ConnectError(OSError):
    pass


class _Request:
    def __init__(self, header: dict, progress: Callable[[int, int], None] | None):
        self.header = header
        self.progress = progress
        self.done = threading.Event()
        self.result = None
        self.error: str | None = None
        self.chunks: dict[int, bytes] = {}
        self.total = 0
        self.meta: dict = {}
        self.started = time.monotonic()
        self.touched = time.monotonic()


class ClientConnection:
    def __init__(self, address: str, port: int, client_name: str, join_code: str,
                 events: Callable[[dict], None], copy_folder: str | os.PathLike | None = None):
        self.address, self.port = address, port
        self.client_name = client_name
        self._code = join_code
        self._events = events
        self._copy_folder = Path(copy_folder) if copy_folder else None
        self._conn: Connection | None = None
        self._ids = itertools.count(1)
        self._requests: dict[int, _Request] = {}
        self._lock = threading.RLock()
        self._pings: dict[int, float] = {}
        self._probe: dict = {}
        self._copies: dict[str, dict] = {}
        self.meter = QualityMeter()
        self.host_info: dict = {}
        self.client_id: str | None = None
        self.state = "disconnected"
        self._leaving = False
        self._stop = threading.Event()

    # -- public API (any thread) ------------------------------------------------------------
    def start(self) -> None:
        threading.Thread(target=self._run, name="llvn-client", daemon=True).start()

    def disconnect(self) -> None:
        self._leaving = True
        self._stop.set()
        connection = self._conn
        if connection is not None and not connection.closed:
            connection.send(P.pack({"type": "bye"}))
            connection.flush_and_close("left", 0.5)
        self._fail_requests("disconnected")
        self._set_state("disconnected", reason="left")

    def request_resync(self) -> None:
        """Ask the Host to resend the full view / YIG content."""
        if self._conn is not None and not self._conn.closed:
            self._conn.send(P.pack({"type": "resync"}))

    def request_structure(self, measurement_id: str) -> _Request:
        return self._request({"type": "get_structure", "mid": measurement_id}, None)

    def read(self, measurement_id: str, path: str, slice_, progress=None) -> _Request:
        return self._request({"type": "read", "mid": measurement_id, "path": path,
                              "slice": P.encode_slice(slice_)}, progress)

    def wait(self, request: _Request, timeout: float | None = None):
        """Block until done (worker threads only)."""
        end = None if timeout is None else time.monotonic() + timeout
        while not request.done.wait(0.2):
            if time.monotonic() - request.touched > REQUEST_TIMEOUT and self.state == "connected":
                self._finish(request, error="timeout")
            if end is not None and time.monotonic() > end:
                self._finish(request, error="timeout")
            if self.state == "disconnected":
                self._finish(request, error="disconnected")
        if request.error:
            raise ConnectError(request.error)
        return request.result

    # -- connection lifecycle --------------------------------------------------------------
    def _run(self) -> None:
        self._set_state("connecting")
        try:
            self._handshake()
        except ConnectError as error:
            self._set_state("disconnected", reason=str(error))
            return
        if not self._gate_quality():
            return
        threading.Thread(target=self._heartbeat_loop, name="llvn-heartbeat", daemon=True).start()

    def _handshake(self) -> None:
        try:
            sock = connect(self.address, self.port)
        except OSError:
            raise ConnectError("host_unavailable") from None
        ready = threading.Event()
        outcome: dict = {}

        def on_message(message: P.Message) -> None:
            if not ready.is_set():
                kind = message.type
                if kind == "challenge":
                    self.host_info = {key: message.header.get(key) for key in
                                      ("host_name", "session_name", "session_id", "clients", "max_clients",
                                       "protocol", "app_version")}
                    if message.header.get("protocol") != P.PROTOCOL_VERSION:
                        outcome["error"] = "protocol_mismatch"
                        ready.set()
                        return
                    nonce, host_key = str(message.header.get("nonce")), str(message.header.get("host_key") or "")
                    keys = KeyPair()
                    try:
                        session = keys.session(host_key, nonce, is_host=False)
                    except SecureError:
                        outcome["error"] = "protocol_mismatch"
                        ready.set()
                        return
                    client_key = keys.public.hex()
                    connection.send(P.pack({"type": "key", "client_key": client_key}))
                    connection.enable_receive(session)          # the Host answers encrypted
                    connection.enable_send(session)             # hello (with the proof) goes encrypted
                    connection.send(P.pack({
                        "type": "hello", "protocol": P.PROTOCOL_VERSION, "client_name": self.client_name,
                        "proof": join_proof(self._code, nonce, self.client_name, client_key, host_key),
                    }))
                elif kind == "welcome":
                    self.client_id = message.header.get("client_id")
                    self.host_info.update({key: message.header.get(key) for key in ("clients", "max_clients")})
                    ready.set()
                elif kind == "reject":
                    outcome["error"] = str(message.header.get("reason") or "rejected")
                    ready.set()
                return
            self._on_message(message)

        connection = Connection(sock, on_message, self._on_closed, name="host")
        self._conn = connection
        connection.start()
        end = time.monotonic() + 8.0
        while not ready.wait(0.1):
            if connection.closed:
                reason = connection._close_reason
                raise ConnectError("protocol_mismatch" if "version" in reason else
                                   "host_unavailable" if not reason.startswith("protocol") else "protocol_error")
            if time.monotonic() > end:
                connection.close("handshake timeout")
                raise ConnectError("timeout")
        if "error" in outcome:
            connection.close(outcome["error"])
            raise ConnectError(outcome["error"])
        self._set_state("connected", host=self.host_info)

    def _gate_quality(self) -> bool:
        """Measure RTT and throughput; refuse to stay below 200 ms / 1 MB/s."""
        for _ in range(4):
            self._ping()
            time.sleep(0.15)
        probe_id = next(self._ids)
        done = threading.Event()
        self._probe = {"id": probe_id, "sent": time.monotonic(), "done": done}
        self._conn.send(P.pack({"type": "probe", "id": probe_id, "size": PROBE_BYTES}))
        done.wait(PROBE_BYTES / MIN_THROUGHPUT + 3.0)
        quality = self.meter.quality()
        self._report(quality)
        if quality.rtt_ms is None or quality.rtt_ms > MAX_RTT_MS or quality.throughput is None \
                or quality.throughput < MIN_THROUGHPUT:
            self._leaving = True
            self._conn.send(P.pack({"type": "bye"}))
            self._conn.flush_and_close("quality", 0.5)
            self._set_state("disconnected", reason="poor_quality", quality=quality.as_dict())
            return False
        return True

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(HEARTBEAT_SECONDS)
            if self.state == "connected" and self._conn is not None and not self._conn.closed:
                self._ping()
                self._report(self.meter.quality())
                self._check_stalled()

    def _ping(self) -> None:
        stamp = next(self._ids)
        self._pings[stamp] = time.monotonic()
        if self._conn is not None:
            self._conn.send(P.pack({"type": "ping", "t": stamp}))

    def _report(self, quality) -> None:
        if self._conn is not None:
            self._conn.send(P.pack({"type": "quality", "quality": quality.as_dict()}))
        self._emit("quality", quality=quality.as_dict())

    def _check_stalled(self) -> None:
        with self._lock:
            requests = list(self._requests.values())
        for request in requests:
            if time.monotonic() - request.touched > REQUEST_TIMEOUT:
                self._finish(request, error="timeout")

    def _on_closed(self, reason: str) -> None:
        # Only a connection that was established reconnects; a failed handshake
        # reports its own reason (wrong code, host full, ...).
        if self._leaving or self._stop.is_set() or self.state != "connected":
            return
        if reason == "host_closing":
            self._fail_requests("host closed")
            self._set_state("disconnected", reason="host_closed")
            return
        threading.Thread(target=self._reconnect, args=(reason,), daemon=True).start()

    def _reconnect(self, reason: str) -> None:
        for delay in RECONNECT_DELAYS:
            self._set_state("reconnecting", reason=reason)
            time.sleep(delay)
            if self._leaving or self._stop.is_set():
                return
            try:
                self._handshake()
            except ConnectError as error:
                if str(error) in {"wrong_code", "host_full", "not_same_subnet", "protocol_mismatch"}:
                    break
                continue
            self._resume_requests()
            return
        self._fail_requests("host unavailable")
        self._set_state("disconnected", reason="host_unavailable")

    # -- requests --------------------------------------------------------------------------
    def _request(self, header: dict, progress) -> _Request:
        rid = next(self._ids)
        request = _Request(dict(header, rid=rid), progress)
        with self._lock:
            self._requests[rid] = request
        if self._conn is not None and not self._conn.closed:
            self._conn.send(P.pack(request.header))
        return request

    def _resume_requests(self) -> None:
        with self._lock:
            requests = list(self._requests.values())
        for request in requests:
            header = dict(request.header)
            if request.total:
                header["resume_from"] = self._next_missing(request)
            request.touched = time.monotonic()
            self._conn.send(P.pack(header))

    @staticmethod
    def _next_missing(request: _Request) -> int:
        index = 0
        while index in request.chunks:
            index += 1
        return index

    def _finish(self, request: _Request, result=None, error: str | None = None) -> None:
        with self._lock:
            self._requests.pop(request.header["rid"], None)
        request.result, request.error = result, error
        request.done.set()

    def _fail_requests(self, reason: str) -> None:
        with self._lock:
            requests = list(self._requests.values())
        for request in requests:
            self._finish(request, error=reason)

    # -- incoming -----------------------------------------------------------------------------
    def _on_message(self, message: P.Message) -> None:
        kind = message.type
        header = message.header
        if kind == "pong":
            sent = self._pings.pop(header.get("t"), None)
            if sent is not None:
                self.meter.add_rtt(time.monotonic() - sent)
        elif kind == "probe_data":
            if self._probe.get("id") == header.get("id"):
                self.meter.add_transfer(len(message.payload), time.monotonic() - self._probe["sent"])
                self._probe["done"].set()
        elif kind == "structure":
            request = self._requests.get(header.get("rid"))
            if request is not None:
                self._finish(request, result=P.unpack(message))
        elif kind == "data":
            self._on_data(message)
        elif kind == "error":
            request = self._requests.get(header.get("rid"))
            if request is not None:
                self._finish(request, error=str(header.get("reason")))
        elif kind == "host_closing":
            self._leaving = True
            self._fail_requests("host closed")
            if self._conn is not None:
                self._conn.close("host_closing")
            self._set_state("disconnected", reason="host_closed")
        elif kind == "copy":
            self._on_copy(message)
        elif kind in {"view", "share", "yig", "annotation", "session"}:
            try:
                value = P.unpack(message)
            except P.ProtocolError:
                return
            self._emit("sync", message=header, value=value)

    def _on_data(self, message: P.Message) -> None:
        header = message.header
        request = self._requests.get(header.get("rid"))
        if request is None:
            return
        request.touched = time.monotonic()
        request.total = int(header["total"])
        request.meta = {key: header[key] for key in ("size", "dtype", "shape", "sha256")}
        request.chunks[int(header["index"])] = message.payload
        received = sum(len(chunk) for chunk in request.chunks.values())
        if request.progress is not None:
            request.progress(received, int(header["size"]))
        if len(request.chunks) < request.total:
            return
        data = b"".join(request.chunks[index] for index in range(request.total))
        elapsed = time.monotonic() - request.started
        self.meter.add_transfer(len(data), elapsed)
        if len(data) != request.meta["size"] or hashlib.sha256(data).hexdigest() != request.meta["sha256"]:
            request.chunks.clear()
            request.total = 0
            self._finish(request, error="checksum_mismatch")
            return
        try:
            dtype = P._check_dtype(request.meta["dtype"])
            array = np.frombuffer(data, dtype=dtype).reshape(request.meta["shape"]).copy()
        except (ValueError, P.ProtocolError) as error:
            self._finish(request, error=str(error))
            return
        self._finish(request, result=array)

    def _on_copy(self, message: P.Message) -> None:
        """Permanent copy chosen by the Host: written to the received folder."""
        header = message.header
        transfer = str(header.get("transfer"))
        name = Path(str(header.get("name") or "measurement.hdf5")).name
        state = self._copies.get(transfer)
        if state is None:
            folder = self._copy_folder or Path(tempfile.gettempdir())
            folder.mkdir(parents=True, exist_ok=True)
            part = folder / f".{name}.{transfer}.part"
            state = {"part": part, "stream": part.open("wb"), "digest": hashlib.sha256(), "next": 0,
                     "name": name, "folder": folder}
            self._copies[transfer] = state
            self._emit("copy_started", name=name, size=header.get("size"))
        if int(header["index"]) != state["next"]:
            return                                          # copies are resent from the start if interrupted
        state["stream"].write(message.payload)
        state["digest"].update(message.payload)
        state["next"] += 1
        self._emit("copy_progress", name=name, index=state["next"], total=header.get("total"))
        if state["next"] < int(header["total"]):
            return
        state["stream"].close()
        self._copies.pop(transfer, None)
        if state["digest"].hexdigest() != header.get("sha256"):
            state["part"].unlink(missing_ok=True)
            self._emit("copy_failed", name=name, reason="checksum_mismatch")
            return
        target = state["folder"] / name
        stem, suffix, counter = target.stem, target.suffix, 1
        while target.exists():
            target = state["folder"] / f"{stem} ({counter}){suffix}"
            counter += 1
        os.replace(state["part"], target)
        self._emit("copy_received", name=target.name, path=str(target))

    # -- events ---------------------------------------------------------------------------
    def _set_state(self, state: str, **values) -> None:
        self.state = state
        self._emit("state", state=state, **values)

    def _emit(self, kind: str, **values) -> None:
        try:
            self._events(dict(values, event=kind))
        except Exception:
            pass
