"""v3.4：YIG 3D 預覽的場景、授權回報的內容。"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_yig_scene():
    from dxf_model import Design
    from yig_view import _clip_face, scene_from
    d = Design.template("MS", 30, 20, width=1.0)
    sc = scene_from(d, 0.8, 0.035, {"yig_diameter": 0.4, "yig_gap": 0.1, "yig_pos_mode": "auto"})
    (c, r) = sc["sphere"]
    assert c == pytest.approx((15, 10, 0.835 + 0.1 + 0.2)) and r == pytest.approx(0.2)
    labels = sc["labels"]
    assert "YIG 球心" in labels and any("基板表面" in s for s in labels) and any("訊號線 頂面" in s for s in labels)
    i = labels.index("基板表面（b）")
    assert sc["verts"][i][2] == pytest.approx(0.8)                                   # 上基板表面 z = Die 厚
    assert any(v[2] == pytest.approx(0.835) for v in sc["verts"])                    # 走線頂面
    # 裁到方框
    top = [(0, 0, 0.8), (30, 0, 0.8), (30, 20, 0.8), (0, 20, 0.8)]
    clipped = _clip_face(top, (10, 5, 12, 7))
    assert len(clipped) == 1 and {round(p[0]) for p in clipped[0]} == {10, 12}
    side = [(0, 10, 0.8), (30, 10, 0.8), (30, 10, 0.835), (0, 10, 0.835)]
    assert _clip_face(side, (10, 5, 12, 15))[0][0][:2] == pytest.approx((10, 10))


def test_usage_post(monkeypatch):
    import usage
    got = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):  # noqa: N802
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        hub = f"http://127.0.0.1:{srv.server_address[1]}"
        p = dict(usage.identity(), aedt=usage.aedt_status(), task=None, closing=False)
        assert usage.post(p, ["http://127.0.0.1:9", hub], timeout=2) == hub         # 第一個連不到 → 換下一個
        assert got[0]["host"] and got[0]["version"] and "running" in got[0]["aedt"]
    finally:
        srv.shutdown()
