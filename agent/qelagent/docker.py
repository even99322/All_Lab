"""Docker Engine API（unix socket 或 http），只用標準函式庫。沿用 Lab Control Hub 控制代理。"""
from __future__ import annotations

import http.client
import json
import socket
import struct
import urllib.parse
from typing import Any, Callable, Dict, Optional, Tuple


class DockerError(RuntimeError):
    pass


class _UnixConn(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 60) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


class Docker:
    def __init__(self, host: str) -> None:
        self.host = host

    def _conn(self, timeout: float):
        if self.host.startswith("unix://"):
            return _UnixConn(self.host[len("unix://"):], timeout)
        u = urllib.parse.urlsplit(self.host)
        return http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)

    def req(self, method: str, path: str, body: Any = None, timeout: float = 60,
            on_line: Optional[Callable[[str], None]] = None, raw: bool = False) -> Tuple[int, Any]:
        c = self._conn(timeout)
        try:
            data = json.dumps(body).encode() if body is not None else None
            c.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
            r = c.getresponse()
            if on_line is not None:
                buf = b""
                while True:
                    chunk = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if line.strip():
                            on_line(line.decode("utf-8", "replace"))
                return r.status, None
            payload = r.read()
            if raw:
                return r.status, payload
            try:
                return r.status, json.loads(payload) if payload else None
            except ValueError:
                return r.status, payload.decode("utf-8", "replace")
        except OSError as e:
            raise DockerError(f"連不到 Docker（{self.host}）：{e}；請確認 docker-compose.yml 有掛載 /var/run/docker.sock") from None
        finally:
            c.close()

    @staticmethod
    def _ok(st: int, data: Any, what: str, allow=(200, 201, 204, 304)) -> Any:
        if st not in allow:
            msg = data.get("message") if isinstance(data, dict) else data
            raise DockerError(f"{what}失敗（{st}）：{msg}")
        return data

    def inspect(self, name: str) -> Optional[Dict[str, Any]]:
        st, data = self.req("GET", f"/containers/{urllib.parse.quote(name)}/json")
        if st == 404:
            return None
        return self._ok(st, data, "讀取容器資訊")

    def stop(self, cid: str, t: int = 15) -> None:
        st, data = self.req("POST", f"/containers/{cid}/stop?t={t}", timeout=t + 30)
        self._ok(st, data, "停止容器")

    def start(self, cid: str) -> None:
        st, data = self.req("POST", f"/containers/{cid}/start")
        self._ok(st, data, "啟動容器")

    def restart(self, cid: str, t: int = 15) -> None:
        st, data = self.req("POST", f"/containers/{cid}/restart?t={t}", timeout=t + 30)
        self._ok(st, data, "重新啟動容器")

    def remove(self, cid: str) -> None:
        st, data = self.req("DELETE", f"/containers/{cid}?force=true")
        self._ok(st, data, "刪除容器", allow=(200, 204, 404))

    def pull(self, image: str, on_line: Callable[[str], None]) -> None:
        name, _, tag = image.rpartition(":") if ":" in image.split("/")[-1] else (image, "", "latest")
        st, _ = self.req("POST", f"/images/create?fromImage={urllib.parse.quote(name)}&tag={urllib.parse.quote(tag)}",
                         timeout=900, on_line=on_line)
        if st != 200:
            raise DockerError(f"下載映像 {image} 失敗（{st}）")

    def logs(self, name: str, tail: int = 200) -> str:
        st, data = self.req("GET", f"/containers/{urllib.parse.quote(name)}/logs?stdout=1&stderr=1&timestamps=1"
                                   f"&tail={int(tail)}", raw=True)
        self._ok(st, data, "讀取容器日誌")
        return demux(data or b"")

    def recreate(self, name: str, info: Dict[str, Any]) -> str:
        """以原本的設定（inspect）刪除並重新建立容器，回傳新 id。compose 標籤一併保留。"""
        cfg = dict(info.get("Config") or {})
        keep = ("Env", "Cmd", "Entrypoint", "WorkingDir", "User", "Labels", "ExposedPorts", "Volumes", "StopSignal",
                "Healthcheck", "Tty", "OpenStdin", "StdinOnce", "AttachStdin", "AttachStdout", "AttachStderr")
        body: Dict[str, Any] = {k: cfg[k] for k in keep if cfg.get(k) is not None}
        body["Image"] = cfg.get("Image") or info.get("Image")
        body["HostConfig"] = info.get("HostConfig") or {}
        nets = (info.get("NetworkSettings") or {}).get("Networks") or {}
        items = list(nets.items())
        if items:
            n0, v0 = items[0]
            body["NetworkingConfig"] = {"EndpointsConfig": {n0: {"Aliases": v0.get("Aliases"),
                                                                 "IPAMConfig": v0.get("IPAMConfig")}}}
        self.remove(info["Id"])
        st, data = self.req("POST", f"/containers/create?name={urllib.parse.quote(name.lstrip('/'))}", body)
        new = self._ok(st, data, "建立容器")["Id"]
        for n, v in items[1:]:
            self.req("POST", f"/networks/{urllib.parse.quote(n)}/connect",
                     {"Container": new, "EndpointConfig": {"Aliases": v.get("Aliases")}})
        return new


def demux(raw: bytes) -> str:
    """docker logs 沒有 TTY 時是多工格式：每段前面 8 bytes（類型、0、0、0、長度 big-endian）。"""
    out, i = [], 0
    while i + 8 <= len(raw) and raw[i] in (0, 1, 2) and raw[i + 1:i + 4] == b"\x00\x00\x00":
        n = struct.unpack(">I", raw[i + 4:i + 8])[0]
        out.append(raw[i + 8:i + 8 + n])
        i += 8 + n
    if i == 0:
        return raw.decode("utf-8", "replace")
    return b"".join(out).decode("utf-8", "replace")
