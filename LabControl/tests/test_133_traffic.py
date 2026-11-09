"""1.0.33：Hub 各埠流量監控、分類限流、對外串流壓縮與省流模式。"""
from __future__ import annotations

import gzip
import json
import threading
import time
import urllib.request

import numpy as np
import pytest

from labhub import traffic as tf

OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def test_category_and_client_class(tmp_path):
    assert tf.category("/api/nodes/LAB-1/live") == "live"
    assert tf.category("/api/nodes/LAB-1/live/batch") == "live"
    assert tf.category("/api/m/LAB-1") == "live"
    assert tf.category("/api/files/LAB-1/a/b.nc") == "data"
    assert tf.category("/api/data/preview") == "data"
    assert tf.category("/api/notebook/20261109-abcdef/files/x.png") == "data"
    assert tf.category("/get/labcontrol/1.0.33/x.exe") == "updates"
    assert tf.category("/api/state") == "poll"
    assert tf.category("/api/nodes/LAB-1/commands") == "poll"
    assert tf.category("/api/notebook") == "api"
    assert tf.category("/network") == "pages"
    tr = tf.Traffic(tmp_path)
    assert tr.client_class("192.168.50.10") == ("lan", "192.168.50.10")
    assert tr.client_class("100.70.1.2")[0] == "vpn"
    assert tr.client_class("127.0.0.1")[0] == "local"
    assert tr.client_class("172.17.0.1")[0] == "local"
    assert tr.client_class("127.0.0.1", "100.64.0.9, 127.0.0.1") == ("vpn", "100.64.0.9")   # tailscale serve
    assert tr.client_class("192.168.50.10", "100.64.0.9")[0] == "lan"                      # 區網不信 XFF
    assert tr.client_class("8.8.8.8")[0] == "wan"
    assert tr.is_remote("local")
    tr.set_config({"local_remote": False})
    assert not tr.is_remote("local") and tr.is_remote("vpn")


def test_bucket_rate():
    b = tf.Bucket(100 * 1024)
    t0 = time.monotonic()
    b.take(100 * 1024)                         # 一開始滿的
    assert time.monotonic() - t0 < 0.1
    b.take(40 * 1024)
    assert time.monotonic() - t0 > 0.3


def test_pack_floats_lossless_and_economy():
    from labcontrol.remote import codec

    rng = np.random.default_rng(1)
    z = (rng.normal(size=5001) + 1j * rng.normal(size=5001)) * 0.3
    enc = codec.enc_array(z)
    raw_len = len(enc["re"]) + len(enc["im"])
    p = tf._pack_arrays({"shot": {"S21": enc}}, economy=False)["shot"]["S21"]
    assert set(p) == {"zre", "zim", "n"}
    back = codec.dec_array(p)
    assert np.array_equal(back, codec.dec_array(enc))                      # 無損
    e = tf._pack_arrays({"shot": {"S21": enc}}, economy=True)["shot"]["S21"]
    eb = codec.dec_array(e)
    assert np.max(np.abs(eb - z) / np.maximum(np.abs(z), 1e-9)) < 3e-3    # 尾數 10 位
    assert len(e["zre"]) + len(e["zim"]) < 0.6 * raw_len
    f = tf._pack_arrays({"f": codec.enc_array(np.linspace(1e9, 2e9, 101))["f"], "n": 101}, economy=False)
    assert np.allclose(codec.dec_array(f), np.linspace(1e9, 2e9, 101), rtol=1e-7)


def test_thin_trace():
    y = [0.0] * 1000
    y[537] = -30.0
    t = tf.thin_trace({"x": list(range(1000)), "y": y, "index": 3}, 300)
    assert len(t["y"]) <= 300 and min(t["y"]) == -30.0 and t["thinned"] == 1000 and t["index"] == 3


@pytest.fixture
def hub(tmp_path):
    from labhub.server import Config, make_server
    from tests import fake_paperlib

    psrv, _, purl = fake_paperlib.start()
    srv, state = make_server(Config(tmp_path / "hub", token="dev", paperlib_url=purl), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield url, state
    srv.shutdown()
    psrv.shutdown()


def _req(url, method="GET", body=None, headers=None, raw=False):
    data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Authorization": "Bearer dev", **(headers or {})})
    try:
        with OP.open(req, timeout=10) as r:
            b = r.read()
            return r.status, dict(r.headers), b
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_hub_counts_compresses_and_limits(hub):
    url, state = hub
    tr = tf.store(state)
    # 127.0.0.1 = NAS 本機轉入（算區網以外）→ 說可以 gzip 就 gzip
    code, h, b = _req(url + "/network", headers={"Accept-Encoding": "gzip"})
    assert code in (200, 302, 303)
    code, h, b = _req(url + "/api/traffic", headers={"Accept-Encoding": "gzip"})
    assert code == 200 and h.get("Content-Encoding") == "gzip"
    v = json.loads(gzip.decompress(b))
    assert v["ok"] and "pages" in v["today"] and v["today"]["pages"]["local"]["n"] >= 1
    code, h, b = _req(url + "/api/traffic")
    assert "Content-Encoding" not in h                                   # 沒說可以 → 不壓
    # 送 gzip 的 JSON（經 VPN 的量測電腦）
    ev = [{"t": "log", "level": "info", "message": "x" * 5000}]
    code, _, b = _req(url + "/api/nodes/N1/live/batch", "POST", gzip.compress(json.dumps({"events": ev}).encode()),
                      {"Content-Type": "application/json", "Content-Encoding": "gzip"})
    assert code == 200, b
    # 即時串流：看得懂 zs 才換格式
    from labcontrol.remote import codec

    z = np.exp(1j * np.linspace(0, 6, 2001)) * 0.5
    shot = {"t": "point.shot", "shot": {"S21": codec.enc_array(z)}}
    _req(url + "/api/nodes/N1/live/batch", "POST", {"events": [shot]}, {"Content-Type": "application/json"})
    code, h, b = _req(url + "/api/nodes/N1/live?after=0", headers={"X-Labhub-Enc": "zs"})
    out = json.loads(b)
    assert out["enc"] == "zs" and out["economy"] is False
    got = out["batches"][-1][1][0]["shot"]["S21"]
    assert "zre" in got and np.allclose(codec.dec_array(got), z, atol=1e-6)
    code, h, b = _req(url + "/api/nodes/N1/live?after=0")
    assert "re" in json.loads(b)["batches"][-1][1][0]["shot"]["S21"]        # 舊用戶端：照舊
    # 省流模式（手動開）
    tr.set_config({"economy": {"force": True}})
    out = json.loads(_req(url + "/api/nodes/N1/live?after=0", headers={"X-Labhub-Enc": "zs"})[2])
    assert out["economy"] is True and out["batches"][-1][1][0]["shot"]["S21"].get("q") == 10
    # 手機：trace 沒變就不重送；省流模式抽點
    trace = {"index": 0, "x": list(range(1000)), "y": [float(i % 7) for i in range(1000)], "min": {"x": 0, "y": 0}}
    _req(url + "/api/m/PC1/update", "POST", {"run": {"id": "r1", "status": "running"}}, {"Content-Type": "application/json"})
    _req(url + "/api/m/PC1/update", "POST", {"run": {"id": "r1", "status": "running"}, "trace": trace},
         {"Content-Type": "application/json"})
    d = json.loads(_req(url + "/api/m/PC1")[2])
    assert len(d["trace"]["y"]) <= 300 and d["tv"] >= 1
    d2 = json.loads(_req(url + f"/api/m/PC1?tv={d['tv']}")[2])
    assert d2["trace"] is None and d2["trace_same"]
    tr.set_config({"economy": {"force": False}})
    # 每日上限：網頁（區網以外）用完 → 429；網路用量頁、/api/traffic 不擋
    tr.set_config({"limits": {"pages": {"daily_mb": 0.0001}}})
    _req(url + "/notebook")
    code, _, b = _req(url + "/notebook")
    assert code == 429 and "上限" in json.loads(b)["error"]
    assert _req(url + "/api/traffic")[0] == 200
    tr.set_config({"limits": {"pages": {"scope": "all", "daily_mb": 0}}})
    assert _req(url + "/notebook")[0] != 429
    # 速度上限：數據檔 50 KB/s → 送 120 KB 的檔要 1 秒以上
    tr.set_config({"limits": {"data": {"rate_kbps": 50}}})
    blob = b"x" * (120 * 1024)
    assert _req(url + "/api/files/N1/big.bin", "PUT", blob, {"Content-Length": str(len(blob))})[0] == 200
    t0 = time.monotonic()
    code, _, b = _req(url + "/api/files/N1/big.bin")
    assert code == 200 and len(b) == len(blob) and time.monotonic() - t0 > 1.0
    tr.save(force=True)
    assert tf.Traffic(state.cfg.data_dir).cfg["limits"]["data"]["rate_kbps"] == 50
    assert tr.view(7)["ports"]
