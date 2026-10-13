"""1.0.5：數據索引沒有在跑時說清楚原因（Hub /api/index/status、控制台「數據索引」卡、控制代理看容器狀態與紀錄）。"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_104 import _get, _hub

ROOT = Path(__file__).resolve().parents[1]


def test_status_before_the_index_exists(tmp_path):
    from labhub import indexer
    from labhub.index_routes import NOT_RUNNING, index_summary

    data = tmp_path / "data"
    assert index_summary(data) == {"available": False, "phase": None, "message": NOT_RUNNING}
    srv, _state, base = _hub(data)
    try:
        code, body, _ = _get(base + "/api/index/status", {"Authorization": "Bearer tok"})
        assert code == 404 and ".qel" in json.loads(body)["error"]
        indexer.write_status(data, state="installing", message="x")
        code, body, _ = _get(base + "/api/index/status", {"Authorization": "Bearer tok"})
        assert code == 503 and "安裝" in json.loads(body)["error"]
        indexer.write_status(data, state="error", message="安裝 numpy、h5py 失敗", detail="No matching distribution")
        s = index_summary(data)
        assert s["phase"] == "error" and "No matching distribution" in s["message"]
    finally:
        srv.shutdown()


def test_status_keeps_the_key_flag(tmp_path):
    from labhub import indexer
    from labhub.index_routes import index_summary

    data = tmp_path / "data"
    indexer._STATUS_EXTRA["key_ok"] = False
    try:
        indexer.write_status(data, state="indexing", message="")
        ix = indexer.Indexer(tmp_path / "empty", data)
        (tmp_path / "empty").mkdir()
        ix.pass_once()
        s = index_summary(data)
        assert s["available"] and s["phase"] == "indexing" and s["key_ok"] is False and s["indexed"] == 0
    finally:
        indexer._STATUS_EXTRA.clear()


def test_console_status_has_the_index(tmp_path):
    from labhub import indexer

    srv, state, _base = _hub(tmp_path / "data")
    try:
        s = state.console.status()                     # 控制代理不在（測試）也要有索引的狀態
        assert s["index"]["available"] is False and ".qel" in s["index"]["message"]
        indexer.write_status(tmp_path / "data", state="installing", message="")
        assert state.console.status()["index"]["phase"] == "installing"
    finally:
        srv.shutdown()


class _FakeConn:
    def __init__(self, status, data):
        self.status, self.data = status, data

    def request(self, *a, **k):
        pass

    def getresponse(self):
        return self

    def read(self):
        return self.data

    def close(self):
        pass


def test_agent_reads_container_logs_and_state():
    from labhub import agent

    frames = b"".join(bytes([s, 0, 0, 0]) + len(t).to_bytes(4, "big") + t
                      for s, t in ((1, "安裝 numpy\n".encode()), (2, b"pip failed\n")))
    d = agent.Docker("unix:///nope")
    d._conn = lambda timeout: _FakeConn(200, frames)
    assert d.logs("abc") == ["安裝 numpy", "pip failed"]
    d._conn = lambda timeout: _FakeConn(200, b"plain line\n")                  # 有 TTY：沒有標頭
    assert d.logs("abc") == ["plain line"]

    a = agent.Agent.__new__(agent.Agent)
    a.cfg = type("C", (), {"indexer_container": "qel-indexer"})()

    class D:
        def __init__(self, info):
            self.info = info

        def inspect(self, name):
            return self.info

        def logs(self, cid, tail=20):
            return ["Bind mount failed"]

    a.docker = D(None)
    assert a.indexer_status()["status"] == "missing"
    a.docker = D({"Id": "x", "RestartCount": 7, "State": {"Status": "restarting", "Running": False, "ExitCode": 1,
                                                          "Error": ""}})
    s = a.indexer_status()
    assert s["status"] == "restarting" and not s["running"] and s["restarts"] == 7 and s["log"] == ["Bind mount failed"]


@pytest.mark.skipif(shutil.which("node") is None, reason="沒有 node")
def test_console_index_card_texts():
    html = (ROOT / "labhub" / "static" / "portal.html").read_text(encoding="utf-8")
    fn = re.search(r"function indexCard\(ix, c\) \{.*?\n\}\n", html, re.S).group(0)
    js = ("const esc = t => String(t ?? '');\n" + fn + """
const cases = [
  [{}, {status: "missing"}],
  [{}, {status: "restarting", running: false, error: "", log: ["x", "Bind mount failed: .qel does not exist"]}],
  [{available: false, phase: "installing", message: "安裝"}, {status: "running", running: true, log: []}],
  [{available: true, state: "indexing", done: 5, todo: 9000, files: 9000}, {status: "running", running: true}],
  [{available: true, state: "idle", indexed: 9000, errors: 2, last_pass: Date.now() / 1000 - 120, key_ok: false}, undefined],
];
console.log(JSON.stringify(cases.map(([ix, c]) => indexCard(ix, c))));
""")
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert "沒有容器" in out[0][1] and ".qel" in out[0][2]
    assert "一直重新啟動" in out[1][1] and "Bind mount failed" in out[1][2]
    assert "安裝套件中" in out[2][1]
    assert out[3][1] == "5 / 9000"
    assert out[4][1] == "9000" and "2 個讀不了" in out[4][2] and ".qel" in out[4][2]
