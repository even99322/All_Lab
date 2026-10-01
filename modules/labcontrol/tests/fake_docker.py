"""測試用的假 Docker Engine API：「容器」就是一個 python -m labhub 子程序（程式碼 = hub_dir/labhub，模擬 bind mount）。"""
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeDocker:
    def __init__(self, hub_dir, name, port, token, image="python:3.12-slim"):
        self.hub_dir, self.name, self.port, self.token = str(hub_dir), name, port, token
        self.calls = []
        self.proc = None
        self.pulls = 0
        cid = uuid.uuid4().hex
        self.c = {"Id": cid, "Name": "/" + name, "Image": "sha256:x",
                  "Config": {"Image": image, "Env": [f"LABHUB_TOKEN={token}"], "Cmd": ["python", "-m", "labhub"],
                             "WorkingDir": "/app", "Labels": {"com.docker.compose.project": "lab_control"}},
                  "HostConfig": {"Binds": ["./labhub:/app/labhub:ro"], "RestartPolicy": {"Name": "unless-stopped"}},
                  "NetworkSettings": {"Networks": {"lab_control_default": {"Aliases": ["labhub"]}}},
                  "State": {"Status": "created", "Running": False}}
        srv = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        srv.daemon_threads = True
        self.srv = srv
        self.url = f"http://127.0.0.1:{srv.server_address[1]}"
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    # ---- 容器 = 子程序 ------------------------------------------------------------
    def _start(self):
        if self.proc is None or self.proc.poll() is not None:
            env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUB")}
            env["LABHUB_TOKEN"] = self.token
            self.proc = subprocess.Popen([sys.executable, "-S", "-m", "labhub", "--port", str(self.port),
                                          "--data", os.path.join(self.hub_dir, "data")], cwd=self.hub_dir, env=env,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.c["State"] = {"Status": "running", "Running": True, "StartedAt": "now"}

    def _stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(10)
        self.c["State"] = {"Status": "exited", "Running": False}

    def close(self):
        self._stop()
        self.srv.shutdown()

    def _handler(self):
        fd = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None):
                body = json.dumps(obj).encode() if obj is not None else b""
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n)) if n else None

            def do_GET(self):
                fd.calls.append(("GET", self.path))
                m = re.match(r"/containers/([^/]+)/json", self.path)
                if m and m.group(1) in (fd.name, fd.c["Id"]):
                    return self._send(200, fd.c)
                self._send(404, {"message": "No such container"})

            def do_DELETE(self):
                fd.calls.append(("DELETE", self.path))
                fd._stop()
                fd.c["Id"] = None
                self._send(204)

            def do_POST(self):
                fd.calls.append(("POST", self.path))
                path = self.path.split("?")[0]
                if path.startswith("/images/create"):
                    fd.pulls += 1
                    body = b'{"status":"Pulling from library/python"}\n{"status":"Status: Image is up to date"}\n'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/containers/create":
                    b = self._body()
                    assert b["Image"] and b["HostConfig"]["Binds"] and b["Labels"]["com.docker.compose.project"]
                    fd.c["Id"] = uuid.uuid4().hex
                    fd.c["State"] = {"Status": "created", "Running": False}
                    return self._send(201, {"Id": fd.c["Id"]})
                m = re.match(r"/containers/([^/]+)/(start|stop|restart)", path)
                if not m or m.group(1) not in (fd.name, fd.c["Id"]):
                    return self._send(404, {"message": "No such container"})
                act = m.group(2)
                if act == "start":
                    if fd.c["State"]["Running"]:
                        return self._send(304)
                    fd._start()
                elif act == "stop":
                    if not fd.c["State"]["Running"]:
                        return self._send(304)
                    fd._stop()
                else:
                    fd._stop()
                    fd._start()
                self._send(204)
        return H
