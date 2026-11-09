"""1.0.34：網路用量「即時」（每分鐘一格、1 分～1 天的總量與目前速率）；已經壓縮的回應不再壓一次；沒有預覽的原因與重建。"""
from __future__ import annotations

import gzip
import json
import threading
import urllib.request

from labhub import netusage as nu
from labhub.netrecent import Recent

OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Clock:
    def __init__(self, t=1_762_660_800.0):
        self.t = t

    def __call__(self):
        return self.t


def test_recent_windows_series_and_save(tmp_path):
    clk = Clock()
    r = Recent(tmp_path / "r.json", now=clk)
    for i in range(120):                                      # 兩小時，每分鐘 1 MB 下、0.5 MB 上
        r.add("nas", 512 * 1024, 1024 * 1024, clk.t - (119 - i) * 60)
    r.add("nas", 100, 200, dt=20)
    w = r.window("nas", 60)
    assert w["down"] == 1024 * 1024 + 200 and w["rate"]["down"] == 10
    assert r.window("nas", 600)["down"] == 10 * 1024 * 1024 + 200
    w = r.window("nas", 86400, series=True)
    assert len(w["series"]) == 1440 and w["down"] == 120 * 1024 * 1024 + 200 and w["step"] == 60
    assert w["series"][-1][2] == 1024 * 1024 + 200 and w["series"][0][2] == 0
    r.save(force=True)
    r2 = Recent(tmp_path / "r.json", now=clk)
    assert r2.window("nas", 3600)["down"] == r.window("nas", 3600)["down"]
    clk.t += 26 * 3600                                         # 超過 25 小時的丟掉
    r2.add("nas", 1, 1)
    r2.save(force=True)
    assert len(Recent(tmp_path / "r.json", now=clk).data["nas"]) == 1


def test_netusage_feeds_recent(tmp_path):
    from tests.test_132 import _status

    clk = Clock()
    st = nu.NetUsage(tmp_path, now=clk)
    st.cfg["nas"]["enabled"] = True
    # Tailscale
    st.poll_tailscale(_status(0, 0, 0, 0))
    clk.t += 20
    st.poll_tailscale(_status(2000, 1000, 40, 20))
    assert st.recent.window("ts:n1", 60) == {"up": 1000, "down": 2000, "rate": {"up": 50.0, "down": 100.0, "t": clk.t}}
    # 電腦：每分鐘回報今天的累計 → 差值
    day = st.today()
    st.report_device({"id": "nm-abc12345", "host": "PC", "days": {day: {"up": 100, "down": 1000}}, "rate": {"up": 1, "down": 2}})
    clk.t += 60
    st.report_device({"id": "nm-abc12345", "host": "PC", "days": {day: {"up": 400, "down": 5000}}, "rate": {"up": 5, "down": 66}})
    w = st.recent.window("pc:nm-abc12345", 600)
    assert w["up"] == 300 and w["down"] == 4000 and w["rate"]["down"] == 66
    # 路由器：今天累計的差值
    class FakeRouter:
        def __init__(self):
            self.up, self.down = 10.0, 100.0

        def traffic_meter(self):
            return {"TodayUpload": self.up, "TodayDownload": self.down}

        def info(self):
            return {}
        port, ssl = 5000, False

    fr = FakeRouter()
    st.cfg["router"]["host"] = "192.0.2.1"
    st.poll_router(fr)
    clk.t += 120
    fr.up, fr.down = 12.0, 130.0
    st.poll_router(fr)
    w = st.recent.window("router", 600)
    assert w["up"] == 2 * nu.MB and w["down"] == 30 * nu.MB
    v = st.recent_view(600, "pc:nm-abc12345")
    keys = [s["key"] for s in v["sources"]]
    assert {"nas", "pc:nm-abc12345", "ts:n1", "ts:n2"} <= set(keys) and "ts:n3" not in keys
    assert v["chart"]["key"] == "pc:nm-abc12345" and len(v["chart"]["series"]) == 10
    assert st.recent_view(5000, "")["window"] == 3600                          # 取最接近的視窗
    st.remove_pc("nm-abc12345")
    assert "pc:nm-abc12345" not in st.recent.data


def test_api_recent_and_no_double_gzip(tmp_path):
    from labhub.server import Config, Handler, make_server, route
    from tests import fake_paperlib
    from tests.test_122 import _call

    psrv, _, purl = fake_paperlib.start()
    srv, state = make_server(Config(tmp_path / "hub", token="dev", paperlib_url=purl), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    body = json.dumps({"ok": True, "x": "y" * 5000}).encode()

    @route("GET", r"/api/_test_pregz", auth=False)
    def _pre(h: Handler) -> None:
        h._send(200, gzip.compress(body), "application/json", {"Content-Encoding": "gzip"})
    try:
        req = urllib.request.Request(url + "/api/_test_pregz", headers={"Accept-Encoding": "gzip"})
        with OP.open(req, timeout=10) as r:
            assert r.headers["Content-Encoding"] == "gzip"
            assert json.loads(gzip.decompress(r.read()))["x"] == "y" * 5000     # 只壓一次
        tok = _call(url + "/api/platform/login", "POST", {"username": "alice", "password": "alicepw"})[1]["token"]
        nu.store(state).recent.add("hub:lan", 10, 20)
        code, d = _call(url + "/api/netusage/recent?window=600", cookie=f"labhub_token={tok}")
        assert code == 200 and d["window"] == 600 and any(s["key"] == "hub:remote" for s in d["sources"])
        # Hub 自己的請求也記進「Hub ⇄ 區網以外」（127.0.0.1 = NAS 本機轉入）
        assert nu.store(state).recent.window("hub:remote", 600)["up"] > 0
    finally:
        Handler.routes[:] = [r for r in Handler.routes if r[2] is not _pre]
        srv.shutdown()
        psrv.shutdown()


def test_no_preview_reason():
    from labhub.data_routes import no_preview_reason

    base = {"status": "ok", "error": None}
    assert "還沒算到" in no_preview_reason(dict(base, preview=None))
    assert "大小上限" in no_preview_reason(dict(base, preview="none: too big"))
    assert "中斷" in no_preview_reason(dict(base, preview="none: indexer stopped"))
    assert no_preview_reason(dict(base, preview="none: MemoryError: ")) == "建立預覽失敗：MemoryError: "
    assert "讀不懂" in no_preview_reason({"status": "error", "error": "bad header", "preview": "none"})
