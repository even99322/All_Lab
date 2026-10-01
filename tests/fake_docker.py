"""測試用的假 Docker Engine API：每個「容器」是一個子程序（程式碼 = 服務資料夾，模擬 bind mount）。"""
from __future__ import annotations

import json
import re
import struct
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List


class Container:
    def __init__(self, name: str, cmd: List[str], cwd: str, env: Dict[str, str]) -> None:
        self.name, self.cmd, self.cwd, self.env = name, cmd, cwd, env
        self.proc = None
        self.id = uuid.uuid4().hex
        self.state = {"Status": "created", "Running": False}

    def info(self):
        return {"Id": self.id, "Name": "/" + self.name, "Image": "sha256:x",
                "Config": {"Image": "python:3.12-slim", "Env": [], "Cmd": self.cmd, "Labels": {"p": "qel"}},
                "HostConfig": {"Binds": ["./x:/x"]}, "NetworkSettings": {"Networks": {"qel_default": {"Aliases": [self.name]}}},
                "State": dict(self.state), "RestartCount": 0}

    def start(self):
        if self.proc is None or self.proc.poll() is not None:
            self.proc = subprocess.Popen(self.cmd, cwd=self.cwd, env=self.env, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
        self.state = {"Status": "running", "Running": True, "StartedAt": "now"}

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(10)
        self.state = {"Status": "exited", "Running": False}


class FakeDocker:
    def __init__(self) -> None:
        self.containers: Dict[str, Container] = {}
        self.calls: List[tuple] = []
        srv = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        srv.daemon_threads = True
        self.srv = srv
        self.url = f"http://127.0.0.1:{srv.server_address[1]}"
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    def add(self, name, cmd, cwd, env) -> Container:
        c = Container(name, cmd, cwd, env)
        self.containers[name] = c
        return c

    def _find(self, key):
        for c in self.containers.values():
            if key in (c.name, c.id):
                return c
        return None

    def close(self):
        for c in self.containers.values():
            c.stop()
        self.srv.shutdown()

    def _handler(self):
        fd = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None, raw=None):
                body = raw if raw is not None else (json.dumps(obj).encode() if obj is not None else b"")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                fd.calls.append(("GET", self.path))
                m = re.match(r"/containers/([^/?]+)/(json|logs)", self.path)
                c = fd._find(m.group(1)) if m else None
                if c is None:
                    return self._send(404, {"message": "No such container"})
                if m.group(2) == "logs":
                    out = b""
                    for line in (b"2026-10-01T00:00:00Z started\n", b"2026-10-01T00:00:01Z ready\n"):
                        out += bytes([1, 0, 0, 0]) + struct.pack(">I", len(line)) + line
                    return self._send(200, raw=out)
                return self._send(200, c.info())

            def do_DELETE(self):
                fd.calls.append(("DELETE", self.path))
                c = fd._find(re.match(r"/containers/([^/?]+)", self.path).group(1))
                if c:
                    c.stop()
                    c.id = None
                self._send(204)

            def do_POST(self):
                fd.calls.append(("POST", self.path))
                path = self.path.split("?")[0]
                if path.startswith("/images/create"):
                    return self._send(200, raw=b'{"status":"Status: Image is up to date"}\n')
                if path == "/containers/create":
                    name = re.search(r"name=([^&]+)", self.path).group(1)
                    n = int(self.headers.get("Content-Length") or 0)
                    json.loads(self.rfile.read(n))
                    c = fd.containers[name]
                    c.id = uuid.uuid4().hex
                    c.state = {"Status": "created", "Running": False}
                    return self._send(201, {"Id": c.id})
                if path.startswith("/networks/"):
                    return self._send(200, {})
                m = re.match(r"/containers/([^/]+)/(start|stop|restart)", path)
                c = fd._find(m.group(1)) if m else None
                if c is None:
                    return self._send(404, {"message": "No such container"})
                act = m.group(2)
                if act == "start":
                    if c.state["Running"]:
                        return self._send(304)
                    c.start()
                elif act == "stop":
                    if not c.state["Running"]:
                        return self._send(304)
                    c.stop()
                else:
                    c.stop()
                    c.start()
                self._send(204)
        return H
