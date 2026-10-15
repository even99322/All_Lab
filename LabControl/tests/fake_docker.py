"""測試用的假 Docker Engine API：「容器」就是一個 python -m labhub 子程序（程式碼 = hub_dir/labhub，模擬 bind mount）。

0.0.14：也可以加一個「論文庫」容器（add_paperlib）。它的程式在映像裡：POST /build 把 tar 解開成映像，
容器啟動時跑一個小網站，/api/site 回傳映像裡 app/config.py 的 VERSION（app/main.py 含 BROKEN → 啟動失敗）。"""
import io
import json
import os
import tarfile
import tempfile
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
        self.extra = {}                       # 其他容器（論文庫）：name → {c, port, proc}
        self.images = {}                      # tag → 解開的建置內容資料夾
        self.builds = []
        self.copies = []                      # docker cp（PUT archive）
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
        for e in self.extra.values():
            self._x_stop(e)
        self.srv.shutdown()

    # ---- 論文庫容器（映像 = 建置時的程式快照）------------------------------------------
    def add_paperlib(self, name, port, image, code_dir):
        d = tempfile.mkdtemp(prefix="img_")
        import shutil
        shutil.copytree(code_dir, d, dirs_exist_ok=True, ignore=shutil.ignore_patterns("data"))
        self.images[image] = d
        c = {"Id": uuid.uuid4().hex, "Name": "/" + name, "Image": "sha256:p",
             "Config": {"Image": image, "Env": ["TZ=Asia/Taipei"], "Labels": {"com.docker.compose.project": "paperlib"}},
             "HostConfig": {"Binds": ["/volume1/docker/paperlib/data:/data"]},
             "NetworkSettings": {"Networks": {"paperlib_default": {"Aliases": ["paperlib"]}}},
             "State": {"Status": "created", "Running": False}}
        self.extra[name] = {"c": c, "port": port, "proc": None}
        self._x_start(self.extra[name])

    def _x_start(self, e):
        img = self.images[e["c"]["Config"]["Image"]]
        code = ("import re,sys,json,http.server\n"
                f"img={img!r}\n"
                "src=open(img+'/app/main.py').read()\n"
                "if 'BROKEN' in src: sys.exit(1)\n"
                "v=re.search(r'VERSION\\s*=\\s*\"([^\"]+)', open(img+'/app/config.py').read()).group(1)\n"
                "class H(http.server.BaseHTTPRequestHandler):\n"
                "    def log_message(self,*a): pass\n"
                "    def do_GET(self):\n"
                "        b=json.dumps({'version':v,'name':'fake'}).encode()\n"
                "        self.send_response(200); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)\n"
                f"http.server.HTTPServer(('127.0.0.1',{e['port']}),H).serve_forever()\n")
        if e["proc"] is None or e["proc"].poll() is not None:
            e["proc"] = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
        e["c"]["State"] = {"Status": "running", "Running": True, "StartedAt": "now"}

    def _x_stop(self, e):
        if e["proc"] is not None and e["proc"].poll() is None:
            e["proc"].terminate()
            e["proc"].wait(10)
        e["c"]["State"] = {"Status": "exited", "Running": False}

    def _x_find(self, key):
        for e in self.extra.values():
            if key in (e["c"]["Name"].lstrip("/"), e["c"]["Id"]):
                return e
        return None

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
                e = fd._x_find(m.group(1)) if m else None
                if e is not None:
                    return self._send(200, e["c"])
                self._send(404, {"message": "No such container"})

            def do_PUT(self):
                """docker cp：tar 解開到「容器」裡（論文庫容器的檔案 = 映像資料夾，WorkingDir /app）。"""
                fd.calls.append(("PUT", self.path))
                m = re.match(r"/containers/([^/]+)/archive", self.path)
                e = fd._x_find(m.group(1)) if m else None
                if e is None:
                    return self._send(404, {"message": "No such container"})
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                d = fd.images[e["c"]["Config"]["Image"]]
                with tarfile.open(fileobj=io.BytesIO(raw)) as t:
                    t.extractall(d)
                fd.copies.append(t.getnames() if False else len(raw))
                self._send(200)

            def do_DELETE(self):
                fd.calls.append(("DELETE", self.path))
                m = re.match(r"/containers/([^/?]+)", self.path)
                e = fd._x_find(m.group(1)) if m else None
                if e is not None:
                    fd._x_stop(e)
                    e["c"]["Id"] = None
                    return self._send(204)
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
                if path == "/build":
                    import urllib.parse as up
                    tag = up.parse_qs(up.urlsplit(self.path).query)["t"][0]
                    n = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(n)
                    d = tempfile.mkdtemp(prefix="img_")
                    with tarfile.open(fileobj=io.BytesIO(raw)) as t:
                        names = t.getnames()
                        t.extractall(d)
                    fd.images[tag] = d
                    fd.builds.append((tag, names))
                    body = b'{"stream":"Step 1/5 : FROM python:3.11-slim\\n"}\n{"stream":"Successfully built"}\n'
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/containers/create":
                    import urllib.parse as up
                    nm = up.parse_qs(up.urlsplit(self.path).query).get("name", [""])[0]
                    e = fd._x_find(nm) if nm else None
                    if e is None:
                        e = next((x for x in fd.extra.values() if x["c"]["Name"].lstrip("/") == nm), None)
                    if e is not None:
                        b = self._body()
                        e["c"]["Id"] = uuid.uuid4().hex
                        e["c"]["Config"]["Image"] = b["Image"]
                        e["c"]["State"] = {"Status": "created", "Running": False}
                        return self._send(201, {"Id": e["c"]["Id"]})
                    b = self._body()
                    assert b["Image"] and b["HostConfig"]["Binds"] and b["Labels"]["com.docker.compose.project"]
                    fd.c["Id"] = uuid.uuid4().hex
                    fd.c["State"] = {"Status": "created", "Running": False}
                    return self._send(201, {"Id": fd.c["Id"]})
                m = re.match(r"/containers/([^/]+)/(start|stop|restart)", path)
                e = fd._x_find(m.group(1)) if m else None
                if e is not None:
                    act = m.group(2)
                    if act == "start":
                        fd._x_start(e)
                    elif act == "stop":
                        fd._x_stop(e)
                    else:
                        fd._x_stop(e)
                        fd._x_start(e)
                    return self._send(204)
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
