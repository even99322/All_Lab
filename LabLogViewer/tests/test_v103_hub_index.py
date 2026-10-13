"""v1.0.3: the NAS index — file information and default previews worked out on the NAS by the lab hub.

A database inside the lab share "數據集散地" asks the hub for the whole folder at once (``hub_index.records_for``)
and for each file's default preview (``hub_index.fetch_preview``); the share's ``.qel/index_key`` is the access key.
Here a small fake hub stands in for Lab Control's (its own tests run the real indexer and hub against this code).
"""

from __future__ import annotations

import io
import json
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.test_nd_slice_synthetic import _build_synthetic_labber_file

KEY = "k" * 32


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def _encode(preview) -> bytes:
    """The hub's preview format (Lab Control labhub/index_codec.py), written out for the test."""
    data = preview.data
    meta = {"format": 1, "kind": preview.kind, "log_name": preview.log_name + " (NAS)",
            "channel_name": preview.channel_name, "colormap": preview.colormap, "z_min": None, "z_max": None,
            "metadata": preview.metadata, "acquisition": None, "x_name": data.x_name, "x_unit": data.x_unit,
            "z_name": data.z_name, "z_unit": data.z_unit, "transform": data.transform, "fixed_dims": {},
            "y_name": getattr(data, "y_name", ""), "y_unit": getattr(data, "y_unit", None)}
    arrays = {"x": np.asarray(data.x_values, float), "y": np.asarray(data.y_values, float)}
    if preview.kind == "2d":
        arrays["z"] = np.asarray(data.z_values, np.float32)
    arrays["meta"] = np.frombuffer(json.dumps(meta).encode(), np.uint8)
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    return buf.getvalue()


@pytest.fixture()
def share(tmp_path, monkeypatch):
    from app.core import hub_index

    root = tmp_path / "ccuqel" / "數據集散地"
    db = root / "proj" / "mirror"
    (db / "Data_1014").mkdir(parents=True)
    (root / ".qel").mkdir()
    (root / ".qel" / "index_key").write_text(KEY)
    for k in range(3):
        _build_synthetic_labber_file(db / "Data_1014" / f"m{k}.hdf5", [("Current", "A", np.linspace(0, 1, 4))],
                                     np.linspace(1e9, 2e9, 9))
    monkeypatch.setenv("LABLOGVIEWER_NET_CACHE", "force")
    monkeypatch.setenv("LABLOGVIEWER_NET_CACHE_DIR", str(tmp_path / "cache"))
    hub_index.forget()
    yield root, db
    hub_index.forget()


@pytest.fixture()
def hub(share, monkeypatch):
    """A fake hub: /api/index/tree and /api/index/preview for the share, needing the share key."""
    from app.core.quick_preview import build_quick_preview

    root, db = share
    seen = {"tree": 0, "preview": 0}

    def record(path: Path) -> dict:
        st = path.stat()
        return {"rel": path.relative_to(db).as_posix(), "size": st.st_size, "mtime": st.st_mtime, "status": "ok",
                "error": None, "log_name": "NAS " + path.stem, "creation_time": 1.7e9,
                "sweep_dimension": "1D · 4", "file_tags": ["BG"], "file_star": True, "preview": True}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            q = dict(urllib.parse.parse_qsl(url.query))
            if self.headers.get("X-QEL-Share-Key") != KEY:
                return self._send(401, b"{}")
            if url.path == "/api/index/tree":
                seen["tree"] += 1
                assert q["root"] == "proj/mirror"
                body = {"ok": True, "folders": ["Data_1014"],
                        "entries": [record(p) for p in sorted(db.rglob("*.hdf5"))]}
                return self._send(200, json.dumps(body).encode())
            if url.path == "/api/index/preview":
                seen["preview"] += 1
                path = root / q["path"]
                if int(q["size"]) != path.stat().st_size:
                    return self._send(404, b"{}")
                return self._send(200, _encode(build_quick_preview(str(path))))
            self._send(404, b"{}")

        def _send(self, code, body):
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("QEL_HUB", f"http://127.0.0.1:{srv.server_address[1]}")
    yield seen
    srv.shutdown()


def test_split_share():
    from app.core.hub_index import split_share

    assert split_share(r"\\100.114.33.20\ccuqel\數據集散地\張\mirror") == (r"\\100.114.33.20\ccuqel\數據集散地", "張/mirror")
    assert split_share(r"Z:\數據集散地") == (r"Z:\數據集散地", "")
    assert split_share("/Volumes/ccuqel/數據集散地/a/b") == ("/Volumes/ccuqel/數據集散地", "a/b")
    assert split_share(r"C:\Users\x\data") is None


def test_hub_bases_prefer_the_server_of_the_path(monkeypatch):
    from app.core import hub_index

    monkeypatch.delenv("QEL_HUB", raising=False)
    assert hub_index.hub_bases(r"\\100.114.33.20\ccuqel\數據集散地")[0] == "http://100.114.33.20:8765"
    assert hub_index.hub_bases(r"\\192.168.50.2\ccuqel\數據集散地")[0] == "http://192.168.50.2:8765"


def test_scan_takes_the_index_without_opening_files(share, hub, monkeypatch):
    from app.core import hub_index
    from app.core.database_scanner import DatabaseScanner

    _root, db = share
    later = db / "Data_1014" / "m2.hdf5"                     # changed after indexing: read here
    opened = []
    real = DatabaseScanner._scan_one

    def spy(root, path, stat=None):
        opened.append(Path(path).name)
        return real(root, path, stat=stat)

    monkeypatch.setattr(DatabaseScanner, "_scan_one", staticmethod(spy))
    records = hub_index.records_for(db)
    records["Data_1014/m2.hdf5"]["size"] += 1
    monkeypatch.setattr(hub_index, "records_for", lambda root: records)
    result = DatabaseScanner.scan(db, remote_index=lambda: hub_index.records_for(db))
    by_name = {e.file_name: e for e in result.entries}
    assert opened == [later.name]
    m0 = by_name["m0.hdf5"]
    assert m0.log_name == "NAS m0" and m0.metadata_complete and m0.file_tags == ["BG"] and m0.file_star
    assert m0.mtime == (db / "Data_1014" / "m0.hdf5").stat().st_mtime    # this computer's own time
    assert by_name["m2.hdf5"].log_name == "m2"
    assert hub["tree"] == 1


def test_no_key_or_switched_off(share, hub, monkeypatch):
    from app.core import hub_index

    root, db = share
    monkeypatch.setenv("LABLOGVIEWER_HUB_INDEX", "0")
    assert hub_index.records_for(db) is None
    monkeypatch.delenv("LABLOGVIEWER_HUB_INDEX")
    (root / ".qel" / "index_key").unlink()
    assert hub_index.records_for(db) is None and hub["tree"] == 0


def test_preview_comes_from_the_nas(qapp, share, hub, monkeypatch):
    from app.core import net_cache
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    _root, db = share

    def no_copy(*_a, **_k):
        raise AssertionError("the file must not be copied over VPN when the NAS has its preview")

    monkeypatch.setattr(net_cache, "ensure_local", no_copy)
    browser = BrowserWindow(star_store=StarStore(db.parent.parent.parent.parent / "state" / "stars.json"))
    browser.open_database(str(db))
    end = time.monotonic() + 20
    while time.monotonic() < end and "NAS index" not in browser.preview_status.text():
        qapp.processEvents()
        time.sleep(0.01)
    assert browser.scan_result is not None and all(e.metadata_complete for e in browser.scan_result.entries)
    assert "Preview from the NAS index" in browser.preview_status.text() and hub["preview"] >= 1
    assert browser._preview_data.log_name.endswith("(NAS)")
    browser._stop_enrichment()
    browser.close()


def test_no_hub_is_not_asked_again_and_again(share, monkeypatch):
    from app.core import hub_index

    _root, db = share
    monkeypatch.setenv("QEL_HUB", "http://127.0.0.1:9")              # nothing listens there
    calls = []
    real = hub_index._get
    monkeypatch.setattr(hub_index, "_get", lambda *a: calls.append(a) or real(*a))
    assert hub_index.records_for(db) is None
    n = len(calls)
    hub_index._tree_cache.clear()
    assert hub_index.fetch_preview(str(db / "Data_1014" / "m0.hdf5"), 1, 1.0) is None
    assert len(calls) == n                                         # the hub was down a moment ago: not asked
