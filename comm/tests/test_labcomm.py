import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import labcomm  # noqa: E402
from labcomm import actions, config, handoff, local, tags  # noqa: E402
from labcomm.errors import CommError, Unreachable  # noqa: E402


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("QEL_HOME", str(tmp_path / "qel"))
    monkeypatch.delenv("QEL_TOKEN", raising=False)
    monkeypatch.delenv("QEL_PORTAL_URL", raising=False)
    return tmp_path / "qel"


# ---- tags -----------------------------------------------------------------------
def test_canonical_names():
    assert tags.canonical("  best_data ") == "Best Data"
    assert tags.canonical("debg") == "De-background"
    assert tags.canonical("bic") == "BIC"
    assert tags.canonical("vna_sweep") == "VNA Sweep"
    assert tags.canonical("My  New   Tag") == "My New Tag"
    assert tags.canonical(None) == ""
    assert tags.canonical("yig", {"YIG sphere": "YIG", "yig": "YIG"}) == "YIG"


def test_normalize_dedupes_keeping_order():
    assert tags.normalize_list(["BG", "bg", "Flux", "", "best-data", "Best Data"]) == ["BG", "Flux", "Best Data"]


def test_taxonomy_fallbacks_to_cache_then_default(home):
    t = tags.shared_taxonomy(None)
    assert t["source"] == "default"
    assert "LRCPAEP" in tags.tag_names(t)

    class Fake:
        def tags(self):
            return {"categories": [], "tags": [{"name": "YIG", "category": "Paper Topic", "papers": [3]}]}

    t2 = tags.shared_taxonomy(Fake())
    assert t2["source"] == "portal"

    class Down:
        def tags(self):
            raise Unreachable("x")

    t3 = tags.shared_taxonomy(Down())
    assert t3["source"] == "cache" and tags.tag_names(t3) == ["YIG"]


# ---- config ---------------------------------------------------------------------
def test_session_roundtrip_and_env_override(home, monkeypatch):
    cfg = config.load_config()
    assert cfg.token == "" and cfg.portal_url[0].startswith("http://")
    p = config.save_session("nas:8090, 10.0.0.2:8090", "tok123", {"username": "amy"})
    if os.name != "nt":
        assert (p.stat().st_mode & 0o777) == 0o600
    cfg = config.load_config()
    assert cfg.portal_url == ["http://nas:8090", "http://10.0.0.2:8090"]
    assert cfg.token == "tok123" and cfg.user["username"] == "amy"
    monkeypatch.setenv("QEL_TOKEN", "envtok")
    monkeypatch.setenv("QEL_PORTAL_URL", "http://x:1")
    cfg = config.load_config()
    assert cfg.token == "envtok" and cfg.portal_url == ["http://x:1"]
    monkeypatch.delenv("QEL_TOKEN")
    config.clear_session()
    assert config.load_config().token == ""


# ---- local IPC ------------------------------------------------------------------
def test_local_send_and_ping():
    got = []

    def handler(action, payload):
        got.append((action, payload))
        return {"accepted": True, "n": len(got)}

    ep = local.LocalEndpoint("labcontrol", "9.9.9", handler, actions=[actions.APPLY_SCHEME]).start()
    try:
        assert local.is_running("labcontrol")
        assert local.send("labcontrol", "ping")["version"] == "9.9.9"
        r = local.send("labcontrol", actions.APPLY_SCHEME, {"path": "/x.hdf5"}, sender="lablogviewer")
        assert r == {"accepted": True, "n": 1}
        assert got == [("apply_scheme", {"path": "/x.hdf5"})]
        with pytest.raises(CommError, match="不支援"):
            local.send("labcontrol", actions.OPEN_FILE, {})
        assert [m["module"] for m in local.running_modules()] == ["labcontrol"]
        assert "secret" not in local.running_modules()[0]
    finally:
        ep.stop()
    assert not local.is_running("labcontrol")
    with pytest.raises(CommError, match="沒有開著"):
        local.send("labcontrol", "ping")


def test_local_rejects_wrong_secret():
    ep = local.LocalEndpoint("lablogviewer", "1", lambda a, p: "ok").start()
    try:
        info_path = local.run_dir() / "lablogviewer.json"
        d = json.loads(info_path.read_text())
        d["secret"] = "wrong"
        info_path.write_text(json.dumps(d))
        with pytest.raises(CommError, match="密語"):
            local.send("lablogviewer", "open_file", {})
    finally:
        ep.stop()


def test_handler_error_is_reported():
    def boom(a, p):
        raise ValueError("壞掉")

    ep = local.LocalEndpoint("m", "1", boom).start()
    try:
        with pytest.raises(CommError, match="壞掉"):
            local.send("m", "open_file", {})
    finally:
        ep.stop()


# ---- handoff --------------------------------------------------------------------
h5py = pytest.importorskip("h5py")

SCHEME = {"scheme": 2, "name": "YIG 2D", "graph": {"start": [0, 0], "nodes": [
    {"kind": "set", "id": "n1", "target": "magnet_A", "mode": "sweep", "start": 100, "stop": 150, "step": 0.5,
     "unit": "mA"},
    {"kind": "measure", "id": "n2", "instrument": "VNA1", "traces": ["S21"]}], "links": []},
    "output": {"tags": ["BIC", "Flux"]}}


def _labber_like(path: Path, labber_tags=("BIC",)):
    with h5py.File(path, "w") as f:
        g = f.create_group("Tags")
        g.attrs["Tags"] = [t.encode() for t in labber_tags]
        g.attrs["Project"] = "10/1001"
        g.attrs["User"] = "QEL"
        f.create_dataset("Data/Data", data=[[1.0, 2.0]])


def test_embed_and_extract(tmp_path):
    p = tmp_path / "Data_1001" / "yig.hdf5"
    p.parent.mkdir()
    _labber_like(p)
    assert handoff.extract_scheme(p) is None
    assert handoff.embed(p, SCHEME, {"tags": ["BIC", "Flux"], "source": {"module": "labcontrol"}})
    assert handoff.extract_scheme(p) == SCHEME
    m = handoff.read_meta(p)
    assert m["tags"] == ["BIC", "Flux"] and m["project"] == "10/1001" and m["user"] == "QEL"
    assert m["source"]["module"] == "labcontrol" and m["labcomm"] == labcomm.__version__
    handoff.embed(p, None, {"dataset_id": 7})
    assert handoff.read_meta(p)["dataset_id"] == 7 and handoff.read_meta(p)["source"]["module"] == "labcontrol"


def test_extract_from_native_and_raw_sibling(tmp_path):
    day = tmp_path / "Data_1001"
    (day / "_raw").mkdir(parents=True)
    raw = day / "_raw" / "yig_153012.lm.h5"
    with h5py.File(raw, "w") as f:
        f.attrs["schema"] = "labcontrol.dataset/1"
        f.attrs["metadata"] = json.dumps({"experiment": {"name": "x", "scheme": SCHEME}})
    assert handoff.extract_scheme(raw) == SCHEME
    labber = day / "yig_003_52mA.hdf5"           # 分檔
    _labber_like(labber)
    assert handoff.extract_scheme(labber) == SCHEME
    other = day / "other.hdf5"
    _labber_like(other)
    assert handoff.extract_scheme(other) is None


def test_fingerprint_stable_across_rename(tmp_path):
    a = tmp_path / "a.hdf5"
    a.write_bytes(os.urandom(6 << 20))
    fp = handoff.fingerprint(a)
    b = tmp_path / "b.hdf5"
    a.rename(b)
    assert handoff.fingerprint(b) == fp and fp.startswith("qfp1:")


def test_scheme_summary():
    lines = handoff.scheme_summary(SCHEME)
    assert lines[0] == "方案：YIG 2D"
    assert "• magnet_A：100 → 150 mA（步進 0.5）" in lines
    assert "• 量測 VNA1：S21" in lines
    assert lines[-1] == "標籤：BIC、Flux"
    assert handoff.scheme_summary(None) == ["（沒有量測設置）"]


def test_client_reports_unreachable():
    c = labcomm.PortalClient("http://127.0.0.1:9, http://127.0.0.1:10", timeout=1)
    with pytest.raises(Unreachable):
        c.ping()
