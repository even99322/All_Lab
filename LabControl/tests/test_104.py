"""1.0.4：NAS 上的數據索引（indexer → Hub /api/index → 讀檔程式）與手機看即時量測（Lab Control → Hub /api/m → /m）。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from tests.test_102 import _run, _split_scheme, _station

ROOT = Path(__file__).resolve().parents[1]
LLVCORE = ROOT / "labhub" / "llvcore"


# ---- 共用 -------------------------------------------------------------------------
@pytest.fixture(scope="module")
def nc_files(tmp_path_factory):
    """Lab Control 量出來的 .nc（讀檔程式讀得懂）：data_158600mA.nc、data_158610mA.nc。"""
    st = _station()
    try:
        out = tmp_path_factory.mktemp("meas")
        _run(_split_scheme(dict(start=158.6, stop=158.61, step=0.01)), st, out)
    finally:
        st.close()
    files = sorted(out.rglob("data_*.nc"))
    assert len(files) == 2
    return files


def _share(tmp_path: Path, files) -> Path:
    share = tmp_path / "ccuqel" / "數據集散地"
    (share / "proj" / "Data_1014").mkdir(parents=True)
    (share / ".qel").mkdir()
    (share / "@eaDir").mkdir()
    for f in files:
        shutil.copy2(f, share / "proj" / "Data_1014" / f.name)
    shutil.copy2(files[0], share / "@eaDir" / "skip.nc")            # Synology 的縮圖資料夾：不索引
    old = time.time() - 600
    for f in share.rglob("*.nc"):
        os.utime(f, (old, old))
    return share


def _hub(data: Path, token: str = "tok"):
    from labhub.server import Config, make_server

    srv, state = make_server(Config(data, token=token), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state, f"http://127.0.0.1:{srv.server_address[1]}"


def _get(url: str, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


@pytest.fixture()
def llvcore():
    sys.path.insert(0, str(LLVCORE))
    yield
    sys.path.remove(str(LLVCORE))


# ---- 索引程式 ---------------------------------------------------------------------
def test_indexer_pass(tmp_path, nc_files, llvcore):
    from labhub import indexer
    from labhub.index_codec import decode_arrays

    share, data = _share(tmp_path, nc_files), tmp_path / "data"
    key = indexer.ensure_key(data, share / ".qel")
    assert (share / ".qel" / "index_key").read_text() == key == indexer.ensure_key(data, share / ".qel")
    fresh = share / "proj" / "Data_1014" / "writing.nc"
    shutil.copy2(nc_files[0], fresh)                                   # 剛寫的檔：下一輪再做
    ix = indexer.Indexer(share, data)
    r = ix.pass_once()
    assert r["files"] == 3 and r["indexed"] == 2 and r["pending"] == 1
    rows = {row["rel"]: dict(row) for row in ix.con.execute("SELECT * FROM files")}
    assert set(rows) == {f"proj/Data_1014/{f.name}" for f in nc_files}
    row = rows[f"proj/Data_1014/{nc_files[0].name}"]
    assert row["status"] == "ok" and row["preview"] == "ok" and row["sweep_dimension"].startswith("2D")
    meta, arrays = decode_arrays(indexer.preview_path(row["rel"], data).read_bytes())
    assert meta["kind"] == "2d" and arrays["z"].shape == (3, 11)
    # 1.0.23：VNA 是複數 → 另存原始資料（網頁可以換相位、實部、虛部）
    assert meta.get("complex") and arrays["zre"].shape == arrays["zim"].shape == (3, 11)
    assert np.allclose(20 * np.log10(np.hypot(arrays["zre"], arrays["zim"])), arrays["z"], atol=1e-3)
    assert {r["rel"] for r in ix.con.execute("SELECT rel FROM folders")} == {"proj", "proj/Data_1014"}
    assert ix.pass_once()["indexed"] == 0                              # 沒變：不再解析
    fresh.unlink()
    (share / "proj" / "Data_1014" / nc_files[1].name).unlink()
    r = ix.pass_once()
    assert r["removed"] == 1 and not indexer.preview_path(f"proj/Data_1014/{nc_files[1].name}", data).exists()
    m = indexer.get_meta(ix.con)
    assert m["state"] == "idle" and m["complete"] and m["llv_version"] == json.loads(
        (LLVCORE / "SOURCE.json").read_text())["llv_version"]


def test_indexer_skips_a_file_that_crashed_it(tmp_path, nc_files, llvcore):
    from labhub import indexer

    share, data = _share(tmp_path, nc_files), tmp_path / "data"
    ix = indexer.Indexer(share, data)
    bad = f"proj/Data_1014/{nc_files[0].name}"
    st = (share / bad).stat()
    indexer.set_meta(ix.con, current={"rel": bad, "size": st.st_size, "mtime": st.st_mtime})
    ix.con.commit()
    ix2 = indexer.Indexer(share, data)                                 # 重新啟動
    assert ix2.pass_once()["indexed"] == 1                             # 只做另一個
    row = ix2.con.execute("SELECT status, error FROM files WHERE rel = ?", (bad,)).fetchone()
    assert row["status"] == "error" and "stopped" in row["error"]


def test_preview_reduction_keeps_narrow_dips():
    from labhub.index_codec import _reduce

    z = np.zeros((3, 10000))
    z[1, 5003] = -40.0
    out, x = _reduce(z, np.arange(10000.0), 1, 800)
    assert out.shape[0] == 3 and 700 < out.shape[1] <= 800 and out[1].min() == -40.0 and x.size == out.shape[1]


# ---- Hub API ---------------------------------------------------------------------
def test_hub_index_api(tmp_path, nc_files, llvcore):
    from labhub import indexer

    share, data = _share(tmp_path, nc_files), tmp_path / "data"
    key = indexer.ensure_key(data, share / ".qel")
    indexer.Indexer(share, data).pass_once()
    srv, _state, base = _hub(data)
    try:
        assert _get(base + "/api/index/status")[0] == 401
        assert _get(base + "/api/index/status", {"X-QEL-Share-Key": "wrong"})[0] == 401
        code, body, _ = _get(base + "/api/index/status", {"Authorization": "Bearer tok"})
        assert code == 200 and json.loads(body)["files"] == 2
        code, body, hdr = _get(base + "/api/index/tree?root=proj", {"X-QEL-Share-Key": key})
        tree = json.loads(body)
        assert code == 200 and tree["folders"] == ["Data_1014"]
        e = next(x for x in tree["entries"] if x["rel"] == f"Data_1014/{nc_files[0].name}")
        assert e["preview"] and e["status"] == "ok" and isinstance(e["file_tags"], list)
        assert _get(base + "/api/index/tree?root=nothing", {"X-QEL-Share-Key": key})[0] == 404
        assert _get(base + "/api/index/tree?root=../etc", {"X-QEL-Share-Key": key})[0] == 400
        q = f"/api/index/preview?path=proj/{e['rel']}&size={e['size']}&mtime={e['mtime'] + 1.5}"
        code, blob, _ = _get(base + q, {"X-QEL-Share-Key": key})
        assert code == 200 and blob[:2] == b"PK"
        q = f"/api/index/preview?path=proj/{e['rel']}&size={e['size'] + 1}&mtime={e['mtime']}"
        assert _get(base + q, {"X-QEL-Share-Key": key})[0] == 404            # 改過了：讀檔程式自己讀
    finally:
        srv.shutdown()


def test_no_index_yet(tmp_path):
    srv, _state, base = _hub(tmp_path / "data")
    try:
        code, body, _ = _get(base + "/api/index/status", {"Authorization": "Bearer tok"})
        assert code == 404 and "indexer" in json.loads(body)["error"]
    finally:
        srv.shutdown()


# ---- 讀檔程式（llvcore 裡的同一份 hub_index）-----------------------------------------------
def test_reader_takes_the_index(tmp_path, nc_files, llvcore, monkeypatch):
    from labhub import indexer

    share, data = _share(tmp_path, nc_files), tmp_path / "data"
    indexer.ensure_key(data, share / ".qel")
    indexer.Indexer(share, data).pass_once()
    srv, _state, base = _hub(data)
    monkeypatch.setenv("QEL_HUB", base)
    try:
        from app.core import hub_index
        from app.core.database_scanner import DatabaseScanner

        hub_index.forget()
        root = share / "proj"
        assert hub_index.split_share(str(root)) == (str(share), "proj")
        assert hub_index.split_share(r"\\192.168.50.2\ccuqel\數據集散地\a\b")[1] == "a/b"
        assert hub_index.split_share(r"Z:\數據集散地\x")[0].endswith("數據集散地")
        assert hub_index.split_share("/tmp/elsewhere") is None
        records = hub_index.records_for(root)
        assert set(records) == {f"Data_1014/{f.name}" for f in nc_files}

        def no_parse(*_a, **_k):
            raise AssertionError("an indexed file must not be opened")

        monkeypatch.setattr(DatabaseScanner, "_scan_one", staticmethod(no_parse))
        result = DatabaseScanner.scan(root, remote_index=lambda: hub_index.records_for(root))
        assert len(result.entries) == 2 and all(e.metadata_complete and e.status == "ok" for e in result.entries)
        entry = result.entries[0]
        preview = hub_index.fetch_preview(entry.absolute_path, entry.size_bytes, entry.mtime)
        assert preview is not None and preview.from_index and preview.log_name
        assert hub_index.fetch_preview(entry.absolute_path, entry.size_bytes + 5, entry.mtime) is None
        (share / ".qel" / "index_key").write_text("wrong")              # 鑰匙不對 → 當作沒有索引
        hub_index.forget()
        assert hub_index.records_for(root) is None
    finally:
        srv.shutdown()


def test_llvcore_matches_the_reader_source():
    """llvcore 是讀檔程式的複本：有讀檔程式的原始碼時，兩邊要一樣（改讀檔程式後執行 tools/sync_llvcore.py）。"""
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        from sync_llvcore import source_info
    finally:
        sys.path.pop(0)
    candidates = [os.environ.get("LLV_SRC", ""), str(ROOT.parent / "LabLogViewer"), "/home/claude/LLV_work"]
    src = next((Path(c) for c in candidates if c and (Path(c) / "app" / "core" / "hub_index.py").is_file()), None)
    if src is None:
        pytest.skip("沒有讀檔程式的原始碼")
    assert json.loads((LLVCORE / "SOURCE.json").read_text(encoding="utf-8")) == source_info(src)


def test_compose_and_agent(tmp_path):
    import yaml

    from labhub import agent

    d = yaml.safe_load((ROOT / "deploy" / "platform" / "docker-compose.yml").read_text(encoding="utf-8"))
    ix = d["services"]["indexer"]
    assert ix["command"] == ["python", "-m", "labhub.indexer"] and ix["image"].startswith("python:")
    assert any(v.endswith(":/share:ro") for v in ix["volumes"]) and any(":/share_key" in v for v in ix["volumes"])
    assert d["services"]["agent"]["environment"]["INDEXER_CONTAINER"] == ix["container_name"]

    class FakeDocker:
        def __init__(self, info):
            self.info, self.restarted = info, []

        def inspect(self, name):
            return self.info

        def restart(self, cid, t=15):
            self.restarted.append(cid)

    class Job:
        def __init__(self):
            self.said = []

        def say(self, m):
            self.said.append(m)

    a = agent.Agent.__new__(agent.Agent)
    a.cfg = type("C", (), {"indexer_container": "qel-indexer"})()
    a.docker = FakeDocker({"Id": "abc"})
    job = Job()
    a._restart_indexer(job)
    assert a.docker.restarted == ["abc"] and job.said
    a.docker = FakeDocker(None)                                         # 沒有索引容器：略過
    a._restart_indexer(Job())
    assert a.docker.restarted == []


# ---- 手機看即時量測 -----------------------------------------------------------------
def test_live_publisher_to_mobile_page(tmp_path):
    from labcontrol.remote.hub import Hub
    from labcontrol.remote.live_publish import LivePublisher

    srv, state, base = _hub(tmp_path / "data")
    hub = Hub(base, "tok")
    st = _station()
    pub = LivePublisher(st, lambda: hub, lambda: "LAB-1", interval=60)        # 手動送
    pub.start()
    sent = []
    st.bus.subscribe("point.committed", lambda t, p: sent.append(pub.publish_once()) if len(sent) < 4 else None)
    try:
        _run(_split_scheme(dict(start=158.6, stop=158.61, step=0.01)), st, tmp_path / "out")
        assert pub.publish_once() and all(sent)
        auth = {"Authorization": "Bearer tok"}
        code, body, _ = _get(base + "/api/m", auth)
        lst = json.loads(body)
        assert code == 200 and lst["stations"][0]["name"] == "LAB-1" and lst["stations"][0]["online"]
        assert lst["stations"][0]["run"]["state"] == "finished"
        d = json.loads(_get(base + "/api/m/LAB-1", auth)[1])
        f = d["frame"]
        assert f["cols"] == [0.0, 1.0, 2.0] and set(f["rows"]) == {"0", "1", "2"} and f["reset"]
        assert f["y0"] is not None and f["y_label"].startswith("Frequency") and "158.61" in f["label"]
        assert d["trace"]["min"]["y"] is not None and len(d["trace"]["x"]) == len(d["trace"]["y"])
        assert any("結束：完成" in line["message"] for line in d["log"])
        again = json.loads(_get(base + f"/api/m/LAB-1?frame={f['id']}&since={d['ver']}", auth)[1])
        assert again["frame"]["rows"] == {} and not again["frame"]["reset"]       # 沒有新的列
        state.mobile.stations.clear()                                           # Hub 重新啟動
        pub._dirty = True
        body = pub.payload()
        assert not body["frame"]["reset"]
        r = hub.post("/api/m/LAB-1/update", body)
        assert r.get("need_full")
        assert pub.publish_once() and pub.publish_once()                         # 第一次 need_full，第二次整張
        d = json.loads(_get(base + "/api/m/LAB-1", auth)[1])
        assert set(d["frame"]["rows"]) == {"0", "1", "2"}
    finally:
        pub.stop()
        st.close()
        srv.shutdown()


def test_rollback_clears_columns():
    from labhub.mobile_routes import MobileStore

    s = MobileStore()
    s.update("A", {"run": {"id": "r"}, "frame": {"id": "r:0", "reset": True, "cols": [0, 1, 2],
                                                 "rows": {"0": [1], "1": [2], "2": [3]}}})
    s.update("A", {"frame": {"id": "r:0", "clear_from": 1, "rows": {"1": [5]}}})
    d = s.detail("A", None, -1)
    assert d["frame"]["rows"] == {"0": [1], "1": [5]}
    assert s.update("A", {"frame": {"id": "other", "rows": {"0": [1]}}}).get("need_full")


def test_mobile_page_login_and_files(tmp_path):
    srv, _state, base = _hub(tmp_path / "data")
    try:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            opener.open(base + "/m", timeout=5)
            raise AssertionError("should redirect")
        except urllib.error.HTTPError as e:
            assert e.code == 303 and e.headers["Location"].startswith("/login?next=/m")
        code, body, _ = _get(base + "/m", {"Cookie": "labhub_token=tok"})
        assert code == 200 and b'apple-touch-icon' in body and b"/api/m" in body
        code, body, hdr = _get(base + "/m/manifest.webmanifest")
        assert code == 200 and json.loads(body)["start_url"] == "/m"
        for size in (180, 192, 512):
            code, body, hdr = _get(base + f"/m/icon-{size}.png")
            assert code == 200 and body[:4] == b"\x89PNG"
    finally:
        srv.shutdown()


def test_window_starts_the_publisher(tmp_path):
    pytest.importorskip("PyQt6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.workbench import LabControlWindow
    from labcontrol.core.station import Station
    from tests.conftest import SIM_LAB

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    st = Station(SIM_LAB)
    w = LabControlWindow(st, None, root=str(tmp_path / "data"))
    try:
        assert w.remote.live is not None and w.remote.live._thread.is_alive()
        w.remote._update_live_name()
        assert w.remote._live_name in (None, w.local_lab_node())
        w.remote.live.ever_ran = True
        w.remote._update_live_name()
        assert w.remote._live_name
    finally:
        w.doc.dirty = False
        w.close()
        st.close()
    assert w.remote.live._stop.is_set()
