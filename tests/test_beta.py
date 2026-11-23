"""Beta-Testumgebung: Kopie der Live-Daten, Live bleibt unverändert."""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from erp import db  # noqa: E402
from erp.services import auth, dealer, masterdata  # noqa: E402
from erp.web.app import create_app  # noqa: E402


def test_beta_uses_copy_and_never_writes_live(tmp_path, monkeypatch):
    live = f"sqlite:///{tmp_path / 'live.sqlite'}"
    db.connect(live)
    db.init_schema()
    with db.session_scope() as s:
        admin = s.query(db.m.User).filter_by(username="admin").one()
        admin.password_hash = auth.hash_password("geheim123")
        masterdata.save(s, "customer", None, {"name": "Live-Kunde"})
        dealer.save_model(s, None, {"make": "Karin", "model": "190z", "purchase_price": "35000", "sales_price": "50000"})
    db.engine().dispose()

    monkeypatch.setenv("ERP_BETA_SOURCE_URL", live)
    monkeypatch.setenv("ERP_INSECURE_COOKIES", "1")
    monkeypatch.setenv("ERP_SECRET_KEY", "x")
    with TestClient(create_app(f"sqlite:///{tmp_path / 'beta.sqlite'}")) as c:
        assert "BETA" in c.get("/").text
        assert c.post("/api/login", json={"username": "admin", "password": "geheim123"}).status_code == 200  # Live-Zugang gilt
        st = c.get("/api/state").json()
        assert st["beta"]["rows"] > 0 and [x["name"] for x in st["customers"]] == ["Live-Kunde"]
        r = c.post("/api/action/saveCustomer", json={"name": "Nur in Beta"})
        assert r.status_code == 200
        assert len(c.get("/api/state").json()["customers"]) == 2
        assert c.post("/api/beta/reload", json={}).status_code == 200                       # frische Kopie
        assert len(c.get("/api/state").json()["customers"]) == 1

    with create_engine(live).connect() as conn:                                               # Live unverändert
        assert conn.execute(text("SELECT COUNT(*) FROM customers")).scalar() == 1


def test_beta_refuses_same_database(tmp_path):
    from erp.web import beta
    url = f"sqlite:///{tmp_path / 'x.sqlite'}"
    with pytest.raises(RuntimeError):
        beta.clone(create_engine(url), url)
