"""Network Workspace Host: serves shared measurements to up to 5 Clients.

The Host owns the HDF5 files. Clients receive the parsed structure and the
raw arrays they need to mirror the Host's view; only datasets referenced by
that structure can be read, never arbitrary files or paths. Every call from
the GUI returns immediately — all network work runs in background threads.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import socket
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from app import __version__
from app.network import protocol as P
from app.network.netinfo import has_lan, same_subnet
from app.network.quality import PROBE_BYTES, Quality
from app.network.remote_data import describe_experiment
from app.network.transport import Connection, sandbox_mode

MAX_CLIENTS = 5
DEFAULT_PORT = 47811
AUTH_FAILURE_LIMIT = 5
AUTH_LOCK_SECONDS = 60.0


def new_join_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


from app.network.secure import KeyPair, SecureError, join_proof  # noqa: E402  (HMAC; the code never crosses the network)


@dataclass
class ClientInfo:
    client_id: str
    name: str
    address: str
    connected_at: float
    quality: Quality = field(default_factory=lambda: Quality(None, None, "unknown"))


class _Shared:
    """One shared measurement, opened by the Host server itself (read-only)."""

    def __init__(self, measurement_id: str, path: str, relative_hint: str):
        from app.core.labber_parser import load_experiment

        self.id = measurement_id
        self.path = path
        self.relative_hint = relative_hint
        self.lock = threading.Lock()
        self.experiment = load_experiment(path)
        self.description = describe_experiment(self.experiment)
        allowed = {trace["trace_path"] for trace in self.description["vector_traces"]}
        matrix = (self.description.get("metadata_tree") or {}).get("scalar_data_matrix") or {}
        if isinstance(matrix, dict) and isinstance(matrix.get("path"), str):
            allowed.add(matrix["path"])
        self.allowed_paths = allowed
        self._fingerprint = None
        self._cache: "OrderedDict[str, bytes]" = OrderedDict()

    def fingerprint(self) -> dict:
        """size + SHA-256 so a Client can use its own copy (e.g. on shared storage)."""
        if self._fingerprint is None:
            digest = hashlib.sha256()
            with open(self.path, "rb") as stream:
                for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                    digest.update(block)
            self._fingerprint = {"size": os.path.getsize(self.path), "sha256": digest.hexdigest(),
                                 "relative": self.relative_hint}
        return self._fingerprint

    def read_bytes(self, path: str, slice_) -> tuple[bytes, str, list]:
        if path not in self.allowed_paths:
            raise PermissionError("This dataset is not part of the shared measurement.")
        key = P.slice_key(path, slice_)
        with self.lock:
            cached = self._cache.get(key)
            if cached is None:
                array = np.ascontiguousarray(self.experiment._reader.read(path, slice_=slice_))
                cached = (array.tobytes(), array.dtype.str, list(array.shape))
                self._cache[key] = cached
                while len(self._cache) > 4:
                    self._cache.popitem(last=False)
            else:
                self._cache.move_to_end(key)
        return cached

    def close(self) -> None:
        with self.lock:
            self._cache.clear()
            self.experiment.close()


class HostServer:
    """Background TCP server + discovery responder."""

    def __init__(self, host_name: str, session_name: str, events: Callable[[dict], None],
                 port: int = DEFAULT_PORT, discovery_port: int | None = None, join_code: str | None = None):
        from app.network.discovery import DISCOVERY_PORT

        self.host_name = host_name
        self.session_name = session_name
        self.join_code = join_code or new_join_code()
        self.port = port
        self.discovery_port = DISCOVERY_PORT if discovery_port is None else discovery_port
        self.session_id = uuid.uuid4().hex[:12]
        self._events = events
        self._lock = threading.RLock()
        self._clients: dict[str, tuple[Connection, ClientInfo]] = {}
        self._pending: dict[Connection, dict] = {}
        self._failures: dict[str, list[float]] = {}
        self._shared: dict[str, _Shared] = {}
        self._current: dict | None = None               # latest sync state sent to everyone
        self._server: socket.socket | None = None
        self._responder = None
        self._stop = threading.Event()
        self.running = False

    # -- lifecycle ---------------------------------------------------------------
    def start(self) -> None:
        sandbox = sandbox_mode()
        if not sandbox and not has_lan():
            raise OSError("No active local network connection was found, so hosting cannot start.")
        bind = "127.0.0.1" if sandbox else "0.0.0.0"
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((bind, self.port))
        except OSError:
            server.bind((bind, 0))                     # port in use: take any free port
        server.listen(8)
        server.settimeout(0.5)
        self._server = server
        self.port = server.getsockname()[1]
        self.running = True
        threading.Thread(target=self._accept_loop, name="llvn-host-accept", daemon=True).start()
        from app.network.discovery import DiscoveryResponder

        self._responder = DiscoveryResponder(self.info, self.discovery_port)
        self._responder.start()
        self._emit("hosting", running=True, port=self.port, join_code=self.join_code)

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self._stop.set()
        if self._responder is not None:
            self._responder.stop()
        with self._lock:
            clients = list(self._clients.values())
        goodbye = P.pack({"type": "host_closing"})
        for connection, _info in clients:
            connection.send(goodbye)
        for connection, _info in clients:
            connection.flush_and_close("host stopped", 0.5)
        try:
            self._server.close()
        except OSError:
            pass
        for shared in list(self._shared.values()):
            shared.close()
        self._shared.clear()
        self._emit("hosting", running=False)

    def info(self) -> dict:
        with self._lock:
            count = len(self._clients)
        return {"host_name": self.host_name, "session_name": self.session_name, "session_id": self.session_id,
                "port": self.port, "clients": count, "max_clients": MAX_CLIENTS,
                "protocol": P.PROTOCOL_VERSION, "app_version": __version__}

    def clients(self) -> list[ClientInfo]:
        with self._lock:
            return [info for _connection, info in self._clients.values()]

    # -- sharing (called from the GUI thread) -------------------------------------------
    def share(self, path: str, relative_hint: str) -> str:
        """Open ``path`` for Clients (in the server, read-only); returns its id."""
        for shared in self._shared.values():
            if shared.path == path:
                return shared.id
        measurement_id = uuid.uuid4().hex[:10]
        self._shared[measurement_id] = _Shared(measurement_id, path, relative_hint)
        return measurement_id

    def publish(self, message: dict, value=None) -> None:
        """Send a sync message to every Client (and remember the latest view state)."""
        if message.get("type") == "view":
            self._current = (message, value)
        frame = P.pack(message, value)
        with self._lock:
            connections = [connection for connection, _info in self._clients.values()]
        for connection in connections:
            connection.send(frame)

    def send_copy(self, client_id: str, measurement_id: str) -> None:
        """Permanent copy of the original HDF5 file to one Client (Host decides)."""
        with self._lock:
            entry = self._clients.get(client_id)
        shared = self._shared.get(measurement_id)
        if entry is None or shared is None:
            raise KeyError("Unknown client or measurement.")
        threading.Thread(target=self._stream_copy, args=(entry[0], shared), daemon=True).start()

    # -- internals ---------------------------------------------------------------------
    def _emit(self, kind: str, **values) -> None:
        try:
            self._events(dict(values, event=kind))
        except Exception:
            pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                sock, address = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._admit(sock, address[0])

    def _admit(self, sock: socket.socket, address: str) -> None:
        def refuse(reason: str) -> None:
            try:
                sock.sendall(P.pack({"type": "reject", "reason": reason}))
            except OSError:
                pass
            sock.close()

        if not same_subnet(address, include_loopback=sandbox_mode()):
            refuse("not_same_subnet")
            return
        failures = [t for t in self._failures.get(address, []) if time.monotonic() - t < AUTH_LOCK_SECONDS]
        if len(failures) >= AUTH_FAILURE_LIMIT:
            refuse("too_many_attempts")
            return
        with self._lock:
            if len(self._clients) >= MAX_CLIENTS:
                refuse("host_full")
                return
        nonce = secrets.token_hex(16)
        holder = {}
        connection = Connection(sock, lambda message: self._on_message(holder["c"], message),
                                lambda reason: self._on_closed(holder["c"], reason), name=f"client-{address}")
        holder["c"] = connection
        with self._lock:
            keys = KeyPair()
            self._pending[connection] = {"nonce": nonce, "address": address, "since": time.monotonic(),
                                         "keys": keys}
        connection.start()
        connection.send(P.pack(dict({"type": "challenge", "nonce": nonce, "host_key": keys.public.hex()},
                                    **self.info())))

    def _on_closed(self, connection: Connection, reason: str) -> None:
        with self._lock:
            self._pending.pop(connection, None)
            gone = [client_id for client_id, (conn, _info) in self._clients.items() if conn is connection]
            for client_id in gone:
                _conn, info = self._clients.pop(client_id)
        for client_id in gone:
            self._emit("client_left", client_id=client_id, name=info.name, reason=reason)

    def _on_message(self, connection: Connection, message: P.Message) -> None:
        kind = message.type
        with self._lock:
            pending = self._pending.get(connection)
        if pending is not None:
            if kind == "key" and "session" not in pending:
                # From here on everything is encrypted in both directions.
                try:
                    client_key = str(message.header.get("client_key") or "")
                    session = pending["keys"].session(client_key, pending["nonce"], is_host=True)
                except SecureError:
                    connection.close("bad key")
                    return
                pending["client_key"], pending["session"] = client_key, session
                connection.enable_receive(session)
                connection.enable_send(session)
            elif kind == "hello" and "session" in pending:
                self._authenticate(connection, pending, message.header)
            else:
                connection.close("not authenticated")
            return
        client = self._client_for(connection)
        if client is None:
            return
        if kind == "ping":
            connection.send(P.pack({"type": "pong", "t": message.header.get("t")}))
        elif kind == "quality":
            client.quality = Quality.from_dict(message.header.get("quality"))
            self._emit("client_quality", client_id=client.client_id, quality=client.quality.as_dict())
        elif kind == "probe":
            size = min(int(message.header.get("size") or PROBE_BYTES), 4 * PROBE_BYTES)
            connection.send(P.encode_frame({"type": "probe_data", "id": message.header.get("id")},
                                           os.urandom(size)), bulk=True)
        elif kind == "get_structure":
            self._send_structure(connection, message.header)
        elif kind == "read":
            threading.Thread(target=self._serve_read, args=(connection, message.header), daemon=True).start()
        elif kind == "resync":
            self._emit("client_resync", client_id=client.client_id)
        elif kind == "bye":
            connection.close("client left")

    def _client_for(self, connection: Connection) -> ClientInfo | None:
        with self._lock:
            for conn, info in self._clients.values():
                if conn is connection:
                    return info
        return None

    def _authenticate(self, connection: Connection, pending: dict, header: dict) -> None:
        name = str(header.get("client_name") or "Client")[:64]
        if header.get("protocol") != P.PROTOCOL_VERSION:
            connection.send(P.pack({"type": "reject", "reason": "protocol_mismatch"}))
            connection.flush_and_close("protocol mismatch")
            return
        expected = join_proof(self.join_code, pending["nonce"], name, pending["client_key"],
                              pending["keys"].public.hex())
        if not hmac.compare_digest(expected, str(header.get("proof") or "")):
            self._failures.setdefault(pending["address"], []).append(time.monotonic())
            connection.send(P.pack({"type": "reject", "reason": "wrong_code"}))
            connection.flush_and_close("wrong join code")
            return
        with self._lock:
            self._pending.pop(connection, None)
            if len(self._clients) >= MAX_CLIENTS:
                connection.send(P.pack({"type": "reject", "reason": "host_full"}))
                connection.flush_and_close("host full")
                return
            client_id = uuid.uuid4().hex[:8]
            info = ClientInfo(client_id, name, pending["address"], time.time())
            self._clients[client_id] = (connection, info)
        connection.send(P.pack(dict({"type": "welcome", "client_id": client_id}, **self.info())))
        self._emit("client_joined", client_id=client_id, name=name, address=pending["address"])
        if self._current is not None:
            message, value = self._current
            connection.send(P.pack(message, value))

    def _send_structure(self, connection: Connection, header: dict) -> None:
        shared = self._shared.get(str(header.get("mid")))
        if shared is None:
            connection.send(P.pack({"type": "error", "rid": header.get("rid"), "reason": "unknown_measurement"}))
            return
        connection.send(P.pack({"type": "structure", "rid": header.get("rid"), "mid": shared.id},
                               {"description": shared.description, "fingerprint": shared.fingerprint()}), bulk=True)

    def _serve_read(self, connection: Connection, header: dict) -> None:
        rid = header.get("rid")
        shared = self._shared.get(str(header.get("mid")))
        try:
            if shared is None:
                raise KeyError("unknown_measurement")
            path = str(header.get("path"))
            data, dtype, shape = shared.read_bytes(path, P.decode_slice(header.get("slice")))
        except Exception as error:
            connection.send(P.pack({"type": "error", "rid": rid, "reason": f"{type(error).__name__}: {error}"}))
            return
        total = max(1, -(-len(data) // P.CHUNK_BYTES))
        start = max(0, min(int(header.get("resume_from") or 0), total - 1))
        digest = hashlib.sha256(data).hexdigest()
        for index in range(start, total):
            if connection.closed:
                return
            chunk = data[index * P.CHUNK_BYTES:(index + 1) * P.CHUNK_BYTES]
            connection.send(P.encode_frame({
                "type": "data", "rid": rid, "index": index, "total": total, "size": len(data),
                "dtype": dtype, "shape": shape, "sha256": digest,
            }, chunk, compress=True), bulk=True)
            while connection.pending_bulk() > 8 and not connection.closed:
                time.sleep(0.005)                       # keep memory bounded on slow links

    def _stream_copy(self, connection: Connection, shared: _Shared) -> None:
        name = Path(shared.path).name
        fingerprint = shared.fingerprint()
        total = max(1, -(-fingerprint["size"] // P.CHUNK_BYTES))
        transfer = uuid.uuid4().hex[:8]
        with open(shared.path, "rb") as stream:
            for index in range(total):
                if connection.closed:
                    return
                chunk = stream.read(P.CHUNK_BYTES)
                connection.send(P.encode_frame({
                    "type": "copy", "transfer": transfer, "index": index, "total": total, "name": name,
                    "size": fingerprint["size"], "sha256": fingerprint["sha256"],
                }, chunk, compress=True), bulk=True)
                while connection.pending_bulk() > 8 and not connection.closed:
                    time.sleep(0.005)
