"""v0.19A Network Workspace — virtual acceptance (loopback 127.0.0.1 only).

Nothing here touches the LAN: LABLOGVIEWER_NET_SANDBOX=1 binds and discovers on
127.0.0.1 only; tests/net_proxy.py simulates latency, bandwidth, cuts and
timeouts locally.
"""

from __future__ import annotations

import hashlib
import os
import socket
import time
from pathlib import Path

import numpy as np
import pytest

from tests.real_data import BIG_FILE, FLUX_FILE, PROJECT_ROOT

DATA = PROJECT_ROOT.parent / "Data"
MULTI_SIJ = DATA / "2025 0827 LR CPAEP/2025/08/Data_0826/0825 LR @4.812GHz.hdf5"
FUNCTION_DIM = DATA / "CPAEP/Data_0909/0909 4.94-5..14GZCCEP.hdf5"


@pytest.fixture(autouse=True)
def sandbox(monkeypatch):
    monkeypatch.setenv("LABLOGVIEWER_NET_SANDBOX", "1")


def _wait(predicate, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def host():
    from app.network.host import HostServer

    if not BIG_FILE.exists():
        pytest.skip("Real measurement fixture unavailable")
    events = []
    server = HostServer("Host A", "Session", events.append, port=0, discovery_port=_free_udp_port())
    server.start()
    server.events = events
    server.mid = server.share(str(BIG_FILE), "PRL/0828 RSMEP_1.hdf5")
    yield server
    server.stop()


def _free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _client(port, code, name="Client", **kwargs):
    from app.network.client import ClientConnection

    events = []
    client = ClientConnection("127.0.0.1", port, name, code, events.append, **kwargs)
    client.events = events
    client.start()
    return client


def _reason(client):
    states = [e for e in client.events if e.get("event") == "state"]
    return states[-1].get("reason") if states else None


# -- protocol ------------------------------------------------------------------------
def test_frames_checksums_versions_and_no_objects():
    from app.network import protocol as P

    frame = P.pack({"type": "x"}, {"a": np.arange(5, dtype=np.complex128), "t": (1, 2), "n": float("nan")})
    value = P.unpack(P.FrameReader().feed(frame)[0])
    assert np.array_equal(value["a"], np.arange(5, dtype=np.complex128)) and value["t"] == (1, 2)
    corrupted = bytearray(frame)
    corrupted[-1] ^= 0xFF
    with pytest.raises(P.ProtocolError, match="Checksum"):
        P.FrameReader().feed(bytes(corrupted))
    future = bytearray(frame)
    future[4:6] = (99).to_bytes(2, "big")
    with pytest.raises(P.ProtocolError, match="version"):
        P.FrameReader().feed(bytes(future))
    with pytest.raises(P.ProtocolError):
        P.pack({"type": "x"}, {"f": object()})
    with pytest.raises(P.ProtocolError):
        P.pack({"type": "x"}, np.array([object()]))


# -- measurement structure (Parser output, not re-guessed) --------------------------------------
@pytest.mark.parametrize("path", [BIG_FILE, FLUX_FILE, MULTI_SIJ, FUNCTION_DIM])
def test_remote_experiment_is_identical_to_local(path):
    from app.core.labber_parser import load_experiment
    from app.network import protocol as P
    from app.network.remote_data import RemoteReader, build_remote_experiment, describe_experiment

    if not path.exists():
        pytest.skip(f"{path.name} unavailable")
    local = load_experiment(str(path))
    try:
        description = P.unpack(P.FrameReader().feed(P.pack({"type": "s"}, describe_experiment(local)))[0])
        assert "/" not in description["file_name"] and str(path.parent) not in str(description)
        remote = build_remote_experiment(description, RemoteReader(lambda p, s: local._reader.read(p, slice_=s)), "t/1")
        assert list(remote.channels) == list(local.channels)
        for name, info in local.channels.items():
            assert remote.channels[name] == info                          # incl. Function / relation flags
        assert [a.channel.name for a in remote.step_axes] == [a.channel.name for a in local.step_axes]
        for a, b in zip(local.step_axes, remote.step_axes):
            assert np.array_equal(a.values, b.values) and a.dim_index == b.dim_index
        assert list(remote.vector_traces) == list(local.vector_traces)    # every Sij that exists, no others
        for name in local.vector_traces:
            assert [(d.name, d.unit, d.size) for d in remote.list_dimensions(name)] == \
                   [(d.name, d.unit, d.size) for d in local.list_dimensions(name)]
            for transform in ("raw", "real", "imag", "magnitude", "magnitude_db", "phase_rad"):
                expected = local.get_data(name, transform)
                actual = remote.get_data(name, transform)
                assert actual.dtype == expected.dtype and np.array_equal(actual, expected, equal_nan=True)
        assert remote.metadata_tree.keys() == local.metadata_tree.keys()
    finally:
        local.close()


def test_multi_sij_are_exactly_the_measured_ones():
    import re
    from app.core.labber_parser import load_experiment
    from app.network.remote_data import RemoteReader, build_remote_experiment, describe_experiment

    if not MULTI_SIJ.exists():
        pytest.skip("multi-Sij fixture unavailable")
    local = load_experiment(str(MULTI_SIJ))
    remote = build_remote_experiment(describe_experiment(local), RemoteReader(), "t/1")
    found = sorted({m.group(0) for name in remote.channels for m in [re.search(r"S[1-4][1-4]", name)] if m})
    assert found == ["S11", "S12", "S21", "S22", "S31", "S42"]
    complex_traces = [name for name, trace in remote.vector_traces.items() if trace.complex]
    assert complex_traces                                     # complex kept: Real / Imag / Phase all derivable
    local.close()


# -- Host / Client -----------------------------------------------------------------------
def test_join_code_quality_structure_and_read(host):
    wrong = _client(host.port, "000000" if host.join_code != "000000" else "111111")
    assert _wait(lambda: wrong.state == "disconnected" and _reason(wrong))
    assert _reason(wrong) == "wrong_code"
    client = _client(host.port, host.join_code, "學生電腦")
    assert _wait(lambda: client.state == "connected" and client.meter.quality().level != "unknown")
    assert client.meter.quality().usable
    answer = client.wait(client.request_structure(host.mid), 10)
    assert answer["fingerprint"]["relative"] == "PRL/0828 RSMEP_1.hdf5"
    assert "Desktop" not in str(answer)                        # no Host absolute path
    trace = answer["description"]["vector_traces"][0]["trace_path"]
    full = client.wait(client.read(host.mid, trace, None), 30)
    assert full.dtype == np.float64 and full.shape[1] == 2    # raw real/imag pair (complex preserved)
    with pytest.raises(Exception):
        client.wait(client.read(host.mid, "/not/shared", None), 5)
    client.disconnect()


def test_at_most_five_clients(host):
    clients = [_client(host.port, host.join_code, f"C{i}") for i in range(5)]
    assert _wait(lambda: all(c.state == "connected" for c in clients), 20)
    sixth = _client(host.port, host.join_code, "C6")
    assert _wait(lambda: sixth.state == "disconnected" and _reason(sixth))
    assert _reason(sixth) == "host_full"
    assert len(host.clients()) == 5
    for client in clients:
        client.disconnect()
    assert _wait(lambda: not host.clients())


def test_discovery_and_search_stay_on_loopback(host):
    from app.network.discovery import discover, search

    found = discover(1.0, port=host.discovery_port)
    assert [h["session_id"] for h in found] == [host.session_id]
    assert found[0]["address"] == "127.0.0.1"
    assert search(found, "host a") and search(found, "session") and not search(found, "nobody")


def test_poor_network_is_refused(host):
    from tests.net_proxy import NetProxy

    slow = NetProxy(host.port, delay_ms=260)                  # above the 200 ms limit
    try:
        client = _client(slow.port, host.join_code)
        assert _wait(lambda: client.state == "disconnected" and _reason(client), 30)
        assert _reason(client) == "poor_quality"
    finally:
        slow.close()
    narrow = NetProxy(host.port, bandwidth=300_000)           # 0.3 MB/s < 1 MB/s
    try:
        client = _client(narrow.port, host.join_code)
        assert _wait(lambda: client.state == "disconnected" and _reason(client), 30)
        assert _reason(client) == "poor_quality"
    finally:
        narrow.close()


def test_interrupted_transfer_reconnects_resumes_and_verifies(host):
    from app.core.labber_parser import load_experiment
    from tests.net_proxy import NetProxy

    proxy = NetProxy(host.port)
    try:
        client = _client(proxy.port, host.join_code)
        assert _wait(lambda: client.state == "connected" and client.meter.quality().throughput)
        answer = client.wait(client.request_structure(host.mid), 10)
        trace = answer["description"]["vector_traces"][0]["trace_path"]
        proxy.cut_after = proxy.downstream_bytes + 2_500_000       # cut mid-transfer
        progress = []
        request = client.read(host.mid, trace, None, lambda done, total: progress.append(done))
        data = client.wait(request, 60)
        states = [e.get("state") for e in client.events if e.get("event") == "state"]
        assert "reconnecting" in states and client.state == "connected"
        local = load_experiment(str(BIG_FILE))
        assert np.array_equal(data, local._reader.read(trace))         # checksum-verified, complete
        local.close()
        assert progress and progress[-1] == data.nbytes
    finally:
        proxy.close()


def test_silent_host_times_out_and_ends_disconnected(host):
    from tests.net_proxy import NetProxy

    proxy = NetProxy(host.port)
    client = _client(proxy.port, host.join_code)
    assert _wait(lambda: client.state == "connected" and client.meter.quality().throughput)
    time.sleep(0.5)                                            # quality gate finished
    proxy.blackhole = True
    proxy.close()                                              # host now unreachable through the proxy
    assert _wait(lambda: client.state == "disconnected", 40)
    assert _reason(client) in {"host_unavailable", "timeout"}


def test_host_unavailable_and_host_stop_are_safe():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()                                               # nothing listens here
    client = _client(port, "123456")
    assert _wait(lambda: client.state == "disconnected" and _reason(client))
    assert _reason(client) == "host_unavailable"


def test_host_stop_returns_clients_to_disconnected(host):
    client = _client(host.port, host.join_code)
    assert _wait(lambda: client.state == "connected")
    host.stop()
    assert _wait(lambda: client.state == "disconnected")
    assert _reason(client) == "host_closed"


def test_permanent_copy_only_when_host_sends_it(host, tmp_path):
    client = _client(host.port, host.join_code, copy_folder=tmp_path)
    assert _wait(lambda: client.state == "connected")
    assert not list(tmp_path.iterdir())                        # nothing is saved by just viewing
    client_id = host.clients()[0].client_id
    host.send_copy(client_id, host.mid)
    assert _wait(lambda: any(e.get("event") == "copy_received" for e in client.events), 60)
    saved = Path([e for e in client.events if e.get("event") == "copy_received"][0]["path"])
    assert saved.name == BIG_FILE.name
    assert hashlib.sha256(saved.read_bytes()).hexdigest() == hashlib.sha256(BIG_FILE.read_bytes()).hexdigest()
    client.disconnect()


def test_shared_folder_copy_is_used_instead_of_downloading(tmp_path):
    from app.network.workspace import find_local_copy

    if not BIG_FILE.exists():
        pytest.skip("fixture unavailable")
    shared = tmp_path / "SimulatedNAS" / "PRL"
    shared.mkdir(parents=True)
    copy = shared / BIG_FILE.name
    copy.write_bytes(BIG_FILE.read_bytes())
    digest = hashlib.sha256(BIG_FILE.read_bytes()).hexdigest()
    fingerprint = {"size": BIG_FILE.stat().st_size, "sha256": digest, "relative": f"PRL/{BIG_FILE.name}"}
    assert find_local_copy(fingerprint, [str(tmp_path / "SimulatedNAS")]) == str(copy)
    tampered = bytearray(BIG_FILE.read_bytes())
    tampered[len(tampered) // 2] ^= 0xFF                       # same name and size, different content
    copy.write_bytes(bytes(tampered))
    assert find_local_copy(fingerprint, [str(tmp_path / "SimulatedNAS")]) is None


def test_quality_grades():
    from app.network.quality import grade

    assert grade(5, 50e6) == "excellent" and grade(60, 8e6) == "good"
    assert grade(190, 1.2e6) == "fair"
    assert grade(210, 50e6) == "unusable" and grade(10, 0.5e6) == "unusable"


# ---- v0.19B encryption ---------------------------------------------------------------
def test_traffic_is_encrypted_on_the_wire(host):
    from app.core.labber_parser import load_experiment
    from tests.net_proxy import NetProxy

    proxy = NetProxy(host.port)
    try:
        client = _client(proxy.port, host.join_code, "SecretClientName")
        assert _wait(lambda: client.state == "connected" and client.meter.quality().throughput)
        assert client._conn.encrypted
        answer = client.wait(client.request_structure(host.mid), 10)
        trace = answer["description"]["vector_traces"][0]["trace_path"]
        data = client.wait(client.read(host.mid, trace, (slice(None), slice(None), 3)), 10)
        wire = bytes(proxy.captured)
        assert b"SecretClientName" not in wire                      # name and proof travel encrypted
        assert host.join_code.encode() not in wire
        # structure encrypted. Look for long plaintext only: about 1 MB of ciphertext contains
        # any given 3 bytes (e.g. b"VNA") by pure chance ~6 % of the time (a false failure).
        channel = trace.encode()
        assert len(channel) >= 6
        assert b'"trace_path"' not in wire and channel not in wire
        assert data.tobytes()[:64] not in wire                      # raw data encrypted
        local = load_experiment(str(BIG_FILE))
        assert np.array_equal(data, local._reader.read(trace, slice_=(slice(None), slice(None), 3)))
        local.close()
        client.disconnect()
    finally:
        proxy.close()


def test_tampered_record_is_rejected(host):
    from tests.net_proxy import NetProxy

    proxy = NetProxy(host.port)
    try:
        client = _client(proxy.port, host.join_code)
        assert _wait(lambda: client.state == "connected" and client.meter.quality().throughput)
        proxy.tamper_after = proxy.downstream_bytes + 5000        # flip a byte inside an encrypted record
        request = client.read(host.mid, "/Traces/VNA - S21", None)
        assert _wait(lambda: request.done.is_set() or client.state != "connected", 20)
        states = [e.get("state") for e in client.events if e.get("event") == "state"]
        assert "reconnecting" in states                             # the altered record closed the link
    finally:
        proxy.close()


def test_old_protocol_peer_is_refused():
    from app.network import protocol as P

    frame = bytearray(P.pack({"type": "challenge"}))
    frame[4:6] = (1).to_bytes(2, "big")                            # a v0.19A peer
    with pytest.raises(P.ProtocolError, match="version"):
        P.FrameReader().feed(bytes(frame))
