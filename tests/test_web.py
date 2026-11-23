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
    assert client.post("/api/password", json={"old": "admin", "new": ""}).status_code == 400
    assert client.post("/api/password", json={"old": "admin", "new": "admin"}).status_code == 400
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
    do(client, "settings", {"company": "Premium Deluxe Motorsport"})
    mid = do(client, "saveModel", {"make": "Aurelio", "model": "GT", "cat": "Sportwagen", "ek": "100.000", "vk": "130.000",
                                   "active": True})["id"]
    cid = do(client, "saveCustomer", {"name": "Testkunde"})["id"]
    vid = do(client, "saveVehicle", {"modelId": mid, "cond": "neu", "plate": "PDM 1", "inDate": "2026-11-01"})["id"]
    do(client, "showroom", {"id": vid, "loc": "Platz 1"})
    oid = do(client, "saveOrder", {"modelId": mid, "customerId": cid, "deposit": "10.000", "expected": "2026-12-01"})["id"]
    do(client, "receiveOrder", {"id": oid, "vin": "W1"})
    sale = do(client, "sell", {"vehicleId": vid, "customerId": cid, "price": "", "pay": "bar",
                               "trade": {"make": "Alt", "model": "Wagen", "value": "20.000"}})
    assert "Rohertrag $30,000.00" in sale["message"]
    do(client, "purchase", {"customerId": cid, "make": "Gebraucht", "model": "Kombi", "price": "9.000", "pay": "bank",
                            "margin": True})
    do(client, "importModels", {"text": "Hersteller;Modell;EK;VK\nNova;Van;20.000;30.000\n"})
    st = client.get("/api/state").json()
    assert "vat" not in st["settings"] and "margin" not in st["vehicles"][0] and "tax" not in st["sales"][0]
    from erp import db as _db
    from erp.services import finance
    with _db.session_scope() as s:                       # Verkauf ohne Steuer gebucht
        bal = {a.number: saldo for a, _, _, saldo in finance.balances(s)}
    assert bal["1776"] == 0 and bal["8400"] == -130000
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
    do(client, "saveUser", {"username": "kurz", "role": "sales", "password": "1"})          # keine Mindestlänge
    do(client, "saveUser", {"username": "ohne", "role": "sales", "password": ""}, 400)      # aber nicht leer
    client.post("/api/logout", json={})
    st = login(client, "lager", "lager1234")
    assert "verkauf" not in st["me"]["views"] and "bestand" in st["me"]["views"]
    assert "users" not in st
    do(client, "sell", {"vehicleId": "1", "customerId": "1"}, 403)
    do(client, "settings", {"company": "X"}, 403)


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


def test_offer_order_sale_over_api(client):
    login(client)
    client.post("/api/password", json={"old": "admin", "new": "geheim123"})
    mid = do(client, "saveModel", {"make": "Obey", "model": "9F", "ek": "95.000", "vk": "120.000", "active": True})["id"]
    cid = do(client, "saveCustomer", {"name": "Testkunde"})["id"]
    oid = do(client, "saveOffer", {"customerId": cid, "modelId": mid, "price": "", "validUntil": "2026-12-31"})["id"]
    st = client.get("/api/state").json()
    assert st["offers"][0]["status"] == "offen" and st["offers"][0]["price"] == 120000
    r = do(client, "offerStatus", {"id": oid, "status": "bestellt", "deposit": "30.000"})
    order_id = r["id"]
    do(client, "orderStatus", {"id": order_id, "status": "eingetroffen", "vin": "W9F"})
    r = do(client, "orderStatus", {"id": order_id, "status": "ausgeliefert", "pay": "bar"})
    assert r["sale"] and "Restzahlung $90,000.00" in r["message"]
    st = client.get("/api/state").json()
    assert st["offers"][0]["status"] == "bestellt" and st["orders"][0]["status"] == "ausgeliefert"
    sale = st["sales"][0]
    assert sale["deposit"] == 30000 and sale["orderId"] == order_id and sale["status"] == "ok"
    do(client, "deleteOrder", {"id": order_id}, 400)          # erst Verkauf löschen
    do(client, "deleteSale", {"id": sale["id"]})
    st = client.get("/api/state").json()
    assert st["sales"] == [] and st["orders"][0]["status"] == "eingetroffen"
    do(client, "orderStatus", {"id": order_id, "status": "bestellt"})
    do(client, "deleteOrder", {"id": order_id})
    st = client.get("/api/state").json()
    assert st["orders"] == [] and st["offers"][0]["status"] == "offen"
    do(client, "offerStatus", {"id": oid, "status": "abgelehnt"})
    do(client, "deleteOffer", {"id": oid})
    assert client.get("/api/state").json()["offers"] == []


def test_trade_in_selection_over_api(client):
    login(client)
    client.post("/api/password", json={"old": "admin", "new": "geheim123"})
    mid = do(client, "saveModel", {"make": "Karin", "model": "190z", "ek": "35.000", "vk": "50.000", "active": True})["id"]
    cid = do(client, "saveCustomer", {"name": "Snake"})["id"]
    pid = do(client, "purchase", {"customerId": cid, "make": "Alt", "model": "Wagen", "price": "8.000",
                                  "status": "inzahlungnahme"})["id"]
    st = client.get("/api/state").json()
    assert st["purchases"][0]["status"] == "inzahlungnahme" and st["vehicles"][0]["status"] == "inzahlung"
    oid = do(client, "saveOffer", {"customerId": cid, "modelId": mid})["id"]
    order_id = do(client, "offerStatus", {"id": oid, "status": "bestellt", "deposit": "10.000"})["id"]
    do(client, "orderStatus", {"id": order_id, "status": "eingetroffen"})
    r = do(client, "orderStatus", {"id": order_id, "status": "ausgeliefert", "tradePurchaseId": pid})
    assert "Restzahlung $32,000.00" in r["message"]
    st = client.get("/api/state").json()
    p = st["purchases"][0]
    assert p["status"] == "verrechnet" and p["saleId"] == st["sales"][0]["id"]
    assert st["sales"][0]["tradeValue"] == 8000
    do(client, "purchaseStatus", {"id": pid, "status": "storniert"}, 400)   # verrechnet -> erst Verkauf stornieren


def _filled(client):
    login(client)
    client.post("/api/password", json={"old": "admin", "new": "geheim123"})
    mid = do(client, "saveModel", {"make": "Karin", "model": "190z", "ek": "35.000", "vk": "50.000", "active": True})["id"]
    cid = do(client, "saveCustomer", {"name": "Snake"})["id"]
    vid = do(client, "saveVehicle", {"modelId": mid, "cond": "neu"})["id"]
    do(client, "sell", {"vehicleId": vid, "customerId": cid})
    oid = do(client, "saveOffer", {"customerId": cid, "modelId": mid})["id"]
    do(client, "offerStatus", {"id": oid, "status": "bestellt", "deposit": "5.000"})
    do(client, "purchase", {"customerId": cid, "make": "Alt", "model": "Wagen", "price": "1.000"})
    return mid, cid, vid


def test_admin_purge(client):
    mid, cid, vid = _filled(client)
    do(client, "purge", {"kind": "vehicle", "id": vid})          # verkauftes Fahrzeug samt Verkauf
    st = client.get("/api/state").json()
    assert st["sales"] == [] and all(v["id"] != vid for v in st["vehicles"])
    do(client, "purge", {"kind": "customer", "id": cid})         # Kunde samt Angebot/Ankauf
    st = client.get("/api/state").json()
    assert st["customers"] == [] and st["offers"] == [] and st["purchases"] == []
    assert st["orders"][0]["customerId"] is None                  # Bestellung bleibt als Lagerbestellung
    do(client, "purge", {"kind": "model", "id": mid})            # Modell samt Bestellung
    st = client.get("/api/state").json()
    assert st["models"] == [] and st["orders"] == []
    do(client, "purge", {"kind": "user", "id": st["users"][0]["id"]}, 400)   # sich selbst nicht


def test_admin_wipe(client):
    _filled(client)
    do(client, "wipe", {"password": "geheim123", "confirmation": "falsch", "keepCatalog": True}, 400)
    do(client, "wipe", {"password": "falsch", "confirmation": "LÖSCHEN", "keepCatalog": True}, 400)
    do(client, "wipe", {"password": "geheim123", "confirmation": "löschen", "keepCatalog": True})
    st = client.get("/api/state").json()
    assert not any(st[k] for k in ("vehicles", "customers", "sales", "orders", "offers", "purchases"))
    assert len(st["models"]) == 1 and len(st["users"]) == 1        # Katalog und Benutzer bleiben
    cid = do(client, "saveCustomer", {"name": "Neu"})["id"]
    assert client.get("/api/state").json()["customers"][0]["nr"] == "10000"   # Nummern beginnen von vorn
    do(client, "wipe", {"password": "geheim123", "confirmation": "LÖSCHEN", "keepCatalog": False})
    assert client.get("/api/state").json()["models"] == []


def test_purge_and_wipe_admin_only(client):
    login(client)
    client.post("/api/password", json={"old": "admin", "new": "geheim123"})
    do(client, "saveUser", {"username": "vk", "fullName": "V", "role": "sales", "password": "vk", "active": True})
    client.post("/api/logout", json={})
    login(client, "vk", "vk")
    do(client, "purge", {"kind": "customer", "id": "1"}, 403)
    do(client, "wipe", {"password": "vk", "confirmation": "LÖSCHEN"}, 403)
