"""1.0.25：推播通知——標準函式庫的 Web Push 密碼學（用 cryptography 交叉驗證）、訂閱設定、模擬 / 量測結束事件。"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request

import pytest

crypto = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: E402

from labhub import webpush as wp  # noqa: E402


def _ua():
    """瀏覽器那一端：P-256 金鑰＋auth secret。"""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    return priv, pub, auth


def _decrypt(body: bytes, priv, ua_pub: bytes, auth: bytes) -> bytes:
    """照 RFC 8291 用 cryptography 解開（瀏覽器做的事）。"""
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    as_pub = body[21:21 + idlen]
    ct = body[21 + idlen:]
    assert rs == 4096 and idlen == 65
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub)
    secret = priv.exchange(ec.ECDH(), peer)
    ikm = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + ua_pub + as_pub).derive(secret)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    pt = AESGCM(cek).decrypt(nonce, ct, None)
    assert pt.endswith(b"\x02")
    return pt[:-1]


def test_aes_gcm_matches_cryptography():
    for n in (0, 1, 15, 16, 17, 33, 1000):
        key, nonce, pt, aad = os.urandom(16), os.urandom(12), os.urandom(n), os.urandom(n % 5)
        assert wp.aes_gcm_encrypt(key, nonce, pt, aad) == AESGCM(key).encrypt(nonce, pt, aad)


def test_ecdh_and_es256():
    priv, pub, _ = _ua()
    mine = wp.new_private()
    my_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), wp.point_bytes(wp.mul(mine)))
    assert wp.ecdh(mine, wp.point_from(pub)) == priv.exchange(ec.ECDH(), my_pub)
    for msg in (b"", b"hello", os.urandom(300)):
        sig = wp.sign_es256(mine, msg)
        my_pub.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")), msg,
                      ec.ECDSA(hashes.SHA256()))
    with pytest.raises(ValueError):
        wp.point_from(b"\x04" + bytes(64))          # 不在曲線上


def test_rfc8291_payload_decrypts():
    priv, pub, auth = _ua()
    payload = json.dumps({"title": "✅ S 參數模擬完成", "body": "LAB-PC：MS_YIG_test，81 分"}, ensure_ascii=False).encode()
    body = wp.encrypt(payload, pub, auth)
    assert _decrypt(body, priv, pub, auth) == payload


def test_vapid_jwt(tmp_path):
    keys = wp.VapidKeys.load_or_create(tmp_path / "v.json")
    again = wp.VapidKeys.load_or_create(tmp_path / "v.json")
    assert again.public == keys.public and len(wp.unb64u(keys.public_b64)) == 65
    tok = keys.jwt("https://fcm.googleapis.com/fcm/send/abc", "https://hub.example:8443")
    h, c, s = tok.split(".")
    claims = json.loads(wp.unb64u(c))
    assert json.loads(wp.unb64u(h))["alg"] == "ES256"
    assert claims["aud"] == "https://fcm.googleapis.com" and claims["sub"] == "https://hub.example:8443"
    assert claims["exp"] > time.time() + 3600
    sig = wp.unb64u(s)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), keys.public)
    pub.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
               f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))


def test_send_builds_request():
    priv, pub, auth = _ua()
    keys = wp.VapidKeys(wp.new_private())
    seen = {}

    class R:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return b""

    def opener(req, timeout=0):
        seen.update(url=req.full_url, headers=dict(req.header_items()), data=req.data)
        return R()
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": wp.b64u(pub), "auth": wp.b64u(auth)}}
    status, _ = wp.send(sub, b'{"title":"x"}', keys, subject="https://hub.example", opener=opener, topic="t1")
    assert status == 201 and seen["url"] == "https://push.example/abc"
    hd = {k.lower(): v for k, v in seen["headers"].items()}
    assert hd["content-encoding"] == "aes128gcm" and hd["ttl"] and hd["topic"] == "t1"
    assert hd["authorization"].startswith("vapid t=") and f"k={keys.public_b64}" in hd["authorization"]
    assert _decrypt(seen["data"], priv, pub, auth) == b'{"title":"x"}'


def _browser_sub():
    priv, pub, auth = _ua()
    ep = "https://push.example/" + base64.urlsafe_b64encode(os.urandom(9)).decode()
    return priv, pub, auth, {"endpoint": ep, "keys": {"p256dh": wp.b64u(pub), "auth": wp.b64u(auth)}}


def test_notifier_filters_and_cleanup(tmp_path):
    from labhub.notify import Notifier
    sent = []
    status = {"code": 201}

    def sender(sub, payload, keys, **kw):
        sent.append((sub["endpoint"], json.loads(payload)))
        return status["code"], ""
    n = Notifier(tmp_path, sender=sender)
    _, _, _, b1 = _browser_sub()
    _, _, _, b2 = _browser_sub()
    s1 = n.subscribe({"subscription": b1}, "alice", "Android Chrome", {"measure": True, "hfss": True})
    s2 = n.subscribe({"subscription": b2, "cats": {"measure": False}}, "bob", "iPhone", {"measure": False, "hfss": True})
    assert s1["cats"] == {"measure": True, "hfss": True} and s2["denied"] == ["measure"]
    assert n.lookup(b1["endpoint"])["id"] == s1["id"]
    # 只收某台電腦的模擬
    n.update(s1["id"], {"sources": ["hfss:lab-pc-a@ming", "bogus"]}, {"measure": True, "hfss": True})
    assert n.get(s1["id"])["sources"] == ["hfss:lab-pc-a@ming"]
    assert n.notify("hfss", "hfss:lab-pc-b@x", "t", "b") == 1          # 只有 s2
    assert n.notify("hfss", "hfss:lab-pc-a@ming", "t2", "b") == 2
    assert n.notify("measure", "measure:VNA", "t3", "b") == 1          # s2 沒權限
    assert n.notify("measure", "measure:VNA", "t3", "b") == 0          # 同一事件 5 秒內不重送
    n.update(s1["id"], {"enabled": False}, {"measure": True, "hfss": True})
    assert n.notify("hfss", "hfss:lab-pc-a@ming", "t4", "b") == 1
    deadline = time.time() + 10
    while len(sent) < 5 and time.time() < deadline:
        time.sleep(0.05)
    assert len(sent) == 5 and sent[0][1]["title"] == "t"
    # 瀏覽器取消訂閱（410）→ 刪掉
    status["code"] = 410
    r = n.send_now(s2["id"], {"title": "x", "cat": "test"})
    assert not r["ok"] and r["status"] == 410 and n.lookup(b2["endpoint"]) is None
    # 存檔後重新載入還在
    n2 = Notifier(tmp_path, sender=sender)
    assert n2.lookup(b1["endpoint"])["enabled"] is False and n2.keys.public == n.keys.public


def test_event_texts(tmp_path):
    from labhub.notify import Notifier, fmt_dur
    got = []
    n = Notifier(tmp_path, sender=lambda sub, payload, keys, **kw: got.append(json.loads(payload)) or (201, ""))
    _, _, _, b = _browser_sub()
    n.subscribe({"subscription": b}, "alice", "x", {"measure": True, "hfss": True})
    n.hfss_event({"task": "SIM_S", "label": "S 參數模擬", "ok": True, "elapsed": 4867, "project": "MS_YIG_test",
                  "detail": "YIG 共振 5.0903 GHz"}, "小明的電腦", "lab-pc-a@ming")
    n.measure_event("VNA-1", {"id": "r1", "name": "flux map", "state": "failed", "started": 100, "finished": 3700,
                              "error": "VNA timeout"})
    deadline = time.time() + 10
    while len(got) < 2 and time.time() < deadline:
        time.sleep(0.05)
    t = {g["title"]: g for g in got}
    assert t["✅ S 參數模擬完成"]["body"].startswith("小明的電腦：MS_YIG_test，1 小時 21 分")
    assert "5.0903" in t["✅ S 參數模擬完成"]["body"] and t["✅ S 參數模擬完成"]["url"] == "/licenses"
    assert t["❌ 量測失敗"]["body"] == "VNA-1：flux map，1 小時\nVNA timeout" and t["❌ 量測失敗"]["url"] == "/m#VNA-1"
    assert fmt_dur(42) == "42 秒" and fmt_dur(125) == "2 分"


def test_mobile_store_detects_end():
    from labhub.mobile_routes import MobileStore
    st = MobileStore()
    run = {"id": "r1", "name": "scan", "state": "running", "started": time.time()}
    st.update("VNA-1", {"run": run})
    st.update("VNA-1", {"run": dict(run, index=5)})
    assert st.pop_ended() == []
    st.update("VNA-1", {"run": dict(run, state="finished", finished=time.time())})
    ev = st.pop_ended()
    assert len(ev) == 1 and ev[0]["station"] == "VNA-1" and ev[0]["run"]["state"] == "finished"
    st.update("VNA-1", {"run": dict(run, state="finished", finished=time.time())})     # 之後每秒還會送 → 不重複
    assert st.pop_ended() == []
    # Hub 剛重新啟動：很久以前結束的不通知，剛結束的才通知
    st2 = MobileStore()
    st2.update("A", {"run": {"id": "x", "state": "finished", "finished": time.time() - 3600}})
    st2.update("B", {"run": {"id": "y", "state": "aborted", "finished": time.time() - 5}})
    assert [e["station"] for e in st2.pop_ended()] == ["B"]


def _call(url, method="GET", body=None, cookie=None, headers=None):
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {}),
                                          **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as r:
            ct = r.headers.get("Content-Type", "")
            raw = r.read()
            return r.status, (json.loads(raw or b"{}") if "json" in ct else raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_http_flow(tmp_path, monkeypatch):
    from labhub.server import Config, make_server
    from tests import fake_paperlib

    got = []
    monkeypatch.setattr(wp, "send", lambda sub, payload, keys, **kw: got.append((sub["endpoint"], json.loads(payload), kw))
                        or (201, ""))
    psrv, _, purl = fake_paperlib.start()
    srv, state = make_server(Config(tmp_path / "hub", token="dev", paperlib_url=purl), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        ow = _call(url + "/api/platform/login", "POST", {"username": "admin", "password": "adminpw"})[1]
        oc = f"labhub_token={ow['token']}"
        al = _call(url + "/api/platform/login", "POST", {"username": "alice", "password": "alicepw"})[1]
        ac = f"labhub_token={al['token']}"
        assert _call(url + "/api/notify")[0] == 401
        code, page = _call(url + "/notify", cookie=oc)
        assert code == 200 and "這台裝置的通知" in page and "🔔 通知" in page
        info = _call(url + "/api/notify", cookie=oc)[1]
        assert len(wp.unb64u(info["key"])) == 65 and {c["id"] for c in info["cats"]} == {"measure", "hfss"}
        _, _, _, b = _browser_sub()
        code, r = _call(url + "/api/notify/subscribe", "POST",
                        {"subscription": b, "origin": "https://hub.example:8443", "device": "Android Chrome"}, oc)
        assert code == 200 and r["sub"]["enabled"]
        sid = r["sub"]["id"]
        assert _call(url + "/api/notify/lookup", "POST", {"endpoint": b["endpoint"]}, oc)[1]["sub"]["id"] == sid
        # 別人的裝置不能改
        assert _call(url + f"/api/notify/sub/{sid}", "PUT", {"enabled": False}, ac)[0] == 403
        # 測試通知
        code, r = _call(url + f"/api/notify/sub/{sid}/test", "POST", {}, oc)
        assert code == 200 and r["ok"] and got[-1][1]["cat"] == "test" and got[-1][2]["subject"] == "https://hub.example:8443"
        # 模擬做完（工具不用登入）
        code, r = _call(url + "/api/hfss/event", "POST", {"host": "LAB-PC-A", "user": "ming", "task": "SIM_S",
                                                          "label": "S 參數模擬", "ok": True, "elapsed": 90,
                                                          "project": "P1"})
        assert code == 200 and r["sent"] == 1
        # 量測結束（量測電腦用裝置 token）
        dev = {"Authorization": "Bearer dev"}
        run = {"id": "r9", "name": "scan", "state": "running", "started": time.time()}
        assert _call(url + "/api/m/VNA-1/update", "POST", {"run": run}, headers=dev)[0] == 200
        assert _call(url + "/api/m/VNA-1/update", "POST", {"run": dict(run, state="finished", finished=time.time())},
                     headers=dev)[0] == 200
        deadline = time.time() + 10
        while len(got) < 3 and time.time() < deadline:
            time.sleep(0.05)
        titles = [g[1]["title"] for g in got]
        assert "✅ S 參數模擬完成" in titles and "✅ 量測完成" in titles
        # 只收模擬 → 量測不再送
        _call(url + f"/api/notify/sub/{sid}", "PUT", {"cats": {"measure": False}}, oc)
        run2 = dict(run, id="r10")
        _call(url + "/api/m/VNA-1/update", "POST", {"run": run2}, headers=dev)
        _call(url + "/api/m/VNA-1/update", "POST", {"run": dict(run2, state="finished", finished=time.time())}, headers=dev)
        time.sleep(0.3)
        assert sum(1 for g in got if g[1]["title"] == "✅ 量測完成") == 1
        info = _call(url + "/api/notify", cookie=oc)[1]
        assert any(s["id"] == "measure:VNA-1" for s in info["sources"]) and info["count"] == 1
        assert _call(url + f"/api/notify/sub/{sid}", "DELETE", None, oc)[1]["removed"]
    finally:
        srv.shutdown()
        psrv.shutdown()
