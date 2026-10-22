"""1.0.20：直接問授權伺服器（lmutil lmstat）——解析、一次只叫一台去查、合併標註。"""
from __future__ import annotations

import datetime as dt
import json
import threading
import urllib.request

SAMPLE = r"""lmutil - Copyright (c) 1989-2023 Flexera. All Rights Reserved.
Flexible License Manager status on Thu 10/22/2026 16:10

[Detecting lmgrd processes...]
License server status: 1055@10.0.0.11
    License file(s) on 10.0.0.11: C:\Program Files\ANSYS Inc\Shared Files\Licensing\license_files\ansyslmd.lic:

10.0.0.11: license server UP (MASTER) v11.19.5

Vendor daemon status (on 10.0.0.11):

  ansyslmd: UP v11.19.5
Feature usage info:

Users of electronics_desktop:  (Total of 4 licenses issued;  Total of 2 licenses in use)

  "electronics_desktop" v2025.0630, vendor: ansyslmd, expiry: 31-dec-2026
  vendor_string: customer:00123456
  floating license

    ming LAB-PC-A LAB-PC-A (v2025.0630) (10.0.0.11/1055 1201), start Thu 10/22 15:45
    student LAB-PC-B LAB-PC-B (v2025.0630) (10.0.0.11/1055 1302), start Thu 10/22 9:02

Users of hfss_solve:  (Total of 4 licenses issued;  Total of 1 license in use)

  "hfss_solve" v2025.0630, vendor: ansyslmd, expiry: 31-dec-2026
  floating license

    ming LAB-PC-A LAB-PC-A (v2025.0630) (10.0.0.11/1055 2201), start Thu 10/22 15:46

Users of anshpc:  (Total of 32 licenses issued;  Total of 8 licenses in use)

  "anshpc" v2025.0630, vendor: ansyslmd, expiry: 31-dec-2026
  floating license

    ming LAB-PC-A LAB-PC-A (v2025.0630) (10.0.0.11/1055 3301), start Thu 10/22 15:46, 8 licenses

Users of mech_1:  (Total of 2 licenses issued;  Total of 0 licenses in use)

Users of node_thing:  (Uncounted, node-locked)
"""
NOW = dt.datetime(2026, 10, 22, 16, 10).timestamp()


def test_parse_lmstat():
    from labhub import lmstat

    s = lmstat.parse(SAMPLE, NOW)
    assert s["status"] == "UP" and s["role"] == "MASTER" and s["version"] == "v11.19.5" and not s["error"]
    f = {x["name"]: x for x in s["features"]}
    assert f["electronics_desktop"]["issued"] == 4 and f["electronics_desktop"]["in_use"] == 2
    assert [u["user"] for u in f["electronics_desktop"]["users"]] == ["ming", "student"]
    assert f["anshpc"]["users"][0]["count"] == 8 and f["hfss_solve"]["in_use"] == 1
    assert f["node_thing"]["issued"] == 0 and "Uncounted" in f["node_thing"]["note"]
    assert f["electronics_desktop"]["users"][1]["start"] == dt.datetime(2026, 10, 22, 9, 2).timestamp()
    devs = {d["host"]: d for d in lmstat.devices(s)}
    ev = devs["LAB-PC-A"]
    assert ev["solve"] == 1 and ev["hpc"] == 8 and ev["desktop"] == 1 and ev["electronics"]
    assert ev["since"] == dt.datetime(2026, 10, 22, 15, 45).timestamp()
    assert lmstat.devices(s)[0]["host"] == "LAB-PC-B"            # 最早開始的排前面
    summ = {x["title"]: x for x in lmstat.summary(s)}
    assert summ["Electronics Desktop"] == {"title": "Electronics Desktop", "in_use": 2, "issued": 4, "unit": ""}
    assert summ["HPC"]["in_use"] == 8 and summ["HPC"]["issued"] == 32
    # 跨年：1/1 看 12/31 開的
    jan = dt.datetime(2027, 1, 1, 1, 0).timestamp()
    s2 = lmstat.parse("Users of hfss_solve:  (Total of 1 license issued;  Total of 1 license in use)\n"
                      "    a PC PC (v1) (s/1055 1), start Thu 12/31 23:10\n", jan)
    assert s2["features"][0]["users"][0]["start"] == dt.datetime(2026, 12, 31, 23, 10).timestamp()
    # 連不到
    bad = lmstat.parse("lmgrd is not running: Cannot connect to license server system. (-15,570:10061)\n", NOW)
    assert bad["error"] == "連不到授權伺服器" and not bad["features"]


def test_store_leases_and_merges(tmp_path, monkeypatch):
    from labhub import hfss_usage

    st = hfss_usage.UsageStore(tmp_path)
    r = st.report({"host": "LAB-PC-A", "user": "ming", "aedt": {"running": True}}, "1.1.1.1")
    assert "lmstat" not in r                                            # 還沒設定伺服器
    st.set_server(" 1055@10.0.0.11 ; 1055@100.64.0.80 ")
    assert st.server == "1055@10.0.0.11;1055@100.64.0.80"
    a = st.report({"host": "LAB-PC-A", "user": "ming", "name": "譯文"}, "1.1.1.1")["lmstat"]
    b = st.report({"host": "LAB-PC2", "user": "qel"}, "1.1.1.2")["lmstat"]
    assert a["due"] and not b["due"] and a["server"] == st.server       # 一次只叫一台
    # 別台搶著送 → 不收；叫到的那台送 → 收
    try:
        st.lmstat({"host": "LAB-PC2", "user": "qel", "text": SAMPLE}, "x")
        raise AssertionError("應該被拒")
    except hfss_usage.HubHTTPError as e:
        assert e.code == 409
    st.lmstat({"host": "LAB-PC-A", "user": "ming", "text": SAMPLE, "server": "1055@10.0.0.11"}, "x")
    try:
        st.lmstat({"host": "LAB-PC2", "user": "qel", "text": ""}, "x")
        raise AssertionError("應該被拒")
    except hfss_usage.HubHTTPError as e:
        assert e.code == 409
    assert not st.report({"host": "LAB-PC-A", "user": "ming", "name": "譯文"}, "1.1.1.1")["lmstat"]["due"]  # 剛查過
    v = st.view()["lm"]
    assert v["ok"] and not v["stale"] and v["from_host"] == "LAB-PC-A"
    devs = {d["host"]: d for d in v["devices"]}
    assert devs["LAB-PC-A"]["who"] == "譯文" and devs["LAB-PC-A"]["tool"]["online"]
    assert devs["LAB-PC-B"]["tool"] is None and devs["LAB-PC-B"]["who"] == ""
    # 沒裝模擬工具的電腦也能標註
    st.set_label(devs["LAB-PC-B"]["key"], "學弟")
    assert {d["host"]: d for d in st.view()["lm"]["devices"]}["LAB-PC-B"]["who"] == "學弟"
    # 30 秒後再叫；查不了（沒有 lmutil）→ 換別台
    monkeypatch.setattr(hfss_usage, "LM_EVERY", 0.0)
    assert st.report({"host": "LAB-PC2", "user": "qel"}, "1.1.1.2")["lmstat"]["due"]
    st.lmstat({"host": "LAB-PC2", "user": "qel", "error": "找不到 lmutil.exe"}, "x")
    assert not st.report({"host": "LAB-PC2", "user": "qel"}, "1.1.1.2")["lmstat"]["due"]
    assert st.report({"host": "LAB-PC-A", "user": "ming"}, "1.1.1.1")["lmstat"]["due"]
    assert st.view()["lm"]["error"]["text"] == "找不到 lmutil.exe"
    # 伺服器設定記得；格式錯 → 400
    assert hfss_usage.UsageStore(tmp_path).server == st.server
    try:
        st.set_server("1055@x; rm -rf /")
        raise AssertionError("應該被拒")
    except hfss_usage.HubHTTPError as e:
        assert e.code == 400


def _call(url, method="GET", body=None, cookie=None):
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_http_flow(tmp_path):
    from labhub.server import Config, make_server
    from tests import fake_paperlib

    psrv, _, purl = fake_paperlib.start()
    srv, state = make_server(Config(tmp_path / "hub", token="dev", paperlib_url=purl), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        login = _call(url + "/api/platform/login", "POST", {"username": "alice", "password": "alicepw"})[1]
        uc = f"labhub_token={login['token']}"
        ow = _call(url + "/api/platform/login", "POST", {"username": "admin", "password": "adminpw"})[1]
        oc = f"labhub_token={ow['token']}"
        assert _call(url + "/api/hfss/usage/server", "PUT", {"server": "1055@10.0.0.11"}, uc)[0] == 403
        assert _call(url + "/api/hfss/usage/server", "PUT", {"server": "1055@10.0.0.11"}, oc)[0] == 200
        code, r = _call(url + "/api/hfss/usage", "POST", {"host": "LAB-PC-A", "user": "ming"})
        assert code == 200 and r["lmstat"]["due"]
        code, r = _call(url + "/api/hfss/lmstat", "POST", {"host": "LAB-PC-A", "user": "ming", "text": SAMPLE})
        assert code == 200 and r["features"] == 5
        v = _call(url + "/api/hfss/usage", cookie=uc)[1]["lm"]
        assert v["ok"] and len(v["devices"]) == 2 and v["server"] == "1055@10.0.0.11"
    finally:
        srv.shutdown()
        psrv.shutdown()
