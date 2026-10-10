import pytest

from labcontrol.apps.web.server import create_app
from labcontrol.core.instrument import acting_as


@pytest.fixture
def client(station):
    app = create_app(station)
    yield app.test_client(), app.config["panel"]
    app.config["panel"].close()


def test_legacy_panel_routes(client, station):
    c, panel = client
    assert c.post("/api/connect", json={"devices": ["DC3", "DC5"]}).json["status"] == "success"
    html = c.get("/").data.decode()
    assert "DC5 (CH2)" in html and "Yokogawa" in html
    cur = c.get("/api/get_currents").json
    assert set(cur) == {"DC3", "DC5_CH1", "DC5_CH2"}
    assert c.post("/api/set_current", json={"dc_id": "DC3", "value": 0.15862}).status_code == 200
    assert station.source("DC3").get_level() == pytest.approx(0.15862)


def test_panel_respects_lease(client, station):
    c, panel = client
    c.post("/api/connect", json={"devices": ["DC3"]})
    with station.lease("run-x", ["pair"]):
        r = c.post("/api/set_current", json={"dc_id": "DC3", "value": 0.1})
        assert r.status_code == 409
    with acting_as(None):
        assert c.post("/api/set_current", json={"dc_id": "DC3", "value": 0.15861}).status_code == 200


def test_v1_api(client, station):
    c, _ = client
    station.connect()
    names = [i["name"] for i in c.get("/api/v1/instruments").json]
    assert "pair" in names
    assert c.post("/api/v1/param/VNA1.power", json={"value": -20}).json["value"] == -20
    assert c.post("/api/v1/param/DC3.level", json={"value": 5}).status_code == 400   # 超出上限
