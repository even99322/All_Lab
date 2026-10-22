"""1.0.19：HFSS 授權占用——模擬工具回報、Hub 列出誰在用、標註是誰。"""
from __future__ import annotations

import json
import threading
import time
import urllib.request


def _call(url, method="GET", body=None, cookie=None):
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_usage_report_label_and_seats(tmp_path, monkeypatch):
    from labhub import hfss_usage
    from labhub.server import Config, make_server
    from tests import fake_paperlib

    psrv, _, purl = fake_paperlib.start()
    srv, state = make_server(Config(tmp_path / "hub", token="dev", paperlib_url=purl), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        now = time.time()
        # 模擬工具回報（不用登入）
        code, r = _call(url + "/api/hfss/usage", "POST", {
            "host": "LAB-PC1", "user": "qel", "name": "", "version": "3.4",
            "aedt": {"running": True, "count": 1, "since": now - 600},
            "task": {"name": "SIM_S", "project": "YIG_test", "started": now - 120, "cores": 8, "tasks": 1, "gpus": 0}})
        assert code == 200 and r["key"] == "lab-pc1@qel"
        _call(url + "/api/hfss/usage", "POST", {"host": "LAB-PC2", "user": "student", "name": "小明", "version": "3.4",
                                               "aedt": {"running": False}})
        # 看要登入
        assert _call(url + "/api/hfss/usage")[0] == 401
        code, login = _call(url + "/api/platform/login", "POST", {"username": "alice", "password": "alicepw"})
        cookie = f"labhub_token={login['token']}"
        code, v = _call(url + "/api/hfss/usage", cookie=cookie)
        assert code == 200 and v["used"] == 1 and v["online"] == 2
        first = v["items"][0]
        assert first["host"] == "LAB-PC1" and first["using"] and first["task"]["name"] == "SIM_S"
        assert v["items"][1]["who"] == "小明"                                       # 工具裡填的名字
        # 標註是誰
        assert _call(url + "/api/hfss/usage/label", "PUT", {"key": "lab-pc1@qel", "label": "譯文"}, cookie)[0] == 200
        v = _call(url + "/api/hfss/usage", cookie=cookie)[1]
        assert v["items"][0]["label"] == "譯文" and v["items"][0]["who"] == "譯文"
        # 授權總數只有站長能改
        assert _call(url + "/api/hfss/usage/seats", "PUT", {"seats": 4}, cookie)[0] == 403
        code, ow = _call(url + "/api/platform/login", "POST", {"username": "admin", "password": "adminpw"})
        oc = f"labhub_token={ow['token']}"
        assert _call(url + "/api/hfss/usage/seats", "PUT", {"seats": 4}, oc)[0] == 200
        assert _call(url + "/api/hfss/usage", cookie=cookie)[1]["seats"] == 4
        # 關掉工具 → 離線、不占用；Hub 重開後還記得標註與電腦
        _call(url + "/api/hfss/usage", "POST", {"host": "LAB-PC1", "user": "qel", "aedt": {"running": False}, "closing": True})
        v = _call(url + "/api/hfss/usage", cookie=cookie)[1]
        pc1 = next(x for x in v["items"] if x["host"] == "LAB-PC1")
        assert not pc1["online"] and not pc1["using"] and v["used"] == 0 and pc1["last_aedt"]
        st2 = hfss_usage.UsageStore(tmp_path / "hub")
        again = {x["key"]: x for x in st2.view()["items"]}
        assert again["lab-pc1@qel"]["label"] == "譯文" and st2.seats == 4 and not again["lab-pc1@qel"]["online"]
        # 頁面
        req = urllib.request.Request(url + "/licenses", headers={"Cookie": cookie})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as r:
            page = r.read().decode()
        assert "HFSS 授權占用" in page and 'href="/licenses"' in page
        # 刪掉一台（站長）
        assert _call(url + "/api/hfss/usage/lab-pc2@student", "DELETE", None, oc)[0] == 200
        assert len(_call(url + "/api/hfss/usage", cookie=cookie)[1]["items"]) == 1
    finally:
        srv.shutdown()
        psrv.shutdown()


def test_usage_offline_after_silence(tmp_path, monkeypatch):
    from labhub import hfss_usage

    st = hfss_usage.UsageStore(tmp_path)
    st.report({"host": "PC", "user": "u", "aedt": {"running": True}}, "1.2.3.4")
    assert st.view()["used"] == 1
    monkeypatch.setattr(hfss_usage, "OFFLINE_S", 0.0)
    assert st.view()["used"] == 0 and not st.view()["items"][0]["online"]
