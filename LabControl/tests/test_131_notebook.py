"""1.0.31：Hub 電子實驗記錄（/notebook）＋ Lab Control 量測結束自動新增。"""
from __future__ import annotations

import threading
import time
import urllib.request

import numpy as np
import pytest

from labhub import notebook as nbm


class Clock:
    def __init__(self, t=1_762_660_800.0):
        self.t = t

    def __call__(self):
        return self.t


ALICE = {"username": "alice", "display_name": "Alice"}
ADMIN = {"username": "admin", "display_name": "Admin", "owner": True}
CAROL = {"username": "carol", "display_name": "Carol"}


def test_store_edit_history_conflict_permissions(tmp_path):
    clk = Clock()
    nb = nbm.Notebook(tmp_path, now=clk)
    e = nb.create(ALICE, {"title": "YIG #3", "body": "第一版", "tags": "YIG, FMR，YIG", "links": [
        {"kind": "url", "ref": "javascript:alert(1)"}, {"kind": "data", "ref": "yig3.nc", "label": "數據"}]})
    assert e["tags"] == ["YIG", "FMR"] and e["links"] == [{"kind": "data", "ref": "yig3.nc", "label": "數據"}]
    assert e["rev"] == 1 and e["author"] == "alice"
    clk.t += 60
    nb.update(e["id"], ALICE, {"body": "第二版", "rev": 1})
    assert nb.get(e["id"])["rev"] == 2
    nb.update(e["id"], ALICE, {"body": "第二版", "rev": 2})               # 沒有改東西 → 不留版本
    assert nb.get(e["id"])["rev"] == 2
    with pytest.raises(nbm.Conflict) as ex:                               # 別人用舊版本存 → 409
        nb.update(e["id"], ADMIN, {"body": "站長改的", "rev": 1})
    assert ex.value.current["rev"] == 2 and ex.value.current["body"] == "第二版"
    nb.update(e["id"], ADMIN, {"body": "站長改的", "rev": 2})               # 站長可以改
    assert [h["rev"] for h in nb.history(e["id"])] == [2, 1]
    assert nb.version(e["id"], 1)["body"] == "第一版"
    with pytest.raises(PermissionError):
        nb.update(e["id"], CAROL, {"body": "x"})
    nb.update(e["id"], ALICE, {"editors": "carol"})
    nb.update(e["id"], CAROL, {"body": "Carol 補充", "editors": ""})        # 共同編輯者可以改內容，但不能改共同編輯者
    assert nb.get(e["id"])["editors"] == ["carol"] and nb.get(e["id"])["body"] == "Carol 補充"
    # 留言
    c = nb.comment(e["id"], CAROL, "  好結果  ")
    assert c["text"] == "好結果"
    with pytest.raises(PermissionError):
        nb.delete_comment(e["id"], c["id"], ALICE)
    nb.delete_comment(e["id"], c["id"], CAROL)
    # 正在編輯
    nb.touch_editing(e["id"], CAROL)
    assert nb.view(e["id"], ALICE)["editing"] == ["Carol"] and nb.view(e["id"], CAROL)["editing"] == []
    clk.t += 100
    assert nb.view(e["id"], ALICE)["editing"] == []
    # 附檔：檔名清乾淨、同名加編號、不能跑出資料夾
    f1 = nb.add_file(e["id"], ALICE, "../../a b.png", b"\x89PNG1")
    f2 = nb.add_file(e["id"], ALICE, "a b.png", b"\x89PNG2")
    assert f1["name"] == "a b.png" and f2["name"] == "a b_2.png"
    assert nb.file_path(e["id"], "a b_2.png").read_bytes() == b"\x89PNG2"
    with pytest.raises(KeyError):
        nb.file_path(e["id"], "../entries/" + e["id"] + ".json")
    with pytest.raises(PermissionError):
        nb.add_file(e["id"], {"username": "dave"}, "x.txt", b"x")
    # 重新載入
    nb2 = nbm.Notebook(tmp_path, now=clk)
    assert nb2.get(e["id"])["body"] == "Carol 補充" and len(nb2.get(e["id"])["files"]) == 2
    # 搜尋
    nb.create(CAROL, {"title": "VNA 校正", "body": "SOLT", "tags": ["VNA"], "project": {"id": "7", "name": "子", "path_ids": ["3", "7"]}})
    assert nb.search({"q": "carol 補充"})["total"] == 1
    assert nb.search({"tag": "VNA"})["items"][0]["title"] == "VNA 校正"
    assert nb.search({"project": "3"})["total"] == 1                      # 主專案也找得到子專案的記錄
    assert nb.search({"author": "alice"})["total"] == 1
    assert dict(nb.search({})["tags"])["YIG"] == 1
    # 刪除：作者或站長；移到回收
    with pytest.raises(PermissionError):
        nb.delete(e["id"], CAROL)
    nb.delete(e["id"], ALICE)
    assert e["id"] not in nb.entries and (tmp_path / "notebook" / "trash" / f"{e['id']}.json").exists()
    assert (tmp_path / "notebook" / "trash" / f"{e['id']}_files" / "a b.png").exists()


def test_auto_entry_idempotent_and_editable(tmp_path):
    clk = Clock()
    nb = nbm.Notebook(tmp_path, now=clk)
    rec = {"run_id": "r-1", "scheme": "FMR map", "status": "finished", "host": "LAB-1", "user": "alice",
           "started": clk.t - 600, "finished": clk.t, "points": 100, "planned": 100,
           "axes": [{"name": "Magnet", "unit": "mA", "start": 0.0, "stop": 500.0, "n": 101, "target": "mag.level"}],
           "measures": [{"instrument": "VNA1", "traces": ["S21", "S43"], "settings": {"power": "-10"}}],
           "setup": {"mag.ramp": "5"}, "files": ["fmr.nc"], "tags": ["YIG"], "project": {"id": "3", "name": "P"}}
    e = nb.auto(dict(rec))
    assert e["kind"] == "auto" and e["open_edit"] and e["title"].startswith("量測：FMR map")
    assert "| Magnet | 0 mA | 500 mA | 101 |" in e["body"] and "S21、S43" in e["body"] and "### 備註" in e["body"]
    assert e["tags"] == ["YIG"] and e["project"]["id"] == "3"
    e2 = nb.auto(dict(rec, files=["fmr.csv"]))                            # 同一個 run_id：更新同一筆
    assert e2["id"] == e["id"] and e2["auto"]["files"] == ["fmr.nc", "fmr.csv"] and len(nb.entries) == 1
    nb.update(e["id"], CAROL, {"body": e2["body"] + "\n觀察到兩個模態", "rev": 1})   # 任何人都可以補寫
    nb.auto(dict(rec, status="stopped"))                                   # 有人改過 → 不覆蓋內文
    assert "觀察到兩個模態" in nb.get(e["id"])["body"] and nb.get(e["id"])["auto"]["status"] == "stopped"
    assert nbm.Notebook(tmp_path).by_run["r-1"] == e["id"]
    with pytest.raises(ValueError):
        nb.auto({"scheme": "x"})


def test_http_routes(tmp_path):
    from labhub.server import Config, make_server
    from tests import fake_paperlib
    from tests.test_122 import _call

    psrv, _, purl = fake_paperlib.start()
    srv, state = make_server(Config(tmp_path / "hub", token="dev", paperlib_url=purl), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    dev = {"Authorization": "Bearer dev"}
    try:
        ow = _call(url + "/api/platform/login", "POST", {"username": "admin", "password": "adminpw"})[1]
        oc = f"labhub_token={ow['token']}"
        al = _call(url + "/api/platform/login", "POST", {"username": "alice", "password": "alicepw"})[1]
        ac = f"labhub_token={al['token']}"
        assert _call(url + "/api/notebook")[0] == 401
        code, page = _call(url + "/notebook", cookie=ac)
        assert code == 200 and "實驗記錄" in page and 'href="/notebook"' in page
        code, r = _call(url + "/api/notebook", "POST", {"title": "T", "body": "B", "tags": "a"}, ac)
        assert code == 200 and r["entry"]["can_edit"] and r["entry"]["author"] == "alice"
        eid = r["entry"]["id"]
        assert _call(url + "/api/notebook", "POST", {"title": "x"}, headers=dev)[0] == 403      # 裝置 token 不能手寫
        assert _call(url + f"/api/notebook/{eid}", "PUT", {"body": "B2", "rev": 1}, ac)[0] == 200
        code, r = _call(url + f"/api/notebook/{eid}", "PUT", {"body": "B3", "rev": 1}, oc)
        assert code == 409 and r["conflict"] and r["entry"]["body"] == "B2"
        assert _call(url + f"/api/notebook/{eid}/history", cookie=ac)[1]["history"][0]["rev"] == 1
        assert _call(url + f"/api/notebook/{eid}/history/1", cookie=ac)[1]["version"]["body"] == "B"
        assert _call(url + f"/api/notebook/{eid}/comments", "POST", {"text": "讚"}, oc)[0] == 200
        assert _call(url + f"/api/notebook/{eid}/editing", "PUT", {"on": True}, oc)[0] == 200
        assert _call(url + f"/api/notebook/{eid}", cookie=ac)[1]["entry"]["editing"] == ["站長"]
        # 附檔上傳 / 下載（svg 一律下載，不在網頁打開）
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(url + f"/api/notebook/{eid}/files?name=plot.png", data=b"\x89PNGdata",
                                     method="POST", headers={"Cookie": ac, "Content-Type": "image/png"})
        import json

        with op.open(req, timeout=10) as resp:
            f = json.loads(resp.read())["file"]
        req = urllib.request.Request(url + f["url"], headers={"Cookie": ac})
        with op.open(req, timeout=10) as resp:
            assert resp.read() == b"\x89PNGdata" and resp.headers["Content-Type"] == "image/png"
        req = urllib.request.Request(url + f"/api/notebook/{eid}/files?name=x.svg", data=b"<svg/>", method="POST",
                                     headers={"Cookie": ac})
        with op.open(req, timeout=10) as resp:
            g = json.loads(resp.read())["file"]
        with op.open(urllib.request.Request(url + g["url"], headers={"Cookie": ac}), timeout=10) as resp:
            assert resp.headers["Content-Type"] == "application/octet-stream" and "attachment" in resp.headers["Content-Disposition"]
        # 量測自動新增（裝置 token）
        code, r = _call(url + "/api/notebook/auto", "POST", {"run_id": "run-9", "scheme": "S", "files": ["a.nc"]},
                        headers=dev)
        assert code == 200
        assert _call(url + "/api/notebook/auto", "POST", {"run_id": "run-9", "scheme": "S"}, headers=dev)[1]["id"] == r["id"]
        lst = _call(url + "/api/notebook?kind=auto", cookie=ac)[1]
        assert lst["total"] == 1 and lst["items"][0]["id"] == r["id"]
        assert _call(url + "/api/notebook?q=B2", cookie=ac)[1]["total"] == 1
        # 刪除
        assert _call(url + f"/api/notebook/{r['id']}", "DELETE", None, ac)[0] == 403
        assert _call(url + f"/api/notebook/{eid}", "DELETE", None, ac)[0] == 200
        assert _call(url + f"/api/notebook/{eid}", cookie=ac)[0] == 404
    finally:
        srv.shutdown()
        psrv.shutdown()
        time.sleep(0.05)


# ---- Lab Control 端 ---------------------------------------------------------------
def test_labcontrol_record_and_post(monkeypatch):
    from labcontrol.data.dataset import AxisSpec, Dataset
    from labcontrol.integrations import notebook as lc

    ds = Dataset("FMR", [AxisSpec("Magnet", "mag.level", "A", np.linspace(0, 0.5, 11), "mA", 1e3),
                         AxisSpec("Repeat", "", "", np.arange(1, 4))], [],
                 {"run_id": "abc123", "created": "2026-11-09T10:00:00"})
    ds.records = [object()] * 20                       # 33 點只量了 20 點

    class Exp:
        name = "FMR map"
        config = {"procedure": {"readouts": [{"ref": "VNA1", "trace": "S21"}, {"ref": "VNA1", "trace": "S43"}]},
                  "setup": {"VNA1": {"power": -10.0, "if_bw": 1000}, "mag.ramp": 0.005},
                  "output": {"export": [{"type": "netcdf", "user": "alice", "comment": "樣品 3"}]}}

        def output_tags(self):
            return ["YIG"]

        def output_project(self):
            return {"id": "3", "name": "P"}

    monkeypatch.setattr(Dataset, "__len__", lambda self: len(self.records))
    rec = lc.record(Exp(), ds, ["/x/fmr.nc"])
    assert rec["run_id"] == "abc123" and rec["status"] == "stopped" and rec["points"] == 20 and rec["planned"] == 33
    assert rec["axes"][0] == {"name": "Magnet", "unit": "mA", "start": 0.0, "stop": 500.0, "n": 11, "target": "mag.level"}
    assert rec["axes"][1]["name"] == "Repeat"
    assert rec["measures"] == [{"instrument": "VNA1", "traces": ["S21", "S43"], "settings": {"power": "-10", "if_bw": "1000"}}]
    assert rec["setup"] == {"mag.ramp": "0.005"} and rec["files"] == ["fmr.nc"] and rec["user"] == "alice"
    assert rec["comment"] == "樣品 3" and rec["tags"] == ["YIG"] and rec["project"]["id"] == "3"

    sent = []

    class Hub:
        def post(self, path, body):
            sent.append((path, body))
            return {"id": "20261109-abcdef"}

    from labcontrol.integrations import platform as plat
    from labcontrol.remote import hub as rhub

    monkeypatch.setattr(plat, "cached_hub", lambda: Hub())
    monkeypatch.setattr(rhub, "hub_token", lambda: "")
    assert lc.post_run(Exp(), ds, [], wait=True) is None and not sent           # 沒有 token：不送
    monkeypatch.setattr(rhub, "hub_token", lambda: "tok")
    lc.post_run(Exp(), ds, [], wait=True)
    assert sent and sent[0][0] == "/api/notebook/auto" and sent[0][1]["scheme"] == "FMR map"
    # Hub 端吃得下這份內容
    body = nbm.auto_body(nbm.clean_auto(sent[0][1]))
    assert "中途停止" in body and "VNA1" in body
