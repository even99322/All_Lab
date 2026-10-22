"""v3.5：Hub 叫這台查授權伺服器 → 跑 lmutil lmstat → 把輸出送回 Hub。"""
import json
import os
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE = "Users of hfss_solve:  (Total of 4 licenses issued;  Total of 1 license in use)\n" \
         "    ming LAB-PC-A LAB-PC-A (v2025.0630) (s/1055 1), start Thu 10/22 15:46\n"


@pytest.mark.skipif(sys.platform == "win32", reason="假的 lmutil 用 shell 腳本")
def test_run_lmstat_tries_each_server(tmp_path):
    import usage
    exe = tmp_path / "lmutil"
    exe.write_text("#!/bin/sh\nif [ \"$4\" = \"1055@good\" ]; then printf '%s' \"" + SAMPLE.replace("\n", "\\n")
                   + "\"; else echo 'Cannot connect to license server system.'; fi\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    r = usage.run_lmstat("1055@bad; 1055@good", str(exe))
    assert r["server"] == "1055@good" and "Users of hfss_solve" in r["text"]
    r = usage.run_lmstat("1055@bad", str(exe))
    assert "Cannot connect" in r["text"]
    assert "error" in usage.run_lmstat("1055@x", None) or usage.find_lmutil()


def test_find_lmutil_override(tmp_path, monkeypatch):
    import updater
    import usage
    exe = tmp_path / "lmutil.exe"
    exe.write_text("x")
    monkeypatch.setattr(updater, "load_conf", lambda: {"lmutil": str(exe)})
    assert usage.find_lmutil() == str(exe)
    assert usage._version_key(r"C:\Program Files\ANSYS Inc\v251\licensingclient\winx64\lmutil.exe") == 251


def test_due_runs_lmstat_and_posts(monkeypatch):
    import usage
    got = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            got.append((self.path, body))
            out = json.dumps({"ok": True, "lmstat": {"server": "1055@a;1055@b", "due": True}}
                             if self.path == "/api/hfss/usage" else {"ok": True, "features": 1}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        hub = f"http://127.0.0.1:{srv.server_address[1]}"
        ident = {"host": "PC", "user": "u"}
        h, resp = usage.post_ex(dict(ident, aedt={"running": False}), [hub], timeout=2)
        assert h == hub and resp["lmstat"]["due"]
        calls = []
        r = usage.send_lmstat(hub, ident, resp["lmstat"]["server"],
                              runner=lambda s: calls.append(s) or {"text": SAMPLE, "server": "1055@a"})
        assert r == {"ok": True, "features": 1} and calls == ["1055@a;1055@b"]
        path, body = got[-1]
        assert path == "/api/hfss/lmstat" and body["host"] == "PC" and body["text"] == SAMPLE
        usage.send_lmstat(hub, ident, "1055@a", runner=lambda s: {"error": "找不到 lmutil"})
        assert got[-1][1]["error"] == "找不到 lmutil"
    finally:
        srv.shutdown()
