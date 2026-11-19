"""Tests des Smartphone-Servers (echte HTTP-Anfragen gegen einen lokalen Server)."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from erp import db
from erp.demo import load_inspection_demo
from erp.services import auth
from erp_mobile import server as srv


@pytest.fixture()
def base(db_url):
    db.connect(db_url)
    db.init_schema()
    with db.session_scope() as s:
        load_inspection_demo(s)
        auth.save_user(s, None, "vertrieb", "V", "sales", True, "vertrieb")
    srv._secret = None
    srv.LOGIN_DELAY = 0
    httpd = srv.make_server("127.0.0.1", 0, quiet=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def call(base, path, body=None, token=None, method=None):
    req = urllib.request.Request(base + path, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req) as r:
            ctype = r.headers.get("Content-Type", "")
            raw = r.read()
            return r.status, (json.loads(raw) if "json" in ctype else raw)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def login(base, user, pw=None):
    status, data = call(base, "/api/login", {"username": user, "password": pw or user})
    assert status == 200, data
    return data["token"]


def test_static_files(base):
    status, html = call(base, "/")
    assert status == 200 and b"Pr\xc3\xbcfplanung" in html
    for path in ("/app.js", "/app.css", "/sw.js", "/manifest.webmanifest", "/icon.svg"):
        assert call(base, path)[0] == 200
    assert call(base, "/../erp/db.py")[0] == 404
    assert call(base, "/static/app.js")[0] == 404


def test_login_and_permissions(base):
    assert call(base, "/api/login", {"username": "pruefer1", "password": "falsch"})[0] == 401
    assert call(base, "/api/login", {"username": "vertrieb", "password": "vertrieb"})[0] == 403
    assert call(base, "/api/plans")[0] == 401
    assert call(base, "/api/plans", token="1.9999999999.abc")[0] == 401
    token = login(base, "pruefer1")
    status, me = call(base, "/api/me", token=token)
    assert status == 200 and me["user"]["username"] == "pruefer1"
    assert [s["key"] for s in me["statuses"]][0] == "open"
    # Manipuliertes Token (andere Benutzer-ID) wird abgelehnt
    uid, exp, sig = token.split(".")
    assert call(base, "/api/me", token=f"{int(uid) + 1}.{exp}.{sig}")[0] == 401


def test_plans_and_status_flow(base):
    t1, t2 = login(base, "pruefer1"), login(base, "pruefer2")
    status, plans = call(base, "/api/plans", token=t1)
    plan = plans["plans"][0]
    assert plan["name"] == "Verwaltung 1. OG" and plan["summary"]["total"] == 13
    _, data = call(base, f"/api/plans/{plan['id']}", token=t1)
    areas = {a["name"]: a for a in data["areas"]}
    office = areas["Büro 1.5"]
    assert office["inspector"] == "Paul Prüfer"

    status, r = call(base, f"/api/areas/{office['id']}/status",
                     {"status": "appointment", "date": "2030-01-15", "note": "Urlaub", "version": office["version"]}, t1)
    assert status == 200 and r["area"]["date"] == "2030-01-15" and r["area"]["status_by"] == "pruefer1"
    # Prüfer 2 darf fremden Bereich nicht ändern
    status, r2 = call(base, f"/api/areas/{office['id']}/status", {"status": "done"}, t2)
    assert status == 400 and "zugewiesen" in r2["error"]
    # veraltete Version -> Konflikt
    status, r3 = call(base, f"/api/areas/{office['id']}/status", {"status": "done", "version": office["version"]}, t1)
    assert status == 409 and r3["conflict"]
    # Pflichtangaben
    assert call(base, f"/api/areas/{office['id']}/status", {"status": "clarify"}, t1)[0] == 400
    assert call(base, f"/api/areas/{office['id']}/status", {"status": "appointment", "date": "kaputt"}, t1)[0] == 400

    # freien Bereich übernehmen
    server = areas["Serverraum"]
    status, r4 = call(base, f"/api/areas/{server['id']}/claim", {"version": server["version"]}, t2)
    assert status == 200 and r4["area"]["inspector"] == "Petra Prüferin"

    _, mine = call(base, "/api/areas?inspector=me", token=t1)
    assert len(mine["areas"]) == 6
    _, dates = call(base, "/api/areas?status=appointment&inspector=me", token=t1)
    assert [a["name"] for a in dates["areas"]] == ["Büro 1.4", "Büro 1.5"]
    _, hist = call(base, f"/api/areas/{office['id']}/history", token=t1)
    assert hist["history"][0]["note"] == "Urlaub"
    assert call(base, f"/api/plans/{plan['id']}/background", token=t1)[0] == 404


def test_parallel_inspectors(base):
    """Mehrere Prüfer setzen gleichzeitig Status – Benutzerkontext darf nicht vermischt werden."""
    tokens = {u: login(base, u) for u in ("pruefer1", "pruefer2")}
    _, plans = call(base, "/api/plans", token=tokens["pruefer1"])
    _, data = call(base, f"/api/plans/{plans['plans'][0]['id']}", token=tokens["pruefer1"])
    by_user = {"pruefer1": [], "pruefer2": []}
    for a in data["areas"]:
        if a["inspector"] == "Paul Prüfer":
            by_user["pruefer1"].append(a)
        elif a["inspector"] == "Petra Prüferin":
            by_user["pruefer2"].append(a)
    errors = []

    def work(user):
        for a in by_user[user]:
            status, r = call(base, f"/api/areas/{a['id']}/status", {"status": "done"}, tokens[user])
            if status != 200 or r["area"]["status_by"] != user:
                errors.append((user, status, r))

    threads = [threading.Thread(target=work, args=(u,)) for u in by_user]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Nie darf eine Änderung dem falschen Prüfer zugeschrieben werden. (SQLite kann bei echter
    # Parallelität einzelne Anfragen mit "database is locked" ablehnen – PostgreSQL nicht.)
    assert not [e for e in errors if e[1] == 200]
    if base and db.engine().url.get_backend_name() == "postgresql":
        assert not errors
