"""Tests der Web-Version (FastAPI) – Anmeldung, Rechte und die Autohaus-Abläufe über die API."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from erp.web.app import create_app  # noqa: E402


@pytest.fixture()
def client(db_url, monkeypatch):
    monkeypatch.setenv("ERP_INSECURE_COOKIES", "1")
    monkeypatch.setenv("ERP_SECRET_KEY", "test-secret")
    monkeypatch.delenv("ERP_ADMIN_PASSWORD", raising=False)
    with TestClient(create_app(db_url)) as c:
        yield c


def login(c, user="admin", pw="admin"):
    r = c.post("/api/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()


def do(c, name, body, status=200):
    r = c.post(f"/api/action/{name}", json=body)
    assert r.status_code == status, r.text
    return r.json()


def test_login_required_and_default_password_must_change(client):
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/login", json={"username": "admin", "password": "x"}).status_code == 400
    st = login(client)
    assert st["me"]["mustChangePassword"] is True
    do(client, "saveCustomer", {"name": "X"}, 403)           # gesperrt bis zum Passwortwechsel
    r = client.post("/api/password", json={"old": "admin", "new": "kurz"})
    assert r.status_code == 400
    assert client.post("/api/password", json={"old": "admin", "new": "geheim123"}).status_code == 200
    assert client.get("/api/state").json()["me"]["mustChangePassword"] is False
    client.post("/api/logout", json={})
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/login", json={"username": "admin", "password": "admin"}).status_code == 400
    login(client, pw="geheim123")


def test_json_required(client):
    login(client)
    r = client.post("/api/action/saveCustomer", content="name=x", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415


def test_dealer_flow_over_api(client):
    login(client)
    client.post("/api/password", json={"old": "admin", "new": "geheim123"})
    do(client, "settings", {"company": "Premium Deluxe Motorsport", "vat": "0"})
    mid = do(client, "saveModel", {"make": "Aurelio", "model": "GT", "cat": "Sportwagen", "ek": "100.000", "vk": "130.000",
                                   "active": True})["id"]
    cid = do(client, "saveCustomer", {"name": "Testkunde"})["id"]
    vid = do(client, "saveVehicle", {"modelId": mid, "cond": "neu", "plate": "PDM 1", "inDate": "2026-11-01"})["id"]
    do(client, "showroom", {"id": vid, "loc": "Platz 1"})
    oid = do(client, "saveOrder", {"modelId": mid, "customerId": cid, "deposit": "10.000", "expected": "2026-12-01"})["id"]
    do(client, "receiveOrder", {"id": oid, "vin": "W1"})
    sale = do(client, "sell", {"vehicleId": vid, "customerId": cid, "price": "", "pay": "bar",
                               "trade": {"make": "Alt", "model": "Wagen", "value": "20.000"}})
    assert "Rohertrag 30.000,00" in sale["message"]
    do(client, "purchase", {"customerId": cid, "make": "Gebraucht", "model": "Kombi", "price": "9.000", "pay": "bank",
                            "margin": True})
    do(client, "importModels", {"text": "Hersteller;Modell;EK;VK\nNova;Van;20.000;30.000\n"})
    st = client.get("/api/state").json()
    assert st["settings"]["vat"] == 0
    v = next(x for x in st["vehicles"] if x["id"] == vid)
    assert v["status"] == "verkauft" and v["ek"] == 100000
    assert {x["status"] for x in st["vehicles"]} == {"verkauft", "reserviert", "lager"}
    assert st["sales"][0]["pay"] == "bar" and st["sales"][0]["tradeValue"] == 20000
    assert st["orders"][0]["status"] == "eingetroffen"
    assert len(st["models"]) == 2 and len(st["purchases"]) == 1
    # Geschäftsregeln kommen als lesbare Fehlermeldung zurück
    err = do(client, "sell", {"vehicleId": vid, "customerId": cid}, 400)
    assert "Verkauf nicht möglich" in err["error"]
    do(client, "cancelSale", {"id": st["sales"][0]["id"]})


def test_roles_limit_actions(client):
    login(client)
    client.post("/api/password", json={"old": "admin", "new": "geheim123"})
    do(client, "saveUser", {"username": "lager", "fullName": "Lars", "role": "warehouse", "password": "lager1234",
                            "active": True})
    do(client, "saveUser", {"username": "kurz", "role": "sales", "password": "123"}, 400)
    client.post("/api/logout", json={})
    st = login(client, "lager", "lager1234")
    assert "verkauf" not in st["me"]["views"] and "bestand" in st["me"]["views"]
    assert "users" not in st
    do(client, "sell", {"vehicleId": "1", "customerId": "1"}, 403)
    do(client, "settings", {"company": "X", "vat": "0"}, 403)


def test_index_served(client):
    from erp.web.app import asset_version
    r = client.get("/")
    v = asset_version()
    assert r.status_code == 200 and f"/static/app.js?v={v}" in r.text and f"/static/app.css?v={v}" in r.text
    assert r.headers["cache-control"] == "no-cache"
    assert "immutable" in client.get(f"/static/app.js?v={v}").headers["cache-control"]
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/healthz").json() == {"ok": True}
