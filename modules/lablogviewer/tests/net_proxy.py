"""Loopback network simulator for tests: delay, bandwidth limit, cut, blackhole.

Listens on 127.0.0.1 and forwards to another 127.0.0.1 port. Nothing leaves
the machine.
"""

from __future__ import annotations

import socket
import threading
import time


class NetProxy:
    def __init__(self, target_port: int, *, delay_ms: float = 0.0, bandwidth: float | None = None):
        self.target_port = target_port
        self.delay = delay_ms / 1000.0
        self.bandwidth = bandwidth
        self.cut_after: int | None = None           # close after this many downstream bytes
        self.blackhole = False                      # stop forwarding (connection stays open)
        self.downstream_bytes = 0
        self.captured = bytearray()                 # every byte in both directions (sniffing tests)
        self.tamper_after: int | None = None        # flip one downstream byte after this many bytes
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(16)
        self._server.settimeout(0.3)
        self.port = self._server.getsockname()[1]
        self._stop = threading.Event()
        self._pairs: list[tuple[socket.socket, socket.socket]] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                upstream = socket.create_connection(("127.0.0.1", self.target_port), timeout=3)
            except OSError:
                client.close()
                continue
            self._pairs.append((client, upstream))
            threading.Thread(target=self._pump, args=(client, upstream, False), daemon=True).start()
            threading.Thread(target=self._pump, args=(upstream, client, True), daemon=True).start()

    def _pump(self, source: socket.socket, sink: socket.socket, downstream: bool) -> None:
        try:
            while not self._stop.is_set():
                data = source.recv(64 * 1024)
                if not data:
                    break
                while self.blackhole and not self._stop.is_set():
                    time.sleep(0.05)
                if self.delay:
                    time.sleep(self.delay)
                if self.bandwidth:
                    time.sleep(len(data) / self.bandwidth)
                self.captured.extend(data)
                if downstream and self.tamper_after is not None and self.downstream_bytes + len(data) > self.tamper_after:
                    index = max(0, self.tamper_after - self.downstream_bytes)
                    data = data[:index] + bytes([data[index] ^ 0xFF]) + data[index + 1:]
                    self.tamper_after = None
                if downstream:
                    self.downstream_bytes += len(data)
                    if self.cut_after is not None and self.downstream_bytes > self.cut_after:
                        self.cut_after = None
                        self.cut_all()
                        return
                sink.sendall(data)
        except OSError:
            pass
        finally:
            for sock in (source, sink):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def cut_all(self) -> None:
        pairs, self._pairs = self._pairs, []
        for pair in pairs:
            for sock in pair:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                    sock.close()
                except OSError:
                    pass

    def close(self) -> None:
        self._stop.set()
        self.cut_all()
        self._server.close()
