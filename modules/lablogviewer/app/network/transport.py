"""Threaded framed connection shared by Host and Client.

One reader thread parses frames and hands messages to a callback; one sender
thread writes queued frames. Control frames (heartbeats, sync state) always go
before bulk frames (data chunks), so a 100 MB transfer never delays a
heartbeat or a view update. Nothing here touches Qt widgets.
"""

from __future__ import annotations

import os
import queue
import select
import socket
import threading
import time
from typing import Callable

from app.network.protocol import FrameReader, Message, ProtocolError
from app.network.secure import SecureError, Session

CONNECT_TIMEOUT = 5.0
IDLE_TIMEOUT = 8.0                  # no bytes for this long -> connection lost


def sandbox_mode() -> bool:
    """Loopback only (tests / virtual acceptance): never touches the LAN."""
    return os.environ.get("LABLOGVIEWER_NET_SANDBOX", "").strip() in {"1", "true", "yes"}


class Connection:
    def __init__(self, sock: socket.socket, on_message: Callable[[Message], None],
                 on_closed: Callable[[str], None], name: str = "peer"):
        self.sock = sock
        self.peer = self._peer_address(sock)
        self.name = name
        self._on_message = on_message
        self._on_closed = on_closed
        self._control: "queue.Queue[bytes | None]" = queue.Queue()
        self._bulk: "queue.Queue[bytes]" = queue.Queue()
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._close_reason = ""
        self._rx: Session | None = None           # set by the handshake
        self._tx: Session | None = None
        self.last_received = time.monotonic()
        self.bytes_received = 0
        self.bytes_sent = 0
        # Blocking sends (never a partial frame on timeout); reads poll with select.
        self.sock.settimeout(None)
        self._reader = threading.Thread(target=self._read_loop, name=f"llvn-read-{name}", daemon=True)
        self._sender = threading.Thread(target=self._send_loop, name=f"llvn-send-{name}", daemon=True)

    @staticmethod
    def _peer_address(sock) -> str:
        try:
            return str(sock.getpeername()[0])
        except OSError:
            return "?"

    def start(self) -> None:
        self._reader.start()
        self._sender.start()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def send(self, frame: bytes, *, bulk: bool = False) -> None:
        if self._closed.is_set():
            return
        (self._bulk if bulk else self._control).put(frame)
        self._wake.set()

    def enable_receive(self, session: Session) -> None:
        """Decrypt everything after the current frame (call from the handshake)."""
        self._rx = session

    def enable_send(self, session: Session) -> None:
        """Encrypt every frame queued after this point (earlier ones go out plain)."""
        self._control.put(("encrypt", session))
        self._wake.set()

    @property
    def encrypted(self) -> bool:
        return self._rx is not None and self._tx is not None

    def pending_bulk(self) -> int:
        return self._bulk.qsize()

    def close(self, reason: str = "closed") -> None:
        if self._closed.is_set():
            return
        self._close_reason = reason
        self._closed.set()
        self._wake.set()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        try:
            self._on_closed(reason)
        except Exception:
            pass

    def flush_and_close(self, reason: str, timeout: float = 1.0) -> None:
        """Send what is queued in the control queue (e.g. a goodbye), then close."""
        end = time.monotonic() + timeout
        while not self._control.empty() and time.monotonic() < end:
            time.sleep(0.02)
        time.sleep(0.05)
        self.close(reason)

    def _read_loop(self) -> None:
        parser = FrameReader()                    # plain frames (handshake) and decrypted frames
        raw = bytearray()                         # encrypted records not yet complete
        while not self._closed.is_set():
            try:
                ready, _, _ = select.select([self.sock], [], [], 1.0)
                if not ready:
                    if time.monotonic() - self.last_received > IDLE_TIMEOUT:
                        self.close("timeout")
                    continue
                data = self.sock.recv(256 * 1024)
            except (OSError, ValueError):
                self.close("connection lost")
                return
            if not data:
                self.close("peer closed")
                return
            self.last_received = time.monotonic()
            self.bytes_received += len(data)
            try:
                if self._rx is None:
                    parser.append(data)
                    while self._rx is None:
                        message = parser.pop()
                        if message is None:
                            break
                        self._deliver(message)
                    if self._rx is not None:      # switched mid-buffer: the rest is encrypted
                        raw.extend(parser.take_remaining())
                else:
                    raw.extend(data)
                if self._rx is not None:
                    for plaintext in self._rx.open_records(raw):
                        for message in parser.feed(plaintext):
                            self._deliver(message)
            except (ProtocolError, SecureError) as error:
                self.close(f"protocol error: {error}")
                return

    def _deliver(self, message: Message) -> None:
        try:
            self._on_message(message)
        except Exception:
            # A handler bug must never take the connection (or the app) down.
            import traceback

            traceback.print_exc()

    def _next_frame(self):
        try:
            return self._control.get_nowait()
        except queue.Empty:
            pass
        try:
            return self._bulk.get_nowait()
        except queue.Empty:
            return None

    def _send_loop(self) -> None:
        while not self._closed.is_set():
            frame = self._next_frame()
            if frame is None:
                self._wake.wait(0.5)
                self._wake.clear()
                continue
            if isinstance(frame, tuple):          # ("encrypt", session) marker
                self._tx = frame[1]
                continue
            if self._tx is not None:
                frame = self._tx.seal(frame)
            try:
                self.sock.sendall(frame)
                self.bytes_sent += len(frame)
            except OSError:
                self.close("connection lost")
                return


def connect(host: str, port: int, timeout: float = CONNECT_TIMEOUT) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock
